"""The session server: the GM's app (or a headless box) hosting the table.

Pure Python and Qt-free — stdlib :mod:`socket` and :mod:`threading` only, so the
whole thing is headless-testable and reusable by ``python -m
mm_companion.server`` later. One accept thread hands each peer to its own reader
thread; every mutation goes through one lock, is persisted through
:mod:`.store`, and is broadcast to the connections that should see it.

Two rules shape the design:

**The server rolls.** A client sends a :class:`~.protocol.RollRequest` — a label,
its modifiers and a DC — and the server resolves it with
:func:`mm_companion.core.dice.resolve_check`. No client ever reports its own
number, so none can edit it.

**A hidden roll is never broadcast.** It is recorded and persisted, the GM's own
window is told through :func:`on_event`, and it is left out of the wire entirely
— there is nothing for a player client to peek at. Only a slot marked ``is_gm``
may ask for one; a player's ``hidden`` flag is ignored.

The Qt side never subclasses this. It passes an ``on_event`` callback and
receives ``(kind, payload)`` pairs where the payload is always a plain dict;
``ui/session_bridge.py`` turns those into signals.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Iterable
from random import Random

from mm_companion.core import storage
from mm_companion.core.dice import CheckResult, resolve_check, roll_d20

from . import store
from .model import PlayerSlot, RollRecord, SessionState, tokens_match, utc_now
from .net import IO_TIMEOUT, Connection, TcpTransport, Transport
from .protocol import (
    ERROR_BAD_TOKEN,
    ERROR_MALFORMED,
    ERROR_MOD_SKEW,
    ERROR_PROTOCOL_VERSION,
    ERROR_RATE_LIMIT,
    ERROR_SESSION_FULL,
    PROTOCOL_VERSION,
    ApplyCondition,
    CharacterSnapshot,
    ErrorMessage,
    Hello,
    Kicked,
    Message,
    Ping,
    Pong,
    ProtocolError,
    RemoveCondition,
    RemoveRollRequest,
    RollAdded,
    RollRemoved,
    RollRequest,
    Roster,
    Welcome,
    sanitize_snapshot,
)

#: How many player connections a session accepts at once.
DEFAULT_MAX_CLIENTS = 8

#: A peer that connects but never says :class:`~.protocol.Hello` is dropped after
#: this, so an idle socket cannot hold a reader thread indefinitely.
HANDSHAKE_TIMEOUT = 15.0

#: Per-connection rate limit: at most this many messages per
#: :data:`RATE_LIMIT_WINDOW` seconds. A live sheet pushes a snapshot per edit, so
#: the budget is generous; a client that blows through it is looping.
RATE_LIMIT_MESSAGES = 120
RATE_LIMIT_WINDOW = 5.0

#: Clamps on the numbers a client may put in a roll request, so the shared
#: history cannot be filled with absurd values.
MAX_ROLL_MODIFIER = 1000
MAX_LABEL_CHARS = 120

#: How many recent rolls a :class:`~.protocol.Welcome` carries. The full history
#: keeps growing across evenings (that is the point of persistence), and a roll
#: dict is ~220 bytes against a :data:`~.protocol.MAX_MESSAGE_BYTES` of 256 KiB —
#: past roughly a thousand rolls an uncapped welcome would fail to encode and
#: the join would be refused outright. The full log stays on the server; a
#: joining client gets the recent slice.
WELCOME_HISTORY_ROLLS = 200

#: How long :meth:`SessionServer.stop` waits for each worker thread.
_JOIN_TIMEOUT = 2.0

# Event kinds handed to ``on_event``. The payload is always a dict.
EVENT_STARTED = "started"  # {"session_id", "host", "port"}
EVENT_STOPPED = "stopped"  # {"session_id"}
EVENT_PLAYER_JOINED = "player_joined"  # {"player": public slot dict, "new": bool}
EVENT_PLAYER_LEFT = "player_left"  # {"player": public slot dict}
EVENT_ROSTER = "roster"  # {"players": [roster dicts — no tokens, no characters]}
EVENT_SNAPSHOT = "snapshot"  # {"player_id", "character"}
EVENT_ROLL = "roll"  # a full roll dict, hidden rolls included
EVENT_ROLL_REMOVED = "roll_removed"  # {"seq"}
EVENT_REFUSED = "refused"  # {"code", "message", "address"}
EVENT_ERROR = "error"  # {"code", "message"}


class SessionServer:
    """Hosts one :class:`~.model.SessionState` over a :class:`~.net.Transport`.

    The GM's window drives the local half directly — :meth:`roll`,
    :meth:`apply_condition`, :meth:`kick` — while remote players arrive over the
    transport. The GM has a roster slot of its own (:meth:`gm_slot`) so its rolls
    appear in the shared history under a name like everyone else's.
    """

    def __init__(
        self,
        state: SessionState,
        *,
        host: str = "0.0.0.0",
        port: int = 0,
        transport: Transport | None = None,
        workspace: storage.Workspace | None = None,
        on_event: Callable[[str, dict], None] | None = None,
        max_clients: int = DEFAULT_MAX_CLIENTS,
        mod_fingerprint: str = "",
        gm_name: str = "GM",
        persist: bool = True,
        rng: Random | None = None,
    ) -> None:
        self.state = state
        self.max_clients = max_clients
        self.mod_fingerprint = mod_fingerprint

        self._host = host
        self._port = port
        self._transport = transport or TcpTransport()
        self._workspace = workspace
        self._on_event = on_event
        self._persist_enabled = persist
        self._rng = rng
        self._gm_name = gm_name

        self._lock = threading.RLock()
        self._listener = None
        self._accept_thread: threading.Thread | None = None
        self._threads: list[threading.Thread] = []
        self._connections: dict[str, Connection] = {}
        # Seated but not yet welcomed. A connection is in ``_connections`` from
        # the moment it claims a slot (so the client limit counts it), but must
        # not receive a broadcast until its Welcome has gone out first — a client
        # reads the handshake answer before anything else, and a roster that
        # overtook it would look like a protocol violation.
        self._welcomed: set[str] = set()
        self._running = False
        self._address: tuple[str, int] = (host, port)

    # -- lifecycle ---------------------------------------------------------

    @property
    def running(self) -> bool:
        return self._running

    @property
    def address(self) -> tuple[str, int]:
        """The bound ``(host, port)``; the real port once :meth:`start` has run."""
        return self._address

    def start(self) -> tuple[str, int]:
        """Bind, start accepting, and return the real address."""
        if self._running:
            return self._address
        self._listener = self._transport.listen(self._host, self._port)
        self._address = self._listener.address
        self._running = True
        self.gm_slot()  # the GM has a roster seat from the start
        self._persist()
        self._accept_thread = threading.Thread(
            target=self._accept_loop, name="session-accept", daemon=True
        )
        self._accept_thread.start()
        self._emit(
            EVENT_STARTED,
            {
                "session_id": self.state.id,
                "host": self._address[0],
                "port": self._address[1],
            },
        )
        return self._address

    def stop(self) -> None:
        """Stop listening, drop every connection, and persist the final roster."""
        if not self._running:
            return
        self._running = False
        if self._listener is not None:
            self._listener.close()
        with self._lock:
            connections = list(self._connections.values())
            self._connections.clear()
            self._welcomed.clear()
            for slot in self.state.players.values():
                slot.connected = False
            self._persist()
        for connection in connections:
            connection.close()
        for thread in [self._accept_thread, *self._threads]:
            if thread is not None and thread is not threading.current_thread():
                thread.join(timeout=_JOIN_TIMEOUT)
        self._threads.clear()
        self._accept_thread = None
        self._listener = None
        self._emit(EVENT_STOPPED, {"session_id": self.state.id})

    def __enter__(self) -> SessionServer:
        self.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        self.stop()

    # -- the GM's own half -------------------------------------------------

    def gm_slot(self) -> PlayerSlot:
        """The host's roster slot, created on first use.

        The GM drives the server object in-process rather than over a socket, but
        still needs a slot so its rolls carry a name and its card appears in the
        roster like anyone else's.
        """
        with self._lock:
            for slot in self.state.players.values():
                if slot.is_gm:
                    return slot
            slot = self.state.add_player(self._gm_name, is_gm=True)
            slot.connected = True
            return slot

    def roll(
        self,
        *,
        label: str = "",
        bonus: int = 0,
        penalty: int = 0,
        dc: int | None = None,
        hidden: bool = False,
        player_id: str | None = None,
    ) -> RollRecord:
        """Resolve a roll for the GM (or for *player_id*) and publish it.

        This is the one roll path: a :class:`~.protocol.RollRequest` from a client
        lands here too, which is why a client can never supply the die.
        """
        with self._lock:
            slot = self.state.players.get(player_id) if player_id else self.gm_slot()
        if slot is None:
            raise KeyError(f"no player {player_id!r}")
        return self._resolve_roll(
            slot, label=label, bonus=bonus, penalty=penalty, dc=dc, hidden=hidden
        )

    def remove_roll(self, seq: int) -> bool:
        """Drop one roll from the shared log and tell every client (a GM action).

        Returns ``False`` when no roll carried that sequence number. A hidden roll's
        removal is not broadcast — it was never on the wire in the first place, so
        only the GM's own window is told (through :data:`EVENT_ROLL_REMOVED`).
        """
        with self._lock:
            record = self.state.remove_roll(seq)
            if record is not None:
                self._rewrite_rolls()
        if record is None:
            return False
        self._emit(EVENT_ROLL_REMOVED, {"seq": seq})
        if not record.hidden:
            self.broadcast(RollRemoved(seq=seq))
        return True

    def apply_condition(
        self, player_id: str, condition_id: str, parameter: str | None = None
    ) -> bool:
        """Tell one player's client to put a condition on their live sheet."""
        return self.send_to(
            player_id,
            ApplyCondition(player_id=player_id, condition_id=condition_id, parameter=parameter),
        )

    def remove_condition(
        self, player_id: str, condition_id: str, parameter: str | None = None
    ) -> bool:
        """Tell one player's client to take a condition off their live sheet."""
        return self.send_to(
            player_id,
            RemoveCondition(player_id=player_id, condition_id=condition_id, parameter=parameter),
        )

    def kick(self, player_id: str, reason: str = "") -> bool:
        """Drop a player's connection *and* their roster slot."""
        with self._lock:
            connection = self._connections.pop(player_id, None)
            self._welcomed.discard(player_id)
            removed = self.state.remove_player(player_id)
            self._persist()
        if connection is not None:
            try:
                connection.send(Kicked(reason=reason))
            except OSError:
                pass
            connection.close()
        if removed is not None:
            self._broadcast_roster()
        return removed is not None

    def set_session_name(self, name: str) -> None:
        """Rename the session and persist it."""
        with self._lock:
            self.state.name = name
            self.state.touch()
            self._persist()

    def set_npc_paths(self, paths: Iterable[str]) -> None:
        """Replace the session's NPC list and persist it.

        NPCs are GM-only: the filenames name characters in the workspace
        ``gm_characters/`` dir and nothing about them ever goes on the wire. This
        exists so the GM's window can change the cast while a session is running
        without racing the server's own writes — the state is shared, so the
        write takes the same lock every other mutation does.
        """
        with self._lock:
            self.state.npc_paths = [str(path) for path in paths]
            self.state.touch()
            self._persist()

    # -- outbound ----------------------------------------------------------

    def roster(self) -> list[dict]:
        """The public roster, as it goes on the wire."""
        with self._lock:
            return self.state.roster()

    def history(self, *, include_hidden: bool = True) -> list[dict]:
        """The roll log as dicts. The GM's window wants the hidden rolls too."""
        with self._lock:
            rolls = self.state.rolls if include_hidden else self.state.visible_rolls()
            return [roll.to_dict() for roll in rolls]

    def connected_player_ids(self) -> list[str]:
        """Ids of the players with a live socket right now."""
        with self._lock:
            return list(self._connections)

    def send_to(self, player_id: str, message: Message) -> bool:
        """Send to one player; False if they are not connected or the write failed."""
        with self._lock:
            connection = self._connections.get(player_id) if player_id in self._welcomed else None
        if connection is None:
            return False
        try:
            connection.send(message)
        except ProtocolError as exc:
            # Our own message failed to encode (oversized); the peer is fine and
            # keeps its seat — dropping it would punish the wrong side.
            self._emit(EVENT_ERROR, {"code": "encode", "message": str(exc)})
            return False
        except OSError:
            self._drop(player_id)
            return False
        return True

    def broadcast(self, message: Message, *, exclude: Iterable[str] = ()) -> None:
        """Send to every connected player except *exclude*."""
        skip = set(exclude)
        with self._lock:
            targets = [
                (pid, conn)
                for pid, conn in self._connections.items()
                if pid not in skip and pid in self._welcomed
            ]
        for player_id, connection in targets:
            try:
                connection.send(message)
            except ProtocolError as exc:
                # Encode failure: the same message would fail for every peer, so
                # report once and stop — nobody's connection is at fault.
                self._emit(EVENT_ERROR, {"code": "encode", "message": str(exc)})
                return
            except OSError:
                self._drop(player_id)

    # -- accepting ---------------------------------------------------------

    def _accept_loop(self) -> None:
        while self._running:
            listener = self._listener
            if listener is None:
                return
            connection = listener.accept()
            if connection is None:
                return
            if not self._running:
                connection.close()
                return
            thread = threading.Thread(
                target=self._serve, args=(connection,), name="session-client", daemon=True
            )
            # Rebind rather than mutate: ``stop`` may be unpacking the old list
            # on another thread. Pruning finished threads keeps a long-running
            # server from accumulating one dead handle per join ever made.
            self._threads = [t for t in self._threads if t.is_alive()] + [thread]
            thread.start()

    def _serve(self, connection: Connection) -> None:
        """One connection's whole life: handshake, then read until it ends."""
        slot: PlayerSlot | None = None
        try:
            slot = self._handshake(connection)
            if slot is None:
                return
            self._read_loop(connection, slot)
        except (OSError, ProtocolError):
            pass  # the peer went away or spoke nonsense; the finally below tidies up
        finally:
            connection.close()
            if slot is not None:
                self._disconnect(slot)

    def _handshake(self, connection: Connection) -> PlayerSlot | None:
        """Validate a peer's :class:`~.protocol.Hello` and seat it, or refuse."""
        connection.set_timeout(HANDSHAKE_TIMEOUT)
        try:
            message = connection.receive()
        except ProtocolError as exc:
            self._refuse(connection, ERROR_MALFORMED, str(exc))
            return None
        if message is None:
            return None
        if not isinstance(message, Hello):
            self._refuse(connection, ERROR_MALFORMED, "expected a hello first")
            return None
        if message.protocol_version != PROTOCOL_VERSION:
            self._refuse(
                connection,
                ERROR_PROTOCOL_VERSION,
                f"this session speaks protocol v{PROTOCOL_VERSION}, "
                f"you speak v{message.protocol_version}",
            )
            return None
        if not tokens_match(message.token, self.state.host_token):
            self._refuse(connection, ERROR_BAD_TOKEN, "that join code is not for this session")
            return None

        replaced: Connection | None = None
        with self._lock:
            slot = self.state.player_by_token(message.player_token)
            is_new = slot is None
            if slot is None:
                if len(self._connections) >= self.max_clients:
                    self._refuse(connection, ERROR_SESSION_FULL, "this session is full")
                    return None
                slot = self.state.add_player(message.display_name.strip() or "Player")
            elif message.display_name.strip():
                slot.display_name = message.display_name.strip()
            # A second client presenting the same slot token takes the seat over;
            # the stale socket is dropped rather than left half-alive.
            replaced = self._connections.get(slot.player_id)
            slot.connected = True
            slot.last_seen = utc_now()
            self._connections[slot.player_id] = connection
            self._welcomed.discard(slot.player_id)
            self._persist()
            welcome = Welcome(
                session_id=self.state.id,
                session_name=self.state.name,
                player_id=slot.player_id,
                player_token=slot.token,
                roster=self.state.roster(),
                history=[
                    roll.to_dict() for roll in self.state.visible_rolls()[-WELCOME_HISTORY_ROLLS:]
                ],
            )

        if replaced is not None and replaced is not connection:
            replaced.close()

        connection.set_timeout(IO_TIMEOUT)
        try:
            connection.send(welcome)
        except (OSError, ProtocolError):
            # The peer vanished between claiming the seat and being told about
            # it; give the seat back rather than leaving a dead socket in it.
            self._forget(slot.player_id, connection)
            return None
        with self._lock:
            if self._connections.get(slot.player_id) is connection:
                self._welcomed.add(slot.player_id)

        if self.mod_fingerprint and message.mod_fingerprint != self.mod_fingerprint:
            # A warning, not a refusal — the session works, but ids coming from
            # the other end may not resolve against the mods loaded here.
            try:
                connection.send(
                    ErrorMessage(
                        code=ERROR_MOD_SKEW,
                        message="the GM and this client load different mods; "
                        "some conditions and effects may not match",
                    )
                )
            except (OSError, ProtocolError):
                return None

        self._emit(EVENT_PLAYER_JOINED, {"player": slot.public_dict(), "new": is_new})
        self._broadcast_roster()
        return slot

    def _refuse(self, connection: Connection, code: str, message: str) -> None:
        """Send a refusal and close, telling the GM's window why."""
        try:
            connection.send(ErrorMessage(code=code, message=message))
            connection.send(Kicked(reason=code))
        except (OSError, ProtocolError):
            pass
        self._emit(
            EVENT_REFUSED, {"code": code, "message": message, "address": list(connection.address)}
        )
        connection.close()

    # -- reading -----------------------------------------------------------

    def _read_loop(self, connection: Connection, slot: PlayerSlot) -> None:
        stamps: list[float] = []
        while self._running and not connection.closed:
            try:
                message = connection.receive()
            except TimeoutError:
                # The shared IO_TIMEOUT expired on an idle recv. Only a stalled
                # *send* means a bad peer; an idle one just has nothing to say.
                continue
            except ProtocolError as exc:
                self._send_quietly(connection, ErrorMessage(code=ERROR_MALFORMED, message=str(exc)))
                return
            if message is None:
                return
            if not self._rate_ok(stamps):
                self._send_quietly(
                    connection,
                    ErrorMessage(code=ERROR_RATE_LIMIT, message="too many messages; slowing down"),
                )
                self._send_quietly(connection, Kicked(reason=ERROR_RATE_LIMIT))
                return
            self._handle(message, slot, connection)

    def _rate_ok(self, stamps: list[float]) -> bool:
        """True while this connection stays inside its per-window budget."""
        now = time.monotonic()
        cutoff = now - RATE_LIMIT_WINDOW
        while stamps and stamps[0] < cutoff:
            stamps.pop(0)
        stamps.append(now)
        return len(stamps) <= RATE_LIMIT_MESSAGES

    def _handle(self, message: Message, slot: PlayerSlot, connection: Connection) -> None:
        if isinstance(message, CharacterSnapshot):
            character = sanitize_snapshot(message.character)
            with self._lock:
                self.state.set_snapshot(slot.player_id, character)
                self._persist()
            self._emit(EVENT_SNAPSHOT, {"player_id": slot.player_id, "character": character})
            self._broadcast_roster()
        elif isinstance(message, RollRequest):
            self._resolve_roll(
                slot,
                label=message.label,
                bonus=message.bonus,
                penalty=message.penalty,
                dc=message.dc,
                # Only the GM may roll unseen; a player's flag is simply ignored.
                hidden=message.hidden and slot.is_gm,
            )
        elif isinstance(message, RemoveRollRequest) and slot.is_gm:
            # Removing a roll is a GM privilege; a player's request is ignored.
            self.remove_roll(message.seq)
        elif isinstance(message, Ping):
            self._send_quietly(connection, Pong(nonce=message.nonce))
        elif isinstance(message, (ApplyCondition, RemoveCondition)) and slot.is_gm:
            # A GM driving a headless server from a remote app: relay the command
            # on to the player it names.
            self.send_to(message.player_id, message)

    # -- rolls -------------------------------------------------------------

    def _resolve_roll(
        self,
        slot: PlayerSlot,
        *,
        label: str,
        bonus: int,
        penalty: int,
        dc: int | None,
        hidden: bool,
    ) -> RollRecord:
        bonus = _clamp(bonus, MAX_ROLL_MODIFIER)
        penalty = _clamp(penalty, MAX_ROLL_MODIFIER)
        dc = None if dc is None else _clamp(dc, MAX_ROLL_MODIFIER)
        label = label[:MAX_LABEL_CHARS]

        result: CheckResult | None = None
        if dc is None:
            die = roll_d20(self._rng)
        else:
            result = resolve_check(bonus - penalty, dc, rng=self._rng)
            die = result.die_roll

        with self._lock:
            record = self.state.record_roll(
                player_id=slot.player_id,
                player_name=slot.display_name,
                die=die,
                bonus=bonus,
                penalty=penalty,
                result=result,
                label=label,
                hidden=hidden,
            )
            self._append_roll(record)

        payload = record.to_dict()
        self._emit(EVENT_ROLL, payload)
        if not record.hidden:
            self.broadcast(RollAdded(roll=payload))
        return record

    # -- housekeeping ------------------------------------------------------

    def _disconnect(self, slot: PlayerSlot) -> None:
        """Mark a slot offline once its socket ends, and tell everyone."""
        with self._lock:
            current = self._connections.get(slot.player_id)
            if current is not None and current.closed:
                self._connections.pop(slot.player_id, None)
                self._welcomed.discard(slot.player_id)
            if slot.player_id in self._connections:
                # The seat was already taken over by a newer connection; that one
                # owns the slot's ``connected`` flag now.
                return
            slot.connected = False
            slot.last_seen = utc_now()
            self._persist()
        self._emit(EVENT_PLAYER_LEFT, {"player": slot.public_dict()})
        self._broadcast_roster()

    def _drop(self, player_id: str) -> None:
        """Forget a connection that failed mid-write."""
        with self._lock:
            connection = self._connections.pop(player_id, None)
            self._welcomed.discard(player_id)
        if connection is not None:
            connection.close()

    def _forget(self, player_id: str, connection: Connection) -> None:
        """Release a seat still held by *connection*, without touching a newer one."""
        with self._lock:
            if self._connections.get(player_id) is connection:
                self._connections.pop(player_id, None)
                self._welcomed.discard(player_id)
            slot = self.state.players.get(player_id)
            if slot is not None and player_id not in self._connections:
                slot.connected = False

    def _broadcast_roster(self) -> None:
        with self._lock:
            players = self.state.roster()
        self._emit(EVENT_ROSTER, {"players": players})
        self.broadcast(Roster(players=players))

    def _send_quietly(self, connection: Connection, message: Message) -> None:
        try:
            connection.send(message)
        except (OSError, ProtocolError):
            pass

    def _persist(self) -> None:
        if not self._persist_enabled:
            return
        try:
            store.save_session(self.state, self._workspace)
        except (OSError, store.SessionStoreError) as exc:
            self._emit(EVENT_ERROR, {"code": "persist", "message": str(exc)})

    def _append_roll(self, record: RollRecord) -> None:
        if not self._persist_enabled:
            return
        try:
            store.append_roll(self.state.id, record, self._workspace)
        except (OSError, store.SessionStoreError) as exc:
            self._emit(EVENT_ERROR, {"code": "persist", "message": str(exc)})

    def _rewrite_rolls(self) -> None:
        """Rewrite the whole roll log after a removal (the log can't delete in place)."""
        if not self._persist_enabled:
            return
        try:
            store.save_session(self.state, self._workspace, write_rolls=True)
        except (OSError, store.SessionStoreError) as exc:
            self._emit(EVENT_ERROR, {"code": "persist", "message": str(exc)})

    def _emit(self, kind: str, payload: dict) -> None:
        """Hand one event to the owner. A misbehaving callback never kills a thread."""
        if self._on_event is None:
            return
        try:
            self._on_event(kind, payload)
        except Exception:  # noqa: BLE001 - a UI callback must not take the server down
            pass


def _clamp(value: int, limit: int) -> int:
    return max(-limit, min(limit, value))

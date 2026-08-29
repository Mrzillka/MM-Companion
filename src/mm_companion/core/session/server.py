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
from .net import IO_TIMEOUT, PEER_TIMEOUT, Connection, TcpTransport, Transport
from .protocol import (
    ERROR_BAD_TOKEN,
    ERROR_MALFORMED,
    ERROR_MOD_SKEW,
    ERROR_PROTOCOL_VERSION,
    ERROR_RATE_LIMIT,
    ERROR_SESSION_FULL,
    MAX_MOD_KEY,
    MAX_MOD_TEXT,
    MAX_SCENE_TEXT,
    PROTOCOL_VERSION,
    REASON_SESSION_CLOSED,
    ApplyCondition,
    CharacterSnapshot,
    ErrorMessage,
    Hello,
    Kicked,
    KickRequest,
    Message,
    ModNote,
    ModRequest,
    ModStateUpdate,
    NoteRequest,
    Ping,
    PlayerSnapshot,
    Pong,
    ProtocolError,
    RemoveCondition,
    RemoveRollRequest,
    RollAdded,
    RollPrompt,
    RollRemoved,
    RollRequest,
    Roster,
    ScenePortrait,
    SceneUpdate,
    SetHeroPoints,
    SetModState,
    SetNpcPaths,
    SetScene,
    SetScenePortrait,
    SetSessionName,
    Welcome,
    sanitize_mod_id,
    sanitize_scene,
    sanitize_scene_portrait,
    sanitize_scene_sources,
    sanitize_snapshot,
    sanitize_spec,
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

#: Longest a note may be. A sentence, not a chat message — the history is a log of
#: what happened, and a card that has to wrap five times pushes the rolls off screen.
MAX_NOTE_CHARS = 200

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
EVENT_PLAYER_JOINED = "player_joined"  # {"player": ..., "new": bool, "adopted": bool}
EVENT_PLAYER_LEFT = "player_left"  # {"player": public slot dict}
EVENT_ROSTER = "roster"  # {"players": [roster dicts — no tokens, no characters]}
EVENT_SNAPSHOT = "snapshot"  # {"player_id", "character"}
EVENT_ROLL = "roll"  # a full roll dict, hidden rolls included
EVENT_ROLL_REMOVED = "roll_removed"  # {"seq"}
EVENT_SCENE = "scene"  # {"entries": [scene entry dicts]}
EVENT_SCENE_PORTRAIT = "scene_portrait"  # {"ref", "portrait"}
EVENT_MOD_STATE = "mod_state"  # {"mod_id", "key", "payload"} — payload None means gone
#: A mod at some seat asking the GM's mod for something. Reaches the hosting
#: process only, and only when it *is* the GM's — see :meth:`SessionServer._forward_mod_request`.
EVENT_MOD_REQUEST = "mod_request"  # {"mod_id", "topic", "player_id", "payload"}
EVENT_REFUSED = "refused"  # {"code", "message", "address"}
EVENT_ERROR = "error"  # {"code", "message"}
#: The listener stopped handing out connections while we still believe we are
#: hosting — a relay whose control link died, say. Nothing is wrong with the
#: session itself and nobody who already joined is affected, but no new player
#: can get in, and without this the GM's window would go on saying "hosting"
#: forever. See :meth:`SessionServer._accept_loop`.
EVENT_LISTENER_LOST = "listener_lost"  # {"session_id"}


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
        on_activate: Callable[[], None] | None = None,
        max_clients: int = DEFAULT_MAX_CLIENTS,
        mod_fingerprint: str = "",
        gm_name: str = "GM",
        gm_in_process: bool = True,
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
        # Called on each arriving connection before its handshake. The seam a
        # supervisor uses to bring a session it had let go idle back into memory;
        # ``None`` for an ordinary server, which is always fully loaded.
        self._on_activate = on_activate
        self._persist_enabled = persist
        self._rng = rng
        self._gm_name = gm_name
        # True when the GM *is* this process (the app hosting its own game), so
        # the GM seat is occupied the moment the server starts. False on a
        # headless host, where the seat waits for someone to claim it with the
        # session's gm token — showing a GM online that nobody is sitting in
        # would be a lie the player cards would faithfully render.
        self._gm_in_process = gm_in_process

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

        # Say the table has closed *first*, while the sockets are still ours.
        # A client that only saw its connection die cannot tell a deliberate end
        # from a sleeping laptop, and spends its whole retry window redialling a
        # session that is not coming back — which is the whole reason this
        # message exists. Clearing ``_running`` before sending would let every
        # reader loop exit and close its own connection out from under us, and a
        # farewell written to a closed socket is dropped without a sound: the
        # failure would be timing-dependent, so it would work in testing and go
        # wrong in the field.
        with self._lock:
            connections = list(self._connections.values())
        for connection in connections:
            self._send_quietly(connection, Kicked(reason=REASON_SESSION_CLOSED))

        self._running = False
        if self._listener is not None:
            self._listener.close()
        with self._lock:
            self._connections.clear()
            self._welcomed.clear()
            for slot in self.state.players.values():
                slot.connected = False
            self._persist()
        for connection in connections:
            # The bytes above are already with the kernel, and a graceful close
            # still delivers them before the FIN.
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
        """The GM's roster slot, created on first use.

        The GM needs a slot whether they drive this server in-process or dial in
        over a socket, so their rolls carry a name and their card appears in the
        roster like anyone else's.

        The seat is marked occupied here only when the GM *is* this process. A
        headless host leaves it empty until someone presents the session's gm
        token, at which point the handshake marks it connected like any other.
        """
        with self._lock:
            slot = next((s for s in self.state.players.values() if s.is_gm), None)
            if slot is None:
                slot = self.state.add_player(self._gm_name, is_gm=True)
            elif self._gm_name and slot.display_name != self._gm_name:
                # A resumed session used to keep whatever name it was saved
                # under, ignoring the one this run was started with.
                slot.display_name = self._gm_name
            if self._gm_in_process:
                # Also on the resumed path: store.load_session clears every
                # connected flag, so without this a restarted session showed its
                # own GM permanently offline.
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
        spec: dict | None = None,
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
            slot, label=label, bonus=bonus, penalty=penalty, dc=dc, hidden=hidden, spec=spec
        )

    def note(self, text: str, *, player_id: str | None = None) -> RollRecord:
        """Write a line in the shared log for the GM (or for *player_id*).

        The counterpart of :meth:`roll` for something that happened rather than
        something rolled — a hero point spent. Like a roll it is the one path in,
        so a :class:`~.protocol.NoteRequest` from a client lands here too, and like
        a roll it is attributed to the seat it came from rather than to whatever
        the text claims.
        """
        with self._lock:
            slot = self.state.players.get(player_id) if player_id else self.gm_slot()
        if slot is None:
            raise KeyError(f"no player {player_id!r}")
        return self._record_note(slot, text)

    def record_mod_note(
        self, mod_id: str, text: str, *, player_id: str | None = None
    ) -> RollRecord | None:
        """Write a mod's line in the shared log for the GM (or for *player_id*).

        The counterpart of :meth:`note` for a line a mod composed rather than a
        person, and the one path in for the same reason: a
        :class:`~.protocol.ModNote` from a dialled-in GM lands here too, so a GM
        hosting on their own laptop and one driving a session on a server put the
        identical record in the identical log.

        Answers ``None`` when the mod id or the text does not survive checking —
        see :meth:`_record_mod_note`.
        """
        with self._lock:
            slot = self.state.players.get(player_id) if player_id else self.gm_slot()
        if slot is None:
            raise KeyError(f"no player {player_id!r}")
        return self._record_mod_note(slot, mod_id, text)

    def prompt_roll(self, spec: dict | None, *, player_id: str | None = None) -> RollRecord | None:
        """Ask the table for a roll on behalf of the GM (or of *player_id*).

        The third of the three ways an entry gets into the log, beside
        :meth:`roll` and :meth:`note`, and the one path in for a
        :class:`~.protocol.RollPrompt` from a client too.
        """
        with self._lock:
            slot = self.state.players.get(player_id) if player_id else self.gm_slot()
        if slot is None:
            raise KeyError(f"no player {player_id!r}")
        return self._record_request(slot, spec)

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

    def set_hero_points(self, player_id: str, value: int) -> bool:
        """Tell one player's client to set their hero-point total on their sheet."""
        return self.send_to(player_id, SetHeroPoints(player_id=player_id, value=value))

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

    # -- the scene ---------------------------------------------------------

    def set_scene(self, entries: object, sources: object = None) -> list[dict]:
        """Replace the shared scene, persist it, and tell the table.

        The GM's window calls this directly when it is hosting; a remote GM's
        :class:`~.protocol.SetScene` lands here too. Returns the sanitized
        entries — what was actually stored — so an in-process caller sees the
        same board everyone else got rather than assuming its own.

        Portraits of entries that have left the scene are dropped here rather
        than by whoever removed them: this is the only place that knows the whole
        new membership, and a session that only ever accumulated pictures would
        grow its ``session.json`` all campaign.
        """
        kept = sanitize_scene(entries)
        with self._lock:
            self.state.scene = kept
            if sources is not None:
                self.state.scene_sources = sanitize_scene_sources(sources)
            live = {entry["ref"] for entry in kept}
            self.state.scene_portraits = {
                ref: portrait for ref, portrait in self.state.scene_portraits.items() if ref in live
            }
            self.state.touch()
            self._persist()
        self._emit(EVENT_SCENE, {"entries": [dict(entry) for entry in kept]})
        self.broadcast(SceneUpdate(entries=[dict(entry) for entry in kept]))
        return kept

    def set_scene_portrait(self, ref: str, portrait: str) -> None:
        """Store one scene entry's thumbnail and pass it on to the table.

        An empty *portrait* clears it. A ``ref`` that is not in the scene is
        still accepted: the GM sends a picture as an entry joins, and the two
        messages are not ordered against each other.
        """
        ref = str(ref)[:MAX_SCENE_TEXT]
        if not ref:
            return
        kept = sanitize_scene_portrait(portrait)
        with self._lock:
            if kept:
                self.state.scene_portraits[ref] = kept
            else:
                self.state.scene_portraits.pop(ref, None)
            self.state.touch()
            self._persist()
        self._emit(EVENT_SCENE_PORTRAIT, {"ref": ref, "portrait": kept})
        self.broadcast(ScenePortrait(ref=ref, portrait=kept))

    def scene(self) -> list[dict]:
        """The scene as it goes on the wire."""
        with self._lock:
            return [dict(entry) for entry in self.state.scene]

    def scene_portraits(self) -> dict[str, str]:
        """Every stored scene thumbnail, by ``ref``."""
        with self._lock:
            return dict(self.state.scene_portraits)

    # -- mod state ---------------------------------------------------------

    def set_mod_state(self, mod_id: str, key: str, payload: object) -> dict | None:
        """Store one mod's entry, persist it, and tell the table.

        The GM's window calls this directly when it is hosting; a remote GM's
        :class:`~.protocol.SetModState` lands here too. Returns what was actually
        stored — ``None`` when the entry was deleted *or* when the payload did not
        survive sanitizing — so an in-process caller sees what everyone else got
        rather than assuming its own. That mattering is the lesson
        :meth:`~mm_companion.ui.session_bridge.SessionBridge.set_scene` already
        learned: reporting a dropped payload as success leaves a mod believing the
        table can see something it cannot.

        The broadcast goes out either way. A deletion is as much news as a change
        — it is what a share toggle turning off looks like from the other side.
        """
        with self._lock:
            kept = self.state.set_mod_state(mod_id, key, payload)
            # Re-read the id and key the state actually used, rather than echoing
            # what arrived: the broadcast has to name the entry as *stored*, or a
            # client would key its copy differently from the server and never
            # manage to overwrite it.
            checked_id = sanitize_mod_id(mod_id)
            checked_key = key[:MAX_MOD_KEY] if isinstance(key, str) and key.strip() else ""
            if checked_id and checked_key:
                self._persist()
        if not checked_id or not checked_key:
            return None
        detail = {"mod_id": checked_id, "key": checked_key, "payload": kept}
        self._emit(EVENT_MOD_STATE, detail)
        self.broadcast(ModStateUpdate(mod_id=checked_id, key=checked_key, payload=kept))
        return kept

    def mod_state(self) -> dict[str, dict[str, dict]]:
        """Every mod's shared state, as it goes on the wire (a deep-enough copy)."""
        with self._lock:
            return {
                mod: {key: dict(payload) for key, payload in entries.items()}
                for mod, entries in self.state.mod_state.items()
            }

    def _forward_mod_request(self, slot: PlayerSlot, message: ModRequest) -> None:
        """Pass one seat's mod request on to the GM's seat, and nowhere else.

        Aimed like :meth:`_forward_snapshot_to_gm`, and unlike everything else a
        mod sends: nothing is stored and nothing is broadcast, because a request
        is one mod talking to one other mod rather than a thing the table is meant
        to see. ``player_id`` is stamped here from the *slot* — never from what
        the sender wrote — so the channel cannot be used to impersonate a seat.

        A request from the GM's own seat is dropped rather than looped back: the
        GM's mod already has whatever it was going to ask itself.
        """
        mod_id = sanitize_mod_id(message.mod_id)
        if not mod_id:
            return
        gm_id = self._gm_id()
        if not gm_id or gm_id == slot.player_id:
            return
        stamped = ModRequest(
            mod_id=mod_id,
            topic=message.topic[:MAX_MOD_TEXT],
            payload=message.payload,
            player_id=slot.player_id,
        )
        # A GM hosting in this process has no connection to send down, so the
        # event is what reaches them; ``send_to`` covers the dialled-in GM.
        self._emit(EVENT_MOD_REQUEST, stamped.to_dict())
        self.send_to(gm_id, stamped)

    def _record_mod_note(self, slot: PlayerSlot, mod_id: str, text: str) -> RollRecord | None:
        """Append a mod's line to the shared log, the way a note is appended.

        Dropped rather than recorded when the mod id does not check out or the
        text is empty — a blank line in the history is worse than no line, and
        unlike a roll there is nothing else on the record to read.
        """
        checked_id = sanitize_mod_id(mod_id)
        line = text.strip()[:MAX_NOTE_CHARS]
        if not checked_id or not line:
            return None
        with self._lock:
            record = self.state.record_mod_note(
                player_id=slot.player_id,
                player_name=slot.display_name,
                mod_id=checked_id,
                text=line,
            )
            self._append_roll(record)

        payload = record.to_dict()
        self._emit(EVENT_ROLL, payload)
        self.broadcast(RollAdded(roll=payload))
        return record

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
                if self._running:
                    # Not our own ``stop`` — the listener gave up under us. A
                    # relay whose control link died does exactly this, and the
                    # session then looks perfectly healthy while nobody on earth
                    # can join it. Say so rather than letting it go quiet.
                    self._emit(EVENT_LISTENER_LOST, {"session_id": self.state.id})
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
            if self._on_activate is not None:
                # Before the handshake, because the Welcome carries the roll
                # history: a supervisor that shed an idle session's history has
                # to put it back before anyone is told what it is.
                try:
                    self._on_activate()
                except Exception as exc:  # noqa: BLE001 - a supervisor bug must
                    # not take the session down with it, exactly as with _emit.
                    self._emit(EVENT_ERROR, {"code": "activate", "message": str(exc)})
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
        # A GM claim is checked before the seat is worked out, and a wrong one is
        # refused rather than downgraded to a player seat. Silently seating a GM
        # as a player would "work" — they would just find their hidden rolls
        # broadcast to the table, which is the one failure that cannot be undone.
        claims_gm = bool(message.gm_token)
        if claims_gm and not tokens_match(message.gm_token, self.state.gm_token):
            self._refuse(connection, ERROR_BAD_TOKEN, "that is not this session's GM token")
            return None

        replaced: Connection | None = None
        adopted = False
        with self._lock:
            slot = self.gm_slot() if claims_gm else self.state.player_by_token(message.player_token)
            if slot is None and not claims_gm:
                # The token missed. Before minting a second seat for someone who
                # is plainly already on the board, let them back into the empty
                # one they name — see ``player_by_id_if_free`` for what that does
                # and does not allow. The Welcome below re-issues the seat's real
                # token, so this client never comes back this way again.
                slot = self.state.player_by_id_if_free(message.player_id)
                adopted = slot is not None
            is_new = slot is None
            if slot is None:
                # The GM's own connection does not take a player's place: their
                # seat is the table's, not one of the chairs around it.
                seated = sum(1 for pid in self._connections if pid != self._gm_id())
                if seated >= self.max_clients:
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
                is_gm=slot.is_gm,
                npc_paths=list(self.state.npc_paths) if slot.is_gm else [],
                scene=[dict(entry) for entry in self.state.scene],
                scene_sources=dict(self.state.scene_sources) if slot.is_gm else {},
                # Everyone's, unlike scene_sources: mod state exists to be seen.
                # It fits in the welcome because it is bounded three ways over
                # (MAX_MOD_IDS x MAX_MOD_KEYS x MAX_MOD_PAYLOAD_CHARS), which is
                # exactly what the scene's portraits are not.
                mod_state={
                    mod: {key: dict(payload) for key, payload in entries.items()}
                    for mod, entries in self.state.mod_state.items()
                },
            )
            # Read under the same lock as the welcome so the pictures cannot
            # describe a scene other than the one just sent; delivered after it.
            portraits = dict(self.state.scene_portraits)

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

        # The scene's pictures, one message each. They are not in the welcome
        # because a welcome already carries a roster and two hundred rolls, and a
        # dozen thumbnails on top of that is how you fail to encode a join. Sent
        # on this connection alone; a failure here costs a placeholder or two,
        # not the seat, so it is swallowed rather than allowed to unseat a player
        # who is otherwise in.
        for ref, portrait in portraits.items():
            try:
                connection.send(ScenePortrait(ref=ref, portrait=portrait))
            except (OSError, ProtocolError):
                break

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

        self._emit(
            EVENT_PLAYER_JOINED,
            {"player": slot.public_dict(), "new": is_new, "adopted": adopted},
        )
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
        # Every client keeps its link warm (see :data:`~.net.KEEPALIVE_INTERVAL`),
        # so silence here really is silence: past PEER_TIMEOUT this peer is gone,
        # however healthy its socket still looks. Returning is enough — ``_serve``
        # closes the connection and calls ``_disconnect``, which flips the slot
        # offline and rebroadcasts the roster, so a ghost card stops claiming to
        # be connected. Kept as a local rather than on the slot: touching
        # ``last_seen`` per keepalive would drag ``_persist`` into a 30 s heartbeat.
        last_rx = time.monotonic()
        while self._running and not connection.closed:
            try:
                message = connection.receive()
            except TimeoutError:
                # The shared IO_TIMEOUT expired on an idle recv — that alone is
                # not a dead peer, it is just the tick we check the deadline on.
                if time.monotonic() - last_rx > PEER_TIMEOUT:
                    return
                continue
            except ProtocolError as exc:
                self._send_quietly(connection, ErrorMessage(code=ERROR_MALFORMED, message=str(exc)))
                return
            if message is None:
                return
            last_rx = time.monotonic()
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
            self._forward_snapshot_to_gm(slot.player_id, character)
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
                spec=message.spec,
            )
        elif isinstance(message, NoteRequest):
            self._record_note(slot, message.text)
        elif isinstance(message, RollPrompt):
            # Deliberately not GM-only: asking the table to roll something is a
            # thing any seat may do, and the entry is attributed to the seat it
            # came from like every other.
            self._record_request(slot, message.spec)
        elif isinstance(message, RemoveRollRequest) and slot.is_gm:
            # Removing a roll is a GM privilege; a player's request is ignored.
            self.remove_roll(message.seq)
        elif isinstance(message, Ping):
            self._send_quietly(connection, Pong(nonce=message.nonce))
        elif isinstance(message, (ApplyCondition, RemoveCondition, SetHeroPoints)) and slot.is_gm:
            # A GM driving a headless server from a remote app: relay the command
            # on to the player it names.
            self.send_to(message.player_id, message)
        elif isinstance(message, KickRequest) and slot.is_gm:
            # Never let a GM kick themselves out of their own session.
            if message.player_id != slot.player_id:
                self.kick(message.player_id, reason=message.reason or "removed by the GM")
        elif isinstance(message, SetSessionName) and slot.is_gm:
            self.set_session_name(message.name)
        elif isinstance(message, SetNpcPaths) and slot.is_gm:
            self.set_npc_paths(message.paths)
        elif isinstance(message, SetScene) and slot.is_gm:
            # The scene is the GM's to author. A player's SetScene is dropped on
            # the floor like their RemoveRollRequest: the board is what everyone
            # is looking at, so it has exactly one writer.
            self.set_scene(message.entries, message.sources)
        elif isinstance(message, SetScenePortrait) and slot.is_gm:
            self.set_scene_portrait(message.ref, message.portrait)
        elif isinstance(message, SetModState) and slot.is_gm:
            # A mod's shared state is the GM's to author, exactly as the scene is.
            # A player's mod says its piece with a ModRequest instead.
            self.set_mod_state(message.mod_id, message.key, message.payload)
        elif isinstance(message, ModNote) and slot.is_gm:
            self._record_mod_note(slot, message.mod_id, message.text)
        elif isinstance(message, ModRequest):
            # Deliberately not GM-only, and the only half of the mod channel that
            # is not: this is how a player's mod reaches the GM's, and it obliges
            # the GM's mod to do nothing at all.
            self._forward_mod_request(slot, message)

    def _forward_snapshot_to_gm(self, player_id: str, character: dict) -> None:
        """Send one player's sheet on to a GM who is dialled in over a socket.

        A no-op when the GM is this process (they read the state directly) or is
        not connected. The snapshot goes to the GM's connection alone — it is
        never broadcast, since no player needs another player's sheet and the
        combined size would not fit in one message anyway.
        """
        gm_id = self._gm_id()
        if not gm_id or gm_id == player_id:
            return
        self.send_to(gm_id, PlayerSnapshot(player_id=player_id, character=character))

    def _gm_id(self) -> str:
        """The GM slot's player id, or ``""`` if the session has no GM seat yet.

        ``send_to`` is a no-op for a seat with no live connection, so this is
        safe to aim at whether or not a remote GM is currently dialled in.
        """
        with self._lock:
            gm = next((s for s in self.state.players.values() if s.is_gm), None)
            return gm.player_id if gm is not None else ""

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
        spec: dict | None = None,
    ) -> RollRecord:
        bonus = _clamp(bonus, MAX_ROLL_MODIFIER)
        penalty = _clamp(penalty, MAX_ROLL_MODIFIER)
        dc = None if dc is None else _clamp(dc, MAX_ROLL_MODIFIER)
        label = label[:MAX_LABEL_CHARS]
        # The one place a client-supplied spec is checked, alongside the other
        # client-supplied values. It is opaque to this module — the server records
        # and rebroadcasts it without ever reading what it means.
        spec = sanitize_spec(spec)

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
                spec=spec,
            )
            self._append_roll(record)

        payload = record.to_dict()
        self._emit(EVENT_ROLL, payload)
        if not record.hidden:
            self.broadcast(RollAdded(roll=payload))
        else:
            # A hidden roll is kept off the wire, but its own author still has to
            # learn what they rolled. An in-process GM reads it from the event
            # above; a GM dialled in over a socket has only this.
            self.send_to(self._gm_id(), RollAdded(roll=payload))
        return record

    def _record_note(self, slot: PlayerSlot, text: str) -> RollRecord:
        """Append a note and publish it, on the same path a resolved roll takes.

        There is nothing to resolve, so this is the whole of it: cap the text, take
        a sequence number, persist, and broadcast. A note is never hidden — it says
        what happened at the table, and hiding is a property of *rolls*.
        """
        with self._lock:
            record = self.state.record_note(
                player_id=slot.player_id,
                player_name=slot.display_name,
                text=text[:MAX_NOTE_CHARS],
            )
            self._append_roll(record)

        payload = record.to_dict()
        self._emit(EVENT_ROLL, payload)
        self.broadcast(RollAdded(roll=payload))
        return record

    def _record_request(self, slot: PlayerSlot, spec: dict | None) -> RollRecord | None:
        """Append a requested roll and publish it, the way a note is published.

        The spec goes through :func:`~.protocol.sanitize_spec` for the reason a
        roll's does — it is client-supplied data that will be rendered on other
        people's screens — and a spec that does not survive it is dropped rather
        than recorded, since a request with nothing to roll is a card with a dead
        button on it. Never hidden: asking in secret would be asking nobody.
        """
        clean = sanitize_spec(spec)
        if clean is None:
            return None
        with self._lock:
            record = self.state.record_request(
                player_id=slot.player_id,
                player_name=slot.display_name,
                label=str(clean.get("label", ""))[:MAX_LABEL_CHARS],
                dc=clean.get("dc"),
                spec=clean,
            )
            self._append_roll(record)

        payload = record.to_dict()
        self._emit(EVENT_ROLL, payload)
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

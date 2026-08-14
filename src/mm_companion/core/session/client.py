"""The session client: a player's app talking to the GM's session.

Pure Python and Qt-free. :meth:`SessionClient.connect` performs the handshake on
the calling thread — it either returns the :class:`~.protocol.Welcome` or raises,
so a caller gets a real answer instead of having to wait for a callback — and
then leaves one reader thread delivering everything that follows to ``on_event``
as ``(kind, payload)`` pairs of plain dicts.

The client never computes a roll. :meth:`request_roll` asks; the server resolves
and broadcasts, and the answer arrives as an :data:`EVENT_ROLL`.

**Staying alive.** That one thread also keeps the link warm: hearing nothing for
:data:`~.net.KEEPALIVE_INTERVAL` it sends a :class:`~.protocol.Ping`, and hearing
nothing for :data:`~.net.PEER_TIMEOUT` it gives up on the connection. The first
is what stops a relay reaping a quiet table mid-session; the second is what makes
a half-open link visible at all, since a black-holed socket otherwise just times
out its ``recv`` forever.

**Coming back.** A connection that ends for a reason worth retrying is redialled
in the background — same thread, backing off along :data:`RECONNECT_DELAYS` for
up to :data:`RECONNECT_WINDOW` — re-presenting the ``player_id``/``player_token``
we already hold, so we land back in the *same* roster seat rather than appearing
as a second player. The states that walk past are published as :data:`EVENT_STATE`.

One rule holds the whole design together: **:data:`EVENT_DISCONNECTED` means the
session is over**, not that a packet went missing. It fires once, on an explicit
:meth:`close`, a terminal refusal (a kick, a bad token, a version mismatch) or the
retry window running out. A blip raises :data:`EVENT_STATE` and nothing else —
which is what keeps a dropped Wi-Fi from tearing the shared roll history out of
the player's dice block and telling them they left.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable

from mm_companion import __version__

from .net import (
    CONNECT_TIMEOUT,
    IO_TIMEOUT,
    KEEPALIVE_INTERVAL,
    PEER_TIMEOUT,
    Connection,
    TcpTransport,
    Transport,
    TransportError,
)
from .protocol import (
    ERROR_BAD_TOKEN,
    ERROR_PROTOCOL_VERSION,
    ERROR_RATE_LIMIT,
    ERROR_SESSION_FULL,
    PROTOCOL_VERSION,
    REASON_SESSION_CLOSED,
    ApplyCondition,
    CharacterSnapshot,
    ErrorMessage,
    Hello,
    Kicked,
    KickRequest,
    Message,
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
    SetHeroPoints,
    SetNpcPaths,
    SetSessionName,
    Welcome,
    sanitize_snapshot,
)

# Event kinds handed to ``on_event``. The payload is always a dict.
EVENT_CONNECTED = "connected"  # the welcome envelope
EVENT_DISCONNECTED = "disconnected"  # {"reason"}
EVENT_ROSTER = "roster"  # {"players": [public slot dicts]}
EVENT_ROLL = "roll"  # one roll dict
EVENT_ROLL_REMOVED = "roll_removed"  # {"seq"}
# Reaches a GM client only. Named to match the hosting side's EVENT_SNAPSHOT so
# the Qt bridge can raise one signal from either half.
EVENT_SNAPSHOT = "snapshot"  # {"player_id", "character"}
EVENT_APPLY_CONDITION = "apply_condition"  # {"player_id", "condition_id", "parameter"}
EVENT_REMOVE_CONDITION = "remove_condition"  # {"player_id", "condition_id", "parameter"}
EVENT_SET_HERO_POINTS = "set_hero_points"  # {"player_id", "value"}
EVENT_ERROR = "error"  # {"code", "message"}
EVENT_KICKED = "kicked"  # {"reason"}
EVENT_PONG = "pong"  # {"nonce"}
#: The connection state changed: ``{"state", "reason", "attempt", "retry_in"}``.
#: Raised for every transition, including the ones a blip walks through and back.
EVENT_STATE = "state"

#: The four states a client's connection can be in. ``reconnecting`` is the one
#: that matters to a caller: there is no socket, so nothing can be sent, but the
#: session has *not* ended and will very likely be back in a few seconds.
STATE_CONNECTING = "connecting"
STATE_ONLINE = "online"
STATE_RECONNECTING = "reconnecting"
STATE_OFFLINE = "offline"

#: How long to wait before each redial. The last value repeats for as long as
#: :data:`RECONNECT_WINDOW` allows. No jitter: a table is at most a handful of
#: clients, so there is no herd to thunder, and a fixed ladder is testable.
RECONNECT_DELAYS = (1.0, 2.0, 5.0, 10.0, 20.0, 30.0)

#: How long we keep trying before calling the session over. Five minutes covers a
#: closed lid, a Wi-Fi handover and a router reboot; past that the player has
#: genuinely left and should rejoin deliberately rather than have an app quietly
#: dialling an address that may not be theirs any more.
RECONNECT_WINDOW = 300.0

#: Refusals there is no point retrying — the answer would be the same in five
#: minutes. Everything else (a closed socket, an unreachable host, a torn frame,
#: our own peer timeout) is a network artefact and gets the full window.
#: ``session_full`` is here on manners as much as logic: hammering a full table
#: for five minutes helps nobody, and the honest answer is "someone must leave".
TERMINAL_CODES = frozenset(
    {ERROR_BAD_TOKEN, ERROR_PROTOCOL_VERSION, ERROR_SESSION_FULL, ERROR_RATE_LIMIT}
)


class SessionClientError(Exception):
    """The join was refused, or the connection could not be made.

    ``code`` is one of the :mod:`.protocol` ``ERROR_*`` constants when the server
    refused us for a reason it named, so the UI can phrase it rather than showing
    raw prose.
    """

    def __init__(self, message: str, code: str = "") -> None:
        super().__init__(message)
        self.code = code


class SessionClient:
    """One player's connection to a session.

    ``player_id`` / ``player_token`` are empty on a first join and remembered
    afterwards; passing them back reclaims the same roster slot (and the same GM
    card) instead of appearing as a second player. A reconnect presents whatever
    the last :class:`~.protocol.Welcome` handed us, so it always lands on the
    right seat without the caller doing anything.

    ``reconnect=False`` turns the retry loop off, so a dropped connection ends the
    session at once. Mainly for tests, which would otherwise have to wait out a
    backoff to observe a disconnect.
    """

    def __init__(
        self,
        host: str,
        port: int,
        *,
        token: str,
        display_name: str,
        player_id: str = "",
        player_token: str = "",
        gm_token: str = "",
        transport: Transport | None = None,
        on_event: Callable[[str, dict], None] | None = None,
        app_version: str = __version__,
        mod_fingerprint: str = "",
        reconnect: bool = True,
    ) -> None:
        self.host = host
        self.port = port
        self.token = token
        self.display_name = display_name
        self.player_id = player_id
        self.player_token = player_token
        self.gm_token = gm_token
        self.app_version = app_version
        self.mod_fingerprint = mod_fingerprint

        self.session_id = ""
        self.session_name = ""
        self.roster: list[dict] = []
        self.history: list[dict] = []
        #: Filled from the Welcome. ``is_gm`` says whether the gm token was
        #: accepted; ``npc_paths`` arrives only for the GM.
        self.is_gm = False
        self.npc_paths: list[str] = []

        #: Where the connection stands right now — one of the ``STATE_*`` values.
        #: Unlike :attr:`connected` this stays truthful across a blip, which is
        #: what a UI should read to decide whether the session is still on.
        self.connection_state = STATE_OFFLINE
        #: Round trip of the last keepalive, in milliseconds, or ``None`` before
        #: one has come back. Nothing depends on it; it is for showing a player.
        self.latency_ms: float | None = None

        self._transport = transport or TcpTransport()
        self._on_event = on_event
        self._reconnect_enabled = bool(reconnect)
        self._connection: Connection | None = None
        self._reader: threading.Thread | None = None
        self._closing = False
        # Waited on between redials, so ``close`` can cut a backoff short instead
        # of leaving a thread to wake up and dial a session we have left.
        self._wake = threading.Event()
        self._ping_nonce = 0
        self._ping_sent_at = 0.0

    # -- lifecycle ---------------------------------------------------------

    @property
    def connected(self) -> bool:
        """Whether there is a live socket *right now*.

        Deliberately narrow: this is False during a reconnect, which is what makes
        :meth:`send` fail honestly mid-blip. For "is this app still in a session",
        read :attr:`connection_state`.
        """
        return self._connection is not None and not self._connection.closed

    def connect(self, timeout: float = CONNECT_TIMEOUT) -> Welcome:
        """Dial the session, complete the handshake, and start reading.

        Raises :class:`SessionClientError` if the server is unreachable, refuses
        the join code, speaks another protocol version, or says anything other
        than a :class:`~.protocol.Welcome` first. The *first* connection is always
        the caller's to see fail — only a connection that was once established is
        retried in the background.
        """
        if self.connected:
            raise SessionClientError("already connected")
        self._closing = False
        self._wake.clear()
        self._set_state(STATE_CONNECTING)
        try:
            message = self._dial_and_handshake(timeout)
        except SessionClientError:
            self._set_state(STATE_OFFLINE)
            raise
        self._set_state(STATE_ONLINE)
        self._emit(EVENT_CONNECTED, message.to_dict())
        self._reader = threading.Thread(target=self._run, name="session-client", daemon=True)
        self._reader.start()
        return message

    def _dial_and_handshake(self, timeout: float = CONNECT_TIMEOUT) -> Welcome:
        """Open one connection and seat ourselves on it, or raise.

        The single handshake in this module: :meth:`connect` and the reconnect
        loop both come through here, so a redial necessarily re-presents the
        ``player_id``/``player_token`` we already hold and lands on the same seat.
        Leaves :attr:`_connection` set and armed for the read loop on success.
        """
        try:
            connection = self._transport.connect(self.host, self.port, timeout=timeout)
        except TransportError as exc:
            raise SessionClientError(str(exc)) from exc

        connection.set_timeout(timeout)
        try:
            connection.send(
                Hello(
                    token=self.token,
                    display_name=self.display_name,
                    protocol_version=PROTOCOL_VERSION,
                    app_version=self.app_version,
                    mod_fingerprint=self.mod_fingerprint,
                    player_id=self.player_id,
                    player_token=self.player_token,
                    gm_token=self.gm_token,
                )
            )
            message = connection.receive()
        except (OSError, ProtocolError) as exc:
            connection.close()
            raise SessionClientError(f"the session did not answer: {exc}") from exc

        if isinstance(message, ErrorMessage):
            connection.close()
            raise SessionClientError(message.message or message.code, message.code)
        if not isinstance(message, Welcome):
            connection.close()
            raise SessionClientError("the session did not send a welcome")

        self.session_id = message.session_id
        self.session_name = message.session_name
        self.player_id = message.player_id
        self.player_token = message.player_token or self.player_token
        self.roster = list(message.roster)
        self.history = list(message.history)
        self.is_gm = bool(message.is_gm)
        self.npc_paths = list(message.npc_paths)

        connection.set_timeout(IO_TIMEOUT)
        self._connection = connection
        return message

    def close(self, reason: str = "closed") -> None:
        """Hang up, emitting :data:`EVENT_DISCONNECTED` once.

        Closing a client that is not connected — never was, closed already, or
        already torn down by the reader after a kick — emits nothing, so the UI
        can call this unconditionally without seeing phantom disconnects. The
        test is :attr:`connection_state`, not whether a socket happens to exist:
        a client in the middle of a reconnect has no socket and is very much
        still in a session it needs to be told it has left.
        """
        was_live = self.connection_state != STATE_OFFLINE
        self._closing = True
        # Set before waking, so a thread parked in a backoff sees the flag the
        # moment it comes round rather than dialling a session we have left.
        self._wake.set()
        connection = self._connection
        self._connection = None
        if connection is not None:
            connection.close()
        reader = self._reader
        self._reader = None
        if reader is not None and reader is not threading.current_thread():
            reader.join(timeout=2.0)
        self.connection_state = STATE_OFFLINE
        if was_live:
            self._emit(EVENT_STATE, {"state": STATE_OFFLINE, "reason": reason})
            self._emit(EVENT_DISCONNECTED, {"reason": reason})

    def __enter__(self) -> SessionClient:
        self.connect()
        return self

    def __exit__(self, *_exc: object) -> None:
        if self.connected:
            self.close()

    # -- sending -----------------------------------------------------------

    def send(self, message: Message) -> bool:
        """Send one message; False when there is no live connection or it failed."""
        connection = self._connection
        if connection is None or connection.closed:
            return False
        try:
            connection.send(message)
        except (OSError, ProtocolError) as exc:
            self._emit(EVENT_ERROR, {"code": "send", "message": str(exc)})
            return False
        return True

    def _send_quiet(self, message: Message) -> bool:
        """Send without raising an error event — for the keepalive.

        A failed :class:`~.protocol.Ping` is not something to report: it means the
        connection has ended, and the read loop is about to say so properly.
        Reporting it as well would put a bare socket error in front of a player
        every time their Wi-Fi hiccuped.
        """
        connection = self._connection
        if connection is None or connection.closed:
            return False
        try:
            connection.send(message)
        except (OSError, ProtocolError):
            return False
        return True

    def send_snapshot(self, character: dict) -> bool:
        """Push the live character. Sanitized here so no caller can forget to."""
        return self.send(CharacterSnapshot(character=sanitize_snapshot(character)))

    def request_roll(
        self,
        label: str = "",
        *,
        bonus: int = 0,
        penalty: int = 0,
        dc: int | None = None,
        hidden: bool = False,
        spec: dict | None = None,
    ) -> bool:
        """Ask the server to roll. The result comes back as :data:`EVENT_ROLL`."""
        return self.send(
            RollRequest(label=label, bonus=bonus, penalty=penalty, dc=dc, hidden=hidden, spec=spec)
        )

    def post_note(self, text: str) -> bool:
        """Ask the server to write *text* in the shared log (no dice involved).

        Comes back as :data:`EVENT_ROLL` like a roll does — one history, one feed —
        carrying ``kind="note"``.
        """
        return self.send(NoteRequest(text=text))

    def prompt_roll(self, spec: dict) -> bool:
        """Ask the table to roll something, without rolling it.

        Comes back as :data:`EVENT_ROLL` like a roll and a note do — one history,
        one feed — carrying ``kind="request"`` and the spec that makes its button.
        """
        return self.send(RollPrompt(spec=spec))

    def request_remove_roll(self, seq: int) -> bool:
        """Ask the server to drop one roll from the log (honored only for the GM).

        The removal comes back as :data:`EVENT_ROLL_REMOVED` once the server applies
        it, the same as it reaches every other client.
        """
        return self.send(RemoveRollRequest(seq=seq))

    def ping(self, nonce: int = 0) -> bool:
        """Keepalive; the answer arrives as :data:`EVENT_PONG`."""
        return self.send(Ping(nonce=nonce))

    # -- GM commands -------------------------------------------------------
    #
    # Each of these is honored by the server only for the seat that presented the
    # session's gm token; from a player they are ignored. They are *requests*, so
    # a True return means the bytes left, not that the server agreed.

    def apply_condition(self, player_id: str, condition_id: str, parameter: str | None = None):
        """Put a condition on one player's live sheet."""
        return self.send(
            ApplyCondition(player_id=player_id, condition_id=condition_id, parameter=parameter)
        )

    def remove_condition(self, player_id: str, condition_id: str, parameter: str | None = None):
        """Take a condition back off one player's live sheet."""
        return self.send(
            RemoveCondition(player_id=player_id, condition_id=condition_id, parameter=parameter)
        )

    def set_hero_points(self, player_id: str, value: int) -> bool:
        """Set one player's hero-point total on their live sheet."""
        return self.send(SetHeroPoints(player_id=player_id, value=int(value)))

    def request_kick(self, player_id: str, reason: str = "") -> bool:
        """Remove a player from the session, dropping their slot."""
        return self.send(KickRequest(player_id=player_id, reason=reason))

    def set_session_name(self, name: str) -> bool:
        """Rename the session; the new name reaches everyone on the next roster."""
        return self.send(SetSessionName(name=name))

    def set_npc_paths(self, paths: list[str]) -> bool:
        """Store the NPC cast list on the server so it follows the session."""
        return self.send(SetNpcPaths(paths=list(paths)))

    # -- reading -----------------------------------------------------------

    def _run(self) -> None:
        """The reader thread's whole life: read, and redial while it is worth it."""
        deadline: float | None = None
        while True:
            reason, terminal = self._read_until_end()
            if self._closing or terminal or not self._reconnect_enabled:
                break
            # The window is measured from the *first* failure of a run of them, so
            # a link that flaps every four minutes cannot retry forever; a redial
            # that actually succeeds clears it and buys a fresh five minutes.
            if deadline is None:
                deadline = time.monotonic() + RECONNECT_WINDOW
            given_up = self._reconnect(reason, deadline)
            if given_up is None:
                deadline = None
                continue
            reason = given_up
            break
        if self._closing:
            return
        self.connection_state = STATE_OFFLINE
        self._emit(EVENT_STATE, {"state": STATE_OFFLINE, "reason": reason})
        self._emit(EVENT_DISCONNECTED, {"reason": reason})

    def _read_until_end(self) -> tuple[str, bool]:
        """Read one connection until it ends. Returns ``(reason, terminal)``.

        *terminal* means there is no point redialling — we were kicked, so the
        seat is gone. Everything else here is a network artefact, including a
        torn frame: the retry window is what bounds the damage if the server
        really is broken.
        """
        connection = self._connection
        reason = "closed"
        terminal = False
        # Silence is measured from the last thing *received*, never from the last
        # thing sent: pinging off our own sends would let a link that is dead in
        # only one direction suppress its own probes forever.
        last_rx = time.monotonic()
        last_ping = 0.0
        try:
            while connection is not None and not connection.closed:
                try:
                    message = connection.receive()
                except TimeoutError:
                    now = time.monotonic()
                    if now - last_rx > PEER_TIMEOUT:
                        # Three keepalives with nothing back. The socket may still
                        # look open — that is exactly the failure this catches.
                        reason = "timeout"
                        break
                    if (
                        now - last_rx >= KEEPALIVE_INTERVAL
                        and now - last_ping >= KEEPALIVE_INTERVAL
                    ):
                        last_ping = now
                        self._ping_nonce += 1
                        self._ping_sent_at = now
                        if not self._send_quiet(Ping(nonce=self._ping_nonce)):
                            reason = "closed"
                            break
                    continue
                except ProtocolError as exc:
                    self._emit(EVENT_ERROR, {"code": "malformed", "message": str(exc)})
                    reason = "malformed"
                    break
                if message is None:
                    reason = "closed"
                    break
                last_rx = time.monotonic()
                ending = self._dispatch(message)
                if ending is not None:
                    reason = ending
                    terminal = True
                    break
        except OSError as exc:
            reason = str(exc) if not self._closing else "closed"
        self._connection = None
        if connection is not None:
            connection.close()
        return reason, terminal

    def _reconnect(self, reason: str, deadline: float) -> str | None:
        """Redial until it works, the window closes, or the answer is final.

        Returns ``None`` once we are back on, or the reason the session is over.
        """
        attempt = 0
        while not self._closing:
            delay = RECONNECT_DELAYS[min(attempt, len(RECONNECT_DELAYS) - 1)]
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return "timeout"
            attempt += 1
            self._set_state(
                STATE_RECONNECTING,
                reason=reason,
                attempt=attempt,
                retry_in=min(delay, remaining),
            )
            # Waiting on the event rather than sleeping is what lets ``close``
            # cut a 30 s backoff short instead of blocking a window from closing.
            if self._wake.wait(min(delay, remaining)) or self._closing:
                return reason
            try:
                message = self._dial_and_handshake()
            except SessionClientError as exc:
                if exc.code in TERMINAL_CODES:
                    # The session said no for a reason five more minutes will not
                    # change. Report it as the reason the session ended.
                    self._emit(EVENT_ERROR, {"code": exc.code, "message": str(exc)})
                    return exc.code
                reason = str(exc)
                continue
            self._set_state(STATE_ONLINE)
            # The same event a first join raises, carrying a fresh Welcome: the
            # roster and the roll history repaint from it, so a caller needs no
            # separate "you are back" path.
            self._emit(EVENT_CONNECTED, message.to_dict())
            return None
        return reason

    def _set_state(self, state: str, **detail: object) -> None:
        self.connection_state = state
        self._emit(EVENT_STATE, {"state": state, **detail})

    def _dispatch(self, message: Message) -> str | None:
        """Turn one message into an event.

        Returns the reason the session is over, or ``None`` to keep reading. A
        reason from here is always *terminal* — there is no seat to redial to.
        """
        if isinstance(message, Roster):
            self.roster = list(message.players)
            self._emit(EVENT_ROSTER, {"players": self.roster})
        elif isinstance(message, RollAdded):
            self.history.append(message.roll)
            self._emit(EVENT_ROLL, message.roll)
        elif isinstance(message, RollRemoved):
            self.history = [r for r in self.history if r.get("seq") != message.seq]
            self._emit(EVENT_ROLL_REMOVED, {"seq": message.seq})
        elif isinstance(message, PlayerSnapshot):
            self._emit(
                EVENT_SNAPSHOT,
                {"player_id": message.player_id, "character": dict(message.character)},
            )
        elif isinstance(message, ApplyCondition):
            self._emit(EVENT_APPLY_CONDITION, message.to_dict())
        elif isinstance(message, RemoveCondition):
            self._emit(EVENT_REMOVE_CONDITION, message.to_dict())
        elif isinstance(message, SetHeroPoints):
            self._emit(EVENT_SET_HERO_POINTS, message.to_dict())
        elif isinstance(message, ErrorMessage):
            self._emit(EVENT_ERROR, {"code": message.code, "message": message.message})
        elif isinstance(message, Pong):
            if message.nonce and message.nonce == self._ping_nonce and self._ping_sent_at:
                self.latency_ms = (time.monotonic() - self._ping_sent_at) * 1000.0
            self._emit(EVENT_PONG, {"nonce": message.nonce})
        elif isinstance(message, Kicked):
            if message.reason == REASON_SESSION_CLOSED:
                # The table closed; nobody was removed. Deliberately *not* an
                # EVENT_KICKED, which every UI phrases as "the GM removed you".
                return REASON_SESSION_CLOSED
            self._emit(EVENT_KICKED, {"reason": message.reason})
            return "kicked"
        return None

    def _emit(self, kind: str, payload: dict) -> None:
        if self._on_event is None:
            return
        try:
            self._on_event(kind, payload)
        except Exception:  # noqa: BLE001 - a UI callback must not kill the reader
            pass

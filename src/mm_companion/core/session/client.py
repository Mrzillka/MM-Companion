"""The session client: a player's app talking to the GM's session.

Pure Python and Qt-free. :meth:`SessionClient.connect` performs the handshake on
the calling thread — it either returns the :class:`~.protocol.Welcome` or raises,
so a caller gets a real answer instead of having to wait for a callback — and
then leaves one reader thread delivering everything that follows to ``on_event``
as ``(kind, payload)`` pairs of plain dicts.

The client never computes a roll. :meth:`request_roll` asks; the server resolves
and broadcasts, and the answer arrives as an :data:`EVENT_ROLL`.
"""

from __future__ import annotations

import threading
from collections.abc import Callable

from mm_companion import __version__

from .net import (
    CONNECT_TIMEOUT,
    IO_TIMEOUT,
    Connection,
    TcpTransport,
    Transport,
    TransportError,
)
from .protocol import (
    PROTOCOL_VERSION,
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
    card) instead of appearing as a second player.
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

        self._transport = transport or TcpTransport()
        self._on_event = on_event
        self._connection: Connection | None = None
        self._reader: threading.Thread | None = None
        self._closing = False

    # -- lifecycle ---------------------------------------------------------

    @property
    def connected(self) -> bool:
        return self._connection is not None and not self._connection.closed

    def connect(self, timeout: float = CONNECT_TIMEOUT) -> Welcome:
        """Dial the session, complete the handshake, and start reading.

        Raises :class:`SessionClientError` if the server is unreachable, refuses
        the join code, speaks another protocol version, or says anything other
        than a :class:`~.protocol.Welcome` first.
        """
        if self.connected:
            raise SessionClientError("already connected")
        self._closing = False
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
        self._emit(EVENT_CONNECTED, message.to_dict())
        self._reader = threading.Thread(target=self._read_loop, name="session-client", daemon=True)
        self._reader.start()
        return message

    def close(self, reason: str = "closed") -> None:
        """Hang up, emitting :data:`EVENT_DISCONNECTED` once.

        Closing a client that is not connected — never was, closed already, or
        already torn down by the reader after a kick — emits nothing, so the UI
        can call this unconditionally without seeing phantom disconnects.
        """
        self._closing = True
        connection = self._connection
        self._connection = None
        if connection is not None:
            connection.close()
        reader = self._reader
        self._reader = None
        if reader is not None and reader is not threading.current_thread():
            reader.join(timeout=2.0)
        if connection is not None:
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

    def _read_loop(self) -> None:
        connection = self._connection
        reason = "closed"
        try:
            while connection is not None and not connection.closed:
                try:
                    message = connection.receive()
                except TimeoutError:
                    # IO_TIMEOUT expired on an idle recv; a quiet table is not a
                    # dead server. Only a stalled send means trouble.
                    continue
                except ProtocolError as exc:
                    self._emit(EVENT_ERROR, {"code": "malformed", "message": str(exc)})
                    reason = "malformed"
                    break
                if message is None:
                    reason = "closed"
                    break
                if self._dispatch(message):
                    reason = "kicked"
                    break
        except OSError as exc:
            reason = str(exc) if not self._closing else "closed"
        if not self._closing:
            self._connection = None
            if connection is not None:
                connection.close()
            self._emit(EVENT_DISCONNECTED, {"reason": reason})

    def _dispatch(self, message: Message) -> bool:
        """Turn one message into an event. Returns True when we have been kicked."""
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
            self._emit(EVENT_PONG, {"nonce": message.nonce})
        elif isinstance(message, Kicked):
            self._emit(EVENT_KICKED, {"reason": message.reason})
            return True
        return False

    def _emit(self, kind: str, payload: dict) -> None:
        if self._on_event is None:
            return
        try:
            self._on_event(kind, payload)
        except Exception:  # noqa: BLE001 - a UI callback must not kill the reader
            pass

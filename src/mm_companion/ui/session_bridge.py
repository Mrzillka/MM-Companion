"""Qt's view of an online session: core callbacks in, Qt signals out.

:mod:`mm_companion.core.session` is deliberately Qt-free — the server and the
client hand every event to an ``on_event(kind, payload)`` callback, called from
whichever worker thread the socket lives on. This module is the one place that
translates those callbacks into signals, so the rest of ``ui/`` never touches a
socket, a thread, or a raw event string.

**Threading.** A :class:`SessionBridge` is created on the GUI thread, so a
``Signal.emit`` from a reader thread is dispatched with ``AutoConnection`` — Qt
compares the emitting thread against the *receiver's* thread and queues the call
onto the GUI event loop. Worker threads may therefore emit directly, and every
slot runs where widgets can be touched. Payloads are plain dicts and lists built
fresh by the core layer, so nothing is shared across the hand-off; the signals
carry them as ``object`` rather than ``dict``/``list`` because those map onto
``QVariantMap``/``QVariantList`` and would flatten nested values and ``None``.

**Publishing is off the GUI thread.** :meth:`SessionBridge.publish` runs
:func:`~mm_companion.core.session.discovery.publish_session` — SSDP multicast and
a couple of HTTP round trips to the router, seconds in the bad case — on a worker
thread, and reports back with :attr:`SessionBridge.published`.

:func:`active_session` / :func:`set_active_session` hold the process-wide bridge,
so a character sheet or the dice roller can find the live session without one
being threaded through every constructor.
"""

from __future__ import annotations

import threading

from PySide6.QtCore import QObject, Signal

from mm_companion.core import mods, storage
from mm_companion.core.session import client as session_client
from mm_companion.core.session import discovery, store
from mm_companion.core.session import relay as session_relay
from mm_companion.core.session import server as session_server
from mm_companion.core.session.model import SessionState, new_session
from mm_companion.core.session.net import DEFAULT_PORT

#: How long :meth:`SessionBridge.stop` waits for a publish still in flight.
_PUBLISH_JOIN_TIMEOUT = 10.0


class SessionBridgeError(Exception):
    """The bridge was asked for something its current state cannot do."""


class SessionBridge(QObject):
    """One live session — hosted or joined — as a Qt object.

    A bridge is either *hosting* (it owns a
    :class:`~mm_companion.core.session.server.SessionServer`) or *joined* (it owns
    a :class:`~mm_companion.core.session.client.SessionClient`), never both. The
    signals are the same either way where the concept exists on both sides
    (:attr:`rosterChanged`, :attr:`rollAdded`, :attr:`error`), which is what lets
    a shared widget — the roll history, later — be written once.
    """

    #: The server bound and started listening: ``(host, port)`` as bound, which
    #: is not necessarily what a player types (see :attr:`published`).
    started = Signal(str, int)
    #: Hosting stopped.
    stopped = Signal()
    #: A :class:`~mm_companion.core.session.discovery.Reachability` — where the
    #: session can be reached from, and the prose to show the GM about it.
    published = Signal(object)

    #: Joined a session; carries the welcome envelope as a dict.
    connected = Signal(object)
    #: Left, dropped, or kicked; carries the reason.
    disconnected = Signal(str)

    #: The roster, as a list of roster dicts (no tokens, no characters).
    rosterChanged = Signal(object)
    #: One resolved roll, as a dict. On the host this includes hidden GM rolls.
    rollAdded = Signal(object)
    #: One roll removed from the log, by its ``seq`` (int). GM-driven either side.
    rollRemoved = Signal(int)
    #: The whole roll history at once — on attach, and on a fresh join.
    historyReplaced = Signal(object)

    #: A player took a seat (``{"player": ..., "new": bool}``), host side only.
    playerJoined = Signal(object)
    #: A player's socket ended (``{"player": ...}``), host side only.
    playerLeft = Signal(object)
    #: ``(player_id, character)`` — a player pushed their live sheet, host side only.
    snapshotReceived = Signal(str, object)
    #: A join was refused (``{"code", "message", "address"}``), host side only.
    refused = Signal(object)

    #: ``("apply" | "remove", payload)`` — the GM told this client to change a
    #: condition on its live sheet. Client side only.
    conditionCommand = Signal(str, object)
    #: ``value`` — the GM set this client's hero-point total. Client side only.
    heroPointsCommand = Signal(int)
    #: The GM dropped us; carries the reason. Client side only.
    kicked = Signal(str)

    #: ``(code, message)`` — anything that went wrong on either side.
    error = Signal(str, str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._server: session_server.SessionServer | None = None
        self._client: session_client.SessionClient | None = None
        self._reachability: discovery.Reachability | None = None
        self._relay: session_relay.RelayTransport | None = None
        self._publish_thread: threading.Thread | None = None
        # The reachability probe, indirected so a test can swap the network for a
        # canned answer without patching the discovery module globally.
        self._publish_session = discovery.publish_session

    # -- state -------------------------------------------------------------

    @property
    def hosting(self) -> bool:
        return self._server is not None and self._server.running

    @property
    def joined(self) -> bool:
        return self._client is not None and self._client.connected

    @property
    def server(self) -> session_server.SessionServer | None:
        return self._server

    @property
    def client(self) -> session_client.SessionClient | None:
        return self._client

    @property
    def state(self) -> SessionState | None:
        """The hosted session's state; ``None`` when this bridge is a client."""
        return self._server.state if self._server is not None else None

    @property
    def reachability(self) -> discovery.Reachability | None:
        """The last :meth:`publish` answer, or ``None`` before one arrives."""
        return self._reachability

    @property
    def relaying(self) -> bool:
        """True when this session is hosted through a relay rather than directly."""
        return self._relay is not None

    def join_code(self) -> str:
        """The code to share, or ``""`` before the session is published."""
        if self._server is None or self._reachability is None:
            return ""
        return self._reachability.join_code(self._server.state.host_token)

    def own_player_id(self) -> str:
        """This app's own seat in the session — the GM's slot, or ours as a player."""
        if self._server is not None:
            return self._server.gm_slot().player_id
        if self._client is not None:
            return self._client.player_id
        return ""

    def history(self) -> list[dict]:
        """The roll history as it stands right now, for a widget attaching late.

        The host's is the whole log, hidden rolls included; a player's is what the
        :class:`~mm_companion.core.session.protocol.Welcome` carried plus whatever
        has been broadcast since. A widget built after the join would otherwise
        start empty and only fill from the next roll onwards.
        """
        if self._server is not None:
            return self._server.history()
        if self._client is not None:
            return list(self._client.history)
        return []

    # -- rolling -----------------------------------------------------------

    def request_roll(
        self,
        *,
        label: str = "",
        bonus: int = 0,
        penalty: int = 0,
        dc: int | None = None,
        hidden: bool = False,
    ) -> bool:
        """Put one roll to the session, whichever end of it this app is on.

        The server always resolves — the host calls it in-process, a player asks
        over the wire — so neither side ever supplies its own die. The answer
        arrives as :attr:`rollAdded` either way, which is what lets the roller
        treat hosting and joining identically. Returns False when there is no
        live session to roll in.

        ``hidden`` is honoured only for the host; the server ignores the flag on a
        player's request rather than trusting it.
        """
        if self._server is not None:
            self._server.roll(label=label, bonus=bonus, penalty=penalty, dc=dc, hidden=hidden)
            return True
        if self._client is not None:
            return self._client.request_roll(
                label, bonus=bonus, penalty=penalty, dc=dc, hidden=hidden
            )
        return False

    def remove_roll(self, seq: int) -> bool:
        """Drop one roll from the shared log (a GM action).

        The host removes it in-process; a GM driving a headless server asks over the
        wire. Either way the removal comes back as :attr:`rollRemoved`. Returns False
        when there is no live session.
        """
        if self._server is not None:
            return self._server.remove_roll(seq)
        if self._client is not None:
            return self._client.request_remove_roll(seq)
        return False

    def kick(self, player_id: str, reason: str = "") -> bool:
        """Remove a player from the session (a GM action). Returns whether a seat
        was actually removed.

        Only the in-process host can do this — the host owns the server and every
        connection. A player, or a GM driving a headless server over the wire, has
        no server here, so this is a no-op that returns False.
        """
        if self._server is None:
            return False
        return self._server.kick(player_id, reason)

    # -- hosting -----------------------------------------------------------

    def host(
        self,
        state: SessionState | None = None,
        *,
        port: int = DEFAULT_PORT,
        bind: str = "0.0.0.0",
        gm_name: str = "GM",
        relay_url: str = "",
    ) -> tuple[str, int]:
        """Start hosting *state* (a fresh session when omitted) and return the address.

        The bound address is not what players type — the join code comes from
        :meth:`publish`, which is what decides whether they need the LAN address,
        a public one, or a tunnel's.

        ``relay_url`` hosts through a relay instead of a listening socket: the GM's
        app dials *out* to it, as every player does, which is the only thing that
        works from behind carrier-grade NAT. It is the second rung of the ladder —
        the caller tries a direct host first, because a direct connection costs the
        relay nothing.
        """
        if self._server is not None:
            raise SessionBridgeError("this bridge is already hosting a session")
        if self._client is not None:
            raise SessionBridgeError("this bridge is joined to someone else's session")

        state = state if state is not None else new_session()
        transport = None
        if relay_url:
            transport = session_relay.RelayTransport(session_relay.relay_url(relay_url, state.id))
        server = session_server.SessionServer(
            state,
            host=bind,
            port=int(port),
            transport=transport,
            on_event=self._on_server_event,
            mod_fingerprint=mods.stack_fingerprint(),
            gm_name=gm_name,
        )
        address = server.start()
        self._server = server
        self._relay = transport
        _remember_session(state.id)
        self.historyReplaced.emit(server.history())
        self.rosterChanged.emit(server.roster())
        return address

    def publish(
        self, *, manual_host: str = "", external_port: int = 0, use_upnp: bool = True
    ) -> None:
        """Work out how the world reaches this session, off the GUI thread.

        Emits :attr:`published` with a
        :class:`~mm_companion.core.session.discovery.Reachability` — which never
        raises, so there is exactly one outcome to render, success or not.
        ``manual_host``/``external_port`` are the tunnel (or hand-forwarded)
        address the GM typed: discovery is skipped and the code points there.
        """
        if self._server is None:
            raise SessionBridgeError("nothing is being hosted, so there is nothing to publish")
        if self._relay is not None:
            # Nothing to probe: the relay accepted the session or hosting would
            # have failed, and the join code points at the relay either way.
            reachability = discovery.relay_reachability(self._relay.url, self._relay.relay.port)
            self._reachability = reachability
            self.published.emit(reachability)
            return
        port = self._server.address[1]
        thread = threading.Thread(
            target=self._publish_worker,
            args=(port, manual_host, int(external_port), use_upnp),
            name="session-publish",
            daemon=True,
        )
        self._publish_thread = thread
        thread.start()

    def _publish_worker(
        self, port: int, manual_host: str, external_port: int, use_upnp: bool
    ) -> None:
        reachability = self._publish_session(
            port,
            manual_host=manual_host,
            external_port=external_port,
            use_upnp=use_upnp,
        )
        self._reachability = reachability
        self.published.emit(reachability)

    # -- joining -----------------------------------------------------------

    def join(
        self,
        code: discovery.JoinCode,
        display_name: str,
        *,
        player_id: str = "",
        player_token: str = "",
    ) -> session_client.SessionClient:
        """Dial a session from a decoded join code and complete the handshake.

        The handshake runs on the calling thread — the caller gets a real answer
        (or a :class:`~mm_companion.core.session.client.SessionClientError`)
        rather than having to wait for a signal — and everything after it arrives
        as signals.
        """
        if self._server is not None:
            raise SessionBridgeError("this bridge is hosting; stop that first")
        if self._client is not None:
            raise SessionBridgeError("this bridge is already joined to a session")

        client = session_client.SessionClient(
            code.host,
            code.port,
            token=code.token,
            display_name=display_name,
            player_id=player_id,
            player_token=player_token,
            transport=discovery.transport_for(code.host),
            on_event=self._on_client_event,
            mod_fingerprint=mods.stack_fingerprint(),
        )
        self._client = client
        try:
            client.connect()
        except Exception:
            self._client = None
            raise
        _remember_session(client.session_id)
        return client

    # -- teardown ----------------------------------------------------------

    def stop(self) -> None:
        """Stop hosting or leave the session, and undo any port mapping taken out."""
        thread, self._publish_thread = self._publish_thread, None
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=_PUBLISH_JOIN_TIMEOUT)

        client, self._client = self._client, None
        if client is not None:
            client.close()

        server, self._server = self._server, None
        self._relay = None
        if server is not None:
            server.stop()

        reachability, self._reachability = self._reachability, None
        if reachability is not None and reachability.mapping is not None:
            # Releasing talks to the router over HTTP, so it must not sit on the
            # GUI thread while a window is closing. Nothing waits on the answer:
            # a mapping we fail to remove expires or is overwritten next time.
            threading.Thread(
                target=reachability.release, name="session-unpublish", daemon=True
            ).start()

    # -- event translation -------------------------------------------------

    def _on_server_event(self, kind: str, payload: dict) -> None:
        if kind == session_server.EVENT_STARTED:
            self.started.emit(str(payload.get("host", "")), int(payload.get("port", 0)))
        elif kind == session_server.EVENT_STOPPED:
            self.stopped.emit()
        elif kind == session_server.EVENT_ROSTER:
            self.rosterChanged.emit(payload.get("players", []))
        elif kind == session_server.EVENT_PLAYER_JOINED:
            self.playerJoined.emit(payload)
        elif kind == session_server.EVENT_PLAYER_LEFT:
            self.playerLeft.emit(payload)
        elif kind == session_server.EVENT_SNAPSHOT:
            self.snapshotReceived.emit(
                str(payload.get("player_id", "")), payload.get("character", {})
            )
        elif kind == session_server.EVENT_ROLL:
            self.rollAdded.emit(payload)
        elif kind == session_server.EVENT_ROLL_REMOVED:
            self.rollRemoved.emit(int(payload.get("seq", 0)))
        elif kind == session_server.EVENT_REFUSED:
            self.refused.emit(payload)
        elif kind == session_server.EVENT_ERROR:
            self.error.emit(str(payload.get("code", "")), str(payload.get("message", "")))

    def _on_client_event(self, kind: str, payload: dict) -> None:
        if kind == session_client.EVENT_CONNECTED:
            self.connected.emit(payload)
            self.rosterChanged.emit(payload.get("roster", []))
            self.historyReplaced.emit(payload.get("history", []))
        elif kind == session_client.EVENT_DISCONNECTED:
            self.disconnected.emit(str(payload.get("reason", "")))
        elif kind == session_client.EVENT_ROSTER:
            self.rosterChanged.emit(payload.get("players", []))
        elif kind == session_client.EVENT_ROLL:
            self.rollAdded.emit(payload)
        elif kind == session_client.EVENT_ROLL_REMOVED:
            self.rollRemoved.emit(int(payload.get("seq", 0)))
        elif kind == session_client.EVENT_APPLY_CONDITION:
            self.conditionCommand.emit("apply", payload)
        elif kind == session_client.EVENT_REMOVE_CONDITION:
            self.conditionCommand.emit("remove", payload)
        elif kind == session_client.EVENT_SET_HERO_POINTS:
            self.heroPointsCommand.emit(int(payload.get("value", 0)))
        elif kind == session_client.EVENT_KICKED:
            self.kicked.emit(str(payload.get("reason", "")))
        elif kind == session_client.EVENT_ERROR:
            self.error.emit(str(payload.get("code", "")), str(payload.get("message", "")))


# --------------------------------------------------------------------------
# The process-wide session
# --------------------------------------------------------------------------

_active: SessionBridge | None = None


def active_session() -> SessionBridge | None:
    """The bridge for the session this app is in, or ``None``."""
    return _active


def set_active_session(bridge: SessionBridge | None) -> None:
    """Publish (or clear) the process-wide session bridge."""
    global _active
    _active = bridge


def live_session() -> SessionBridge | None:
    """The active bridge, but only while it actually has a session going.

    A bridge exists from the moment GM Mode opens and outlives a stopped host or
    a dropped join, so "is there a bridge" is the wrong question for a widget
    deciding whether to roll through the session or by itself. This asks the one
    that matters.
    """
    bridge = _active
    if bridge is None or not (bridge.hosting or bridge.joined):
        return None
    return bridge


# --------------------------------------------------------------------------
# Resuming
# --------------------------------------------------------------------------


def last_session() -> SessionState | None:
    """Reload the session this app was last attached to, if it is still on disk."""
    session_id = storage.load_settings().get("session_last_id")
    if not isinstance(session_id, str) or not session_id:
        return None
    try:
        return store.load_session(session_id)
    except (OSError, store.SessionStoreError):
        return None


def _remember_session(session_id: str) -> None:
    """Record the session to resume next launch; a failure here is not worth raising."""
    if not session_id:
        return
    try:
        storage.update_settings(session_last_id=session_id)
    except OSError:
        pass

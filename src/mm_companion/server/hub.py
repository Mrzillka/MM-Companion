"""The session hub: one box holding every session, hosting them all at once.

Where :class:`~mm_companion.core.session.server.SessionServer` is one table, the
hub is the venue. It exists so a session outlives the GM's laptop: the sessions
live here, players join whenever they like, and the GM dials in and takes their
seat like anyone else — with the powers that come from the session's gm token.

**Reachability is free.** Each session registers with a relay by dialling *out*
to it, exactly as a GM's app does, and a relay join code already carries the
session id (``mmrelay://host:port/<session-id>``). So the hub needs no inbound
port of its own, no new transport, and no change to the join code — a player
joining a session hosted here cannot tell it from one hosted on a laptop.

**Who may create a session.** A session token opens one session; the hub's
*admin secret* opens the catalog — list, create, rename, delete. It lives on the
server and is given to the GM alone, which is the whole of the "only a GM can
create a session" rule. A player has a join code and nothing else.

Pure Python, no Qt, no game data: like the rest of ``core/session``, this module
must never import :mod:`mm_companion.core.rules`.
"""

from __future__ import annotations

import logging
import secrets
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from mm_companion.core import storage
from mm_companion.core.session import discovery, store
from mm_companion.core.session.model import SessionState, new_session, tokens_match
from mm_companion.core.session.net import Connection, Transport
from mm_companion.core.session.protocol import (
    ERROR_BAD_TOKEN,
    ERROR_HUB_FULL,
    ERROR_MALFORMED,
    ERROR_PROTOCOL_VERSION,
    ERROR_UNKNOWN_SESSION,
    PROTOCOL_VERSION,
    AdminHello,
    CreateSessionRequest,
    DeleteSessionRequest,
    ErrorMessage,
    ListSessionsRequest,
    ProtocolError,
    RenameSessionRequest,
    SessionCatalog,
)
from mm_companion.core.session.relay import RelayTransport, relay_url
from mm_companion.core.session.server import SessionServer

log = logging.getLogger("mm_companion.server.hub")

#: The relay session id the control channel registers under. Well known on
#: purpose — it is guarded by the admin secret, not by being hard to guess.
DEFAULT_CONTROL_ID = "mm-control"

#: How long a session sits with nobody connected before its roll history is
#: dropped from memory. It stays registered and joinable throughout; only the
#: log is shed, and the next arrival reads it back off disk.
DEFAULT_IDLE_UNLOAD = 600.0

#: How often the janitor looks for sessions to unload.
JANITOR_INTERVAL = 30.0

#: A ceiling so a runaway client cannot fill the disk with empty sessions.
DEFAULT_MAX_SESSIONS = 50

MAX_SESSION_NAME = 120

#: Seconds between attempts to re-register a session whose relay link dropped.
#: Backs off to :data:`MAX_RETRY_DELAY` so a relay that is down for an hour is
#: not hammered once a second for an hour.
BASE_RETRY_DELAY = 2.0
MAX_RETRY_DELAY = 120.0


class HubError(Exception):
    """The hub could not do what was asked; the message is fit to show a GM."""


@dataclass
class _Entry:
    """One hosted session, and the bookkeeping the hub keeps beside it."""

    server: SessionServer
    #: False once the roll history has been shed. The state is still here and
    #: still joinable — only ``state.rolls`` is empty, and ``_activate`` refills
    #: it before any handshake reads it.
    loaded: bool = True
    #: When the last connection left, or None while somebody is here.
    empty_since: float | None = field(default_factory=time.monotonic)
    lock: threading.Lock = field(default_factory=threading.Lock)

    @property
    def state(self) -> SessionState:
        return self.server.state


class SessionHub:
    """Every session on this machine, hosted at once.

    ``relay_base`` is the relay to register with (``host``, ``host:port``, or a
    full ``mmrelay://…`` URL). ``admin_secret`` guards the control channel.

    ``transport_factory`` is the seam tests use: given a session id it returns
    the :class:`~mm_companion.core.session.net.Transport` that session listens
    on. The default builds a :class:`~mm_companion.core.session.relay.RelayTransport`.
    """

    def __init__(
        self,
        relay_base: str,
        admin_secret: str,
        *,
        workspace: storage.Workspace | None = None,
        mod_fingerprint: str = "",
        max_sessions: int = DEFAULT_MAX_SESSIONS,
        idle_unload: float = DEFAULT_IDLE_UNLOAD,
        control_id: str = DEFAULT_CONTROL_ID,
        transport_factory: Callable[[str], Transport | None] | None = None,
    ) -> None:
        if not admin_secret:
            raise HubError("the hub needs an admin secret; nobody could create a session without")
        self.relay_base = relay_base
        self.admin_secret = admin_secret
        self.workspace = workspace
        self.mod_fingerprint = mod_fingerprint
        self.max_sessions = max_sessions
        self.idle_unload = idle_unload
        self.control_id = control_id

        self._transport_factory = transport_factory or self._relay_transport
        self._entries: dict[str, _Entry] = {}
        self._lock = threading.RLock()
        self._running = False
        self._threads: list[threading.Thread] = []
        self._control_listener = None
        self._wake = threading.Event()

    # -- lifecycle ---------------------------------------------------------

    @property
    def running(self) -> bool:
        return self._running

    def start(self) -> None:
        """Host every session in the workspace, then open the control channel."""
        if self._running:
            return
        self._running = True
        for summary in store.list_sessions(self.workspace):
            try:
                self._host(store.load_session(summary.id, self.workspace))
            except (store.SessionStoreError, OSError) as exc:
                # One unreadable session must not stop the other tables playing.
                log.warning("could not host session %s: %s", summary.id, exc)
        self._spawn(self._control_loop, "hub-control")
        self._spawn(self._janitor_loop, "hub-janitor")
        log.info("hub up: %d session(s), control channel %r", len(self._entries), self.control_id)

    def stop(self) -> None:
        """Stop every session and close the control channel."""
        if not self._running:
            return
        self._running = False
        self._wake.set()
        listener = self._control_listener
        self._control_listener = None
        if listener is not None:
            listener.close()
        with self._lock:
            entries = list(self._entries.values())
            self._entries.clear()
        for entry in entries:
            entry.server.stop()
        for thread in self._threads:
            if thread is not threading.current_thread():
                thread.join(timeout=2.0)
        self._threads = []

    def __enter__(self) -> SessionHub:
        self.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        self.stop()

    # -- the catalog -------------------------------------------------------

    def session_ids(self) -> list[str]:
        with self._lock:
            return list(self._entries)

    def catalog(self) -> list[dict]:
        """Every session, as the control channel describes it to a GM.

        The join code and the gm token are in here because they are the two
        things a GM cannot work out for themselves, and this channel — already
        gated on the admin secret — is the only place either is handed out.
        """
        with self._lock:
            entries = list(self._entries.values())
        return [self._describe(entry) for entry in entries]

    def _describe(self, entry: _Entry) -> dict:
        state = entry.state
        return {
            "id": state.id,
            "name": state.name,
            "join_code": self.join_code(state),
            "gm_token": state.gm_token,
            "player_count": sum(1 for slot in state.players.values() if not slot.is_gm),
            "connected": len(entry.server.connected_player_ids()),
            # Read from disk when the history has been shed, so an idle session
            # does not suddenly report zero rolls.
            "roll_count": (
                len(state.rolls)
                if entry.loaded
                else len(store.load_rolls(state.id, self.workspace))
            ),
            "updated_at": state.updated_at,
        }

    def join_code(self, state: SessionState) -> str:
        """The code a player types to reach this session."""
        url = relay_url(self.relay_base, state.id)
        return discovery.relay_reachability(url, _relay_port(url)).join_code(state.host_token)

    # -- creating and destroying -------------------------------------------

    def create(self, name: str) -> dict:
        """Mint a session, host it, and return its catalog entry."""
        with self._lock:
            if len(self._entries) >= self.max_sessions:
                raise HubError(f"this server is holding its limit of {self.max_sessions} sessions")
        state = new_session((name or "Session").strip()[:MAX_SESSION_NAME] or "Session")
        store.save_session(state, self.workspace, write_rolls=True)
        entry = self._host(state)
        log.info("created session %r (%s)", state.name, state.id)
        return self._describe(entry)

    def delete(self, session_id: str) -> None:
        """Stop a session and erase it — roll history, roster and all."""
        with self._lock:
            entry = self._entries.pop(session_id, None)
        if entry is None:
            raise HubError("that session is not on this server")
        entry.server.stop()
        store.delete_session(session_id, self.workspace)
        log.info("deleted session %s", session_id)

    def rename(self, session_id: str, name: str) -> None:
        entry = self._entry(session_id)
        entry.server.set_session_name((name or "").strip()[:MAX_SESSION_NAME] or "Session")

    def _entry(self, session_id: str) -> _Entry:
        with self._lock:
            entry = self._entries.get(session_id)
        if entry is None:
            raise HubError("that session is not on this server")
        return entry

    # -- hosting -----------------------------------------------------------

    def _host(self, state: SessionState) -> _Entry:
        """Start one session and register it in the catalog."""
        entry = _Entry(server=self._build_server(state))
        entry.server.start()
        with self._lock:
            self._entries[state.id] = entry
        return entry

    def _build_server(self, state: SessionState) -> SessionServer:
        session_id = state.id
        return SessionServer(
            state,
            transport=self._transport_factory(session_id),
            workspace=self.workspace,
            mod_fingerprint=self.mod_fingerprint,
            # Nobody is sitting in the GM's chair until somebody dials in with
            # the gm token, and the roster should say so.
            gm_in_process=False,
            on_event=lambda kind, payload: self._on_session_event(session_id, kind, payload),
            on_activate=lambda: self._activate(session_id),
        )

    def _relay_transport(self, session_id: str) -> Transport:
        """A relay transport whose host secret survives a restart."""
        return RelayTransport(
            relay_url(self.relay_base, session_id),
            secret=relay_secret(session_id, self.workspace),
        )

    # -- idle sessions -----------------------------------------------------

    def _on_session_event(self, session_id: str, kind: str, _payload: dict) -> None:
        if kind not in ("player_joined", "player_left"):
            return
        with self._lock:
            entry = self._entries.get(session_id)
        if entry is None:
            return
        # Read the live count rather than trusting the event: stop() closes every
        # socket, so a player_left can arrive after the session is already gone.
        entry.empty_since = None if entry.server.connected_player_ids() else time.monotonic()

    def _activate(self, session_id: str) -> None:
        """Put a shed roll history back before anyone is told what it is.

        Called on every arriving connection *before* its handshake, because the
        Welcome carries the recent history. Cheap and idempotent when the session
        was never unloaded, which is the common case.
        """
        with self._lock:
            entry = self._entries.get(session_id)
        if entry is None:
            return
        entry.empty_since = None
        if entry.loaded:
            return
        with entry.lock:
            if entry.loaded:
                return
            entry.state.rolls = store.load_rolls(session_id, self.workspace)
            entry.loaded = True
            log.info("reloaded session %s (%d rolls)", session_id, len(entry.state.rolls))

    def _unload(self, entry: _Entry) -> None:
        """Drop an idle session's roll history from memory.

        Safe only while nothing is connected, and that is checked under the
        entry's lock: sequence numbers are assigned from the tail of this list,
        so shedding it under a live roll would restart the numbering and corrupt
        the log. Nothing can roll without a connection, and a connection cannot
        arrive without ``_activate`` running first.
        """
        with entry.lock:
            if not entry.loaded or entry.server.connected_player_ids():
                return
            count = len(entry.state.rolls)
            entry.state.rolls = []
            entry.loaded = False
        if count:
            log.info("unloaded idle session %s (%d rolls shed)", entry.state.id, count)

    def _janitor_loop(self) -> None:
        while self._running:
            self._wake.wait(JANITOR_INTERVAL)
            if not self._running:
                return
            cutoff = time.monotonic() - self.idle_unload
            with self._lock:
                entries = list(self._entries.values())
            for entry in entries:
                since = entry.empty_since
                if entry.loaded and since is not None and since <= cutoff:
                    self._unload(entry)

    # -- the control channel -----------------------------------------------

    def _control_loop(self) -> None:
        """Accept control connections, re-registering when the relay drops us."""
        delay = BASE_RETRY_DELAY
        while self._running:
            try:
                transport = self._transport_factory(self.control_id)
                if transport is None:
                    return
                listener = transport.listen()
            except Exception as exc:  # noqa: BLE001 - any failure is "try again"
                if not self._running:
                    return
                log.warning("control channel unavailable (%s); retrying in %.0fs", exc, delay)
                self._wake.wait(delay)
                delay = min(delay * 2, MAX_RETRY_DELAY)
                continue

            delay = BASE_RETRY_DELAY
            self._control_listener = listener
            log.info("control channel open")
            try:
                while self._running:
                    connection = listener.accept()
                    if connection is None:
                        break
                    self._spawn(
                        lambda c=connection: self._serve_control(c),
                        "hub-control-client",
                    )
            finally:
                listener.close()

    def _serve_control(self, connection: Connection) -> None:
        """One admin conversation: authenticate once, then answer requests."""
        try:
            if not self._authenticate(connection):
                return
            self._send(connection, SessionCatalog(sessions=self.catalog()))
            while self._running:
                try:
                    message = connection.receive()
                except TimeoutError:
                    continue
                except ProtocolError as exc:
                    self._send(connection, ErrorMessage(code=ERROR_MALFORMED, message=str(exc)))
                    return
                if message is None:
                    return
                self._handle_control(connection, message)
        except OSError:
            pass
        finally:
            connection.close()

    def _authenticate(self, connection: Connection) -> bool:
        try:
            message = connection.receive()
        except (OSError, ProtocolError):
            return False
        if not isinstance(message, AdminHello):
            self._send(
                connection,
                ErrorMessage(code=ERROR_MALFORMED, message="expected an admin hello first"),
            )
            return False
        if message.protocol_version != PROTOCOL_VERSION:
            self._send(
                connection,
                ErrorMessage(
                    code=ERROR_PROTOCOL_VERSION,
                    message=f"this server speaks protocol v{PROTOCOL_VERSION}, "
                    f"you speak v{message.protocol_version}",
                ),
            )
            return False
        if not tokens_match(message.secret, self.admin_secret):
            log.warning("control channel refused a bad secret from %s", connection.address)
            self._send(
                connection,
                ErrorMessage(
                    code=ERROR_BAD_TOKEN, message="that is not this server's admin secret"
                ),
            )
            return False
        return True

    def _handle_control(self, connection: Connection, message: object) -> None:
        try:
            if isinstance(message, CreateSessionRequest):
                self.create(message.name)
            elif isinstance(message, DeleteSessionRequest):
                self.delete(message.session_id)
            elif isinstance(message, RenameSessionRequest):
                self.rename(message.session_id, message.name)
            elif not isinstance(message, ListSessionsRequest):
                return
        except HubError as exc:
            code = ERROR_HUB_FULL if "limit" in str(exc) else ERROR_UNKNOWN_SESSION
            self._send(connection, ErrorMessage(code=code, message=str(exc)))
            return
        # Every mutation answers with the whole catalog, so a GM's list can never
        # drift out of step with the server's.
        self._send(connection, SessionCatalog(sessions=self.catalog()))

    def _send(self, connection: Connection, message: object) -> None:
        try:
            connection.send(message)
        except (OSError, ProtocolError) as exc:
            log.debug("control send failed: %s", exc)

    # -- plumbing ----------------------------------------------------------

    def _spawn(self, target: Callable[[], None], name: str) -> None:
        thread = threading.Thread(target=target, name=name, daemon=True)
        self._threads = [t for t in self._threads if t.is_alive()] + [thread]
        thread.start()


def relay_secret(session_id: str, workspace: storage.Workspace | None = None) -> str:
    """This session's relay host secret, minted once and then kept.

    The secret is how the relay knows a re-registration of a session id is the
    same host coming back rather than someone else claiming the name.
    :class:`~mm_companion.core.session.relay.RelayTransport` mints a fresh one per
    instance, which is right for a GM's app — a laptop that stops hosting is done
    — and wrong here: a restarted hub has to reclaim every id it had.

    Kept beside the session it belongs to, so deleting a session takes its secret
    with it.
    """
    path = store.session_dir(session_id, workspace) / "relay.secret"
    try:
        existing = path.read_text(encoding="utf-8").strip()
    except OSError:
        existing = ""
    if existing:
        return existing
    secret = secrets.token_urlsafe(32)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(secret, encoding="utf-8")
    return secret


def _relay_port(url: str) -> int:
    """The relay's own port, for the join code's readability."""
    from mm_companion.core.session.relay import parse_relay_url

    return parse_relay_url(url).port

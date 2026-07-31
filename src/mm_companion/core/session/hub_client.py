"""Talking to a session hub: the catalog, and making sessions in it.

A short, synchronous conversation, deliberately unlike :mod:`.client`. That one
is a live session — a reader thread, events arriving for as long as the game
lasts. This is a GM opening a filing cabinet: connect, ask, read the answer,
close. Every request is answered with the whole catalog, so there is no state to
keep in step and no callback to wait on.

Pure Python and Qt-free, so the dialog that drives it is thin and the behaviour
is testable without a window.
"""

from __future__ import annotations

from . import discovery
from .net import CONNECT_TIMEOUT, Connection, Transport, TransportError
from .protocol import (
    AdminHello,
    CreateSessionRequest,
    DeleteSessionRequest,
    ErrorMessage,
    ListSessionsRequest,
    Message,
    ProtocolError,
    RenameSessionRequest,
    SessionCatalog,
)
from .relay import RelayUrlError, relay_url

#: The relay session id a hub's control channel registers under. Matches
#: ``mm_companion.server.hub.DEFAULT_CONTROL_ID``; kept here too so the app never
#: has to import the server package.
DEFAULT_CONTROL_ID = "mm-control"


class HubClientError(Exception):
    """The hub could not be reached, or refused. The message is fit to show."""

    def __init__(self, message: str, code: str = "") -> None:
        super().__init__(message)
        self.code = code


def control_url(server: str, control_id: str = DEFAULT_CONTROL_ID) -> str:
    """The relay address of *server*'s control channel.

    ``server`` is what the GM typed — ``mmcompanion.duckdns.org``, a
    ``host:port``, or a full ``mmrelay://…`` URL.
    """
    try:
        return relay_url(server, control_id)
    except RelayUrlError as exc:
        raise HubClientError(str(exc)) from exc


class HubClient:
    """One conversation with a hub's control channel.

    ``secret`` is the server's admin secret — not any session's token. It is what
    separates a GM from a player: a join code opens one session, this opens the
    catalog.
    """

    def __init__(
        self,
        server: str,
        secret: str,
        *,
        control_id: str = DEFAULT_CONTROL_ID,
        transport: Transport | None = None,
    ) -> None:
        self.server = server
        self.secret = secret
        self.url = control_url(server, control_id)
        self._transport = transport or discovery.transport_for(self.url)
        self._connection: Connection | None = None
        self.sessions: list[dict] = []

    @property
    def connected(self) -> bool:
        return self._connection is not None and not self._connection.closed

    def connect(self, timeout: float = CONNECT_TIMEOUT) -> list[dict]:
        """Dial the hub, authenticate, and return its catalog.

        Raises :class:`HubClientError` for every failure — unreachable, wrong
        secret, a version mismatch — so a caller has one thing to catch and one
        sentence to show.
        """
        if self.connected:
            raise HubClientError("already connected to this server")
        try:
            connection = self._transport.connect(timeout=timeout)
        except (TransportError, OSError) as exc:
            raise HubClientError(f"could not reach {self.server}: {exc}") from exc
        connection.set_timeout(timeout)
        self._connection = connection
        return self._exchange(AdminHello(secret=self.secret))

    def create(self, name: str) -> list[dict]:
        """Make a new session on the server and return the updated catalog."""
        return self._exchange(CreateSessionRequest(name=name))

    def rename(self, session_id: str, name: str) -> list[dict]:
        return self._exchange(RenameSessionRequest(session_id=session_id, name=name))

    def delete(self, session_id: str) -> list[dict]:
        """Erase a session and its whole roll history."""
        return self._exchange(DeleteSessionRequest(session_id=session_id))

    def refresh(self) -> list[dict]:
        return self._exchange(ListSessionsRequest())

    def close(self) -> None:
        connection, self._connection = self._connection, None
        if connection is not None:
            connection.close()

    def __enter__(self) -> HubClient:
        self.connect()
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def _exchange(self, request: Message) -> list[dict]:
        connection = self._connection
        if connection is None or connection.closed:
            raise HubClientError("not connected to a server")
        try:
            connection.send(request)
            answer = connection.receive()
        except (OSError, ProtocolError) as exc:
            self.close()
            raise HubClientError(f"the server stopped answering: {exc}") from exc
        if answer is None:
            self.close()
            raise HubClientError("the server closed the connection")
        if isinstance(answer, ErrorMessage):
            # Not fatal to the connection: a refused create still leaves the
            # channel usable, and only a bad secret ends the conversation — which
            # the server does by closing, caught on the next exchange.
            raise HubClientError(answer.message or answer.code, answer.code)
        if not isinstance(answer, SessionCatalog):
            raise HubClientError("the server sent something unexpected")
        self.sessions = list(answer.sessions)
        return self.sessions

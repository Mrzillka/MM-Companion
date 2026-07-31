"""Talking to a session hub: making sessions on it, and looking after your own.

A short, synchronous conversation, deliberately unlike :mod:`.client`. That one
is a live session — a reader thread, events arriving for as long as the game
lasts. This is a GM at a counter: connect, ask, read the answer, close.

**Anyone may create a session**; the server is a public utility. What a session
*belongs* to is its ``gm_token``, handed back by the create and held by nobody
else. Renaming it, deleting it and taking the GM's seat in it all need that
token, so a GM's own sessions are remembered by their app rather than listed by
the server — a server-side list is exactly the thing that would hand every
join code to whoever asked for it.

Pure Python and Qt-free, so the dialog that drives it is thin and the behaviour
is testable without a window.
"""

from __future__ import annotations

from . import discovery
from .net import CONNECT_TIMEOUT, Connection, Transport, TransportError
from .protocol import (
    ControlHello,
    ControlWelcome,
    CreateSessionRequest,
    DeleteSessionRequest,
    ErrorMessage,
    ListSessionsRequest,
    Message,
    ProtocolError,
    RenameSessionRequest,
    SessionCatalog,
    SessionInfo,
    SessionStatusRequest,
)
from .relay import RelayUrlError, relay_url

#: The relay session id a hub's control channel registers under. Matches
#: ``mm_companion.server.hub.DEFAULT_CONTROL_ID``; kept here too so the app never
#: has to import the server package.
DEFAULT_CONTROL_ID = "mm-control"

#: The server the app points at out of the box, so someone who has just installed
#: it can host a game without knowing anyone. It is only a default — the field is
#: editable, and a group that would rather run their own puts that address here.
DEFAULT_SERVER = "mmcompanion.duckdns.org"


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

    **Opening it needs no credential** — anyone running the app may create a
    session. What every other request needs is that session's own ``gm_token``,
    handed back by the create and held by nobody else, which is what makes a
    session belong to whoever made it.

    ``secret`` is for the person who *runs* the server. It grants the full
    catalog and the right to delete anything, so abandoned or abusive sessions
    can be cleaned up. Leave it empty, which is the normal case.
    """

    def __init__(
        self,
        server: str,
        secret: str = "",
        *,
        control_id: str = DEFAULT_CONTROL_ID,
        transport: Transport | None = None,
    ) -> None:
        self.server = server
        self.secret = secret
        self.url = control_url(server, control_id)
        self._transport = transport or discovery.transport_for(self.url)
        self._connection: Connection | None = None
        #: True once the server has accepted an operator secret.
        self.operator = False
        #: Every session on the server — filled for an operator, empty otherwise.
        self.sessions: list[dict] = []

    @property
    def connected(self) -> bool:
        return self._connection is not None and not self._connection.closed

    def connect(self, timeout: float = CONNECT_TIMEOUT) -> bool:
        """Dial the hub and open the channel; returns whether we are the operator.

        Raises :class:`HubClientError` for every failure — unreachable, a wrong
        operator secret, a version mismatch — so a caller has one thing to catch
        and one sentence to show.
        """
        if self.connected:
            raise HubClientError("already connected to this server")
        try:
            connection = self._transport.connect(timeout=timeout)
        except (TransportError, OSError) as exc:
            raise HubClientError(f"could not reach {self.server}: {exc}") from exc
        connection.set_timeout(timeout)
        self._connection = connection

        answer = self._exchange(ControlHello(secret=self.secret))
        if not isinstance(answer, ControlWelcome):
            self.close()
            raise HubClientError("that server did not open a control channel")
        self.operator = answer.operator
        self.sessions = list(answer.sessions)
        return self.operator

    def create(self, name: str) -> dict:
        """Make a session and return it — join code, gm token and all.

        **Keep the gm token.** It is the only proof that this session is yours,
        it is handed out exactly once, and without it the session cannot be
        renamed, deleted, or taken the GM's seat in.
        """
        return self._session_of(self._exchange(CreateSessionRequest(name=name)))

    def rename(self, session_id: str, name: str, gm_token: str = "") -> dict:
        return self._session_of(
            self._exchange(
                RenameSessionRequest(session_id=session_id, name=name, gm_token=gm_token)
            )
        )

    def delete(self, session_id: str, gm_token: str = "") -> None:
        """Erase a session and its whole roll history."""
        self._exchange(DeleteSessionRequest(session_id=session_id, gm_token=gm_token))

    def status(self, session_id: str, gm_token: str = "") -> dict:
        """One session as it stands now, or ``{}`` if it is no longer there.

        How an app refreshes its own list without the server ever offering a view
        of everyone else's.
        """
        return self._session_of(
            self._exchange(SessionStatusRequest(session_id=session_id, gm_token=gm_token))
        )

    def refresh(self) -> list[dict]:
        """The whole catalog. Operator only — refused for anyone else."""
        answer = self._exchange(ListSessionsRequest())
        if not isinstance(answer, SessionCatalog):
            raise HubClientError("that server did not send a catalog")
        self.sessions = list(answer.sessions)
        return self.sessions

    @staticmethod
    def _session_of(answer: Message) -> dict:
        if not isinstance(answer, SessionInfo):
            raise HubClientError("the server sent something unexpected")
        return dict(answer.session)

    def close(self) -> None:
        connection, self._connection = self._connection, None
        if connection is not None:
            connection.close()

    def __enter__(self) -> HubClient:
        self.connect()
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def _exchange(self, request: Message) -> Message:
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
        return answer

"""The relay transport: both ends dial *out*, so NAT never gets a vote.

A port forward — automatic or by hand — cannot reach a machine behind
carrier-grade NAT, and that is the normal case for mobile broadband, fibre
resellers and student housing. *Outbound* connections work from behind every NAT,
so the universal answer is both ends dialling a public box that pairs them. This
module is the app's half of that; the box itself is :mod:`mm_companion.relay`.

It is a :class:`~.net.Transport` and nothing else, so :mod:`.server` and
:mod:`.client` are untouched: a join code whose host is a
``mmrelay://relay.example:47332/<session-id>`` URL resolves through
:func:`~.discovery.transport_for` to a :class:`RelayTransport`, and everything
above the transport carries on as if it were a socket to the GM's machine.

**The envelope.** Same newline-delimited UTF-8 JSON framing as :mod:`.net`, but
deliberately *not* :mod:`.protocol` messages — the relay must never need a
:data:`~.protocol.PROTOCOL_VERSION` bump, so it parses one small frame and is a
dumb byte pipe after that.

1. The GM opens a **control** connection and sends
   ``{"type": "relay_host", "session": "<id>", "secret": "<secret>"}``; the relay
   answers ``relay_ok``. Nothing is piped over this connection — it only carries
   envelopes, plus a ``relay_ping``/``relay_pong`` keepalive so the relay's idle
   timeout can apply to it like any other connection.
2. A player opens a connection and sends
   ``{"type": "relay_join", "session": "<id>"}``. The relay allocates a stream id,
   holds the player, and tells the GM's control connection
   ``{"type": "relay_incoming", "stream": "<stream-id>"}``.
3. The GM opens a **fresh** connection per player and sends
   ``{"type": "relay_accept", "session": "<id>", "secret": "...", "stream": "..."}``.
   The relay answers ``relay_ok`` to *both* ends and splices them: every byte
   after that is forwarded verbatim, in both directions, unread.
4. Anything refused is ``{"type": "relay_error", "code": "...", "message": "..."}``
   followed by a close.

One connection per player rather than a multiplexed stream is what keeps the
relay a genuine dumb pipe — no channel framing to add, no per-message parsing, no
head-of-line blocking between players, and no session state beyond "who is paired
with whom".

**The secret is not the join token.** Every player is given the session's
``host_token`` in the join code, so using it to authenticate ``relay_host`` would
let any player take the session over at the relay. :class:`RelayTransport`
generates a separate secret that never leaves the GM's app; the session id in the
URL is public, and a stranger who guesses it reaches nothing but the session
server's own handshake — exactly as if they had guessed an open port.

**Transport security.** ``mmrelay://`` is TLS to the relay
(:func:`ssl.create_default_context`, a real certificate on the box, no player
configuration). ``mmrelay+tcp://`` is the same protocol in the clear, for a relay
on a trusted network and for tests. TLS terminates *at* the relay, so its operator
could in principle read the traffic — which is why running your own is one
command.
"""

from __future__ import annotations

import json
import queue
import secrets
import socket
import ssl
import threading
import urllib.parse
from dataclasses import dataclass

from . import discovery
from .net import CONNECT_TIMEOUT, Connection, Listener, Transport, TransportError, tune_socket

#: The URL scheme for a TLS relay, and the same protocol in the clear.
RELAY_SCHEME = discovery.RELAY_SCHEME
RELAY_SCHEME_PLAIN = f"{RELAY_SCHEME}+tcp"

#: The port a relay listens on unless its URL says otherwise. Next to
#: :data:`~.net.DEFAULT_PORT`, and equally free of registered claimants.
DEFAULT_RELAY_PORT = 47332

#: Envelope tags. These four plus the two keepalives are the *whole* vocabulary
#: the relay understands; it never learns what a session message is.
ENVELOPE_HOST = "relay_host"
ENVELOPE_JOIN = "relay_join"
ENVELOPE_ACCEPT = "relay_accept"
ENVELOPE_INCOMING = "relay_incoming"
ENVELOPE_OK = "relay_ok"
ENVELOPE_ERROR = "relay_error"
ENVELOPE_PING = "relay_ping"
ENVELOPE_PONG = "relay_pong"

#: ``relay_error`` codes. The UI turns these into advice, so they are stable.
ERROR_BAD_ENVELOPE = "bad_envelope"
ERROR_UNKNOWN_SESSION = "unknown_session"
ERROR_SESSION_EXISTS = "session_exists"
ERROR_BAD_SECRET = "bad_secret"
ERROR_UNKNOWN_STREAM = "unknown_stream"
ERROR_SESSION_FULL = "session_full"
ERROR_RELAY_FULL = "relay_full"

#: An envelope is a handful of short fields; anything larger is not one.
MAX_ENVELOPE_BYTES = 4096

#: Bytes of entropy in the host-only relay secret.
SECRET_BYTES = 24

#: How long the control connection waits for a ``relay_incoming`` before sending
#: a keepalive ping. Must stay well under the relay's idle timeout.
CONTROL_PING_INTERVAL = 30.0


class RelayError(TransportError):
    """The relay refused us, or answered with something that is not the protocol.

    A :class:`~.net.TransportError`, so a caller that already handles "the network
    did not work" needs no new branch; ``code`` carries the ``ERROR_*`` when the
    refusal came from the relay itself.
    """

    def __init__(self, message: str, code: str = "") -> None:
        super().__init__(message)
        self.code = code


class RelayUrlError(ValueError):
    """A relay URL could not be read."""


# --------------------------------------------------------------------------
# Relay URLs
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class RelayAddress:
    """A relay URL, taken apart: where the relay is and which session to ask for."""

    host: str
    port: int
    session_id: str
    tls: bool = True

    @property
    def address(self) -> tuple[str, int]:
        return (self.host, self.port)

    def url(self) -> str:
        scheme = RELAY_SCHEME if self.tls else RELAY_SCHEME_PLAIN
        host = f"[{self.host}]" if ":" in self.host else self.host
        return f"{scheme}://{host}:{self.port}/{self.session_id}"


def parse_relay_url(url: str) -> RelayAddress:
    """Read ``mmrelay://host[:port]/<session-id>`` (or ``mmrelay+tcp://…``)."""
    parts = urllib.parse.urlsplit((url or "").strip())
    scheme = parts.scheme.lower()
    if scheme not in (RELAY_SCHEME, RELAY_SCHEME_PLAIN):
        raise RelayUrlError(
            f"{url!r} is not a relay address (it should start with {RELAY_SCHEME}://)"
        )
    try:
        host = parts.hostname or ""
        port = parts.port or DEFAULT_RELAY_PORT
    except ValueError as exc:  # a port that is not a number
        raise RelayUrlError(f"{url!r} has an unreadable port: {exc}") from exc
    if not host:
        raise RelayUrlError(f"{url!r} does not name a relay server")
    session_id = parts.path.strip("/")
    return RelayAddress(
        host=host, port=int(port), session_id=session_id, tls=scheme == RELAY_SCHEME
    )


def relay_url(base: str, session_id: str) -> str:
    """Build the join-code host for *session_id* on the relay at *base*.

    ``base`` is what the GM configured — ``relay.example.net``,
    ``relay.example.net:9000``, or a full ``mmrelay://…`` URL — and any path it
    carries is replaced by the session id.
    """
    text = (base or "").strip()
    if not text:
        raise RelayUrlError("no relay address is configured")
    if "://" not in text:
        text = f"{RELAY_SCHEME}://{text}"
    address = parse_relay_url(text)
    session = (session_id or "").strip("/")
    if not session:
        raise RelayUrlError("a relay address needs a session id")
    return RelayAddress(address.host, address.port, session, address.tls).url()


# --------------------------------------------------------------------------
# Envelope plumbing
# --------------------------------------------------------------------------


def encode_envelope(envelope: dict) -> bytes:
    """One envelope as a framed line."""
    data = json.dumps(envelope, separators=(",", ":")).encode("utf-8")
    if len(data) + 1 > MAX_ENVELOPE_BYTES:
        raise RelayError("relay envelope is too large to send")
    return data + b"\n"


def _send_envelope(sock: socket.socket, envelope: dict) -> None:
    sock.sendall(encode_envelope(envelope))


def _read_envelope(sock: socket.socket, buffer: bytearray) -> dict:
    """Read one envelope, leaving anything past its newline in *buffer*.

    Those leftover bytes matter: the relay's ``relay_ok`` and the first session
    bytes can share a TCP segment, and they belong to the stream, not to us.
    """
    while True:
        newline = buffer.find(b"\n")
        if newline >= 0:
            line = bytes(buffer[:newline])
            del buffer[: newline + 1]
            return _decode_envelope(line)
        if len(buffer) > MAX_ENVELOPE_BYTES:
            raise RelayError("the relay sent no envelope; it may not be a relay at all")
        chunk = sock.recv(4096)
        if not chunk:
            raise RelayError("the relay closed the connection")
        buffer.extend(chunk)


def _decode_envelope(line: bytes) -> dict:
    try:
        envelope = json.loads(line.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RelayError(f"the relay sent something unreadable: {exc}") from exc
    if not isinstance(envelope, dict):
        raise RelayError("the relay sent something that is not an envelope")
    return envelope


def _expect_ok(envelope: dict) -> dict:
    """Return *envelope* if it is a ``relay_ok``; raise its refusal otherwise."""
    kind = envelope.get("type")
    if kind == ENVELOPE_OK:
        return envelope
    if kind == ENVELOPE_ERROR:
        code = str(envelope.get("code", ""))
        raise RelayError(str(envelope.get("message", "")) or f"the relay refused: {code}", code)
    raise RelayError(f"the relay answered with {kind!r}, which is not part of the protocol")


def _dial(
    address: RelayAddress, ssl_context: ssl.SSLContext | None, timeout: float
) -> socket.socket:
    """Open one connection to the relay, wrapped in TLS when the URL asks for it."""
    try:
        sock: socket.socket = socket.create_connection(address.address, timeout=timeout)
    except OSError as exc:
        raise RelayError(f"cannot reach the relay at {address.host}:{address.port}: {exc}") from exc
    tune_socket(sock)
    if not address.tls:
        return sock
    context = ssl_context or ssl.create_default_context()
    try:
        return context.wrap_socket(sock, server_hostname=address.host)
    except (OSError, ssl.SSLError) as exc:
        sock.close()
        raise RelayError(f"the relay's secure connection failed: {exc}") from exc


# --------------------------------------------------------------------------
# The transport
# --------------------------------------------------------------------------


class RelayListener(Listener):
    """The GM's side: a control connection, and one dialled stream per player.

    :meth:`accept` blocks on the control connection's ``relay_incoming`` frames
    and dials a fresh connection for each, so what comes back is an ordinary
    :class:`~.net.Connection` and :class:`~.server.SessionServer` cannot tell the
    difference between this and a listening socket.
    """

    def __init__(
        self,
        address: RelayAddress,
        secret: str,
        *,
        ssl_context: ssl.SSLContext | None = None,
        timeout: float = CONNECT_TIMEOUT,
        ping_interval: float | None = None,
    ) -> None:
        self._address = address
        self._secret = secret
        self._ssl_context = ssl_context
        self._timeout = timeout
        # Read at call time rather than frozen as a default argument: this
        # interval only means anything relative to the relay's idle timeout, and
        # a def-time default cannot be moved when that one is.
        self._ping_interval = CONTROL_PING_INTERVAL if ping_interval is None else ping_interval
        self._incoming: queue.Queue[str | None] = queue.Queue()
        self._closed = False

        control = _dial(address, ssl_context, timeout)
        self._buffer = bytearray()
        try:
            control.settimeout(timeout)
            _send_envelope(
                control,
                {
                    "type": ENVELOPE_HOST,
                    "session": address.session_id,
                    "secret": secret,
                },
            )
            _expect_ok(_read_envelope(control, self._buffer))
        except (OSError, RelayError):
            control.close()
            raise
        self._control = control
        self._reader = threading.Thread(
            target=self._control_loop, name="relay-control", daemon=True
        )
        self._reader.start()

    @property
    def address(self) -> tuple[str, int]:
        """The relay's own address — there is no local listening socket."""
        return self._address.address

    @property
    def session_id(self) -> str:
        return self._address.session_id

    def accept(self) -> Connection | None:
        """Block for the next player the relay pairs us with."""
        while True:
            stream = self._incoming.get()
            if stream is None or self._closed:
                return None
            try:
                return self._accept_stream(stream)
            except (OSError, RelayError):
                # One player's stream failed to open. That is their connection
                # attempt lost, not the end of hosting: wait for the next.
                continue

    def _accept_stream(self, stream: str) -> Connection:
        sock = _dial(self._address, self._ssl_context, self._timeout)
        buffer = bytearray()
        try:
            sock.settimeout(self._timeout)
            _send_envelope(
                sock,
                {
                    "type": ENVELOPE_ACCEPT,
                    "session": self._address.session_id,
                    "secret": self._secret,
                    "stream": stream,
                },
            )
            _expect_ok(_read_envelope(sock, buffer))
        except (OSError, RelayError):
            sock.close()
            raise
        sock.settimeout(None)
        return Connection(sock, self._address.address, initial_buffer=bytes(buffer))

    def _control_loop(self) -> None:
        """Queue every ``relay_incoming``, and keep the control link warm."""
        try:
            self._control.settimeout(self._ping_interval)
            while not self._closed:
                try:
                    envelope = _read_envelope(self._control, self._buffer)
                except TimeoutError:
                    _send_envelope(self._control, {"type": ENVELOPE_PING})
                    continue
                kind = envelope.get("type")
                if kind == ENVELOPE_INCOMING:
                    stream = str(envelope.get("stream", ""))
                    if stream:
                        self._incoming.put(stream)
                elif kind == ENVELOPE_ERROR:
                    break
                # relay_pong, and anything else, is nothing to act on.
        except (OSError, RelayError):
            pass
        finally:
            self._incoming.put(None)

    def close(self) -> None:
        self._closed = True
        try:
            self._control.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            self._control.close()
        except OSError:
            pass
        self._incoming.put(None)


class RelayTransport(Transport):
    """Reach a session through a relay instead of straight at the GM's machine.

    Built from the host half of a join code, so ``listen``/``connect`` already
    know which relay and which session: their ``host``/``port`` arguments exist
    only to satisfy :class:`~.net.Transport` and are ignored.
    """

    def __init__(
        self,
        url: str,
        *,
        ssl_context: ssl.SSLContext | None = None,
        secret: str = "",
    ) -> None:
        self.relay = parse_relay_url(url)
        self._ssl_context = ssl_context
        # Host-only, per hosting run, and never in a join code — see the module
        # docstring on why the session's own token would be the wrong secret.
        self.secret = secret or secrets.token_urlsafe(SECRET_BYTES)

    @property
    def url(self) -> str:
        return self.relay.url()

    def listen(self, host: str = "", port: int = 0) -> Listener:
        """Register this session with the relay and accept the players it pairs."""
        if not self.relay.session_id:
            raise RelayError("this relay address carries no session id")
        return RelayListener(self.relay, self.secret, ssl_context=self._ssl_context)

    def connect(
        self, host: str = "", port: int = 0, *, timeout: float = CONNECT_TIMEOUT
    ) -> Connection:
        """Ask the relay to pair us with the session named in the URL."""
        sock = _dial(self.relay, self._ssl_context, timeout)
        buffer = bytearray()
        try:
            sock.settimeout(timeout)
            _send_envelope(sock, {"type": ENVELOPE_JOIN, "session": self.relay.session_id})
            _expect_ok(_read_envelope(sock, buffer))
        except (OSError, RelayError):
            sock.close()
            raise
        sock.settimeout(None)
        return Connection(sock, self.relay.address, initial_buffer=bytes(buffer))


# --------------------------------------------------------------------------
# Registration
# --------------------------------------------------------------------------


def _transport_factory(url: str) -> Transport:
    return RelayTransport(url)


def register_transports() -> None:
    """Teach :func:`~.discovery.transport_for` about relay join codes.

    Called at import, and importing this module is what
    :mod:`mm_companion.core.session` does — so a relay join code works in the app
    with no further wiring. Idempotent, and it never displaces a transport a mod
    registered for the same scheme.
    """
    for scheme in (RELAY_SCHEME, RELAY_SCHEME_PLAIN):
        if discovery.transports.get(scheme) is None:
            discovery.transports.register(scheme, _transport_factory)


register_transports()

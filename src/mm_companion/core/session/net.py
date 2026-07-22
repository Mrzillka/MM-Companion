"""Framing and the transport seam shared by the session server and client.

Pure Python and Qt-free. Two things live here:

- :class:`Connection` — one framed message channel. It turns a byte stream into
  :mod:`.protocol` messages, buffering partial lines and enforcing
  :data:`~.protocol.MAX_MESSAGE_BYTES` on the way in, so a peer that never sends
  a newline cannot make the receiver buffer without bound.
- :class:`Transport` / :class:`Listener` — the swappable seam a relay implements
  later. Everything above this module talks to ``listen``/``connect`` and never
  touches :mod:`socket` directly, so dropping in a relay transport (Phase 3) is a
  constructor argument rather than a rework of the session layer.

:class:`TcpTransport` is the direct implementation: plain TCP, the GM's app
listening and players connecting to it.
"""

from __future__ import annotations

import socket
import threading
from abc import ABC, abstractmethod
from collections.abc import Iterator

from .protocol import MAX_MESSAGE_BYTES, Message, ProtocolError, decode, encode

#: The port a session listens on unless the GM picks another. Chosen from the
#: dynamic/private range so it collides with nothing registered.
DEFAULT_PORT = 47331

#: How long :meth:`Transport.connect` waits for the TCP handshake by default.
CONNECT_TIMEOUT = 10.0

#: The socket timeout a *welcomed* connection runs under, on both ends. Its real
#: target is the send side: a peer that stops draining its socket (a suspended
#: laptop, a frozen process) would otherwise park ``sendall`` — and whoever is
#: broadcasting, eventually the GM's UI thread — forever; after this long the
#: send raises and the peer is treated as gone. Reads share the socket timeout,
#: so both read loops treat a timed-out ``recv`` as "still idle, keep waiting"
#: rather than a dead peer.
IO_TIMEOUT = 15.0

#: Bytes pulled from the socket per ``recv``. Large enough that a character
#: snapshot arrives in a few reads, small enough to stay a cheap allocation.
READ_CHUNK = 64 * 1024

_BACKLOG = 16


class TransportError(OSError):
    """A connection failed at the socket level, or was used after closing.

    Subclasses :class:`OSError` so callers can catch the socket failures and this
    one together — a reader loop only ever cares that the peer went away.
    """


class Connection:
    """A framed, thread-safe message channel over one connected socket.

    :meth:`send` is guarded by a lock, so several threads (the broadcast path and
    a GM command, say) can write to the same peer without interleaving halves of
    two JSON lines. Reading is *not* locked: exactly one reader thread per
    connection is the model everywhere above.
    """

    def __init__(self, sock: socket.socket, address: tuple[str, int] | None = None) -> None:
        self._sock = sock
        self._buffer = bytearray()
        self._send_lock = threading.Lock()
        self._closed = False
        self.address = address if address is not None else _peer_address(sock)

    # -- lifecycle ---------------------------------------------------------

    @property
    def closed(self) -> bool:
        return self._closed

    def set_timeout(self, seconds: float | None) -> None:
        """Bound the next socket operations, or pass ``None`` to block forever.

        The handshake reads under :data:`~.server.HANDSHAKE_TIMEOUT` and treats
        expiry as a dead peer; a welcomed connection runs under
        :data:`IO_TIMEOUT`, where an expired *send* means a stalled peer (and a
        raise) while the read loops swallow an expired ``recv`` and keep waiting.
        """
        self._sock.settimeout(seconds)

    def close(self) -> None:
        """Close the socket, unblocking a reader parked in ``recv``.

        The ``shutdown`` first is what makes that reliable on Windows, where
        closing a socket another thread is reading from does not always wake it.
        Both calls are best-effort: a peer that already vanished raises here, and
        there is nothing left to do about it.
        """
        self._closed = True
        try:
            self._sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            self._sock.close()
        except OSError:
            pass

    # -- messages ----------------------------------------------------------

    def send(self, message: Message) -> None:
        """Write one message as a framed line. Raises on an oversized or dead peer."""
        data = encode(message)
        with self._send_lock:
            if self._closed:
                raise TransportError("connection is closed")
            self._sock.sendall(data)

    def receive(self) -> Message | None:
        """Read the next message, or ``None`` once the peer closed cleanly.

        Raises :class:`~.protocol.ProtocolError` for a line that is malformed or
        over the size cap, and :class:`OSError` if the socket itself fails.
        """
        line = self._read_line()
        if line is None:
            return None
        return decode(line)

    def messages(self) -> Iterator[Message]:
        """Yield messages until the peer closes. Errors propagate to the caller."""
        while True:
            message = self.receive()
            if message is None:
                return
            yield message

    def _read_line(self) -> bytes | None:
        """Pull bytes until one complete frame is buffered; ``None`` at EOF."""
        while True:
            newline = self._buffer.find(b"\n")
            if newline >= 0:
                line = bytes(self._buffer[:newline])
                del self._buffer[: newline + 1]
                return line
            if len(self._buffer) > MAX_MESSAGE_BYTES:
                # No newline in more than a whole message's worth of bytes: the
                # peer is either broken or trying to exhaust our memory. Refuse
                # before buffering any further.
                raise ProtocolError(
                    f"no framing newline within {MAX_MESSAGE_BYTES} bytes; peer refused"
                )
            chunk = self._sock.recv(READ_CHUNK)
            if not chunk:
                # A trailing partial line is dropped: it is by definition an
                # unfinished message, and there will be no more bytes for it.
                return None
            self._buffer.extend(chunk)


# --------------------------------------------------------------------------
# The transport seam
# --------------------------------------------------------------------------


class Listener(ABC):
    """A server socket handing out :class:`Connection` objects."""

    @property
    @abstractmethod
    def address(self) -> tuple[str, int]:
        """The bound ``(host, port)`` — the real port when 0 was requested."""

    @abstractmethod
    def accept(self) -> Connection | None:
        """Block for the next peer, or return ``None`` once the listener closed."""

    @abstractmethod
    def close(self) -> None:
        """Stop listening; a blocked :meth:`accept` returns ``None``."""


class Transport(ABC):
    """How a session reaches the network — direct TCP now, a relay later.

    A relay implementation keeps these two methods and changes nothing above:
    ``listen`` registers the session with the relay and yields connections it
    forwards, ``connect`` dials the relay and asks to be joined to a session.
    """

    @abstractmethod
    def listen(self, host: str, port: int) -> Listener:
        """Start accepting connections on ``(host, port)``; port 0 picks a free one."""

    @abstractmethod
    def connect(self, host: str, port: int, *, timeout: float = CONNECT_TIMEOUT) -> Connection:
        """Dial a listening session and return the framed channel to it."""


class TcpListener(Listener):
    """A plain TCP server socket."""

    def __init__(self, host: str, port: int) -> None:
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._closed = False
        try:
            self._sock.bind((host, port))
            self._sock.listen(_BACKLOG)
        except OSError as exc:
            self._sock.close()
            raise TransportError(f"cannot listen on {host}:{port}: {exc}") from exc
        bound_host, bound_port = self._sock.getsockname()[:2]
        self._address = (str(bound_host), int(bound_port))

    @property
    def address(self) -> tuple[str, int]:
        return self._address

    def accept(self) -> Connection | None:
        try:
            sock, address = self._sock.accept()
        except OSError:
            # Either we closed the listener (the normal way out of the accept
            # loop) or the socket broke; both mean "stop accepting".
            return None
        if self._closed:
            sock.close()
            return None
        return Connection(sock, (str(address[0]), int(address[1])))

    def close(self) -> None:
        self._closed = True
        try:
            self._sock.close()
        except OSError:
            pass


class TcpTransport(Transport):
    """The direct transport: the GM's machine listens, players dial in."""

    def listen(self, host: str, port: int) -> Listener:
        return TcpListener(host, port)

    def connect(self, host: str, port: int, *, timeout: float = CONNECT_TIMEOUT) -> Connection:
        try:
            sock = socket.create_connection((host, port), timeout=timeout)
        except OSError as exc:
            raise TransportError(f"cannot reach {host}:{port}: {exc}") from exc
        sock.settimeout(None)
        return Connection(sock, (host, int(port)))


def _peer_address(sock: socket.socket) -> tuple[str, int]:
    """The connected peer's ``(host, port)``, or a placeholder if it is gone."""
    try:
        peer = sock.getpeername()
    except OSError:
        return ("", 0)
    return (str(peer[0]), int(peer[1]))

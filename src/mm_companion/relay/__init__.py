"""The public relay: a dumb pipe that pairs two *inbound* connections.

Run it with ``python -m mm_companion.relay`` on anything with a public IP. It is
what makes MM-Companion sessions work for players behind carrier-grade NAT, where
no port forward can ever help: the GM dials out, the player dials out, and the
relay splices the two byte streams together.

**It is deliberately, aggressively dumb.** It reads exactly one small JSON
envelope per connection (the protocol is written down in
:mod:`mm_companion.core.session.relay`) and forwards every byte after that
unread. It never parses a session message, never stores a character, never
persists anything, and holds no state beyond "which two sockets are paired". That
is a *cost* decision as much as a privacy one — a stateless ``selectors`` loop
serves thousands of tables from one small box, while a thread per connection or a
message parser would need a fleet.

**It never dials outward.** Every socket here arrives from ``accept()``; there is
no ``connect`` in this module at all. That is what stops an open relay from being
usable as a general-purpose proxy against third parties, and it is a property to
keep on purpose rather than by accident.

**TLS terminates here** when ``--cert``/``--key`` are given, which is how a
player's app gets a verified connection with no configuration. The operator of a
relay can therefore see the traffic passing through it; running your own is one
command, which is the answer for anyone who minds.

Everything is capped: sessions, clients per session, bytes per second per
session, idle time, and absolute session age.
"""

from __future__ import annotations

import json
import logging
import secrets
import selectors
import socket
import ssl
import time
from dataclasses import dataclass, field

from mm_companion.core.session.net import tune_socket
from mm_companion.core.session.relay import (
    ENVELOPE_ACCEPT,
    ENVELOPE_ERROR,
    ENVELOPE_HOST,
    ENVELOPE_INCOMING,
    ENVELOPE_JOIN,
    ENVELOPE_OK,
    ENVELOPE_PING,
    ENVELOPE_PONG,
    ERROR_BAD_ENVELOPE,
    ERROR_BAD_SECRET,
    ERROR_RELAY_FULL,
    ERROR_SESSION_EXISTS,
    ERROR_SESSION_FULL,
    ERROR_UNKNOWN_SESSION,
    ERROR_UNKNOWN_STREAM,
    MAX_ENVELOPE_BYTES,
)

__all__ = ["RelayServer", "RelayLimits", "DEFAULT_LIMITS", "main"]

log = logging.getLogger("mm_companion.relay")

#: Bytes pulled per ``recv``, and the most reads taken in one turn of the loop so
#: one busy pair cannot starve the others.
READ_CHUNK = 64 * 1024
MAX_READS_PER_EVENT = 8

#: How much unsent data may pile up for a slow peer before its partner is stopped
#: from reading. This is the backpressure that keeps memory bounded: a player on
#: a bad connection slows their own stream instead of growing a buffer here.
HIGH_WATER = 512 * 1024
LOW_WATER = 128 * 1024

#: How often the loop wakes with nothing to do — refills rate budgets and expires
#: idle connections.
TICK = 0.5

_MAX_SESSION_ID = 128
_MAX_SECRET = 256


@dataclass(frozen=True)
class RelayLimits:
    """Every cap the relay enforces. All of them are command-line options."""

    #: Concurrent sessions (a GM hosting counts as one).
    max_sessions: int = 200
    #: Connections one session may have, the GM's control link included.
    max_clients: int = 16
    #: Sustained bytes per second per session, in each direction combined.
    rate_bytes: int = 256 * 1024
    #: Burst allowed above the sustained rate.
    burst_bytes: int = 1024 * 1024
    #: A connection with no traffic for this long is dropped, and its paired half
    #: with it. "Traffic" means bytes actually read off the socket, so only a peer
    #: that keeps *sending* survives an idle stretch: the GM's control link pings
    #: every 30 s and is safe, while a session data stream sends nothing at all
    #: while a table talks. Until the client keepalive lands, a deployment facing
    #: quiet tables has to raise this (see ``deploy/mm-relay.service``).
    idle_timeout: float = 120.0
    #: A session is closed at this age however busy it is.
    session_ttl: float = 12 * 3600.0


DEFAULT_LIMITS = RelayLimits()

# Connection states.
_HANDSHAKE = "handshake"  # TLS in progress
_ENVELOPE = "envelope"  # reading the one frame we parse
_CONTROL = "control"  # a GM's control link
_WAITING = "waiting"  # a player, held until the GM accepts
_PIPED = "piped"  # spliced to a partner


class _Peer:
    """One accepted socket and everything the loop needs to know about it."""

    __slots__ = (
        "sock",
        "address",
        "state",
        "inbuf",
        "outbuf",
        "partner",
        "session",
        "stream",
        "last_active",
        "events",
        "registered",
        "back_paused",
        "rate_paused",
        "handshake_events",
        "closing",
    )

    def __init__(self, sock: socket.socket, address: tuple, now: float) -> None:
        self.sock = sock
        self.address = address
        self.state = _ENVELOPE
        self.inbuf = bytearray()
        self.outbuf = bytearray()
        self.partner: _Peer | None = None
        self.session: _Session | None = None
        self.stream = ""
        self.last_active = now
        self.events = 0
        self.registered = False
        self.back_paused = False
        self.rate_paused = False
        self.handshake_events = selectors.EVENT_READ
        self.closing = False

    @property
    def paused(self) -> bool:
        return self.back_paused or self.rate_paused


@dataclass
class _Session:
    """A GM's registration: the control link, and who is waiting or paired."""

    id: str
    secret: str
    control: _Peer
    created: float
    allowance: float
    pending: dict[str, _Peer] = field(default_factory=dict)
    peers: set = field(default_factory=set)

    def size(self) -> int:
        return len(self.peers) + len(self.pending)


class RelayServer:
    """A single-threaded ``selectors`` loop over every connection.

    Constructing it binds the listening socket (so ``port=0`` hands back a real
    port through :attr:`address`); :meth:`serve_forever` runs the loop and
    :meth:`stop` is safe to call from another thread.
    """

    def __init__(
        self,
        host: str = "0.0.0.0",
        port: int = 47332,
        *,
        limits: RelayLimits = DEFAULT_LIMITS,
        ssl_context: ssl.SSLContext | None = None,
        backlog: int = 128,
    ) -> None:
        self.limits = limits
        self._ssl_context = ssl_context
        self._sessions: dict[str, _Session] = {}
        self._peers: set[_Peer] = set()
        self._selector = selectors.DefaultSelector()
        self._running = False
        self._last_tick = time.monotonic()

        self._listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._listener.bind((host, port))
        self._listener.listen(backlog)
        self._listener.setblocking(False)
        self._address = self._listener.getsockname()[:2]
        self._selector.register(self._listener, selectors.EVENT_READ, None)

    # -- lifecycle ---------------------------------------------------------

    @property
    def address(self) -> tuple[str, int]:
        """The bound ``(host, port)`` — the real port when 0 was asked for."""
        return (str(self._address[0]), int(self._address[1]))

    @property
    def running(self) -> bool:
        return self._running

    def session_count(self) -> int:
        return len(self._sessions)

    def connection_count(self) -> int:
        return len(self._peers)

    def serve_forever(self) -> None:
        """Run until :meth:`stop`."""
        self._running = True
        log.info("relay listening on %s:%s", *self.address)
        try:
            while self._running:
                for key, mask in self._selector.select(timeout=TICK):
                    if key.data is None:
                        self._accept()
                    else:
                        self._service(key.data, mask)
                self._tick()
        finally:
            self._running = False
            self._shutdown()

    def stop(self) -> None:
        """Ask the loop to finish. Safe from any thread."""
        self._running = False

    def _shutdown(self) -> None:
        for peer in list(self._peers):
            self._close(peer, quiet=True)
        try:
            self._selector.unregister(self._listener)
        except (KeyError, ValueError):
            pass
        self._listener.close()
        self._selector.close()

    # -- accepting ---------------------------------------------------------

    def _accept(self) -> None:
        try:
            sock, address = self._listener.accept()
        except OSError:
            return
        now = time.monotonic()
        # Nagle hurts most here: every byte of a table's traffic is forwarded
        # through this loop, and a delayed ACK on one hop would tax both.
        tune_socket(sock)
        sock.setblocking(False)
        peer = _Peer(sock, address, now)
        if self._ssl_context is not None:
            try:
                peer.sock = self._ssl_context.wrap_socket(
                    sock, server_side=True, do_handshake_on_connect=False
                )
            except (OSError, ssl.SSLError):
                sock.close()
                return
            peer.state = _HANDSHAKE
        self._peers.add(peer)
        self._set_interest(peer)
        if peer.state == _HANDSHAKE:
            self._do_handshake(peer)

    def _do_handshake(self, peer: _Peer) -> None:
        try:
            peer.sock.do_handshake()
        except ssl.SSLWantReadError:
            peer.handshake_events = selectors.EVENT_READ
            self._set_interest(peer)
            return
        except ssl.SSLWantWriteError:
            peer.handshake_events = selectors.EVENT_WRITE
            self._set_interest(peer)
            return
        except (OSError, ssl.SSLError):
            self._close(peer, quiet=True)
            return
        peer.state = _ENVELOPE
        peer.last_active = time.monotonic()
        self._set_interest(peer)

    # -- the event loop ----------------------------------------------------

    def _service(self, peer: _Peer, mask: int) -> None:
        if peer.state == _HANDSHAKE:
            self._do_handshake(peer)
            return
        if mask & selectors.EVENT_WRITE:
            self._flush(peer)
        if peer.closing or peer.sock.fileno() < 0:
            return
        if mask & selectors.EVENT_READ and not peer.paused:
            self._read(peer)

    def _read(self, peer: _Peer) -> None:
        for _ in range(MAX_READS_PER_EVENT):
            try:
                chunk = peer.sock.recv(READ_CHUNK)
            except (BlockingIOError, ssl.SSLWantReadError, ssl.SSLWantWriteError):
                return
            except OSError:
                self._close(peer)
                return
            if not chunk:
                self._close(peer)
                return
            peer.last_active = time.monotonic()
            if peer.state == _PIPED:
                if not self._forward(peer, chunk):
                    return
            else:
                if not self._consume_envelope(peer, chunk):
                    return
            if peer.paused or peer.closing:
                return

    def _forward(self, peer: _Peer, chunk: bytes) -> bool:
        """Hand *chunk* to the partner. Returns False once the pair is gone."""
        partner = peer.partner
        if partner is None or partner.closing:
            self._close(peer)
            return False
        session = peer.session
        if session is not None:
            session.allowance -= len(chunk)
            if session.allowance <= 0:
                self._pause_rate(session, True)
        self._queue(partner, chunk)
        if len(partner.outbuf) >= HIGH_WATER and not peer.back_paused:
            peer.back_paused = True
            self._set_interest(peer)
        return True

    def _consume_envelope(self, peer: _Peer, chunk: bytes) -> bool:
        """Buffer until one envelope is complete, then act on it."""
        peer.inbuf.extend(chunk)
        newline = peer.inbuf.find(b"\n")
        if newline < 0:
            if len(peer.inbuf) > MAX_ENVELOPE_BYTES:
                self._refuse(peer, ERROR_BAD_ENVELOPE, "that is not a relay envelope")
                return False
            return True
        line = bytes(peer.inbuf[:newline])
        del peer.inbuf[: newline + 1]
        try:
            envelope = json.loads(line.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._refuse(peer, ERROR_BAD_ENVELOPE, "that envelope is not readable JSON")
            return False
        if not isinstance(envelope, dict):
            self._refuse(peer, ERROR_BAD_ENVELOPE, "that envelope is not an object")
            return False
        return self._dispatch(peer, envelope)

    def _dispatch(self, peer: _Peer, envelope: dict) -> bool:
        kind = envelope.get("type")
        if peer.state == _CONTROL:
            if kind == ENVELOPE_PING:
                self._send(peer, {"type": ENVELOPE_PONG})
                return True
            if kind == ENVELOPE_PONG:
                return True
            self._refuse(peer, ERROR_BAD_ENVELOPE, "a control link only pings")
            return False
        if kind == ENVELOPE_HOST:
            return self._host(peer, envelope)
        if kind == ENVELOPE_JOIN:
            return self._join(peer, envelope)
        if kind == ENVELOPE_ACCEPT:
            return self._accept_stream(peer, envelope)
        self._refuse(peer, ERROR_BAD_ENVELOPE, f"unknown envelope {kind!r}")
        return False

    # -- the three envelopes that matter -----------------------------------

    def _host(self, peer: _Peer, envelope: dict) -> bool:
        session_id = _text(envelope.get("session"), _MAX_SESSION_ID)
        secret = _text(envelope.get("secret"), _MAX_SECRET)
        if not session_id or not secret:
            self._refuse(peer, ERROR_BAD_ENVELOPE, "a session needs an id and a secret")
            return False
        if session_id in self._sessions:
            self._refuse(
                peer,
                ERROR_SESSION_EXISTS,
                "that session is already registered on this relay",
            )
            return False
        if len(self._sessions) >= self.limits.max_sessions:
            self._refuse(peer, ERROR_RELAY_FULL, "this relay is at capacity; try again later")
            return False
        session = _Session(
            id=session_id,
            secret=secret,
            control=peer,
            created=time.monotonic(),
            allowance=float(self.limits.burst_bytes),
        )
        self._sessions[session_id] = session
        peer.session = session
        peer.state = _CONTROL
        session.peers.add(peer)
        self._send(peer, {"type": ENVELOPE_OK, "session": session_id})
        log.info("session %s registered from %s", _short(session_id), peer.address[0])
        return True

    def _join(self, peer: _Peer, envelope: dict) -> bool:
        session_id = _text(envelope.get("session"), _MAX_SESSION_ID)
        session = self._sessions.get(session_id)
        if session is None:
            self._refuse(
                peer,
                ERROR_UNKNOWN_SESSION,
                "no session with that join code is on this relay right now",
            )
            return False
        if session.size() >= self.limits.max_clients:
            self._refuse(peer, ERROR_SESSION_FULL, "that session is full")
            return False
        stream = secrets.token_hex(8)
        peer.state = _WAITING
        peer.session = session
        peer.stream = stream
        session.pending[stream] = peer
        self._send(session.control, {"type": ENVELOPE_INCOMING, "stream": stream})
        return True

    def _accept_stream(self, peer: _Peer, envelope: dict) -> bool:
        session_id = _text(envelope.get("session"), _MAX_SESSION_ID)
        secret = _text(envelope.get("secret"), _MAX_SECRET)
        stream = _text(envelope.get("stream"), 64)
        session = self._sessions.get(session_id)
        if session is None:
            self._refuse(peer, ERROR_UNKNOWN_SESSION, "that session is not on this relay")
            return False
        if not secrets.compare_digest(secret.encode("utf-8"), session.secret.encode("utf-8")):
            self._refuse(peer, ERROR_BAD_SECRET, "that is not the host of this session")
            return False
        player = session.pending.pop(stream, None)
        if player is None or player.closing:
            self._refuse(peer, ERROR_UNKNOWN_STREAM, "that player is no longer waiting")
            return False

        peer.session = session
        peer.state = _PIPED
        player.state = _PIPED
        peer.partner = player
        player.partner = peer
        session.peers.add(peer)
        session.peers.add(player)
        self._send(peer, {"type": ENVELOPE_OK, "stream": stream})
        self._send(player, {"type": ENVELOPE_OK, "stream": stream})
        # Anything either side sent behind its envelope belongs to the stream.
        if peer.inbuf:
            self._queue(player, bytes(peer.inbuf))
            peer.inbuf.clear()
        if player.inbuf:
            self._queue(peer, bytes(player.inbuf))
            player.inbuf.clear()
        return True

    # -- writing -----------------------------------------------------------

    def _send(self, peer: _Peer, envelope: dict) -> None:
        self._queue(peer, json.dumps(envelope, separators=(",", ":")).encode("utf-8") + b"\n")

    def _queue(self, peer: _Peer, data: bytes) -> None:
        peer.outbuf.extend(data)
        self._flush(peer)

    def _flush(self, peer: _Peer) -> None:
        while peer.outbuf and not peer.closing:
            try:
                sent = peer.sock.send(peer.outbuf)
            except (BlockingIOError, ssl.SSLWantWriteError, ssl.SSLWantReadError):
                break
            except OSError:
                self._close(peer)
                return
            if sent <= 0:
                break
            del peer.outbuf[:sent]
            peer.last_active = time.monotonic()
        partner = peer.partner
        if partner is not None and partner.back_paused and len(peer.outbuf) <= LOW_WATER:
            partner.back_paused = False
            self._set_interest(partner)
        self._set_interest(peer)

    def _refuse(self, peer: _Peer, code: str, message: str) -> None:
        """Say why, then drop the connection. Never leaves a peer guessing."""
        self._send(peer, {"type": ENVELOPE_ERROR, "code": code, "message": message})
        peer.closing = True
        self._close(peer, drain=True)

    # -- interest and teardown ---------------------------------------------

    def _set_interest(self, peer: _Peer) -> None:
        if peer.closing:
            return
        if peer.state == _HANDSHAKE:
            events = peer.handshake_events
        else:
            events = 0
            if not peer.paused:
                events |= selectors.EVENT_READ
            if peer.outbuf:
                events |= selectors.EVENT_WRITE
        if events == peer.events and peer.registered == bool(events):
            return
        try:
            if not events:
                if peer.registered:
                    self._selector.unregister(peer.sock)
                    peer.registered = False
            elif peer.registered:
                self._selector.modify(peer.sock, events, peer)
            else:
                self._selector.register(peer.sock, events, peer)
                peer.registered = True
        except (KeyError, ValueError, OSError):
            self._close(peer, quiet=True)
            return
        peer.events = events

    def _pause_rate(self, session: _Session, paused: bool) -> None:
        for member in list(session.peers):
            if member.rate_paused != paused and member.state == _PIPED:
                member.rate_paused = paused
                self._set_interest(member)

    def _close(self, peer: _Peer, *, quiet: bool = False, drain: bool = False) -> None:
        if peer not in self._peers:
            return
        if drain:
            # Best effort: push the refusal out before the socket goes away, so
            # the other end reads a reason instead of a reset.
            try:
                peer.sock.setblocking(True)
                peer.sock.settimeout(0.2)
                if peer.outbuf:
                    peer.sock.sendall(bytes(peer.outbuf))
                    peer.outbuf.clear()
            except OSError:
                pass
        peer.closing = True
        self._peers.discard(peer)
        if peer.registered:
            try:
                self._selector.unregister(peer.sock)
            except (KeyError, ValueError, OSError):
                pass
            peer.registered = False
        try:
            peer.sock.close()
        except OSError:
            pass

        session = peer.session
        if session is not None:
            session.peers.discard(peer)
            if peer.stream:
                session.pending.pop(peer.stream, None)
            if session.control is peer:
                self._close_session(session, quiet=quiet)
        partner, peer.partner = peer.partner, None
        if partner is not None:
            partner.partner = None
            self._flush(partner)
            self._close(partner, quiet=True)

    def _close_session(self, session: _Session, *, quiet: bool = False) -> None:
        """The GM went away: the session and every stream in it go with them."""
        if self._sessions.get(session.id) is not session:
            return
        del self._sessions[session.id]
        if not quiet:
            log.info("session %s ended", _short(session.id))
        for member in list(session.peers) + list(session.pending.values()):
            member.session = None
            self._close(member, quiet=True)

    # -- the periodic sweep ------------------------------------------------

    def _tick(self) -> None:
        now = time.monotonic()
        elapsed, self._last_tick = now - self._last_tick, now
        if elapsed <= 0:
            return
        for session in list(self._sessions.values()):
            if now - session.created > self.limits.session_ttl:
                self._close_session(session)
                continue
            was_empty = session.allowance <= 0
            session.allowance = min(
                float(self.limits.burst_bytes),
                session.allowance + self.limits.rate_bytes * elapsed,
            )
            if was_empty and session.allowance > 0:
                self._pause_rate(session, False)
        for peer in list(self._peers):
            if now - peer.last_active > self.limits.idle_timeout:
                self._close(peer, quiet=True)


def _text(value: object, limit: int) -> str:
    """A string field from an envelope, or ``""`` if it is not one (or too long)."""
    if not isinstance(value, str) or len(value) > limit:
        return ""
    return value


def _short(session_id: str) -> str:
    """Session ids end up in logs; only enough of one to follow a session."""
    return session_id[:8]


def main(argv: list[str] | None = None) -> int:
    """Entry point for ``python -m mm_companion.relay``."""
    from .cli import run

    return run(argv)

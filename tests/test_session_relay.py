"""The relay: envelope handling, the caps, and a real session over one.

Everything here runs a genuine :class:`~mm_companion.relay.RelayServer` on
loopback with an ephemeral port, in a daemon thread. No test touches the network
beyond ``127.0.0.1``, and none binds a fixed port.
"""

from __future__ import annotations

import json
import shutil
import socket
import ssl
import subprocess
import threading
import time

import pytest

from mm_companion.core.session import discovery
from mm_companion.core.session import relay as relay_transport
from mm_companion.core.session.client import SessionClient
from mm_companion.core.session.model import new_session
from mm_companion.core.session.net import Connection
from mm_companion.core.session.relay import (
    ENVELOPE_ERROR,
    ENVELOPE_INCOMING,
    ENVELOPE_OK,
    ERROR_BAD_ENVELOPE,
    ERROR_BAD_SECRET,
    ERROR_RELAY_FULL,
    ERROR_SESSION_EXISTS,
    ERROR_SESSION_FULL,
    ERROR_UNKNOWN_SESSION,
    RelayError,
    RelayTransport,
    RelayUrlError,
    parse_relay_url,
    relay_url,
)
from mm_companion.core.session.server import SessionServer
from mm_companion.relay import RelayLimits, RelayServer

TIMEOUT = 5.0


# --------------------------------------------------------------------------
# Fixtures and helpers
# --------------------------------------------------------------------------


class _Box:
    """A relay running in a thread, plus the URLs that point at it."""

    def __init__(self, server: RelayServer, thread: threading.Thread) -> None:
        self.server = server
        self.thread = thread

    @property
    def address(self) -> tuple[str, int]:
        return self.server.address

    def url(self, session_id: str) -> str:
        host, port = self.server.address
        return relay_url(f"{relay_transport.RELAY_SCHEME_PLAIN}://{host}:{port}", session_id)

    def transport(self, session_id: str) -> RelayTransport:
        return RelayTransport(self.url(session_id))


def _run(server: RelayServer) -> _Box:
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    _wait_for(lambda: server.running)
    return _Box(server, thread)


@pytest.fixture
def box():
    """A plaintext relay with generous caps."""
    server = RelayServer("127.0.0.1", 0)
    running = _run(server)
    yield running
    server.stop()
    running.thread.join(timeout=TIMEOUT)


def _capped(**limits) -> RelayLimits:
    return RelayLimits(**limits)


def _wait_for(predicate, timeout: float = TIMEOUT) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


class Raw:
    """A hand-driven relay connection, for testing the envelope layer itself."""

    def __init__(self, address: tuple[str, int]) -> None:
        self.sock = socket.create_connection(address, timeout=TIMEOUT)
        self.buffer = bytearray()

    def send(self, **envelope) -> None:
        self.sock.sendall(json.dumps(envelope).encode("utf-8") + b"\n")

    def send_raw(self, data: bytes) -> None:
        self.sock.sendall(data)

    def read(self) -> dict:
        while True:
            newline = self.buffer.find(b"\n")
            if newline >= 0:
                line = bytes(self.buffer[:newline])
                del self.buffer[: newline + 1]
                return json.loads(line.decode("utf-8"))
            chunk = self.sock.recv(4096)
            if not chunk:
                raise AssertionError("the relay closed without answering")
            self.buffer.extend(chunk)

    def read_bytes(self, count: int) -> bytes:
        while len(self.buffer) < count:
            chunk = self.sock.recv(4096)
            if not chunk:
                break
            self.buffer.extend(chunk)
        data, self.buffer = bytes(self.buffer[:count]), bytearray(self.buffer[count:])
        return data

    def close(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass


def _pair(box: _Box, session_id: str = "s1", secret: str = "hush") -> tuple[Raw, Raw, Raw]:
    """Register a host, join a player, and splice them. Returns (control, host, player)."""
    control = Raw(box.address)
    control.send(type="relay_host", session=session_id, secret=secret)
    assert control.read()["type"] == ENVELOPE_OK

    player = Raw(box.address)
    player.send(type="relay_join", session=session_id)
    incoming = control.read()
    assert incoming["type"] == ENVELOPE_INCOMING

    host = Raw(box.address)
    host.send(type="relay_accept", session=session_id, secret=secret, stream=incoming["stream"])
    assert host.read()["type"] == ENVELOPE_OK
    assert player.read()["type"] == ENVELOPE_OK
    return control, host, player


# --------------------------------------------------------------------------
# Relay URLs
# --------------------------------------------------------------------------


def test_a_relay_url_carries_the_relay_and_the_session():
    address = parse_relay_url("mmrelay://relay.example.net:9000/abc123")
    assert (address.host, address.port, address.session_id) == ("relay.example.net", 9000, "abc123")
    assert address.tls is True


def test_a_relay_url_defaults_to_the_relay_port():
    assert (
        parse_relay_url("mmrelay://relay.example.net/abc").port
        == relay_transport.DEFAULT_RELAY_PORT
    )


def test_the_plain_scheme_turns_tls_off():
    address = parse_relay_url("mmrelay+tcp://127.0.0.1:9000/abc")
    assert address.tls is False
    assert address.url() == "mmrelay+tcp://127.0.0.1:9000/abc"


@pytest.mark.parametrize("text", ["", "relay.example.net/abc", "http://relay.example.net/abc"])
def test_something_that_is_not_a_relay_url_is_refused(text):
    with pytest.raises(RelayUrlError):
        parse_relay_url(text)


def test_a_relay_url_without_a_host_is_refused():
    with pytest.raises(RelayUrlError):
        parse_relay_url("mmrelay:///abc")


@pytest.mark.parametrize(
    "base",
    ["relay.example.net", "mmrelay://relay.example.net", "mmrelay://relay.example.net/old"],
)
def test_a_configured_relay_becomes_a_join_code_host(base):
    assert (
        relay_url(base, "sid")
        == f"mmrelay://relay.example.net:{relay_transport.DEFAULT_RELAY_PORT}/sid"
    )


def test_a_relay_url_needs_both_halves():
    with pytest.raises(RelayUrlError):
        relay_url("", "sid")
    with pytest.raises(RelayUrlError):
        relay_url("relay.example.net", "")


# --------------------------------------------------------------------------
# Registration into the transport seam
# --------------------------------------------------------------------------


def test_importing_the_session_package_registers_the_relay_transport():
    for scheme in (relay_transport.RELAY_SCHEME, relay_transport.RELAY_SCHEME_PLAIN):
        assert discovery.transports.get(scheme) is not None


def test_a_relay_join_code_resolves_to_a_relay_transport():
    transport = discovery.transport_for("mmrelay://relay.example.net:9000/abc")
    assert isinstance(transport, RelayTransport)
    assert transport.relay.session_id == "abc"


def test_each_hosting_run_gets_its_own_secret():
    # The join code carries the session's *host token*, which every player sees;
    # authenticating the relay registration with it would let a player take the
    # session over at the relay.
    first = RelayTransport("mmrelay://relay.example.net/abc")
    second = RelayTransport("mmrelay://relay.example.net/abc")
    assert first.secret and first.secret != second.secret


# --------------------------------------------------------------------------
# The envelope protocol
# --------------------------------------------------------------------------


def test_a_host_registration_is_acknowledged(box):
    control = Raw(box.address)
    control.send(type="relay_host", session="s1", secret="hush")
    assert control.read() == {"type": ENVELOPE_OK, "session": "s1"}
    assert _wait_for(lambda: box.server.session_count() == 1)
    control.close()


def test_a_second_host_cannot_take_over_a_registered_session(box):
    control = Raw(box.address)
    control.send(type="relay_host", session="s1", secret="hush")
    control.read()

    other = Raw(box.address)
    other.send(type="relay_host", session="s1", secret="mine")
    answer = other.read()
    assert answer["type"] == ENVELOPE_ERROR
    assert answer["code"] == ERROR_SESSION_EXISTS
    control.close()
    other.close()


def test_joining_a_session_nobody_hosts_is_refused(box):
    player = Raw(box.address)
    player.send(type="relay_join", session="nobody")
    answer = player.read()
    assert answer["type"] == ENVELOPE_ERROR
    assert answer["code"] == ERROR_UNKNOWN_SESSION
    assert answer["message"]
    player.close()


def test_only_the_holder_of_the_secret_can_accept_a_stream(box):
    control = Raw(box.address)
    control.send(type="relay_host", session="s1", secret="hush")
    control.read()
    player = Raw(box.address)
    player.send(type="relay_join", session="s1")
    stream = control.read()["stream"]

    impostor = Raw(box.address)
    impostor.send(type="relay_accept", session="s1", secret="guess", stream=stream)
    answer = impostor.read()
    assert answer["type"] == ENVELOPE_ERROR
    assert answer["code"] == ERROR_BAD_SECRET
    for peer in (control, player, impostor):
        peer.close()


def test_a_paired_stream_forwards_bytes_verbatim_both_ways(box):
    control, host, player = _pair(box)
    # Not JSON, not a session message: the relay does not read what it carries.
    host.send_raw(b"\x00\x01binary\xff\n")
    assert player.read_bytes(10) == b"\x00\x01binary\xff\n"
    player.send_raw(b"back")
    assert host.read_bytes(4) == b"back"
    for peer in (control, host, player):
        peer.close()


def test_bytes_sent_behind_the_envelope_are_not_lost(box):
    """A player's first session bytes may share a segment with the join envelope."""
    control = Raw(box.address)
    control.send(type="relay_host", session="s1", secret="hush")
    control.read()

    player = Raw(box.address)
    player.send_raw(json.dumps({"type": "relay_join", "session": "s1"}).encode() + b"\nhello")
    stream = control.read()["stream"]

    host = Raw(box.address)
    host.send(type="relay_accept", session="s1", secret="hush", stream=stream)
    assert host.read()["type"] == ENVELOPE_OK
    assert host.read_bytes(5) == b"hello"
    for peer in (control, host, player):
        peer.close()


def test_rubbish_instead_of_an_envelope_is_refused_with_a_reason(box):
    peer = Raw(box.address)
    peer.send_raw(b"not json at all\n")
    answer = peer.read()
    assert answer["type"] == ENVELOPE_ERROR
    assert answer["code"] == ERROR_BAD_ENVELOPE
    peer.close()


def test_an_envelope_that_never_ends_is_refused(box):
    peer = Raw(box.address)
    peer.send_raw(b"x" * (relay_transport.MAX_ENVELOPE_BYTES + 1024))
    answer = peer.read()
    assert answer["code"] == ERROR_BAD_ENVELOPE
    peer.close()


def test_the_control_link_is_kept_warm_by_pings(box):
    control = Raw(box.address)
    control.send(type="relay_host", session="s1", secret="hush")
    control.read()
    control.send(type="relay_ping")
    assert control.read() == {"type": "relay_pong"}
    control.close()


def test_losing_the_host_takes_the_whole_session_with_it(box):
    control, host, player = _pair(box)
    control.close()
    assert _wait_for(lambda: box.server.session_count() == 0)
    assert _wait_for(lambda: box.server.connection_count() == 0)
    assert player.sock.recv(16) == b""
    host.close()
    player.close()


def test_one_player_leaving_closes_only_their_stream(box):
    control, host, player = _pair(box)
    player.close()
    assert _wait_for(lambda: box.server.connection_count() == 1)  # the control link
    assert box.server.session_count() == 1
    control.close()
    host.close()


# --------------------------------------------------------------------------
# The caps
# --------------------------------------------------------------------------


def test_the_relay_refuses_more_sessions_than_it_is_configured_for():
    server = RelayServer("127.0.0.1", 0, limits=_capped(max_sessions=1))
    running = _run(server)
    try:
        first = Raw(running.address)
        first.send(type="relay_host", session="s1", secret="hush")
        assert first.read()["type"] == ENVELOPE_OK

        second = Raw(running.address)
        second.send(type="relay_host", session="s2", secret="hush")
        answer = second.read()
        assert answer["code"] == ERROR_RELAY_FULL
        first.close()
        second.close()
    finally:
        server.stop()
        running.thread.join(timeout=TIMEOUT)


def test_a_session_refuses_more_clients_than_it_is_configured_for():
    # Two: the GM's control link, and one player.
    server = RelayServer("127.0.0.1", 0, limits=_capped(max_clients=2))
    running = _run(server)
    try:
        control, host, player = _pair(running)
        crowd = Raw(running.address)
        crowd.send(type="relay_join", session="s1")
        answer = crowd.read()
        assert answer["code"] == ERROR_SESSION_FULL
        for peer in (control, host, player, crowd):
            peer.close()
    finally:
        server.stop()
        running.thread.join(timeout=TIMEOUT)


def test_a_connection_that_says_nothing_is_dropped():
    server = RelayServer("127.0.0.1", 0, limits=_capped(idle_timeout=0.3))
    running = _run(server)
    try:
        peer = Raw(running.address)
        assert _wait_for(lambda: server.connection_count() == 1)
        assert _wait_for(lambda: server.connection_count() == 0, timeout=TIMEOUT)
        peer.close()
    finally:
        server.stop()
        running.thread.join(timeout=TIMEOUT)


def test_a_session_over_its_rate_is_slowed_rather_than_broken():
    server = RelayServer(
        "127.0.0.1", 0, limits=_capped(rate_bytes=2 * 1024 * 1024, burst_bytes=256 * 1024)
    )
    running = _run(server)
    try:
        control, host, player = _pair(running)
        # Comfortably more than one burst, so the budget has to run out and refill.
        payload = b"z" * (768 * 1024)
        started = time.monotonic()
        sender = threading.Thread(target=host.send_raw, args=(payload,), daemon=True)
        sender.start()
        received = player.read_bytes(len(payload))
        elapsed = time.monotonic() - started
        sender.join(timeout=TIMEOUT)

        assert received == payload  # throttled, never truncated
        assert elapsed > 0.3  # and it really was held back
        for peer in (control, host, player):
            peer.close()
    finally:
        server.stop()
        running.thread.join(timeout=TIMEOUT)


# --------------------------------------------------------------------------
# A real session over the relay
# --------------------------------------------------------------------------


def test_a_whole_session_runs_over_the_relay(box):
    state = new_session("Relay table")
    server = SessionServer(state, transport=box.transport(state.id), persist=False)
    server.start()
    try:
        client = SessionClient(
            box.url(state.id),
            0,
            token=state.host_token,
            display_name="Ada",
            transport=box.transport(state.id),
        )
        welcome = client.connect()
        assert welcome.session_name == "Relay table"

        client.send_snapshot({"name": "Ada", "power_level": 10})
        assert _wait_for(lambda: state.players[welcome.player_id].character.get("name") == "Ada")

        client.request_roll(label="Perception", bonus=5)
        assert _wait_for(lambda: len(state.rolls) == 1)
        assert state.rolls[0].label == "Perception"
        client.close()
    finally:
        server.stop()


def test_two_players_reach_the_same_hosted_session(box):
    state = new_session("Table")
    server = SessionServer(state, transport=box.transport(state.id), persist=False)
    server.start()
    clients = []
    try:
        for name in ("Ada", "Bo"):
            client = SessionClient(
                box.url(state.id),
                0,
                token=state.host_token,
                display_name=name,
                transport=box.transport(state.id),
            )
            client.connect()
            clients.append(client)
        assert _wait_for(lambda: len(server.connected_player_ids()) == 2)
    finally:
        for client in clients:
            client.close()
        server.stop()


def test_the_wrong_token_is_still_refused_through_a_relay(box):
    """The relay authenticates nothing about the session — the server still does."""
    state = new_session("Table")
    server = SessionServer(state, transport=box.transport(state.id), persist=False)
    server.start()
    try:
        client = SessionClient(
            box.url(state.id),
            0,
            token="not-the-token",
            display_name="Mallory",
            transport=box.transport(state.id),
        )
        with pytest.raises(Exception) as caught:
            client.connect()
        assert "token" in str(caught.value).lower() or getattr(caught.value, "code", "")
    finally:
        server.stop()


def test_joining_a_session_the_relay_does_not_know_fails_readably(box):
    transport = box.transport("no-such-session")
    with pytest.raises(RelayError) as caught:
        transport.connect()
    assert caught.value.code == ERROR_UNKNOWN_SESSION


def test_hosting_fails_when_the_relay_is_not_there():
    transport = RelayTransport("mmrelay+tcp://127.0.0.1:1/session")
    with pytest.raises(RelayError):
        transport.listen()


# --------------------------------------------------------------------------
# TLS
# --------------------------------------------------------------------------


@pytest.fixture(scope="session")
def tls_cert(tmp_path_factory):
    """A throwaway self-signed certificate for ``localhost``.

    Generated rather than checked in — a private key in the repository is a
    liability, and a checked-in certificate expires. Skips where openssl is not
    installed; CI has it.
    """
    openssl = shutil.which("openssl")
    if openssl is None:  # pragma: no cover - depends on the machine
        pytest.skip("openssl is not installed")
    directory = tmp_path_factory.mktemp("relay-tls")
    cert, key = directory / "cert.pem", directory / "key.pem"
    result = subprocess.run(
        [
            openssl,
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-keyout",
            str(key),
            "-out",
            str(cert),
            "-days",
            "3650",
            "-subj",
            "/CN=localhost",
            "-addext",
            "subjectAltName=DNS:localhost,IP:127.0.0.1",
        ],
        capture_output=True,
    )
    if result.returncode != 0:  # pragma: no cover - depends on the machine
        pytest.skip(f"openssl could not make a certificate: {result.stderr.decode()[:200]}")
    return cert, key


def test_a_session_runs_over_a_tls_relay(tls_cert):
    cert, key = tls_cert
    server_context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    server_context.load_cert_chain(str(cert), str(key))
    client_context = ssl.create_default_context(cafile=str(cert))

    relay_box = RelayServer("127.0.0.1", 0, ssl_context=server_context)
    running = _run(relay_box)
    state = new_session("Encrypted")
    port = relay_box.address[1]
    url = relay_url(f"mmrelay://localhost:{port}", state.id)
    server = SessionServer(
        state,
        transport=RelayTransport(url, ssl_context=client_context),
        persist=False,
    )
    try:
        server.start()
        client = SessionClient(
            url,
            port,
            token=state.host_token,
            display_name="Ada",
            transport=RelayTransport(url, ssl_context=client_context),
        )
        welcome = client.connect()
        assert welcome.session_name == "Encrypted"
        client.request_roll(label="Stealth")
        assert _wait_for(lambda: len(state.rolls) == 1)
        client.close()
    finally:
        server.stop()
        relay_box.stop()
        running.thread.join(timeout=TIMEOUT)


def test_a_relay_with_an_untrusted_certificate_is_refused(tls_cert):
    cert, key = tls_cert
    server_context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    server_context.load_cert_chain(str(cert), str(key))
    relay_box = RelayServer("127.0.0.1", 0, ssl_context=server_context)
    running = _run(relay_box)
    try:
        # The default context verifies against the system store, which has never
        # heard of this certificate — exactly what a player's app would do.
        transport = RelayTransport(relay_url(f"mmrelay://localhost:{relay_box.address[1]}", "s1"))
        with pytest.raises(RelayError):
            transport.connect()
    finally:
        relay_box.stop()
        running.thread.join(timeout=TIMEOUT)


# --------------------------------------------------------------------------
# The framing seam the relay needed
# --------------------------------------------------------------------------


def test_a_connection_can_be_primed_with_bytes_read_before_it_existed():
    left, right = socket.socketpair()
    try:
        connection = Connection(left, initial_buffer=b'{"type":"ping"}\n')
        message = connection.receive()
        assert message.TYPE == "ping"
    finally:
        left.close()
        right.close()

"""The session server and client over real loopback sockets.

Headless — no Qt. Every server binds an ephemeral port on ``127.0.0.1`` and every
worker thread is a daemon, so a failing assertion can never wedge the suite. The
helpers below (:class:`Events`, :func:`wait_for`) do the waiting; nothing here
sleeps for a fixed time.
"""

from __future__ import annotations

import json
import queue
import socket
import threading
import time
from pathlib import Path
from random import Random

import pytest

from mm_companion.core import storage
from mm_companion.core.session import client as client_mod
from mm_companion.core.session import net, store
from mm_companion.core.session import server as server_mod
from mm_companion.core.session.client import (
    EVENT_APPLY_CONDITION,
    EVENT_CONNECTED,
    EVENT_DISCONNECTED,
    EVENT_ERROR,
    EVENT_KICKED,
    EVENT_PONG,
    EVENT_ROLL,
    EVENT_ROSTER,
    SessionClient,
    SessionClientError,
)
from mm_companion.core.session.model import new_session
from mm_companion.core.session.net import Connection, TcpTransport
from mm_companion.core.session.protocol import (
    ERROR_BAD_TOKEN,
    ERROR_MALFORMED,
    ERROR_MOD_SKEW,
    ERROR_PROTOCOL_VERSION,
    ERROR_SESSION_FULL,
    MAX_MESSAGE_BYTES,
    CharacterSnapshot,
    ErrorMessage,
    Hello,
    Ping,
    ProtocolError,
    decode,
    encode,
)
from mm_companion.core.session.server import SessionServer

TIMEOUT = 5.0


@pytest.fixture(autouse=True)
def _home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv(storage.HOME_ENV_VAR, str(tmp_path))
    storage.ensure_workspace()
    return tmp_path


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


class Events:
    """Collects ``(kind, payload)`` callbacks and lets a test wait for one."""

    def __init__(self) -> None:
        self.queue: queue.Queue[tuple[str, dict]] = queue.Queue()
        self.seen: list[tuple[str, dict]] = []
        self._lock = threading.Lock()

    def __call__(self, kind: str, payload: dict) -> None:
        with self._lock:
            self.seen.append((kind, payload))
        self.queue.put((kind, payload))

    def next_of(self, kind: str, timeout: float = TIMEOUT) -> dict:
        """The payload of the next event of *kind*, skipping any others."""
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise AssertionError(f"no {kind!r} event within {timeout}s; saw {self.kinds()}")
            try:
                got_kind, payload = self.queue.get(timeout=remaining)
            except queue.Empty:
                continue
            if got_kind == kind:
                return payload

    def kinds(self) -> list[str]:
        with self._lock:
            return [kind for kind, _ in self.seen]


def wait_for(predicate, timeout: float = TIMEOUT, message: str = "condition") -> None:
    """Poll *predicate* until it is true, or fail."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError(f"{message} never became true within {timeout}s")


@pytest.fixture
def running_server(request: pytest.FixtureRequest):
    """Factory for a started server on an ephemeral loopback port."""
    servers: list[SessionServer] = []

    def make(**kwargs) -> SessionServer:
        state = kwargs.pop("state", None) or new_session("Test Table")
        srv = SessionServer(state, host="127.0.0.1", port=0, **kwargs)
        srv.start()
        servers.append(srv)
        return srv

    yield make

    for srv in servers:
        srv.stop()


@pytest.fixture
def connect(request: pytest.FixtureRequest):
    """Factory for a connected client, closed again at the end of the test."""
    clients: list[SessionClient] = []

    def make(srv: SessionServer, name: str = "Player", **kwargs) -> tuple[SessionClient, Events]:
        events = kwargs.pop("events", None) or Events()
        host, port = srv.address
        client = SessionClient(
            host, port, token=srv.state.host_token, display_name=name, on_event=events, **kwargs
        )
        client.connect(timeout=TIMEOUT)
        clients.append(client)
        return client, events

    yield make

    for client in clients:
        if client.connected:
            client.close()


def raw_connect(srv: SessionServer) -> Connection:
    """A framed connection with no handshake — for testing the refusals."""
    host, port = srv.address
    return TcpTransport().connect("127.0.0.1" if host == "0.0.0.0" else host, port)


def send_bytes(connection: Connection, data: bytes) -> None:
    """Write past the framing, to feed the server something it must reject."""
    connection._sock.sendall(data)  # noqa: SLF001 - deliberately bypassing encode()


def read_until(connection: Connection, kind: type):
    """The next message of *kind*, skipping the roster chatter a join stirs up."""
    for _ in range(20):
        message = connection.receive()
        if message is None:
            raise AssertionError(f"connection closed before any {kind.__name__}")
        if isinstance(message, kind):
            return message
    raise AssertionError(f"no {kind.__name__} arrived")


# --------------------------------------------------------------------------
# Framing (net.Connection)
# --------------------------------------------------------------------------


def test_connection_round_trips_a_message() -> None:
    left, right = socket.socketpair()
    a, b = Connection(left, ("test", 0)), Connection(right, ("test", 0))
    try:
        a.send(Ping(nonce=7))
        message = b.receive()
        assert isinstance(message, Ping)
        assert message.nonce == 7
    finally:
        a.close()
        b.close()


def test_connection_reassembles_a_split_frame() -> None:
    """A message arriving in two chunks is still one message."""
    left, right = socket.socketpair()
    a, b = Connection(left, ("test", 0)), Connection(right, ("test", 0))
    try:
        line = encode(Ping(nonce=3))
        left.sendall(line[:5])
        time.sleep(0.01)
        left.sendall(line[5:])
        assert b.receive() == Ping(nonce=3)
    finally:
        a.close()
        b.close()


def test_connection_splits_two_messages_in_one_chunk() -> None:
    left, right = socket.socketpair()
    a, b = Connection(left, ("test", 0)), Connection(right, ("test", 0))
    try:
        left.sendall(encode(Ping(nonce=1)) + encode(Ping(nonce=2)))
        assert b.receive() == Ping(nonce=1)
        assert b.receive() == Ping(nonce=2)
    finally:
        a.close()
        b.close()


def test_connection_returns_none_at_eof() -> None:
    left, right = socket.socketpair()
    b = Connection(right, ("test", 0))
    left.close()
    try:
        assert b.receive() is None
    finally:
        b.close()


def test_connection_refuses_an_unframed_flood() -> None:
    """Bytes without a newline are refused rather than buffered without bound."""
    left, right = socket.socketpair()
    b = Connection(right, ("test", 0))
    sender = threading.Thread(
        target=lambda: _push(left, b"x" * 8192, MAX_MESSAGE_BYTES // 8192 + 2), daemon=True
    )
    sender.start()
    try:
        with pytest.raises(ProtocolError) as excinfo:
            b.receive()
        assert "newline" in str(excinfo.value)
    finally:
        b.close()
        left.close()


def _push(sock: socket.socket, chunk: bytes, times: int) -> None:
    try:
        for _ in range(times):
            sock.sendall(chunk)
    except OSError:
        pass


def test_listener_reports_its_bound_port() -> None:
    listener = TcpTransport().listen("127.0.0.1", 0)
    try:
        assert listener.address[1] > 0
    finally:
        listener.close()


def test_accept_returns_none_once_closed() -> None:
    listener = TcpTransport().listen("127.0.0.1", 0)
    result: list[object] = []
    thread = threading.Thread(target=lambda: result.append(listener.accept()), daemon=True)
    thread.start()
    time.sleep(0.05)
    listener.close()
    thread.join(timeout=TIMEOUT)
    assert result == [None]


# --------------------------------------------------------------------------
# Handshake
# --------------------------------------------------------------------------


def test_join_welcomes_and_seats_the_player(running_server, connect) -> None:
    srv = running_server()
    client, events = connect(srv, "Volt")

    assert client.session_id == srv.state.id
    assert client.session_name == "Test Table"
    assert client.player_id
    assert client.player_token
    welcome = events.next_of(EVENT_CONNECTED)
    assert welcome["session_name"] == "Test Table"
    wait_for(lambda: client.player_id in srv.connected_player_ids(), message="player connected")


def test_roster_never_carries_a_player_token(running_server, connect) -> None:
    srv = running_server()
    client, _ = connect(srv, "Volt")
    assert client.player_token  # we know our own
    for entry in client.roster:
        assert "token" not in entry


def test_gm_has_a_slot_from_the_start(running_server) -> None:
    srv = running_server(gm_name="Boss")
    gm = srv.gm_slot()
    assert gm.is_gm and gm.display_name == "Boss"
    assert [slot.player_id for slot in srv.state.players.values() if slot.is_gm] == [gm.player_id]


def test_a_wrong_token_is_refused(running_server) -> None:
    srv = running_server()
    host, port = srv.address
    client = SessionClient(host, port, token="nope", display_name="Sneak")
    with pytest.raises(SessionClientError) as excinfo:
        client.connect(timeout=TIMEOUT)
    assert excinfo.value.code == ERROR_BAD_TOKEN
    assert len(srv.state.players) == 1  # only the GM


def test_a_non_ascii_token_is_refused_cleanly(running_server, connect) -> None:
    # ``secrets.compare_digest`` raises TypeError on non-ASCII str; compared
    # naively, a hostile token would kill the handler thread instead of being
    # refused. The refusal must look exactly like any other bad token.
    srv = running_server()
    host, port = srv.address
    sneak = SessionClient(host, port, token="žetón", display_name="Sneak")
    with pytest.raises(SessionClientError) as excinfo:
        sneak.connect(timeout=TIMEOUT)
    assert excinfo.value.code == ERROR_BAD_TOKEN

    client, _ = connect(srv, "Volt")  # the server shrugged it off
    assert client.connected


def test_a_non_ascii_player_token_joins_as_a_fresh_seat(running_server, connect) -> None:
    srv = running_server()
    client, _ = connect(srv, "Volt", player_token="žetón")
    assert client.connected
    assert len(srv.state.players) == 2  # the GM plus one new seat, no crash


def test_the_welcome_history_is_capped_to_the_recent_slice(
    running_server, connect, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The full log keeps growing across evenings; an uncapped welcome would
    # eventually outgrow MAX_MESSAGE_BYTES and refuse the join outright.
    monkeypatch.setattr(server_mod, "WELCOME_HISTORY_ROLLS", 5)
    state = new_session("Long Campaign")
    for _ in range(8):
        state.record_roll(player_id="p", player_name="Old Hand", die=12)
    srv = running_server(state=state)

    client, _ = connect(srv, "Late Joiner")

    assert [roll["seq"] for roll in client.history] == [4, 5, 6, 7, 8]


def test_a_protocol_mismatch_is_refused(running_server) -> None:
    srv = running_server()
    connection = raw_connect(srv)
    try:
        connection.send(Hello(token=srv.state.host_token, display_name="Old", protocol_version=999))
        message = connection.receive()
        assert isinstance(message, ErrorMessage)
        assert message.code == ERROR_PROTOCOL_VERSION
    finally:
        connection.close()


def test_a_non_hello_first_message_is_refused(running_server) -> None:
    srv = running_server()
    connection = raw_connect(srv)
    try:
        connection.send(Ping(nonce=1))
        message = connection.receive()
        assert isinstance(message, ErrorMessage)
        assert message.code == ERROR_MALFORMED
    finally:
        connection.close()


def test_a_full_session_is_refused(running_server) -> None:
    srv = running_server(max_clients=1)
    host, port = srv.address
    first = SessionClient(host, port, token=srv.state.host_token, display_name="One")
    first.connect(timeout=TIMEOUT)
    try:
        wait_for(lambda: len(srv.connected_player_ids()) == 1, message="first client seated")
        second = SessionClient(host, port, token=srv.state.host_token, display_name="Two")
        with pytest.raises(SessionClientError) as excinfo:
            second.connect(timeout=TIMEOUT)
        assert excinfo.value.code == ERROR_SESSION_FULL
    finally:
        first.close()


def test_mod_skew_warns_but_still_joins(running_server) -> None:
    srv = running_server(mod_fingerprint="aaaa")
    host, port = srv.address
    events = Events()
    client = SessionClient(
        host,
        port,
        token=srv.state.host_token,
        display_name="Modded",
        mod_fingerprint="bbbb",
        on_event=events,
    )
    client.connect(timeout=TIMEOUT)
    try:
        assert client.connected
        assert events.next_of(EVENT_ERROR)["code"] == ERROR_MOD_SKEW
    finally:
        client.close()


def test_matching_mod_fingerprints_do_not_warn(running_server) -> None:
    srv = running_server(mod_fingerprint="aaaa")
    host, port = srv.address
    events = Events()
    client = SessionClient(
        host,
        port,
        token=srv.state.host_token,
        display_name="Same",
        mod_fingerprint="aaaa",
        on_event=events,
    )
    client.connect(timeout=TIMEOUT)
    try:
        client.ping(5)
        assert events.next_of(EVENT_PONG)["nonce"] == 5  # the pong overtakes no warning
        assert ERROR_MOD_SKEW not in [p.get("code") for k, p in events.seen if k == EVENT_ERROR]
    finally:
        client.close()


# --------------------------------------------------------------------------
# Roster, snapshots
# --------------------------------------------------------------------------


def test_a_second_player_updates_the_first_ones_roster(running_server, connect) -> None:
    srv = running_server()
    first, _ = connect(srv, "Volt")
    connect(srv, "Mesa")

    wait_for(
        lambda: {"GM", "Volt", "Mesa"} == {entry["display_name"] for entry in first.roster},
        message="Volt's roster showing everyone",
    )


def test_a_snapshot_reaches_the_server_and_is_sanitized(running_server, connect) -> None:
    events = Events()
    srv = running_server(on_event=events)
    client, _ = connect(srv, "Volt")

    client.send_snapshot({"name": "Volt", "power_level": 10, "image_path": "C:/secret/volt.png"})
    payload = events.next_of(server_mod.EVENT_SNAPSHOT)

    assert payload["player_id"] == client.player_id
    assert payload["character"]["name"] == "Volt"
    assert "image_path" not in payload["character"]
    assert srv.state.players[client.player_id].character["power_level"] == 10


def test_a_snapshot_refreshes_the_roster_but_never_travels_in_it(running_server, connect) -> None:
    # The roster broadcast still fans out on a snapshot, but the character itself
    # stays on the server: a full table's combined sheets would otherwise outgrow
    # MAX_MESSAGE_BYTES and take every broadcast down with them.
    srv = running_server()
    first, first_events = connect(srv, "Volt")
    second, _ = connect(srv, "Mesa")

    second.send_snapshot({"name": "Mesa", "power_level": 8})

    # Volt sees exactly three roster refreshes: its own join, Mesa's join, and
    # Mesa's snapshot. Waiting on the count is what makes this deterministic.
    wait_for(
        lambda: first_events.kinds().count(EVENT_ROSTER) >= 3,
        message="the snapshot's roster refresh reaching Volt",
    )
    assert any(entry["display_name"] == "Mesa" for entry in first.roster)
    assert all("character" not in entry for entry in first.roster)
    wait_for(
        lambda: srv.state.players[second.player_id].character.get("power_level") == 8,
        message="the snapshot landing on the server",
    )


def test_a_snapshot_sent_by_the_raw_wire_is_still_sanitized(running_server) -> None:
    events = Events()
    srv = running_server(on_event=events)
    connection = raw_connect(srv)
    try:
        connection.send(Hello(token=srv.state.host_token, display_name="Raw"))
        connection.receive()  # welcome
        connection.send(CharacterSnapshot(character={"name": "Raw", "image_path": "/etc/passwd"}))
        payload = events.next_of(server_mod.EVENT_SNAPSHOT)
        assert "image_path" not in payload["character"]
    finally:
        connection.close()


def test_a_disconnect_keeps_the_slot_but_marks_it_offline(running_server, connect) -> None:
    events = Events()
    srv = running_server(on_event=events)
    client, _ = connect(srv, "Volt")
    player_id = client.player_id
    client.send_snapshot({"name": "Volt"})
    events.next_of(server_mod.EVENT_SNAPSHOT)

    client.close()
    events.next_of(server_mod.EVENT_PLAYER_LEFT)

    slot = srv.state.players[player_id]
    assert slot.connected is False
    assert slot.character["name"] == "Volt"  # the card survives the drop


def test_a_reconnect_reclaims_the_same_slot(running_server, connect) -> None:
    srv = running_server()
    client, _ = connect(srv, "Volt")
    player_id, token = client.player_id, client.player_token
    client.send_snapshot({"name": "Volt"})
    wait_for(lambda: bool(srv.state.players[player_id].character), message="snapshot stored")
    client.close()

    again, _ = connect(srv, "Volt", player_id=player_id, player_token=token)
    assert again.player_id == player_id
    # Two joins, one seat: the GM plus Volt.
    assert len(srv.state.players) == 2


def test_a_reconnect_receives_the_visible_history(running_server, connect) -> None:
    srv = running_server(rng=Random(1))
    client, _ = connect(srv, "Volt")
    client.request_roll("Athletics", bonus=5, dc=15)
    wait_for(lambda: len(srv.state.rolls) == 1, message="the roll landing")
    srv.roll(label="Secret", dc=15, hidden=True)
    client.close()

    again, _ = connect(srv, "Volt", player_id=client.player_id, player_token=client.player_token)
    labels = [roll["label"] for roll in again.history]
    assert labels == ["Athletics"]  # the hidden GM roll is not in there


def test_kick_removes_the_slot_and_drops_the_socket(running_server, connect) -> None:
    srv = running_server()
    client, events = connect(srv, "Volt")
    wait_for(lambda: client.player_id in srv.connected_player_ids(), message="seated")

    assert srv.kick(client.player_id, reason="behave") is True
    assert events.next_of(EVENT_KICKED)["reason"] == "behave"
    assert client.player_id not in srv.state.players
    events.next_of(EVENT_DISCONNECTED)


# --------------------------------------------------------------------------
# Rolls
# --------------------------------------------------------------------------


def test_the_server_resolves_a_roll_request(running_server, connect) -> None:
    srv = running_server(rng=Random(7))
    client, events = connect(srv, "Volt")

    client.request_roll("Athletics", bonus=6, penalty=2, dc=15)
    roll = events.next_of(EVENT_ROLL)

    assert roll["label"] == "Athletics"
    assert roll["player_name"] == "Volt"
    assert roll["bonus"] == 6 and roll["penalty"] == 2
    assert 1 <= roll["die"] <= 20
    assert roll["dc"] == 15
    assert roll["degree"] is not None
    assert roll["seq"] == 1


def test_a_roll_without_a_dc_is_ungraded(running_server, connect) -> None:
    srv = running_server(rng=Random(3))
    client, events = connect(srv, "Volt")

    client.request_roll("Just a d20")
    roll = events.next_of(EVENT_ROLL)

    assert roll["dc"] is None and roll["degree"] is None


def test_a_roll_reaches_every_player(running_server, connect) -> None:
    srv = running_server(rng=Random(11))
    first, first_events = connect(srv, "Volt")
    _second, second_events = connect(srv, "Mesa")

    first.request_roll("Perception", bonus=3, dc=12)
    assert first_events.next_of(EVENT_ROLL)["label"] == "Perception"
    assert second_events.next_of(EVENT_ROLL)["label"] == "Perception"


def test_a_player_cannot_roll_hidden(running_server, connect) -> None:
    srv = running_server(rng=Random(5))
    client, events = connect(srv, "Volt")

    client.request_roll("Sneaky", dc=10, hidden=True)
    roll = events.next_of(EVENT_ROLL)

    assert roll["hidden"] is False
    assert srv.state.rolls[0].hidden is False


def test_a_hidden_gm_roll_never_reaches_a_player(running_server, connect) -> None:
    gm_events = Events()
    srv = running_server(rng=Random(2), on_event=gm_events)
    client, events = connect(srv, "Volt")

    srv.roll(label="Ambush", bonus=4, dc=15, hidden=True)
    assert gm_events.next_of(server_mod.EVENT_ROLL)["label"] == "Ambush"

    # Give a broadcast every chance to arrive, then confirm none did.
    client.ping(1)
    events.next_of(EVENT_PONG)
    assert EVENT_ROLL not in events.kinds()
    assert srv.state.rolls[0].hidden is True


def test_rolls_are_sequenced_across_connections(running_server, connect) -> None:
    srv = running_server(rng=Random(4))
    first, first_events = connect(srv, "Volt")
    second, _ = connect(srv, "Mesa")

    first.request_roll("One", dc=10)
    first_events.next_of(EVENT_ROLL)
    second.request_roll("Two", dc=10)
    first_events.next_of(EVENT_ROLL)
    srv.roll(label="Three", dc=10)

    assert [roll.seq for roll in srv.state.rolls] == [1, 2, 3]
    assert [roll.label for roll in srv.state.rolls] == ["One", "Two", "Three"]


def test_absurd_modifiers_are_clamped(running_server, connect) -> None:
    srv = running_server(rng=Random(9))
    client, events = connect(srv, "Volt")

    client.request_roll("x" * 500, bonus=10**9, penalty=-(10**9), dc=10**9)
    roll = events.next_of(EVENT_ROLL)

    assert roll["bonus"] == server_mod.MAX_ROLL_MODIFIER
    assert roll["penalty"] == -server_mod.MAX_ROLL_MODIFIER
    assert roll["dc"] == server_mod.MAX_ROLL_MODIFIER
    assert len(roll["label"]) == server_mod.MAX_LABEL_CHARS


# --------------------------------------------------------------------------
# GM commands
# --------------------------------------------------------------------------


def test_apply_condition_reaches_only_the_named_player(running_server, connect) -> None:
    srv = running_server()
    first, first_events = connect(srv, "Volt")
    _second, second_events = connect(srv, "Mesa")

    assert srv.apply_condition(first.player_id, "dazed", parameter="mental") is True
    command = first_events.next_of(EVENT_APPLY_CONDITION)
    assert command["condition_id"] == "dazed"
    assert command["parameter"] == "mental"

    _second.ping(2)
    second_events.next_of(EVENT_PONG)
    assert EVENT_APPLY_CONDITION not in second_events.kinds()


def test_apply_condition_to_an_absent_player_reports_false(running_server) -> None:
    srv = running_server()
    assert srv.apply_condition("nobody", "dazed") is False


def test_remove_condition_reaches_the_player(running_server, connect) -> None:
    srv = running_server()
    client, events = connect(srv, "Volt")
    assert srv.remove_condition(client.player_id, "dazed") is True
    assert events.next_of("remove_condition")["condition_id"] == "dazed"


# --------------------------------------------------------------------------
# Persistence
# --------------------------------------------------------------------------


def test_the_session_and_its_rolls_are_persisted(running_server, connect) -> None:
    srv = running_server(rng=Random(8))
    client, events = connect(srv, "Volt")
    client.send_snapshot({"name": "Volt", "power_level": 10})
    wait_for(lambda: bool(srv.state.players[client.player_id].character), message="snapshot stored")
    client.request_roll("Athletics", dc=15)
    events.next_of(EVENT_ROLL)
    srv.roll(label="Hidden", dc=15, hidden=True)

    directory = store.session_dir(srv.state.id)
    saved = json.loads((directory / store.SESSION_FILENAME).read_text(encoding="utf-8"))
    assert saved["name"] == "Test Table"
    assert "rolls" not in saved  # the log is its own file
    lines = (directory / store.ROLLS_FILENAME).read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2


def test_a_restarted_session_resumes_from_disk(running_server, connect) -> None:
    srv = running_server(rng=Random(6))
    client, events = connect(srv, "Volt")
    player_id, player_token = client.player_id, client.player_token
    client.send_snapshot({"name": "Volt"})
    client.request_roll("Athletics", dc=15)
    events.next_of(EVENT_ROLL)
    client.close()
    srv.stop()

    resumed = store.load_session(srv.state.id)
    assert resumed.players[player_id].character["name"] == "Volt"
    assert resumed.players[player_id].connected is False
    assert [roll.label for roll in resumed.rolls] == ["Athletics"]

    second = SessionServer(resumed, host="127.0.0.1", port=0)
    second.start()
    try:
        again = SessionClient(
            *second.address,
            token=resumed.host_token,
            display_name="Volt",
            player_id=player_id,
            player_token=player_token,
        )
        again.connect(timeout=TIMEOUT)
        assert again.player_id == player_id
        assert [roll["label"] for roll in again.history] == ["Athletics"]
        # The next roll continues the sequence rather than restarting it.
        record = second.roll(label="After", dc=10)
        assert record.seq == 2
        again.close()
    finally:
        second.stop()


def test_persist_false_writes_nothing(running_server, connect, tmp_path: Path) -> None:
    srv = running_server(persist=False)
    client, _ = connect(srv, "Volt")
    client.request_roll("Athletics", dc=10)
    wait_for(lambda: len(srv.state.rolls) == 1, message="the roll landing")
    assert not (storage.get_workspace().sessions_dir / srv.state.id).exists()


# --------------------------------------------------------------------------
# Robustness
# --------------------------------------------------------------------------


def test_a_malformed_line_gets_an_error_and_a_close(running_server) -> None:
    srv = running_server()
    connection = raw_connect(srv)
    try:
        connection.send(Hello(token=srv.state.host_token, display_name="Bad"))
        connection.receive()  # welcome
        send_bytes(connection, b"{not json at all\n")
        assert read_until(connection, ErrorMessage).code == ERROR_MALFORMED
        assert connection.receive() is None  # and the server hung up
    finally:
        connection.close()


def test_an_unknown_message_type_is_rejected(running_server) -> None:
    srv = running_server()
    connection = raw_connect(srv)
    try:
        connection.send(Hello(token=srv.state.host_token, display_name="Bad"))
        connection.receive()
        send_bytes(connection, b'{"type":"drop_tables"}\n')
        assert read_until(connection, ErrorMessage).code == ERROR_MALFORMED
    finally:
        connection.close()


def test_an_oversized_message_is_refused_before_sending() -> None:
    huge = {"name": "x" * MAX_MESSAGE_BYTES}
    with pytest.raises(ProtocolError) as excinfo:
        encode(CharacterSnapshot(character=huge))
    assert "cap" in str(excinfo.value)


def test_a_flood_trips_the_rate_limit(running_server) -> None:
    events = Events()
    srv = running_server(on_event=events)
    connection = raw_connect(srv)
    try:
        connection.send(Hello(token=srv.state.host_token, display_name="Loud"))
        connection.receive()  # welcome
        for _ in range(server_mod.RATE_LIMIT_MESSAGES + 5):
            try:
                connection.send(Ping(nonce=1))
            except OSError:
                break
        codes: list[str] = []
        dropped = False
        deadline = time.monotonic() + TIMEOUT
        while time.monotonic() < deadline:
            try:
                message = connection.receive()
            except ConnectionError:
                # The server hung up with pings still in flight; on Windows that
                # can surface as a reset before the error message is readable.
                # Being cut off *is* the punishment — the message is best-effort.
                dropped = True
                break
            if message is None:
                dropped = True
                break
            if isinstance(message, ErrorMessage):
                codes.append(message.code)
        assert "rate_limit" in codes or dropped
        events.next_of(server_mod.EVENT_PLAYER_LEFT)  # and the seat was vacated
    finally:
        connection.close()


def test_an_idle_peer_is_dropped_after_the_handshake_timeout(
    running_server, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(server_mod, "HANDSHAKE_TIMEOUT", 0.2)
    srv = running_server()
    connection = raw_connect(srv)
    try:
        wait_for(lambda: connection.receive() is None, message="the idle socket closing")
    finally:
        connection.close()


def test_stopping_marks_everyone_offline(running_server, connect) -> None:
    srv = running_server()
    client, events = connect(srv, "Volt")
    wait_for(lambda: client.player_id in srv.connected_player_ids(), message="seated")

    srv.stop()
    events.next_of(EVENT_DISCONNECTED)
    assert all(not slot.connected for slot in srv.state.players.values())
    assert srv.running is False


def test_the_client_reports_an_unreachable_session() -> None:
    # Bind and immediately release a port, so nothing is listening on it.
    listener = TcpTransport().listen("127.0.0.1", 0)
    port = listener.address[1]
    listener.close()
    client = SessionClient("127.0.0.1", port, token="x", display_name="Nobody")
    with pytest.raises(SessionClientError):
        client.connect(timeout=1.0)


def test_sending_without_a_connection_is_false() -> None:
    client = SessionClient("127.0.0.1", net.DEFAULT_PORT, token="x", display_name="Nobody")
    assert client.send_snapshot({"name": "Nobody"}) is False
    assert client.request_roll("Athletics", dc=10) is False


def test_decode_rejects_a_pickle_looking_payload() -> None:
    """The wire is JSON only — nothing here ever evaluates a payload."""
    with pytest.raises(ProtocolError):
        decode(b"\x80\x04\x95 cos\nsystem\n")


def test_an_unencodable_command_does_not_cost_the_player_their_seat(
    running_server, connect
) -> None:
    # An oversized message is *our* failure to encode, not the peer's; dropping
    # the peer for it would punish the wrong side — repeatedly, for everyone.
    events = Events()
    srv = running_server(on_event=events)
    client, client_events = connect(srv, "Volt")

    sent = srv.apply_condition(client.player_id, "prone", parameter="x" * MAX_MESSAGE_BYTES)

    assert sent is False
    assert events.next_of(server_mod.EVENT_ERROR)["code"] == "encode"
    assert client.player_id in srv.connected_player_ids()
    srv.roll(label="Still here")
    assert client_events.next_of(EVENT_ROLL)["label"] == "Still here"


def test_an_idle_connection_outlives_the_io_timeout(
    running_server, connect, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The IO timeout exists to unstick *sends* to a stalled peer; a quiet table
    # must not be mistaken for a dead one. This is the one test that has to let
    # real time pass — the behavior under test is nothing happening.
    monkeypatch.setattr(server_mod, "IO_TIMEOUT", 0.2)
    monkeypatch.setattr(client_mod, "IO_TIMEOUT", 0.2)
    srv = running_server()
    client, events = connect(srv, "Volt")

    time.sleep(0.7)  # several timeouts' worth of silence on both ends

    assert client.connected
    assert client.player_id in srv.connected_player_ids()
    client.request_roll("After the lull")
    assert events.next_of(EVENT_ROLL)["label"] == "After the lull"


def test_close_emits_disconnected_exactly_once(running_server, connect) -> None:
    srv = running_server()
    client, events = connect(srv, "Volt")

    client.close()
    client.close()

    assert events.kinds().count(EVENT_DISCONNECTED) == 1


def test_closing_a_never_connected_client_emits_nothing() -> None:
    events = Events()
    client = SessionClient("127.0.0.1", 1, token="x", display_name="Nobody", on_event=events)
    client.close()
    assert events.kinds() == []

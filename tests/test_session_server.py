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
    EVENT_ROLL_REMOVED,
    EVENT_ROSTER,
    EVENT_SNAPSHOT,
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
    REASON_SESSION_CLOSED,
    CharacterSnapshot,
    ErrorMessage,
    Hello,
    ModRequest,
    Ping,
    ProtocolError,
    Welcome,
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
        # Unconditionally: a client midway through a reconnect has no socket, so
        # ``connected`` is False while its thread is very much still running.
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
# Notes — the history's other kind of entry
# --------------------------------------------------------------------------


def test_a_note_reaches_every_player(running_server, connect) -> None:
    srv = running_server(rng=Random(11))
    first, first_events = connect(srv, "Volt")
    _second, second_events = connect(srv, "Mesa")

    first.post_note("spent a hero point — 2 left")

    for events in (first_events, second_events):
        note = events.next_of(EVENT_ROLL)
        assert note["kind"] == "note"
        assert note["text"] == "spent a hero point — 2 left"
        # Attributed to the seat it came from, not to anything the text claims.
        assert note["player_name"] == "Volt"


def test_a_note_shares_the_sequence_with_the_rolls(running_server, connect) -> None:
    """One log, one counter — which is what lets the GM strike a note like a roll."""
    srv = running_server(rng=Random(4))
    client, events = connect(srv, "Volt")

    client.request_roll("One", dc=10)
    events.next_of(EVENT_ROLL)
    client.post_note("gained a hero point — 3 left")
    seq = events.next_of(EVENT_ROLL)["seq"]

    assert [entry.seq for entry in srv.state.rolls] == [1, 2]
    assert [entry.kind for entry in srv.state.rolls] == ["roll", "note"]

    assert srv.remove_roll(seq) is True
    assert events.next_of(EVENT_ROLL_REMOVED)["seq"] == seq
    assert [entry.seq for entry in srv.state.rolls] == [1]


def test_a_note_is_persisted_and_reloads_as_a_note(running_server, connect) -> None:
    srv = running_server(rng=Random(6))
    client, _events = connect(srv, "Volt")

    client.post_note("spent a hero point — 0 left")
    # Wait on the *file*, not on ``state.rolls``: the append to the log happens a
    # moment after the in-memory list grows, and waiting on the list left a race
    # this test lost about one run in three.
    wait_for(lambda: len(store.load_rolls(srv.state.id)) == 1, message="the note was persisted")

    reloaded = store.load_rolls(srv.state.id)
    assert [entry.kind for entry in reloaded] == ["note"]
    assert reloaded[0].text == "spent a hero point — 0 left"
    assert reloaded[0].die == 0


def test_an_absurdly_long_note_is_capped(running_server, connect) -> None:
    srv = running_server(rng=Random(9))
    client, events = connect(srv, "Volt")

    client.post_note("x" * 5000)

    assert len(events.next_of(EVENT_ROLL)["text"]) == server_mod.MAX_NOTE_CHARS


def test_the_gm_can_remove_a_roll_for_everyone(running_server, connect) -> None:
    srv = running_server(rng=Random(8))
    client, events = connect(srv, "Volt")

    client.request_roll("Oops", dc=10)
    seq = events.next_of(EVENT_ROLL)["seq"]

    assert srv.remove_roll(seq) is True
    assert events.next_of(EVENT_ROLL_REMOVED)["seq"] == seq
    assert [roll.seq for roll in srv.state.rolls] == []
    # The rewrite reached disk too, so a reload does not resurrect it.
    assert store.load_rolls(srv.state.id) == []


def test_removing_an_absent_roll_is_a_noop(running_server) -> None:
    srv = running_server(rng=Random(8))
    srv.roll(label="Keep", dc=10)
    assert srv.remove_roll(999) is False
    assert [roll.label for roll in srv.state.rolls] == ["Keep"]


def test_removing_a_hidden_roll_is_not_broadcast(running_server, connect) -> None:
    gm_events = Events()
    srv = running_server(rng=Random(2), on_event=gm_events)
    client, events = connect(srv, "Volt")

    record = srv.roll(label="Ambush", dc=15, hidden=True)
    gm_events.next_of(server_mod.EVENT_ROLL)

    assert srv.remove_roll(record.seq) is True
    # The GM's own window is told; the player — who never got the roll — is not.
    assert gm_events.next_of(server_mod.EVENT_ROLL_REMOVED)["seq"] == record.seq
    client.ping(1)
    events.next_of(EVENT_PONG)
    assert EVENT_ROLL_REMOVED not in events.kinds()


def test_a_player_cannot_remove_a_roll(running_server, connect) -> None:
    srv = running_server(rng=Random(8))
    client, events = connect(srv, "Volt")

    client.request_roll("Mine", dc=10)
    seq = events.next_of(EVENT_ROLL)["seq"]

    client.request_remove_roll(seq)  # a player has no such privilege
    client.ping(1)
    events.next_of(EVENT_PONG)

    assert EVENT_ROLL_REMOVED not in events.kinds()
    assert [roll.seq for roll in srv.state.rolls] == [seq]


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
    # A deliberate stop says so, so the client ends the session instead of
    # spending its retry window redialling a table that has closed.
    assert events.next_of(EVENT_DISCONNECTED)["reason"] == REASON_SESSION_CLOSED
    assert all(not slot.connected for slot in srv.state.players.values())
    assert srv.running is False


def test_the_farewell_goes_out_before_anything_is_torn_down(running_server, connect) -> None:
    """The ordering inside ``stop``, asserted directly rather than raced for.

    ``stop`` used to clear ``_running`` before sending the farewell, which let
    each reader loop exit and close its own socket first; the message then went
    to a closed connection and was dropped without a sound. The client read a
    bare EOF, called it a network fault, and redialled a deliberately closed
    table for five minutes.

    Reproducing that by repetition does not work — on loopback the reader threads
    never get scheduled in time, so the buggy order passes locally and only fails
    under the load of a full suite. So this pins the invariant that actually
    matters: **when the farewell is written, the session is still running and the
    socket is still open.** Both were false in the broken order.
    """
    srv = running_server()
    client, _events = connect(srv, "Volt")
    wait_for(lambda: client.player_id in srv.connected_player_ids(), message="seated")

    observed: list[tuple[str, bool, bool]] = []
    original = srv._send_quietly

    def spy(connection, message):
        observed.append((getattr(message, "TYPE", ""), srv._running, connection.closed))
        return original(connection, message)

    srv._send_quietly = spy
    srv.stop()

    farewells = [entry for entry in observed if entry[0] == "kicked"]
    assert farewells, "stopping sent no farewell at all"
    for _type, running, closed in farewells:
        assert running is True, "the farewell was sent after teardown had begun"
        assert closed is False, "the farewell was written to an already-closed socket"


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


# --------------------------------------------------------------------------
# Keepalive, dead peers, and coming back
# --------------------------------------------------------------------------


@pytest.fixture
def brisk(monkeypatch: pytest.MonkeyPatch):
    """Shrink the keepalive clocks so a test can watch them work.

    Everything here is real elapsed time — the behaviour under test is a peer
    saying *nothing* — so the numbers are as small as loopback tolerates.
    """

    def apply(*, keepalive: float = 0.1, peer: float = 0.5, io: float = 0.05) -> None:
        monkeypatch.setattr(client_mod, "IO_TIMEOUT", io)
        monkeypatch.setattr(server_mod, "IO_TIMEOUT", io)
        monkeypatch.setattr(client_mod, "KEEPALIVE_INTERVAL", keepalive)
        monkeypatch.setattr(client_mod, "PEER_TIMEOUT", peer)
        monkeypatch.setattr(server_mod, "PEER_TIMEOUT", peer)

    return apply


def test_a_quiet_client_pings_on_its_own(running_server, connect, brisk) -> None:
    """The whole point: nobody at the table does anything, and the link stays warm.

    Before this, a session that was merely being roleplayed sent no bytes at all
    and was reaped by the relay after two minutes.
    """
    brisk()
    srv = running_server()
    client, events = connect(srv, "Volt")

    # Nothing calls ping(); the reader thread does it because the server has
    # been quiet for longer than the keepalive interval.
    assert events.next_of(EVENT_PONG, timeout=2.0)["nonce"] >= 1
    assert client.connected
    assert client.connection_state == client_mod.STATE_ONLINE


def test_a_silent_peer_is_reaped_by_the_server(running_server, brisk) -> None:
    """A hand-rolled client that never pings loses its seat, rather than haunting it."""
    brisk()
    events = Events()
    srv = running_server(on_event=events)
    connection = raw_connect(srv)
    try:
        connection.send(Hello(token=srv.state.host_token, display_name="Ghost"))
        assert connection.receive() is not None  # the welcome
        left = events.next_of(server_mod.EVENT_PLAYER_LEFT, timeout=3.0)
        assert left["player"]["display_name"] == "Ghost"
        assert not any(entry["connected"] for entry in srv.roster() if not entry["is_gm"])
    finally:
        connection.close()


def test_a_stalled_link_is_not_invisible(brisk, monkeypatch: pytest.MonkeyPatch) -> None:
    """A half-open connection ends, instead of looking healthy forever.

    The stub below welcomes the client and then never speaks again, holding the
    socket open — which is what a black-holed connection, a suspended laptop or a
    NAT that dropped its mapping all look like from this end. There is nothing to
    read and nothing to fail, so before the peer deadline the client would sit in
    ``recv`` until the app was quit.
    """
    brisk()
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = listener.getsockname()[1]
    held: list[socket.socket] = []

    def serve_once_then_go_quiet() -> None:
        sock, _address = listener.accept()
        held.append(sock)
        sock.recv(4096)  # the Hello
        sock.sendall(encode(Welcome(session_id="s", session_name="Stub", player_id="p1")))
        # and now, deliberately, nothing at all — forever.

    thread = threading.Thread(target=serve_once_then_go_quiet, daemon=True)
    thread.start()

    events = Events()
    # reconnect=False so the disconnect is the observable outcome; the redial
    # behaviour has its own test below.
    client = SessionClient(
        "127.0.0.1", port, token="t", display_name="Volt", on_event=events, reconnect=False
    )
    try:
        client.connect(timeout=TIMEOUT)
        assert events.next_of(EVENT_DISCONNECTED, timeout=4.0)["reason"] == "timeout"
    finally:
        client.close()
        for sock in held:
            sock.close()
        listener.close()


def test_a_dropped_client_comes_back_to_the_same_seat(running_server, connect, brisk) -> None:
    """The reported bug, from the client's side: one seat, not two.

    A blip must also *not* look like leaving — the shared roll history and the
    "you left the session" notice both hang off EVENT_DISCONNECTED.
    """
    brisk()
    srv = running_server()
    client, events = connect(srv, "Volt")
    events.next_of(EVENT_CONNECTED)
    first_id = client.player_id
    before = len(srv.state.players)

    # Kill the connection from the server's side, as a network drop would.
    srv._connections[first_id].close()

    assert events.next_of(client_mod.EVENT_STATE, timeout=3.0)["state"] in {
        client_mod.STATE_RECONNECTING,
        client_mod.STATE_ONLINE,
    }
    wait_for(
        lambda: client.connection_state == client_mod.STATE_ONLINE,
        timeout=6.0,
        message="the client reconnecting",
    )

    assert client.player_id == first_id
    assert len(srv.state.players) == before  # no second slot appeared
    assert EVENT_DISCONNECTED not in events.kinds()  # a blip is not leaving


def test_a_kick_is_terminal_and_never_redialled(running_server, connect, brisk) -> None:
    brisk()
    srv = running_server()
    client, events = connect(srv, "Volt")
    wait_for(lambda: client.player_id in srv.connected_player_ids(), message="seated")

    srv.kick(client.player_id, reason="told to go")

    assert events.next_of(EVENT_KICKED)["reason"] == "told to go"
    assert events.next_of(EVENT_DISCONNECTED)["reason"] == "kicked"
    time.sleep(0.5)  # long enough for several backoff steps to have fired
    assert events.kinds().count(EVENT_DISCONNECTED) == 1
    assert client.connection_state == client_mod.STATE_OFFLINE


def test_a_refused_redial_ends_the_session(running_server, connect, brisk) -> None:
    """Retrying is for network faults; a refusal is an answer, and it is final."""
    brisk()
    srv = running_server()
    client, events = connect(srv, "Volt")
    player_id = client.player_id
    wait_for(lambda: player_id in srv.connected_player_ids(), message="seated")

    # The join code changes under us, so the redial is refused rather than failing.
    srv.state.host_token = "a-completely-different-token"
    srv._connections[player_id].close()

    assert events.next_of(EVENT_DISCONNECTED, timeout=6.0)["reason"] == ERROR_BAD_TOKEN
    assert client.connection_state == client_mod.STATE_OFFLINE


def test_an_offline_seat_is_adopted_by_player_id(running_server, connect) -> None:
    """A returning player with no token still lands on their own card.

    This is the GM-facing half of the bug: a player who cleared their settings,
    moved machines, or pasted the code by hand used to arrive as a second card
    while their first sat there greyed out forever.
    """
    srv = running_server()
    first, _events = connect(srv, "Volt")
    player_id = first.player_id
    first.close()
    wait_for(lambda: not srv.state.players[player_id].connected, message="the seat freeing up")

    # No player_token at all — only the public id, which is all a fresh install
    # could possibly know.
    second, events = connect(srv, "Volt", player_id=player_id, reconnect=False)

    assert second.player_id == player_id
    assert len(srv.state.players) == 2  # the GM and one player, not two players
    assert events.next_of(EVENT_CONNECTED)["player_id"] == player_id


def test_a_live_seat_is_never_adopted(running_server, connect) -> None:
    """Adoption is bounded: it hands back an *empty* chair, never occupies one."""
    srv = running_server()
    first, _events = connect(srv, "Volt")
    wait_for(lambda: first.player_id in srv.connected_player_ids(), message="seated")

    second, _events2 = connect(srv, "Impostor", player_id=first.player_id, reconnect=False)

    assert second.player_id != first.player_id
    assert len(srv.state.players) == 3  # the GM, Volt, and a genuinely new seat
    assert first.connected


def test_the_gm_seat_is_never_adopted(running_server, connect) -> None:
    """The one seat that carries a privilege still costs the GM token to claim."""
    srv = running_server()
    gm_id = srv.gm_slot().player_id

    player, _events = connect(srv, "Chancer", player_id=gm_id, reconnect=False)

    assert player.player_id != gm_id
    assert player.is_gm is False


def test_a_lost_listener_is_reported(running_server) -> None:
    """Hosting that nobody can reach says so, instead of looking perfect."""
    events = Events()
    srv = running_server(on_event=events)

    # A relay whose control link dies does exactly this: accept() gives up while
    # the server still believes it is hosting.
    srv._listener.close()

    assert events.next_of(server_mod.EVENT_LISTENER_LOST)["session_id"] == srv.state.id


# --------------------------------------------------------------------------
# The remote GM
#
# A GM whose session lives on an always-on box drives it over the same socket
# every player uses. What separates them is one secret deliberately kept out of
# the join code — everyone at the table has that one.
# --------------------------------------------------------------------------


def gm_connect(srv: SessionServer, name: str = "GM", **kwargs):
    """A client that claims the GM seat with the session's gm token."""
    events = kwargs.pop("events", None) or Events()
    host, port = srv.address
    client = SessionClient(
        host,
        port,
        token=srv.state.host_token,
        display_name=name,
        gm_token=srv.state.gm_token,
        on_event=events,
        **kwargs,
    )
    client.connect(timeout=TIMEOUT)
    return client, events


def test_the_gm_token_seats_a_remote_client_in_the_gm_slot(running_server) -> None:
    srv = running_server(gm_in_process=False)
    client, _ = gm_connect(srv)
    try:
        assert client.is_gm
        assert client.player_id == srv.gm_slot().player_id
    finally:
        client.close()


def test_a_wrong_gm_token_is_refused_rather_than_seated_as_a_player(running_server) -> None:
    # The dangerous failure is not refusal, it is a quiet downgrade: a GM seated
    # as a player only finds out when a hidden roll reaches the table.
    srv = running_server(gm_in_process=False)
    host, port = srv.address
    client = SessionClient(
        host, port, token=srv.state.host_token, display_name="Impostor", gm_token="nope"
    )
    with pytest.raises(SessionClientError) as excinfo:
        client.connect(timeout=TIMEOUT)

    assert excinfo.value.code == ERROR_BAD_TOKEN
    assert [s for s in srv.state.players.values() if not s.is_gm] == []


def test_the_join_code_alone_does_not_confer_gm(running_server, connect) -> None:
    srv = running_server(gm_in_process=False)
    client, _ = connect(srv, "Volt")

    assert not client.is_gm
    assert not srv.state.players[client.player_id].is_gm


def test_a_remote_gm_rolls_hidden_and_players_never_see_it(running_server, connect) -> None:
    srv = running_server(gm_in_process=False)
    player, player_events = connect(srv, "Volt")
    gm, gm_events = gm_connect(srv)
    try:
        gm.request_roll("Ambush check", hidden=True)
        gm.request_roll("In the open")

        # The player's history skips the hidden roll and arrives at the next one.
        assert player_events.next_of(EVENT_ROLL)["label"] == "In the open"
        assert all(not roll.get("hidden") for roll in player.history)
        # It is recorded all the same.
        assert [r.label for r in srv.state.rolls] == ["Ambush check", "In the open"]
        assert gm_events.next_of(EVENT_ROLL)["label"] == "Ambush check"
    finally:
        gm.close()


def test_a_players_hidden_flag_is_still_ignored(running_server, connect) -> None:
    srv = running_server(gm_in_process=False)
    player, _ = connect(srv, "Volt")
    _watcher, watcher_events = connect(srv, "Watcher")

    player.request_roll("Sneaky", hidden=True)

    assert watcher_events.next_of(EVENT_ROLL)["label"] == "Sneaky"
    assert not srv.state.rolls[-1].hidden


def test_a_players_sheet_reaches_a_remote_gm(running_server, connect) -> None:
    # The roster deliberately carries no characters, so without this forward a
    # remote GM would have no way to see a sheet at all.
    srv = running_server(gm_in_process=False)
    gm, gm_events = gm_connect(srv)
    try:
        player, _ = connect(srv, "Volt")
        player.send_snapshot({"power_level": 10, "profile": {"hero_name": "Volt"}})

        payload = gm_events.next_of(EVENT_SNAPSHOT)
        assert payload["player_id"] == player.player_id
        assert payload["character"]["power_level"] == 10
    finally:
        gm.close()


def test_a_snapshot_is_not_forwarded_to_other_players(running_server, connect) -> None:
    srv = running_server(gm_in_process=False)
    player, _ = connect(srv, "Volt")
    _watcher, watcher_events = connect(srv, "Watcher")

    player.send_snapshot({"power_level": 10})
    # A roster still arrives; a sheet does not.
    watcher_events.next_of(EVENT_ROSTER)
    assert EVENT_SNAPSHOT not in watcher_events.kinds()


def test_a_remote_gm_applies_a_condition_to_a_player(running_server, connect) -> None:
    srv = running_server(gm_in_process=False)
    player, player_events = connect(srv, "Volt")
    gm, _ = gm_connect(srv)
    try:
        gm.apply_condition(player.player_id, "dazed")

        assert player_events.next_of(EVENT_APPLY_CONDITION)["condition_id"] == "dazed"
    finally:
        gm.close()


def test_a_player_cannot_apply_a_condition(running_server, connect) -> None:
    srv = running_server(gm_in_process=False)
    attacker, _ = connect(srv, "Volt")
    victim, victim_events = connect(srv, "Target")

    attacker.apply_condition(victim.player_id, "dazed")
    # Provoke a message that *is* answered, so "nothing arrived" is an
    # observation rather than a race with the network.
    attacker.request_roll("proof of life")
    assert victim_events.next_of(EVENT_ROLL)["label"] == "proof of life"
    assert EVENT_APPLY_CONDITION not in victim_events.kinds()


def test_a_remote_gm_kicks_a_player(running_server, connect) -> None:
    srv = running_server(gm_in_process=False)
    player, player_events = connect(srv, "Volt")
    gm, _ = gm_connect(srv)
    try:
        gm.request_kick(player.player_id, "wandered off")

        assert player_events.next_of(EVENT_KICKED)["reason"] == "wandered off"
        wait_for(lambda: player.player_id not in srv.state.players, message="slot dropped")
    finally:
        gm.close()


def test_a_gm_cannot_kick_themselves(running_server) -> None:
    srv = running_server(gm_in_process=False)
    gm, gm_events = gm_connect(srv)
    try:
        gm.request_kick(gm.player_id)
        gm.request_roll("still here")

        assert gm_events.next_of(EVENT_ROLL)["label"] == "still here"
        assert gm.player_id in srv.state.players
    finally:
        gm.close()


def test_a_remote_gm_renames_the_session_and_sets_the_cast(running_server) -> None:
    srv = running_server(gm_in_process=False)
    gm, _ = gm_connect(srv)
    try:
        gm.set_session_name("Friday Game")
        gm.set_npc_paths(["thug.json"])
        wait_for(lambda: srv.state.name == "Friday Game", message="renamed")
        wait_for(lambda: srv.state.npc_paths == ["thug.json"], message="cast stored")
    finally:
        gm.close()

    # The cast follows the session, so a GM picking it up elsewhere still has it.
    again, _ = gm_connect(srv)
    try:
        assert again.npc_paths == ["thug.json"]
    finally:
        again.close()


def test_the_cast_list_is_never_sent_to_a_player(running_server, connect) -> None:
    srv = running_server(gm_in_process=False)
    srv.set_npc_paths(["boss.json"])
    player, _ = connect(srv, "Volt")

    assert player.npc_paths == []


def test_a_headless_session_shows_no_gm_until_one_connects(running_server) -> None:
    srv = running_server(gm_in_process=False)
    assert not srv.gm_slot().connected

    gm, _ = gm_connect(srv)
    try:
        wait_for(lambda: srv.gm_slot().connected, message="gm seated")
    finally:
        gm.close()
    wait_for(lambda: not srv.gm_slot().connected, message="gm left")


def test_an_in_process_gm_is_connected_from_the_start(running_server) -> None:
    assert running_server().gm_slot().connected


def test_a_resumed_session_still_shows_its_in_process_gm(running_server) -> None:
    # store.load_session clears every connected flag, so a resumed GM used to
    # show offline for the rest of the session's life.
    first = running_server(gm_name="Boss")
    first.stop()

    resumed = running_server(state=store.load_session(first.state.id), gm_name="Boss")

    gm = resumed.gm_slot()
    assert gm.connected and gm.display_name == "Boss"


def test_the_gm_seat_does_not_eat_a_player_slot(running_server, connect) -> None:
    srv = running_server(gm_in_process=False, max_clients=2)
    gm, _ = gm_connect(srv)
    try:
        connect(srv, "One")
        connect(srv, "Two")  # refused if the GM counted against the cap
    finally:
        gm.close()


def test_the_gm_token_is_never_broadcast(running_server, connect) -> None:
    srv = running_server(gm_in_process=False)
    client, events = connect(srv, "Volt")

    secret = srv.state.gm_token
    assert secret
    assert secret not in json.dumps(events.next_of(EVENT_CONNECTED))
    assert secret not in json.dumps(client.roster)


def test_a_closed_listener_wakes_its_waiter_on_every_platform() -> None:
    """Closing the socket wakes a blocked accept on Windows and not on Linux.

    Which is the platform the relay and the hub run on: left to the close alone the
    accept thread never returned, so the loop never exited, the thread leaked, and a
    session whose listener had died went on looking perfectly healthy.
    """
    listener = TcpTransport().listen("127.0.0.1", 0)
    result: list[object] = []
    thread = threading.Thread(target=lambda: result.append(listener.accept()), daemon=True)
    thread.start()
    time.sleep(0.05)

    listener.close()
    thread.join(timeout=TIMEOUT)

    assert not thread.is_alive(), "accept never noticed the listener close"
    assert result == [None]


# --------------------------------------------------------------------------
# The mod channel
#
# Everything here turns on one asymmetry: shared *state* is the GM's to author
# and reaches everyone, while a *request* is anyone's to send and reaches the GM
# alone. The tests are mostly about the boundary between those two.
# --------------------------------------------------------------------------


def test_the_gm_publishes_mod_state_and_the_table_sees_it(running_server, connect) -> None:
    srv = running_server()
    _, events = connect(srv)

    srv.set_mod_state("timers", "t1", {"kind": "timer", "remaining": 90})

    payload = events.next_of(client_mod.EVENT_MOD_STATE)
    assert payload["mod_id"] == "timers"
    assert payload["key"] == "t1"
    assert payload["payload"] == {"kind": "timer", "remaining": 90}


def test_a_player_cannot_author_mod_state(running_server, connect) -> None:
    """The rule that makes the whole channel trustworthy.

    A player's ``SetModState`` is dropped the way their ``SetScene`` is — silently,
    by falling off the end of the dispatch chain. If it were honoured, any seat
    could rewrite what the table believes the GM put there.
    """
    srv = running_server()
    client, _ = connect(srv)

    client.set_mod_state("timers", "t1", {"kind": "timer", "remaining": 1})

    # Give it every chance to have been wrongly applied before concluding it was not.
    time.sleep(0.2)
    assert srv.mod_state() == {}


def test_a_late_joiner_gets_the_mod_state_in_their_welcome(running_server, connect) -> None:
    """The reason state is stored rather than merely broadcast.

    A player who joins mid-fight has missed every ``ModStateUpdate`` there has
    ever been, and a timer they cannot see is worse than no timer.
    """
    srv = running_server()
    srv.set_mod_state("timers", "t1", {"kind": "timer", "remaining": 42})

    client, _ = connect(srv)

    assert client.mod_state == {"timers": {"t1": {"kind": "timer", "remaining": 42}}}


def test_a_none_payload_deletes_the_entry_and_says_so(running_server, connect) -> None:
    """What a share toggle turning off looks like on the wire.

    Not a flag saying "ignore this" — the entry ceases to exist for everyone but
    its author, and the deletion is as much news as the change was.
    """
    srv = running_server()
    client, events = connect(srv)
    srv.set_mod_state("timers", "t1", {"kind": "timer"})
    events.next_of(client_mod.EVENT_MOD_STATE)

    srv.set_mod_state("timers", "t1", None)

    assert events.next_of(client_mod.EVENT_MOD_STATE)["payload"] is None
    wait_for(lambda: not client.mod_state, message="the client dropped the entry")
    assert srv.mod_state() == {}


def test_mod_state_survives_a_restart_of_the_session(running_server, connect) -> None:
    """Persisted like the scene, and for the same reason: a table hosted on a
    server has to outlive the process holding it."""
    srv = running_server()
    srv.set_mod_state("timers", "t1", {"kind": "timer", "remaining": 12})
    session_id = srv.state.id
    srv.stop()

    reloaded = store.load_session(session_id)

    assert reloaded.mod_state == {"timers": {"t1": {"kind": "timer", "remaining": 12}}}


def test_a_mod_request_reaches_the_gm_and_nobody_else(running_server, connect) -> None:
    srv = running_server(gm_in_process=False)
    gm, gm_events = gm_connect(srv)
    asker, _ = connect(srv, "Asker")
    bystander, bystander_events = connect(srv, "Bystander")
    try:
        asker.send_mod_request("timers", "nudge", {"id": "t1"})

        received = gm_events.next_of(client_mod.EVENT_MOD_REQUEST)
        assert received["topic"] == "nudge"
        assert received["payload"] == {"id": "t1"}
        assert received["player_id"] == asker.player_id

        # Give the bystander every chance to have been wrongly told.
        time.sleep(0.2)
        assert client_mod.EVENT_MOD_REQUEST not in bystander_events.kinds()
        assert bystander is not None
    finally:
        gm.close()


def test_a_mod_request_is_attributed_by_the_server_not_by_the_sender(running_server, connect):
    """A field a client could fill in itself would make the channel an
    impersonation tool, so the server stamps it from the slot."""
    srv = running_server(gm_in_process=False)
    gm, gm_events = gm_connect(srv)
    asker, _ = connect(srv, "Asker")
    try:
        asker.send(ModRequest(mod_id="timers", topic="nudge", player_id="somebody-else"))

        received = gm_events.next_of(client_mod.EVENT_MOD_REQUEST)
        assert received["player_id"] == asker.player_id
    finally:
        gm.close()


def test_a_mod_note_lands_in_the_one_shared_history(running_server, connect) -> None:
    srv = running_server()
    _, events = connect(srv)

    srv.record_mod_note("timers", "Timer Bomb finished")

    roll = events.next_of(client_mod.EVENT_ROLL)
    assert roll["kind"] == "mod"
    assert roll["mod_id"] == "timers"
    assert roll["text"] == "Timer Bomb finished"
    # Seq-numbered off the same counter as everything else, so the GM can strike
    # it like any other line.
    assert roll["seq"] >= 1


def test_a_mod_note_with_nothing_to_say_is_not_recorded(running_server) -> None:
    """Unlike a roll there is nothing else on the record to read, so a blank
    line in the history is worse than no line."""
    srv = running_server()

    assert srv.record_mod_note("timers", "   ") is None
    assert srv.record_mod_note("../evil", "hello") is None
    assert srv.state.rolls == []


def test_mod_state_broadcasts_the_key_it_actually_stored(running_server, connect) -> None:
    """The broadcast has to name the entry as *stored*, not as it arrived.

    Otherwise a client keys its copy differently from the server and can never
    overwrite it — a timer that could be started and never stopped.
    """
    srv = running_server()
    client, events = connect(srv)
    long_key = "k" * 500

    srv.set_mod_state("timers", long_key, {"kind": "timer"})

    broadcast = events.next_of(client_mod.EVENT_MOD_STATE)
    stored_key = next(iter(srv.mod_state()["timers"]))
    assert broadcast["key"] == stored_key
    assert client.mod_state["timers"].keys() == {stored_key}

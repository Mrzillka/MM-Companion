"""The Qt bridge over a session: core callbacks in, Qt signals out.

Everything here runs against a real loopback server — the bridge's whole job is
threading and translation, and a mocked server would test neither. Signals
emitted from a worker thread are queued onto the GUI thread, so a test drains
them with ``processEvents`` after joining the thread that produced them.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication

from mm_companion.core import storage
from mm_companion.core.session import discovery, store
from mm_companion.core.session.client import SessionClient, SessionClientError
from mm_companion.core.session.model import new_session
from mm_companion.ui import session_bridge
from mm_companion.ui.session_bridge import SessionBridge, active_session, set_active_session


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv(storage.HOME_ENV_VAR, str(tmp_path))
    storage.ensure_workspace()
    return tmp_path


@pytest.fixture(autouse=True)
def _clear_active_session():
    yield
    set_active_session(None)


@pytest.fixture
def bridge(qapp: QApplication) -> SessionBridge:
    made = SessionBridge()
    yield made
    made.stop()


def canned(**kwargs) -> discovery.Reachability:
    """A Reachability as ``publish_session`` would return it, with no network."""
    defaults = {"host": "192.168.0.5", "port": 47331, "method": discovery.METHOD_LAN}
    return discovery.Reachability(**{**defaults, **kwargs})


def collect(signal) -> list:
    """Record everything *signal* emits, as tuples of its arguments."""
    seen: list = []
    signal.connect(lambda *args: seen.append(args))
    return seen


def drain(qapp: QApplication, seen: list, count: int = 1, timeout: float = 5.0) -> list:
    """Pump the event loop until *seen* holds *count* items (queued emits land here)."""
    deadline = time.monotonic() + timeout
    while len(seen) < count and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.01)
    qapp.processEvents()
    return seen


def host_locally(bridge: SessionBridge, state=None):
    return bridge.host(state or new_session("Table"), port=0, bind="127.0.0.1")


# -- hosting ---------------------------------------------------------------


def test_hosting_binds_and_reports_the_address(bridge: SessionBridge) -> None:
    host, port = host_locally(bridge)
    assert host == "127.0.0.1"
    assert port > 0
    assert bridge.hosting is True


def test_hosting_twice_is_refused(bridge: SessionBridge) -> None:
    host_locally(bridge)
    with pytest.raises(session_bridge.SessionBridgeError):
        host_locally(bridge)


def test_hosting_remembers_the_session_to_resume(bridge: SessionBridge) -> None:
    state = new_session("Table")
    host_locally(bridge, state)
    assert storage.load_settings()["session_last_id"] == state.id
    assert store.load_session(state.id).name == "Table"


def test_last_session_reloads_what_was_hosted(bridge: SessionBridge) -> None:
    state = new_session("Wednesday")
    host_locally(bridge, state)
    resumed = session_bridge.last_session()
    assert resumed is not None
    assert resumed.id == state.id
    assert resumed.name == "Wednesday"


def test_last_session_is_none_when_the_files_are_gone(bridge: SessionBridge) -> None:
    state = new_session("Gone")
    host_locally(bridge, state)
    store.delete_session(state.id)
    assert session_bridge.last_session() is None


def test_stopping_ends_the_server(bridge: SessionBridge) -> None:
    host_locally(bridge)
    bridge.stop()
    assert bridge.hosting is False
    assert bridge.server is None


# -- publishing ------------------------------------------------------------


def test_publish_emits_the_reachability_and_builds_a_join_code(
    qapp: QApplication, bridge: SessionBridge
) -> None:
    state = new_session("Table")
    host_locally(bridge, state)
    bridge._publish_session = lambda port, **kw: canned(port=port)
    seen = collect(bridge.published)

    bridge.publish()
    drain(qapp, seen)

    (reachability,) = seen[0]
    assert reachability.host == "192.168.0.5"
    code = discovery.decode_join_code(bridge.join_code())
    assert code.host == "192.168.0.5"
    assert code.token == state.host_token


def test_publish_passes_the_tunnel_address_through(
    qapp: QApplication, bridge: SessionBridge
) -> None:
    host_locally(bridge)
    calls: list[dict] = []

    def fake(port: int, **kwargs):
        calls.append({"port": port, **kwargs})
        return canned(host=kwargs["manual_host"], port=kwargs["external_port"])

    bridge._publish_session = fake
    seen = collect(bridge.published)

    bridge.publish(manual_host="tunnel.example", external_port=12345)
    drain(qapp, seen)

    assert calls[0]["manual_host"] == "tunnel.example"
    assert calls[0]["external_port"] == 12345
    code = discovery.decode_join_code(bridge.join_code())
    assert (code.host, code.port) == ("tunnel.example", 12345)


def test_join_code_is_empty_before_publishing(bridge: SessionBridge) -> None:
    host_locally(bridge)
    assert bridge.join_code() == ""


def test_publishing_without_hosting_is_refused(bridge: SessionBridge) -> None:
    with pytest.raises(session_bridge.SessionBridgeError):
        bridge.publish()


def test_stopping_releases_a_port_mapping(qapp: QApplication, bridge: SessionBridge) -> None:
    released: list[bool] = []

    class FakeMapping:
        def release(self) -> bool:
            released.append(True)
            return True

    host_locally(bridge)
    bridge._publish_session = lambda port, **kw: canned(mapping=FakeMapping())
    seen = collect(bridge.published)
    bridge.publish()
    drain(qapp, seen)

    bridge.stop()
    deadline = time.monotonic() + 5.0
    while not released and time.monotonic() < deadline:
        time.sleep(0.01)
    assert released == [True]


# -- events reaching Qt ----------------------------------------------------


def test_a_joining_player_reaches_the_gui_thread(qapp: QApplication, bridge: SessionBridge) -> None:
    state = new_session("Table")
    host, port = host_locally(bridge, state)
    joined = collect(bridge.playerJoined)
    rosters = collect(bridge.rosterChanged)

    client = SessionClient(host, port, token=state.host_token, display_name="Aria")
    client.connect()
    try:
        drain(qapp, joined)
        assert joined[0][0]["player"]["display_name"] == "Aria"
        names = {entry["display_name"] for entry in rosters[-1][0]}
        assert "Aria" in names
    finally:
        client.close()


def test_a_snapshot_arrives_as_a_signal(qapp: QApplication, bridge: SessionBridge) -> None:
    state = new_session("Table")
    host, port = host_locally(bridge, state)
    snapshots = collect(bridge.snapshotReceived)

    client = SessionClient(host, port, token=state.host_token, display_name="Aria")
    client.connect()
    try:
        client.send_snapshot({"name": "Aria", "power_level": 10})
        drain(qapp, snapshots)
        player_id, character = snapshots[0]
        assert player_id == client.player_id
        assert character["power_level"] == 10
    finally:
        client.close()


def test_a_gm_roll_is_emitted(qapp: QApplication, bridge: SessionBridge) -> None:
    host_locally(bridge)
    rolls = collect(bridge.rollAdded)
    assert bridge.server is not None
    bridge.server.roll(label="Perception", bonus=3, dc=15, hidden=True)
    drain(qapp, rolls)
    assert rolls[0][0]["label"] == "Perception"
    assert rolls[0][0]["hidden"] is True


def test_a_bad_join_code_is_refused_and_reported(qapp: QApplication, bridge: SessionBridge) -> None:
    host, port = host_locally(bridge)
    refusals = collect(bridge.refused)

    client = SessionClient(host, port, token="not-the-token", display_name="Nobody")
    with pytest.raises(SessionClientError):
        client.connect()
    drain(qapp, refusals)
    assert refusals[0][0]["code"]


def test_kicking_removes_the_seat_and_disconnects_the_player(
    qapp: QApplication, bridge: SessionBridge
) -> None:
    state = new_session("Table")
    host, port = host_locally(bridge, state)
    joined = collect(bridge.playerJoined)

    client = SessionClient(host, port, token=state.host_token, display_name="Aria")
    client.connect()
    try:
        drain(qapp, joined)
        player_id = client.player_id
        assert bridge.kick(player_id) is True
        # The seat is gone from the server's state.
        assert player_id not in bridge.server.state.players
        # Kicking a seat that is no longer there answers False.
        assert bridge.kick(player_id) is False
    finally:
        client.close()


def test_kicking_without_hosting_is_a_no_op(qapp: QApplication) -> None:
    joined = SessionBridge()
    try:
        assert joined.kick("p0") is False
    finally:
        joined.stop()


# -- joining ---------------------------------------------------------------


def test_a_client_bridge_reports_connect_and_history(qapp: QApplication) -> None:
    state = new_session("Table")
    host_side = SessionBridge()
    player_side = SessionBridge()
    try:
        host, port = host_side.host(state, port=0, bind="127.0.0.1")
        assert host_side.server is not None
        host_side.server.roll(label="Initiative", bonus=2)

        connected = collect(player_side.connected)
        history = collect(player_side.historyReplaced)
        player_side.join(discovery.JoinCode(host, port, state.host_token), "Aria")

        drain(qapp, connected)
        assert connected[0][0]["session_name"] == "Table"
        assert history[0][0][0]["label"] == "Initiative"
        assert player_side.joined is True
    finally:
        player_side.stop()
        host_side.stop()


def test_a_bridge_cannot_both_host_and_join(bridge: SessionBridge) -> None:
    host_locally(bridge)
    with pytest.raises(session_bridge.SessionBridgeError):
        bridge.join(discovery.JoinCode("127.0.0.1", 1, "token"), "Aria")


# -- the process-wide handle -----------------------------------------------


def test_the_active_session_is_settable(qapp: QApplication) -> None:
    assert active_session() is None
    made = SessionBridge()
    set_active_session(made)
    assert active_session() is made
    set_active_session(None)
    assert active_session() is None


# -- hosting through a relay ----------------------------------------------


def test_hosting_through_a_relay_publishes_a_relay_join_code(
    qapp: QApplication, bridge: SessionBridge, relay_box
) -> None:
    state = new_session("Relayed")
    published = collect(bridge.published)

    bridge.host(state, port=0, bind="127.0.0.1", relay_url=relay_box.base)
    assert bridge.relaying is True
    bridge.publish()
    qapp.processEvents()

    assert len(published) == 1
    reachability = published[0][0]
    assert reachability.method == discovery.METHOD_RELAY
    assert reachability.internet_reachable is True
    assert discovery.ADVICE_RELAY in reachability.advice

    code = discovery.decode_join_code(bridge.join_code())
    assert code.host.startswith("mmrelay")
    assert code.host.endswith(state.id)
    assert code.token == state.host_token


def test_a_player_joins_a_relayed_session_through_its_join_code(
    qapp: QApplication, bridge: SessionBridge, relay_box
) -> None:
    """The whole point of the seam: joining reads the code and nothing else."""
    state = new_session("Relayed")
    bridge.host(state, port=0, bind="127.0.0.1", relay_url=relay_box.base)
    bridge.publish()
    qapp.processEvents()

    code = discovery.decode_join_code(bridge.join_code())
    player = SessionBridge()
    try:
        client = player.join(code, "Ada")
        assert client.connected is True
        deadline = time.monotonic() + 5.0
        while len(bridge.server.connected_player_ids()) < 1 and time.monotonic() < deadline:
            qapp.processEvents()
            time.sleep(0.01)
        assert bridge.server.connected_player_ids()
    finally:
        player.stop()


def test_an_unreachable_relay_refuses_to_host(bridge: SessionBridge) -> None:
    with pytest.raises(OSError):
        bridge.host(new_session(), port=0, bind="127.0.0.1", relay_url="mmrelay+tcp://127.0.0.1:1")
    assert bridge.hosting is False
    assert bridge.relaying is False


def test_stopping_forgets_that_it_was_relaying(
    qapp: QApplication, bridge: SessionBridge, relay_box
) -> None:
    bridge.host(new_session(), port=0, bind="127.0.0.1", relay_url=relay_box.base)
    bridge.stop()
    assert bridge.relaying is False

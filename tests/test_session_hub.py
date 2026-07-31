"""The session hub: many sessions on one box, and who may create them.

These run over plain loopback sockets rather than a relay — the hub takes its
transport as a seam precisely so its own behaviour can be proved without a relay
box in the loop. What a relay adds (dialling out, TLS) is
:mod:`tests.test_session_relay`'s subject, not this one's.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from mm_companion.core import storage
from mm_companion.core.session import store
from mm_companion.core.session.client import SessionClient, SessionClientError
from mm_companion.core.session.discovery import decode_join_code
from mm_companion.core.session.net import TcpTransport
from mm_companion.core.session.protocol import (
    ERROR_BAD_TOKEN,
    ERROR_HUB_FULL,
    AdminHello,
    CreateSessionRequest,
    DeleteSessionRequest,
    ErrorMessage,
    ListSessionsRequest,
    RenameSessionRequest,
    SessionCatalog,
)
from mm_companion.server import hub as hub_mod
from mm_companion.server.hub import HubError, SessionHub

TIMEOUT = 5.0
SECRET = "admin-secret-for-tests"


@pytest.fixture(autouse=True)
def _home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("MM_COMPANION_HOME", str(tmp_path))
    storage.ensure_workspace()
    return tmp_path


class LoopbackTransports:
    """A transport per session id, all on ephemeral loopback ports.

    Stands in for the relay: the hub asks for one transport per session (plus one
    for the control channel) and never cares what kind it gets.
    """

    def __init__(self) -> None:
        self.ports: dict[str, int] = {}
        self._transports: dict[str, TcpTransport] = {}

    def __call__(self, session_id: str) -> TcpTransport:
        transport = _RecordingTransport(self, session_id)
        self._transports[session_id] = transport
        return transport

    def address(self, session_id: str) -> tuple[str, int]:
        deadline = time.monotonic() + TIMEOUT
        while session_id not in self.ports:
            if time.monotonic() > deadline:
                raise AssertionError(f"{session_id} never bound a port")
            time.sleep(0.02)
        return ("127.0.0.1", self.ports[session_id])


class _RecordingTransport(TcpTransport):
    """A loopback transport that reports the port it landed on."""

    def __init__(self, registry: LoopbackTransports, session_id: str) -> None:
        super().__init__()
        self._registry = registry
        self._session_id = session_id

    def listen(self, host: str = "127.0.0.1", port: int = 0):
        listener = super().listen("127.0.0.1", 0)
        self._registry.ports[self._session_id] = listener.address[1]
        return listener


@pytest.fixture
def hub():
    """A started hub over loopback, stopped at the end of the test."""
    hubs: list[SessionHub] = []

    def make(**kwargs) -> tuple[SessionHub, LoopbackTransports]:
        transports = kwargs.pop("transports", None) or LoopbackTransports()
        instance = SessionHub(
            kwargs.pop("relay_base", "relay.example.net:47332"),
            kwargs.pop("admin_secret", SECRET),
            transport_factory=transports,
            **kwargs,
        )
        instance.start()
        hubs.append(instance)
        return instance, transports

    yield make

    for instance in hubs:
        instance.stop()


def admin(hub: SessionHub, transports: LoopbackTransports, secret: str = SECRET):
    """An authenticated control connection, plus its first catalog."""
    host, port = transports.address(hub.control_id)
    connection = TcpTransport().connect(host, port, timeout=TIMEOUT)
    connection.set_timeout(TIMEOUT)
    connection.send(AdminHello(secret=secret))
    return connection, connection.receive()


def join(hub: SessionHub, transports: LoopbackTransports, entry: dict, name="Volt", **kwargs):
    """A player client dialling the session the catalog entry describes."""
    code = decode_join_code(entry["join_code"])
    host, port = transports.address(entry["id"])
    client = SessionClient(host, port, token=code.token, display_name=name, **kwargs)
    client.connect(timeout=TIMEOUT)
    return client


# -- the catalog -----------------------------------------------------------


def test_a_new_hub_holds_nothing(hub) -> None:
    instance, _ = hub()
    assert instance.catalog() == []


def test_creating_a_session_hosts_it_at_once(hub) -> None:
    instance, _ = hub()

    entry = instance.create("Friday Game")

    assert entry["name"] == "Friday Game"
    assert instance.session_ids() == [entry["id"]]
    assert store.load_session(entry["id"]).name == "Friday Game"


def test_the_catalog_carries_the_two_secrets_a_gm_cannot_derive(hub) -> None:
    instance, _ = hub()
    entry = instance.create("Friday Game")

    assert entry["gm_token"] == store.load_session(entry["id"]).gm_token
    assert decode_join_code(entry["join_code"]).token == store.load_session(entry["id"]).host_token


def test_the_join_code_points_at_the_relay_and_names_the_session(hub) -> None:
    instance, _ = hub(relay_base="relay.example.net:47332")
    entry = instance.create("Friday Game")

    code = decode_join_code(entry["join_code"])

    assert code.is_relay
    assert code.host.endswith(f"/{entry['id']}")
    assert "relay.example.net" in code.host


def test_a_hub_resumes_every_session_it_had(hub) -> None:
    first, _ = hub()
    kept = first.create("Friday Game")["id"]
    first.stop()

    resumed, _ = hub()

    assert resumed.session_ids() == [kept]


def test_the_session_limit_is_enforced(hub) -> None:
    instance, _ = hub(max_sessions=1)
    instance.create("One")

    with pytest.raises(HubError, match="limit"):
        instance.create("Two")


def test_deleting_a_session_erases_it(hub) -> None:
    instance, _ = hub()
    entry = instance.create("Friday Game")

    instance.delete(entry["id"])

    assert instance.session_ids() == []
    with pytest.raises(store.SessionStoreError):
        store.load_session(entry["id"])


def test_deleting_an_unknown_session_is_an_error_not_a_crash(hub) -> None:
    instance, _ = hub()
    with pytest.raises(HubError):
        instance.delete("no-such-session")


def test_a_hub_without_an_admin_secret_refuses_to_exist() -> None:
    # The secret is the only thing between a stranger and the catalog, so an
    # empty one has to fail loudly at startup rather than quietly allow all.
    with pytest.raises(HubError):
        SessionHub("relay.example.net", "")


# -- the control channel ---------------------------------------------------


def test_the_admin_secret_opens_the_catalog(hub) -> None:
    instance, transports = hub()
    instance.create("Friday Game")

    connection, catalog = admin(instance, transports)
    try:
        assert isinstance(catalog, SessionCatalog)
        assert [e["name"] for e in catalog.sessions] == ["Friday Game"]
    finally:
        connection.close()


def test_a_wrong_admin_secret_is_refused(hub) -> None:
    instance, transports = hub()

    connection, answer = admin(instance, transports, secret="guess")
    try:
        assert isinstance(answer, ErrorMessage)
        assert answer.code == ERROR_BAD_TOKEN
    finally:
        connection.close()


def test_a_players_join_code_does_not_open_the_catalog(hub) -> None:
    # The whole of "only a GM creates sessions": a join code is a session's
    # secret, and the catalog is guarded by a different one entirely.
    instance, transports = hub()
    entry = instance.create("Friday Game")

    connection, answer = admin(
        instance, transports, secret=decode_join_code(entry["join_code"]).token
    )
    try:
        assert isinstance(answer, ErrorMessage)
        assert answer.code == ERROR_BAD_TOKEN
    finally:
        connection.close()


def test_a_gm_creates_a_session_over_the_control_channel(hub) -> None:
    instance, transports = hub()
    connection, _ = admin(instance, transports)
    try:
        connection.send(CreateSessionRequest(name="Friday Game"))
        catalog = connection.receive()

        assert [e["name"] for e in catalog.sessions] == ["Friday Game"]
        assert instance.session_ids() == [catalog.sessions[0]["id"]]
    finally:
        connection.close()


def test_every_control_request_answers_with_the_whole_catalog(hub) -> None:
    # So a GM's list cannot drift out of step with the server's.
    instance, transports = hub()
    connection, _ = admin(instance, transports)
    try:
        connection.send(CreateSessionRequest(name="One"))
        created = connection.receive()
        session_id = created.sessions[0]["id"]

        connection.send(RenameSessionRequest(session_id=session_id, name="Renamed"))
        renamed = connection.receive()
        assert [e["name"] for e in renamed.sessions] == ["Renamed"]

        connection.send(ListSessionsRequest())
        listed = connection.receive()
        assert [e["name"] for e in listed.sessions] == ["Renamed"]

        connection.send(DeleteSessionRequest(session_id=session_id))
        deleted = connection.receive()
        assert deleted.sessions == []
    finally:
        connection.close()


def test_the_limit_comes_back_as_a_named_error(hub) -> None:
    instance, transports = hub(max_sessions=1)
    connection, _ = admin(instance, transports)
    try:
        connection.send(CreateSessionRequest(name="One"))
        connection.receive()
        connection.send(CreateSessionRequest(name="Two"))
        answer = connection.receive()

        assert isinstance(answer, ErrorMessage)
        assert answer.code == ERROR_HUB_FULL
    finally:
        connection.close()


# -- playing on a hosted session -------------------------------------------


def test_a_player_joins_a_session_with_nobody_else_in_it(hub) -> None:
    # The point of the whole exercise: no GM, no other players, still a game.
    instance, transports = hub()
    entry = instance.create("Friday Game")

    client = join(instance, transports, entry)
    try:
        assert client.session_name == "Friday Game"
        assert not client.is_gm
        client.request_roll("Athletics")
    finally:
        client.close()


def test_a_gm_takes_their_seat_with_the_catalogs_gm_token(hub) -> None:
    instance, transports = hub()
    entry = instance.create("Friday Game")

    gm = join(instance, transports, entry, name="GM", gm_token=entry["gm_token"])
    try:
        assert gm.is_gm
    finally:
        gm.close()


def test_the_join_code_alone_never_confers_gm_on_a_hosted_session(hub) -> None:
    instance, transports = hub()
    entry = instance.create("Friday Game")

    client = join(instance, transports, entry)
    try:
        assert not client.is_gm
    finally:
        client.close()


def test_a_wrong_gm_token_is_refused(hub) -> None:
    instance, transports = hub()
    entry = instance.create("Friday Game")

    with pytest.raises(SessionClientError) as excinfo:
        join(instance, transports, entry, name="Impostor", gm_token="nope")

    assert excinfo.value.code == ERROR_BAD_TOKEN


def test_rolls_survive_everyone_leaving(hub) -> None:
    instance, transports = hub()
    entry = instance.create("Friday Game")

    client = join(instance, transports, entry)
    client.request_roll("Athletics")
    _wait(lambda: len(store.load_rolls(entry["id"])) == 1, "roll persisted")
    client.close()

    returning = join(instance, transports, entry, name="Volt")
    try:
        assert [r["label"] for r in returning.history] == ["Athletics"]
    finally:
        returning.close()


# -- idle sessions ---------------------------------------------------------


def test_an_idle_session_sheds_its_history_but_stays_joinable(hub, monkeypatch) -> None:
    monkeypatch.setattr(hub_mod, "JANITOR_INTERVAL", 0.05)
    instance, transports = hub(idle_unload=0.0)
    entry = instance.create("Friday Game")

    client = join(instance, transports, entry)
    client.request_roll("Athletics")
    _wait(lambda: len(store.load_rolls(entry["id"])) == 1, "roll persisted")
    client.close()

    _wait(lambda: not instance._entries[entry["id"]].loaded, "history shed")

    # Still reachable, and the history comes back with the next arrival.
    returning = join(instance, transports, entry, name="Volt")
    try:
        assert [r["label"] for r in returning.history] == ["Athletics"]
        assert instance._entries[entry["id"]].loaded
    finally:
        returning.close()


def test_a_reloaded_session_keeps_numbering_where_it_left_off(hub, monkeypatch) -> None:
    # Sequence numbers come from the tail of the in-memory log, so shedding it
    # without reloading first would restart them and corrupt the history.
    monkeypatch.setattr(hub_mod, "JANITOR_INTERVAL", 0.05)
    instance, transports = hub(idle_unload=0.0)
    entry = instance.create("Friday Game")

    first = join(instance, transports, entry)
    first.request_roll("One")
    _wait(lambda: len(store.load_rolls(entry["id"])) == 1, "first roll")
    first.close()
    _wait(lambda: not instance._entries[entry["id"]].loaded, "history shed")

    second = join(instance, transports, entry, name="Volt")
    try:
        second.request_roll("Two")
        _wait(lambda: len(store.load_rolls(entry["id"])) == 2, "second roll")
        assert [r.seq for r in store.load_rolls(entry["id"])] == [1, 2]
    finally:
        second.close()


def test_a_busy_session_is_never_unloaded(hub, monkeypatch) -> None:
    monkeypatch.setattr(hub_mod, "JANITOR_INTERVAL", 0.05)
    instance, transports = hub(idle_unload=0.0)
    entry = instance.create("Friday Game")

    client = join(instance, transports, entry)
    try:
        client.request_roll("Athletics")
        time.sleep(0.3)  # several janitor passes with somebody still here
        assert instance._entries[entry["id"]].loaded
    finally:
        client.close()


def test_stopping_the_hub_stops_every_session(hub) -> None:
    instance, _ = hub()
    instance.create("One")
    instance.create("Two")
    servers = [instance._entries[i].server for i in instance.session_ids()]

    instance.stop()

    assert all(not server.running for server in servers)


def _wait(predicate, message: str, timeout: float = TIMEOUT) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.02)
    raise AssertionError(f"timed out waiting for {message}")


def test_the_relay_secret_is_minted_once_and_then_kept(hub) -> None:
    # A fresh secret each run would leave a restarted hub unable to reclaim its
    # own session ids on the relay.
    instance, _ = hub()
    entry = instance.create("Friday Game")

    first = hub_mod.relay_secret(entry["id"])

    assert first
    assert hub_mod.relay_secret(entry["id"]) == first
    assert (store.session_dir(entry["id"]) / "relay.secret").read_text(encoding="utf-8") == first


def test_deleting_a_session_takes_its_relay_secret_with_it(hub) -> None:
    instance, _ = hub()
    entry = instance.create("Friday Game")
    hub_mod.relay_secret(entry["id"])

    instance.delete(entry["id"])

    assert not (store.session_dir(entry["id"]) / "relay.secret").exists()


def test_threads_do_not_pile_up_across_control_connections(hub) -> None:
    instance, transports = hub()
    before = threading.active_count()

    for _ in range(5):
        connection, _ = admin(instance, transports)
        connection.close()

    time.sleep(0.3)
    assert threading.active_count() < before + 5

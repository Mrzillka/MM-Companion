"""The session hub: many sessions on one box, and who is allowed to touch them.

The rule these are mostly about: **creating is open, everything else needs the
session's own gm token.** Anyone running the app may host a game here; nobody can
rename, delete, or enumerate a table they did not make. The server's operator is
the one exception, so abandoned sessions can be cleaned up.

These run over plain loopback sockets rather than a relay — the hub takes its
transport as a seam precisely so its own behaviour can be proved without a relay
box in the loop. What a relay adds (dialling out, TLS) is
:mod:`tests.test_session_relay`'s subject, not this one's.
"""

from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from mm_companion.core import storage
from mm_companion.core.session import hub_client as hub_client_mod
from mm_companion.core.session import store
from mm_companion.core.session.client import SessionClient, SessionClientError
from mm_companion.core.session.discovery import decode_join_code
from mm_companion.core.session.hub_client import HubClient, HubClientError
from mm_companion.core.session.net import TcpTransport
from mm_companion.core.session.protocol import (
    ERROR_BAD_TOKEN,
    ERROR_HUB_FULL,
    ERROR_RATE_LIMIT,
    ERROR_UNKNOWN_SESSION,
    ControlHello,
    ControlWelcome,
    CreateSessionRequest,
    DeleteSessionRequest,
    ErrorMessage,
    ListSessionsRequest,
    SessionCatalog,
)
from mm_companion.server import hub as hub_mod
from mm_companion.server.hub import HubError, SessionHub, UnknownSessionError

TIMEOUT = 5.0
SECRET = "operator-secret-for-tests"


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

    def __call__(self, session_id: str) -> TcpTransport:
        return _RecordingTransport(self, session_id)

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


class _FixedTransport(TcpTransport):
    """Dials one known address whatever it is asked for — the relay's job."""

    def __init__(self, host: str, port: int) -> None:
        super().__init__()
        self._host = host
        self._port = port

    def connect(self, host: str = "", port: int = 0, *, timeout: float = TIMEOUT):
        return super().connect(self._host, self._port, timeout=timeout)


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


def control(hub: SessionHub, transports: LoopbackTransports, secret: str = ""):
    """A raw control connection, plus the server's opening answer."""
    host, port = transports.address(hub.control_id)
    connection = TcpTransport().connect(host, port, timeout=TIMEOUT)
    connection.set_timeout(TIMEOUT)
    connection.send(ControlHello(secret=secret))
    return connection, connection.receive()


def client(hub: SessionHub, transports: LoopbackTransports, secret: str = "") -> HubClient:
    """A HubClient pointed at the loopback stand-in for the relay."""
    host, port = transports.address(hub.control_id)
    return HubClient(
        "relay.example.net:47332",
        secret,
        control_id=hub.control_id,
        transport=_FixedTransport(host, port),
    )


def join(transports: LoopbackTransports, entry: dict, name: str = "Volt", **kwargs):
    """A player client dialling the session an entry describes."""
    code = decode_join_code(entry["join_code"])
    host, port = transports.address(entry["id"])
    session = SessionClient(host, port, token=code.token, display_name=name, **kwargs)
    session.connect(timeout=TIMEOUT)
    return session


def _wait(predicate, message: str, timeout: float = TIMEOUT) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.02)
    raise AssertionError(f"timed out waiting for {message}")


# -- anyone may create -----------------------------------------------------


def test_a_new_hub_holds_nothing(hub) -> None:
    instance, _ = hub()
    assert instance.catalog() == []


def test_creating_a_session_needs_no_credential(hub) -> None:
    # The whole point of the rework: the server is a public utility, and someone
    # who has just installed the app can host a game on it.
    instance, transports = hub()

    with client(instance, transports) as anyone:
        assert not anyone.operator
        entry = anyone.create("Friday Game")

    assert entry["name"] == "Friday Game"
    assert instance.session_ids() == [entry["id"]]


def test_a_hub_with_no_operator_secret_still_lets_anyone_host(hub) -> None:
    instance, transports = hub(admin_secret="")

    with client(instance, transports) as anyone:
        assert anyone.create("Friday Game")["id"]


def test_an_empty_secret_does_not_make_everyone_the_operator(hub) -> None:
    # tokens_match("", "") is True, so a hub configured without a secret would
    # hand operator rights to every caller if this were not guarded.
    instance, transports = hub(admin_secret="")

    with client(instance, transports) as anyone:
        assert not anyone.operator
        with pytest.raises(HubClientError):
            anyone.refresh()


def test_the_create_hands_back_the_two_secrets_and_nothing_else_does(hub) -> None:
    instance, transports = hub()

    with client(instance, transports) as anyone:
        entry = anyone.create("Friday Game")
        # The opening answer carries no catalog for an ordinary caller.
        assert anyone.sessions == []

    stored = store.load_session(entry["id"])
    assert entry["gm_token"] == stored.gm_token
    assert decode_join_code(entry["join_code"]).token == stored.host_token


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


# -- ownership -------------------------------------------------------------


def test_only_the_session_s_own_gm_can_delete_it(hub) -> None:
    instance, transports = hub()
    with client(instance, transports) as owner:
        mine = owner.create("Mine")

    with client(instance, transports) as stranger:
        with pytest.raises(HubClientError) as excinfo:
            stranger.delete(mine["id"])
    assert excinfo.value.code == ERROR_UNKNOWN_SESSION
    assert instance.session_ids() == [mine["id"]]

    with client(instance, transports) as owner:
        owner.delete(mine["id"], mine["gm_token"])
    assert instance.session_ids() == []


def test_only_the_session_s_own_gm_can_rename_it(hub) -> None:
    instance, transports = hub()
    with client(instance, transports) as owner:
        mine = owner.create("Mine")

    with client(instance, transports) as stranger:
        with pytest.raises(HubClientError):
            stranger.rename(mine["id"], "Hijacked")

    assert instance._entry(mine["id"]).state.name == "Mine"


def test_a_wrong_token_and_an_unknown_id_are_indistinguishable(hub) -> None:
    # Otherwise the difference is an oracle for which session ids exist here.
    instance, transports = hub()
    with client(instance, transports) as owner:
        mine = owner.create("Mine")

    with client(instance, transports) as stranger:
        with pytest.raises(HubClientError) as wrong_token:
            stranger.delete(mine["id"], "not-the-token")
        with pytest.raises(HubClientError) as unknown_id:
            stranger.delete("no-such-session", "not-the-token")

    assert wrong_token.value.code == unknown_id.value.code
    assert str(wrong_token.value) == str(unknown_id.value)


def test_a_gm_asks_after_their_own_session(hub) -> None:
    instance, transports = hub()
    with client(instance, transports) as owner:
        mine = owner.create("Mine")
        again = owner.status(mine["id"], mine["gm_token"])

    assert again["name"] == "Mine"
    assert again["id"] == mine["id"]


def test_status_reports_a_session_that_is_gone_as_empty(hub) -> None:
    # How a GM's app learns its session was swept, rather than showing a row that
    # no longer opens anything.
    instance, transports = hub()
    with client(instance, transports) as owner:
        mine = owner.create("Mine")
        owner.delete(mine["id"], mine["gm_token"])

        assert owner.status(mine["id"], mine["gm_token"]) == {}


def test_status_without_the_token_reveals_nothing(hub) -> None:
    instance, transports = hub()
    with client(instance, transports) as owner:
        mine = owner.create("Mine")

    with client(instance, transports) as stranger:
        assert stranger.status(mine["id"]) == {}


# -- the operator ----------------------------------------------------------


def test_the_operator_secret_opens_the_whole_catalog(hub) -> None:
    instance, transports = hub()
    instance.create("Friday Game")

    with client(instance, transports, SECRET) as operator:
        assert operator.operator
        assert [e["name"] for e in operator.sessions] == ["Friday Game"]


def test_an_ordinary_caller_cannot_list_the_sessions(hub) -> None:
    instance, transports = hub()
    instance.create("Someone else's game")

    with client(instance, transports) as anyone:
        with pytest.raises(HubClientError) as excinfo:
            anyone.refresh()

    assert excinfo.value.code == ERROR_BAD_TOKEN


def test_the_operator_deletes_anything(hub) -> None:
    instance, transports = hub()
    with client(instance, transports) as owner:
        theirs = owner.create("Not mine")

    with client(instance, transports, SECRET) as operator:
        operator.delete(theirs["id"])

    assert instance.session_ids() == []


def test_a_wrong_operator_secret_is_refused_not_downgraded(hub) -> None:
    # Being quietly seated as an ordinary caller would leave an operator
    # believing they had powers they do not.
    instance, transports = hub()

    with pytest.raises(HubClientError) as excinfo:
        client(instance, transports, "guess").connect(timeout=TIMEOUT)

    assert excinfo.value.code == ERROR_BAD_TOKEN


def test_a_join_code_is_not_an_operator_secret(hub) -> None:
    instance, transports = hub()
    entry = instance.create("Friday Game")

    connection, answer = control(
        instance, transports, secret=decode_join_code(entry["join_code"]).token
    )
    try:
        assert isinstance(answer, ErrorMessage)
        assert answer.code == ERROR_BAD_TOKEN
    finally:
        connection.close()


# -- limits ----------------------------------------------------------------


def test_the_session_ceiling_is_enforced_and_named(hub) -> None:
    instance, transports = hub(max_sessions=1)

    with client(instance, transports) as anyone:
        anyone.create("One")
        with pytest.raises(HubClientError) as excinfo:
            anyone.create("Two")

    assert excinfo.value.code == ERROR_HUB_FULL


def test_one_connection_cannot_create_without_limit(hub, monkeypatch) -> None:
    monkeypatch.setattr(hub_mod, "MAX_CREATES_PER_CONNECTION", 2)
    instance, transports = hub()

    connection, _ = control(instance, transports)
    try:
        for index in range(2):
            connection.send(CreateSessionRequest(name=f"Game {index}"))
            connection.receive()
        connection.send(CreateSessionRequest(name="One too many"))
        answer = connection.receive()

        assert isinstance(answer, ErrorMessage)
        assert answer.code == ERROR_RATE_LIMIT
    finally:
        connection.close()

    assert len(instance.session_ids()) == 2


def test_the_operator_is_not_rate_limited(hub, monkeypatch) -> None:
    monkeypatch.setattr(hub_mod, "MAX_CREATES_PER_CONNECTION", 1)
    instance, transports = hub()

    with client(instance, transports, SECRET) as operator:
        operator.create("One")
        operator.create("Two")

    assert len(instance.session_ids()) == 2


# -- the sweep -------------------------------------------------------------


def test_a_session_untouched_for_too_long_is_swept(hub) -> None:
    instance, _ = hub(retention_days=30)
    entry = instance.create("Abandoned")
    state = instance._entry(entry["id"]).state
    state.updated_at = (datetime.now(timezone.utc) - timedelta(days=31)).isoformat()

    assert instance.sweep() == [entry["id"]]
    assert instance.session_ids() == []


def test_a_recently_used_session_is_left_alone(hub) -> None:
    instance, _ = hub(retention_days=30)
    entry = instance.create("Active")
    state = instance._entry(entry["id"]).state
    state.updated_at = (datetime.now(timezone.utc) - timedelta(days=29)).isoformat()

    assert instance.sweep() == []
    assert instance.session_ids() == [entry["id"]]


def test_a_session_with_someone_in_it_is_never_swept(hub) -> None:
    # However old the timestamp looks, ending a game in progress is never right.
    instance, transports = hub(retention_days=30)
    entry = instance.create("In progress")
    instance._entry(entry["id"]).state.updated_at = (
        datetime.now(timezone.utc) - timedelta(days=999)
    ).isoformat()

    player = join(transports, entry)
    try:
        _wait(lambda: instance._entry(entry["id"]).server.connected_player_ids(), "player seated")
        assert instance.sweep() == []
    finally:
        player.close()


def test_the_sweep_can_be_turned_off(hub) -> None:
    instance, _ = hub(retention_days=0)
    entry = instance.create("Ancient")
    instance._entry(entry["id"]).state.updated_at = "2001-01-01T00:00:00+00:00"

    assert instance.sweep() == []
    assert instance.session_ids() == [entry["id"]]


def test_an_unreadable_timestamp_is_treated_as_very_old(hub) -> None:
    # The alternative -- treating it as brand new -- would leave a corrupt entry
    # on the box for good.
    instance, _ = hub(retention_days=30)
    entry = instance.create("Corrupt")
    instance._entry(entry["id"]).state.updated_at = "not a date"

    assert instance.sweep() == [entry["id"]]


# -- playing on a hosted session -------------------------------------------


def test_a_player_joins_a_session_with_nobody_else_in_it(hub) -> None:
    # The point of the whole exercise: no GM, no other players, still a game.
    instance, transports = hub()
    entry = instance.create("Friday Game")

    player = join(transports, entry)
    try:
        assert player.session_name == "Friday Game"
        assert not player.is_gm
        player.request_roll("Athletics")
    finally:
        player.close()


def test_the_creator_takes_the_gm_seat_with_the_token_they_were_given(hub) -> None:
    instance, transports = hub()
    with client(instance, transports) as owner:
        entry = owner.create("Friday Game")

    gm = join(transports, entry, name="GM", gm_token=entry["gm_token"])
    try:
        assert gm.is_gm
    finally:
        gm.close()


def test_the_join_code_alone_never_confers_gm(hub) -> None:
    instance, transports = hub()
    entry = instance.create("Friday Game")

    player = join(transports, entry)
    try:
        assert not player.is_gm
    finally:
        player.close()


def test_a_wrong_gm_token_is_refused(hub) -> None:
    instance, transports = hub()
    entry = instance.create("Friday Game")

    with pytest.raises(SessionClientError) as excinfo:
        join(transports, entry, name="Impostor", gm_token="nope")

    assert excinfo.value.code == ERROR_BAD_TOKEN


def test_rolls_survive_everyone_leaving(hub) -> None:
    instance, transports = hub()
    entry = instance.create("Friday Game")

    player = join(transports, entry)
    player.request_roll("Athletics")
    _wait(lambda: len(store.load_rolls(entry["id"])) == 1, "roll persisted")
    player.close()

    returning = join(transports, entry)
    try:
        assert [r["label"] for r in returning.history] == ["Athletics"]
    finally:
        returning.close()


# -- idle sessions ---------------------------------------------------------


def test_an_idle_session_sheds_its_history_but_stays_joinable(hub, monkeypatch) -> None:
    monkeypatch.setattr(hub_mod, "JANITOR_INTERVAL", 0.05)
    instance, transports = hub(idle_unload=0.0)
    entry = instance.create("Friday Game")

    player = join(transports, entry)
    player.request_roll("Athletics")
    _wait(lambda: len(store.load_rolls(entry["id"])) == 1, "roll persisted")
    player.close()

    _wait(lambda: not instance._entry(entry["id"]).loaded, "history shed")

    returning = join(transports, entry)
    try:
        assert [r["label"] for r in returning.history] == ["Athletics"]
        assert instance._entry(entry["id"]).loaded
    finally:
        returning.close()


def test_a_reloaded_session_keeps_numbering_where_it_left_off(hub, monkeypatch) -> None:
    # Sequence numbers come from the tail of the in-memory log, so shedding it
    # without reloading first would restart them and corrupt the history.
    monkeypatch.setattr(hub_mod, "JANITOR_INTERVAL", 0.05)
    instance, transports = hub(idle_unload=0.0)
    entry = instance.create("Friday Game")

    first = join(transports, entry)
    first.request_roll("One")
    _wait(lambda: len(store.load_rolls(entry["id"])) == 1, "first roll")
    first.close()
    _wait(lambda: not instance._entry(entry["id"]).loaded, "history shed")

    second = join(transports, entry)
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

    player = join(transports, entry)
    try:
        player.request_roll("Athletics")
        time.sleep(0.3)  # several janitor passes with somebody still here
        assert instance._entry(entry["id"]).loaded
    finally:
        player.close()


# -- plumbing --------------------------------------------------------------


def test_stopping_the_hub_stops_every_session(hub) -> None:
    instance, _ = hub()
    instance.create("One")
    instance.create("Two")
    servers = [instance._entry(i).server for i in instance.session_ids()]

    instance.stop()

    assert all(not server.running for server in servers)


def test_deleting_an_unknown_session_raises_rather_than_crashing(hub) -> None:
    instance, _ = hub()
    with pytest.raises(UnknownSessionError):
        instance.delete("no-such-session", operator=True)


def test_the_relay_secret_is_minted_once_and_then_kept(hub) -> None:
    instance, _ = hub()
    entry = instance.create("Friday Game")

    first = hub_mod.relay_secret(entry["id"])

    assert first
    assert hub_mod.relay_secret(entry["id"]) == first


def test_deleting_a_session_takes_its_relay_secret_with_it(hub) -> None:
    instance, _ = hub()
    entry = instance.create("Friday Game")
    hub_mod.relay_secret(entry["id"])

    instance.delete(entry["id"], operator=True)

    assert not (store.session_dir(entry["id"]) / "relay.secret").exists()


def test_an_unreachable_server_is_one_readable_error() -> None:
    unreachable = HubClient("relay.example.net:47332", transport=_FixedTransport("127.0.0.1", 1))
    with pytest.raises(HubClientError, match="could not reach"):
        unreachable.connect(timeout=1.0)


def test_the_control_url_names_the_channel_on_the_relay() -> None:
    url = hub_client_mod.control_url("mmcompanion.duckdns.org")
    assert url.startswith("mmrelay://mmcompanion.duckdns.org")
    assert url.endswith("/mm-control")


def test_the_app_ships_with_a_default_server() -> None:
    # So someone who has just installed it can host without knowing anyone.
    assert hub_client_mod.DEFAULT_SERVER


def test_a_refused_request_does_not_poison_the_channel(hub) -> None:
    instance, transports = hub(max_sessions=1)

    with client(instance, transports) as anyone:
        anyone.create("One")
        with pytest.raises(HubClientError):
            anyone.create("Two")
        # Still usable: the GM should be able to try something else.
        assert anyone.status("no-such-session") == {}


def test_threads_do_not_pile_up_across_control_connections(hub) -> None:
    instance, transports = hub()
    before = threading.active_count()

    for _ in range(5):
        connection, _ = control(instance, transports)
        connection.close()

    time.sleep(0.3)
    assert threading.active_count() < before + 5


def test_the_opening_answer_is_a_control_welcome(hub) -> None:
    instance, transports = hub()

    connection, answer = control(instance, transports)
    try:
        assert isinstance(answer, ControlWelcome)
        assert not answer.operator and answer.sessions == []
    finally:
        connection.close()


def test_an_operator_channel_can_still_list_after_changes(hub) -> None:
    instance, transports = hub()

    connection, _ = control(instance, transports, SECRET)
    try:
        connection.send(CreateSessionRequest(name="One"))
        created = connection.receive()
        connection.send(ListSessionsRequest())
        catalog = connection.receive()

        assert isinstance(catalog, SessionCatalog)
        assert [e["id"] for e in catalog.sessions] == [created.session["id"]]

        connection.send(DeleteSessionRequest(session_id=created.session["id"], gm_token=""))
        connection.receive()
        connection.send(ListSessionsRequest())
        assert connection.receive().sessions == []
    finally:
        connection.close()


def test_a_hub_with_no_operator_refuses_to_delete_someone_elses_session(hub) -> None:
    instance, transports = hub(admin_secret="")
    with client(instance, transports) as owner:
        mine = owner.create("Mine")

    with client(instance, transports) as stranger:
        with pytest.raises(HubClientError):
            stranger.delete(mine["id"])

    assert instance.session_ids() == [mine["id"]]


def test_hub_error_is_still_the_base_of_the_specific_ones() -> None:
    assert issubclass(UnknownSessionError, HubError)
    assert issubclass(hub_mod.HubFullError, HubError)

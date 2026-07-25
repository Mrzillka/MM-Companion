"""The headless ``python -m mm_companion.server`` entrypoint.

Qt-free — it drives the same :class:`SessionServer` the app hosts with, so the
tests here cover the *orchestration* (which session, which address, the join-code
banner) rather than the server itself, which ``test_session_server.py`` already
exercises. Real sockets on ``127.0.0.1`` port 0, so nothing pops a firewall
prompt.
"""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

from mm_companion.core import storage
from mm_companion.core.session import discovery, store
from mm_companion.core.session.model import new_session
from mm_companion.core.session.relay import RelayTransport
from mm_companion.core.session.server import SessionServer
from mm_companion.server import cli


@pytest.fixture(autouse=True)
def _home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv(storage.HOME_ENV_VAR, str(tmp_path))
    storage.ensure_workspace()
    return tmp_path


def _args(*argv: str):
    return cli.build_parser().parse_args(list(argv))


@pytest.fixture
def running_server():
    """A started server on an ephemeral loopback port, stopped on teardown."""
    server = SessionServer(new_session("Table"), host="127.0.0.1", port=0)
    server.start()
    try:
        yield server
    finally:
        server.stop()


# -- resolve_session ------------------------------------------------------


def test_new_creates_and_persists_a_session() -> None:
    state = cli.resolve_session(_args("--new", "Friday Game"))

    assert state.name == "Friday Game"
    # It is on disk, so a later run --session finds it.
    assert store.load_session(state.id).name == "Friday Game"


def test_session_loads_an_existing_one_by_id() -> None:
    created = new_session("Old One")
    store.save_session(created, write_rolls=True)

    loaded = cli.resolve_session(_args("--session", created.id))

    assert loaded.id == created.id


def test_no_argument_resumes_the_most_recent_session() -> None:
    # updated_at has whole-second resolution, so set them apart explicitly.
    older = new_session("Older")
    older.updated_at = "2020-01-01T00:00:00+00:00"
    store.save_session(older, write_rolls=True)
    newer = new_session("Newer")
    newer.updated_at = "2020-01-02T00:00:00+00:00"
    store.save_session(newer, write_rolls=True)

    resolved = cli.resolve_session(_args())

    assert resolved.id == newer.id


def test_no_argument_with_no_sessions_is_a_clean_error() -> None:
    with pytest.raises(store.SessionStoreError):
        cli.resolve_session(_args())


def test_a_missing_session_id_raises() -> None:
    with pytest.raises(store.SessionStoreError):
        cli.resolve_session(_args("--session", "does-not-exist"))


# -- build_transport ------------------------------------------------------


def test_no_relay_means_a_direct_listening_socket() -> None:
    assert cli.build_transport("", new_session()) is None


def test_a_relay_builds_a_relay_transport_carrying_the_session_id() -> None:
    state = new_session()
    transport = cli.build_transport("relay.example.net:9000", state)

    assert isinstance(transport, RelayTransport)
    assert transport.relay.session_id == state.id


# -- publish --------------------------------------------------------------


def test_publish_lan_only_encodes_the_bound_address(running_server: SessionServer) -> None:
    reach = cli.publish(running_server, None, _args("--no-upnp"))

    assert reach.method == discovery.METHOD_LAN
    code = discovery.decode_join_code(reach.join_code(running_server.state.host_token))
    assert code.port == running_server.address[1]


def test_publish_takes_a_manual_host_at_its_word(running_server: SessionServer) -> None:
    reach = cli.publish(running_server, None, _args("--manual-host", "play.example.net:5000"))

    assert reach.method == discovery.METHOD_MANUAL
    code = discovery.decode_join_code(reach.join_code(running_server.state.host_token))
    assert (code.host, code.port) == ("play.example.net", 5000)


def test_publish_off_a_relay_needs_no_probe() -> None:
    state = new_session()
    transport = cli.build_transport("relay.example.net:9000", state)
    server = SessionServer(state, host="127.0.0.1", port=0, transport=None)
    # We do not start it: relay publishing reads the transport, not the socket.
    reach = cli.publish(server, transport, _args("--relay", "relay.example.net:9000"))

    assert reach.method == discovery.METHOD_RELAY
    assert reach.internet_reachable


# -- describe -------------------------------------------------------------


def test_the_banner_shows_the_session_and_a_join_code(running_server: SessionServer) -> None:
    reach = cli.publish(running_server, None, _args("--no-upnp"))
    banner = cli.describe(running_server.state, running_server, reach)

    assert running_server.state.name in banner
    assert reach.join_code(running_server.state.host_token) in banner


# -- run end to end -------------------------------------------------------


def test_run_hosts_persists_and_stops(monkeypatch: pytest.MonkeyPatch) -> None:
    # A pre-set stop event makes run() return the moment it would otherwise block.
    stop = threading.Event()
    stop.set()

    code = cli.run(
        ["--new", "Run Test", "--bind", "127.0.0.1", "--port", "0", "--no-upnp"],
        stop=stop,
    )

    assert code == 0
    # The session was created, hosted, and recorded as the last one.
    summaries = store.list_sessions()
    assert [s.name for s in summaries] == ["Run Test"]
    assert storage.load_settings()["session_last_id"] == summaries[0].id


def test_run_reports_a_missing_session() -> None:
    assert cli.run(["--session", "nope", "--no-upnp"], stop=threading.Event()) == 1


def test_run_rejects_a_bad_manual_host() -> None:
    stop = threading.Event()
    stop.set()
    code = cli.run(
        ["--new", "Bad Host", "--bind", "127.0.0.1", "--port", "0", "--manual-host", "host:99999"],
        stop=stop,
    )
    assert code == 2


def test_run_list_is_a_clean_exit() -> None:
    store.save_session(new_session("Listed"), write_rolls=True)
    assert cli.run(["--list"], stop=threading.Event()) == 0


def test_run_and_a_real_client_join(monkeypatch: pytest.MonkeyPatch) -> None:
    # Host in a background thread, connect a real client, then stop it.
    from mm_companion.core.session.client import SessionClient

    state = new_session("Live")
    store.save_session(state, write_rolls=True)

    server = SessionServer(state, host="127.0.0.1", port=0)
    server.start()
    host, port = server.address
    try:
        client = SessionClient(host, port, token=state.host_token, display_name="Aria")
        welcome = client.connect()
        assert welcome.session_name == "Live"
        client.close()
    finally:
        server.stop()

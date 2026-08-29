"""The scene on the wire: one author, everyone else a reader.

Headless — no Qt. Reuses the loopback harness in :mod:`tests.test_session_server`
rather than rebuilding it, so a scene test and a roll test bind their sockets the
same way and wait the same way.

What is worth testing here is not that a list survives a round trip — the
protocol suite covers that — but the three rules the scene actually rests on:
the GM is the only writer, everybody is a reader, and the pictures travel apart
from the board.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from mm_companion.core import storage
from mm_companion.core.session import store
from mm_companion.core.session.client import EVENT_PONG, EVENT_SCENE, EVENT_SCENE_PORTRAIT
from mm_companion.core.session.model import SessionState, new_session
from mm_companion.core.session.protocol import MAX_SCENE_PORTRAIT_CHARS
from mm_companion.core.session.server import EVENT_SCENE as SERVER_EVENT_SCENE
from tests import test_session_server as harness
from tests.test_session_server import Events, gm_connect, wait_for

# The loopback harness lives with the server tests; a scene test wants the same
# sockets and the same waiting. Bound rather than imported because a fixture that
# is also a test parameter reads to the linter as a redefinition.
running_server = harness.running_server
connect = harness.connect

THUG = {"ref": "e1", "name": "Thug", "initiative": 14}
BOSS = {"ref": "e2", "name": "Boss"}


@pytest.fixture(autouse=True)
def _home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv(storage.HOME_ENV_VAR, str(tmp_path))
    storage.ensure_workspace()
    return tmp_path


# --------------------------------------------------------------------------
# The state
# --------------------------------------------------------------------------


def test_the_scene_survives_a_trip_through_the_session_file() -> None:
    """A table hosted on a server outlives the GM's laptop, scene included."""
    state = new_session("Table")
    state.scene = [dict(THUG)]
    state.scene_sources = {"e1": "npc:thug.json"}
    state.scene_portraits = {"e1": "AAAA"}

    back = SessionState.from_dict(state.to_dict())

    assert back.scene == [THUG]
    assert back.scene_sources == {"e1": "npc:thug.json"}
    assert back.scene_portraits == {"e1": "AAAA"}


def test_a_scene_read_back_from_disk_is_sanitized_not_trusted() -> None:
    """A session written by a newer build should degrade to what this one
    understands rather than smuggle a surprise into the next broadcast."""
    back = SessionState.from_dict(
        {"scene": [{"ref": "e1", "toughness": 12}, {"name": "no ref"}], "scene_portraits": "?"}
    )

    assert back.scene == [{"ref": "e1"}]
    assert back.scene_portraits == {}


def test_the_store_writes_the_scene_alongside_the_session() -> None:
    state = new_session("Table")
    state.scene = [dict(THUG)]
    store.save_session(state)

    reloaded = store.load_session(state.id)

    assert reloaded is not None
    assert reloaded.scene == [THUG]


# --------------------------------------------------------------------------
# One author
# --------------------------------------------------------------------------


def test_the_gm_sets_the_scene_and_the_table_is_told(running_server, connect) -> None:
    srv = running_server()
    _player, events = connect(srv, "Alex")

    srv.set_scene([dict(THUG), dict(BOSS)], {"e1": "npc:thug.json"})

    payload = events.next_of(EVENT_SCENE)
    assert [entry["ref"] for entry in payload["entries"]] == ["e1", "e2"]


def test_a_players_scene_is_ignored(running_server, connect) -> None:
    """The board is what everyone is looking at, so it has exactly one writer."""
    srv = running_server(gm_in_process=False)
    player, events = connect(srv, "Alex")

    player.set_scene([dict(THUG)])

    # There is nothing to wait *for*, so wait for something that would have
    # overtaken it: a later message on the same connection, answered.
    player.ping(nonce=1)
    events.next_of(EVENT_PONG)
    assert srv.scene() == []


def test_a_remote_gms_scene_is_honored(running_server, connect) -> None:
    srv = running_server(gm_in_process=False)
    _player, events = connect(srv, "Alex")
    gm, _gm_events = gm_connect(srv)
    try:
        gm.set_scene([dict(THUG)], {"e1": "npc:thug.json"})

        assert events.next_of(EVENT_SCENE)["entries"] == [THUG]
        wait_for(lambda: srv.state.scene_sources == {"e1": "npc:thug.json"})
    finally:
        gm.close()


def test_a_creatures_disposition_reaches_the_table(running_server, connect) -> None:
    """The whole reason the field is public rather than a note in the GM's head:
    telling friend from foe at a glance is most of what a player needs the board
    for, and only the GM knows it."""
    srv = running_server()
    player, events = connect(srv, "Alex")

    srv.set_scene([dict(THUG, disposition="friendly"), dict(BOSS, disposition="neutral")])
    events.next_of(EVENT_SCENE)

    assert [e.get("disposition") for e in player.scene] == ["friendly", "neutral"]


def test_the_scene_is_replaced_whole_rather_than_merged(running_server) -> None:
    srv = running_server()
    srv.set_scene([dict(THUG), dict(BOSS)])

    srv.set_scene([dict(BOSS)])

    assert srv.scene() == [BOSS]


def test_the_gms_private_source_map_is_never_broadcast(running_server, connect) -> None:
    """An NPC's file name is the GM's business, and can be a spoiler outright."""
    srv = running_server()
    player, events = connect(srv, "Alex")

    srv.set_scene([dict(THUG)], {"e1": "npc:TheTraitorIsMarcus.json"})
    events.next_of(EVENT_SCENE)

    assert player.scene == [THUG]
    assert player.scene_sources == {}
    assert not any("Marcus" in str(payload) for _kind, payload in events.seen)


# --------------------------------------------------------------------------
# The pictures, which travel apart from the board
# --------------------------------------------------------------------------


def test_a_portrait_reaches_the_table_on_its_own(running_server, connect) -> None:
    srv = running_server()
    player, events = connect(srv, "Alex")
    srv.set_scene([dict(THUG)])
    events.next_of(EVENT_SCENE)

    srv.set_scene_portrait("e1", "AAAA")

    assert events.next_of(EVENT_SCENE_PORTRAIT) == {"ref": "e1", "portrait": "AAAA"}
    wait_for(lambda: player.scene_portraits == {"e1": "AAAA"})


def test_a_joining_client_is_sent_the_scenes_portraits(running_server, connect) -> None:
    """They are not in the welcome — one already carries a roster and two hundred
    rolls, and a dozen thumbnails on top of that is how a join fails to encode."""
    srv = running_server()
    srv.set_scene([dict(THUG), dict(BOSS)])
    srv.set_scene_portrait("e1", "AAAA")
    srv.set_scene_portrait("e2", "BBBB")

    player, _events = connect(srv, "Late")

    assert player.scene == [THUG, BOSS]
    wait_for(
        lambda: player.scene_portraits == {"e1": "AAAA", "e2": "BBBB"},
        message="both portraits followed the welcome",
    )


def test_an_empty_portrait_clears_the_stored_one(running_server) -> None:
    srv = running_server()
    srv.set_scene([dict(THUG)])
    srv.set_scene_portrait("e1", "AAAA")

    srv.set_scene_portrait("e1", "")

    assert srv.scene_portraits() == {}


def test_an_oversized_portrait_is_dropped_rather_than_stored(running_server) -> None:
    srv = running_server()
    srv.set_scene([dict(THUG)])

    srv.set_scene_portrait("e1", "A" * (MAX_SCENE_PORTRAIT_CHARS + 1))

    assert srv.scene_portraits() == {}


def test_leaving_the_scene_takes_a_portrait_with_it(running_server) -> None:
    """Only the writer of a new scene knows the whole new membership, so the
    pruning has to happen there or a campaign accumulates pictures all year."""
    srv = running_server()
    srv.set_scene([dict(THUG), dict(BOSS)])
    srv.set_scene_portrait("e1", "AAAA")
    srv.set_scene_portrait("e2", "BBBB")

    srv.set_scene([dict(BOSS)])

    assert srv.scene_portraits() == {"e2": "BBBB"}


def test_a_portrait_may_arrive_before_the_entry_that_names_it(running_server) -> None:
    """The GM sends a picture as an entry joins; the two are not ordered."""
    srv = running_server()

    srv.set_scene_portrait("e1", "AAAA")

    assert srv.scene_portraits() == {"e1": "AAAA"}


# --------------------------------------------------------------------------
# The hosting GM's own view
# --------------------------------------------------------------------------


def test_the_hosting_gm_sees_the_board_everyone_else_got(running_server) -> None:
    """``set_scene`` returns what was stored, not what was asked for, so an
    in-process caller cannot end up drawing a scene the table never received."""
    events = Events()
    srv = running_server(on_event=events)

    kept = srv.set_scene([dict(THUG), {"name": "no ref"}])

    assert kept == [THUG]
    assert events.next_of(SERVER_EVENT_SCENE)["entries"] == [THUG]

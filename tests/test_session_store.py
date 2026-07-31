"""The session model and its workspace persistence.

Headless — no Qt, no sockets. Covers the roster and roll-log semantics the
server will rely on in the next phase, and the split-file store (``session.json``
plus an appended ``rolls.jsonl``).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mm_companion.core import storage
from mm_companion.core.dice import resolve_check
from mm_companion.core.session.model import PlayerSlot, RollRecord, SessionState, new_session
from mm_companion.core.session.store import (
    ROLLS_FILENAME,
    SESSION_FILENAME,
    SessionStoreError,
    append_roll,
    delete_session,
    list_sessions,
    load_session,
    save_session,
    session_dir,
)


@pytest.fixture(autouse=True)
def _home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv(storage.HOME_ENV_VAR, str(tmp_path))
    return tmp_path


# --------------------------------------------------------------------------
# The workspace gains a sessions dir
# --------------------------------------------------------------------------


def test_ensure_workspace_creates_the_sessions_dir(_home: Path) -> None:
    workspace = storage.ensure_workspace()

    assert workspace.sessions_dir == _home / storage.SESSIONS_DIRNAME
    assert workspace.sessions_dir.is_dir()


def test_session_settings_keys_exist_with_safe_defaults() -> None:
    assert storage.DEFAULT_SETTINGS["session_last_id"] is None
    assert storage.DEFAULT_SETTINGS["session_player_name"] == ""
    assert storage.DEFAULT_SETTINGS["session_recent_codes"] == []


# --------------------------------------------------------------------------
# The model
# --------------------------------------------------------------------------


def test_a_new_session_gets_an_id_and_a_host_token() -> None:
    one, two = new_session("Tuesday"), new_session("Tuesday")

    assert one.name == "Tuesday"
    assert one.id and one.host_token
    assert one.id != two.id and one.host_token != two.host_token


def test_add_player_registers_a_slot_with_its_own_token() -> None:
    state = new_session()

    alex = state.add_player("Alex")
    sam = state.add_player("Sam", is_gm=True)

    assert state.players[alex.player_id] is alex
    assert alex.token and alex.token != sam.token
    assert sam.is_gm and not alex.is_gm


def test_a_returning_client_reclaims_its_slot_by_token() -> None:
    state = new_session()
    alex = state.add_player("Alex")

    assert state.player_by_token(alex.token) is alex
    assert state.player_by_token("not-the-token") is None
    assert state.player_by_token("") is None


def test_the_public_roster_never_carries_a_players_token() -> None:
    state = new_session()
    state.add_player("Alex")

    roster = state.roster()

    assert roster[0]["display_name"] == "Alex"
    assert "token" not in roster[0]


def test_the_wire_roster_carries_no_character_snapshot() -> None:
    # Characters stay on the server; embedding every sheet in every roster
    # broadcast would eventually outgrow the protocol's message cap.
    state = new_session()
    alex = state.add_player("Alex")
    state.set_snapshot(alex.player_id, {"power_level": 9})

    entry = state.roster()[0]

    assert "character" not in entry
    assert alex.public_dict()["character"] == {"power_level": 9}  # host-side events keep it


def test_a_non_ascii_token_probe_returns_none_not_an_error() -> None:
    # Tokens arrive off the wire; ``secrets.compare_digest`` raises TypeError on
    # non-ASCII str, so the comparison runs over UTF-8 bytes instead.
    state = new_session()
    state.add_player("Alex")

    assert state.player_by_token("žetón") is None


def test_snapshots_land_on_the_slot_and_unknown_ids_are_refused() -> None:
    state = new_session()
    alex = state.add_player("Alex")

    assert state.set_snapshot(alex.player_id, {"power_level": 12})
    assert not state.set_snapshot("nobody", {"power_level": 12})
    assert state.players[alex.player_id].character == {"power_level": 12}


def test_rolls_are_numbered_in_order_across_players() -> None:
    state = new_session()

    first = state.record_roll(player_id="p1", player_name="Alex", die=11)
    second = state.record_roll(player_id="p2", player_name="Sam", die=3)

    assert [first.seq, second.seq] == [1, 2]
    assert state.next_seq() == 3


def test_removing_a_roll_leaves_a_gap_and_never_reuses_the_seq() -> None:
    state = new_session()
    state.record_roll(player_id="p1", player_name="Alex", die=11)
    second = state.record_roll(player_id="p2", player_name="Sam", die=3)
    state.record_roll(player_id="p1", player_name="Alex", die=7)

    removed = state.remove_roll(second.seq)

    assert removed is second
    assert [roll.seq for roll in state.rolls] == [1, 3]
    assert state.next_seq() == 4  # the gap is not backfilled
    assert state.remove_roll(second.seq) is None  # already gone


def test_a_roll_carries_the_resolved_check() -> None:
    result = resolve_check(4, 15, roll=13)

    record = new_session().record_roll(
        player_id="p1",
        player_name="Alex",
        die=13,
        bonus=6,
        penalty=2,
        result=result,
        label="Notice",
    )

    assert (record.bonus, record.penalty, record.modifier, record.total) == (6, 2, 4, 17)
    assert (record.dc, record.degree, record.label) == (15, 1, "Notice")
    assert not record.critical


def test_a_rolls_spec_survives_the_round_trip_to_disk() -> None:
    """The chain has to outlive a restart: a resumed session still offers the save.

    The record carries the spec opaquely — the store never reads it — so this is
    really a check that it is written and read back untouched.
    """
    state = new_session("Table")
    spec = {"label": "7 vs. Defense", "follow_up": {"label": "Toughness vs. 18", "dc": 18}}
    save_session(state)
    append_roll(state.id, state.record_roll(player_id="p1", player_name="Alex", die=9, spec=spec))

    restored = load_session(state.id)

    assert restored.rolls[0].spec == spec


def test_a_roll_with_no_dc_is_ungraded() -> None:
    record = new_session().record_roll(player_id="p1", player_name="Alex", die=20)

    assert record.dc is None and record.degree is None
    assert record.critical  # a natural 20 is still a natural 20 with nothing to beat


def test_hidden_rolls_are_stored_but_kept_off_the_wire() -> None:
    state = new_session()
    state.record_roll(player_id="gm", player_name="GM", die=4)
    state.record_roll(player_id="gm", player_name="GM", die=18, hidden=True)

    assert len(state.rolls) == 2
    assert [roll.die for roll in state.visible_rolls()] == [4]


def test_the_session_round_trips_through_dicts() -> None:
    state = new_session("Tuesday")
    alex = state.add_player("Alex")
    state.set_snapshot(alex.player_id, {"power_level": 10})
    state.npc_paths.append("thug.json")
    state.record_roll(player_id=alex.player_id, player_name="Alex", die=9, hidden=True)

    restored = SessionState.from_dict(state.to_dict())

    assert restored.to_dict() == state.to_dict()
    assert restored.players[alex.player_id].character == {"power_level": 10}
    assert restored.rolls[0].hidden


def test_a_record_round_trips_on_its_own() -> None:
    record = RollRecord(seq=4, player_id="p1", player_name="Alex", die=7, dc=10, degree=-1)

    assert RollRecord.from_dict(record.to_dict()) == record
    assert PlayerSlot.from_dict(PlayerSlot("p1", "Alex").to_dict()).display_name == "Alex"


# --------------------------------------------------------------------------
# The store
# --------------------------------------------------------------------------


def test_save_and_load_round_trip_the_roster() -> None:
    state = new_session("Tuesday")
    alex = state.add_player("Alex")
    state.set_snapshot(alex.player_id, {"power_level": 11})

    directory = save_session(state)
    restored = load_session(state.id)

    assert directory == session_dir(state.id)
    assert restored.name == "Tuesday"
    assert restored.host_token == state.host_token
    assert restored.players[alex.player_id].character == {"power_level": 11}


def test_the_roll_log_is_appended_not_rewritten() -> None:
    state = new_session()
    save_session(state)

    for die in (4, 17, 9):
        append_roll(state.id, state.record_roll(player_id="p1", player_name="Alex", die=die))

    log = (session_dir(state.id) / ROLLS_FILENAME).read_text(encoding="utf-8")
    assert [json.loads(line)["die"] for line in log.splitlines()] == [4, 17, 9]
    # The session file stays small: the history lives only in the log.
    assert "rolls" not in json.loads((session_dir(state.id) / SESSION_FILENAME).read_text("utf-8"))


def test_loading_stitches_the_log_back_onto_the_session() -> None:
    state = new_session()
    save_session(state)
    for die in (4, 17):
        append_roll(state.id, state.record_roll(player_id="p1", player_name="Alex", die=die))
    append_roll(state.id, state.record_roll(player_id="gm", player_name="GM", die=20, hidden=True))

    restored = load_session(state.id)

    assert [roll.seq for roll in restored.rolls] == [1, 2, 3]
    assert [roll.die for roll in restored.visible_rolls()] == [4, 17]
    assert restored.next_seq() == 4


def test_a_torn_last_line_costs_one_roll_not_the_session() -> None:
    state = new_session()
    save_session(state)
    append_roll(state.id, state.record_roll(player_id="p1", player_name="Alex", die=4))
    # Simulate the app dying mid-write, leaving a half-written line behind.
    with (session_dir(state.id) / ROLLS_FILENAME).open("a", encoding="utf-8") as log:
        log.write('{"seq": 2, "die": 1')

    restored = load_session(state.id)

    assert [roll.die for roll in restored.rolls] == [4]


def test_write_rolls_rewrites_the_whole_log() -> None:
    state = new_session()
    state.record_roll(player_id="p1", player_name="Alex", die=6)
    state.record_roll(player_id="p1", player_name="Alex", die=12)

    save_session(state, write_rolls=True)

    assert [roll.die for roll in load_session(state.id).rolls] == [6, 12]


def test_everyone_starts_disconnected_after_a_restart() -> None:
    state = new_session()
    alex = state.add_player("Alex")
    alex.connected = True
    save_session(state)

    # The flag describes a live socket, which does not survive the process.
    assert not load_session(state.id).players[alex.player_id].connected


def test_list_sessions_summarizes_newest_first() -> None:
    older = new_session("Monday")
    older.updated_at = "2026-07-01T20:00:00+00:00"
    newer = new_session("Tuesday")
    newer.updated_at = "2026-07-08T20:00:00+00:00"
    newer.add_player("Alex")
    save_session(older)
    save_session(newer)
    append_roll(newer.id, newer.record_roll(player_id="p1", player_name="Alex", die=5))

    summaries = list_sessions()

    assert [s.name for s in summaries] == ["Tuesday", "Monday"]
    assert (summaries[0].player_count, summaries[0].roll_count) == (1, 1)
    assert summaries[0].path == session_dir(newer.id)


def test_list_sessions_skips_junk_and_an_empty_workspace() -> None:
    assert list_sessions() == []

    storage.ensure_workspace()
    sessions = storage.get_workspace().sessions_dir
    (sessions / "loose-file.json").write_text("{}", encoding="utf-8")
    (sessions / "broken").mkdir()
    (sessions / "broken" / SESSION_FILENAME).write_text("{not json", encoding="utf-8")
    (sessions / "empty").mkdir()
    save_session(new_session("Real"))

    assert [s.name for s in list_sessions()] == ["Real"]


def test_delete_session_removes_the_directory_and_tolerates_a_missing_one() -> None:
    state = new_session()
    save_session(state)
    append_roll(state.id, state.record_roll(player_id="p1", player_name="Alex", die=5))

    delete_session(state.id)
    delete_session(state.id)  # again: not an error

    assert not session_dir(state.id).exists()


def test_loading_a_session_that_is_not_there_raises() -> None:
    with pytest.raises(SessionStoreError):
        load_session("deadbeef")


def test_loading_a_session_file_that_is_not_an_object_raises() -> None:
    state = new_session()
    directory = save_session(state)
    (directory / SESSION_FILENAME).write_text("[1, 2, 3]\n", encoding="utf-8")

    with pytest.raises(SessionStoreError):
        load_session(state.id)


@pytest.mark.parametrize("bad_id", ["../escape", "a/b", "", "with space", "x" * 65])
def test_a_session_id_can_never_walk_out_of_the_sessions_dir(bad_id: str) -> None:
    # Ids arrive over the network in later phases, so this is a real boundary.
    with pytest.raises(SessionStoreError):
        session_dir(bad_id)

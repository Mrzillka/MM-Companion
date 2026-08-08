"""The player's side of a session: the join dialog and the snapshot pusher.

The pusher runs against a real loopback server — its job is to talk to one — but
its interesting property is what it does *not* send: a burst of edits has to
coalesce into a single snapshot, because a snapshot is the largest message the
protocol carries and a per-keystroke push is what would make relayed traffic
expensive. :func:`test_a_realistic_snapshot_is_small_enough_to_relay` is the
measurement the relay bandwidth estimate rests on.

The other direction is the GM's condition commands: they must land on the live
sheet through the *same* rules resolver the player's own "+" uses — bundling,
supersession and the dirty flag included — and the snapshot they trigger must
bounce back, so the GM's card shows the player's real state rather than what the
GM assumed.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication, QDialog, QLabel, QMessageBox

from mm_companion.core import library, storage
from mm_companion.core.character import AdvantageSelection, Character
from mm_companion.core.data_loader import load_game_data
from mm_companion.core.powers import ModifierSelection, Power, PowerEffectInstance
from mm_companion.core.rules import apply_condition
from mm_companion.core.session import discovery
from mm_companion.core.session.model import new_session
from mm_companion.core.session.protocol import (
    MAX_MESSAGE_BYTES,
    CharacterSnapshot,
    encode,
    sanitize_snapshot,
)
from mm_companion.ui import dice_roller
from mm_companion.ui.blocks.bus import BUILD_CHANGED, EDITED
from mm_companion.ui.character_sheet import CharacterSheet
from mm_companion.ui.dice_roller import DiceRollerView
from mm_companion.ui.session_bridge import SessionBridge, active_session, set_active_session
from mm_companion.ui.session_dialogs import (
    NO_CHARACTER,
    JoinSessionDialog,
    load_session_history,
    record_session_history,
    remove_session_history,
)
from mm_companion.ui.session_player import ConditionReceiver, SnapshotPusher, snapshot_size
from mm_companion.ui.start_window import StartWindow


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
def table(qapp: QApplication):
    """A hosted session on loopback and a player bridge joined to it."""
    host = SessionBridge()
    address = host.host(new_session("Table"), port=0, bind="127.0.0.1")
    code = _code_for(host, address)
    player = SessionBridge()
    player.join(code, "Aria")
    yield host, player
    player.stop()
    host.stop()


def _code_for(host: SessionBridge, address: tuple[str, int]):
    return discovery.decode_join_code(
        discovery.encode_join_code(address[0], address[1], host.state.host_token)
    )


def wait_for(qapp: QApplication, predicate, timeout: float = 5.0) -> bool:
    """Pump the event loop until *predicate* holds (queued signals land here)."""
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.01)
    qapp.processEvents()
    return predicate()


# -- how big a snapshot actually is ----------------------------------------


def built_character() -> Character:
    """A plausible mid-campaign PL 10 sheet — not a blank one, not a monster.

    Full ability spread, a dozen skills, ten advantages, four powers with
    modifiers, a filled-in profile, and a couple of conditions. This is the
    shape the relay cost estimate is built on.
    """
    data = load_game_data()
    character = Character.new_default(data)
    character.profile.update(
        {
            "hero_name": "Nightingale",
            "name": "Wren Alcott",
            "identity": "Secret",
            "occupation": "Field medic",
            "base": "The Roost, dockside",
            "group": "The Long Watch",
            "description": "Lean, grey coat, always the first one into the smoke.",
        }
    )
    ability_ranks = [4, 6, 2, 3, 5, 1, 4, 2]
    for index, ability in enumerate(data.abilities):
        character.abilities[ability.key] = ability_ranks[index % len(ability_ranks)]
    resistance_ranks = [8, 6, 6, 4]
    for index, resistance in enumerate(data.resistances):
        character.resistances[resistance.key] = resistance_ranks[index % len(resistance_ranks)]
    for skill in [s.name for s in data.skills][:12]:
        character.skill_ranks[skill] = 6
    for advantage in [a.name for a in data.advantages][:10]:
        character.advantages.append(AdvantageSelection(name=advantage, rank=2))
    modifiers = [m.id for m in data.modifiers][:2]
    for index, effect in enumerate(list(data.effects)[:4]):
        character.powers.append(
            Power(
                name=f"Signature Power {index + 1}",
                description="A paragraph of flavour text, which is what a real "
                "power carries and what makes a snapshot as big as it is.",
                descriptors=["magic", "light"],
                effects=[
                    PowerEffectInstance(
                        effect_id=effect.id,
                        rank=8,
                        extras=[ModifierSelection(modifier_id=m) for m in modifiers],
                        flaws=[],
                    )
                ],
            )
        )
    apply_condition(character, "dazed", data)
    apply_condition(character, "hindered", data)
    return character


def test_a_realistic_snapshot_is_small_enough_to_relay() -> None:
    size = snapshot_size(built_character())
    print(f"\nbuilt PL 10 character snapshot: {size} bytes")
    # Two independent bounds. The hard one is the protocol's own frame cap: an
    # oversized snapshot cannot be sent at all. The soft one guards the relay cost
    # estimate in GM_SESSION_PLAN.md, which this build measured at ~3 KB (a
    # 12-power PL 12 sheet reaches ~14 KB, a 40-power monster ~76 KB). If a
    # *typical* sheet ever crosses this, the estimate is wrong and sending deltas
    # rather than whole sheets becomes the next lever.
    assert size < MAX_MESSAGE_BYTES
    assert size < 8 * 1024


def test_a_blank_character_is_smaller_still() -> None:
    blank = snapshot_size(Character.new_default(load_game_data()))
    assert blank < snapshot_size(built_character())


def test_a_snapshot_carries_no_image_path() -> None:
    character = Character.new_default(load_game_data())
    character.image_path = "/home/someone/secret/portrait.png"
    line = encode(CharacterSnapshot(character=sanitize_snapshot(character.to_dict())))
    assert b"portrait.png" not in line


# -- the pusher -------------------------------------------------------------


def test_joining_pushes_the_sheet_straight_away(qapp: QApplication, table) -> None:
    host, player = table
    sheet = CharacterSheet(load_game_data())
    sheet.character.profile["hero_name"] = "Nightingale"

    pusher = SnapshotPusher(sheet, player)

    assert pusher.sent == 1
    slot_id = player.client.player_id
    assert wait_for(qapp, lambda: bool(host.state.players[slot_id].character))
    assert host.state.players[slot_id].character["profile"]["hero_name"] == "Nightingale"


def test_a_burst_of_edits_coalesces_into_one_send(qapp: QApplication, table) -> None:
    _host, player = table
    sheet = CharacterSheet(load_game_data())
    pusher = SnapshotPusher(sheet, player, debounce_ms=50)
    assert pusher.sent == 1  # the join snapshot

    for _ in range(20):
        sheet.bus.publish(EDITED)
    assert pusher.pending is True
    assert pusher.sent == 1  # nothing has gone out yet

    assert wait_for(qapp, lambda: pusher.sent > 1)
    assert pusher.sent == 2  # twenty edits, one snapshot


def test_a_runtime_change_pushes_too(qapp: QApplication, table) -> None:
    """Toggling a power on is not an *edit*, but the GM still needs to see it."""
    _host, player = table
    sheet = CharacterSheet(load_game_data())
    pusher = SnapshotPusher(sheet, player, debounce_ms=50)

    sheet.bus.publish(BUILD_CHANGED)
    assert wait_for(qapp, lambda: pusher.sent > 1)


def test_a_real_sheet_edit_reaches_the_gm(qapp: QApplication, table) -> None:
    host, player = table
    sheet = CharacterSheet(load_game_data())
    pusher = SnapshotPusher(sheet, player, debounce_ms=50)
    slot_id = player.client.player_id

    sheet.abilities._abilities["STR"].setValue(7)

    assert wait_for(qapp, lambda: pusher.sent > 1)
    assert wait_for(qapp, lambda: host.state.players[slot_id].character.get("abilities"))
    assert wait_for(qapp, lambda: host.state.players[slot_id].character["abilities"]["STR"] == 7)


def test_a_detached_pusher_sends_nothing_more(qapp: QApplication, table) -> None:
    _host, player = table
    sheet = CharacterSheet(load_game_data())
    pusher = SnapshotPusher(sheet, player, debounce_ms=10)
    sent = pusher.sent

    pusher.detach()
    sheet.bus.publish(EDITED)

    assert pusher.pending is False
    assert pusher.push_now() is False
    qapp.processEvents()
    assert pusher.sent == sent


def test_a_pusher_without_a_connection_is_harmless(qapp: QApplication) -> None:
    sheet = CharacterSheet(load_game_data())
    pusher = SnapshotPusher(sheet, SessionBridge())
    assert pusher.sent == 0
    sheet.bus.publish(EDITED)
    assert pusher.push_now() is False


# -- the join dialog --------------------------------------------------------


def test_the_dialog_refuses_a_mistyped_code(qapp: QApplication) -> None:
    dialog = JoinSessionDialog()
    dialog._name_edit.setText("Aria")
    dialog._code_edit.setText("AAAAA-BBBBB-CCCCC")

    dialog._try_accept()

    assert dialog.result() != int(dialog.DialogCode.Accepted)
    assert dialog._problem.text()


def test_the_dialog_refuses_an_empty_name(qapp: QApplication) -> None:
    dialog = JoinSessionDialog()
    dialog._name_edit.setText("   ")
    dialog._code_edit.setText("whatever")

    dialog._try_accept()

    assert "name" in dialog._problem.text().lower()


def test_a_good_code_is_decoded_and_remembered(qapp: QApplication) -> None:
    code = discovery.encode_join_code("203.0.113.7", 47331, "s3cret-token")
    dialog = JoinSessionDialog()
    dialog._name_edit.setText("Aria")
    dialog._code_edit.setText(code)

    dialog._try_accept()

    assert dialog.join_code().host == "203.0.113.7"
    assert dialog.join_code().token == "s3cret-token"
    assert dialog.display_name() == "Aria"
    settings = storage.load_settings()
    assert settings["session_player_name"] == "Aria"
    assert settings["session_recent_codes"][0] == code


def test_the_dialog_offers_the_saved_characters(qapp: QApplication) -> None:
    character = Character.new_default(load_game_data())
    character.profile["hero_name"] = "Nightingale"
    path = library.save_character(character)

    dialog = JoinSessionDialog()

    assert dialog._character_box.itemText(0) == NO_CHARACTER
    assert dialog.character_path() is None
    labels = [dialog._character_box.itemText(i) for i in range(dialog._character_box.count())]
    assert any("Nightingale" in label for label in labels)
    dialog._character_box.setCurrentIndex(1)
    assert dialog.character_path() == path


def test_the_last_code_is_offered_back(qapp: QApplication) -> None:
    storage.update_settings(session_recent_codes=["AAAAA-BBBBB"], session_player_name="Aria")
    dialog = JoinSessionDialog()
    assert dialog._code_edit.text() == "AAAAA-BBBBB"
    assert dialog._name_edit.text() == "Aria"


# -- the player-side session history ---------------------------------------


def test_recording_and_loading_session_history() -> None:
    record_session_history(
        code="CODE1",
        session_id="s1",
        session_name="Wednesday",
        display_name="Aria",
        player_id="p1",
        player_token="t1",
    )
    (entry,) = load_session_history()
    assert entry["code"] == "CODE1"
    assert entry["session_name"] == "Wednesday"
    assert entry["player_id"] == "p1" and entry["player_token"] == "t1"


def test_rejoining_the_same_code_updates_one_row() -> None:
    record_session_history(code="CODE1", session_name="Old")
    record_session_history(code="CODE1", session_name="New")
    history = load_session_history()
    assert [e["code"] for e in history] == ["CODE1"]
    assert history[0]["session_name"] == "New"


def test_forgetting_a_session_removes_it() -> None:
    record_session_history(code="CODE1", session_name="Wednesday")
    remove_session_history("CODE1")
    assert load_session_history() == []


def test_history_folds_in_legacy_recent_codes() -> None:
    storage.update_settings(session_recent_codes=["LEGACY-CODE"])
    assert any(e["code"] == "LEGACY-CODE" for e in load_session_history())


def test_the_dialog_lists_and_reclaims_a_previous_session(qapp: QApplication) -> None:
    record_session_history(
        code="CODE1",
        session_name="Wednesday",
        display_name="Aria",
        player_id="p1",
        player_token="t1",
    )
    dialog = JoinSessionDialog()

    assert dialog._history_table is not None and dialog._history_table.rowCount() == 1
    dialog._history_table.selectRow(0)
    assert dialog.code_text() == "CODE1"
    assert dialog._name_edit.text() == "Aria"
    assert dialog.reclaim_ids() == ("p1", "t1")


def test_typing_a_different_code_drops_the_reclaimed_seat(qapp: QApplication) -> None:
    record_session_history(code="CODE1", player_id="p1", player_token="t1")
    dialog = JoinSessionDialog()
    dialog._history_table.selectRow(0)

    dialog._code_edit.setText("SOMETHING-ELSE")

    assert dialog.reclaim_ids() == ("", "")


def test_the_prefilled_code_reclaims_without_a_row_click(qapp: QApplication) -> None:
    """The reported bug: rejoining used to need a click nobody knew to make.

    The dialog opens with the newest code already in the box, so the obvious way
    to rejoin is to press Join. That used to arrive as a *first* join, giving the
    player a second card on the GM's board beside their own greyed-out one.
    """
    record_session_history(code="CODE1", player_id="p1", player_token="t1")
    dialog = JoinSessionDialog()

    assert dialog.code_text() == "CODE1"  # prefilled, nothing selected
    assert dialog.reclaim_ids() == ("p1", "t1")


def test_a_hand_typed_known_code_reclaims(qapp: QApplication) -> None:
    """Pasting the code the GM sent is the same return visit as picking the row."""
    record_session_history(code="CODE1", player_id="p1", player_token="t1")
    record_session_history(code="CODE2", player_id="p2", player_token="t2")
    dialog = JoinSessionDialog()

    dialog._code_edit.setText("CODE1")

    assert dialog.reclaim_ids() == ("p1", "t1")


def test_a_forgotten_code_no_longer_reclaims(qapp: QApplication) -> None:
    """Forgetting a session means forgetting the seat, not just hiding the row."""
    record_session_history(code="CODE1", player_id="p1", player_token="t1")
    dialog = JoinSessionDialog()
    dialog._history_table.selectRow(0)

    dialog._forget_selected()
    dialog._code_edit.setText("CODE1")

    assert dialog.reclaim_ids() == ("", "")


def test_forgetting_from_the_dialog_removes_the_row(qapp: QApplication) -> None:
    record_session_history(code="CODE1", session_name="Wednesday")
    dialog = JoinSessionDialog()
    dialog._history_table.selectRow(0)

    dialog._forget_selected()

    assert dialog._history_table.rowCount() == 0
    assert load_session_history() == []


def test_a_returning_player_reclaims_their_slot(qapp: QApplication) -> None:
    host = SessionBridge()
    address = host.host(new_session("Table"), port=0, bind="127.0.0.1")
    code = _code_for(host, address)
    try:
        first = SessionBridge()
        client = first.join(code, "Aria")
        player_id, player_token = client.player_id, client.player_token
        first.stop()

        returning = SessionBridge()
        reclaimed = returning.join(code, "Aria", player_id=player_id, player_token=player_token)
        try:
            assert reclaimed.player_id == player_id  # the same seat, not a new one
        finally:
            returning.stop()
    finally:
        host.stop()


# -- the launcher's join, end to end ---------------------------------------


@pytest.fixture
def joined_launcher(qapp: QApplication, monkeypatch: pytest.MonkeyPatch):
    """A hosted session, plus a launcher wired to join it with a saved character."""
    host = SessionBridge()
    address = host.host(new_session("Table"), port=0, bind="127.0.0.1")
    code = _code_for(host, address)

    character = Character.new_default(load_game_data())
    character.profile["hero_name"] = "Nightingale"
    path = library.save_character(character)

    monkeypatch.setattr(JoinSessionDialog, "exec", lambda self: int(QDialog.DialogCode.Accepted))
    monkeypatch.setattr(JoinSessionDialog, "join_code", lambda self: code)
    monkeypatch.setattr(JoinSessionDialog, "display_name", lambda self: "Aria")
    monkeypatch.setattr(JoinSessionDialog, "character_path", lambda self: path)

    launcher = StartWindow()
    yield host, launcher
    for window in list(launcher._child_windows):
        window.close()
    bridge = active_session()
    if bridge is not None:
        bridge.stop()
        set_active_session(None)
    host.stop()


def test_the_launcher_joins_and_the_gm_gets_the_sheet(qapp: QApplication, joined_launcher) -> None:
    host, launcher = joined_launcher

    launcher._join_session()

    bridge = active_session()
    assert bridge is not None
    player_id = bridge.client.player_id
    assert wait_for(qapp, lambda: bool(host.state.players[player_id].character))
    assert host.state.players[player_id].character["profile"]["hero_name"] == "Nightingale"
    assert host.state.players[player_id].display_name == "Aria"


def test_closing_the_sheet_leaves_the_session(qapp: QApplication, joined_launcher) -> None:
    _host, launcher = joined_launcher
    launcher._join_session()
    window = launcher._child_windows[-1]

    window.close()

    assert active_session() is None


def test_the_app_refuses_to_join_two_sessions_at_once(
    qapp: QApplication, joined_launcher, monkeypatch: pytest.MonkeyPatch
) -> None:
    _host, launcher = joined_launcher
    launcher._join_session()
    first = active_session()

    told: list[str] = []
    monkeypatch.setattr(
        QMessageBox, "information", lambda *args, **kw: told.append(args[2]) or None
    )
    launcher._join_session()

    assert active_session() is first
    assert told


# -- the GM's condition commands -------------------------------------------


def joined_sheet(player: SessionBridge):
    """A live sheet wired to a session: pushing out, and taking the GM's commands."""
    sheet = CharacterSheet(load_game_data())
    pusher = SnapshotPusher(sheet, player, debounce_ms=20)
    receiver = ConditionReceiver(sheet, player)
    return sheet, pusher, receiver


def test_the_gm_can_put_a_condition_on_a_players_sheet(qapp: QApplication, table) -> None:
    host, player = table
    sheet, _pusher, receiver = joined_sheet(player)
    player_id = player.client.player_id

    host.server.apply_condition(player_id, "dazed")

    assert wait_for(qapp, lambda: receiver.applied == 1)
    assert [c.condition_id for c in sheet.character.conditions] == ["dazed"]
    # And it bounces straight back, so the GM's card shows the player's real state.
    assert wait_for(qapp, lambda: host.state.players[player_id].character.get("conditions"))
    stored = host.state.players[player_id].character["conditions"]
    assert [c["id"] for c in stored] == ["dazed"]


def test_a_gm_applied_condition_bundles_like_a_local_one(qapp: QApplication, table) -> None:
    """The command goes through the same resolver, so an umbrella brings its members."""
    host, player = table
    sheet, _pusher, receiver = joined_sheet(player)

    host.server.apply_condition(player.client.player_id, "incapacitated")

    assert wait_for(qapp, lambda: receiver.applied == 1)
    assert len(sheet.character.conditions) == 4


def test_a_gm_applied_condition_marks_the_sheet_dirty(qapp: QApplication, table) -> None:
    host, player = table
    sheet, _pusher, receiver = joined_sheet(player)
    edits: list[int] = []
    sheet.edited.connect(lambda: edits.append(1))

    host.server.apply_condition(player.client.player_id, "prone")

    assert wait_for(qapp, lambda: receiver.applied == 1)
    assert edits


def test_the_gm_can_take_a_condition_off_again(qapp: QApplication, table) -> None:
    host, player = table
    sheet, _pusher, receiver = joined_sheet(player)
    player_id = player.client.player_id
    host.server.apply_condition(player_id, "impaired", "Attack")
    assert wait_for(qapp, lambda: receiver.applied == 1)

    host.server.remove_condition(player_id, "impaired", "Attack")

    assert wait_for(qapp, lambda: receiver.applied == 2)
    assert sheet.character.conditions == []


def test_the_gm_can_set_a_players_hero_points(qapp: QApplication, table) -> None:
    host, player = table
    sheet, _pusher, receiver = joined_sheet(player)
    player_id = player.client.player_id

    host.server.set_hero_points(player_id, 3)

    assert wait_for(qapp, lambda: receiver.applied == 1)
    assert sheet.character.characteristics["hero_points"] == 3
    # And it bounces back, so the GM's card shows the player's real total.
    assert wait_for(
        qapp,
        lambda: (host.state.players[player_id].character.get("characteristics") or {}).get(
            "hero_points"
        )
        == 3,
    )


def test_a_command_naming_somebody_else_is_ignored(qapp: QApplication, table) -> None:
    _host, player = table
    sheet, _pusher, receiver = joined_sheet(player)

    receiver.handle("apply", {"player_id": "someone-else", "condition_id": "dazed"})

    assert receiver.applied == 0
    assert sheet.character.conditions == []


def test_a_detached_receiver_applies_nothing(qapp: QApplication, table) -> None:
    _host, player = table
    sheet, _pusher, receiver = joined_sheet(player)

    receiver.detach()
    receiver.handle("apply", {"player_id": player.client.player_id, "condition_id": "dazed"})

    assert receiver.applied == 0
    assert sheet.character.conditions == []


def test_the_gm_reaches_a_sheet_the_launcher_opened(
    qapp: QApplication, monkeypatch: pytest.MonkeyPatch, joined_launcher
) -> None:
    """The whole path, as a player actually gets it: join, then the GM's "+".

    ``monkeypatch`` is requested *before* the launcher so its patch outlives the
    fixture's teardown: a GM-applied condition dirties the sheet exactly like a
    local one, so closing the window really does ask about unsaved changes.
    """
    monkeypatch.setattr(
        QMessageBox, "question", lambda *args, **kw: QMessageBox.StandardButton.Discard
    )
    host, launcher = joined_launcher
    launcher._join_session()
    bridge = active_session()
    assert bridge is not None
    player_id = bridge.client.player_id
    sheet = launcher._child_windows[-1].sheet

    host.server.apply_condition(player_id, "dazed")

    assert wait_for(qapp, lambda: bool(sheet.character.conditions))
    assert [c.condition_id for c in sheet.character.conditions] == ["dazed"]


# -- rolling from a player's roller -----------------------------------------


def test_a_players_roll_is_resolved_by_the_gm_and_shared(
    qapp: QApplication, monkeypatch: pytest.MonkeyPatch, table
) -> None:
    """The whole round trip: the player asks, the GM's server rolls, both see it."""
    host, player = table
    monkeypatch.setattr(dice_roller, "ROLL_DURATION_MS", 0)
    set_active_session(player)
    view = DiceRollerView()
    view.panel._bonus_spin.setValue(7)
    view.panel._dc_check.setChecked(True)
    view.panel._dc_spin.setValue(12)

    view.panel._start_roll()
    assert wait_for(qapp, lambda: len(host.server.state.rolls) == 1)

    recorded = host.server.state.rolls[0]
    assert recorded.player_name == "Aria"
    assert (recorded.bonus, recorded.dc) == (7, 12)
    # The number on the player's screen is the server's, not one of its own.
    assert wait_for(qapp, lambda: str(recorded.total) in view.panel._readout.text())
    assert wait_for(qapp, lambda: len(view._session_history.cards()) == 1)


def test_a_players_roller_shows_the_gms_rolls_too(qapp: QApplication, table) -> None:
    host, player = table
    set_active_session(player)
    view = DiceRollerView()

    host.server.roll(label="the GM's own", bonus=1)

    assert wait_for(qapp, lambda: len(view._session_history.cards()) == 1)


def test_a_players_roller_never_receives_a_hidden_roll(qapp: QApplication, table) -> None:
    host, player = table
    set_active_session(player)
    view = DiceRollerView()

    host.server.roll(label="behind the screen", hidden=True)
    host.server.roll(label="in the open")

    assert wait_for(qapp, lambda: len(view._session_history.cards()) == 1)
    labels = view._session_history.cards()[0].findChildren(QLabel)
    assert any("in the open" in label.text() for label in labels)

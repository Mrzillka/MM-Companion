"""GUI tests for the NPC sheet (headless / offscreen).

An NPC is an ordinary :class:`~mm_companion.core.character.Character` on the
ordinary sheet, so what is worth testing is only what is *different*: it saves
into the GM's own folder rather than the character library, and the power-point
pool is replaced by a Power Level *estimated from the build's traits* (its
Resistances and best attack). The point of the estimate is that a GM never
budgets an NPC, so the two things that must hold are that the number tracks the
traits and that editing the Power Level no longer drags a budget around behind it.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication, QLabel, QMessageBox

from mm_companion.core import library, storage
from mm_companion.core.data_loader import load_game_data
from mm_companion.core.rules import estimated_power_level
from mm_companion.ui.blocks import npc_hidden_keys
from mm_companion.ui.main_window import MainWindow
from mm_companion.ui.npc_window import NPCWindow


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv(storage.HOME_ENV_VAR, str(tmp_path))
    storage.ensure_workspace()
    return tmp_path


@pytest.fixture(autouse=True)
def _discard_unsaved(monkeypatch: pytest.MonkeyPatch) -> None:
    """Close without the "save your changes?" modal — nothing here can answer it."""
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Discard)


@pytest.fixture
def npc(qapp: QApplication) -> NPCWindow:
    window = NPCWindow()
    yield window
    window.close()


def row_label(window: MainWindow) -> str:
    """The caption of the sheet's power-point row — the one NPC mode renames."""
    section = window.sheet.system_info
    label = section._form.labelForField(section._points_row)
    return label.text() if isinstance(label, QLabel) else ""


def test_an_npc_saves_into_the_gm_folder_not_the_library(npc: NPCWindow) -> None:
    workspace = storage.get_workspace()
    assert npc.storage_dir() == workspace.gm_characters_dir

    npc.sheet.character.profile["hero_name"] = "Thug"
    saved = library.save_character(npc.sheet.character, directory=npc.storage_dir())

    assert saved.parent == workspace.gm_characters_dir
    assert [s.name for s in library.list_saved_characters(workspace.gm_characters_dir)] == ["Thug"]
    assert library.list_saved_characters() == []  # the launcher's library is untouched


def test_the_point_pool_is_replaced_by_an_estimated_power_level(npc: NPCWindow) -> None:
    section = npc.sheet.system_info
    assert row_label(npc) == "Estimated PL:"
    assert section._estimated_pl.isVisibleTo(npc)
    assert not section._power_points.isVisibleTo(npc)
    assert not section._pool_current.isVisibleTo(npc)


def test_an_ordinary_character_sheet_still_has_its_pool(qapp: QApplication) -> None:
    window = MainWindow(locked=False)
    section = window.sheet.system_info
    assert row_label(window) == "Power Points:"
    assert section._power_points.isVisibleTo(window)
    assert not section._estimated_pl.isVisibleTo(window)
    window.close()


def test_the_estimate_follows_the_traits_not_the_points(npc: NPCWindow) -> None:
    data = load_game_data()
    before = npc.sheet.system_info._estimated_pl.text()

    character = npc.sheet.character
    # Raise a paired-cap resistance so the estimate must move (Dodge + Toughness).
    character.resistances["TOUGHNESS"] = 8
    character.resistances["DODGE"] = 6
    npc.sheet._recompute_derived()

    expected = estimated_power_level(character, data)
    text = npc.sheet.system_info._estimated_pl.text()
    assert text != before
    assert text == str(expected)
    assert expected == 7  # ceil((6 + 8) / 2)


def test_npc_priced_blocks_drop_the_pp_subtotal(npc: NPCWindow) -> None:
    # An NPC has no budget, so the priced blocks show a plain caption, not "— N PP".
    for key in ("abilities", "resistances", "advantages", "skills", "powers"):
        title = getattr(npc.sheet, key).block_title()
        assert "PP" not in title, f"{key} block still shows a PP subtotal: {title!r}"


def test_a_player_sheet_keeps_the_pp_subtotal(qapp: QApplication) -> None:
    window = MainWindow(locked=False)
    assert "PP" in window.sheet.abilities.block_title()
    window.close()


def test_an_npcs_power_level_does_not_drag_a_budget_behind_it(npc: NPCWindow) -> None:
    section = npc.sheet.system_info
    budget = npc.sheet.character.power_points_total

    section._power_level.setValue(section._power_level.value() + 3)

    # A player character snaps its point budget to the new level's band; an NPC has
    # no budget to snap, so the field is left exactly where it was.
    assert npc.sheet.character.power_points_total == budget
    assert npc.sheet.character.power_level == section._power_level.value()


def test_a_player_character_still_links_its_level_and_budget(qapp: QApplication) -> None:
    window = MainWindow(locked=False)
    section = window.sheet.system_info
    budget = window.sheet.character.power_points_total

    section._power_level.setValue(section._power_level.value() + 3)

    assert window.sheet.character.power_points_total > budget
    window.close()


def test_the_window_says_it_is_an_npc(npc: NPCWindow) -> None:
    assert npc.windowTitle().startswith("NPC — ")


def test_an_npc_opens_unlocked_so_the_gm_can_write_it(npc: NPCWindow) -> None:
    assert not npc._lock_action.isChecked()


def test_opening_from_an_npc_window_opens_another_npc(npc: NPCWindow, tmp_path: Path) -> None:
    npc.sheet.character.profile["hero_name"] = "Goon"
    path = library.save_character(npc.sheet.character, directory=npc.storage_dir())

    child = npc._new_child(library.load_character(path), path)

    assert isinstance(child, NPCWindow)
    assert child.storage_dir() == npc.storage_dir()
    assert row_label(child) == "Estimated PL:"
    child.close()


def test_a_saved_npc_remembers_its_file(npc: NPCWindow) -> None:
    assert npc.path is None
    npc.sheet.character.profile["hero_name"] = "Minion"
    npc._write(npc.storage_dir() / "minion.json")
    assert npc.path is not None and npc.path.name == "minion.json"


# -- the arrangement an NPC opens with, and where it is remembered ---------------


def test_an_npc_sheet_does_not_build_the_blocks_that_hold_no_trait(npc: NPCWindow) -> None:
    """A GM opening a thug wants its numbers, not a second roller and the Scene board.

    Not closed — absent. Building four blocks only to hide them was a fifth of the
    cost of opening a mook, and the View menu has nothing to offer for them either.
    """
    missing = npc_hidden_keys()
    assert {"dice", "scene", "notes", "complications"} <= set(missing)
    for key in missing:
        assert key not in npc.sheet.block_keys(), key
        assert npc.sheet.section(key) is None, key
    assert not set(missing) & set(npc._block_actions)
    # Notes is the one block a second copy can be made of; not here.
    assert not npc.sheet.multi_templates()
    # ...and every block that *does* hold a trait is still there, and open.
    assert not npc.sheet.is_block_hidden("abilities")
    assert not npc.sheet.is_block_hidden("powers")


def test_a_player_sheet_is_untouched_by_that(qapp: QApplication) -> None:
    window = MainWindow()
    try:
        assert set(npc_hidden_keys()) <= set(window.sheet.block_keys())
        assert not window.sheet.is_block_hidden("dice")
    finally:
        window.close()


def test_an_npc_arrangement_is_remembered_apart_from_the_character_sheets(
    npc: NPCWindow, qapp: QApplication
) -> None:
    """Sharing ``layout`` would mean a mook's missing blocks went missing on a hero."""
    assert NPCWindow.LAYOUT_KEY != MainWindow.LAYOUT_KEY
    npc.sheet.hide_block("equipment")
    npc.close()

    assert storage.load_settings().get(NPCWindow.LAYOUT_KEY, {}).get("dock_state")
    assert not storage.load_settings().get(MainWindow.LAYOUT_KEY, {}).get("dock_state")

    reopened = NPCWindow()
    try:
        # Reopened it is the GM's own arrangement, not the default one, that comes back.
        assert reopened.sheet.is_block_hidden("equipment")
    finally:
        reopened.close()


def test_an_arrangement_saved_while_those_blocks_were_built_still_restores(
    qapp: QApplication,
) -> None:
    """The layout a GM had before the NPC sheet stopped building its closed blocks.

    The arrangement validator wants exactly the live blocks, so a layout naming four
    the sheet no longer has would be thrown away whole — and the GM's arrangement
    with it. Those four were closed, so they come out of ``hidden`` and nothing that
    was on screen moves. One the GM had docked comes out of the page the same way.
    """
    player = MainWindow()
    try:
        player.sheet.hide_block("equipment")
        for key in ("scene", "notes", "complications"):
            player.sheet.hide_block(key)
        # The roller stays docked on the page, as a GM who reopened it would have it.
        assert not player.sheet.is_block_hidden("dice")
        old = player.sheet.save_layout()
    finally:
        player.close()
    storage.set_sheet_layout(NPCWindow.LAYOUT_KEY, "", old)

    reopened = NPCWindow()
    try:
        assert reopened.sheet.is_block_hidden("equipment")
        assert not reopened.sheet.is_block_hidden("abilities")
        assert "dice" not in reopened.sheet.block_keys()
    finally:
        reopened.close()


def test_rolling_on_an_npc_sheet_goes_out_to_whoever_opened_it(npc: NPCWindow) -> None:
    """With no roller of its own, a roll must not vanish into a void.

    It leaves through ``requestUnserved`` — the GM window connects that to its own
    roller (tests/test_gm_window.py). A player's sheet, which has a roller, sends
    nothing out.
    """
    from mm_companion.core.rules import ability_roll

    sent: list[tuple[str, object]] = []
    npc.requestUnserved.connect(lambda topic, payload: sent.append((topic, payload)))
    spec = ability_roll(npc.sheet.character, load_game_data(), "AGL")
    npc.sheet.bus.publish_request("roll-requested", spec)
    assert sent == [("roll-requested", spec)]

    player = MainWindow()
    try:
        player.requestUnserved.connect(lambda topic, payload: sent.append((topic, payload)))
        player.sheet.bus.publish_request("roll-requested", spec)
        assert len(sent) == 1
    finally:
        player.close()


def test_loading_a_character_into_a_built_sheet_matches_building_one_for_it(
    qapp: QApplication,
) -> None:
    """The spare sheet a GM window keeps: built blank, then handed a creature.

    Every visible value on every block has to come out the same as a sheet built for
    that creature in the first place — a block that did not restate itself would show
    the blank one's numbers under the creature's name.
    """
    from collections import Counter

    from PySide6.QtCore import QCoreApplication, QEvent
    from PySide6.QtWidgets import QAbstractButton, QComboBox, QLineEdit, QSpinBox

    from mm_companion.core.character import AdvantageSelection, AppliedCondition
    from mm_companion.core.npc import preset_stats, quick_npc
    from mm_companion.core.powers import Power, PowerEffectInstance

    data = load_game_data()
    stats = preset_stats(data, "brute", 11)
    creature = quick_npc(data, name="Dr Volt", power_level=11, **vars(stats))
    creature.abilities["STR"] = 7
    creature.skill_ranks["Stealth"] = 5
    creature.advantages.append(AdvantageSelection("Close Attack", 2))
    creature.conditions.append(AppliedCondition("dazed"))
    creature.powers.append(
        Power(name="Fire Blast", effects=[PowerEffectInstance(effect_id="damage", rank=8)])
    )

    def readout(window: NPCWindow) -> Counter:
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        seen = []
        for key in window.sheet.block_keys():
            section = window.sheet.section(key)
            for kind in (QSpinBox, QLineEdit, QComboBox, QAbstractButton, QLabel):
                for widget in section.findChildren(kind):
                    if not widget.isVisibleTo(section):
                        continue
                    if isinstance(widget, QSpinBox):
                        value = widget.value()
                    elif isinstance(widget, QComboBox):
                        value = widget.currentText()
                    elif isinstance(widget, QAbstractButton):
                        value = (widget.text(), widget.isChecked())
                    else:
                        value = widget.text()
                    seen.append((key, kind.__name__, value))
        return Counter(seen)

    path = storage.get_workspace().gm_characters_dir / "volt.json"
    built = NPCWindow(character=creature, path=path, pin_target=True)
    spare = NPCWindow(pin_target=True)
    try:
        spare.sheet.character.abilities["STR"] = 3  # a blank sheet someone had touched
        spare.load(creature, path)
        assert readout(spare) == readout(built)
        assert spare.sheet.character.to_dict() == built.sheet.character.to_dict()
        assert spare.path == path
        assert spare.windowTitle() == built.windowTitle()
        assert not spare._dirty
        assert not spare._router.can_undo
    finally:
        built.close()
        spare.close()


def test_a_sheet_with_no_roller_shows_no_shrink_button(npc: NPCWindow, qapp: QApplication) -> None:
    """The compact button floats over a roller; with none it must not float anywhere.

    It is a child of the window and only ever hid when moved *off* a roller, so on a
    sheet that never had one it showed with the window — over the File menu.
    """
    npc.show()
    qapp.processEvents()
    assert not npc._compact.button.isVisible()


def test_an_old_layout_with_extra_notes_blocks_still_restores(qapp: QApplication) -> None:
    """``notes#2`` is an instance of a block the NPC sheet leaves out — it goes too."""
    player = MainWindow()
    try:
        player.sheet.add_block_instance("notes")
        assert "notes#2" in player.sheet.block_keys()
        player.sheet.hide_block("equipment")
        old = player.sheet.save_layout()
    finally:
        player.close()
    storage.set_sheet_layout(NPCWindow.LAYOUT_KEY, "", old)

    reopened = NPCWindow()
    try:
        assert reopened.sheet.is_block_hidden("equipment")
        assert not any(key.startswith("notes") for key in reopened.sheet.block_keys())
    finally:
        reopened.close()


def test_an_npc_sheet_can_take_another_creature_in_place(npc: NPCWindow) -> None:
    """Every block it builds can restate itself — the spare sheet depends on it."""
    assert npc.can_load()


def test_a_block_with_no_way_to_restate_itself_says_so(npc: NPCWindow) -> None:
    from dataclasses import replace

    descriptor = npc.sheet._descriptor_by_key["equipment"]
    # Equipment is restated by topics; take them away and it has no route left.
    npc.sheet._descriptors = [
        replace(d, subscribes={}) if d.key == "equipment" else d for d in npc.sheet._descriptors
    ]
    assert descriptor is not None
    assert not npc.can_load()

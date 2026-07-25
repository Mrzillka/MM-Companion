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

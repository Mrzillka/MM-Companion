"""Advantages spent at the table — Luck, Determination, Edit Scene — and their dots."""

from __future__ import annotations

import pytest
from PySide6.QtCore import QEvent
from PySide6.QtWidgets import QApplication

from mm_companion.core.character import AdvantageSelection, Character
from mm_companion.core.data_loader import load_game_data
from mm_companion.core.rules import (
    advantage_uses,
    advantage_uses_left,
    reset_advantage_uses,
    skill_allows_untrained,
    skill_for_row,
    skill_usable,
)
from mm_companion.ui.advantage_uses import UsePips, advantage_use_note
from mm_companion.ui.character_sheet import CharacterSheet
from mm_companion.ui.sections.advantages import COL_USES


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


@pytest.fixture(scope="module")
def data():
    return load_game_data()


def _settle(qapp: QApplication, sheet=None) -> None:
    for _ in range(3):
        qapp.processEvents()
    if sheet is not None:
        sheet.bus.flush()
        qapp.processEvents()
    # A rebuilt table's old cell widgets are only scheduled for deletion.
    qapp.sendPostedEvents(None, QEvent.Type.DeferredDelete)


# -- the rules ---------------------------------------------------------------------


def test_a_per_adventure_advantage_has_one_use_per_rank(data) -> None:
    assert advantage_uses(data, AdvantageSelection("Luck", 3)) == 3
    assert advantage_uses(data, AdvantageSelection("Determination", 1)) == 1
    assert advantage_uses(data, AdvantageSelection("Edit Scene", 2)) == 2
    # A standing advantage is not a resource at all.
    assert advantage_uses(data, AdvantageSelection("Evasion", 2)) == 0


def test_uses_left_never_goes_below_zero(data) -> None:
    luck = AdvantageSelection("Luck", 2, used=1)
    assert advantage_uses_left(data, luck) == 1
    # A rank sold back after a use was spent.
    luck.rank = 1
    luck.used = 2
    assert advantage_uses_left(data, luck) == 0


def test_a_new_adventure_gives_every_use_back() -> None:
    char = Character()
    char.advantages = [AdvantageSelection("Luck", 2, used=2), AdvantageSelection("Evasion")]
    assert reset_advantage_uses(char) is True
    assert [a.used for a in char.advantages] == [0, 0]
    assert reset_advantage_uses(char) is False


def test_spent_uses_are_saved_and_only_while_there_are_any() -> None:
    char = Character()
    char.advantages = [AdvantageSelection("Luck", 2, used=1), AdvantageSelection("Evasion")]
    raw = char.to_dict()
    assert raw["advantages"][0]["used"] == 1
    assert "used" not in raw["advantages"][1]
    assert Character.from_dict(raw).advantages[0].used == 1


def test_the_note_names_the_advantage_and_what_is_left() -> None:
    assert advantage_use_note("Luck", 1, 2, spent=True) == "used Luck — 1 of 2 left"
    assert advantage_use_note("Luck", 2, 2, spent=False).startswith("regained a use of Luck")


# -- the edit sheet ------------------------------------------------------------------


def _sheet(qapp, data, *advantages: AdvantageSelection) -> CharacterSheet:
    char = Character.new_default(data)
    char.advantages.extend(advantages)
    sheet = CharacterSheet(data, char)
    sheet.set_locked(True)
    sheet.show()
    _settle(qapp, sheet)
    return sheet


def _pips(sheet: CharacterSheet) -> list[UsePips]:
    return sheet.advantages.findChildren(UsePips)


def test_the_uses_column_shows_only_while_an_advantage_has_uses(qapp, data) -> None:
    sheet = _sheet(qapp, data, AdvantageSelection("Evasion"))
    assert all(table.isColumnHidden(COL_USES) for table in sheet.advantages._tables)
    assert _pips(sheet) == []

    sheet = _sheet(qapp, data, AdvantageSelection("Luck", 2))
    assert not sheet.advantages._tables[0].isColumnHidden(COL_USES)
    [pips] = _pips(sheet)
    assert (pips.total(), pips.left()) == (2, 2)


def test_a_dot_spends_a_use_and_writes_it_in_the_history(qapp, data) -> None:
    sheet = _sheet(qapp, data, AdvantageSelection("Luck", 2))
    notes: list[str] = []
    sheet.advantages.noteRequested.connect(notes.append)
    edited: list[bool] = []
    sheet.advantages.edited.connect(lambda: edited.append(True))

    [pips] = _pips(sheet)
    pips._buttons[0].click()  # a lit dot (the sheet is locked: still live)

    assert sheet.character.advantages[0].used == 1
    assert notes == ["used Luck — 1 of 2 left"]
    assert edited == [True]
    assert pips.left() == 1


def test_a_hollow_dot_gives_the_use_back(qapp, data) -> None:
    sheet = _sheet(qapp, data, AdvantageSelection("Luck", 2, used=2))
    notes: list[str] = []
    sheet.advantages.noteRequested.connect(notes.append)
    [pips] = _pips(sheet)
    assert pips.left() == 0

    pips._buttons[1].click()

    assert sheet.character.advantages[0].used == 1
    assert notes == ["regained a use of Luck — 1 of 2 left"]


def test_the_note_reaches_the_dice_blocks_history(qapp, data) -> None:
    sheet = _sheet(qapp, data, AdvantageSelection("Determination", 1))
    [pips] = _pips(sheet)
    pips._buttons[0].click()
    _settle(qapp, sheet)
    from PySide6.QtWidgets import QLabel

    labels = [label.text() for label in sheet.dice.view.findChildren(QLabel)]
    assert "used Determination — 0 of 1 left" in labels


def test_resetting_restores_every_use(qapp, data) -> None:
    sheet = _sheet(
        qapp,
        data,
        AdvantageSelection("Luck", 2, used=1),
        AdvantageSelection("Edit Scene", 1, used=1),
    )
    notes: list[str] = []
    sheet.advantages.noteRequested.connect(notes.append)

    sheet.advantages.reset_advantage_uses()

    assert [a.used for a in sheet.character.advantages] == [0, 0]
    assert len(notes) == 1 and "Luck" in notes[0] and "Edit Scene" in notes[0]
    assert all(p.left() == p.total() for p in _pips(sheet))


def test_spending_a_use_is_undoable(qapp, data) -> None:
    from mm_companion.ui.main_window import MainWindow

    char = Character.new_default(data)
    char.advantages.append(AdvantageSelection("Luck", 1))
    win = MainWindow(character=char, locked=True)
    win.show()
    _settle(qapp, win.sheet)
    sheet = win.sheet
    [pips] = _pips(sheet)
    pips._buttons[0].click()
    _settle(qapp, sheet)
    sheet.undo.flush()
    assert sheet.character.advantages[0].used == 1

    win._router.undo()
    _settle(qapp, sheet)
    assert sheet.character.advantages[0].used == 0
    [pips] = _pips(sheet)
    assert pips.left() == 1


# -- the simple sheet -----------------------------------------------------------------


def test_the_simple_sheet_draws_the_same_dots_and_spends_through_the_block(qapp, data) -> None:
    sheet = _sheet(qapp, data, AdvantageSelection("Luck", 2))
    sheet.set_simple(True)
    _settle(qapp, sheet)
    view = sheet.simple_sheet.view("advantages")
    [pips] = view.findChildren(UsePips)
    notes: list[str] = []
    sheet.advantages.noteRequested.connect(notes.append)

    pips._buttons[0].click()
    _settle(qapp, sheet)

    assert sheet.character.advantages[0].used == 1
    assert notes == ["used Luck — 1 of 2 left"]
    # And the edit sheet's dots follow.
    [edit_pips] = _pips(sheet)
    assert edit_pips.left() == 1


# -- Jack-of-All-Trades and the trained-only skills ------------------------------------


def test_jack_of_all_trades_opens_trained_only_skills_but_not_its_exceptions(data) -> None:
    char = Character.new_default(data)
    technology = skill_for_row(data, "Technology")
    expertise = skill_for_row(data, "Expertise")
    assert not skill_allows_untrained(char, data, technology)
    assert not skill_usable(char, data, "Technology")

    char.advantages.append(AdvantageSelection("Jack-of-All-Trades"))

    assert skill_allows_untrained(char, data, technology)
    assert skill_usable(char, data, "Technology")
    assert not skill_allows_untrained(char, data, expertise)


def test_ranks_make_a_trained_only_skill_usable(data) -> None:
    char = Character.new_default(data)
    char.skill_ranks["Technology"] = 2
    assert skill_usable(char, data, "Technology")
    # A skill with no trainedOnly flag is always usable.
    assert skill_usable(char, data, "Athletics")

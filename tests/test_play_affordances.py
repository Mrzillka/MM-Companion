"""Small play-time affordances: what says it rolls, and what a skill needs to be used."""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QApplication, QGridLayout

from mm_companion.core.character import AdvantageSelection, Character
from mm_companion.core.data_loader import load_game_data
from mm_companion.ui.character_sheet import CharacterSheet
from mm_companion.ui.roll_click import RollChip
from mm_companion.ui.sections.dice import DiceSection
from mm_companion.ui.sections.skills import (
    COL_TOTAL,
    COL_UNTRAINED,
    UNTRAINED_NO,
    UNTRAINED_YES,
    SkillsSection,
)
from mm_companion.ui.sections.stat_table import ROLL_ROLE


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


@pytest.fixture(scope="module")
def data():
    return load_game_data()


def _untrained_cell(section: SkillsSection, name: str) -> str:
    for table in section._tables:
        for row in range(table.rowCount()):
            total = table.item(row, COL_TOTAL)
            payload = None if total is None else total.data(ROLL_ROLE)
            if payload and payload[0] == name:
                return table.item(row, COL_UNTRAINED).text()
    raise AssertionError(f"no row for {name}")


# -- the skills' Untrained? column ------------------------------------------------------


def test_the_untrained_column_shows_while_editing_and_says_which_skills_need_ranks(
    qapp, data
) -> None:
    section = SkillsSection(data, Character.new_default(data))
    section.set_locked(False)
    assert not any(table.isColumnHidden(COL_UNTRAINED) for table in section._tables)
    assert _untrained_cell(section, "Athletics") == UNTRAINED_YES
    assert _untrained_cell(section, "Technology") == UNTRAINED_NO


def test_the_locked_sheet_hides_the_column_and_mutes_what_cannot_be_used(qapp, data) -> None:
    char = Character.new_default(data)
    char.skill_ranks["Treatment"] = 2
    section = SkillsSection(data, char)
    section.set_locked(True)
    assert all(table.isColumnHidden(COL_UNTRAINED) for table in section._tables)

    def colour(name: str):
        for row in section._rows:
            if row.row_id == name:
                return row.total_item.foreground().color().name()
        raise AssertionError(name)

    muted = colour("Technology")  # trained only, no ranks
    assert muted != colour("Athletics")  # usable untrained
    assert muted != colour("Treatment")  # trained only, but trained


def test_jack_of_all_trades_ticks_the_trained_only_skills(qapp, data) -> None:
    char = Character.new_default(data)
    char.advantages.append(AdvantageSelection("Jack-of-All-Trades"))
    section = SkillsSection(data, char)
    section.set_locked(False)
    assert _untrained_cell(section, "Technology") == UNTRAINED_YES


# -- Initiative looks like the roll it is -------------------------------------------------


def test_initiative_is_a_roll_chip_on_the_edit_sheet(qapp, data) -> None:
    sheet = CharacterSheet(data, Character.new_default(data))
    chip = sheet.system_info._initiative_chip
    assert isinstance(chip, RollChip)
    assert chip.content is sheet.system_info._initiative
    # The tooltip lives on the chip, since its label is transparent to the mouse.
    assert "double-click" in chip.toolTip()


def test_initiative_is_dressed_as_a_roll_on_the_simple_sheet(qapp, data) -> None:
    sheet = CharacterSheet(data, Character.new_default(data))
    sheet.show()
    sheet.set_simple(True)
    qapp.processEvents()
    box = sheet.simple_sheet.view("system_info").initiative
    assert box.property("rollChip") is True
    assert box.caption() == "Init"  # the border says it rolls; no die beside it
    # Power Level, beside it, is not a roll and is not dressed as one.
    assert not sheet.simple_sheet.view("system_info").level.property("rollChip")


# -- the roller's Request row sits on the grid -------------------------------------------


def test_the_request_row_lines_up_with_the_sliders_and_spin_boxes(qapp, data) -> None:
    section = DiceSection(data, Character.new_default(data))
    panel = section.panel
    grid = panel._settings_part.layout()
    assert isinstance(grid, QGridLayout)

    def cell(widget) -> tuple[int, int]:
        row, column, _rows, columns = grid.getItemPosition(grid.indexOf(widget))
        return column, columns

    # The trait under the sliders, the difficulty and its button under the spin boxes.
    assert cell(panel._request_combo) == cell(panel._bonus_slider)
    assert cell(panel._request_part) == cell(panel._bonus_spin)

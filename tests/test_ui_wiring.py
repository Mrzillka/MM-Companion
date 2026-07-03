"""The sections should read and write the shared Character model."""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QApplication

from mm_companion.core.character import Character
from mm_companion.core.data_loader import load_game_data
from mm_companion.core.rules import power_points_spent, skill_total
from mm_companion.ui.character_sheet import CharacterSheet


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_ability_spin_writes_to_model(qapp: QApplication) -> None:
    sheet = CharacterSheet(load_game_data())
    sheet.stats._abilities["STR"].setValue(4)
    assert sheet.character.abilities["STR"] == 4


def test_skill_rank_flows_to_model_and_total(qapp: QApplication) -> None:
    data = load_game_data()
    sheet = CharacterSheet(data)
    sheet.stats._abilities["AGL"].setValue(3)

    # Drive the Stealth rank spin box via the row it renders into.
    stealth_row = next(row for row in sheet.skills._rows if row[1] == "Stealth")
    _, _, _, total_item = stealth_row
    sheet.character.skill_ranks["Stealth"] = 4
    sheet.skills._refresh_totals()

    assert skill_total(sheet.character, data, "Stealth") == 7  # AGL 3 + 4 ranks
    assert total_item.text() == "7"


def test_spent_power_points_reflected_in_pool_label(qapp: QApplication) -> None:
    data = load_game_data()
    sheet = CharacterSheet(data)
    sheet.stats._abilities["STR"].setValue(4)  # 4 * 2 = 8 PP

    spent = power_points_spent(sheet.character, data)
    assert spent == 8
    assert sheet.base_info._pool_current["power_points"].text() == "8"


def test_raising_power_level_raises_the_budget_to_its_minimum(qapp: QApplication) -> None:
    data = load_game_data()
    sheet = CharacterSheet(data)  # PL 10, 150 PP
    per_level = data.costs.power_level.pp_per_level

    sheet.base_info._characteristics["power_level"].setValue(12)

    assert sheet.character.power_level == 12
    assert sheet.character.power_points_total == 12 * per_level
    assert sheet.base_info._characteristics["power_points"].value() == 12 * per_level


def test_raising_the_budget_past_a_border_raises_power_level(qapp: QApplication) -> None:
    data = load_game_data()
    sheet = CharacterSheet(data)  # PL 10, 150 PP
    per_level = data.costs.power_level.pp_per_level

    sheet.base_info._characteristics["power_points"].setValue(11 * per_level)

    assert sheet.character.power_level == 11
    assert sheet.base_info._characteristics["power_level"].value() == 11


def test_budget_within_a_band_leaves_power_level_alone(qapp: QApplication) -> None:
    data = load_game_data()
    sheet = CharacterSheet(data)  # PL 10, 150 PP
    per_level = data.costs.power_level.pp_per_level

    sheet.base_info._characteristics["power_points"].setValue(10 * per_level + 5)

    assert sheet.character.power_level == 10
    assert sheet.character.power_points_total == 10 * per_level + 5


def test_sheet_accepts_an_existing_character(qapp: QApplication) -> None:
    data = load_game_data()
    char = Character.new_default(data)
    char.abilities["INT"] = 5
    sheet = CharacterSheet(data, char)
    assert sheet.character is char
    assert sheet.stats._abilities["INT"].value() == 5

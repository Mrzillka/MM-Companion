"""The advantages block's ordering, reorder, and panel-mapping behaviour."""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QApplication

from mm_companion.core.character import AdvantageSelection, Character
from mm_companion.core.data_loader import load_game_data
from mm_companion.ui.sections.advantages import (
    SORT_MANUAL,
    SORT_NAME,
    SORT_RANK,
    SORT_TYPE,
    AdvantagesSection,
)


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


def _section(selections: list[AdvantageSelection]) -> AdvantagesSection:
    data = load_game_data()
    char = Character.new_default(data)
    char.advantages = list(selections)
    return AdvantagesSection(data, char)


def _names(section: AdvantagesSection) -> list[str]:
    return [s.name for s in section._character.advantages]


def test_name_sort_reorders_the_model(qapp: QApplication) -> None:
    section = _section(
        [
            AdvantageSelection("Assessment", 1),
            AdvantageSelection("Agile Grab", 1),
            AdvantageSelection("Animal Empathy", 1),
        ]
    )
    section._sort_combo.setCurrentIndex(section._sort_combo.findData(SORT_NAME))

    # A preset permanently rewrites the saved order (so it persists on save).
    assert _names(section) == ["Agile Grab", "Animal Empathy", "Assessment"]


def test_rank_sort_is_high_to_low(qapp: QApplication) -> None:
    section = _section(
        [
            AdvantageSelection("Assessment", 2),
            AdvantageSelection("Benefit", 5),
            AdvantageSelection("Close Attack", 3),
        ]
    )
    section._sort_combo.setCurrentIndex(section._sort_combo.findData(SORT_RANK))

    assert [s.rank for s in section._character.advantages] == [5, 3, 2]


def test_type_sort_groups_by_type(qapp: QApplication) -> None:
    section = _section(
        [
            AdvantageSelection("Assessment", 1),  # General
            AdvantageSelection("Agile Grab", 1),  # Combat
            AdvantageSelection("Animal Empathy", 1),  # Skill
        ]
    )
    section._sort_combo.setCurrentIndex(section._sort_combo.findData(SORT_TYPE))

    # Combat < General < Skill alphabetically.
    assert _names(section) == ["Agile Grab", "Assessment", "Animal Empathy"]


def test_manual_move_mutates_the_model(qapp: QApplication) -> None:
    section = _section(
        [
            AdvantageSelection("Assessment", 1),
            AdvantageSelection("Agile Grab", 1),
            AdvantageSelection("Animal Empathy", 1),
        ]
    )
    assert section._sort_mode == SORT_MANUAL

    section._selected = section._character.advantages[2]  # Animal Empathy
    section._move_selected(-1)  # move it earlier

    assert [s.name for s in section._character.advantages] == [
        "Assessment",
        "Animal Empathy",
        "Agile Grab",
    ]


def test_move_at_the_edge_is_a_no_op(qapp: QApplication) -> None:
    section = _section([AdvantageSelection("Assessment", 1), AdvantageSelection("Benefit", 1)])
    section._selected = section._character.advantages[0]
    section._move_selected(-1)  # already first

    assert [s.name for s in section._character.advantages] == ["Assessment", "Benefit"]


def test_row_refs_map_every_advantage(qapp: QApplication) -> None:
    selections = [AdvantageSelection("Assessment", i + 1) for i in range(3)]
    section = _section(selections)

    # One row reference per advantage, each pointing at a real model object.
    assert len(section._row_refs) == 3
    referenced = {id(sel) for _, _, sel in section._row_refs}
    assert referenced == {id(s) for s in section._character.advantages}

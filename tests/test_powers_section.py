"""The Powers section's drag-to-group tree mutations.

Real drag-and-drop events are unreliable headless, so these drive the public mutation
seams the drop handlers delegate to (``_on_combine`` / ``_on_move`` / ``_ungroup``)
and assert on the resulting ``Character.powers`` tree.
"""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QApplication

from mm_companion.core.character import Character
from mm_companion.core.data_loader import load_game_data
from mm_companion.core.powers import (
    STRUCTURE_ARRAY,
    STRUCTURE_LINKED,
    Power,
    PowerEffectInstance,
    PowerGroup,
)
from mm_companion.ui.character_sheet import CharacterSheet


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


def _sheet_with(*names: str) -> tuple[CharacterSheet, Character]:
    data = load_game_data()
    char = Character.new_default(data)
    for name in names:
        char.powers.append(Power(name=name, effects=[PowerEffectInstance("damage", rank=4)]))
    return CharacterSheet(data, char), char


def _names(nodes: list[object]) -> list[object]:
    """A nested list of names mirroring the tree, groups shown as ``[mode, [...]]``."""
    out: list[object] = []
    for node in nodes:
        if isinstance(node, PowerGroup):
            out.append([node.mode, _names(node.children)])
        else:
            out.append(node.name)
    return out


def test_combine_wraps_two_cards_into_a_group(qapp: QApplication) -> None:
    sheet, char = _sheet_with("Alpha", "Beta", "Gamma")
    alpha, beta = char.powers[0], char.powers[1]

    # Drop Beta onto Alpha → a new Independent group [Alpha, Beta] in Alpha's slot.
    sheet.powers._on_combine(beta.id, alpha.id)
    assert _names(char.powers) == [["independent", ["Alpha", "Beta"]], "Gamma"]


def test_combine_nests_a_group_inside_a_group(qapp: QApplication) -> None:
    sheet, char = _sheet_with("E1", "E2", "E3")
    e1, e2, e3 = char.powers
    sheet.powers._on_combine(e2.id, e1.id)  # group (E1, E2)
    group = char.powers[0]
    sheet.powers._set_group_mode(group, STRUCTURE_LINKED)

    # Drop E3 onto the whole linked group's title bar → array(linked(E1,E2), E3).
    sheet.powers._on_combine(e3.id, group.id)
    assert _names(char.powers) == [["independent", [["linked", ["E1", "E2"]], "E3"]]]


def test_move_into_a_group_adds_a_member(qapp: QApplication) -> None:
    sheet, char = _sheet_with("A", "B", "C")
    a, b, c = char.powers
    sheet.powers._on_combine(b.id, a.id)  # group [A, B]
    group = char.powers[0]

    # Drop C into the group's body (a gap at the end of its children) → it joins.
    sheet.powers._on_move(c.id, group.id, 2)
    assert _names(char.powers) == [["independent", ["A", "B", "C"]]]


def test_move_out_of_a_group_collapses_a_singleton(qapp: QApplication) -> None:
    sheet, char = _sheet_with("A", "B", "C")
    a, b, c = char.powers
    sheet.powers._on_combine(b.id, a.id)  # group [A, B]

    # Pull B back out to the top level; the group is left with one child and dissolves.
    sheet.powers._on_move(b.id, "", 2)
    assert _names(char.powers) == ["A", "C", "B"]


def test_ungroup_dissolves_but_keeps_members(qapp: QApplication) -> None:
    sheet, char = _sheet_with("A", "B", "C")
    a, b = char.powers[0], char.powers[1]
    sheet.powers._on_combine(b.id, a.id)  # group [A, B] at index 0
    group = char.powers[0]

    sheet.powers._ungroup(group)
    assert _names(char.powers) == ["A", "B", "C"]


def test_combining_a_node_into_its_own_descendant_is_rejected(qapp: QApplication) -> None:
    sheet, char = _sheet_with("A", "B")
    a, b = char.powers
    sheet.powers._on_combine(b.id, a.id)  # group [A, B]
    group = char.powers[0]
    before = _names(char.powers)

    # Dropping the whole group onto its own child A must be a no-op (no cycle).
    sheet.powers._on_combine(group.id, a.id)
    assert _names(char.powers) == before


def test_array_group_active_member_normalizes(qapp: QApplication) -> None:
    sheet, char = _sheet_with("A", "B")
    a, b = char.powers
    sheet.powers._on_combine(b.id, a.id)
    group = char.powers[0]
    sheet.powers._set_group_mode(group, STRUCTURE_ARRAY)
    # An array always has a valid active child after a structural change.
    assert group.active_child_id in {a.id, b.id}
    sheet.powers._set_array_active(group, b.id)
    assert group.active_child_id == b.id

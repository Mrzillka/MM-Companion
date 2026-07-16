"""The Powers section's drag-to-group tree mutations.

Real drag-and-drop events are unreliable headless, so these drive the public mutation
seams the drop handlers delegate to (``_on_combine`` / ``_on_move`` / ``_ungroup``)
and assert on the resulting ``Character.powers`` tree.
"""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QApplication, QCheckBox, QPushButton

from mm_companion.core.character import Character
from mm_companion.core.data_loader import load_game_data
from mm_companion.core.powers import (
    STRUCTURE_ARRAY,
    STRUCTURE_LINKED,
    Power,
    PowerEffectInstance,
    PowerGroup,
)
from mm_companion.core.rules import power_trait_bonuses
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


def _sheet_for(char: Character) -> CharacterSheet:
    return CharacterSheet(load_game_data(), char)


def test_all_instant_array_shows_no_member_control(qapp: QApplication) -> None:
    char = Character.new_default(load_game_data())
    a = Power(name="Bolt", effects=[PowerEffectInstance("damage", rank=6)])
    b = Power(name="Beam", effects=[PowerEffectInstance("damage", rank=4)])
    group = PowerGroup(mode=STRUCTURE_ARRAY, children=[a, b])
    char.powers.append(group)
    sec = _sheet_for(char).powers
    # Nothing in the array stands on the sheet, so neither member gets a control.
    assert sec._array_member_control(a, group) is None
    assert sec._array_member_control(b, group) is None


def test_mixed_array_gives_continuous_active_and_instant_use(qapp: QApplication) -> None:
    char = Character.new_default(load_game_data())
    field = Power(name="Field", effects=[PowerEffectInstance("protection", rank=6)])
    bolt = Power(name="Bolt", effects=[PowerEffectInstance("damage", rank=8)])
    group = PowerGroup(mode=STRUCTURE_ARRAY, children=[field, bolt])
    char.powers.append(group)
    sec = _sheet_for(char).powers
    # The continuous member keeps a persistent "Active" radio; the instant one gets a
    # momentary "Use" (it isn't kept active — using it just drops the field).
    assert isinstance(sec._array_member_control(field, group), QCheckBox)
    assert isinstance(sec._array_member_control(bolt, group), QPushButton)


def test_linked_group_one_toggle_drops_a_permanent_member(qapp: QApplication) -> None:
    data = load_game_data()
    char = Character.new_default(data)
    might = Power(  # permanent, ungated Enhanced Trait
        name="Might",
        effects=[PowerEffectInstance("enhanced_trait", rank=3, config={"target": "STR"})],
    )
    flight = Power(name="Flight", effects=[PowerEffectInstance("flight", rank=2)])  # sustained
    group = PowerGroup(mode=STRUCTURE_LINKED, children=[might, flight])
    char.powers.append(group)
    sec = _sheet_for(char).powers

    assert sec._node_is_gateable(group) is True  # the sustained member can be turned off
    assert sec._node_has_standing(group) is True
    assert sec._group_is_active(group) is True
    assert power_trait_bonuses(char, data)["ability"]["STR"].amount == 3

    sec._set_group_active(group, False)  # the one group toggle turns everything off
    assert sec._group_is_active(group) is False
    # Even the permanent member's boost drops when the linked group is switched off.
    assert power_trait_bonuses(char, data)["ability"].get("STR") is None

    sec._set_group_active(group, True)
    assert power_trait_bonuses(char, data)["ability"]["STR"].amount == 3


def test_inactive_linked_group_disables_nested_member_controls(qapp: QApplication) -> None:
    data = load_game_data()
    char = Character.new_default(data)
    # A linked group holding a mixed array (so a nested "Use" control exists) plus a
    # sustained power that makes the group gateable.
    field = Power(name="Field", effects=[PowerEffectInstance("protection", rank=6)])
    bolt = Power(name="Bolt", effects=[PowerEffectInstance("damage", rank=8)])
    arr = PowerGroup(mode=STRUCTURE_ARRAY, children=[field, bolt])
    flight = Power(name="Flight", effects=[PowerEffectInstance("flight", rank=2)])
    linked = PowerGroup(mode=STRUCTURE_LINKED, children=[arr, flight])
    char.powers.append(linked)
    sec = _sheet_for(char).powers

    def nested_use_button(card: object) -> QPushButton:
        buttons = [b for b in card.findChildren(QPushButton) if b.text() in ("Use", "In use")]
        assert len(buttons) == 1  # only the array's instant member has a Use control
        return buttons[0]

    on_card = sec._render_node(linked, None)  # kept referenced so Qt doesn't free it
    assert nested_use_button(on_card).isEnabled() is True  # linked group is active

    sec._set_group_active(linked, False)
    off_card = sec._render_node(linked, None)
    # With the group switched off the nested member can no longer be re-activated.
    assert nested_use_button(off_card).isEnabled() is False


def test_homerule_power_shows_the_badge(qapp: QApplication) -> None:
    from PySide6.QtWidgets import QLabel

    data = load_game_data()
    char = Character.new_default(data)
    plain = Power(name="Blast", effects=[PowerEffectInstance("damage", rank=4)])
    effect = PowerEffectInstance("damage", rank=4)
    effect.overrides["range"] = {"value": "Planetary", "order": "after"}
    homebrew = Power(name="Homebrew", effects=[effect])
    char.powers.extend([plain, homebrew])

    sheet = CharacterSheet(data, char)
    badges = [lbl for lbl in sheet.powers.findChildren(QLabel) if lbl.text() == "⌂"]
    # Exactly one card (the homerule one) carries the badge.
    assert len(badges) == 1
    assert "homerule" in badges[0].toolTip().lower()

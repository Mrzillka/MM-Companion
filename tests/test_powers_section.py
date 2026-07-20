"""The Powers section's drag-to-group tree mutations.

Real drag-and-drop events are unreliable headless, so these drive the public mutation
seams the drop handlers delegate to (``_on_combine`` / ``_on_move`` / ``_ungroup``)
and assert on the resulting ``Character.powers`` tree.
"""

from __future__ import annotations

import pytest
from PySide6.QtCore import QEvent, QPointF, QVariantAnimation
from PySide6.QtGui import QEnterEvent
from PySide6.QtWidgets import QApplication, QGridLayout, QLabel

from mm_companion.core.character import Character
from mm_companion.core.data_loader import load_game_data
from mm_companion.core.powers import (
    STRUCTURE_ARRAY,
    STRUCTURE_LINKED,
    ModifierSelection,
    Power,
    PowerEffectInstance,
    PowerGroup,
)
from mm_companion.core.rules import power_trait_bonuses
from mm_companion.ui.character_sheet import CharacterSheet
from mm_companion.ui.sections.powers import PowersSection, _DraggableCard


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


def test_all_instant_array_cards_are_not_clickable(qapp: QApplication) -> None:
    char = Character.new_default(load_game_data())
    a = Power(name="Bolt", effects=[PowerEffectInstance("damage", rank=6)])
    b = Power(name="Beam", effects=[PowerEffectInstance("damage", rank=4)])
    group = PowerGroup(mode=STRUCTURE_ARRAY, children=[a, b])
    char.powers.append(group)
    sec = _sheet_for(char).powers
    # Nothing in the array stands on the sheet, so there is no live member to pick.
    assert sec._activation_role(a, group) == ""
    assert sec._activation_role(b, group) == ""


def test_mixed_array_members_select_the_live_alternate(qapp: QApplication) -> None:
    char = Character.new_default(load_game_data())
    field = Power(name="Field", effects=[PowerEffectInstance("protection", rank=6)])
    bolt = Power(name="Bolt", effects=[PowerEffectInstance("damage", rank=8)])
    group = PowerGroup(mode=STRUCTURE_ARRAY, children=[field, bolt])
    char.powers.append(group)
    sec = _sheet_for(char).powers
    # Clicking either member makes it the array's live alternate — the continuous one
    # to switch it on, the instant one to use it (which drops the field).
    assert sec._activation_role(field, group) == "select"
    assert sec._activation_role(bolt, group) == "select"

    sec._on_card_clicked(bolt, group, "select")
    assert group.active_child_id == bolt.id
    # Clicking the live member again is a no-op: an array always keeps one member live.
    sec._on_card_clicked(bolt, group, "select")
    assert group.active_child_id == bolt.id


def test_clicking_a_gated_power_card_round_trips(qapp: QApplication) -> None:
    char = Character.new_default(load_game_data())
    armor = Power(
        name="Armor",
        effects=[PowerEffectInstance("protection", rank=6, flaws=[ModifierSelection("removable")])],
    )
    char.powers.append(armor)
    sec = _sheet_for(char).powers
    assert sec._activation_role(armor, None) == "toggle"
    assert sec._power_is_active(armor) is True

    sec._on_card_clicked(armor, None, "toggle")
    assert sec._power_is_active(armor) is False
    sec._on_card_clicked(armor, None, "toggle")
    assert sec._power_is_active(armor) is True


def test_a_switched_off_card_is_dimmed(qapp: QApplication) -> None:
    char = Character.new_default(load_game_data())
    armor = Power(
        name="Armor",
        effects=[PowerEffectInstance("protection", rank=6, flaws=[ModifierSelection("removable")])],
    )
    char.powers.append(armor)
    sec = _sheet_for(char).powers

    on_card = sec._render_node(armor, None)  # kept referenced so Qt doesn't free it
    assert on_card.is_clickable() is True
    assert on_card.graphicsEffect() is None  # active: full strength

    sec._set_power_active(armor, False)
    off_card = sec._render_node(armor, None)
    assert off_card.graphicsEffect() is not None  # switched off: dimmed, still readable
    assert off_card.is_clickable() is True  # ...and still the way back on

    # Back on again: the effect is dropped rather than left sitting at full opacity, so
    # a live card never pays for painting its whole subtree through an offscreen buffer.
    sec._set_power_active(armor, True)
    assert sec._render_node(armor, None).graphicsEffect() is None


def _gated_power_section() -> tuple[CharacterSheet, PowersSection, Power]:
    """A sheet holding one gated, standing power, with a real transition duration.

    The sheet is returned alongside the section because a section is only a child
    widget: drop the sheet and Python collects it, taking the cards down with it.
    """
    char = Character.new_default(load_game_data())
    armor = Power(
        name="Armor",
        effects=[PowerEffectInstance("protection", rank=6, flaws=[ModifierSelection("removable")])],
    )
    char.powers.append(armor)
    sheet = _sheet_for(char)
    sheet.powers.TRANSITION_MS = 400  # the conftest fixture zeroes it for other tests
    return sheet, sheet.powers, armor


def _transition_of(sec: PowersSection) -> QVariantAnimation:
    """The animation easing the section's one card, driven by hand.

    Stepped with ``setCurrentTime`` rather than by waiting on Qt's animation timer:
    a wait is both slow and unreliable here — under the full suite the timer can go a
    whole second without delivering a frame — while stepping is exact and immediate.
    """
    animation = sec.findChild(_DraggableCard).findChild(QVariantAnimation)
    assert animation is not None
    return animation


def test_flipping_a_card_eases_between_the_two_looks(qapp: QApplication) -> None:
    sheet, sec, armor = _gated_power_section()

    sec._set_power_active(armor, False)
    card = sec.findChild(_DraggableCard)
    # The replacement card picks up the live look its predecessor was showing, rather
    # than cutting straight to dimmed.
    assert card.off_progress() == pytest.approx(0.0)
    assert card.graphicsEffect() is None

    ease = _transition_of(sec)
    assert ease.duration() == 400
    ease.setCurrentTime(200)
    # Genuinely part-way: dimmer than live, not yet as dim as off.
    assert 0.0 < card.off_progress() < 1.0
    assert 0.5 < card.graphicsEffect().opacity() < 1.0

    ease.setCurrentTime(ease.duration())
    assert card.off_progress() == pytest.approx(1.0)
    assert card.graphicsEffect().opacity() == pytest.approx(0.5)


def test_a_toggle_mid_transition_resumes_from_what_is_on_screen(qapp: QApplication) -> None:
    sheet, sec, armor = _gated_power_section()

    sec._set_power_active(armor, False)
    _transition_of(sec).setCurrentTime(150)  # part-way out
    partial = sec.findChild(_DraggableCard).off_progress()
    assert 0.0 < partial < 1.0

    # Clicking again rebuilds the card, which must resume from where the eye left it —
    # not snap back to the look the interrupted transition was heading for.
    sec._set_power_active(armor, True)
    assert sec.findChild(_DraggableCard).off_progress() == pytest.approx(partial)


def test_only_a_switchable_card_advertises_itself(qapp: QApplication) -> None:
    char = Character.new_default(load_game_data())
    armor = Power(  # gated + standing: clickable
        name="Armor",
        effects=[PowerEffectInstance("protection", rank=6, flaws=[ModifierSelection("removable")])],
    )
    blast = Power(name="Blast", effects=[PowerEffectInstance("damage", rank=6)])  # instant: inert
    char.powers.extend([armor, blast])
    sec = _sheet_for(char).powers
    cards = {card.node_id: card for card in sec.findChildren(_DraggableCard)}

    assert "border-left" in cards[armor.id].styleSheet()
    assert "border-left" not in cards[blast.id].styleSheet()

    # Hovering confirms the target under the pointer — on a switch, and only there.
    for card in (cards[armor.id], cards[blast.id]):
        card.enterEvent(QEnterEvent(QPointF(), QPointF(), QPointF()))
    assert "background" in cards[armor.id].styleSheet()
    assert "background" not in cards[blast.id].styleSheet()

    cards[armor.id].leaveEvent(QEvent(QEvent.Type.Leave))
    assert "background" not in cards[armor.id].styleSheet()


def _enter(card: _DraggableCard) -> None:
    card.enterEvent(QEnterEvent(QPointF(), QPointF(), QPointF()))


def test_hovering_a_member_stands_its_group_down(qapp: QApplication) -> None:
    char = Character.new_default(load_game_data())
    field = Power(name="Field", effects=[PowerEffectInstance("protection", rank=6)])
    bolt = Power(name="Bolt", effects=[PowerEffectInstance("damage", rank=6)])
    array = PowerGroup(mode=STRUCTURE_ARRAY, name="Force", children=[field, bolt])
    linked = PowerGroup(
        mode=STRUCTURE_LINKED,
        name="Rig",
        children=[
            Power(
                name="Wings",
                effects=[
                    PowerEffectInstance("flight", rank=3, flaws=[ModifierSelection("removable")])
                ],
            ),
            array,
        ],
    )
    char.powers.append(linked)
    sec = _sheet_for(char).powers
    cards = {card.node_id: card for card in sec.findChildren(_DraggableCard)}

    # The Linked group is the switch, so hovering it lights it.
    _enter(cards[linked.id])
    assert cards[linked.id]._hovered is True
    # Qt sends no Leave to a widget the pointer merely moved *deeper* into, so the
    # member has to stand its ancestors down itself — otherwise the group would stay
    # lit and, being an ancestor, would light up half the block behind the member.
    _enter(cards[field.id])
    assert cards[field.id]._hovered is True
    assert cards[linked.id]._hovered is False


def test_hovering_an_inert_member_keeps_its_group_lit(qapp: QApplication) -> None:
    char = Character.new_default(load_game_data())
    wings = Power(
        name="Wings",
        effects=[PowerEffectInstance("flight", rank=3, flaws=[ModifierSelection("removable")])],
    )
    group = PowerGroup(mode=STRUCTURE_LINKED, name="Rig", children=[wings])
    char.powers.append(group)
    sec = _sheet_for(char).powers
    cards = {card.node_id: card for card in sec.findChildren(_DraggableCard)}

    # A Linked group's member has no switch of its own — its press bubbles up to the
    # group. So the highlight must stay on the group, or it would vanish exactly where
    # clicking still works.
    assert cards[wings.id].is_clickable() is False
    _enter(cards[group.id])
    _enter(cards[wings.id])
    assert cards[group.id]._hovered is True


def test_a_hovered_group_lights_its_outline_but_does_not_fill(qapp: QApplication) -> None:
    char = Character.new_default(load_game_data())
    group = PowerGroup(
        mode=STRUCTURE_LINKED,
        children=[
            Power(
                name="Wings",
                effects=[
                    PowerEffectInstance("flight", rank=3, flaws=[ModifierSelection("removable")])
                ],
            )
        ],
    )
    char.powers.append(group)
    sec = _sheet_for(char).powers
    card = next(c for c in sec.findChildren(_DraggableCard) if c.node_id == group.id)

    _enter(card)
    # A stylesheet background paints behind every child, so a filled group would wash
    # its whole subtree. Its outline carries the hover instead.
    assert "border" in card.styleSheet()
    assert "background" not in card.styleSheet()


def test_a_power_that_rolls_nothing_has_no_dice_footer(qapp: QApplication) -> None:
    char = Character.new_default(load_game_data())
    armor = Power(name="Armor", effects=[PowerEffectInstance("protection", rank=6)])
    blast = Power(name="Blast", effects=[PowerEffectInstance("damage", rank=6)])
    char.powers.extend([armor, blast])
    sec = _sheet_for(char).powers
    cards = {card.node_id: card for card in sec.findChildren(_DraggableCard)}

    def dice(card: _DraggableCard) -> list[str]:
        return [lb.text() for lb in card.findChildren(QLabel) if lb.text().startswith("🎲")]

    # Nothing to roll, so nothing is said about it — no placeholder line, and no rule
    # above the footer that is not there.
    assert dice(cards[armor.id]) == []
    assert sec._rolls_lines(armor) == []

    # An attack and the save it forces are two rolls, made by two people: a line each.
    attack, save = dice(cards[blast.id])
    assert attack == "🎲 0 vs. Defense"
    assert save.startswith("🎲 Toughness vs. ")


def test_an_effects_terms_sit_beside_its_modifiers(qapp: QApplication) -> None:
    char = Character.new_default(load_game_data())
    char.powers.append(
        Power(
            name="Armor",
            effects=[
                PowerEffectInstance("protection", rank=6, flaws=[ModifierSelection("removable")])
            ],
        )
    )
    sec = _sheet_for(char).powers
    labels = {lb.text().split(":")[0]: lb for lb in sec.findChildren(QLabel)}

    # Side by side, not stacked: the modifiers column and the terms grid are two items
    # of one horizontal row, the modifiers first. Asserted structurally rather than by
    # geometry — nothing here is ever shown, so every widget would sit at 0,0.
    column = labels["Flaws"].parentWidget()
    effect_box = column.parentWidget()
    assert labels["Type"].parentWidget() is effect_box

    stack = effect_box.layout()
    rows = (stack.itemAt(i).layout() for i in range(stack.count()))
    row = next(r for r in rows if r is not None and r.indexOf(column) >= 0)
    assert row.indexOf(column) == 0
    assert any(isinstance(row.itemAt(i).layout(), QGridLayout) for i in range(row.count()))


def test_card_type_sizes_ride_the_transition(qapp: QApplication) -> None:
    char = Character.new_default(load_game_data())
    char.powers.append(Power(name="Blast", effects=[PowerEffectInstance("damage", rank=6)]))
    sec = _sheet_for(char).powers
    card = sec.findChild(_DraggableCard)
    labels = {label.text(): label for label in card.findChildren(QLabel)}

    # The name and the game-term table carry their size on the QFont, never in the
    # stylesheet: a stylesheet font-size outranks the card's font and would sit the
    # switched-off transition out, leaving those two lines at full size.
    for text in ("Blast", "Type:"):
        assert "font-size" not in labels[text].styleSheet()

    before = {text: labels[text].font().pointSizeF() for text in ("Blast", "Type:")}
    card.set_off_progress(1.0)
    for text, size in before.items():
        assert labels[text].font().pointSizeF() == pytest.approx(size * 0.9)


def test_cards_still_toggle_in_the_locked_view(qapp: QApplication) -> None:
    char = Character.new_default(load_game_data())
    armor = Power(
        name="Armor",
        effects=[PowerEffectInstance("protection", rank=6, flaws=[ModifierSelection("removable")])],
    )
    char.powers.append(armor)
    sec = _sheet_for(char).powers
    sec.set_locked(True)

    # Switching a power on/off is a mid-play action, not an edit to the build, so the
    # read-only sheet keeps it — only the editing chrome goes away.
    card = sec.findChild(_DraggableCard)
    assert card is not None and card.is_clickable()
    card.clicked.emit()
    assert sec._power_is_active(armor) is False


def test_cards_show_their_game_terms_without_hovering(qapp: QApplication) -> None:
    char = Character.new_default(load_game_data())
    char.powers.append(Power(name="Blast", effects=[PowerEffectInstance("damage", rank=6)]))
    sheet = CharacterSheet(load_game_data(), char)

    labels = {label.text() for label in sheet.powers.findChildren(QLabel)}
    # The game-term table is part of the card itself, not a tooltip.
    assert {"Type:", "Range:", "Action:", "Duration:"} <= labels


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


def test_inactive_linked_group_disables_nested_member_cards(qapp: QApplication) -> None:
    data = load_game_data()
    char = Character.new_default(data)
    # A linked group holding a mixed array (so its members are clickable selectors) plus
    # a sustained power that makes the group gateable.
    field = Power(name="Field", effects=[PowerEffectInstance("protection", rank=6)])
    bolt = Power(name="Bolt", effects=[PowerEffectInstance("damage", rank=8)])
    arr = PowerGroup(mode=STRUCTURE_ARRAY, children=[field, bolt])
    flight = Power(name="Flight", effects=[PowerEffectInstance("flight", rank=2)])
    linked = PowerGroup(mode=STRUCTURE_LINKED, children=[arr, flight])
    char.powers.append(linked)
    sec = _sheet_for(char).powers

    def nested_member_cards(card: object) -> list[object]:
        cards = [c for c in card.findChildren(_DraggableCard) if c.node_id in (field.id, bolt.id)]
        assert len(cards) == 2  # both array members are rendered inside the group
        return cards

    on_card = sec._render_node(linked, None)  # kept referenced so Qt doesn't free it
    assert all(c.is_clickable() for c in nested_member_cards(on_card))

    sec._set_group_active(linked, False)
    off_card = sec._render_node(linked, None)
    # With the group switched off the nested members can no longer be re-activated.
    assert not any(c.is_clickable() for c in nested_member_cards(off_card))


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

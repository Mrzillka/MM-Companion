"""The Powers section's drag-to-group tree mutations.

Real drag-and-drop events are unreliable headless, so these drive the public mutation
seams the drop handlers delegate to (``_on_combine`` / ``_on_move`` / ``_ungroup``)
and assert on the resulting ``Character.powers`` tree.
"""

from __future__ import annotations

import pytest
from PySide6.QtCore import QEvent, QPointF, Qt, QVariantAnimation
from PySide6.QtGui import QEnterEvent, QFont, QFontMetrics
from PySide6.QtWidgets import QApplication, QGridLayout, QLabel, QPushButton

from mm_companion.core import library, storage
from mm_companion.core.character import Character
from mm_companion.core.data_loader import load_game_data
from mm_companion.core.powers import (
    STRUCTURE_ARRAY,
    STRUCTURE_INDEPENDENT,
    STRUCTURE_LINKED,
    ModifierSelection,
    Power,
    PowerEffectInstance,
    PowerGroup,
)
from mm_companion.core.rules import effect_readout_rows, effective_size, power_trait_bonuses
from mm_companion.ui import theme
from mm_companion.ui.character_sheet import CharacterSheet
from mm_companion.ui.sections.powers import (
    PowersSection,
    _DraggableCard,
    _ModeToggle,
    _RollLine,
    _SizeLadder,
)


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

    def rolled(card: _DraggableCard) -> list[str]:
        """The lines this card can be clicked to roll — the whole line is the target."""
        # The text label is the last of the line's own — a rollable line leads with 🎲.
        return [
            line.findChildren(QLabel, options=Qt.FindChildOption.FindDirectChildrenOnly)[-1].text()
            for line in card.findChildren(_RollLine)
            if line.is_rollable()
        ]

    # Nothing to roll, so nothing is said about it — no placeholder line, and no rule
    # above the footer that is not there.
    assert rolled(cards[armor.id]) == []
    assert sec._rolls_lines(armor) == []

    # An attack and the save it forces are two rolls, made by two people: a line each.
    attack, save = sec._rolls_lines(blast)
    assert attack == "0 vs. Defense"
    assert save.startswith("Toughness vs. ")

    # But only the attack is the wielder's to roll. The save is written down and left
    # inert — it reaches whoever makes it as a chip on the attack's history card.
    assert rolled(cards[blast.id]) == [attack]


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


def test_linked_group_drives_members_through_an_intervening_subgroup(
    qapp: QApplication,
) -> None:
    """Dragging one card onto another always makes an *Independent* group, so a Linked
    group's members are routinely one level deeper than the group. They must still
    switch as one rather than sprouting their own switches."""
    from mm_companion.core.powers import STRUCTURE_INDEPENDENT

    data = load_game_data()
    char = Character.new_default(data)
    flight = Power(name="Flight", effects=[PowerEffectInstance("flight", rank=4)])
    shield = Power(name="Shield", effects=[PowerEffectInstance("protection", rank=6)])
    inner = PowerGroup(mode=STRUCTURE_INDEPENDENT, children=[flight, shield])
    glow = Power(name="Glow", effects=[PowerEffectInstance("protection", rank=2)])
    linked = PowerGroup(mode=STRUCTURE_LINKED, children=[inner, glow])
    char.powers.append(linked)
    sec = _sheet_for(char).powers

    # The group owns the switch; nothing beneath it does — not the sub-group and not
    # the leaves inside the sub-group.
    assert sec._activation_role(linked, None) == "toggle"
    assert sec._activation_role(inner, linked) == ""
    assert sec._activation_role(flight, inner) == ""
    assert sec._activation_role(glow, linked) == ""

    # And a nested leaf's activation set is every leaf under the linked group.
    assert sorted(p.name for p in sec._linked_activation_set(flight)) == [
        "Flight",
        "Glow",
        "Shield",
    ]

    # Flipping the group takes the nested members with it.
    sec._set_group_active(linked, False)
    assert not any(p.activated for p in (flight, shield, glow))


def test_a_linked_group_nested_in_another_switches_from_the_outermost(
    qapp: QApplication,
) -> None:
    data = load_game_data()
    char = Character.new_default(data)
    # Flight is sustained, so it carries a runtime gate and the group is switchable.
    a = Power(name="A", effects=[PowerEffectInstance("flight", rank=2)])
    b = Power(name="B", effects=[PowerEffectInstance("flight", rank=2)])
    inner = PowerGroup(mode=STRUCTURE_LINKED, children=[a, b])
    c = Power(name="C", effects=[PowerEffectInstance("flight", rank=2)])
    outer = PowerGroup(mode=STRUCTURE_LINKED, children=[inner, c])
    char.powers.append(outer)
    sec = _sheet_for(char).powers

    assert sec._activation_role(outer, None) == "toggle"
    assert sec._activation_role(inner, outer) == ""  # the inner group defers outward
    assert sorted(p.name for p in sec._linked_activation_set(a)) == ["A", "B", "C"]


def test_a_top_level_power_keeps_its_own_switch(qapp: QApplication) -> None:
    """The ancestor walk must not make an ungrouped power inert."""
    data = load_game_data()
    char = Character.new_default(data)
    flight = Power(name="Flight", effects=[PowerEffectInstance("flight", rank=4)])
    char.powers.append(flight)
    sec = _sheet_for(char).powers

    assert sec._activation_role(flight, None) == "toggle"
    assert sec._linked_activation_set(flight) == [flight]


# -- a group's mode is readable ------------------------------------------------


def _grouped_sheet(mode: str) -> tuple[CharacterSheet, PowerGroup]:
    """A sheet holding one group of two powers, in *mode*."""
    sheet, char = _sheet_with("A", "B")
    first, second = char.powers
    sheet.powers._on_combine(second.id, first.id)
    group = char.powers[0]
    sheet.powers._set_group_mode(group, mode)
    return sheet, group


def _mode_toggle(sec: PowersSection) -> _ModeToggle:
    toggle = sec.findChild(_ModeToggle)
    assert toggle is not None
    return toggle


@pytest.mark.parametrize("preset", ["classic", "slate-dark", "parchment-light", "crimson-gold"])
def test_a_group_card_states_which_mode_is_lit(qapp: QApplication, preset: str) -> None:
    """The regression: the lit segment painted exactly like its two neighbours.

    A styled preset states ``QPushButton``'s box in the application sheet, which
    makes ``QStyleSheetStyle`` take the box over and stop painting the platform's
    checked panel — and no ``:checked`` rule replaced it. Classic states no widget
    chrome at all, so an app-level rule could not fix it for every preset either.
    The switch therefore says what "lit" looks like itself, under all of them.
    """
    theme.set_active_theme(preset)
    sheet, _group = _grouped_sheet(STRUCTURE_ARRAY)

    toggle = _mode_toggle(sheet.powers)
    assert [b.text() for b in toggle.findChildren(QPushButton) if b.isChecked()] == ["Array"]
    lit = toggle.styleSheet().split("QPushButton:checked")
    assert len(lit) == 2, "the widget must state its own checked look"
    assert "background:" in lit[1]


def test_a_lit_segment_has_room_for_its_bolder_label(qapp: QApplication) -> None:
    """ "Independent" came out as "Independen" the moment it was the mode in force.

    The lit segment is bold and the rest are not, so a width taken from the resting
    font is a few pixels short of the label it has to hold. Every segment carries
    the same allowance, which also stops the strip re-widthing as the mode changes.
    """
    sheet, group = _grouped_sheet(STRUCTURE_INDEPENDENT)
    toggle = _mode_toggle(sheet.powers)

    for button in toggle.findChildren(QPushButton):
        bold = QFont(button.font())
        bold.setBold(True)
        assert button.minimumWidth() > QFontMetrics(bold).horizontalAdvance(button.text())

    widths = {}
    for mode in (STRUCTURE_INDEPENDENT, STRUCTURE_ARRAY, STRUCTURE_LINKED):
        sheet.powers._set_group_mode(group, mode)
        toggle = _mode_toggle(sheet.powers)
        widths[mode] = [b.minimumWidth() for b in toggle.findChildren(QPushButton)]
    assert len(set(map(tuple, widths.values()))) == 1


def test_the_locked_group_card_keeps_the_mode_and_drops_the_switch(qapp: QApplication) -> None:
    """Locking is not ``setEnabled(False)`` — a greyed strip of three reads as nothing.

    The unlit segments go instead, leaving the lit one as a static chip naming the
    mode, transparent to the mouse so the click falls through to the group card,
    which is the switch.
    """
    sheet, _group = _grouped_sheet(STRUCTURE_LINKED)

    sheet.powers.set_locked(True)
    toggle = _mode_toggle(sheet.powers)
    shown = [b for b in toggle.findChildren(QPushButton) if not b.isHidden()]
    assert [b.text() for b in shown] == ["Linked"]
    assert shown[0].isEnabled()
    assert shown[0].testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

    sheet.powers.set_locked(False)
    toggle = _mode_toggle(sheet.powers)
    shown = [b for b in toggle.findChildren(QPushButton) if not b.isHidden()]
    assert [b.text() for b in shown] == ["Independent", "Array", "Linked"]
    assert not shown[0].testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)


# -- the size ladder -----------------------------------------------------------


def _size_sheet(rank: int = 3, *, effect: str = "growth", size: str = "Medium"):
    """A sheet holding one size power, and that power."""
    data = load_game_data()
    char = Character.new_default(data)
    char.characteristics["size"] = size
    power = Power(name="Giant Form", effects=[PowerEffectInstance(effect, rank=rank)])
    char.powers.append(power)
    return CharacterSheet(data, char), char, power


def _ladder(sec: PowersSection) -> _SizeLadder:
    ladder = sec.findChild(_SizeLadder)
    assert ladder is not None
    return ladder


def _rungs(sec: PowersSection) -> list[tuple[str, bool]]:
    return [(b.text(), b.isChecked()) for b in _ladder(sec).findChildren(QPushButton)]


def test_a_growth_card_carries_one_rung_per_rank(qapp: QApplication) -> None:
    """Labelled with the size reached, not the rank paid — and the top one lit."""
    sheet, _char, _power = _size_sheet(3)

    assert _rungs(sheet.powers) == [("Large", False), ("Huge", False), ("Gargantuan", True)]


def test_picking_a_rung_moves_the_sheet_and_the_card(qapp: QApplication) -> None:
    sheet, char, power = _size_sheet(3)
    data = load_game_data()

    huge = next(b for b in _ladder(sheet.powers).findChildren(QPushButton) if b.text() == "Huge")
    huge.click()

    assert power.effects[0].current_rank == 2
    assert effective_size(char, data) == "Huge"
    # The card is rebuilt from the model, so the new strip agrees with it.
    assert _rungs(sheet.powers) == [("Large", False), ("Huge", True), ("Gargantuan", False)]


def test_a_rung_wakes_a_dormant_power_at_that_rung(qapp: QApplication) -> None:
    """One click from off to Huge, rather than on-at-full then down to Huge."""
    sheet, char, power = _size_sheet(3)
    data = load_game_data()
    sheet.powers._set_power_active(power, False)

    assert _rungs(sheet.powers) == [("Large", False), ("Huge", False), ("Gargantuan", False)]

    huge = next(b for b in _ladder(sheet.powers).findChildren(QPushButton) if b.text() == "Huge")
    huge.click()
    assert power.activated and power.effects[0].toggled_on
    assert effective_size(char, data) == "Huge"


def test_a_rung_is_a_runtime_change_that_still_marks_the_sheet_dirty(
    qapp: QApplication,
) -> None:
    """Like every other card switch — and, like them, saved with the build.

    It stays a *runtime* signal (``changed`` is for build edits, and a rung re-derives
    rather than re-costing), but the rung is in the save now, so the sheet has to know
    it has something unwritten or closing the window would drop it.
    """
    sheet, _char, _power = _size_sheet(3)
    edits: list[int] = []
    runtime: list[int] = []
    dirtied: list[int] = []
    sheet.powers.changed.connect(lambda: edits.append(1))
    sheet.powers.runtimeChanged.connect(lambda: runtime.append(1))
    sheet.edited.connect(lambda: dirtied.append(1))

    next(b for b in _ladder(sheet.powers).findChildren(QPushButton) if b.text() == "Large").click()

    assert edits == []
    assert runtime == [1]
    assert dirtied


def test_a_rung_picked_on_the_card_survives_a_save_and_a_reopen(
    qapp: QApplication, tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The reported bug, end to end: click Large, save, open the file again.

    The two halves are tested apart (the flag round-trips in ``tests/test_powers.py``,
    the click is above), and this is the one that says the user's actual sequence
    works — the sheet writes what the card is showing.
    """
    monkeypatch.setenv(storage.HOME_ENV_VAR, str(tmp_path))
    sheet, char, _power = _size_sheet(3)
    char.profile["hero_name"] = "Colossus"

    next(b for b in _ladder(sheet.powers).findChildren(QPushButton) if b.text() == "Large").click()
    reopened = library.load_character(library.save_character(char))

    assert reopened.powers[0].effects[0].current_rank == 1
    assert effective_size(reopened, load_game_data()) == "Large"


def test_the_rungs_stay_live_in_the_locked_sheet(qapp: QApplication) -> None:
    """How big you are standing there is a play action, not a build edit."""
    sheet, char, power = _size_sheet(3)
    data = load_game_data()
    sheet.set_locked(True)

    next(b for b in _ladder(sheet.powers).findChildren(QPushButton) if b.text() == "Large").click()
    assert effective_size(char, data) == "Large"


def test_a_single_rung_power_gets_no_strip(qapp: QApplication) -> None:
    """A Growth 1's one rung *is* the card's own switch; a strip would be a second one."""
    sheet, _char, _power = _size_sheet(1)

    assert sheet.powers.findChild(_SizeLadder) is None


def test_a_power_that_changes_no_size_gets_no_strip(qapp: QApplication) -> None:
    sheet, _char = _sheet_with("Blast")

    assert sheet.powers.findChild(_SizeLadder) is None


def test_the_card_readout_follows_the_rung_that_is_lit(qapp: QApplication) -> None:
    """The card's Size row and the sheet's Size line must never name two sizes."""
    sheet, char, power = _size_sheet(3)
    data = load_game_data()

    next(b for b in _ladder(sheet.powers).findChildren(QPushButton) if b.text() == "Large").click()

    row = next(r for r in effect_readout_rows(power.effects[0], data, char) if r.key == "size")
    assert row.value == "Large" == effective_size(char, data)


def test_a_rung_inside_a_switched_off_linked_group_is_a_read_out(qapp: QApplication) -> None:
    """Visible — it still says where the power is set — but not a control.

    Never ``setEnabled(False)``: nothing in this app greys a control out, so the
    buttons go transparent to the mouse and the click falls through to the card.
    """
    data = load_game_data()
    char = Character.new_default(data)
    growth = Power(name="Giant Form", effects=[PowerEffectInstance("growth", rank=3)])
    flight = Power(name="Flight", effects=[PowerEffectInstance("flight", rank=4)])
    char.powers.append(PowerGroup(mode=STRUCTURE_LINKED, children=[growth, flight]))
    sec = _sheet_for(char).powers
    sec._set_group_active(char.powers[0], False)

    buttons = _ladder(sec).findChildren(QPushButton)
    assert [b.text() for b in buttons] == ["Large", "Huge", "Gargantuan"]
    assert all(b.isEnabled() for b in buttons)
    assert all(b.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents) for b in buttons)


def test_a_rung_makes_an_array_member_the_live_alternate(qapp: QApplication) -> None:
    """Picking a rung does whatever clicking the card would, then lands on that rung."""
    data = load_game_data()
    char = Character.new_default(data)
    growth = Power(name="Giant Form", effects=[PowerEffectInstance("growth", rank=3)])
    flight = Power(name="Flight", effects=[PowerEffectInstance("flight", rank=4)])
    group = PowerGroup(mode=STRUCTURE_ARRAY, children=[flight, growth], active_child_id=flight.id)
    char.powers.append(group)
    sec = _sheet_for(char).powers

    assert _rungs(sec) == [("Large", False), ("Huge", False), ("Gargantuan", False)]
    next(b for b in _ladder(sec).findChildren(QPushButton) if b.text() == "Huge").click()

    assert group.active_child_id == growth.id
    assert effective_size(char, data) == "Huge"


@pytest.mark.parametrize("preset", ["classic", "slate-dark", "parchment-light", "crimson-gold"])
def test_the_lit_rung_states_its_own_look(qapp: QApplication, preset: str) -> None:
    """The same trap the group's mode toggle fell into, on the same tokens."""
    theme.set_active_theme(preset)
    sheet, _char, _power = _size_sheet(3)

    lit = _ladder(sheet.powers).styleSheet().split("QPushButton:checked")
    assert len(lit) == 2, "the strip must state its own checked look"
    assert "background:" in lit[1]


def test_the_rung_already_lit_switches_the_power_off(qapp: QApplication) -> None:
    """A click on the lit rung is the card's own click — the strip is a whole control."""
    sheet, char, power = _size_sheet(3)
    data = load_game_data()

    next(b for b in _ladder(sheet.powers).findChildren(QPushButton) if b.text() == "Huge").click()
    assert effective_size(char, data) == "Huge"

    next(b for b in _ladder(sheet.powers).findChildren(QPushButton) if b.text() == "Huge").click()
    assert effective_size(char, data) == "Medium"
    assert not power.activated
    assert _rungs(sheet.powers) == [("Large", False), ("Huge", False), ("Gargantuan", False)]

    # And the rung is remembered, so pressing it again comes back to Huge.
    next(b for b in _ladder(sheet.powers).findChildren(QPushButton) if b.text() == "Huge").click()
    assert effective_size(char, data) == "Huge"


def test_the_lit_rung_of_an_array_alternate_is_a_no_op(qapp: QApplication) -> None:
    """An array always keeps exactly one live member, so its rung must not switch off."""
    data = load_game_data()
    char = Character.new_default(data)
    growth = Power(name="Giant Form", effects=[PowerEffectInstance("growth", rank=3)])
    flight = Power(name="Flight", effects=[PowerEffectInstance("flight", rank=4)])
    group = PowerGroup(mode=STRUCTURE_ARRAY, children=[growth, flight], active_child_id=growth.id)
    char.powers.append(group)
    sec = _sheet_for(char).powers

    next(b for b in _ladder(sec).findChildren(QPushButton) if b.text() == "Gargantuan").click()

    assert group.active_child_id == growth.id
    assert effective_size(char, data) == "Gargantuan"


def test_a_clamped_rung_spans_the_ranks_that_fold_into_it(qapp: QApplication) -> None:
    """The button carries the span's lowest rank, so 'is this the lit one' must too."""
    sheet, char, power = _size_sheet(6, size="Gargantuan")
    data = load_game_data()

    assert _rungs(sheet.powers) == [("Colossal", False), ("Awesome", True)]
    assert power.effects[0].current_rank is None  # lit at full rank, inside the span

    next(
        b for b in _ladder(sheet.powers).findChildren(QPushButton) if b.text() == "Awesome"
    ).click()
    assert effective_size(char, data) == "Gargantuan"  # the span's rung switched it off


def test_picking_a_rung_leaves_the_page_where_it_was(qapp: QApplication) -> None:
    """The regression: the sheet jumped away from the card that was just clicked.

    Every runtime setter rebuilds the whole card tree, so the block is briefly empty —
    and whatever held focus inside it is destroyed, handing focus to a widget in some
    other block, which a ``QScrollArea`` then scrolls into view. Forced here, because
    the rungs themselves are ``NoFocus`` precisely so it cannot happen by hand.
    """
    from mm_companion.ui.main_window import MainWindow

    win = MainWindow(locked=False)
    char = win._sheet.character
    for index in range(8):
        char.powers.append(
            Power(name=f"Giant {index}", effects=[PowerEffectInstance("growth", rank=3)])
        )
    win._sheet.powers.refresh()
    win.resize(900, 700)
    win.show()
    for _ in range(12):
        qapp.processEvents()

    bar = win._sheet._scroll.verticalScrollBar()
    bar.setValue(bar.maximum() // 2)
    for _ in range(6):
        qapp.processEvents()
    before = bar.value()
    assert before > 0, "the page has to be scrollable for this to mean anything"

    button = next(
        b
        for b in win._sheet.powers.findChildren(_SizeLadder)[-1].findChildren(QPushButton)
        if b.text() == "Huge"
    )
    button.setFocus()
    for _ in range(4):
        qapp.processEvents()
    button.click()
    for _ in range(10):
        qapp.processEvents()

    assert bar.value() == before
    assert char.powers[-1].effects[0].current_rank == 2  # and the click still landed


def test_a_rung_never_takes_focus(qapp: QApplication) -> None:
    """The cause-level half: the button is destroyed by its own click, so focus on it
    could only ever be handed to another block — which is what moved the page."""
    sheet, _char, _power = _size_sheet(3)

    for button in _ladder(sheet.powers).findChildren(QPushButton):
        assert button.focusPolicy() == Qt.FocusPolicy.NoFocus

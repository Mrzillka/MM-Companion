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
from mm_companion.core.rules import (
    counter_rolls,
    effect_current_rank,
    effect_readout_rows,
    effect_roll_numbers,
    effect_total_cost,
    effective_size,
    live_powers,
    node_cost,
    power_trait_bonuses,
)
from mm_companion.ui import theme
from mm_companion.ui.character_sheet import CharacterSheet
from mm_companion.ui.power_constructor.canvas import MODE_ARRAY_DYNAMIC
from mm_companion.ui.sections.powers import (
    PowersSection,
    _DraggableCard,
    _ModeToggle,
    _RankDial,
    _RollLine,
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


def test_dynamic_is_the_mode_toggles_fourth_segment_not_a_per_card_switch(
    qapp: QApplication,
) -> None:
    from PySide6.QtWidgets import QCheckBox

    sheet, char = _sheet_with("A", "B")
    a, b = char.powers
    sheet.powers._on_combine(b.id, a.id)
    group = char.powers[0]

    def boxes() -> list[QCheckBox]:
        return [
            box
            for box in sheet.powers._list_host.findChildren(QCheckBox)
            if box.text() == "Dynamic"
        ]

    # No card carries a Dynamic box any more - the question is asked once, by the strip.
    sheet.powers._set_group_mode(group, STRUCTURE_ARRAY)
    assert boxes() == []
    assert not any(child.dynamic for child in group.children)

    # The fourth segment fans the flag out over every member, and Array takes it back off.
    sheet.powers._set_group_mode(group, MODE_ARRAY_DYNAMIC)
    assert group.mode == STRUCTURE_ARRAY
    assert all(child.dynamic for child in group.children)
    assert boxes() == []
    sheet.powers._set_group_mode(group, STRUCTURE_ARRAY)
    assert not any(child.dynamic for child in group.children)


def test_the_dynamic_segment_lights_for_an_array_with_any_dynamic_member(
    qapp: QApplication,
) -> None:
    """A mixed array saved while Dynamic was per-member reads as what it is."""

    sheet, char = _sheet_with("A", "B")
    a, b = char.powers
    sheet.powers._on_combine(b.id, a.id)
    group = char.powers[0]
    sheet.powers._set_group_mode(group, STRUCTURE_ARRAY)

    group.children[1].dynamic = True
    sheet.powers._rebuild_list()
    toggle = _mode_toggle(sheet.powers)
    assert [b.text() for b in toggle.findChildren(QPushButton) if b.isChecked()] == [
        "Dynamic array"
    ]


def test_the_dynamic_segment_reprices_the_array(qapp: QApplication) -> None:
    sheet, char = _sheet_with("Base", "Alt")
    base, alt = char.powers
    sheet.powers._on_combine(alt.id, base.id)
    group = char.powers[0]
    sheet.powers._set_group_mode(group, STRUCTURE_ARRAY)
    data = load_game_data()
    before = node_cost(group, data)

    # A 1-point alternate becomes a 2-point one, and the base pays an Alternate Effect
    # rank on top of its own full cost.
    sheet.powers._set_group_mode(group, MODE_ARRAY_DYNAMIC)
    assert all(child.dynamic for child in group.children)
    assert node_cost(group, data) == before + 2


def test_the_counter_menu_is_offered_only_where_something_could_be_readied(
    qapp: QApplication,
) -> None:
    data = load_game_data()
    char = Character.new_default(data)
    blast = Power(name="Blast", effects=[PowerEffectInstance("damage", rank=6)])
    armor = Power(name="Armor", effects=[PowerEffectInstance("protection", rank=6)])
    char.powers.extend([blast, armor])
    sec = _sheet_for(char).powers
    cards = {card.node_id: card for card in sec.findChildren(_DraggableCard)}

    # Countering costs no space on the card: it is a right-click menu, not a footer line.
    assert cards[blast.id].contextMenuPolicy() == Qt.ContextMenuPolicy.CustomContextMenu
    # An always-on Protection is never readied, so its card gets no menu rather than an
    # empty one.
    assert cards[armor.id].contextMenuPolicy() != Qt.ContextMenuPolicy.CustomContextMenu


def test_the_counter_menu_asks_the_roller_rather_than_rolling(qapp: QApplication) -> None:
    data = load_game_data()
    char = Character.new_default(data)
    blast = Power(
        name="Blast",
        effects=[
            PowerEffectInstance("damage", rank=6),
            PowerEffectInstance("affliction", rank=4),
        ],
    )
    char.powers.append(blast)
    sec = _sheet_for(char).powers
    card = next(c for c in sec.findChildren(_DraggableCard) if c.node_id == blast.id)

    menu = sec.card_menu(card, blast)
    # One entry per effect that could be readied, each naming which one it is — below
    # the separator that divides them from the card's Extra Effort entries.
    actions = menu.actions()
    counters = actions[[a.isSeparator() for a in actions].index(True) + 1 :]
    labels = [action.text() for action in counters]
    assert len(labels) == len(counter_rolls(blast, char, data)) == 2
    assert "Damage" in labels[0] and "+6" in labels[0]
    assert "Affliction" in labels[1] and "+4" in labels[1]

    seen: list = []
    sec.rollRequested.connect(seen.append)
    counters[0].trigger()
    # The section asks the roller, exactly as its footer lines do; it never rolls.
    assert len(seen) == 1 and seen[0].modifier == 6 and seen[0].dc is None


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


def _effect_array_sheet(qapp: QApplication):
    from mm_companion.ui.character_sheet import CharacterSheet as _Sheet

    data = load_game_data()
    char = Character.new_default(data)
    power = Power(
        name="Elemental Command",
        structure=STRUCTURE_ARRAY,
        effects=[
            PowerEffectInstance("protection", rank=10),
            PowerEffectInstance("flight", rank=6),
        ],
    )
    char.powers.append(power)
    return _Sheet(data, char), char, power


def test_an_array_power_gets_a_picker_for_the_effect_it_is_using(qapp: QApplication) -> None:
    from mm_companion.ui.sections.powers import _EffectSelector

    sheet, _char, power = _effect_array_sheet(qapp)
    (selector,) = sheet.powers.findChildren(_EffectSelector)
    # Seeded on the base — Flight 6 costs 12 to the Protection's 10 — not on the first
    # effect that happens to have been dropped on the canvas.
    assert selector._combo.currentText() == "Flight 6"
    assert [selector._combo.itemText(i) for i in range(selector._combo.count())] == [
        "Protection 10",
        "Flight 6",
    ]


def test_picking_an_effect_is_runtime_not_a_build_change(qapp: QApplication) -> None:
    from mm_companion.ui.sections.powers import _EffectSelector

    sheet, _char, power = _effect_array_sheet(qapp)
    runtime: list[int] = []
    changed: list[int] = []
    sheet.powers.runtimeChanged.connect(lambda: runtime.append(1))
    sheet.powers.changed.connect(lambda: changed.append(1))

    sheet.powers.findChildren(_EffectSelector)[0]._combo.setCurrentIndex(0)
    assert power.active_effect == 0
    # Which alternate you are using is a play action, so it never marks the *build* as
    # having moved — the same bargain the rank dial and the array-member click strike.
    assert runtime and not changed


def test_only_an_array_power_gets_the_picker(qapp: QApplication) -> None:
    from mm_companion.ui.sections.powers import _EffectSelector

    sheet, _char, power = _effect_array_sheet(qapp)
    power.structure = STRUCTURE_INDEPENDENT
    sheet.powers.refresh()
    assert not sheet.powers.findChildren(_EffectSelector)

    # ...and neither does an array of one, which pools nothing.
    power.structure = STRUCTURE_ARRAY
    del power.effects[1]
    sheet.powers.refresh()
    assert not sheet.powers.findChildren(_EffectSelector)


def test_the_effect_picker_survives_the_lock(qapp: QApplication) -> None:
    from mm_companion.ui.sections.powers import _EffectSelector

    sheet, _char, _power = _effect_array_sheet(qapp)
    sheet.set_locked(True)
    (selector,) = sheet.powers.findChildren(_EffectSelector)
    assert not selector._combo.testAttribute(
        Qt.WidgetAttribute.WA_TransparentForMouseEvents
    ), "choosing an alternate mid-play is not a build edit"


def test_a_power_stunt_is_refused_a_group(qapp: QApplication) -> None:
    data = load_game_data()
    char = Character.new_default(data)
    blast = Power(name="Fire Blast", effects=[PowerEffectInstance("damage", rank=10)])
    bolt = Power(name="Ice Bolt", effects=[PowerEffectInstance("damage", rank=6)])
    stunt = Power(name="Flame Shield", effects=[PowerEffectInstance("protection", rank=8)])
    stunt.stunt_of = blast.id
    char.powers.extend([blast, bolt, stunt])
    sheet = CharacterSheet(data, char)
    sec = sheet.powers

    # A stunt costs 0, which inside an array makes it the cheapest member by definition
    # — moving the base, the pool and every other member's flat price. And it is never
    # saved, so the group would come back a member short.
    sec._on_combine(stunt.id, blast.id)
    assert [node.id for node in char.powers] == [blast.id, bolt.id, stunt.id]
    # Neither direction: grouping something *onto* a stunt is the same drop.
    sec._on_combine(bolt.id, stunt.id)
    assert [node.id for node in char.powers] == [blast.id, bolt.id, stunt.id]

    # An ordinary pair still groups, and the stunt is refused the group that results.
    sec._on_combine(bolt.id, blast.id)
    group = next(node for node in char.powers if isinstance(node, PowerGroup))
    sec._on_move(stunt.id, group.id, 0)
    assert [child.id for child in group.children] == [blast.id, bolt.id]
    assert stunt in char.powers

    # Reordering it at the top level is untouched — that is where a stunt belongs.
    sec._on_move(stunt.id, "", 0)
    assert char.powers[0] is stunt


def test_the_group_list_shows_the_refusal_rather_than_swallowing_it(
    qapp: QApplication,
) -> None:
    data = load_game_data()
    char = Character.new_default(data)
    blast = Power(name="Fire Blast", effects=[PowerEffectInstance("damage", rank=10)])
    stunt = Power(name="Flame Shield", effects=[PowerEffectInstance("protection", rank=8)])
    stunt.stunt_of = blast.id
    char.powers.append(PowerGroup(mode=STRUCTURE_INDEPENDENT, children=[blast]))
    char.powers.append(stunt)
    sec = CharacterSheet(data, char).powers

    # The admission rule a group's NodeList is built with, so a refused drag is washed
    # red instead of accepted and quietly dropped on the floor.
    assert sec._groupable(blast.id)
    assert not sec._groupable(stunt.id)
    assert not sec._groupable("no such node")


def test_a_broken_build_warns_on_its_card_not_only_in_the_constructor(
    qapp: QApplication,
) -> None:
    from PySide6.QtWidgets import QLabel

    data = load_game_data()
    char = Character.new_default(data)
    # A Concealment 2 that has spent six of its ranks on senses, and an Affliction whose
    # Transformed condition imposes an effect four times dearer than the Affliction is.
    # Both are constructor-only checks: before the shared POWER_CHECKS registry the card
    # showed Power Level breaches and a stunt's ceiling and nothing else, so a character
    # built under a different ruleset carried these with no marker on the sheet at all.
    hidden = PowerEffectInstance(
        "concealment",
        rank=2,
        config={"senses": [{"id": "sight", "tier": 2}, {"id": "hearing", "tier": 2}]},
    )
    curse = PowerEffectInstance(
        "affliction",
        rank=2,
        config={
            "resistance": "Will",
            "degree3": "transformed",
            "imposedEffect": "flight",
            "imposedRank": 20,
        },
    )
    char.powers.append(Power(name="Vanish", effects=[hidden]))
    char.powers.append(Power(name="Hex", effects=[curse]))

    sheet = CharacterSheet(data, char)
    warnings = [lbl for lbl in sheet.powers.findChildren(QLabel) if lbl.text() == "⚠"]
    assert len(warnings) == 2
    tips = " ".join(w.toolTip() for w in warnings)
    assert "allocated 6 of 2 ranks" in tips  # the over-spent Concealment
    assert "imposed" in tips.lower()  # the unaffordable Transformed effect


def test_the_card_and_the_constructor_read_the_same_checks(qapp: QApplication) -> None:
    from PySide6.QtWidgets import QLabel

    from mm_companion.ui.power_constructor import PowerConstructorWindow

    data = load_game_data()
    char = Character.new_default(data)
    effect = PowerEffectInstance(
        "concealment",
        rank=1,
        config={"senses": [{"id": "sight", "tier": 2}]},
    )
    power = Power(name="Vanish", effects=[effect])
    char.powers.append(power)

    sheet = CharacterSheet(data, char)
    (warning,) = [lbl for lbl in sheet.powers.findChildren(QLabel) if lbl.text() == "⚠"]

    window = PowerConstructorWindow(data, character=char, power=power)
    # The card lists every sentence; the constructor puts the same ones behind a headline
    # naming which checks failed. Neither can gain or lose one without the other.
    assert window._warning.isVisible() or window._warning.toolTip()
    assert window._warning.toolTip() == warning.toolTip()
    assert "Over-allocated" in window._warning.text()
    window.close()


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
    assert [b.text() for b in shown] == [
        "Independent",
        "Array",
        "Dynamic array",
        "Linked",
    ]
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


def _dial(sec: PowersSection) -> _RankDial:
    dial = sec.findChild(_RankDial)
    assert dial is not None
    return dial


def _dial_state(sec: PowersSection) -> tuple[int, int, str]:
    """``(value, maximum, label)`` — where the dial is, how far it goes, what that is."""
    dial = _dial(sec)
    return dial._slider.value(), dial._slider.maximum(), dial._value.text()


def _dial_labels(sec: PowersSection) -> list[str]:
    """What every notch of the dial reads, from Off upwards."""
    dial = _dial(sec)
    return [dial._label_for(rank) for rank in range(dial._slider.maximum() + 1)]


def _turn(sec: PowersSection, rank: int) -> None:
    """Move the dial the way a keyboard or groove step does — handle up, so it commits."""
    _dial(sec)._slider.setValue(rank)


def test_a_growth_card_carries_a_dial_named_by_size(qapp: QApplication) -> None:
    """Notched by the size reached, not the rank paid — and standing at the top."""
    sheet, _char, _power = _size_sheet(3)

    assert _dial_state(sheet.powers) == (3, 3, "Gargantuan")
    assert _dial_labels(sheet.powers) == ["Off", "Large", "Huge", "Gargantuan"]


def test_turning_the_dial_moves_the_sheet_and_the_card(qapp: QApplication) -> None:
    sheet, char, power = _size_sheet(3)
    data = load_game_data()

    _turn(sheet.powers, 2)

    assert power.effects[0].current_rank == 2
    assert effective_size(char, data) == "Huge"
    # The card is rebuilt from the model, so the new dial agrees with it.
    assert _dial_state(sheet.powers) == (2, 3, "Huge")


def test_the_dial_wakes_a_dormant_power_at_that_notch(qapp: QApplication) -> None:
    """One gesture from off to Huge, rather than on-at-full then down to Huge."""
    sheet, char, power = _size_sheet(3)
    data = load_game_data()
    sheet.powers._set_power_active(power, False)

    assert _dial_state(sheet.powers) == (0, 3, "Off")

    _turn(sheet.powers, 2)
    assert power.activated and power.effects[0].toggled_on
    assert effective_size(char, data) == "Huge"


def test_the_dial_is_a_runtime_change_that_still_marks_the_sheet_dirty(
    qapp: QApplication,
) -> None:
    """Like every other card switch — and, like them, saved with the build.

    It stays a *runtime* signal (``changed`` is for build edits, and the dial re-derives
    rather than re-costing), but where it stands is in the save now, so the sheet has to
    know it has something unwritten or closing the window would drop it.
    """
    sheet, _char, _power = _size_sheet(3)
    edits: list[int] = []
    runtime: list[int] = []
    dirtied: list[int] = []
    sheet.powers.changed.connect(lambda: edits.append(1))
    sheet.powers.runtimeChanged.connect(lambda: runtime.append(1))
    sheet.edited.connect(lambda: dirtied.append(1))

    _turn(sheet.powers, 1)

    assert edits == []
    assert runtime == [1]
    assert dirtied


def test_a_notch_picked_on_the_card_survives_a_save_and_a_reopen(
    qapp: QApplication, tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The reported bug, end to end: dial to Large, save, open the file again.

    The two halves are tested apart (the flag round-trips in ``tests/test_powers.py``,
    the gesture is above), and this is the one that says the user's actual sequence
    works — the sheet writes what the card is showing.
    """
    monkeypatch.setenv(storage.HOME_ENV_VAR, str(tmp_path))
    sheet, char, _power = _size_sheet(3)
    char.profile["hero_name"] = "Colossus"

    _turn(sheet.powers, 1)
    reopened = library.load_character(library.save_character(char))

    assert reopened.powers[0].effects[0].current_rank == 1
    assert effective_size(reopened, load_game_data()) == "Large"


def test_the_dial_stays_live_in_the_locked_sheet(qapp: QApplication) -> None:
    """How big you are standing there is a play action, not a build edit."""
    sheet, char, power = _size_sheet(3)
    data = load_game_data()
    sheet.set_locked(True)

    _turn(sheet.powers, 1)
    assert effective_size(char, data) == "Large"


def test_a_single_rank_size_power_gets_no_dial(qapp: QApplication) -> None:
    """A Growth 1's one notch *is* the card's own switch; a dial would be a second one."""
    sheet, _char, _power = _size_sheet(1)

    assert sheet.powers.findChild(_RankDial) is None


def test_a_power_that_changes_no_size_gets_no_dial_unless_it_asks(
    qapp: QApplication,
) -> None:
    sheet, _char = _sheet_with("Blast")

    assert sheet.powers.findChild(_RankDial) is None


def test_an_effect_that_asks_for_a_dial_gets_one_named_by_rank(qapp: QApplication) -> None:
    """The constructor's *Add a rank slider*: a Damage 10 can be fired at 5."""
    data = load_game_data()
    char = Character.new_default(data)
    power = Power(name="Blast", effects=[PowerEffectInstance("damage", rank=10, rank_dial=True)])
    char.powers.append(power)
    sec = _sheet_for(char).powers

    assert _dial_state(sec) == (10, 10, "Rank 10")
    _turn(sec, 5)
    assert power.effects[0].current_rank == 5
    assert _dial_state(sec) == (5, 10, "Rank 5")


def test_a_dialled_effect_forces_a_smaller_save(qapp: QApplication) -> None:
    """The point of the dial: pulling a punch really does land softer."""
    data = load_game_data()
    char = Character.new_default(data)
    power = Power(name="Blast", effects=[PowerEffectInstance("damage", rank=10, rank_dial=True)])
    char.powers.append(power)
    sec = _sheet_for(char).powers
    effect = power.effects[0]

    assert effect_roll_numbers(effect, data, char).dc == 20
    _turn(sec, 5)
    assert effect_roll_numbers(effect, data, char).dc == 15
    # And it is still worth what it was bought at — dialling down refunds nothing.
    assert effect_total_cost(effect, data, char) == 10


def test_the_card_readout_follows_the_dial(qapp: QApplication) -> None:
    """The card's Size row and the sheet's Size line must never name two sizes."""
    sheet, char, power = _size_sheet(3)
    data = load_game_data()

    _turn(sheet.powers, 1)

    row = next(r for r in effect_readout_rows(power.effects[0], data, char) if r.key == "size")
    assert row.value == "Large" == effective_size(char, data)


def test_a_dial_inside_a_switched_off_linked_group_is_a_read_out(qapp: QApplication) -> None:
    """Visible — it still says where the power is set — but not a control.

    Never ``setEnabled(False)``: nothing in this app greys a control out, so the slider
    goes transparent to the mouse and the click falls through to the card.
    """
    data = load_game_data()
    char = Character.new_default(data)
    growth = Power(name="Giant Form", effects=[PowerEffectInstance("growth", rank=3)])
    flight = Power(name="Flight", effects=[PowerEffectInstance("flight", rank=4)])
    char.powers.append(PowerGroup(mode=STRUCTURE_LINKED, children=[growth, flight]))
    sec = _sheet_for(char).powers
    sec._set_group_active(char.powers[0], False)

    slider = _dial(sec)._slider
    assert _dial_labels(sec) == ["Off", "Large", "Huge", "Gargantuan"]
    assert slider.isEnabled()
    assert slider.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)


def test_the_dial_makes_an_array_member_the_live_alternate(qapp: QApplication) -> None:
    """Turning the dial does whatever clicking the card would, then lands on that notch."""
    data = load_game_data()
    char = Character.new_default(data)
    growth = Power(name="Giant Form", effects=[PowerEffectInstance("growth", rank=3)])
    flight = Power(name="Flight", effects=[PowerEffectInstance("flight", rank=4)])
    group = PowerGroup(mode=STRUCTURE_ARRAY, children=[flight, growth], active_child_id=flight.id)
    char.powers.append(group)
    sec = _sheet_for(char).powers

    assert _dial_state(sec) == (0, 3, "Off")
    _turn(sec, 2)

    assert group.active_child_id == growth.id
    assert effective_size(char, data) == "Huge"


def test_dialling_back_to_zero_switches_the_power_off(qapp: QApplication) -> None:
    """Zero is the card's own click — the dial is a whole control, not a one-way one."""
    sheet, char, power = _size_sheet(3)
    data = load_game_data()

    _turn(sheet.powers, 2)
    assert effective_size(char, data) == "Huge"

    _turn(sheet.powers, 0)
    assert effective_size(char, data) == "Medium"
    assert not power.activated
    assert _dial_state(sheet.powers) == (0, 3, "Off")

    # And the notch is remembered, so turning it up again comes back to Huge.
    _turn(sheet.powers, 2)
    assert effective_size(char, data) == "Huge"


def test_zeroing_an_array_alternate_is_a_no_op(qapp: QApplication) -> None:
    """An array always keeps exactly one live member, so its dial must not switch off."""
    data = load_game_data()
    char = Character.new_default(data)
    growth = Power(name="Giant Form", effects=[PowerEffectInstance("growth", rank=3)])
    flight = Power(name="Flight", effects=[PowerEffectInstance("flight", rank=4)])
    group = PowerGroup(mode=STRUCTURE_ARRAY, children=[growth, flight], active_child_id=growth.id)
    char.powers.append(group)
    sec = _sheet_for(char).powers

    _turn(sec, 0)

    assert group.active_child_id == growth.id
    assert effective_size(char, data) == "Gargantuan"


def test_ranks_the_size_table_clamps_repeat_their_category(qapp: QApplication) -> None:
    """A Growth 6 on a Gargantuan hero really does spend five ranks at Awesome.

    The dial has a notch per rank rather than per rung, so it can stop wherever the
    player puts it; the label simply repeats where the table ran out.
    """
    sheet, char, power = _size_sheet(6, size="Gargantuan")
    data = load_game_data()

    assert _dial_labels(sheet.powers) == ["Off", "Colossal"] + ["Awesome"] * 5
    assert power.effects[0].current_rank is None  # standing at full rank

    _turn(sheet.powers, 0)
    assert effective_size(char, data) == "Gargantuan"  # switched off, back to its own size


def test_turning_the_dial_leaves_the_page_where_it_was(qapp: QApplication) -> None:
    """The regression: the sheet jumped away from the card that was just used.

    Every runtime setter rebuilds the whole card tree, so the block is briefly empty —
    and whatever held focus inside it is destroyed, handing focus to a widget in some
    other block, which a ``QScrollArea`` then scrolls into view. Forced here, because
    the slider itself is ``NoFocus`` precisely so it cannot happen by hand.
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

    slider = win._sheet.powers.findChildren(_RankDial)[-1]._slider
    slider.setFocus()
    for _ in range(4):
        qapp.processEvents()
    slider.setValue(2)
    for _ in range(10):
        qapp.processEvents()

    assert bar.value() == before
    assert char.powers[-1].effects[0].current_rank == 2  # and the gesture still landed


def test_the_dial_never_takes_focus(qapp: QApplication) -> None:
    """The cause-level half: the slider is destroyed by its own commit, so focus on it
    could only ever be handed to another block — which is what moved the page."""
    sheet, _char, _power = _size_sheet(3)

    assert _dial(sheet.powers)._slider.focusPolicy() == Qt.FocusPolicy.NoFocus


def test_a_drag_commits_once_on_release(qapp: QApplication) -> None:
    """A slider that wrote on every tick would delete itself under the player's thumb.

    The label tracks the drag so the notch under the handle is readable; only letting go
    reaches the section — which is what makes a rebuild-per-commit survivable at all.
    """
    sheet, _char, power = _size_sheet(3)
    dial = _dial(sheet.powers)
    committed: list[int] = []
    dial.rankPicked.connect(committed.append)

    dial._slider.setSliderDown(True)
    dial._slider.setValue(2)
    dial._slider.setValue(1)
    assert committed == []
    assert dial._value.text() == "Large"  # but the label followed the handle
    assert power.effects[0].current_rank is None  # nothing written yet

    dial._slider.setSliderDown(False)  # QAbstractSlider emits sliderReleased itself
    assert committed == [1]


# --- the Dynamic point pool -----------------------------------------------------------


def _pool_array(qapp: QApplication) -> tuple[CharacterSheet, Character, PowerGroup]:
    """A Dynamic Protection 8 (8 PP) beside a Dynamic Flight 3 (6 PP), so 8 is the pool."""

    char = Character.new_default(load_game_data())
    armour = Power(name="Force Field", effects=[PowerEffectInstance("protection", rank=8)])
    flight = Power(name="Flight", effects=[PowerEffectInstance("flight", rank=3)])
    group = PowerGroup(mode=STRUCTURE_ARRAY, children=[armour, flight])
    armour.dynamic = flight.dynamic = True
    char.powers.append(group)
    return _sheet_for(char), char, group


def _share_dials(sec: PowersSection) -> list[_RankDial]:
    """Every Dynamic-share slider on the board — the split's only control now.

    A share dial's label always prices the notch in points, whatever its caption says:
    a Dynamic member's slider *is* its rank slider, so it is captioned "Size" on a Growth
    and "Rank" on a Flight rather than announcing the machinery behind it.
    """

    return [
        d
        for d in sec._list_host.findChildren(_RankDial)
        if any("PP" in text for text in d._labels.values())
    ]


def test_a_share_dial_appears_only_on_a_dynamic_member_of_an_array(
    qapp: QApplication,
) -> None:
    sheet, _char, group = _pool_array(qapp)
    group.children[0].dynamic_points = 4
    group.children[1].dynamic_points = 2
    sheet.powers._rebuild_list()
    assert len(_share_dials(sheet.powers)) == 2

    # Nothing Dynamic, nothing to share.
    sheet.powers._set_group_mode(group, STRUCTURE_ARRAY)
    assert _share_dials(sheet.powers) == []

    # Nor outside an array: the pool is an array's, and only an array has one.
    sheet.powers._set_group_mode(group, MODE_ARRAY_DYNAMIC)
    sheet.powers._set_group_mode(group, STRUCTURE_LINKED)
    assert _share_dials(sheet.powers) == []


def test_the_share_dial_survives_the_lock_because_it_is_a_free_action(
    qapp: QApplication,
) -> None:
    """Deciding the split happens at the table (p101), so it is not build chrome."""

    sheet, _char, group = _pool_array(qapp)
    group.children[0].dynamic_points = 4
    group.children[1].dynamic_points = 2
    sheet.set_locked(True)
    assert len(_share_dials(sheet.powers)) == 2


def test_the_share_dials_groove_is_its_whole_ladder_however_little_is_left(
    qapp: QApplication,
) -> None:
    """The range is the member's own ranks; only the reachable ceiling follows the pool.

    Bounding the range instead made a member's groove a function of everyone else's
    allocation, so moving one member visibly jumped another's unchanged handle.
    """

    sheet, _char, group = _pool_array(qapp)
    armour, flight = group.children
    sec = sheet.powers

    # Protection 8 costs 8, so its ladder is a point a rank whatever the pool has left.
    assert sec._share_steps(armour, 8, 0) == [0, 1, 2, 3, 4, 5, 6, 7, 8]
    assert sec._share_steps(armour, 8, 0) == sec._share_steps(armour, 8, 0)

    # Flight 3 costs 6, so two points a rank — and the ceiling is what says how far up.
    assert sec._share_steps(flight, 6, 0) == [0, 2, 4, 6]
    assert sec._share_index([0, 2, 4, 6], 3) == 1  # 3 points can only pay for the 2 notch
    assert sec._share_index([0, 2, 4, 6], 0) == 0

    # A stored share that is not a notch is shown rather than rounded away, or the pool
    # would go on charging a point the card had stopped displaying.
    assert sec._share_steps(flight, 6, 3) == [0, 2, 3, 4, 6]


def test_a_member_whose_siblings_hold_the_pool_keeps_its_slider(
    qapp: QApplication,
) -> None:
    """It used to vanish outright — not disabled, absent — with no way back."""

    sheet, _char, group = _pool_array(qapp)
    armour, flight = group.children
    armour.dynamic_points = 8  # the whole pool
    sheet.powers._rebuild_list()

    dials = _share_dials(sheet.powers)
    assert len(dials) == 2
    # The one with nothing left is still there, ended at the single notch it can reach.
    starved = dials[1]
    assert starved._slider.maximum() == 0
    assert flight.dynamic_points is None

    # Hand a point back and the starved slider gains exactly one division; hand two back
    # and it gains another. The right-hand end of the groove is what the pool has left.
    armour.dynamic_points = 6
    sheet.powers._rebuild_list()
    assert _share_dials(sheet.powers)[1]._slider.maximum() == 1
    armour.dynamic_points = 4
    sheet.powers._rebuild_list()
    assert _share_dials(sheet.powers)[1]._slider.maximum() == 2


def test_moving_a_share_dial_moves_every_members_ranks(qapp: QApplication) -> None:
    data = load_game_data()
    sheet, char, group = _pool_array(qapp)
    armour, flight = group.children
    assert power_trait_bonuses(char, data)["resistance"]["TOUGHNESS"].amount == 8

    sheet.powers._on_share_dialled(armour, [0, 2, 4, 6, 8], 2)
    sheet.powers._on_share_dialled(flight, [0, 2, 4, 6], 2)

    assert armour.dynamic_points == 4 and flight.dynamic_points == 4
    # Half the pool, half the Toughness - and the Flight is running at the same time.
    assert power_trait_bonuses(char, data)["resistance"]["TOUGHNESS"].amount == 4
    assert [p.name for p in live_powers(char.powers)] == ["Force Field", "Flight"]


def test_a_share_dialled_to_nothing_stores_nothing_at_all(qapp: QApplication) -> None:
    sheet, _char, group = _pool_array(qapp)
    group.children[0].dynamic_points = 4

    sheet.powers._on_share_dialled(group.children[0], [0, 2, 4, 6, 8], 0)

    # A zero share and no share behave alike, so the file keeps the quieter one.
    assert group.children[0].dynamic_points is None
    assert "dynamic_points" not in group.children[0].to_dict()


def test_the_last_share_dialled_to_nothing_switches_its_member_off(
    qapp: QApplication,
) -> None:
    """A Growth parked on "Off" used to come straight back on, and grow the character.

    Handing back the *last* share returns the array to its selected alternate at full
    rank — which is what an array saved before the pool existed does on load — so the
    member the player had just dialled to zero was live again: a Diminutive character
    read as Medium under a slider saying the power was off. Zero is off on this dial as
    it is on the rank dial it replaces, whatever the array then does with the member.
    """

    data = load_game_data()
    char = Character.new_default(data)
    char.characteristics["size"] = "Diminutive"
    growth = Power(name="Giant Form", effects=[PowerEffectInstance("growth", rank=6)])
    reach = Power(name="Long Arms", effects=[PowerEffectInstance("elongation", rank=3)])
    growth.dynamic = reach.dynamic = True
    growth.dynamic_points = reach.dynamic_points = 3
    group = PowerGroup(mode=STRUCTURE_ARRAY, children=[growth, reach])
    char.powers.append(group)
    sec = _sheet_for(char).powers
    assert effective_size(char, data) == "Medium"  # Diminutive, plus the three ranks held

    size_dial, reach_dial = _share_dials(sec)
    assert size_dial.caption() == "Size"
    reach_dial._slider.setValue(0)
    _share_dials(sec)[0]._slider.setValue(0)  # the card tree is rebuilt under each commit

    assert effective_size(char, data) == "Diminutive"
    assert not growth.activated
    assert growth.dynamic_points is None  # the file still keeps the quieter of the two
    # The card says so too, rather than being drawn as the array's live alternate.
    assert sec._node_is_inactive(growth, group, sec._activation_role(growth, group))

    # ...and the same handle pushed back up wakes it at the notch asked for.
    _share_dials(sec)[0]._slider.setValue(4)
    assert growth.activated
    assert effective_size(char, data) == "Large"  # Diminutive, plus four ranks


def test_a_powers_own_dynamic_effects_get_share_dials_too(qapp: QApplication) -> None:
    """An array exists at two levels, and so does its pool — so the control does too."""

    char = Character.new_default(load_game_data())
    blast = PowerEffectInstance("damage", rank=10)
    fly = PowerEffectInstance("flight", rank=5)
    power = Power(name="Fire Control", structure=STRUCTURE_ARRAY, effects=[blast, fly])
    char.powers.append(power)
    sheet = _sheet_for(char)
    assert _share_dials(sheet.powers) == []

    blast.dynamic = fly.dynamic = True
    blast.dynamic_points = 6
    fly.dynamic_points = 4
    sheet.powers._rebuild_list()
    assert len(_share_dials(sheet.powers)) == 2

    # The share is written to the effect, not to its dialled rank.
    sheet.powers._on_share_dialled(fly, [0, 2, 4, 6, 8, 10], 1)
    assert fly.dynamic_points == 2
    assert fly.current_rank is None
    # The book's own worked example: a Flight 5 costing 10, given 2, runs at 1 rank.
    assert effect_current_rank(fly, load_game_data(), char) == 1


def test_a_split_array_dims_the_members_that_are_not_running(qapp: QApplication) -> None:
    sheet, char, group = _pool_array(qapp)
    armour, flight = group.children
    sec = sheet.powers

    # Untouched: the selected member is lit and its sibling is dimmed.
    assert sec._node_is_inactive(armour, group, "select") is False
    assert sec._node_is_inactive(flight, group, "select") is True

    armour.dynamic_points = 4
    flight.dynamic_points = 4
    assert sec._node_is_inactive(armour, group, "select") is False
    assert sec._node_is_inactive(flight, group, "select") is False


def test_a_split_array_stops_arming_a_click_that_would_do_nothing(qapp: QApplication) -> None:
    sheet, _char, group = _pool_array(qapp)
    sec = sheet.powers
    armour = group.children[0]

    # Unsplit, a member is the array's selector and says so.
    assert sec._activation_role(armour, group) == "select"
    card = sec._render_node(armour, group)
    assert card.is_clickable()
    assert "siblings switch off" in card.toolTip()

    # Split, the pool decides who is running: the click is not armed at all — it used to
    # move active_child_id silently with nothing visible happening — but the card still
    # explains why it has stopped being a control.
    armour.dynamic_points = 4
    assert sec._activation_role(armour, group) == ""
    card = sec._render_node(armour, group)
    assert not card.is_clickable()
    assert "all running at once" in card.toolTip()
    assert "siblings switch off" not in card.toolTip()


def test_a_split_array_still_dims_by_what_is_running_with_no_role_left(
    qapp: QApplication,
) -> None:
    sheet, _char, group = _pool_array(qapp)
    sec = sheet.powers
    armour, flight = group.children

    # Taking the click away must not take the dimming with it: an ordinary member of a
    # split array is off, and has to look it.
    flight.dynamic_points = 4
    assert sec._activation_role(armour, group) == ""
    assert sec._node_is_inactive(armour, group, "") is True
    assert sec._node_is_inactive(flight, group, "") is False

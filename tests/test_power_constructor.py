"""The Power Constructor window builds and mutates a Power via its drop seams.

Real drag-and-drop events are unreliable headless, so these drive the public
mutation methods the drop handlers delegate to (``add_effect`` / ``attach_modifier``).
"""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QApplication

from mm_companion.core.data_loader import load_game_data
from mm_companion.ui.character_sheet import CharacterSheet
from mm_companion.ui.power_constructor import PowerConstructorWindow


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_dropping_an_effect_adds_a_card_and_costs(qapp: QApplication) -> None:
    window = PowerConstructorWindow(load_game_data())
    card = window.canvas.add_effect("damage")
    assert len(window.canvas.cards) == 1
    assert window.power.effects[0].effect_id == "damage"
    assert window._cost.text() == "Total cost: 1 PP"  # Damage rank 1

    card._rank.setValue(8)
    assert window.power.effects[0].rank == 8
    assert window._cost.text() == "Total cost: 8 PP"


def test_attaching_a_modifier_updates_model_and_cost(qapp: QApplication) -> None:
    window = PowerConstructorWindow(load_game_data())
    card = window.canvas.add_effect("damage")
    card._rank.setValue(8)
    card.attach_modifier("ranged")  # per-rank extra: (1 + 1) * 8 = 16

    assert window.power.effects[0].extras[0].modifier_id == "ranged"
    assert window._cost.text() == "Total cost: 16 PP"


def test_ranked_modifier_chip_has_a_rank_spin_box_that_drives_cost(qapp: QApplication) -> None:
    from PySide6.QtWidgets import QSpinBox

    window = PowerConstructorWindow(load_game_data())
    card = window.canvas.add_effect("damage")
    card._rank.setValue(5)
    card.attach_modifier("accurate")  # ranked flat extra

    chip = card._chips[0]
    spin = chip.findChild(QSpinBox)
    assert spin is not None  # ranked modifiers expose a rank spin box
    spin.setValue(3)

    assert window.power.effects[0].extras[0].rank == 3
    assert window._cost.text() == "Total cost: 8 PP"  # 1*5 + 1*3


def test_unranked_modifier_chip_has_no_rank_spin_box(qapp: QApplication) -> None:
    from PySide6.QtWidgets import QSpinBox

    window = PowerConstructorWindow(load_game_data())
    card = window.canvas.add_effect("damage")
    card.attach_modifier("ranged")  # per-rank, not ranked

    assert card._chips[0].findChild(QSpinBox) is None


def test_extras_and_flaws_groups_reveal_and_hide_with_their_chips(qapp: QApplication) -> None:
    window = PowerConstructorWindow(load_game_data())
    card = window.canvas.add_effect("damage")

    # Both groups hidden until something is attached.
    assert not card._extras_group.isVisibleTo(card)
    assert not card._flaws_group.isVisibleTo(card)

    card.attach_modifier("ranged")  # an extra
    assert card._extras_group.isVisibleTo(card)
    assert not card._flaws_group.isVisibleTo(card)

    card.attach_modifier("limited")  # a flaw
    assert card._flaws_group.isVisibleTo(card)

    # Removing the only extra hides the Extras group again; Flaws stays.
    card._remove_chip(card._chips[0])
    assert not card._extras_group.isVisibleTo(card)
    assert card._flaws_group.isVisibleTo(card)


def test_removing_an_effect_clears_it(qapp: QApplication) -> None:
    window = PowerConstructorWindow(load_game_data())
    card = window.canvas.add_effect("damage")
    window.canvas._remove_card(card)
    assert window.canvas.cards == []
    assert window.power.effects == []
    assert window._cost.text() == "Total cost: 0 PP"


def _stat(window: PowerConstructorWindow, effect_index: int, key: str):
    """The rendered game-term row for one effect field (or ``None`` if absent)."""
    rows = window._terms.effect_rows[effect_index]
    return next((r for r in rows if r.key == key), None)


def test_game_terms_table_tints_the_fields_a_modifier_changes(qapp: QApplication) -> None:
    window = PowerConstructorWindow(load_game_data())

    card = window.canvas.add_effect("affliction")
    range_row = _stat(window, 0, "range")
    assert range_row.value == "Close"
    assert range_row.change == ""  # untouched — no tint

    card.attach_modifier("ranged")  # an extra: overrides range to Ranged
    range_row = _stat(window, 0, "range")
    assert range_row.value == "Ranged"
    assert range_row.base == "Close"
    assert range_row.change == "better"  # improved — tinted green


def test_effect_config_combos_write_choices_to_the_model(qapp: QApplication) -> None:
    from PySide6.QtWidgets import QComboBox

    window = PowerConstructorWindow(load_game_data())
    card = window.canvas.add_effect("affliction")
    combos = card.findChildren(QComboBox)
    assert combos  # Affliction exposes configurable qualities as selects

    resistance = next(c for c in combos if c.findData("Will") >= 0)
    resistance.setCurrentIndex(resistance.findData("Will"))

    assert window.power.effects[0].config["resistance"] == "Will"
    # The chosen resistance now carries the numeric save DC (10 + rank 1).
    assert _stat(window, 0, "resistance").value == "Will vs. DC 11"


def test_degrees_are_single_select_until_extra_condition(qapp: QApplication) -> None:
    from PySide6.QtWidgets import QCheckBox, QComboBox

    window = PowerConstructorWindow(load_game_data())
    card = window.canvas.add_effect("affliction")

    # By default the degrees are single-select combos and there are no check boxes.
    assert len(card.findChildren(QComboBox)) == 4  # resistance + 3 degrees
    assert card.findChildren(QCheckBox) == []

    card.attach_modifier("extra_condition")  # the Affliction-only gating extra
    assert card.findChildren(QCheckBox)  # degrees are now multiselect
    assert len(card.findChildren(QComboBox)) == 1  # only resistance stays a combo


def test_extra_condition_enables_two_conditions_per_degree(qapp: QApplication) -> None:
    from PySide6.QtWidgets import QCheckBox

    window = PowerConstructorWindow(load_game_data())
    card = window.canvas.add_effect("affliction")
    card.attach_modifier("extra_condition")

    boxes = {b.text(): b for b in card.findChildren(QCheckBox)}
    boxes["Dazed"].setChecked(True)
    boxes["Vulnerable"].setChecked(True)

    assert window.power.effects[0].config["degree1"] == ["dazed", "vulnerable"]
    assert _stat(window, 0, "degree1").value == "Dazed + Vulnerable"


def test_removing_extra_condition_collapses_the_degree_back_to_one(qapp: QApplication) -> None:
    window = PowerConstructorWindow(load_game_data())
    card = window.canvas.add_effect("affliction")
    card.attach_modifier("extra_condition")
    card.instance.config["degree1"] = ["dazed", "vulnerable"]

    card._remove_chip(card._chips[0])  # drop Extra Condition
    assert card.instance.config["degree1"] == "dazed"  # collapsed to a single value
    assert _stat(window, 0, "degree1").value == "Dazed"  # no longer "Dazed + Vulnerable"


def test_effect_without_config_has_no_combos(qapp: QApplication) -> None:
    from PySide6.QtWidgets import QComboBox

    window = PowerConstructorWindow(load_game_data())
    card = window.canvas.add_effect("damage")  # Damage has no config fields
    assert card.findChildren(QComboBox) == []


def test_effect_specific_menu_lists_only_this_effects_modifiers(qapp: QApplication) -> None:
    window = PowerConstructorWindow(load_game_data())
    card = window.canvas.add_effect("damage")

    # The button is shown because Damage has effect-specific modifiers.
    assert card._specific_button.isVisibleTo(card)
    card._populate_specific_menu()
    labels = {a.text() for a in card._specific_menu.actions() if not a.isSeparator()}
    assert "Strength-Based" in labels  # Damage-specific extra
    assert "Rocket" not in labels  # a Flight-specific flaw, not offered here


def test_effect_without_specific_modifiers_hides_the_menu_button(qapp: QApplication) -> None:
    window = PowerConstructorWindow(load_game_data())
    # Move Object relies solely on the general pool (no effect_modifiers entry).
    card = window.canvas.add_effect("move_object")
    assert not card._specific_button.isVisibleTo(card)


def test_menu_attaches_an_effect_specific_modifier_and_disables_it(qapp: QApplication) -> None:
    window = PowerConstructorWindow(load_game_data())
    card = window.canvas.add_effect("flight")
    card._rank.setValue(6)

    card.attach_modifier("rocket")  # a Flight-specific flaw (-1/rank)
    assert window.power.effects[0].flaws[0].modifier_id == "rocket"
    assert window._cost.text() == "Total cost: 6 PP"  # 6 * (2 - 1)

    # Reopening the menu greys out the already-attached modifier.
    card._populate_specific_menu()
    rocket = next(a for a in card._specific_menu.actions() if a.text() == "Rocket")
    assert not rocket.isEnabled()


def test_palette_search_filters_bricks_instantly(qapp: QApplication) -> None:
    window = PowerConstructorWindow(load_game_data())
    search, bricks = window._search_tabs["effects"]

    search.setText("damage")
    shown = [b for b in bricks if not b.isHidden()]
    assert shown  # at least the Damage brick
    assert all("damage" in b.search_key for b in shown)
    assert any(b.isHidden() for b in bricks)  # non-matches are hidden

    search.clear()  # clearing restores the whole list
    assert all(not b.isHidden() for b in bricks)


def test_palette_search_matches_names_not_cost_text(qapp: QApplication) -> None:
    window = PowerConstructorWindow(load_game_data())
    search, bricks = window._search_tabs["effects"]

    # A digit only occurs in the shared cost text ("1 per rank"), never in a name,
    # so it must hide everything rather than matching every brick.
    search.setText("1")
    assert all(b.isHidden() for b in bricks)

    # A single letter that does appear in names filters to just those.
    search.setText("a")
    shown = [b for b in bricks if not b.isHidden()]
    assert shown
    assert all("a" in b.search_key for b in shown)


def test_palette_search_is_case_insensitive_and_per_tab(qapp: QApplication) -> None:
    window = PowerConstructorWindow(load_game_data())
    effects_search, effect_bricks = window._search_tabs["effects"]
    _, extra_bricks = window._search_tabs["extras"]

    effects_search.setText("HEAL")  # upper-case still matches "Healing"
    assert any(not b.isHidden() for b in effect_bricks)
    # Searching the Effects tab leaves the Extras tab's bricks untouched.
    assert all(not b.isHidden() for b in extra_bricks)


def test_name_and_description_write_to_model(qapp: QApplication) -> None:
    window = PowerConstructorWindow(load_game_data())
    window._name.setText("Fire Blast")
    window._description.setPlainText("whoosh")
    assert window.power.name == "Fire Blast"
    assert window.power.description == "whoosh"


def test_mode_bar_appears_only_with_two_or_more_effects(qapp: QApplication) -> None:
    window = PowerConstructorWindow(load_game_data())
    bar = window.canvas._mode_bar

    card = window.canvas.add_effect("damage")
    assert not bar.isVisibleTo(window.canvas)  # single effect: no switch

    window.canvas.add_effect("affliction")
    assert bar.isVisibleTo(window.canvas)  # a second effect reveals it

    window.canvas._remove_card(card)  # back to one effect
    assert not bar.isVisibleTo(window.canvas)


def test_switching_to_array_recomputes_cost_and_badges_cards(qapp: QApplication) -> None:
    from mm_companion.core.powers import STRUCTURE_ARRAY

    window = PowerConstructorWindow(load_game_data())
    base = window.canvas.add_effect("damage")
    base._rank.setValue(8)  # 8 PP, the costliest → base
    alt = window.canvas.add_effect("affliction")
    alt._rank.setValue(2)  # 2 PP alternate

    # Independent by default: costs sum.
    assert window._cost.text() == "Total cost: 10 PP"

    window.canvas._mode_bar.changed.emit(STRUCTURE_ARRAY)
    assert window.power.structure == STRUCTURE_ARRAY
    assert window._cost.text() == "Total cost: 9 PP"  # 8 base + 1 flat alternate
    assert base._role_badge.text() == "Base"
    assert alt._role_badge.text().startswith("Alternate")


def test_array_base_badge_follows_the_costliest_effect(qapp: QApplication) -> None:
    from mm_companion.core.powers import STRUCTURE_ARRAY

    window = PowerConstructorWindow(load_game_data())
    first = window.canvas.add_effect("damage")
    first._rank.setValue(3)
    second = window.canvas.add_effect("damage")
    second._rank.setValue(8)
    window.canvas._mode_bar.changed.emit(STRUCTURE_ARRAY)

    assert second._role_badge.text() == "Base"  # the rank-8 effect is the base
    first._rank.setValue(10)  # now the first effect is costliest
    assert first._role_badge.text() == "Base"
    assert second._role_badge.text().startswith("Alternate")


def test_dropping_below_two_effects_resets_structure_to_independent(qapp: QApplication) -> None:
    from mm_companion.core.powers import STRUCTURE_ARRAY, STRUCTURE_INDEPENDENT

    window = PowerConstructorWindow(load_game_data())
    keep = window.canvas.add_effect("damage")
    drop = window.canvas.add_effect("affliction")
    window.canvas._mode_bar.changed.emit(STRUCTURE_ARRAY)
    assert window.power.structure == STRUCTURE_ARRAY

    window.canvas._remove_card(drop)
    assert window.power.structure == STRUCTURE_INDEPENDENT  # lone effect can't be an array
    assert keep._role_badge.text() == ""  # badge cleared


def test_linked_badges_every_card(qapp: QApplication) -> None:
    from mm_companion.core.powers import STRUCTURE_LINKED

    window = PowerConstructorWindow(load_game_data())
    a = window.canvas.add_effect("damage")
    b = window.canvas.add_effect("affliction")
    window.canvas._mode_bar.changed.emit(STRUCTURE_LINKED)

    assert a._role_badge.text() == "Linked"
    assert b._role_badge.text() == "Linked"


def test_powers_section_launches_and_locks(qapp: QApplication) -> None:
    sheet = CharacterSheet(load_game_data())
    button = sheet.powers._add_button
    assert button.isVisibleTo(sheet.powers)
    sheet.set_locked(True)
    assert not button.isVisibleTo(sheet.powers)

"""The Power Constructor window builds and mutates a Power via its drop seams.

Real drag-and-drop events are unreliable headless, so these drive the public
mutation methods the drop handlers delegate to (``add_effect`` / ``attach_modifier``).
"""

from __future__ import annotations

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QLabel

from mm_companion.core.character import Character
from mm_companion.core.data_loader import load_game_data
from mm_companion.core.powers import (
    STRUCTURE_INDEPENDENT,
    STRUCTURE_LINKED,
    Power,
    PowerEffectInstance,
)
from mm_companion.core.rules import effect_total_cost, power_total_cost
from mm_companion.ui.character_sheet import CharacterSheet
from mm_companion.ui.power_constructor import PowerConstructorWindow


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


def _pl10_character() -> Character:
    """A blank PL 10 character (no attack bonus) — the context the constructor needs
    to check Power Level caps."""
    return Character.new_default(load_game_data())


def test_attack_skill_combo_links_a_combat_focus_to_the_effect(qapp: QApplication) -> None:
    char = _pl10_character()
    char.abilities["ATK"] = 3
    char.focuses["Close Combat"] = ["Blades"]
    char.skill_ranks["Close Combat::Blades"] = 4  # focus total = ATK 3 + 4 = 7
    window = PowerConstructorWindow(load_game_data(), character=char)

    card = window.canvas.add_effect("damage")
    card._rank.setValue(8)
    # The picker lives on the effect card and is off until "Use attack skill" is ticked.
    assert card._attack_skill_check is not None
    assert not card._attack_skill_check.isChecked()
    assert card.instance.attack_skill == ""

    card._attack_skill_check.setChecked(True)
    index = card._attack_skill.findData("Close Combat::Blades")
    assert index >= 0
    card._attack_skill.setCurrentIndex(index)
    assert card.instance.attack_skill == "Close Combat::Blades"

    # The game-term attack line now reads the focus total, replacing the bare Attack.
    rows = {r.key: r for r in window._terms.effect_rows[0]}
    assert rows["check"].value == "7 vs. Defense"

    # Unticking drops the link, so the roll falls back to the bare Attack (3).
    card._attack_skill_check.setChecked(False)
    assert card.instance.attack_skill == ""
    rows = {r.key: r for r in window._terms.effect_rows[0]}
    assert rows["check"].value == "3 vs. Defense"


def test_attack_skill_combo_absent_without_combat_focuses(qapp: QApplication) -> None:
    # A character with no Close/Ranged Combat focuses has nothing to link, so the
    # per-effect picker isn't built.
    window = PowerConstructorWindow(load_game_data(), character=_pl10_character())
    card = window.canvas.add_effect("damage")
    assert card._attack_skill is None
    assert card._attack_skill_check is None


def _char_with_focus() -> Character:
    char = _pl10_character()
    char.focuses["Close Combat"] = ["Blades"]
    char.skill_ranks["Close Combat::Blades"] = 4
    return char


def test_attack_skill_row_hidden_for_a_non_attack_effect(qapp: QApplication) -> None:
    # Protection resolves with no attack roll, so there's nothing to reskill — the
    # row is built (the wielder has a focus) but stays hidden.
    window = PowerConstructorWindow(load_game_data(), character=_char_with_focus())
    card = window.canvas.add_effect("protection")
    assert card._attack_skill_check is not None
    assert card._attack_skill_row.isHidden()


def test_attack_extra_shows_the_attack_skill_row_on_a_non_attacking_effect(
    qapp: QApplication,
) -> None:
    # Flight makes no attack roll, so there is nothing to reskill — until the Attack
    # extra gives it one. Removing the extra takes the row away again.
    window = PowerConstructorWindow(load_game_data(), character=_char_with_focus())
    card = window.canvas.add_effect("flight")
    assert card._attack_skill_row.isHidden()

    card.attach_modifier("attack")
    assert not card._attack_skill_row.isHidden()

    card._remove_chip(card._chips[-1])
    assert card._attack_skill_row.isHidden()


def test_attaching_an_already_implicit_modifier_is_a_no_op(qapp: QApplication) -> None:
    # Damage already carries the Attack extra implicitly, so dropping a second copy on
    # it must change nothing — no chip, no selection, no extra cost.
    data = load_game_data()
    window = PowerConstructorWindow(data, character=_char_with_focus())
    card = window.canvas.add_effect("damage")
    before = effect_total_cost(card.instance, data)

    card.attach_modifier("attack")

    assert card._chips == []
    assert card.instance.extras == []
    assert effect_total_cost(card.instance, data) == before


def test_the_palette_offers_the_standard_configurations(qapp: QApplication) -> None:
    data = load_game_data()
    window = PowerConstructorWindow(data)
    _search, bricks = window._search_tabs["configurations"]
    assert len(bricks) == len(data.configurations)
    assert "blast" in {b._payload for b in bricks}


def test_dropping_a_configuration_builds_it_and_titles_the_power(qapp: QApplication) -> None:
    data = load_game_data()
    window = PowerConstructorWindow(data)

    cards = window.canvas.add_configuration("blast")

    assert len(cards) == 1
    assert [m.modifier_id for m in cards[0].instance.extras] == ["ranged"]
    assert window._name.text() == "Blast"
    cards[0].instance.rank = 8
    assert power_total_cost(window.power, data) == 16


def test_a_multi_effect_configuration_arrives_with_its_structure(qapp: QApplication) -> None:
    data = load_game_data()
    window = PowerConstructorWindow(data)

    cards = window.canvas.add_configuration("berserker_rage")

    assert len(cards) == 2
    assert window.power.structure == STRUCTURE_LINKED


def test_a_configuration_drop_leaves_an_existing_build_alone(qapp: QApplication) -> None:
    data = load_game_data()
    window = PowerConstructorWindow(data)
    window._name.setText("Sunfire Lance")
    window.canvas.add_effect("damage")

    window.canvas.add_configuration("berserker_rage")

    # Appended, not substituted: the player's own name and structure survive, and a
    # Linked configuration does not silently relink a power they set up themselves.
    assert window._name.text() == "Sunfire Lance"
    assert window.power.structure == STRUCTURE_INDEPENDENT
    assert len(window.power.effects) == 3


def test_an_unknown_configuration_id_adds_nothing(qapp: QApplication) -> None:
    window = PowerConstructorWindow(load_game_data())
    assert window.canvas.add_configuration("no-such-configuration") == []
    assert window.power.effects == []


def test_duplicate_attach_is_a_no_op_only_when_the_copies_are_indistinguishable(
    qapp: QApplication,
) -> None:
    data = load_game_data()
    window = PowerConstructorWindow(data, character=_char_with_focus())
    card = window.canvas.add_effect("damage")

    # Penetrating carries no config, so a second copy would change no game term and only
    # double-charge the power.
    card.attach_modifier("penetrating")
    cost = effect_total_cost(card.instance, data)
    card.attach_modifier("penetrating")
    assert [s.modifier_id for s in card.instance.extras] == ["penetrating"]
    assert effect_total_cost(card.instance, data) == cost

    # Limited carries a free-text circumstance, so each copy means something different
    # and taking it twice is legitimate.
    card.attach_modifier("limited")
    card.attach_modifier("limited")
    assert [s.modifier_id for s in card.instance.flaws] == ["limited", "limited"]


def test_perception_range_hides_the_attack_skill_row(qapp: QApplication) -> None:
    # Damage rolls to hit, so the row shows; a Perception-Range extra makes it auto-hit,
    # so the row hides — and removing the extra restores it.
    window = PowerConstructorWindow(load_game_data(), character=_char_with_focus())
    card = window.canvas.add_effect("damage")
    assert not card._attack_skill_row.isHidden()

    card.attach_modifier("perception_range")
    assert card._attack_skill_row.isHidden()

    chip = card._chips[-1]
    card._remove_chip(chip)
    assert not card._attack_skill_row.isHidden()


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


def test_strength_based_chip_amount_spin_box_is_fixed_and_pinned(
    qapp: QApplication,
) -> None:
    from PySide6.QtWidgets import QSpinBox

    from mm_companion.ui.power_constructor import STRENGTH_AMOUNT_MAX

    char = _pl10_character()
    char.abilities["STR"] = 6
    window = PowerConstructorWindow(load_game_data(), character=char)
    card = window.canvas.add_effect("damage")
    card.attach_modifier("strength_based")

    chip = card._chips[0]
    spin = chip.findChild(QSpinBox)
    assert spin is not None
    # The ceiling is fixed (well above Strength), not the wielder's Strength.
    assert spin.maximum() == STRENGTH_AMOUNT_MAX
    # Seeded at — and stored as — the wielder's current Strength, so it's a fixed cost basis.
    assert spin.value() == 6
    assert window.power.effects[0].extras[0].config["amount"] == 6

    spin.setValue(2)  # pay for only part of Strength
    assert window.power.effects[0].extras[0].config["amount"] == 2

    spin.setValue(6)  # always pinned now — never cleared back to dynamic tracking
    assert window.power.effects[0].extras[0].config["amount"] == 6

    # The amount may be set above the wielder's actual Strength (up to the ceiling).
    spin.setValue(20)
    assert window.power.effects[0].extras[0].config["amount"] == 20


def test_unranked_modifier_chip_has_no_rank_spin_box(qapp: QApplication) -> None:
    from PySide6.QtWidgets import QSpinBox

    window = PowerConstructorWindow(load_game_data())
    card = window.canvas.add_effect("damage")
    card.attach_modifier("ranged")  # per-rank, not ranked

    # The rank spin is the one wearing the "×" prefix. A per-rank modifier does carry
    # the rank-band pair, but those are hidden until the band is asked for.
    spins = card._chips[0].findChildren(QSpinBox)
    assert [s for s in spins if s.prefix() == "×"] == []
    assert all(not s.isVisibleTo(card._chips[0]) for s in spins)


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
    assert len(card.findChildren(QComboBox)) == 5  # resistance + overcomeBy + 3 degrees
    assert card.findChildren(QCheckBox) == []

    card.attach_modifier("extra_condition")  # the Affliction-only gating extra
    assert card.findChildren(QCheckBox)  # all three degrees are now multiselect
    # only resistance and overcomeBy stay single-select combos
    assert len(card.findChildren(QComboBox)) == 2


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
    card = window.canvas.add_effect("damage")  # Damage's only config is a checkbox
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
    # Speed relies solely on the general pool (no effect_modifiers entry).
    card = window.canvas.add_effect("speed")
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


def test_effects_palette_is_grouped_by_type_with_headers(qapp: QApplication) -> None:
    window = PowerConstructorWindow(load_game_data())
    _, bricks = window._search_tabs["effects"]

    # Every effect still shows up (the flat seam is the union of all sections)...
    assert len(bricks) == 42
    # ...and the section headers (a superset of the effect types) are present.
    headers = {lbl.text() for lbl in window.findChildren(QLabel)}
    assert {"Attack", "Movement", "Sensory"} <= headers


def test_search_hides_empty_section_headers(qapp: QApplication) -> None:
    window = PowerConstructorWindow(load_game_data())
    search, _ = window._search_tabs["effects"]

    def header(text: str) -> QLabel:
        return next(lbl for lbl in window.findChildren(QLabel) if lbl.text() == text)

    search.setText("damage")  # an Attack effect, so only the Attack header survives
    assert not header("Attack").isHidden()
    assert header("Movement").isHidden()  # no Movement match → its header hides

    search.clear()
    assert not header("Movement").isHidden()  # cleared search brings every header back


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


def test_save_button_emits_finished_power_and_closes(qapp: QApplication) -> None:
    window = PowerConstructorWindow(load_game_data())
    window._name.setText("Fire Blast")
    card = window.canvas.add_effect("damage")
    card._rank.setValue(8)

    saved: list = []
    window.powerSaved.connect(saved.append)
    window._save_power()

    assert saved and saved[0] is window.power
    assert saved[0].name == "Fire Blast"
    assert not window.isVisible()  # saving closes the window


def test_save_button_rejects_an_empty_power(qapp: QApplication, monkeypatch) -> None:
    from PySide6.QtWidgets import QMessageBox

    window = PowerConstructorWindow(load_game_data())
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: None)

    saved: list = []
    window.powerSaved.connect(saved.append)
    window._save_power()  # no effects on the canvas

    assert saved == []  # nothing handed off


def test_saved_power_lands_on_the_sheet_and_reports_change(qapp: QApplication) -> None:
    sheet = CharacterSheet(load_game_data())
    changes: list = []
    sheet.powers.changed.connect(lambda: changes.append(True))

    sheet.powers._open_constructor()
    window = sheet.powers._windows[0]
    window._name.setText("Fire Blast")
    window.canvas.add_effect("damage")  # a rank-1 Damage effect
    window._save_power()

    # The power is stored on the shared model and the section reports the change,
    # so the sheet recomputes spent points and the window is dropped from the list.
    assert [p.name for p in sheet.character.powers] == ["Fire Blast"]
    assert changes
    assert sheet.powers._windows == []


def test_loaded_powers_repopulate_the_section(qapp: QApplication) -> None:
    from mm_companion.core.character import Character

    data = load_game_data()
    character = Character.new_default(data)
    character.powers.append(
        Power(name="Fire Blast", effects=[PowerEffectInstance(effect_id="damage", rank=8)])
    )

    sheet = CharacterSheet(data, character)
    assert not sheet.powers._empty.isVisibleTo(sheet.powers)  # not the empty state
    labels = [lbl.text() for lbl in sheet.powers._list_host.findChildren(QLabel)]
    assert "Fire Blast" in labels
    assert "8 PP" in labels


def test_pl_warning_appears_only_when_a_power_breaks_a_cap(qapp: QApplication) -> None:
    window = PowerConstructorWindow(load_game_data(), character=_pl10_character())
    card = window.canvas.add_effect("damage")

    card._rank.setValue(20)  # exactly at the PL 10 cap of 20 (no attack bonus)
    assert not window._warning.isVisibleTo(window)

    card._rank.setValue(25)  # over the cap
    assert window._warning.isVisibleTo(window)
    assert "rank 25" in window._warning.toolTip()


def test_pl_check_is_skipped_without_a_character(qapp: QApplication) -> None:
    window = PowerConstructorWindow(load_game_data())  # no character context
    card = window.canvas.add_effect("damage")
    card._rank.setValue(30)
    assert not window._warning.isVisibleTo(window)


def test_warn_enforcement_saves_an_over_cap_power(qapp: QApplication, monkeypatch) -> None:
    from mm_companion.core import storage

    monkeypatch.setattr(storage, "pl_enforcement", lambda: storage.PL_ENFORCE_WARN)
    window = PowerConstructorWindow(load_game_data(), character=_pl10_character())
    window.canvas.add_effect("damage")._rank.setValue(25)

    saved: list = []
    window.powerSaved.connect(saved.append)
    window._save_power()
    assert saved  # warning mode still lets it through


def test_block_enforcement_refuses_an_over_cap_power(qapp: QApplication, monkeypatch) -> None:
    from PySide6.QtWidgets import QMessageBox

    from mm_companion.core import storage

    monkeypatch.setattr(storage, "pl_enforcement", lambda: storage.PL_ENFORCE_BLOCK)
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: None)
    window = PowerConstructorWindow(load_game_data(), character=_pl10_character())
    window.canvas.add_effect("damage")._rank.setValue(25)

    saved: list = []
    window.powerSaved.connect(saved.append)
    window._save_power()
    assert saved == []  # blocking mode refuses the save (no power handed off)


def test_section_row_marks_a_power_that_breaks_the_cap(qapp: QApplication) -> None:

    data = load_game_data()
    character = Character.new_default(data)  # PL 10, cap 20
    character.powers.append(
        Power(name="Overkill", effects=[PowerEffectInstance(effect_id="damage", rank=30)])
    )

    sheet = CharacterSheet(data, character)
    warnings = [lbl for lbl in sheet.powers._list_host.findChildren(QLabel) if lbl.text() == "⚠"]
    assert warnings  # the over-cap power carries a warning marker
    assert "rank 30" in warnings[0].toolTip()


def test_strength_based_damage_uses_strength_in_the_pl_check(qapp: QApplication) -> None:
    from mm_companion.core.powers import ModifierSelection

    data = load_game_data()
    character = Character.new_default(data)
    character.abilities["STR"] = 12  # a strong bruiser

    # A modest rank-10 Strength-Based Damage resolves at rank 22 with STR 12 — over
    # the PL 10 cap of 20 once Strength is folded in.
    effect = PowerEffectInstance("damage", rank=10, extras=[ModifierSelection("strength_based")])
    character.powers.append(Power(name="Haymaker", effects=[effect]))

    sheet = CharacterSheet(data, character)
    warnings = [lbl for lbl in sheet.powers._list_host.findChildren(QLabel) if lbl.text() == "⚠"]
    assert warnings  # Strength pushed the effective rank over the cap
    assert "rank 22" in warnings[0].toolTip()


def test_constructor_shows_strength_folded_into_the_damage_dc(qapp: QApplication) -> None:
    character = Character.new_default(load_game_data())
    character.abilities["STR"] = 5
    window = PowerConstructorWindow(load_game_data(), character=character)

    card = window.canvas.add_effect("damage")
    card._rank.setValue(8)
    card.attach_modifier("strength_based")

    # Toughness DC = 10 + effective rank (8 + 5) = 23, not the bought-rank 18.
    rows = {r.key: r for r in window._terms.effect_rows[0]}
    assert rows["resistance"].value == "Toughness vs. 23"


def test_constructor_summary_shows_the_characters_attack_bonus(qapp: QApplication) -> None:
    character = Character.new_default(load_game_data())
    character.abilities["ATK"] = 7
    window = PowerConstructorWindow(load_game_data(), character=character)

    window.canvas.add_effect("damage")._rank.setValue(9)
    # The attack roll in the summary reads the character's Attack, not the rank.
    rows = {r.key: r for r in window._terms.effect_rows[0]}
    assert rows["check"].value == "7 vs. Defense"


# -- Enhanced Trait trait allocation & trait-boost display --------------------


def _target_combo(card):
    """The single-target boost combo on an effect card, or None.

    Enhanced Trait no longer has one — it allocates ranks across several traits through
    :func:`_trait_rows` instead. Kept to assert exactly that, and that the boosters which
    never had a picker still don't.
    """
    from PySide6.QtWidgets import QComboBox

    return next((c for c in card.findChildren(QComboBox) if c.findData("STR") >= 0), None)


def _trait_rows(card):
    """The (trait picker, rank spin) pairs of an effect card's trait-allocation rows.

    The picker is a composite now — a trait combo plus the qualifier control that
    appears for a focused skill or a subject-taking advantage — so a row is addressed
    through it rather than through the bare combo it contains.
    """
    from PySide6.QtWidgets import QSpinBox

    from mm_companion.ui.power_constructor import TraitPicker

    return [(p, p.parent().findChild(QSpinBox)) for p in card.findChildren(TraitPicker)]


def _trait_combo(picker):
    """The trait half of a picker — the combo that names the trait."""
    from PySide6.QtWidgets import QComboBox

    return next(c for c in picker.findChildren(QComboBox) if c.findData("STR") >= 0)


def _qualifier(picker):
    """The picker's qualifier control, or ``None`` while the chosen trait needs none."""
    from PySide6.QtWidgets import QComboBox, QLineEdit

    trait = _trait_combo(picker)
    # Direct children only: an *editable* combo owns an internal QLineEdit, and a
    # recursive search would hand that back as though it were the qualifier field.
    direct = Qt.FindChildOption.FindDirectChildrenOnly
    controls = [
        *picker.findChildren(QComboBox, options=direct),
        *picker.findChildren(QLineEdit, options=direct),
    ]
    # isHidden, not isVisible: nothing in an unshown window is "visible".
    return next((w for w in controls if not w.isHidden() and w is not trait), None)


def _allocate(card, pairs) -> None:
    """Fill the card's trait-allocation rows with ``(trait key, ranks)``, adding as needed.

    A *qualified* key ("Expertise::Law") is set on both halves of the picker, the way a
    player would: the trait, then the focus or subject it narrows to.
    """
    from PySide6.QtWidgets import QComboBox, QPushButton

    from mm_companion.core.rules import split_trait_key

    add = next(b for b in card.findChildren(QPushButton) if b.text().endswith("Add"))
    while len(_trait_rows(card)) < len(pairs):
        add.click()
    for (picker, spin), (target, ranks) in zip(_trait_rows(card), pairs, strict=False):
        base, qualifier = split_trait_key(target)
        combo = _trait_combo(picker)
        combo.setCurrentIndex(combo.findData(base))
        if qualifier:
            control = _qualifier(picker)
            assert control is not None, f"{base} offers no qualifier control"
            if isinstance(control, QComboBox):
                index = control.findData(qualifier)
                if index >= 0:
                    control.setCurrentIndex(index)
                else:
                    control.setCurrentText(qualifier)
            else:
                control.setText(qualifier)
        spin.setValue(ranks)


def test_configurable_effect_offers_a_trait_allocation(qapp: QApplication) -> None:
    window = PowerConstructorWindow(load_game_data())
    card = window.canvas.add_effect("enhanced_trait")
    rows = _trait_rows(card)
    assert len(rows) == 1  # one blank row to start — the picker *is* the question
    picker, spin = rows[0]
    combo = _trait_combo(picker)
    assert combo.findData("TOUGHNESS") >= 0
    assert combo.findData("Acrobatics") >= 0
    assert combo.findData("Fearless") >= 0  # advantages are traits an Enhanced Trait raises
    assert spin is not None
    # An ability has one row and nothing to narrow, so no second control appears.
    combo.setCurrentIndex(combo.findData("STR"))
    assert _qualifier(picker) is None


def test_fixed_and_plain_effects_have_no_target_picker(qapp: QApplication) -> None:
    window = PowerConstructorWindow(load_game_data())
    # Protection's target is fixed (Toughness), Damage isn't a booster at all.
    assert _target_combo(window.canvas.add_effect("protection")) is None
    assert _target_combo(window.canvas.add_effect("damage")) is None
    # Enhanced Trait's rows replace the old one-trait combo rather than joining it.
    card = window.canvas.add_effect("enhanced_trait")
    assert card._build_target_picker(card._effect()) is None


def test_allocating_traits_writes_rows_to_the_effect_config(qapp: QApplication) -> None:
    window = PowerConstructorWindow(load_game_data())
    card = window.canvas.add_effect("enhanced_trait")
    card._rank.setValue(8)
    _allocate(card, [("AWE", 2), ("Stealth", 6)])
    assert card.instance.config["traits"] == [
        {"trait": "AWE", "ranks": 2},
        {"trait": "Stealth", "ranks": 6},
    ]


def test_each_allocated_trait_is_priced_at_its_own_rate(qapp: QApplication) -> None:
    """The worked example: Strength 2 + Treatment 6 + Expertise 2, Limited, is 4 PP.

    4 + 3 + 1 = 8 at each trait's own buying rate, halved by the -1/rank Limited flaw.
    """
    window = PowerConstructorWindow(load_game_data())
    card = window.canvas.add_effect("enhanced_trait")
    card._rank.setValue(10)
    _allocate(card, [("STR", 2), ("Treatment", 6), ("Expertise", 2)])
    assert effect_total_cost(card.instance, load_game_data()) == 8
    card.attach_modifier("limited_enhanced_trait")
    assert effect_total_cost(card.instance, load_game_data()) == 4


def test_saved_enhanced_trait_shows_on_the_stat_and_feeds_skills(qapp: QApplication) -> None:
    sheet = CharacterSheet(load_game_data())
    sheet.abilities._abilities["STR"].setValue(2)

    sheet.powers._open_constructor()
    window = sheet.powers._windows[0]
    window._name.setText("Mighty")
    card = window.canvas.add_effect("enhanced_trait")
    card._rank.setValue(3)
    _allocate(card, [("STR", 3)])
    window._save_power()

    enh = sheet.abilities._ability_enh["STR"]
    assert enh.text() == "→ 5"  # 2 bought + 3 boost
    assert "Mighty" in enh.toolTip()

    # A Strength-linked skill total reflects the boosted ability.
    sheet.character.skill_ranks["Athletics"] = 1
    sheet.skills.refresh_totals()
    athletics = next(r for r in sheet.skills._rows if r.row_id == "Athletics")
    assert athletics.total_item.text() == "6"  # effective STR 5 + 1 rank


def test_removing_a_boosting_power_clears_the_enhancement(qapp: QApplication) -> None:
    from mm_companion.core.character import Character

    data = load_game_data()
    character = Character.new_default(data)
    character.powers.append(
        Power(name="Armor", effects=[PowerEffectInstance("protection", rank=5)])
    )
    sheet = CharacterSheet(data, character)

    tough = sheet.resistances._resistance_enh["TOUGHNESS"]
    assert tough.text() == "→ 5"  # Protection boost shown on load

    sheet.powers._remove_power(character.powers[0])
    assert tough.text() == ""  # boost cleared when the power goes


# -- edit-in-place ------------------------------------------------------------


def test_editing_seeds_the_window_from_the_existing_power(qapp: QApplication) -> None:
    from mm_companion.core.powers import ModifierSelection

    effect = PowerEffectInstance("damage", rank=8, extras=[ModifierSelection("ranged")])
    power = Power(name="Fire Blast", description="whoosh", effects=[effect])

    window = PowerConstructorWindow(load_game_data(), power=power)

    # The name, description, effect card, and its rank/modifier chip all seed from
    # the power being edited.
    assert window.windowTitle() == "Edit Power"
    assert window._name.text() == "Fire Blast"
    assert window._description.toPlainText() == "whoosh"
    assert len(window.canvas.cards) == 1
    card = window.canvas.cards[0]
    assert card._rank.value() == 8
    assert [c.selection.modifier_id for c in card._chips] == ["ranged"]
    assert window._cost.text() == "Total cost: 16 PP"  # (1 + 1) * 8


def test_editing_works_on_a_copy_until_saved(qapp: QApplication) -> None:

    power = Power(name="Fire Blast", effects=[PowerEffectInstance("damage", rank=8)])
    window = PowerConstructorWindow(load_game_data(), power=power)

    # The window edits a distinct copy, so mutating it leaves the original alone
    # until a save hands the copy back.
    assert window.power is not power
    window._name.setText("Ice Blast")
    window.canvas.cards[0]._rank.setValue(3)
    assert power.name == "Fire Blast"  # original untouched
    assert power.effects[0].rank == 8


def test_editing_a_multi_effect_power_restores_its_structure(qapp: QApplication) -> None:
    from mm_companion.core.powers import STRUCTURE_ARRAY

    power = Power(
        name="Elements",
        structure=STRUCTURE_ARRAY,
        effects=[
            PowerEffectInstance("damage", rank=8),
            PowerEffectInstance("affliction", rank=2),
        ],
    )
    window = PowerConstructorWindow(load_game_data(), power=power)

    # The structure switch is shown and reflects the loaded Array, and the cards
    # carry their base/alternate badges.
    assert window.canvas._mode_bar.isVisibleTo(window.canvas)
    assert window.power.structure == STRUCTURE_ARRAY
    assert window.canvas.cards[0]._role_badge.text() == "Base"
    assert window.canvas.cards[1]._role_badge.text().startswith("Alternate")
    assert window._cost.text() == "Total cost: 9 PP"  # 8 base + 1 flat alternate


def test_editing_from_the_section_replaces_the_power_in_place(qapp: QApplication) -> None:

    data = load_game_data()
    character = Character.new_default(data)
    character.powers.append(
        Power(name="Fire Blast", effects=[PowerEffectInstance("damage", rank=8)])
    )
    keep = Power(name="Force Field", effects=[PowerEffectInstance("protection", rank=4)])
    character.powers.append(keep)
    sheet = CharacterSheet(data, character)

    changes: list = []
    sheet.powers.changed.connect(lambda: changes.append(True))

    sheet.powers._edit_power(character.powers[0])
    window = sheet.powers._windows[0]
    window._name.setText("Ice Blast")
    window.canvas.cards[0]._rank.setValue(10)
    window._save_power()

    # The edited power replaces the original at its index (not appended), the other
    # power is untouched, and the section reports the change.
    assert [p.name for p in character.powers] == ["Ice Blast", "Force Field"]
    assert character.powers[0].effects[0].rank == 10
    assert character.powers[1] is keep
    assert changes
    assert sheet.powers._windows == []


def test_closing_the_editor_without_saving_leaves_the_power_unchanged(qapp: QApplication) -> None:

    data = load_game_data()
    character = Character.new_default(data)
    original = Power(name="Fire Blast", effects=[PowerEffectInstance("damage", rank=8)])
    character.powers.append(original)
    sheet = CharacterSheet(data, character)

    sheet.powers._edit_power(character.powers[0])
    window = sheet.powers._windows[0]
    window._name.setText("Ice Blast")
    window.canvas.cards[0]._rank.setValue(3)
    window.close()  # no save

    # The stored power is still the original object, unmodified.
    assert character.powers == [original]
    assert character.powers[0].name == "Fire Blast"
    assert character.powers[0].effects[0].rank == 8
    assert sheet.powers._windows == []


def test_edit_button_hidden_in_locked_view(qapp: QApplication) -> None:
    from PySide6.QtWidgets import QPushButton

    data = load_game_data()
    character = Character.new_default(data)
    character.powers.append(
        Power(name="Fire Blast", effects=[PowerEffectInstance("damage", rank=8)])
    )
    sheet = CharacterSheet(data, character)

    def edit_buttons() -> list[QPushButton]:
        return [b for b in sheet.powers._list_host.findChildren(QPushButton) if b.text() == "✎"]

    assert edit_buttons() and all(b.isVisibleTo(sheet.powers) for b in edit_buttons())
    sheet.set_locked(True)
    assert all(not b.isVisibleTo(sheet.powers) for b in edit_buttons())


def test_reordering_a_card_moves_the_power_in_the_model(qapp: QApplication) -> None:

    data = load_game_data()
    character = Character.new_default(data)
    for name in ("Alpha", "Beta", "Gamma"):
        character.powers.append(Power(name=name, effects=[PowerEffectInstance("damage", rank=1)]))
    sheet = CharacterSheet(data, character)

    changes: list[int] = []
    sheet.powers.changed.connect(lambda: changes.append(1))

    alpha = character.powers[0]
    # Drop the first card into the gap after the last one (top-level gap index 3, dragged
    # still in place). It lands at the end; the section emits `changed`.
    sheet.powers._on_move(alpha.id, "", 3)
    assert [p.name for p in character.powers] == ["Beta", "Gamma", "Alpha"]
    assert changes

    # Move it back up to the top (gap index 0).
    sheet.powers._on_move(alpha.id, "", 0)
    assert [p.name for p in character.powers] == ["Alpha", "Beta", "Gamma"]


def test_reorder_grip_hidden_in_locked_view(qapp: QApplication) -> None:
    from mm_companion.ui.sections.powers import _DragHandle

    data = load_game_data()
    character = Character.new_default(data)
    character.powers.append(
        Power(name="Fire Blast", effects=[PowerEffectInstance("damage", rank=8)])
    )
    sheet = CharacterSheet(data, character)

    def grips() -> list[_DragHandle]:
        return sheet.powers._list_host.findChildren(_DragHandle)

    assert grips() and all(g.isVisibleTo(sheet.powers) for g in grips())
    sheet.set_locked(True)
    assert all(not g.isVisibleTo(sheet.powers) for g in grips())


def test_damage_strength_based_checkbox_toggles_the_extra(qapp: QApplication) -> None:
    from PySide6.QtWidgets import QCheckBox

    window = PowerConstructorWindow(load_game_data())
    card = window.canvas.add_effect("damage")
    box = next(b for b in card.findChildren(QCheckBox))  # the Strength-Based config

    box.setChecked(True)
    assert [s.modifier_id for s in card.instance.extras] == ["strength_based"]
    box.setChecked(False)
    assert card.instance.extras == []


def test_allocation_checklist_spends_ranks_and_warns_when_over(qapp: QApplication) -> None:
    from PySide6.QtWidgets import QCheckBox

    window = PowerConstructorWindow(load_game_data())
    card = window.canvas.add_effect("enhanced_senses")
    card._rank.setValue(2)

    boxes = {b.text().split(" (")[0]: b for b in card.findChildren(QCheckBox)}
    boxes["Accurate"].setChecked(True)  # tiered 2/4 → default tier 1 = 2 ranks
    assert card.instance.config["senses"] == [{"id": "accurate", "tier": 1}]
    assert not window._warning.isVisibleTo(window)  # 2 of 2 ranks — exactly on budget

    boxes["Acute"].setChecked(True)  # +1 rank → 3 of 2, over budget
    assert window._warning.isVisibleTo(window)
    assert "Over-allocated" in window._warning.text()


def test_repeatable_rows_add_remove_and_persist(qapp: QApplication) -> None:
    from PySide6.QtWidgets import QLineEdit, QPushButton

    window = PowerConstructorWindow(load_game_data())
    card = window.canvas.add_effect("feature")
    card._rank.setValue(2)

    add = next(b for b in card.findChildren(QPushButton) if "Add" in b.text())
    add.click()
    name = next(e for e in card.findChildren(QLineEdit) if e.placeholderText() == "Feature")
    name.setText("Battery")
    assert card.instance.config["features"] == [{"name": "Battery", "description": ""}]

    remove = next(b for b in card.findChildren(QPushButton) if b.text() == "✕" and b.isFlat())
    remove.click()
    assert card.instance.config["features"] == []


def test_modifier_chip_config_drives_cost(qapp: QApplication) -> None:
    from PySide6.QtWidgets import QComboBox

    window = PowerConstructorWindow(load_game_data())
    card = window.canvas.add_effect("protection")
    card._rank.setValue(10)
    card.attach_modifier("removable")
    # Removable is charged against the *power's* total, 10 here, at its value per 5
    # points rounded up — so -1 x 2, and the footer shows that arithmetic.
    assert window._cost.text() == "Total cost: 8 PP  (10 − 2 Removable)"

    chip = card._chips[0]
    combo = chip.findChild(QComboBox)  # the tier selector, the first of the chip's two
    combo.setCurrentIndex(combo.findData("easily_removable"))
    assert chip.selection.config == {"tier": "easily_removable", "loss": "long_term"}
    assert window._cost.text() == "Total cost: 6 PP  (10 − 4 Removable)"  # -2 x 2


def test_modifier_chip_text_field_writes_config(qapp: QApplication) -> None:
    from PySide6.QtWidgets import QLineEdit

    window = PowerConstructorWindow(load_game_data())
    card = window.canvas.add_effect("damage")
    card.attach_modifier("limited")  # a flaw carrying a free-text condition

    chip = card._chips[0]
    edit = chip.findChild(QLineEdit)
    edit.setText("only at night")
    assert chip.selection.config == {"condition": "only at night"}


def test_reordering_chips_reorders_the_backing_selection_list(qapp: QApplication) -> None:
    window = PowerConstructorWindow(load_game_data())
    card = window.canvas.add_effect("damage")
    card.attach_modifier("ranged")  # extra 0
    card.attach_modifier("accurate")  # extra 1

    assert [s.modifier_id for s in card.instance.extras] == ["ranged", "accurate"]

    # Drag the first chip past the second (insertion point 2 in the pre-move list).
    changes: list = []
    card.changed.connect(lambda: changes.append(True))
    card._extras_group.move_chip(0, 2)

    assert [s.modifier_id for s in card.instance.extras] == ["accurate", "ranged"]
    assert card._extras_group._chips[0].selection.modifier_id == "accurate"
    assert changes  # a real reorder reports a change


def test_reordering_a_chip_in_place_is_a_no_op(qapp: QApplication) -> None:
    window = PowerConstructorWindow(load_game_data())
    card = window.canvas.add_effect("damage")
    card.attach_modifier("ranged")
    card.attach_modifier("accurate")

    changes: list = []
    card.changed.connect(lambda: changes.append(True))
    card._extras_group.move_chip(0, 1)  # dropped just after itself → no change

    assert [s.modifier_id for s in card.instance.extras] == ["ranged", "accurate"]
    assert changes == []  # settling in place fires nothing


def test_variable_conditions_full_scope_hides_all_degree_pickers(qapp: QApplication) -> None:
    from PySide6.QtWidgets import QComboBox, QLabel

    window = PowerConstructorWindow(load_game_data())
    card = window.canvas.add_effect("affliction")
    assert len(card.findChildren(QComboBox)) == 5  # resistance + overcomeBy + 3 degrees

    card.attach_modifier("variable_conditions")  # defaults to the 2-point (all) scope
    notes = [lbl.text() for lbl in card.findChildren(QLabel)]
    assert notes.count("chosen when used") == 3  # every degree deferred to use-time
    # Only resistance + overcomeBy remain as *visible* combos; the chip's own "which
    # degree" picker exists but is hidden at full scope, so it doesn't count.
    visible = [c for c in card.findChildren(QComboBox) if c.isVisibleTo(card)]
    assert len(visible) == 2


def test_variable_conditions_partial_scope_defers_one_degree(qapp: QApplication) -> None:
    from PySide6.QtWidgets import QComboBox, QLabel, QSpinBox

    window = PowerConstructorWindow(load_game_data())
    card = window.canvas.add_effect("affliction")
    card.attach_modifier("variable_conditions")
    chip = card._chips[0]

    # Dial the scope down to 1 point/rank — now only one chosen degree is deferred.
    spin = next(s for s in chip.findChildren(QSpinBox) if s.suffix() == " pt")
    spin.setValue(1)
    degree_combo = next(c for c in chip.findChildren(QComboBox))
    degree_combo.setCurrentIndex(degree_combo.findData("degree2"))

    notes = [lbl.text() for lbl in card.findChildren(QLabel)]
    assert notes.count("chosen when used") == 1  # only the 2nd degree is deferred
    assert card.instance.extras[0].config["degree"] == "degree2"
    # Cost drops from the +2/rank full scope to +1/rank.
    assert effect_total_cost(card.instance, load_game_data()) == card.instance.rank * 2


def test_limited_degree_hides_the_chosen_degree_picker(qapp: QApplication) -> None:
    from PySide6.QtWidgets import QComboBox

    window = PowerConstructorWindow(load_game_data())
    card = window.canvas.add_effect("affliction")
    # resistance + overcomeBy + 3 degree pickers.
    assert len(card.findChildren(QComboBox)) == 5

    card.attach_modifier("limited_degree")  # an Affliction flaw with a degree picker
    # The flaw defaults to its first option (1st degree), so that degree's condition
    # picker vanishes; the chip's own degree combo replaces it in the count.
    selection = card.instance.flaws[0]
    assert selection.config["degree"] == "degree1"
    notes = [lbl.text() for lbl in card.findChildren(QLabel)]
    assert "no effect (Limited Degree)" in notes

    # Re-point the flaw at the 3rd degree: the hidden picker follows the choice.
    chip = card._chips[0]
    degree_combo = chip.findChild(QComboBox)
    degree_combo.setCurrentIndex(degree_combo.findData("degree3"))
    assert card.instance.flaws[0].config["degree"] == "degree3"
    # degree3 is now suppressed; degree1's picker is back.
    assert "degree3" not in card.instance.config  # the disabled tier stores no condition


def test_reduced_trait_offers_data_driven_trait_rows(qapp: QApplication) -> None:
    from PySide6.QtWidgets import QComboBox, QSpinBox

    window = PowerConstructorWindow(load_game_data())
    card = window.canvas.add_effect("enhanced_trait")
    card.attach_modifier("reduced_trait")  # an Enhanced Trait flaw: which traits drop
    chip = card._chips[0]
    combo = chip.findChild(QComboBox)
    assert combo is not None
    # The picker is populated from the game data (abilities/resistances/skills/
    # advantages), not a static option list, and defaults to the unset "choose" row.
    labels = [combo.itemText(i).strip() for i in range(combo.count())]
    assert "Strength" in labels and "Dodge" in labels
    assert combo.currentData() == ""  # no trait forced by default
    combo.setCurrentIndex(combo.findData("STR"))
    chip.findChild(QSpinBox).setValue(2)
    assert card.instance.flaws[0].config["reduced"] == [{"trait": "STR", "ranks": 2}]


def test_reduced_trait_discounts_by_what_the_lowered_trait_cost(qapp: QApplication) -> None:
    from PySide6.QtWidgets import QComboBox, QSpinBox

    data = load_game_data()
    window = PowerConstructorWindow(data)
    card = window.canvas.add_effect("enhanced_trait")
    card._rank.setValue(4)
    _allocate(card, [("STR", 4)])
    assert effect_total_cost(card.instance, data) == 8  # 4 ranks at 2 PP a rank

    card.attach_modifier("reduced_trait")
    chip = card._chips[0]
    chip.findChild(QComboBox).setCurrentIndex(chip.findChild(QComboBox).findData("DODGE"))
    chip.findChild(QSpinBox).setValue(3)
    assert effect_total_cost(card.instance, data) == 5  # 8 less the 3 PP of Dodge

    chip.findChild(QSpinBox).setValue(50)  # more than the effect was ever worth
    assert effect_total_cost(card.instance, data) == 1  # the rules' 1 PP floor holds


# Cross-power relationships (Independent / Array / Linked between whole powers) are no
# longer set in the constructor — they're built on the character sheet by dragging one
# power card onto another (see tests/test_powers_section.py). The constructor's own mode
# bar (structure of a single power's effects) is covered elsewhere in this module.


def test_config_widget_registry_has_base_builders(qapp: QApplication) -> None:
    from mm_companion.ui.power_constructor import CONFIG_WIDGET_BUILDERS

    # Every built-in field type has a builder; ``select`` deliberately does not — it is
    # the generic fallback rendered when no builder is registered for a type.
    for field_type in ("text", "checkbox", "allocation", "repeatable", "multiselect"):
        assert field_type in CONFIG_WIDGET_BUILDERS
    assert "select" not in CONFIG_WIDGET_BUILDERS


def test_mod_registered_config_field_type_renders_via_registry(qapp: QApplication) -> None:
    from types import SimpleNamespace

    from PySide6.QtWidgets import QComboBox

    from mm_companion.ui.power_constructor import CONFIG_WIDGET_BUILDERS

    window = PowerConstructorWindow(load_game_data(), character=_pl10_character())
    card = window.canvas.add_effect("damage")
    field = SimpleNamespace(key="mod_field", options=[])

    # An unregistered type falls back to the generic option combo.
    assert isinstance(card._config_widget(field, "stars"), QComboBox)

    # A mod registering a builder for that type gets its widget used, no constructor edit.
    marker = QLabel("mod widget")
    CONFIG_WIDGET_BUILDERS.register("stars", lambda c, f, ft: marker)
    try:
        assert card._config_widget(field, "stars") is marker
    finally:
        CONFIG_WIDGET_BUILDERS.unregister("stars")
    assert isinstance(card._config_widget(field, "stars"), QComboBox)


def test_structural_modifiers_are_absent_from_the_palette(qapp: QApplication) -> None:
    # Linked and Alternate Effect are applied from a power/group's structure now, so
    # they are hidden from the draggable extras palette (a normal extra still shows).
    window = PowerConstructorWindow(load_game_data())
    _, extra_bricks = window._search_tabs["extras"]
    names = {b.search_key for b in extra_bricks}
    assert "linked" not in names
    assert "alternate effect" not in names
    assert "ranged" in names
    # Attack is implicit on attacking effects but still draggable onto any other one.
    assert "attack" in names


def test_subtle_points_spin_box_drives_the_cost(qapp: QApplication) -> None:
    from PySide6.QtWidgets import QSpinBox

    window = PowerConstructorWindow(load_game_data())
    card = window.canvas.add_effect("damage")
    card._rank.setValue(8)
    card.attach_modifier("subtle")  # flat extra, defaults to +1 point

    assert window._cost.text() == "Total cost: 9 PP"
    chip = card._chips[0]
    spin = chip.findChild(QSpinBox)
    assert spin is not None  # Subtle exposes a points spin box
    spin.setValue(2)

    assert window.power.effects[0].extras[0].config["points"] == 2
    assert window._cost.text() == "Total cost: 10 PP"


def test_effects_sort_toggle_hides_group_headers(qapp: QApplication) -> None:
    from PySide6.QtWidgets import QCheckBox, QLabel

    from mm_companion.ui.power_constructor import _GROUP_HEADER

    window = PowerConstructorWindow(load_game_data())
    # Scoped to the Effects tab: Configurations is grouped and sortable too, and a
    # window-wide search would toggle one tab and assert about both.
    tab = window._search_tabs["effects"][0].parentWidget()
    # Select headers by their object name, not their text — a brick can share a group's
    # name (the Attack extra vs. the Attack effect group).
    headers = [lbl for lbl in tab.findChildren(QLabel) if lbl.objectName() == _GROUP_HEADER]
    assert headers  # grouped by effect type by default
    assert all(not h.isHidden() for h in headers)

    check = next(c for c in tab.findChildren(QCheckBox) if "Sort A" in c.text())
    check.setChecked(True)  # flat alphabetical view drops the headers
    assert all(h.isHidden() for h in headers)

    check.setChecked(False)  # back to grouped
    assert all(not h.isHidden() for h in headers)


def _editable_combos(widget):
    """The editable comboboxes under ``widget``, in child order (the std-field value
    combos of an override group; order combos are non-editable and excluded)."""
    from PySide6.QtWidgets import QComboBox

    return [c for c in widget.findChildren(QComboBox) if c.isEditable()]


def test_dev_mode_makes_the_terms_panel_editable_and_cost_flows(qapp: QApplication) -> None:
    window = PowerConstructorWindow(load_game_data(), character=_pl10_character())
    card = window.canvas.add_effect("damage")
    card._rank.setValue(8)

    # Off: the game-terms panel is the read-only summary (no editable combos).
    assert not window._terms._editable
    assert not _editable_combos(window._terms)

    window._dev_mode.setChecked(True)
    assert window._terms._editable
    assert _editable_combos(window._terms)  # the panel is now the override editor

    window._terms._cost_override_check.setChecked(True)
    window._terms._cost_override_spin.setValue(30)
    assert window.power.cost_override == 30
    assert "30 PP" in window._cost.text()
    assert "homerule" in window._cost.text()

    window._terms._cost_override_check.setChecked(False)
    assert window.power.cost_override is None


def test_dev_mode_term_override_flows_to_the_read_only_summary(qapp: QApplication) -> None:
    from mm_companion.core.powers import power_is_homerule

    window = PowerConstructorWindow(load_game_data(), character=_pl10_character())
    card = window.canvas.add_effect("damage")
    card._rank.setValue(8)
    window._dev_mode.setChecked(True)

    combos = _editable_combos(window._terms)
    # _OVERRIDE_STD_FIELDS order: effect_type, range, action, ... — index 1 is Range.
    combos[1].setCurrentText("Planetary")
    assert card.instance.overrides["range"]["value"] == "Planetary"
    assert power_is_homerule(window.power)

    # Turning Dev mode off re-renders the read-only summary, which shows the override.
    window._dev_mode.setChecked(False)
    rows = {r.key: r for r in window._terms.effect_rows[0]}
    assert rows["range"].value == "Planetary"
    assert rows["range"].change == "homerule"


def test_dev_mode_seeds_auto_values_and_resolves_option_numbers(qapp: QApplication) -> None:
    window = PowerConstructorWindow(load_game_data(), character=_pl10_character())
    card = window.canvas.add_effect("damage")
    card._rank.setValue(8)
    window._dev_mode.setChecked(True)

    # _OVERRIDE_STD_FIELDS order: effect_type, range, action, duration, check, resistance.
    resistance = _editable_combos(window._terms)[5]
    # The field starts pre-filled at its resolved auto value (DC filled in), no override.
    assert resistance.currentText() == "Toughness vs. 18"
    assert "resistance" not in card.instance.overrides
    # The dropdown offers resolved numbers, not the raw "vs. Effect" templates.
    items = [resistance.itemText(i) for i in range(resistance.count())]
    assert "Will vs. 18" in items
    assert all("vs. Effect" not in it for it in items)

    # Picking a resolved alternative stores it verbatim.
    resistance.setCurrentText("Will vs. 18")
    assert card.instance.overrides["resistance"]["value"] == "Will vs. 18"
    # Re-selecting the auto value clears the override again.
    resistance.setCurrentText("Toughness vs. 18")
    assert "resistance" not in card.instance.overrides


def test_edited_homerule_power_reopens_with_dev_mode_on(qapp: QApplication) -> None:
    effect = PowerEffectInstance("damage", rank=8)
    effect.overrides["range"] = {"value": "Planetary", "order": "after"}
    power = Power(name="Homebrew", effects=[effect])
    window = PowerConstructorWindow(load_game_data(), character=_pl10_character(), power=power)
    assert window._dev_mode.isChecked()
    assert window._terms._editable


# -- drag affordances: the reorder indicator and drag-to-remove -----------------


def test_drop_indicator_tracks_the_insertion_point_between_chips(qapp: QApplication) -> None:
    window = PowerConstructorWindow(load_game_data())
    card = window.canvas.add_effect("damage")
    card.attach_modifier("ranged")
    card.attach_modifier("accurate")
    group = card._extras_group
    group.resize(320, 80)  # lay the chips out so they have real geometry
    group.show()
    qapp.processEvents()

    before_first = group._indicator_rect(0)
    at_end = group._indicator_rect(len(group._chips))
    assert before_first.width() == group.INDICATOR_WIDTH
    # The end marker sits to the right of the one before the first chip.
    assert at_end.left() > before_first.left()

    # isHidden (not isVisible): the card around the group is never shown in a
    # headless test, so every descendant reports itself invisible regardless.
    group._show_indicator(before_first.topLeft())
    assert not group._indicator.isHidden()
    group._end_drag()
    assert group._indicator.isHidden()


def test_indicator_stays_hidden_for_an_empty_group(qapp: QApplication) -> None:
    window = PowerConstructorWindow(load_game_data())
    card = window.canvas.add_effect("damage")

    group = card._flaws_group  # nothing attached
    assert group._indicator_rect(0).isEmpty()
    group._show_indicator(group.rect().topLeft())
    assert group._indicator.isHidden()


def test_dropping_a_chip_on_the_palette_detaches_it(qapp: QApplication) -> None:
    from mm_companion.ui.power_constructor.bricks import PaletteDropZone

    window = PowerConstructorWindow(load_game_data())
    card = window.canvas.add_effect("damage")
    card.attach_modifier("ranged")
    card.attach_modifier("accurate")
    chip = card._chips[0]

    # Removal is deferred past the drag's own event, so let the timer fire.
    PaletteDropZone.remove_chip(chip)
    qapp.processEvents()

    assert [s.modifier_id for s in card.instance.extras] == ["accurate"]
    assert len(card._chips) == 1


def test_palette_accepts_only_chip_drags(qapp: QApplication) -> None:
    from types import SimpleNamespace

    from PySide6.QtCore import QMimeData
    from PySide6.QtWidgets import QWidget

    from mm_companion.ui.power_constructor.common import CHIP_MIME, EFFECT_MIME

    def drag(fmt: bytes, payload: bytes, source):
        """The two things ``PaletteDropZone._accepts`` reads off a drag event."""
        mime = QMimeData()
        mime.setData(fmt, payload)
        return SimpleNamespace(mimeData=lambda: mime, source=lambda: source)

    window = PowerConstructorWindow(load_game_data())
    zone = window.palette_zone
    card = window.canvas.add_effect("damage")
    card.attach_modifier("ranged")

    # A palette brick dragged around inside the palette is not a removal.
    assert not zone._accepts(drag(EFFECT_MIME, b"damage", QWidget()))
    assert zone._accepts(drag(CHIP_MIME, b"1", card._chips[0]))


# -- hover descriptions ---------------------------------------------------------


def test_palette_bricks_hint_their_description(qapp: QApplication) -> None:
    window = PowerConstructorWindow(load_game_data())
    data = load_game_data()

    _search, effect_bricks = window._search_tabs["effects"]
    damage = next(b for b in effect_bricks if b.search_key == "damage")
    record = next(e for e in data.effects if e.id == "damage")
    assert record.description in damage.toolTip()
    assert record.name in damage.toolTip()

    _search, extra_bricks = window._search_tabs["extras"]
    assert all(b.toolTip() for b in extra_bricks)  # every modifier ships a description


def test_an_attached_chip_hints_the_same_description(qapp: QApplication) -> None:
    data = load_game_data()
    window = PowerConstructorWindow(data)
    card = window.canvas.add_effect("damage")
    card.attach_modifier("accurate")

    record = next(m for m in data.modifiers if m.id == "accurate")
    assert record.description in card._chips[0].toolTip()


def test_allocation_options_hint_what_each_mode_does(qapp: QApplication) -> None:
    from PySide6.QtWidgets import QCheckBox

    window = PowerConstructorWindow(load_game_data())
    card = window.canvas.add_effect("enhanced_movement")

    boxes = card.findChildren(QCheckBox)
    wall_crawling = next(b for b in boxes if b.text().startswith("Wall-Crawling"))
    assert "walls" in wall_crawling.toolTip()


def test_check_required_offers_derived_traits_the_boost_picker_hides(qapp: QApplication) -> None:
    from PySide6.QtWidgets import QComboBox

    window = PowerConstructorWindow(load_game_data())
    card = window.canvas.add_effect("damage")
    card.attach_modifier("check_required")

    combo = card._chips[0].findChild(QComboBox)
    assert combo.findData("Acrobatics") > 0  # a skill
    assert combo.findData("AGL") > 0  # an ability
    assert combo.findData("initiative") > 0  # a derived stat, only in `all_traits`


# --- Identity, not equality: the model is a list of plain dataclasses, so two
#     copies of the same part compare equal and a value-based lookup drops the
#     wrong one. ---------------------------------------------------------------


def test_removing_one_of_two_identical_effects_keeps_the_other_card_live(
    qapp: QApplication,
) -> None:
    """Two copies of an effect are equal dataclasses; removing the second must not
    unbind the first from the power."""
    window = PowerConstructorWindow(load_game_data())
    first = window.canvas.add_effect("damage")
    second = window.canvas.add_effect("damage")
    assert first.instance == second.instance  # equal by value...
    assert first.instance is not second.instance  # ...but distinct objects

    window.canvas._remove_card(second)

    assert len(window.power.effects) == 1
    assert window.power.effects[0] is first.instance

    # The surviving card still writes through to the power, so what is saved is
    # what the card shows.
    first._rank.setValue(8)
    assert window.power.effects[0].rank == 8


def test_removing_one_of_two_identical_modifier_chips_keeps_the_other_live(
    qapp: QApplication,
) -> None:
    """A modifier with config fields can be taken twice, and two fresh copies seed
    identical configs — so chip removal has to match by identity."""
    window = PowerConstructorWindow(load_game_data())
    card = window.canvas.add_effect("damage")
    card.attach_modifier("custom_extra")
    card.attach_modifier("custom_extra")
    assert len(card.instance.extras) == 2
    first, second = card._chips[0], card._chips[1]
    assert first.selection == second.selection

    card._remove_chip(second)

    assert len(card.instance.extras) == 1
    assert card.instance.extras[0] is first.selection

    # Removing the survivor too must not raise (the old code hit ValueError here).
    card._remove_chip(first)
    assert card.instance.extras == []


def test_editing_a_power_preserves_its_switched_off_runtime_state(qapp: QApplication) -> None:
    """The save format omits runtime state on purpose, so the edit copy must not be a
    ``to_dict``/``from_dict`` round-trip — that would switch a powered-down power on."""
    data = load_game_data()
    power = Power(name="Flight", effects=[PowerEffectInstance("flight", rank=4)])
    power.activated = False
    power.effects[0].toggled_on = False

    window = PowerConstructorWindow(data, character=_pl10_character(), power=power)

    assert window.power is not power  # still an isolated copy...
    assert window.power.effects[0] is not power.effects[0]
    assert window.power.activated is False  # ...that kept the runtime state
    assert window.power.effects[0].toggled_on is False

    # And the copy is genuinely deep: editing it leaves the original alone.
    window.power.effects[0].rank = 9
    assert power.effects[0].rank == 4


def test_dev_mode_keeps_the_attack_skill_bonus_in_its_auto_values(qapp: QApplication) -> None:
    """Ticking Dev mode must restate the same numbers the read-only panel showed. An
    attack-skill link is not an override, so dropping it would make the by-the-book
    value look like a homerule edit."""
    char = _pl10_character()
    char.abilities["ATK"] = 3
    char.focuses["Close Combat"] = ["Blades"]
    char.skill_ranks["Close Combat::Blades"] = 5  # focus total = ATK 3 + 5 = 8
    window = PowerConstructorWindow(load_game_data(), character=char)

    card = window.canvas.add_effect("damage")
    card._rank.setValue(6)
    card._attack_skill_check.setChecked(True)
    index = card._attack_skill.findData("Close Combat::Blades")
    card._attack_skill.setCurrentIndex(index)

    read_only = {r.key: r.value for r in window._terms.effect_rows[0]}
    assert read_only["check"] == "8 vs. Defense"

    window._terms.set_editable(True)
    dev_mode = {r.key: r.value for r in window._terms.effect_rows[0]}
    assert dev_mode["check"] == read_only["check"]

    # And nothing was recorded as an override just by opening the editor.
    assert card.instance.overrides == {}


def test_allocation_checklist_is_tall_enough_for_every_wrapped_row(
    qapp: QApplication,
) -> None:
    """A two-dozen-option checklist (Enhanced Senses, Enhanced Movement) wraps onto
    several rows, and the card has to grow for all of them.

    The flow deliberately reports no height-for-width, so a bare ``QWidget`` host is
    sized by its one-row hint and the form row around it clips everything below the
    first line. The host is a ``FlowContainer``, which pins its height to what the
    flow really wraps to at the width it was given."""
    from PySide6.QtWidgets import QCheckBox

    from mm_companion.ui.flow_layout import FlowContainer, FlowLayout

    window = PowerConstructorWindow(load_game_data())
    for effect_id in ("enhanced_senses", "enhanced_movement"):
        card = window.canvas.add_effect(effect_id)
        window.show()
        qapp.processEvents()

        hosts = [
            child
            for child in card.findChildren(FlowContainer)
            if isinstance(child.layout(), FlowLayout) and child.findChildren(QCheckBox)
        ]
        assert hosts, f"{effect_id} has no allocation checklist"
        for host in hosts:
            wrapped = host.layout().heightForWidth(host.width())
            assert wrapped > host.layout().itemAt(0).sizeHint().height(), "expected >1 row"
            assert host.height() >= wrapped, f"{effect_id}: checklist clipped"
            # And the last option really lands inside the host, not painted past it.
            last = host.findChildren(QCheckBox)[-1]
            assert last.geometry().bottom() <= host.height()
    window.close()


# -- the Extended settings section ------------------------------------------------


def test_extended_settings_appear_only_once_something_could_use_them(
    qapp: QApplication,
) -> None:
    """The bargain the structure bar already makes: a control with no subject is hidden."""
    window = PowerConstructorWindow(load_game_data(), character=_pl10_character())

    assert window._extended_row.isVisibleTo(window) is False

    window.canvas.add_effect("flight")  # forces no resistance — still nothing to say
    assert window._extended_row.isVisibleTo(window) is False

    window.canvas.add_effect("damage")
    assert window._extended_row.isVisibleTo(window) is True


def test_the_size_switch_writes_every_effect_it_covers(qapp: QApplication) -> None:
    window = PowerConstructorWindow(load_game_data(), character=_pl10_character())
    window.canvas.add_effect("damage")
    window.canvas.add_effect("affliction")

    window._size_damage.setChecked(False)

    assert [e.size_scales_damage for e in window.power.effects] == [False, False]


def test_an_effect_added_later_inherits_the_switch(qapp: QApplication) -> None:
    """Or turning it off and then adding an effect would quietly turn it back on."""
    window = PowerConstructorWindow(load_game_data(), character=_pl10_character())
    window.canvas.add_effect("damage")
    window._size_damage.setChecked(False)

    window.canvas.add_effect("affliction")

    assert window._size_damage.isChecked() is False
    assert all(not e.size_scales_damage for e in window.power.effects)


def test_the_switch_seeds_from_the_power_it_opens_on(qapp: QApplication) -> None:
    laser = Power(
        name="Laser",
        effects=[PowerEffectInstance("damage", rank=8, size_scales_damage=False)],
    )
    window = PowerConstructorWindow(load_game_data(), character=_pl10_character(), power=laser)

    assert window._extended_row.isVisibleTo(window) is True
    assert window._size_damage.isChecked() is False


def test_the_switch_moves_the_save_dc_it_is_about(qapp: QApplication) -> None:
    from mm_companion.core.rules import effect_effective_rank

    data = load_game_data()
    char = _pl10_character()
    char.characteristics["size"] = "Huge"
    window = PowerConstructorWindow(data, character=char)
    card = window.canvas.add_effect("damage")
    card._rank.setValue(8)

    effect = window.power.effects[0]
    assert effect_effective_rank(effect, data, char) == 10

    window._size_damage.setChecked(False)
    assert effect_effective_rank(effect, data, char) == 8


def test_the_note_says_what_this_wielder_size_is_worth(qapp: QApplication) -> None:
    char = _pl10_character()
    char.characteristics["size"] = "Huge"
    window = PowerConstructorWindow(load_game_data(), character=char)
    window.canvas.add_effect("damage")

    assert "Huge" in window._size_damage_note.text()
    assert "+2" in window._size_damage_note.text()


def test_allocating_a_trait_updates_the_cards_own_cost_line(qapp: QApplication) -> None:
    """The card's footer must move when the allocation does.

    Every other cost-changing gesture on a card refreshes it — a rank change, a
    modifier attaching, a chip reordering. An effect's *config* never did, because
    until Enhanced Trait was priced "as trait" no config field could change a cost.
    It can now, and the footer is the only place the arithmetic is shown.
    """
    window = PowerConstructorWindow(load_game_data())
    card = window.canvas.add_effect("enhanced_trait")
    card._rank.setValue(4)
    before = card._cost.text()

    _allocate(card, [("STR", 4)])
    assert card._cost.text() != before
    assert card._cost.text().endswith("8 PP")


def test_allocating_a_trait_updates_the_windows_total(qapp: QApplication) -> None:
    window = PowerConstructorWindow(load_game_data())
    card = window.canvas.add_effect("enhanced_trait")
    card._rank.setValue(4)
    _allocate(card, [("STR", 4)])
    assert "8" in window._cost.text()


def test_clearing_an_allocated_trait_takes_the_cost_back_down(qapp: QApplication) -> None:
    from PySide6.QtWidgets import QSpinBox

    window = PowerConstructorWindow(load_game_data())
    card = window.canvas.add_effect("enhanced_trait")
    card._rank.setValue(4)
    _allocate(card, [("STR", 4)])

    # Back to nothing chosen: an unallocated Enhanced Trait costs nothing, and the
    # footer has to say so rather than keeping the number it last showed.
    combo, spin = _trait_rows(card)[0]
    spin.setValue(0)
    assert isinstance(spin, QSpinBox)
    assert card._cost.text().endswith("0 PP")


def test_reduced_trait_rows_update_the_cost_line_too(qapp: QApplication) -> None:
    """The flaw's rows travel a different wire — the chip's ``changed`` — so prove it."""
    from PySide6.QtWidgets import QComboBox, QSpinBox

    window = PowerConstructorWindow(load_game_data())
    card = window.canvas.add_effect("enhanced_trait")
    card._rank.setValue(4)
    _allocate(card, [("STR", 4)])
    assert card._cost.text().endswith("8 PP")

    card.attach_modifier("reduced_trait")
    chip = card._chips[0]
    combo = chip.findChild(QComboBox)
    combo.setCurrentIndex(combo.findData("DODGE"))
    chip.findChild(QSpinBox).setValue(3)
    assert card._cost.text().endswith("5 PP")  # 8 less the 3 PP three ranks of Dodge cost


# --- the trait + qualifier picker -----------------------------------------------------


def test_a_focused_skill_asks_which_focus(qapp: QApplication) -> None:
    """Picking "Expertise" alone would silently raise every field of study the hero has,
    so the picker asks which — offering their own focuses first, and taking anything."""

    data = load_game_data()
    character = Character.new_default(data)
    character.focuses["Expertise"] = ["Law"]
    window = PowerConstructorWindow(data, character=character)
    card = window.canvas.add_effect("enhanced_trait")
    picker, _spin = _trait_rows(card)[0]
    combo = _trait_combo(picker)
    combo.setCurrentIndex(combo.findData("Expertise"))

    focus = _qualifier(picker)
    assert focus is not None and focus.isEditable()
    assert focus.itemText(0) == "Law"  # the hero's own row leads
    focus.setCurrentText("Stealth")  # and anything else may simply be typed
    assert picker.value() == "Expertise::Stealth"


def test_a_specialized_pool_is_offered_but_the_whole_skill_is_the_default(
    qapp: QApplication,
) -> None:
    data = load_game_data()
    character = Character.new_default(data)
    character.specializations["Stealth"] = ["Urban"]
    window = PowerConstructorWindow(data, character=character)
    card = window.canvas.add_effect("enhanced_trait")
    picker, _spin = _trait_rows(card)[0]
    combo = _trait_combo(picker)
    combo.setCurrentIndex(combo.findData("Stealth"))

    pool = _qualifier(picker)
    assert picker.value() == "Stealth"  # the whole skill, every row of it
    pool.setCurrentIndex(pool.findData("spec::Urban"))
    assert picker.value() == "Stealth::spec::Urban"


def test_a_new_specialization_can_be_named_for_a_skill_that_has_none(
    qapp: QApplication, monkeypatch
) -> None:
    """The pool a power grants need not be one anybody bought — an Enhanced Trait may
    invent *Stealth: Rooftops*, and a list of the hero's existing pools could never
    offer it."""

    from PySide6.QtWidgets import QToolButton

    from mm_companion.ui.power_constructor import trait_picker as picker_module

    data = load_game_data()
    window = PowerConstructorWindow(data)  # no character at all
    card = window.canvas.add_effect("enhanced_trait")
    picker, _spin = _trait_rows(card)[0]
    combo = _trait_combo(picker)
    combo.setCurrentIndex(combo.findData("Stealth"))

    add = picker.findChild(QToolButton)
    assert add is not None and not add.isHidden()
    asked: list[tuple] = []

    def fake(_parent, _title, label, items, *a, **k):
        asked.append((label, list(items)))
        return ("Rooftops", True)

    monkeypatch.setattr(picker_module.QInputDialog, "getItem", staticmethod(fake))
    add.click()
    # The same question the Skills block's *Add specialization…* asks: the catalog's
    # suggestions, and the skill's own guidance as the prompt.
    assert asked[-1] == ("By specific environment or terrain", ["Hiding", "Sneaking", "Tailing"])
    assert picker.value() == "Stealth::spec::Rooftops"
    # And it is a row of the list from here on, not a value the widget merely remembers.
    assert _qualifier(picker).findData("spec::Rooftops") >= 0


def test_naming_a_specialization_is_not_a_purchase(qapp: QApplication, monkeypatch) -> None:
    """The power pays for the pool it grants, so nothing lands on the sheet: the Skills
    block grows the row itself, muted, out of the granted bonus."""

    from PySide6.QtWidgets import QToolButton

    from mm_companion.ui.power_constructor import trait_picker as picker_module

    data = load_game_data()
    character = Character.new_default(data)
    window = PowerConstructorWindow(data, character=character)
    card = window.canvas.add_effect("enhanced_trait")
    picker, _spin = _trait_rows(card)[0]
    combo = _trait_combo(picker)
    combo.setCurrentIndex(combo.findData("Stealth"))
    monkeypatch.setattr(
        picker_module.QInputDialog, "getItem", staticmethod(lambda *a, **k: ("Rooftops", True))
    )
    picker.findChild(QToolButton).click()

    assert character.specializations.get("Stealth") is None


def test_a_cancelled_specialization_leaves_the_row_alone(qapp: QApplication, monkeypatch) -> None:
    from PySide6.QtWidgets import QToolButton

    from mm_companion.ui.power_constructor import trait_picker as picker_module

    data = load_game_data()
    character = Character.new_default(data)
    character.specializations["Stealth"] = ["Urban"]
    window = PowerConstructorWindow(data, character=character)
    card = window.canvas.add_effect("enhanced_trait")
    picker, _spin = _trait_rows(card)[0]
    combo = _trait_combo(picker)
    combo.setCurrentIndex(combo.findData("Stealth"))
    pool = _qualifier(picker)
    pool.setCurrentIndex(pool.findData("spec::Urban"))

    monkeypatch.setattr(
        picker_module.QInputDialog, "getItem", staticmethod(lambda *a, **k: ("", False))
    )
    picker.findChild(QToolButton).click()
    assert picker.value() == "Stealth::spec::Urban"


def test_a_focused_skill_offers_its_pools_beside_its_focuses(qapp: QApplication) -> None:
    """Expertise carries both kinds of row, so the one qualifier control has to answer
    for both — and a pool reads as a pool rather than as its raw key."""

    data = load_game_data()
    character = Character.new_default(data)
    character.focuses["Expertise"] = ["Law"]
    character.specializations["Expertise"] = ["Case Law"]
    window = PowerConstructorWindow(data, character=character)
    card = window.canvas.add_effect("enhanced_trait")
    picker, _spin = _trait_rows(card)[0]
    combo = _trait_combo(picker)
    combo.setCurrentIndex(combo.findData("Expertise"))

    choice = _qualifier(picker)
    index = choice.findData("spec::Case Law")
    assert index >= 0 and choice.itemText(index) == "Case Law (specialized)"
    choice.setCurrentIndex(index)
    assert picker.value() == "Expertise::spec::Case Law"
    choice.setCurrentText("Law")  # and a focus is still whatever was typed
    assert picker.value() == "Expertise::Law"


def test_a_granted_pool_survives_reopening_the_power(qapp: QApplication) -> None:
    """A saved Enhanced Trait naming a pool nobody bought has to find it in the list it
    is rebuilt against, or committing the row would quietly reselect the whole skill."""

    data = load_game_data()
    power = Power(
        name="Rooftop Runner",
        effects=[
            PowerEffectInstance(
                "enhanced_trait",
                rank=4,
                config={"traits": [{"trait": "Stealth::spec::Rooftops", "ranks": 4}]},
            )
        ],
    )
    window = PowerConstructorWindow(data, power=power)
    card = window.canvas.cards[0]
    picker, _spin = _trait_rows(card)[0]

    assert picker.value() == "Stealth::spec::Rooftops"


def test_an_advantage_with_a_subject_asks_for_it(qapp: QApplication) -> None:
    """Improved Critical is bought per attack; granting it without saying which would be
    granting an advantage that names nothing."""

    data = load_game_data()
    character = Character.new_default(data)
    character.powers.append(Power(name="Sword", effects=[PowerEffectInstance("damage", rank=3)]))
    window = PowerConstructorWindow(data, character=character)
    card = window.canvas.add_effect("enhanced_trait")
    picker, _spin = _trait_rows(card)[0]
    combo = _trait_combo(picker)
    combo.setCurrentIndex(combo.findData("Improved Critical"))

    subject = _qualifier(picker)
    assert subject is not None
    subject.setCurrentIndex(subject.findData("Sword"))
    assert picker.value() == "Improved Critical::Sword"


def test_changing_the_trait_drops_the_qualifier_it_answered(qapp: QApplication) -> None:
    """ "Law" is not a focus of Close Combat; carrying it over would be a row nobody
    chose."""

    window = PowerConstructorWindow(load_game_data())
    card = window.canvas.add_effect("enhanced_trait")
    picker, _spin = _trait_rows(card)[0]
    combo = _trait_combo(picker)
    combo.setCurrentIndex(combo.findData("Expertise"))
    _qualifier(picker).setCurrentText("Law")
    assert picker.value() == "Expertise::Law"
    combo.setCurrentIndex(combo.findData("STR"))
    assert picker.value() == "STR"
    assert _qualifier(picker) is None


def test_the_trait_list_explains_its_advantages(qapp: QApplication) -> None:
    """Ninety bare names is not a list anyone can build from; every one of them hints,
    as the palette bricks beside it do."""

    window = PowerConstructorWindow(load_game_data())
    card = window.canvas.add_effect("enhanced_trait")
    combo = _trait_combo(_trait_rows(card)[0][0])
    hint = combo.itemData(combo.findData("Fearless"), Qt.ItemDataRole.ToolTipRole)
    assert "Fearless" in hint and "not ranked" not in hint  # Fearless is ranked, max 2
    assert "max 2" in hint
    assert "Acrobatics" in combo.itemData(combo.findData("Acrobatics"), Qt.ItemDataRole.ToolTipRole)


# --- the rank that follows its allocation ---------------------------------------------


def test_an_enhanced_traits_rank_follows_its_rows(qapp: QApplication) -> None:
    window = PowerConstructorWindow(load_game_data())
    card = window.canvas.add_effect("enhanced_trait")
    assert card._rank.isReadOnly()
    _allocate(card, [("STR", 2), ("Treatment", 6)])
    assert card.instance.rank == 8
    assert card._rank.value() == 8
    # And the readout meters nothing, because there is no budget to meter against.
    label = next(lbl for lbl in card.findChildren(QLabel) if lbl.text().startswith("Allocated"))
    assert label.text() == "Allocated 8 ranks"


def test_an_ordinary_allocation_effect_keeps_its_hand_set_rank(qapp: QApplication) -> None:
    window = PowerConstructorWindow(load_game_data())
    card = window.canvas.add_effect("enhanced_senses")
    assert not card._rank.isReadOnly()
    label = next(lbl for lbl in card.findChildren(QLabel) if lbl.text().startswith("Allocated"))
    assert "/" in label.text()


def test_a_rank_spin_stops_at_the_advantages_own_ceiling(qapp: QApplication) -> None:
    """Three ranks of a two-rank advantage is a point thrown away; the spin says so
    before the point is spent rather than after."""

    window = PowerConstructorWindow(load_game_data())
    card = window.canvas.add_effect("enhanced_trait")
    picker, spin = _trait_rows(card)[0]
    combo = _trait_combo(picker)
    combo.setCurrentIndex(combo.findData("Fearless"))
    assert spin.maximum() == 2
    combo.setCurrentIndex(combo.findData("Agile Grab"))  # not ranked at all
    assert spin.maximum() == 1
    combo.setCurrentIndex(combo.findData("STR"))  # an ability: bounded by the PL, not here
    assert spin.maximum() > 2


# --- the cost line and what it hides --------------------------------------------------


def test_the_cost_line_groups_by_trait_kind_and_hovers_the_detail(qapp: QApplication) -> None:
    window = PowerConstructorWindow(load_game_data())
    card = window.canvas.add_effect("enhanced_trait")
    _allocate(card, [("STR", 2), ("Stealth", 1), ("Perception", 1)])
    assert card._cost.text() == "Abilities 4 + Skills 1 = 5 PP"
    detail = card._cost.toolTip()
    for trait in ("Strength", "Stealth", "Perception"):
        assert trait in detail
    assert "1/2" in detail  # the halves the grouped line pooled


def test_an_ordinary_effects_cost_line_hides_nothing(qapp: QApplication) -> None:
    window = PowerConstructorWindow(load_game_data())
    card = window.canvas.add_effect("damage")
    assert card._cost.toolTip() == ""

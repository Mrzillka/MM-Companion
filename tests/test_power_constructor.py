"""The Power Constructor window builds and mutates a Power via its drop seams.

Real drag-and-drop events are unreliable headless, so these drive the public
mutation methods the drop handlers delegate to (``add_effect`` / ``attach_modifier``).
"""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QApplication, QLabel

from mm_companion.core.character import Character
from mm_companion.core.data_loader import load_game_data
from mm_companion.ui.character_sheet import CharacterSheet
from mm_companion.ui.power_constructor import PowerConstructorWindow


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


def _pl10_character() -> Character:
    """A blank PL 10 character (no attack bonus) — the context the constructor needs
    to check Power Level caps."""
    return Character.new_default(load_game_data())


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
    assert len(card.findChildren(QComboBox)) == 5  # resistance + overcomeBy + 3 degrees
    assert card.findChildren(QCheckBox) == []

    card.attach_modifier("extra_condition")  # the Affliction-only gating extra
    assert card.findChildren(QCheckBox)  # 1st/2nd degrees are now multiselect
    # resistance, overcomeBy, and the 3rd degree stay single-select combos
    assert len(card.findChildren(QComboBox)) == 3


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
    from mm_companion.core.powers import Power, PowerEffectInstance

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
    from mm_companion.core.powers import Power, PowerEffectInstance

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
    from mm_companion.core.powers import ModifierSelection, Power, PowerEffectInstance

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


# -- Enhanced Trait target picker & trait-boost display -----------------------


def _target_combo(card):
    """The Enhanced-Trait target combo on an effect card, or None."""
    from PySide6.QtWidgets import QComboBox

    return next((c for c in card.findChildren(QComboBox) if c.findData("STR") >= 0), None)


def test_configurable_effect_offers_a_trait_target_picker(qapp: QApplication) -> None:
    window = PowerConstructorWindow(load_game_data())
    card = window.canvas.add_effect("enhanced_trait")
    combo = _target_combo(card)
    assert combo is not None  # abilities, resistances, skills all offered
    assert combo.findData("TOUGHNESS") >= 0
    assert combo.findData("Acrobatics") >= 0


def test_fixed_and_plain_effects_have_no_target_picker(qapp: QApplication) -> None:
    window = PowerConstructorWindow(load_game_data())
    # Protection's target is fixed (Toughness), Damage isn't a booster at all.
    assert _target_combo(window.canvas.add_effect("protection")) is None
    assert _target_combo(window.canvas.add_effect("damage")) is None


def test_picking_a_target_writes_it_to_the_effect_config(qapp: QApplication) -> None:
    window = PowerConstructorWindow(load_game_data())
    card = window.canvas.add_effect("enhanced_trait")
    combo = _target_combo(card)
    combo.setCurrentIndex(combo.findData("AWE"))
    assert card.instance.config["target"] == "AWE"


def test_saved_enhanced_trait_shows_on_the_stat_and_feeds_skills(qapp: QApplication) -> None:
    sheet = CharacterSheet(load_game_data())
    sheet.stats._abilities["STR"].setValue(2)

    sheet.powers._open_constructor()
    window = sheet.powers._windows[0]
    window._name.setText("Mighty")
    card = window.canvas.add_effect("enhanced_trait")
    card._rank.setValue(3)
    combo = _target_combo(card)
    combo.setCurrentIndex(combo.findData("STR"))
    window._save_power()

    enh = sheet.stats._ability_enh["STR"]
    assert enh.isVisibleTo(sheet.stats)
    assert enh.text() == "→ 5"  # 2 bought + 3 boost
    assert "Mighty" in enh.toolTip()

    # A Strength-linked skill total reflects the boosted ability.
    sheet.character.skill_ranks["Athletics"] = 1
    sheet.skills.refresh_totals()
    athletics = next(r for r in sheet.skills._rows if r[1] == "Athletics")
    assert athletics[3].text() == "6"  # effective STR 5 + 1 rank


def test_removing_a_boosting_power_clears_the_enhancement(qapp: QApplication) -> None:
    from mm_companion.core.character import Character
    from mm_companion.core.powers import Power, PowerEffectInstance

    data = load_game_data()
    character = Character.new_default(data)
    character.powers.append(
        Power(name="Armor", effects=[PowerEffectInstance("protection", rank=5)])
    )
    sheet = CharacterSheet(data, character)

    tough = sheet.stats._resistance_enh["TOUGHNESS"]
    assert tough.isVisibleTo(sheet.stats)  # Protection boost shown on load

    sheet.powers._remove_power(character.powers[0])
    assert not tough.isVisibleTo(sheet.stats)  # boost cleared when the power goes


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
    assert window._cost.text() == "Total cost: 9 PP"  # -1 flat by default

    chip = card._chips[0]
    combo = chip.findChild(QComboBox)  # the tier selector
    combo.setCurrentIndex(combo.findData("easily_removable"))
    assert chip.selection.config == {"tier": "easily_removable"}
    assert window._cost.text() == "Total cost: 8 PP"  # -2 flat


def test_modifier_chip_text_field_writes_config(qapp: QApplication) -> None:
    from PySide6.QtWidgets import QLineEdit

    window = PowerConstructorWindow(load_game_data())
    card = window.canvas.add_effect("damage")
    card.attach_modifier("limited")  # a flaw carrying a free-text condition

    chip = card._chips[0]
    edit = chip.findChild(QLineEdit)
    edit.setText("only at night")
    assert chip.selection.config == {"condition": "only at night"}


def test_variable_conditions_hides_the_degree_pickers(qapp: QApplication) -> None:
    from PySide6.QtWidgets import QComboBox, QLabel

    window = PowerConstructorWindow(load_game_data())
    card = window.canvas.add_effect("affliction")
    assert len(card.findChildren(QComboBox)) == 5  # resistance + overcomeBy + 3 degrees

    card.attach_modifier("variable_conditions")  # defers the choices to use-time
    assert len(card.findChildren(QComboBox)) == 2  # only resistance + overcomeBy remain
    notes = [lbl.text() for lbl in card.findChildren(QLabel)]
    assert notes.count("chosen when used") == 3  # one per hidden degree

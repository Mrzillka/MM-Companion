"""The conditions catalog, the non-roll resolver, and the conditions UI."""

from __future__ import annotations

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QFrame

from mm_companion.core.character import AdvantageSelection, AppliedCondition, Character
from mm_companion.core.data_loader import load_game_data
from mm_companion.core.powers import Power
from mm_companion.core.rules import (
    apply_condition,
    condition_attack_mods,
    condition_check_penalty,
    condition_resistance_penalty,
    condition_scope_penalty,
    condition_speed_rank_mod,
    debilitated_traits,
    decrement_condition,
    expand_includes,
    hit_stack_penalty,
    remove_condition,
    resistance_condition_effect,
    roll_confused_action,
)
from mm_companion.ui.character_sheet import CharacterSheet
from mm_companion.ui.sections.condition_dialog import ConditionParameterDialog
from mm_companion.ui.sections.conditions import ConditionsSection


def _ids(char: Character) -> list[str]:
    return [c.condition_id for c in char.conditions]


def _find(char: Character, condition_id: str) -> AppliedCondition:
    return next(c for c in char.conditions if c.condition_id == condition_id)


# --------------------------------------------------------------------------- #
# Data layer
# --------------------------------------------------------------------------- #


def test_loader_parses_the_new_condition_fields() -> None:
    catalog = load_game_data().condition_catalog()

    impaired = catalog["impaired"]
    assert impaired.penalty == -2
    assert "check_penalty" in impaired.mechanisms
    assert impaired.parameter is not None
    assert impaired.parameter.type == "trait_select"
    assert impaired.parameter.required is False
    assert impaired.tooltip  # short always-visible copy survived the merge

    hit = catalog["hit"]
    assert hit.stacking is True
    assert hit.stacking_rule is not None
    assert hit.stacking_rule.per_instance_penalty == -1

    prone = catalog["prone"]
    assert prone.attack_mods is not None
    assert prone.attack_mods.own_close == -5
    assert prone.speed_rank_mod == 0  # "zero" maps to 0

    assert catalog["hindered"].speed_rank_mod == -1
    assert catalog["vulnerable"].defense_mod is not None
    assert catalog["debilitated"].debilitates is not None


def test_condition_catalog_covers_every_condition() -> None:
    data = load_game_data()
    catalog = data.condition_catalog()
    assert len(catalog) == len(data.conditions)
    assert "normal" in catalog  # the meta bookkeeping entry loads too


def test_debilitation_cascade_references_known_ids() -> None:
    catalog = load_game_data().condition_catalog()
    for condition in catalog.values():
        if condition.debilitates is None:
            continue
        for cascade in condition.debilitates.cascade.values():
            for ref in cascade:
                assert ref in catalog, f"{condition.id} cascades to unknown {ref}"


# --------------------------------------------------------------------------- #
# Resolver: includes / supersedes / stacking / debilitation
# --------------------------------------------------------------------------- #


def test_expand_includes_flattens_nested_umbrellas() -> None:
    catalog = load_game_data().condition_catalog()
    members = expand_includes(catalog["dying"], catalog)
    # Dying -> Incapacitated -> Defenseless / Stunned / Unaware.
    assert "incapacitated" in members
    assert {"defenseless", "stunned", "unaware"} <= set(members)


def test_apply_expands_includes_with_provenance() -> None:
    data = load_game_data()
    char = Character()
    apply_condition(char, "incapacitated", data)
    assert _find(char, "incapacitated").provenance is None
    for member in ("defenseless", "stunned", "unaware"):
        assert _find(char, member).provenance == "incapacitated"


def test_removing_an_umbrella_removes_its_members() -> None:
    data = load_game_data()
    char = Character()
    apply_condition(char, "incapacitated", data)
    remove_condition(char, _find(char, "incapacitated"))
    assert char.conditions == []


def test_a_member_can_be_removed_individually() -> None:
    data = load_game_data()
    char = Character()
    apply_condition(char, "staggered", data)  # Dazed + Hindered
    remove_condition(char, _find(char, "dazed"))
    assert set(_ids(char)) == {"staggered", "hindered"}


def test_supersedes_drops_the_less_severe_condition() -> None:
    data = load_game_data()
    char = Character()
    apply_condition(char, "dazed", data)
    apply_condition(char, "stunned", data)  # Stunned supersedes Dazed
    assert "dazed" not in _ids(char)
    assert "stunned" in _ids(char)


def test_per_part_supersession_leaves_the_rest_of_the_bundle() -> None:
    data = load_game_data()
    char = Character()
    apply_condition(char, "staggered", data)  # Dazed + Hindered
    apply_condition(char, "stunned", data)  # supersedes the Dazed half only
    assert "dazed" not in _ids(char)
    assert {"staggered", "hindered", "stunned"} == set(_ids(char))


def test_incapacitated_supersedes_the_whole_staggered_bundle() -> None:
    data = load_game_data()
    char = Character()
    apply_condition(char, "staggered", data)
    apply_condition(char, "incapacitated", data)  # supersedes Staggered
    # The superseded umbrella and every member it granted are gone.
    assert "staggered" not in _ids(char)
    assert "dazed" not in _ids(char)
    assert _find(char, "stunned").provenance == "incapacitated"


def test_trait_scoped_supersession_only_replaces_the_same_trait() -> None:
    data = load_game_data()
    char = Character()
    apply_condition(char, "impaired", data, parameter="Attack")
    apply_condition(char, "impaired", data, parameter="Perception")
    apply_condition(char, "disabled", data, parameter="Attack")  # supersedes Attack Impaired
    kinds = {(c.condition_id, c.parameter) for c in char.conditions}
    assert ("impaired", "Attack") not in kinds
    assert ("impaired", "Perception") in kinds  # different trait coexists
    assert ("disabled", "Attack") in kinds


def test_superseded_conditions_do_not_return() -> None:
    data = load_game_data()
    char = Character()
    apply_condition(char, "dazed", data)
    apply_condition(char, "stunned", data)
    remove_condition(char, _find(char, "stunned"))
    assert char.conditions == []  # Dazed was replaced, not suppressed


def test_hit_stacks_and_accumulates_penalty() -> None:
    data = load_game_data()
    char = Character()
    for _ in range(3):
        apply_condition(char, "hit", data)
    assert _ids(char) == ["hit"]
    assert _find(char, "hit").count == 3
    assert hit_stack_penalty(char, data) == -3


def test_non_stacking_conditions_are_idempotent() -> None:
    data = load_game_data()
    char = Character()
    apply_condition(char, "stunned", data)
    apply_condition(char, "stunned", data)
    assert _ids(char) == ["stunned"]


def test_debilitation_cascades_into_hard_conditions() -> None:
    data = load_game_data()
    char = Character()
    apply_condition(char, "debilitated", data, parameter="Strength")
    # Strength -> Incapacitated (which bundles Defenseless / Stunned / Unaware).
    assert {"debilitated", "incapacitated", "defenseless", "stunned", "unaware"} == set(_ids(char))
    remove_condition(char, _find(char, "debilitated"))
    assert char.conditions == []


# --------------------------------------------------------------------------- #
# Queryable accessors (computed, not yet displayed on the sheet)
# --------------------------------------------------------------------------- #


def test_check_penalty_respects_scope() -> None:
    data = load_game_data()
    char = Character()
    apply_condition(char, "impaired", data, parameter="Attack")
    assert condition_check_penalty(char, data, scope="Attack") == -2
    assert condition_check_penalty(char, data, scope="Perception") == 0
    assert condition_check_penalty(char, data) == 0  # generic ignores a scoped penalty


def test_unscoped_penalty_applies_everywhere() -> None:
    data = load_game_data()
    char = Character()
    apply_condition(char, "impaired", data)  # no parameter -> all checks
    assert condition_check_penalty(char, data) == -2
    assert condition_check_penalty(char, data, scope="Attack") == -2


def test_movement_and_attack_and_resistance_accessors() -> None:
    data = load_game_data()
    char = Character()
    apply_condition(char, "hindered", data)
    assert condition_speed_rank_mod(char, data) == -1
    apply_condition(char, "prone", data)  # zeroes ground speed
    assert condition_speed_rank_mod(char, data) is None
    assert condition_attack_mods(char, data)["own_close"] == -5

    char = Character()
    apply_condition(char, "susceptible", data, parameter="Fire Damage")
    assert condition_resistance_penalty(char, data, "fire damage", effect_rank=8) == -4


# --------------------------------------------------------------------------- #
# Model serialization
# --------------------------------------------------------------------------- #


def test_applied_condition_round_trip() -> None:
    char = Character()
    char.conditions = [
        AppliedCondition("impaired", parameter="Attack"),
        AppliedCondition("hit", count=3),
        AppliedCondition("stunned", provenance="incapacitated"),
    ]
    restored = Character.from_dict(char.to_dict())
    assert restored.conditions == char.conditions


def test_legacy_condition_list_still_loads() -> None:
    restored = Character.from_dict({"conditions": ["dazed", "prone"]})
    assert restored.conditions == [AppliedCondition("dazed"), AppliedCondition("prone")]


# --------------------------------------------------------------------------- #
# UI
# --------------------------------------------------------------------------- #


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_section_renders_a_chip_per_applied_condition(qapp: QApplication) -> None:
    data = load_game_data()
    char = Character()
    apply_condition(char, "incapacitated", data)  # 1 umbrella + 3 members
    section = ConditionsSection(data, char)
    assert len(section._condition_chips) == len(char.conditions) == 4


def test_choosing_a_plain_condition_updates_model_and_chips(qapp: QApplication) -> None:
    data = load_game_data()
    char = Character()
    section = ConditionsSection(data, char)
    section._choose_condition(data.condition_catalog()["prone"])
    assert _ids(char) == ["prone"]
    assert len(section._condition_chips) == 1


def test_display_name_folds_parameter_and_count(qapp: QApplication) -> None:
    data = load_game_data()
    catalog = data.condition_catalog()
    section = ConditionsSection(data, Character())
    impaired = AppliedCondition("impaired", parameter="Attack")
    assert section._condition_display_name(impaired, catalog["impaired"]) == "Attack Impaired"
    hit = AppliedCondition("hit", count=3)
    assert section._condition_display_name(hit, catalog["hit"]) == "Hit ×3"
    susceptible = AppliedCondition("susceptible", parameter="Fire Damage")
    name = section._condition_display_name(susceptible, catalog["susceptible"])
    assert name == "Susceptible (Fire Damage)"


def test_parameter_dialog_normalizes_unscoped_value(qapp: QApplication) -> None:
    data = load_game_data()
    dialog = ConditionParameterDialog(data.condition_catalog()["impaired"], data, Character())
    # The optional trait combo defaults to "All checks" -> treated as unscoped.
    assert dialog.value() is None


def test_required_parameter_gates_the_ok_button(qapp: QApplication) -> None:
    data = load_game_data()
    dialog = ConditionParameterDialog(
        data.condition_catalog()["susceptible"], data, Character()
    )  # required descriptor_text
    assert dialog._ok_button.isEnabled() is False
    dialog._input.setText("Fire Damage")
    assert dialog._ok_button.isEnabled() is True
    assert dialog.value() == "Fire Damage"


def test_dialog_two_step_resolves_a_specific_skill(qapp: QApplication) -> None:
    data = load_game_data()
    dialog = ConditionParameterDialog(data.condition_catalog()["impaired"], data, Character())
    dialog._input.setCurrentText("a specific skill")
    dialog._on_scope_changed()
    assert not dialog._specific.isHidden()  # the second combo is revealed
    dialog._specific.setCurrentText("Stealth")
    assert dialog.value() == "Stealth"


# --------------------------------------------------------------------------- #
# Pass 2 — condition effects surfaced on the sheet, plus box interactions
# --------------------------------------------------------------------------- #


def test_condition_scope_penalty_scoped_vs_unscoped() -> None:
    data = load_game_data()
    char = Character()
    apply_condition(char, "impaired", data, parameter="Stealth")
    on = condition_scope_penalty(char, data, {"Stealth"})
    assert on.delta == -2 and on.condition_ids == frozenset({"impaired"})
    assert condition_scope_penalty(char, data, {"Acrobatics"}).delta == 0
    # A blanket Impaired reaches every row.
    char2 = Character()
    apply_condition(char2, "impaired", data)
    assert condition_scope_penalty(char2, data, {"Anything"}).delta == -2


def test_resistance_condition_effect_hit_and_defense() -> None:
    data = load_game_data()
    char = Character()
    apply_condition(char, "hit", data)
    apply_condition(char, "hit", data)
    tough = resistance_condition_effect(char, data, "TOUGHNESS")
    assert tough.delta == -2 and tough.apply(8) == 6
    char = Character()
    apply_condition(char, "vulnerable", data)
    assert resistance_condition_effect(char, data, "DODGE").apply(10) == 5
    char = Character()
    apply_condition(char, "defenseless", data)
    assert resistance_condition_effect(char, data, "DEF").apply(10) == 0


def test_decrement_condition_peels_hits_one_at_a_time() -> None:
    data = load_game_data()
    char = Character()
    for _ in range(3):
        apply_condition(char, "hit", data)
    decrement_condition(char, char.conditions[0])
    assert char.conditions[0].count == 2
    decrement_condition(char, char.conditions[0])
    decrement_condition(char, char.conditions[0])
    assert char.conditions == []


def test_decrement_condition_removes_an_umbrella_whole() -> None:
    data = load_game_data()
    char = Character()
    apply_condition(char, "incapacitated", data)
    decrement_condition(char, next(c for c in char.conditions if c.condition_id == "incapacitated"))
    assert char.conditions == []


def test_roll_confused_action_matches_the_table() -> None:
    data = load_game_data()
    char = Character()
    die, row = roll_confused_action(char, data, roll=8)
    assert die == 8 and row is not None and "nothing" in row.outcome.lower()
    die, row = roll_confused_action(char, data, roll=1)
    assert die == 1 and "source" in row.outcome.lower()


@pytest.fixture(scope="module")
def qapp2() -> QApplication:
    return QApplication.instance() or QApplication([])


def _direct_chip_frames(section: ConditionsSection) -> int:
    total = 0
    for _head, _rule, container in section._category_sections.values():
        total += len(
            container.findChildren(QFrame, options=Qt.FindChildOption.FindDirectChildrenOnly)
        )
    return total


def test_chips_sort_into_category_groups(qapp2: QApplication) -> None:
    data = load_game_data()
    char = Character()
    apply_condition(char, "prone", data)  # general
    apply_condition(char, "dazed", data)  # damage
    section = ConditionsSection(data, char)
    general = section._category_sections["condition"][2]
    damage = section._category_sections["damage_condition"][2]
    opt = Qt.FindChildOption.FindDirectChildrenOnly
    assert len(general.findChildren(QFrame, options=opt)) == 1
    assert len(damage.findChildren(QFrame, options=opt)) == 1


def test_no_ghost_chips_after_repeated_renders(qapp2: QApplication) -> None:
    data = load_game_data()
    char = Character()
    section = ConditionsSection(data, char)
    apply_condition(char, "incapacitated", data)
    section._render_conditions()
    apply_condition(char, "prone", data)
    section._render_conditions()
    apply_condition(char, "dazed", data)
    section._render_conditions()
    # Every chip frame still parented to a container is a live one.
    assert _direct_chip_frames(section) == len(char.conditions)


def test_scoped_impaired_reddens_skill_total_disabled_strikes(qapp2: QApplication) -> None:
    data = load_game_data()
    sheet = CharacterSheet(data)
    char = sheet.character
    sheet.skills._ranks["Stealth"] = 6
    total_item = next(r for r in sheet.skills._rows if r.row_id == "Stealth").total_item

    apply_condition(char, "impaired", data, parameter="Stealth")
    sheet.skills.refresh_totals()
    assert total_item.text() == "4"  # 6 - 2, display only
    assert total_item.foreground().color().name() == "#d15b5b"
    assert total_item.font().strikeOut() is False

    apply_condition(char, "disabled", data, parameter="Stealth")  # supersedes impaired
    sheet.skills.refresh_totals()
    assert total_item.text() == "1"  # 6 - 5
    assert total_item.font().strikeOut() is True


def test_hit_on_toughness_shows_a_red_effective_label(qapp2: QApplication) -> None:
    data = load_game_data()
    sheet = CharacterSheet(data)
    char = sheet.character
    sheet.abilities._abilities["STA"].setValue(3)  # Toughness base 3
    apply_condition(char, "hit", data)
    apply_condition(char, "hit", data)
    sheet.resistances.refresh_enhancements()
    label = sheet.resistances._resistance_enh["TOUGHNESS"]
    assert label.text() == "→ 1"  # 3 - 2
    assert not label.isHidden()


def test_hit_chip_remove_button_decrements(qapp2: QApplication) -> None:
    data = load_game_data()
    char = Character()
    apply_condition(char, "hit", data)
    apply_condition(char, "hit", data)
    section = ConditionsSection(data, char)
    section._shed_condition(char.conditions[0])
    assert char.conditions[0].condition_id == "hit" and char.conditions[0].count == 1


def test_confused_chip_records_a_roll(qapp2: QApplication) -> None:
    data = load_game_data()
    char = Character()
    apply_condition(char, "confused", data)
    section = ConditionsSection(data, char)
    section._roll_confused(char.conditions[0])
    assert section._confused_rolls  # a rolled outcome was stored for the chip


def test_flow_container_reports_wrapped_height(qapp2: QApplication) -> None:
    from PySide6.QtWidgets import QLabel

    from mm_companion.ui.flow_layout import FlowContainer, FlowLayout

    container = FlowContainer()
    flow = FlowLayout(container)
    for _ in range(6):
        label = QLabel("wide-chip-xxxxx")
        label.setFixedSize(120, 24)
        flow.addWidget(label)
    # Narrow enough that six 120px chips must wrap to several rows.
    assert container.sizePolicy().hasHeightForWidth() is True
    one_row = container.heightForWidth(1000)
    many_rows = container.heightForWidth(200)
    assert many_rows > one_row


# --------------------------------------------------------------------------- #
# Debilitated — a chosen trait is effectively lost across the sheet
# --------------------------------------------------------------------------- #


def test_debilitated_zeroes_and_strikes_a_scoped_row() -> None:
    data = load_game_data()
    char = Character()
    apply_condition(char, "debilitated", data, parameter="Stealth")
    effect = condition_scope_penalty(char, data, {"Stealth"})
    assert effect.op == "zero" and effect.apply(6) == 0
    assert "debilitated" in effect.condition_ids  # so the UI strikes the row through
    # An unrelated row is untouched — Debilitated is always scoped to its named trait.
    assert condition_scope_penalty(char, data, {"Acrobatics"}).active is False


def test_debilitated_traits_lists_named_subjects() -> None:
    data = load_game_data()
    char = Character()
    apply_condition(char, "debilitated", data, parameter="Leadership")
    assert "Leadership" in debilitated_traits(char, data)
    assert debilitated_traits(Character(), data) == frozenset()


def test_dialog_two_step_resolves_a_specific_advantage(qapp: QApplication) -> None:
    data = load_game_data()
    char = Character()
    char.advantages.append(AdvantageSelection("Leadership"))
    dialog = ConditionParameterDialog(data.condition_catalog()["debilitated"], data, char)
    dialog._input.setCurrentText("a specific Advantage")  # capitalized in the catalog
    dialog._on_scope_changed()
    assert not dialog._specific.isHidden()  # second combo revealed for advantages too
    options = [dialog._specific.itemText(i) for i in range(dialog._specific.count())]
    assert options == ["Leadership"]
    dialog._specific.setCurrentText("Leadership")
    assert dialog.value() == "Leadership"


def test_debilitated_advantage_row_struck_through(qapp2: QApplication) -> None:
    data = load_game_data()
    sheet = CharacterSheet(data)
    char = sheet.character
    advantage = data.advantages[0]
    char.advantages.append(AdvantageSelection(advantage.name))
    sheet.advantages._rebuild()

    apply_condition(char, "debilitated", data, parameter=advantage.name)
    sheet.advantages.refresh_conditions()
    table, row, _ = sheet.advantages._row_refs[0]
    item = table.item(row, 0)
    assert item.font().strikeOut() is True
    assert item.foreground().color().name() == "#d15b5b"

    remove_condition(char, char.conditions[0])
    sheet.advantages.refresh_conditions()
    assert item.font().strikeOut() is False


def test_debilitated_power_card_struck_through(qapp2: QApplication) -> None:
    from PySide6.QtWidgets import QLabel

    data = load_game_data()
    sheet = CharacterSheet(data)
    char = sheet.character
    char.powers.append(Power(name="Force Field"))
    apply_condition(char, "debilitated", data, parameter="Force Field")
    sheet.powers.refresh()
    struck = [
        label
        for label in sheet.powers.findChildren(QLabel)
        if label.text() == "Force Field" and label.font().strikeOut()
    ]
    assert struck

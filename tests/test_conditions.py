"""The conditions catalog, the non-roll resolver, and the conditions UI."""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QApplication

from mm_companion.core.character import AppliedCondition, Character
from mm_companion.core.data_loader import load_game_data
from mm_companion.core.rules import (
    apply_condition,
    condition_attack_mods,
    condition_check_penalty,
    condition_resistance_penalty,
    condition_speed_rank_mod,
    expand_includes,
    hit_stack_penalty,
    remove_condition,
)
from mm_companion.ui.sections.base_info import BaseInfoSection
from mm_companion.ui.sections.condition_dialog import ConditionParameterDialog


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
    section = BaseInfoSection(data, char)
    assert len(section._condition_chips) == len(char.conditions) == 4


def test_choosing_a_plain_condition_updates_model_and_chips(qapp: QApplication) -> None:
    data = load_game_data()
    char = Character()
    section = BaseInfoSection(data, char)
    section._choose_condition(data.condition_catalog()["prone"])
    assert _ids(char) == ["prone"]
    assert len(section._condition_chips) == 1


def test_display_name_folds_parameter_and_count(qapp: QApplication) -> None:
    data = load_game_data()
    catalog = data.condition_catalog()
    section = BaseInfoSection(data, Character())
    impaired = AppliedCondition("impaired", parameter="Attack")
    assert section._condition_display_name(impaired, catalog["impaired"]) == "Attack Impaired"
    hit = AppliedCondition("hit", count=3)
    assert section._condition_display_name(hit, catalog["hit"]) == "Hit ×3"
    susceptible = AppliedCondition("susceptible", parameter="Fire Damage")
    name = section._condition_display_name(susceptible, catalog["susceptible"])
    assert name == "Susceptible (Fire Damage)"


def test_parameter_dialog_normalizes_unscoped_value(qapp: QApplication) -> None:
    catalog = load_game_data().condition_catalog()
    dialog = ConditionParameterDialog(catalog["impaired"])
    # The optional trait combo defaults to "All checks" -> treated as unscoped.
    assert dialog.value() is None


def test_required_parameter_gates_the_ok_button(qapp: QApplication) -> None:
    catalog = load_game_data().condition_catalog()
    dialog = ConditionParameterDialog(catalog["susceptible"])  # required descriptor_text
    assert dialog._ok_button.isEnabled() is False
    dialog._input.setText("Fire Damage")
    assert dialog._ok_button.isEnabled() is True
    assert dialog.value() == "Fire Damage"

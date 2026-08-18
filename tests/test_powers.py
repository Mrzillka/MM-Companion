"""The power model and its cost math (``docs/mm-powers-architecture.md`` §2)."""

from __future__ import annotations

import dataclasses
import json
import math
from fractions import Fraction

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
    node_from_dict,
)
from mm_companion.core.rules import (
    ability_rank_contribution,
    advantage_points_spent,
    array_alternate_cost,
    array_base_index,
    effect_allocation_used,
    effect_attack_skill_bonus,
    effect_cost_breakdown,
    effect_cost_formula,
    effect_effective_rank,
    effect_game_terms,
    effect_is_active,
    effect_makes_attack,
    effect_per_rank_cost,
    effect_readout_rows,
    effect_size_rank_shift,
    effect_stat_rows,
    effect_total_cost,
    effective_ability,
    effective_effect_stats,
    granted_advantage_selections,
    granted_advantages,
    granted_skill_rows,
    group_array_base_index,
    live_powers,
    modifier_label,
    node_cost,
    node_display_cost,
    power_allocation_violations,
    power_display_name,
    power_game_terms,
    power_has_custom_modifier,
    power_has_standing_effect,
    power_linked_range_violations,
    power_modifier_requirement_violations,
    power_pl_violations,
    power_runtime_gates,
    power_strength_amount_violations,
    power_total_cost,
    power_trait_allocation_violations,
    power_trait_bonuses,
    powers_points_spent,
    resistance_total,
    selection_band,
    skill_bonus,
    skill_points_spent,
    skill_total,
    synced_effect_rank,
    trait_display_name,
    trait_rate,
)


def test_base_effect_cost_is_per_rank() -> None:
    data = load_game_data()
    # Damage is 1 PP/rank; rank 8 with no modifiers costs 8.
    effect = PowerEffectInstance("damage", rank=8)
    assert effect_total_cost(effect, data) == 8


def test_allocation_used_sums_selected_tier_costs() -> None:
    data = load_game_data()
    # Enhanced Senses: Accurate at tier 1 (2 ranks) + Acute at tier 2 (2 ranks) = 4.
    effect = PowerEffectInstance(
        "enhanced_senses",
        rank=4,
        config={"senses": [{"id": "accurate", "tier": 1}, {"id": "acute", "tier": 2}]},
    )
    assert effect_allocation_used(effect, data) == 4
    assert power_allocation_violations(Power(effects=[effect]), data) == []


def test_over_allocation_is_flagged() -> None:
    data = load_game_data()
    effect = PowerEffectInstance(
        "comprehend", rank=3, config={"categories": [{"id": "languages", "tier": 4}]}
    )
    assert effect_allocation_used(effect, data) == 4
    violations = power_allocation_violations(Power(effects=[effect]), data)
    assert len(violations) == 1 and "over budget" in violations[0]


def test_repeatable_immunity_sums_ranks_and_feature_counts_rows() -> None:
    data = load_game_data()
    immunity = PowerEffectInstance(
        "immunity", rank=10, config={"scopes": [{"name": "Fire", "rank": 10}]}
    )
    assert effect_allocation_used(immunity, data) == 10
    feature = PowerEffectInstance(
        "feature", rank=1, config={"features": [{"name": "Battery"}, {"name": "Remote"}]}
    )
    assert effect_allocation_used(feature, data) == 2  # one per row
    assert power_allocation_violations(Power(effects=[feature]), data)  # 2 rows > rank 1


def test_allocation_choices_appear_in_game_terms() -> None:
    data = load_game_data()
    effect = PowerEffectInstance(
        "comprehend", rank=4, config={"categories": [{"id": "languages", "tier": 3}]}
    )
    line = effect_game_terms(effect, data)
    assert "Languages 3" in line


def test_growth_readout_maps_rank_to_size_table_modifiers() -> None:
    data = load_game_data()
    rows = {r.label: r for r in effect_readout_rows(PowerEffectInstance("growth", rank=2), data)}
    assert rows["Size"].value == "Huge"
    assert rows["Damage"].value == "+2" and rows["Damage"].change == "better"
    assert rows["Defense"].value == "-2" and rows["Defense"].change == "worse"
    # Shrinking is the same table in the opposite direction.
    shrink = {
        r.label: r for r in effect_readout_rows(PowerEffectInstance("shrinking", rank=2), data)
    }
    assert shrink["Size"].value == "Tiny"
    assert shrink["Stealth"].value == "+4" and shrink["Stealth"].change == "better"


def test_a_growth_readout_is_relative_to_the_character_growing() -> None:
    """A Small character's Growth 2 makes them Large — the card used to say Huge."""
    from mm_companion.core.character import Character

    data = load_game_data()
    char = Character.new_default(data)
    char.characteristics["size"] = "Small"

    rows = {
        r.label: r for r in effect_readout_rows(PowerEffectInstance("growth", rank=2), data, char)
    }
    assert rows["Size"].value == "Large"
    # The modifiers are the *delta* between where they land and where they started.
    assert rows["Toughness"].value == "+2"


def test_a_growth_readout_past_the_table_shows_only_what_it_really_gains() -> None:
    from mm_companion.core.character import Character

    data = load_game_data()
    char = Character.new_default(data)
    char.characteristics["size"] = "Colossal"  # +4, one rung off the top

    rows = {
        r.label: r for r in effect_readout_rows(PowerEffectInstance("growth", rank=2), data, char)
    }
    assert rows["Size"].value == "Awesome"
    assert rows["Toughness"].value == "+1"


# -- size scales what the character's own body drives -----------------------------


def test_size_raises_a_resisted_effects_rank() -> None:
    from mm_companion.core.character import Character

    data = load_game_data()
    char = Character.new_default(data)
    effect = PowerEffectInstance("damage", rank=8)

    char.characteristics["size"] = "Huge"
    assert effect_size_rank_shift(effect, data, char) == 2
    assert effect_effective_rank(effect, data, char) == 10

    char.characteristics["size"] = "Small"
    assert effect_effective_rank(effect, data, char) == 7


def test_size_leaves_an_effect_that_forces_no_resistance_alone() -> None:
    from mm_companion.core.character import Character

    data = load_game_data()
    char = Character.new_default(data)
    char.characteristics["size"] = "Huge"

    assert effect_size_rank_shift(PowerEffectInstance("flight", rank=4), data, char) == 0


def test_the_extended_setting_switches_size_scaling_off() -> None:
    from mm_companion.core.character import Character

    data = load_game_data()
    char = Character.new_default(data)
    char.characteristics["size"] = "Huge"
    effect = PowerEffectInstance("damage", rank=8, size_scales_damage=False)

    assert effect_size_rank_shift(effect, data, char) == 0
    assert effect_effective_rank(effect, data, char) == 8


def test_size_scaling_costs_nothing_and_needs_a_wielder() -> None:
    data = load_game_data()
    effect = PowerEffectInstance("damage", rank=8)

    assert effect_size_rank_shift(effect, data, None) == 0
    assert effect_total_cost(effect, data) == 8


def test_the_size_switch_round_trips_and_defaults_on_for_an_older_save() -> None:
    effect = PowerEffectInstance("damage", rank=8)
    assert "size_scales_damage" not in effect.to_dict()  # nothing written while it is on
    assert PowerEffectInstance.from_dict({"effect_id": "damage"}).size_scales_damage is True

    effect.size_scales_damage = False
    assert PowerEffectInstance.from_dict(effect.to_dict()).size_scales_damage is False


def test_state_readout_clamps_above_the_table() -> None:
    data = load_game_data()

    def state(rank: int) -> str:
        return effect_readout_rows(PowerEffectInstance("insubstantial", rank=rank), data)[0].value

    assert state(3) == "Energy"
    assert state(9) == "Incorporeal"  # clamped to the highest defined rank


def test_illusion_maintenance_readout_flips_on_the_moving_checkbox() -> None:
    data = load_game_data()
    static = effect_readout_rows(PowerEffectInstance("illusion", rank=4), data)[0]
    moving = effect_readout_rows(
        PowerEffectInstance("illusion", rank=4, config={"moving": True}), data
    )[0]
    assert "Sustain" in static.value
    assert "Concentrate" in moving.value


def test_checkbox_config_does_not_crash_game_terms() -> None:
    data = load_game_data()
    # Illusion's 'moving' is a bare boolean config — it must not reach _config_display.
    line = effect_game_terms(PowerEffectInstance("illusion", rank=4, config={"moving": True}), data)
    assert line.startswith("Illusion 4")
    assert "moving" not in line.lower()  # surfaced via the readout, not the term line


def test_side_effect_toggle_changes_the_per_rank_discount() -> None:
    data = load_game_data()
    # Damage 8: on-failure Side Effect is -1/rank (net 0 → 4 PP); always is -2/rank
    # (net -1 → ceil(8/3) = 3 PP).
    on_failure = PowerEffectInstance("damage", rank=8, flaws=[ModifierSelection("side_effect")])
    always = PowerEffectInstance(
        "damage", rank=8, flaws=[ModifierSelection("side_effect", config={"when": "always"})]
    )
    assert effect_total_cost(on_failure, data) == 4
    assert effect_total_cost(always, data) == 3


def test_removable_tier_changes_the_flat_discount() -> None:
    data = load_game_data()
    # Protection 10: Removable is -1 flat by default, Easily Removable -2.
    default = PowerEffectInstance("protection", rank=10, flaws=[ModifierSelection("removable")])
    easily = PowerEffectInstance(
        "protection",
        rank=10,
        flaws=[ModifierSelection("removable", config={"tier": "easily_removable"})],
    )
    assert effect_total_cost(default, data) == 9
    assert effect_total_cost(easily, data) == 8


def test_subtle_points_config_sets_the_flat_cost() -> None:
    data = load_game_data()
    # Subtle is a flat extra worth 1 or 2 points, dialed on a points spin box.
    # Damage 8 (8 PP) + Subtle defaults to +1 flat, and +2 when set to 2.
    default = PowerEffectInstance("damage", rank=8, extras=[ModifierSelection("subtle")])
    two = PowerEffectInstance(
        "damage", rank=8, extras=[ModifierSelection("subtle", config={"points": 2})]
    )
    assert effect_total_cost(default, data) == 9
    assert effect_total_cost(two, data) == 10


def test_custom_extra_per_rank_scales_with_effect_rank_only() -> None:
    data = load_game_data()
    # Custom Extra in per-rank mode charges its points per rank of the effect; its own
    # rank spin is ignored (it already scales with the effect). Damage 5 (5) + 2/rank = 15.
    effect = PowerEffectInstance(
        "damage",
        rank=5,
        extras=[
            ModifierSelection("custom_extra", rank=3, config={"points": 2, "mode": "per_rank"})
        ],
    )
    assert effect_total_cost(effect, data) == 15


def test_custom_extra_flat_multiplies_by_its_own_rank() -> None:
    data = load_game_data()
    # In flat mode the points are charged once, times the modifier's own rank.
    # Damage 5 (5) + 2 points x rank 3 = 6 flat -> 11.
    effect = PowerEffectInstance(
        "damage",
        rank=5,
        extras=[ModifierSelection("custom_extra", rank=3, config={"points": 2, "mode": "flat"})],
    )
    assert effect_total_cost(effect, data) == 11


def test_custom_flaw_subtracts_points() -> None:
    data = load_game_data()
    # A flat Custom Flaw subtracts its points x its rank. Damage 8 (8) - 2 x 2 = 4.
    flat = PowerEffectInstance(
        "damage",
        rank=8,
        flaws=[ModifierSelection("custom_flaw", rank=2, config={"points": 2, "mode": "flat"})],
    )
    assert effect_total_cost(flat, data) == 4
    # A per-rank Custom Flaw of 1 halves the per-rank cost (Damage's 1/rank net 0 -> ceil).
    per_rank = PowerEffectInstance(
        "damage",
        rank=8,
        flaws=[ModifierSelection("custom_flaw", config={"points": 1, "mode": "per_rank"})],
    )
    assert effect_total_cost(per_rank, data) == 4


def test_custom_modifier_leaves_game_terms_unchanged() -> None:
    data = load_game_data()
    plain = PowerEffectInstance("damage", rank=5)
    with_custom = PowerEffectInstance(
        "damage",
        rank=5,
        extras=[ModifierSelection("custom_extra", config={"name": "Warp", "points": 1})],
    )
    # A custom modifier only moves points — the effect's derived game terms are identical.
    assert effect_game_terms(with_custom, data) == effect_game_terms(plain, data)


def test_custom_modifier_label_leads_with_typed_name() -> None:
    data = load_game_data()
    modifier = data.modifier_catalog()["custom_extra"]
    named = ModifierSelection("custom_extra", rank=2, config={"name": "Warp Field"})
    assert modifier_label(modifier, named) == "Warp Field 2"
    # Falls back to the record name until the player types one.
    assert modifier_label(modifier, ModifierSelection("custom_extra")) == "Custom Extra"


def test_power_has_custom_modifier_detects_homebrew() -> None:
    data = load_game_data()
    custom = Power(
        name="Homebrew",
        effects=[
            PowerEffectInstance(
                "damage", rank=5, extras=[ModifierSelection("custom_extra", config={"points": 1})]
            )
        ],
    )
    plain = Power(name="Vanilla", effects=[PowerEffectInstance("damage", rank=5)])
    assert power_has_custom_modifier(custom, data) is True
    assert power_has_custom_modifier(plain, data) is False


def test_power_display_name_falls_back_to_effect_names() -> None:
    data = load_game_data()
    named = Power(name="Fire Blast", effects=[PowerEffectInstance("damage", rank=8)])
    assert power_display_name(named, data) == "Fire Blast"
    unnamed = Power(
        effects=[PowerEffectInstance("damage", rank=8), PowerEffectInstance("flight", rank=2)]
    )
    assert power_display_name(unnamed, data) == "Damage / Flight"
    assert power_display_name(Power(), data) == "Unnamed Power"


def test_limited_while_insubstantial_gates_on_an_active_insubstantial_power() -> None:
    data = load_game_data()
    boost = PowerEffectInstance(
        "enhanced_trait",
        rank=3,
        config={"target": "STR"},
        flaws=[ModifierSelection("limited_while_insubstantial")],
    )
    boost_power = Power(name="Ghostly Might", effects=[boost])
    char = _char_with(boost_power)

    base = {e.id: e for e in data.effects}["enhanced_trait"]
    # No Insubstantial power on the sheet: the gate blocks the bonus.
    assert effect_is_active(boost_power, boost, base, data, char) is False
    assert "STR" not in power_trait_bonuses(char, data)["ability"]

    ghost = Power(name="Ghost Form", effects=[PowerEffectInstance("insubstantial", rank=1)])
    char.powers.append(ghost)
    # An active Insubstantial power satisfies the gate.
    assert effect_is_active(boost_power, boost, base, data, char) is True
    assert power_trait_bonuses(char, data)["ability"]["STR"].amount == 3

    # Turning the Insubstantial effect off drops the bonus again.
    ghost.effects[0].toggled_on = False
    assert effect_is_active(boost_power, boost, base, data, char) is False
    assert "STR" not in power_trait_bonuses(char, data)["ability"]


def test_modifier_config_round_trips_through_json() -> None:
    data = load_game_data()
    effect = PowerEffectInstance(
        "damage",
        rank=8,
        flaws=[ModifierSelection("side_effect", config={"when": "always", "detail": "prone"})],
    )
    restored = PowerEffectInstance.from_dict(effect.to_dict())
    assert restored.flaws[0].config == {"when": "always", "detail": "prone"}
    assert effect_total_cost(restored, data) == 3


def test_per_rank_extra_scales_with_rank() -> None:
    data = load_game_data()
    # Damage (1/rank) + Ranged (+1/rank) at rank 8 => (1 + 1) * 8 = 16.
    effect = PowerEffectInstance("damage", rank=8, extras=[ModifierSelection("ranged")])
    assert effect_total_cost(effect, data) == 16


def test_flat_extra_adds_once() -> None:
    data = load_game_data()
    # Damage 5 + Accurate (flat +1) => 1 * 5 + 1 = 6.
    effect = PowerEffectInstance("damage", rank=5, extras=[ModifierSelection("accurate")])
    assert effect_total_cost(effect, data) == 6


def test_sub_one_per_rank_cost_becomes_a_ceiled_fraction() -> None:
    data = load_game_data()
    # Damage (1/rank) with two per-rank flaws is 1 - 1 - 1 = -1/rank; below 1/rank
    # M&M charges 1 point per (2 - net) = 3 ranks, so rank 4 costs ceil(4/3) = 2.
    effect = PowerEffectInstance(
        "damage",
        rank=4,
        flaws=[ModifierSelection("limited"), ModifierSelection("distracting")],
    )
    assert effect_total_cost(effect, data) == 2


def test_single_per_rank_flaw_halves_the_cost() -> None:
    data = load_game_data()
    # Damage (1/rank) - Limited (1/rank) = 0/rank => 1 point per 2 ranks.
    effect = PowerEffectInstance("damage", rank=8, flaws=[ModifierSelection("limited")])
    assert effect_total_cost(effect, data) == 4


def test_ranked_flat_extra_multiplies_by_its_own_rank() -> None:
    data = load_game_data()
    # Damage 5 + Accurate (ranked flat, +1) bought at rank 3 => 1*5 + 1*3 = 8.
    effect = PowerEffectInstance("damage", rank=5, extras=[ModifierSelection("accurate", rank=3)])
    assert effect_total_cost(effect, data) == 8
    assert effect_cost_formula(effect, data) == "5 × 1 + 3"


def test_unranked_modifier_ignores_its_rank() -> None:
    data = load_game_data()
    # Ranged is per-rank (not ranked); a stray rank on the selection is ignored.
    effect = PowerEffectInstance("damage", rank=8, extras=[ModifierSelection("ranged", rank=5)])
    assert effect_total_cost(effect, data) == 16  # (1 + 1) * 8, not affected by rank=5


def test_unknown_effect_costs_nothing() -> None:
    data = load_game_data()
    assert effect_total_cost(PowerEffectInstance("nonesuch", rank=5), data) == 0


def test_effect_specific_modifier_counts_toward_cost() -> None:
    data = load_game_data()
    # Damage 8 + the Damage-specific Strength-Based extra (+0) + general Ranged (+1).
    effect = PowerEffectInstance(
        "damage",
        rank=8,
        extras=[ModifierSelection("strength_based"), ModifierSelection("ranged")],
    )
    assert effect_cost_formula(effect, data) == "8 × (1 + 0 + 1)"
    assert effect_total_cost(effect, data) == 16


def test_strength_based_folds_ability_into_per_rank_modifier_cost() -> None:
    data = load_game_data()
    char = Character()
    char.abilities["STR"] = 4
    # Damage 5 + Strength-Based + Ranged (+1/rank). Ranged puts the effect at 2 points
    # per rank, so the §4 divisor lets only floor(4 / 2) = 2 of the wielder's Strength
    # arrive. The bought ranks pay base + mods; the folded-in ranks pay the mods but
    # not the base: 5 × (1 + 1) + 2 × 1 = 12.
    effect = PowerEffectInstance(
        "damage",
        rank=5,
        extras=[ModifierSelection("strength_based"), ModifierSelection("ranged")],
    )
    assert effect_total_cost(effect, data, char) == 12
    assert effect_cost_formula(effect, data, char) == "5 × (1 + 0 + 1) + 2 × (0 + 1)"
    # Without a character (or Strength) the folded ranks are unknown, so only the
    # bought ranks are priced.
    assert effect_total_cost(effect, data) == 10


def test_plain_strength_based_damage_leaves_folded_ranks_free() -> None:
    data = load_game_data()
    char = Character()
    char.abilities["STR"] = 4
    # No other per-rank modifier, so the Strength ranks add no cost — only rank.
    effect = PowerEffectInstance("damage", rank=5, extras=[ModifierSelection("strength_based")])
    assert effect_total_cost(effect, data, char) == 5
    assert effect_cost_formula(effect, data, char) == "5 × (1 + 0)"


def test_effect_specific_ranked_flat_modifier_scales_with_its_own_rank() -> None:
    data = load_game_data()
    # Teleport 10 (2/rank) + the Teleport-specific Increased Mass (flat per rank) at rank 3.
    effect = PowerEffectInstance(
        "teleport", rank=10, extras=[ModifierSelection("increased_mass_teleport", rank=3)]
    )
    assert effect_total_cost(effect, data) == 23  # 2*10 + 1*3


def test_effect_specific_flaw_and_extra_combine() -> None:
    data = load_game_data()
    # Flight 6 (2/rank) + Safe Landing (+1 flat) - Rocket (-1/rank) => 6*(2-1) + 1 = 7.
    effect = PowerEffectInstance(
        "flight",
        rank=6,
        extras=[ModifierSelection("safe_landing")],
        flaws=[ModifierSelection("rocket")],
    )
    assert effect_total_cost(effect, data) == 7


def test_formula_shows_bare_base_when_no_modifiers() -> None:
    data = load_game_data()
    effect = PowerEffectInstance("damage", rank=8)
    assert effect_cost_formula(effect, data) == "8 × 1"


def test_formula_parenthesises_per_rank_modifiers() -> None:
    data = load_game_data()
    # Damage (1/rank) + Ranged (+1/rank) at rank 8.
    effect = PowerEffectInstance("damage", rank=8, extras=[ModifierSelection("ranged")])
    assert effect_cost_formula(effect, data) == "8 × (1 + 1)"


def test_formula_appends_flat_modifiers_outside_the_group() -> None:
    data = load_game_data()
    # Damage 5 + Accurate (flat +1); flat term sits outside the rank multiplier.
    effect = PowerEffectInstance("damage", rank=5, extras=[ModifierSelection("accurate")])
    assert effect_cost_formula(effect, data) == "5 × 1 + 1"


def test_formula_annotates_sub_one_group_with_its_fraction() -> None:
    data = load_game_data()
    # Raw terms stay visible; the group is tagged with the 1/3 per-rank it resolves to.
    effect = PowerEffectInstance(
        "damage",
        rank=4,
        flaws=[ModifierSelection("limited"), ModifierSelection("distracting")],
    )
    assert effect_cost_formula(effect, data) == "4 × (1 − 1 − 1 = 1/3)"


def test_formula_is_empty_for_unknown_effect() -> None:
    data = load_game_data()
    assert effect_cost_formula(PowerEffectInstance("nonesuch", rank=5), data) == ""


def test_game_terms_render_base_effect_stats() -> None:
    data = load_game_data()
    # Affliction is a Close-range Attack; the summary reflects its base stats.
    line = effect_game_terms(PowerEffectInstance("affliction", rank=4), data)
    assert line.startswith("Affliction 4: ")
    assert "Attack" in line and "Close range" in line and "Instant duration" in line


def test_ranged_modifier_overrides_the_effect_range_in_game_terms() -> None:
    data = load_game_data()
    # Ranged forces range to Ranged, replacing Affliction's Close base.
    effect = PowerEffectInstance("affliction", rank=4, extras=[ModifierSelection("ranged")])
    assert "Ranged range" in effect_game_terms(effect, data)
    assert "Close range" not in effect_game_terms(effect, data)


def test_ranged_overrides_perception_range_too() -> None:
    data = load_game_data()
    # Mind Reading is Perception range; Ranged drops it to Ranged.
    effect = PowerEffectInstance("mind_reading", rank=6, extras=[ModifierSelection("ranged")])
    assert effective_effect_stats(effect, data)["range"] == "Ranged"


def test_effect_stat_rows_flag_no_change_on_a_bare_effect() -> None:
    data = load_game_data()
    rows = {r.key: r for r in effect_stat_rows(PowerEffectInstance("affliction", rank=4), data)}
    # Every base stat renders, and with no modifiers none is tinted.
    assert rows["range"].value == "Close" and rows["range"].base == "Close"
    assert all(r.change == "" for r in rows.values())


def test_effect_stat_rows_tint_an_extra_better_and_a_flaw_worse() -> None:
    data = load_game_data()
    effect = PowerEffectInstance(
        "affliction",
        rank=4,
        extras=[ModifierSelection("sustained_extra")],  # duration Instant -> Sustained
        flaws=[ModifierSelection("close_flaw")],  # range Close -> Close (no real change)
    )
    rows = {r.key: r for r in effect_stat_rows(effect, data)}
    assert rows["duration"].value == "Sustained"
    assert rows["duration"].base == "Instant"
    assert rows["duration"].change == "better"  # an extra improved it
    # A modifier that lands back on the base value isn't reported as a change.
    assert rows["range"].value == "Close" and rows["range"].change == ""


def test_effect_stat_rows_tint_a_stat_a_flaw_limits_worse() -> None:
    data = load_game_data()
    # Concentration flaw drops Affliction's Instant duration to Concentration.
    effect = PowerEffectInstance(
        "affliction", rank=4, flaws=[ModifierSelection("concentration_flaw")]
    )
    duration = next(r for r in effect_stat_rows(effect, data) if r.key == "duration")
    assert duration.value == "Concentration"
    assert duration.change == "worse"  # a flaw limited it


def test_effect_stat_rows_append_configured_conditions_untinted() -> None:
    data = load_game_data()
    effect = PowerEffectInstance("affliction", rank=4, config={"degree1": ["dazed", "vulnerable"]})
    rows = {r.key: r for r in effect_stat_rows(effect, data)}
    assert rows["degree1"].value == "Dazed + Vulnerable"
    assert rows["degree1"].change == ""  # a player choice, not a modifier


def test_effect_stat_rows_add_a_movement_speed_measure() -> None:
    data = load_game_data()
    # Speed rank 2 covers distance rank 2 (30 ft) per round.
    rows = {r.key: r for r in effect_stat_rows(PowerEffectInstance("speed", rank=2), data)}
    assert rows["measure"].label == "Speed"
    assert rows["measure"].value == "30 feet/round"
    # Rank drives it — bumping the rank moves the measure up the table.
    rows = {r.key: r for r in effect_stat_rows(PowerEffectInstance("flight", rank=5), data)}
    assert rows["measure"].value == "250 feet/round"


def test_effect_stat_rows_measure_without_per_round_has_no_suffix() -> None:
    data = load_game_data()
    # Leaping is a one-off jump distance, not a per-round speed.
    rows = {r.key: r for r in effect_stat_rows(PowerEffectInstance("leaping", rank=3), data)}
    assert rows["measure"].label == "Leap"
    assert rows["measure"].value == "60 feet"


def test_effect_stat_rows_render_a_rank_range_as_a_distance() -> None:
    data = load_game_data()
    # Teleport's "Rank" range is a distance equal to its rank on the measures table.
    rows = {r.key: r for r in effect_stat_rows(PowerEffectInstance("teleport", rank=5), data)}
    assert rows["range"].value == "250 feet"
    assert rows["range"].change == ""  # a rank readout, not a modifier change


def test_effect_stat_rows_fill_in_the_attack_bonus_and_save_dc() -> None:
    data = load_game_data()
    rows = {r.key: r for r in effect_stat_rows(PowerEffectInstance("damage", rank=8), data)}
    # Attack bonus reads as the effect rank; Damage's Toughness DC is 10 + rank.
    assert rows["check"].value == "8 vs. Defense"
    assert rows["resistance"].value == "Toughness vs. 18"


def test_effect_stat_rows_use_a_ten_base_dc_for_non_damage() -> None:
    data = load_game_data()
    rows = {r.key: r for r in effect_stat_rows(PowerEffectInstance("mind_reading", rank=5), data)}
    assert rows["resistance"].value == "Will vs. 15"  # 10 + rank


def test_attack_roll_shows_the_characters_attack_when_given() -> None:
    data = load_game_data()
    char = Character.new_default(data)
    char.abilities["ATK"] = 6

    effect = PowerEffectInstance("damage", rank=8)
    rows = {r.key: r for r in effect_stat_rows(effect, data, char)}
    # The attack roll is the character's Attack, not the effect rank; the DC still
    # tracks the rank.
    assert rows["check"].value == "6 vs. Defense"
    assert rows["resistance"].value == "Toughness vs. 18"


def test_attack_roll_adds_accurate_over_the_characters_attack() -> None:
    data = load_game_data()
    char = Character.new_default(data)
    char.abilities["ATK"] = 6

    effect = PowerEffectInstance("damage", rank=8, extras=[ModifierSelection("accurate")])
    check = next(r for r in effect_stat_rows(effect, data, char) if r.key == "check")
    assert check.base == "6 vs. Defense"  # Attack alone
    assert check.value == "8 vs. Defense"  # + Accurate 2
    assert check.change == "better"


def test_non_attack_roll_still_uses_the_effect_rank() -> None:
    data = load_game_data()
    char = Character.new_default(data)
    char.abilities["ATK"] = 6

    # Nullify resolves "Effect vs. Will" — its own rank, never the character's Attack.
    rows = {r.key: r for r in effect_stat_rows(PowerEffectInstance("nullify", rank=7), data, char)}
    assert rows["resistance"].value == "7 vs. Will or rank"


def test_effect_stat_rows_opposed_effect_uses_rank_as_the_threshold() -> None:
    data = load_game_data()
    # Move Object is resisted by Strength against its effective Strength (its rank).
    rows = {r.key: r for r in effect_stat_rows(PowerEffectInstance("move_object", rank=6), data)}
    assert rows["resistance"].value == "Strength vs. 6"  # base 0 + rank


def test_effect_stat_rows_append_dc_to_a_config_chosen_resistance() -> None:
    data = load_game_data()
    effect = PowerEffectInstance("affliction", rank=4, config={"resistance": "Will"})
    rows = {r.key: r for r in effect_stat_rows(effect, data)}
    assert rows["resistance"].value == "Will vs. DC 14"  # chosen resistance keeps the DC


def test_effect_stat_rows_leave_dc_less_effects_as_prose() -> None:
    data = load_game_data()
    # Nullify is opposed (no static DC); the actor roll still resolves to its rank.
    rows = {r.key: r for r in effect_stat_rows(PowerEffectInstance("nullify", rank=7), data)}
    assert rows["resistance"].value == "7 vs. Will or rank"


def test_effect_stat_rows_accurate_raises_and_tints_the_attack_roll() -> None:
    data = load_game_data()
    # Accurate is +2 attack per its own rank; at rank 2 that is +4 over the base.
    effect = PowerEffectInstance("damage", rank=8, extras=[ModifierSelection("accurate", rank=2)])
    check = next(r for r in effect_stat_rows(effect, data) if r.key == "check")
    assert check.base == "8 vs. Defense"
    assert check.value == "12 vs. Defense"
    assert check.change == "better"


def test_effect_stat_rows_inaccurate_lowers_and_tints_the_attack_roll() -> None:
    data = load_game_data()
    effect = PowerEffectInstance("damage", rank=8, flaws=[ModifierSelection("inaccurate")])
    check = next(r for r in effect_stat_rows(effect, data) if r.key == "check")
    assert check.value == "6 vs. Defense"  # 8 - 2
    assert check.change == "worse"


def test_effect_stat_rows_perception_range_drops_the_attack_roll() -> None:
    data = load_game_data()
    # Perception Range forces range to Perception and removes the attack roll.
    effect = PowerEffectInstance("damage", rank=8, extras=[ModifierSelection("perception_range")])
    rows = {r.key: r for r in effect_stat_rows(effect, data)}
    assert "check" not in rows  # no attack roll row at all
    assert rows["range"].value == "Perception" and rows["range"].change == "better"
    assert rows["resistance"].value == "Toughness vs. 18"  # target still resists


def test_implicit_attack_modifier_renders_damage_untinted() -> None:
    data = load_game_data()
    # Damage's attack roll comes from the implicit "attack" extra rather than a check
    # written on the record — but that is an invisible refactor: the rows must read
    # exactly as if it were, with no tint marking a modifier win.
    rows = {r.key: r for r in effect_stat_rows(PowerEffectInstance("damage", rank=8), data)}
    assert rows["check"].value == rows["check"].base == "8 vs. Defense"
    assert rows["check"].change == ""
    assert rows["effect_type"].value == rows["effect_type"].base == "Attack"
    assert rows["effect_type"].change == ""


def test_implicit_attack_modifier_is_not_costed_or_noted() -> None:
    data = load_game_data()
    # The implicit extra never sits on the instance, so it costs nothing and is not
    # listed among the effect's modifiers.
    effect = PowerEffectInstance("damage", rank=8)
    assert effect_total_cost(effect, data) == 8  # 1/rank, unchanged by the implicit extra
    assert "notes" not in {r.key for r in effect_stat_rows(effect, data)}
    assert "Attack" not in effect_cost_formula(effect, data)


def test_attack_extra_grants_an_attack_roll_to_a_non_attacking_effect() -> None:
    data = load_game_data()
    # The point of making Attack a modifier: any effect can take it. Flight normally
    # makes no attack roll; the +0 extra gives it one, tinted as the extra's win.
    effect = PowerEffectInstance("flight", rank=8, extras=[ModifierSelection("attack")])
    rows = {r.key: r for r in effect_stat_rows(effect, data)}
    assert rows["check"].value == "8 vs. Defense"
    assert rows["check"].base == ""  # bare Flight has no check at all
    assert rows["check"].change == "better"
    assert rows["effect_type"].value == "Attack" and rows["effect_type"].change == "better"
    assert effect_makes_attack(effect, data) is True
    # +0 points: taking Attack costs the same as the bare effect.
    assert effect_total_cost(effect, data) == effect_total_cost(
        PowerEffectInstance("flight", rank=8), data
    )


def test_deflect_does_not_make_an_attack_roll() -> None:
    data = load_game_data()
    # Regression guard: "Deflect vs. Attack" used to satisfy a substring test for
    # "Attack", wrongly marking Deflect an attack. It rolls against an attack, not one.
    effect = PowerEffectInstance("deflect", rank=8)
    assert effect_makes_attack(effect, data) is False
    check = next(r for r in effect_stat_rows(effect, data) if r.key == "check")
    assert check.value == "8 vs. Attack"  # still displayed, still off the effect's rank


def test_offensive_control_effects_read_as_attack_type() -> None:
    data = load_game_data()
    # Create is filed under Control as a catalog taxonomy, but it does roll to hit, so
    # its implicit Attack extra sets the effective Type row — untinted, it's the base.
    line = effect_game_terms(PowerEffectInstance("create", rank=4), data)
    assert line.startswith("Create 4: Attack,")
    assert effect_makes_attack(PowerEffectInstance("move_object", rank=4), data) is True


def test_effect_stat_rows_area_keeps_the_attack_roll_with_a_note() -> None:
    data = load_game_data()
    effect = PowerEffectInstance("damage", rank=8, extras=[ModifierSelection("area_effect")])
    check = next(r for r in effect_stat_rows(effect, data) if r.key == "check")
    assert check.value == "8 vs. Defense (area; Dodge for half)"


def test_effect_stat_rows_increased_duration_steps_up_the_ladder() -> None:
    data = load_game_data()
    # Damage is Instant; Increased Duration steps it one rung to Concentration.
    effect = PowerEffectInstance("damage", rank=8, extras=[ModifierSelection("increased_duration")])
    duration = next(r for r in effect_stat_rows(effect, data) if r.key == "duration")
    assert duration.base == "Instant"
    assert duration.value == "Concentration"
    assert duration.change == "better"


def test_effect_stat_rows_increased_action_steps_to_a_slower_action() -> None:
    data = load_game_data()
    # Move Object is a Standard action; Increased Action pushes it a rung slower.
    effect = PowerEffectInstance(
        "move_object", rank=6, flaws=[ModifierSelection("increased_action")]
    )
    action = next(r for r in effect_stat_rows(effect, data) if r.key == "action")
    assert action.base == "Standard"
    assert action.value == "Full round"
    assert action.change == "worse"


def test_sustained_extra_raises_a_sub_free_action_to_free() -> None:
    data = load_game_data()
    # Enhanced Senses is Permanent with action "None"; the Sustained extra makes it
    # toggleable, and toggling on / maintaining it takes at least a free action.
    effect = PowerEffectInstance(
        "enhanced_senses", rank=2, extras=[ModifierSelection("sustained_extra")]
    )
    action = next(r for r in effect_stat_rows(effect, data) if r.key == "action")
    assert action.base == "None"
    assert action.value == "Free"
    assert action.change == ""  # a rule consequence, not a modifier win — no tint
    assert "Free action" in effect_game_terms(effect, data)


def test_action_floor_never_lowers_a_slower_activation_action() -> None:
    data = load_game_data()
    # Create is a Standard action, Sustained duration — the free-action floor must
    # only raise a sub-free action, never pull a slower one down to Free.
    action = next(
        r
        for r in effect_stat_rows(PowerEffectInstance("create", rank=3), data)
        if r.key == "action"
    )
    assert action.value == "Standard"


def test_increased_action_steps_from_the_sustained_free_floor() -> None:
    data = load_game_data()
    # Immunity is Permanent/None; the Sustained extra floors its action at Free, and
    # Increased Action must step from that floor (Free -> Simple), not be absorbed by
    # it. Without the floor, the raw None -> Reaction step would be re-floored to Free.
    effect = PowerEffectInstance(
        "immunity",
        rank=10,
        extras=[ModifierSelection("sustained_immunity")],
        flaws=[ModifierSelection("increased_action")],
    )
    action = next(r for r in effect_stat_rows(effect, data) if r.key == "action")
    assert action.value == "Simple"
    assert action.change == "worse"


def test_effect_stat_rows_gather_impactless_modifiers_into_a_notes_row() -> None:
    data = load_game_data()
    # Penetrating and Multiattack change combat resolution the table doesn't model,
    # so they surface in the Notes row rather than silently vanishing.
    effect = PowerEffectInstance(
        "damage",
        rank=8,
        extras=[ModifierSelection("penetrating"), ModifierSelection("multiattack")],
    )
    notes = next(r for r in effect_stat_rows(effect, data) if r.key == "notes")
    assert notes.value == "Penetrating, Multiattack"


def test_notes_row_qualifies_a_modifier_with_its_typed_detail() -> None:
    data = load_game_data()
    # A Limited flaw with a typed circumstance reads "Limited (only at night)" in the
    # Notes row, never a bare "Limited" that hides the restriction the player chose.
    effect = PowerEffectInstance(
        "damage",
        rank=8,
        flaws=[ModifierSelection("limited", config={"condition": "only at night"})],
    )
    notes = next(r for r in effect_stat_rows(effect, data) if r.key == "notes")
    assert notes.value == "Limited (only at night)"

    # Without a typed detail it stays the bare name.
    bare = PowerEffectInstance("damage", rank=8, flaws=[ModifierSelection("limited")])
    notes = next(r for r in effect_stat_rows(bare, data) if r.key == "notes")
    assert notes.value == "Limited"


def test_effect_stat_rows_impactful_modifiers_stay_out_of_the_notes_row() -> None:
    data = load_game_data()
    # Ranged shows in the Range cell, so it is not repeated in Notes; Penetrating is.
    effect = PowerEffectInstance(
        "damage",
        rank=8,
        extras=[ModifierSelection("ranged"), ModifierSelection("penetrating")],
    )
    rows = {r.key: r for r in effect_stat_rows(effect, data)}
    assert rows["range"].value == "Ranged"
    assert rows["notes"].value == "Penetrating"


def test_effect_stat_rows_effect_specific_override_tints_like_a_general_one() -> None:
    data = load_game_data()
    # Deflect's own Aura extra makes it automatic — action Standard -> None, green.
    effect = PowerEffectInstance("deflect", rank=4, extras=[ModifierSelection("aura_deflect")])
    action = next(r for r in effect_stat_rows(effect, data) if r.key == "action")
    assert action.base == "Standard"
    assert action.value == "None"
    assert action.change == "better"


def test_effect_stat_rows_effect_specific_flaw_adds_an_attack_check() -> None:
    data = load_game_data()
    # Fortune Control is a no-attack Perception effect; its Attack Check flaw makes it
    # a ranged attack — a check row appears and the range drops to Ranged, both red.
    effect = PowerEffectInstance(
        "fortune_control", rank=5, flaws=[ModifierSelection("attack_check_fortune")]
    )
    rows = {r.key: r for r in effect_stat_rows(effect, data)}
    assert rows["range"].value == "Ranged" and rows["range"].change == "worse"
    assert rows["check"].value == "5 vs. Defense" and rows["check"].change == "worse"
    # The flaw grants a real attack, not just a check row: it used to show the row
    # while the rules layer still read the base's null check and called it auto-hit.
    assert effect_makes_attack(effect, data) is True


def test_effect_stat_rows_effect_specific_area_notes_the_check() -> None:
    data = load_game_data()
    # Nullify's own Area Effect keeps the attack roll and annotates it.
    effect = PowerEffectInstance(
        "nullify", rank=7, extras=[ModifierSelection("area_effect_nullify")]
    )
    check = next(r for r in effect_stat_rows(effect, data) if r.key == "check")
    assert check.value == "7 vs. Defense (area)"


def test_effect_stat_rows_effect_specific_narrative_modifier_lands_in_notes() -> None:
    data = load_game_data()
    # Affliction's Cumulative extra has no game-term cell, so it surfaces in Notes.
    effect = PowerEffectInstance("affliction", rank=4, extras=[ModifierSelection("cumulative")])
    notes = next(r for r in effect_stat_rows(effect, data) if r.key == "notes")
    assert notes.value == "Cumulative"


def test_affliction_exposes_config_fields() -> None:
    data = load_game_data()
    affliction = next(e for e in data.effects if e.id == "affliction")
    assert [f.key for f in affliction.config_fields] == [
        "resistance",
        "overcomeBy",
        "degree1",
        "degree2",
        "degree3",
    ]


def test_config_resistance_overrides_and_conditions_append_in_game_terms() -> None:
    data = load_game_data()
    effect = PowerEffectInstance(
        "affliction",
        rank=4,
        config={"resistance": "Will", "degree1": ["dazed"], "degree2": ["stunned"]},
    )
    line = effect_game_terms(effect, data)
    assert "(resisted by Will)" in line  # config choice overrides the base resistance
    assert "1st degree: Dazed" in line and "2nd degree: Stunned" in line  # appended
    assert effective_effect_stats(effect, data)["resistance"] == "Will"


def test_multiselect_degree_joins_same_degree_conditions() -> None:
    data = load_game_data()
    # A degree can hold two same-degree conditions instead of escalating.
    effect = PowerEffectInstance("affliction", rank=4, config={"degree1": ["dazed", "vulnerable"]})
    assert "1st degree: Dazed + Vulnerable" in effect_game_terms(effect, data)


def test_power_game_terms_is_one_line_per_effect() -> None:
    data = load_game_data()
    power = Power(
        effects=[PowerEffectInstance("damage", rank=8), PowerEffectInstance("affliction", rank=4)]
    )
    assert power_game_terms(power, data).count("\n") == 1  # two effects, one newline


def test_power_total_sums_its_effects() -> None:
    data = load_game_data()
    power = Power(
        name="Fire",
        effects=[
            PowerEffectInstance("damage", rank=8, extras=[ModifierSelection("ranged")]),
            PowerEffectInstance("damage", rank=5, extras=[ModifierSelection("accurate")]),
        ],
    )
    assert power_total_cost(power, data) == 16 + 6


def test_power_round_trips_through_dict() -> None:
    power = Power(
        name="Fire Blast",
        description="whoosh",
        descriptors=["fire"],
        structure=STRUCTURE_ARRAY,
        activated=False,
        item_present=False,
        effects=[
            PowerEffectInstance(
                "damage",
                rank=8,
                extras=[ModifierSelection("ranged")],
                flaws=[ModifierSelection("limited", rank=1)],
                config={"target": "combat.attack"},
                descriptors=["fire"],
                toggled_on=False,
                suppressed=True,
                attack_skill="Close Combat::Blades",
            )
        ],
    )
    restored = Power.from_dict(power.to_dict())
    assert restored.to_dict() == power.to_dict()
    assert restored.effects[0].extras[0].modifier_id == "ranged"
    assert restored.structure == STRUCTURE_ARRAY
    assert restored.effects[0].attack_skill == "Close Combat::Blades"
    # Runtime on/off state *is* persisted, so a power switched off reopens switched
    # off rather than coming up in a default all-active state.
    assert power.to_dict()["activated"] is False
    assert power.to_dict()["effects"][0]["toggled_on"] is False
    assert restored.activated is False and restored.item_present is False
    assert restored.effects[0].toggled_on is False
    assert restored.effects[0].suppressed is True


def test_a_power_at_its_defaults_writes_no_runtime_keys() -> None:
    """The flags are written only when they say something, so an old save is unmoved.

    That is what makes persisting runtime additive: a power nobody has switched off,
    suppressed or dialled down serializes byte-for-byte as it did before, and a save
    written back then still loads all-active.
    """
    raw = Power(name="Plain", effects=[PowerEffectInstance("protection", rank=4)]).to_dict()
    for key in ("activated", "item_present", "array_active"):
        assert key not in raw
    for key in ("toggled_on", "suppressed", "current_rank"):
        assert key not in raw["effects"][0]

    legacy = Power.from_dict({"name": "Legacy", "effects": [{"effect_id": "protection"}]})
    assert legacy.activated is True and legacy.item_present is True
    assert legacy.effects[0].toggled_on is True and legacy.effects[0].suppressed is False
    assert legacy.effects[0].current_rank is None


def test_a_modifier_at_its_defaults_writes_no_band_keys() -> None:
    """A selection nobody has banded serializes exactly as it did before bands existed."""
    raw = ModifierSelection("tiring").to_dict()
    assert "applies_from" not in raw and "applies_to" not in raw

    legacy = ModifierSelection.from_dict({"modifier_id": "tiring"})
    assert legacy.applies_from == 0 and legacy.applies_to == 0


def test_a_rank_band_round_trips_through_json() -> None:
    selection = ModifierSelection("tiring", applies_from=9, applies_to=12)
    restored = ModifierSelection.from_dict(json.loads(json.dumps(selection.to_dict())))
    assert (restored.applies_from, restored.applies_to) == (9, 12)


def test_structure_defaults_to_independent_and_rejects_junk() -> None:
    assert Power().structure == STRUCTURE_INDEPENDENT
    # A malformed persisted value falls back rather than corrupting cost math.
    assert Power.from_dict({"structure": "nonsense"}).structure == STRUCTURE_INDEPENDENT


def test_linked_power_costs_the_sum_like_independent() -> None:
    data = load_game_data()
    effects = [
        PowerEffectInstance("damage", rank=8, extras=[ModifierSelection("ranged")]),  # 16
        PowerEffectInstance("affliction", rank=4),  # 4
    ]
    independent = Power(effects=list(effects), structure=STRUCTURE_INDEPENDENT)
    linked = Power(effects=list(effects), structure=STRUCTURE_LINKED)
    assert power_total_cost(independent, data) == 20
    assert power_total_cost(linked, data) == 20  # linking is a +0 bundle


def test_array_pays_base_in_full_plus_a_flat_point_per_alternate() -> None:
    data = load_game_data()
    # Damage 8 + Ranged = 16 (the costliest → base); two cheaper alternates at 1 pt each.
    power = Power(
        structure=STRUCTURE_ARRAY,
        effects=[
            PowerEffectInstance("damage", rank=8, extras=[ModifierSelection("ranged")]),  # 16
            PowerEffectInstance("affliction", rank=4),  # 4, alternate
            PowerEffectInstance("move_object", rank=8),  # alternate
        ],
    )
    flat = array_alternate_cost(data)
    assert power_total_cost(power, data) == 16 + 2 * flat


def test_array_base_is_the_costliest_effect_regardless_of_order() -> None:
    data = load_game_data()
    power = Power(
        structure=STRUCTURE_ARRAY,
        effects=[
            PowerEffectInstance("affliction", rank=4),  # cheaper, dropped first
            PowerEffectInstance("damage", rank=8, extras=[ModifierSelection("ranged")]),  # 16
        ],
    )
    assert array_base_index(power, data) == 1  # the Damage effect, not the first one


def test_array_with_a_single_effect_is_just_that_effects_cost() -> None:
    data = load_game_data()
    # The structure only bites at two-plus effects; a lone effect pays its own way.
    power = Power(structure=STRUCTURE_ARRAY, effects=[PowerEffectInstance("damage", rank=8)])
    assert power_total_cost(power, data) == 8


def test_array_game_terms_mark_the_base_and_alternates() -> None:
    data = load_game_data()
    power = Power(
        structure=STRUCTURE_ARRAY,
        effects=[
            PowerEffectInstance("damage", rank=8, extras=[ModifierSelection("ranged")]),
            PowerEffectInstance("affliction", rank=4),
        ],
    )
    summary = power_game_terms(power, data)
    assert summary.startswith("Array (one effect active at a time):")
    assert "[base]" in summary
    assert "Alternate Effect" in summary


def test_linked_game_terms_prefix_a_header() -> None:
    data = load_game_data()
    power = Power(
        structure=STRUCTURE_LINKED,
        effects=[PowerEffectInstance("damage", rank=8), PowerEffectInstance("affliction", rank=4)],
    )
    assert power_game_terms(power, data).startswith("Linked (all effects activate together):")


def _pl_char(data, *, atk: int = 0, strength: int = 0, power_level: int = 10) -> Character:
    char = Character.new_default(data)
    char.power_level = power_level
    char.abilities["ATK"] = atk
    char.abilities["STR"] = strength
    return char


def test_pl_violations_flag_an_attack_effect_over_the_cap() -> None:
    data = load_game_data()
    char = _pl_char(data)  # PL 10, no attack bonus → cap of 20 on attack + rank
    at_cap = Power(effects=[PowerEffectInstance("damage", rank=20)])
    assert power_pl_violations(at_cap, char, data) == []

    over = Power(effects=[PowerEffectInstance("damage", rank=21)])
    violations = power_pl_violations(over, char, data)
    assert len(violations) == 1
    assert "rank 21" in violations[0]
    assert "20" in violations[0]  # names the PL 10 cap


def test_pl_violations_add_the_characters_attack_bonus() -> None:
    data = load_game_data()
    # A rank-16 Damage is fine on its own, but the character's Attack 5 pushes
    # attack + rank to 21, over the PL 10 cap of 20.
    power = Power(effects=[PowerEffectInstance("damage", rank=16)])
    assert power_pl_violations(power, _pl_char(data, atk=4), data) == []  # 4 + 16 = 20
    assert power_pl_violations(power, _pl_char(data, atk=5), data)  # 5 + 16 = 21


def test_pl_violations_fold_strength_into_a_strength_based_damage() -> None:
    data = load_game_data()
    # Strength-Based Damage rank 10 + Strength 8 resolves at rank 18; with no attack
    # bonus that's 18 ≤ 20 (fine), but Strength 11 makes it 21 (over).
    effect = PowerEffectInstance("damage", rank=10, extras=[ModifierSelection("strength_based")])
    assert power_pl_violations(Power(effects=[effect]), _pl_char(data, strength=8), data) == []
    over = power_pl_violations(Power(effects=[effect]), _pl_char(data, strength=11), data)
    assert over and "rank 21" in over[0]


def test_strength_based_amount_caps_the_folded_in_strength() -> None:
    data = load_game_data()
    char = _pl_char(data, strength=8)
    # No amount stored → full Strength folds in: rank 10 + 8 = 18.
    full = PowerEffectInstance("damage", rank=10, extras=[ModifierSelection("strength_based")])
    assert effect_effective_rank(full, data, char) == 18
    # amount=3 uses only 3 of the 8 Strength: rank 10 + 3 = 13.
    capped = PowerEffectInstance(
        "damage",
        rank=10,
        extras=[ModifierSelection("strength_based", config={"amount": 3})],
    )
    assert effect_effective_rank(capped, data, char) == 13
    # A stored amount above the wielder's actual Strength never folds in more than it.
    greedy = PowerEffectInstance(
        "damage",
        rank=10,
        extras=[ModifierSelection("strength_based", config={"amount": 20})],
    )
    assert effect_effective_rank(greedy, data, char) == 18


def test_strength_based_cost_uses_the_bought_amount_not_current_strength() -> None:
    data = load_game_data()
    # Strength-Based (folds STR) + Ranged (+1/rank), amount bought = 4. Ranged puts the
    # effect at 2 points per rank, so the §4 divisor lands floor(4 / 2) = 2 of those
    # bought ranks — and the cost pays for them regardless of the wielder's current
    # Strength: 5 × (1 + 1) + 2 × 1 = 12, whether Strength is 4, 8, or 1.
    effect = PowerEffectInstance(
        "damage",
        rank=5,
        extras=[
            ModifierSelection("strength_based", config={"amount": 4}),
            ModifierSelection("ranged"),
        ],
    )
    for strength in (1, 4, 8):
        char = Character()
        char.abilities["STR"] = strength
        assert effect_total_cost(effect, data, char) == 12
        assert effect_cost_formula(effect, data, char) == "5 × (1 + 0 + 1) + 2 × (0 + 1)"
    # The effect *value*, by contrast, still tracks the current (capped) Strength —
    # through the same divisor, so Strength 1 into a 2-point-per-rank effect arrives
    # as floor(min(4, 1) / 2) = 0 and the rank is the bought one.
    weak = Character()
    weak.abilities["STR"] = 1
    assert effect_effective_rank(effect, data, weak) == 5
    strong = Character()
    strong.abilities["STR"] = 8
    assert effect_effective_rank(effect, data, strong) == 7  # 5 + floor(min(4, 8) / 2)


def test_strength_amount_over_strength_is_a_warning() -> None:
    data = load_game_data()
    # amount=8 but the wielder only has Strength 5 → the power pays for 3 ranks it
    # can't fold in. Flagged as a warning (not repriced).
    effect = PowerEffectInstance(
        "damage",
        rank=10,
        extras=[ModifierSelection("strength_based", config={"amount": 8})],
    )
    power = Power(effects=[effect])
    over = power_strength_amount_violations(power, _pl_char(data, strength=5), data)
    assert over and "8 ranks" in over[0]
    # Enough Strength to cover the bought amount → no warning.
    assert power_strength_amount_violations(power, _pl_char(data, strength=8), data) == []
    # A selection that tracks Strength dynamically (no amount stored) never warns.
    tracking = Power(
        effects=[
            PowerEffectInstance("damage", rank=10, extras=[ModifierSelection("strength_based")])
        ]
    )
    assert power_strength_amount_violations(tracking, _pl_char(data, strength=5), data) == []


def test_pl_violations_ignore_non_attack_effects() -> None:
    data = load_game_data()
    # Flight imposes no resistance check, so the attack cap doesn't apply at any rank.
    power = Power(effects=[PowerEffectInstance("flight", rank=30)])
    assert power_pl_violations(power, _pl_char(data), data) == []


def test_pl_violations_count_the_powers_own_accurate_bonus() -> None:
    data = load_game_data()
    # Rank 20 is at the cap, but Accurate adds +2 to the attack, pushing it over.
    effect = PowerEffectInstance("damage", rank=20, extras=[ModifierSelection("accurate")])
    assert power_pl_violations(Power(effects=[effect]), _pl_char(data), data)


def test_pl_violations_respect_inaccurate_trade_off() -> None:
    data = load_game_data()
    # Inaccurate lowers the attack, so a rank-21 Damage trades back under the cap.
    effect = PowerEffectInstance("damage", rank=21, flaws=[ModifierSelection("inaccurate")])
    assert power_pl_violations(Power(effects=[effect]), _pl_char(data), data) == []


def test_effect_attack_skill_bonus_uses_the_focus_total() -> None:
    data = load_game_data()
    char = _pl_char(data, atk=3)
    char.focuses["Close Combat"] = ["Blades"]
    char.skill_ranks["Close Combat::Blades"] = 4
    effect = PowerEffectInstance("damage", attack_skill="Close Combat::Blades")
    # Close Combat is an ATK skill, so its total already folds Attack in: 3 + 4 = 7.
    assert effect_attack_skill_bonus(effect, char, data) == 7
    # No link → None, so the caller falls back to the Attack ability.
    assert effect_attack_skill_bonus(PowerEffectInstance("damage"), char, data) is None


def test_pl_violations_use_the_linked_combat_skill_instead_of_attack() -> None:
    data = load_game_data()
    char = _pl_char(data, atk=2)
    char.focuses["Ranged Combat"] = ["Guns"]
    char.skill_ranks["Ranged Combat::Guns"] = 6  # focus total = ATK 2 + 6 = 8
    effect = PowerEffectInstance("damage", rank=14, attack_skill="Ranged Combat::Guns")
    linked = Power(effects=[effect])
    violations = power_pl_violations(linked, char, data)  # 8 + 14 = 22 > 20
    assert violations and "22" in violations[0]
    # Without the link the bare Attack (2) replaces it: 2 + 14 = 16, under the cap.
    plain = Power(effects=[PowerEffectInstance("damage", rank=14)])
    assert power_pl_violations(plain, char, data) == []


def test_effect_stat_rows_attack_bonus_overrides_the_attack_roll() -> None:
    data = load_game_data()
    char = Character.new_default(data)
    char.abilities["ATK"] = 6
    effect = PowerEffectInstance("damage", rank=8)
    # A linked combat focus passes its total as attack_bonus, replacing Attack 6.
    rows = {r.key: r for r in effect_stat_rows(effect, data, char, attack_bonus=9)}
    assert rows["check"].value == "9 vs. Defense"


# -- trait boosts from powers (Enhanced Trait, Protection) --------------------
#
# The tests in this first block use the *legacy* single-``config['target']`` shape,
# which is exactly why they are left in it: an Enhanced Trait now allocates its rank
# across a list of traits, and every character saved before that must keep boosting and
# costing precisely what it always did. The multi-trait block follows below.


def _char_with(power: Power) -> Character:
    data = load_game_data()
    char = Character.new_default(data)
    char.powers.append(power)
    return char


def test_enhanced_trait_boosts_a_chosen_ability() -> None:
    data = load_game_data()
    char = _char_with(
        Power(
            name="Mighty",
            effects=[PowerEffectInstance("enhanced_trait", rank=3, config={"target": "STR"})],
        )
    )
    char.abilities["STR"] = 2

    bonus = power_trait_bonuses(char, data)["ability"]["STR"]
    assert bonus.amount == 3
    assert bonus.sources == ("Mighty",)
    assert effective_ability(char, data, "STR") == 5  # 2 bought + 3 boost


def test_protection_boosts_toughness_via_its_fixed_target() -> None:
    data = load_game_data()
    char = _char_with(Power(name="Armor", effects=[PowerEffectInstance("protection", rank=5)]))
    # Protection carries no config target — it's baked into the effect's TraitBoost.
    assert resistance_total(char, data, "TOUGHNESS") == 5


def test_enhanced_ability_propagates_into_linked_skill_total() -> None:
    data = load_game_data()
    char = _char_with(
        Power(effects=[PowerEffectInstance("enhanced_trait", rank=4, config={"target": "STR"})])
    )
    char.skill_ranks["Athletics"] = 1  # Athletics is Strength-linked
    assert skill_total(char, data, "Athletics") == 5  # effective STR 4 + 1 rank


# -- runtime effect state: activation / removable / toggle / suppression (§5-7) --


def test_removable_power_bonus_drops_when_item_absent() -> None:
    data = load_game_data()
    power = Power(
        name="Armor",
        effects=[PowerEffectInstance("protection", rank=5, flaws=[ModifierSelection("removable")])],
    )
    char = _char_with(power)
    assert power_runtime_gates(power, data) == {"removable"}
    assert resistance_total(char, data, "TOUGHNESS") == 5  # item present by default
    power.item_present = False  # taken away → the Removable gate switches the bonus off
    assert resistance_total(char, data, "TOUGHNESS") == 0
    power.item_present = True  # restored
    assert resistance_total(char, data, "TOUGHNESS") == 5


def test_activation_gate_requires_the_power_switched_on() -> None:
    data = load_game_data()
    power = Power(
        name="Focus",
        effects=[
            PowerEffectInstance(
                "enhanced_trait",
                rank=3,
                config={"target": "STR"},
                flaws=[ModifierSelection("activation")],
            )
        ],
    )
    char = _char_with(power)
    assert effective_ability(char, data, "STR") == 3
    power.activated = False
    assert effective_ability(char, data, "STR") == 0


def test_suppressed_effect_contributes_no_bonus() -> None:
    data = load_game_data()
    power = Power(name="Armor", effects=[PowerEffectInstance("protection", rank=4)])
    char = _char_with(power)
    assert resistance_total(char, data, "TOUGHNESS") == 4
    power.effects[0].suppressed = True  # a transient Nullify
    assert resistance_total(char, data, "TOUGHNESS") == 0


def test_permanent_ungated_effect_is_always_active() -> None:
    data = load_game_data()
    base = {e.id: e for e in data.effects}["protection"]
    power = Power(effects=[PowerEffectInstance("protection", rank=2)])
    assert power_runtime_gates(power, data) == set()
    assert effect_is_active(power, power.effects[0], base, data) is True


def test_toggle_pattern_follows_the_toggle_switch() -> None:
    data = load_game_data()
    base = {e.id: e for e in data.effects}["flight"]  # a passive_toggle movement effect
    flight = PowerEffectInstance("flight", rank=2)
    power = Power(effects=[flight])
    assert power_runtime_gates(power, data) == {"toggle"}
    assert effect_is_active(power, flight, base, data) is True
    flight.toggled_on = False
    assert effect_is_active(power, flight, base, data) is False


def test_instant_effect_is_never_a_standing_contributor() -> None:
    data = load_game_data()
    base = {e.id: e for e in data.effects}["damage"]
    dmg = PowerEffectInstance("damage", rank=5)
    assert effect_is_active(Power(effects=[dmg]), dmg, base, data) is False


def test_power_has_standing_effect_distinguishes_instant_from_passive() -> None:
    data = load_game_data()
    # A plain attack (instant) contributes nothing standing.
    assert (
        power_has_standing_effect(Power(effects=[PowerEffectInstance("damage", rank=5)]), data)
        is False
    )
    # Protection (passive_permanent) and Flight (passive_toggle) both stand on the sheet.
    assert (
        power_has_standing_effect(Power(effects=[PowerEffectInstance("protection", rank=4)]), data)
        is True
    )
    assert (
        power_has_standing_effect(Power(effects=[PowerEffectInstance("flight", rank=2)]), data)
        is True
    )
    # A mixed power counts as standing if any one effect is.
    mixed = Power(
        effects=[PowerEffectInstance("damage", rank=5), PowerEffectInstance("protection", rank=2)]
    )
    assert power_has_standing_effect(mixed, data) is True


def test_limited_gate_is_informational_and_never_gates() -> None:
    data = load_game_data()
    power = Power(
        name="Sun Power",
        effects=[
            PowerEffectInstance(
                "enhanced_trait",
                rank=3,
                config={"target": "STR"},
                flaws=[ModifierSelection("limited")],
            )
        ],
    )
    char = _char_with(power)
    # Limited is a gate kind the UI surfaces, but the engine never auto-switches it off.
    assert power_runtime_gates(power, data) == {"limited"}
    assert effective_ability(char, data, "STR") == 3


def test_enhanced_trait_can_boost_a_skill_directly() -> None:
    data = load_game_data()
    char = _char_with(
        Power(
            effects=[PowerEffectInstance("enhanced_trait", rank=6, config={"target": "Acrobatics"})]
        )
    )
    # No ranks bought, no linked-ability value: the whole total is the power boost.
    assert skill_total(char, data, "Acrobatics") == 6


def test_skill_bonus_reports_the_boosting_power_as_its_source() -> None:
    data = load_game_data()
    char = _char_with(
        Power(
            name="Cat's Grace",
            effects=[
                PowerEffectInstance("enhanced_trait", rank=6, config={"target": "Acrobatics"})
            ],
        )
    )
    bonus = skill_bonus(char, data, "Acrobatics")
    assert bonus is not None
    assert (bonus.amount, bonus.sources) == (6, ("Cat's Grace",))
    assert skill_bonus(char, data, "Stealth") is None


def test_trait_boosts_from_several_powers_stack() -> None:
    data = load_game_data()
    char = _char_with(
        Power(
            name="A",
            effects=[PowerEffectInstance("enhanced_trait", rank=2, config={"target": "STR"})],
        )
    )
    char.powers.append(
        Power(
            name="B",
            effects=[PowerEffectInstance("enhanced_trait", rank=3, config={"target": "STR"})],
        )
    )
    bonus = power_trait_bonuses(char, data)["ability"]["STR"]
    assert bonus.amount == 5
    assert bonus.sources == ("A", "B")


def test_enhanced_trait_without_a_chosen_target_is_ignored() -> None:
    data = load_game_data()
    char = _char_with(Power(effects=[PowerEffectInstance("enhanced_trait", rank=4)]))  # no config
    assert power_trait_bonuses(char, data) == {"ability": {}, "resistance": {}, "skill": {}}


def test_trait_boost_does_not_change_point_cost() -> None:
    from mm_companion.core.rules import power_points_spent

    data = load_game_data()
    char = _char_with(
        Power(effects=[PowerEffectInstance("enhanced_trait", rank=3, config={"target": "STR"})])
    )
    char.abilities["STR"] = 2
    # STR costs for the 2 *bought* ranks (4 PP), not the boosted 5; the boost is
    # paid by the power's own cost (enhanced_trait 2/rank × 3 = 6).
    assert power_points_spent(char, data) == 2 * 2 + 6


# -- Enhanced Trait's multi-trait allocation ----------------------------------


def _enhanced(rank: int, rows, *, extras=(), flaws=()) -> PowerEffectInstance:
    """An Enhanced Trait allocating ``(trait, ranks)`` pairs out of ``rank``."""

    return PowerEffectInstance(
        "enhanced_trait",
        rank=rank,
        config={"traits": [{"trait": t, "ranks": r} for t, r in rows]},
        extras=[ModifierSelection(m) for m in extras],
        flaws=[ModifierSelection(m) for m in flaws],
    )


def test_one_enhanced_trait_raises_every_allocated_trait() -> None:
    data = load_game_data()
    char = _char_with(
        Power(
            name="Berserker Rage",
            effects=[_enhanced(10, [("STR", 2), ("Treatment", 6), ("Fearless", 2)])],
        )
    )
    char.abilities["STR"] = 3

    assert effective_ability(char, data, "STR") == 5  # 3 bought + 2 allocated
    assert skill_total(char, data, "Treatment") == 6
    granted = granted_advantages(char, data)
    assert granted["Fearless"].amount == 2
    assert granted["Fearless"].sources == ("Berserker Rage",)


def test_each_allocated_trait_is_priced_at_its_own_rate() -> None:
    """The worked example: Strength 2, Treatment 6, Expertise 2, Limited = 4 PP.

    Strength at 2 PP a rank is 4; the eight skill ranks at two a point are 3 + 1; the
    -1/rank Limited then halves the 8.
    """

    data = load_game_data()
    rows = [("STR", 2), ("Treatment", 6), ("Expertise", 2)]
    assert effect_total_cost(_enhanced(10, rows), data) == 8
    assert effect_total_cost(_enhanced(10, rows, flaws=["limited_enhanced_trait"]), data) == 4


def test_sustained_is_free_and_a_second_flaw_quarters_the_cost() -> None:
    data = load_game_data()
    rows = [("STR", 4)]  # 8 PP flat
    assert effect_total_cost(_enhanced(4, rows, extras=["sustained_enhanced_trait"]), data) == 8
    # Two -1/rank flaws take the nominal 2/rank to 0, i.e. 1 point per 2 ranks: a quarter.
    both = _enhanced(4, rows, flaws=["limited_enhanced_trait", "limited_enhanced_trait"])
    assert effect_total_cost(both, data) == 2


def test_one_point_buys_a_rank_in_each_of_two_skills() -> None:
    """Skill ranks pool and round *once*, as the bought skills do.

    A point buys two skill ranks, and those two ranks may go into two different skills —
    charging each row on its own would make the same two ranks cost two points.
    """

    data = load_game_data()
    assert effect_total_cost(_enhanced(2, [("Stealth", 1), ("Treatment", 1)]), data) == 1
    assert effect_total_cost(_enhanced(2, [("Stealth", 2)]), data) == 1


def test_an_unallocated_enhanced_trait_costs_nothing() -> None:
    data = load_game_data()
    assert effect_total_cost(_enhanced(6, []), data) == 0


def test_an_enhanced_traits_rank_is_its_allocation_not_a_budget() -> None:
    """Its rank has no meaning of its own, so it cannot be overspent — see
    :func:`synced_effect_rank`. The budget warning is for the effects that do have one."""

    data = load_game_data()
    power = Power(effects=[_enhanced(3, [("STR", 2), ("Treatment", 7)])])
    assert effect_allocation_used(power.effects[0], data) == 9
    assert synced_effect_rank(power.effects[0], data) == 9
    assert power_allocation_violations(power, data) == []


def test_an_ordinary_allocation_effect_still_warns_when_overspent() -> None:
    data = load_game_data()
    senses = PowerEffectInstance(
        "enhanced_senses",
        rank=2,
        config={"senses": [{"id": "accurate", "tier": 2}, {"id": "acute", "tier": 1}]},
    )
    assert "over budget" in power_allocation_violations(Power(effects=[senses]), data)[0]


def test_a_legacy_single_target_costs_and_boosts_as_it_always_did() -> None:
    """A save written before the allocation existed reads as the one trait it named."""

    data = load_game_data()
    legacy = PowerEffectInstance("enhanced_trait", rank=4, config={"target": "STR"})
    char = _char_with(Power(name="Old", effects=[legacy]))
    assert effective_ability(char, data, "STR") == 4
    assert effect_total_cost(legacy, data) == 8  # 4 ranks at the 2 PP an ability rank costs


def test_a_legacy_skill_target_is_now_priced_as_a_skill() -> None:
    """The bug this change fixes: a boosted skill was charged at 2 PP a rank.

    Enhanced Trait's base cost is "as trait", so four ranks of a skill cost the two
    points four ranks of that skill cost — not the eight a flat 2/rank charged.
    """

    data = load_game_data()
    legacy = PowerEffectInstance("enhanced_trait", rank=4, config={"target": "Stealth"})
    assert effect_total_cost(legacy, data) == 2


def test_an_enhanced_advantage_reaches_the_sheet_without_costing_advantage_points() -> None:
    from mm_companion.core.rules import (
        advantage_points_spent,
        heroic_advantage_ranks,
        powers_points_spent,
    )

    data = load_game_data()
    char = _char_with(Power(name="Rage", effects=[_enhanced(2, [("Fearless", 2)])]))

    assert granted_advantages(char, data)["Fearless"].amount == 2
    # The power paid for it: it is not a bought advantage, so neither the advantage
    # points nor the shared Heroic budget moves.
    assert advantage_points_spent(char, data) == 0
    assert heroic_advantage_ranks(char, data) == 0
    assert powers_points_spent(char, data) == 2  # 2 ranks at the 1 PP an advantage costs


def test_a_granted_advantage_chains_its_own_skill_bonus() -> None:
    """An Enhanced Advantage grants whatever the advantage itself would have granted."""

    import dataclasses

    data = load_game_data()
    granting = dataclasses.replace(
        data.advantages[0], skill_bonus_per_rank=2, skill_bonus_target="Stealth"
    )
    data = dataclasses.replace(data, advantages=(granting, *data.advantages[1:]))

    char = Character.new_default(data)
    char.powers.append(Power(name="Knack", effects=[_enhanced(3, [(granting.name, 3)])]))

    bonus = skill_bonus(char, data, "Stealth")
    assert bonus is not None
    assert bonus.amount == 6  # 3 granted ranks at 2 points of Stealth each
    assert granting.name in bonus.sources[0] and "Knack" in bonus.sources[0]


def test_reduced_trait_discounts_by_what_the_lowered_trait_cost() -> None:
    data = load_game_data()
    reduced = ModifierSelection(
        "reduced_trait", config={"reduced": [{"trait": "DODGE", "ranks": 3}]}
    )
    effect = _enhanced(4, [("STR", 4)])
    effect.flaws.append(reduced)
    assert effect_total_cost(effect, data) == 5  # 8 less the 3 PP three ranks of Dodge cost

    reduced.config["reduced"] = [{"trait": "DODGE", "ranks": 50}]
    assert effect_total_cost(effect, data) == 1  # the rules floor the effect at 1 point


def test_a_trait_allocation_survives_a_save_and_load() -> None:
    """The rows are lists of dicts, not scalars — the one config shape that could break."""

    data = load_game_data()
    effect = _enhanced(6, [("STR", 2), ("Treatment", 4)])
    effect.flaws.append(
        ModifierSelection("reduced_trait", config={"reduced": [{"trait": "DODGE", "ranks": 2}]})
    )
    power = Power(name="Rage", effects=[effect])

    restored = Power.from_dict(json.loads(json.dumps(power.to_dict())))
    assert restored.effects[0].config == effect.config
    assert restored.effects[0].flaws[0].config == effect.flaws[0].config
    assert effect_total_cost(restored.effects[0], data) == effect_total_cost(effect, data)


def test_the_cost_formula_shows_each_traits_own_price() -> None:
    """The footer explains the number beside it — including why the halves pooled."""

    data = load_game_data()
    rows = [("STR", 2), ("Treatment", 6), ("Expertise", 2)]
    assert effect_cost_formula(_enhanced(10, rows), data) == "Abilities 4 + Skills 4"
    limited = _enhanced(10, rows, flaws=["limited_enhanced_trait"])
    assert effect_cost_formula(limited, data) == "(Abilities 4 + Skills 4) × 1/2"
    # A rank in each of two skills is half a point each, and the pooled subtotal is the
    # one point they come to — the run of raw halves this replaced said nothing.
    assert effect_cost_formula(_enhanced(2, [("Stealth", 1), ("Treatment", 1)]), data) == "Skills 1"
    # A subtotal that lands between points keeps its half: the next rank is free from it.
    assert effect_cost_formula(_enhanced(5, [("Stealth", 5)]), data) == "Skills 2 1/2"


def test_the_cost_breakdown_keeps_every_row_apart() -> None:
    """The grouped footer pools; the breakdown behind it does not, so the card can
    explain which trait bought which part of the subtotal."""

    data = load_game_data()
    terms = effect_cost_breakdown(_enhanced(4, [("STR", 2), ("Stealth", 1), ("Fearless", 1)]), data)
    assert [(t.target, t.ranks, t.category, str(t.cost)) for t in terms] == [
        ("STR", 2, "ability", "4"),
        ("Stealth", 1, "skill", "1/2"),
        ("Fearless", 1, "advantage", "1"),
    ]
    # Nothing to break down for an effect priced the ordinary way.
    assert effect_cost_breakdown(PowerEffectInstance("damage", rank=5), data) == ()


def test_the_enhances_row_names_every_allocated_trait() -> None:
    data = load_game_data()
    effect = _enhanced(10, [("STR", 2), ("Treatment", 6)])
    row = next(r for r in effect_stat_rows(effect, data) if r.key == "enhances")
    assert row.value == "Strength +2, Treatment +6"
    assert "Enhances: Strength +2, Treatment +6" in effect_game_terms(effect, data)


def test_linked_effects_with_matching_range_are_clean() -> None:
    data = load_game_data()
    # Two Close-range effects linked together share a Range — no violation.
    power = Power(
        effects=[PowerEffectInstance("damage", rank=5), PowerEffectInstance("affliction", rank=5)],
        structure=STRUCTURE_LINKED,
    )
    assert power_linked_range_violations(power, data) == []


def test_linked_effects_with_mismatched_range_are_flagged() -> None:
    data = load_game_data()
    # Damage is Close, Flight is Personal — linking them is a Range mismatch.
    power = Power(
        effects=[PowerEffectInstance("damage", rank=5), PowerEffectInstance("flight", rank=5)],
        structure=STRUCTURE_LINKED,
    )
    violations = power_linked_range_violations(power, data)
    assert len(violations) == 1
    assert "Flight" in violations[0] and "Range" in violations[0]


def test_range_override_reconciles_a_linked_mismatch() -> None:
    data = load_game_data()
    # A Ranged extra pushes the Damage effect to Ranged range, matching a naturally
    # ranged partner — the override participates in the Range comparison.
    ranged_damage = PowerEffectInstance("damage", rank=5, extras=[ModifierSelection("ranged")])
    move = PowerEffectInstance("move_object", rank=5)  # Ranged by default
    power = Power(effects=[ranged_damage, move], structure=STRUCTURE_LINKED)
    assert power_linked_range_violations(power, data) == []


def test_linked_range_check_ignores_non_linked_structures() -> None:
    data = load_game_data()
    power = Power(
        effects=[PowerEffectInstance("damage", rank=5), PowerEffectInstance("flight", rank=5)],
        structure=STRUCTURE_ARRAY,
    )
    assert power_linked_range_violations(power, data) == []


# -- power groups: the nested tree (independent / array / linked) ----------


def test_legacy_power_without_id_is_migrated() -> None:
    # A power saved before ids existed still round-trips, minted a fresh id.
    clone = Power.from_dict({"name": "Old", "effects": []})
    assert clone.id  # non-empty
    assert clone.alternate_of == "" and clone.linked_with == []


def _character_with_powers(*powers: object) -> Character:
    char = Character()
    char.powers = list(powers)
    return char


def test_power_group_round_trips_and_dispatches() -> None:
    group = PowerGroup(
        mode=STRUCTURE_ARRAY,
        children=[Power(name="Fire"), Power(name="Ice")],
    )
    group.active_child_id = group.children[1].id
    raw = group.to_dict()
    assert raw["kind"] == "group"
    # Which array member is live is runtime state, and persisted with the rest of it.
    assert raw["active_child_id"] == group.children[1].id

    clone = node_from_dict(raw)
    assert isinstance(clone, PowerGroup)
    assert clone.id == group.id
    assert clone.mode == STRUCTURE_ARRAY
    assert clone.active_child_id == group.children[1].id
    # A group that has never had a child picked writes nothing and loads on its first.
    untouched = PowerGroup(mode=STRUCTURE_ARRAY, children=[Power(name="Fire")])
    assert "active_child_id" not in untouched.to_dict()
    assert node_from_dict(untouched.to_dict()).active_child_id == ""
    assert [c.name for c in clone.children] == ["Fire", "Ice"]

    # A bare power dict (no "kind"/"children") still dispatches to a leaf Power.
    assert isinstance(node_from_dict(Power(name="Lone").to_dict()), Power)


def test_group_cost_sums_independent_and_linked() -> None:
    data = load_game_data()
    a = Power(name="A", effects=[PowerEffectInstance("damage", rank=10)])  # 10 PP
    b = Power(name="B", effects=[PowerEffectInstance("damage", rank=6)])  # 6 PP
    independent = PowerGroup(mode=STRUCTURE_INDEPENDENT, children=[a, b])
    linked = PowerGroup(mode=STRUCTURE_LINKED, children=[a, b])
    assert node_cost(independent, data) == 16
    assert node_cost(linked, data) == 16  # linking is a +0 bundle


def test_array_group_pays_costliest_plus_flat_alternates() -> None:
    data = load_game_data()
    base = Power(name="Fire Bolt", effects=[PowerEffectInstance("damage", rank=10)])  # 10 PP
    alt = Power(name="Ice Bolt", effects=[PowerEffectInstance("damage", rank=6)])  # 6 PP
    group = PowerGroup(mode=STRUCTURE_ARRAY, children=[base, alt])
    flat = array_alternate_cost(data)

    # Costliest paid in full, each other child a flat alternate — not 10 + 6.
    assert node_cost(group, data) == 10 + flat
    # The base child shows its full cost; the alternate shows the flat pooled cost.
    assert group_array_base_index(group, data) == 0
    assert node_display_cost(base, group, data) == 10
    assert node_display_cost(alt, group, data) == flat

    char = _character_with_powers(group)
    assert powers_points_spent(char, data) == 10 + flat


def test_nested_groups_price_recursively() -> None:
    data = load_game_data()
    e1 = Power(name="E1", effects=[PowerEffectInstance("damage", rank=8)])  # 8 PP
    e2 = Power(name="E2", effects=[PowerEffectInstance("damage", rank=4)])  # 4 PP
    e3 = Power(name="E3", effects=[PowerEffectInstance("damage", rank=10)])  # 10 PP
    flat = array_alternate_cost(data)

    linked = PowerGroup(mode=STRUCTURE_LINKED, children=[e1, e2])  # 12 PP
    outer = PowerGroup(mode=STRUCTURE_ARRAY, children=[linked, e3])  # array of a group + a leaf
    # Costliest child is the 12-PP linked group; the 10-PP leaf is a flat alternate.
    assert node_cost(outer, data) == 12 + flat


def test_inactive_array_member_drops_its_trait_boost() -> None:
    data = load_game_data()
    base = Power(name="Base", effects=[PowerEffectInstance("damage", rank=8)])
    boost = Power(
        name="Might",
        effects=[PowerEffectInstance("enhanced_trait", rank=4, config={"target": "STR"})],
    )
    group = PowerGroup(mode=STRUCTURE_ARRAY, children=[base, boost])
    char = _character_with_powers(group)

    # Selected (active) → the +4 Strength boost flows through.
    group.active_child_id = boost.id
    assert effective_ability(char, data, "STR") == 4
    # Not the selected member → gated off, boost drops.
    group.active_child_id = base.id
    assert effective_ability(char, data, "STR") == 0


def test_live_powers_walks_the_tree_honouring_arrays() -> None:
    a = Power(name="A")
    b = Power(name="B")
    c = Power(name="C")
    array = PowerGroup(mode=STRUCTURE_ARRAY, children=[b, c], active_child_id=b.id)
    linked = PowerGroup(mode=STRUCTURE_LINKED, children=[a, array])
    # Linked keeps every branch; the array contributes only its active child.
    assert [p.name for p in live_powers([linked])] == ["A", "B"]
    array.active_child_id = c.id
    assert [p.name for p in live_powers([linked])] == ["A", "C"]


def test_legacy_flat_relations_migrate_into_groups() -> None:
    # A save from before groups existed: a flat list with alternate_of / linked_with.
    base = Power(name="Fire Bolt", effects=[PowerEffectInstance("damage", rank=10)])
    alt = Power(name="Ice Bolt", effects=[PowerEffectInstance("damage", rank=6)])
    alt.alternate_of = base.id
    partner = Power(name="Left")
    other = Power(name="Right")
    partner.linked_with = [other.id]
    raw = _character_with_powers(base, alt, partner, other).to_dict()

    restored = Character.from_dict(raw)
    modes = {n.mode for n in restored.powers if isinstance(n, PowerGroup)}
    assert modes == {STRUCTURE_ARRAY, STRUCTURE_LINKED}
    # The dead flat fields are cleared after migration, so a re-save stays group-only.
    for group in restored.powers:
        for child in getattr(group, "children", []):
            assert child.alternate_of == "" and child.linked_with == []


# -- Affliction modifier tuning (extra condition, fatal, onset, empowering, ...) --


def _affliction(rank: int, *mods: tuple[str, dict]) -> PowerEffectInstance:
    """A rank-``rank`` Affliction carrying the given ``(modifier_id, config)`` mods,
    routed into extras/flaws by the modifier's category in the loaded catalog."""
    data = load_game_data()
    catalog = data.modifier_catalog()
    effect = PowerEffectInstance("affliction", rank=rank)
    for modifier_id, config in mods:
        selection = ModifierSelection(modifier_id=modifier_id, config=dict(config))
        bucket = effect.flaws if catalog[modifier_id].category == "flaw" else effect.extras
        bucket.append(selection)
    return effect


def test_onset_switches_between_flat_and_per_rank_by_choice() -> None:
    data = load_game_data()
    base = effect_total_cost(_affliction(4), data)  # 4
    # "One round": a flat -1 point.
    assert effect_total_cost(_affliction(4, ("onset", {"delay": "round"})), data) == base - 1
    # "One scene": -1 per rank — the sub-1 PP/rank rule makes 4 ranks cost ceil(4/2) = 2.
    assert effect_total_cost(_affliction(4, ("onset", {"delay": "scene"})), data) == 2


def test_empowering_costs_two_per_rank_and_notes_the_bonus_points() -> None:
    data = load_game_data()
    effect = _affliction(4, ("empowering", {}))
    assert effect_total_cost(effect, data) == 4 * 3  # base 1 + Empowering 2, per rank
    notes = next(r.value for r in effect_stat_rows(effect, data) if r.label == "Notes")
    assert "60 power points" in notes  # rank 4 × 15


def test_reversible_flat_cost_tracks_the_chosen_reach() -> None:
    data = load_game_data()
    base = effect_total_cost(_affliction(4), data)
    within = _affliction(4, ("reversible_affliction", {"reach": "range"}))
    anywhere = _affliction(4, ("reversible_affliction", {"reach": "any"}))
    assert effect_total_cost(within, data) == base + 1
    assert effect_total_cost(anywhere, data) == base + 2


def test_variable_conditions_scope_sets_the_per_rank_cost() -> None:
    data = load_game_data()
    full = _affliction(4, ("variable_conditions", {}))  # default 2 points/rank
    one = _affliction(4, ("variable_conditions", {"points": 1}))
    assert effect_total_cost(full, data) == 4 * 3  # base 1 + 2 per rank
    assert effect_total_cost(one, data) == 4 * 2  # base 1 + 1 per rank


def test_fatal_costs_one_per_rank_and_notes_the_dying_condition() -> None:
    data = load_game_data()
    effect = _affliction(4, ("fatal", {}))
    assert effect_total_cost(effect, data) == 4 * 2
    notes = next(r.value for r in effect_stat_rows(effect, data) if r.label == "Notes")
    assert "Dying" in notes


def test_increasing_difficulty_requires_cumulative_or_progressive() -> None:
    data = load_game_data()
    alone = Power(effects=[_affliction(4, ("increasing_difficulty", {}))])
    assert power_modifier_requirement_violations(alone, data)  # unmet dependency
    paired = Power(effects=[_affliction(4, ("increasing_difficulty", {}), ("cumulative", {}))])
    assert power_modifier_requirement_violations(paired, data) == []


# -- Dev-mode homerule overrides ----------------------------------------------


def _damage_with_override(key, value, order="after", **extra):
    effect = PowerEffectInstance("damage", rank=8)
    entry = {"value": value, "order": order}
    entry.update(extra)
    effect.overrides[key] = entry
    return effect


def test_after_override_wins_over_a_modifier() -> None:
    data = load_game_data()
    # The Ranged extra would set range to "Ranged"; an "after" override beats it.
    effect = _damage_with_override("range", "Planetary")
    effect.extras.append(ModifierSelection("ranged"))
    row = next(r for r in effect_stat_rows(effect, data) if r.key == "range")
    assert row.value == "Planetary"
    assert row.change == "homerule"


def test_before_override_is_still_overridden_by_a_modifier() -> None:
    data = load_game_data()
    # A "before" override sets the base the Ranged extra then replaces.
    effect = _damage_with_override("range", "Planetary", order="before")
    effect.extras.append(ModifierSelection("ranged"))
    row = next(r for r in effect_stat_rows(effect, data) if r.key == "range")
    assert row.value == "Ranged"  # the modifier wins over a "before" override


def test_before_override_untouched_by_modifiers_reads_homerule() -> None:
    data = load_game_data()
    effect = _damage_with_override("effect_type", "Utility", order="before")
    row = next(r for r in effect_stat_rows(effect, data) if r.key == "effect_type")
    assert row.value == "Utility"
    assert row.change == "homerule"


def test_after_override_of_check_is_verbatim_not_numeric() -> None:
    data = load_game_data()
    # A base "Attack vs. Defense" check would resolve to "<n> vs. Defense"; an "after"
    # override is kept exactly as typed.
    effect = _damage_with_override("check", "roll a d6")
    row = next(r for r in effect_stat_rows(effect, data) if r.key == "check")
    assert row.value == "roll a d6"
    assert row.change == "homerule"


def test_custom_override_row_is_appended() -> None:
    data = load_game_data()
    effect = _damage_with_override("custom_1", "42", label="Ammo")
    rows = effect_stat_rows(effect, data)
    row = next(r for r in rows if r.key == "custom_1")
    assert row.label == "Ammo"
    assert row.value == "42"
    assert row.change == "homerule"


def test_cost_override_replaces_the_power_total() -> None:
    data = load_game_data()
    power = Power(effects=[PowerEffectInstance("damage", rank=8)])
    assert power_total_cost(power, data) == 8
    power.cost_override = 3
    assert power_total_cost(power, data) == 3


def test_cost_override_flows_into_points_spent() -> None:
    data = load_game_data()
    char = Character()
    power = Power(name="Homebrew", effects=[PowerEffectInstance("damage", rank=8)])
    power.cost_override = 25
    char.powers.append(power)
    assert powers_points_spent(char, data) == 25


def test_power_is_homerule_only_with_an_override() -> None:
    from mm_companion.core.powers import power_is_homerule

    plain = Power(effects=[PowerEffectInstance("damage", rank=8)])
    assert not power_is_homerule(plain)
    with_cost = Power(effects=[PowerEffectInstance("damage", rank=8)], cost_override=5)
    assert power_is_homerule(with_cost)
    with_term = Power(effects=[_damage_with_override("range", "Planetary")])
    assert power_is_homerule(with_term)


def test_overrides_round_trip_through_serialization() -> None:
    effect = _damage_with_override("range", "Planetary", order="before")
    effect.overrides["custom_1"] = {"value": "9", "order": "after", "label": "Ammo"}
    power = Power(name="HR", effects=[effect], cost_override=17)
    restored = Power.from_dict(power.to_dict())
    assert restored.cost_override == 17
    assert restored.effects[0].overrides == effect.overrides


def test_old_power_dict_loads_without_override_keys() -> None:
    restored = Power.from_dict({"name": "x", "effects": [{"effect_id": "damage", "rank": 3}]})
    assert restored.effects[0].overrides == {}
    assert restored.cost_override is None


def test_resolve_stat_display_fills_in_the_numbers() -> None:
    from mm_companion.core.rules import resolve_stat_display

    data = load_game_data()
    effect = PowerEffectInstance("damage", rank=8)  # save DC base 10 + rank 8 = 18
    assert resolve_stat_display(effect, data, "resistance", "Will vs. Effect") == "Will vs. 18"
    assert resolve_stat_display(effect, data, "range", "Rank") == "1,800 feet"
    # Fields without a numeric form come back unchanged.
    assert resolve_stat_display(effect, data, "effect_type", "Attack") == "Attack"
    assert resolve_stat_display(effect, data, "action", "Standard") == "Standard"


# -- ranged reach (docs/mm-powers-architecture.md §10) ---------------------------


def _rows_by_key(effect, data, char=None) -> dict:
    return {row.key: row for row in effect_stat_rows(effect, data, char)}


def test_ranged_effect_states_its_distance_rank_and_increments() -> None:
    data = load_game_data()
    effect = PowerEffectInstance("damage", rank=8, extras=[ModifierSelection("ranged")])

    rows = _rows_by_key(effect, data)
    assert rows["distance_rank"].value == "rank 8"
    assert rows["distance_rank"].change == ""  # nothing bought it up
    # Short / medium / long are distance ranks 8 / 9 / 10 — the x1/x2/x4 progression.
    assert rows["distance"].value == "short 1,800 feet / medium 1/2 mile / long 1 mile"


def test_close_effect_has_no_distance_rows() -> None:
    data = load_game_data()
    rows = _rows_by_key(PowerEffectInstance("damage", rank=8), data)
    assert "distance_rank" not in rows
    assert "distance" not in rows


def test_extended_range_raises_the_distance_rank_and_leaves_the_notes_row() -> None:
    data = load_game_data()
    effect = PowerEffectInstance(
        "damage",
        rank=8,
        extras=[ModifierSelection("ranged"), ModifierSelection("extended_range", rank=2)],
    )

    rows = _rows_by_key(effect, data)
    assert rows["distance_rank"].base == "rank 8"
    assert rows["distance_rank"].value == "rank 10"
    assert rows["distance_rank"].change == "better"
    # It now shows in a stat cell, so it is no longer listed as an invisible Note.
    assert "Extended Range" not in (rows["notes"].value if "notes" in rows else "")


def test_extended_range_on_a_close_effect_shows_no_distance() -> None:
    data = load_game_data()
    # No Ranged extra, so there is no reach for Extended Range to extend.
    effect = PowerEffectInstance(
        "damage", rank=8, extras=[ModifierSelection("extended_range", rank=2)]
    )
    assert "distance_rank" not in _rows_by_key(effect, data)


def test_ranged_distance_ranks_returns_none_without_a_ranged_range() -> None:
    from mm_companion.core.rules import ranged_distance_ranks

    data = load_game_data()
    assert ranged_distance_ranks(PowerEffectInstance("damage", rank=4), data) is None
    ranged = PowerEffectInstance("damage", rank=4, extras=[ModifierSelection("ranged")])
    assert ranged_distance_ranks(ranged, data) == (4, 4)


# -- Extra Limbs' capped bonus --------------------------------------------------


def test_extra_limbs_bonus_climbs_per_rank_and_stops_at_the_cap() -> None:
    data = load_game_data()
    at_three = PowerEffectInstance("extra_limbs", rank=3, config={"appliesTo": "grab"})
    assert _rows_by_key(at_three, data)["bonus"].value == "+3 Grab"

    at_seven = PowerEffectInstance("extra_limbs", rank=7, config={"appliesTo": "stability"})
    assert _rows_by_key(at_seven, data)["bonus"].value == "+5 Stability (capped)"


def test_extra_limbs_with_variable_defers_the_subject_to_use_time() -> None:
    data = load_game_data()
    # The Variable extra gates the picker (hiddenWith), so no subject is stored.
    effect = PowerEffectInstance(
        "extra_limbs", rank=4, extras=[ModifierSelection("variable_extra_limbs")]
    )
    assert _rows_by_key(effect, data)["bonus"].value == ("+4 (Grab or Stability, chosen each turn)")


# -- Check Required -------------------------------------------------------------


def test_check_required_names_the_check_and_its_dc() -> None:
    data = load_game_data()
    effect = PowerEffectInstance(
        "damage",
        rank=6,
        flaws=[ModifierSelection("check_required", rank=3, config={"trait": "Acrobatics"})],
    )
    # DC is the system's base 10 plus the flaw's rank.
    assert _rows_by_key(effect, data)["required_check"].value == "Acrobatics check, DC 13"


def test_check_required_renders_a_trait_key_by_its_name() -> None:
    data = load_game_data()
    effect = PowerEffectInstance(
        "damage",
        rank=6,
        flaws=[ModifierSelection("check_required", rank=2, config={"trait": "AGL"})],
    )
    assert _rows_by_key(effect, data)["required_check"].value == "Agility check, DC 12"


def test_unconfigured_check_required_still_reads_as_a_sentence() -> None:
    data = load_game_data()
    effect = PowerEffectInstance(
        "damage", rank=6, flaws=[ModifierSelection("check_required", rank=1)]
    )
    assert _rows_by_key(effect, data)["required_check"].value == "Check, DC 11"


def test_deflect_no_longer_carries_its_own_limited_to_row() -> None:
    data = load_game_data()
    # The general Limited flaw already asks for this, so the duplicate config went away.
    assert "limitedCategory" not in _rows_by_key(PowerEffectInstance("deflect", rank=5), data)


# --- the Strength-Based divisor (docs/mm-equipment-design.md §4) ------------------------


def test_a_cheap_effect_takes_the_whole_ability() -> None:
    """Damage is 1 point per rank, so Strength arrives undivided — no divisor at all."""
    data = load_game_data()
    char = Character()
    char.abilities["STR"] = 5
    effect = PowerEffectInstance("damage", rank=3, extras=[ModifierSelection("strength_based")])

    assert effect_per_rank_cost(effect, data) == 1
    assert effect_effective_rank(effect, data, char) == 8


def test_a_two_point_effect_halves_the_ability_it_folds_in() -> None:
    """The compound bow from the design doc: Ranged Strength-Based Damage, Strength 5 → +2."""
    data = load_game_data()
    char = Character()
    char.abilities["STR"] = 5
    effect = PowerEffectInstance(
        "damage",
        rank=3,
        extras=[ModifierSelection("strength_based"), ModifierSelection("ranged")],
    )

    assert effect_per_rank_cost(effect, data) == 2
    assert effect_effective_rank(effect, data, char) == 5  # 3 + floor(5 / 2)


def test_a_flat_modifier_never_triggers_the_divisor() -> None:
    """Accurate is charged once, so it does not change what a rank costs (carve-out 2)."""
    data = load_game_data()
    char = Character()
    char.abilities["STR"] = 5
    effect = PowerEffectInstance(
        "damage",
        rank=3,
        extras=[ModifierSelection("strength_based"), ModifierSelection("accurate", rank=2)],
    )

    assert effect_per_rank_cost(effect, data) == 1
    assert effect_effective_rank(effect, data, char) == 8


def test_a_sub_one_point_effect_takes_the_whole_ability() -> None:
    """Carve-out 1: below 1 point per rank the ability is not multiplied either way."""
    data = load_game_data()
    char = Character()
    char.abilities["STR"] = 5
    effect = PowerEffectInstance(
        "damage",
        rank=4,
        extras=[ModifierSelection("strength_based")],
        flaws=[ModifierSelection("limited"), ModifierSelection("unreliable")],
    )

    assert effect_per_rank_cost(effect, data) < 1
    assert effect_effective_rank(effect, data, char) == 9


def test_the_divisor_is_arithmetic_and_needs_no_data() -> None:
    """The rule itself, stated once: floor division above 1 PP/rank, pass-through below."""
    assert ability_rank_contribution(5, 1) == 5
    assert ability_rank_contribution(5, 0) == 5
    assert ability_rank_contribution(5, 2) == 2
    assert ability_rank_contribution(5, 3) == 1
    assert ability_rank_contribution(5, 6) == 0
    assert ability_rank_contribution(0, 2) == 0
    assert ability_rank_contribution(-3, 1) == 0


# --- qualified trait keys: focuses, specialized pools, advantage subjects -------------


def test_a_skill_row_is_priced_the_same_bought_or_granted() -> None:
    """The point of one :func:`skill_row_rate`: an Enhanced Trait cannot make a rank of
    a skill cost something other than what buying it costs."""

    data = load_game_data()
    char = Character.new_default(data)
    char.specializations["Stealth"] = ["Urban"]
    for row_id, expected in (
        ("Stealth", Fraction(1, 2)),
        ("Expertise::Law", Fraction(1, 2)),  # a plain focus is priced at the normal rate
        ("Stealth::spec::Urban", Fraction(1, 4)),  # a narrow pool at the specialized one
    ):
        assert trait_rate(char, data, row_id) == expected
        char.skill_ranks = {row_id: 4}
        assert skill_points_spent(char, data) == math.ceil(expected * 4)


def test_a_per_skill_homebrew_rate_prices_every_row_of_that_skill() -> None:
    data = load_game_data()
    char = Character.new_default(data)
    char.item_cost_overrides["skills"] = {"Expertise": 1}  # 1 rank per point
    assert trait_rate(char, data, "Expertise") == 1
    assert trait_rate(char, data, "Expertise::Law") == 1


def test_enhancing_one_focus_leaves_the_others_alone() -> None:
    """The bug the qualifier fixes: allocating to bare "Expertise" raised every focus."""

    data = load_game_data()
    effect = _enhanced(2, [("Expertise::Stealth", 2)])
    char = _char_with(Power(name="Chameleon Field", effects=[effect]))
    char.focuses["Expertise"] = ["Stealth", "Law"]
    assert effect_total_cost(effect, data) == 1
    assert skill_bonus(char, data, "Expertise::Stealth").amount == 2
    assert skill_bonus(char, data, "Expertise::Law") is None


def test_a_granted_focus_the_hero_never_bought_still_has_a_row_to_land_on() -> None:
    data = load_game_data()
    effect = _enhanced(2, [("Expertise::Stealth", 2)])
    char = _char_with(Power(name="Chameleon Field", effects=[effect]))
    granted = granted_skill_rows(char, data)
    assert list(granted) == ["Expertise::Stealth"]
    assert granted["Expertise::Stealth"].amount == 2
    assert granted["Expertise::Stealth"].sources == ("Chameleon Field",)
    # Once it *is* bought it is an ordinary row, and the block needs no help with it.
    char.focuses["Expertise"] = ["Stealth"]
    assert granted_skill_rows(char, data) == {}


def test_the_same_advantage_can_be_granted_twice_for_two_subjects() -> None:
    data = load_game_data()
    effect = _enhanced(2, [("Improved Critical::Sword", 1), ("Improved Critical::Bow", 1)])
    char = _char_with(Power(name="Duellist", effects=[effect]))
    granted = granted_advantage_selections(char, data)
    assert [(s.name, s.parameter, s.rank) for s, _src in granted] == [
        ("Improved Critical", "Sword", 1),
        ("Improved Critical", "Bow", 1),
    ]
    assert effect_total_cost(effect, data) == 2  # one point each, as bought
    # Still outside the advantage tally and the Heroic budget: the power paid for them.
    assert advantage_points_spent(char, data) == 0


def test_a_granted_advantages_subject_chains_into_the_skill_it_names() -> None:
    """Skill Mastery-shaped advantage: its skill bonus lands on the subject it was
    granted for, exactly as a bought one lands on the subject the player chose."""

    data = load_game_data()
    base = next(a for a in data.advantages if a.name == "Improved Initiative")
    granting = dataclasses.replace(base, name="Deft Touch", skill_bonus_per_rank=2)
    data = dataclasses.replace(data, advantages=(*data.advantages, granting))
    effect = _enhanced(1, [("Deft Touch::Sleight of Hand", 1)])
    char = _char_with(Power(name="Nimble Fingers", effects=[effect]))
    assert skill_bonus(char, data, "Sleight of Hand").amount == 2


def test_an_over_ranked_advantage_row_is_a_warning() -> None:
    data = load_game_data()
    power = Power(effects=[_enhanced(4, [("Fearless", 4)])])
    assert power_trait_allocation_violations(power, data) == [
        "Enhanced Trait: Fearless is capped at 2 ranks; 4 allocated."
    ]
    # An ability has no cap of its own — the Power Level bounds it, checked elsewhere.
    mighty = Power(effects=[_enhanced(20, [("STR", 20)])])
    assert power_trait_allocation_violations(mighty, data) == []


def test_a_qualified_key_reads_as_a_name_everywhere_it_is_shown() -> None:
    data = load_game_data()
    effect = _enhanced(4, [("Expertise::Law", 2), ("Improved Critical::Sword", 1)])
    row = next(r for r in effect_stat_rows(effect, data) if r.key == "enhances")
    assert row.value == "Expertise: Law +2, Improved Critical (Sword) +1"
    assert trait_display_name(data, "Stealth::spec::Urban") == "Stealth: Urban (specialized)"


# --- rank bands: a modifier applied to only some of an effect's ranks ------------------


def _banded(rank: int, modifier_id: str, low: int, high: int, extras: list | None = None):
    """A Damage effect at ``rank`` carrying ``modifier_id`` over ranks ``low..high``."""
    return PowerEffectInstance(
        effect_id="damage",
        rank=rank,
        extras=extras or [ModifierSelection("ranged")],
        flaws=[ModifierSelection(modifier_id, applies_from=low, applies_to=high)],
    )


def test_a_banded_flaw_discounts_only_the_ranks_it_covers() -> None:
    """The book's own example: a Blast 12 whose top four ranks alone are Tiring.

    Eight ranks at the plain 2 PP and four at the discounted 1 PP, so the effect lands
    between the undiscounted 24 and the wholly-Tiring 12.
    """
    data = load_game_data()
    ranged = [ModifierSelection("ranged")]

    plain = PowerEffectInstance(effect_id="damage", rank=12, extras=ranged)
    whole = PowerEffectInstance(
        effect_id="damage", rank=12, extras=ranged, flaws=[ModifierSelection("tiring")]
    )
    assert effect_total_cost(plain, data) == 24
    assert effect_total_cost(whole, data) == 12
    assert effect_total_cost(_banded(12, "tiring", 9, 12), data) == 20


def test_a_band_shows_the_ranks_it_covers_in_the_formula_and_the_label() -> None:
    data = load_game_data()
    effect = _banded(12, "tiring", 9, 12)
    formula = effect_cost_formula(effect, data)
    assert "8 ×" in formula and "4 ×" in formula

    label = modifier_label(data.modifier_catalog()["tiring"], effect.flaws[0], effect_rank=12)
    assert label == "Tiring (ranks 9–12)"
    # A single-rank band reads as one rank, and an unbanded selection says nothing.
    tiring = data.modifier_catalog()["tiring"]
    one = ModifierSelection("tiring", applies_from=3, applies_to=3)
    assert modifier_label(tiring, one, effect_rank=12) == "Tiring (rank 3)"
    assert modifier_label(tiring, ModifierSelection("tiring"), effect_rank=12) == "Tiring"


def test_a_band_on_a_flat_modifier_changes_nothing() -> None:
    """A one-time charge costs the same over four ranks as over twelve.

    So the constructor never offers a band there, and one stored by any other route is
    ignored rather than quietly repricing the effect.
    """
    data = load_game_data()
    ranged = [ModifierSelection("ranged")]
    plain = PowerEffectInstance(
        effect_id="damage", rank=12, extras=ranged, flaws=[ModifierSelection("quirk")]
    )
    assert effect_total_cost(_banded(12, "quirk", 9, 12), data) == effect_total_cost(plain, data)


def test_overlapping_bands_price_each_rank_once() -> None:
    """Two flaws over different spans stack only where they actually overlap."""
    data = load_game_data()
    effect = PowerEffectInstance(
        effect_id="damage",
        rank=12,
        extras=[ModifierSelection("ranged")],
        flaws=[
            ModifierSelection("tiring", applies_from=5, applies_to=12),
            ModifierSelection("limited", applies_from=9, applies_to=12),
        ],
    )
    # 4 ranks at 2 + 4 at 1 + 4 at 1/2 (the sub-1 PP rule) = 8 + 4 + 2.
    assert effect_total_cost(effect, data) == 14


def test_bands_are_summed_unrounded_and_rounded_once() -> None:
    """Two half-point bands come to one point together, not two.

    Rounding each band on its own would charge for a part of a point twice — the same
    trap the "as trait" rows already avoid by summing before they round.
    """
    data = load_game_data()
    effect = PowerEffectInstance(
        effect_id="damage",
        rank=2,
        flaws=[
            ModifierSelection("tiring", applies_from=1, applies_to=1),
            ModifierSelection("limited", applies_from=2, applies_to=2),
        ],
    )
    assert effect_total_cost(effect, data) == 1


def test_a_band_is_clamped_to_a_rank_that_has_since_been_lowered() -> None:
    """Lowering the effect's rank must not leave a band pricing ranks that are gone."""
    data = load_game_data()
    effect = _banded(6, "tiring", 9, 12)
    assert selection_band(effect.flaws[0], effect.rank) == (6, 6)
    # 5 plain ranks at 2 PP plus the one discounted rank at 1.
    assert effect_total_cost(effect, data) == 11


def test_a_banded_flaw_does_not_discount_a_strength_based_fold_in() -> None:
    """Folded ranks sit above the bought ones and are in nobody's band.

    A flaw restricted to ranks 9-12 says nothing about the ranks the wielder's Strength
    brings, so they keep paying the undiscounted per-rank extras.
    """
    data = load_game_data()
    char = Character.new_default(data)
    char.abilities["STR"] = 4
    folded = [
        ModifierSelection("ranged"),
        ModifierSelection("strength_based", config={"amount": 4}),
    ]

    plain = PowerEffectInstance(effect_id="damage", rank=12, extras=folded)
    banded = PowerEffectInstance(
        effect_id="damage",
        rank=12,
        extras=folded,
        flaws=[ModifierSelection("tiring", applies_from=9, applies_to=12)],
    )
    # Both fold in the same 4 ranks at the same undiscounted rate; only the bought
    # ranks 9-12 differ, so the gap is exactly the 4 points the band saves.
    assert effect_total_cost(plain, data, char) - effect_total_cost(banded, data, char) == 4

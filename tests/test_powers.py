"""The power model and its cost math (``docs/mm-powers-architecture.md`` §2)."""

from __future__ import annotations

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
    array_alternate_cost,
    array_base_index,
    effect_allocation_used,
    effect_attack_skill_bonus,
    effect_cost_formula,
    effect_effective_rank,
    effect_game_terms,
    effect_is_active,
    effect_readout_rows,
    effect_stat_rows,
    effect_total_cost,
    effective_ability,
    effective_effect_stats,
    group_array_base_index,
    live_powers,
    node_cost,
    node_display_cost,
    power_allocation_violations,
    power_display_name,
    power_game_terms,
    power_has_standing_effect,
    power_linked_range_violations,
    power_modifier_requirement_violations,
    power_pl_violations,
    power_runtime_gates,
    power_strength_amount_violations,
    power_total_cost,
    power_trait_bonuses,
    powers_points_spent,
    resistance_total,
    skill_bonus,
    skill_total,
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
    # Damage 5 + Strength-Based (folds in STR 4) + Ranged (+1/rank). The bought ranks
    # pay base + mods; the folded-in Strength ranks pay the mods but not the base:
    # 5 × (1 + 1) + 4 × 1 = 14.
    effect = PowerEffectInstance(
        "damage",
        rank=5,
        extras=[ModifierSelection("strength_based"), ModifierSelection("ranged")],
    )
    assert effect_total_cost(effect, data, char) == 14
    assert effect_cost_formula(effect, data, char) == "5 × (1 + 0 + 1) + 4 × (0 + 1)"
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
    # Runtime on/off state is *not* persisted — the round trip drops it and the power
    # comes back in its default all-active state, regardless of the flags set above.
    assert "activated" not in power.to_dict()
    assert "toggled_on" not in power.to_dict()["effects"][0]
    assert restored.activated is True and restored.item_present is True
    assert restored.effects[0].toggled_on is True
    assert restored.effects[0].suppressed is False


def test_runtime_flags_in_json_are_ignored_and_default_to_active() -> None:
    # Runtime state is never persisted, so loading always reads as on — whether the
    # JSON omits the flags (a legacy save) or still carries stale ones (an older save
    # from before this changed): both come up active.
    legacy = Power.from_dict({"name": "Legacy", "effects": [{"effect_id": "protection"}]})
    assert legacy.activated is True and legacy.item_present is True
    assert legacy.effects[0].toggled_on is True and legacy.effects[0].suppressed is False

    stale = Power.from_dict(
        {
            "name": "Stale",
            "activated": False,
            "item_present": False,
            "effects": [{"effect_id": "protection", "toggled_on": False, "suppressed": True}],
        }
    )
    assert stale.activated is True and stale.item_present is True
    assert stale.effects[0].toggled_on is True and stale.effects[0].suppressed is False


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
    # Strength-Based (folds STR) + Ranged (+1/rank), amount bought = 4. The cost pays
    # for those 4 folded ranks regardless of the wielder's current Strength:
    # 5 × (1 + 1) + 4 × 1 = 14, whether Strength is 4, 8, or 1.
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
        assert effect_total_cost(effect, data, char) == 14
        assert effect_cost_formula(effect, data, char) == "5 × (1 + 0 + 1) + 4 × (0 + 1)"
    # The effect *value*, by contrast, still tracks the current (capped) Strength.
    weak = Character()
    weak.abilities["STR"] = 1
    assert effect_effective_rank(effect, data, weak) == 6  # 5 + min(4, 1)


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
    group.active_child_id = group.children[0].id
    raw = group.to_dict()
    assert raw["kind"] == "group"
    # Which array member is live is runtime state — not persisted.
    assert "active_child_id" not in raw

    clone = node_from_dict(raw)
    assert isinstance(clone, PowerGroup)
    assert clone.id == group.id
    assert clone.mode == STRUCTURE_ARRAY
    # Runtime selection resets on load — an array defaults to its first child.
    assert clone.active_child_id == ""
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

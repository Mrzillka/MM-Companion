"""The power model and its cost math (``mm-powers-architecture.md`` §2)."""

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
)
from mm_companion.core.rules import (
    array_alternate_cost,
    array_base_index,
    effect_cost_formula,
    effect_game_terms,
    effect_is_active,
    effect_stat_rows,
    effect_total_cost,
    effective_ability,
    effective_effect_stats,
    power_game_terms,
    power_pl_violations,
    power_runtime_gates,
    power_total_cost,
    power_trait_bonuses,
    resistance_total,
    skill_total,
)


def test_base_effect_cost_is_per_rank() -> None:
    data = load_game_data()
    # Damage is 1 PP/rank; rank 8 with no modifiers costs 8.
    effect = PowerEffectInstance("damage", rank=8)
    assert effect_total_cost(effect, data) == 8


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
    # Attack bonus reads as the effect rank; Damage's Toughness DC is 15 + rank.
    assert rows["check"].value == "8 vs. Defense"
    assert rows["resistance"].value == "Toughness vs. 23"


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
    assert rows["resistance"].value == "Toughness vs. 23"


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
    assert rows["resistance"].value == "Toughness vs. 23"  # target still resists


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
            )
        ],
    )
    restored = Power.from_dict(power.to_dict())
    assert restored.to_dict() == power.to_dict()
    assert restored.effects[0].extras[0].modifier_id == "ranged"
    assert restored.structure == STRUCTURE_ARRAY
    # Runtime on/off state survives the round trip.
    assert restored.activated is False and restored.item_present is False
    assert restored.effects[0].toggled_on is False
    assert restored.effects[0].suppressed is True


def test_older_saves_without_runtime_flags_default_to_active() -> None:
    # A power JSON from before runtime state existed omits the flags → reads as on.
    restored = Power.from_dict({"name": "Legacy", "effects": [{"effect_id": "protection"}]})
    assert restored.activated is True and restored.item_present is True
    assert restored.effects[0].toggled_on is True and restored.effects[0].suppressed is False


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

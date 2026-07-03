"""The power model and its cost math (``mm-powers-architecture.md`` §2)."""

from __future__ import annotations

from mm_companion.core.data_loader import load_game_data
from mm_companion.core.powers import ModifierSelection, Power, PowerEffectInstance
from mm_companion.core.rules import (
    effect_cost_formula,
    effect_game_terms,
    effect_total_cost,
    effective_effect_stats,
    power_game_terms,
    power_total_cost,
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
        effects=[
            PowerEffectInstance(
                "damage",
                rank=8,
                extras=[ModifierSelection("ranged")],
                flaws=[ModifierSelection("limited", rank=1)],
                config={"target": "combat.attack"},
                descriptors=["fire"],
            )
        ],
    )
    restored = Power.from_dict(power.to_dict())
    assert restored.to_dict() == power.to_dict()
    assert restored.effects[0].extras[0].modifier_id == "ranged"

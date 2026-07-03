"""The power model and its cost math (``mm-powers-architecture.md`` §2)."""

from __future__ import annotations

from mm_companion.core.data_loader import load_game_data
from mm_companion.core.powers import ModifierSelection, Power, PowerEffectInstance
from mm_companion.core.rules import effect_cost_formula, effect_total_cost, power_total_cost


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

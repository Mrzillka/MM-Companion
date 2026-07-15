"""Tests for the character model and the derived-math / validation rules."""

from __future__ import annotations

from dataclasses import replace

from mm_companion.core.character import AdvantageSelection, AppliedCondition, Character
from mm_companion.core.data_loader import load_game_data
from mm_companion.core.powers import Power, PowerEffectInstance
from mm_companion.core.rules import (
    advantage_rank_cap,
    advantage_violations,
    apply_condition,
    defense_class,
    heroic_advantage_budget,
    heroic_advantage_ranks,
    min_power_points,
    power_level_for_points,
    power_level_violations,
    power_points_remaining,
    power_points_spent,
    reconcile_points_to_level,
    resistance_total,
    skill_bonus,
    skill_modifiers,
    skill_points_spent,
    skill_total,
)


def test_new_default_seeds_from_game_data() -> None:
    data = load_game_data()
    char = Character.new_default(data)
    assert char.power_level == 10
    assert char.power_points_total == 150
    # Every ability/resistance key is present and starts at 0.
    assert set(char.abilities) == {a.key for a in data.abilities}
    assert set(char.resistances) == {r.key for r in data.resistances}
    assert all(v == 0 for v in char.abilities.values())


def test_to_dict_from_dict_round_trip() -> None:
    data = load_game_data()
    char = Character.new_default(data)
    char.abilities["STR"] = 4
    char.skill_ranks["Stealth"] = 6
    char.focuses["Close Combat"] = ["Swords"]
    char.specializations["Stealth"] = ["Urban"]
    char.skill_ranks["Stealth::spec::Urban"] = 4
    char.advantages.append(AdvantageSelection("Close Attack", 2))
    char.advantages.append(AdvantageSelection("Alternate Initiative", parameter="AWE"))
    char.conditions.append(AppliedCondition("dazed"))
    char.powers.append(
        Power(name="Fire Blast", effects=[PowerEffectInstance(effect_id="damage", rank=8)])
    )

    restored = Character.from_dict(char.to_dict())
    assert restored == char


def test_saved_powers_count_toward_points_spent() -> None:
    data = load_game_data()
    char = Character.new_default(data)
    baseline = power_points_spent(char, data)

    char.powers.append(
        Power(name="Fire Blast", effects=[PowerEffectInstance(effect_id="damage", rank=8)])
    )
    # Damage costs 1 PP/rank, so a rank-8 power adds 8 to the build.
    assert power_points_spent(char, data) == baseline + 8


def test_power_points_spent_matches_hand_computed_build() -> None:
    data = load_game_data()
    char = Character.new_default(data)
    char.abilities["STR"] = 3  # 3 * 2 = 6
    char.abilities["ATK"] = 2  # derived combat: 2 * 2 = 4
    char.resistances["TOUGHNESS"] = 4  # 4 * 1 = 4
    char.resistances["DEF"] = 2  # derived combat: 2 * 2 = 4
    # Skills pool: 4 + 8 = 12 ranks at 2/PP (focused skills cost the normal rate) →
    # ceil(12 / 2) = 6.
    char.skill_ranks["Stealth"] = 4
    char.skill_ranks["Close Combat: Swords"] = 8
    char.advantages.append(AdvantageSelection("Close Attack", 3))  # 3 * 1 = 3

    assert power_points_spent(char, data) == 27
    assert power_points_remaining(char, data) == 150 - 27


def test_skill_cost_is_pooled_across_skills() -> None:
    data = load_game_data()
    char = Character.new_default(data)
    # One rank in four different skills is 4 ranks total → ceil(4 / 2) = 2 PP, not
    # 4 (one per row).
    for name in ("Stealth", "Deception", "Perception", "Insight"):
        char.skill_ranks[name] = 1
    assert skill_points_spent(char, data) == 2


def test_focused_skill_costs_the_normal_rate() -> None:
    data = load_game_data()
    char = Character.new_default(data)
    # A focused focus pools at 2 ranks/PP just like any skill (not the old 4/PP).
    char.skill_ranks["Close Combat::Blades"] = 6
    assert skill_points_spent(char, data) == 3  # ceil(6 / 2)


def test_expertise_is_priced_at_the_normal_rate() -> None:
    data = load_game_data()
    char = Character.new_default(data)
    # Expertise's focuses pool at the normal 2 ranks/PP like any other focused skill.
    char.focuses["Expertise"] = ["Science"]
    char.skill_ranks["Expertise::Science"] = 8
    assert skill_points_spent(char, data) == 4  # ceil(8 / 2)


def test_specialized_pool_costs_half_and_pools_separately() -> None:
    data = load_game_data()
    char = Character.new_default(data)
    char.specializations["Stealth"] = ["Urban"]
    char.skill_ranks["Stealth::spec::Urban"] = 8  # 8 ranks at 4/PP → 2 PP
    assert skill_points_spent(char, data) == 2


def test_mixed_normal_and_specialized_ranks_round_together() -> None:
    data = load_game_data()
    char = Character.new_default(data)
    char.skill_ranks["Stealth"] = 1  # 1/2 PP
    char.specializations["Deception"] = ["Bluffing"]
    char.skill_ranks["Deception::spec::Bluffing"] = 1  # 1/4 PP
    # 1/2 + 1/4 = 3/4 → ceil = 1 PP (pooled rounding, not 1 + 1).
    assert skill_points_spent(char, data) == 1


def test_min_power_points_is_level_times_per_level_rate() -> None:
    data = load_game_data()
    per_level = data.costs.power_level.pp_per_level  # 15
    assert min_power_points(10, data) == 10 * per_level
    assert min_power_points(0, data) == 0


def test_power_level_for_points_floors_at_the_border() -> None:
    data = load_game_data()
    per_level = data.costs.power_level.pp_per_level  # 15
    assert power_level_for_points(10 * per_level, data) == 10  # exactly on the border
    assert power_level_for_points(10 * per_level + 1, data) == 10  # within the band
    assert power_level_for_points(11 * per_level, data) == 11  # next border → next level


def test_reconcile_points_keeps_a_budget_inside_the_band() -> None:
    data = load_game_data()
    per_level = data.costs.power_level.pp_per_level  # 15
    # 160 sits inside PL 10's band (150–164), so it is left untouched.
    assert reconcile_points_to_level(10, 10 * per_level + 10, data) == 10 * per_level + 10


def test_reconcile_points_raises_a_budget_below_the_level_minimum() -> None:
    data = load_game_data()
    per_level = data.costs.power_level.pp_per_level  # 15
    # PL raised to 11 while the budget is still PL 10's minimum → snaps up to 165.
    assert reconcile_points_to_level(11, 10 * per_level, data) == 11 * per_level


def test_reconcile_points_lowers_a_budget_from_a_higher_band() -> None:
    data = load_game_data()
    per_level = data.costs.power_level.pp_per_level  # 15
    # PL lowered to 9 while the budget is in PL 11's band → snaps down to PL 9's min.
    assert reconcile_points_to_level(9, 11 * per_level, data) == 9 * per_level


def test_skill_total_is_ability_plus_ranks() -> None:
    data = load_game_data()
    char = Character.new_default(data)
    char.abilities["AGL"] = 2
    char.skill_ranks["Stealth"] = 4
    assert skill_total(char, data, "Stealth") == 6  # AGL 2 + 4 ranks, nothing granted
    assert skill_bonus(char, data, "Stealth") is None


def test_skill_bonus_folds_in_an_advantage_that_grants_one() -> None:
    data = load_game_data()
    # No shipped advantage grants a flat skill bonus, so stand one up the way a mod
    # would (skillBonusPerRank + skillBonusTarget) rather than mutating the cached data.
    granting = replace(
        next(a for a in data.advantages if a.name == "Favored Foe"),
        skill_bonus_per_rank=2,
        skill_bonus_target="Perception",
    )
    data = replace(
        data, advantages=[granting if a.name == granting.name else a for a in data.advantages]
    )
    char = Character.new_default(data)
    char.advantages.append(AdvantageSelection(name="Favored Foe", rank=2))

    bonus = skill_bonus(char, data, "Perception")
    assert bonus is not None
    assert bonus.amount == 4  # 2 per rank, bought at rank 2
    assert bonus.sources == ("Favored Foe",)
    assert skill_bonus(char, data, "Stealth") is None  # only the named skill


def test_skill_bonus_targets_the_skill_the_selection_chose() -> None:
    data = load_game_data()
    # With no fixed target the bonus lands on the skill the selection's parameter names.
    granting = replace(
        next(a for a in data.advantages if a.name == "Skill Mastery"), skill_bonus_per_rank=3
    )
    data = replace(
        data, advantages=[granting if a.name == granting.name else a for a in data.advantages]
    )
    char = Character.new_default(data)
    char.advantages.append(AdvantageSelection(name="Skill Mastery", parameter="Stealth"))

    assert skill_bonus(char, data, "Stealth").amount == 3
    assert skill_bonus(char, data, "Acrobatics") is None


def test_skill_modifiers_net_a_condition_penalty_against_the_grants() -> None:
    data = load_game_data()
    char = Character.new_default(data)
    char.abilities["AGL"] = 2
    char.skill_ranks["Stealth"] = 4
    apply_condition(char, "impaired", data, parameter="Stealth")

    mods = skill_modifiers(char, data, "Stealth")
    assert mods.has_flat_modifier
    assert mods.amount == -2  # nothing granted, so just the penalty
    assert mods.condition.condition_ids == frozenset({"impaired"})
    # The penalty is display-only: the build value keeps the bought ranks whole, and
    # the sheet's number is the overlay on top of it.
    assert skill_total(char, data, "Stealth") == 6
    assert mods.condition.apply(skill_total(char, data, "Stealth")) == 4
    # Scoped, so a row the condition doesn't name carries nothing.
    assert skill_modifiers(char, data, "Acrobatics").has_flat_modifier is False


def test_skill_modifiers_ignore_an_override_that_is_not_a_flat_modifier() -> None:
    data = load_game_data()
    char = Character.new_default(data)
    char.skill_ranks["Stealth"] = 6
    # Debilitated zeroes the trait outright rather than shifting it by an amount, so
    # there is no number for the "+" column — the override lands on the total instead.
    apply_condition(char, "debilitated", data, parameter="Stealth")

    mods = skill_modifiers(char, data, "Stealth")
    assert mods.condition.active
    assert mods.has_flat_modifier is False
    assert mods.condition.apply(skill_total(char, data, "Stealth")) == 0


def test_focused_skill_row_resolves_parent_ability() -> None:
    data = load_game_data()
    char = Character.new_default(data)
    char.abilities["ATK"] = 3
    char.skill_ranks["Close Combat: Swords"] = 5
    # Close Combat is an ATK skill; the "<Skill>: <focus>" row inherits that.
    assert skill_total(char, data, "Close Combat: Swords") == 8


def test_resistance_and_defense_derivation() -> None:
    data = load_game_data()
    char = Character.new_default(data)
    char.abilities["STA"] = 3
    char.resistances["TOUGHNESS"] = 4  # Toughness links to Stamina
    assert resistance_total(char, data, "TOUGHNESS") == 7

    char.resistances["DEF"] = 5  # Defence is derived (no linked ability)
    assert resistance_total(char, data, "DEF") == 5
    assert defense_class(char, data) == 15


def test_power_level_violation_is_reported() -> None:
    data = load_game_data()
    char = Character.new_default(data)  # PL 10 -> skill-mod cap 20
    char.abilities["AGL"] = 15
    char.skill_ranks["Stealth"] = 10  # total 25 > 20
    violations = power_level_violations(char, data)
    assert any("Stealth" in v for v in violations)


def test_clean_build_has_no_violations() -> None:
    data = load_game_data()
    char = Character.new_default(data)
    char.abilities["AGL"] = 2
    char.skill_ranks["Stealth"] = 4  # total 6, well under cap
    assert power_level_violations(char, data) == []


def _advantage(data, name):
    return next(a for a in data.advantages if a.name == name)


def test_advantage_rank_cap_reads_the_cap_kind() -> None:
    data = load_game_data()
    # Fixed cap: Improved Critical is Ranked 4 regardless of Power Level.
    assert advantage_rank_cap(_advantage(data, "Improved Critical"), 10) == 4
    # Improved Initiative caps at ceil(PL / 2).
    assert advantage_rank_cap(_advantage(data, "Improved Initiative"), 10) == 5
    assert advantage_rank_cap(_advantage(data, "Improved Initiative"), 5) == 3
    # PL-shared and Heroic-budget advantages carry no standalone number here.
    assert advantage_rank_cap(_advantage(data, "Close Attack"), 10) is None
    assert advantage_rank_cap(_advantage(data, "Luck"), 10) is None
    # An unranked advantage is always a single rank.
    assert advantage_rank_cap(_advantage(data, "Diehard"), 10) == 1


def test_heroic_budget_pools_across_heroic_advantages() -> None:
    data = load_game_data()
    char = Character.new_default(data)  # PL 10 -> budget 5
    assert heroic_advantage_budget(char.power_level) == 5
    char.advantages.append(AdvantageSelection("Luck", 3))  # ranked Heroic: 3
    char.advantages.append(AdvantageSelection("Encouragement", 1))  # unranked Heroic: flat 1
    char.advantages.append(AdvantageSelection("Close Attack", 4))  # not Heroic: ignored
    assert heroic_advantage_ranks(char, data) == 4
    assert advantage_violations(char, data) == []


def test_advantage_violations_flag_over_cap_and_over_budget() -> None:
    data = load_game_data()
    char = Character.new_default(data)  # PL 10
    char.advantages.append(AdvantageSelection("Improved Critical", 5))  # cap 4
    char.advantages.append(AdvantageSelection("Determination", 4))
    char.advantages.append(AdvantageSelection("Guidance", 3))  # Heroic total 7 > budget 5
    violations = advantage_violations(char, data)
    assert any("Improved Critical" in v for v in violations)
    assert any("Heroic advantages" in v for v in violations)

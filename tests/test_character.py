"""Tests for the character model and the derived-math / validation rules."""

from __future__ import annotations

from mm_companion.core.character import AdvantageSelection, Character
from mm_companion.core.data_loader import load_game_data
from mm_companion.core.powers import Power, PowerEffectInstance
from mm_companion.core.rules import (
    defense_class,
    min_power_points,
    power_level_for_points,
    power_level_violations,
    power_points_remaining,
    power_points_spent,
    reconcile_points_to_level,
    resistance_total,
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
    char.advantages.append(AdvantageSelection("Close Attack", 2))
    char.conditions.add("dazed")
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
    char.skill_ranks["Stealth"] = 4  # ceil(4 / 2) = 2
    char.skill_ranks["Close Combat: Swords"] = 8  # focused: ceil(8 / 4) = 2
    char.advantages.append(AdvantageSelection("Close Attack", 3))  # 3 * 1 = 3

    assert power_points_spent(char, data) == 25
    assert power_points_remaining(char, data) == 150 - 25


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


def test_skill_total_is_ability_plus_ranks_plus_mods() -> None:
    data = load_game_data()
    char = Character.new_default(data)
    char.abilities["AGL"] = 2
    char.skill_ranks["Stealth"] = 4
    char.skill_mods["Stealth"] = 1
    assert skill_total(char, data, "Stealth") == 7  # AGL 2 + 4 ranks + 1 mod


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

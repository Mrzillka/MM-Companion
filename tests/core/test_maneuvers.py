"""The four attack maneuvers (PDF p200): their caps, the spec they shift, and what they
leave on the attacker (combat maneuvers, phase 5)."""

from __future__ import annotations

import pytest

from mm_companion.core.character import Character
from mm_companion.core.powers import ModifierSelection, Power, PowerEffectInstance
from mm_companion.core.rules import (
    RollSpec,
    SpecLadder,
    apply_condition,
    apply_maneuvers,
    attack_choices,
    has_attack_check,
    lasting_conditions,
    maneuver_caption,
    maneuver_limit,
    maneuver_values,
    power_rolls,
    same_attack,
)

ACCURATE, ALL_OUT, DEFENSIVE, POWER = (
    "accurate_attack",
    "all_out_attack",
    "defensive_attack",
    "power_attack",
)


def _blast(rank: int = 8) -> Power:
    damage = PowerEffectInstance("damage", rank=rank)
    damage.extras.append(ModifierSelection("ranged", config={"from": "close"}))
    return Power(name="Blast", effects=[damage])


def _hero(data, *, attack: int = 6, defense: int = 4, rank: int = 8) -> Character:
    hero = Character.new_default(data)
    hero.abilities["ATK"] = attack
    hero.resistances["DEF"] = defense
    hero.powers = [_blast(rank)]
    return hero


def _attack(hero, data) -> RollSpec:
    (choice,) = attack_choices(hero, data)
    return choice.spec


def _limit(data, action, hero, others=None) -> int:
    return maneuver_limit(data, action, _attack(hero, data), hero, others).maximum


# -- what the maneuvers read -----------------------------------------------------------


def test_an_attack_line_names_its_attack_bonus_effect_rank_and_the_defense(data) -> None:
    hero = _hero(data)
    spec = _attack(hero, data)
    assert has_attack_check(spec, data)
    assert spec.follow_up.effect_rank == 8
    assert maneuver_values(spec, hero, data) == {
        "attack_check": 6,
        "effect_rank": 8,
        "defense": 4,
    }


def test_the_defense_read_is_the_one_in_play(data) -> None:
    hero = _hero(data, defense=6)
    apply_condition(hero, "defending", data)  # +2 Defense
    assert maneuver_values(_attack(hero, data), hero, data)["defense"] == 8


def test_a_save_with_no_known_rank_gives_no_effect_rank(data) -> None:
    save = RollSpec(label="Will vs. the check result", rolled_by_target=True)
    spec = RollSpec(
        label="Attack +4 vs. Defense", modifier=4, follow_up=save, marks=("attack_check",)
    )
    assert maneuver_values(spec, None, data)["effect_rank"] is None
    limit = maneuver_limit(data, POWER, spec, None)
    assert limit.maximum == 0 and "effect rank" in limit.reason


# -- the caps ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("action", "attack", "defense", "rank", "expected"),
    [
        # min(5, penalised, bonused)
        (ACCURATE, 6, 4, 8, 5),  # attack doubles to 12, rank 8 → capped at 5
        (ACCURATE, 3, 4, 8, 3),  # attack can only double: +3
        (ACCURATE, 6, 4, 2, 2),  # the rank can't go below 0
        (ALL_OUT, 6, 4, 8, 4),  # Defense 4 can't go below 0
        (ALL_OUT, 2, 9, 8, 2),  # attack +2 doubles to +4
        (DEFENSIVE, 6, 3, 8, 3),  # Defense 3 can only double
        (DEFENSIVE, 1, 9, 8, 1),  # attack +1 can't go below 0
        (POWER, 4, 4, 8, 4),  # attack +4 can't go below 0
        (POWER, 9, 4, 3, 3),  # rank 3 can only double
        (POWER, 9, 4, 12, 5),  # never more than 5
    ],
)
def test_each_maneuver_is_capped_at_five_the_floor_and_double(
    data, action, attack, defense, rank, expected
) -> None:
    assert _limit(data, action, _hero(data, attack=attack, defense=defense, rank=rank)) == expected


@pytest.mark.parametrize("action", [ACCURATE, ALL_OUT, DEFENSIVE, POWER])
def test_an_attack_bonus_of_zero_trades_nothing(data, action) -> None:
    hero = _hero(data, attack=0)
    limit = maneuver_limit(data, action, _attack(hero, data), hero)
    assert limit.maximum == 0
    assert "attack bonus" in limit.reason


def test_no_attack_loaded_is_no_limit(data) -> None:
    limit = maneuver_limit(data, ALL_OUT, None, None)
    assert limit.maximum == 0 and limit.reason


def test_a_check_that_is_no_attack_check_takes_no_attack_maneuver(data) -> None:
    hero = _hero(data)
    spec = _attack(hero, data)
    from dataclasses import replace

    effect_check = replace(spec, marks=())
    assert not has_attack_check(effect_check, data)
    limit = maneuver_limit(data, ACCURATE, effect_check, hero)
    assert limit.maximum == 0 and "no attack bonus" in limit.reason


def test_two_maneuvers_raising_the_attack_share_the_room_to_double_it(data) -> None:
    hero = _hero(data, attack=4, defense=9)
    # Accurate +3 already on: All-Out may raise the attack only one more, to +8.
    assert _limit(data, ALL_OUT, hero, {ACCURATE: 3}) == 1
    assert _limit(data, ALL_OUT, hero, {ACCURATE: 4}) == 0


def test_opposite_maneuvers_give_each_other_room(data) -> None:
    hero = _hero(data, attack=4, defense=9, rank=4)
    # Alone, Power is held to the +4 attack bonus it spends.
    assert _limit(data, POWER, hero) == 4
    # Accurate +4 first raises that bonus to +8 (and lowers the rank to 0), so the
    # combined totals leave Power the full 5: +3 attack, rank 5 — neither below 0 nor
    # more than doubled.
    assert _limit(data, POWER, hero, {ACCURATE: 4}) == 5


def test_a_maneuver_ignores_its_own_entry_in_the_others(data) -> None:
    hero = _hero(data, attack=4)
    assert _limit(data, ACCURATE, hero, {ACCURATE: 4}) == 4


# -- the shifted spec --------------------------------------------------------------------


def test_all_out_raises_the_attack_check_and_names_it(data) -> None:
    hero = _hero(data)
    spec = apply_maneuvers(_attack(hero, data), [(ALL_OUT, 3)], data)
    assert spec.modifier == 9
    assert spec.label == "Attack +9 vs. Defense (All-Out Attack +3)"
    assert spec.follow_up.dc == 18  # the save is untouched


def test_power_attack_moves_the_save_dc_its_label_and_the_ladder_rank(data) -> None:
    hero = _hero(data)
    spec = apply_maneuvers(_attack(hero, data), [(POWER, 2)], data)
    assert spec.modifier == 4
    save = spec.follow_up
    assert (save.dc, save.effect_rank, save.ladder.rank) == (20, 10, 10)
    assert save.label == "Toughness vs. 20"


def test_accurate_lowers_the_save_and_raises_the_check(data) -> None:
    hero = _hero(data)
    spec = apply_maneuvers(_attack(hero, data), [(ACCURATE, 5)], data)
    assert spec.modifier == 11
    assert (spec.follow_up.dc, spec.follow_up.ladder.rank) == (13, 3)


def test_opposite_maneuvers_cancel_on_the_spec(data) -> None:
    hero = _hero(data)
    attack = _attack(hero, data)
    spec = apply_maneuvers(attack, [(ACCURATE, 2), (POWER, 2)], data)
    assert spec.modifier == attack.modifier
    assert spec.follow_up.dc == attack.follow_up.dc
    assert "Accurate Attack +2, Power Attack +2" in spec.label


def test_defensive_lowers_only_the_check(data) -> None:
    hero = _hero(data)
    spec = apply_maneuvers(_attack(hero, data), [(DEFENSIVE, 2)], data)
    assert spec.modifier == 4 and spec.follow_up.dc == 18


def test_a_linked_attack_moves_every_save(data) -> None:
    hero = _hero(data)
    damage = PowerEffectInstance("damage", rank=8)
    affliction = PowerEffectInstance(
        "affliction",
        rank=6,
        config={"resistance": "Fortitude", "degree1": "dazed", "degree2": "stunned"},
    )
    power = Power(name="Thunderbolt", structure="linked", effects=[damage, affliction])
    attack = next(s for s in power_rolls(power, hero, data) if s.follow_up is not None)
    assert len(attack.follow_ups()) == 2
    # The lowest rank of the two caps the trade.
    assert maneuver_values(attack, hero, data)["effect_rank"] == 6
    shifted = apply_maneuvers(attack, [(POWER, 2)], data)
    assert [s.dc for s in shifted.follow_ups()] == [s.dc + 2 for s in attack.follow_ups()]


def test_nothing_that_moves_anything_leaves_the_spec_alone(data) -> None:
    hero = _hero(data)
    attack = _attack(hero, data)
    assert apply_maneuvers(attack, [], data) is attack
    assert apply_maneuvers(attack, [(ALL_OUT, 0), ("charge", None)], data) is attack


def test_a_ladder_with_no_rank_is_left_at_zero() -> None:
    from mm_companion.core.rules.maneuvers import _shift_save

    save = RollSpec(label="Fortitude vs. DC 16", dc=16, effect_rank=6, ladder=SpecLadder("effect"))
    shifted = _shift_save(save, 2)
    assert shifted.ladder.rank == 0 and shifted.dc == 18
    assert shifted.label == "Fortitude vs. DC 18"


def test_the_caption_lists_what_moves(data) -> None:
    assert maneuver_caption(data, [(ALL_OUT, 3), (POWER, 0), ("aim", None)]) == "All-Out Attack +3"


# -- what stays on the attacker ----------------------------------------------------------


def test_only_all_out_and_defensive_leave_a_condition(data) -> None:
    picks = [(ACCURATE, 2), (ALL_OUT, 3), (DEFENSIVE, 1), (POWER, 4), (ALL_OUT, 0)]
    assert lasting_conditions(picks, data) == [("all_out_attack", 3), ("defensive_attack", 1)]


# -- the same attack, aimed or not -------------------------------------------------------


def test_an_aimed_copy_is_the_same_attack(data) -> None:
    from dataclasses import replace

    attack = _attack(_hero(data), data)
    aimed = replace(attack, targets=("Thug",), action_id="a1", title="Ana → Thug", actor="Ana")
    assert same_attack(attack, aimed)
    assert not same_attack(attack, replace(attack, modifier=attack.modifier + 1))
    assert same_attack(None, None) and not same_attack(attack, None)

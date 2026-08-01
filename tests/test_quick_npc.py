"""The Quick NPC builder: five numbers in, a playable creature out.

Headless — :mod:`mm_companion.core.npc` is pure Python, and the point of it living
in ``core`` is that what it builds can be proved without a display. The wizard that
collects the numbers is covered in ``test_gm_window.py``.
"""

from __future__ import annotations

from mm_companion.core.data_loader import load_game_data
from mm_companion.core.npc import quick_npc
from mm_companion.core.powers import Power
from mm_companion.core.rules import (
    effect_game_terms,
    estimated_power_level,
    power_rolls,
    resistance_total,
)
from mm_companion.ui.npc_names import npc_names, random_npc_name


def _npc(**overrides):
    args = {"name": "Bandit", "attack": 6, "effect": 5, "defence": 7, "toughness": 4}
    args.update(overrides)
    return quick_npc(load_game_data(), **args)


def test_the_numbers_land_where_the_rules_read_them() -> None:
    data = load_game_data()
    npc = _npc()

    assert npc.profile["hero_name"] == "Bandit"
    # A quick NPC has no abilities, so every resistance base is 0 and what was typed
    # is what the sheet shows.
    assert resistance_total(npc, data, "DEF") == 7
    assert resistance_total(npc, data, "TOUGHNESS") == 4
    # Dodge derives from Defence, so it follows without being asked for.
    assert resistance_total(npc, data, "DODGE") == 7
    assert npc.abilities["ATK"] == 6


def test_it_comes_with_something_that_hurts_and_something_that_stops_you() -> None:
    npc = _npc(effect=8)

    names = [power.name for power in npc.powers]
    assert names == ["Damage", "Affliction"]
    assert all(isinstance(power, Power) for power in npc.powers)
    assert [power.effects[0].rank for power in npc.powers] == [8, 8]


def test_the_attack_bonus_reaches_both_powers() -> None:
    data = load_game_data()
    npc = _npc(attack=9, effect=6)

    for power in npc.powers:
        attack = next(spec for spec in power_rolls(power, npc, data) if not spec.rolled_by_target)
        assert attack.modifier == 9


def test_the_affliction_carries_a_full_degree_ladder() -> None:
    data = load_game_data()
    npc = _npc(effect=6)
    affliction = npc.powers[1].effects[0]

    assert affliction.config["degree1"] == ["dazed"]
    assert affliction.config["degree2"] == ["stunned"]
    assert affliction.config["degree3"] == ["incapacitated"]
    # And the chosen resistance replaces the base line rather than sitting beside it.
    terms = effect_game_terms(affliction, data)
    assert "Fortitude" in terms
    assert "Dazed" in terms and "Stunned" in terms


def test_the_power_level_is_estimated_from_what_it_can_do() -> None:
    data = load_game_data()

    # attack 6 + effect 6 → 6; dodge 6 + toughness 6 → 6. Nothing states a PL.
    assert estimated_power_level(_npc(attack=6, effect=6, defence=6, toughness=6), data) == 6
    # Raise the offence alone and the estimate follows it.
    assert estimated_power_level(_npc(attack=10, effect=10, defence=6, toughness=6), data) == 10


def test_a_quick_npc_round_trips_through_its_own_serialization() -> None:
    from mm_companion.core.character import Character

    npc = _npc(effect=7)
    restored = Character.from_dict(npc.to_dict())

    assert [p.name for p in restored.powers] == ["Damage", "Affliction"]
    assert restored.powers[1].effects[0].config["degree2"] == ["stunned"]
    assert restored.resistances["TOUGHNESS"] == npc.resistances["TOUGHNESS"]


def test_a_zero_rank_effect_is_allowed_and_a_negative_one_is_floored() -> None:
    # A pure brawler with no special attack is a legitimate mook; a negative rank is not.
    assert _npc(effect=0).powers[0].effects[0].rank == 0
    assert _npc(effect=-3).powers[0].effects[0].rank == 0


# -- the suggested names -----------------------------------------------------


def test_the_name_list_loads_and_re_rolling_always_changes_it() -> None:
    names = npc_names()
    assert "Bandit" in names and "Boss" in names

    for _ in range(20):
        assert random_npc_name(exclude="Bandit") != "Bandit"

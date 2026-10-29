"""Team Attack (PDF p201), headless: which attacks combine and what they come to."""

from __future__ import annotations

import pytest

from mm_companion.core.character import Character
from mm_companion.core.npc import quick_npc
from mm_companion.core.powers import ModifierSelection, Power, PowerEffectInstance
from mm_companion.core.rules import (
    TeamEntry,
    combine_team_attack,
    team_attack_options,
    team_incompatibility,
    team_save_spec,
)


def _damage(data, rank: int, resisted: str = "") -> Character:
    char = Character.new_default(data)
    effect = PowerEffectInstance("damage", rank=rank)
    effect.extras.append(ModifierSelection("ranged", config={"from": "close"}))
    char.powers.append(Power(name="Blast", effects=[effect]))
    return char


def test_a_quick_npcs_attacks_are_options(data) -> None:
    thug = quick_npc(data, name="Thug", attack=4, effect=4, defence=4, toughness=3)
    options = team_attack_options(thug, data)
    assert options and all(o.save is not None for o in options)
    assert any(o.attack is not None for o in options)


def test_same_effect_within_five_ranks_combines(data) -> None:
    a = team_attack_options(_damage(data, 6), data)[0]
    b = team_attack_options(_damage(data, 10), data)[0]
    c = team_attack_options(_damage(data, 12), data)[0]
    assert team_incompatibility([a, b], data) == ""
    assert "within 5" in team_incompatibility([a, c], data)
    assert "two attackers" in team_incompatibility([a], data)


def test_different_resistances_do_not_combine(data) -> None:
    thug = quick_npc(data, name="Thug", attack=4, effect=4, defence=4, toughness=3)
    options = team_attack_options(thug, data)
    damage = team_attack_options(_damage(data, 6), data)[0]
    other = next((o for o in options if o.save.trait_key != damage.save.trait_key), None)
    if other is None:
        pytest.skip("a quick NPC's attacks are all resisted alike")
    assert "same effect" in team_incompatibility([damage, other], data)


def test_the_largest_hit_is_primary_and_the_others_raise_it(data) -> None:
    entries = [
        TeamEntry("Thug 1", 4, hit=True, degree=2),
        TeamEntry("Thug 2", 6, hit=True, degree=1),
        TeamEntry("Thug 3", 8, hit=False, degree=-2),
    ]
    result = combine_team_attack(entries, data)
    # Thug 3 missed: its rank does not count, nor do its degrees of failure.
    assert result.primary == 1
    assert result.other_degrees == 2 and result.step_bonus == 2 and result.bonus == 2


def test_three_degrees_is_plus_five_and_a_critical_adds_five_once(data) -> None:
    entries = [
        TeamEntry("A", 6, hit=True, degree=1),
        TeamEntry("B", 6, hit=True, degree=2, critical=True),
        TeamEntry("C", 6, hit=True, degree=1, critical=True),
    ]
    result = combine_team_attack(entries, data)
    assert result.other_degrees == 3
    assert result.step_bonus == 5 and result.critical_bonus == 5 and result.bonus == 10


def test_a_lone_hit_gains_nothing_and_no_hit_is_no_attack(data) -> None:
    lone = combine_team_attack([TeamEntry("A", 6, True), TeamEntry("B", 6, False, -1)], data)
    assert lone.primary == 0 and lone.bonus == 0
    assert not combine_team_attack([TeamEntry("A", 6, False, -1)], data).any_hit


def test_the_save_rises_with_the_bonus(data) -> None:
    save = team_attack_options(_damage(data, 8), data)[0].save
    raised = team_save_spec(save, 5)
    assert raised.dc == save.dc + 5
    assert raised.ladder.rank == save.ladder.rank + 5
    assert str(raised.dc) in raised.label
    assert team_save_spec(save, 0).dc == save.dc

"""Grab, Trip, Disarm, Smash, Escape and Block in ``core`` (combat maneuvers phase 9): the
records, the attack against Defense, the opposed second stage, the DC Escape reads off
the grabber, the outcome ladders (Grab's Holding on the grabber, the narrated rungs), and
Block's reaction check."""

from __future__ import annotations

from dataclasses import replace

import pytest

from mm_companion.core.character import Character
from mm_companion.core.data_loader import ConditionChange
from mm_companion.core.npc import quick_npc
from mm_companion.core.rules import (
    KIND_BEST_OF,
    action_reason,
    apply_condition,
    apply_condition_changes,
    attack_choices,
    attack_damage_rank,
    clear_temporary,
    concrete_changes,
    defense_class_in_play,
    localize_spec,
    reacting,
    reaction_changes,
    reaction_spec,
    self_action,
    targeted_action,
    targeted_check_spec,
    targeted_dc_from,
    targeted_opposed_spec,
    targeted_outcome_text,
    targeted_resist_spec,
    targeted_rung,
)


@pytest.fixture
def ana(data) -> Character:
    hero = quick_npc(data, name="Ana", attack=8, effect=6, defence=6, toughness=6)
    hero.abilities["STR"] = 3
    return hero


@pytest.fixture
def thug(data) -> Character:
    brute = quick_npc(data, name="Thug", attack=4, effect=6, defence=8, toughness=6)
    brute.abilities["STR"] = 5
    return brute


def _attack(char: Character, data):
    return attack_choices(char, data)[0].spec


def _rung(data, action_id: str, degree: int, option: str = ""):
    return targeted_rung(targeted_action(data, action_id).effect_for(option), degree)


def _changes(data, action_id: str, degree: int, option: str = ""):
    rung = _rung(data, action_id, degree, option)
    return concrete_changes(
        rung.applies if rung is not None else (), actor_name="Ana", target_name="Thug"
    )


def _ids(char: Character) -> list[tuple[str, str | None]]:
    return [(c.condition_id, c.parameter) for c in char.conditions if c.provenance is None]


# -- the records -------------------------------------------------------------------------


def test_the_phase_9_actions_load(data) -> None:
    for action_id in ("grab", "trip", "disarm", "smash", "escape"):
        assert targeted_action(data, action_id) is not None, action_id
    for action_id in ("grab", "trip", "disarm", "smash"):
        rules = targeted_action(data, action_id).targeted
        assert rules.vs_defense and rules.uses_attack and rules.dc is None
    assert targeted_action(data, "smash").targeted.opposed is None
    for action_id in ("grab", "trip", "disarm"):
        assert targeted_action(data, action_id).targeted.opposed is not None
    escape = targeted_action(data, "escape")
    assert not escape.targeted.uses_attack and escape.targeted.dc_from.dc == 10
    assert escape.requires == ("restrained", "bound")
    block = self_action(data, "block")
    assert block.reaction.condition == "blocking" and block.reaction.step == -2


def test_holding_bundles_hindered_and_vulnerable_and_is_not_temporary(data, ana) -> None:
    record = data.condition_catalog()["holding"]
    assert set(record.includes) == {"hindered", "vulnerable"}
    assert record.parameter.type == "character_ref" and not record.temporary
    before = defense_class_in_play(ana, data)
    apply_condition(ana, "holding", data, parameter="Thug")
    assert defense_class_in_play(ana, data) < before  # Vulnerable halves it
    assert clear_temporary(ana) == []  # a hold outlasts the turn
    apply_condition_changes(ana, (ConditionChange("holding", remove=True),), data)
    assert ana.conditions == []  # letting go takes the bundle with it


# -- the checks -----------------------------------------------------------------------------


def test_a_grab_is_an_attack_against_defense(data, ana) -> None:
    attack = _attack(ana, data)
    check = targeted_check_spec(data, targeted_action(data, "grab"), "", ana, attack)
    assert check.modifier == attack.modifier and check.dc is None
    assert check.label.endswith("vs. Defense") and check.follow_up is None and not check.marks


def test_disarm_and_smash_take_their_penalties(data, ana) -> None:
    attack = _attack(ana, data)
    disarm = targeted_check_spec(data, targeted_action(data, "disarm"), "", ana, attack)
    assert disarm.modifier == attack.modifier - 2
    smash = targeted_action(data, "smash")
    worn = targeted_check_spec(data, smash, "worn", ana, attack)
    held = targeted_check_spec(data, smash, "held", ana, attack)
    assert worn.modifier == attack.modifier - 2 and held.modifier == attack.modifier - 5


def test_the_opposed_stage_reads_the_actors_best(data, ana) -> None:
    attack = _attack(ana, data)
    grab = targeted_opposed_spec(data, targeted_action(data, "grab"), "", ana, attack)
    assert grab.modifier == 3 and "Strength" in grab.label and not grab.trait_key
    # Trip: the close attack bonus or Strength, whichever is higher.
    trip = targeted_opposed_spec(data, targeted_action(data, "trip"), "", ana, attack)
    assert trip.modifier == max(attack.modifier, 3)
    # Disarm: the attack's Damage rank.
    disarm = targeted_opposed_spec(data, targeted_action(data, "disarm"), "", ana, attack)
    assert disarm.modifier == attack_damage_rank(attack) == 6
    # An attack that deals no Damage cannot make a disarm's check.
    harmless = replace(attack, follow_up=None, also=())
    assert targeted_opposed_spec(data, targeted_action(data, "disarm"), "", ana, harmless) is None
    # Smash and the one-stage actions have no opposed stage.
    assert targeted_opposed_spec(data, targeted_action(data, "smash"), "", ana, attack) is None


def test_the_target_resists_with_strength_or_dodge(data, thug) -> None:
    grab = targeted_action(data, "grab")
    resist = targeted_resist_spec(data, grab, "Grab", 18)
    assert resist.kind == KIND_BEST_OF and resist.dc == 18
    assert "ability" in resist.trait_key and "DODGE" in resist.trait_key
    own = localize_spec(resist, thug, data)
    assert own.modifier >= 5  # at least the Thug's Strength
    disarm = targeted_resist_spec(data, targeted_action(data, "disarm"), "Disarm", 12)
    assert "DODGE" not in disarm.trait_key  # Strength alone
    assert targeted_resist_spec(data, targeted_action(data, "smash"), "Smash", 12) is None


def test_escape_rolls_the_best_against_the_grabbers_strength(data, ana, thug) -> None:
    escape = targeted_action(data, "escape")
    check = targeted_check_spec(data, escape, "", ana)
    assert check is not None and check.dc is None
    assert targeted_dc_from(data, escape, thug) == 15
    assert targeted_dc_from(data, targeted_action(data, "grab"), thug) is None
    assert action_reason(data, escape, ana) == "Only while Restrained or Bound."
    apply_condition(ana, "bound", data)
    assert action_reason(data, escape, ana) is None


# -- the outcomes -----------------------------------------------------------------------------


def test_a_grab_holds_or_binds_and_the_grabber_is_holding(data, ana, thug) -> None:
    one = _changes(data, "grab", 1)
    assert [(on, c.condition, c.parameter) for on, c in one] == [
        ("target", "restrained", None),
        ("actor", "holding", "Thug"),
    ]
    two = _changes(data, "grab", 3)
    assert ("target", "bound", None) in [(on, c.condition, c.parameter) for on, c in two]
    # Losing the opposed check: grabbed, not held — said, nothing lands.
    lost = _rung(data, "grab", -1)
    assert lost.applies == () and "{target}" in lost.text


def test_trip_leaves_the_target_prone_and_disarm_and_smash_are_narrated(data) -> None:
    assert [c.condition for _on, c in _changes(data, "trip", 1)] == ["prone"]
    assert _rung(data, "trip", 2).text
    assert _changes(data, "disarm", 1) == () and "drops" in _rung(data, "disarm", 1).text
    assert _changes(data, "smash", 1, "held") == () and _rung(data, "smash", 1, "held").text
    assert _rung(data, "disarm", -1) is None


def test_escape_ends_the_hold_on_both(data, ana, thug) -> None:
    apply_condition(ana, "restrained", data)
    apply_condition(thug, "holding", data, parameter="Ana")
    apply_condition(thug, "holding", data, parameter="Bo")
    changes = concrete_changes(
        _rung(data, "escape", 1).applies, actor_name="Ana", target_name="Thug"
    )
    mine = tuple(c for on, c in changes if on == "actor")
    theirs = tuple(c for on, c in changes if on == "target")
    assert targeted_outcome_text(data, mine, "Ana", ana) == "Ana: no longer Restrained"
    apply_condition_changes(ana, mine, data)
    apply_condition_changes(thug, theirs, data)
    assert ana.conditions == []
    assert _ids(thug) == [("holding", "Bo")]  # only Ana's hold ends
    assert "{actor}" in _rung(data, "escape", 2).text


# -- Block --------------------------------------------------------------------------------------


def test_block_readies_then_each_block_is_harder(data, ana) -> None:
    block = self_action(data, "block")
    attack = _attack(ana, data)
    assert not reacting(block, ana)
    apply_condition_changes(ana, block.effect.applies, data)
    assert reacting(block, ana)
    first = reaction_spec(data, block, ana, attack)
    assert first.modifier == attack.modifier and first.dc is None and first.follow_up is None
    assert "attacker's result" in first.label
    apply_condition_changes(ana, reaction_changes(block), data)
    second = reaction_spec(data, block, ana, attack)
    assert second.modifier == attack.modifier - 2 and "1 earlier block" in second.label
    apply_condition_changes(ana, reaction_changes(block), data)
    assert reaction_spec(data, block, ana, attack).modifier == attack.modifier - 4
    assert reaction_spec(data, block, ana, None) is None
    # The start of the next turn resets it.
    clear_temporary(ana)
    assert not reacting(block, ana)

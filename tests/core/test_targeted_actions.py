"""Aid, Feint and Trick in ``core`` (combat maneuvers phase 8): the checks, the opposed
resistance, the outcome ladders, and the opposing conditions they leave — counted and
spent only in an attack between the two creatures they name (decision 17)."""

from __future__ import annotations

import pytest

from mm_companion.core.character import Character
from mm_companion.core.data_loader import ConditionChange
from mm_companion.core.npc import quick_npc
from mm_companion.core.rules import (
    KIND_BEST_OF,
    actor_degree,
    apply_condition,
    apply_condition_changes,
    attack_choices,
    clear_temporary,
    concrete_changes,
    condition_amount_mods,
    defense_against,
    defense_class_in_play,
    has_attack_check,
    has_temporary,
    localize_spec,
    opposing_effects,
    opposing_spent,
    targeted_action,
    targeted_check_spec,
    targeted_outcome,
    targeted_outcome_text,
    targeted_resist_spec,
    targeted_title,
)


@pytest.fixture
def ana(data) -> Character:
    return quick_npc(data, name="Ana", attack=8, effect=6, defence=6, toughness=6)


@pytest.fixture
def thug(data) -> Character:
    return quick_npc(data, name="Thug", attack=4, effect=6, defence=8, toughness=6)


def _attack(char: Character, data):
    return attack_choices(char, data)[0].spec


def _changes(data, action_id: str, option: str, degree: int):
    action = targeted_action(data, action_id)
    return concrete_changes(
        targeted_outcome(action.effect_for(option), degree), actor_name="Ana", target_name="Thug"
    )


# -- the records -------------------------------------------------------------------------


def test_the_three_actions_are_targeted_and_only_they(data) -> None:
    for action_id in ("aid", "feint", "trick"):
        assert targeted_action(data, action_id) is not None
    # Surprise Attack is targeted too, but the GM grants it: no roll of its own.
    assert targeted_action(data, "surprise_attack") is None
    assert targeted_action(data, "defend") is None


def test_the_opposing_conditions_are_not_temporary(data, thug) -> None:
    catalog = data.condition_catalog()
    for condition_id in ("aided_attack", "aided_defense", "feinted", "tricked", "trick_advantage"):
        record = catalog[condition_id]
        assert record.opposing is not None and not record.temporary
        assert record.parameter is not None and record.parameter.type == "character_ref"
    apply_condition(thug, "feinted", data, parameter="Ana")
    assert not has_temporary(thug)
    assert clear_temporary(thug) == [] and thug.conditions  # Clear temporary leaves it


# -- the checks -----------------------------------------------------------------------------


def test_a_feint_rolls_the_attack_without_its_save_or_marks(data, ana) -> None:
    feint = targeted_action(data, "feint")
    attack = _attack(ana, data)
    spec = targeted_check_spec(data, feint, "standard", ana, attack)
    assert spec.modifier == attack.modifier and spec.dc is None
    assert spec.follow_up is None and spec.also == () and spec.ladder is None
    # Not an Attack action: the attack maneuvers do not ride on it.
    assert not has_attack_check(spec, data)
    assert spec.label == f"Feint (Attack {attack.modifier:+d})"
    simple = targeted_check_spec(data, feint, "simple", ana, attack)
    assert simple.modifier == attack.modifier - 5
    assert targeted_check_spec(data, feint, "standard", ana, None) is None


def test_a_trick_rolls_deception_and_aid_rolls_against_dc_10(data, ana) -> None:
    ana.skill_ranks["Deception"] = 6
    trick = targeted_action(data, "trick")
    spec = targeted_check_spec(data, trick, "", ana)
    assert spec.label.startswith("Trick (Deception") and spec.dc is None
    aid = targeted_action(data, "aid")
    spec = targeted_check_spec(data, aid, "attack", ana, _attack(ana, data))
    assert spec.dc == 10 and spec.label.endswith("vs. DC 10")
    assert targeted_title(aid, "defense") == "Aid their Defense"


def test_the_target_resists_with_the_best_of_its_candidates(data, thug) -> None:
    feint = targeted_action(data, "feint")
    spec = targeted_resist_spec(data, feint, "Feint", 17)
    assert spec.kind == KIND_BEST_OF and spec.dc == 17
    assert "Close Combat" in spec.trait_key and "Insight" in spec.trait_key
    thug.skill_ranks["Insight"] = 9
    own = localize_spec(spec, thug, data)
    assert own.modifier == 9  # Insight (AWE 0 + 9) beats its attack-based combat skills (4)
    assert targeted_resist_spec(data, targeted_action(data, "aid"), "Aid") is None


# -- the outcome ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("target_degree", "expected"), [(-1, 1), (-3, 3), (1, -1), (2, -2), (None, 0)]
)
def test_the_actors_degree_mirrors_the_targets(target_degree, expected) -> None:
    assert actor_degree(target_degree) == expected


def test_a_won_feint_leaves_the_target_feinted_against_the_feinter(data) -> None:
    ((on, change),) = _changes(data, "feint", "standard", 1)
    assert on == "target" and change.condition == "feinted" and change.parameter == "Ana"
    assert _changes(data, "feint", "standard", -2) == ()


def test_a_trick_lost_badly_gives_the_target_its_advantage(data) -> None:
    assert _changes(data, "trick", "", -1) == ()
    ((on, change),) = _changes(data, "trick", "", -2)
    assert change.condition == "trick_advantage" and change.parameter == "Ana"
    ((_on, won),) = _changes(data, "trick", "", 2)
    assert won.condition == "tricked"


def test_aid_lands_on_the_ally_against_the_opponent_by_degrees(data) -> None:
    ((on, change),) = _changes(data, "aid", "attack", 1)
    assert on == "ally" and change.condition == "aided_attack"
    assert change.parameter == "Thug" and change.amount == 2
    ((_on, strong),) = _changes(data, "aid", "defense", 3)
    assert strong.condition == "aided_defense" and strong.amount == 5
    assert _changes(data, "aid", "attack", -1) == ()
    text = targeted_outcome_text(data, (strong,), "Bo")
    assert text == "Bo: Aided Defense (Thug) +5"


# -- the opposing conditions in one attack ------------------------------------------------


def test_opposing_conditions_count_only_between_the_two_they_name(data, ana, thug) -> None:
    apply_condition(thug, "feinted", data, parameter="Ana")
    apply_condition(ana, "aided_attack", data, parameter="Thug", amount=5)
    # Nothing on the sheets moves: the conditions are about one attack.
    assert condition_amount_mods(ana, data) == {}
    effects = opposing_effects(ana, "Ana", thug, "Thug", data)
    assert effects.attack == 5 and effects.defense_as == ("vulnerable",)
    assert defense_against(thug, data, effects) < defense_class_in_play(thug, data)
    # Someone else attacking the Thug gets neither.
    other = opposing_effects(Character(), "Bo", thug, "Thug", data)
    assert not other.any and defense_against(thug, data, other) == defense_class_in_play(thug, data)


def test_aided_defense_counts_against_the_named_attacker(data, ana, thug) -> None:
    apply_condition(ana, "aided_defense", data, parameter="Thug", amount=2)
    effects = opposing_effects(thug, "Thug", ana, "Ana", data)
    assert effects.defense == 2 and effects.target_spent
    assert defense_against(ana, data, effects) == defense_class_in_play(ana, data) + 2


def test_spending_takes_off_only_the_instance_named(data, ana) -> None:
    apply_condition(ana, "aided_attack", data, parameter="Thug", amount=2)
    apply_condition(ana, "aided_attack", data, parameter="Brute", amount=5)
    effects = opposing_effects(ana, "Ana", Character(), "Thug", data)
    assert apply_condition_changes(ana, opposing_spent(effects.attacker_spent), data)
    assert [(c.condition_id, c.parameter) for c in ana.conditions] == [("aided_attack", "Brute")]


def test_a_change_carries_its_parameter_onto_the_sheet(data, thug) -> None:
    apply_condition_changes(thug, (ConditionChange("feinted", parameter="Ana"),), data)
    assert thug.conditions[0].parameter == "Ana"

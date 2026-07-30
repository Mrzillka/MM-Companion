"""``core.rules.rolls``: what rolling a trait or a power looks like.

The layer the whole double-click-to-roll feature stands on, and the reason no
widget computes a roll modifier. Headless — no Qt anywhere in this file, which is
the point: the arithmetic is provable without a display.
"""

from __future__ import annotations

import pytest

from mm_companion.core.character import AppliedCondition, Character
from mm_companion.core.data_loader import load_game_data
from mm_companion.core.powers import ModifierSelection, Power, PowerEffectInstance
from mm_companion.core.rules import (
    KIND_POWER_CHECK,
    KIND_POWER_SAVE,
    RollSpec,
    ability_roll,
    apply_condition,
    effect_outcome_ladder,
    follow_up_offered,
    initiative_roll,
    power_roll_lines,
    power_rolls,
    resistance_outcome,
    resistance_roll,
    skill_roll,
    skill_row_label,
)


@pytest.fixture(scope="module")
def data():
    return load_game_data()


@pytest.fixture
def hero(data) -> Character:
    char = Character.new_default(data)
    char.abilities.update({"STR": 4, "STA": 5, "AGL": 3, "AWE": 2, "ATK": 7})
    char.skill_ranks["Athletics"] = 5
    return char


# -- trait rolls --------------------------------------------------------------


def test_an_ability_rolls_its_effective_value_under_its_display_name(hero, data) -> None:
    spec = ability_roll(hero, data, "STR")
    assert spec.label == "Strength"
    assert spec.modifier == 4
    # A trait check carries no DC of its own — the roller's DC box decides.
    assert spec.dc is None


def test_a_resistance_rolls_its_total_not_its_bought_ranks(hero, data) -> None:
    # Toughness derives from Stamina, so the total is the base even with nothing bought.
    assert hero.resistances.get("TOUGHNESS", 0) == 0
    assert resistance_roll(hero, data, "TOUGHNESS").modifier == 5


def test_a_skill_rolls_the_total_the_sheet_shows(hero, data) -> None:
    spec = skill_roll(hero, data, "Athletics")
    assert spec.label == "Athletics"
    assert spec.modifier == 9  # Strength 4 + 5 ranks


def test_initiative_rolls_its_modifier_and_names_the_ability(hero, data) -> None:
    spec = initiative_roll(hero, data)
    assert spec.label == "Initiative"
    assert spec.modifier == 3  # Agility, no Improved Initiative
    assert "AGL" in spec.hint


def test_a_condition_penalty_is_rolled_because_it_is_shown(hero, data) -> None:
    """What is rolled must match what the sheet displays, penalties included.

    The build math stays condition-free (a penalty never rewrites what was bought),
    but the *roll* is the displayed number — otherwise a penalised character would
    silently roll their unpenalised total.
    """
    before = skill_roll(hero, data, "Athletics").modifier
    apply_condition(hero, "impaired", data, parameter="Athletics")
    after = skill_roll(hero, data, "Athletics").modifier
    assert after < before
    assert isinstance(hero.conditions[0], AppliedCondition)


def test_a_specialized_skill_row_reads_as_its_pool() -> None:
    assert skill_row_label("Stealth::spec::Urban") == "Stealth (Urban)"
    assert skill_row_label("Expertise: Law") == "Expertise: Law"


# -- power rolls --------------------------------------------------------------


def _blast(rank: int = 8) -> Power:
    return Power(name="Blast", effects=[PowerEffectInstance("damage", rank=rank)])


def test_an_attack_and_the_save_it_forces_are_separate_rolls(hero, data) -> None:
    attack, save = power_rolls(_blast(), hero, data)

    # The attacker rolls their Attack; the DC is the *target's* Defense, which this
    # sheet cannot know, so the roller's DC box supplies it.
    assert attack.kind == KIND_POWER_CHECK
    assert attack.modifier == 7
    assert attack.dc is None

    # The save is the mirror image: the DC is known (10 + rank), the modifier is the
    # target's resistance and therefore isn't.
    assert save.kind == KIND_POWER_SAVE
    assert save.modifier == 0
    assert save.dc == 18


def test_the_attack_carries_the_save_as_its_follow_up(hero, data) -> None:
    attack, save = power_rolls(_blast(), hero, data)
    assert attack.follow_up is not None
    assert attack.follow_up.label == save.label
    assert attack.follow_up.dc == save.dc


def test_a_strength_based_damage_raises_the_save_dc_it_forces(hero, data) -> None:
    power = Power(
        name="Punch",
        effects=[
            PowerEffectInstance("damage", rank=2, extras=[ModifierSelection("strength_based")])
        ],
    )
    _attack, save = power_rolls(power, hero, data)
    # 10 + (rank 2 folded with Strength 4): the effective rank sets the DC, the
    # bought rank pays for it.
    assert save.dc == 16


def test_a_multi_effect_power_says_which_effect_each_roll_belongs_to(hero, data) -> None:
    power = Power(
        name="Sleep Ray",
        effects=[
            PowerEffectInstance("damage", rank=4),
            PowerEffectInstance("affliction", rank=4, config={"resistance": "Will"}),
        ],
    )
    labels = power_roll_lines(power, hero, data)
    assert any(label.startswith("Damage: ") for label in labels)
    assert any(label.startswith("Affliction: ") for label in labels)


def test_a_power_that_rolls_nothing_yields_no_specs(hero, data) -> None:
    armor = Power(name="Armor", effects=[PowerEffectInstance("protection", rank=6)])
    assert power_rolls(armor, hero, data) == []


def test_the_footer_text_is_exactly_the_specs_labels(hero, data) -> None:
    # What is written on the card and what its 🎲 rolls can never drift apart.
    power = _blast()
    assert power_roll_lines(power, hero, data) == [s.label for s in power_rolls(power, hero, data)]


# -- outcome ladders ----------------------------------------------------------


def test_damage_reads_its_ladder_from_the_condition_catalog(hero, data) -> None:
    _attack, save = power_rolls(_blast(), hero, data)
    assert save.outcomes[0].startswith("Hit + Dazed")
    assert "Incapacitated" in save.outcomes[-1]


def test_an_afflictions_ladder_is_the_conditions_its_builder_chose(hero, data) -> None:
    effect = PowerEffectInstance(
        "affliction",
        rank=6,
        config={
            "resistance": "Will",
            "degree1": "dazed",
            "degree2": "stunned",
            "degree3": "asleep",
        },
    )
    assert effect_outcome_ladder(effect, data) == ("Dazed", "Stunned", "Asleep")


def test_an_unconfigured_affliction_has_no_ladder_to_show(data) -> None:
    assert effect_outcome_ladder(PowerEffectInstance("affliction", rank=6), data) == ()


def test_the_last_rung_answers_every_deeper_failure() -> None:
    spec = RollSpec(label="Toughness", dc=18, outcomes=("Dazed", "Staggered", "Incapacitated"))
    assert resistance_outcome(spec, -1) == "Dazed"
    assert resistance_outcome(spec, -3) == "Incapacitated"
    assert resistance_outcome(spec, -7) == "Incapacitated"


def test_a_save_that_held_has_no_outcome() -> None:
    spec = RollSpec(label="Toughness", dc=18, outcomes=("Dazed",))
    assert resistance_outcome(spec, 2) == ""
    # No DC was set, so the roll was never graded — there is no outcome to state.
    assert resistance_outcome(spec, None) == ""


def test_a_follow_up_is_offered_on_a_hit_or_an_ungraded_roll() -> None:
    save = RollSpec(label="Toughness", dc=18)
    attack = RollSpec(label="Attack", modifier=7, follow_up=save)
    assert follow_up_offered(attack, 1) is True
    # Nobody typed the target's Defense, so the player judges it — offer it anyway.
    assert follow_up_offered(attack, None) is True
    assert follow_up_offered(attack, -2) is False
    # A roll that provokes nothing never offers anything.
    assert follow_up_offered(save, 1) is False

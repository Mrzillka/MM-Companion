"""The attacks and areas the roller's Actions popup offers a character (``attack_choices``)."""

from __future__ import annotations

from mm_companion.core.character import Character
from mm_companion.core.powers import ModifierSelection, Power, PowerEffectInstance
from mm_companion.core.rules import (
    KIND_AREA,
    area_choices,
    attack_choices,
    targetable_attack,
)


def _blast(rank: int = 8) -> Power:
    damage = PowerEffectInstance("damage", rank=rank)
    damage.extras.append(ModifierSelection("ranged", config={"from": "close"}))
    return Power(name="Blast", effects=[damage])


def _burst(rank: int = 6) -> Power:
    effect = PowerEffectInstance(
        "damage", rank=rank, extras=[ModifierSelection("area_effect", config={})]
    )
    return Power(name="Fire Burst", effects=[effect])


def _hero(data, *powers: Power) -> Character:
    hero = Character.new_default(data)
    hero.powers = list(powers)
    return hero


def test_a_character_with_no_powers_has_no_attacks(data) -> None:
    hero = _hero(data)
    assert attack_choices(hero, data) == []
    assert area_choices(hero, data) == []


def test_an_attack_power_is_offered_by_name_with_its_attack_line(data) -> None:
    (choice,) = attack_choices(_hero(data, _blast()), data)
    assert targetable_attack(choice.spec)
    assert choice.caption.startswith("Blast: ")
    assert choice.spec.label in choice.caption


def test_an_area_power_is_an_area_choice_keyed_for_the_area_window(data) -> None:
    hero = _hero(data, _burst(), _blast())
    (area,) = area_choices(hero, data)
    assert area.spec.kind == KIND_AREA
    assert area.caption.startswith("Fire Burst")
    assert area.spec.trait_key  # the area key DiceSection.start_area opens
    # The picker offers exactly the attack lines the cards draw, in sheet order (the
    # area power's card has one too, for a primary target).
    assert [c.caption.split(":")[0] for c in attack_choices(hero, data)] == [
        "Fire Burst",
        "Blast",
    ]

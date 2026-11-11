"""Prone's attack modifiers reaching the attack (PDF p239).

The record's ``attackMods`` — −5 on the bearer's own close attacks, +5 to close and −5
to ranged attacks against them — used to be read by the Conditions block's display
only. The attacker's own goes into the roll ``power_rolls`` builds; the target's is
added where the attack is judged against it (``opposing_effects``), which needs the
spec to say whether it is a close or a ranged attack (``RollSpec.attack_range``).
Headless: everything here is ``core``.
"""

from __future__ import annotations

import pytest

from mm_companion.core.character import Character
from mm_companion.core.data_loader import AttackRanges
from mm_companion.core.data_loader.parsing_rules import _parse_system
from mm_companion.core.powers import ModifierSelection, Power, PowerEffectInstance
from mm_companion.core.rules import (
    ATTACK_CLOSE,
    ATTACK_RANGED,
    RollSpec,
    apply_condition,
    condition_attack_check_mod,
    condition_incoming_attack_mod,
    effect_attack_range,
    opposing_effects,
    power_rolls,
)
from mm_companion.core.session.protocol import sanitize_spec


@pytest.fixture
def hero(data) -> Character:
    char = Character.new_default(data)
    char.abilities.update({"STR": 4, "STA": 5, "AGL": 3, "AWE": 2, "ATK": 7})
    char.resistances["DEF"] = 8
    return char


def _punch() -> PowerEffectInstance:
    return PowerEffectInstance("damage", rank=8)


def _blast() -> PowerEffectInstance:
    return PowerEffectInstance("damage", rank=8, extras=[ModifierSelection("ranged")])


def _stare() -> PowerEffectInstance:
    return PowerEffectInstance(
        "affliction",
        rank=8,
        config={"resistance": "Will", "degree1": "dazed", "degree2": "stunned"},
        extras=[ModifierSelection("perception_range")],
    )


def _attack(effect: PowerEffectInstance, char: Character, data) -> RollSpec:
    return power_rolls(Power(name="Attack", effects=[effect]), char, data)[0]


# -- which attacks are close and which ranged ---------------------------------------------


def test_the_effective_range_says_close_or_ranged(data) -> None:
    assert effect_attack_range(_punch(), data) == ATTACK_CLOSE
    # The Ranged extra makes a Close Damage a ranged attack.
    assert effect_attack_range(_blast(), data) == ATTACK_RANGED
    assert effect_attack_range(_stare(), data) == ""


def test_the_attack_spec_records_its_range(hero, data) -> None:
    assert _attack(_punch(), hero, data).attack_range == ATTACK_CLOSE
    assert _attack(_blast(), hero, data).attack_range == ATTACK_RANGED


def test_which_ranges_are_which_is_data() -> None:
    parsed = _parse_system({"system": {"attackRanges": {"close": "Melee"}}})
    assert parsed.attack_ranges == AttackRanges(close="Melee", ranged="Ranged")
    assert _parse_system({}).attack_ranges == AttackRanges()


# -- the prone attacker's own −5 ----------------------------------------------------------


def test_a_prone_attackers_close_attack_takes_the_penalty(hero, data) -> None:
    before = _attack(_punch(), hero, data)
    apply_condition(hero, "prone", data)
    after = _attack(_punch(), hero, data)
    assert after.modifier == before.modifier - 5
    assert after.label == f"Attack {after.modifier:+d} vs. Defense"
    assert "Prone" in after.hint
    assert ("Conditions", -5) in after.parts
    # The save the attack forces is untouched.
    assert after.follow_up == before.follow_up


def test_a_prone_attackers_ranged_attack_is_untouched(hero, data) -> None:
    before = _attack(_blast(), hero, data)
    apply_condition(hero, "prone", data)
    assert _attack(_blast(), hero, data).modifier == before.modifier


def test_the_penalty_adds_to_an_attack_check_amount_mod(hero, data) -> None:
    apply_condition(hero, "prone", data)
    apply_condition(hero, "defensive_attack", data, amount=2)
    total, text = condition_attack_check_mod(hero, data, ATTACK_CLOSE)
    assert total == -7
    assert "Prone" in text and "Defensive Attack" in text
    # With no range given (the default), only the amount mod counts.
    assert condition_attack_check_mod(hero, data)[0] == -2


def test_a_linked_close_attack_takes_the_penalty_once(hero, data) -> None:
    affliction = PowerEffectInstance(
        "affliction",
        rank=8,
        config={"resistance": "Fortitude", "degree1": "dazed", "degree2": "stunned"},
    )
    power = Power(name="Thunderbolt", structure="linked", effects=[_punch(), affliction])
    before = power_rolls(power, hero, data)[0]
    apply_condition(hero, "prone", data)
    after = power_rolls(power, hero, data)[0]
    assert after.modifier == before.modifier - 5
    assert after.attack_range == ATTACK_CLOSE


# -- the prone target's +5 close / −5 ranged ----------------------------------------------


def test_a_prone_target_helps_close_attacks_and_hinders_ranged_ones(data) -> None:
    thug = Character()
    assert condition_incoming_attack_mod(thug, data, ATTACK_CLOSE) == (0, "")
    apply_condition(thug, "prone", data)
    assert condition_incoming_attack_mod(thug, data, ATTACK_CLOSE) == (5, "+5 Prone")
    assert condition_incoming_attack_mod(thug, data, ATTACK_RANGED) == (-5, "-5 Prone")
    # An attack whose range is unknown (an older client's spec) gets nothing.
    assert condition_incoming_attack_mod(thug, data, "") == (0, "")


def test_the_judge_adds_the_targets_posture_and_spends_nothing(hero, data) -> None:
    thug = Character()
    apply_condition(thug, "prone", data)
    close = opposing_effects(hero, "Ana", thug, "Thug", data, attack_range=ATTACK_CLOSE)
    assert close.attack == 5
    assert close.notes == ("Thug: +5 Prone",)
    # Prone lasts until the target stands: nothing is spent by the attack.
    assert not close.any and not close.target_spent
    ranged = opposing_effects(hero, "Ana", thug, "Thug", data, attack_range=ATTACK_RANGED)
    assert ranged.attack == -5
    assert opposing_effects(hero, "Ana", thug, "Thug", data).attack == 0


def test_the_attackers_own_posture_is_not_counted_twice(hero, data) -> None:
    """A prone attacker's −5 is in the rolled bonus already; the judge adds only the
    target's."""
    apply_condition(hero, "prone", data)
    effects = opposing_effects(hero, "Ana", Character(), "Thug", data, attack_range=ATTACK_CLOSE)
    assert effects.attack == 0 and effects.notes == ()


# -- on the wire --------------------------------------------------------------------------


def test_the_range_travels_with_the_spec(hero, data) -> None:
    spec = _attack(_punch(), hero, data)
    raw = spec.to_dict()
    assert raw["attack_range"] == ATTACK_CLOSE
    assert sanitize_spec(raw)["attack_range"] == ATTACK_CLOSE
    assert RollSpec.from_dict(sanitize_spec(raw)).attack_range == ATTACK_CLOSE


def test_a_junk_range_off_the_wire_is_dropped() -> None:
    spec = RollSpec.from_dict({"label": "Attack +5", "attack_range": "sideways"})
    assert spec is not None and spec.attack_range == ""
    assert "attack_range" not in RollSpec(label="Toughness").to_dict()

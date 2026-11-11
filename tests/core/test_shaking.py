"""How a condition an impression or an Affliction put on comes off (``core.rules.shaking``).

The record is written on the condition itself (``AppliedCondition.shake_off``), so it
saves, travels to a player and back in their snapshot, and outlives the GM's app. A
condition carrying one offers no recovery check of its own: an Affliction's Stunned is
overcome with the Affliction (PDF p109), not rolled off with the damage-caused Stunned's
DC 10 Stamina check (p27). Headless.
"""

from __future__ import annotations

from mm_companion.core.character import (
    SHAKE_EFFECT,
    SHAKE_IMPRESSION,
    AppliedCondition,
    Character,
    ShakeOff,
)
from mm_companion.core.powers import Power, PowerEffectInstance
from mm_companion.core.rules import (
    LADDER_EFFECT,
    SpecLadder,
    apply_condition,
    area_shake_off,
    can_recover,
    effect_spec_ladder,
    end_of_turn_recoveries,
    held_shake_offs,
    impression_shake_off,
    ladder_shake_off,
    power_rolls,
    recovery_request,
    shake_off_each_minute,
    shake_off_hint,
    shake_off_request,
    shake_off_roll,
)

AFFLICTION = {
    "resistance": "Fortitude",
    "degree1": "dazed",
    "degree2": "stunned",
    "degree3": "incapacitated",
}


def _sleep(**config) -> ShakeOff:
    return ShakeOff(kind=SHAKE_EFFECT, name="Sleep Ray", dc=17, ref="affliction", **config)


# -- the record --------------------------------------------------------------------------


def test_a_record_round_trips_and_an_old_save_loads_without_one() -> None:
    shake = _sleep(trait="resistance:WILL", source="Doc", degree=2)
    applied = AppliedCondition("stunned", shake_off=shake)
    raw = applied.to_dict()
    assert raw["shakeOff"]["dc"] == 17
    assert AppliedCondition.from_dict(raw) == applied
    # Saved before this existed: no key, no record — and nothing written for one.
    assert AppliedCondition.from_dict({"id": "stunned"}).shake_off is None
    assert "shakeOff" not in AppliedCondition("stunned").to_dict()


def test_a_junk_record_is_dropped_rather_than_trusted() -> None:
    assert ShakeOff.from_dict({"kind": "effect", "name": "X", "dc": "17"}) is None
    assert ShakeOff.from_dict({"kind": "", "name": "X", "dc": 17}) is None
    assert ShakeOff.from_dict({"kind": "effect", "name": "X", "dc": True}) is None
    assert ShakeOff.from_dict("effect") is None
    kept = ShakeOff.from_dict({"kind": "effect", "name": "X", "dc": 9, "degree": -4})
    assert kept is not None and kept.degree == 0


def test_only_the_condition_itself_carries_it_not_what_it_bundles(data) -> None:
    char = Character()
    apply_condition(char, "staggered", data, shake_off=_sleep())
    direct = [c for c in char.conditions if c.provenance is None]
    members = [c for c in char.conditions if c.provenance is not None]
    assert [c.condition_id for c in direct] == ["staggered"] and direct[0].shake_off
    assert members and all(c.shake_off is None for c in members)


def test_a_condition_already_on_for_another_reason_keeps_coming_off_that_way(data) -> None:
    """Dazed by damage, then dazed by an Affliction: the Stamina check still applies."""
    char = Character()
    apply_condition(char, "dazed", data)
    apply_condition(char, "dazed", data, shake_off=_sleep())
    (dazed,) = char.conditions
    assert dazed.shake_off is None
    # And the other way round, damage dazing it again: it is now a damage condition too.
    other = Character()
    apply_condition(other, "dazed", data, shake_off=_sleep())
    apply_condition(other, "dazed", data)
    assert other.conditions[0].shake_off is None


# -- no Stamina check for an Affliction's Stunned (the bug this fixes) -------------------


def test_an_afflictions_stunned_offers_no_stamina_check(data) -> None:
    char = Character()
    apply_condition(char, "stunned", data, shake_off=_sleep())
    (stunned,) = char.conditions
    assert not can_recover(char, data, stunned)
    assert end_of_turn_recoveries(char, data) == []
    assert recovery_request(data, stunned) is None
    # A damage-caused one still does.
    damage = Character()
    apply_condition(damage, "stunned", data)
    assert [c.condition_id for c in end_of_turn_recoveries(damage, data)] == ["stunned"]


# -- writing it ----------------------------------------------------------------------------


def test_an_afflictions_save_carries_how_it_is_overcome(data) -> None:
    effect = PowerEffectInstance("affliction", rank=7, config={**AFFLICTION, "overcomeBy": "Will"})
    ladder = effect_spec_ladder(effect, data, name="Sleep Ray")
    assert ladder.effect == "affliction" and ladder.name == "Sleep Ray"
    assert ladder.overcome == "resistance:WILL"
    assert SpecLadder.from_dict(ladder.to_dict()) == ladder
    # Overcome by the resistance it was resisted with when none is chosen.
    plain = effect_spec_ladder(PowerEffectInstance("affliction", rank=7, config=AFFLICTION), data)
    assert plain.effect == "affliction" and plain.overcome == ""
    # Damage's conditions recover as their records say.
    assert effect_spec_ladder(PowerEffectInstance("damage", rank=7), data).effect == ""


def test_a_powers_save_names_the_ladder_after_the_power(data) -> None:
    effect = PowerEffectInstance("affliction", rank=7, config=AFFLICTION)
    saves = [s for s in power_rolls(Power(name="Sleep Ray", effects=[effect]), None, data)]
    save = next(s for s in saves if s.ladder is not None)
    assert save.ladder.name == "Sleep Ray"


def test_a_failed_save_writes_a_record_and_a_made_one_does_not(data) -> None:
    ladder = SpecLadder(LADDER_EFFECT, effect="affliction", name="Sleep Ray")
    shake = ladder_shake_off(ladder, 17, -2, trait="resistance:FORTITUDE", source="Doc")
    assert shake == ShakeOff(
        SHAKE_EFFECT, "Sleep Ray", 17, "affliction", "resistance:FORTITUDE", "", "Doc", 2
    )
    assert ladder_shake_off(ladder, 17, 1) is None
    assert ladder_shake_off(ladder, None, -2) is None
    assert ladder_shake_off(SpecLadder("damage"), 17, -2) is None
    # The chosen "overcome by" wins over the trait it was resisted with.
    chosen = SpecLadder(LADDER_EFFECT, effect="affliction", overcome="resistance:WILL")
    assert ladder_shake_off(chosen, 17, -1, trait="resistance:DODGE").trait == "resistance:WILL"


def test_an_areas_affliction_writes_one_from_its_config(data) -> None:
    shake = area_shake_off(
        "affliction", AFFLICTION, data, 15, -99, name="Gas Cloud", resisted_with="FORTITUDE"
    )
    assert shake is not None and shake.trait == "resistance:FORTITUDE"
    assert shake.degree == 3  # the last rung covers every deeper failure
    assert area_shake_off("damage", {}, data, 15, -1, name="Blast") is None


def test_an_impression_writes_what_resists_it_again(data) -> None:
    demoralized = data.impress.impression("demoralized")
    shake = impression_shake_off(demoralized, "Intimidation", 19, source="Thug", degree=-1)
    assert shake.kind == SHAKE_IMPRESSION and shake.ref == "demoralized" and shake.dc == 19
    assert shake.skill == "Intimidation" and shake.trait == "" and shake.degree == 1


# -- reading it back as a turn ends -------------------------------------------------------


def test_one_rungs_conditions_are_held_and_shaken_together(data) -> None:
    char = Character()
    sleep = _sleep()
    for condition_id in ("stunned", "hindered"):
        apply_condition(char, condition_id, data, shake_off=sleep)
    apply_condition(char, "hit", data)
    ((shake, changes),) = held_shake_offs(char)
    assert shake == sleep and changes == (("stunned", None), ("hindered", None))


def test_an_affliction_at_its_third_degree_is_overcome_once_a_minute(data) -> None:
    assert not shake_off_each_minute(data, _sleep(degree=2))
    assert shake_off_each_minute(data, _sleep(degree=3))
    demoralized = data.impress.impression("demoralized")
    impression = impression_shake_off(demoralized, "Intimidation", 19, degree=-3)
    assert not shake_off_each_minute(data, impression)


def test_an_npc_overcomes_it_with_its_own_number_at_the_original_dc(data) -> None:
    npc = Character()
    npc.resistances["WILL"] = 4
    spec = shake_off_roll(npc, data, _sleep(trait="resistance:WILL"))
    assert spec is not None and spec.dc == 17 and "Sleep Ray" in spec.label
    assert spec.modifier == shake_off_roll(npc, data, _sleep(trait="resistance:WILL")).modifier
    assert shake_off_roll(npc, data, _sleep()) is None  # nothing named to roll


def test_a_player_is_asked_for_the_trait_by_name(data) -> None:
    spec = shake_off_request(_sleep(trait="resistance:WILL"))
    assert spec is not None and spec.kind == "resistance" and spec.trait_key == "WILL"
    assert spec.modifier == 0 and spec.dc == 17
    assert shake_off_request(_sleep()) is None


def test_a_chip_says_how_it_comes_off_in_words(data) -> None:
    shake = _sleep(trait="resistance:WILL", source="Doc", degree=2)
    assert shake_off_hint(data, shake) == (
        "From Sleep Ray (Doc): overcome with Will against DC 17 at the end of each turn; "
        "success removes everything it put on."
    )
    assert "once a minute" in shake_off_hint(data, _sleep(degree=3))
    demoralized = data.impress.impression("demoralized")
    impression = impression_shake_off(demoralized, "Intimidation", 19, source="Thug")
    assert shake_off_hint(data, impression).startswith("From Demoralized (Thug): resisted again")

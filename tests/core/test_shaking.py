"""How a condition an impression or an Affliction put on comes off (``core.rules.shaking``).

The record is written on the condition itself (``AppliedCondition.shake_off``), so it
saves, travels to a player and back in their snapshot, and outlives the GM's app. A
condition carrying one offers no recovery check of its own: an Affliction's Stunned is
overcome with the Affliction (PDF p109), not rolled off with the damage-caused Stunned's
DC 10 Stamina check (p27). Headless.
"""

from __future__ import annotations

from dataclasses import replace

from mm_companion.core.character import (
    SHAKE_EFFECT,
    SHAKE_IMPRESSION,
    AppliedCondition,
    Character,
    ShakeOff,
)
from mm_companion.core.powers import ModifierSelection, Power, PowerEffectInstance
from mm_companion.core.rules import (
    CADENCE_CLEARS,
    CADENCE_MINUTE,
    CADENCE_SCENE,
    CADENCE_TURN,
    LADDER_EFFECT,
    Landing,
    RollSpec,
    ShakeFailure,
    SpecLadder,
    apply_condition,
    area_shake_off,
    can_recover,
    effect_spec_ladder,
    end_of_turn_recoveries,
    held_shake_offs,
    impress_resist_spec,
    impression_shake_off,
    ladder_shake_off,
    maintained_by,
    merge_landing,
    overcome_mods,
    power_rolls,
    reapplied_landing,
    reapply_request,
    reapply_roll,
    recover_choices,
    recovery_request,
    self_action,
    shake_off_cadence,
    shake_off_dc,
    shake_off_each_minute,
    shake_off_failed,
    shake_off_failed_on,
    shake_off_hint,
    shake_off_made,
    shake_off_maintained,
    shake_off_request,
    shake_off_roll,
    shake_off_stopped,
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


# -- the Affliction's modifiers (PDF p110-111) ---------------------------------------------


def _with(*mod_ids: str) -> PowerEffectInstance:
    return PowerEffectInstance(
        "affliction",
        rank=7,
        config=AFFLICTION,
        extras=[ModifierSelection(m) for m in mod_ids],
    )


def _held(data, *mod_ids: str, degree: int = 1, checks: int = 0) -> ShakeOff:
    ladder = effect_spec_ladder(_with(*mod_ids), data, name="Sleep Ray")
    shake = ladder_shake_off(ladder, 17, -degree)
    return replace(shake, checks=checks)


def test_the_ladder_carries_the_modifiers_that_change_overcoming(data) -> None:
    ladder = effect_spec_ladder(_with("progressive", "contagious_affliction"), data)
    assert ladder.mods == ("progressive",)  # Contagious changes nothing about it
    assert SpecLadder.from_dict(ladder.to_dict()) == ladder
    # Instant Recovery for an Instant Affliction, or a maintained one (it clears when the
    # wielder stops).
    assert overcome_mods(_with("instant_recovery"), None, data) == ()
    base = next(e for e in data.effects if e.id == "affliction")
    assert overcome_mods(_with("instant_recovery"), base, data) == ("instant_recovery",)
    sustained = _with("instant_recovery", "sustained_affliction")
    assert overcome_mods(sustained, base, data) == ("instant_recovery", "sustained_affliction")


def test_progressive_goes_a_degree_deeper_on_a_failed_check(data) -> None:
    shake = _held(data, "progressive")
    assert shake.rungs == (("dazed",), ("stunned",), ("incapacitated",))
    failure = shake_off_failed(data, shake)
    assert failure.worse == ("stunned",) and failure.record.degree == 2
    # And no further than the last rung.
    assert shake_off_failed(data, replace(shake, degree=3)).worse is None
    # Without it a failure changes nothing, and the record is not rewritten.
    plain = _held(data)
    assert shake_off_failed(data, plain) == ShakeFailure(plain)
    assert plain.rungs == ()


def test_increasing_and_decreasing_difficulty_move_the_dc(data) -> None:
    rising = _held(data, "progressive", "increasing_difficulty")
    assert shake_off_dc(data, rising) == 18  # the first overcome check is already +1
    after = shake_off_failed(data, rising).record
    assert after.checks == 1 and shake_off_dc(data, after) == 19
    falling = _held(data, "decreasing_difficulty")
    assert shake_off_dc(data, falling) == 17  # the first at the DC
    assert shake_off_dc(data, shake_off_failed(data, falling).record) == 16
    assert shake_off_roll(Character(), data, replace(rising, trait="resistance:WILL")).dc == 18


def test_fatal_adds_dying_to_a_failed_check_at_the_third_degree(data) -> None:
    assert shake_off_failed(data, _held(data, "fatal", degree=2)).also == ()
    assert shake_off_failed(data, _held(data, "fatal", degree=3)).also == ("dying",)
    assert "adds Dying" in shake_off_hint(data, _held(data, "fatal", degree=3))


def test_when_the_check_comes(data) -> None:
    assert shake_off_cadence(data, _held(data)) == CADENCE_TURN
    assert shake_off_cadence(data, _held(data, degree=3)) == CADENCE_MINUTE
    assert shake_off_cadence(data, _held(data, "prolonged")) == CADENCE_SCENE
    assert shake_off_cadence(data, _held(data, "instant_recovery")) == CADENCE_CLEARS
    # Instant Recovery at the third degree still waits for its minute.
    assert shake_off_cadence(data, _held(data, "instant_recovery", degree=3)) == CADENCE_MINUTE
    assert "end of each scene" in shake_off_hint(data, _held(data, "prolonged"))


def test_an_area_carries_the_modifiers_too(data) -> None:
    shake = area_shake_off(
        "affliction", AFFLICTION, data, 15, -1, name="Gas", mods=("progressive",)
    )
    assert shake.mods == ("progressive",) and shake.rungs[1] == ("stunned",)


# -- an impression's request card writes the record too ----------------------------------


def test_an_impression_request_cards_ladder_writes_an_impression_record(data) -> None:
    demoralized = data.impress.impression("demoralized")
    spec = impress_resist_spec(data, demoralized, "Intimidation", source="Thug")
    ladder = SpecLadder.from_dict(spec.to_dict()["ladder"])
    shake = ladder_shake_off(ladder, 21, -1, trait="resistance:WILL")
    assert shake == impression_shake_off(demoralized, "Intimidation", 21, source="Thug", degree=-1)


# -- Recover's new resistance check (PDF p206) ---------------------------------------------


def test_recover_offers_a_new_check_against_each_record(data) -> None:
    char = Character()
    char.resistances["WILL"] = 3
    sleep = _sleep(trait="resistance:WILL")
    apply_condition(char, "stunned", data, shake_off=sleep)
    recover = self_action(data, "recover")
    (choice,) = [c for c in recover_choices(char, data, recover) if c.spec is not None]
    assert choice.spec.shakes_off == sleep and choice.spec.dc == 17
    assert "Sleep Ray" in choice.caption
    assert RollSpec.from_dict(choice.spec.to_dict()).shakes_off == sleep


def test_a_made_check_takes_off_everything_its_record_put_on(data) -> None:
    char = Character()
    sleep = _sleep()
    for condition_id in ("stunned", "hindered"):
        apply_condition(char, condition_id, data, shake_off=sleep)
    apply_condition(char, "dazed", data)
    # Rolled before End turn counted a failure on it: still the same record's conditions.
    gone = shake_off_made(char, replace(sleep, checks=2))
    assert sorted(gone) == [("hindered", None), ("stunned", None)]
    assert [c.condition_id for c in char.conditions] == ["dazed"]
    assert shake_off_made(char, sleep) == []


def test_a_failed_recover_check_runs_the_modifiers_on_the_sheet(data) -> None:
    char = Character()
    shake = _held(data, "fatal", degree=3)
    apply_condition(char, "incapacitated", data, shake_off=shake)
    assert shake_off_failed_on(char, data, shake)
    ids = {c.condition_id for c in char.conditions if c.provenance is None}
    assert {"incapacitated", "dying"} <= ids
    # Progressive moves the conditions a degree deeper, with the record.
    other = Character()
    dazed = _held(data, "progressive", degree=1)
    apply_condition(other, "dazed", data, shake_off=dazed)
    assert shake_off_failed_on(other, data, dazed)
    (stunned,) = [c for c in other.conditions if c.provenance is None]
    assert stunned.condition_id == "stunned" and stunned.shake_off.degree == 2
    # Nothing carrying it: nothing to do.
    assert not shake_off_failed_on(Character(), data, dazed)


# -- Cumulative: a new use on a creature it is already on (p109-110) -----------------------


def _landed(data, *mod_ids: str, degree: int) -> tuple[Character, ShakeOff]:
    char = Character()
    shake = _held(data, *mod_ids, degree=degree)
    for condition_id in shake.rungs[degree - 1] if shake.rungs else ("dazed",):
        apply_condition(char, condition_id, data, shake_off=shake)
    return char, shake


def test_without_cumulative_only_the_highest_degree_applies(data) -> None:
    char, old = _landed(data, "progressive", degree=2)
    again = _held(data, "progressive", degree=1)
    assert merge_landing(char, data, [("dazed", None)], again) == Landing()
    deeper = _held(data, "progressive", degree=3)
    landing = merge_landing(char, data, [("incapacitated", None)], deeper)
    assert landing.remove == (("stunned", None),)
    assert landing.apply == (("incapacitated", None),) and landing.shake.degree == 3


def test_a_cumulative_one_adds_its_degrees(data) -> None:
    char, old = _landed(data, "cumulative", degree=1)
    again = _held(data, "cumulative", degree=1)
    landing = merge_landing(char, data, [("dazed", None)], again)
    assert landing.remove == (("dazed", None),)
    assert landing.apply == (("stunned", None),) and landing.shake.degree == 2
    # Up to the last rung, and no further.
    char3, _ = _landed(data, "cumulative", degree=3)
    assert merge_landing(char3, data, [("dazed", None)], again) == Landing()


def test_a_first_landing_or_another_power_lands_as_it_is(data) -> None:
    changes = [("dazed", None)]
    first = _held(data, "cumulative", degree=1)
    assert merge_landing(Character(), data, changes, first).apply == tuple(changes)
    char, _ = _landed(data, "cumulative", degree=1)
    other = replace(first, name="Other Ray")
    assert merge_landing(char, data, changes, other).remove == ()


# -- Concentration and Sustained: maintained (p110) ----------------------------------------


def test_a_maintained_one_is_resisted_anew_with_its_save(data) -> None:
    ladder = effect_spec_ladder(_with("sustained_affliction"), data, name="Sleep Ray")
    shake = ladder_shake_off(ladder, 17, -1, trait="resistance:FORTITUDE")
    assert shake_off_maintained(data, shake) and shake.save == "resistance:FORTITUDE"
    npc = Character()
    npc.resistances["FORTITUDE"] = 2
    spec = reapply_roll(npc, data, shake)
    assert spec.dc == 17 and "again" in spec.label
    asked = reapply_request(shake)
    assert asked.kind == "resistance" and asked.trait_key == "FORTITUDE" and asked.dc == 17
    # Failed by two: the second rung, as a new use (no deeper than what is on: nothing).
    char = Character()
    apply_condition(char, "dazed", data, shake_off=shake)
    landing = reapplied_landing(char, data, shake, -2)
    assert landing.apply == (("stunned", None),) and landing.remove == (("dazed", None),)
    assert reapplied_landing(char, data, shake, -1) == Landing()
    assert reapplied_landing(char, data, shake, 1) == Landing()


def test_whether_its_wielder_still_maintains_it(data) -> None:
    shake = _sleep()
    wielder = Character()
    assert maintained_by(None, shake) is None
    assert maintained_by(wielder, shake) is None  # no power by that name
    power = Power(name="Sleep Ray", effects=[_with("sustained_affliction")])
    wielder.powers.append(power)
    assert maintained_by(wielder, shake) is True
    power.effects[0].toggled_on = False
    assert maintained_by(wielder, shake) is False


def test_when_its_wielder_stops(data) -> None:
    ladder = effect_spec_ladder(
        _with("sustained_affliction", "instant_recovery"), data, name="Sleep Ray"
    )
    shake = ladder_shake_off(ladder, 17, -1)
    # Maintained, its Instant Recovery waits for the wielder to stop.
    assert shake_off_cadence(data, shake) == CADENCE_TURN
    clears, after = shake_off_stopped(data, shake)
    assert clears and after.mods == ()
    plain = ladder_shake_off(
        effect_spec_ladder(_with("sustained_affliction"), data, name="Sleep Ray"), 17, -1
    )
    clears, after = shake_off_stopped(data, plain)
    assert not clears and not shake_off_maintained(data, after)

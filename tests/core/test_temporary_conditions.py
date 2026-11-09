"""Temporary conditions and conditions with an amount (combat maneuvers, phase 1).

A maneuver's lasting part — All-Out Attack's −X Defense — is a condition applied with an
*amount*, marked *temporary* so "Clear temporary" can take it off at the start of the
bearer's next turn. Headless: everything here is ``core``.
"""

from __future__ import annotations

import pytest

from mm_companion.core.character import AppliedCondition, Character
from mm_companion.core.components import MECH_AMOUNT_MOD, TONE_BENEFIT, TONE_HINDRANCE, TONES
from mm_companion.core.data_loader import AmountMod, Condition
from mm_companion.core.powers import ModifierSelection, Power, PowerEffectInstance
from mm_companion.core.rules import (
    MARK_BETTER,
    MARK_WORSE,
    applied_condition_name,
    apply_condition,
    clear_temporary,
    clear_temporary_note,
    condition_amount_mods,
    condition_asks_amount,
    condition_attack_check_mod,
    defense_class,
    defense_class_in_play,
    has_temporary,
    power_rolls,
    resistance_condition_effect,
    temporary_marks,
)

NEW_RECORDS = (
    "all_out_attack",
    "defensive_attack",
    "defending",
    "recovering",
    "aided_attack",
    "aided_defense",
    "blocking",
    "trick_advantage",
)


@pytest.fixture
def hero(data) -> Character:
    char = Character.new_default(data)
    char.abilities.update({"STR": 4, "STA": 5, "AGL": 3, "AWE": 2, "ATK": 7})
    char.resistances["DEF"] = 8
    return char


@pytest.fixture
def scoped(data, monkeypatch) -> str:
    """A lasting record whose amount moves the attack check or Defense by its parameter.

    No shipped record scopes its amount so any more — Aid's bonus became the opposing
    ``aided_attack`` / ``aided_defense`` (phase 8) — but the engine reads it, and a mod's
    record may.
    """
    record = Condition(
        name="Scoped",
        id="scoped",
        mechanisms=(MECH_AMOUNT_MOD,),
        tone=TONE_BENEFIT,
        amount_mods=(
            AmountMod(data.system.trait_keys.attack_check, 1, "Attack"),
            AmountMod("DEF", 1, "Defense"),
        ),
        default_amount=2,
    )
    monkeypatch.setitem(data.condition_catalog(), "scoped", record)
    return "scoped"


def _name(char: Character, data, index: int = 0) -> str:
    applied = char.conditions[index]
    return applied_condition_name(applied, data.condition_catalog()[applied.condition_id])


# -- the model ---------------------------------------------------------------------------


def test_amount_and_temporary_round_trip_and_stay_out_of_old_saves() -> None:
    plain = AppliedCondition("prone")
    assert plain.to_dict() == {"id": "prone"}
    assert AppliedCondition.from_dict({"id": "prone"}) == plain

    marked = AppliedCondition("all_out_attack", amount=3, temporary=True)
    assert marked.to_dict() == {"id": "all_out_attack", "amount": 3, "temporary": True}
    assert AppliedCondition.from_dict(marked.to_dict()) == marked


def test_a_character_save_and_an_undo_restore_carry_them(hero, data) -> None:
    apply_condition(hero, "all_out_attack", data, amount=4)
    saved = hero.to_dict()
    assert Character.from_dict(saved).conditions == hero.conditions

    # Undo is a snapshot restore: the same path, in place.
    clear_temporary(hero)
    assert hero.conditions == []
    hero.restore(saved)
    assert hero.conditions == [AppliedCondition("all_out_attack", amount=4, temporary=True)]


# -- the data ----------------------------------------------------------------------------


def test_every_existing_condition_is_still_a_hindrance(data) -> None:
    catalog = data.condition_catalog()
    for cid, cond in catalog.items():
        assert cond.tone in TONES, cid
        if cid not in NEW_RECORDS:
            assert cond.tone == TONE_HINDRANCE, cid
            assert cond.amount_mods == () and not cond.temporary, cid


def test_the_new_records_are_offered_under_the_maneuver_group(data) -> None:
    catalog = data.condition_catalog()
    groups = {g.group for g in data.condition_groups}
    for cid in NEW_RECORDS:
        assert cid in catalog, cid
        assert catalog[cid].group == "maneuver" and "maneuver" in groups
    assert catalog["all_out_attack"].tone == TONE_HINDRANCE
    assert catalog["defensive_attack"].tone == TONE_BENEFIT
    assert catalog["defending"].default_amount == 2
    assert catalog["blocking"].stacking


def test_every_amount_mod_names_a_trait_the_engine_reads(data) -> None:
    known = {r.key for r in data.resistances} | {data.system.trait_keys.attack_check}
    for cid, cond in data.condition_catalog().items():
        for mod in cond.amount_mods:
            assert mod.trait in known, (cid, mod.trait)
            assert mod.sign in (1, -1), cid


# -- applying ----------------------------------------------------------------------------


def test_a_temporary_record_is_temporary_from_wherever_it_is_applied(hero, data) -> None:
    apply_condition(hero, "all_out_attack", data, amount=3)
    assert hero.conditions[0].temporary
    assert _name(hero, data) == "All-Out Attack -3"


def test_a_fixed_amount_comes_from_the_record(hero, data) -> None:
    apply_condition(hero, "defending", data)
    assert hero.conditions[0].amount == 2
    assert _name(hero, data) == "Defending +2"


def test_reapplying_replaces_the_amount_rather_than_stacking(hero, data) -> None:
    apply_condition(hero, "all_out_attack", data, amount=2)
    apply_condition(hero, "all_out_attack", data, amount=5)
    assert len(hero.conditions) == 1
    assert hero.conditions[0].amount == 5
    assert condition_amount_mods(hero, data) == {"DEF": -5}


def test_an_existing_condition_can_be_applied_temporarily(hero, data) -> None:
    apply_condition(hero, "vulnerable", data, temporary=True)
    assert hero.conditions[0].temporary
    # Without an amount it reads as it always did.
    assert _name(hero, data) == "Vulnerable"


def test_a_lasting_instance_is_not_shortened_by_a_temporary_one(hero, data) -> None:
    apply_condition(hero, "vulnerable", data)
    apply_condition(hero, "vulnerable", data, temporary=True)
    assert not hero.conditions[0].temporary
    assert clear_temporary(hero) == []
    assert [c.condition_id for c in hero.conditions] == ["vulnerable"]


def test_a_temporary_umbrella_marks_its_members_too(hero, data) -> None:
    apply_condition(hero, "staggered", data, temporary=True)
    assert len(hero.conditions) > 1
    assert all(c.temporary for c in hero.conditions)


# -- what they do ------------------------------------------------------------------------


def test_amounts_sum_per_trait_and_opposites_cancel(hero, data) -> None:
    apply_condition(hero, "all_out_attack", data, amount=3)
    apply_condition(hero, "defending", data)
    assert condition_amount_mods(hero, data) == {"DEF": -1}
    apply_condition(hero, "defensive_attack", data, amount=1)
    attack_key = data.system.trait_keys.attack_check
    # −3 + 2 + 1 nets nothing on Defense: the key is left out.
    assert condition_amount_mods(hero, data) == {attack_key: -1}


def test_a_parameter_scoped_amount_counts_only_for_its_parameter(hero, data, scoped) -> None:
    apply_condition(hero, scoped, data, parameter="Defense")
    assert condition_amount_mods(hero, data) == {"DEF": 2}
    hero.conditions.clear()
    apply_condition(hero, scoped, data, parameter="Attack", amount=5)
    assert condition_amount_mods(hero, data) == {data.system.trait_keys.attack_check: 5}
    assert _name(hero, data) == "Scoped (Attack) +5"


def test_defense_shows_the_amount_but_the_build_stays_condition_free(hero, data) -> None:
    built = defense_class(hero, data)
    apply_condition(hero, "all_out_attack", data, amount=3)
    effect = resistance_condition_effect(hero, data, "DEF")
    assert effect.apply(8) == 5
    assert "-3 All-Out Attack" in effect.tooltip
    assert defense_class_in_play(hero, data) == built - 3
    assert defense_class(hero, data) == built
    # Dodge is not Defense.
    assert not resistance_condition_effect(hero, data, "DODGE").active


def test_halving_comes_before_the_amount(hero, data) -> None:
    apply_condition(hero, "vulnerable", data)
    apply_condition(hero, "defending", data)
    assert resistance_condition_effect(hero, data, "DEF").apply(8) == 6  # 8 // 2 + 2


# -- the attack check --------------------------------------------------------------------


def _blast(rank: int = 8) -> Power:
    return Power(name="Blast", effects=[PowerEffectInstance("damage", rank=rank)])


def test_an_attack_check_mod_reaches_the_attack_roll_and_not_the_save(hero, data) -> None:
    attack, save = power_rolls(_blast(), hero, data)
    assert attack.modifier == 7
    apply_condition(hero, "defensive_attack", data, amount=2)
    assert condition_attack_check_mod(hero, data)[0] == -2
    shifted, same_save = power_rolls(_blast(), hero, data)
    assert shifted.modifier == 5
    assert shifted.label == "Attack +5 vs. Defense"
    assert "Defensive Attack" in shifted.hint
    assert same_save.dc == save.dc


def test_a_check_that_is_not_an_attack_is_left_alone(hero, data) -> None:
    """A maneuver can't touch a value the attack doesn't use (p201)."""
    affliction = PowerEffectInstance(
        "affliction",
        rank=8,
        config={"resistance": "Will", "degree1": "dazed", "degree2": "stunned"},
        extras=[ModifierSelection("perception_range")],
    )
    power = Power(name="Stare", effects=[affliction])
    before = [s.modifier for s in power_rolls(power, hero, data)]
    apply_condition(hero, "defensive_attack", data, amount=3)
    assert [s.modifier for s in power_rolls(power, hero, data)] == before


def test_a_linked_attack_takes_the_mod_once(hero, data) -> None:
    damage = PowerEffectInstance("damage", rank=8)
    affliction = PowerEffectInstance(
        "affliction",
        rank=8,
        config={"resistance": "Fortitude", "degree1": "dazed", "degree2": "stunned"},
    )
    power = Power(name="Thunderbolt", structure="linked", effects=[damage, affliction])
    before = power_rolls(power, hero, data)[0]
    apply_condition(hero, "defensive_attack", data, amount=2)
    after = power_rolls(power, hero, data)[0]
    assert after.modifier == before.modifier - 2
    assert f"Attack {after.modifier:+d} vs." in after.label


# -- marks and clearing ------------------------------------------------------------------


def test_marks_follow_each_stats_own_sign(hero, data) -> None:
    apply_condition(hero, "defensive_attack", data, amount=2)
    marks = temporary_marks(hero, data)
    attack_key = data.system.trait_keys.attack_check
    assert marks["DEF"].tone == MARK_BETTER and marks["DEF"].delta == 2
    assert marks[attack_key].tone == MARK_WORSE
    assert marks["DEF"].sources == ("Defensive Attack +2",)


def test_a_lasting_condition_leaves_no_mark(hero, data, scoped) -> None:
    apply_condition(hero, scoped, data, parameter="Defense")
    assert not hero.conditions[0].temporary
    assert temporary_marks(hero, data) == {}


def test_a_temporary_halving_marks_both_active_defenses_worse(hero, data) -> None:
    apply_condition(hero, "vulnerable", data, temporary=True)
    marks = temporary_marks(hero, data)
    assert marks["DEF"].tone == MARK_WORSE and marks["DODGE"].tone == MARK_WORSE


def test_a_net_zero_is_not_marked(hero, data) -> None:
    apply_condition(hero, "all_out_attack", data, amount=2)
    apply_condition(hero, "defending", data)
    assert "DEF" not in temporary_marks(hero, data)


def test_clear_temporary_takes_only_the_temporary_ones(hero, data) -> None:
    apply_condition(hero, "prone", data)
    apply_condition(hero, "all_out_attack", data, amount=3)
    apply_condition(hero, "staggered", data, temporary=True)
    assert has_temporary(hero)
    removed = clear_temporary(hero)
    assert removed[0].condition_id == "all_out_attack"
    assert {c.condition_id for c in removed} >= {"staggered", "dazed", "hindered"}
    assert [c.condition_id for c in hero.conditions] == ["prone"]
    assert not has_temporary(hero)
    assert clear_temporary(hero) == []


# -- phase 2: what the sheet reads ---------------------------------------------------------


def test_a_raise_only_overlay_reads_better_and_anything_else_worse(hero, data) -> None:
    apply_condition(hero, "defending", data)
    assert resistance_condition_effect(hero, data, "DEF").better
    apply_condition(hero, "vulnerable", data)
    assert not resistance_condition_effect(hero, data, "DEF").better  # halving wins


def test_a_penalty_overlay_is_not_better(hero, data) -> None:
    apply_condition(hero, "all_out_attack", data, amount=2)
    assert not resistance_condition_effect(hero, data, "DEF").better


def test_an_attack_check_names_the_mark_it_folds_in(hero, data) -> None:
    attack, save = power_rolls(_blast(), hero, data)
    assert attack.marks == (data.system.trait_keys.attack_check,)
    assert save.marks == ()


def test_a_check_with_no_attack_carries_no_mark(hero, data) -> None:
    affliction = PowerEffectInstance(
        "affliction",
        rank=8,
        config={"resistance": "Will", "degree1": "dazed", "degree2": "stunned"},
        extras=[ModifierSelection("perception_range")],
    )
    power = Power(name="Stare", effects=[affliction])
    assert all(not spec.marks for spec in power_rolls(power, hero, data))


def test_marks_stay_off_the_wire_and_out_of_equality(hero, data) -> None:
    from mm_companion.core.rules import RollSpec

    attack, _save = power_rolls(_blast(), hero, data)
    assert "marks" not in attack.to_dict()
    assert RollSpec.from_dict(attack.to_dict()) == attack


def test_only_a_condition_with_no_default_asks_for_an_amount(data) -> None:
    catalog = data.condition_catalog()
    assert condition_asks_amount(catalog["all_out_attack"])
    assert condition_asks_amount(catalog["defensive_attack"])
    assert not condition_asks_amount(catalog["defending"])  # +2 by default
    assert not condition_asks_amount(catalog["prone"])
    assert catalog["all_out_attack"].max_amount == 5
    assert catalog["defending"].max_amount is None


def test_the_clear_note_names_only_the_directly_applied_ones(hero, data) -> None:
    apply_condition(hero, "all_out_attack", data, amount=3)
    apply_condition(hero, "incapacitated", data, temporary=True)  # bundles members
    removed = clear_temporary(hero)
    note = clear_temporary_note(removed, data)
    assert note.startswith("temporary conditions cleared: ")
    assert "All-Out Attack -3" in note and "Incapacitated" in note
    assert "Defenseless" not in note  # a member, gone with its umbrella
    assert clear_temporary_note([], data) == ""

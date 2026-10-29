"""What an attacking power rolls: its attack, the save it forces, and what each lands.

The powers audit's rules, each against the book page it comes from — a modifier giving an
effect a save (Resistible, the Attack extra), a Linked power's single attack check, an
effect check that states its DC, Mind Reading's opposed save, Nullify's opposed check off
the hit, and the few data fixes behind them. Headless: no Qt in this file.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from mm_companion.core.character import Character
from mm_companion.core.powers import ModifierSelection, Power, PowerEffectInstance
from mm_companion.core.rules import (
    KIND_POWER_SAVE,
    RollSpec,
    effect_resistance_dc_base,
    effect_stat_rows,
    follow_ups_for_result,
    linked_attacks,
    localize_spec,
    power_pl_violations,
    power_rolls,
    power_violations,
    resistance_total,
    structure_header,
)
from mm_companion.core.session.protocol import sanitize_spec


@pytest.fixture
def hero(data) -> Character:
    char = Character.new_default(data)
    char.abilities.update({"STR": 4, "STA": 5, "AGL": 3, "AWE": 2, "ATK": 7})
    return char


def _effect(data, effect_id, rank=8, mods=(), config=None) -> PowerEffectInstance:
    effect = PowerEffectInstance(effect_id, rank=rank, config=dict(config or {}))
    catalog = data.modifier_catalog()
    for modifier_id, cfg in mods:
        selection = ModifierSelection(modifier_id, config=dict(cfg))
        if catalog[modifier_id].category == "extra":
            effect.extras.append(selection)
        else:
            effect.flaws.append(selection)
    return effect


def _labels(specs: list[RollSpec]) -> list[str]:
    return [spec.label for spec in specs]


# -- a modifier can give an effect its save --------------------------------------------


def test_the_attack_extra_with_a_resistance_makes_a_teleport_attack(data, hero) -> None:
    """Attack vs. Defense, and a Will save against the effect DC for whoever it hits."""
    effect = _effect(data, "teleport", mods=[("attack", {"resistance": "Will"})])
    attack, save = power_rolls(Power(name="Banish", effects=[effect]), hero, data)
    assert attack.label == "Attack +7 vs. Defense"
    assert attack.follow_up is not None and attack.follow_up.dc == 18
    assert save.label == "Will vs. DC 18"
    assert save.trait_key == "WILL" and save.rolled_by_target
    assert effect_resistance_dc_base(effect, data) == 10


def test_the_attack_extra_adds_no_save_until_one_is_picked(data, hero) -> None:
    effect = _effect(data, "teleport", mods=[("attack", {})])
    (attack,) = power_rolls(Power(name="Shove", effects=[effect]), hero, data)
    assert attack.follow_up is None
    assert effect_resistance_dc_base(effect, data) is None


def test_the_attack_extras_picker_stands_down_on_an_effect_with_its_own_save(data) -> None:
    """An Illusion is already resisted; the Attack extra does not replace that."""
    effect = _effect(data, "illusion", mods=[("attack", {"resistance": "Toughness"})])
    rows = {row.key: row.value for row in effect_stat_rows(effect, data)}
    assert rows["resistance"].startswith("Insight or Perception")


def test_resistible_shows_its_save_at_once_and_warns_until_one_is_chosen(data, hero) -> None:
    effect = _effect(data, "flight", mods=[("resistible", {})])
    power = Power(name="Wings", effects=[effect])
    (save,) = power_rolls(power, hero, data)
    assert save.label == "Resistance (not chosen yet) vs. DC 18"
    assert any("Resistible needs Resisted by" in m for m in power_violations(power, hero, data))

    effect.flaws[0].config["resistance"] = "Fortitude"
    (save,) = power_rolls(power, hero, data)
    assert save.label == "Fortitude vs. DC 18" and save.trait_key == "FORTITUDE"
    assert not any("Resistible" in m for m in power_violations(power, hero, data))


def test_fortune_controls_resistible_is_against_the_wielders_will(data, hero) -> None:
    """p127: a DC of 10 + your Will rank, not the effect's rank."""
    effect = _effect(data, "fortune_control", rank=3, mods=[("resistible_fortune", {})])
    hero.resistances["WILL"] = 6
    (save,) = power_rolls(Power(name="Luck", effects=[effect]), hero, data)
    will = resistance_total(hero, data, "WILL")
    assert will != 3  # the effect's rank, which this DC must not be built from
    assert save.dc == 10 + will


def test_a_granted_save_is_held_to_power_level(data, hero) -> None:
    """A Teleport attack resisted by Will is an attack, and capped as one."""
    hero.power_level = 6
    effect = _effect(data, "teleport", rank=10, mods=[("attack", {"resistance": "Will"})])
    assert power_pl_violations(Power(name="Banish", effects=[effect]), hero, data)


# -- an Affliction left unfinished -------------------------------------------------------


def test_an_affliction_with_nothing_chosen_says_so(data, hero) -> None:
    power = Power(name="Stun", effects=[_effect(data, "affliction")])
    messages = power_violations(power, hero, data)
    assert any("choose Resisted by" in m and "3rd degree" in m for m in messages)
    save = power_rolls(power, hero, data)[1]
    assert save.label.startswith("Resistance (not chosen yet)")


def test_a_finished_affliction_raises_no_unfinished_warning(data, hero) -> None:
    config = {
        "resistance": "Fortitude",
        "degree1": "dazed",
        "degree2": "stunned",
        "degree3": "incapacitated",
    }
    power = Power(name="Stun", effects=[_effect(data, "affliction", config=config)])
    assert not any("choose" in m for m in power_violations(power, hero, data))


# -- Alternate Defense -------------------------------------------------------------------


def test_alternate_defense_names_what_the_attack_is_made_against(data, hero) -> None:
    """p149: the attack check is made against that resistance + 10 instead of Defense."""
    effect = _effect(
        data, "damage", mods=[("alternate_defense", {"defense": "Will", "against": "easier"})]
    )
    attack = power_rolls(Power(name="Mind Spike", effects=[effect]), hero, data)[0]
    assert attack.label == "Attack +7 vs. Will + 10"
    assert "Will + 10" in attack.hint


def test_alternate_defense_best_of_two_reads_as_a_choice(data) -> None:
    effect = _effect(
        data,
        "damage",
        mods=[
            (
                "alternate_defense",
                {"defense": "Will", "defenseSecond": "Defense", "against": "best"},
            )
        ],
    )
    rows = {row.key: row.value for row in effect_stat_rows(effect, data)}
    assert rows["check"].endswith("vs. Will or Defense + 10")


# -- a Linked power's one attack ---------------------------------------------------------


def _linked(data) -> Power:
    damage = _effect(data, "damage", mods=[("accurate", {})])
    damage.extras[0].rank = 2
    affliction = _effect(
        data,
        "affliction",
        config={"resistance": "Fortitude", "degree1": "dazed", "degree2": "stunned"},
    )
    affliction.config["degree3"] = "incapacitated"
    return Power(name="Thunderbolt", structure="linked", effects=[damage, affliction])


def test_a_linked_power_rolls_one_attack_that_lands_every_save(data, hero) -> None:
    """p155: linked effects use a single attack check; each is resisted separately."""
    specs = power_rolls(_linked(data), hero, data)
    assert _labels(specs) == [
        "Damage + Affliction: Attack +11 vs. Defense",
        "Damage: Toughness vs. 18",
        "Affliction: Fortitude vs. DC 18",
    ]
    attack = specs[0]
    assert [s.label for s in attack.follow_ups()] == _labels(specs[1:])


def test_the_linked_attack_uses_the_rulesets_pick_of_the_bonuses(data, hero) -> None:
    (attack,) = linked_attacks(_linked(data), data, hero)
    assert attack.bonuses == (11, 7)
    assert attack.bonus == 11  # system.linkedAttackBonus: "first" — the Accurate Damage
    reordered = _linked(data)
    reordered.effects.reverse()
    assert linked_attacks(reordered, data, hero)[0].bonus == 7  # now the Affliction leads
    highest = replace(data, system=replace(data.system, linked_attack_bonus="highest"))
    assert linked_attacks(reordered, highest, hero)[0].bonus == 11
    lowest = replace(data, system=replace(data.system, linked_attack_bonus="lowest"))
    assert linked_attacks(_linked(data), lowest, hero)[0].bonus == 7
    assert any(
        "roll once at +11, the first effect's (Damage +11, Affliction +7)" in m
        for m in power_violations(_linked(data), hero, data)
    )


def test_each_linked_effect_is_capped_on_its_own_at_the_shared_bonus(data, hero) -> None:
    """Attack plus rank per effect, never summed — and the attack is the one rolled.

    Damage +11 rank 6 (17) and Affliction rank 8: alone at +7 that is 15, but it rolls
    at the lead Damage's +11, so 19 — over PL 9's 18. Put it first and nothing breaches.
    """
    hero.power_level = 9
    power = _linked(data)
    power.effects[0].rank = 6
    (breach,) = power_pl_violations(power, hero, data)
    assert breach.startswith("Affliction: attack +11 plus rank 8 = 19")
    power.effects.reverse()
    assert power_pl_violations(power, hero, data) == []


def test_a_critical_on_the_linked_attack_raises_every_save(data, hero) -> None:
    attack = power_rolls(_linked(data), hero, data)[0]
    landed = follow_ups_for_result(attack, 20, 1, data)
    assert [spec.dc for spec in landed] == [23, 23]
    assert follow_ups_for_result(attack, 5, -1, data) == []


def test_several_follow_ups_survive_the_wire(data, hero) -> None:
    attack = power_rolls(_linked(data), hero, data)[0]
    wired = RollSpec.from_dict(sanitize_spec(attack.to_dict()))
    assert wired is not None
    assert [s.label for s in wired.follow_ups()] == [s.label for s in attack.follow_ups()]


def test_the_linked_header_names_the_slowest_action(data) -> None:
    """p155: the Action of linked effects is the longest of its components."""
    power = Power(
        name="Bolt",
        structure="linked",
        effects=[
            _effect(data, "damage"),
            _effect(data, "damage", mods=[("increased_action", {})]),
        ],
    )
    assert "as a move action" not in structure_header(power, data)
    assert structure_header(power, data).startswith("Linked (all effects activate together, as a")


def test_an_effect_linked_to_itself_warns_without_refusing(data, hero) -> None:
    power = Power(
        name="Double",
        structure="linked",
        effects=[_effect(data, "damage"), _effect(data, "damage")],
    )
    assert any("appears 2 times" in m for m in power_violations(power, hero, data))


# -- effect checks, opposed saves, and fill-ins -----------------------------------------


def test_healing_carries_the_dc_its_check_states(data, hero) -> None:
    (check,) = power_rolls(Power(name="Mend", effects=[_effect(data, "healing")]), hero, data)
    assert check.label == "Effect check +8 vs. DC 10"
    assert check.dc == 10 and check.modifier == 8


def test_perception_range_never_drops_an_effects_own_check(data, hero) -> None:
    effect = _effect(data, "healing", mods=[("perception_range", {})])
    (check,) = power_rolls(Power(name="Mend", effects=[effect]), hero, data)
    assert check.dc == 10


def test_perception_range_still_drops_an_attack_roll(data, hero) -> None:
    effect = _effect(data, "damage", mods=[("perception_range", {})])
    (save,) = power_rolls(Power(name="Glare", effects=[effect]), hero, data)
    assert save.kind == KIND_POWER_SAVE


def test_mind_readings_first_save_is_against_the_check_result(data, hero) -> None:
    """p133: Will vs. the effect check result; later checks are against the effect DC."""
    check, listed = power_rolls(
        Power(name="Read", effects=[_effect(data, "mind_reading")]), hero, data
    )
    assert check.follow_up is not None and check.follow_up.dc_from_total
    landed = follow_ups_for_result(check, 12, None, data, total=20)
    assert landed[0].dc == 20
    assert listed.dc == 18


def test_nullifys_hit_provokes_its_opposed_check(data, hero) -> None:
    attack, opposed = power_rolls(Power(name="Off", effects=[_effect(data, "nullify")]), hero, data)
    assert attack.follow_up is not None
    assert attack.follow_up.label == opposed.label


def test_move_object_is_resisted_by_strength_at_the_effect_dc(data, hero) -> None:
    """p134: Strength vs. Effect DC, which is 10 + rank (p105)."""
    attack, save = power_rolls(Power(name="TK", effects=[_effect(data, "move_object")]), hero, data)
    assert save.dc == 18 and save.trait_key == "STR"
    target = Character.new_default(data)
    target.abilities["STR"] = 6
    assert localize_spec(save, target, data).modifier == 6


def test_a_capped_effect_check_rolls_the_rank_the_dc_is_built_from(data, hero) -> None:
    """A biting hard cap lowers the check as well as the DC beside it."""
    hero.power_level = 5
    effect = _effect(data, "mind_reading", rank=8)
    effect.pl_cap = "attack"
    check, save = power_rolls(Power(name="Read", effects=[effect]), hero, data)
    assert check.modifier == 5
    assert save.dc == 15

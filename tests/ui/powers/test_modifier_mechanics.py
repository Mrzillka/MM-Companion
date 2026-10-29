"""The modifiers the rules audit of 2026-10-17 turned from notes into numbers.

Headless. Each test is one rule the book states and the app used to only *say*:
Impervious and its kin on a Damage save, the checks Unreliable and Leaping's Acrobatics
flaw call for, what Tiring and Distracting cost the wielder, the uses Short-Term and
Fades count — and the data fixes beside them (a ranked Quirk, a Diminished Range that
shortens, the per-location extras that are bought in ranks).
"""

from __future__ import annotations

from mm_companion.core.character import Character
from mm_companion.core.powers import ModifierSelection, Power, PowerEffectInstance
from mm_companion.core.rules import (
    CATEGORY_DAMAGE,
    RollSpec,
    SpecLadder,
    answer_rung,
    apply_use_costs,
    damage_negated,
    effect_current_rank,
    effect_is_spent,
    effect_stat_rows,
    effect_total_cost,
    localize_spec,
    power_modifier_requirement_violations,
    power_rolls,
    toughness_defense,
)


def _protected(data, rank: int, *extras: str, band: tuple[int, int] = (0, 0)) -> Character:
    """A character whose Toughness carries a Protection *rank* with *extras* on it."""

    char = Character.new_default(data)
    effect = PowerEffectInstance("protection", rank=rank)
    for extra in extras:
        effect.extras.append(ModifierSelection(extra, applies_from=band[0], applies_to=band[1]))
    char.powers.append(Power(name="Armour", effects=[effect]))
    return char


def _blast(rank: int, *extras: str) -> PowerEffectInstance:
    effect = PowerEffectInstance("damage", rank=rank)
    effect.extras.append(ModifierSelection("ranged", config={"from": "close"}))
    effect.extras.extend(ModifierSelection(extra) for extra in extras)
    return effect


def _save(power: Power, char, data) -> RollSpec:
    return next(s for s in power_rolls(power, char, data) if s.ladder is not None)


# -- Impervious, Hardened, Impenetrable, Penetrating (p152, p154, p116) -------------------


def test_a_toughness_defence_covers_the_ranks_it_is_bought_on(data) -> None:
    assert toughness_defense(_protected(data, 10, "impervious"), data).immune == 10
    # Impervious on only the top four ranks of the Protection covers four.
    banded = _protected(data, 10, "impervious", band=(7, 10))
    assert toughness_defense(banded, data).immune == 4
    assert toughness_defense(Character.new_default(data), data).any is False


def test_impervious_ignores_a_damage_rank_it_is_not_exceeded_by(data) -> None:
    defense = toughness_defense(_protected(data, 10, "impervious"), data)
    assert damage_negated(defense, rank=8, degree=-3)  # however badly the save went
    assert not damage_negated(defense, rank=12, degree=-1)


def test_penetrating_cuts_through_impervious_but_not_impenetrable(data) -> None:
    impervious = toughness_defense(_protected(data, 10, "impervious"), data)
    assert not damage_negated(impervious, rank=8, penetrating=2, degree=-1)
    impenetrable = toughness_defense(_protected(data, 10, "impenetrable"), data)
    assert damage_negated(impenetrable, rank=8, penetrating=2, degree=-1)


def test_two_degrees_against_a_hardened_toughness_leave_no_hit(data) -> None:
    hardened = toughness_defense(_protected(data, 4, "hardened"), data)
    assert damage_negated(hardened, rank=12, degree=2)
    assert not damage_negated(hardened, rank=12, degree=1)  # one degree: still a Hit
    assert not damage_negated(hardened, rank=12, penetrating=12, degree=2)


def test_the_save_carries_the_attacks_rank_and_penetration(data) -> None:
    attacker = Character.new_default(data)
    spec = _save(Power(name="Blast", effects=[_blast(8, "penetrating")]), attacker, data)
    assert spec.ladder.kind == CATEGORY_DAMAGE
    assert (spec.ladder.rank, spec.ladder.penetrating) == (8, 8)
    assert SpecLadder.from_dict(spec.ladder.to_dict()) == spec.ladder


def test_answering_an_impervious_npcs_save_puts_nothing_on_it(data) -> None:
    attacker = Character.new_default(data)
    ladder = _save(Power(name="Blast", effects=[_blast(8)]), attacker, data).ladder
    npc = _protected(data, 10, "impervious")
    assert answer_rung(ladder, -2, npc, data) is None
    # Without the creature, the ladder reads as it always did.
    assert answer_rung(ladder, -2).conditions == ("hit", "stunned", "staggered")


def test_localizing_a_save_says_what_the_defences_do(data) -> None:
    attacker = Character.new_default(data)
    spec = replace_trait(_save(Power(name="Blast", effects=[_blast(8)]), attacker, data))
    immune = localize_spec(spec, _protected(data, 10, "impervious"), data)
    assert "unaffected: Impervious 10" in immune.label
    hardened = localize_spec(spec, _protected(data, 10, "hardened"), data)
    assert "roll twice" in hardened.label
    pierced = replace_trait(
        _save(Power(name="Blast", effects=[_blast(8, "penetrating")]), attacker, data)
    )
    # Against Impervious the target resists the Penetrating rank instead: here all 8.
    assert localize_spec(pierced, _protected(data, 10, "impervious"), data).dc == pierced.dc
    # Only two of the eight ranks Penetrating: the save is against rank 2, six lower.
    partly = _blast(8)
    partly.extras.append(ModifierSelection("penetrating", applies_from=1, applies_to=2))
    two = replace_trait(_save(Power(name="Blast", effects=[partly]), attacker, data))
    assert localize_spec(two, _protected(data, 10, "impervious"), data).dc == two.dc - 6


def replace_trait(spec: RollSpec) -> RollSpec:
    """A save as it arrives at the target's sheet: naming its trait, at modifier 0."""

    from dataclasses import replace

    return replace(spec, trait_key=spec.trait_key or "TOUGHNESS")


# -- rolls the rules fix: Unreliable, Leaping's Acrobatics, Check Required ---------------


def test_unreliable_rolls_its_reliability_die(data) -> None:
    effect = _blast(6)
    effect.flaws.append(ModifierSelection("unreliable"))
    specs = power_rolls(Power(name="Blast", effects=[effect]), Character.new_default(data), data)
    reliability = specs[0]
    assert reliability.dc == 11 and reliability.modifier == 0
    assert "Reliability" in reliability.label


def test_check_required_rolls_the_wielders_number(data) -> None:
    char = Character.new_default(data)
    char.abilities["AGL"] = 4
    effect = _blast(6)
    effect.flaws.append(ModifierSelection("check_required", rank=2, config={"trait": "AGL"}))
    check = power_rolls(Power(name="Spell", effects=[effect]), char, data)[0]
    assert (check.modifier, check.dc) == (4, 12)


def test_leapings_acrobatics_flaw_is_a_dc_10_acrobatics_check(data) -> None:
    effect = PowerEffectInstance("leaping", rank=6)
    effect.flaws.append(ModifierSelection("acrobatics_check_required"))
    (check,) = power_rolls(Power(name="Leap", effects=[effect]), Character.new_default(data), data)
    assert check.dc == 10 and check.label.startswith("Acrobatics check")


# -- what using the effect costs: Tiring, Distracting --------------------------------------


def test_the_use_roll_carries_what_using_the_effect_costs(data) -> None:
    effect = _blast(6)
    effect.flaws.append(ModifierSelection("tiring"))
    effect.flaws.append(ModifierSelection("distracting"))
    attack, save = power_rolls(Power(name="Blast", effects=[effect]), None, data)
    assert attack.costs == ("fatigue-ladder", "vulnerable")
    assert save.costs == ()  # the target's roll costs the wielder nothing
    assert "costs" not in attack.to_dict()  # a reroll off the history is no new use


def test_tiring_climbs_the_fatigue_ladder_extra_effort_climbs(data) -> None:
    char = Character.new_default(data)
    assert apply_use_costs(char, ("fatigue-ladder",), data) == ["fatigued"]
    assert apply_use_costs(char, ("fatigue-ladder",), data) == ["exhausted"]


# -- uses: Short-Term, Fades ---------------------------------------------------------------


def test_fades_loses_a_rank_a_use_and_stops_at_nothing(data) -> None:
    effect = _blast(3)
    effect.flaws.append(ModifierSelection("fades"))
    power = Power(name="Blast", effects=[effect])
    attack, _save = power_rolls(power, None, data)
    assert attack.spends == ((power.id, 0),)
    effect.uses = 2
    assert effect_current_rank(effect, data) == 1
    assert not effect_is_spent(effect, data)
    effect.uses = 3
    assert effect_is_spent(effect, data)
    assert "spent" in {r.key: r.value for r in effect_stat_rows(effect, data)}["uses"]


def test_short_term_stops_after_six_uses(data) -> None:
    effect = _blast(6)
    effect.flaws.append(ModifierSelection("short_term"))
    effect.uses = 5
    assert "1 of 6 left" in {r.key: r.value for r in effect_stat_rows(effect, data)}["uses"]
    effect.uses = 6
    assert effect_is_spent(effect, data)
    restored = PowerEffectInstance.from_dict(effect.to_dict())
    assert restored.uses == 6


# -- the data fixes ------------------------------------------------------------------------


def test_quirk_is_bought_in_ranks(data) -> None:
    effect = _blast(6)
    effect.flaws.append(ModifierSelection("quirk", rank=2))
    assert effect_total_cost(effect, data) == 2 * 6 - 2


def test_diminished_range_shortens_the_reach(data) -> None:
    effect = _blast(6)
    effect.flaws.append(ModifierSelection("diminished_range", rank=2))
    row = {r.key: r for r in effect_stat_rows(effect, data)}["distance_rank"]
    assert (row.value, row.change) == ("rank 4", "worse")


def test_the_per_unit_extras_are_bought_in_ranks(data) -> None:
    catalog = data.modifier_catalog()
    for modifier_id in ("known_location", "additional_use", "enhanced_advantages"):
        assert catalog[modifier_id].ranked, modifier_id


def test_extended_only_needs_extended(data) -> None:
    effect = PowerEffectInstance("teleport", rank=8)
    effect.flaws.append(ModifierSelection("extended_only"))
    assert power_modifier_requirement_violations(Power(name="Jump", effects=[effect]), data)


def test_an_aura_is_priced_by_its_options(data) -> None:
    effect = PowerEffectInstance("damage", rank=5)
    aura = ModifierSelection("aura", config={"duration": "Sustained"})
    effect.extras.append(aura)
    assert effect_total_cost(effect, data) == 5 * (1 + 3)
    aura.config.update(duration="Continuous", selective="selective")
    assert effect_total_cost(effect, data) == 5 * (1 + 5)
    aura.config.update(duration="Concentration", selective="everyone")
    assert effect_total_cost(effect, data) == 5 * (1 + 2)


def test_immunity_offers_no_trait_to_enhance(data) -> None:
    immunity = next(e for e in data.effects if e.id == "immunity")
    assert immunity.integration.trait_boost is None


# -- the sheet: a card's roll is the use ----------------------------------------------


def test_rolling_a_tiring_short_term_power_off_its_card_charges_the_use(qapp, data) -> None:
    from mm_companion.ui.character_sheet import CharacterSheet

    char = Character.new_default(data)
    effect = _blast(6)
    effect.flaws.append(ModifierSelection("tiring"))
    effect.flaws.append(ModifierSelection("short_term"))
    power = Power(name="Blast", effects=[effect])
    char.powers.append(power)
    sheet = CharacterSheet(data, char)
    attack = power_rolls(power, char, data)[0]

    # The card's roll line asks the roller, which throws the die and says it was a use.
    sheet.powers.rollRequested.emit(attack)

    assert power.effects[0].uses == 1
    assert "fatigued" in {c.condition_id for c in char.conditions}
    # A click the roller refuses — the die still tumbling — neither counts nor charges.
    sheet.dice.view.panel._rolling = True
    sheet.powers.rollRequested.emit(attack)
    assert power.effects[0].uses == 1
    assert "exhausted" not in {c.condition_id for c in char.conditions}
    sheet.dice.view.panel._rolling = False

    assert sheet.powers.recharge(power)
    assert power.effects[0].uses == 0


def test_the_relay_keeps_the_attacks_rank_and_penetration_bounded(data) -> None:
    from mm_companion.core.session.protocol import MAX_LADDER_RANK, sanitize_spec

    attacker = Character.new_default(data)
    spec = _save(Power(name="Blast", effects=[_blast(8, "penetrating")]), attacker, data)
    wired = RollSpec.from_dict(sanitize_spec(spec.to_dict()))
    assert (wired.ladder.rank, wired.ladder.penetrating) == (8, 8)
    raw = spec.to_dict()
    raw["ladder"]["rank"] = 10**9
    raw["ladder"]["penetrating"] = -3
    clipped = sanitize_spec(raw)["ladder"]
    assert clipped["rank"] == MAX_LADDER_RANK and "penetrating" not in clipped

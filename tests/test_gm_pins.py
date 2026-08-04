"""The pinned-parameter model: what a GM card's chips resolve to.

Headless — no Qt, no window. That is the point of putting
:mod:`mm_companion.core.rules.pins` in ``core``: the interesting questions here
are "does a Weakened creature's chip show the weakened number" and "what happens
when the power a pin names is deleted", and neither needs a screen to answer.
"""

from __future__ import annotations

import pytest

from mm_companion.core.character import Character
from mm_companion.core.data_loader import GameData, load_game_data
from mm_companion.core.npc import quick_npc
from mm_companion.core.rules import (
    MISSING_VALUE,
    PIN_ABILITY,
    PIN_DEFENSE_CLASS,
    PIN_INITIATIVE,
    PIN_POWER,
    PIN_RESISTANCE,
    PIN_SKILL,
    SELECT_FIRST_DAMAGE,
    PinRef,
    apply_condition,
    available_pins,
    default_pin_choices,
    default_pins,
    parse_pins,
    pin_label,
    resolve_pin,
    resolve_pins,
)
from mm_companion.core.storage import DEFAULT_SETTINGS


@pytest.fixture(scope="module")
def data() -> GameData:
    return load_game_data()


@pytest.fixture
def goon(data: GameData) -> Character:
    """A quick NPC: Attack 6, a Damage and an Affliction at rank 6, Defence 6."""
    return quick_npc(data, name="Goon", attack=6, effect=6, defence=6, toughness=6)


# -- the reference round-trips ----------------------------------------------


def test_a_ref_round_trips_through_plain_json() -> None:
    ref = PinRef(PIN_POWER, key="abc123", index=1)
    assert PinRef.from_dict(ref.to_dict()) == ref


def test_a_ref_writes_only_what_differs_from_the_default() -> None:
    assert PinRef(PIN_INITIATIVE).to_dict() == {"kind": "initiative"}
    assert PinRef(PIN_RESISTANCE, "DEF").to_dict() == {"kind": "resistance", "key": "DEF"}


@pytest.mark.parametrize(
    "raw",
    [
        None,
        "resistance",
        {},
        {"kind": "nonsense"},
        {"key": "DEF"},
    ],
)
def test_an_unusable_ref_is_dropped_rather_than_raising(raw: object) -> None:
    """These parse a settings file a user may have edited; one bad entry costs one chip."""
    assert PinRef.from_dict(raw) is None


def test_parse_pins_keeps_the_readable_entries_and_their_order() -> None:
    refs = parse_pins(
        [{"kind": "resistance", "key": "DEF"}, {"kind": "junk"}, {"kind": "initiative"}]
    )
    assert refs == [PinRef(PIN_RESISTANCE, "DEF"), PinRef(PIN_INITIATIVE)]


# -- resolving each kind ------------------------------------------------------


def test_an_ability_pin_reads_signed(goon: Character, data: GameData) -> None:
    value = resolve_pin(goon, data, PinRef(PIN_ABILITY, "ATK"))
    assert (value.label, value.value) == ("ATK", "+6")
    assert value.spec is not None and value.spec.modifier == 6


def test_a_resistance_pin_reads_unsigned(goon: Character, data: GameData) -> None:
    """A defence is a number to beat, not a bonus to add — "+6" would invite the
    wrong side of an attack roll."""
    value = resolve_pin(goon, data, PinRef(PIN_RESISTANCE, "DEF"))
    assert (value.label, value.value) == ("DEF", "6")


def test_the_defence_dc_is_its_own_pin_ten_above_the_rank(goon: Character, data: GameData) -> None:
    """The number a GM types into the DC box, kept apart from the rank the sheet shows."""
    rank = resolve_pin(goon, data, PinRef(PIN_RESISTANCE, "DEF"))
    dc = resolve_pin(goon, data, PinRef(PIN_DEFENSE_CLASS))

    assert (dc.label, dc.value) == ("DEF DC", "16")
    assert int(dc.value) == int(rank.value) + 10
    # Nobody rolls a difficulty, so it carries no spec — but it is not *missing*.
    assert dc.spec is None
    assert dc.missing is False


def test_a_skill_pin_reads_its_row(goon: Character, data: GameData) -> None:
    goon.skill_ranks["Perception"] = 4
    value = resolve_pin(goon, data, PinRef(PIN_SKILL, "Perception"))
    assert value.label == "Perception"
    assert value.value == "+4"


def test_an_initiative_pin_is_labelled_from_the_ruleset(goon: Character, data: GameData) -> None:
    value = resolve_pin(goon, data, PinRef(PIN_INITIATIVE))
    assert value.label == "Initiative"
    assert value.spec is not None and value.spec.kind == "initiative"


def test_a_power_pin_reads_an_attack_as_a_bonus_and_a_save_as_a_dc(
    goon: Character, data: GameData
) -> None:
    damage = next(p for p in goon.powers if p.name == "Damage")

    attack = resolve_pin(goon, data, PinRef(PIN_POWER, damage.id, index=0))
    save = resolve_pin(goon, data, PinRef(PIN_POWER, damage.id, index=1))

    assert (attack.label, attack.value) == ("Damage", "+6")
    assert (save.label, save.value) == ("Damage", "DC 16")
    # The wielder rolls the attack; the *target* rolls the save, and it already
    # reaches them as the follow-up chip on the attack's history card. So the save
    # chip is read, not thrown — and is not "missing" either.
    assert attack.spec is not None
    assert save.spec is None
    assert save.missing is False


# -- the late-bound default ---------------------------------------------------


def test_the_first_damage_default_finds_the_damage_power(goon: Character, data: GameData) -> None:
    value = resolve_pin(goon, data, PinRef(PIN_POWER, select=SELECT_FIRST_DAMAGE))

    # The *attack roll*, which is the chip a GM clicks when the mook swings —
    # not the save it forces, which is the target's to roll.
    assert (value.label, value.value) == ("Damage", "+6")
    assert value.spec is not None and value.spec.kind == "power-check"
    # Resolving pins it down: the returned ref names the power outright, so a
    # picker can tell "already pinned" from "not yet".
    assert value.ref.key == next(p for p in goon.powers if p.name == "Damage").id
    assert value.ref.select == ""


def test_the_first_damage_default_follows_the_powers_the_npc_actually_has(
    data: GameData,
) -> None:
    """Late binding earns its keep here: the default was written before this NPC."""
    weak, strong = (
        quick_npc(data, name=n, attack=attack, effect=4, defence=4, toughness=4)
        for n, attack in (("Weak", 3), ("Strong", 11))
    )
    ref = PinRef(PIN_POWER, select=SELECT_FIRST_DAMAGE)

    assert resolve_pin(weak, data, ref).value == "+3"
    assert resolve_pin(strong, data, ref).value == "+11"


def test_the_npc_defaults_are_the_three_a_gm_asked_for(goon: Character, data: GameData) -> None:
    refs = default_pins("npc", DEFAULT_SETTINGS["gm_default_pins"])
    values = resolve_pins(goon, data, refs)

    assert [(v.label, v.value) for v in values] == [
        ("DEF", "6"),
        ("Toughness", "6"),
        ("ATK", "+6"),
        ("Damage", "+6"),
    ]


def test_the_player_defaults_are_the_three_a_gm_asked_for(data: GameData) -> None:
    character = Character.new_default(data)
    character.skill_ranks["Perception"] = 3
    refs = default_pins("player", DEFAULT_SETTINGS["gm_default_pins"])

    labels = [v.label for v in resolve_pins(character, data, refs)]
    assert labels == ["DEF", "Toughness", "Initiative", "Perception"]


def test_defaults_for_an_unknown_card_kind_are_simply_empty() -> None:
    assert default_pins("dragon", DEFAULT_SETTINGS["gm_default_pins"]) == []
    assert default_pins("npc", None) == []


# -- what a default may name --------------------------------------------------


def test_the_defaults_picker_offers_no_concrete_power(data: GameData) -> None:
    """A power id is per-character, and a default is written before the card is.

    So the only power a default may name is the late-bound one — offering the
    concrete kind would be offering pins that resolve to nothing on every card but
    the one they were read off.
    """
    powers = [ref for group in default_pin_choices(data) for ref in _refs(group)]
    power_refs = [ref for ref in powers if ref.kind == PIN_POWER]

    assert power_refs == [PinRef(PIN_POWER, select=SELECT_FIRST_DAMAGE)]


def test_the_defaults_picker_covers_the_character_free_traits(data: GameData) -> None:
    groups = {group.title: [v.ref for v in group.values] for group in default_pin_choices(data)}

    assert set(groups) == {"Abilities", "Resistances", "Derived", "Skills", "Powers"}
    assert PinRef(PIN_ABILITY, "ATK") in groups["Abilities"]
    assert PinRef(PIN_RESISTANCE, "DEF") in groups["Resistances"]
    assert groups["Derived"] == [PinRef(PIN_INITIATIVE), PinRef(PIN_DEFENSE_CLASS)]
    assert PinRef(PIN_SKILL, "Perception") in groups["Skills"]


def test_every_offered_default_is_captioned_and_storable(data: GameData) -> None:
    for group in default_pin_choices(data):
        for value in group.values:
            assert value.label
            assert value.value == ""  # no character, so no reading
            assert PinRef.from_dict(value.ref.to_dict()) == value.ref


def test_the_late_bound_damage_pin_is_named_before_it_resolves(data: GameData) -> None:
    """It is the one power pin written down while unresolved, so "Power" won't do."""
    assert pin_label(PinRef(PIN_POWER, select=SELECT_FIRST_DAMAGE), data) == "First Damage"
    assert pin_label(PinRef(PIN_POWER, key="abc123"), data) == "Power"


def _refs(group) -> list[PinRef]:
    return [value.ref for value in group.values]


# -- conditions come through for free ----------------------------------------


def test_a_condition_moves_the_chip_and_says_why(goon: Character, data: GameData) -> None:
    """The reason values come off the roll builders rather than the derived ones.

    ``resistance_total`` is condition-free by design (the build math must be), so
    a chip reading it would show a number the GM must then adjust in their head.
    """
    before = resolve_pin(goon, data, PinRef(PIN_RESISTANCE, "DODGE"))
    apply_condition(goon, "vulnerable", data)
    after = resolve_pin(goon, data, PinRef(PIN_RESISTANCE, "DODGE"))

    assert int(after.value) < int(before.value)
    assert after.hint != before.hint and after.hint.strip()


# -- a pin that no longer resolves --------------------------------------------


def test_a_pin_to_a_deleted_power_reads_as_a_dash(goon: Character, data: GameData) -> None:
    """Shown, not dropped: a chip that vanishes leaves no way to remove it."""
    damage = next(p for p in goon.powers if p.name == "Damage")
    ref = PinRef(PIN_POWER, damage.id, index=1)
    goon.powers = [p for p in goon.powers if p.id != damage.id]

    value = resolve_pin(goon, data, ref)

    assert value.value == MISSING_VALUE
    assert value.missing is True
    assert value.spec is None
    assert value.ref == ref  # still removable


def test_the_first_damage_default_on_a_creature_with_no_powers(data: GameData) -> None:
    value = resolve_pin(
        Character.new_default(data), data, PinRef(PIN_POWER, select=SELECT_FIRST_DAMAGE)
    )
    assert value.missing is True


def test_an_unknown_trait_key_reads_as_a_dash(goon: Character, data: GameData) -> None:
    """A mod could be disabled between saving a pin and reading it back."""
    for ref in (PinRef(PIN_ABILITY, "PSI"), PinRef(PIN_RESISTANCE, "SANITY")):
        assert resolve_pin(goon, data, ref).missing is True


def test_a_skill_row_the_character_never_took_reads_as_a_dash(
    goon: Character, data: GameData
) -> None:
    """Rather than quietly reading as the bare ability, which looks like a real number."""
    assert resolve_pin(goon, data, PinRef(PIN_SKILL, "Expertise::Law")).missing is True

    goon.focuses["Expertise"] = ["Law"]
    goon.skill_ranks["Expertise::Law"] = 5
    assert resolve_pin(goon, data, PinRef(PIN_SKILL, "Expertise::Law")).missing is False


def test_there_is_nothing_to_resolve_before_a_player_pushes_a_sheet(data: GameData) -> None:
    assert resolve_pin(None, data, PinRef(PIN_RESISTANCE, "DEF")).missing is True


def test_a_pin_is_captioned_the_same_whether_or_not_it_resolves(data: GameData) -> None:
    """A player card exists before that player pushes a sheet, and a strip of raw
    keys ("initiative —") is a worse thing to show than a strip of proper names."""
    refs = [
        PinRef(PIN_RESISTANCE, "DEF"),
        PinRef(PIN_INITIATIVE),
        PinRef(PIN_SKILL, "Perception"),
        PinRef(PIN_DEFENSE_CLASS),
    ]
    character = Character.new_default(data)

    assert [resolve_pin(None, data, r).label for r in refs] == [
        resolve_pin(character, data, r).label for r in refs
    ]
    assert [pin_label(r, data) for r in refs] == ["DEF", "Initiative", "Perception", "DEF DC"]


# -- the picker's content -----------------------------------------------------


def test_available_pins_covers_every_trait_and_every_power_roll(
    goon: Character, data: GameData
) -> None:
    groups = {g.title: g.values for g in available_pins(goon, data)}

    assert set(groups) == {"Abilities", "Resistances", "Derived", "Skills", "Powers"}
    assert len(groups["Abilities"]) == len(data.abilities)
    assert len(groups["Resistances"]) == len(data.resistances)
    # A quick NPC has a Damage and an Affliction, each an attack and a save.
    assert len(groups["Powers"]) == 4
    assert {v.label for v in groups["Powers"]} == {"Damage", "Affliction"}
    assert [v.label for v in groups["Derived"]] == ["Initiative", "DEF DC"]


def test_available_pins_offers_an_untrained_skill_and_a_taken_focus(
    goon: Character, data: GameData
) -> None:
    goon.focuses["Expertise"] = ["Law"]
    goon.skill_ranks["Expertise::Law"] = 5

    skills = next(g for g in available_pins(goon, data) if g.title == "Skills").values
    labels = [v.label for v in skills]

    assert "Perception" in labels  # anyone may try it
    assert "Expertise::Law" in labels  # only because this NPC took it
    assert all(not v.missing for v in skills)


def test_everything_offered_by_the_picker_resolves(goon: Character, data: GameData) -> None:
    """A picker row that pins to a dash would be a bug the GM discovers afterwards."""
    for group in available_pins(goon, data):
        for value in group.values:
            assert resolve_pin(goon, data, value.ref).missing is False, value.label


def test_the_same_pin_reads_two_ways_and_rolls_one(goon: Character, data: GameData) -> None:
    """The chip and the picker disagree about the number, and only the number.

    A chip is read mid-fight, so it shows what to actually use. The picker is a
    catalogue, so it shows what the creature is. The RollSpec is the same either
    way, which is what stops the split reaching anything that matters.
    """
    apply_condition(goon, "vulnerable", data)
    ref = PinRef(PIN_RESISTANCE, "DODGE")

    live = resolve_pin(goon, data, ref)
    base = resolve_pin(goon, data, ref, with_conditions=False)

    assert (live.value, base.value) == ("3", "6")
    assert live.spec == base.spec
    assert live.hint == base.hint  # the reason travels either way


def test_the_split_reaches_every_trait_kind(goon: Character, data: GameData) -> None:
    goon.skill_ranks["Perception"] = 4
    apply_condition(goon, "impaired", data, parameter="Perception")
    apply_condition(goon, "fatigued", data)

    for ref in (
        PinRef(PIN_ABILITY, "AGL"),
        PinRef(PIN_RESISTANCE, "DODGE"),
        PinRef(PIN_SKILL, "Perception"),
        PinRef(PIN_INITIATIVE),
    ):
        live = resolve_pin(goon, data, ref)
        base = resolve_pin(goon, data, ref, with_conditions=False)
        assert live.label == base.label
        assert live.spec == base.spec


def test_available_pins_can_be_asked_for_base_numbers(goon: Character, data: GameData) -> None:
    apply_condition(goon, "vulnerable", data)

    def dodge(**kwargs) -> str:
        groups = available_pins(goon, data, **kwargs)
        resistances = next(g for g in groups if g.title == "Resistances").values
        return next(v.value for v in resistances if v.label == "Dodge")

    assert dodge(with_conditions=False) == "6"
    assert dodge() == "3"

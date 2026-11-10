"""What a roll's modifier is made of (``RollSpec.parts``): every trait roll says, the
breakdown always adds up, and it survives the wire."""

from __future__ import annotations

import pytest

from mm_companion.core.character import Character
from mm_companion.core.data_loader import load_game_data
from mm_companion.core.powers import Power, PowerEffectInstance
from mm_companion.core.rules import (
    KIND_POWER_SAVE,
    OTHER_PART,
    RollSpec,
    ability_roll,
    add_part,
    apply_maneuvers,
    initiative_roll,
    localize_spec,
    power_rolls,
    resistance_roll,
    skill_roll,
    spec_parts,
)
from mm_companion.core.session.protocol import MAX_SPEC_PARTS, sanitize_spec


@pytest.fixture(scope="module")
def data():
    return load_game_data()


@pytest.fixture
def hero(data) -> Character:
    char = Character.new_default(data)
    char.abilities.update({"STR": 4, "STA": 5, "AGL": 3, "ATK": 7})
    char.skill_ranks["Athletics"] = 5
    return char


def _adds_up(spec: RollSpec) -> None:
    assert sum(amount for _name, amount in spec_parts(spec)) == spec.modifier


def test_a_skill_roll_names_its_ability_and_its_ranks(data, hero) -> None:
    spec = skill_roll(hero, data, "Athletics")
    assert spec.modifier == 9
    assert spec_parts(spec) == (("Strength", 4), ("Athletics ranks", 5))


def test_every_trait_roll_adds_up(data, hero) -> None:
    rolls = [ability_roll(hero, data, "STR"), initiative_roll(hero, data)]
    rolls += [resistance_roll(hero, data, r.key) for r in data.resistances]
    for spec in rolls:
        _adds_up(spec)


def test_a_resistance_names_the_trait_it_derives_from(data, hero) -> None:
    key = next(r.key for r in data.resistances if r.name == "Toughness")
    hero.resistances[key] = 2
    assert spec_parts(resistance_roll(hero, data, key)) == (
        ("Stamina", 5),
        ("Toughness ranks", 2),
    )


def test_a_power_attack_and_a_maneuver_on_it(data, hero) -> None:
    hero.powers.append(Power(name="Blast", effects=[PowerEffectInstance("damage", rank=8)]))
    attack = power_rolls(hero.powers[0], hero, data)[0]
    assert spec_parts(attack) == (("Attack", 7),)
    traded = apply_maneuvers(attack, [("all_out_attack", 3)], data)
    assert spec_parts(traded) == (("Attack", 7), ("All-Out Attack", 3))
    _adds_up(traded)


def test_a_save_filled_in_on_the_targets_sheet_names_their_trait(data, hero) -> None:
    key = next(r.key for r in data.resistances if r.name == "Toughness")
    save = RollSpec(label="Toughness vs. 18", dc=18, kind=KIND_POWER_SAVE, trait_key=key)
    filled = localize_spec(save, hero, data)
    assert filled.modifier == 5
    assert spec_parts(filled) == (("Stamina", 5),)


def test_a_spec_with_no_parts_still_explains_itself() -> None:
    assert spec_parts(RollSpec(label="Attack +7", modifier=7)) == (("Attack +7", 7),)
    assert spec_parts(RollSpec(label="Plain")) == ()
    # Parts that fell behind the modifier are made up by an Other line.
    behind = RollSpec(label="X", modifier=6, parts=(("Strength", 4),))
    assert spec_parts(behind) == (("Strength", 4), (OTHER_PART, 2))


def test_add_part_shifts_and_names() -> None:
    spec = add_part(RollSpec(label="X", modifier=4, parts=(("Strength", 4),)), "Cover", 2)
    assert spec.modifier == 6 and spec_parts(spec) == (("Strength", 4), ("Cover", 2))
    assert add_part(spec, "Nothing", 0) == spec


def test_parts_cross_the_wire(data, hero) -> None:
    spec = skill_roll(hero, data, "Athletics")
    raw = sanitize_spec(spec.to_dict())
    assert raw["parts"] == [["Strength", 4], ["Athletics ranks", 5]]
    assert RollSpec.from_dict(raw).parts == spec.parts


def test_the_server_keeps_parts_in_shape() -> None:
    raw = {
        "label": "X",
        "parts": [["Strength", 4], ["bad", True], ["", 2], "junk", ["Ranks", "5"]],
    }
    assert sanitize_spec(raw)["parts"] == [["Strength", 4]]
    many = {"label": "X", "parts": [[f"p{i}", 1] for i in range(40)]}
    assert len(sanitize_spec(many)["parts"]) == MAX_SPEC_PARTS
    assert RollSpec.from_dict({"label": "X", "parts": "nonsense"}).parts == ()

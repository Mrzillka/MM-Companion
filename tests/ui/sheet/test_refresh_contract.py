"""What a bus subscriber is allowed to do, checked rather than asserted in prose.

Two things now rest on the same promise, so it is worth pinning from the outside:

* :func:`~mm_companion.core.rules.stable_build` lets a whole refresh pass reuse one
  gather of the build, which is only sound while nothing writes the character
  *inside* the pass;
* coalescing lets an expensive subscriber run once at the end of the turn rather
  than once per publish, which is only sound while every subscriber is an
  **idempotent** redraw.

Both are properties of every subscriber at once, so neither belongs in a test about
one block. A block added later — a mod's included — is covered by walking the
registry rather than a list written out here.
"""

from __future__ import annotations

import json

import pytest
from PySide6.QtWidgets import QApplication

from mm_companion.core.character import AdvantageSelection, Character
from mm_companion.core.data_loader import load_game_data
from mm_companion.core.powers import (
    STRUCTURE_ARRAY,
    Power,
    PowerEffectInstance,
    PowerGroup,
)
from mm_companion.core.rules import build_item_from_entry, resistance_total
from mm_companion.ui.blocks import block_descriptors
from mm_companion.ui.character_sheet import CharacterSheet


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


@pytest.fixture(scope="module")
def data():
    return load_game_data()


def _furnished(data) -> Character:
    """A character with something in every block a subscriber reads."""
    char = Character.new_default(data)
    char.abilities.update({"STR": 4, "STA": 3, "AGL": 2, "AWE": 2})
    char.resistances.update({"DODGE": 3, "TOUGHNESS": 2})
    char.skill_ranks["Athletics"] = 4
    char.advantages.append(AdvantageSelection(name="Equipment", rank=5))
    for catalog_id in ("chain_mail", "leather_armor"):
        char.equipment.append(build_item_from_entry(data.equipment_catalog()[catalog_id], data))
    char.powers.append(Power(name="Blast", effects=[PowerEffectInstance("damage", rank=6)]))
    char.powers.append(Power(name="Armor", effects=[PowerEffectInstance("protection", rank=6)]))
    return char


def _state(char: Character) -> str:
    return json.dumps(char.to_dict(), sort_keys=True, default=str)


def test_no_subscriber_writes_the_character(qapp, data) -> None:
    """The one rule a `stable_build` scope asks for, checked block by block.

    A subscriber that wrote the model would read its own stale numbers back for the
    rest of its pass, since the gather it shares was made before the write. The one
    subscriber that legitimately writes says so itself — see the next test.
    """
    char = _furnished(data)
    sheet = CharacterSheet(data, char)
    sheet.bus.flush()

    offenders: list[str] = []
    for descriptor in block_descriptors():
        section = sheet._sections_by_key.get(descriptor.key)
        if section is None:
            continue
        for topic, method in descriptor.subscribes.items():
            before = _state(char)
            getattr(section, method)()
            if _state(char) != before:
                offenders.append(f"{descriptor.key}.{method} (on {topic})")

    assert offenders == []


def test_every_subscriber_is_idempotent(qapp, data) -> None:
    """What coalescing rests on: running a handler twice reads the same as once."""
    char = _furnished(data)
    sheet = CharacterSheet(data, char)
    sheet.bus.flush()

    for descriptor in block_descriptors():
        section = sheet._sections_by_key.get(descriptor.key)
        if section is None:
            continue
        for method in descriptor.subscribes.values():
            getattr(section, method)()
            once = _state(char)
            getattr(section, method)()
            assert _state(char) == once, f"{descriptor.key}.{method} is not idempotent"


def test_an_array_with_a_stale_active_member_draws_the_one_it_settles_on(qapp, data) -> None:
    """`PowersSection._rebuild_list` normalizes an array before it draws anything.

    The one subscriber that writes the model, and the write moves real numbers: an
    array's active member is the branch whose trait boosts stand. This is the pass
    agreeing with itself — what the rebuild settled on, and what the rest of the sheet
    then reads, have to be the same member.
    """
    char = Character.new_default(data)
    strong = Power(name="Iron Skin", effects=[PowerEffectInstance("protection", rank=8)])
    weak = Power(name="Thin Skin", effects=[PowerEffectInstance("protection", rank=1)])
    group = PowerGroup(name="Armoury", mode=STRUCTURE_ARRAY, children=[strong, weak])
    # A stale id, as a file written before that member was removed would carry: the
    # rebuild has to settle it on the first child before anything reads the build.
    group.active_child_id = "gone"
    char.powers.append(group)

    sheet = CharacterSheet(data, char)
    sheet.bus.flush()

    assert group.active_child_id == strong.id
    # Iron Skin is the live member, so Toughness reads its 8 — not Thin Skin's 1, and
    # not the nothing a group with no valid member would have granted.
    assert resistance_total(char, data, "TOUGHNESS") == 8
    assert sheet.resistances._resistance_total["TOUGHNESS"].text() == "8"

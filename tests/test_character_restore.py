"""``Character.restore`` — putting an earlier build back without moving anything else.

The model half of undo (the controller is ``tests/test_undo.py``). Two promises are
under test, and both are the kind that fail silently:

* the *containers* survive, because a block may hold a reference to one of them
  rather than to the character; and
* the *runtime* flags survive. A power's own switches ride in the snapshot now, so
  those come back with the build; what ``to_dict`` still omits is the gear in the
  character's hands, which a plain round trip would re-wear.
"""

from __future__ import annotations

from dataclasses import fields

from mm_companion.core.character import (
    AdvantageSelection,
    AppliedCondition,
    Character,
    Complication,
    apply_runtime,
    capture_runtime,
)
from mm_companion.core.data_loader import load_game_data
from mm_companion.core.equipment import EquipmentItem
from mm_companion.core.powers import Power, PowerEffectInstance, PowerGroup


def _built_character() -> Character:
    """A character with something in every collection worth restoring."""
    char = Character.new_default(load_game_data())
    char.profile["hero_name"] = "Ghost"
    char.abilities["STR"] = 4
    char.skill_ranks["Stealth"] = 6
    char.focuses["Close Combat"] = ["Swords"]
    char.specializations["Stealth"] = ["Urban"]
    char.advantages.append(AdvantageSelection("Close Attack", 2))
    char.complications.append(Complication("Enemy", "Dr. Volt swears revenge."))
    char.conditions.append(AppliedCondition("dazed"))
    char.powers.append(
        Power(name="Fire Blast", effects=[PowerEffectInstance(effect_id="damage", rank=8)])
    )
    char.equipment.append(
        EquipmentItem(
            catalog_id="rifle",
            category="weapons",
            build=Power(name="Rifle", effects=[PowerEffectInstance(effect_id="damage", rank=5)]),
        )
    )
    return char


def test_restore_reproduces_the_snapshot() -> None:
    """The build comes back exactly, which is the whole promise."""
    char = _built_character()
    snapshot = char.to_dict()

    char.abilities["STR"] = 12
    char.powers.clear()
    char.conditions.clear()
    char.profile["hero_name"] = "Typo"
    char.restore(snapshot)

    assert char == Character.from_dict(snapshot)


def test_restore_keeps_every_container_identity() -> None:
    """A block may alias one of these dicts, so rebinding it would desync silently.

    Driven off ``fields()`` rather than a list of names, so a collection added to the
    model later is covered without editing this test.
    """
    char = _built_character()
    snapshot = char.to_dict()
    before = {
        spec.name: id(getattr(char, spec.name))
        for spec in fields(char)
        if isinstance(getattr(char, spec.name), (dict, list))
    }

    char.abilities["STR"] = 12
    char.restore(snapshot)

    assert {name: id(getattr(char, name)) for name in before} == before


def test_restore_keeps_a_switched_off_power_off() -> None:
    """Undoing a *build* edit must not flip what the player has switched on."""
    char = _built_character()
    char.powers[0].activated = False
    char.powers[0].effects[0].toggled_on = False
    snapshot = char.to_dict()

    char.abilities["STR"] = 9
    char.restore(snapshot)

    assert char.abilities["STR"] == 4
    assert char.powers[0].activated is False
    assert char.powers[0].effects[0].toggled_on is False


def test_restore_keeps_a_stowed_item_stowed_and_a_vehicle_at_speed() -> None:
    """``worn`` and ``current_speed`` are runtime for the same reason ``activated`` is."""
    char = _built_character()
    char.equipment[0].worn = False
    char.equipment[0].current_speed = 2
    snapshot = char.to_dict()

    char.abilities["STR"] = 9
    char.restore(snapshot)

    assert char.equipment[0].worn is False
    assert char.equipment[0].current_speed == 2


def test_restore_keeps_runtime_on_an_items_own_build() -> None:
    """An item's build *is* a Power, so its switches ride in the snapshot too.

    Only ``worn`` and ``current_speed`` are held over by hand; a rifle whose build is
    switched off comes back off from the saved build itself.
    """
    char = _built_character()
    char.equipment[0].build.activated = False
    char.equipment[0].build.effects[0].toggled_on = False
    snapshot = char.to_dict()

    char.restore(snapshot)

    assert char.equipment[0].build.activated is False
    assert char.equipment[0].build.effects[0].toggled_on is False


def test_restore_keeps_runtime_on_a_fitted_accessory() -> None:
    """An accessory rides on its host, so the walk has to recurse into it."""
    char = _built_character()
    scope = EquipmentItem(catalog_id="scope", category="gear", worn=False)
    char.equipment[0].accessories.append(scope)
    snapshot = char.to_dict()

    char.restore(snapshot)

    assert char.equipment[0].accessories[0].worn is False


def test_restore_keeps_an_arrays_active_child() -> None:
    """Which member of an array is live is runtime, and saved with the build."""
    char = Character.new_default(load_game_data())
    first = Power(name="Blast", effects=[PowerEffectInstance(effect_id="damage", rank=8)])
    second = Power(name="Burst", effects=[PowerEffectInstance(effect_id="damage", rank=6)])
    char.powers.append(
        PowerGroup(mode="array", children=[first, second], active_child_id=second.id)
    )
    snapshot = char.to_dict()

    char.abilities["STR"] = 3
    char.restore(snapshot)

    assert char.powers[0].active_child_id == second.id


def test_restore_leaves_a_power_the_snapshot_never_had_switched_on() -> None:
    """Undoing *past* a power's creation: the newcomer comes up at its defaults."""
    char = _built_character()
    without = char.to_dict()
    extra = Power(name="Shield", effects=[PowerEffectInstance(effect_id="protection", rank=4)])
    char.powers.append(extra)

    char.restore(char.to_dict())  # the state *with* the new power
    assert char.powers[1].activated is True

    char.restore(without)
    assert len(char.powers) == 1


def test_runtime_for_a_resized_effect_list_is_left_alone() -> None:
    """Effects have no id, so they are keyed by position — meaningless once resized.

    Writing them anyway is the one way this misapplies, so the mismatch is skipped.
    """
    char = _built_character()
    char.powers[0].effects.append(PowerEffectInstance(effect_id="protection", rank=2))
    char.powers[0].effects[0].toggled_on = False
    char.powers[0].effects[1].toggled_on = False
    captured = capture_runtime(char)

    char.powers[0].effects.pop()
    char.powers[0].effects[0].toggled_on = True
    apply_runtime(char, captured)

    assert char.powers[0].effects[0].toggled_on is True  # untouched, not misapplied


def test_restore_without_keep_runtime_re_wears_everything() -> None:
    """The opt-out exists so the carrying is a choice, not a hidden rule of restore.

    What it covers is only what the save leaves out, which is now the gear in your
    hands: a power's own switch travels *in* the snapshot, so it comes back from
    there either way.
    """
    char = _built_character()
    char.powers[0].activated = False
    char.equipment[0].worn = False

    char.restore(char.to_dict(), keep_runtime=False)

    assert char.equipment[0].worn is True
    assert char.powers[0].activated is False

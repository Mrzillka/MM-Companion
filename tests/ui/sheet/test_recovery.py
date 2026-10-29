"""Recovery checks: the roll that sheds a condition, from the data to the sheet.

``conditions.json`` has carried a structured ``recoveryCheck`` since the catalog was
written; :mod:`mm_companion.core.rules.recovery` is what rolls it. The rules half is
proved here without a display; the sheet half (a chip's 🎲, the roller, the condition
coming off) with one, since the round trip runs over the block bus.
"""

from __future__ import annotations

import mm_companion.ui.dice_roller as roller_module
from mm_companion.core.character import AppliedCondition, Character
from mm_companion.core.rules import (
    KIND_ABILITY,
    RollSpec,
    apply_condition,
    apply_damage_step,
    apply_recovery,
    can_recover,
    damage_steps,
    recovery_request,
    recovery_roll,
    recovery_succeeded,
)
from mm_companion.core.session.protocol import sanitize_spec


def _applied(char: Character, condition_id: str) -> AppliedCondition:
    return next(c for c in char.conditions if c.condition_id == condition_id and not c.provenance)


# -- which conditions offer a check ------------------------------------------------


def test_a_damage_condition_is_rolled_off_with_stamina(data) -> None:
    char = Character.new_default(data)
    char.abilities["STA"] = 3
    apply_condition(char, "dazed", data)

    spec = recovery_roll(char, data, _applied(char, "dazed"))

    assert spec is not None
    assert (spec.dc, spec.modifier, spec.recovers) == (10, 3, "dazed")
    assert spec.label == "Recover from Dazed (Stamina)"


def test_rest_and_bundled_members_offer_no_die(data) -> None:
    char = Character.new_default(data)
    apply_condition(char, "fatigued", data)  # cleared by rest, not a roll
    apply_condition(char, "staggered", data)  # bundles Dazed and Hindered

    offered = {c.condition_id: can_recover(char, data, c) for c in char.conditions}

    assert offered["fatigued"] is False
    assert offered["staggered"] is True
    member = next(c for c in char.conditions if c.condition_id == "dazed")
    assert member.provenance == "staggered" and offered["dazed"] is False


def test_staggered_waits_until_nothing_worse_is_on(data) -> None:
    char = Character.new_default(data)
    apply_damage_step(char, damage_steps(data)[2], data)  # Hit, Staggered, Stunned

    assert can_recover(char, data, _applied(char, "staggered")) is False
    assert can_recover(char, data, _applied(char, "stunned")) is True


# -- what a made check does --------------------------------------------------------


def test_a_made_check_peels_one_hit_off_a_stack(data) -> None:
    char = Character.new_default(data)
    apply_condition(char, "hit", data)
    apply_condition(char, "hit", data)

    removed, added = apply_recovery(char, data, "hit")

    assert removed == [("hit", None)] and added == []
    assert _applied(char, "hit").count == 1


def test_dying_needs_two_degrees_and_stabilises_to_incapacitated(data) -> None:
    char = Character.new_default(data)
    apply_condition(char, "dying", data)
    spec = recovery_roll(char, data, _applied(char, "dying"))

    assert recovery_succeeded(data, spec, 1) is False
    assert recovery_succeeded(data, spec, 2) is True

    removed, added = apply_recovery(char, data, "dying")

    assert removed == [("dying", None)] and added == [("incapacitated", None)]
    ids = {c.condition_id for c in char.conditions if c.provenance is None}
    assert ids == {"incapacitated"}


def test_a_condition_gone_meanwhile_is_left_alone(data) -> None:
    char = Character.new_default(data)

    assert apply_recovery(char, data, "dazed") == ([], [])


def test_a_failed_or_missing_degree_is_no_recovery(data) -> None:
    spec = RollSpec(label="x", recovers="dazed")

    assert recovery_succeeded(data, spec, -1) is False
    assert recovery_succeeded(data, spec, None) is False
    assert recovery_succeeded(data, RollSpec(label="x"), 3) is False


# -- asking somebody else ----------------------------------------------------------


def test_a_request_is_a_template_their_own_sheet_fills_in(data) -> None:
    spec = recovery_request(data, AppliedCondition("stunned"))

    assert spec is not None
    assert (spec.kind, spec.trait_key, spec.modifier, spec.dc) == (KIND_ABILITY, "STA", 0, 10)
    assert spec.recovers == "stunned"


def test_the_condition_survives_the_wire(data) -> None:
    """Protocol v15: the server's whitelist keeps what the check is for."""
    spec = recovery_request(data, AppliedCondition("hit"))

    carried = RollSpec.from_dict(sanitize_spec(spec.to_dict()))

    assert carried is not None and carried.recovers == "hit"


# -- the sheet: chip, roller, condition gone ---------------------------------------


def _roll(sheet, die: int) -> None:
    original = roller_module.roll_d20
    roller_module.roll_d20 = lambda: die
    try:
        sheet.dice.panel._finish_roll()
    finally:
        roller_module.roll_d20 = original


def _recovery_button(section, condition_id: str):
    from PySide6.QtWidgets import QToolButton

    index = next(
        i
        for i, applied in enumerate(section._character.conditions)
        if applied.condition_id == condition_id
    )
    chip = section._condition_chips[index]
    return next(b for b in chip.findChildren(QToolButton) if b.text() == "🎲")


def test_the_sheets_die_rolls_the_check_and_a_success_takes_it_off(qapp, data) -> None:
    from mm_companion.ui.character_sheet import CharacterSheet

    sheet = CharacterSheet(data)
    sheet.conditions.apply_condition_by_id("dazed")

    _recovery_button(sheet.conditions, "dazed").click()
    assert sheet.dice.panel._spec is not None
    assert sheet.dice.panel._spec.recovers == "dazed"
    _roll(sheet, 20)

    assert sheet.character.conditions == []
    assert sheet.conditions._condition_chips == []


def test_a_failed_roll_on_the_sheet_leaves_it_on(qapp, data) -> None:
    from mm_companion.ui.character_sheet import CharacterSheet

    sheet = CharacterSheet(data)
    sheet.conditions.apply_condition_by_id("dazed")

    _recovery_button(sheet.conditions, "dazed").click()
    _roll(sheet, 1)

    assert [c.condition_id for c in sheet.character.conditions] == ["dazed"]


def test_a_session_record_of_a_made_check_takes_it_off(qapp, data) -> None:
    """The path a check the GM *asked* for takes: the player rolled it off a request
    card, and the record coming back is what says which condition it was for."""
    from mm_companion.ui.character_sheet import CharacterSheet

    sheet = CharacterSheet(data)
    sheet.conditions.apply_condition_by_id("stunned")
    spec = recovery_request(data, AppliedCondition("stunned"))

    sheet.dice.settle_recovery({"spec": sanitize_spec(spec.to_dict()), "degree": 1})

    assert sheet.character.conditions == []

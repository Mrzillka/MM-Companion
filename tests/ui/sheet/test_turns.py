"""A turn's start and end on a sheet, and the 🎲 on a condition someone else put on.

Shaking a condition off is no Recover action (the user's call, 2026-11-13): an
impression or an Affliction is resisted again as a turn ends, several in one round, so
its chip rolls the check straight off (``core.rules.condition_check``), and the
Conditions block's **End turn** rolls every one a turn's end makes, one after another
(``core.rules.turn_end``); **Start turn** takes the temporary conditions off.
"""

from __future__ import annotations

from PySide6.QtWidgets import QToolButton

import mm_companion.ui.dice_roller as roller_module
import mm_companion.ui.sections.dice as dice_module
from mm_companion.core.character import SHAKE_EFFECT, AppliedCondition, Character, ShakeOff
from mm_companion.core.rules import (
    apply_condition,
    condition_check,
    condition_check_offered,
    condition_check_request,
    turn_end,
)


def _sleep(**config) -> ShakeOff:
    return ShakeOff(SHAKE_EFFECT, "Sleep Ray", 17, "affliction", "resistance:WILL", **config)


def _held(data, char: Character, *ids: str, shake: ShakeOff | None = None) -> ShakeOff:
    shake = shake or _sleep()
    for condition_id in ids:
        apply_condition(char, condition_id, data, shake_off=shake)
    return shake


def _direct(char: Character, condition_id: str) -> AppliedCondition:
    return next(c for c in char.conditions if c.condition_id == condition_id and not c.provenance)


# -- the rules -------------------------------------------------------------------------


def test_a_shaken_condition_offers_its_own_check_not_recover(data) -> None:
    char = Character.new_default(data)
    char.resistances["WILL"] = 4
    shake = _held(data, char, "stunned")
    applied = _direct(char, "stunned")

    assert condition_check_offered(char, data, applied)
    spec = condition_check(char, data, applied)
    assert spec is not None and spec.shakes_off == shake and spec.dc == 17
    assert spec.label.startswith("Shake off Sleep Ray")
    # Not the damage-caused Stunned's DC 10 Stamina check.
    assert spec.recovers == ""


def test_a_request_for_a_player_carries_the_record(data) -> None:
    char = Character.new_default(data)
    _held(data, char, "dazed")

    spec = condition_check_request(char, data, _direct(char, "dazed"))

    assert spec is not None and spec.shakes_off == _sleep()
    assert (spec.kind, spec.trait_key, spec.modifier) == ("resistance", "WILL", 0)


def test_a_turns_end_rolls_each_record_once_and_the_damage_checks(data) -> None:
    char = Character.new_default(data)
    _held(data, char, "dazed", "hindered")  # one rung: one record, one check
    apply_condition(char, "stunned", data)  # damage-caused: DC 10 Stamina

    plan = turn_end(char, data)

    labels = [spec.label for spec in plan.checks]
    assert len(labels) == 2
    assert labels[0].startswith("Shake off Sleep Ray")
    assert labels[1].startswith("Recover from Stunned")
    assert not plan.clears and not plan.waiting


def test_a_third_degree_affliction_waits_for_its_minute(data) -> None:
    char = Character.new_default(data)
    _held(data, char, "incapacitated", shake=_sleep(degree=3))

    plan = turn_end(char, data)

    assert plan.checks == ()
    assert plan.waiting == (("Sleep Ray", "minute"),)


# -- the sheet ---------------------------------------------------------------------------


def _die(section, condition_id: str) -> QToolButton:
    index = next(
        i
        for i, applied in enumerate(section._character.conditions)
        if applied.condition_id == condition_id
    )
    chip = section._condition_chips[index]
    return next(b for b in chip.findChildren(QToolButton) if b.text() == "🎲")


def _roll(sheet, die: int) -> None:
    original = roller_module.roll_d20
    roller_module.roll_d20 = lambda: die
    try:
        sheet.dice.panel._finish_roll()
    finally:
        roller_module.roll_d20 = original


def test_the_chips_die_shakes_it_off_and_everything_it_put_on(qapp, data) -> None:
    from mm_companion.ui.character_sheet import CharacterSheet

    sheet = CharacterSheet(data)
    _held(data, sheet.character, "dazed", "hindered")
    sheet.conditions.reseed()

    _die(sheet.conditions, "dazed").click()
    assert sheet.dice.panel._spec is not None
    assert sheet.dice.panel._spec.shakes_off == _sleep()
    _roll(sheet, 20)

    assert sheet.character.conditions == []


def test_start_turn_takes_the_temporary_ones_off(qapp, data) -> None:
    from mm_companion.ui.character_sheet import CharacterSheet

    sheet = CharacterSheet(data)
    notes: list[str] = []
    sheet.conditions.noteRequested.connect(notes.append)
    apply_condition(sheet.character, "vulnerable", data, temporary=True)
    apply_condition(sheet.character, "dazed", data)
    sheet.conditions.reseed()

    sheet.conditions._start_turn_button.click()

    assert [c.condition_id for c in sheet.character.conditions] == ["dazed"]
    assert notes and notes[-1].startswith("starts their turn")


def test_end_turn_rolls_every_check_one_after_another(qapp, data, monkeypatch) -> None:
    from mm_companion.ui.character_sheet import CharacterSheet

    monkeypatch.setattr(roller_module, "ROLL_DURATION_MS", 0)
    monkeypatch.setattr(dice_module, "QUEUE_POLL_MS", 0)
    monkeypatch.setattr(dice_module, "QUEUE_GAP_MS", 0)
    monkeypatch.setattr(roller_module, "roll_d20", lambda: 20)
    sheet = CharacterSheet(data)
    _held(data, sheet.character, "hindered")
    apply_condition(sheet.character, "stunned", data)  # supersedes a direct Dazed
    sheet.conditions.reseed()
    notes: list[str] = []
    sheet.conditions.noteRequested.connect(notes.append)

    sheet.conditions._end_turn_button.click()
    for _ in range(40):
        qapp.processEvents()
        if not sheet.dice.queued_rolls() and not sheet.dice.panel.rolling:
            break

    assert sheet.character.conditions == []
    assert notes and "rolling 2 checks" in notes[-1]


def test_end_turn_with_nothing_on_says_so(qapp, data) -> None:
    from mm_companion.ui.character_sheet import CharacterSheet

    sheet = CharacterSheet(data)
    notes: list[str] = []
    sheet.conditions.noteRequested.connect(notes.append)

    plan = sheet.conditions.end_turn()

    assert plan.empty
    assert notes == ["ends their turn — nothing to shake off"]

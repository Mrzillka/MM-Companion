"""A creature's turn as it starts and as it ends: what comes off then, and each chip's die.

Two kinds of check take a condition off, and they are not the same thing (the user's
call, 2026-11-13). **Recover** is an action (PDF p206): taken once, on one's turn, with
its own limits. **Shaking off** what someone else put on — an impression resisted
again at its original DC (p201), an Affliction overcome against its own (p109) — is not
an action at all: it simply happens as a turn ends, and several can come off in one
round (Dazed by damage, Frightened by an impression and Stunned by an Affliction). So
every condition that comes off by a check offers a 🎲 on its chip, the damage-caused
kind's recovery check and the shake-off alike (:func:`condition_check_offered`,
:func:`condition_check`), and neither goes through Recover.

The turn's buttons: **Start turn** takes off what lasts "until the start of your next
turn" (the temporary conditions, :func:`~.conditions.clear_temporary`), and **End turn**
rolls every check a turn's end makes and clears what comes off with no roll
(:func:`turn_end`) — the same split the GM's End turn on a creature's card makes.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from ..character import AppliedCondition, Character, ShakeOff
from ..data_loader import CADENCE_CLEARS, GameData
from .recovery import (
    can_recover,
    end_of_turn_recoveries,
    recovery_check,
    recovery_hint,
    recovery_request,
    recovery_roll,
)
from .rolls import RollSpec
from .shaking import (
    CADENCE_MINUTE,
    CADENCE_TURN,
    held_shake_offs,
    shake_off_cadence,
    shake_off_choice,
    shake_off_dc,
    shake_off_hint,
    shake_off_recover_spec,
)

__all__ = [
    "can_shake_off",
    "condition_check_offered",
    "condition_check",
    "condition_check_request",
    "condition_check_hint",
    "TurnEnd",
    "turn_end",
    "waiting_text",
    "SHAKE_OFF_HINT",
]

#: What a shake-off check's chip 🎲 and request say under their line.
SHAKE_OFF_HINT = "Make it and it comes off, with everything it put on."


def can_shake_off(game_data: GameData, applied: AppliedCondition) -> bool:
    """Whether *applied* is shaken off by a check of its own: put on directly by an
    impression or an Affliction that is resisted again (one that clears by itself at
    the turn's end has nothing to roll)."""
    if applied.provenance is not None or applied.shake_off is None:
        return False
    return shake_off_cadence(game_data, applied.shake_off) != CADENCE_CLEARS


def condition_check_offered(
    char: Character | None, game_data: GameData, applied: AppliedCondition
) -> bool:
    """Whether *applied*'s chip offers a 🎲: its recovery check, or a shake-off."""
    return can_recover(char, game_data, applied) or can_shake_off(game_data, applied)


def condition_check(
    char: Character, game_data: GameData, applied: AppliedCondition
) -> RollSpec | None:
    """The check *char* rolls to take *applied* off — its chip's 🎲 — or ``None``.

    A shake-off resists what put it on, at *char*'s own number, and carries the record
    so a made one takes off everything it put on (``shakes_off``); anything else is its
    recovery check (``recovers``). Not Recover: no action is spent on it.
    """
    if can_shake_off(game_data, applied):
        spec = shake_off_recover_spec(char, game_data, applied.shake_off)
        return None if spec is None else replace(spec, hint=SHAKE_OFF_HINT)
    return recovery_roll(char, game_data, applied)


def condition_check_request(
    char: Character | None, game_data: GameData, applied: AppliedCondition
) -> RollSpec | None:
    """The same check as a request, for a player to roll on their own sheet.

    A template :func:`~.rolls.localize_spec` fills in. A shake-off names the record's
    trait — or, for an impression whose record names none, the best of what *char*
    (the GM's latest view of the player's sheet) may resist it with — and carries the
    record, so the player's app takes the conditions off when it is made.
    """
    if not can_shake_off(game_data, applied):
        return recovery_request(game_data, applied)
    shake = applied.shake_off
    kind, _, key = shake.trait.partition(":")
    if not (kind and key) and char is not None:
        choice = shake_off_choice(char, game_data, shake)
        if choice is not None:
            kind, key = choice.kind, choice.key
    if not (kind and key):
        return None
    return RollSpec(
        label=f"Shake off {shake.name}",
        kind=kind,
        trait_key=key,
        dc=shake_off_dc(game_data, shake),
        shakes_off=shake,
        hint=SHAKE_OFF_HINT,
    )


def condition_check_hint(game_data: GameData, applied: AppliedCondition) -> str:
    """What a chip's 🎲 says on hover: how the condition comes off, and that a click
    rolls it."""
    if can_shake_off(game_data, applied):
        return f"{shake_off_hint(game_data, applied.shake_off)} Click to roll it now."
    check = recovery_check(game_data.condition_catalog().get(applied.condition_id))
    if check is None:
        return "Roll the recovery check"
    return f"{recovery_hint(check)} — click to roll it; a success takes this off."


@dataclass(frozen=True)
class TurnEnd:
    """What a creature's turn's end does (:func:`turn_end`).

    ``checks`` are rolled, one after another: each shake-off due as a turn ends, then
    each damage-caused condition's end-of-turn recovery check. ``clears`` come off with
    no roll (an Instant Recovery Affliction). ``waiting`` are named only —
    ``(name, cadence)`` for one checked once a minute or at the end of a scene.
    ``unrolled`` names a shake-off there is nothing on the sheet to resist with.
    """

    checks: tuple[RollSpec, ...] = ()
    clears: tuple[ShakeOff, ...] = ()
    waiting: tuple[tuple[str, str], ...] = ()
    unrolled: tuple[str, ...] = ()

    @property
    def empty(self) -> bool:
        return not (self.checks or self.clears or self.waiting or self.unrolled)


def turn_end(char: Character, game_data: GameData) -> TurnEnd:
    """Everything *char*'s turn's end shakes off, in the order it is done.

    The shake-offs first (one check per record: one rung's conditions share it), then
    the recovery checks — and a condition an impression or an Affliction put on is not
    rolled twice (:func:`~.recovery.can_recover` refuses it).
    """
    checks: list[RollSpec] = []
    clears: list[ShakeOff] = []
    waiting: list[tuple[str, str]] = []
    unrolled: list[str] = []
    for shake, _changes in held_shake_offs(char):
        cadence = shake_off_cadence(game_data, shake)
        if cadence == CADENCE_CLEARS:
            clears.append(shake)
        elif cadence != CADENCE_TURN:
            waiting.append((shake.name, cadence))
        else:
            spec = shake_off_recover_spec(char, game_data, shake)
            if spec is None:
                unrolled.append(shake.name)
            else:
                checks.append(replace(spec, hint=SHAKE_OFF_HINT))
    for applied in end_of_turn_recoveries(char, game_data):
        spec = recovery_roll(char, game_data, applied)
        if spec is not None:
            checks.append(spec)
    return TurnEnd(tuple(checks), tuple(clears), tuple(waiting), tuple(unrolled))


def waiting_text(name: str, cadence: str) -> str:
    """The line End turn writes for a check that is not the turn's."""
    when = "once a minute" if cadence == CADENCE_MINUTE else "at the end of a scene"
    return f"{name} is overcome {when}, not as a turn ends."

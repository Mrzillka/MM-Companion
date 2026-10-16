"""Recovery checks: the roll that sheds a condition, and what making it does.

A handful of conditions clear by being *rolled off* — Dazed and Stunned with a DC 10
Stamina check at the end of each turn, a Hit or Staggered once a minute, Dying with a
check that needs two degrees to stabilise. ``conditions.json`` has always carried that
as a structured ``recoveryCheck`` (``docs/mm-conditions-design.md`` §8); nothing rolled
it. This module is the roll layer it was waiting for, and it is all data:

- **which** conditions offer a check — a record with both a ``trait`` and a ``dc``
  (Fatigued's rest has neither and offers nothing), applied **directly** (a bundled
  member goes when its umbrella goes, so Staggered's Dazed is not rolled off on its
  own), and not held back by its ``blockedBy`` list (Staggered recovers only once
  nothing worse is on the creature);
- **what** is rolled — the trait's ordinary roll builder, so the number folds in any
  condition penalty exactly as the sheet's own line does, against the record's DC;
- **what** a made check does — ``successDegrees`` says how good it has to be (Dying
  needs two), and it sheds one instance (a Hit stack peels off one at a time, §5) or
  turns into ``becomes`` (Dying stabilises to Incapacitated).

The cadence (end of turn, once a minute) is left to the table: the app has no turn
clock, and a die the GM clicks when the time comes is the honest version of one.

Nothing here knows who rolls. A spec carries :attr:`~.rolls.RollSpec.recovers`, and
whoever owns the creature it was rolled for applies :func:`apply_recovery` when the
result comes back — the player's own sheet, or the GM's model of an NPC.
"""

from __future__ import annotations

from dataclasses import replace

from ..character import AppliedCondition, Character
from ..data_loader import Condition, GameData, RecoveryCheck
from .conditions import apply_condition, decrement_condition
from .rolls import KIND_ABILITY, KIND_RESISTANCE, RollSpec, ability_roll, resistance_roll

__all__ = [
    "recovery_check",
    "can_recover",
    "recovery_roll",
    "recovery_request",
    "recovery_succeeded",
    "apply_recovery",
    "recovery_hint",
]


def recovery_check(record: Condition | None) -> RecoveryCheck | None:
    """The check *record* is rolled off with, or ``None`` if it is not a check.

    A ``recoveryCheck`` naming no trait or no DC is rest or treatment, not a roll — the
    die would have nothing to throw.
    """
    if record is None or record.recovery_check is None:
        return None
    check = record.recovery_check
    if not check.trait or check.dc is None:
        return None
    return check


def can_recover(char: Character | None, game_data: GameData, applied: AppliedCondition) -> bool:
    """Whether *applied* offers a recovery die on *char* right now.

    Only a directly applied condition (a bundled member leaves with its umbrella), only
    one whose record is a check, and only while nothing in its ``blockedBy`` list is on
    the creature too.
    """
    if applied.provenance is not None:
        return False
    check = recovery_check(game_data.condition_catalog().get(applied.condition_id))
    if check is None:
        return False
    if char is not None and check.blocked_by:
        present = {c.condition_id for c in char.conditions}
        if present & set(check.blocked_by):
            return False
    return True


def _trait_name(game_data: GameData, key: str) -> str:
    ability = next((a for a in game_data.abilities if a.key == key), None)
    if ability is not None:
        return ability.name
    resistance = next((r for r in game_data.resistances if r.key == key), None)
    return resistance.name if resistance is not None else key


def _label(game_data: GameData, applied: AppliedCondition, check: RecoveryCheck) -> str:
    record = game_data.condition_catalog().get(applied.condition_id)
    name = record.name if record is not None else applied.condition_id
    return f"Recover from {name} ({_trait_name(game_data, str(check.trait))})"


def _tag(spec: RollSpec, applied: AppliedCondition, check: RecoveryCheck) -> RollSpec:
    return replace(
        spec,
        dc=int(check.dc),  # type: ignore[arg-type]
        recovers=applied.condition_id,
        recovers_parameter=applied.parameter or "",
    )


def recovery_roll(
    char: Character, game_data: GameData, applied: AppliedCondition
) -> RollSpec | None:
    """The recovery check for *applied* on *char*, ready to roll — or ``None``.

    Built off the trait's own roll builder (an ability, or a resistance if a mod names
    one), so the number is the one the sheet shows, condition penalties and all.
    """
    if not can_recover(char, game_data, applied):
        return None
    check = recovery_check(game_data.condition_catalog().get(applied.condition_id))
    assert check is not None and check.trait is not None
    trait = check.trait
    if any(a.key == trait for a in game_data.abilities):
        spec = ability_roll(char, game_data, trait)
    elif any(r.key == trait for r in game_data.resistances):
        spec = resistance_roll(char, game_data, trait)
    else:
        return None
    # The trait's own hint is the condition penalty folded into its number, if any —
    # kept under the line saying what this check is.
    hint = "\n".join(part for part in (recovery_hint(check), spec.hint) if part)
    spec = replace(spec, label=_label(game_data, applied, check), hint=hint)
    return _tag(spec, applied, check)


def recovery_request(game_data: GameData, applied: AppliedCondition) -> RollSpec | None:
    """The same check as a character-free **request** — for somebody else to roll.

    A template in the shape :func:`~.rolls.localize_spec` fills in: no modifier, the
    trait named by ``trait_key`` and ``kind``, so whoever clicks it rolls with their
    own Stamina. What the GM sends a player whose condition it is.
    """
    record = game_data.condition_catalog().get(applied.condition_id)
    check = recovery_check(record)
    if check is None or applied.provenance is not None or check.trait is None:
        return None
    trait = check.trait
    if any(a.key == trait for a in game_data.abilities):
        kind = KIND_ABILITY
    elif any(r.key == trait for r in game_data.resistances):
        kind = KIND_RESISTANCE
    else:
        return None
    spec = RollSpec(
        label=_label(game_data, applied, check),
        kind=kind,
        trait_key=trait,
        hint=recovery_hint(check),
    )
    return _tag(spec, applied, check)


def recovery_succeeded(game_data: GameData, spec: RollSpec | None, degree: object) -> bool:
    """Whether a rolled recovery check came back good enough to shed its condition."""
    if spec is None or not spec.recovers or not isinstance(degree, int):
        return False
    check = recovery_check(game_data.condition_catalog().get(spec.recovers))
    needed = check.success_degrees if check is not None else 1
    return degree >= needed


def apply_recovery(
    char: Character, game_data: GameData, condition_id: str, parameter: str | None = None
) -> tuple[list[tuple[str, str | None]], list[tuple[str, str | None]]]:
    """Shed what a made recovery check sheds. Returns ``(removed, added)``.

    One instance comes off — a Hit stack loses one — and a condition that *becomes*
    another (Dying stabilising) puts that one on in its place. Nothing happens if the
    condition is no longer there to recover from: the roll took a moment, and the GM
    may have taken it off by hand meanwhile.
    """
    applied = next(
        (
            c
            for c in char.conditions
            if c.condition_id == condition_id and c.parameter == parameter and c.provenance is None
        ),
        None,
    )
    if applied is None:
        return [], []
    check = recovery_check(game_data.condition_catalog().get(condition_id))
    decrement_condition(char, applied)
    removed: list[tuple[str, str | None]] = [(condition_id, parameter)]
    added: list[tuple[str, str | None]] = []
    if check is not None and check.becomes:
        apply_condition(char, check.becomes, game_data)
        added.append((check.becomes, None))
    return removed, added


def recovery_hint(check: RecoveryCheck) -> str:
    """One line on when the check is made and what it takes."""
    when = {
        "end_of_turn": "at the end of each turn",
        "start_of_turn": "at the start of each turn",
        "per_minute": "once a minute",
    }.get(check.cadence, "")
    parts = [f"DC {check.dc}"]
    if when:
        parts.append(when)
    if check.success_degrees > 1:
        parts.append(f"{check.success_degrees}+ degrees of success to clear")
    return "Recovery check — " + ", ".join(parts)

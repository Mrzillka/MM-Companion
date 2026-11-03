"""A single-target attack aimed at someone: did it hit, and was it a critical?

An attack rolled off a power card says nothing about its target — the Defense goes in
the DC box by hand. One aimed at a creature on the Scene (right-click the attack line,
pick the target) is judged here instead, by the GM's app, which can see the target's
Defense: :func:`judge_attack` is the same arithmetic the roller does with a DC set,
plus the minion's routine opposition (PDF p25), and :func:`targetable_attack` says
which roll lines can be aimed at all.

Pure: the GM window's ``ui/attack_gm.py`` runs the rest (asking for the save, landing
it). The area attack's primary judges its hit with the same function.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..dice import resolve_check
from .rolls import KIND_AREA, KIND_IMPRESS, RollSpec

__all__ = ["AttackVerdict", "aimed_attack", "judge_attack", "targetable_attack"]


def targetable_attack(spec: RollSpec) -> bool:
    """Whether a roll line can be aimed at a target: an attack the wielder rolls that
    forces a save. A save line (the target rolls it) and the area line (no roll) are not.
    """

    return spec.follow_up is not None and not spec.rolled_by_target and spec.kind != KIND_AREA


def aimed_attack(spec: RollSpec | None) -> bool:
    """Whether *spec* is an attack already aimed at a target: tied to its declaration by
    an action id and naming whom it is at.

    Its save is the GM's app's to resolve (``ui/attack_gm.py``), so nothing should offer
    the save a second time. An impression's check asked of a player has an action id
    and a follow-up too, but its resist *is* answered off the card; it is no attack.
    """

    if spec is None or not spec.action_id or not spec.targets or not targetable_attack(spec):
        return False
    if spec.kind == KIND_IMPRESS or spec.follow_up_always:
        return False
    return not any(f.dc_from_total for f in spec.follow_ups())


@dataclass(frozen=True)
class AttackVerdict:
    """How an aimed attack went against its target's Defense."""

    hit: bool
    #: A critical hit: a natural 20 that counted (not a minion's, not a routine 10).
    critical: bool
    #: The attack's degree against the Defense DC (the roller's arithmetic).
    degree: int
    #: Hit only because the routine result did, which the attacker may take against a
    #: minion whatever they rolled.
    by_routine: bool = False


def judge_attack(
    modifier: int,
    die: int,
    defense_dc: int,
    *,
    natural: bool = True,
    added_success: bool = True,
    routine_total: int | None = None,
) -> AttackVerdict:
    """Judge an attack of *modifier* that rolled *die* against *defense_dc*.

    A natural 20 that counts adds a degree (which can turn a near miss into a hit) and is
    a critical; a natural 1 takes one away. *added_success* false is a minion's roll,
    *natural* false a routine 10 or a lucky re-roll's lifted die. *routine_total* is what
    a routine check would have come to — given only when the target is a minion and the
    session lets attacks on minions be routine — and a hit by it is a hit.
    """

    result = resolve_check(
        modifier, defense_dc, roll=die, natural=natural, added_success=added_success
    )
    hit = result.degree > 0
    by_routine = False
    if not hit and routine_total is not None and routine_total >= defense_dc:
        hit = by_routine = True
    critical = bool(result.critical and die == 20 and natural and added_success)
    return AttackVerdict(
        hit=hit, critical=critical and hit, degree=result.degree, by_routine=by_routine
    )

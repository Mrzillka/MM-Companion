"""Dice and check resolution for Mutants & Masterminds 4e.

Pure Python — no PySide6, no game *content*. This implements the single core
resolution mechanic described in ``mm-core-mechanics.md`` §1-2, §10: roll a d20,
add a modifier, compare to a difficulty class, and grade the outcome in degrees
of success/failure.

Everything higher-level (attacks, resistances, opposed/team/group checks, the
damage-condition ladder) builds on :func:`resolve_check`.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

D20_SIDES = 20


@dataclass(frozen=True)
class CheckResult:
    """The outcome of a single d20 check.

    ``degree`` is signed: positive counts degrees of success (1 at the DC),
    negative counts degrees of failure (-1 just under the DC). ``critical`` is
    true when the die itself came up a natural 1 or 20, regardless of whether the
    check hit — callers decide what a crit means in context (e.g. an attack).
    """

    die_roll: int
    modifier: int
    total: int
    dc: int
    degree: int
    critical: bool

    @property
    def success(self) -> bool:
        return self.degree > 0


def roll_d20(rng: random.Random | None = None) -> int:
    """Roll a single d20 (1-20). Pass an ``rng`` to make the roll reproducible."""

    roller = rng if rng is not None else random
    return roller.randint(1, D20_SIDES)


def degrees(margin: int) -> int:
    """Convert a ``check_total - dc`` margin into a signed degree count.

    +1 at margin 0-4, +2 at 5-9, ...; -1 at -1..-5, -2 at -6..-10, ...
    (``mm-core-mechanics.md`` §2). Does not apply the natural 1/20 adjustment;
    :func:`resolve_check` layers that on top.
    """

    if margin >= 0:
        return 1 + margin // 5
    return -(1 + (-margin - 1) // 5)


def resolve_check(
    modifier: int,
    dc: int,
    *,
    graded: bool = True,
    roll: int | None = None,
    rng: random.Random | None = None,
) -> CheckResult:
    """Resolve ``d20 + modifier`` against ``dc``.

    ``roll`` forces a specific die result instead of rolling — pass ``10`` for a
    routine check, or a fixed value in tests. ``rng`` seeds the d20 when ``roll``
    is not given.

    When ``graded`` is true the result carries degrees of success/failure and the
    natural-20/1 adjustment (a nat 20 adds a degree, a nat 1 subtracts one — which
    can flip the outcome). When false, ``degree`` is simply +1 (hit) or -1 (miss)
    and no crit adjustment is applied.
    """

    die = roll if roll is not None else roll_d20(rng)
    total = die + modifier
    margin = total - dc
    critical = die in (1, D20_SIDES)

    if not graded:
        return CheckResult(die, modifier, total, dc, 1 if margin >= 0 else -1, critical)

    degree = degrees(margin)
    # The degree ladder has no zero step (... -2, -1, +1, +2 ...), so a natural
    # 20 that improves a 1-degree failure lands on a 1-degree success (and a
    # natural 1 drags a 1-degree success to a 1-degree failure), rather than the
    # nonexistent 0 the raw +/- 1 in the §10 pseudocode would produce.
    if die == D20_SIDES:
        degree = 1 if degree == -1 else degree + 1
    elif die == 1:
        degree = -1 if degree == 1 else degree - 1
    return CheckResult(die, modifier, total, dc, degree, critical)

"""Team checks and group checks (PDF p18): several characters making one check together.

* A **team check**: one leader, the rest helpers. Each helper rolls against
  ``helper_dc`` (10), and every degree of success the helpers score adds
  ``bonus_per_degree`` (+1) to the leader's check, which is the one that counts.
* A **group check**: everyone rolls the same check. If at least ``success_share`` (half)
  of them succeed, they all succeed at the lowest degree of success among them; then the
  added successes and failures (natural 20s and 1s) are counted on top, which can move
  the whole group across success and failure. The book does not say where a group that
  fails lands; this reads it as the least severe failure among them, the mirror image.

The numbers are data (``system.json``'s ``teamCheck`` and ``groupCheck``,
:class:`~..data_loader.GroupCheckRules`). The GM's window that runs either for a group
of NPCs is ``ui/group_check.py``.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..data_loader import GameData
from ..dice import degrees

__all__ = [
    "GroupCheckMember",
    "GroupCheckResult",
    "group_check_result",
    "shift_degree",
    "team_check_bonus",
]


def team_check_bonus(helper_degrees: list[int], game_data: GameData) -> int:
    """What the helpers' degrees (as rolled against ``helper_dc``) add to the leader.

    Only degrees of *success* count; a helper who failed adds nothing (and takes
    nothing away).
    """

    per = game_data.system.group_checks.bonus_per_degree
    return per * sum(d for d in helper_degrees if d > 0)


def shift_degree(degree: int, steps: int) -> int:
    """*degree* moved *steps* along the degree ladder, which has no zero (… -1, +1 …)."""

    for _ in range(abs(steps)):
        if steps > 0:
            degree = 1 if degree == -1 else degree + 1
        else:
            degree = -1 if degree == 1 else degree - 1
    return degree


@dataclass(frozen=True)
class GroupCheckMember:
    """One member's roll in a group check, before natural 20s and 1s are counted."""

    name: str
    total: int
    #: +1 for a natural 20 that counts, -1 for a natural 1, 0 otherwise.
    added: int = 0


@dataclass(frozen=True)
class GroupCheckResult:
    """What a group check came to — the one degree the whole group lands on."""

    degree: int
    #: How many succeeded on their own roll, out of how many rolled.
    successes: int
    count: int
    #: The degree before the added successes and failures were counted.
    base_degree: int
    #: Their sum: the natural 20s less the natural 1s.
    added: int

    @property
    def success(self) -> bool:
        return self.degree > 0


def group_check_result(
    members: list[GroupCheckMember], dc: int, game_data: GameData
) -> GroupCheckResult | None:
    """The group's degree on a check against *dc* (``None`` with nobody rolling)."""

    if not members:
        return None
    raw = [degrees(m.total - dc) for m in members]
    passed = [d for d in raw if d > 0]
    share = game_data.system.group_checks.success_share
    if len(passed) >= share * len(members) and passed:
        base = min(passed)
    else:
        base = max(d for d in raw if d < 0) if any(d < 0 for d in raw) else -1
    added = sum(m.added for m in members)
    return GroupCheckResult(
        degree=shift_degree(base, added),
        successes=len(passed),
        count=len(members),
        base_degree=base,
        added=added,
    )

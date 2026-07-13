"""Advantage caps, the shared Heroic budget, and advantage-limit validation."""

from __future__ import annotations

import math

from ..character import Character
from ..data_loader import GameData

# The category tag whose members share one Power-Level-derived rank budget.
HEROIC_TYPE = "Heroic"


def advantage_by_name(game_data: GameData, name: str):
    """The :class:`~.data_loader.Advantage` content record for a chosen name, or ``None``."""

    return next((a for a in game_data.advantages if a.name == name), None)


def advantage_rank_cap(advantage, power_level: int) -> int | None:
    """The standalone rank cap for one advantage at a Power Level, or ``None`` if uncapped.

    Reads the advantage's ``max_rank_kind`` (``docs/mm-advantages-design.md`` §3), so the
    cap style is data-driven rather than hardcoded:

    - ``"fixed"`` — the number the rules give (``max_rank``): Improved Critical 4, etc.
    - ``"power_level_half"`` — Improved Initiative's own ``ceil(power_level / 2)``.

    The other kinds carry no standalone number: ``"power_level"`` advantages share a
    Power-Level trait cap with the character's base ranks (checked elsewhere),
    ``"heroic_budget"`` advantages draw from the shared pool
    (:func:`heroic_advantage_budget`), and ``"none"`` is uncapped. All three return
    ``None`` here. An unranked advantage is always a single rank.
    """

    if not advantage.ranked:
        return 1
    kind = advantage.max_rank_kind
    if kind == "fixed":
        return advantage.max_rank
    if kind == "power_level_half":
        return math.ceil(power_level / 2)
    return None


def heroic_advantage_budget(power_level: int, divisor: int = 2) -> int:
    """Total ranks available across all Heroic-type advantages: ``power_level // divisor``.

    One shared pool for every Heroic advantage on the sheet (``docs/mm-advantages-design.md``
    §3.4), not a per-advantage cap. The divisor (2 in the core rules) comes from
    ``system.json`` (``heroic_budget_divisor``) at the resolver's call sites; it defaults
    here so the bare ``heroic_advantage_budget(power_level)`` form keeps working.
    """

    return power_level // divisor


def heroic_advantage_ranks(char: Character, game_data: GameData) -> int:
    """Ranks the character currently draws from the shared Heroic-advantage budget.

    A ranked Heroic advantage spends its rank; an unranked one spends a flat 1. Non-
    Heroic advantages don't touch the pool.
    """

    total = 0
    for selection in char.advantages:
        advantage = advantage_by_name(game_data, selection.name)
        if advantage and HEROIC_TYPE in advantage.types:
            total += selection.rank if advantage.ranked else 1
    return total


def advantage_violations(char: Character, game_data: GameData) -> list[str]:
    """Advantage limit breaches in the current build; an empty list means it is valid.

    Two limits (``docs/mm-advantages-design.md`` §3): each ranked advantage against its own
    cap (:func:`advantage_rank_cap` — the fixed numbers and Improved Initiative's
    ``ceil(PL/2)``), and the shared Heroic-advantage budget
    (:func:`heroic_advantage_budget`). The Power-Level *shared* trait caps for
    ``"power_level"`` advantages are folded into the trait totals checked by
    :func:`power_level_violations`, so they aren't repeated here.
    """

    pl = char.power_level
    violations: list[str] = []
    for selection in char.advantages:
        advantage = advantage_by_name(game_data, selection.name)
        if advantage is None:
            continue
        cap = advantage_rank_cap(advantage, pl)
        if cap is not None and selection.rank > cap:
            violations.append(f"{advantage.name} rank {selection.rank} exceeds its cap of {cap}.")

    budget = heroic_advantage_budget(pl, game_data.system.heroic_budget_divisor)
    used = heroic_advantage_ranks(char, game_data)
    if used > budget:
        violations.append(
            f"Heroic advantages use {used} ranks, exceeding the PL {pl} budget of {budget}."
        )
    return violations

"""Point-spending totals and Power-Level/points reconciliation."""

from __future__ import annotations

import math
from fractions import Fraction

from ..character import Character
from ..data_loader import GameData
from .derived import _skill_for_row
from .powers_cost import node_cost


def ability_points_spent(char: Character, game_data: GameData) -> int:
    """Power points spent on abilities and combat stats (``docs/mm-core-mechanics.md`` §7).

    Each ability costs per rank; the ``derived`` combat stats (Attack) cost at the
    combat rate. Negative ranks refund points.
    """

    costs = game_data.costs.traits
    total = 0
    for ability in game_data.abilities:
        rank = char.abilities.get(ability.key, 0)
        rate = costs.combat_per_rank if ability.derived else costs.ability_per_rank
        total += rank * rate
    return total


def resistance_points_spent(char: Character, game_data: GameData) -> int:
    """Power points spent on resistances (``docs/mm-core-mechanics.md`` §7).

    Only the ranks *bought above the derived base* cost points (that delta is what
    the model stores); non-derived resistances cost per rank, the ``derived`` combat
    Defense at the combat rate. Ranks bought below the base refund points.
    """

    costs = game_data.costs.traits
    total = 0
    for res in game_data.resistances:
        bought = char.resistances.get(res.key, 0)
        rate = costs.combat_per_rank if res.derived else costs.resistance_per_rank
        total += bought * rate
    return total


def _specialized_row_ids(char: Character) -> set[str]:
    """Row ids of every specialized (narrow, half-cost) skill pool on the character.

    A specialization is stored as a distinct row id ``"<Skill>::spec::<name>"`` whose
    ranks live in ``skill_ranks`` like any other row; this set is what tells the cost
    math to charge those ranks at the specialized rate.
    """

    return {
        f"{skill}::spec::{name}" for skill, names in char.specializations.items() for name in names
    }


def skill_points_spent(char: Character, game_data: GameData) -> int:
    """Power points spent on skills, pooled across every skill (``docs/mm-skills-design.md`` §4/§7).

    All skill ranks share one purchase pool that rounds *once*, so 1 rank in four
    skills costs 2 PP, not 4. Ordinary ranks (most focused skills included) cost
    ``skill_ranks_per_pp`` ranks per point; ranks in a specialized narrow pool — or in a
    skill flagged ``specialized_cost`` (Expertise, whose mandatory focus is inherently
    priced that way) — cost the cheaper ``skill_specialized_ranks_per_pp``. The two
    fractional costs are summed and the total ceiled, so mixed builds round together
    rather than per row.
    """

    costs = game_data.costs.traits
    specialized = _specialized_row_ids(char)
    normal_ranks = 0
    specialized_ranks = 0
    for row_id, ranks in char.skill_ranks.items():
        if ranks <= 0:
            continue
        skill = _skill_for_row(game_data, row_id)
        if row_id in specialized or (skill is not None and skill.specialized_cost):
            specialized_ranks += ranks
        else:
            normal_ranks += ranks
    total = Fraction(normal_ranks, costs.skill_ranks_per_pp) + Fraction(
        specialized_ranks, costs.skill_specialized_ranks_per_pp
    )
    return math.ceil(total)


def advantage_points_spent(char: Character, game_data: GameData) -> int:
    """Power points spent on advantages: the advantage rate per rank."""

    rate = game_data.costs.traits.advantage_per_rank
    return sum(adv.rank * rate for adv in char.advantages)


def powers_points_spent(char: Character, game_data: GameData) -> int:
    """Power points spent on powers: the cost of every top-level node in the tree.

    A character's powers form a tree of :data:`~mm_companion.core.powers.PowerNode`
    (leaf powers and :class:`~mm_companion.core.powers.PowerGroup` containers); each
    top-level node is priced by :func:`node_cost`, which folds in array pooling (a
    group's alternates contribute only a flat point) recursively through any nesting.
    """

    return sum(node_cost(node, game_data, char) for node in char.powers)


def power_points_spent(char: Character, game_data: GameData) -> int:
    """Total power points the character's current build costs (``docs/mm-core-mechanics.md`` §7).

    The sum of the per-category costs — abilities/combat stats, resistances, skills,
    advantages, and powers — each of which is also available on its own for the
    per-section totals the sheet shows.
    """

    return (
        ability_points_spent(char, game_data)
        + resistance_points_spent(char, game_data)
        + skill_points_spent(char, game_data)
        + advantage_points_spent(char, game_data)
        + powers_points_spent(char, game_data)
    )


def power_points_remaining(char: Character, game_data: GameData) -> int:
    """Unspent power points (budget minus :func:`power_points_spent`; may go negative)."""

    return char.power_points_total - power_points_spent(char, game_data)


def min_power_points(power_level: int, game_data: GameData) -> int:
    """The minimum power-point budget a Power Level requires: ``PL × pp_per_level``.

    A character's Power Level sets the floor on their point budget — a PL 10 hero is
    built on at least 150 points at 15 points per level (``docs/mm-core-mechanics.md`` §7).
    Data-driven: the per-level rate comes from ``costs.json``, never hardcoded here.
    """

    return power_level * game_data.costs.power_level.pp_per_level


def power_level_for_points(power_points: int, game_data: GameData) -> int:
    """The Power Level a point budget affords: ``floor(power_points / pp_per_level)``.

    Every further ``pp_per_level`` points crosses into the next Power Level band, so a
    budget raised past a level's border raises the Power Level to match. Guards a
    non-positive ``pp_per_level`` by returning 0.
    """

    per_level = game_data.costs.power_level.pp_per_level
    if per_level <= 0:
        return 0
    return power_points // per_level


def reconcile_points_to_level(power_level: int, power_points: int, game_data: GameData) -> int:
    """Point budget after a Power Level change: snap it to the level's band minimum.

    Keeps the two linked so :func:`power_level_for_points` of the result equals
    ``power_level``. A budget already inside the level's band is left untouched (so a
    character can carry extra points within a level); one below the minimum, or up in
    a higher band, snaps to :func:`min_power_points`.
    """

    if power_level_for_points(power_points, game_data) != power_level:
        return min_power_points(power_level, game_data)
    return power_points

"""Point-spending totals and Power-Level/points reconciliation."""

from __future__ import annotations

import math
from dataclasses import fields, replace
from fractions import Fraction

from ..character import Character
from ..data_loader import Ability, GameData, Resistance, Skill, TraitCosts
from .derived import _skill_for_row
from .powers_cost import node_cost

#: The three per-item override categories keyed on ``Character.item_cost_overrides``.
ABILITIES_CATEGORY = "abilities"
RESISTANCES_CATEGORY = "resistances"
SKILLS_CATEGORY = "skills"


def effective_trait_costs(char: Character, game_data: GameData) -> TraitCosts:
    """The trait-cost rates for a character, with any homebrew overrides applied.

    Reads ``char.cost_overrides`` (see :attr:`Character.cost_overrides`) and layers the
    per-rank / ranks-per-point rates the player has changed over the ruleset defaults
    from ``costs.json``. Overrides for non-``TraitCosts`` keys (e.g. ``pp_per_level``)
    are ignored here — see :func:`effective_pp_per_level`.
    """

    names = {f.name for f in fields(TraitCosts)}
    changes = {k: int(v) for k, v in char.cost_overrides.items() if k in names}
    if not changes:
        return game_data.costs.traits
    return replace(game_data.costs.traits, **changes)


def effective_pp_per_level(char: Character, game_data: GameData) -> int:
    """The power-points-per-Power-Level budget rate, with any homebrew override applied."""

    return int(char.cost_overrides.get("pp_per_level", game_data.costs.power_level.pp_per_level))


def ability_category_key(ability: Ability) -> str:
    """The ``TraitCosts`` rate field an ability's cost draws from (combat vs. core)."""

    return "combat_per_rank" if ability.derived else "ability_per_rank"


def resistance_category_key(resistance: Resistance) -> str:
    """The ``TraitCosts`` rate field a resistance's cost draws from (combat vs. core)."""

    return "combat_per_rank" if resistance.derived else "resistance_per_rank"


def skill_category_key(skill: Skill) -> str:
    """The ``TraitCosts`` ranks-per-PP field a skill's cost draws from (specialized vs. normal)."""

    return "skill_specialized_ranks_per_pp" if skill.specialized_cost else "skill_ranks_per_pp"


def ability_cost_rate(char: Character, game_data: GameData, ability: Ability) -> int:
    """The PP-per-rank an ability costs: a per-item homebrew override, else the category rate."""

    override = char.item_cost_overrides.get(ABILITIES_CATEGORY, {}).get(ability.key)
    if override is not None:
        return int(override)
    return getattr(effective_trait_costs(char, game_data), ability_category_key(ability))


def resistance_cost_rate(char: Character, game_data: GameData, resistance: Resistance) -> int:
    """The PP-per-rank a resistance costs: a per-item homebrew override, else the category rate."""

    override = char.item_cost_overrides.get(RESISTANCES_CATEGORY, {}).get(resistance.key)
    if override is not None:
        return int(override)
    return getattr(effective_trait_costs(char, game_data), resistance_category_key(resistance))


def skill_cost_rate(char: Character, game_data: GameData, skill: Skill) -> int:
    """The ranks-per-PP a skill costs: a per-item homebrew override, else the category rate."""

    override = char.item_cost_overrides.get(SKILLS_CATEGORY, {}).get(skill.name)
    if override is not None:
        return int(override)
    return getattr(effective_trait_costs(char, game_data), skill_category_key(skill))


def _item_overrides_differ(char: Character, game_data: GameData) -> bool:
    """True when any stored per-item rate differs from its ruleset category default."""

    traits = game_data.costs.traits
    abilities = char.item_cost_overrides.get(ABILITIES_CATEGORY, {})
    for ability in game_data.abilities:
        value = abilities.get(ability.key)
        if value is not None and int(value) != getattr(traits, ability_category_key(ability)):
            return True
    resistances = char.item_cost_overrides.get(RESISTANCES_CATEGORY, {})
    for resistance in game_data.resistances:
        value = resistances.get(resistance.key)
        if value is not None and int(value) != getattr(traits, resistance_category_key(resistance)):
            return True
    skills = char.item_cost_overrides.get(SKILLS_CATEGORY, {})
    for skill in game_data.skills:
        value = skills.get(skill.name)
        if value is not None and int(value) != getattr(traits, skill_category_key(skill)):
            return True
    return False


def has_cost_overrides(char: Character, game_data: GameData) -> bool:
    """True when the character homebrews any non-power PP-cost rate away from default.

    The single derived predicate the sheet's "homebrew PP cost" notice reads (mirrors
    :func:`~mm_companion.core.rules.power_is_homerule`). Compares each stored override —
    both the global category rates and any per-item rate — to its ruleset default, so a
    rate stored but equal to default reads as no override.
    """

    traits = game_data.costs.traits
    for key, value in char.cost_overrides.items():
        if key == "pp_per_level":
            default: int = game_data.costs.power_level.pp_per_level
        else:
            default = getattr(traits, key, None)
        if default is not None and int(value) != default:
            return True
    return _item_overrides_differ(char, game_data)


def ability_points_spent(char: Character, game_data: GameData) -> int:
    """Power points spent on abilities and combat stats (``docs/mm-core-mechanics.md`` §7).

    Each ability costs per rank; the ``derived`` combat stats (Attack) cost at the
    combat rate. Negative ranks refund points.
    """

    total = 0
    for ability in game_data.abilities:
        rank = char.abilities.get(ability.key, 0)
        total += rank * ability_cost_rate(char, game_data, ability)
    return total


def resistance_points_spent(char: Character, game_data: GameData) -> int:
    """Power points spent on resistances (``docs/mm-core-mechanics.md`` §7).

    Only the ranks *bought above the derived base* cost points (that delta is what
    the model stores); non-derived resistances cost per rank, the ``derived`` combat
    Defense at the combat rate. Ranks bought below the base refund points.
    """

    total = 0
    for res in game_data.resistances:
        bought = char.resistances.get(res.key, 0)
        total += bought * resistance_cost_rate(char, game_data, res)
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

    costs = effective_trait_costs(char, game_data)
    overrides = char.item_cost_overrides.get(SKILLS_CATEGORY, {})
    specialized = _specialized_row_ids(char)
    total = Fraction(0)
    for row_id, ranks in char.skill_ranks.items():
        if ranks <= 0:
            continue
        skill = _skill_for_row(game_data, row_id)
        if skill is not None and skill.name in overrides:
            # A per-skill homebrew rate prices every rank of that skill, spec pools included.
            rate = int(overrides[skill.name])
        elif row_id in specialized or (skill is not None and skill.specialized_cost):
            rate = costs.skill_specialized_ranks_per_pp
        else:
            rate = costs.skill_ranks_per_pp
        total += Fraction(ranks, rate)
    return math.ceil(total)


def advantage_points_spent(char: Character, game_data: GameData) -> int:
    """Power points spent on advantages: the advantage rate per rank."""

    rate = effective_trait_costs(char, game_data).advantage_per_rank
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


def min_power_points(power_level: int, game_data: GameData, char: Character | None = None) -> int:
    """The minimum power-point budget a Power Level requires: ``PL × pp_per_level``.

    A character's Power Level sets the floor on their point budget — a PL 10 hero is
    built on at least 150 points at 15 points per level (``docs/mm-core-mechanics.md`` §7).
    Data-driven: the per-level rate comes from ``costs.json`` (or the character's
    homebrew override when ``char`` is given), never hardcoded here.
    """

    per_level = (
        effective_pp_per_level(char, game_data)
        if char is not None
        else game_data.costs.power_level.pp_per_level
    )
    return power_level * per_level


def power_level_for_points(
    power_points: int, game_data: GameData, char: Character | None = None
) -> int:
    """The Power Level a point budget affords: ``floor(power_points / pp_per_level)``.

    Every further ``pp_per_level`` points crosses into the next Power Level band, so a
    budget raised past a level's border raises the Power Level to match. Uses the
    character's homebrew per-level rate when ``char`` is given. Guards a non-positive
    ``pp_per_level`` by returning 0.
    """

    per_level = (
        effective_pp_per_level(char, game_data)
        if char is not None
        else game_data.costs.power_level.pp_per_level
    )
    if per_level <= 0:
        return 0
    return power_points // per_level


def reconcile_points_to_level(
    power_level: int, power_points: int, game_data: GameData, char: Character | None = None
) -> int:
    """Point budget after a Power Level change: snap it to the level's band minimum.

    Keeps the two linked so :func:`power_level_for_points` of the result equals
    ``power_level``. A budget already inside the level's band is left untouched (so a
    character can carry extra points within a level); one below the minimum, or up in
    a higher band, snaps to :func:`min_power_points`. Honours the character's homebrew
    per-level rate when ``char`` is given.
    """

    if power_level_for_points(power_points, game_data, char) != power_level:
        return min_power_points(power_level, game_data, char)
    return power_points

"""What one rank of any trait costs — the purchase rates every cost path prices against.

Split out of :mod:`.costs` so that both the *bought* traits on the sheet and the
*enhanced* ones a power grants read one set of rates. An Enhanced Trait costs "as
trait" (``docs/mm-powers-architecture.md`` §6), so :mod:`.powers_cost` has to reach
these too, and it sits below ``costs`` in the dependency DAG — hence a module of
their own rather than an import back up the chain.

Rates are data (``costs.json``) layered with the character's homebrew overrides, and
they come in two shapes the rest of the engine has to keep straight: abilities,
resistances and advantages cost *points per rank*, while skills cost *ranks per
point*. :func:`trait_rate` reconciles the two into a single ``Fraction`` of points
per rank, which is what lets a mixed allocation be summed exactly and rounded once
(see :func:`trait_allocation_cost`).
"""

from __future__ import annotations

from dataclasses import fields, replace
from fractions import Fraction

from ..character import Character
from ..data_loader import Ability, GameData, Resistance, Skill, TraitCosts
from .advantages import advantage_by_name
from .appliers import (
    SPECIALIZED_ROW_MARKER,
    skill_for_row,
    split_trait_key,
    trait_key_candidates,
)

#: The three per-item override categories keyed on ``Character.item_cost_overrides``.
ABILITIES_CATEGORY = "abilities"
RESISTANCES_CATEGORY = "resistances"
SKILLS_CATEGORY = "skills"


def effective_trait_costs(char: Character | None, game_data: GameData) -> TraitCosts:
    """The trait-cost rates for a character, with any homebrew overrides applied.

    Reads ``char.cost_overrides`` (see :attr:`Character.cost_overrides`) and layers the
    per-rank / ranks-per-point rates the player has changed over the ruleset defaults
    from ``costs.json``. Overrides for non-``TraitCosts`` keys (e.g. ``pp_per_level``)
    are ignored here — see :func:`effective_pp_per_level`.

    ``char`` may be ``None``, which reads the plain ruleset rates: the powers cost path
    prices an effect with or without a character in hand, and a power's price must not
    depend on having one.
    """

    if char is None:
        return game_data.costs.traits
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


def _item_override(char: Character | None, category: str, key: str) -> int | None:
    """A per-item homebrew rate for one trait, or ``None`` when it has none."""

    if char is None:
        return None
    value = char.item_cost_overrides.get(category, {}).get(key)
    return None if value is None else int(value)


def ability_cost_rate(char: Character | None, game_data: GameData, ability: Ability) -> int:
    """The PP-per-rank an ability costs: a per-item homebrew override, else the category rate."""

    override = _item_override(char, ABILITIES_CATEGORY, ability.key)
    if override is not None:
        return override
    return getattr(effective_trait_costs(char, game_data), ability_category_key(ability))


def resistance_cost_rate(
    char: Character | None, game_data: GameData, resistance: Resistance
) -> int:
    """The PP-per-rank a resistance costs: a per-item homebrew override, else the category rate."""

    override = _item_override(char, RESISTANCES_CATEGORY, resistance.key)
    if override is not None:
        return override
    return getattr(effective_trait_costs(char, game_data), resistance_category_key(resistance))


def skill_cost_rate(char: Character | None, game_data: GameData, skill: Skill) -> int:
    """The ranks-per-PP a skill costs: a per-item homebrew override, else the category rate."""

    override = _item_override(char, SKILLS_CATEGORY, skill.name)
    if override is not None:
        return override
    return getattr(effective_trait_costs(char, game_data), skill_category_key(skill))


def skill_row_rate(char: Character | None, game_data: GameData, row_id: str) -> int:
    """The ranks-per-PP one *skill row* costs — a focus or specialized pool included.

    The single statement of M&M's skill pricing, read by both sides of the sheet:
    :func:`~.costs.skill_points_spent` for the ranks the character bought and
    :func:`trait_rate` for the ranks a power grants. They were two rules until an
    Enhanced Trait could name a row, and two rules would have priced the same
    ``"Stealth::spec::Urban"`` differently depending on who paid for it.

    Precedence, highest first: a per-skill homebrew override (keyed by the *base* skill,
    so it prices every row of that skill alike), then the cheaper specialized rate for a
    ``spec::`` pool or a skill flagged ``specialized_cost``, then the ordinary rate.
    Ordinary is deliberately where a plain *focus* lands (``docs/mm-skills-design.md``).

    The ``spec::`` marker is read off the row id rather than looked up in
    :attr:`Character.specializations` so a power can be priced with no character in hand
    — and so an orphaned row (a pool removed from the sheet while ranks remained) is
    still charged at the rate it was bought at.
    """

    skill = skill_for_row(game_data, row_id)
    if skill is not None:
        override = _item_override(char, SKILLS_CATEGORY, skill.name)
        if override is not None:
            return override
    costs = effective_trait_costs(char, game_data)
    if SPECIALIZED_ROW_MARKER in split_trait_key(row_id)[1] or (
        skill is not None and skill.specialized_cost
    ):
        return costs.skill_specialized_ranks_per_pp
    return costs.skill_ranks_per_pp


def trait_rank_cap(char: Character | None, game_data: GameData, target: str) -> int | None:
    """The most ranks of ``target`` one allocation row may sensibly hold, or ``None``.

    ``1`` for an advantage the rules do not rank at all, its own ``max_rank`` for one
    capped at a fixed number, and ``None`` — no cap of its own — for everything else:
    abilities, resistances and skills are bounded by the Power Level rather than by a
    per-trait ceiling, as are the advantages whose cap is drawn from the PL or the shared
    Heroic budget (neither is a property of the trait, and both are checked against the
    whole build elsewhere).

    Asked here so the constructor's rank spin and
    :func:`~.validation.power_trait_allocation_violations` cannot disagree about what the
    ceiling is.
    """

    advantage = advantage_by_name(game_data, split_trait_key(target)[0])
    if advantage is None:
        return None
    if not advantage.ranked:
        return 1
    return advantage.max_rank if advantage.max_rank_kind == "fixed" else None


def advantage_cost_rate(char: Character | None, game_data: GameData) -> int:
    """The PP-per-rank an advantage costs.

    Advantages have no per-item override category of their own — the cost dialog
    homebrews the one ``advantage_per_rank`` rate for all of them.
    """

    return effective_trait_costs(char, game_data).advantage_per_rank


def trait_rate(char: Character | None, game_data: GameData, target: str) -> Fraction | None:
    """Power Points one rank of ``target`` costs, whatever kind of trait it names.

    Resolved in the same order as :func:`~.appliers.trait_category` — ability key,
    resistance key, skill name, advantage name — so the thing that decides *which list*
    a target lands in and the thing that decides *what it costs* can never disagree.
    ``None`` for a target naming nothing the sheet buys.

    A ``Fraction`` rather than an int because skills are priced the other way up: two
    ranks per point is ``Fraction(1, 2)`` per rank, and it is exactly that fraction
    surviving unrounded into :func:`trait_allocation_cost` that lets one point buy a
    rank in each of two different skills.

    A *qualified* key (:func:`~.appliers.split_trait_key`) is tried whole and then by its
    base, so ``"Expertise::Law"`` is a skill and ``"Improved Critical::Sword"`` an
    advantage. A qualified skill is then priced by :func:`skill_row_rate` against the
    whole row, which is what makes a specialized pool cheaper than its parent skill.
    """

    if not target:
        return None
    for key in trait_key_candidates(target):
        ability = next((a for a in game_data.abilities if a.key == key), None)
        if ability is not None:
            return Fraction(ability_cost_rate(char, game_data, ability))
        resistance = next((r for r in game_data.resistances if r.key == key), None)
        if resistance is not None:
            return Fraction(resistance_cost_rate(char, game_data, resistance))
        if any(s.name == key for s in game_data.skills):
            # Priced against the *row*, not the base skill: a specialized pool costs the
            # cheaper rate whether its ranks were bought or granted.
            per_point = skill_row_rate(char, game_data, target)
            # A homebrew rate of zero ranks per point would divide by zero; read it as
            # free rather than crashing the sheet on a nonsense number.
            return Fraction(1, per_point) if per_point > 0 else Fraction(0)
        if advantage_by_name(game_data, key) is not None:
            return Fraction(advantage_cost_rate(char, game_data))
    return None


def trait_allocation_cost(
    rows: tuple[tuple[str, int], ...], char: Character | None, game_data: GameData
) -> Fraction:
    """The unrounded Power-Point cost of a ``(trait, ranks)`` allocation.

    Every row is priced at its own :func:`trait_rate` and the fractions are summed
    *without rounding*, exactly as :func:`~.costs.skill_points_spent` pools the bought
    skills. Rounding once at the end is what makes 1 rank of Stealth and 1 rank of
    Treatment cost 1 point together rather than 1 point each, and it is the caller's
    job — this returns the ``Fraction`` so a modifier can still scale it first.

    Rows naming a trait the sheet does not buy contribute nothing.
    """

    total = Fraction(0)
    for target, ranks in rows:
        rate = trait_rate(char, game_data, target)
        if rate is None or not ranks:
            continue
        total += rate * int(ranks)
    return total

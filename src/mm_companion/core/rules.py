"""Character math and validation — the build-time rules engine.

Pure functions over a :class:`~.character.Character` plus the :class:`~.data_loader.GameData`
content. No PySide6, no widget state: this is where derived values (skill totals,
resistances, defense class), point-cost accounting, and Power Level validation
live, so the UI can stop computing rules itself.

Everything is data-driven — costs and caps come from ``game_data.costs`` and the
trait lists, never hardcoded here.
"""

from __future__ import annotations

import math

from .character import Character
from .data_loader import GameData, Modifier, Resistance, Skill
from .powers import Power, PowerEffectInstance


def _skill_for_row(game_data: GameData, row_id: str) -> Skill | None:
    """Resolve a skill *row id* to its :class:`Skill` record.

    A row id is either a skill name (non-focused) or ``"<Skill>: <focus>"`` for a
    focused instance; both map back to the same base skill.
    """

    by_name = {s.name: s for s in game_data.skills}
    if row_id in by_name:
        return by_name[row_id]
    base = row_id.split(":", 1)[0].strip()
    return by_name.get(base)


def _resistance(game_data: GameData, key: str) -> Resistance | None:
    for res in game_data.resistances:
        if res.key == key:
            return res
    return None


def skill_total(char: Character, game_data: GameData, row_id: str) -> int:
    """``ability value + skill ranks + situational modifier`` for one skill row."""

    skill = _skill_for_row(game_data, row_id)
    ability_key = skill.ability if skill else ""
    ability_value = char.abilities.get(ability_key, 0)
    return ability_value + char.skill_ranks.get(row_id, 0) + char.skill_mods.get(row_id, 0)


def resistance_total(char: Character, game_data: GameData, key: str) -> int:
    """A resistance's total: its linked ability value plus the ranks bought in it.

    Derived resistances with no linked ability (e.g. Defence) are just their
    bought ranks.
    """

    res = _resistance(game_data, key)
    bought = char.resistances.get(key, 0)
    ability_value = char.abilities.get(res.ability, 0) if res and res.ability else 0
    return ability_value + bought


def defense_class(char: Character, game_data: GameData, key: str = "DEF") -> int:
    """The DC an attacker must beat: ``10 + defense rank`` (``mm-core-mechanics.md`` §5)."""

    return 10 + resistance_total(char, game_data, key)


def initiative(char: Character, bonus: int = 0) -> int:
    """Initiative modifier: Agility rank plus any misc bonus (``mm-core-mechanics.md`` §8)."""

    return char.abilities.get("AGL", 0) + bonus


def power_points_spent(char: Character, game_data: GameData) -> int:
    """Total power points the character's current build costs (``mm-core-mechanics.md`` §6).

    Abilities and combat stats cost per rank (combat stats — the ``derived`` ones —
    at the combat rate); non-derived resistances cost per rank; skills cost 1 PP
    per N ranks (a higher N for focused skills); advantages cost per rank. Negative
    ability/combat ranks refund points.
    """

    costs = game_data.costs.traits
    total = 0

    for ability in game_data.abilities:
        rank = char.abilities.get(ability.key, 0)
        rate = costs.combat_per_rank if ability.derived else costs.ability_per_rank
        total += rank * rate

    for res in game_data.resistances:
        bought = char.resistances.get(res.key, 0)
        rate = costs.combat_per_rank if res.derived else costs.resistance_per_rank
        total += bought * rate

    for row_id, ranks in char.skill_ranks.items():
        if ranks <= 0:
            continue
        skill = _skill_for_row(game_data, row_id)
        focused = bool(skill and skill.focused)
        per_pp = costs.skill_focus_ranks_per_pp if focused else costs.skill_ranks_per_pp
        total += math.ceil(ranks / per_pp)

    for adv in char.advantages:
        total += adv.rank * costs.advantage_per_rank

    return total


def power_points_remaining(char: Character, game_data: GameData) -> int:
    """Unspent power points (budget minus :func:`power_points_spent`; may go negative)."""

    return char.power_points_total - power_points_spent(char, game_data)


def _signed_modifier_cost(mods: list, sign: int, game_data: GameData, *, flat: bool) -> int:
    """Sum the ``cost_value`` of the given modifier selections in one bucket.

    ``sign`` is ``+1`` for extras and ``-1`` for flaws; ``flat`` selects either the
    per-rank bucket (``flat=False``) or the one-time bucket (``flat=True``).
    """

    catalog: dict[str, Modifier] = {m.id: m for m in game_data.modifiers}
    total = 0
    for selection in mods:
        modifier = catalog.get(selection.modifier_id)
        if modifier is None or modifier.flat != flat:
            continue
        total += sign * modifier.cost_value
    return total


def _per_rank_cost(effect: PowerEffectInstance, base_cost_value: int, game_data: GameData) -> int:
    """Net per-rank cost of an effect: ``base + Σ per-rank extras − Σ per-rank flaws``."""

    net = base_cost_value
    net += _signed_modifier_cost(effect.extras, +1, game_data, flat=False)
    net += _signed_modifier_cost(effect.flaws, -1, game_data, flat=False)
    return net


def _ranked_cost(net_per_rank: int, rank: int) -> int:
    """Points for ``rank`` ranks at ``net_per_rank`` PP/rank.

    Above 1 PP/rank it is simply ``net × rank``. When flaws push the per-rank cost
    below 1, M&M switches to *1 point per N ranks* (``N = 2 − net``: net 0 → 1/2,
    net −1 → 1/3, …), so the cost is ``ceil(rank / (2 − net))``.
    """

    if net_per_rank >= 1:
        return net_per_rank * rank
    return math.ceil(rank / (2 - net_per_rank))


def effect_total_cost(effect: PowerEffectInstance, game_data: GameData) -> int:
    """Power-point cost of one assembled effect (``mm-powers-architecture.md`` §2).

    ``ranked = ceil`` of the per-rank cost times rank (see :func:`_ranked_cost` for
    the sub-1 PP/rank fraction rule), then ``total = ranked + Σ flat extras − Σ flat
    flaws``. An unknown effect id contributes nothing.
    """

    base = next((e for e in game_data.effects if e.id == effect.effect_id), None)
    if base is None:
        return 0

    ranked = _ranked_cost(_per_rank_cost(effect, base.base_cost_value, game_data), effect.rank)

    flat = _signed_modifier_cost(effect.extras, +1, game_data, flat=True)
    flat += _signed_modifier_cost(effect.flaws, -1, game_data, flat=True)

    return ranked + flat


def _modifier_terms(mods: list, sign: int, game_data: GameData, *, flat: bool) -> list[int]:
    """Signed ``cost_value`` of each modifier in one bucket, for formula display.

    Same selection as :func:`_signed_modifier_cost` but keeps the terms apart so a
    breakdown can list them individually rather than as a single sum.
    """

    catalog: dict[str, Modifier] = {m.id: m for m in game_data.modifiers}
    terms: list[int] = []
    for selection in mods:
        modifier = catalog.get(selection.modifier_id)
        if modifier is None or modifier.flat != flat:
            continue
        terms.append(sign * modifier.cost_value)
    return terms


def effect_cost_formula(effect: PowerEffectInstance, game_data: GameData) -> str:
    """Human-readable cost breakdown for one effect, e.g. ``3 × (2 + 1 − 1) + 1``.

    Mirrors :func:`effect_total_cost`: the parenthesised group is the per-rank cost
    (base plus per-rank extras minus per-rank flaws), multiplied by rank, then flat
    extras/flaws added outside. The raw terms are always shown — when flaws push the
    group below 1 PP/rank it is annotated with the resulting fraction (e.g.
    ``4 × (1 − 1 − 1 = 1/3)``), since the total is then a ceil, not that arithmetic.
    Returns ``""`` for an unknown effect.
    """

    base = next((e for e in game_data.effects if e.id == effect.effect_id), None)
    if base is None:
        return ""

    per_rank_terms = [base.base_cost_value]
    per_rank_terms += _modifier_terms(effect.extras, +1, game_data, flat=False)
    per_rank_terms += _modifier_terms(effect.flaws, -1, game_data, flat=False)
    net = sum(per_rank_terms)

    per_rank_str = _join_terms(per_rank_terms)
    if net < 1:  # sub-1 PP/rank: 1 point per (2 − net) ranks
        per_rank_str = f"({per_rank_str} = 1/{2 - net})"
    elif len(per_rank_terms) > 1:
        per_rank_str = f"({per_rank_str})"

    formula = f"{effect.rank} × {per_rank_str}"

    flat_terms = _modifier_terms(effect.extras, +1, game_data, flat=True)
    flat_terms += _modifier_terms(effect.flaws, -1, game_data, flat=True)
    for term in flat_terms:
        formula += f" {'−' if term < 0 else '+'} {abs(term)}"

    return formula


def _join_terms(terms: list[int]) -> str:
    """Render signed integers as ``a + b − c`` (leading term keeps its own sign)."""

    parts = [str(terms[0])]
    for term in terms[1:]:
        parts.append(f"{'−' if term < 0 else '+'} {abs(term)}")
    return " ".join(parts)


def power_total_cost(power: Power, game_data: GameData) -> int:
    """Total power-point cost of a power: the sum of its effects' costs."""

    return sum(effect_total_cost(e, game_data) for e in power.effects)


def power_level_violations(char: Character, game_data: GameData) -> list[str]:
    """Report Power Level cap breaches (``mm-core-mechanics.md`` §7); empty list = valid.

    Evaluates the caps whose inputs exist today: per-skill modifier, Dodge +
    Toughness, and Fortitude + Will. The attack + effect-rank cap is deferred
    until powers are modelled.
    """

    caps = game_data.costs.power_level.caps
    pl = char.power_level
    violations: list[str] = []

    skill_cap = caps.get("skill_modifier")
    if skill_cap is not None:
        limit = skill_cap.limit(pl)
        for row_id in char.skill_ranks:
            total = skill_total(char, game_data, row_id)
            if total > limit:
                violations.append(f"{row_id} modifier {total} exceeds PL cap {limit}.")

    def _pair(cap_name: str, a_key: str, b_key: str, label: str) -> None:
        cap = caps.get(cap_name)
        if cap is None:
            return
        limit = cap.limit(pl)
        value = resistance_total(char, game_data, a_key) + resistance_total(char, game_data, b_key)
        if value > limit:
            violations.append(f"{label} {value} exceeds PL cap {limit}.")

    _pair("defense_toughness", "DODGE", "TOUGHNESS", "Dodge + Toughness")
    _pair("fortitude_will", "FORTITUDE", "WILL", "Fortitude + Will")

    return violations

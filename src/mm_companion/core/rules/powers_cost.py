"""Power/effect point-cost math (modifiers, ranks, array pooling, tree nodes)."""

from __future__ import annotations

import math

from ..character import Character
from ..data_loader import GameData, Modifier
from ..powers import (
    STRUCTURE_ARRAY,
    Power,
    PowerEffectInstance,
    PowerGroup,
    PowerNode,
)
from .derived import effective_ability


def _modifier_config_cost(modifier: Modifier, selection) -> int | None:
    """A cost magnitude a chosen config option overrides the modifier's with, if any.

    The first of the modifier's config fields whose selected option carries a
    ``cost_value`` (a Side Effect always/on-failure toggle, a Removable tier) wins;
    ``None`` when no such choice is set, leaving the modifier's own ``cost_value``.
    """

    for cfg in modifier.config_fields:
        chosen = selection.config.get(cfg.key)
        option = next(
            (o for o in cfg.options if o.value == chosen and o.cost_value is not None), None
        )
        if option is not None:
            return option.cost_value
    return None


def _modifier_magnitude(modifier: Modifier, selection) -> int:
    """One modifier's cost magnitude: ``cost_value`` (or a config override), times its
    rank when ``ranked``."""

    override = _modifier_config_cost(modifier, selection)
    magnitude = modifier.cost_value if override is None else override
    return magnitude * (selection.rank if modifier.ranked else 1)


def _signed_modifier_cost(mods: list, sign: int, game_data: GameData, *, flat: bool) -> int:
    """Sum the ``cost_value`` of the given modifier selections in one bucket.

    ``sign`` is ``+1`` for extras and ``-1`` for flaws; ``flat`` selects either the
    per-rank bucket (``flat=False``) or the one-time bucket (``flat=True``). A
    ``ranked`` modifier contributes ``cost_value × its rank`` (see
    :func:`_modifier_magnitude`).
    """

    catalog = game_data.modifier_catalog()
    total = 0
    for selection in mods:
        modifier = catalog.get(selection.modifier_id)
        if modifier is None or modifier.flat != flat:
            continue
        total += sign * _modifier_magnitude(modifier, selection)
    return total


def _net_per_rank_modifiers(effect: PowerEffectInstance, game_data: GameData) -> int:
    """Net per-rank extra/flaw cost of an effect (base cost excluded):
    ``Σ per-rank extras − Σ per-rank flaws``."""

    return _signed_modifier_cost(effect.extras, +1, game_data, flat=False) + _signed_modifier_cost(
        effect.flaws, -1, game_data, flat=False
    )


def _ranked_cost(net_per_rank: int, rank: int) -> int:
    """Points for ``rank`` ranks at ``net_per_rank`` PP/rank.

    Above 1 PP/rank it is simply ``net × rank``. When flaws push the per-rank cost
    below 1, M&M switches to *1 point per N ranks* (``N = 2 − net``: net 0 → 1/2,
    net −1 → 1/3, …), so the cost is ``ceil(rank / (2 − net))``.
    """

    if net_per_rank >= 1:
        return net_per_rank * rank
    return math.ceil(rank / (2 - net_per_rank))


def effect_total_cost(
    effect: PowerEffectInstance, game_data: GameData, char: Character | None = None
) -> int:
    """Power-point cost of one assembled effect (``mm-powers-architecture.md`` §2).

    ``ranked = ceil`` of the per-rank cost times rank (see :func:`_ranked_cost` for
    the sub-1 PP/rank fraction rule), then ``total = ranked + Σ flat extras − Σ flat
    flaws``. An unknown effect id contributes nothing.

    When an ability a modifier folds in raises the effect's rank (Strength-Based
    Damage picking up the wielder's Strength), the per-rank extras and flaws apply
    to those folded-in ranks too — the ranks come free of *base* cost, but each
    per-rank modifier still costs against them, so the total is
    ``rank × (base + net mods) + strength × net mods + flat``. This needs ``char``
    to know how much ability is folded in; without one, only the bought ranks count.
    """

    base = next((e for e in game_data.effects if e.id == effect.effect_id), None)
    if base is None:
        return 0

    net_mods = _net_per_rank_modifiers(effect, game_data)
    ranked = _ranked_cost(base.base_cost_value + net_mods, effect.rank)
    ranked += effect_rank_trait_bonus_cost(effect, game_data, char) * net_mods

    flat = _signed_modifier_cost(effect.extras, +1, game_data, flat=True)
    flat += _signed_modifier_cost(effect.flaws, -1, game_data, flat=True)

    return ranked + flat


def effect_rank_trait_bonus(
    effect: PowerEffectInstance, game_data: GameData, char: Character | None
) -> int:
    """Ranks a modifier folds in from a character ability *as it resolves in play*.

    Sums the *effective* value of each ability an attached modifier's
    :attr:`~mm_companion.core.data_loader.Modifier.adds_ability` names — so a
    Strength-Based Damage picks up the wielder's Strength (Enhanced Trait boosts to
    that ability included). Zero without a character or when no such modifier is
    attached. This is the value that sets the effect's DC / effective rank, so it
    tracks the wielder's *current* ability.

    A selection caps how much of the ability it uses via ``config["amount"]`` (the
    Strength-Based chip's spin box): the folded-in rank is the lesser of that cap and
    the ability the wielder actually has. Absent, the full ability is used and tracks
    it dynamically. Point cost is charged separately against the *bought* cap — see
    :func:`effect_rank_trait_bonus_cost` — so cost stays stable when the ability moves.
    """

    if char is None:
        return 0
    catalog = game_data.modifier_catalog()
    bonus = 0
    for selection in (*effect.extras, *effect.flaws):
        modifier = catalog.get(selection.modifier_id)
        if modifier and modifier.adds_ability:
            ability = effective_ability(char, game_data, modifier.adds_ability)
            amount = selection.config.get("amount")
            bonus += ability if amount is None else max(0, min(int(amount), ability))
    return bonus


def effect_rank_trait_bonus_cost(
    effect: PowerEffectInstance, game_data: GameData, char: Character | None
) -> int:
    """Ability ranks a modifier folds in *for point-cost purposes* (Strength-Based).

    The player buys a fixed amount of the ability to fold in via the Strength-Based
    chip's spin box (``config["amount"]``); the power pays for that amount every rank
    regardless of the wielder's *current* ability, so the cost is stable when Strength
    is enhanced or suppressed. This is deliberately decoupled from
    :func:`effect_rank_trait_bonus` (which tracks the current ability for the DC): a
    build that pays for more of the ability than the wielder has is a warning, not a
    price change (see :func:`~mm_companion.core.rules.power_strength_amount_violations`).

    When no amount is stored (a legacy selection that tracked the ability), it falls
    back to the wielder's current ability so old builds keep their previous cost.
    """

    catalog = game_data.modifier_catalog()
    bonus = 0
    for selection in (*effect.extras, *effect.flaws):
        modifier = catalog.get(selection.modifier_id)
        if not (modifier and modifier.adds_ability):
            continue
        amount = selection.config.get("amount")
        if amount is not None:
            bonus += max(0, int(amount))
        elif char is not None:
            bonus += max(0, effective_ability(char, game_data, modifier.adds_ability))
    return bonus


def effect_effective_rank(
    effect: PowerEffectInstance, game_data: GameData, char: Character | None = None
) -> int:
    """The effect's rank as it resolves in play: bought rank plus any ability a
    modifier folds in (:func:`effect_rank_trait_bonus`).

    This is the rank that sets the resistance DC and counts against the Power Level
    attack/effect cap — not the point-cost rank, which stays the bought value.
    """

    return effect.rank + effect_rank_trait_bonus(effect, game_data, char)


def _modifier_terms(mods: list, sign: int, game_data: GameData, *, flat: bool) -> list[int]:
    """Signed ``cost_value`` of each modifier in one bucket, for formula display.

    Same selection as :func:`_signed_modifier_cost` but keeps the terms apart so a
    breakdown can list them individually rather than as a single sum.
    """

    catalog = game_data.modifier_catalog()
    terms: list[int] = []
    for selection in mods:
        modifier = catalog.get(selection.modifier_id)
        if modifier is None or modifier.flat != flat:
            continue
        terms.append(sign * _modifier_magnitude(modifier, selection))
    return terms


def effect_cost_formula(
    effect: PowerEffectInstance, game_data: GameData, char: Character | None = None
) -> str:
    """Human-readable cost breakdown for one effect, e.g. ``3 × (2 + 1 − 1) + 1``.

    Mirrors :func:`effect_total_cost`: the parenthesised group is the per-rank cost
    (base plus per-rank extras minus per-rank flaws), multiplied by rank, then flat
    extras/flaws added outside. The raw terms are always shown — when flaws push the
    group below 1 PP/rank it is annotated with the resulting fraction (e.g.
    ``4 × (1 − 1 − 1 = 1/3)``), since the total is then a ceil, not that arithmetic.
    When an ability folds ranks in (Strength-Based Damage), a ``+ strength × (mods)``
    term is appended for the per-rank modifiers those ranks also pay. Returns ``""``
    for an unknown effect.
    """

    base = next((e for e in game_data.effects if e.id == effect.effect_id), None)
    if base is None:
        return ""

    per_rank_terms = [base.base_cost_value]
    mod_terms = _modifier_terms(effect.extras, +1, game_data, flat=False)
    mod_terms += _modifier_terms(effect.flaws, -1, game_data, flat=False)
    per_rank_terms += mod_terms
    net = sum(per_rank_terms)

    per_rank_str = _join_terms(per_rank_terms)
    if net < 1:  # sub-1 PP/rank: 1 point per (2 − net) ranks
        per_rank_str = f"({per_rank_str} = 1/{2 - net})"
    elif len(per_rank_terms) > 1:
        per_rank_str = f"({per_rank_str})"

    formula = f"{effect.rank} × {per_rank_str}"

    # Ranks folded in from an ability (Strength-Based Damage) pay the per-rank
    # modifiers, but not the base cost — a separate ``strength × (mods)`` term. This
    # is the bought amount (:func:`effect_rank_trait_bonus_cost`), so the breakdown
    # matches the cost even when the wielder's current ability differs.
    strength = effect_rank_trait_bonus_cost(effect, game_data, char)
    if strength and sum(mod_terms) != 0:
        mods_str = _join_terms(mod_terms)
        formula += f" + {strength} × {f'({mods_str})' if len(mod_terms) > 1 else mods_str}"

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


def array_alternate_cost(game_data: GameData) -> int:
    """The flat point cost of one array alternate, read from the ``Alternate Effect`` extra.

    Kept data-driven: the number lives on the ``alternate_effect`` modifier in
    ``modifiers.json`` (``costValue``), and *which* modifier id counts as the array
    alternate comes from ``system.json`` (``alternate_effect_modifier``), not hardcoded
    here. Falls back to 1 if the record is missing.
    """

    modifier = game_data.modifier_catalog().get(game_data.system.alternate_effect_modifier)
    return modifier.cost_value if modifier else 1


def array_base_index(power: Power, game_data: GameData, char: Character | None = None) -> int:
    """Index of an array's *base* effect — the costliest one (ties break to the first).

    The base is paid for in full; every other effect is a flat-cost alternate.
    Returns ``-1`` for a power with no effects. Only meaningful for an array, but
    computed purely from the effects so callers can badge cards uniformly.
    """

    if not power.effects:
        return -1
    full = [effect_total_cost(e, game_data, char) for e in power.effects]
    return full.index(max(full))


def power_total_cost(power: Power, game_data: GameData, char: Character | None = None) -> int:
    """Total power-point cost of a power (``mm-powers-architecture.md`` §4).

    ``independent`` and ``linked`` powers cost the sum of their effects (linking
    is a +0 bundle). An ``array`` instead pays the costliest effect in full and a
    flat :func:`array_alternate_cost` for each remaining effect, since only one is
    active at a time. ``char`` is threaded to :func:`effect_total_cost` so a
    Strength-Based effect's folded-in ranks are priced against the wielder.
    """

    if power.structure == STRUCTURE_ARRAY and len(power.effects) > 1:
        full = [effect_total_cost(e, game_data, char) for e in power.effects]
        return max(full) + (len(full) - 1) * array_alternate_cost(game_data)
    return sum(effect_total_cost(e, game_data, char) for e in power.effects)


def node_cost(node: PowerNode, game_data: GameData, char: Character | None = None) -> int:
    """Total point cost of a powers-tree node — a leaf power or a nested group.

    A leaf :class:`~mm_companion.core.powers.Power` costs its :func:`power_total_cost`.
    A :class:`~mm_companion.core.powers.PowerGroup` recurses: ``independent`` and
    ``linked`` groups sum their children (linking is a +0 bundle), while an ``array``
    group pays its costliest child in full plus a flat :func:`array_alternate_cost` for
    each other child (only one is active at a time). Nesting is handled by the
    recursion — a child that is itself a group is priced the same way.
    """

    if isinstance(node, PowerGroup):
        costs = [node_cost(child, game_data, char) for child in node.children]
        if not costs:
            return 0
        if node.mode == STRUCTURE_ARRAY and len(costs) > 1:
            return max(costs) + (len(costs) - 1) * array_alternate_cost(game_data)
        return sum(costs)
    return power_total_cost(node, game_data, char)


def group_array_base_index(
    group: PowerGroup, game_data: GameData, char: Character | None = None
) -> int:
    """Index of an ``array`` group's *base* child — the costliest (ties → first).

    The base is paid in full; every other child is a flat-cost alternate. Returns
    ``-1`` for an empty group. Computed purely from :func:`node_cost` so callers can
    badge children uniformly regardless of the group's mode.
    """

    if not group.children:
        return -1
    costs = [node_cost(c, game_data, char) for c in group.children]
    return costs.index(max(costs))


def node_display_cost(
    node: PowerNode,
    parent: PowerGroup | None,
    game_data: GameData,
    char: Character | None = None,
) -> int:
    """The point cost a node contributes *within its parent group*.

    Inside an ``array`` parent every child except the costliest (the base) contributes
    only the flat :func:`array_alternate_cost`, since they share one pool. A base child,
    or any node under a non-array parent (or at top level, ``parent=None``), contributes
    its full :func:`node_cost`.
    """

    if parent is not None and parent.mode == STRUCTURE_ARRAY and len(parent.children) > 1:
        base = group_array_base_index(parent, game_data, char)
        if parent.children[base] is not node:
            return array_alternate_cost(game_data)
    return node_cost(node, game_data, char)

"""Power/effect point-cost math (modifiers, ranks, array pooling, tree nodes)."""

from __future__ import annotations

import math
from collections.abc import Callable

from ..character import Character
from ..data_loader import GameData, Modifier
from ..powers import (
    STRUCTURE_ARRAY,
    Power,
    PowerEffectInstance,
    PowerGroup,
    PowerNode,
)
from .appliers import CATEGORY_ABILITY
from .derived import effective_ability
from .size import size_trait_modifier


def _modifier_config_cost(modifier: Modifier, selection) -> int | None:
    """A cost magnitude a chosen config option overrides the modifier's with, if any.

    A ``points`` field's stored integer *is* the magnitude (a Subtle extra the player
    dials to 1 or 2), falling back to the field's ``default_value`` when unset. Otherwise
    the first config field whose selected option carries a ``cost_value`` (a Side Effect
    always/on-failure toggle, a Removable tier) wins. ``None`` when no such choice is
    set, leaving the modifier's own ``cost_value``.
    """

    for cfg in modifier.config_fields:
        chosen = selection.config.get(cfg.key)
        if cfg.type == "points":
            return int(chosen) if chosen is not None else cfg.default_value
        option = next(
            (o for o in cfg.options if o.value == chosen and o.cost_value is not None), None
        )
        if option is not None:
            return option.cost_value
    return None


def _effective_flat(modifier: Modifier, selection) -> bool:
    """Whether this selection is charged once (flat) or per effect rank.

    Defaults to the modifier's own ``flat``, but the first chosen config option that
    carries a ``flat`` override wins — Affliction's Onset flips between a flat ``-1``
    (conditions land after a round) and a per-rank ``-1`` (after a scene) purely from
    the option selected.
    """

    for cfg in modifier.config_fields:
        chosen = selection.config.get(cfg.key)
        option = next((o for o in cfg.options if o.value == chosen and o.flat is not None), None)
        if option is not None:
            return option.flat
    return modifier.flat


def _effective_ranked(modifier: Modifier, selection) -> bool:
    """Whether this selection's cost is multiplied by its own rank.

    Mirrors :func:`_effective_flat`: defaults to the modifier's own ``ranked``, but the
    first chosen config option carrying a ``ranked`` override wins — a Custom modifier's
    *flat* mode multiplies by its rank while its *per-rank* mode does not (it already
    scales with the effect's rank, so multiplying again would double-count).
    """

    for cfg in modifier.config_fields:
        chosen = selection.config.get(cfg.key)
        option = next((o for o in cfg.options if o.value == chosen and o.ranked is not None), None)
        if option is not None:
            return option.ranked
    return modifier.ranked


def _modifier_magnitude(modifier: Modifier, selection) -> int:
    """One modifier's cost magnitude: ``cost_value`` (or a config override), times its
    rank when ``ranked``."""

    override = _modifier_config_cost(modifier, selection)
    magnitude = modifier.cost_value if override is None else override
    return magnitude * (selection.rank if _effective_ranked(modifier, selection) else 1)


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
        if modifier is None or _effective_flat(modifier, selection) != flat:
            continue
        total += sign * _modifier_magnitude(modifier, selection)
    return total


def _net_per_rank_modifiers(effect: PowerEffectInstance, game_data: GameData) -> int:
    """Net per-rank extra/flaw cost of an effect (base cost excluded):
    ``Σ per-rank extras − Σ per-rank flaws``."""

    return _signed_modifier_cost(effect.extras, +1, game_data, flat=False) + _signed_modifier_cost(
        effect.flaws, -1, game_data, flat=False
    )


def effect_per_rank_cost(effect: PowerEffectInstance, game_data: GameData) -> int:
    """The effect's net cost **per rank**: base cost plus per-rank extras minus per-rank
    flaws.

    Flat modifiers are deliberately excluded — they are charged once, so they do not
    change what a rank costs. That is exactly the distinction the Strength-Based
    divisor turns on (:func:`ability_rank_contribution`), and it is the same figure
    :func:`_ranked_cost` prices ranks at. ``0`` for an unknown effect id.
    """

    base = next((e for e in game_data.effects if e.id == effect.effect_id), None)
    if base is None:
        return 0
    return base.base_cost_value + _net_per_rank_modifiers(effect, game_data)


def ability_rank_contribution(ability: int, per_rank_cost: int) -> int:
    """How much of an ability actually reaches an effect's rank — M&M's *second*
    divisor (``docs/mm-equipment-design.md`` §4).

    A Strength-Based Damage adds the wielder's Strength to the Damage rank, but only
    while a rank is cheap: once extras push the effect **above 1 point per rank**, the
    wielder adds ``floor(ability / cost per rank)`` instead. A compound bow with Ranged
    Strength-Based Damage costs 2 points per rank, so a Strength 5 wielder adds
    ``floor(5 / 2) = +2``, not ``+5``.

    Two carve-outs, and both fall out of the arithmetic rather than being special
    cases: an effect costing **less** than 1 point per rank adds the ability
    unmultiplied (there is no divisor and no bonus either way), and a **flat** modifier
    never triggers the divisor because it is not part of
    :func:`effect_per_rank_cost` in the first place.

    This is not the existing effective-rank *cost* rule and must not be conflated with
    it. That rule prices per-rank extras against the folded-in ranks; this one decides
    how many folded-in ranks there are at all. The divisor is applied first, and the
    reduced number is what feeds both the resistance DC and that pricing — see
    :func:`effect_rank_trait_bonus` and :func:`effect_rank_trait_bonus_cost`.
    """

    if ability <= 0:
        return 0
    if per_rank_cost <= 1:
        return ability
    return ability // per_rank_cost


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
    """Power-point cost of one assembled effect (``docs/mm-powers-architecture.md`` §2).

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
    effect: PowerEffectInstance,
    game_data: GameData,
    char: Character | None,
    *,
    ability_offset: Callable[[str], int] | None = None,
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

    Whatever survives that cap is then put through
    :func:`ability_rank_contribution`, M&M's second divisor: an effect costing more
    than a point per rank only picks up ``floor(ability / cost per rank)``.

    ``ability_offset`` subtracts from each ability *before* the cap and the divisor, so a
    caller can ask what the rank would have been without some part of it — which is how
    :func:`effect_size_rank_shift` isolates the ranks the character's size is paying
    for, without a second copy of this arithmetic to keep in step.
    """

    if char is None:
        return 0
    catalog = game_data.modifier_catalog()
    per_rank = effect_per_rank_cost(effect, game_data)
    bonus = 0
    for selection in (*effect.extras, *effect.flaws):
        modifier = catalog.get(selection.modifier_id)
        if modifier and modifier.adds_ability:
            ability = effective_ability(char, game_data, modifier.adds_ability)
            if ability_offset is not None:
                ability -= ability_offset(modifier.adds_ability)
            amount = selection.config.get("amount")
            raw = ability if amount is None else max(0, min(int(amount), ability))
            bonus += ability_rank_contribution(raw, per_rank)
    return bonus


def effect_size_rank_shift(
    effect: PowerEffectInstance, game_data: GameData, char: Character | None
) -> int:
    """How many of this effect's *effective* ranks the character's **size** is paying for.

    The same computation run twice — once as the sheet has it, once with the Size Table's
    ability modifiers taken back out — because the Power Level cap must move by exactly
    what size moved in its input, and that is not simply the size modifier. A
    Strength-Based Damage bought against a fixed ``amount`` folds in
    ``min(amount, Strength)``, so a large character whose Strength already exceeds the
    amount gains no rank and is owed no extra cap; the same amount is then put through
    M&M's per-rank-cost divisor, which is not linear either.

    Zero for an effect that folds no ability in, which is most of them.
    """

    if char is None:
        return 0

    def offset(key: str) -> int:
        return size_trait_modifier(char, game_data, CATEGORY_ABILITY, key)

    return effect_rank_trait_bonus(effect, game_data, char) - effect_rank_trait_bonus(
        effect, game_data, char, ability_offset=offset
    )


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

    The bought amount goes through :func:`ability_rank_contribution` exactly as the
    play-time one does, because the two must agree: the per-rank extras are charged
    against the ranks that actually arrive, not against the ranks a divisor threw
    away.
    """

    catalog = game_data.modifier_catalog()
    per_rank = effect_per_rank_cost(effect, game_data)
    bonus = 0
    for selection in (*effect.extras, *effect.flaws):
        modifier = catalog.get(selection.modifier_id)
        if not (modifier and modifier.adds_ability):
            continue
        amount = selection.config.get("amount")
        if amount is not None:
            raw = max(0, int(amount))
        elif char is not None:
            raw = max(0, effective_ability(char, game_data, modifier.adds_ability))
        else:
            continue
        bonus += ability_rank_contribution(raw, per_rank)
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
        if modifier is None or _effective_flat(modifier, selection) != flat:
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
    """Total power-point cost of a power (``docs/mm-powers-architecture.md`` §4).

    ``independent`` and ``linked`` powers cost the sum of their effects (linking
    is a +0 bundle). An ``array`` instead pays the costliest effect in full and a
    flat :func:`array_alternate_cost` for each remaining effect, since only one is
    active at a time. ``char`` is threaded to :func:`effect_total_cost` so a
    Strength-Based effect's folded-in ranks are priced against the wielder.

    A Dev-mode :attr:`~mm_companion.core.powers.Power.cost_override` replaces the
    whole computed total, so a homerule power spends exactly that many points; it
    flows through :func:`node_cost` into the character's power-point tally.
    """

    if power.cost_override is not None:
        return power.cost_override
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

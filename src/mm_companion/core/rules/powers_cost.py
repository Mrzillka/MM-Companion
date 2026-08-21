"""Power/effect point-cost math (modifiers, ranks, array pooling, tree nodes)."""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from fractions import Fraction

from ..character import Character
from ..data_loader import Effect, EffectConfigField, GameData, Modifier
from ..powers import (
    STRUCTURE_ARRAY,
    Power,
    PowerEffectInstance,
    PowerGroup,
    PowerNode,
    power_is_stunt,
)
from ..registry import Registry
from .appliers import (
    CATEGORY_ABILITY,
    CATEGORY_ADVANTAGE,
    CATEGORY_RESISTANCE,
    CATEGORY_SKILL,
    trait_category,
)
from .derived import effective_ability
from .runtime import (
    boost_allocations,
    config_trait_allocation,
    effect_current_rank,
    set_array_base_index,
    set_dynamic_rank_cap,
)
from .size import effective_size_rank
from .trait_rates import trait_allocation_cost, trait_rate


def _modifier_config_cost(modifier: Modifier, selection) -> int | None:
    """A cost magnitude a chosen config option overrides the modifier's with, if any.

    A ``points`` field's stored integer *is* the magnitude (a Subtle extra the player
    dials to 1 or 2), falling back to the field's ``default_value`` when unset. Otherwise
    the first config field whose selected option carries a ``cost_value`` (a Side Effect
    always/on-failure toggle, a Removable tier) wins. ``None`` when no such choice is
    set, leaving the modifier's own ``cost_value``.

    :func:`_config_cost_delta` is then added on top of whichever number that is — so a
    second field can shade the price the first one set, which is what Removable's
    Short-Term Only does to its tier.
    """

    override = None
    for cfg in modifier.config_fields:
        chosen = selection.config.get(cfg.key)
        if cfg.type == "points":
            override = int(chosen) if chosen is not None else cfg.default_value
            break
        option = next(
            (o for o in cfg.options if o.value == chosen and o.cost_value is not None), None
        )
        if option is not None:
            override = option.cost_value
            break
    delta = _config_cost_delta(modifier, selection)
    if delta == 0:
        return override
    base = modifier.cost_value if override is None else override
    return max(0, base + delta)


def _config_cost_delta(modifier: Modifier, selection) -> int:
    """Total ``cost_delta`` of the options currently chosen, across **all** config fields.

    Unlike the first-wins overrides beside it this one is a sum: the delta shades a
    magnitude another field decided, so several of them have to be able to stack. Floored
    at zero by the caller — a flaw's magnitude is unsigned (the sign comes from its
    category), and the rules let Short-Term Only take Removable's value all the way down
    to "no discount at all" (p160) rather than to a bonus.
    """

    total = 0
    for cfg in modifier.config_fields:
        chosen = selection.config.get(cfg.key)
        values = chosen if isinstance(chosen, list) else [chosen]
        for option in cfg.options:
            if option.cost_delta and option.value in values:
                total += option.cost_delta
    return total


#: Priced against the effect it is attached to — the default, and all but one modifier.
COST_SCOPE_EFFECT = ""

#: Priced against the whole assembled *power* instead. Removable "applies to the power as
#: a whole and not to individual effects" (p161), so its discount cannot be worked out
#: until every effect has been summed. A modifier marked this way is skipped by every
#: effect-level bucket below and applied once by :func:`power_scope_adjustment`.
COST_SCOPE_POWER = "power"


def modifier_is_power_scope(modifier: Modifier) -> bool:
    """Whether this modifier is priced from the power's total rather than the effect's.

    Asked here rather than compared inline so the effect-level buckets, the formula
    builders and the constructor all read the same one line.
    """

    return modifier.cost_scope == COST_SCOPE_POWER


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


#: What a ranked modifier may be bought up to when the ruleset names no ceiling. Not a
#: rule — a spin box has to stop somewhere, and this is the same "no practical limit"
#: the effect ranks themselves use.
MODIFIER_RANK_MAX = 250


def modifier_rank_cap(modifier: Modifier) -> int:
    """How many ranks of ``modifier`` may be bought — the data's ceiling, or 1 unranked.

    An *unranked* modifier is not bought in ranks at all: it stands at whatever rank its
    host effect has, so there is one of it. A ranked one takes the ruleset's ``max_rank``
    (Striding's 5) and :data:`MODIFIER_RANK_MAX` when it names none. Asked here rather
    than read off the record so the UI's spin box and any later validation cannot
    disagree about what the cap is.
    """

    if not modifier.ranked:
        return 1
    return modifier.max_rank if modifier.max_rank is not None else MODIFIER_RANK_MAX


def _modifier_magnitude(
    modifier: Modifier, selection, game_data: GameData, char: Character | None = None
) -> int:
    """One modifier's cost magnitude: ``cost_value`` (or a config override), times its
    rank when ``ranked``.

    A modifier whose ``cost_mode`` is :data:`BASE_COST_AS_TRAIT` is priced from its own
    trait allocation instead of a fixed number — Enhanced Trait's Reduced Trait flaw is
    worth "the cost of the reduced trait", whatever the player lowered and by how much.
    It is a flat magnitude, so it is never multiplied by a rank.
    """

    if modifier.cost_mode == BASE_COST_AS_TRAIT:
        rows = config_trait_allocation(selection.config, modifier)
        return math.ceil(trait_allocation_cost(rows, char, game_data))

    override = _modifier_config_cost(modifier, selection)
    magnitude = modifier.cost_value if override is None else override
    return magnitude * (selection.rank if _effective_ranked(modifier, selection) else 1)


def selection_band(selection, effect_rank: int) -> tuple[int, int]:
    """The ranks of its host effect a modifier selection covers — ``(lo, hi)``, inclusive.

    ``(1, effect_rank)`` for the ordinary selection, which stores no band at all. A
    stored band is **clamped** here rather than trusted, the way
    :func:`~.runtime.effect_current_rank` clamps a dialled rank: the effect's rank can be
    edited down long after the band was set, and a band hanging off the end of it would
    otherwise price ranks that no longer exist.
    """

    lo = selection.applies_from or 1
    hi = selection.applies_to or effect_rank
    lo = max(1, min(lo, max(1, effect_rank)))
    hi = max(lo, min(hi, max(1, effect_rank)))
    return lo, hi


def modifier_is_per_rank(modifier: Modifier, selection) -> bool:
    """Whether this selection is charged once *per effect rank* rather than once.

    The modifier's own ``flat``, with a chosen config option's override applied
    (:func:`_effective_flat`). Public because it is also the question the constructor
    asks before offering a rank band: only a per-rank charge can cost differently over
    part of an effect, so only a per-rank modifier gets the control.
    """

    return not _effective_flat(modifier, selection)


def modifier_is_banded(modifier: Modifier, selection, effect_rank: int) -> bool:
    """Whether this selection is priced over only *part* of its effect's ranks.

    False for a **flat** modifier however its band reads: a one-time charge costs the
    same over four ranks as over twelve, so a band there would change no number. The
    constructor offers none for that reason, and this is what makes a band stored by
    some other route harmless.
    """

    if _effective_flat(modifier, selection):
        return False
    return selection_band(selection, effect_rank) != (1, effect_rank)


def _signed_modifier_cost(
    mods: list,
    sign: int,
    game_data: GameData,
    *,
    flat: bool,
    char: Character | None = None,
    effect_rank: int = 0,
) -> int:
    """Sum the ``cost_value`` of the given modifier selections in one bucket.

    ``sign`` is ``+1`` for extras and ``-1`` for flaws; ``flat`` selects either the
    per-rank bucket (``flat=False``) or the one-time bucket (``flat=True``). A
    ``ranked`` modifier contributes ``cost_value × its rank`` (see
    :func:`_modifier_magnitude`).

    A **banded** per-rank selection is left out: it does not have one price for the whole
    effect, so it is priced rank by rank in :func:`_rank_runs` instead. Nothing changes
    for a selection with no band, which is every one saved before bands existed.
    """

    catalog = game_data.modifier_catalog()
    total = 0
    for selection in mods:
        modifier = catalog.get(selection.modifier_id)
        if modifier is None or modifier_is_power_scope(modifier):
            continue
        if _effective_flat(modifier, selection) != flat:
            continue
        if not flat and effect_rank and modifier_is_banded(modifier, selection, effect_rank):
            continue
        total += sign * _modifier_magnitude(modifier, selection, game_data, char)
    return total


def _net_per_rank_modifiers(
    effect: PowerEffectInstance, game_data: GameData, char: Character | None = None
) -> int:
    """Net per-rank extra/flaw cost of an effect (base cost excluded):
    ``Σ per-rank extras − Σ per-rank flaws``.

    Counts only the selections that cover the **whole** effect. A banded one is charged
    per rank by :func:`_rank_runs`, and its absence here is deliberate in two further
    places: this is the figure a Strength-Based fold-in pays per folded rank, and the
    divisor :func:`ability_rank_contribution` reads. Folded ranks sit above the bought
    ones and are in nobody's band, so a flaw restricted to ranks 9–12 must not discount
    them.
    """

    return _signed_modifier_cost(
        effect.extras, +1, game_data, flat=False, char=char, effect_rank=effect.rank
    ) + _signed_modifier_cost(
        effect.flaws, -1, game_data, flat=False, char=char, effect_rank=effect.rank
    )


def _net_flat_modifiers(
    effect: PowerEffectInstance, game_data: GameData, char: Character | None = None
) -> int:
    """Net one-time extra/flaw cost of an effect: ``Σ flat extras − Σ flat flaws``."""

    return _signed_modifier_cost(
        effect.extras, +1, game_data, flat=True, char=char
    ) + _signed_modifier_cost(effect.flaws, -1, game_data, flat=True, char=char)


def effect_per_rank_cost(effect: PowerEffectInstance, game_data: GameData) -> int:
    """The effect's net cost **per rank**: base cost plus per-rank extras minus per-rank
    flaws.

    The base is :func:`effect_base_cost_value`, not the raw constant — for the handful of
    effects priced from their configuration (Illusion, Obscure, …) what a rank costs moves
    with the choices the player made, and this is the figure the Strength-Based divisor
    below reads.

    Flat modifiers are deliberately excluded — they are charged once, so they do not
    change what a rank costs. That is exactly the distinction the Strength-Based
    divisor turns on (:func:`ability_rank_contribution`), and it is the same figure
    :func:`_ranked_cost` prices ranks at. ``0`` for an unknown effect id.
    """

    base = next((e for e in game_data.effects if e.id == effect.effect_id), None)
    if base is None:
        return 0
    return effect_base_cost_value(effect, base) + _net_per_rank_modifiers(effect, game_data)


def _chosen_values(config: dict, field: EffectConfigField) -> tuple[str, ...]:
    """The option values currently selected in one config field.

    A ``multiselect`` stores a list and a ``select`` a bare string; both are normalised
    to a tuple here so a caller need not know which kind of field it is looking at. An
    unset field yields nothing.
    """

    stored = config.get(field.key)
    if not stored:
        return ()
    return tuple(stored) if isinstance(stored, (list, tuple)) else (str(stored),)


def effect_base_cost_value(effect: PowerEffectInstance, base: Effect) -> int:
    """The effect's points **per rank** before any modifier — a constant, or configured.

    Almost every effect is priced at its ``base_cost_value`` flat. Five are not: what
    Illusion, Obscure, Remote Sensing, Transmute and Environment cost per rank depends on
    how the player configured them (see :class:`~mm_companion.core.data_loader.BaseCostBy`
    for the arithmetic and why it lives on the effect rather than in
    :data:`BASE_COST_KINDS`). Those effects declare a ``baseCostBy`` block naming the
    config fields that drive the price, and every option in them carries its own
    ``cost_value``.

    This is the single place the two cases are told apart. Everything that used to read
    ``base.base_cost_value`` for pricing goes through here instead — the per-rank cost,
    the flat total, and the formula the card prints — so a configured Illusion cannot end
    up costing one thing and explaining another.

    An option carrying no ``cost_value`` contributes nothing, which is what lets a
    cost-driving field also hold ordinary descriptive choices.
    """

    by = base.base_cost_by
    if by is None:
        return base.base_cost_value
    fields = {f.key: f for f in base.config_fields if f.key in by.fields}
    total = by.base
    for key in by.fields:
        field = fields.get(key)
        if field is None:
            continue
        prices = {o.value: o.cost_value for o in field.options if o.cost_value is not None}
        total += sum(prices.get(value, 0) for value in _chosen_values(effect.config, field))
    total = max(by.minimum, total)
    return total if by.maximum is None else min(total, by.maximum)


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


def _ranked_cost_fraction(net_per_rank: int, rank: int) -> Fraction:
    """Points for ``rank`` ranks at ``net_per_rank`` PP/rank, **unrounded**.

    Above 1 PP/rank it is simply ``net × rank``. When flaws push the per-rank cost
    below 1, M&M switches to *1 point per N ranks* (``N = 2 − net``: net 0 → 1/2,
    net −1 → 1/3, …), so the cost is ``rank / (2 − net)``.

    Kept as a :class:`~fractions.Fraction` because an effect can now be priced in
    several bands at once (a modifier applied to only some of its ranks), and rounding
    each band would charge for a part-point several times over. The bands are summed
    here and rounded **once** — the same rule
    :func:`~mm_companion.core.rules.effect_cost_breakdown`'s "as trait" rows follow.
    """

    if net_per_rank >= 1:
        return Fraction(net_per_rank * rank)
    return Fraction(rank, 2 - net_per_rank)


def _ranked_cost(net_per_rank: int, rank: int) -> int:
    """Points for ``rank`` ranks at ``net_per_rank`` PP/rank, rounded up."""

    return math.ceil(_ranked_cost_fraction(net_per_rank, rank))


def _banded_rank_terms(
    effect: PowerEffectInstance, game_data: GameData, char: Character | None = None
) -> tuple[tuple[int, ...], ...]:
    """The signed banded-modifier terms in force at each rank ``1..effect.rank``.

    One tuple per rank, empty where no banded modifier reaches it. Every entry is
    empty for a power whose modifiers all cover the whole effect, which is what lets
    :func:`_rank_runs` fall straight back to the single un-banded term.
    """

    catalog = game_data.modifier_catalog()
    per_rank: list[list[int]] = [[] for _ in range(max(0, effect.rank))]
    if not per_rank:
        return ()
    for selections, sign in ((effect.extras, +1), (effect.flaws, -1)):
        for selection in selections:
            modifier = catalog.get(selection.modifier_id)
            if modifier is None or modifier_is_power_scope(modifier):
                continue
            if not modifier_is_banded(modifier, selection, effect.rank):
                continue
            magnitude = sign * _modifier_magnitude(modifier, selection, game_data, char)
            low, high = selection_band(selection, effect.rank)
            for index in range(low - 1, high):
                per_rank[index].append(magnitude)
    return tuple(tuple(terms) for terms in per_rank)


# --- the base-cost registry -----------------------------------------------------------

#: Priced at a fixed number of points per rank — every effect but one.
BASE_COST_FLAT = "flat"

#: Priced "as trait" (the rules' own words for Enhanced Trait's base cost): the effect
#: costs whatever the traits it raises would cost to buy outright. Also the ``cost_mode``
#: of the Reduced Trait flaw, which discounts by what the lowered trait was worth.
BASE_COST_AS_TRAIT = "as_trait"


@dataclass(frozen=True)
class BaseCostContext:
    """Everything a base-cost kind needs to price one assembled effect.

    ``net_per_rank`` and ``flat`` are the modifier sums already bucketed by
    :func:`_net_per_rank_modifiers` / :func:`_net_flat_modifiers`, so a kind decides only
    how the *base* is charged and how the modifiers land on it. ``base_value`` is that
    base already resolved by :func:`effect_base_cost_value` — a constant for most effects,
    and the configured price for the few whose cost depends on how they are set up — so a
    kind never has to ask which sort it is holding. ``folded_ranks`` is the ranks an
    ability-folding modifier contributes (Strength-Based Damage) — free of base cost, but
    still paying every per-rank modifier.

    ``char`` may be ``None``: a power is priced with or without a character in hand, and
    what it costs must not depend on having one.
    """

    effect: PowerEffectInstance
    base: Effect
    game_data: GameData
    char: Character | None
    base_value: int
    net_per_rank: int
    flat: int
    folded_ranks: int


@dataclass(frozen=True)
class BaseCostKind:
    """How one ``baseCostMode`` prices an effect, and how it explains itself.

    ``price`` returns the points; ``formula`` returns the breakdown the card's footer
    shows. Both take a :class:`BaseCostContext`, and they are one record rather than two
    registries because a rule whose explanation is registered separately is a rule whose
    explanation can silently stop matching the number beside it.
    """

    price: Callable[[BaseCostContext], int]
    formula: Callable[[BaseCostContext], str]


#: One handler per ``baseCostMode`` in ``effects.json``. A mod's Python module can
#: :func:`register_base_cost_kind` a pricing rule of its own — the same seam
#: :data:`~.appliers.STAT_APPLIERS` gives stat effects — and an effect naming an
#: unregistered mode falls back to :data:`BASE_COST_FLAT`, i.e. behaves as it always has.
BASE_COST_KINDS: Registry[BaseCostKind] = Registry("baseCostMode")


def register_base_cost_kind(mode: str, kind: BaseCostKind, *, replace: bool = False) -> None:
    """Register a pricing rule for a ``baseCostMode``, for a mod to call at load time."""

    BASE_COST_KINDS.register(mode, kind, replace=replace)


def _base_cost_kind(base: Effect) -> BaseCostKind:
    """The pricing rule an effect declares, falling back to the flat one."""

    return BASE_COST_KINDS.get(base.base_cost_mode) or BASE_COST_KINDS.get(BASE_COST_FLAT)


def _rank_runs(context: BaseCostContext) -> tuple[tuple[int, tuple[int, ...]], ...]:
    """The effect's ranks grouped into ``(how many, the banded terms they pay)`` runs.

    A power whose modifiers all cover the whole effect comes back as a single run
    carrying no banded terms — the shape the cost and the formula have always had, which
    is why neither changes for a build nobody has banded. A Blast 12 whose top four ranks
    are Tiring comes back as two: eight ranks at the plain rate, then four at the
    discounted one.
    """

    banded = _banded_rank_terms(context.effect, context.game_data, context.char)
    if not any(banded):
        return ((context.effect.rank, ()),)
    runs: list[tuple[int, tuple[int, ...]]] = []
    for terms in banded:
        if runs and runs[-1][1] == terms:
            runs[-1] = (runs[-1][0] + 1, terms)
        else:
            runs.append((1, terms))
    return tuple(runs)


def _flat_base_cost(context: BaseCostContext) -> int:
    """The ordinary rule: ``rank × (base + net mods) + folded × net mods + flat``.

    The historical arithmetic — see :func:`_ranked_cost_fraction` for what happens when
    flaws push the per-rank cost below 1 PP — except that the ranks are priced in
    :func:`_rank_runs` bands rather than as one block, so a modifier applied to only some
    of them charges only those. A build with no band has exactly one run and the answer
    is the one it always was.

    The runs are summed **unrounded** and rounded once, so two bands that each come to
    half a point come to one point together rather than two.

    Flat flaws are then floored: the rules are explicit that a flat-value flaw cannot take
    an effect below **1 point** (``docs/mm-powers-architecture.md`` §2, and the book's own
    "flat-value modifiers" note), and without that a heavily-discounted cheap effect —
    an Equipment-tier Removable on a 1-point Commlink — comes out *negative* and pays the
    character back. An effect with no ranks bought is left at zero: nothing has been
    bought yet, and charging a point for an empty card would read as a bug.
    """

    net = context.base_value + context.net_per_rank
    ranked = math.ceil(
        sum(
            (
                _ranked_cost_fraction(net + sum(terms), count)
                for count, terms in _rank_runs(context)
            ),
            Fraction(0),
        )
    )
    ranked += context.folded_ranks * context.net_per_rank
    total = ranked + context.flat
    return max(1, total) if ranked > 0 else total


def _modifier_scale(nominal: int, net_per_rank: int) -> Fraction:
    """What per-rank modifiers multiply an "as trait" effect's cost by.

    An as-trait effect has no single price per rank to add ``-1`` to — Strength costs 2
    a rank and Stealth costs half of one, in the same effect. So the modifiers are read
    against the effect's *nominal* rate (its ``baseCostValue``, the 2 points per rank a
    trait ordinarily costs) and the result applied to the real total as a ratio: the
    typical -1/rank Limited turns 2 into 1 and so halves the whole thing, which is
    exactly what that flaw is understood to do to an Enhanced Trait.

    Below 1 PP/rank it follows the same "1 point per ``2 - net`` ranks" rule
    :func:`_ranked_cost` uses, so a second flaw quarters the cost rather than zeroing it.
    """

    if nominal <= 0:
        return Fraction(1)
    net = nominal + net_per_rank
    if net >= 1:
        return Fraction(net, nominal)
    return Fraction(1, (2 - net) * nominal)


def _as_trait_rows(context: BaseCostContext) -> tuple[tuple[str, int], ...]:
    """The ``(trait, ranks)`` rows an as-trait effect is priced from."""

    boost = context.base.integration.trait_boost if context.base.integration else None
    return boost_allocations(context.effect, context.base, boost) if boost else ()


def _as_trait_cost(context: BaseCostContext) -> int:
    """Enhanced Trait's rule: the effect costs what its traits cost to buy.

    Each allocated trait is priced at its own rate (:func:`~.trait_rates.trait_rate`)
    and the fractions summed unrounded, so a rank of Stealth and a rank of Treatment
    cost 1 point together rather than 1 point each — the same pooling the bought skills
    get from :func:`~.costs.skill_points_spent`. Per-rank modifiers then scale that total
    (:func:`_modifier_scale`), flat ones are added after, and the result cannot fall
    below the 1 point the rules floor an Enhanced Trait at.

    An effect with nothing allocated costs nothing: an empty Enhanced Trait is a power
    the player has not finished building, and charging for it would read as a bug.
    """

    total = trait_allocation_cost(_as_trait_rows(context), context.char, context.game_data)
    if total <= 0:
        return 0
    scale = _modifier_scale(context.base.base_cost_value, context.net_per_rank)
    return max(1, math.ceil(total * scale) + context.flat)


def effect_total_cost(
    effect: PowerEffectInstance, game_data: GameData, char: Character | None = None
) -> int:
    """Power-point cost of one assembled effect (``docs/mm-powers-architecture.md`` §2).

    *How* the base is charged is data — the effect's ``baseCostMode`` picks a handler out
    of :data:`BASE_COST_KINDS`. The default :data:`BASE_COST_FLAT` is the familiar
    ``ranked = ceil`` of the per-rank cost times rank (see :func:`_ranked_cost` for the
    sub-1 PP/rank fraction rule), then ``total = ranked + Σ flat extras − Σ flat flaws``;
    :data:`BASE_COST_AS_TRAIT` prices Enhanced Trait from the traits it raises. An unknown
    effect id contributes nothing, and an unknown mode prices as flat.

    When an ability a modifier folds in raises the effect's rank (Strength-Based
    Damage picking up the wielder's Strength), the per-rank extras and flaws apply
    to those folded-in ranks too — the ranks come free of *base* cost, but each
    per-rank modifier still costs against them, so the flat rule is
    ``rank × (base + net mods) + strength × net mods + flat``. This needs ``char``
    to know how much ability is folded in; without one, only the bought ranks count.
    """

    base = next((e for e in game_data.effects if e.id == effect.effect_id), None)
    if base is None:
        return 0

    return _base_cost_kind(base).price(_base_cost_context(effect, base, game_data, char))


def _base_cost_context(
    effect: PowerEffectInstance, base: Effect, game_data: GameData, char: Character | None
) -> BaseCostContext:
    """Bucket an effect's modifiers and folded ranks for a base-cost kind to read."""

    return BaseCostContext(
        effect=effect,
        base=base,
        game_data=game_data,
        char=char,
        base_value=effect_base_cost_value(effect, base),
        net_per_rank=_net_per_rank_modifiers(effect, game_data, char),
        flat=_net_flat_modifiers(effect, game_data, char),
        folded_ranks=effect_rank_trait_bonus_cost(effect, game_data, char),
    )


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

    Whatever survives that cap is then put through
    :func:`ability_rank_contribution`, M&M's second divisor: an effect costing more
    than a point per rank only picks up ``floor(ability / cost per rank)``.
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
            amount = selection.config.get("amount")
            raw = ability if amount is None else max(0, min(int(amount), ability))
            bonus += ability_rank_contribution(raw, per_rank)
    return bonus


def effect_size_rank_shift(
    effect: PowerEffectInstance, game_data: GameData, char: Character | None
) -> int:
    """Ranks this effect gains from the wielder's sheer **size**.

    The Size Table's damage column (:attr:`Measurements.size_rank_column`), read at the
    character's current size — so a Huge character's punch lands two ranks harder and a
    Tiny one's two ranks softer, and Growth moves it in play.

    It is a *rank*, not a trait bonus, which is the whole point of putting it here rather
    than on Strength: it reaches the save DC and the Power Level cap together, and leaves
    carrying capacity, Athletics and every other Strength roll alone.

    Zero in three cases, and each is a decision:

    * **The effect forces no resistance.** Size cannot make a Flight faster or a
      Concealment thicker. The test is the base effect's ``resistance_dc_base``, which is
      the same "not an attack/resisted effect" question
      :func:`~.validation.power_pl_violations` already asks.
    * **The player switched it off** (``size_scales_damage`` — the constructor's
      *Extended settings*). A giant's fist hits harder; a giant's laser does not, and
      only the player knows which this is.
    * **There is no character.** Size is the wielder's, and an effect inspected in the
      abstract has no wielder.
    """

    if char is None or not effect.size_scales_damage:
        return 0
    column = game_data.measurements.size_rank_column
    if not column:
        return 0
    base = next((e for e in game_data.effects if e.id == effect.effect_id), None)
    if base is None or base.resistance_dc_base is None:
        return 0
    row = game_data.measurements.size_row(effective_size_rank(char, game_data))
    return row.modifier(column) if row is not None else 0


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


def effect_build_rank(
    effect: PowerEffectInstance, game_data: GameData, char: Character | None = None
) -> int:
    """The effect's rank as **bought** — what the build says it can do.

    The bought rank, plus any ability a modifier folds in
    (:func:`effect_rank_trait_bonus`), plus what the wielder's size is worth
    (:func:`effect_size_rank_shift`). Both additions are free at the till for the same
    reason: the character already paid for their Strength, and nobody pays for being
    large.

    This — not :func:`effect_effective_rank` — is what Power Level validation reads. A
    cap is a statement about the build, and a player who has dialled a power down for
    this round has not rebuilt it: a sheet that passed validation by turning the dial
    would be validating nothing, which is the same bargain
    :func:`~.validation.offensive_builds` strikes over a sheathed sword.
    """

    return (
        effect.rank
        + effect_rank_trait_bonus(effect, game_data, char)
        + effect_size_rank_shift(effect, game_data, char)
    )


def effect_effective_rank(
    effect: PowerEffectInstance, game_data: GameData, char: Character | None = None
) -> int:
    """The effect's rank as it resolves in play.

    :func:`effect_build_rank` with the **runtime dial** applied
    (:func:`~.runtime.effect_current_rank`), so a Damage 10 a player has turned down to
    5 forces a save against 5. An effect nobody has dialled reads its bought rank, which
    is every effect until someone touches a slider.

    This is the rank that sets the resistance DC. It stops short of the *hard* Power
    Level cap, which needs an effect's attack bonus in hand and so lives one layer up as
    :func:`~.powers_terms.effect_live_rank`.

    Cost never asks either of them: what a power is worth is what it was bought at, and
    dialling one down mid-fight refunds nothing.
    """

    return (
        effect_current_rank(effect, game_data, char)
        + effect_rank_trait_bonus(effect, game_data, char)
        + effect_size_rank_shift(effect, game_data, char)
    )


def _modifier_terms(
    mods: list,
    sign: int,
    game_data: GameData,
    *,
    flat: bool,
    char: Character | None = None,
    effect_rank: int = 0,
) -> list[int]:
    """Signed ``cost_value`` of each modifier in one bucket, for formula display.

    Same selection as :func:`_signed_modifier_cost` — banded selections excluded, since
    they belong to one run of ranks rather than to the rate the whole effect pays — but
    keeps the terms apart so a breakdown can list them individually rather than as a
    single sum.
    """

    catalog = game_data.modifier_catalog()
    terms: list[int] = []
    for selection in mods:
        modifier = catalog.get(selection.modifier_id)
        if modifier is None or modifier_is_power_scope(modifier):
            continue
        if _effective_flat(modifier, selection) != flat:
            continue
        if not flat and effect_rank and modifier_is_banded(modifier, selection, effect_rank):
            continue
        terms.append(sign * _modifier_magnitude(modifier, selection, game_data, char))
    return terms


def _flat_terms(context: BaseCostContext) -> list[int]:
    """The one-time modifier terms of an effect, kept apart for display."""

    terms = _modifier_terms(
        context.effect.extras, +1, context.game_data, flat=True, char=context.char
    )
    terms += _modifier_terms(
        context.effect.flaws, -1, context.game_data, flat=True, char=context.char
    )
    return terms


def _append_flat_terms(formula: str, terms: list[int]) -> str:
    """Append ``+ n`` / ``− n`` for each one-time modifier."""

    for term in terms:
        formula += f" {'−' if term < 0 else '+'} {abs(term)}"
    return formula


def _flat_cost_formula(context: BaseCostContext) -> str:
    """Breakdown of the ordinary rule, e.g. ``3 × (2 + 1 − 1) + 1``.

    The parenthesised group is the per-rank cost (base plus per-rank extras minus
    per-rank flaws), multiplied by rank, then flat extras/flaws added outside. The raw
    terms are always shown — when flaws push the group below 1 PP/rank it is annotated
    with the resulting fraction (e.g. ``4 × (1 − 1 − 1 = 1/3)``), since the total is then
    a ceil, not that arithmetic. When an ability folds ranks in (Strength-Based Damage),
    a ``+ strength × (mods)`` term is appended for the per-rank modifiers those ranks
    also pay.
    """

    effect, game_data = context.effect, context.game_data
    mod_terms = _modifier_terms(
        effect.extras, +1, game_data, flat=False, char=context.char, effect_rank=effect.rank
    )
    mod_terms += _modifier_terms(
        effect.flaws, -1, game_data, flat=False, char=context.char, effect_rank=effect.rank
    )

    def run_str(count: int, banded: tuple[int, ...]) -> str:
        terms = [context.base_value, *mod_terms, *banded]
        net = sum(terms)
        rate = _join_terms(terms)
        if net < 1:  # sub-1 PP/rank: 1 point per (2 − net) ranks
            rate = f"({rate} = 1/{2 - net})"
        elif len(terms) > 1:
            rate = f"({rate})"
        return f"{count} × {rate}"

    # One term per band of ranks. A build nobody has banded has a single run holding no
    # banded terms, so this is the string it has always been.
    formula = " + ".join(run_str(count, banded) for count, banded in _rank_runs(context))

    # Ranks folded in from an ability (Strength-Based Damage) pay the per-rank
    # modifiers, but not the base cost — a separate ``strength × (mods)`` term. This
    # is the bought amount (:func:`effect_rank_trait_bonus_cost`), so the breakdown
    # matches the cost even when the wielder's current ability differs.
    if context.folded_ranks and sum(mod_terms) != 0:
        mods_str = _join_terms(mod_terms)
        formula += (
            f" + {context.folded_ranks} × {f'({mods_str})' if len(mod_terms) > 1 else mods_str}"
        )

    return _append_flat_terms(formula, _flat_terms(context))


def _fraction_str(value: Fraction) -> str:
    """A Fraction as ``4`` or ``1/2`` — the way the rules write a part of a point."""

    return (
        str(value.numerator) if value.denominator == 1 else f"{value.numerator}/{value.denominator}"
    )


def _mixed_fraction_str(value: Fraction) -> str:
    """A Fraction as ``2``, ``1/2`` or ``2 1/2`` — how a pooled subtotal is read aloud.

    :func:`_fraction_str` writes a bare improper fraction, which is right for a single
    rate (a skill rank *is* half a point) and wrong for a subtotal: ``5/2`` points of
    skills is five ranks, and nobody counts them that way. Whole and fractional parts are
    kept apart so the number reads at a glance, with the sign carried out in front.
    """

    sign = "−" if value < 0 else ""
    value = abs(value)
    whole, remainder = divmod(value.numerator, value.denominator)
    if not remainder:
        return f"{sign}{whole}"
    part = f"{remainder}/{value.denominator}"
    return f"{sign}{part}" if not whole else f"{sign}{whole} {part}"


@dataclass(frozen=True)
class TraitTerm:
    """One allocated trait's contribution to an as-trait effect's price.

    ``cost`` is ``ranks × rate`` left **unrounded**, which is the whole point of keeping
    the terms apart: a rank of Stealth and a rank of Treatment are half a point each and
    one point together, and only the unrounded fractions can say so. ``category`` is the
    trait list it came from (:func:`~.appliers.trait_category`), which is what the footer
    groups by.
    """

    target: str
    ranks: int
    category: str
    rate: Fraction
    cost: Fraction


#: The order an as-trait breakdown lists its groups in, with the noun each is called by.
#: Presentation, not rules content — the categories themselves are
#: :mod:`.appliers`', and a trait's *rate* is data as it always was. Kept as a tuple so
#: the footer's order is fixed rather than following whichever row was typed first.
_TRAIT_GROUP_LABELS = (
    (CATEGORY_ABILITY, "Abilities"),
    (CATEGORY_RESISTANCE, "Resistances"),
    (CATEGORY_SKILL, "Skills"),
    (CATEGORY_ADVANTAGE, "Advantages"),
)


def _as_trait_terms(context: BaseCostContext) -> tuple[TraitTerm, ...]:
    """One :class:`TraitTerm` per priced row of an as-trait effect's allocation."""

    terms: list[TraitTerm] = []
    for target, ranks in _as_trait_rows(context):
        rate = trait_rate(context.char, context.game_data, target)
        if rate is None or not ranks:
            continue
        terms.append(
            TraitTerm(
                target=target,
                ranks=int(ranks),
                category=trait_category(context.game_data, target),
                rate=rate,
                cost=rate * int(ranks),
            )
        )
    return tuple(terms)


def effect_cost_breakdown(
    effect: PowerEffectInstance, game_data: GameData, char: Character | None = None
) -> tuple[TraitTerm, ...]:
    """What each allocated trait added to an as-trait effect's price — ``()`` otherwise.

    The per-trait detail behind :func:`effect_cost_formula`'s grouped subtotals, so the
    card can show the workings without recomputing them beside the number. Empty for
    every effect priced the ordinary way, which has no traits to break down.
    """

    base = next((e for e in game_data.effects if e.id == effect.effect_id), None)
    if base is None or base.base_cost_mode != BASE_COST_AS_TRAIT:
        return ()
    return _as_trait_terms(_base_cost_context(effect, base, game_data, char))


def _as_trait_formula(context: BaseCostContext) -> str:
    """Breakdown of the as-trait rule, e.g. ``(Abilities 4 + Skills 2 1/2) × 1/2``.

    One term per *kind* of trait rather than one per row, with the rows' fractions pooled
    inside each — which is the arithmetic the rules actually do, and the difference
    between reading ``Skills 2`` and reading ``1/2 + 1/2 + 1/2 + 1/2``. A subtotal that
    lands between points keeps its fraction (``Skills 2 1/2``), since that half point is
    real and the next skill rank is free because of it.

    After the groups come the per-rank modifiers, as the ratio they scale the total by
    (:func:`_modifier_scale`), and the flat ones after that. The per-trait rows behind
    the groups are :func:`effect_cost_breakdown`, which the card hangs off the same label.
    """

    terms = _as_trait_terms(context)
    if not terms:
        return ""

    groups = []
    for category, label in _TRAIT_GROUP_LABELS:
        subtotal = sum((t.cost for t in terms if t.category == category), Fraction(0))
        if subtotal:
            groups.append(f"{label} {_mixed_fraction_str(subtotal)}")
    # A row naming something the sheet does not list still costs what it costs; group it
    # under no label rather than dropping it from a sum it is part of.
    other = sum((t.cost for t in terms if t.category not in dict(_TRAIT_GROUP_LABELS)), Fraction(0))
    if other:
        groups.append(_mixed_fraction_str(other))
    if not groups:
        return ""

    body = " + ".join(groups)
    scale = _modifier_scale(context.base.base_cost_value, context.net_per_rank)
    if scale != 1:
        body = (
            f"({body}) × {_fraction_str(scale)}"
            if len(groups) > 1
            else f"{body} × {_fraction_str(scale)}"
        )
    return _append_flat_terms(body, _flat_terms(context))


BASE_COST_KINDS.register(BASE_COST_FLAT, BaseCostKind(_flat_base_cost, _flat_cost_formula))
BASE_COST_KINDS.register(BASE_COST_AS_TRAIT, BaseCostKind(_as_trait_cost, _as_trait_formula))


def effect_cost_formula(
    effect: PowerEffectInstance, game_data: GameData, char: Character | None = None
) -> str:
    """Human-readable cost breakdown for one effect, e.g. ``3 × (2 + 1 − 1) + 1``.

    Mirrors :func:`effect_total_cost` term for term, and through the same
    :data:`BASE_COST_KINDS` record — a mode's price and its explanation are registered
    together so the footer can never drift from the number beside it. Returns ``""``
    for an unknown effect.
    """

    base = next((e for e in game_data.effects if e.id == effect.effect_id), None)
    if base is None:
        return ""
    return _base_cost_kind(base).formula(_base_cost_context(effect, base, game_data, char))


def _join_terms(terms: list[int]) -> str:
    """Render signed integers as ``a + b − c`` (leading term keeps its own sign)."""

    parts = [str(terms[0])]
    for term in terms[1:]:
        parts.append(f"{'−' if term < 0 else '+'} {abs(term)}")
    return " ".join(parts)


def effect_is_personal(effect: Effect) -> bool:
    """Whether an effect works on **its user alone** — the rules' "Personal Range effect".

    Reads the record's :attr:`~mm_companion.core.data_loader.Effect.personal` when it
    states one and falls back to ``range_ == "Personal"`` otherwise, so only the
    exceptions have to be written down. The exceptions exist because a few self-only
    effects spend their Range parameter on a *distance* instead — Teleport's Range is
    "Rank", meaning how far you go — and the book settles those by naming Teleport
    alongside Morph and Shrinking as effects an Affliction may impose (p110).
    """

    if effect.personal is not None:
        return effect.personal
    return effect.range_ == "Personal"


def effect_action_at_most(effect: Effect, limit: str, game_data: GameData) -> bool:
    """Whether an effect's Action is ``limit`` or faster, along the ``action`` ladder.

    The ladder is data (``modifiers.json``'s ``gameTermLadders``), running from ``None``
    up to ``Full round``, so "a standard action or less" is a position on it rather than
    a list of action names spelled here. An effect whose action is not on the ladder at
    all is treated as *not* within the limit, which is the cautious direction: a rule
    that says "standard action or less" should not silently admit something it cannot
    place.
    """

    ladder = game_data.game_term_ladders.get("action", ())
    if limit not in ladder or effect.action not in ladder:
        return False
    return ladder.index(effect.action) <= ladder.index(limit)


#: The action ceiling the rules put on an Affliction's imposed effect (p110).
IMPOSED_EFFECT_ACTION_LIMIT = "Standard"


def imposable_effects(game_data: GameData) -> tuple[Effect, ...]:
    """The effects an Affliction's Transformed condition may impose (p110), in name order.

    "The Transformed condition can be configured to impose a particular Personal Range
    effect on the target ... The imposed effect must have a Power Point cost equal to or
    less than the total cost of the Affliction and require a standard action or less to
    activate."

    Two of those three conditions are answered here, by *offering nothing that breaks
    them* — the same bargain Concealment's missing Touch option strikes, and a better one
    than a warning after the fact. The cost condition is the one that cannot be: it moves
    as the Affliction is edited, so it is checked by
    :func:`~.validation.power_imposed_effect_violations` instead.
    """

    return tuple(
        sorted(
            (
                effect
                for effect in game_data.effects
                if effect_is_personal(effect)
                and effect_action_at_most(effect, IMPOSED_EFFECT_ACTION_LIMIT, game_data)
            ),
            key=lambda e: e.name,
        )
    )


def imposed_effect_cost(effect: PowerEffectInstance, game_data: GameData) -> int:
    """What the effect an Affliction imposes costs, from its ``config``; 0 when none is set.

    The imposed effect is named (``imposedEffect``) and ranked (``imposedRank``) in the
    Affliction's own config rather than built as a power of its own, so it is priced by
    costing a bare instance of it — the ordinary :func:`effect_total_cost`, not a second
    pricing path that could disagree with the first.

    Bare is the point and also the limit: an effect whose rank *is* an allocation
    (Enhanced Trait) has nothing to price from a rank alone and reads 0. That leaves the
    budget check unable to bite there, which is the permissive direction for a warning
    and the honest one for a build the player has not actually assembled.
    """

    effect_id = str(effect.config.get("imposedEffect", "") or "")
    if not effect_id or not any(e.id == effect_id for e in game_data.effects):
        return 0
    rank = max(1, int(effect.config.get("imposedRank", 1) or 1))
    return effect_total_cost(PowerEffectInstance(effect_id, rank=rank), game_data)


def array_alternate_cost(game_data: GameData, *, dynamic: bool = False) -> int:
    """The flat point cost of one array alternate, read from the ``Alternate Effect`` extra.

    "An Alternate Effect costs 1 Power Point, while a Dynamic Alternate Effect costs 2"
    (p151), so ``dynamic`` picks the record's ``dynamicCostValue`` over its ``costValue``.
    A Dynamic alternate is dearer because it is not mutually exclusive with its siblings:
    it shares the array's point pool and runs alongside the array's other Dynamic members
    at reduced effectiveness (p101).

    Kept data-driven: both numbers live on the ``alternate_effect`` modifier in
    ``modifiers.json``, and *which* modifier id counts as the array alternate comes from
    ``system.json`` (``alternate_effect_modifier``), not hardcoded here. Falls back to
    1 (2 when dynamic) if the record is missing.
    """

    modifier = game_data.modifier_catalog().get(game_data.system.alternate_effect_modifier)
    if modifier is None:
        return 2 if dynamic else 1
    if dynamic and modifier.dynamic_cost_value:
        return modifier.dynamic_cost_value
    return modifier.cost_value


def array_dynamic_primary_cost(game_data: GameData) -> int:
    """What making an array's *base* member Dynamic costs.

    "Making the primary power of an array Dynamic requires 1 Alternate Effect rank"
    (p101) — so it is the ordinary :func:`array_alternate_cost`, not a third number, and
    it is charged *on top of* the base's own full cost rather than instead of it. The
    book's own worked example: Empyrean's base Create takes "a 1-point modifier to make
    it Dynamic", and each Dynamic alternate beside it costs 2.
    """

    return array_alternate_cost(game_data)


def array_members_cost(members: Sequence[tuple[int, bool]], game_data: GameData) -> int:
    """Total cost of one array from its members' ``(full cost, dynamic)`` pairs.

    The costliest member is the base and is paid in full (ties break to the first, as
    :func:`array_base_index` and :func:`group_array_base_index` report); every other
    member costs only its flat :func:`array_alternate_cost`, since they share one pool.
    A Dynamic member pays the dearer alternate price, or — when it *is* the base —
    :func:`array_dynamic_primary_cost` on top of its full cost.

    Shared by the two levels an array exists at: a power's own effects
    (:func:`power_gross_cost`) and a group's whole child powers (:func:`node_cost`).
    An empty sequence costs nothing.
    """

    if not members:
        return 0
    costs = [cost for cost, _ in members]
    base = costs.index(max(costs))
    total = costs[base]
    for index, (_, dynamic) in enumerate(members):
        if index == base:
            if dynamic:
                total += array_dynamic_primary_cost(game_data)
        else:
            total += array_alternate_cost(game_data, dynamic=dynamic)
    return total


# --- the Dynamic point pool (p101) -----------------------------------------------------
# Ordinary alternates are mutually exclusive; Dynamic ones instead "share the power
# points of the array, allowing them to operate at the same time, but at reduced
# effectiveness", split "once per turn as a free action". The split itself is runtime
# state on the member (``dynamic_points``); everything below turns it into a rank.


def array_pool_points(group: PowerGroup, game_data: GameData, char: Character | None = None) -> int:
    """The point pool an ``array`` group's Dynamic members share out between them.

    The array's points *are* its base member's full cost — every other member is bought
    for a flat point or two on top (:func:`array_members_cost`), so the pool a Dynamic
    split draws on is what the costliest child costs, which is the same number
    :func:`group_array_base_index` picks the base by. Zero for anything that is not a
    real array.
    """

    if group.mode != STRUCTURE_ARRAY or len(group.children) < 2:
        return 0
    return max(node_cost(child, game_data, char) for child in group.children)


def power_pool_points(power: Power, game_data: GameData, char: Character | None = None) -> int:
    """The pool a single power's own Dynamic effects share out between them.

    The effect-level twin of :func:`array_pool_points`, and the same rule one level
    down: an ``array`` power pays its costliest effect in full and a flat point or two
    for each other one, so the pool is what that base effect costs. Zero for anything
    that is not a real array of effects.
    """

    if power.structure != STRUCTURE_ARRAY or len(power.effects) < 2:
        return 0
    return max(effect_total_cost(effect, game_data, char) for effect in power.effects)


def dynamic_rank_share(rank: int, points: int, full_cost: int) -> int:
    """What ``points`` of an array's pool buys of a member bought at ``rank``.

    Proportional and rounded down: ``rank x points / full_cost``. The book's own worked
    example is the check — a Flight 5 costs 10 points, so the 2 points assigned to it buy
    ``5 x 2 / 10 = 1``, and it is "limited to 1 rank of Flight" (p101).

    **Zero is a real answer**, unlike everywhere else a rank is computed: a member
    holding less than one rank's worth is below the minimum the rules give it and is
    simply not running, which needs no separate off switch. A member holding its whole
    cost gets its whole rank, and one somehow holding more is capped there — a share
    cannot buy ranks nobody bought.
    """

    if full_cost <= 0 or points <= 0 or rank <= 0:
        return 0
    return max(0, min(rank, rank * points // full_cost))


def dynamic_share_points(rank: int, wanted: int, full_cost: int) -> int:
    """The smallest share that buys ``wanted`` ranks of a member bought at ``rank``.

    The exact inverse of :func:`dynamic_rank_share`, and the reason a Dynamic member's
    slider can be a *rank* slider at all: every notch converts to the cheapest number of
    points that reaches it, so the split always lands on a legal cost. It is also what
    makes the notches step differently per member — a member costing 2 points a rank
    moves the split 2 points per notch and one costing 1 moves it by 1, which is what
    keeps both of them priced correctly out of the one pool.

    Zero ranks cost nothing; asking for more ranks than were bought is capped at the
    member's whole cost, since a share cannot buy ranks nobody paid for.

    Not every rank is necessarily *reachable*: a 6-rank member costing 3 points gets two
    ranks for its first point, so nothing buys exactly one. :func:`dynamic_share_steps`
    is the list of the ones that are, and is what a control should offer.
    """

    if full_cost <= 0 or rank <= 0 or wanted <= 0:
        return 0
    if wanted >= rank:
        return full_cost
    return -(-wanted * full_cost // rank)  # ceil, so the share actually reaches `wanted`


def dynamic_held_rank(effect: PowerEffectInstance, points: int, full_cost: int) -> int | None:
    """The rank a Dynamic member is deliberately held *below* its share at, or ``None``.

    A share buys a **ceiling**, not a rank, and the two are not the same ladder: five
    points of a six-rank member costing five buys all six ranks, and no whole number of
    points buys exactly five — so a Growth whose wielder wants Huge rather than
    Gargantuan has to be able to pay for six and stand at five. Bigger is not better for
    a size effect (a Gargantuan character is easier to hit and impossible to hide), and
    the same is true of any effect a player would pull: the pool decides what a member
    *may* do, and the player still decides what it *is* doing. So the slider carries a
    notch per rank rather than per price (:func:`dynamic_share_steps` is what it used to
    carry) and writes the rank it stopped on into ``current_rank`` beside the share it
    spent.

    Read as a **pair**, which is the whole of the rule here: the hold counts only when
    the stored share is exactly what that rank's notch costs. That is what tells a
    deliberate hold apart from a ``current_rank`` left behind by a rank dial the effect
    had before it joined a pool — which would otherwise quietly cap a member nobody had
    touched, the same way two controls writing one number deadlocked the card that used
    to carry both (see :func:`~.runtime.effect_current_rank`) — and it is what keeps the
    two notches sharing a price (five ranks and six, both five points) apart.
    """

    held = effect.current_rank
    if held is None or held <= 0 or full_cost <= 0:
        return None
    if dynamic_share_points(effect.rank, held, full_cost) != points:
        return None
    return min(held, effect.rank)


def dynamic_share_steps(rank: int, full_cost: int) -> tuple[tuple[int, int], ...]:
    """Every ``(points, rank)`` a member's share can actually stop at, cheapest first.

    A Dynamic member's card slider is a *rank* slider whose value is spent in points, and
    these are its notches: one per rank the pool can genuinely buy, priced by
    :func:`dynamic_share_points` and confirmed against :func:`dynamic_rank_share`, so the
    two can never disagree about what a share is worth. Always starts at ``(0, 0)`` —
    holding nothing is a legal split and the way a member is switched off.

    Ranks that no share buys are simply absent: a member costing less than a point a rank
    climbs two rungs for its first point, and a notch that silently landed somewhere else
    would be a control that lies.
    """

    steps = [(0, 0)]
    for wanted in range(1, max(0, rank) + 1):
        points = dynamic_share_points(rank, wanted, full_cost)
        reached = dynamic_rank_share(rank, points, full_cost)
        if reached != wanted or points <= steps[-1][0]:
            continue
        steps.append((points, reached))
    return tuple(steps)


def allocated_array_members(nodes: Sequence[PowerNode]) -> list[tuple[PowerNode, int]]:
    """Every array member currently holding a share of its array's pool, with the share.

    Walks the whole powers tree, so a nested array inside another array is found too.
    Empty for every character nobody has split a pool on, which is what keeps
    :func:`dynamic_rank_cap` free on the ordinary sheet: the walk is a list traversal and
    no point cost is computed until a share is actually found.
    """

    found: list[tuple[PowerNode, int]] = []
    for node in nodes:
        if not isinstance(node, PowerGroup):
            continue
        if node.mode == STRUCTURE_ARRAY:
            for child in node.children:
                if child.dynamic and child.dynamic_points is not None:
                    found.append((child, int(child.dynamic_points)))
        found.extend(allocated_array_members(node.children))
    return found


def member_effects(node: PowerNode) -> list[PowerEffectInstance]:
    """Every effect under one array member — its own, or its whole subtree's.

    Public because the card that shows a member's share has the same question to ask:
    what a share buys is stated effect by effect, and a member can be a whole sub-group
    of cards as easily as a single one.
    """

    if isinstance(node, PowerGroup):
        return [e for child in node.children for e in member_effects(child)]
    return list(node.effects)


def dynamic_member_cost(node, game_data: GameData) -> int:
    """What one array member costs for the purpose of rationing its share.

    The **one** denominator in the Dynamic pool, and it exists because there were two:
    the cards priced their share notches *with* the wielder while :func:`dynamic_rank_cap`
    priced the same member *without* one, so a notch could promise "6 PP · Flight 3" while
    the sheet ran Flight 2. Whatever asks what a share is worth — the cap, the slider's
    notches, the label beside them — asks here.

    It is priced **without the wielder** on purpose, and that is not merely a tie-break.
    Passing a character would let a legacy Strength-Based selection reach
    :func:`~.derived.effective_ability`, which asks what the character's powers are
    contributing, which asks the cap — a loop that terminates today only because the cap
    happens to price character-free. It is also the right number on its own terms: the
    pool is a share of what the array was **bought** for.

    Takes a whole member (a card or a sub-group) or a single effect, since an array exists
    at both levels and so does its pool.
    """

    if isinstance(node, PowerEffectInstance):
        return effect_total_cost(node, game_data)
    return node_cost(node, game_data)


def dynamic_rank_cap(
    effect: PowerEffectInstance, game_data: GameData, char: Character
) -> int | None:
    """The rank the Dynamic pool holds one effect to, or ``None`` when no pool reaches it.

    The resolver :mod:`.runtime` asks through :func:`~.runtime.set_dynamic_rank_cap` —
    installed from here because the answer needs point costs, and cost sits a layer above
    runtime rather than beside it. Finding the effect is a walk over the character's own
    powers tree by identity: a share is held by a *member* of an array (a whole card or a
    whole sub-group), and it holds every effect beneath it down by the same proportion,
    which is what "at reduced effectiveness" means for a member with several effects.

    A member nested inside two allocated arrays is held to the **tighter** of the two
    caps. That is the honest reading of two pools each rationing it, and it is the only
    combination that cannot let an outer split hand back ranks an inner one withheld.
    An effect holding a share of its *own* power's pool — the effect-level array, whose
    members are effects rather than cards — is folded into the same minimum, so a card
    inside a split group whose own effects are also split is rationed by both.

    The member is priced through :func:`dynamic_member_cost`, which is character-free and
    says why.
    """

    cap: int | None = None
    for member, points in allocated_array_members(char.powers):
        if not any(e is effect for e in member_effects(member)):
            continue
        share = _share_cap(effect, points, dynamic_member_cost(member, game_data))
        cap = share if cap is None else min(cap, share)
    if effect.dynamic and effect.dynamic_points is not None:
        own = _share_cap(effect, int(effect.dynamic_points), dynamic_member_cost(effect, game_data))
        cap = own if cap is None else min(cap, own)
    return cap


def _share_cap(effect: PowerEffectInstance, points: int, full_cost: int) -> int:
    """What one share holds an effect to: the rank it buys, or the lower one it is held at.

    The two halves of a Dynamic member's single slider — the points it spent and the notch
    it stopped on (:func:`dynamic_held_rank`) — asked as one number, so both places a
    share is read (a member's own, and the one an enclosing array hands it) answer alike.
    """

    share = dynamic_rank_share(effect.rank, points, full_cost)
    held = dynamic_held_rank(effect, points, full_cost)
    return share if held is None else min(share, held)


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


@dataclass(frozen=True)
class PowerScopeTerm:
    """One power-scope modifier's contribution to a power's total.

    ``magnitude`` is what one unit of it is worth (Easily Removable's 2), ``units`` how
    many units the power's own cost bought (``ceil(gross / cost_per_points)``, or 1 for a
    modifier charged once), and ``amount`` the signed points it adds — negative for a
    flaw. Kept as a record rather than a bare number because the constructor shows the
    working: a −20 that appears from nowhere on a 98-point armour looks like a bug.
    """

    modifier: Modifier
    magnitude: int
    units: int
    amount: int


def power_scope_terms(
    power: Power, game_data: GameData, gross: int, char: Character | None = None
) -> tuple[PowerScopeTerm, ...]:
    """The power-scope modifiers on a power, priced against its ``gross`` (pre-adjustment)
    cost.

    Every selection of a :data:`COST_SCOPE_POWER` modifier anywhere in the power is
    collected, and **only the costliest selection of each modifier counts**. That is the
    rule rather than a tidy-up: Removable "applies to the power as a whole and not to
    individual effects" (p161), and the constructor attaches modifiers to effects, so a
    five-effect suit of armour naturally ends up carrying five copies of the one flaw.
    Summing them would quintuple a discount the book charges once.

    Returns nothing for a power with no such modifier, which is all but the removable
    ones — so the ordinary power pays exactly what it always did.
    """

    catalog = game_data.modifier_catalog()
    best: dict[str, PowerScopeTerm] = {}
    for effect in power.effects:
        for selections, sign in ((effect.extras, +1), (effect.flaws, -1)):
            for selection in selections:
                modifier = catalog.get(selection.modifier_id)
                if modifier is None or not modifier_is_power_scope(modifier):
                    continue
                magnitude = _modifier_magnitude(modifier, selection, game_data, char)
                units = (
                    math.ceil(gross / modifier.cost_per_points)
                    if modifier.cost_per_points > 0
                    else 1
                )
                term = PowerScopeTerm(
                    modifier=modifier,
                    magnitude=magnitude,
                    units=units,
                    amount=sign * magnitude * units,
                )
                previous = best.get(modifier.id)
                if previous is None or abs(term.amount) > abs(previous.amount):
                    best[modifier.id] = term
    return tuple(term for term in best.values() if term.amount)


def power_scope_adjustment(
    power: Power, game_data: GameData, gross: int, char: Character | None = None
) -> int:
    """Net points the power-scope modifiers add to (or take off) a power's ``gross`` cost."""

    return sum(term.amount for term in power_scope_terms(power, game_data, gross, char))


def power_gross_cost(power: Power, game_data: GameData, char: Character | None = None) -> int:
    """A power's cost from its effects alone, *before* any power-scope modifier.

    An ``array`` power is pooled here rather than summed (:func:`array_members_cost`),
    so the Alternate Effect points a Dynamic member costs are inside the gross a
    power-scope modifier is then charged against — they are part of what the power cost
    to build.

    This is the 98 in the rules' own worked example (p161) — the total of every effect
    with the extras and flaws applied to those effects — and the number Removable's
    per-5-points rate is charged against. Split out from :func:`power_total_cost` so the
    constructor can show both halves of that arithmetic, and so a caller that wants the
    undiscounted price (equipment does) need not rebuild the power to get it.
    """

    if power.structure == STRUCTURE_ARRAY and len(power.effects) > 1:
        return array_members_cost(
            [(effect_total_cost(e, game_data, char), e.dynamic) for e in power.effects],
            game_data,
        )
    return sum(effect_total_cost(e, game_data, char) for e in power.effects)


def power_total_cost(power: Power, game_data: GameData, char: Character | None = None) -> int:
    """Total power-point cost of a power (``docs/mm-powers-architecture.md`` §4).

    ``independent`` and ``linked`` powers cost the sum of their effects (linking
    is a +0 bundle). An ``array`` instead pays the costliest effect in full and a
    flat :func:`array_alternate_cost` for each remaining effect, since only one is
    active at a time — dearer for a **Dynamic** member, which is not mutually exclusive
    with its siblings (:func:`array_members_cost`). ``char`` is threaded to
    :func:`effect_total_cost` so a Strength-Based effect's folded-in ranks are priced
    against the wielder. That sum is :func:`power_gross_cost`.

    A **power-scope** modifier is then applied on top of it, once for the whole power
    rather than once per effect — Removable's "value per 5 total Power Points of the
    power's final cost, rounded up". The result is floored at 1 point for the same reason
    a flat flaw is (p150): a discount may make a power cheap, never free and never a
    refund. A power that costs nothing to begin with still costs nothing.

    A Dev-mode :attr:`~mm_companion.core.powers.Power.cost_override` replaces the
    whole computed total, so a homerule power spends exactly that many points; it
    flows through :func:`node_cost` into the character's power-point tally.
    """

    if power.cost_override is not None:
        return power.cost_override
    gross = power_gross_cost(power, game_data, char)
    if gross <= 0:
        return gross
    return max(1, gross + power_scope_adjustment(power, game_data, gross, char))


def array_cost_formula(members: Sequence[tuple[int, bool]], game_data: GameData) -> str:
    """The working behind one array's total, from the same ``(full cost, dynamic)`` pairs
    :func:`array_members_cost` prices; ``""`` for anything that is not a real array.

    An array is the one place a total cannot be got at by adding up the numbers already
    on the cards: three cards reading 10, 16 and 20 come to 23, because only the
    costliest is paid in full and the rest are a flat point or two on top. Without the
    working that is indistinguishable from a bug, which is the same complaint
    :func:`power_cost_formula` was built for over Removable.

    Members of a kind are collapsed rather than listed one by one — six alternates read
    ``6 × 1 alternate``, not six ``+ 1`` terms — because the point is the *rule* being
    applied, and a card that names each member is already above it.
    """

    if len(members) < 2:
        return ""
    costs = [cost for cost, _ in members]
    base = costs.index(max(costs))
    parts = [f"{costs[base]} base"]
    if members[base][1]:
        parts.append(f"{array_dynamic_primary_cost(game_data)} Dynamic base")
    for dynamic, label in ((False, "alternate"), (True, "Dynamic alternate")):
        count = sum(1 for i, (_, d) in enumerate(members) if i != base and d == dynamic)
        if not count:
            continue
        price = array_alternate_cost(game_data, dynamic=dynamic)
        parts.append(f"{price} {label}" if count == 1 else f"{count} × {price} {label}")
    return " + ".join(parts)


def power_cost_formula(power: Power, game_data: GameData, char: Character | None = None) -> str:
    """The working behind a power's total, or ``""`` when there is nothing to explain.

    Two things make a total anything other than the sum of the effect costs already shown
    on the cards, and each has its own clause. An **array** pools its members
    (:func:`array_cost_formula`), and a **power-scope** modifier is charged once against
    the whole power — Removable reads ``98 − 20 Removable``, the arithmetic the rules
    print (p161) and the thing a player is most likely to think is a bug.

    With both in play the array's working is parenthesised behind the gross it produces
    (``22 (20 base + 2 × 1 alternate) − 5 Removable``) rather than nested inside the
    subtraction, so the number the discount is charged against stays the one being read.
    An ordinary power has neither and gets the bare number.
    """

    if power.cost_override is not None:
        return ""
    gross = power_gross_cost(power, game_data, char)
    if gross <= 0:
        return ""
    working = ""
    if power.structure == STRUCTURE_ARRAY:
        working = array_cost_formula(
            [(effect_total_cost(e, game_data, char), e.dynamic) for e in power.effects],
            game_data,
        )
    terms = power_scope_terms(power, game_data, gross, char)
    if not terms:
        return working
    text = f"{gross} ({working})" if working else str(gross)
    for term in terms:
        sign = "−" if term.amount < 0 else "+"
        text += f" {sign} {abs(term.amount)} {term.modifier.name}"
    if gross + sum(term.amount for term in terms) < 1:
        text += " (1 minimum)"
    return text


def group_scope_note(group: PowerGroup, game_data: GameData, char: Character | None = None) -> str:
    """What a **power-scope** modifier costs a group, when several children carry one.

    Removable is charged "per 5 points of the power's final cost" and applies to "the
    power as a whole" (p161) — and a `Power` is where that whole stops. So a device
    modelled as a *group* of three removable powers is charged three separate discounts,
    each rounded up on its own, which is not the same number as one power with the same
    effects: three powers of 6 points are discounted 2 each, where an 18-point power is
    discounted 4.

    That is not a mistake to be corrected — three genuinely separate removable devices
    really are charged three times, and nothing here can tell that build from the other
    one. So this **states the arithmetic** rather than warning about it, and only when
    the two numbers actually differ. The honest single-device build is one power with
    many effects, which is how the book's own armour is written; a player who can see
    what the split is worth can decide which they meant.

    ``""`` when no modifier is doubled up, or when splitting changes nothing.
    """

    if group.mode == STRUCTURE_ARRAY:
        # An array pays for one member, so a second child's discount is not a second
        # discount on the same points — there is nothing to compare against.
        return ""
    children = [child for child in group.children if isinstance(child, Power)]
    charged: dict[str, list[PowerScopeTerm]] = {}
    for child in children:
        for term in power_scope_terms(
            child, game_data, power_gross_cost(child, game_data, char), char
        ):
            charged.setdefault(term.modifier.id, []).append(term)
    notes = []
    for terms in charged.values():
        if len(terms) < 2:
            continue
        separate = sum(term.amount for term in terms)
        # What one power holding every one of these effects would have been charged: the
        # costliest tier taken, against the summed gross.
        whole = sum(power_gross_cost(child, game_data, char) for child in children)
        dearest = max(terms, key=lambda t: abs(t.amount))
        units = (
            math.ceil(whole / dearest.modifier.cost_per_points)
            if dearest.modifier.cost_per_points > 0
            else 1
        )
        combined = (-1 if dearest.amount < 0 else 1) * dearest.magnitude * units
        if separate == combined:
            continue
        notes.append(
            f"{dearest.modifier.name} is charged once per power, so this group takes it "
            f"{len(terms)} times ({abs(separate)} points, where one power holding the "
            f"same effects would be {abs(combined)})."
        )
    return " ".join(notes)


def node_cost_formula(node: PowerNode, game_data: GameData, char: Character | None = None) -> str:
    """The working behind a *tree node's* total — the group-level twin of
    :func:`power_cost_formula`, and empty whenever there is nothing to explain.

    A group's children are whole cards, each already printing its own contribution, so
    the only group that owes an explanation is an ``array``: its total is
    :func:`array_cost_formula` over what those children cost at full rank, which is not
    the sum of the flat prices beside their names. A leaf falls through to its own
    formula, and a stunt — which costs nothing at all — explains itself on its card.
    """

    if power_is_stunt(node):
        return ""
    if isinstance(node, PowerGroup):
        if node.mode != STRUCTURE_ARRAY:
            return ""
        return array_cost_formula(
            [(node_cost(child, game_data, char), child.dynamic) for child in node.children],
            game_data,
        )
    return power_cost_formula(node, game_data, char)


def node_cost(node: PowerNode, game_data: GameData, char: Character | None = None) -> int:
    """Total point cost of a powers-tree node — a leaf power or a nested group.

    A leaf :class:`~mm_companion.core.powers.Power` costs its :func:`power_total_cost`.
    A :class:`~mm_companion.core.powers.PowerGroup` recurses: ``independent`` and
    ``linked`` groups sum their children (linking is a +0 bundle), while an ``array``
    group pays its costliest child in full plus a flat :func:`array_alternate_cost` for
    each other child (only one is active at a time), or the dearer Dynamic price for a
    child that shares the pool instead (:func:`array_members_cost`). Nesting is handled
    by the recursion — a child that is itself a group is priced the same way, and
    carries its own ``dynamic`` flag for the array it sits in.

    **A power stunt costs nothing.** It was bought with Extra Effort and a Hero Point
    rather than with Power Points (p20, p101), so it contributes 0 here — which is what
    keeps it out of ``powers_points_spent`` — while :func:`power_total_cost` still says
    what it *would* cost, since that is the number its ceiling is checked against
    (:func:`~.validation.power_stunt_violations`).
    """

    if power_is_stunt(node):
        return 0
    if isinstance(node, PowerGroup):
        costs = [node_cost(child, game_data, char) for child in node.children]
        if not costs:
            return 0
        if node.mode == STRUCTURE_ARRAY and len(costs) > 1:
            return array_members_cost(
                list(zip(costs, (child.dynamic for child in node.children), strict=True)),
                game_data,
            )
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
    only the flat :func:`array_alternate_cost`, since they share one pool — the dearer
    price when the child is **Dynamic**. A Dynamic *base* contributes its full
    :func:`node_cost` plus :func:`array_dynamic_primary_cost`, since making the primary
    Dynamic is bought on top of it rather than instead of it. Any other base child, or
    any node under a non-array parent (or at top level, ``parent=None``), contributes
    its full :func:`node_cost`.
    """

    if parent is not None and parent.mode == STRUCTURE_ARRAY and len(parent.children) > 1:
        base = group_array_base_index(parent, game_data, char)
        if parent.children[base] is not node:
            return array_alternate_cost(game_data, dynamic=node.dynamic)
        if node.dynamic:
            return node_cost(node, game_data, char) + array_dynamic_primary_cost(game_data)
    return node_cost(node, game_data, char)


# The pool's arithmetic needs point costs, so runtime cannot import it; it is handed over
# here instead (see :func:`~.runtime.set_dynamic_rank_cap`). Importing this module is what
# turns the pool on, and :mod:`mm_companion.core.rules` always does.
set_dynamic_rank_cap(dynamic_rank_cap)

# The same hand-over for the same reason: which effect of an array is its base is a
# question about cost, and an unselected array runs its base
# (:func:`~.runtime.active_array_effect_index`).
set_array_base_index(array_base_index)

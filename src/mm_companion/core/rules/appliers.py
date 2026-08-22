"""Data-driven stat effects — how a rank on the sheet becomes a bonus on a trait.

The lowest rules layer, below :mod:`.runtime`. Two halves:

* **The applier registry.** A stat effect is a *data record* (today the
  :class:`~mm_companion.core.components.TraitBoost` parsed from an effect's
  ``statIntegration``) plus an ``apply`` **kind** naming what that record *means*.
  Each kind is a registered :data:`StatApplier` — given an :class:`ApplyContext`
  (the record, the rank it stands at, the resolved target and who is granting it)
  it yields :class:`TraitContribution` s. Before this split, "the bonus is the
  effect's rank, added to the target" was hardcoded in ``power_trait_bonuses``;
  now it is one registered handler among five, and
  :func:`register_stat_applier` is the mod hook (the same shape as
  ``PATTERN_BEHAVIOURS`` and ``GATE_KINDS`` in :mod:`.runtime`).
* **The stacking resolver.** :func:`resolve_contributions` nets the
  contributions standing on one trait into a single :class:`TraitBonus`, and
  names what was **superseded** on the way. Bonuses bought with Power Points
  (powers, advantages) *sum*, as they always have; equipment does not stack with
  itself or with powers — M&M grants the best bonus, not their total — so the
  groups are compared with ``max`` rather than added.

Pure Python: this module reads :class:`~mm_companion.core.data_loader.GameData`
and nothing else from the app.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass

from ..components import (
    APPLY_BONUS,
    APPLY_PENALTY_REMOVED,
    APPLY_PENALTY_REPLACED,
    APPLY_SENSE,
    APPLY_SPEED,
    TraitBoost,
)
from ..data_loader import GameData, Skill
from ..registry import Registry

# --- vocabularies ---------------------------------------------------------------------

# Which list of the sheet a contribution lands in. The first three are *numeric traits*
# — the ones a bonus adds to a printed total (:data:`NUMERIC_CATEGORIES`). The rest are
# declared here so an applier has somewhere honest to put a non-numeric grant; the
# movement readout still derives its own lines in :mod:`.movement`, and nothing reads
# ``penalty`` yet (the equipment phases do).
#
# ``advantage`` is a trait a power can raise but *not* a number on a printed total:
# an Enhanced Advantage grants the advantage itself at the ranks it names, so it is
# deliberately outside :data:`NUMERIC_CATEGORIES` and read by the Advantages block
# rather than added to anything.
CATEGORY_ABILITY = "ability"
CATEGORY_RESISTANCE = "resistance"
CATEGORY_SKILL = "skill"
CATEGORY_ADVANTAGE = "advantage"
CATEGORY_MOVEMENT = "movement"
CATEGORY_SENSE = "sense"
CATEGORY_PENALTY = "penalty"

NUMERIC_CATEGORIES = (CATEGORY_ABILITY, CATEGORY_RESISTANCE, CATEGORY_SKILL)

# The ``affects`` categories a record may declare that mean "this raises a trait on
# the sheet" — the author's own statement of intent, which is why the :data:`APPLY_BONUS`
# applier honours it rather than inferring everything from the target key. (``defense``
# is here because the defence resistances live in the resistances list; ``advantage``
# because Enhanced Trait can raise one, even though an advantage totals nothing.) A
# record declaring no ``affects`` at all states nothing, and is taken at its target.
BOOST_TRAIT_CATEGORIES = frozenset({"ability", "resistance", "defense", "skill", "advantage"})

# How one contribution combines with the others on the same trait.
STACK_SUM = "sum"  # adds on top of everything else in its group
STACK_MAX = "max"  # only the largest of its group's exclusive contributions counts

# Which pool a contribution was bought out of. Bonuses within ``powers`` (powers and
# advantages — everything paid for with Power Points) sum; the groups are then compared
# with ``max``, never added, so gear cannot be piled on top of a power.
GROUP_POWERS = "powers"
GROUP_EQUIPMENT = "equipment"

# The group that does not compete: what the creature simply *is* (its size), which is
# added on top of whichever bought group won rather than being weighed against it. A
# suit of armour must not delete a Colossal creature's Toughness, and a Colossal
# creature must not stop the armour counting either.
GROUP_INTRINSIC = "intrinsic"

# The other group that does not compete: what the character is straining past their
# limits for. Extra Effort's benefits "can even increase your ranks or bonuses beyond
# the normal Power Level limits" (p20), so a pushed rank is added on top of whatever the
# build already nets rather than weighed against it — a Strength pushed by 1 is 1 more
# than the character had, however they came to have it.
GROUP_EFFORT = "effort"

#: Groups that are always added rather than competing. Kept as a set so a mod can
#: neither be surprised by the special case nor need a second resolver to express one.
ALWAYS_ADDED_GROUPS = frozenset({GROUP_INTRINSIC, GROUP_EFFORT})


@dataclass(frozen=True)
class TraitContribution:
    """One thing on the sheet raising (or clearing a penalty on) one trait.

    ``stat`` is the trait key — an ability/resistance key, a skill name or row id, a
    movement mode — and ``category`` says which list it belongs to. ``source`` is what
    the sheet names as granting it (a power's display name, an advantage's name, an
    item's). ``stacking`` and ``group`` decide how :func:`resolve_contributions` nets it
    against its neighbours, and ``kind`` records which applier produced it.

    ``origin`` is the **id** of the thing on the sheet whose card has to explain this —
    an equipment item's, today. It is deliberately not the same as ``source``: two
    identical Leather Armours share a display name, and matching a superseded bonus by
    name and amount had both of their cards claiming to be the one that lost. Powers
    leave it empty, since a power's card explains itself from its own build.
    """

    amount: int
    stat: str
    category: str
    source: str
    stacking: str = STACK_SUM
    group: str = GROUP_POWERS
    kind: str = APPLY_BONUS
    origin: str = ""


@dataclass(frozen=True)
class SupersededBonus:
    """A contribution that was outclassed — what the card explains away.

    An item whose +2 Toughness loses to a power's +4 still sits on the sheet, and a
    silently inert bonus reads as a bug. ``beaten_by`` names the source that won, and
    ``origin`` carries the losing contribution's id straight through — that is what lets
    a card ask "was it *me* that lost" rather than comparing a name and an amount, which
    two copies of the same item answer identically.
    """

    source: str
    amount: int
    beaten_by: str
    origin: str = ""


@dataclass(frozen=True)
class TraitBonus:
    """The net standing bonus on one trait, and what it cost to get there.

    ``amount`` is the resolved number; ``sources`` names every contributor that
    counted towards it, in the order they were gathered, so the UI can explain the
    boost on a tooltip. ``superseded`` holds the contributions that did *not* count
    because something better was already there — empty whenever everything stacked.
    """

    amount: int
    sources: tuple[str, ...]
    superseded: tuple[SupersededBonus, ...] = ()


# --- the applier registry -------------------------------------------------------------


@dataclass(frozen=True)
class ApplyContext:
    """Everything one stat effect needs to say what it contributes.

    ``record`` is the data (the parsed :class:`TraitBoost`); ``rank`` is the rank it
    currently stands at; ``target`` is the trait it was resolved against (the player's
    choice for a configurable boost, the baked-in target otherwise). ``stacking`` and
    ``group`` are the *granter's* terms — a power stacks, an equipment item does not
    unless its owner ticked "stacks with other bonuses" — and travel through onto every
    contribution the applier yields, as does ``origin`` (the granting item's id; see
    :class:`TraitContribution`).
    """

    record: TraitBoost
    rank: int
    target: str
    source: str
    game_data: GameData
    stacking: str = STACK_SUM
    group: str = GROUP_POWERS
    origin: str = ""

    @property
    def amount(self) -> int:
        """What the record is worth at this rank: ``flat + rank × per_rank``.

        Both scalars are data (``statIntegration``'s ``amountFlat`` / ``amountPerRank``)
        and default to the historical rule — the bonus *is* the rank.
        """

        return self.record.flat + self.rank * self.record.per_rank


StatApplier = Callable[[ApplyContext], tuple[TraitContribution, ...]]

STAT_APPLIERS: Registry[StatApplier] = Registry("statIntegration.apply")


def register_stat_applier(kind: str, applier: StatApplier, *, replace: bool = False) -> StatApplier:
    """Bind an ``apply`` kind to the handler that turns its records into contributions.

    The mod hook for the stat layer: a mod's Python module registers a kind, its data
    files name that kind in an effect's ``statIntegration.apply``, and the engine routes
    to it with no further edits. An *unregistered* kind yields nothing rather than
    raising — an effect declaring a kind whose mod is disabled simply grants no bonus.
    """

    return STAT_APPLIERS.register(kind, applier, replace=replace)


def apply_stat_effect(kind: str, context: ApplyContext) -> tuple[TraitContribution, ...]:
    """Run the applier for ``kind``, or yield nothing when none is registered."""

    applier = STAT_APPLIERS.get(kind)
    return applier(context) if applier is not None else ()


#: Separates a trait key from the *qualifier* narrowing it — the same separator the
#: character's own skill rows are keyed by (``"Expertise::Law"`` for a focus,
#: ``"Stealth::spec::Urban"`` for a specialized pool; see
#: :attr:`~mm_companion.core.character.Character.skill_ranks`). An advantage bought for a
#: subject reuses it (``"Improved Critical::Sword"``), so one convention covers every
#: trait a power can name and a qualified key *is* the row id the sheet already knows.
TRAIT_QUALIFIER_SEP = "::"

#: Marks a *specialized* (narrow, half-cost) rank pool inside a skill row id's qualifier
#: — the ``"Stealth::spec::Urban"`` shape :attr:`Character.specializations` produces.
#: Beside the separator because it is the other half of one key format, and three
#: modules (display, pricing, the picker) have to read it the same way.
SPECIALIZED_ROW_MARKER = "spec::"


def split_trait_key(target: str) -> tuple[str, str]:
    """A trait key as ``(base, qualifier)`` — ``"Expertise::Law"`` → ``("Expertise", "Law")``.

    The qualifier is ``""`` for an unqualified key, which is every ability, resistance,
    plain skill and plain advantage. A specialized pool keeps its ``"spec::"`` marker
    inside the qualifier (``("Stealth", "spec::Urban")``) rather than splitting twice:
    the marker is part of *which row* is meant, and the cost rules read it.
    """

    base, _sep, qualifier = target.partition(TRAIT_QUALIFIER_SEP)
    return base, qualifier


def trait_category(game_data: GameData, target: str) -> str:
    """Which trait list ``target`` belongs to — ability/resistance/skill/advantage, or ``""``.

    A target naming nothing the sheet tracks (a descriptor an Immunity names, a sense)
    resolves to ``""``, which is how the :data:`APPLY_BONUS` applier declines to
    contribute rather than inventing a row.

    Advantages resolve **last**, after the three keyed lists, so a target that is both
    a skill name and an advantage name reads as the skill it always did. The same order
    is what :func:`~.trait_rates.trait_rate` prices in, so the list a target lands in and
    the rate it is charged at can never disagree.

    A *qualified* key (:func:`split_trait_key`) is tried whole first and then by its
    base, so ``"Expertise::Law"`` lands in the skills and ``"Improved Critical::Sword"``
    in the advantages. Whole-first matters: a trait whose own name happens to contain the
    separator would otherwise be resolved as something else entirely.
    """

    for key in trait_key_candidates(target):
        if any(a.key == key for a in game_data.abilities):
            return CATEGORY_ABILITY
        if any(r.key == key for r in game_data.resistances):
            return CATEGORY_RESISTANCE
        if any(s.name == key for s in game_data.skills):
            return CATEGORY_SKILL
        if any(a.name == key for a in game_data.advantages):
            return CATEGORY_ADVANTAGE
    return ""


def skill_for_row(game_data: GameData, row_id: str) -> Skill | None:
    """Resolve a skill *row id* to its :class:`Skill` record.

    A row id is a skill name, ``"<Skill>::<focus>"`` for a focused instance, or
    ``"<Skill>::spec::<name>"`` for a specialized pool; all three map back to the same
    base skill. The whole id is tried before its base, so a skill whose name somehow
    contained the separator would still resolve to itself.

    A bare ``"<Skill>: <focus>"`` — one colon, the *display* form — resolves too. It is
    not what the sheet writes, but it is what a hand-written or pre-``::`` save can hold,
    and reading it as an unknown skill would silently drop that row's ability.

    Down here rather than beside the cost rates because both sides of the sheet need it
    and they sit on opposite sides of the dependency chain: the derived totals resolve a
    row to know which ability it adds, and :func:`~.trait_rates.skill_row_rate` resolves
    the same row to know what it costs.
    """

    by_name = {s.name: s for s in game_data.skills}
    if row_id in by_name:
        return by_name[row_id]
    base = split_trait_key(row_id)[0]
    return by_name.get(base) or by_name.get(base.split(":", 1)[0].strip())


def trait_key_candidates(target: str) -> tuple[str, ...]:
    """``target`` itself, then its base when it carries a qualifier.

    The lookup order every resolver over trait keys walks — :func:`trait_category` here
    and :func:`~.trait_rates.trait_rate` next door — so which list a target lands in and
    what it is charged can never be decided by different rules.
    """

    base, qualifier = split_trait_key(target)
    return (target, base) if qualifier else (target,)


def _contribution(context: ApplyContext, category: str, kind: str) -> tuple[TraitContribution, ...]:
    """One contribution built from a context, or none when it names no target."""

    if not context.target or not category:
        return ()
    return (
        TraitContribution(
            amount=context.amount,
            stat=context.target,
            category=category,
            source=context.source,
            stacking=context.stacking,
            group=context.group,
            kind=kind,
            origin=context.origin,
        ),
    )


@STAT_APPLIERS.handler(APPLY_BONUS)
def _apply_bonus(context: ApplyContext) -> tuple[TraitContribution, ...]:
    """Add the record's amount to a numeric trait — Enhanced Trait, Protection.

    The category is inferred from which trait list the target key belongs to, so an
    effect that boosts something the sheet does not track (Immunity naming a
    descriptor) contributes nothing — nor does a record whose ``affects`` says it
    raises senses or movement, whatever its target happens to name. This is the
    historical behaviour of ``power_trait_bonuses``, now expressed as one registered
    kind.
    """

    record = context.record
    if record.affects and not (record.affects & BOOST_TRAIT_CATEGORIES):
        return ()
    return _contribution(context, trait_category(context.game_data, context.target), APPLY_BONUS)


@STAT_APPLIERS.handler(APPLY_SPEED)
def _apply_speed(context: ApplyContext) -> tuple[TraitContribution, ...]:
    """Grant ranks of a named movement mode.

    The Speed readout still derives its lines from the powers themselves
    (:mod:`.movement`); this kind is how a record *declares* a movement grant, and is
    what a vehicle's speed rank travels on once the equipment block reaches the System
    readout.
    """

    return _contribution(context, CATEGORY_MOVEMENT, APPLY_SPEED)


@STAT_APPLIERS.handler(APPLY_SENSE)
def _apply_sense(context: ApplyContext) -> tuple[TraitContribution, ...]:
    """Grant ranks of a named sense quality (no numeric trait moves)."""

    return _contribution(context, CATEGORY_SENSE, APPLY_SENSE)


@STAT_APPLIERS.handler(APPLY_PENALTY_REMOVED)
def _apply_penalty_removed(context: ApplyContext) -> tuple[TraitContribution, ...]:
    """Cancel a penalty standing on a trait — the amount is how much of it goes away.

    Gear that lifts a circumstance penalty (a light source against a darkness penalty)
    rather than granting a bonus. Kept apart from :data:`APPLY_BONUS` because the two
    net differently: a removed penalty cannot push a trait above its unpenalised value.
    """

    return _contribution(context, CATEGORY_PENALTY, APPLY_PENALTY_REMOVED)


@STAT_APPLIERS.handler(APPLY_PENALTY_REPLACED)
def _apply_penalty_replaced(context: ApplyContext) -> tuple[TraitContribution, ...]:
    """Replace a penalty with a smaller one — the amount is the penalty that remains."""

    return _contribution(context, CATEGORY_PENALTY, APPLY_PENALTY_REPLACED)


# --- the stacking resolver ------------------------------------------------------------


def _resolve_group(
    contributions: Sequence[TraitContribution],
) -> tuple[int, tuple[str, ...], tuple[TraitContribution, ...]]:
    """Net one group: its stackers sum, and the best of its exclusives joins them.

    Returns the group's amount, the sources that counted (in gathering order), and the
    exclusive contributions the best one shut out.
    """

    exclusive = [c for c in contributions if c.stacking == STACK_MAX]
    best = max(exclusive, key=lambda c: c.amount, default=None)
    counted = [c for c in contributions if c.stacking != STACK_MAX or c is best]
    amount = sum(c.amount for c in counted)
    shut_out = tuple(c for c in exclusive if c is not best)
    return amount, tuple(c.source for c in counted), shut_out


def resolve_contributions(contributions: Iterable[TraitContribution]) -> TraitBonus | None:
    """Net every contribution standing on **one** trait into a single :class:`TraitBonus`.

    Three rules, and they are not the same rule:

    * **Within a group**, the ``sum`` contributions add up and the largest ``max`` one
      joins them — so several powers stack (as they always have) while several pieces
      of armour do not, and an item its owner marked "stacks with other bonuses" adds
      on top of the winner.
    * **Between the bought groups**, the larger total wins outright. Gear is never added
      to a power's bonus; M&M grants the better of the two. Ties go to the group seen
      first, which is the powers group whenever there is one.
    * **An intrinsic group does not compete at all** (:data:`ALWAYS_ADDED_GROUPS`). What
      the creature *is* — its size — is added on top of whichever bought group won, and
      neither supersedes nor is superseded. Weighing it against gear would let a suit of
      armour delete a Colossal creature's Toughness, and letting it win would delete the
      armour.

    Everything that did not count is reported in :attr:`TraitBonus.superseded`, named
    against whichever source won, so a card can say *why* its bonus is not on the
    sheet. Returns ``None`` when there is nothing at all.
    """

    gathered = list(contributions)
    if not gathered:
        return None

    groups: dict[str, list[TraitContribution]] = {}
    intrinsic: list[TraitContribution] = []
    for contribution in gathered:
        if contribution.group in ALWAYS_ADDED_GROUPS:
            intrinsic.append(contribution)
        else:
            groups.setdefault(contribution.group, []).append(contribution)

    # Intrinsic contributions always sum: their amounts are legitimately negative (a
    # small creature's Defense is a *bonus*, its Stealth penalty is not), and an
    # exclusive one would be picked or dropped by a comparison that means nothing here.
    extra = sum(c.amount for c in intrinsic)
    extra_sources = tuple(c.source for c in intrinsic)

    if not groups:  # nothing bought stands on this trait — the intrinsic part is all of it
        return TraitBonus(extra, extra_sources, ())

    resolved = {key: _resolve_group(members) for key, members in groups.items()}
    winner = max(resolved, key=lambda key: resolved[key][0])
    amount, sources, shut_out = resolved[winner]

    # Whatever won names itself to everything it beat: the winning group's own
    # shut-out exclusives, plus every member of every losing group. The intrinsic
    # contributions are in neither list — they never lost.
    beaten_by = max(groups[winner], key=lambda c: c.amount).source
    superseded: list[SupersededBonus] = []
    for key, members in groups.items():
        beaten = shut_out if key == winner else tuple(members)
        superseded.extend(SupersededBonus(c.source, c.amount, beaten_by, c.origin) for c in beaten)

    return TraitBonus(amount + extra, sources + extra_sources, tuple(superseded))


def resolve_bonuses(
    contributions: Iterable[TraitContribution],
) -> dict[str, dict[str, TraitBonus]]:
    """Net a whole sheet's contributions, grouped ``category -> {stat: TraitBonus}``.

    Each trait is resolved independently by :func:`resolve_contributions`; categories
    and stats appear in the order their first contribution was gathered.
    """

    buckets: dict[str, dict[str, list[TraitContribution]]] = {}
    for contribution in contributions:
        bucket = buckets.setdefault(contribution.category, {})
        bucket.setdefault(contribution.stat, []).append(contribution)

    result: dict[str, dict[str, TraitBonus]] = {}
    for category, stats in buckets.items():
        resolved = {}
        for stat, members in stats.items():
            bonus = resolve_contributions(members)
            if bonus is not None:
                resolved[stat] = bonus
        result[category] = resolved
    return result

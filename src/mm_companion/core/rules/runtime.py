"""Runtime power state and standing trait bonuses (lowest rules layer).

Which passive effect is currently on (gating), array/live-power selection, and the
stat contributions a character's active powers make. Everything that reads an
"effective" trait builds on this module, so it depends on nothing in the rules
package except :mod:`.appliers` — which decides what a stat effect *means* once
this module has decided it is on.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from ..character import Character
from ..components import (
    GATE_REMOVABLE,
    GATE_REQUIRES_EFFECT,
    GATE_TOGGLE,
    INSTANT_ACTION,
    PASSIVE_PERMANENT,
    PASSIVE_TOGGLE,
    RESOURCE_POOL,
)
from ..data_loader import GameData
from ..equipment import EquipmentItem
from ..powers import STRUCTURE_ARRAY, Power, PowerEffectInstance, PowerGroup, PowerNode
from ..registry import Registry
from .accessories import item_effective_build
from .appliers import (
    BOOST_TRAIT_CATEGORIES,
    GROUP_EQUIPMENT,
    GROUP_POWERS,
    NUMERIC_CATEGORIES,
    SPECIALIZED_ROW_MARKER,
    STACK_MAX,
    STACK_SUM,
    ApplyContext,
    TraitBonus,
    TraitContribution,
    apply_stat_effect,
    resolve_bonuses,
    split_trait_key,
)

# The trait categories a ``TraitBoost`` can name that map to a numeric trait bonus on
# the sheet. Defined by (and shared with) the ``bonus`` applier that enforces it; kept
# under this name because the constructor's target picker reads it from here.
TRAIT_CATEGORIES = BOOST_TRAIT_CATEGORIES


# --- statIntegration pattern registry -------------------------------------------------
# How an effect's contribution reaches the sheet (§5). Each of the base patterns
# registers a :class:`PatternBehaviour`: ``standing`` — does it sit on the sheet as a
# live bonus (the passive patterns) rather than an on-demand / resource-pool effect that
# never stands; ``toggled`` — does it carry an implicit player on/off switch (a
# Sustained/Continuous ``passive_toggle``). A mod's Python module can register another
# pattern. An *unregistered* pattern resolves to ``None`` and each call site keeps its
# pre-registry default (fall through in :func:`effect_is_active`, non-standing in
# :func:`power_has_standing_effect`, no implied toggle) — behaviour unchanged.


@dataclass(frozen=True)
class PatternBehaviour:
    """The runtime traits of a statIntegration pattern (§5-6)."""

    standing: bool  # contributes a standing bonus that can sit on the sheet
    toggled: bool  # carries an implicit player toggle (a passive_toggle effect)


PATTERN_BEHAVIOURS: Registry[PatternBehaviour] = Registry("statIntegration.pattern")
PATTERN_BEHAVIOURS.register(PASSIVE_PERMANENT, PatternBehaviour(standing=True, toggled=False))
PATTERN_BEHAVIOURS.register(PASSIVE_TOGGLE, PatternBehaviour(standing=True, toggled=True))
PATTERN_BEHAVIOURS.register(INSTANT_ACTION, PatternBehaviour(standing=False, toggled=False))
PATTERN_BEHAVIOURS.register(RESOURCE_POOL, PatternBehaviour(standing=False, toggled=False))


# --- runtime gate registry ------------------------------------------------------------
# What can switch an otherwise-active effect off (§7). Each gate kind registers a
# predicate ``(power, effect) -> bool`` answering "does this gate currently block the
# effect?". Only the gates that gate *here* register a blocker: the Activation gate is
# the power's master switch (``power.activated``, checked directly) and the Limited gate
# is informational, so neither registers and both are ignored in
# :func:`effect_is_active`. A mod can register a new gate kind.
GateBlock = Callable[[Power, PowerEffectInstance], bool]
GATE_KINDS: Registry[GateBlock] = Registry("gate.kind")


@GATE_KINDS.handler(GATE_REMOVABLE)
def _gate_removable(power: Power, effect: PowerEffectInstance) -> bool:
    """A Removable gate blocks while the associated item is absent."""
    return not power.item_present


@GATE_KINDS.handler(GATE_TOGGLE)
def _gate_toggle(power: Power, effect: PowerEffectInstance) -> bool:
    """A toggle gate blocks while the player has switched the effect off."""
    return not effect.toggled_on


# --- the Dynamic point pool's hook into rank ------------------------------------------
# A Dynamic array member runs at "reduced effectiveness" in proportion to the share of
# the array's point pool it currently holds (p101), and working that share out needs the
# member's **point cost** — which lives a layer above this module, since
# :mod:`.powers_cost` imports this one and not the other way about. So the arithmetic is
# *injected* rather than imported: importing ``powers_cost`` installs the resolver here
# (see :func:`set_dynamic_rank_cap`), and until it does — or in a build that never loads
# it — the hook is ``None`` and every rank reads exactly as it did before the pool
# existed. Same bargain as the registries above: nothing registered, nothing changes.
#
# The resolver answers "what rank is this effect held to by the pool?" — ``None`` when
# the effect is under no allocated array at all, which is every effect on a character
# nobody has split a pool on.
DynamicRankCap = Callable[[PowerEffectInstance, GameData, "Character | None"], int | None]

_dynamic_rank_cap: DynamicRankCap | None = None


def set_dynamic_rank_cap(resolver: DynamicRankCap | None) -> None:
    """Install the resolver :func:`effect_current_rank` asks about the Dynamic pool.

    Called once by :mod:`.powers_cost` at import. Passing ``None`` uninstalls it, which
    is what a test wanting the pre-pool behaviour back does.
    """

    global _dynamic_rank_cap
    _dynamic_rank_cap = resolver


# The second injected resolver, and injected for exactly the reason above: which effect
# of an array is its *base* is a question about cost (the costliest is paid in full),
# and cost lives one layer up. It answers "which index is this power's base effect?".
ArrayBaseIndex = Callable[[Power, GameData, "Character | None"], int]

_array_base_index: ArrayBaseIndex | None = None


def set_array_base_index(resolver: ArrayBaseIndex | None) -> None:
    """Install the resolver :func:`active_array_effect_index` asks for an array's base.

    Called once by :mod:`.powers_cost` at import, like :func:`set_dynamic_rank_cap`.
    Without it an unselected array falls back to its first effect, which is what the
    group level does anyway (:func:`active_array_child`).
    """

    global _array_base_index
    _array_base_index = resolver


def _boost_target(effect: PowerEffectInstance, boost) -> str:
    """The trait a :class:`TraitBoost` names, whatever kind of trait it is.

    The player's choice (``config['target']``) for a ``configurable`` boost, the
    baked-in ``target`` (e.g. Protection's ``"TOUGHNESS"``) otherwise. Unfiltered:
    deciding whether the named trait is one the sheet totals is the *applier's* job
    (:mod:`.appliers`), since a movement or sense grant names a target too.
    """

    return (effect.config.get("target", "") if boost.configurable else boost.target) or ""


#: The ``repeatable`` column ``type`` whose stored value is a trait key. A config field
#: carrying one is a *trait allocation*: each row names a trait and the ranks put into
#: it, which is how Enhanced Trait raises several traits at once out of one rank pool.
TRAIT_COLUMN_TYPE = "trait"


def trait_allocation_field(record) -> tuple[object, str, str] | None:
    """An effect's or modifier's trait-allocation field as ``(field, trait key, rank key)``.

    The first ``repeatable`` config field carrying a :data:`TRAIT_COLUMN_TYPE` column,
    with the column keys its rows are shaped by. ``None`` for a record that has none —
    which is every record but Enhanced Trait and its Reduced Trait flaw today, and is
    exactly what keeps the single-target path below alive for all of them.

    The rank key is ``""`` when the field declares no ``int`` column, in which case each
    row counts as one rank.
    """

    for field in getattr(record, "config_fields", ()):
        if field.type != "repeatable":
            continue
        trait_key = next((c.key for c in field.columns if c.type == TRAIT_COLUMN_TYPE), "")
        if not trait_key:
            continue
        rank_key = next((c.key for c in field.columns if c.type == "int"), "")
        return field, trait_key, rank_key
    return None


def config_trait_allocation(config: dict, record) -> tuple[tuple[str, int], ...]:
    """The ``(trait, ranks)`` rows a config dict holds for ``record``'s allocation field.

    Rows missing a trait or standing at zero ranks are dropped — a half-filled row in
    the constructor is a row the player is still typing, not a trait worth nothing.
    Shared by the effect's own allocation and a modifier selection's (Reduced Trait),
    since both store the same shape against the same kind of field.
    """

    found = trait_allocation_field(record)
    if found is None:
        return ()
    field, trait_key, rank_key = found
    rows: list[tuple[str, int]] = []
    for row in config.get(field.key) or ():
        if not isinstance(row, dict):
            continue
        target = str(row.get(trait_key, "") or "")
        ranks = int(row.get(rank_key, 0) or 0) if rank_key else 1
        if target and ranks:
            rows.append((target, ranks))
    return tuple(rows)


def boost_allocations(
    effect: PowerEffectInstance,
    base,
    boost,
    *,
    live: bool = False,
    game_data: GameData | None = None,
    char: Character | None = None,
) -> tuple[tuple[str, int], ...]:
    """Which traits one effect raises, and by how many ranks each.

    An Enhanced Trait spreads its rank across several traits at once (the book's own
    Berserker Rage is *Enhanced Advantage: Fearless 2* plus *Enhanced Strength*), so a
    boost is a *list* of targets rather than one. The rows come from the effect's
    trait-allocation config field.

    Falling back to a single ``(target, effect rank)`` pair is what keeps everything
    that predates the allocation working untouched: Protection's baked-in
    ``"TOUGHNESS"``, a shield's authored ``config["target"]``, and every character
    saved before this existed. Nothing is migrated on load — an instance with no rows
    simply reads as the one target it always had.

    ``live`` asks for the boost *as it currently stands* rather than as it was bought,
    so a dialled-down effect grants less — and so does one held down by its share of a
    Dynamic array's point pool, which is why the wielder and the game data travel with
    it (:func:`effect_current_rank`). It reaches only
    the single-target fallback, and deliberately: an allocation's rows each carry a rank
    the player wrote down, and there is no honest way to turn "3 ranks of Stealth and 1
    of Treatment" half off. The effects that carry rows are the ones whose rank *is*
    their allocation, which is exactly where the constructor declines to offer a dial.

    Cost never passes ``live``. What a power is worth is what it was bought at.
    """

    rows = config_trait_allocation(effect.config, base)
    if rows:
        return rows
    rank = effect_current_rank(effect, game_data, char) if live else effect.rank
    return ((_boost_target(effect, boost), rank),)


def _resolved_trait_target(effect: PowerEffectInstance, base) -> str:
    """The **numeric** trait key one effect boosts, or ``""`` when it isn't one.

    :func:`_boost_target` narrowed to the boosts whose ``affects`` names a raisable
    trait category. Single-target by definition — for an effect that may raise several
    at once, ask :func:`resolved_trait_allocation`.
    """

    boost = base.integration.trait_boost if base.integration else None
    if boost is None or not (boost.affects & TRAIT_CATEGORIES):
        return ""
    return _boost_target(effect, boost)


def resolved_trait_allocation(
    effect: PowerEffectInstance,
    base,
    game_data: GameData | None = None,
    char: Character | None = None,
) -> tuple[tuple[str, int], ...]:
    """Every trait one effect raises and by how much, or ``()`` when it raises none.

    :func:`boost_allocations` narrowed to the boosts whose ``affects`` names a raisable
    trait category — what the game-terms summary renders its "Trait +N" line from. An
    Enhanced Trait returns one pair per allocated row; Protection returns its single
    fixed target, and an effect with no target chosen returns nothing at all.

    Read **live**, like the contributions it describes: a Protection dialled down to 2
    says "Toughness +2", because a row claiming +4 beside a sheet showing +2 would be
    the summary disagreeing with the numbers it summarises.
    """

    boost = base.integration.trait_boost if base.integration else None
    if boost is None or not (boost.affects & TRAIT_CATEGORIES):
        return ()
    allocation = boost_allocations(effect, base, boost, live=True, game_data=game_data, char=char)
    return tuple((target, ranks) for target, ranks in allocation if target)


def trait_display_name(game_data: GameData, target: str) -> str:
    """The display name for a trait key — ``"AGL"`` → ``"Agility"``.

    Skills and advantages are named by their key, since the key *is* the name. A
    *qualified* key (:func:`~.appliers.split_trait_key`) is rendered by its two halves so
    a stored row id never reaches a reader raw: ``"Expertise::Law"`` reads
    ``"Expertise: Law"``, ``"Stealth::spec::Urban"`` reads ``"Stealth: Urban
    (specialized)"``, and an advantage bought for a subject reads
    ``"Improved Critical (Sword)"`` — the same shapes the Skills and Advantages blocks
    print, so the Enhances row and the sheet agree.

    Anything unrecognised passes through unchanged; a descriptor an Immunity names is
    free text and has no better name than itself.
    """

    base, qualifier = split_trait_key(target)
    plain = _plain_trait_display_name(game_data, base)
    if not qualifier:
        return plain
    if qualifier.startswith(SPECIALIZED_ROW_MARKER):
        return f"{plain}: {qualifier[len(SPECIALIZED_ROW_MARKER):]} (specialized)"
    if any(s.name == base for s in game_data.skills):
        return f"{plain}: {qualifier}"
    # An advantage's subject is a parenthetical, the way the Advantages block prints it.
    return f"{plain} ({qualifier})"


def _plain_trait_display_name(game_data: GameData, target: str) -> str:
    """The display name for an *unqualified* trait key."""

    for a in game_data.abilities:
        if a.key == target:
            return a.name
    for r in game_data.resistances:
        if r.key == target:
            return r.name
    return target  # skills, advantages (and anything else) display by their key/name


def _effect_gates(effect: PowerEffectInstance, game_data: GameData) -> set[str]:
    """The runtime gate kinds an effect carries, from its attached modifiers (§7)."""

    catalog = game_data.modifier_catalog()
    gates: set[str] = set()
    for sel in list(effect.extras) + list(effect.flaws):
        mod = catalog.get(sel.modifier_id)
        if mod and mod.gate:
            gates.add(mod.gate)
    return gates


def effect_current_rank(
    effect: PowerEffectInstance,
    game_data: GameData | None = None,
    char: Character | None = None,
) -> int:
    """The rank an effect is *currently running at* — its bought rank unless dialled down.

    :attr:`~mm_companion.core.powers.PowerEffectInstance.current_rank` is runtime state
    (a Growth 3 held at Large rather than Gargantuan), so it is clamped here rather than
    trusted: ``None`` means all the way up, and a value left over from a larger bought
    rank comes back as the rank the effect actually has. The floor is 1, not 0 — "off"
    is the effect's own toggle, not a zeroth rung, and a rank-0 effect would quietly
    read as a rank-1 one nowhere else.

    **The Dynamic point pool is the one thing that can push it to 0.** Given the wielder
    and the game data, an effect inside an array member holding a share of that array's
    pool is capped at what the share buys (:func:`set_dynamic_rank_cap`) — the book's own
    example is a Flight 5 costing 10 points held to 1 rank by the 2 points assigned to it
    (p101). A member holding too little for even rank 1 caps at 0 and is simply off,
    which is what "a character can maintain the Dynamic Alternate Effect ... so long as
    at least 2 Power Points are assigned to it" means from the other side. Without both
    arguments — the Power Constructor, where nothing is dialled and nothing is wielded —
    the cap is not asked for and the bought rank comes back, exactly as it always did.

    **Extra Effort is the one thing that pushes it up**, and it is added last, after
    every clamp above: pushing an effect past what it was bought at is the whole of
    "straining body and mind to do more when it really counts", and the benefit
    explicitly ignores the Power Level limits (p20). A member held to 0 by its share of
    a Dynamic pool can therefore still be pushed to 1, which is the same rule read from
    the other end. What it costs is a rung of the fatigue ladder, and that is charged on
    the *character* (:func:`~.extra_effort.spend_extra_effort`), never here.

    Cost never asks this. What a power is *worth* is what it was bought at, and dialling
    one down mid-fight refunds nothing.
    """

    if effect.current_rank is None:
        rank = effect.rank
    else:
        rank = max(1, min(effect.rank, int(effect.current_rank)))
    push = max(0, effect.extra_effort)
    if game_data is None or char is None or _dynamic_rank_cap is None:
        return rank + push
    cap = _dynamic_rank_cap(effect, game_data, char)
    return (rank if cap is None else max(0, min(rank, cap))) + push


def effect_is_active(
    power: Power,
    effect: PowerEffectInstance,
    base,
    game_data: GameData,
    char: Character | None = None,
    *,
    _skip_requires: bool = False,
) -> bool:
    """Whether a passive effect's standing bonus currently applies (§6).

    Instant-action and resource-pool effects are never standing contributors. An
    otherwise-passive effect is on unless a gate switches it off: a runtime Nullify
    (``effect.suppressed``); the power's master on/off switch (``power.activated``);
    an array member the player hasn't currently selected (``power.array_active`` —
    only one member of an array is active at a time); a Sustained/Continuous toggle
    the player has turned off (``effect.toggled_on``, for a ``passive_toggle``
    pattern or a toggle-gated effect); or a Removable gate whose item is absent
    (``power.item_present``). The Limited gate is informational — the player
    self-applies it — and never gates here.

    A Requires-Effect gate (the *Limited to While Insubstantial* flaw) instead reaches
    across the character: the effect's bonus applies only while some *other* live power
    has the named base effect (e.g. Insubstantial) currently active. This needs
    ``char`` to scan the wielder's other powers; without one the cross-effect gate is
    left un-enforced (the effect stays on). ``_skip_requires`` short-circuits that scan
    to bound the recursion when this function is itself asked whether the required
    effect is active.

    ``power.activated`` is a master switch, not only the Activation gate's flag: a
    linked group turning off (see the section's ``_set_group_active``) clears it on
    every member — including a permanent, ungated one — so the whole bundle drops
    together. It defaults on, so an untouched power is unaffected.
    """

    pattern = base.integration.pattern if base.integration else ""
    behaviour = PATTERN_BEHAVIOURS.get(pattern)
    if behaviour is not None and not behaviour.standing:  # instant-action / resource-pool
        return False
    if effect.suppressed:
        return False
    if not power.activated:  # master on/off (also the Activation gate)
        return False
    if not power.array_active:  # an array member not currently selected as active
        return False
    if not effect_is_selected(power, effect, game_data, char):
        return False  # ...and the same rule one level down, among the power's own effects
    gates = _effect_gates(effect, game_data)
    if behaviour is not None and behaviour.toggled:  # passive_toggle implies a toggle gate
        gates = gates | {GATE_TOGGLE}
    for gate in gates:
        blocker = GATE_KINDS.get(gate)
        if blocker is not None and blocker(power, effect):
            return False
    if not _skip_requires and char is not None and GATE_REQUIRES_EFFECT in gates:
        if not _requires_effect_met(effect, game_data, char):
            return False
    return True


def _requires_effect_met(effect: PowerEffectInstance, game_data: GameData, char: Character) -> bool:
    """Whether every Requires-Effect gate on ``effect`` is currently satisfied.

    Each such flaw names a base effect id (``requires_effect_id``) that must be active
    on the wielder — the effect's bonus only stands while that effect stands.
    """

    catalog = game_data.modifier_catalog()
    for sel in list(effect.extras) + list(effect.flaws):
        mod = catalog.get(sel.modifier_id)
        if mod and mod.gate == GATE_REQUIRES_EFFECT and mod.requires_effect_id:
            if not _has_active_effect(char, game_data, mod.requires_effect_id):
                return False
    return True


def _has_active_effect(char: Character, game_data: GameData, effect_id: str) -> bool:
    """Whether any live power on ``char`` has an active effect with base id ``effect_id``.

    Used by the Requires-Effect gate (Limited to While Insubstantial). The activity
    check reuses :func:`effect_is_active` with ``_skip_requires`` set so a required
    effect that itself carries a Requires-Effect gate can't recurse indefinitely.
    """

    for power in live_powers(char.powers):
        for effect in power.effects:
            if effect.effect_id != effect_id:
                continue
            base = next((e for e in game_data.effects if e.id == effect_id), None)
            if base is None:
                continue
            if effect_is_active(power, effect, base, game_data, char, _skip_requires=True):
                return True
    return False


def power_display_name(power: Power, game_data: GameData) -> str:
    """A power's shown name: its player-given title, or one derived from its effects.

    When a power is left unnamed, fall back to the names of its base effects joined
    with ``" / "`` (e.g. ``"Damage / Flight"``), rather than a bare "Unnamed Power".
    Duplicate effect names collapse. A power with no resolvable effects still yields
    ``"Unnamed Power"`` as a last resort.
    """

    if power.name:
        return power.name
    names: list[str] = []
    for effect in power.effects:
        base = next((e for e in game_data.effects if e.id == effect.effect_id), None)
        label = base.name if base else effect.effect_id
        if label and label not in names:
            names.append(label)
    return " / ".join(names) if names else "Unnamed Power"


def effect_display_name(effect: PowerEffectInstance, game_data: GameData) -> str:
    """What one effect *inside* a power is called: its own label, else the base effect's.

    The idiom the cards, the dice footer and the split dialog had each spelled for
    themselves — a vehicle's "Cannon" is a Damage, and a list showing "Damage" twice
    says nothing. Falls back to the raw id so an effect from a ruleset that is no longer
    loaded is still nameable rather than blank.
    """

    if effect.label:
        return effect.label
    base = next((e for e in game_data.effects if e.id == effect.effect_id), None)
    return base.name if base is not None else effect.effect_id


def power_runtime_gates(power: Power, game_data: GameData) -> set[str]:
    """The union of runtime gate kinds across a power's effects (empty if none).

    Includes the implicit toggle gate of any ``passive_toggle`` effect. The UI uses
    this to decide whether a power needs an on/off control.
    """

    gates: set[str] = set()
    for effect in power.effects:
        base = next((e for e in game_data.effects if e.id == effect.effect_id), None)
        if base is None:
            continue
        behaviour = PATTERN_BEHAVIOURS.get(base.integration.pattern) if base.integration else None
        if behaviour is not None and behaviour.toggled:
            gates.add(GATE_TOGGLE)
        gates |= _effect_gates(effect, game_data)
    return gates


def power_has_standing_effect(power: Power, game_data: GameData) -> bool:
    """Whether the power contributes a *standing* bonus that can sit on the sheet.

    True when any effect is passive (``passive_permanent`` or ``passive_toggle``) —
    the patterns :func:`effect_is_active` can report as on. Instant-action and
    resource-pool effects never stand on the sheet, so an all-instant power (a plain
    attack) is ``False``. The UI uses this to decide whether an "Active" control is
    meaningful: an instant effect is *used*, not toggled on/off.
    """

    for effect in power.effects:
        base = next((e for e in game_data.effects if e.id == effect.effect_id), None)
        if base is None or not base.integration:
            continue
        behaviour = PATTERN_BEHAVIOURS.get(base.integration.pattern)
        if behaviour is not None and behaviour.standing:
            return True
    return False


def power_has_custom_modifier(power: Power, game_data: GameData) -> bool:
    """Whether any of the power's effects carries a blank homebrew (Custom) modifier.

    A Custom Extra / Custom Flaw is player-defined (name and cost typed by hand) and
    marked ``custom`` on its :class:`~mm_companion.core.data_loader.Modifier` record, so
    it makes the power homerule the same way a Dev-mode override does. The UI badges such
    a power with the ``⌂`` marker (alongside :func:`mm_companion.core.powers.power_is_homerule`).
    """

    catalog = game_data.modifier_catalog()
    for effect in power.effects:
        for selection in (*effect.extras, *effect.flaws):
            modifier = catalog.get(selection.modifier_id)
            if modifier is not None and modifier.custom:
                return True
    return False


def build_contributions(
    power: Power,
    char: Character,
    game_data: GameData,
    *,
    stacking: str = STACK_SUM,
    group: str = GROUP_POWERS,
    origin: str = "",
) -> tuple[TraitContribution, ...]:
    """Every stat contribution **one** assembled build currently makes.

    An effect contributes when it carries a
    :class:`~mm_companion.core.components.TraitBoost` — an Enhanced-Trait-style boost
    or a fixed-target one like Protection — *and* is currently active
    (:func:`effect_is_active`, so a switched-off or suppressed one drops out). Which
    traits it raises and at what rank each comes from :func:`boost_allocations`, so
    one Enhanced Trait yields one contribution *per allocated trait*.

    **So does a modifier attached to it.** An extra like Elongation's Striding ("longer
    strides grant ranks of Speed") is a stat effect that happens to hang off another
    effect, and it carries a ``statIntegration`` of its own
    (:attr:`~mm_companion.core.data_loader.Modifier.integration`). Note the walk is
    therefore *not* gated on the base effect having a boost — Elongation has none, so
    gating on it is exactly how Striding would go on granting nothing. A modifier is
    worth its own rank when it is ``ranked`` and the **host effect's** otherwise, which
    is what a per-rank price already says it is charging for.

    What the record then *means* is not decided here: the boost's ``apply`` kind picks
    an applier out of :data:`~.appliers.STAT_APPLIERS`, which yields the contributions
    (a numeric trait bonus for ``bonus``, a movement grant for ``speed``, and so on).

    ``stacking`` and ``group`` are the *granter's* terms and travel onto every
    contribution: a power stacks within the Power-Point group, a worn item is
    exclusive within the equipment one. This is the seam equipment shares with
    powers — an item's :attr:`~mm_companion.core.equipment.EquipmentItem.build` is a
    real :class:`~mm_companion.core.powers.Power`, so it is gathered by this same
    function rather than a parallel one that could drift.

    ``origin`` travels the same way and identifies the *granter itself* rather than
    what it is called, so a card can recognise its own superseded bonus among several
    identically named ones (see :class:`~.appliers.TraitContribution`). Powers pass
    none: a power's card explains itself from its own build.
    """

    catalog = game_data.modifier_catalog()
    contributions: list[TraitContribution] = []
    for effect in power.effects:
        base = next((e for e in game_data.effects if e.id == effect.effect_id), None)
        if base is None or base.integration is None:
            continue
        if not effect_is_active(power, effect, base, game_data, char):
            continue  # switched off, suppressed, or not a standing bonus
        source = power.name or base.name

        boost = base.integration.trait_boost
        if boost is not None:
            for target, ranks in boost_allocations(
                effect, base, boost, live=True, game_data=game_data, char=char
            ):
                contributions.extend(
                    apply_stat_effect(
                        boost.apply,
                        ApplyContext(
                            record=boost,
                            rank=ranks,
                            target=target,
                            source=source,
                            game_data=game_data,
                            stacking=stacking,
                            group=group,
                            origin=origin,
                        ),
                    )
                )

        for selection in (*effect.extras, *effect.flaws):
            modifier = catalog.get(selection.modifier_id)
            if modifier is None or modifier.integration is None:
                continue
            granted = modifier.integration.trait_boost
            if granted is None:
                continue
            contributions.extend(
                apply_stat_effect(
                    granted.apply,
                    ApplyContext(
                        record=granted,
                        rank=(
                            selection.rank
                            if modifier.ranked
                            else effect_current_rank(effect, game_data, char)
                        ),
                        target=_boost_target(effect, granted),
                        # Named for the pair, since neither half explains it alone: the
                        # power is what the sheet lists, the modifier is what granted it.
                        source=f"{source} ({modifier.name})",
                        game_data=game_data,
                        stacking=stacking,
                        group=group,
                        origin=origin,
                    ),
                )
            )
    return tuple(contributions)


def power_contributions(char: Character, game_data: GameData) -> tuple[TraitContribution, ...]:
    """Every stat contribution the character's live powers currently make.

    :func:`build_contributions` over every :func:`live_powers` build. Everything a
    power grants is bought with Power Points, so it is gathered into the
    :data:`~.appliers.GROUP_POWERS` group as a stacking contribution — several powers
    boosting one trait add up, exactly as they always have.
    """

    contributions: list[TraitContribution] = []
    for power in live_powers(char.powers):
        contributions.extend(
            build_contributions(power, char, game_data, stacking=STACK_SUM, group=GROUP_POWERS)
        )
    return tuple(contributions)


def worn_items(char: Character) -> list[EquipmentItem]:
    """The character's gear that is currently being worn or carried.

    Wearing is the equipment answer to a power's on/off switch, and it is *runtime*
    state: an item the player has taken off keeps its Equipment Point price (it is
    still owned — see :func:`~.equipment.equipment_points_spent`) but grants nothing.
    """

    return [item for item in char.equipment if item.worn]


def equipment_contributions(char: Character, game_data: GameData) -> tuple[TraitContribution, ...]:
    """Every stat contribution the character's **worn** gear currently makes.

    The equipment twin of :func:`power_contributions`, and deliberately the same code
    underneath (:func:`build_contributions`) — an item is a real build, so anything
    true of a power's Protection is true of a suit of armour's.

    Two things differ, and both are the no-stacking rule
    (``docs/mm-equipment-design.md`` §3). The contributions land in the
    :data:`~.appliers.GROUP_EQUIPMENT` group, which is compared against the powers
    group with ``max`` rather than added to it; and within that group each item is
    :data:`~.appliers.STACK_MAX` — only the best piece of armour counts — unless its
    owner ticked :attr:`~mm_companion.core.equipment.EquipmentItem.stacks`, the
    per-item homerule that opts one item back into adding on top.

    Gathered off the item's **effective** build, so an accessory carrying a trait boost
    of its own grants it: a fitted accessory is off
    :attr:`~mm_companion.core.character.Character.equipment`, and the loop above would
    walk straight past it. The contribution's ``origin`` stays the *host's* id, which is
    the honest answer — the host's card is what has to explain the bonus, and a fitted
    accessory has no card of its own to put it on.
    """

    contributions: list[TraitContribution] = []
    for item in worn_items(char):
        contributions.extend(
            build_contributions(
                item_effective_build(item, game_data),
                char,
                game_data,
                stacking=STACK_SUM if item.stacks else STACK_MAX,
                group=GROUP_EQUIPMENT,
                origin=item.id,
            )
        )
    return tuple(contributions)


def effect_stands(
    power: Power, effect: PowerEffectInstance, game_data: GameData, char: Character | None
) -> bool:
    """Whether this effect is currently *standing on the sheet* — contributing at all.

    Two questions, and it has to be both. An array member that is not the live
    alternate answers :func:`effect_is_active` perfectly happily (``array_active`` is a
    flag nothing maintains — the array group's own ``active_child_id`` is the truth, and
    only :func:`live_powers` reads it), so asking the effect alone reported a power that
    contributes nothing as switched on.

    What the card's rank dial is positioned from, and what
    :func:`~.size.size_steps` lights a rung by: an effect that is standing nowhere is
    *nowhere*, not at rank 1.
    """

    if char is None:
        return False
    base = next((e for e in game_data.effects if e.id == effect.effect_id), None)
    if base is None:
        return False
    if not any(p is power for p in live_powers(char.powers)):
        return False
    return effect_is_active(power, effect, base, game_data, char)


def power_trait_bonuses(char: Character, game_data: GameData) -> dict[str, dict[str, TraitBonus]]:
    """Trait bonuses every saved power grants, grouped ``category -> {key: TraitBonus}``.

    The numeric view of :func:`power_contributions`, netted by
    :func:`~.appliers.resolve_contributions`: the three trait lists the sheet totals
    (abilities, resistances, skills), each always present so a caller can index
    straight in. Contributions in the other categories — a movement or sense grant, a
    penalty removal — are deliberately dropped here; they have their own readers.

    This is the *powers-only* view, which is what the Abilities and Resistances blocks
    show in their enhancement column. The sheet-wide number every derived total reads
    is :func:`~.derived.trait_bonuses`, which folds in the advantages too.
    """

    resolved = resolve_bonuses(power_contributions(char, game_data))
    return {category: dict(resolved.get(category, {})) for category in NUMERIC_CATEGORIES}


def active_array_child(group: PowerGroup) -> PowerNode | None:
    """An ``array`` group's currently-selected live child (``active_child_id``, else first).

    Returns ``None`` for an empty group. Meaningful only for arrays; other modes keep
    every child live (see :func:`live_powers`).
    """

    if not group.children:
        return None
    for child in group.children:
        if child.id == group.active_child_id:
            return child
    return group.children[0]


def power_effects_are_array(power: Power) -> bool:
    """Whether this power's **own effects** are an array — two or more, pooled.

    The same test :func:`~.powers_cost.power_gross_cost` prices by, so the level that
    decides only one effect runs is the level that charged for only one.
    """

    return power.structure == STRUCTURE_ARRAY and len(power.effects) > 1


def active_array_effect_index(
    power: Power, game_data: GameData, char: Character | None = None
) -> int:
    """Which of an array power's own effects is in use — a clamped index into ``effects``.

    ``power.active_effect`` when the player has chosen one, else the **base**: the
    costliest effect, the one the array pays for in full, which is also the one the
    constructor and the card badge "base". (The group level defaults to its *first*
    child instead — a group's children are cards the player ordered themselves, while a
    power's effects are ordered by when they were dropped on the canvas, so "the one you
    paid for" is the better guess here than "the one you built first".)

    Clamped, so a build edited down to fewer effects keeps a selection it can honour —
    the same bargain :func:`effect_current_rank` strikes with a dialled rank. ``0`` for
    anything that is not an array of two or more.
    """

    if not power_effects_are_array(power):
        return 0
    if power.active_effect is None:
        if _array_base_index is None:  # cost not loaded; the first effect is the guess
            return 0
        return max(0, min(_array_base_index(power, game_data, char), len(power.effects) - 1))
    return max(0, min(power.active_effect, len(power.effects) - 1))


def effect_is_selected(
    power: Power,
    effect: PowerEffectInstance,
    game_data: GameData,
    char: Character | None = None,
) -> bool:
    """Whether *effect* is the one its power's array currently has in use.

    ``True`` for every effect of a power that is not an array, which is almost all of
    them. An array's alternates are mutually exclusive — that is precisely what makes an
    array cheaper than the same effects bought independently — so an array power whose
    effects all applied at once was handing out an independent build's bonuses for an
    array's price.

    Identity, not equality: two effects of one power can be the same base effect at the
    same rank (a Damage array of two descriptors), and they are still different members.
    """

    if not power_effects_are_array(power):
        return True
    index = active_array_effect_index(power, game_data, char)
    return power.effects[index] is effect


def live_array_children(group: PowerGroup) -> list[PowerNode]:
    """Which of an ``array`` group's children are running right now.

    Ordinarily exactly one — :func:`active_array_child`, the selected alternate, since
    an array's members are mutually exclusive. **Once points have been split across the
    array's Dynamic members that stops being true**: they "operate at the same time, at
    reduced effectiveness" (p101), so every member holding a positive share is live
    together and the selection stops deciding anything.

    A member holding *no* share is not live, which is how a player switches the pool
    off again: with every share cleared the list falls back to the selected member at
    full rank, which is what an array saved before the pool existed does on load.

    Only the share's presence is read here, never its size — deciding whether a share
    is large enough to buy a rank needs point costs, and that is
    :func:`effect_current_rank`'s job one layer up. A member holding a share too small
    to reach rank 1 is therefore *live* but running at rank 0, which contributes
    nothing; the sheet reaches the same place either way.
    """

    shared = [c for c in group.children if c.dynamic and (c.dynamic_points or 0) > 0]
    if shared:
        return shared
    child = active_array_child(group)
    return [child] if child is not None else []


def live_powers(nodes: list[PowerNode]) -> list[Power]:
    """Every leaf power currently contributing to the sheet, honouring array selection.

    Descends the powers tree: an ``array`` group contributes only its live children
    (:func:`live_array_children` — the selected alternate, or every Dynamic member
    sharing the pool), while ``independent`` and ``linked`` groups contribute all of
    them. Leaf powers pass straight through. Whether a *live* power's bonus actually
    applies is then a per-power/effect runtime question left to
    :func:`effect_is_active`.
    """

    result: list[Power] = []
    for node in nodes:
        if isinstance(node, PowerGroup):
            if node.mode == STRUCTURE_ARRAY and node.children:
                for child in live_array_children(node):
                    result.extend(live_powers([child]))
            else:
                result.extend(live_powers(node.children))
        else:
            result.append(node)
    return result

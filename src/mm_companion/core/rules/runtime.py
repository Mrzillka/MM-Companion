"""Runtime power state and standing trait bonuses (lowest rules layer).

Which passive effect is currently on (gating), array/live-power selection, and the
per-trait bonuses a character's active powers add. Everything that reads an
"effective" trait builds on this module, so it takes no dependency on the rest of
the rules package.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from ..character import Character
from ..components import (
    GATE_REMOVABLE,
    GATE_TOGGLE,
    INSTANT_ACTION,
    PASSIVE_PERMANENT,
    PASSIVE_TOGGLE,
    RESOURCE_POOL,
)
from ..data_loader import GameData
from ..powers import STRUCTURE_ARRAY, Power, PowerEffectInstance, PowerGroup, PowerNode
from ..registry import Registry

# The trait categories a ``TraitBoost`` can name that map to a numeric trait bonus on
# the sheet (``defense`` resistances live in the resistances list).
TRAIT_CATEGORIES = frozenset({"ability", "resistance", "defense", "skill"})


@dataclass(frozen=True)
class TraitBonus:
    """A standing bonus a character's powers add to one trait.

    ``amount`` is the summed bonus; ``sources`` names each contributing power (its
    name, or the effect's name when the power is unnamed) so the UI can explain the
    boost on a tooltip.
    """

    amount: int
    sources: tuple[str, ...]


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


def _resolved_trait_target(effect: PowerEffectInstance, base) -> str:
    """The trait key one effect boosts, or ``""`` when it isn't a trait booster.

    Reads the effect's :class:`~mm_companion.core.components.TraitBoost` component:
    the target is the player's choice (``config['target']``) for a ``configurable``
    boost or the baked-in ``target`` (e.g. Protection) otherwise, and only when the
    boost's ``affects`` names a numeric trait category.
    """

    boost = base.integration.trait_boost if base.integration else None
    if boost is None or not (boost.affects & TRAIT_CATEGORIES):
        return ""
    target = effect.config.get("target", "") if boost.configurable else boost.target
    return target or ""


def _trait_category(game_data: GameData, target: str) -> str:
    """Which trait list ``target`` belongs to — ``ability``/``resistance``/``skill``, or ``""``."""
    if any(a.key == target for a in game_data.abilities):
        return "ability"
    if any(r.key == target for r in game_data.resistances):
        return "resistance"
    if any(s.name == target for s in game_data.skills):
        return "skill"
    return ""


def _trait_name(game_data: GameData, target: str) -> str:
    """The display name for a trait key (its ``name``; skills are named by key)."""
    for a in game_data.abilities:
        if a.key == target:
            return a.name
    for r in game_data.resistances:
        if r.key == target:
            return r.name
    return target  # skills (and anything else) display by their key/name


def _effect_gates(effect: PowerEffectInstance, game_data: GameData) -> set[str]:
    """The runtime gate kinds an effect carries, from its attached modifiers (§7)."""

    catalog = game_data.modifier_catalog()
    gates: set[str] = set()
    for sel in list(effect.extras) + list(effect.flaws):
        mod = catalog.get(sel.modifier_id)
        if mod and mod.gate:
            gates.add(mod.gate)
    return gates


def effect_is_active(power: Power, effect: PowerEffectInstance, base, game_data: GameData) -> bool:
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
    gates = _effect_gates(effect, game_data)
    if behaviour is not None and behaviour.toggled:  # passive_toggle implies a toggle gate
        gates = gates | {GATE_TOGGLE}
    for gate in gates:
        blocker = GATE_KINDS.get(gate)
        if blocker is not None and blocker(power, effect):
            return False
    return True


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


def power_trait_bonuses(char: Character, game_data: GameData) -> dict[str, dict[str, TraitBonus]]:
    """Trait bonuses every saved power grants, grouped ``category -> {key: TraitBonus}``.

    A power enhances a trait when one of its effects is a trait booster — an
    Enhanced-Trait-style effect (a configurable :class:`TraitBoost`, the target read
    from the instance ``config['target']``) or a fixed-target one like Protection
    (the target baked into the boost) — its ``affects`` names a trait category, *and*
    the effect is currently active (:func:`effect_is_active`, so a switched-off or
    suppressed power drops out). The bonus is the effect's rank, added to the resolved
    target; the category is inferred from which trait list the target key belongs to
    (abilities, resistances, or skills). Effects that boost non-numeric traits (senses,
    movement) or carry no resolvable target are skipped. Multiple powers stack.
    """

    result: dict[str, dict[str, TraitBonus]] = {"ability": {}, "resistance": {}, "skill": {}}

    for power in live_powers(char.powers):
        for effect in power.effects:
            base = next((e for e in game_data.effects if e.id == effect.effect_id), None)
            if base is None:
                continue
            target = _resolved_trait_target(effect, base)
            if not target:
                continue  # not a booster, or no trait chosen / no fixed target
            category = _trait_category(game_data, target)
            if not category:
                continue  # target isn't a trait we track
            if not effect_is_active(power, effect, base, game_data):
                continue  # switched off, suppressed, or not a standing bonus
            source = power.name or base.name
            bucket = result[category]
            prior = bucket.get(target)
            if prior is None:
                bucket[target] = TraitBonus(effect.rank, (source,))
            else:
                bucket[target] = TraitBonus(prior.amount + effect.rank, prior.sources + (source,))
    return result


def _trait_bonus(
    char: Character, game_data: GameData, category: str, key: str
) -> TraitBonus | None:
    """The :class:`TraitBonus` powers add to one trait, or ``None`` when there is none."""
    return power_trait_bonuses(char, game_data)[category].get(key)


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


def live_powers(nodes: list[PowerNode]) -> list[Power]:
    """Every leaf power currently contributing to the sheet, honouring array selection.

    Descends the powers tree: an ``array`` group contributes only its
    :func:`active_array_child` (so an unselected alternate's bonuses drop off), while
    ``independent`` and ``linked`` groups contribute all their children. Leaf powers
    pass straight through. Whether a *live* power's bonus actually applies is then a
    per-power/effect runtime question left to :func:`effect_is_active`.
    """

    result: list[Power] = []
    for node in nodes:
        if isinstance(node, PowerGroup):
            if node.mode == STRUCTURE_ARRAY and node.children:
                child = active_array_child(node)
                if child is not None:
                    result.extend(live_powers([child]))
            else:
                result.extend(live_powers(node.children))
        else:
            result.append(node)
    return result

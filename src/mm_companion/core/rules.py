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
import re
from dataclasses import dataclass
from fractions import Fraction

from .character import AppliedCondition, Character
from .components import (
    GATE_REMOVABLE,
    GATE_TOGGLE,
    INSTANT_ACTION,
    MECH_CHECK_PENALTY,
    MECH_DEBILITATE_TRAIT,
    MECH_MOVEMENT_MOD,
    PASSIVE_PERMANENT,
    PASSIVE_TOGGLE,
    RESOURCE_POOL,
)
from .data_loader import Condition, GameData, Modifier, RandomActionRow, Resistance, Skill
from .dice import roll_d20
from .powers import (
    ALTERNATE_EFFECT_MODIFIER,
    STRUCTURE_ARRAY,
    STRUCTURE_LINKED,
    Power,
    PowerEffectInstance,
    PowerGroup,
    PowerNode,
)


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
    if pattern in (INSTANT_ACTION, RESOURCE_POOL):
        return False
    if effect.suppressed:
        return False
    if not power.activated:  # master on/off (also the Activation gate)
        return False
    if not power.array_active:  # an array member not currently selected as active
        return False
    gates = _effect_gates(effect, game_data)
    if GATE_REMOVABLE in gates and not power.item_present:
        return False
    if (pattern == PASSIVE_TOGGLE or GATE_TOGGLE in gates) and not effect.toggled_on:
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
        if base.integration and base.integration.pattern == PASSIVE_TOGGLE:
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
        if (
            base
            and base.integration
            and base.integration.pattern
            in (
                PASSIVE_PERMANENT,
                PASSIVE_TOGGLE,
            )
        ):
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


def effective_ability(char: Character, game_data: GameData, key: str) -> int:
    """An ability's value for all derived math: its bought rank plus any power bonus.

    This is the value skills, resistances, initiative, and the like read, so an
    Enhanced-Trait boost to an ability flows through the whole sheet. The point cost
    (:func:`power_points_spent`) still counts only the *bought* rank — the boost is
    paid for by the power itself.
    """

    bonus = _trait_bonus(char, game_data, "ability", key)
    return char.abilities.get(key, 0) + (bonus.amount if bonus else 0)


def skill_total(char: Character, game_data: GameData, row_id: str) -> int:
    """``ability value + skill ranks + situational modifier`` for one skill row.

    The ability value is the *effective* one (:func:`effective_ability`), and a skill
    a power enhances also adds that power bonus, so an Enhanced-Trait boost to either
    the linked ability or the skill itself shows up in the total.
    """

    skill = _skill_for_row(game_data, row_id)
    ability_key = skill.ability if skill else ""
    ability_value = effective_ability(char, game_data, ability_key)
    total = ability_value + char.skill_ranks.get(row_id, 0) + char.skill_mods.get(row_id, 0)
    if skill:
        bonus = _trait_bonus(char, game_data, "skill", skill.name)
        if bonus:
            total += bonus.amount
    return total


def resistance_base(char: Character, game_data: GameData, key: str) -> int:
    """The trait a resistance derives from, before its bought ranks and power boosts.

    Fortitude and Toughness derive from Stamina, Will from Awareness — an *ability*,
    read at its effective value (:func:`effective_ability`). Dodge instead derives
    from the Defense combat trait, which is itself a (derived) resistance, so the base
    can be another resistance's total. A resistance with no linked trait (Defense
    itself) has base 0. This is the value a "no ranks bought" resistance equals.
    """

    res = _resistance(game_data, key)
    base_key = res.ability if res else ""
    if not base_key:
        return 0
    if any(a.key == base_key for a in game_data.abilities):
        return effective_ability(char, game_data, base_key)
    if any(r.key == base_key for r in game_data.resistances):
        return resistance_total(char, game_data, base_key)
    return 0


def resistance_total(char: Character, game_data: GameData, key: str) -> int:
    """A resistance's total: its derived base plus the bought and power ranks.

    The base is the linked trait's value (:func:`resistance_base`) — Stamina for
    Toughness/Fortitude, Awareness for Will, the Defense trait for Dodge; derived
    resistances with no linked trait (Defence itself) have base 0. On top of that sit
    the ranks bought above the base and any power boost (Protection → Toughness). A
    power that raises the linked trait therefore raises the total too.
    """

    base = resistance_base(char, game_data, key)
    bought = char.resistances.get(key, 0)
    bonus = _trait_bonus(char, game_data, "resistance", key)
    return base + bought + (bonus.amount if bonus else 0)


def defense_class(char: Character, game_data: GameData, key: str = "DEF") -> int:
    """The DC an attacker must beat: ``10 + defense rank`` (``mm-core-mechanics.md`` §5)."""

    return 10 + resistance_total(char, game_data, key)


def initiative(char: Character, bonus: int = 0, game_data: GameData | None = None) -> int:
    """Initiative modifier: Agility rank plus any misc bonus (``mm-core-mechanics.md`` §8).

    Uses the *effective* Agility (bought plus any Enhanced-Trait boost) when
    ``game_data`` is supplied; without it, the bare bought rank.
    """

    agility = (
        effective_ability(char, game_data, "AGL")
        if game_data is not None
        else char.abilities.get("AGL", 0)
    )
    return agility + bonus


def ability_points_spent(char: Character, game_data: GameData) -> int:
    """Power points spent on abilities and combat stats (``mm-core-mechanics.md`` §7).

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
    """Power points spent on resistances (``mm-core-mechanics.md`` §7).

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
    """Power points spent on skills, pooled across every skill (``mm-skills-design.md`` §4/§7).

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


# The category tag whose members share one Power-Level-derived rank budget.
HEROIC_TYPE = "Heroic"


def advantage_by_name(game_data: GameData, name: str):
    """The :class:`~.data_loader.Advantage` content record for a chosen name, or ``None``."""

    return next((a for a in game_data.advantages if a.name == name), None)


def advantage_rank_cap(advantage, power_level: int) -> int | None:
    """The standalone rank cap for one advantage at a Power Level, or ``None`` if uncapped.

    Reads the advantage's ``max_rank_kind`` (``mm-advantages-design.md`` §3), so the
    cap style is data-driven rather than hardcoded:

    - ``"fixed"`` — the number the rules give (``max_rank``): Improved Critical 4, etc.
    - ``"power_level_half"`` — Improved Initiative's own ``ceil(power_level / 2)``.

    The other kinds carry no standalone number: ``"power_level"`` advantages share a
    Power-Level trait cap with the character's base ranks (checked elsewhere),
    ``"heroic_budget"`` advantages draw from the shared pool
    (:func:`heroic_advantage_budget`), and ``"none"`` is uncapped. All three return
    ``None`` here. An unranked advantage is always a single rank.
    """

    if not advantage.ranked:
        return 1
    kind = advantage.max_rank_kind
    if kind == "fixed":
        return advantage.max_rank
    if kind == "power_level_half":
        return math.ceil(power_level / 2)
    return None


def heroic_advantage_budget(power_level: int) -> int:
    """Total ranks available across all Heroic-type advantages: ``floor(power_level / 2)``.

    One shared pool for every Heroic advantage on the sheet (``mm-advantages-design.md``
    §3.4), not a per-advantage cap.
    """

    return power_level // 2


def heroic_advantage_ranks(char: Character, game_data: GameData) -> int:
    """Ranks the character currently draws from the shared Heroic-advantage budget.

    A ranked Heroic advantage spends its rank; an unranked one spends a flat 1. Non-
    Heroic advantages don't touch the pool.
    """

    total = 0
    for selection in char.advantages:
        advantage = advantage_by_name(game_data, selection.name)
        if advantage and HEROIC_TYPE in advantage.types:
            total += selection.rank if advantage.ranked else 1
    return total


def advantage_violations(char: Character, game_data: GameData) -> list[str]:
    """Advantage limit breaches in the current build; an empty list means it is valid.

    Two limits (``mm-advantages-design.md`` §3): each ranked advantage against its own
    cap (:func:`advantage_rank_cap` — the fixed numbers and Improved Initiative's
    ``ceil(PL/2)``), and the shared Heroic-advantage budget
    (:func:`heroic_advantage_budget`). The Power-Level *shared* trait caps for
    ``"power_level"`` advantages are folded into the trait totals checked by
    :func:`power_level_violations`, so they aren't repeated here.
    """

    pl = char.power_level
    violations: list[str] = []
    for selection in char.advantages:
        advantage = advantage_by_name(game_data, selection.name)
        if advantage is None:
            continue
        cap = advantage_rank_cap(advantage, pl)
        if cap is not None and selection.rank > cap:
            violations.append(f"{advantage.name} rank {selection.rank} exceeds its cap of {cap}.")

    budget = heroic_advantage_budget(pl)
    used = heroic_advantage_ranks(char, game_data)
    if used > budget:
        violations.append(
            f"Heroic advantages use {used} ranks, exceeding the PL {pl} budget of {budget}."
        )
    return violations


def powers_points_spent(char: Character, game_data: GameData) -> int:
    """Power points spent on powers: the cost of every top-level node in the tree.

    A character's powers form a tree of :data:`~mm_companion.core.powers.PowerNode`
    (leaf powers and :class:`~mm_companion.core.powers.PowerGroup` containers); each
    top-level node is priced by :func:`node_cost`, which folds in array pooling (a
    group's alternates contribute only a flat point) recursively through any nesting.
    """

    return sum(node_cost(node, game_data, char) for node in char.powers)


def power_points_spent(char: Character, game_data: GameData) -> int:
    """Total power points the character's current build costs (``mm-core-mechanics.md`` §7).

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
    built on at least 150 points at 15 points per level (``mm-core-mechanics.md`` §7).
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
    ranked += effect_rank_trait_bonus(effect, game_data, char) * net_mods

    flat = _signed_modifier_cost(effect.extras, +1, game_data, flat=True)
    flat += _signed_modifier_cost(effect.flaws, -1, game_data, flat=True)

    return ranked + flat


def effect_rank_trait_bonus(
    effect: PowerEffectInstance, game_data: GameData, char: Character | None
) -> int:
    """Ranks a modifier folds in from a character ability (Strength-Based → Strength).

    Sums the *effective* value of each ability an attached modifier's
    :attr:`~mm_companion.core.data_loader.Modifier.adds_ability` names — so a
    Strength-Based Damage picks up the wielder's Strength (Enhanced Trait boosts to
    that ability included). Zero without a character or when no such modifier is
    attached. The bought point cost is unaffected — the folded-in rank is free.

    A selection may cap how much of the ability it uses via ``config["amount"]`` (the
    Strength-Based chip's spin box): when set, no more than that many ranks are folded
    in (and never more than the wielder actually has). Absent, the full ability is
    used and tracks it dynamically.
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
            bonus += ability if amount is None else min(int(amount), ability)
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
    # modifiers, but not the base cost — a separate ``strength × (mods)`` term.
    strength = effect_rank_trait_bonus(effect, game_data, char)
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
    ``modifiers.json`` (``costValue``), not hardcoded here. Falls back to 1 if the
    record is missing.
    """

    modifier = game_data.modifier_catalog().get(ALTERNATE_EFFECT_MODIFIER)
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


def effect_attack_skill_bonus(
    effect: PowerEffectInstance, char: Character | None, game_data: GameData
) -> int | None:
    """The attack-roll bonus an effect's linked Close/Ranged Combat focus supplies.

    ``None`` when the effect has no ``attack_skill`` link (or there is no character),
    so callers fall back to the wielder's Attack ability. Otherwise the linked focus
    row's :func:`skill_total` — which already folds in the Attack ability, since these
    combat skills derive from ``ATK`` — so it *replaces* the bare Attack rather than
    stacking with it. A dangling row id degrades to that ability value (its ranks read
    as 0).
    """

    if not effect.attack_skill or char is None:
        return None
    return skill_total(char, game_data, effect.attack_skill)


def effect_makes_attack(effect: PowerEffectInstance, game_data: GameData) -> bool:
    """Whether the effect resolves with an **attack roll** (vs. auto-hit / no check).

    True when the base effect's check phrase is an "Attack …" roll and no attached
    modifier drops it (a Perception-Range extra removes the roll, making the effect
    auto-hit). This is the same condition :func:`power_pl_violations` uses to pick the
    attack-plus-rank cap, and what gates the constructor's attack-skill picker.
    """

    base = next((e for e in game_data.effects if e.id == effect.effect_id), None)
    if base is None:
        return False
    impact = _effective_stats(effect, game_data)[3]
    return "Attack" in (base.check or "") and not impact.drops_check


def power_pl_violations(power: Power, char: Character, game_data: GameData) -> list[str]:
    """Power Level cap breaches within a single power, for its wielding character.

    Checks each offensive effect (one with a
    :attr:`~mm_companion.core.data_loader.Effect.resistance_dc_base`) against the
    Power Level caps in ``mm-core-mechanics.md`` §7, reading the character so the
    real inputs apply:

    - An effect that makes an **attack roll** obeys ``max_attack + effect_rank <=
      power_level * 2``. The attack bonus is the character's *effective* Attack
      ability — or, when the effect links a Close/Ranged Combat focus
      (:func:`effect_attack_skill_bonus`), that focus's total instead — plus the
      power's own Accurate/Inaccurate; the effect rank is the
      *effective* rank (:func:`effect_effective_rank`), so a Strength-Based Damage
      folds in the wielder's Strength.
    - A resisted effect with **no attack roll** (auto-hit — Perception range, or a
      Perception-Range modifier) instead obeys ``effect_rank <= power_level``.

    Returns one message per offending effect. Both caps derive from Power Level (the
    ``attack_effect`` cap for the ×2 ceiling), never hardcoded.
    """

    cap = game_data.costs.power_level.caps.get("attack_effect")
    if cap is None:
        return []
    power_level = char.power_level
    limit = cap.limit(power_level)

    violations: list[str] = []
    for effect in power.effects:
        base = next((e for e in game_data.effects if e.id == effect.effect_id), None)
        if base is None or base.resistance_dc_base is None:
            continue  # not an attack/resisted effect — these caps don't apply
        # An effect linked to a Close/Ranged Combat focus uses that focus's total as
        # its attack bonus (replacing the bare Attack ability); otherwise the Attack.
        linked = effect_attack_skill_bonus(effect, char, game_data)
        attack_ability = linked if linked is not None else effective_ability(char, game_data, "ATK")
        impact = _effective_stats(effect, game_data)[3]
        rank = effect_effective_rank(effect, game_data, char)
        if effect_makes_attack(effect, game_data):
            attack = attack_ability + impact.check_bonus
            if attack + rank > limit:
                violations.append(
                    f"{base.name}: attack +{attack} plus rank {rank} = {attack + rank} "
                    f"exceeds the PL {power_level} cap of {limit}."
                )
        elif rank > power_level:  # auto-hit effect: rank alone is capped at PL
            violations.append(
                f"{base.name} rank {rank} exceeds the PL {power_level} rank cap of {power_level}."
            )
    return violations


def effect_allocation_used(effect: PowerEffectInstance, game_data: GameData) -> int:
    """Ranks the effect's Tier-4 config fields have spent from its rank pool.

    A Tier-4 effect (Enhanced Senses/Movement, Comprehend, Immunity, Feature) spends
    its rank as a currency: an ``allocation`` field sums the chosen tier cost of each
    selected option, a ``repeatable`` field with a numeric column sums those ranks,
    and a plain ``repeatable`` (Feature) counts one per row. Other field types spend
    nothing. Returns the total spent across all such fields.
    """

    base = next((e for e in game_data.effects if e.id == effect.effect_id), None)
    if base is None:
        return 0
    used = 0
    for cfg in base.config_fields:
        value = effect.config.get(cfg.key)
        if not value:
            continue
        if cfg.type == "allocation":
            by_id = {o.id: o for o in cfg.alloc_options}
            for entry in value:
                option = by_id.get(entry.get("id"))
                if option is None or not option.tiers:
                    continue
                tier = min(max(int(entry.get("tier", 1)), 1), len(option.tiers))
                used += option.tiers[tier - 1]
        elif cfg.type == "repeatable":
            int_key = next((c.key for c in cfg.columns if c.type == "int"), None)
            if int_key is not None:
                used += sum(int(row.get(int_key, 0) or 0) for row in value)
            else:
                used += len(value)
    return used


def power_allocation_violations(power: Power, game_data: GameData) -> list[str]:
    """Over-allocation breaches: a Tier-4 effect spending more ranks than it has.

    Enhanced Senses/Movement, Comprehend, Immunity, and Feature allocate the effect's
    rank across a menu (see :func:`effect_allocation_used`); spending more than the
    effect's rank is invalid. Returns one message per over-allocated effect.
    """

    violations: list[str] = []
    for effect in power.effects:
        base = next((e for e in game_data.effects if e.id == effect.effect_id), None)
        if base is None:
            continue
        if not any(f.type in ("allocation", "repeatable") for f in base.config_fields):
            continue
        used = effect_allocation_used(effect, game_data)
        if used > effect.rank:
            violations.append(
                f"{base.name}: allocated {used} of {effect.rank} ranks "
                f"— {used - effect.rank} over budget."
            )
    return violations


def power_linked_range_violations(power: Power, game_data: GameData) -> list[str]:
    """Linked-effect Range mismatches (``mm-powers-architecture.md`` §4).

    Linked effects fire together as one, so they must share the same Range. Reads
    each effect's *effective* Range (base range with any modifier overrides applied,
    via :func:`_effective_stats`) and flags any that differs from the first effect's.
    Returns one message per mismatched effect. Empty unless the power is Linked with
    two or more effects.
    """

    if power.structure != STRUCTURE_LINKED or len(power.effects) < 2:
        return []
    ranges = [_effective_stats(effect, game_data)[1].get("range", "") for effect in power.effects]
    first = ranges[0]
    violations: list[str] = []
    for effect, range_ in zip(power.effects[1:], ranges[1:], strict=True):
        if range_ != first:
            name = _effect_name(effect, game_data)
            violations.append(
                f"{name}: Range '{range_}' differs from the first linked effect's "
                f"'{first}' — linked effects must share the same Range."
            )
    return violations


def _effect_name(effect: PowerEffectInstance, game_data: GameData) -> str:
    """The display name of an effect's base, falling back to its raw id."""

    base = next((e for e in game_data.effects if e.id == effect.effect_id), None)
    return base.name if base else effect.effect_id


# The base game-term fields, in display order, with their table labels.
_STAT_FIELDS = (
    ("effect_type", "Type"),
    ("range", "Range"),
    ("action", "Action"),
    ("duration", "Duration"),
    ("check", "Check"),
    ("resistance", "Resistance"),
)


@dataclass(frozen=True)
class EffectStat:
    """One row of an effect's game-term table (see :func:`effect_stat_rows`).

    ``base`` is the unmodified value and ``value`` the current one; ``change`` is
    ``"better"`` when an extra improved the field (the UI tints it green),
    ``"worse"`` when a flaw limited it (red), or ``""`` when it is unchanged or set
    by a neutral player choice.
    """

    key: str
    label: str
    base: str
    value: str
    change: str = ""


@dataclass(frozen=True)
class EffectImpact:
    """Modifier-derived game-term adjustments that aren't a plain field override.

    Gathered alongside the stat dicts by :func:`_effective_stats`. ``check_bonus``
    is the net signed number modifiers add to the effect's attack roll (Accurate
    ``+2``/rank, Inaccurate ``-2``/rank); ``drops_check`` is set when a modifier
    removes the attack roll entirely (Perception Range); ``check_notes`` are
    parentheticals a modifier appends to the check row (Area's Dodge-for-half); and
    ``notes`` names every attached modifier with no other visible game-term impact,
    so the table can list them and nothing an effect carries goes unseen.
    """

    check_bonus: int = 0
    drops_check: bool = False
    check_notes: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()


def _step_along(ladder: tuple[str, ...], value: str, step: int) -> str:
    """Move ``value`` ``step`` positions along ``ladder`` (clamped to its ends).

    Returns ``value`` unchanged when it isn't on the ladder or ``step`` is zero, so
    a stepping modifier on a value the ladder doesn't cover is simply a no-op.
    """

    if not ladder or not step or value not in ladder:
        return value
    index = ladder.index(value) + step
    return ladder[max(0, min(len(ladder) - 1, index))]


def modifier_detail(modifier: Modifier, selection) -> str:
    """The free-text detail a player typed into a modifier's text config field.

    A modifier like Limited or Quirk carries a single ``"text"`` config field for
    the player to describe *how* it applies. Returns the first non-empty such value
    (e.g. ``"only at night"``), or ``""`` if none was entered. Used to qualify a
    modifier's displayed name as ``"Limited (only at night)"`` wherever it is
    listed, so a bare ``"Limited"`` never hides the circumstance the player chose.
    """

    for cfg in modifier.config_fields:
        if cfg.type == "text":
            value = str(selection.config.get(cfg.key, "")).strip()
            if value:
                return value
    return ""


def modifier_label(modifier: Modifier, selection, *, rank_sep: str = " ") -> str:
    """A modifier's display name, qualified with its rank and free-text detail.

    A ranked modifier above rank 1 gains its rank (``"Penetrating 3"``); a modifier
    with a typed text detail gains it in parentheses (``"Limited (only at night)"``).
    ``rank_sep`` separates the name from the rank (the card uses ``" ×"``).
    """

    label = modifier.name
    if modifier.ranked and selection.rank > 1:
        label = f"{modifier.name}{rank_sep}{selection.rank}"
    detail = modifier_detail(modifier, selection)
    if detail:
        label = f"{label} ({detail})"
    return label


def _modifier_notes(
    effect: PowerEffectInstance, catalog: dict, impactful: set[str]
) -> tuple[str, ...]:
    """Names of the effect's attached modifiers that produced no visible stat change.

    Skips the ids in ``impactful`` (those already reflected in a stat cell) so the
    Notes row lists only what would otherwise be invisible; a ranked modifier taken
    above rank 1 carries its rank (e.g. ``"Penetrating 3"``), and one with a typed
    detail carries it (``"Limited (only at night)"``).
    """

    notes: list[str] = []
    for selection in (*effect.extras, *effect.flaws):
        modifier = catalog.get(selection.modifier_id)
        if modifier is None or selection.modifier_id in impactful:
            continue
        notes.append(modifier_label(modifier, selection))
    return tuple(notes)


def _effective_stats(
    effect: PowerEffectInstance, game_data: GameData
) -> tuple[dict[str, str], dict[str, str], dict[str, str], EffectImpact]:
    """``(base, effective, change, impact)`` for an effect's game-term fields.

    ``base`` is the unmodified stat, ``effective`` has each modifier and config
    override applied (extras-then-flaws, so a later one wins, then config choices),
    and ``change`` records how the final value differs from the base: ``"better"``
    (an extra changed it), ``"worse"`` (a flaw), or ``""`` (unchanged or a neutral
    config choice). ``impact`` collects the modifier effects that aren't a field
    replacement (attack-roll bonus, dropped/noted check, plus the Notes list). Empty
    dicts and a blank :class:`EffectImpact` for an unknown effect id.
    """

    base_effect = next((e for e in game_data.effects if e.id == effect.effect_id), None)
    if base_effect is None:
        return {}, {}, {}, EffectImpact()
    base = {
        "effect_type": base_effect.effect_type,
        "range": base_effect.range_,
        "action": base_effect.action,
        "duration": base_effect.duration,
        "check": base_effect.check or "",
        "resistance": base_effect.resistance or "",
    }
    stats = dict(base)
    change = dict.fromkeys(base, "")
    ladders = game_data.game_term_ladders
    catalog = game_data.modifier_catalog()

    check_bonus = 0
    drops_check = False
    check_notes: list[str] = []
    impactful: set[str] = set()  # ids reflected in a stat cell — kept out of Notes
    # An action step (Increased/Reduced Action) is deferred and applied below, after
    # the free-action floor a Sustained duration imposes, so it steps from that floor
    # rather than from a Permanent effect's bare "None".
    action_step = 0
    action_step_tint = ""

    for selection in (*effect.extras, *effect.flaws):
        modifier = catalog.get(selection.modifier_id)
        if modifier is None:
            continue
        tint = "better" if modifier.category == "extra" else "worse"
        touched = False
        for key, value in modifier.overrides.items():
            if key in stats:
                stats[key] = value
                change[key] = tint
                touched = True
        if modifier.step_field == "action":
            action_step += modifier.step_by
            if modifier.step_by:
                action_step_tint = tint
            touched = True
        elif modifier.step_field in stats:
            stepped = _step_along(
                ladders.get(modifier.step_field, ()), stats[modifier.step_field], modifier.step_by
            )
            if stepped != stats[modifier.step_field]:
                stats[modifier.step_field] = stepped
                change[modifier.step_field] = tint
            touched = True
        if modifier.check_bonus:
            check_bonus += modifier.check_bonus * (
                selection.rank if modifier.ranked else effect.rank
            )
            touched = True
        if modifier.drops_check:
            drops_check = True
            touched = True
        if modifier.check_note:
            check_notes.append(modifier.check_note)
            touched = True
        if touched:
            impactful.add(selection.modifier_id)

    # Config choices that name a stat replace it (e.g. Affliction's chosen
    # resistance). These are neutral player choices, not modifiers, so they carry
    # no better/worse tint.
    for field in base_effect.config_fields:
        if field.overrides and field.overrides in stats:
            value = effect.config.get(field.key)
            if value:
                stats[field.overrides] = _config_display(field, value)
                change[field.overrides] = ""

    # A Sustained effect must be toggled on and maintained with at least a free
    # action, so its action is floored by the one its (possibly modified) duration
    # implies — a Permanent effect made toggleable by the Sustained extra comes with
    # action "None". The floor is the baseline an Increased/Reduced Action step then
    # moves from, and a hard minimum afterwards (a step can't push below it). The
    # floor itself is a rule consequence, not a modifier win, so it carries no tint.
    action_ladder = ladders.get("action", ())
    floor = game_data.duration_action_floor.get(stats["duration"])

    def _floor_action() -> None:
        if floor in action_ladder and stats["action"] in action_ladder:
            if action_ladder.index(stats["action"]) < action_ladder.index(floor):
                stats["action"] = floor

    _floor_action()  # baseline the action step moves from
    if action_step:
        stepped = _step_along(action_ladder, stats["action"], action_step)
        if stepped != stats["action"]:
            stats["action"] = stepped
            change["action"] = action_step_tint
        _floor_action()  # hard minimum: a step can't drop below the free-action floor

    # A modifier that lands the value back on its base isn't really a change.
    for key in change:
        if stats[key] == base[key]:
            change[key] = ""

    impact = EffectImpact(
        check_bonus=check_bonus,
        drops_check=drops_check,
        check_notes=tuple(check_notes),
        notes=_modifier_notes(effect, catalog, impactful),
    )
    return base, stats, change, impact


def effective_effect_stats(effect: PowerEffectInstance, game_data: GameData) -> dict[str, str]:
    """The base effect's game-term stats with its modifiers' overrides applied.

    Starts from the effect's own ``effect_type``/``range``/``action``/``duration``/
    ``check``/``resistance`` and lets each attached modifier's
    :attr:`~mm_companion.core.data_loader.Modifier.overrides` replace fields — e.g.
    Ranged forces ``range`` to ``"Ranged"``. Modifiers apply extras-then-flaws, so a
    later one wins. Returns ``{}`` for an unknown effect id.
    """

    return _effective_stats(effect, game_data)[1]


# The actor's own roll in a check/resistance phrase ("Attack vs. …", "Effect vs. …")
# — the leading word before "vs." — is the effect's own d20 bonus, its rank.
_ACTOR_ROLL = re.compile(r"^(?:Attack|Deflect|Effect) vs\.")


def _numeric_roll(text: str, actor_bonus: int, dc: int | None, *, resistance: bool) -> str:
    """Fill an effect's attack bonus / save DC into a check or resistance phrase.

    The actor's own roll (the ``Attack``/``Deflect``/``Effect`` before ``vs.``)
    becomes ``actor_bonus`` — the effect rank, plus any Accurate/Inaccurate
    adjustment — so ``"Attack vs. Defense"`` reads ``"8 vs. Defense"``. A resisted
    threshold (``"Effect"`` / ``"Effect DC"`` after ``vs.``) becomes the save
    ``dc``, so ``"Toughness vs. Effect"`` reads ``"Toughness vs. 18"``. A bare
    resistance name a config override left behind (e.g. Affliction's ``"Will"``)
    gets the DC appended. ``dc`` is ``None`` for effects that impose no save DC (the
    phrase's threshold is then left as prose).
    """

    if not text:
        return text
    if dc is not None:
        text = text.replace("Effect DC", f"DC {dc}")
        text = re.sub(r"vs\. Effect\b", f"vs. {dc}", text)
    text = _ACTOR_ROLL.sub(f"{actor_bonus} vs.", text)
    if resistance and dc is not None and " vs. " not in text:
        text = f"{text} vs. DC {dc}"
    return text


def _measure_value(measure, rank: int, game_data: GameData) -> str:
    """The imperial measurement label for a rank, with a ``/round`` suffix for a speed.

    Metric is deferred to a settings toggle — this reads the ``imperial`` column for
    now. Returns ``""`` when the rank is outside the tabulated range.
    """

    label = game_data.measurements.label(measure.column, rank)
    if not label:
        return ""
    return f"{label}/round" if measure.per_round else label


def effect_stat_rows(
    effect: PowerEffectInstance,
    game_data: GameData,
    char: Character | None = None,
    attack_bonus: int | None = None,
) -> list[EffectStat]:
    """The effect's non-empty game-term fields as tintable table rows.

    Each :class:`EffectStat` carries its base and current value plus a ``change``
    tag, so the UI can render a small table and highlight the fields an extra
    improved (green) or a flaw limited (red). Numeric measures derived from the rank
    are filled in from ``measurements.json``: a ``"Rank"`` range becomes the actual
    distance, and an effect with a ``measure`` (movement speeds, leap distance) gets
    its own row. Modifier impacts beyond a field override are folded in too — an
    Accurate/Inaccurate bonus shifts (and tints) the attack roll, Perception Range
    drops the check row, Area annotates it — and every attached modifier with no
    other visible impact is gathered into a trailing ``Notes`` row. The configured
    qualities that don't override a stat (e.g. Affliction's condition degrees) are
    appended as untinted rows so the table stays a complete summary. Empty for an
    unknown effect id.

    When ``char`` is given, the numbers reflect the wielder: an attack roll shows the
    character's Attack (plus Accurate/Inaccurate) rather than the effect rank, and the
    resistance save DC uses the effective effect rank (a Strength-Based Damage folds in
    the wielder's Strength). Without a character both fall back to the effect rank, so a
    context-free summary still reads.

    ``attack_bonus`` overrides the attacker's base d20 bonus for an "Attack vs. …"
    phrase — an effect linked to a Close/Ranged Combat focus passes that focus's total
    (:func:`effect_attack_skill_bonus`) so the shown roll matches the PL check. ``None``
    keeps the default (the character's Attack ability, or the effect rank without one).
    """

    base_effect = next((e for e in game_data.effects if e.id == effect.effect_id), None)
    if base_effect is None:
        return []
    base, stats, change, impact = _effective_stats(effect, game_data)

    # A "Rank" range means "a distance equal to the effect's rank" — show the number
    # (in both base and current, so it isn't mistaken for a modifier change).
    for scope in (base, stats):
        if scope.get("range") == "Rank":
            scope["range"] = game_data.measurements.label("distance", effect.rank) or "Rank"

    # Resolve the check/resistance phrases to concrete numbers: the save DC is
    # ``base + effective rank`` (effective rank folds in a Strength-Based bonus), and
    # the attack roll uses the character's Attack (see below).
    effective_rank = effect_effective_rank(effect, game_data, char)
    dc = (
        None
        if base_effect.resistance_dc_base is None
        else base_effect.resistance_dc_base + effective_rank
    )
    # The attacker's own d20 bonus in the check phrase. An "Attack vs. Defense" roll
    # uses the character's Attack (plus this power's Accurate/Inaccurate); an "Effect
    # vs. …" / "Deflect vs. …" phrase instead uses the effect's own rank. A linked
    # combat focus overrides the Attack via ``attack_bonus``. Without either we fall
    # back to the effect rank so a context-free summary still reads.
    if attack_bonus is not None:
        attack = attack_bonus
    elif char is not None:
        attack = effective_ability(char, game_data, "ATK")
    else:
        attack = effect.rank

    def _actor(phrase: str, *, with_mods: bool) -> int:
        roll = attack if phrase.startswith("Attack") else effect.rank
        return roll + (impact.check_bonus if with_mods else 0)

    base["check"] = _numeric_roll(
        base["check"], _actor(base["check"], with_mods=False), dc, resistance=False
    )
    base["resistance"] = _numeric_roll(base["resistance"], effect.rank, dc, resistance=True)
    stats["check"] = _numeric_roll(
        stats["check"], _actor(stats["check"], with_mods=True), dc, resistance=False
    )
    stats["resistance"] = _numeric_roll(stats["resistance"], effect.rank, dc, resistance=True)

    # Accurate/Inaccurate move the attack number — tint the check by the net sign.
    if stats["check"] and impact.check_bonus:
        change["check"] = "better" if impact.check_bonus > 0 else "worse"
    # Area-style notes ride along on the current check value only (not the base).
    if stats["check"] and impact.check_notes:
        stats["check"] = f"{stats['check']} ({'; '.join(impact.check_notes)})"

    rows = []
    for key, label in _STAT_FIELDS:
        if key == "check" and impact.drops_check:  # e.g. Perception Range — no attack roll
            continue
        if stats[key]:
            rows.append(EffectStat(key, label, base[key], stats[key], change[key]))
    # An effect can impose a save DC without either a (shown) check or resistance
    # phrase to carry it — surface it in its own row so the number is never lost.
    check_shown = "" if impact.drops_check else stats["check"]
    if dc is not None and not check_shown and not stats["resistance"]:
        rows.append(EffectStat("effect_dc", "Effect DC", "", f"DC {dc}", ""))
    if base_effect.measure:
        value = _measure_value(base_effect.measure, effect.rank, game_data)
        if value:
            rows.append(EffectStat("measure", base_effect.measure.label, "", value, ""))
    for field in base_effect.config_fields:
        if field.overrides or field.type == "checkbox":
            continue  # a checkbox is a toggle or surfaced via a readout, not a value row
        value = effect.config.get(field.key)
        if value:
            rows.append(EffectStat(field.key, field.label, "", _config_display(field, value), ""))
    # A trait booster (Enhanced Trait, Protection) shows which trait it raises and by
    # how much — green, since it's an improvement — so the summary isn't blank.
    target = _resolved_trait_target(effect, base_effect)
    if target and _trait_category(game_data, target):
        raised = f"{_trait_name(game_data, target)} +{effect.rank}"
        rows.append(EffectStat("enhances", "Enhances", "", raised, "better"))
    # Tier-5 derived readouts (Growth's size mods, Insubstantial's state, ...) — purely
    # computed information, appended as untinted (or sign-tinted) rows.
    rows.extend(effect_readout_rows(effect, game_data))
    if impact.notes:
        rows.append(EffectStat("notes", "Notes", "", ", ".join(impact.notes), ""))
    return rows


def effect_readout_rows(effect: PowerEffectInstance, game_data: GameData) -> list[EffectStat]:
    """The effect's Tier-5 derived readout rows (``mm-powers-ui-design.md`` §2 Tier 5).

    Reads the effect's entries in ``effect_readouts.json`` and renders each by its
    ``kind`` — a Growth/Shrinking size shift into its Size Table modifiers, an
    Insubstantial rank into its state name, a Communication rank into its range band,
    a Burrowing rank into per-terrain speeds, and so on. These are never editable, so
    the UI shows them as read-only rows. Empty when the effect has no readouts.
    """

    rows: list[EffectStat] = []
    for readout in game_data.effect_readouts.get(effect.effect_id, ()):
        rows.extend(_readout_rows(readout, effect, game_data))
    return rows


def _readout_rows(readout, effect: PowerEffectInstance, game_data: GameData) -> list[EffectStat]:
    """Render one :class:`~mm_companion.core.data_loader.Readout` to table rows."""

    rank = effect.rank
    data = readout.data
    if readout.kind == "size_table":
        sign = int(data.get("sign", 1))
        size = game_data.measurements.size_row(sign * rank)
        if size is None or rank <= 0:
            return []
        out = [EffectStat("size", readout.label or "Size", "", size.size_category, "")]
        for label, mod in (
            ("Defense", size.defense_mod),
            ("Damage", size.damage_mod),
            ("Toughness", size.toughness_mod),
            ("Speed", size.speed_mod),
            ("Intimidation", size.intimidation_mod),
            ("Stealth", size.stealth_mod),
        ):
            if mod:
                change = "better" if mod > 0 else "worse"
                out.append(EffectStat(f"size_{label.lower()}", label, "", f"{mod:+d}", change))
        return out
    if readout.kind == "state":
        by_rank = {int(k): v for k, v in data.get("byRank", {}).items()}
        if not by_rank:
            return []
        eligible = [k for k in sorted(by_rank) if k <= rank]
        chosen = by_rank[eligible[-1]] if eligible else by_rank[min(by_rank)]
        return [EffectStat(readout.label.lower() or "state", readout.label, "", chosen, "")]
    if readout.kind == "measure_offsets":
        column = data.get("column", "distance")
        out = []
        for row in data.get("rows", []):
            value = game_data.measurements.label(column, rank + int(row.get("offset", 0)))
            if not value:
                continue
            if row.get("perRound"):
                value = f"{value}/round"
            out.append(EffectStat("readout", row.get("label", ""), "", value, ""))
        return out
    if readout.kind == "thresholds":
        return [
            EffectStat("readout", row.get("label", ""), "", row.get("text", ""), "")
            for row in data.get("rows", [])
            if rank >= int(row.get("minRank", 0))
        ]
    if readout.kind == "config_flag":
        on = bool(effect.config.get(data.get("key", "")))
        text = data.get("trueText", "") if on else data.get("falseText", "")
        return [EffectStat(readout.label.lower() or "readout", readout.label, "", text, "")]
    if readout.kind == "points_per_rank":
        per = int(data.get("perRank", 1))
        return [EffectStat("pool", readout.label, "", f"{rank * per} points", "")]
    return []


def _config_display(field, value) -> str:
    """Display text for a stored config ``value``: an option's label, or, for a
    multiselect list, its labels joined with ``+`` (falls back to the raw value).

    ``allocation`` values (a list of ``{"id", "tier"}``) render as their option
    labels (tiered ones carry the chosen tier number); ``repeatable`` values (a list
    of row dicts) render as their named rows, an Immunity scope carrying its rank."""

    if field.type == "allocation":
        by_id = {o.id: o for o in field.alloc_options}
        parts = []
        for entry in value:
            option = by_id.get(entry.get("id"))
            if option is None:
                continue
            label = option.label
            if len(option.tiers) > 1:
                label += f" {entry.get('tier', 1)}"
            parts.append(label)
        return ", ".join(parts)
    if field.type == "repeatable":
        name_key = field.columns[0].key if field.columns else "name"
        int_key = next((c.key for c in field.columns if c.type == "int"), None)
        parts = []
        for row in value:
            name = str(row.get(name_key, "")).strip()
            if not name:
                continue
            if int_key and row.get(int_key):
                name += f" ({row[int_key]})"
            parts.append(name)
        return ", ".join(parts)

    values = value if isinstance(value, list) else [value]
    labels = (next((o.label for o in field.options if o.value == v), v) for v in values)
    return " + ".join(labels)


def effect_game_terms(effect: PowerEffectInstance, game_data: GameData) -> str:
    """One-line game-term summary of an effect, e.g.
    ``Affliction 4: Attack, Ranged range, Standard action, Instant duration``.

    Reads the effective stats (base plus modifier and config overrides) and renders
    the non-empty ones; a resistance is appended in parentheses, then any remaining
    configured qualities (Affliction's condition degrees, etc.). Returns ``""`` for
    an unknown effect id.
    """

    base = next((e for e in game_data.effects if e.id == effect.effect_id), None)
    if base is None:
        return ""
    stats = effective_effect_stats(effect, game_data)

    segments = [stats["effect_type"]]
    if stats["range"]:
        segments.append(f"{stats['range']} range")
    if stats["action"] and stats["action"] != "None":
        segments.append(f"{stats['action']} action")
    if stats["duration"]:
        segments.append(f"{stats['duration']} duration")

    line = f"{base.name} {effect.rank}: " + ", ".join(s for s in segments if s)
    if stats["resistance"]:
        line += f" (resisted by {stats['resistance']})"

    # Configured qualities that don't override a stat are appended (e.g. conditions).
    chosen = []
    for field in base.config_fields:
        if field.overrides or field.type == "checkbox":
            continue
        value = effect.config.get(field.key)
        if value:
            chosen.append(f"{field.label}: {_config_display(field, value)}")
    if chosen:
        line += "; " + ", ".join(chosen)
    return line


def power_game_terms(power: Power, game_data: GameData, char: Character | None = None) -> str:
    """The power's game-term summary: one :func:`effect_game_terms` line per effect.

    A ``linked`` or ``array`` power (with two or more effects) prefixes a header and
    tags each line with its role — the array marks its base and notes the flat cost
    of each alternate — so the composite structure reads at a glance. ``char`` is
    threaded to :func:`array_base_index` so the base badge tracks the same
    Strength-adjusted costs the cards show.
    """

    lines = [effect_game_terms(e, game_data) for e in power.effects]
    if len(power.effects) > 1 and power.structure == STRUCTURE_LINKED:
        body = "\n".join(f"• {line}" for line in lines)
        return "Linked (all effects activate together):\n" + body
    if len(power.effects) > 1 and power.structure == STRUCTURE_ARRAY:
        base = array_base_index(power, game_data, char)
        alt = array_alternate_cost(game_data)
        tagged = [
            f"• {line}" + (" [base]" if i == base else f" (Alternate Effect, {alt} pt)")
            for i, line in enumerate(lines)
        ]
        return "Array (one effect active at a time):\n" + "\n".join(tagged)
    return "\n".join(lines)


def power_level_violations(char: Character, game_data: GameData) -> list[str]:
    """Report Power Level cap breaches (``mm-core-mechanics.md`` §7); empty list = valid.

    Evaluates the character-wide caps: per-skill modifier, Dodge + Toughness, and
    Fortitude + Will. The attack + effect-rank cap is per-power and checked in
    :func:`power_pl_violations` instead.
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


# --------------------------------------------------------------------------- #
# Conditions — the non-roll state resolver (mm-conditions-design.md §3-7).
#
# A character's condition state is a *flattened set with provenance*
# (``character.conditions``): applying an umbrella stores the umbrella plus one
# member row per bundled condition (each tagged with the umbrella's id). These
# functions read the condition graph (``includes`` / ``supersedes`` / ``stacking`` /
# ``debilitates``) generically from the catalog — no per-condition branches. Anything
# that rolls dice (recovery checks, auto-fail-on-debilitated-trait, random actions)
# is the roll layer's job and is deliberately not here.
# --------------------------------------------------------------------------- #


def _param_type(condition: Condition | None) -> str:
    """The parameter input type of a condition (``""`` when it takes no parameter)."""

    return condition.parameter.type if condition and condition.parameter else ""


def expand_includes(condition: Condition, catalog: dict[str, Condition]) -> list[str]:
    """Every condition id an umbrella bundles, expanded recursively (deduped, ordered).

    A nested umbrella (Dying → Incapacitated → Defenseless/…) is flattened fully so
    the whole set can be tagged with one provenance and removed together.
    """

    seen: list[str] = []
    queue = list(condition.includes)
    while queue:
        cid = queue.pop(0)
        if cid in seen:
            continue
        seen.append(cid)
        member = catalog.get(cid)
        if member:
            queue.extend(member.includes)
    return seen


def _remove_superseded(
    character: Character,
    catalog: dict[str, Condition],
    condition: Condition,
    parameter: str | None,
) -> None:
    """Drop conditions *condition* supersedes (per-part, trait-scoped).

    Supersession is unconditional except between two ``trait_select`` conditions,
    where a *scoped* superseding condition only replaces same-trait instances
    (Attack Disabled supersedes Attack Impaired, not Perception Impaired); an
    unscoped superseding condition replaces all of them. Superseding a directly
    applied umbrella also drops the members it granted.
    """

    if not condition.supersedes:
        return
    scoped = _param_type(condition) == "trait_select"
    drop: set[int] = set()
    dropped_umbrellas: set[str] = set()
    for applied in character.conditions:
        if applied.condition_id not in condition.supersedes:
            continue
        if (
            scoped
            and parameter is not None
            and _param_type(catalog.get(applied.condition_id)) == "trait_select"
            and applied.parameter != parameter
        ):
            continue  # different trait scope — the two coexist
        drop.add(id(applied))
        if applied.provenance is None:
            dropped_umbrellas.add(applied.condition_id)
    if dropped_umbrellas:
        for applied in character.conditions:
            if applied.provenance in dropped_umbrellas:
                drop.add(id(applied))
    if drop:
        character.conditions[:] = [c for c in character.conditions if id(c) not in drop]


def _add_or_stack(
    character: Character,
    catalog: dict[str, Condition],
    condition: Condition,
    parameter: str | None,
    provenance: str | None,
) -> None:
    """Add one flattened condition, or bump its ``count`` if it stacks (§5).

    Supersession runs first so a bundled part (a Stunned inside Incapacitated) still
    replaces what it should. A non-stacking condition already present with the same
    id + parameter is left untouched (idempotent).
    """

    _remove_superseded(character, catalog, condition, parameter)
    for applied in character.conditions:
        if applied.condition_id == condition.id and applied.parameter == parameter:
            if condition.stacking:
                applied.count += 1
            return
    character.conditions.append(
        AppliedCondition(condition.id, parameter=parameter, count=1, provenance=provenance)
    )


def apply_condition(
    character: Character,
    condition_id: str,
    game_data: GameData,
    *,
    parameter: str | None = None,
    provenance: str | None = None,
) -> None:
    """Apply a condition to a character, expanding bundles and cascades (§3, §7).

    Adds the condition, then every condition it ``includes`` (as members tagged with
    this condition's id), applying supersession across the flattened set. If the
    condition ``debilitates`` a chosen trait, its cascade conditions (Strength →
    Incapacitated) are applied as further members. Unknown ids are ignored.
    """

    catalog = game_data.condition_catalog()
    condition = catalog.get(condition_id)
    if condition is None:
        return
    _add_or_stack(character, catalog, condition, parameter, provenance)
    member_provenance = provenance or condition_id
    for member_id in expand_includes(condition, catalog):
        member = catalog.get(member_id)
        if member is not None:
            _add_or_stack(character, catalog, member, None, member_provenance)
    if condition.debilitates is not None and parameter is not None:
        for cascade_id in condition.debilitates.cascade.get(parameter, ()):
            apply_condition(character, cascade_id, game_data, provenance=condition_id)


def remove_condition(character: Character, applied: AppliedCondition) -> None:
    """Remove one applied-condition instance.

    Removing a directly applied umbrella (``provenance is None``) also removes every
    member it granted; removing a member removes only that member (Dazed off a
    Staggered leaves Hindered). Superseded conditions do **not** return.
    """

    drop = {id(applied)}
    if applied.provenance is None:
        for other in character.conditions:
            if other.provenance == applied.condition_id:
                drop.add(id(other))
    character.conditions[:] = [c for c in character.conditions if id(c) not in drop]


def hit_stack_penalty(character: Character, game_data: GameData) -> int:
    """The accumulated resistance-check penalty from stacking conditions (Hit, §5)."""

    catalog = game_data.condition_catalog()
    total = 0
    for applied in character.conditions:
        cond = catalog.get(applied.condition_id)
        if cond and cond.stacking_rule is not None:
            total += cond.stacking_rule.per_instance_penalty * applied.count
    return total


def condition_check_penalty(
    character: Character, game_data: GameData, scope: str | None = None
) -> int:
    """Total flat check penalty in force (Impaired/Disabled/Frightened, §4).

    An unscoped penalty (``All checks`` / no parameter) always applies; a scoped one
    applies only to a check of the matching category — pass ``scope`` (e.g. ``"Attack"``)
    to include those, or leave it ``None`` for the generic (unscoped-only) total.
    """

    catalog = game_data.condition_catalog()
    total = 0
    for applied in character.conditions:
        cond = catalog.get(applied.condition_id)
        if cond is None or cond.penalty is None or MECH_CHECK_PENALTY not in cond.mechanisms:
            continue
        unscoped = applied.parameter in (None, "All checks")
        if unscoped or (scope is not None and applied.parameter == scope):
            total += cond.penalty
    return total


def condition_speed_rank_mod(character: Character, game_data: GameData) -> int | None:
    """Net movement speed-rank change (§4).

    ``None`` means a condition zeroes ground speed (Immobile / Prone); otherwise the
    summed rank penalty (Hindered's −1).
    """

    catalog = game_data.condition_catalog()
    total = 0
    zeroed = False
    for applied in character.conditions:
        cond = catalog.get(applied.condition_id)
        if cond is None or cond.speed_rank_mod is None or MECH_MOVEMENT_MOD not in cond.mechanisms:
            continue
        if cond.speed_rank_mod == 0:
            zeroed = True
        else:
            total += cond.speed_rank_mod
    return None if zeroed else total


def condition_defense_mods(character: Character, game_data: GameData) -> dict[str, str]:
    """The strongest Defense/Dodge alteration in force (§4).

    Maps ``"defense"`` / ``"dodge"`` to ``"zero"`` (worst) or ``"halve"``, omitting a
    stat with no modifier. Reflects the typed ``defense_mod`` data (Vulnerable halves
    both); Defenseless's routine-attack/auto-fail behaviour is roll-layer.
    """

    catalog = game_data.condition_catalog()
    severity = {"": 0, "halve": 1, "zero": 2}
    best: dict[str, str] = {}
    for applied in character.conditions:
        cond = catalog.get(applied.condition_id)
        if cond is None or cond.defense_mod is None:
            continue
        for stat in ("defense", "dodge"):
            op = getattr(cond.defense_mod, stat)
            if op and severity.get(op, 0) > severity.get(best.get(stat, ""), 0):
                best[stat] = op
    return best


def condition_attack_mods(character: Character, game_data: GameData) -> dict[str, int]:
    """Summed attack modifiers from posture conditions (Prone, §4)."""

    catalog = game_data.condition_catalog()
    mods = {"own_close": 0, "incoming_close": 0, "incoming_ranged": 0}
    for applied in character.conditions:
        cond = catalog.get(applied.condition_id)
        if cond is None or cond.attack_mods is None:
            continue
        mods["own_close"] += cond.attack_mods.own_close
        mods["incoming_close"] += cond.attack_mods.incoming_close
        mods["incoming_ranged"] += cond.attack_mods.incoming_ranged
    return mods


def condition_resistance_penalty(
    character: Character, game_data: GameData, descriptor: str, effect_rank: int
) -> int:
    """Resistance-check penalty vs *descriptor* from Susceptible/Weakness (§4).

    Each matching condition contributes ``−floor(effect_rank / 2)``. The scoped
    descriptor is matched case-insensitively against the stored parameter.
    """

    catalog = game_data.condition_catalog()
    want = descriptor.strip().lower()
    total = 0
    for applied in character.conditions:
        cond = catalog.get(applied.condition_id)
        if cond is None or cond.resistance_mod is None or not applied.parameter:
            continue
        if applied.parameter.strip().lower() == want:
            total += -(effect_rank // 2)
    return total


@dataclass(frozen=True)
class ConditionEffect:
    """A condition's overlay on one displayed stat row (display-only, pass 2).

    ``delta`` is a flat modifier (negative), ``op`` an override (``"halve"`` / ``"zero"``)
    taking precedence over ``delta``; ``condition_ids`` are the contributing conditions
    (the UI decides which render struck through); ``tooltip`` is the breakdown text.
    These never touch the point build — they only re-skin the number a section shows.
    """

    delta: int = 0
    op: str = ""
    condition_ids: frozenset[str] = frozenset()
    tooltip: str = ""

    @property
    def active(self) -> bool:
        return bool(self.delta or self.op or self.condition_ids)

    def apply(self, value: int) -> int:
        """The value after this overlay (``zero`` wins, then ``halve``, then ``delta``)."""

        if self.op == "zero":
            return 0
        if self.op == "halve":
            value //= 2
        return value + self.delta


def condition_scope_penalty(
    character: Character, game_data: GameData, scope_keys: set[str]
) -> ConditionEffect:
    """The condition overlay for a stat row answering to *scope_keys*.

    Two mechanisms feed a stat row. A ``check_penalty`` condition (Impaired/Disabled)
    that is unscoped (``None`` / ``"All checks"``) or whose parameter is one of
    *scope_keys* contributes a flat ``delta``. A ``debilitate_trait`` condition
    (Debilitated) whose parameter matches *scope_keys* loses the trait outright — an
    ``op="zero"`` that dominates the delta. Scope keys are an ability row → ``{key,
    name}`` or a skill row → ``{row_id, base_name}``. Returns the merged penalty, the
    contributing condition ids (for strikethrough), and a tooltip breakdown.
    """

    catalog = game_data.condition_catalog()
    total = 0
    op = ""
    ids: set[str] = set()
    parts: list[str] = []
    for applied in character.conditions:
        cond = catalog.get(applied.condition_id)
        if cond is None:
            continue
        unscoped = applied.parameter in (None, "All checks")
        if MECH_CHECK_PENALTY in cond.mechanisms and cond.penalty is not None:
            if not (unscoped or applied.parameter in scope_keys):
                continue
            total += cond.penalty
            ids.add(cond.id)
            label = cond.name if unscoped else f"{cond.name} ({applied.parameter})"
            parts.append(f"{cond.penalty:+d} {label}")
        elif MECH_DEBILITATE_TRAIT in cond.mechanisms and applied.parameter in scope_keys:
            # A debilitated trait is effectively lost (skills read as untrained, an
            # ability auto-fails its checks) — zero the shown number and strike it out.
            op = "zero"
            ids.add(cond.id)
            parts.append(f"lost — {cond.name} ({applied.parameter})")
    return ConditionEffect(
        delta=total, op=op, condition_ids=frozenset(ids), tooltip="; ".join(parts)
    )


def debilitated_traits(character: Character, game_data: GameData) -> frozenset[str]:
    """The set of trait names a Debilitated condition currently names (its parameter).

    Lets the advantage/power views — which have no numeric row to overlay — strike
    through a trait that is effectively lost. Abilities and skills use
    :func:`condition_scope_penalty` instead.
    """

    catalog = game_data.condition_catalog()
    names: set[str] = set()
    for applied in character.conditions:
        cond = catalog.get(applied.condition_id)
        if cond is not None and MECH_DEBILITATE_TRAIT in cond.mechanisms and applied.parameter:
            names.add(applied.parameter)
    return frozenset(names)


def resistance_condition_effect(
    character: Character, game_data: GameData, res_key: str
) -> ConditionEffect:
    """The condition overlay for one resistance row (display-only).

    Toughness carries the Hit stacking penalty (a ``delta`` on Damage-resistance checks);
    the active defenses Dodge and Defence carry Vulnerable/Defenseless halving/zeroing (an
    ``op``). Other resistances get an inert effect.
    """

    catalog = game_data.condition_catalog()
    delta = 0
    op = ""
    ids: set[str] = set()
    parts: list[str] = []

    if res_key == "TOUGHNESS":
        pen = hit_stack_penalty(character, game_data)
        if pen:
            delta += pen
            for applied in character.conditions:
                cond = catalog.get(applied.condition_id)
                if cond is not None and cond.stacking_rule is not None:
                    ids.add(cond.id)
                    parts.append(f"{pen:+d} {cond.name} ×{applied.count}")

    if res_key in ("DODGE", "DEF"):
        stat = "dodge" if res_key == "DODGE" else "defense"
        chosen = condition_defense_mods(character, game_data).get(stat, "")
        if chosen:
            op = chosen
            for applied in character.conditions:
                cond = catalog.get(applied.condition_id)
                if (
                    cond is not None
                    and cond.defense_mod is not None
                    and getattr(cond.defense_mod, stat)
                ):
                    ids.add(cond.id)
                    parts.append(f"{cond.name} {chosen}s {res_key.title()}")

    return ConditionEffect(
        delta=delta, op=op, condition_ids=frozenset(ids), tooltip="; ".join(parts)
    )


def decrement_condition(character: Character, applied: AppliedCondition) -> None:
    """Shed one instance of a condition (Hit peels off one at a time, §5).

    A stacking condition with more than one instance just loses a ``count``; anything
    else (including an umbrella) is removed outright via :func:`remove_condition`.
    """

    if applied.count > 1:
        applied.count -= 1
    else:
        remove_condition(character, applied)


def _parse_die_range(text: str) -> tuple[int, int]:
    text = text.strip()
    if "-" in text:
        low, high = text.split("-", 1)
        return int(low), int(high)
    return int(text), int(text)


def roll_confused_action(
    character: Character,
    game_data: GameData,
    *,
    rng=None,
    roll: int | None = None,
) -> tuple[int, RandomActionRow | None]:
    """Roll the Confused random-action table and return ``(die, row)``.

    ``roll=`` forces the die (for tests); ``rng=`` seeds it otherwise. ``row`` is the
    matching :class:`RandomActionRow` (``None`` only if the table has a gap).
    """

    confused = game_data.condition_catalog().get("confused")
    die = roll if roll is not None else roll_d20(rng)
    if confused is not None:
        for row in confused.random_table:
            low, high = _parse_die_range(row.range)
            if low <= die <= high:
                return die, row
    return die, None

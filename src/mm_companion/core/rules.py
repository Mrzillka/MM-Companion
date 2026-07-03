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

from .character import Character
from .data_loader import GameData, Modifier, Resistance, Skill
from .powers import (
    ALTERNATE_EFFECT_MODIFIER,
    STRUCTURE_ARRAY,
    STRUCTURE_LINKED,
    Power,
    PowerEffectInstance,
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


# The trait categories an effect's ``stat_affects`` can name that map to a numeric
# trait bonus on the sheet (``defense`` resistances live in the resistances list).
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

    The target is the player's choice (``config['target']``) for a configurable
    effect or the effect's baked-in ``stat_target`` otherwise, and only when the
    effect's ``stat_affects`` names a numeric trait category.
    """

    affects = set(base.stat_affects.split("|")) if base.stat_affects else set()
    if not (affects & TRAIT_CATEGORIES):
        return ""
    target = effect.config.get("target", "") if base.configurable_target else base.stat_target
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


def power_trait_bonuses(char: Character, game_data: GameData) -> dict[str, dict[str, TraitBonus]]:
    """Trait bonuses every saved power grants, grouped ``category -> {key: TraitBonus}``.

    A power enhances a trait when one of its effects is a trait booster — an
    Enhanced-Trait-style effect (``configurable_target``, the target read from the
    instance ``config['target']``) or a fixed-target one like Protection (the target
    baked into the effect's ``stat_target``) — and its ``stat_affects`` names a trait
    category. The bonus is the effect's rank, added to the resolved target; the
    category is inferred from which trait list the target key belongs to (abilities,
    resistances, or skills). Effects that boost non-numeric traits (senses, movement)
    or carry no resolvable target are skipped. Multiple powers stack on one trait.
    """

    result: dict[str, dict[str, TraitBonus]] = {"ability": {}, "resistance": {}, "skill": {}}

    for power in char.powers:
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


def resistance_total(char: Character, game_data: GameData, key: str) -> int:
    """A resistance's total: its linked (effective) ability plus bought and power ranks.

    Derived resistances with no linked ability (e.g. Defence) are just their bought
    plus power ranks. A power that enhances the linked ability (or the resistance
    itself, e.g. Protection → Toughness) raises the total.
    """

    res = _resistance(game_data, key)
    bought = char.resistances.get(key, 0)
    ability_value = effective_ability(char, game_data, res.ability) if res and res.ability else 0
    bonus = _trait_bonus(char, game_data, "resistance", key)
    return ability_value + bought + (bonus.amount if bonus else 0)


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


def power_points_spent(char: Character, game_data: GameData) -> int:
    """Total power points the character's current build costs (``mm-core-mechanics.md`` §6).

    Abilities and combat stats cost per rank (combat stats — the ``derived`` ones —
    at the combat rate); non-derived resistances cost per rank; skills cost 1 PP
    per N ranks (a higher N for focused skills); advantages cost per rank; each saved
    power costs its assembled :func:`power_total_cost`. Negative ability/combat ranks
    refund points.
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

    for power in char.powers:
        total += power_total_cost(power, game_data)

    return total


def power_points_remaining(char: Character, game_data: GameData) -> int:
    """Unspent power points (budget minus :func:`power_points_spent`; may go negative)."""

    return char.power_points_total - power_points_spent(char, game_data)


def min_power_points(power_level: int, game_data: GameData) -> int:
    """The minimum power-point budget a Power Level requires: ``PL × pp_per_level``.

    A character's Power Level sets the floor on their point budget — a PL 10 hero is
    built on at least 150 points at 15 points per level (``mm-core-mechanics.md`` §6).
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


def _modifier_magnitude(modifier: Modifier, selection) -> int:
    """One modifier's cost magnitude: ``cost_value``, times its rank when ``ranked``."""

    return modifier.cost_value * (selection.rank if modifier.ranked else 1)


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


def effect_rank_trait_bonus(
    effect: PowerEffectInstance, game_data: GameData, char: Character | None
) -> int:
    """Ranks a modifier folds in from a character ability (Strength-Based → Strength).

    Sums the *effective* value of each ability an attached modifier's
    :attr:`~mm_companion.core.data_loader.Modifier.adds_ability` names — so a
    Strength-Based Damage picks up the wielder's Strength (Enhanced Trait boosts to
    that ability included). Zero without a character or when no such modifier is
    attached. The bought point cost is unaffected — the folded-in rank is free.
    """

    if char is None:
        return 0
    catalog = game_data.modifier_catalog()
    bonus = 0
    for selection in (*effect.extras, *effect.flaws):
        modifier = catalog.get(selection.modifier_id)
        if modifier and modifier.adds_ability:
            bonus += effective_ability(char, game_data, modifier.adds_ability)
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


def array_alternate_cost(game_data: GameData) -> int:
    """The flat point cost of one array alternate, read from the ``Alternate Effect`` extra.

    Kept data-driven: the number lives on the ``alternate_effect`` modifier in
    ``modifiers.json`` (``costValue``), not hardcoded here. Falls back to 1 if the
    record is missing.
    """

    modifier = game_data.modifier_catalog().get(ALTERNATE_EFFECT_MODIFIER)
    return modifier.cost_value if modifier else 1


def array_base_index(power: Power, game_data: GameData) -> int:
    """Index of an array's *base* effect — the costliest one (ties break to the first).

    The base is paid for in full; every other effect is a flat-cost alternate.
    Returns ``-1`` for a power with no effects. Only meaningful for an array, but
    computed purely from the effects so callers can badge cards uniformly.
    """

    if not power.effects:
        return -1
    full = [effect_total_cost(e, game_data) for e in power.effects]
    return full.index(max(full))


def power_total_cost(power: Power, game_data: GameData) -> int:
    """Total power-point cost of a power (``mm-powers-architecture.md`` §4).

    ``independent`` and ``linked`` powers cost the sum of their effects (linking
    is a +0 bundle). An ``array`` instead pays the costliest effect in full and a
    flat :func:`array_alternate_cost` for each remaining effect, since only one is
    active at a time.
    """

    if power.structure == STRUCTURE_ARRAY and len(power.effects) > 1:
        full = [effect_total_cost(e, game_data) for e in power.effects]
        return max(full) + (len(full) - 1) * array_alternate_cost(game_data)
    return sum(effect_total_cost(e, game_data) for e in power.effects)


def power_pl_violations(power: Power, char: Character, game_data: GameData) -> list[str]:
    """Power Level cap breaches within a single power, for its wielding character.

    Checks each offensive effect (one with a
    :attr:`~mm_companion.core.data_loader.Effect.resistance_dc_base`) against the
    Power Level caps in ``mm-core-mechanics.md`` §7, reading the character so the
    real inputs apply:

    - An effect that makes an **attack roll** obeys ``max_attack + effect_rank <=
      power_level * 2``. The attack bonus is the character's *effective* Attack
      ability plus the power's own Accurate/Inaccurate; the effect rank is the
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
    attack_ability = effective_ability(char, game_data, "ATK")

    violations: list[str] = []
    for effect in power.effects:
        base = next((e for e in game_data.effects if e.id == effect.effect_id), None)
        if base is None or base.resistance_dc_base is None:
            continue  # not an attack/resisted effect — these caps don't apply
        impact = _effective_stats(effect, game_data)[3]
        rank = effect_effective_rank(effect, game_data, char)
        makes_attack = "Attack" in (base.check or "") and not impact.drops_check
        if makes_attack:
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


def _modifier_notes(
    effect: PowerEffectInstance, catalog: dict, impactful: set[str]
) -> tuple[str, ...]:
    """Names of the effect's attached modifiers that produced no visible stat change.

    Skips the ids in ``impactful`` (those already reflected in a stat cell) so the
    Notes row lists only what would otherwise be invisible; a ranked modifier taken
    above rank 1 carries its rank (e.g. ``"Penetrating 3"``).
    """

    notes: list[str] = []
    for selection in (*effect.extras, *effect.flaws):
        modifier = catalog.get(selection.modifier_id)
        if modifier is None or selection.modifier_id in impactful:
            continue
        label = modifier.name
        if modifier.ranked and selection.rank > 1:
            label = f"{modifier.name} {selection.rank}"
        notes.append(label)
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
        if modifier.step_field in stats:
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
    ``dc``, so ``"Toughness vs. Effect"`` reads ``"Toughness vs. 23"``. A bare
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
    effect: PowerEffectInstance, game_data: GameData, char: Character | None = None
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
    # vs. …" / "Deflect vs. …" phrase instead uses the effect's own rank. Without a
    # character we fall back to the effect rank so a context-free summary still reads.
    attack = effective_ability(char, game_data, "ATK") if char is not None else effect.rank

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
        if field.overrides:
            continue
        value = effect.config.get(field.key)
        if value:
            rows.append(EffectStat(field.key, field.label, "", _config_display(field, value), ""))
    # A trait booster (Enhanced Trait, Protection) shows which trait it raises and by
    # how much — green, since it's an improvement — so the summary isn't blank.
    target = _resolved_trait_target(effect, base_effect)
    if target and _trait_category(game_data, target):
        raised = f"{_trait_name(game_data, target)} +{effect.rank}"
        rows.append(EffectStat("enhances", "Enhances", "", raised, "better"))
    if impact.notes:
        rows.append(EffectStat("notes", "Notes", "", ", ".join(impact.notes), ""))
    return rows


def _config_display(field, value) -> str:
    """Display text for a stored config ``value``: an option's label, or, for a
    multiselect list, its labels joined with ``+`` (falls back to the raw value)."""

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
        if field.overrides:
            continue
        value = effect.config.get(field.key)
        if value:
            chosen.append(f"{field.label}: {_config_display(field, value)}")
    if chosen:
        line += "; " + ", ".join(chosen)
    return line


def power_game_terms(power: Power, game_data: GameData) -> str:
    """The power's game-term summary: one :func:`effect_game_terms` line per effect.

    A ``linked`` or ``array`` power (with two or more effects) prefixes a header and
    tags each line with its role — the array marks its base and notes the flat cost
    of each alternate — so the composite structure reads at a glance.
    """

    lines = [effect_game_terms(e, game_data) for e in power.effects]
    if len(power.effects) > 1 and power.structure == STRUCTURE_LINKED:
        body = "\n".join(f"• {line}" for line in lines)
        return "Linked (all effects activate together):\n" + body
    if len(power.effects) > 1 and power.structure == STRUCTURE_ARRAY:
        base = array_base_index(power, game_data)
        alt = array_alternate_cost(game_data)
        tagged = [
            f"• {line}" + (" [base]" if i == base else f" (Alternate Effect, {alt} pt)")
            for i, line in enumerate(lines)
        ]
        return "Array (one effect active at a time):\n" + "\n".join(tagged)
    return "\n".join(lines)


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

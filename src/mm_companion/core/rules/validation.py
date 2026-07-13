"""Power Level / allocation / linked-range validation (warnings for now)."""

from __future__ import annotations

from ..character import Character
from ..data_loader import GameData
from ..powers import STRUCTURE_LINKED, Power, PowerEffectInstance
from .derived import effective_ability, resistance_total, skill_total
from .powers_cost import effect_effective_rank
from .powers_terms import _effect_name, _effective_stats


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
        attack_key = game_data.system.trait_keys.attack
        attack_ability = (
            linked if linked is not None else effective_ability(char, game_data, attack_key)
        )
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


def power_strength_amount_violations(
    power: Power, char: Character, game_data: GameData
) -> list[str]:
    """Ability-folding amounts (Strength-Based) that exceed the wielder's current ability.

    A Strength-Based Damage (any modifier with an
    :attr:`~mm_companion.core.data_loader.Modifier.adds_ability`) pays for a fixed
    ``config["amount"]`` of that ability every rank, independent of the character's
    current value (see :func:`~mm_companion.core.rules.effect_rank_trait_bonus_cost`).
    When the wielder's current ability is *below* that bought amount, the power is
    paying for more of the ability than it can actually fold into its effect — a
    house-rule warning (like a PL cap), not a build error. Returns one message per
    such selection; empty when every folded amount is covered by the wielder's ability.

    This is surfaced only in the Power Constructor; the character-sheet card does not
    show it.
    """

    catalog = game_data.modifier_catalog()
    abbrs = {a.key: a.abbr for a in game_data.abilities}
    violations: list[str] = []
    for effect in power.effects:
        for selection in (*effect.extras, *effect.flaws):
            modifier = catalog.get(selection.modifier_id)
            if not (modifier and modifier.adds_ability):
                continue
            amount = selection.config.get("amount")
            if amount is None:
                continue  # tracks the ability dynamically — never over its value
            ability = effective_ability(char, game_data, modifier.adds_ability)
            if int(amount) > ability:
                abbr = abbrs.get(modifier.adds_ability, modifier.adds_ability)
                violations.append(
                    f"{_effect_name(effect, game_data)}: {modifier.name} pays for "
                    f"{int(amount)} ranks of {abbr} but the wielder has only {ability}."
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


def power_level_violations(char: Character, game_data: GameData) -> list[str]:
    """Report Power Level cap breaches (``mm-core-mechanics.md`` §7); empty list = valid.

    Evaluates the character-wide caps: per-skill modifier plus each paired-resistance
    cap (Dodge + Toughness, Fortitude + Will). The trait pairings and their labels come
    from ``system.json`` (``paired_caps``), not this resolver. The attack + effect-rank
    cap is per-power and checked in :func:`power_pl_violations` instead.
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

    for pair in game_data.system.paired_caps:
        cap = caps.get(pair.cap)
        if cap is None:
            continue
        limit = cap.limit(pl)
        value = sum(resistance_total(char, game_data, key) for key in pair.traits)
        if value > limit:
            violations.append(f"{pair.label} {value} exceeds PL cap {limit}.")

    return violations

"""Power Level / allocation / linked-range validation (warnings for now)."""

from __future__ import annotations

import math
from collections.abc import Iterator

from ..character import Character
from ..data_loader import GameData
from ..powers import STRUCTURE_LINKED, Power, PowerEffectInstance, PowerGroup, PowerNode
from .derived import effective_ability, resistance_total, skill_total
from .equipment import item_effective_build
from .powers_cost import effect_effective_rank, effect_size_rank_shift
from .powers_terms import _effect_name, _effective_stats
from .runtime import config_trait_allocation, trait_display_name
from .size import size_resistance_shift, size_skill_shift
from .trait_rates import trait_rank_cap


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

    True when a modifier grants the attack roll — an attacking effect's implicit
    ``attack`` extra, or one taken explicitly on any other effect — and none drops it
    (a Perception-Range extra removes the roll, making the effect auto-hit). Reads the
    resolved :class:`~mm_companion.core.rules.EffectImpact` rather than the base
    effect's check prose, so Deflect's "Deflect vs. Attack" is correctly *not* an
    attack roll and an effect given the Attack extra correctly is one. This is the same
    condition :func:`power_pl_violations` uses to pick the attack-plus-rank cap, and
    what gates the constructor's attack-skill picker.
    """

    impact = _effective_stats(effect, game_data)[3]
    return impact.grants_attack and not impact.drops_check


def power_pl_violations(power: Power, char: Character, game_data: GameData) -> list[str]:
    """Power Level cap breaches within a single power, for its wielding character.

    Checks each offensive effect (one with a
    :attr:`~mm_companion.core.data_loader.Effect.resistance_dc_base`) against the
    Power Level caps in ``docs/mm-core-mechanics.md`` §7, reading the character so the
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

    Both caps then shift by :func:`~.powers_cost.effect_size_rank_shift` — the ranks the
    character's **size** is paying for. The Size Table raises a large creature's
    Strength, so a Strength-Based Damage would otherwise breach a cap for being large;
    the book raises the Damage limit by the same amount instead. An effect that folds no
    ability in shifts by zero, so a Blast gains nothing.

    Returns one message per offending effect. Both caps derive from Power Level (the
    ``attack_effect`` cap for the ×2 ceiling), never hardcoded.
    """

    cap = game_data.costs.power_level.caps.get("attack_effect")
    if cap is None:
        return []
    power_level = char.power_level
    base_limit = cap.limit(power_level)

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
        # Size raises the Damage limit by as much as it raised the Strength folded into
        # it, so a big creature is not charged twice for being big. Zero for an effect
        # that folds no ability in, which is why a Blast gains nothing here.
        size_shift = effect_size_rank_shift(effect, game_data, char)
        limit = base_limit + size_shift
        rank_limit = power_level + size_shift
        if effect_makes_attack(effect, game_data):
            attack = attack_ability + impact.check_bonus
            if attack + rank > limit:
                violations.append(
                    f"{base.name}: attack +{attack} plus rank {rank} = {attack + rank} "
                    f"exceeds the PL {power_level} cap of {limit}."
                )
        elif rank > rank_limit:  # auto-hit effect: rank alone is capped at PL
            violations.append(
                f"{base.name} rank {rank} exceeds the PL {power_level} rank cap of {rank_limit}."
            )
    return violations


def leaf_powers(nodes: list[PowerNode]) -> Iterator[Power]:
    """Yield every leaf :class:`Power` in a powers tree, ignoring array selection.

    Unlike :func:`~mm_companion.core.rules.live_powers`, this descends into *all*
    children of every group — including an array's unselected alternates — because
    Power Level caps apply to the whole build, not just the currently-active branch.

    Public because three places wanted it and each grew its own copy: PL
    validation here, the GM card's hover summary, and the powers block. A tree
    walk is not a rule, but where the walk *stops* is one, and three answers to
    that is two too many.
    """

    for node in nodes:
        if isinstance(node, PowerGroup):
            yield from leaf_powers(node.children)
        else:
            yield node


def offensive_builds(char: Character, game_data: GameData) -> Iterator[Power]:
    """Every assembled build on *char* whose effects face a Power Level cap.

    The leaf powers **and** the gear. An item's
    :attr:`~mm_companion.core.equipment.EquipmentItem.build` is a real :class:`Power`,
    so a rifle obeys the attack-plus-rank cap exactly as a blast power does — the
    printed rules are explicit that buying an effect with Equipment Points does not buy
    it out of Power Level (``docs/mm-equipment-design.md`` §2).

    Each item is yielded as its **effective** build
    (:func:`~.equipment.item_effective_build`) — the weapon as it is actually used,
    with whatever is fitted to it folded in. A laser sight's Accurate raises the rifle's
    attack, and a cap that read the bare build would have missed it: the card's own ⚠
    already reads the effective build, so the two disagreed, and the one that was wrong
    is the character-wide estimate the NPC cards are drawn from.

    *Every* item, not only the worn ones: wearing is runtime state a player flips
    mid-scene, and a Power Level cap is a statement about the **build**. A sheet that
    passed validation by sheathing its sword would be validating nothing.
    """

    yield from leaf_powers(char.powers)
    for item in char.equipment:
        yield item_effective_build(item, game_data)


def _pl_for_cap(value: int, cap) -> int:
    """Smallest Power Level under which ``value`` obeys ``value <= pl * mult + add``."""

    if cap.mult <= 0:
        return 0
    return max(0, math.ceil((value - cap.add) / cap.mult))


def estimated_power_level(char: Character, game_data: GameData) -> int:
    """Estimate a character's effective Power Level from its traits alone.

    The smallest Power Level that would keep the build legal under the three caps in
    ``docs/mm-core-mechanics.md`` §7 — the ``max`` of:

    - the attack + effect-rank cap over every offensive effect of every build the
      character carries — its powers **and** its gear (:func:`offensive_builds`),
      mirroring :func:`power_pl_violations`: an attack-roll effect needs
      ``ceil((attack + rank) / 2)``, an auto-hit effect needs ``rank`` outright;
    - each paired-resistance cap from ``system.json`` (Dodge + Toughness,
      Fortitude + Will), summed via :func:`resistance_total` — which already folds in
      what worn armour grants, since equipment contributions reach the derived totals
      like any other standing bonus.

    Used for NPCs, which carry no power-point budget, so their Power Level is derived
    from what they can do rather than what they cost. A mook whose whole threat is the
    rifle it carries would otherwise estimate at 0. A trait-less NPC still does.
    All numbers are data-driven (the caps come from ``costs.json``/``system.json``).
    """

    pl = 0

    attack_cap = game_data.costs.power_level.caps.get("attack_effect")
    if attack_cap is not None:
        attack_key = game_data.system.trait_keys.attack
        for power in offensive_builds(char, game_data):
            for effect in power.effects:
                base = next((e for e in game_data.effects if e.id == effect.effect_id), None)
                if base is None or base.resistance_dc_base is None:
                    continue  # not an attack/resisted effect — these caps don't apply
                # Size raises the effective rank *and* the cap it is measured
                # against, so it has to come back off here or a big creature reads
                # as needing a Power Level it does not: this is the same subtraction
                # power_pl_violations makes by adding size_shift to its limits, and
                # the two functions have to agree or the card claims a PL the
                # validator says is legal several ranks lower.
                rank = effect_effective_rank(effect, game_data, char)
                rank -= effect_size_rank_shift(effect, game_data, char)
                if effect_makes_attack(effect, game_data):
                    linked = effect_attack_skill_bonus(effect, char, game_data)
                    attack_ability = (
                        linked
                        if linked is not None
                        else effective_ability(char, game_data, attack_key)
                    )
                    impact = _effective_stats(effect, game_data)[3]
                    attack = attack_ability + impact.check_bonus + rank
                    pl = max(pl, _pl_for_cap(attack, attack_cap))
                else:  # auto-hit effect: rank alone is capped at PL
                    pl = max(pl, rank)

    for pair in game_data.system.paired_caps:
        cap = game_data.costs.power_level.caps.get(pair.cap)
        if cap is None:
            continue
        value = sum(resistance_total(char, game_data, key) for key in pair.traits)
        pl = max(pl, _pl_for_cap(value, cap))

    return pl


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


def synced_effect_rank(effect: PowerEffectInstance, game_data: GameData) -> int | None:
    """The rank an effect's own allocation dictates, or ``None`` when it dictates none.

    An effect whose base declares ``rankFollowsAllocation`` (Enhanced Trait) has no rank
    of its own to set: what it costs comes from the traits it raises, so its rank is
    simply how many ranks those rows spend. Every other allocation effect — Enhanced
    Senses, Comprehend, Immunity, Feature — keeps a rank the player buys and allocates
    *within*, and gets ``None`` here.

    The constructor writes this back onto the instance as the rows change, so the saved
    rank and the rows can never drift apart; :func:`power_allocation_violations` skips
    the same effects, since an effect that *is* its allocation cannot overspend it.
    """

    base = next((e for e in game_data.effects if e.id == effect.effect_id), None)
    if base is None or not base.rank_follows_allocation:
        return None
    return effect_allocation_used(effect, game_data)


def power_allocation_violations(power: Power, game_data: GameData) -> list[str]:
    """Over-allocation breaches: a Tier-4 effect spending more ranks than it has.

    Enhanced Senses/Movement, Comprehend, Immunity, and Feature allocate the effect's
    rank across a menu (see :func:`effect_allocation_used`); spending more than the
    effect's rank is invalid. Returns one message per over-allocated effect.

    An effect whose rank *follows* its allocation (:func:`synced_effect_rank`) is skipped:
    there is no budget to breach, and warning about one would be warning about a number
    the player cannot set.
    """

    violations: list[str] = []
    for effect in power.effects:
        base = next((e for e in game_data.effects if e.id == effect.effect_id), None)
        if base is None or base.rank_follows_allocation:
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


def power_trait_allocation_violations(
    power: Power, game_data: GameData, char: Character | None = None
) -> list[str]:
    """Allocation rows holding more ranks of a trait than that trait can be taken at.

    An advantage is the only trait with a ceiling of its own: most are not ranked at all
    (three ranks of Fearless is not a stronger Fearless, it is three points wasted), and
    a few carry a fixed ``maxRank``. Abilities, resistances and skills are bounded by the
    Power Level instead, which :func:`power_pl_violations` checks against the whole build.

    A warning rather than a clamp. The row is the player's, it is on screen, and silently
    charging for fewer ranks than it shows would leave the footer disagreeing with the
    rows above it — the one thing a cost line must never do.
    """

    violations: list[str] = []
    for effect in power.effects:
        base = next((e for e in game_data.effects if e.id == effect.effect_id), None)
        if base is None:
            continue
        for target, ranks in config_trait_allocation(effect.config, base):
            cap = trait_rank_cap(char, game_data, target)
            if cap is None or ranks <= cap:
                continue
            name = trait_display_name(game_data, target)
            reason = "is not ranked" if cap == 1 else f"is capped at {cap} ranks"
            violations.append(f"{base.name}: {name} {reason}; {ranks} allocated.")
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


def power_modifier_requirement_violations(power: Power, game_data: GameData) -> list[str]:
    """Modifiers attached without a prerequisite they depend on (a warning).

    A modifier can declare :attr:`~mm_companion.core.data_loader.Modifier.requires_any`
    — modifier ids of which at least one must also sit on the same effect. Affliction's
    Increasing Difficulty needs Cumulative or Progressive to have repeated resistance
    checks to escalate, so attaching it alone is flagged. Returns one message per
    unmet requirement; empty when every dependency is satisfied.
    """

    catalog = game_data.modifier_catalog()
    violations: list[str] = []
    for effect in power.effects:
        attached = {sel.modifier_id for sel in (*effect.extras, *effect.flaws)}
        for modifier_id in attached:
            modifier = catalog.get(modifier_id)
            if modifier is None or not modifier.requires_any:
                continue
            if attached.isdisjoint(modifier.requires_any):
                needed = " or ".join(catalog[m].name for m in modifier.requires_any if m in catalog)
                violations.append(
                    f"{_effect_name(effect, game_data)}: {modifier.name} requires " f"{needed}."
                )
    return violations


def power_linked_range_violations(power: Power, game_data: GameData) -> list[str]:
    """Linked-effect Range mismatches (``docs/mm-powers-architecture.md`` §4).

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
    """Report Power Level cap breaches (``docs/mm-core-mechanics.md`` §7); empty list = valid.

    Evaluates the character-wide caps: per-skill modifier plus each paired-resistance
    cap (Dodge + Toughness, Fortitude + Will). The trait pairings and their labels come
    from ``system.json`` (``paired_caps``), not this resolver. The attack + effect-rank
    cap is per-build and checked in :func:`power_pl_violations` instead — which the
    Powers and Equipment blocks both call, so a weapon's breach is marked on its own
    card.

    Toughness from worn armour counts here exactly like bought Toughness, and needs no
    special case to: :func:`resistance_total` reads the sheet-wide bonus, into which
    equipment contributions already flow (``docs/mm-equipment-design.md`` §2 — gear does
    not buy its way out of Power Level).

    Size *does* get a special case, and it is one rule: **a cap moves by exactly what
    size moved in its inputs**, so being large is never paid for twice. That is
    :func:`~.size.size_skill_shift` per row and :func:`~.size.size_resistance_shift` per
    paired trait — the latter following the resistance *derivation* rather than the
    contribution list, since the Size Table's Defense column lands on the Defence trait
    while the cap is written against Dodge. On the base ruleset the pair then cancels to
    zero all by itself (Dodge −N, Toughness +N), which is the arithmetic the book
    intends and the reason this is not simply "raise both caps".
    """

    caps = game_data.costs.power_level.caps
    pl = char.power_level
    violations: list[str] = []

    skill_cap = caps.get("skill_modifier")
    if skill_cap is not None:
        base_limit = skill_cap.limit(pl)
        for row_id in char.skill_ranks:
            total = skill_total(char, game_data, row_id)
            limit = base_limit + size_skill_shift(char, game_data, row_id)
            if total > limit:
                violations.append(f"{row_id} modifier {total} exceeds PL cap {limit}.")

    for pair in game_data.system.paired_caps:
        cap = caps.get(pair.cap)
        if cap is None:
            continue
        limit = cap.limit(pl) + sum(
            size_resistance_shift(char, game_data, key) for key in pair.traits
        )
        value = sum(resistance_total(char, game_data, key) for key in pair.traits)
        if value > limit:
            violations.append(f"{pair.label} {value} exceeds PL cap {limit}.")

    return violations

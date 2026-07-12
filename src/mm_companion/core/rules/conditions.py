"""The non-roll condition state resolver and its queryable accessors.

A character's condition state is a flattened set with provenance
(``character.conditions``): applying an umbrella stores the umbrella plus one member
row per bundled condition. These functions read the condition graph (``includes`` /
``supersedes`` / ``stacking`` / ``debilitates``) generically from the catalog — no
per-condition branches. Anything that rolls dice is the roll layer's job and is
deliberately not here.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from ..character import AppliedCondition, Character
from ..components import MECH_CHECK_PENALTY, MECH_DEBILITATE_TRAIT, MECH_MOVEMENT_MOD
from ..data_loader import Condition, GameData, RandomActionRow
from ..dice import roll_d20
from ..registry import Registry


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


# --- condition mechanism registry (stat-row overlay) ----------------------------------
# Which condition mechanisms overlay a displayed stat row, and how (§4). Each handler
# answers, for one applied condition, "what does this mechanism contribute to the row
# answering to *scope_keys*?" — a flat ``delta``, an override ``op``, and a tooltip
# ``label`` — or ``None`` when it doesn't apply here. The base registers the two
# stat-row mechanisms (``check_penalty``, ``debilitate_trait``); a mod's Python module
# can register another. An unregistered mechanism contributes nothing to the overlay
# (unchanged behaviour), while still feeding whatever dedicated accessor reads its
# typed field.


@dataclass(frozen=True)
class ScopeContribution:
    """One condition mechanism's contribution to a stat-row overlay."""

    delta: int = 0
    op: str = ""
    label: str = ""


MechanismScope = Callable[[Condition, AppliedCondition, set[str]], "ScopeContribution | None"]

MECHANISM_SCOPES: Registry[MechanismScope] = Registry("condition.mechanism")


@MECHANISM_SCOPES.handler(MECH_CHECK_PENALTY)
def _scope_check_penalty(
    cond: Condition, applied: AppliedCondition, scope_keys: set[str]
) -> ScopeContribution | None:
    """A check-penalty condition (Impaired/Disabled) → a flat ``delta`` on the row.

    Applies when unscoped (``None`` / ``"All checks"``) or when its parameter is one of
    *scope_keys*; contributes nothing to a row it doesn't scope to.
    """

    if cond.penalty is None:
        return None
    unscoped = applied.parameter in (None, "All checks")
    if not (unscoped or applied.parameter in scope_keys):
        return None
    label = cond.name if unscoped else f"{cond.name} ({applied.parameter})"
    return ScopeContribution(delta=cond.penalty, label=f"{cond.penalty:+d} {label}")


@MECHANISM_SCOPES.handler(MECH_DEBILITATE_TRAIT)
def _scope_debilitate(
    cond: Condition, applied: AppliedCondition, scope_keys: set[str]
) -> ScopeContribution | None:
    """A debilitated trait (Debilitated) whose parameter matches → the trait is lost.

    An ``op="zero"`` that dominates any delta: skills read as untrained, an ability
    auto-fails its checks. Contributes nothing to a row it doesn't name.
    """

    if applied.parameter not in scope_keys:
        return None
    return ScopeContribution(op="zero", label=f"lost — {cond.name} ({applied.parameter})")


def condition_scope_penalty(
    character: Character, game_data: GameData, scope_keys: set[str]
) -> ConditionEffect:
    """The condition overlay for a stat row answering to *scope_keys*.

    Each applied condition's mechanisms are dispatched through
    :data:`MECHANISM_SCOPES`; a handler that applies contributes a flat ``delta``, an
    override ``op`` (``"zero"`` for a lost trait), and a tooltip label. Scope keys are
    an ability row → ``{key, name}`` or a skill row → ``{row_id, base_name}``. Returns
    the merged penalty, the contributing condition ids (for strikethrough), and a
    tooltip breakdown.
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
        for mechanism in cond.mechanisms:
            handler = MECHANISM_SCOPES.get(mechanism)
            if handler is None:
                continue
            contribution = handler(cond, applied, scope_keys)
            if contribution is None:
                continue
            total += contribution.delta
            if contribution.op:
                op = contribution.op
            ids.add(cond.id)
            parts.append(contribution.label)
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

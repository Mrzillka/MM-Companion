"""Targeted combat actions — Aid, Feint, Trick (combat maneuvers plan, phase 8) and Grab,
Trip, Disarm, Smash, Escape (phase 9).

Each is a check by the actor, read off its ``actions.json`` record's ``targeted`` block
(:class:`~mm_companion.core.data_loader.TargetedRules`) — the best of its candidate
checks on the actor's sheet (:func:`targeted_check_spec`) — judged:

* against a **fixed DC** (Aid: an attack check against DC 10);
* against the target's **Defense** (Grab, Trip, Disarm, Smash: an attack);
* against a DC **derived from the target** (Escape: 10 + the grabber's Strength,
  :func:`targeted_dc_from`); or
* **opposed** by the target: the target rolls the best of its ``resist`` candidates
  (Feint: Close Combat, Ranged Combat, Deception or Insight; Trick: Deception or
  Insight) against the actor's total, a natural 20 counting as an added degree
  (:func:`~.targeting.opposed_dc`).

An attack that hits may lead into an **opposed** second stage (Grab: Strength against
the target's Strength or Dodge): the actor's check (:func:`targeted_opposed_spec`)
against the target's resistance (:func:`targeted_resist_spec`).

The actor's signed degree (:func:`actor_degree`) picks a rung of the action's ``wins``
or ``losses`` ladder (:func:`targeted_rung`), and its conditions land on the target, on
an ally (Aid) or on the actor (Grab's Holding) — each applied **against** someone
(:func:`concrete_changes`): that one's name is the condition's parameter. A rung may also
say what it does that the app does not track (Disarm: "drops what they were holding").

Aid's, Feint's and Trick's conditions are **opposing** ones (decision 17): what they do
counts only in an attack judged between the bearer and the creature they name, and that
attack spends them. :func:`opposing_effects` reads both sides of one such attack, and
:func:`defense_against` is the target's Defense in it. The GM's app judges every aimed
attack through these (``ui/attack_gm.py``), and the attack of a Grab or a Trip too.

Pure: the GM's controller (``ui/targeted_gm.py``) does the rolling and the landing.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field, replace

from ..character import AppliedCondition, Character
from ..data_loader import (
    CombatAction,
    CombatCheck,
    CombatEffect,
    ConditionChange,
    DegreeOutcome,
    GameData,
    TargetedRules,
)
from ..data_loader.game_data import (
    COMBAT_CHECK_ABILITY,
    COMBAT_CHECK_ATTACK,
    COMBAT_CHECK_DAMAGE,
    COMBAT_CHECK_RESISTANCE,
    COMBAT_CHECK_SKILL,
    COMBAT_KIND_TARGETED,
    TARGETED_AGAINST_ACTOR,
    TARGETED_AGAINST_TARGET,
    TARGETED_ON_TARGET,
)
from ..data_loader.traits import OPPOSING_ATTACKED, OPPOSING_ATTACKING
from .conditions import applied_condition_name, apply_condition
from .derived import defense_class_in_play
from .rolls import (
    KIND_ABILITY,
    KIND_BEST_OF,
    KIND_RESISTANCE,
    KIND_SKILL,
    LADDER_DAMAGE,
    RollSpec,
    ability_roll,
    best_of_key,
    resistance_roll,
    skill_roll,
)
from .targeting import failure_degrees

__all__ = [
    "OpposingEffects",
    "actor_degree",
    "concrete_changes",
    "defense_against",
    "opposing_effects",
    "opposing_spent",
    "targeted_action",
    "attack_damage_rank",
    "bare_attack_check",
    "targeted_check_spec",
    "targeted_dc_from",
    "targeted_opposed_spec",
    "targeted_outcome",
    "targeted_outcome_text",
    "targeted_resist_spec",
    "targeted_rung",
    "targeted_title",
]


def targeted_action(game_data: GameData, action_id: str) -> CombatAction | None:
    """The targeted action *action_id* names, when the app knows how to roll it (its
    record carries a ``targeted`` block) — Aid, Feint, Trick."""

    action = game_data.combat.action(action_id)
    if action is None or action.kind != COMBAT_KIND_TARGETED or action.targeted is None:
        return None
    return action


def targeted_title(action: CombatAction, option_id: str = "") -> str:
    """What the action is called on its rolls: the option picked, else the action."""

    option = action.option(option_id) if option_id else None
    return option.name if option is not None else action.name


def _skill_name(game_data: GameData, trait: str) -> str:
    skill = next((s for s in game_data.skills if s.id == trait or s.name == trait), None)
    return skill.name if skill is not None else ""


def bare_attack_check(attack: RollSpec) -> RollSpec:
    """*attack*'s check with what it would force left out — no save, no ladder — for an
    action that uses the number and does no harm itself (Feint, Grab's attack, Block).
    With no ``marks`` the attack maneuvers neither ride on it nor are spent by it."""

    return replace(
        attack,
        follow_up=None,
        also=(),
        marks=(),
        ladder=None,
        then=None,
        critical_hit=False,
        roll_twice=False,
        effect_rank=None,
    )


def attack_damage_rank(attack: RollSpec | None) -> int:
    """The Damage rank of *attack* (the first damage save it forces, as rolled on the
    attacker's sheet); ``0`` when it deals no Damage or the rank is not known here."""

    if attack is None:
        return 0
    for save in attack.follow_ups():
        if save.ladder is not None and save.ladder.kind == LADDER_DAMAGE:
            if save.effect_rank:
                return save.effect_rank
            if save.ladder.rank:
                return save.ladder.rank
    return 0


def _candidate(
    game_data: GameData, check: CombatCheck, character: Character, attack: RollSpec | None
) -> tuple[str, RollSpec] | None:
    """One candidate check as *character* rolls it: ``(what it is called, the spec)``."""

    if check.kind == COMBAT_CHECK_ATTACK:
        if attack is None:
            return None
        return "Attack", bare_attack_check(attack)
    if check.kind == COMBAT_CHECK_DAMAGE:
        rank = attack_damage_rank(attack)
        if rank <= 0:
            return None
        return "Damage", RollSpec(label=f"Damage {rank}", modifier=rank)
    if check.kind == COMBAT_CHECK_ABILITY:
        found = next((a for a in game_data.abilities if a.key == check.trait), None)
        if found is None:
            return None
        return found.name, ability_roll(character, game_data, check.trait)
    if check.kind == COMBAT_CHECK_SKILL:
        name = _skill_name(game_data, check.trait)
        if not name:
            return None
        return name, skill_roll(character, game_data, name)
    if check.kind == COMBAT_CHECK_RESISTANCE:
        found = next((r for r in game_data.resistances if r.key == check.trait), None)
        if found is None:
            return None
        return found.name, resistance_roll(character, game_data, check.trait)
    return None


def _best_candidate(
    game_data: GameData,
    checks: tuple[CombatCheck, ...],
    character: Character,
    attack: RollSpec | None,
) -> tuple[str, RollSpec] | None:
    """The highest of *checks* on *character*'s sheet (ties to the first) — the actor
    picks, and the higher number is always the better pick."""

    found = [c for c in (_candidate(game_data, k, character, attack) for k in checks) if c]
    if not found:
        return None
    return max(found, key=lambda pair: pair[1].modifier)


def targeted_check_spec(
    game_data: GameData,
    action: CombatAction,
    option_id: str,
    character: Character,
    attack: RollSpec | None = None,
) -> RollSpec | None:
    """The actor's check for *action* (taken as *option_id*), as *character* rolls it.

    The best of the record's candidate checks: the attack check of *attack* (one of the
    actor's, off the attack picker), an ability, a skill. The option's
    ``attackModifier`` goes on (Feint's −5 as a simple action, Disarm's −2), and a fixed
    DC (Aid's). ``None`` when there is nothing to roll.
    """

    rules: TargetedRules | None = action.targeted
    if rules is None:
        return None
    effect = action.effect_for(option_id)
    title = targeted_title(action, option_id)
    best = _best_candidate(game_data, rules.checks, character, attack)
    if best is None:
        return None
    source, spec = best
    modifier = spec.modifier + effect.attack_modifier
    dc = effect.dc if effect.dc is not None else rules.dc
    label = f"{title} ({source} {modifier:+d})"
    if dc is not None:
        label += f" vs. DC {dc}"
    elif rules.vs_defense:
        label += " vs. Defense"
    return replace(spec, label=label, modifier=modifier, dc=dc, hint=action.tooltip)


def targeted_opposed_spec(
    game_data: GameData,
    action: CombatAction,
    option_id: str,
    character: Character,
    attack: RollSpec | None = None,
) -> RollSpec | None:
    """The actor's half of the opposed second stage (Grab's Strength, Trip's close
    attack bonus or Strength, Disarm's Damage rank), as *character* rolls it — a plain
    number, so whoever rolls it (the actor asked, or the GM for them) rolls this one.
    ``None`` when the action has no such stage, or nothing on the sheet answers it
    (Disarm with an attack that deals no Damage)."""

    rules = action.targeted
    if rules is None or rules.opposed is None:
        return None
    best = _best_candidate(game_data, rules.opposed.checks, character, attack)
    if best is None:
        return None
    source, spec = best
    title = targeted_title(action, option_id)
    return RollSpec(label=f"{title}: {source} {spec.modifier:+d}", modifier=spec.modifier)


def targeted_dc_from(
    game_data: GameData, action: CombatAction, target: Character | None
) -> int | None:
    """The DC *action* is judged against when it is derived from the target (Escape: 10
    + the grabber's Strength), as *target*'s sheet stands; ``None`` when it is not."""

    rules = action.targeted
    if rules is None or rules.dc_from is None or target is None:
        return None
    best = _candidate(game_data, rules.dc_from, target, None)
    if best is None:
        return None
    return (rules.dc_from.dc or 0) + best[1].modifier


def _resist_candidates(
    game_data: GameData, pairs: tuple[tuple[str, str], ...]
) -> list[tuple[str, str]]:
    """The target's resist candidates as ``(kind, trait key)`` for a best-of key."""

    found = []
    for kind, trait in pairs:
        if kind == COMBAT_CHECK_SKILL:
            name = _skill_name(game_data, trait)
            if name:
                found.append((KIND_SKILL, name))
        elif kind == COMBAT_CHECK_ABILITY:
            if any(a.key == trait for a in game_data.abilities):
                found.append((KIND_ABILITY, trait))
        elif kind == COMBAT_CHECK_RESISTANCE:
            if any(r.key == trait for r in game_data.resistances):
                found.append((KIND_RESISTANCE, trait))
    return found


def targeted_resist_spec(
    game_data: GameData, action: CombatAction, title: str, dc: int | None = None
) -> RollSpec | None:
    """What the target of an opposed check rolls: the best of its candidates (a
    :data:`~.rolls.KIND_BEST_OF` spec its own sheet fills in), against *dc* — the
    actor's total. The second stage's candidates when the action has one (Grab's
    Strength or Dodge), else its own (Feint's). ``None`` when there is nothing to
    resist (Aid, Smash)."""

    rules = action.targeted
    if rules is None:
        return None
    pairs = rules.opposed.resist if rules.opposed is not None else rules.resist
    candidates = _resist_candidates(game_data, pairs)
    if not candidates:
        return None
    label = f"Resist {title}" + (f" vs. {dc}" if dc is not None else "")
    return RollSpec(
        label=label,
        modifier=0,
        kind=KIND_BEST_OF,
        trait_key=best_of_key(candidates),
        dc=dc,
        hint=action.tooltip,
    )


def actor_degree(target_degree: int | None) -> int:
    """The actor's signed degree in an opposed action, from the target's resistance:
    each degree the target failed by is one the actor won by, and each it made it by is
    one the actor lost by. ``0`` when the target's roll was not graded."""

    if target_degree is None:
        return 0
    if target_degree > 0:
        return -target_degree
    return failure_degrees(target_degree)


def targeted_rung(effect: CombatEffect, degree: int) -> DegreeOutcome | None:
    """The rung the actor's signed *degree* reaches: the deepest of ``wins`` its degrees
    of success reach, or of ``losses`` its degrees of failure reach."""

    if degree >= 1:
        ladder, reach = effect.wins, degree
    elif degree < 0:
        ladder, reach = effect.losses, failure_degrees(degree)
    else:
        return None
    rung = None
    for candidate in ladder:
        if candidate.degrees <= reach:
            rung = candidate
    return rung


def targeted_outcome(effect: CombatEffect, degree: int) -> tuple[ConditionChange, ...]:
    """What the actor's signed *degree* lands (:func:`targeted_rung`'s conditions)."""

    rung = targeted_rung(effect, degree)
    return rung.applies if rung is not None else ()


def concrete_changes(
    changes: tuple[ConditionChange, ...], *, actor_name: str, target_name: str
) -> tuple[tuple[str, ConditionChange], ...]:
    """Each change with who it lands on (``target`` or ``ally``) and its parameter set
    to the name it is against (the actor, or the target)."""

    out = []
    for change in changes:
        if change.against == TARGETED_AGAINST_ACTOR:
            parameter = actor_name
        elif change.against == TARGETED_AGAINST_TARGET:
            parameter = target_name
        else:
            parameter = change.parameter
        out.append((change.on or TARGETED_ON_TARGET, replace(change, parameter=parameter)))
    return tuple(out)


def targeted_outcome_text(
    game_data: GameData,
    changes: tuple[ConditionChange, ...],
    bearer: str,
    character: Character | None = None,
) -> str:
    """Conditions a targeted action put on *bearer* (or took off), as the history says
    it: "Thug: Feinted (Ana)", "Ana: no longer Restrained". A removal is only said when
    *character* (the bearer's sheet, before) bore it — Escape takes off Restrained *and*
    Bound, and only one of them was there."""

    catalog = game_data.condition_catalog()
    names = []
    for change in changes:
        condition = catalog.get(change.condition)
        if not change.remove:
            names.append(
                applied_condition_name(
                    AppliedCondition(
                        change.condition, parameter=change.parameter, amount=change.amount
                    ),
                    condition,
                )
            )
            continue
        borne = character is None or any(
            c.condition_id == change.condition
            and c.provenance is None
            and (change.parameter is None or c.parameter == change.parameter)
            for c in character.conditions
        )
        if borne:
            names.append(f"no longer {condition.name if condition else change.condition}")
    return f"{bearer}: {', '.join(names)}" if names else ""


# -- the opposing conditions, in one attack between two creatures --------------------------


@dataclass(frozen=True)
class OpposingEffects:
    """What the opposing conditions on two creatures do in one attack between them.

    :attr:`attack` goes on the attacker's check, :attr:`defense` on the target's Defense,
    and the target's Defense is judged as if under each of :attr:`defense_as`. The
    instances counted are spent by the attack: :attr:`attacker_spent` off the attacker,
    :attr:`target_spent` off the target. :attr:`notes` names each, for the GM's card.
    """

    attack: int = 0
    defense: int = 0
    defense_as: tuple[str, ...] = ()
    attacker_spent: tuple[AppliedCondition, ...] = ()
    target_spent: tuple[AppliedCondition, ...] = ()
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def any(self) -> bool:
        return bool(self.attacker_spent or self.target_spent)


def opposing_effects(
    attacker: Character | None,
    attacker_name: str,
    target: Character | None,
    target_name: str,
    game_data: GameData,
) -> OpposingEffects:
    """The attacker's opposing conditions that name the target and are spent by its
    attacking (Aided Attack, Trick Advantage, Tricked), and the target's that name the
    attacker and are spent by being attacked (Feinted, Aided Defense)."""

    catalog = game_data.condition_catalog()
    attack = defense = 0
    defense_as: list[str] = []
    attacker_spent: list[AppliedCondition] = []
    target_spent: list[AppliedCondition] = []
    notes: list[str] = []

    def amount_of(applied: AppliedCondition, condition) -> int:
        if applied.amount is not None:
            return applied.amount
        return condition.default_amount or 0

    for applied in attacker.conditions if attacker is not None else ():
        condition = catalog.get(applied.condition_id)
        rule = condition.opposing if condition is not None else None
        if rule is None or rule.spent_by != OPPOSING_ATTACKING:
            continue
        if applied.provenance is not None or applied.parameter != target_name:
            continue
        attack += rule.attack * amount_of(applied, condition)
        attacker_spent.append(applied)
        notes.append(applied_condition_name(applied, condition))
    for applied in target.conditions if target is not None else ():
        condition = catalog.get(applied.condition_id)
        rule = condition.opposing if condition is not None else None
        if rule is None or rule.spent_by != OPPOSING_ATTACKED:
            continue
        if applied.provenance is not None or applied.parameter != attacker_name:
            continue
        defense += rule.defense * amount_of(applied, condition)
        if rule.defense_as:
            defense_as.append(rule.defense_as)
        target_spent.append(applied)
        notes.append(f"{target_name}: {applied_condition_name(applied, condition)}")
    return OpposingEffects(
        attack=attack,
        defense=defense,
        defense_as=tuple(defense_as),
        attacker_spent=tuple(attacker_spent),
        target_spent=tuple(target_spent),
        notes=tuple(notes),
    )


def defense_against(target: Character, game_data: GameData, effects: OpposingEffects) -> int:
    """The target's Defense class in an attack *effects* was read for: in play
    (:func:`~.derived.defense_class_in_play`), judged as if under each ``defense_as``
    condition (a feint's Vulnerable), plus the opposing ``defense`` bonus."""

    character = target
    if effects.defense_as:
        character = deepcopy(target)
        for condition_id in effects.defense_as:
            apply_condition(character, condition_id, game_data)
    return defense_class_in_play(character, game_data) + effects.defense


def opposing_spent(spent: tuple[AppliedCondition, ...]) -> tuple[ConditionChange, ...]:
    """The removals that spend *spent* (each by its own parameter)."""

    return tuple(
        ConditionChange(applied.condition_id, remove=True, parameter=applied.parameter)
        for applied in spent
    )

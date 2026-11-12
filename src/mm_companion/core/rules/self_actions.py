"""The self actions of the Actions popup: Aim, Block, Charge (and Slam), Defend, Recover,
Drop Prone, Stand, Finishing Attack and Catch (PDF p200–205).

Each is an ``actions.json`` ``combat`` record of kind ``self`` or ``loader`` whose
:class:`~mm_companion.core.data_loader.CombatEffect` says what it does, read here
generically — no action is named in this module:

- an effect with a ``line`` rides on the next roll as a line on the roller's lane, and
  :func:`apply_actions` folds it into the attack it rides on (Charge's −2, Aim's second
  die, Finishing's DC 10 and critical, a Slam's rank and the slammer's own save, intent
  to kill), together with the attack maneuvers (:mod:`.maneuvers`);
- an effect without one happens at once: conditions put on or taken off the actor
  (:func:`self_action_changes` — Defend, Drop Prone, Stand slowly), or a check loaded into
  the roller (:func:`check_spec` — Catch, Stand), whose success sheds ``clears`` through
  the recovery road (:attr:`~.rolls.RollSpec.recovers`);
- Recover also offers what it may shed (:func:`recover_choices`);
- an action with a ``reaction`` (Block) taken again while its condition is on loads a
  check with the attack picked instead (:func:`reaction_spec`), and adds one to the
  condition's count (:func:`reaction_changes`).

A line's key is the action id, or ``"action/option"`` when the action was taken one of
its ways (:func:`line_key`), so the roll knows a Slam from a plain Charge.

Pure: the roller's host (``ui/sections/dice.py``) calls these.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, replace

from ..character import Character
from ..data_loader import (
    CombatAction,
    CombatCheck,
    CombatEffect,
    ConditionChange,
    GameData,
    ResistanceOutcome,
)
from ..data_loader.game_data import (
    COMBAT_CHECK_ABILITY,
    COMBAT_CHECK_SKILL,
    COMBAT_CHECK_SPEED,
    COMBAT_KIND_LOADER,
    COMBAT_KIND_SELF,
)
from .conditions import apply_condition, remove_condition
from .extra_effort import extra_effort_rules
from .maneuvers import (
    LINE_KEY_SEPARATOR,
    _relabel_check,
    _relabel_save,
    _shift_save,
    has_attack_check,
    maneuver_caption,
    shift_maneuvers,
    split_line_key,
)
from .movement import speed_lines
from .recovery import can_recover, recovery_roll
from .rolls import LADDER_DAMAGE, RollSpec, ability_roll, add_part, localize_spec, skill_roll
from .shaking import held_shake_offs, shake_off_recover_spec
from .targeted import bare_attack_check

__all__ = [
    "LINE_KEY_SEPARATOR",
    "RecoverChoice",
    "action_reason",
    "apply_actions",
    "apply_condition_changes",
    "apply_condition_requests",
    "check_spec",
    "self_action_changes",
    "line_fit",
    "line_key",
    "reacting",
    "reaction_changes",
    "reaction_spec",
    "recover_choices",
    "self_action",
    "self_action_note",
    "speed_rank",
    "split_line_key",
]

Pick = tuple[str, "int | None"]


def line_key(action_id: str, option_id: str = "") -> str:
    """The lane key of *action_id* taken as *option_id* (``"charge/slam"``)."""
    return f"{action_id}{LINE_KEY_SEPARATOR}{option_id}" if option_id else action_id


def self_action(game_data: GameData, action_id: str) -> CombatAction | None:
    """The ``combat`` record of *action_id* when it is a self or loader action that does
    something — an effect, options or a recovery. A record with none (a mod's action this
    app cannot run) is not one, so its tile stays greyed rather than doing nothing."""
    action = game_data.combat.action(action_id)
    if action is None or action.kind not in (COMBAT_KIND_SELF, COMBAT_KIND_LOADER):
        return None
    if action.effect == CombatEffect() and not action.options and action.recovery is None:
        return None
    return action


def _line_effect(game_data: GameData, key: str) -> tuple[CombatAction, CombatEffect] | None:
    action_id, option_id = split_line_key(key)
    action = self_action(game_data, action_id)
    if action is None:
        return None
    effect = action.effect_for(option_id)
    return (action, effect) if effect.line else None


def _has(character: Character | None, condition_id: str) -> bool:
    return character is not None and any(
        c.condition_id == condition_id for c in character.conditions
    )


def _condition_name(game_data: GameData, condition_id: str) -> str:
    record = game_data.condition_catalog().get(condition_id)
    return record.name if record is not None else condition_id


def action_reason(
    game_data: GameData, action: CombatAction, character: Character | None, *, gm: bool = False
) -> str | None:
    """Why *action* cannot be taken by *character* now (``None``: it can) — a player's
    tile for an action the GM runs, a condition it needs or one that rules it out."""
    if action.gm_runs and not gm:
        return action.gm_runs
    if action.requires and not any(_has(character, c) for c in action.requires):
        names = " or ".join(_condition_name(game_data, c) for c in action.requires)
        return f"Only while {names}."
    if action.refused_by and _has(character, action.refused_by):
        return f"Already {_condition_name(game_data, action.refused_by)}."
    return None


def _damage_saves(spec: RollSpec) -> list[RollSpec]:
    return [
        f
        for f in spec.follow_ups()
        if f.ladder is not None and f.ladder.kind == LADDER_DAMAGE and f.dc is not None
    ]


def line_fit(game_data: GameData, key: str, spec: RollSpec | None) -> str:
    """Why the line *key* cannot ride on the attack *spec* (``""``: it can)."""
    found = _line_effect(game_data, key)
    if found is None or spec is None:
        return ""
    _action, effect = found
    touches_check = effect.attack_modifier or effect.roll_twice or effect.dc is not None
    if (touches_check or effect.critical_hit) and not has_attack_check(spec, game_data):
        return "This attack makes no attack check."
    if effect.slam and not any(s.effect_rank > 0 for s in _damage_saves(spec)):
        return "A slam needs an attack that deals Damage."
    return ""


def speed_rank(character: Character, game_data: GameData) -> int:
    """The actor's fastest speed rank, over every mode of movement it has."""
    lines = speed_lines(character, game_data)
    return max((line.rank for line in lines), default=0)


def _slam(
    spec: RollSpec,
    action: CombatAction,
    effect: CombatEffect,
    character: Character | None,
    game_data: GameData,
) -> RollSpec:
    """The attack as a Slam: its Damage at the slam's rank, and the slammer's own save
    against it loaded next (:attr:`~.rolls.RollSpec.then`)."""
    rules = action.slam
    saves = _damage_saves(spec)
    if rules is None or not saves or saves[0].effect_rank <= 0:
        return spec
    rank = saves[0].effect_rank
    speed = speed_rank(character, game_data) if character is not None else 0
    slam_rank = max(rank + rules.rank_bonus, speed)
    if effect.full_speed:
        slam_rank += rules.full_speed_bonus
    shift = slam_rank - rank

    def shifted(save: RollSpec) -> RollSpec:
        return _shift_save(save, shift) if save in saves else save

    follow_up = None if spec.follow_up is None else shifted(spec.follow_up)
    spec = replace(spec, follow_up=follow_up, also=tuple(shifted(s) for s in spec.also))
    save = _damage_saves(spec)[0]
    own = slam_rank // 2 if rules.halve_self else slam_rank
    dc = save.dc - save.effect_rank + own
    ladder = save.ladder if save.ladder is None else replace(save.ladder, rank=own)
    then = replace(
        save,
        label=f"Your own impact — {_relabel_save(save.label, save.dc, dc)}",
        dc=dc,
        effect_rank=own,
        ladder=ladder,
        trait_key=rules.self_resistance or save.trait_key,
        targets=(),
        action_id="",
        actor="",
    )
    if character is not None:
        then = localize_spec(then, character, game_data)
    # The slammer's own roll, made on this sheet: never one for somebody else to make.
    return replace(spec, then=replace(then, rolled_by_target=False))


def _kill(spec: RollSpec, effect: CombatEffect, game_data: GameData) -> RollSpec:
    """Every damage save *spec* provokes with its deeper failures turned fatal."""
    rule = effect.kill
    if rule is None or not rule.condition:
        return spec
    index = max(0, rule.degrees - 1)
    name = _condition_name(game_data, rule.condition)
    fatal = ResistanceOutcome(conditions=(rule.condition,))

    def lethal(save: RollSpec) -> RollSpec:
        if save.ladder is None or save.ladder.kind != LADDER_DAMAGE:
            return save
        rungs = list(save.ladder.rungs)
        if not rungs:
            return save
        # The last rung covers every deeper failure, so one fatal rung at *index* does.
        while len(rungs) < index:
            rungs.append(rungs[-1])
        outcomes = list(save.outcomes)
        while outcomes and len(outcomes) < index:
            outcomes.append(outcomes[-1])
        return replace(
            save,
            ladder=replace(save.ladder, rungs=(*rungs[:index], fatal)),
            outcomes=(*outcomes[:index], name) if outcomes else (),
            label=f"{save.label} — {rule.degrees}+ degrees of failure: {name}",
        )

    follow_up = None if spec.follow_up is None else lethal(spec.follow_up)
    return replace(spec, follow_up=follow_up, also=tuple(lethal(s) for s in spec.also))


def _apply_effect(
    spec: RollSpec,
    action: CombatAction,
    effect: CombatEffect,
    character: Character | None,
    game_data: GameData,
) -> RollSpec:
    attack = has_attack_check(spec, game_data)
    if attack and effect.attack_modifier:
        modifier = spec.modifier + effect.attack_modifier
        spec = add_part(
            spec,
            action.name,
            effect.attack_modifier,
            label=_relabel_check(spec.label, spec.modifier, modifier),
        )
    if attack and effect.roll_twice:
        spec = replace(spec, roll_twice=True)
    if attack and effect.dc is not None:
        spec = replace(spec, dc=effect.dc)
    if attack and effect.critical_hit:
        spec = replace(spec, critical_hit=True)
    if effect.slam:
        spec = _slam(spec, action, effect, character, game_data)
    if effect.kill is not None:
        spec = _kill(spec, effect, game_data)
    return spec


def _line_name(action: CombatAction, key: str) -> str:
    _action_id, option_id = split_line_key(key)
    option = action.option(option_id) if option_id else None
    return option.name if option is not None else action.name


def apply_actions(
    spec: RollSpec,
    picks: Iterable[Pick],
    game_data: GameData,
    character: Character | None = None,
) -> RollSpec:
    """*spec* as rolled with every line in *picks* on it — the attack maneuvers
    (:func:`~.maneuvers.apply_maneuvers`) and the self actions' lines — named together in
    the label (``"… (All-Out Attack +3, Charge)"``). *character* is the actor, whose speed
    a Slam reads and whose own save it fills in. Unchanged when nothing applies."""
    picks = list(picks)
    names = [maneuver_caption(game_data, picks)] if maneuver_caption(game_data, picks) else []
    shifted = shift_maneuvers(spec, picks, game_data) if names else spec
    for key, _value in picks:
        found = _line_effect(game_data, key)
        if found is None or line_fit(game_data, key, spec):
            continue
        action, effect = found
        shifted = _apply_effect(shifted, action, effect, character, game_data)
        names.append(_line_name(action, key))
    if not names:
        return spec
    return replace(shifted, label=f"{shifted.label} ({', '.join(names)})")


def self_action_changes(effect: CombatEffect, game_data: GameData) -> tuple[ConditionChange, ...]:
    """What *effect* puts on or takes off the actor at once, each applied amount
    defaulting to its record's (Defending's +2)."""
    catalog = game_data.condition_catalog()
    changes = []
    for change in effect.applies:
        record = catalog.get(change.condition)
        if record is None:
            continue
        if not change.remove and change.amount is None and record.default_amount:
            change = replace(change, amount=record.default_amount)
        changes.append(change)
    return tuple(changes)


def apply_condition_changes(
    character: Character, changes: Iterable[ConditionChange], game_data: GameData
) -> bool:
    """Put *changes* on *character* (or take them off: every directly applied instance,
    with what it bundles — only the one naming the change's parameter, when it names
    one). Whether anything changed."""
    changed = False
    for change in changes:
        if change.remove:
            for applied in [
                c
                for c in character.conditions
                if c.condition_id == change.condition
                and c.provenance is None
                and (change.parameter is None or c.parameter == change.parameter)
            ]:
                if applied in character.conditions:
                    remove_condition(character, applied)
                    changed = True
            continue
        if game_data.condition_catalog().get(change.condition) is None:
            continue
        apply_condition(
            character,
            change.condition,
            game_data,
            parameter=change.parameter,
            amount=change.amount,
            temporary=bool(change.temporary),
        )
        changed = True
    return changed


def apply_condition_requests(character: Character, requests: object, game_data: GameData) -> bool:
    """Put what the roller asks for on *character* — the ``condition-requested`` payload.

    *requests* is a tuple whose entries are either ``(condition_id, amount)`` — what an
    attack maneuver leaves until the start of the next turn (All-Out Attack −3), put on
    **temporary**, replacing the amount — or a :class:`ConditionChange` from a self action
    (Defend's Defending, Stand's Hindered and Prone off). Anything else is skipped: a mod
    publishes on the same topic. Whether anything changed. The one reading both the
    sheet's Conditions block and the GM's NPC cards apply, so the two copies agree.
    """
    if not isinstance(requests, tuple):
        return False
    changed = False
    for entry in requests:
        if isinstance(entry, ConditionChange):
            changed = apply_condition_changes(character, (entry,), game_data) or changed
            continue
        if not isinstance(entry, tuple) or len(entry) != 2:
            continue
        condition_id, amount = entry
        if not isinstance(condition_id, str) or not isinstance(amount, int):
            continue
        if isinstance(amount, bool) or amount <= 0:
            continue
        if game_data.condition_catalog().get(condition_id) is None:
            continue
        apply_condition(character, condition_id, game_data, amount=amount, temporary=True)
        changed = True
    return changed


def reacting(action: CombatAction, character: Character | None) -> bool:
    """Whether taking *action* now is its reaction (Block while Blocking: a block check)
    rather than its own effect (readying)."""
    rule = action.reaction
    return rule is not None and _has(character, rule.condition)


def reaction_spec(
    game_data: GameData, action: CombatAction, character: Character, attack: RollSpec | None
) -> RollSpec | None:
    """*action*'s reaction check with the attack picked (Block: the close attack check
    against the attacker's result, which goes in the DC box): the record's ``step`` for
    each one already made — the condition's count past the first. ``None`` with no
    attack, or no reaction."""
    rule = action.reaction
    if rule is None or attack is None:
        return None
    applied = next(
        (
            c
            for c in character.conditions
            if c.condition_id == rule.condition and c.provenance is None
        ),
        None,
    )
    made = max(0, (applied.count if applied is not None else 1) - 1)
    shift = rule.step * made
    spec = bare_attack_check(attack)
    modifier = spec.modifier + shift
    detail = f"Attack {modifier:+d}"
    if made:
        detail += f", {made} earlier {'block' if made == 1 else 'blocks'} {shift:+d}"
    label = f"{action.name} ({detail})" + (f" {rule.line}" if rule.line else "")
    spec = add_part(spec, "Earlier blocks", shift)
    return replace(spec, label=label, dc=None, hint=action.tooltip)


def reaction_changes(action: CombatAction) -> tuple[ConditionChange, ...]:
    """What making the reaction once does to the actor: one more on the condition's
    count (the next block is harder)."""
    rule = action.reaction
    return (ConditionChange(rule.condition),) if rule is not None else ()


def check_spec(
    game_data: GameData, character: Character, action: CombatAction, effect: CombatEffect
) -> RollSpec | None:
    """The check *effect* loads into the roller (Catch's Agility, Stand's Acrobatics or
    speed rank), at its DC, carrying what a success sheds (``recovers``)."""
    check: CombatCheck | None = effect.check
    if check is None:
        return None
    if check.kind == COMBAT_CHECK_ABILITY:
        if not any(a.key == check.trait for a in game_data.abilities):
            return None
        spec = ability_roll(character, game_data, check.trait)
    elif check.kind == COMBAT_CHECK_SKILL:
        skill = next((s for s in game_data.skills if s.id == check.trait), None)
        if skill is None:
            return None
        spec = skill_roll(character, game_data, check.trait, label=skill.name)
    elif check.kind == COMBAT_CHECK_SPEED:
        rank = speed_rank(character, game_data)
        spec = RollSpec(label=f"Speed rank {rank}", modifier=rank)
    else:
        return None
    return replace(
        spec,
        label=f"{action.name}: {spec.label}",
        dc=check.dc,
        recovers=effect.clears,
        recovers_parameter="",
    )


@dataclass(frozen=True)
class RecoverChoice:
    """One thing Recover may shed: a recovery check to roll, or conditions changed."""

    caption: str
    spec: RollSpec | None = None
    changes: tuple[ConditionChange, ...] = ()


def recover_choices(
    character: Character, game_data: GameData, action: CombatAction
) -> list[RecoverChoice]:
    """What *action*'s recovery may do for *character* now (p204): a recovery check for
    the most severe of its listed conditions that offers one, one step down the fatigue
    ladder from the most severe fatigue condition, and a new resistance check against
    each impression or Affliction on it (p206: :func:`~.shaking.shake_off_recover_spec`)."""
    rule = action.recovery
    if rule is None:
        return []
    choices: list[RecoverChoice] = []
    direct = [c for c in character.conditions if c.provenance is None]
    for condition_id in rule.conditions:
        applied = next(
            (
                c
                for c in direct
                if c.condition_id == condition_id and can_recover(character, game_data, c)
            ),
            None,
        )
        spec = None if applied is None else recovery_roll(character, game_data, applied)
        if spec is not None:
            choices.append(RecoverChoice(f"{spec.label} vs DC {spec.dc}", spec=spec))
            break
    if rule.fatigue:
        ladder = [
            rung
            for rung in extra_effort_rules(game_data).fatigue_ladder
            if rung not in rule.conditions
        ]
        present = [i for i, rung in enumerate(ladder) if _has(character, rung)]
        if present:
            top = present[-1]
            name = _condition_name(game_data, ladder[top])
            changes = [ConditionChange(ladder[top], remove=True)]
            caption = f"Shake off {name}"
            if top > 0:
                changes.append(ConditionChange(ladder[top - 1]))
                caption += f" (to {_condition_name(game_data, ladder[top - 1])})"
            choices.append(RecoverChoice(caption, changes=tuple(changes)))
    for shake, _changes in held_shake_offs(character):
        spec = shake_off_recover_spec(character, game_data, shake)
        if spec is not None:
            choices.append(RecoverChoice(f"{spec.label} vs DC {spec.dc}", spec=spec))
    return choices


def self_action_note(
    game_data: GameData, actor: str, title: str, changes: Iterable[ConditionChange]
) -> str:
    """The history line for an action that changed conditions at once:
    ``"Ana: Defend — Defending."`` / ``"… — Hindered, no longer Prone."``."""
    parts = []
    for change in changes:
        name = _condition_name(game_data, change.condition)
        if change.remove:
            parts.append(f"no longer {name}")
        elif change.amount:
            parts.append(f"{name} {change.amount:+d}")
        else:
            parts.append(name)
    said = f" — {', '.join(parts)}" if parts else ""
    return f"{actor}: {title}{said}."

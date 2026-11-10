"""The attack maneuvers: Accurate, All-Out, Defensive and Power Attack (PDF p200).

Each one moves up to X points from one value to another — the loaded attack's attack
check, the rank of the save it forces, or the actor's Defense — as ``actions.json``'s
``combat`` records say (``bonus`` / ``penalty``, ``lasting``). This module is the
arithmetic, read generically off those records:

- :func:`maneuver_limit` — how far X may go for one maneuver: at most
  ``limits.maxShift``, the penalised value never below ``limits.floor``, and (with
  ``limits.doubleCap``) the bonused value never more than doubled. Several maneuvers
  may ride on one attack, so the caps are checked against the **combined** shifts of
  the others already set;
- :func:`apply_maneuvers` — the attack spec as rolled: its modifier moved, and every
  save it provokes moved with the effect rank (its DC, and the ladder's rank that a
  target's Impervious is measured against), named in the label;
- :func:`lasting_conditions` — what stays on the actor once the attack is rolled, hit
  or miss (All-Out's Defense penalty, Defensive's bonus), as ``(condition id, X)``.

A *pick* is ``(action id, X)`` — what the roller's lane hands over. Picks that are not
maneuvers, or move nothing, are passed over, so a lane holding other actions can be
handed in whole.

Pure: the roller's host (``ui/sections/dice.py``) calls these; the panel reads no game
data.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace

from ..character import Character
from ..data_loader import GameData
from ..data_loader.game_data import (
    COMBAT_KIND_MANEUVER,
    MANEUVER_ATTACK_CHECK,
    MANEUVER_DEFENSE,
    MANEUVER_EFFECT_RANK,
    CombatAction,
)
from .derived import defense_class_in_play
from .rolls import RollSpec, add_part

__all__ = [
    "ManeuverLimit",
    "apply_maneuvers",
    "has_attack_check",
    "lasting_conditions",
    "maneuver_action",
    "maneuver_caption",
    "maneuver_limit",
    "maneuver_trade",
    "maneuver_values",
    "shift_maneuvers",
]

#: How each value a maneuver moves is named in a reason.
_NOUNS = {
    MANEUVER_ATTACK_CHECK: "attack bonus",
    MANEUVER_EFFECT_RANK: "effect rank",
    MANEUVER_DEFENSE: "Defense",
}

#: ...and how each is named in the short caption beside a maneuver's slider.
_SHORT_NOUNS = {
    MANEUVER_ATTACK_CHECK: "attack",
    MANEUVER_EFFECT_RANK: "effect",
    MANEUVER_DEFENSE: "Defense",
}

#: Picks as the lane hands them over: ``(action id, X)``; a fixed line's X is ``None``.
Pick = tuple[str, "int | None"]


@dataclass(frozen=True)
class ManeuverLimit:
    """How far one maneuver may go on an attack: ``0..maximum``, and why not when 0."""

    maximum: int
    reason: str = ""


def maneuver_action(game_data: GameData, action_id: str) -> CombatAction | None:
    """The ``combat`` record of *action_id* when it is an attack maneuver, else ``None``."""

    action = game_data.combat.action(action_id)
    if action is None or action.kind != COMBAT_KIND_MANEUVER:
        return None
    return action


def has_attack_check(spec: RollSpec, game_data: GameData) -> bool:
    """Whether *spec* is an attack check — the line its condition mods fold into, which
    is what its :attr:`~.rolls.RollSpec.marks` say. An effect check provoking a save
    (no attack roll) is not one, and no maneuver may change a check it never makes."""

    return game_data.system.trait_keys.attack_check in spec.marks


def _ranked_saves(spec: RollSpec) -> list[RollSpec]:
    """The saves *spec* provokes whose rank is known (a DC to move, a rank to read)."""

    return [f for f in spec.follow_ups() if f.dc is not None and f.effect_rank > 0]


def maneuver_values(
    spec: RollSpec | None, character: Character | None, game_data: GameData
) -> dict[str, int | None]:
    """What each value a maneuver can move stands at now, ``None`` where the attack has
    no such value: its attack bonus, the lowest rank of the saves it forces (a Linked
    attack moves every one of them), and the actor's Defense as it stands in play."""

    values: dict[str, int | None] = {
        MANEUVER_ATTACK_CHECK: None,
        MANEUVER_EFFECT_RANK: None,
        MANEUVER_DEFENSE: None,
    }
    if spec is not None:
        if has_attack_check(spec, game_data):
            values[MANEUVER_ATTACK_CHECK] = spec.modifier
        ranks = [f.effect_rank for f in _ranked_saves(spec)]
        if ranks:
            values[MANEUVER_EFFECT_RANK] = min(ranks)
    if character is not None:
        values[MANEUVER_DEFENSE] = (
            defense_class_in_play(character, game_data) - game_data.system.defense_dc_base
        )
    return values


def _shifts(picks: Iterable[Pick], game_data: GameData) -> dict[str, int]:
    """The net shift each value takes from *picks* (a bonus up, a penalty down)."""

    shifts: dict[str, int] = {}
    for action_id, amount in picks:
        action = maneuver_action(game_data, action_id)
        if action is None or not amount or amount < 0:
            continue
        if action.bonus:
            shifts[action.bonus] = shifts.get(action.bonus, 0) + amount
        if action.penalty:
            shifts[action.penalty] = shifts.get(action.penalty, 0) - amount
    return shifts


def maneuver_limit(
    game_data: GameData,
    action_id: str,
    spec: RollSpec | None,
    character: Character | None,
    others: Mapping[str, int | None] | None = None,
) -> ManeuverLimit:
    """How far *action_id* may go on the attack *spec*, given the *others* already set.

    *others* maps the other maneuvers on the lane to their X. Their shifts are added
    before the caps are read, so two maneuvers that both raise the attack check share
    the room to double it, while two that cancel (Accurate and Power) leave each other
    alone. A maximum of 0 comes with the reason.
    """

    action = maneuver_action(game_data, action_id)
    if action is None:
        return ManeuverLimit(0, "Not an attack maneuver.")
    if spec is None:
        return ManeuverLimit(0, "Load an attack first.")
    limits = game_data.combat.limits
    values = maneuver_values(spec, character, game_data)
    shifts = _shifts(((k, v) for k, v in (others or {}).items() if k != action_id), game_data)
    room = limits.max_shift
    for target, penalised in ((action.penalty, True), (action.bonus, False)):
        if not target:
            continue
        noun = _NOUNS.get(target, target)
        value = values.get(target)
        if value is None:
            return ManeuverLimit(0, f"This attack has no {noun} to trade.")
        shifted = value + shifts.get(target, 0)
        if penalised:
            # The penalised value may not go below the floor.
            if value <= limits.floor:
                return ManeuverLimit(0, f"Your {noun} is {value:+d}: there is nothing to trade.")
            room = min(room, shifted - limits.floor)
        elif limits.double_cap:
            # The bonused value may not more than double, so a 0 cannot be raised.
            if value <= 0:
                return ManeuverLimit(0, f"Your {noun} is {value:+d}: it cannot be doubled.")
            room = min(room, 2 * value - shifted)
    if room <= 0:
        return ManeuverLimit(0, "The other maneuvers on this attack already use the room.")
    return ManeuverLimit(room)


def maneuver_caption(game_data: GameData, picks: Iterable[Pick]) -> str:
    """The maneuvers in *picks* that move something, as the history names them:
    ``"All-Out Attack +3, Power Attack +2"`` (``""`` when there are none)."""

    parts = []
    for action_id, amount in picks:
        action = maneuver_action(game_data, action_id)
        if action is not None and amount and amount > 0:
            parts.append(f"{action.name} +{amount}")
    return ", ".join(parts)


def maneuver_trade(game_data: GameData, action_id: str, amount: int) -> str:
    """What *amount* of the maneuver *action_id* trades, for the caption beside its
    slider: ``"+3 attack, −3 Defense"`` (``""`` for anything but a maneuver).

    A bare number beside the slider said how far it was set and nothing of what that
    moved; the tooltip had the rule, but the trade is what a player weighs each turn.
    """

    action = maneuver_action(game_data, action_id)
    if action is None:
        return ""
    parts = []
    if action.bonus:
        parts.append(f"+{amount} {_SHORT_NOUNS.get(action.bonus, action.bonus)}")
    if action.penalty:
        parts.append(f"−{amount} {_SHORT_NOUNS.get(action.penalty, action.penalty)}")
    return ", ".join(parts)


def _relabel_check(label: str, old: int, new: int) -> str:
    """An attack line's label with its bonus restated: ``Attack +8 vs.`` → ``+11``."""

    return label.replace(f"{old:+d} vs.", f"{new:+d} vs.", 1)


def _relabel_save(label: str, old: int, new: int) -> str:
    """A save's label with its DC restated (the last whole ``old`` in it)."""

    matches = list(re.finditer(rf"(?<!\d){old}(?!\d)", label))
    if not matches:
        return label
    last = matches[-1]
    return f"{label[: last.start()]}{new}{label[last.end():]}"


def _shift_save(save: RollSpec, shift: int) -> RollSpec:
    if save.dc is None or save.effect_rank <= 0:
        return save
    dc = save.dc + shift
    ladder = save.ladder
    if ladder is not None and ladder.rank:
        ladder = replace(ladder, rank=max(0, ladder.rank + shift))
    return replace(
        save,
        dc=dc,
        label=_relabel_save(save.label, save.dc, dc),
        ladder=ladder,
        effect_rank=max(0, save.effect_rank + shift),
        dc_if_resisted=(None if save.dc_if_resisted is None else save.dc_if_resisted + shift),
    )


def apply_maneuvers(spec: RollSpec, picks: Iterable[Pick], game_data: GameData) -> RollSpec:
    """*spec* as it is rolled with the maneuvers in *picks* on it.

    The attack-check shift moves the modifier; the effect-rank shift moves every save it
    provokes (its DC and its ladder's rank, which Impervious is measured against).
    Defense is the actor's, not the attack's, and lands as a condition instead
    (:func:`lasting_conditions`). The label names the maneuvers, so the history card
    says what was traded. Unchanged when nothing in *picks* moves anything.
    """

    picks = list(picks)
    caption = maneuver_caption(game_data, picks)
    if not caption:
        return spec
    shifted = shift_maneuvers(spec, picks, game_data)
    return replace(shifted, label=f"{shifted.label} ({caption})")


def shift_maneuvers(spec: RollSpec, picks: Iterable[Pick], game_data: GameData) -> RollSpec:
    """:func:`apply_maneuvers` without the caption in the label — for a caller that
    names the maneuvers together with the other actions on the roll."""

    picks = list(picks)
    shifts = _shifts(picks, game_data)
    attack = shifts.get(MANEUVER_ATTACK_CHECK, 0) if has_attack_check(spec, game_data) else 0
    effect = shifts.get(MANEUVER_EFFECT_RANK, 0)
    label = spec.label
    modifier = spec.modifier + attack
    if attack:
        label = _relabel_check(label, spec.modifier, modifier)
    follow_up = spec.follow_up
    also = spec.also
    if effect:
        follow_up = None if follow_up is None else _shift_save(follow_up, effect)
        also = tuple(_shift_save(s, effect) for s in also)
    shifted = spec
    if attack:
        # One breakdown line per maneuver, so the hover says which traded what.
        for action_id, amount in picks:
            part = _shifts([(action_id, amount)], game_data).get(MANEUVER_ATTACK_CHECK, 0)
            action = maneuver_action(game_data, action_id)
            if part and action is not None:
                shifted = add_part(shifted, action.name, part)
    return replace(shifted, label=label, modifier=modifier, follow_up=follow_up, also=also)


def lasting_conditions(picks: Iterable[Pick], game_data: GameData) -> list[tuple[str, int]]:
    """What the maneuvers in *picks* leave on the actor once the attack is rolled: each
    one's ``lasting`` condition, sized by its X. Hit or miss (decision 1 of the plan)."""

    lasting: list[tuple[str, int]] = []
    for action_id, amount in picks:
        action = maneuver_action(game_data, action_id)
        if action is not None and action.lasting and amount and amount > 0:
            lasting.append((action.lasting, amount))
    return lasting

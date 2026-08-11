"""The damage ladder, walked: a degree of failure in, conditions on the target out.

Every effect that forces a resistance check can carry a ladder of what failing it
*does* — ``resistanceOutcomes`` in ``effects.json``, one rung per degree of failure
plus an optional ``success`` rung for the effects where making the save still costs
something (Damage's Hit). Until now that ladder was only ever **rendered**: the roll
history says "Incapacitated!" and leaves the GM to find three conditions in a menu of
thirty-nine. This module is the other half — the rungs as a small set of steps a UI
can offer as buttons, and the resolver that puts one on a character.

Which effect counts as *the* damage ladder is data too (``system.damage_effect``), so
nothing here names a condition, an effect or a degree.

Two rules make a rung more than a list of ids, and both read the condition graph
rather than spelling anything out:

**Escalation.** "Stunned instead of Dazed if already Dazed" was prose in a ``note``;
it is now a rung's ``escalates`` map, and it *chains* — a rung naming both
``incapacitated -> dying`` and ``dying -> dead`` walks a target one rung further down
with each failure, which is exactly what the book's "further failed checks escalate to
Dying, then Dead" means.

**Order.** A rung's ids are printed in the book's order, which is not always the order
they can be *applied* in. Rung 2 reads ``hit, stunned, staggered``: applying Stunned
and then Staggered re-adds Dazed (a member of the Staggered bundle) with the Stunned
that should have superseded it already down. Applying Staggered first and Stunned
second is correct, and asking the graph which way round that is — does a sibling
supersede anything in my own bundle? — keeps the printed order intact everywhere else.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..character import Character
from ..data_loader import Effect, GameData, ResistanceOutcome
from .conditions import apply_condition, expand_includes

__all__ = [
    "DamageStep",
    "damage_steps",
    "resolve_damage_step",
    "apply_damage_step",
    "damage_step_summary",
]

#: What the step for a *made* save is captioned. Index 0 of the ladder is not a
#: degree of failure, so it gets a tick rather than a number.
SUCCESS_LABEL = "✓"


@dataclass(frozen=True)
class DamageStep:
    """One rung of the ladder, ready to be offered as a button.

    :attr:`index` is ``0`` for a *made* save (which still costs the target a Hit) and
    ``1..n`` for degrees of failure, matching how the ladder is indexed everywhere
    else — the last rung covers every deeper failure, so a three-rung ladder answers a
    five-degree rout without inventing rungs.
    """

    index: int
    #: The button's caption: a tick for the made save, else the degree count.
    label: str
    #: The rung as printed — "Hit + Dazed" — before any escalation.
    caption: str
    #: The condition ids the rung names, in the book's order.
    conditions: tuple[str, ...]
    #: ``(already-has, apply-instead)`` pairs; see the module docstring.
    escalates: tuple[tuple[str, str], ...]
    #: The rung's qualifier, if it has one.
    note: str


def _condition_name(condition_id: str, game_data: GameData) -> str:
    record = game_data.condition_catalog().get(condition_id)
    return record.name if record is not None else condition_id


def _caption(ids: tuple[str, ...], game_data: GameData) -> str:
    return " + ".join(_condition_name(cid, game_data) for cid in ids)


def _damage_effect(game_data: GameData) -> Effect | None:
    """The effect whose ladder is the damage ladder, or ``None`` if it has none."""

    wanted = game_data.system.damage_effect
    return next((effect for effect in game_data.effects if effect.id == wanted), None)


def _step(index: int, rung: ResistanceOutcome, game_data: GameData) -> DamageStep | None:
    """One rung as a step, or ``None`` for a rung this control cannot offer.

    A rung that names no conditions — Affliction's, which reads its ids off the
    *instance* the player configured, or a prose-only ``text`` rung — has nothing to
    apply to a character sitting on a card, so it is skipped rather than shown as a
    button that would do nothing.
    """

    if not rung.conditions:
        return None
    return DamageStep(
        index=index,
        label=SUCCESS_LABEL if index == 0 else str(index),
        caption=_caption(rung.conditions, game_data),
        conditions=rung.conditions,
        escalates=rung.escalates,
        note=rung.note,
    )


def damage_steps(game_data: GameData) -> tuple[DamageStep, ...]:
    """The damage ladder as steps: the made save first, then one per degree of failure.

    Empty when the ruleset's damage effect is missing or carries no ladder — a caller
    then shows no control at all rather than an inert one.
    """

    effect = _damage_effect(game_data)
    if effect is None:
        return ()
    steps: list[DamageStep] = []
    if effect.resistance_success is not None:
        first = _step(0, effect.resistance_success, game_data)
        if first is not None:
            steps.append(first)
    for degree, rung in enumerate(effect.resistance_outcomes, start=1):
        step = _step(degree, rung, game_data)
        if step is not None:
            steps.append(step)
    return tuple(steps)


def _has(character: Character, condition_id: str) -> bool:
    """Whether the character carries a condition, however it got there."""

    return any(applied.condition_id == condition_id for applied in character.conditions)


def _escalate(character: Character, condition_id: str, ladder: dict[str, str]) -> str:
    """Walk *condition_id* up the rung's escalation chain against what is already on.

    One step per condition the target already has, so a rung applied twice lands one
    rung further down each time. The ``seen`` guard is for a ruleset whose map loops
    back on itself; it stops rather than spinning.
    """

    seen = {condition_id}
    while condition_id in ladder and _has(character, condition_id):
        condition_id = ladder[condition_id]
        if condition_id in seen:
            break
        seen.add(condition_id)
    return condition_id


def _supersedes_a_sibling(
    condition_id: str, siblings: tuple[str, ...], game_data: GameData
) -> bool:
    """Whether applying *condition_id* would supersede something a sibling grants.

    "Grants" is the sibling itself *and* everything it bundles, since a bundle's
    members are what actually land on the character — the Dazed inside a Staggered is
    what a Stunned in the same rung has to replace.
    """

    catalog = game_data.condition_catalog()
    record = catalog.get(condition_id)
    if record is None or not record.supersedes:
        return False
    for sibling_id in siblings:
        if sibling_id == condition_id:
            continue
        sibling = catalog.get(sibling_id)
        if sibling is None:
            continue
        granted = {sibling_id, *expand_includes(sibling, catalog)}
        if granted & set(record.supersedes):
            return True
    return False


def resolve_damage_step(
    character: Character, step: DamageStep, game_data: GameData
) -> tuple[str, ...]:
    """What *step* would actually put on *character*: escalated, then ordered.

    Pure — it reads the character's current conditions but changes nothing, which is
    what lets a button say in its tooltip what clicking it will do.
    """

    ladder = dict(step.escalates)
    resolved = tuple(_escalate(character, cid, ladder) for cid in step.conditions)
    # A condition that supersedes something a sibling brings must be applied *after*
    # that sibling, or there is nothing there yet for it to replace. Stable, so
    # everything the graph has no opinion about keeps the book's printed order.
    return tuple(sorted(resolved, key=lambda cid: _supersedes_a_sibling(cid, resolved, game_data)))


def apply_damage_step(
    character: Character, step: DamageStep, game_data: GameData
) -> tuple[str, ...]:
    """Put *step* on *character*, returning the ids it applied.

    Each one goes through :func:`~mm_companion.core.rules.conditions.apply_condition`,
    so bundling, supersession and Hit's stacking are the same resolver's job here as
    they are for a condition picked by hand. The returned ids are what a caller
    replays onto a second copy of the same character (an open sheet), so both agree on
    what the escalation decided rather than each resolving it against its own state.
    """

    applied = resolve_damage_step(character, step, game_data)
    for condition_id in applied:
        apply_condition(character, condition_id, game_data)
    return applied


def damage_step_summary(character: Character, step: DamageStep, game_data: GameData) -> str:
    """What clicking *step* on *character* would do, as a sentence for a tooltip.

    Says so *before* the click, which is the whole point of resolving escalation
    without applying it: a rung captioned "Hit + Dazed" that will really land a
    Stunned should not be a surprise.
    """

    resolved = resolve_damage_step(character, step, game_data)
    # Back into the printed order for reading; the sort above is an apply-order
    # detail, and "Staggered + Stunned" is not how the book says it.
    order = {cid: n for n, cid in enumerate(step.conditions)}
    ordered = tuple(sorted(resolved, key=lambda cid: order.get(cid, len(order))))
    summary = _caption(ordered, game_data)
    if ordered != step.conditions:
        summary += " — escalated"
    elif step.note:
        summary += f" ({step.note})"
    return summary

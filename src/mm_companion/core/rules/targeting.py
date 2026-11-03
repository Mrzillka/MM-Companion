"""One check against several targets: who resisted, and what failing did to them.

The Impress action is the first thing built on this, and the shape is chosen so it is
not the last. An impression, an area Damage effect and an area Affliction all come
down to the same three steps:

1. **A DC.** Either fixed (an effect's ``10 + rank``) or *opposed* — another
   character's check total, which is what an impress check is. :func:`opposed_dc`
   turns a rolled total into one, folding in the natural-20 bonus.
2. **One resistance check per target** against that DC, each possibly adjusted for
   that target alone. The rolling is done elsewhere (by the session server, or by
   :func:`mm_companion.core.dice.resolve_group` off the air) so the numbers are never
   a client's claim; this module only reads the resulting degree.
3. **A rung of a ladder** for every target that failed — the same
   :class:`~mm_companion.core.data_loader.ResistanceOutcome` rungs an effect's
   ``resistanceOutcomes`` uses — resolved against the target's current conditions
   (escalation and apply order, :func:`~.damage.resolve_condition_ids`) and applied.

Nothing here names an action, a condition or a degree: the ladders are data.
"""

from __future__ import annotations

import copy
from collections.abc import Mapping
from dataclasses import dataclass

from ..character import Character
from ..data_loader import GameData, ResistanceOutcome
from .conditions import apply_condition, decrement_condition
from .damage import resolve_condition_ids

__all__ = [
    "SOURCE_PLACEHOLDER",
    "TargetResult",
    "failure_degrees",
    "outcome_rung",
    "opposed_dc",
    "rung_condition_ids",
    "rung_caption",
    "ladder_captions",
    "resolve_rung",
    "concrete_rung",
    "apply_rung",
    "success_removals",
]

#: What a rung's ``parameters`` template writes for whoever imposed it.
SOURCE_PLACEHOLDER = "{source}"


@dataclass(frozen=True)
class TargetResult:
    """One target's resistance check against the action's DC, as it was rolled.

    ``degree`` is the *target's* signed degree: positive means they resisted.
    """

    ref: str
    die: int
    total: int
    dc: int
    degree: int

    @property
    def resisted(self) -> bool:
        return self.degree > 0

    @property
    def failures(self) -> int:
        """Degrees of failure, or 0 for a resisted check."""
        return failure_degrees(self.degree)


def failure_degrees(degree: int | None) -> int:
    """How many degrees a resistance check failed by — 0 when it held (or was ungraded)."""

    if degree is None or degree > 0:
        return 0
    return max(1, -degree)


def outcome_rung(
    ladder: tuple[ResistanceOutcome, ...],
    degree: int | None,
    success: ResistanceOutcome | None = None,
    *,
    worst: bool = False,
) -> ResistanceOutcome | None:
    """The rung a resistance check of *degree* lands on, or ``None`` for none.

    The last rung covers every deeper failure, exactly as an effect's ladder does. A
    check that held lands on *success* (Damage's Hit), which is usually nothing.
    *worst* lands any failure on the last rung — a minion's "maximum degree of
    failure" (PDF p25), see :func:`~.minions.minion_rung`.
    """

    failures = failure_degrees(degree)
    if not failures:
        return success if degree is not None else None
    if not ladder:
        return None
    if worst:
        return ladder[-1]
    return ladder[min(failures, len(ladder)) - 1]


def opposed_dc(lead_total: int, lead_die: int, game_data: GameData, offset: int = 0) -> int:
    """The DC an opposed check's total sets for one target.

    A natural 20 on the opposing check is an added degree of success, which against a
    DC the target resists is the same five points an attack's critical adds to its
    effect — ``system.critical_effect_bonus``. *offset* is a per-target adjustment
    (a bonus or penalty that applies against this target only).
    """

    bonus = game_data.system.critical_effect_bonus if lead_die == 20 else 0
    return lead_total + bonus + offset


def rung_condition_ids(
    rung: ResistanceOutcome, config: Mapping[str, object] | None = None
) -> tuple[str, ...]:
    """The condition ids a rung names — read off *config* for a ``configKey`` rung.

    An Affliction's rungs name the field of the effect instance holding the ids the
    player chose; with no *config* such a rung names nothing.
    """

    if rung.config_key:
        value = (config or {}).get(rung.config_key, "")
        values = value if isinstance(value, (list, tuple)) else [value]
        return tuple(str(v).strip() for v in values if str(v).strip())
    return rung.conditions


def rung_caption(
    rung: ResistanceOutcome, game_data: GameData, config: Mapping[str, object] | None = None
) -> str:
    """One rung as words — "Hit + Dazed (Stunned instead if already Dazed)"."""

    catalog = game_data.condition_catalog()
    ids = rung_condition_ids(rung, config)
    if ids:
        names = []
        for cid in ids:
            name = catalog[cid].name if cid in catalog else cid
            scope = _scope(rung, cid, game_data, config)
            names.append(f"{scope} {name}" if scope else name)
    else:
        names = [rung.text] if rung.text else []
    text = " + ".join(n for n in names if n)
    if not text:
        return ""
    return f"{text} ({rung.note})" if rung.note else text


def ladder_captions(
    ladder: tuple[ResistanceOutcome, ...],
    game_data: GameData,
    config: Mapping[str, object] | None = None,
) -> tuple[str, ...]:
    """A whole ladder as words, one entry per degree — empty if it says nothing."""

    rungs = tuple(rung_caption(rung, game_data, config) for rung in ladder)
    return rungs if any(rungs) else ()


def _scope(
    rung: ResistanceOutcome,
    condition_id: str,
    game_data: GameData,
    config: Mapping[str, object] | None,
) -> str:
    """The trait *rung* scopes *condition_id* to (Weaken's), or ``""`` for none.

    Only a condition whose parameter *is* a trait takes one: Weaken's Impaired becomes
    "Strength Impaired", while a Dazed beside it on the same rung stays Dazed.
    """
    if not rung.scope_key:
        return ""
    scope = str((config or {}).get(rung.scope_key, "") or "").strip()
    if not scope:
        return ""
    record = game_data.condition_catalog().get(condition_id)
    parameter = record.parameter if record is not None else None
    return scope if parameter is not None and parameter.type == "trait_select" else ""


def _parameter(
    rung: ResistanceOutcome,
    condition_id: str,
    source: str,
    game_data: GameData,
    config: Mapping[str, object] | None = None,
) -> str | None:
    template = dict(rung.parameters).get(condition_id)
    if template is None:
        return _scope(rung, condition_id, game_data, config) or None
    value = template.replace(SOURCE_PLACEHOLDER, source).strip()
    return value or None


def concrete_rung(
    rung: ResistanceOutcome,
    game_data: GameData,
    config: Mapping[str, object] | None = None,
) -> ResistanceOutcome:
    """*rung* with everything an effect instance decides folded in.

    Its condition ids read off *config* (an Affliction's picks), and a Weaken's scope
    turned into each scoped condition's parameter — so the rung means the same thing
    with no config at all, which is what lets it travel on a spec. Escalation and the
    ``{source}`` templates stay as they are: those are decided when it is applied.
    """

    ids = rung_condition_ids(rung, config)
    templates = dict(rung.parameters)
    parameters = []
    for cid in ids:
        template = templates.get(cid)
        if template is None:
            template = _scope(rung, cid, game_data, config) or None
        if template is not None:
            parameters.append((cid, template))
    return ResistanceOutcome(conditions=ids, escalates=rung.escalates, parameters=tuple(parameters))


def resolve_rung(
    character: Character,
    rung: ResistanceOutcome,
    game_data: GameData,
    *,
    source: str = "",
    config: Mapping[str, object] | None = None,
) -> tuple[tuple[str, str | None], ...]:
    """What *rung* would put on *character*, as ``(condition id, parameter)`` pairs.

    Escalated and ordered against what the character already has, exactly as the
    damage ladder is (:func:`~.damage.resolve_condition_ids`), and each paired with
    the parameter the rung applies it with — ``{source}`` naming whoever imposed it.
    Pure, so a caller can say what it will do before it does it. A rung that is only
    words (a Coerced attitude) resolves to nothing.
    """

    ids = rung_condition_ids(rung, config)
    if not ids:
        return ()
    resolved = resolve_condition_ids(character, ids, rung.escalates, game_data)
    return tuple((cid, _parameter(rung, cid, source, game_data, config)) for cid in resolved)


def apply_rung(
    character: Character,
    rung: ResistanceOutcome,
    game_data: GameData,
    *,
    source: str = "",
    config: Mapping[str, object] | None = None,
) -> tuple[tuple[str, str | None], ...]:
    """Put *rung* on *character*, returning the ``(id, parameter)`` pairs applied.

    Through the ordinary :func:`~.conditions.apply_condition`, so bundling and
    supersession are the same resolver's job as for a condition picked by hand. The
    pairs are what a caller replays onto a second copy of the character (an open
    sheet, a player's own app) so both agree on what escalation decided.
    """

    applied = resolve_rung(character, rung, game_data, source=source, config=config)
    for condition_id, parameter in applied:
        apply_condition(character, condition_id, game_data, parameter=parameter)
    return applied


def success_removals(
    character: Character, removes: tuple[str, ...], degrees: int
) -> tuple[tuple[str, str | None], ...]:
    """What *degrees* of success take off *character*, as ``(condition id, parameter)``.

    One condition per degree, the first of *removes* the character carries each time —
    Healing's order, least severe first. A stacking condition loses one instance per
    degree (three Hits take three degrees), and an umbrella takes its members with it.
    Worked on a copy, so the caller can say what will happen and apply it where the
    character really lives (an NPC's model, a player's app).
    """

    scratch = Character()
    scratch.conditions = copy.deepcopy(list(character.conditions))
    removed: list[tuple[str, str | None]] = []
    for _ in range(max(0, degrees)):
        found = None
        for condition_id in removes:
            matches = [c for c in scratch.conditions if c.condition_id == condition_id]
            if matches:
                direct = [c for c in matches if c.provenance is None]
                found = direct[0] if direct else matches[0]
                break
        if found is None:
            break
        removed.append((found.condition_id, found.parameter))
        decrement_condition(scratch, found)
    return tuple(removed)

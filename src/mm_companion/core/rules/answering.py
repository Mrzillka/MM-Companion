"""Answering a roll for a creature: whose number goes in, and what failing did.

The GM's history offers every request card and every save chip to the creatures the GM
plays. Two questions follow, and both are rules questions this module answers:

1. **What the creature would roll** (:func:`answer_reading`): the number its own sheet
   puts in, the trait a best-of picks, and whether that trait can be used at all (a
   trained-only skill it has no training in still rolls — the GM is told, not stopped).
2. **What failing did to it** (:func:`answer_rung`). A save's spec carries its ladder as
   data (:attr:`~.rolls.RollSpec.ladder`, a :class:`~.rolls.SpecLadder`), made concrete
   by whoever built it: an Affliction's conditions as the wielder picked them, a
   Weaken's scope as a parameter, the damage ladder's escalation, an impression's
   ``{source}`` template. Applying it is :func:`~.targeting.resolve_rung`'s, as for any
   other ladder.

Which kind of ladder it is (:data:`CATEGORY_DAMAGE` and the rest — the ladder's own
``kind``) is what a GM's setting switches the automatic application on and off by.
Nothing here names a condition, an effect or an impression: the ladders are data.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from ..character import AppliedCondition, Character
from ..data_loader import GameData, ResistanceOutcome
from .derived import skill_usable
from .rolls import (
    KIND_SKILL,
    LADDER_AREA,
    LADDER_DAMAGE,
    LADDER_EFFECT,
    LADDER_IMPRESS,
    RollSpec,
    SpecLadder,
    localize_spec,
    trait_roll_for,
)
from .targeting import outcome_rung

__all__ = [
    "CATEGORY_DAMAGE",
    "CATEGORY_EFFECT",
    "CATEGORY_IMPRESS",
    "CATEGORY_AREA",
    "ANSWER_CATEGORIES",
    "AnswerReading",
    "answer_rung",
    "answer_reading",
    "condition_changes",
]

#: The GM's switches, one per kind of ladder (:attr:`SpecLadder.kind
#: <mm_companion.core.rules.rolls.SpecLadder.kind>`): a Toughness save against the
#: damage ladder; any other effect's (an Affliction's, a Weaken's); resisting an
#: impression; and a save against an area attack, whatever its effect.
CATEGORY_DAMAGE = LADDER_DAMAGE
CATEGORY_EFFECT = LADDER_EFFECT
CATEGORY_IMPRESS = LADDER_IMPRESS
CATEGORY_AREA = LADDER_AREA

#: Every category, in the order a settings page lists them.
ANSWER_CATEGORIES = (CATEGORY_DAMAGE, CATEGORY_EFFECT, CATEGORY_IMPRESS, CATEGORY_AREA)


@dataclass(frozen=True)
class AnswerReading:
    """What one creature would roll for a spec: the trait, the number, and a caveat."""

    label: str
    modifier: int
    #: ``False`` for a trained-only skill the creature has no training in.
    usable: bool = True


def answer_rung(ladder: SpecLadder, degree: int | None) -> ResistanceOutcome | None:
    """The rung a roll of *degree* lands on (a made one: the ladder's success rung)."""

    return outcome_rung(ladder.rungs, degree, ladder.success)


def answer_reading(spec: RollSpec, char: Character, game_data: GameData) -> AnswerReading | None:
    """What *char* would roll for *spec*, or ``None`` when its sheet has no such trait.

    The modifier is the whole of it — the creature's own number plus whatever the spec
    already carries (a GM's cover, a natural 1's bonus, Evasion on an area's Dodge) —
    which is what the roll will be made at before the roller's own adjustment.
    """

    own = trait_roll_for(spec, char, game_data)
    if own is None:
        return None
    usable = True
    if own.kind == KIND_SKILL:
        usable = skill_usable(char, game_data, own.trait_key)
    return AnswerReading(
        label=own.label,
        modifier=localize_spec(spec, char, game_data).modifier,
        usable=usable,
    )


def condition_changes(
    before: list[AppliedCondition], after: list[AppliedCondition]
) -> tuple[list[tuple[str, str | None]], list[tuple[str, str | None]]]:
    """``(added, removed)`` between two condition lists, as ``(id, parameter)`` pairs.

    Only conditions applied *directly* count — a bundle's members come and go with it —
    and a stacking condition counts once per instance, so three Hits are three pairs.
    What undoing a rung needs: take the added back off, put the removed back on.
    """

    def tally(conditions: list[AppliedCondition]) -> Counter:
        counts: Counter = Counter()
        for applied in conditions:
            if applied.provenance is None:
                counts[(applied.condition_id, applied.parameter)] += max(1, applied.count)
        return counts

    was, now = tally(before), tally(after)
    return list((now - was).elements()), list((was - now).elements())

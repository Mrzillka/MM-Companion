"""Minions: the minor characters the rules make easier to defeat (PDF p25).

Some NPCs are **minions** — a thug, a bystander, a horde of demons. A GM marks one
with :attr:`~..character.Character.minion`, and these rules then apply to it:

* **No added success.** A minion's natural 20 is no added success and no critical
  against a non-minion. Its natural 1 still costs it a degree.
* **Routine opposition.** A non-minion may make attack and interaction checks
  against a minion as routine checks (take 10: :data:`~..dice.ROUTINE_DIE`).
* **Maximum degree of failure.** A minion that fails a resistance check takes the
  worst degree of it, whatever it rolled — a failed Damage save is Incapacitated.
* **Criticals.** A critical hit on a minion lands the effect's highest degree with no
  resistance check at all (PDF p197).
* **Impressing minions** is a routine check at ``+impress_bonus`` (PDF p201), and a failed
  resistance takes the most severe of its conditions.

Which of those a table plays by is the GM's choice, made **per session** on the GM
window's Session settings page and published on the session's shared state. That is
:class:`MinionRules`: a set of switches whose defaults are the book. The two numbers
the rules need (the Impress bonus, and the condition a minion stops at) are data —
``system.json``'s ``minions`` block, :class:`~..data_loader.MinionSystem`.

The book has no "stop at Incapacitated" rule of its own; it says a minion "can be
killed simply with a successful attack and a failed resistance check, if the attacker
wishes" (PDF p200). The table here chose to stop a minion at the ceiling and leave the
killing to the GM's hand, so :func:`capped_escalation` cuts every rung of an escalation
chain that would carry a minion past it.

Sits on ``targeting`` (whose :func:`~.targeting.outcome_rung` it wraps) and ``rolls``
(whose save specs it reads), and under
``answering``, ``impress`` and ``area_attack``, which ask it what a failure does to a
minion.
"""

from __future__ import annotations

from dataclasses import dataclass, fields, replace
from typing import TypeVar

from ..character import Character
from ..data_loader import GameData, ResistanceOutcome
from ..dice import ROUTINE_DIE
from .defenses import damage_negated, toughness_defense
from .rolls import LADDER_DAMAGE, RollSpec, SpecLadder
from .targeting import outcome_rung

__all__ = [
    "BOOK_MINION_RULES",
    "MINION_RULE_KEYS",
    "MinionRules",
    "capped_escalation",
    "critical_answer",
    "critical_rung",
    "is_minion",
    "minion_added_success",
    "minion_rung",
    "minion_save_spec",
    "minion_step",
    "routine_impress_dc",
    "routine_total",
]


@dataclass(frozen=True)
class MinionRules:
    """Which of the minion rules this session plays by. Every switch on is the book.

    Published by the GM on the session's shared state (see
    ``ui.action_ui.publish_minion_rules``) and read back by the GM's own window; a
    session that never said reads as :data:`BOOK_MINION_RULES`. An unknown key is
    ignored and a missing one takes its default, so a newer app's extra switch does
    not break an older one's read.
    """

    #: A minion's natural 20 is no added success (and no critical).
    no_added_success: bool = True
    #: A minion that fails a resistance check takes the worst degree of it.
    worst_degree: bool = True
    #: A minion's failed saves never escalate past ``system.minions.ceiling``.
    stop_at_incapacitated: bool = True
    #: A critical hit on a minion lands the worst degree with no save.
    crit_auto_fails: bool = True
    #: An attack on a minion may be judged on its routine result (take 10).
    routine_attacks: bool = True
    #: Impress against minions is a routine check at ``system.minions.impress_bonus``.
    routine_impress: bool = True
    #: Players see which creatures on the Scene are minions.
    show_players: bool = True

    def to_dict(self) -> dict[str, bool]:
        return {spec.name: bool(getattr(self, spec.name)) for spec in fields(self)}

    @classmethod
    def from_dict(cls, raw: object) -> MinionRules:
        if not isinstance(raw, dict):
            return cls()
        values = {
            spec.name: raw[spec.name]
            for spec in fields(cls)
            if isinstance(raw.get(spec.name), bool)
        }
        return cls(**values)


#: The book's rules: every switch on.
BOOK_MINION_RULES = MinionRules()

#: The switches, in the order the Session settings page lists them.
MINION_RULE_KEYS: tuple[str, ...] = tuple(spec.name for spec in fields(MinionRules))


def is_minion(character: Character | None) -> bool:
    """Whether *character* is a minion. ``None`` (a creature with no sheet) is not."""

    return bool(character is not None and character.minion)


def minion_added_success(minion: bool, rules: MinionRules = BOOK_MINION_RULES) -> bool:
    """Whether a roll made by a creature that is (or is not) a *minion* may crit.

    False only for a minion at a table playing the "no added success" rule. The rule
    reads "against non-minions", but a roll does not know its target when it is
    thrown, and minions fight heroes; a minion's 20 against another minion is the one
    case this rounds the wrong way.
    """

    return not (minion and rules.no_added_success)


def routine_total(modifier: int) -> int:
    """A routine check's result: :data:`~..dice.ROUTINE_DIE` plus the modifier (PDF p17)."""

    return ROUTINE_DIE + modifier


def routine_impress_dc(check_modifier: int, game_data: GameData, offset: int = 0) -> int:
    """The DC a minion resists a hero's Impress at: a routine check plus the bonus.

    *check_modifier* is the impresser's whole check modifier (skill, the GM's
    adjustment, the situational modifiers, the group penalty); *offset* is the
    adjustment against this target alone. A routine check makes no natural 20, so
    no critical bonus is ever added.
    """

    return routine_total(check_modifier) + game_data.system.minions.impress_bonus + offset


_Rung = TypeVar("_Rung")


def capped_escalation(
    escalates: tuple[tuple[str, str], ...], ceiling: str
) -> tuple[tuple[str, str], ...]:
    """*escalates* without the pairs that would carry a creature past *ceiling*.

    A pair is cut when what it escalates *from* is the ceiling or anything the chain
    reaches from it — so ``incapacitated -> dying`` and ``dying -> dead`` both go,
    while ``dazed -> stunned`` (below the ceiling) stays.
    """

    chain = dict(escalates)
    beyond = {ceiling}
    current = ceiling
    while current in chain and chain[current] not in beyond:
        current = chain[current]
        beyond.add(current)
    return tuple((before, after) for before, after in escalates if before not in beyond)


def _capped(rung: _Rung, game_data: GameData) -> _Rung:
    """*rung* (or a damage step) with its escalation cut at the ceiling.

    Its note goes with what was cut: on Damage's last rung it is "further failed checks
    escalate to Dying, then Dead", which is exactly what no longer happens.
    """

    escalates = getattr(rung, "escalates", ())
    capped = capped_escalation(escalates, game_data.system.minions.ceiling)
    if capped == escalates:
        return rung
    return replace(rung, escalates=capped, note="")


def minion_rung(
    ladder: tuple[ResistanceOutcome, ...],
    degree: int | None,
    success: ResistanceOutcome | None,
    game_data: GameData,
    *,
    minion: bool,
    rules: MinionRules = BOOK_MINION_RULES,
) -> ResistanceOutcome | None:
    """The rung a resistance check of *degree* lands a creature on, minion or not.

    :func:`~.targeting.outcome_rung` for anyone but a minion. A minion's failure is
    the worst rung (``worst_degree``) and escalates no further than the ceiling
    (``stop_at_incapacitated``); its made save is the same as anyone's.
    """

    worst = minion and rules.worst_degree
    rung = outcome_rung(ladder, degree, success, worst=worst)
    if rung is None or not minion or not rules.stop_at_incapacitated:
        return rung
    if success is not None and rung is success:
        return rung
    return _capped(rung, game_data)


def critical_rung(
    ladder: tuple[ResistanceOutcome, ...],
    game_data: GameData,
    rules: MinionRules = BOOK_MINION_RULES,
) -> ResistanceOutcome | None:
    """What a critical hit on a minion lands: the effect's highest degree, no save.

    "A critical hit against a minion automatically imposes the highest degree of the
    attack's effect on the minion with no resistance check" (PDF p197) — the last
    rung, stopped at the ceiling like any other failure of a minion's. ``None`` when
    the session does not play that rule, or the ladder is empty.
    """

    if not rules.crit_auto_fails or not ladder:
        return None
    rung = ladder[-1]
    return _capped(rung, game_data) if rules.stop_at_incapacitated else rung


def critical_answer(
    ladder: SpecLadder,
    char: Character | None,
    game_data: GameData,
    rules: MinionRules = BOOK_MINION_RULES,
) -> ResistanceOutcome | None:
    """What a critical hit on the minion *char* lands, measured against its Toughness.

    :func:`critical_rung`, unless the hit is Damage the minion's Impervious or
    Impenetrable Toughness shrugs off — at the critical's raised rank, since "a
    critical hit increases the rank of the attack's effect by +5" (PDF p197). ``None``
    then, and the minion makes its save like anyone else (where Hardened may still
    turn it). A ladder whose rank is unknown (0) waives the test, as everywhere.
    """

    if ladder.kind == LADDER_DAMAGE and char is not None and ladder.rank:
        rank = ladder.rank + game_data.system.critical_effect_bonus
        defense = toughness_defense(char, game_data)
        if damage_negated(defense, rank=rank, penetrating=ladder.penetrating):
            return None
    return critical_rung(ladder.rungs, game_data, rules)


def minion_save_spec(spec: RollSpec, rules: MinionRules = BOOK_MINION_RULES) -> RollSpec:
    """*spec* — a save a minion is about to roll — as every seat's card should read it.

    A minion's failure is the worst rung, so its outcome ladder is cut down to that
    one rung: :func:`~.rolls.resistance_outcome` then says "Incapacitated" for any
    failure on every screen, which is what the GM's table applies. Unchanged when the
    session does not play the worst-degree rule.
    """

    if not rules.worst_degree or not spec.outcomes:
        return spec
    return replace(spec, outcomes=(spec.outcomes[-1],))


def minion_step(step: _Rung, game_data: GameData, rules: MinionRules = BOOK_MINION_RULES) -> _Rung:
    """A damage step (or any rung) as it lands on a minion: no escalation past the ceiling.

    The GM's quick-damage buttons pick the step by hand, so only the escalation is
    the minion's to change here; which step is shown is the card's business.
    """

    if not rules.stop_at_incapacitated:
        return step
    return _capped(step, game_data)

"""What rolling a trait looks like: the label, the modifier, and any DC it carries.

The sheet already derives every number a player would dial into the dice roller by
hand — :func:`~.derived.skill_total`, :func:`~.derived.effective_ability`,
:func:`~.derived.resistance_total`, :func:`~.derived.initiative_modifier`, a power's
attack bonus and save DC. This module turns each of those into a :class:`RollSpec`,
the small record the UI hands to the dice roller when a stat line is double-clicked
or a power's 🎲 is pressed.

It exists so **no widget computes a roll modifier**, exactly as no widget computes a
skill total: the arithmetic (including the condition overlay, so a rolled number
always matches the number on the sheet) is here, pure and testable without Qt.

Two things a spec deliberately does *not* know:

* the **DC of an attack** — that is the target's Defense, which this character's
  sheet cannot see, so an attack spec leaves ``dc`` at ``None`` and the roller's own
  DC box supplies it;
* the **target's resistance** — so a save spec's ``modifier`` is 0 and the roller's
  Bonus slider supplies it.

Both gaps are filled by the same sliders that add situational modifiers to every
other roll, so there is one way to say "and +2 for cover".
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace

from ..character import Character
from ..data_loader import Effect, GameData, ResistanceOutcome
from ..powers import Power, PowerEffectInstance
from .area import area_dodge_bonus
from .conditions import (
    condition_check_penalty,
    condition_scope_penalty,
    resistance_condition_effect,
)
from .defenses import localized_damage_save, penetrating_ranks
from .derived import (
    effective_ability,
    initiative_ability,
    initiative_modifier,
    resistance_total,
    skill_modifiers,
    skill_total,
)
from .extra_effort import effect_use_costs
from .improvised import ImprovisedPlan
from .powers_cost import effect_action_at_most, effect_resistance_dc_base
from .powers_terms import (
    effect_attack_skill_bonus,
    effect_live_rank,
    effect_makes_attack,
    effect_opposed_check,
    effect_roll_numbers,
    effect_stat_rows,
    effective_effect_stats,
    linked_attacks,
    required_check_rolls,
)
from .runtime import effect_counts_uses
from .targeting import concrete_rung, opposed_dc, rung_caption

#: The slowest action an effect may take and still be readied to counter in action time:
#: "You must be able to use the readied effect as a standard action or less" (p107).
COUNTER_ACTION_LIMIT = "Standard"

#: Spec ``kind`` values. The UI reads these only for grouping/telemetry — the roll
#: itself is fully described by the label, modifier and DC.
KIND_ABILITY = "ability"
KIND_RESISTANCE = "resistance"
KIND_SKILL = "skill"
KIND_INITIATIVE = "initiative"
KIND_POWER_CHECK = "power-check"
KIND_POWER_SAVE = "power-save"
#: An opposed effect check — ``d20 + effect rank`` against another effect's own check
#: (p107). Countering is the usual reason for one; Nullify's second roll *is* one.
KIND_EFFECT_CHECK = "effect-check"
#: An Impress action's interaction skill check (see :mod:`.impress`).
KIND_IMPRESS = "impress"
#: A roll of whichever of several traits is highest on the roller's own sheet. Its
#: :attr:`~RollSpec.trait_key` lists the candidates (:func:`best_of_key`); resisting
#: an impression — the same skill, Insight or Will — is the first user.
KIND_BEST_OF = "best-of"
#: :data:`KIND_BEST_OF`'s opposite: whichever of the candidates is *lowest* — an effect
#: resisted by the worst of two (Alternate Resistance's worst-of choice).
KIND_WORST_OF = "worst-of"
#: A power used as an **area**: not a die at all, but the footer line that opens the
#: area window. Its :attr:`~RollSpec.trait_key` is the power's id (see
#: :mod:`.area_attack`).
KIND_AREA = "area"
#: The Dodge check a creature in an area makes — a resistance roll whose follow-up is
#: the effect's own save at full or halved rank. Localized like a resistance, plus the
#: area's Dodge bonus (Evasion).
KIND_AREA_DODGE = "area-dodge"

#: What kind of ladder a save's :attr:`~RollSpec.ladder` is — which of the GM's
#: switches governs applying it (see :mod:`.answering`). Damage is the ruleset's
#: damage effect; any other effect is ``effect``; an area's save is ``area`` whatever
#: its effect.
LADDER_DAMAGE = "damage"
LADDER_EFFECT = "effect"
LADDER_IMPRESS = "impress"
LADDER_AREA = "area"

#: Separates the candidates in a :data:`KIND_BEST_OF` trait key, and a candidate's
#: kind from its key.
BEST_OF_SEPARATOR = "|"
BEST_OF_KIND_SEPARATOR = ":"


def _as_int(value: object) -> int | None:
    """*value* as an int, or ``None`` if it is missing or isn't one.

    For :meth:`RollSpec.from_dict`, which parses another player's data and must not
    raise on a field that arrived as prose.
    """

    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _rung_to_dict(rung: ResistanceOutcome) -> dict:
    raw: dict = {"ids": list(rung.conditions)}
    if rung.escalates:
        raw["escalates"] = [list(pair) for pair in rung.escalates]
    if rung.parameters:
        raw["params"] = [list(pair) for pair in rung.parameters]
    return raw


def _pairs(raw: object) -> tuple[tuple[str, str], ...]:
    if not isinstance(raw, list):
        return ()
    return tuple(
        (str(pair[0]), str(pair[1])) for pair in raw if isinstance(pair, list) and len(pair) == 2
    )


def _rung_from_dict(raw: object) -> ResistanceOutcome:
    if not isinstance(raw, dict):
        return ResistanceOutcome()
    ids = raw.get("ids")
    return ResistanceOutcome(
        conditions=(
            tuple(str(i) for i in ids if isinstance(i, str)) if isinstance(ids, list) else ()
        ),
        escalates=_pairs(raw.get("escalates")),
        parameters=_pairs(raw.get("params")),
    )


@dataclass(frozen=True)
class SpecLadder:
    """What failing a save does, as data a GM's table can apply — not only words.

    :attr:`RollSpec.outcomes` is the ladder rendered for reading; this is the same
    ladder made *concrete*: each rung's condition ids (an Affliction's read off the
    wielder's config, a Weaken's scope folded into its parameters), its escalation,
    and its parameter templates (``{source}`` left for whoever applies it). It travels
    on the spec so the GM can put a save an NPC failed straight onto it, whoever
    built the spec (see :mod:`.answering`). ``kind`` is one of the ``LADDER_*`` values.

    ``rank`` and ``penetrating`` are the attack's effect rank and how many of its ranks
    are Penetrating — what the *target's* Impervious or Hardened Toughness is measured
    against (:mod:`.defenses`). Both 0 when unknown, which waives the rank tests and
    leaves a ladder from an older build reading as it always did.
    """

    kind: str
    rungs: tuple[ResistanceOutcome, ...] = ()
    success: ResistanceOutcome | None = None
    rank: int = 0
    penetrating: int = 0

    def to_dict(self) -> dict:
        raw: dict = {"kind": self.kind, "rungs": [_rung_to_dict(r) for r in self.rungs]}
        if self.success is not None:
            raw["success"] = _rung_to_dict(self.success)
        if self.rank:
            raw["rank"] = self.rank
        if self.penetrating:
            raw["penetrating"] = self.penetrating
        return raw

    @classmethod
    def from_dict(cls, raw: object) -> SpecLadder | None:
        """Rebuild a ladder from :meth:`to_dict`, or ``None`` for anything unusable."""
        if not isinstance(raw, dict):
            return None
        rungs = raw.get("rungs")
        if not isinstance(rungs, list):
            return None
        success = raw.get("success")
        return cls(
            kind=str(raw.get("kind", "")),
            rungs=tuple(_rung_from_dict(r) for r in rungs),
            success=_rung_from_dict(success) if isinstance(success, dict) else None,
            rank=_nonnegative(raw.get("rank")),
            penetrating=_nonnegative(raw.get("penetrating")),
        )


def _nonnegative(value: object) -> int:
    """A wire integer that may be missing or junk, as a count (0 for either)."""

    if isinstance(value, int) and not isinstance(value, bool):
        return max(0, value)
    return 0


@dataclass(frozen=True)
class RollSpec:
    """One named d20 roll, ready to hand to the dice roller.

    ``modifier`` is the roller's own bonus and ``dc`` a difficulty the spec already
    knows (a power's save DC); ``dc`` of ``None`` means "whatever the roller's DC box
    says", which is the normal case for a trait check.

    ``follow_up`` is the roll this one *provokes* — an attack that lands makes its
    target save, so the attack spec carries the save spec and whoever is resisting
    gets it as a chip on the attack's history card. ``outcomes`` is the
    degree-of-failure ladder for a save and ``success_outcome`` what making it still
    costs (see :func:`resistance_outcome`), which is what turns a rolled number into
    "Incapacitated!" or "Hit".

    Two fields exist because some rolls are made by **the other side of the
    table**: ``rolled_by_target`` marks a spec the wielder never rolls (so a power
    card shows the line without a 🎲 — the follow-up chip is where it belongs),
    and ``trait_key`` names the trait it is, so whoever's sheet the chip is clicked
    on can fill in their own number (:func:`localize_spec`). A save fills in a
    resistance; a *requested* roll — one player asking the table for an Awareness
    check — fills in whichever of the four trait kinds it names, which is why the
    key is read together with ``kind`` and not on its own.

    A spec is plain, JSON-serializable data (:meth:`to_dict`) because it **travels**:
    it rides along with the roll so every screen at the table renders the same chain
    (see ``docs/mm-session-architecture.md``).
    """

    label: str
    modifier: int = 0
    dc: int | None = None
    kind: str = ""
    hint: str = ""
    follow_up: RollSpec | None = None
    outcomes: tuple[str, ...] = ()
    success_outcome: str = ""
    rolled_by_target: bool = False
    trait_key: str = ""
    #: On a follow-up: its DC is the provoking roll's *total* rather than a number
    #: known in advance — an opposed check, such as resisting an impression. Filled in
    #: by :func:`follow_up_for_result` once that total exists.
    dc_from_total: bool = False
    #: On a follow-up: the DC to use instead when the provoking roll *succeeded* — an
    #: area's save at half rank after a made Dodge check. ``None`` for every other spec.
    dc_if_resisted: int | None = None
    #: On a follow-up: words added to its label when :attr:`dc_if_resisted` was chosen
    #: ("no effect with Evasion 2").
    note_if_resisted: str = ""
    #: Offer :attr:`follow_up` whatever the degree. A missed attack provokes nothing,
    #: but a *failed* Dodge against an area is exactly when the save is due.
    follow_up_always: bool = False
    #: Who this roll concerns, by display name — the creatures an area caught, or the
    #: one row of a group table this line belongs to.
    targets: tuple[str, ...] = ()
    #: The action this roll belongs to (an area attack's id), so the GM's history can
    #: offer to resolve it from the card.
    action_id: str = ""
    #: What a group of rolls is called, as one table card heads it.
    title: str = ""
    #: The creature a seat rolled this *as* — an NPC the GM answered a request with.
    #: The history heads the card with it ("Thug (GM)") rather than with the seat.
    actor: str = ""
    #: The ladder :attr:`outcomes` renders, as data — what the GM's table applies when
    #: an NPC fails this save. ``None`` for a roll that is no save.
    ladder: SpecLadder | None = None
    #: The condition a **recovery check** sheds when it succeeds — Dazed's DC 10
    #: Stamina check, say — with the subject it was applied with, if any. Whoever
    #: owns the creature the roll was made for takes it off when the roll comes back
    #: good enough (:func:`~.recovery.recovery_succeeded`); on the wire so a check the
    #: GM *asks* a player for still knows what it is for when that player rolls it.
    recovers: str = ""
    recovers_parameter: str = ""
    #: What *using* the effect costs its wielder, as condition ids put on the roller's
    #: own sheet when this roll is thrown — Tiring's rung of fatigue, Distracting's
    #: Vulnerable (:func:`~.extra_effort.effect_use_costs`). **Local only**, and never
    #: written by :meth:`to_dict`: a roll thrown again off the history is a reroll of a
    #: use already paid for, not a second use.
    costs: tuple[str, ...] = ()
    #: Which effects this roll *uses*, as ``(power id, index into its effects)`` pairs,
    #: for the ones that count their uses (Short-Term, Fades): the Powers block adds one
    #: to each once the die is really thrown. Local only, for :attr:`costs`' reason.
    spends: tuple[tuple[str, int], ...] = ()
    #: More rolls this one provokes **alongside** :attr:`follow_up`, offered on the same
    #: terms (a hit, a critical, a graze). A Linked power's one attack check lands every
    #: effect in it at once, and each is resisted separately (p155); a Nullify's hit
    #: calls for the opposed check as well as any save.
    also: tuple[RollSpec, ...] = ()

    def follow_ups(self) -> tuple[RollSpec, ...]:
        """Every roll this one provokes: :attr:`follow_up`, then :attr:`also`."""

        return (self.follow_up, *self.also) if self.follow_up is not None else self.also

    def with_follow_up(self, follow_up: RollSpec | None) -> RollSpec:
        """A copy of this spec that provokes *follow_up* when it succeeds."""

        return replace(self, follow_up=follow_up)

    def to_dict(self) -> dict:
        """This spec as plain JSON data, nested ``follow_up`` and all.

        Only the non-default fields are written, so the common case (a trait check:
        a label and a modifier) is a two-key dict on the wire rather than ten.
        """

        raw: dict = {"label": self.label}
        if self.modifier:
            raw["modifier"] = self.modifier
        if self.dc is not None:
            raw["dc"] = self.dc
        if self.kind:
            raw["kind"] = self.kind
        if self.hint:
            raw["hint"] = self.hint
        if self.outcomes:
            raw["outcomes"] = list(self.outcomes)
        if self.success_outcome:
            raw["success_outcome"] = self.success_outcome
        if self.rolled_by_target:
            raw["rolled_by_target"] = True
        if self.trait_key:
            raw["trait_key"] = self.trait_key
        if self.dc_from_total:
            raw["dc_from_total"] = True
        if self.dc_if_resisted is not None:
            raw["dc_if_resisted"] = self.dc_if_resisted
        if self.note_if_resisted:
            raw["note_if_resisted"] = self.note_if_resisted
        if self.follow_up_always:
            raw["follow_up_always"] = True
        if self.targets:
            raw["targets"] = list(self.targets)
        if self.action_id:
            raw["action_id"] = self.action_id
        if self.title:
            raw["title"] = self.title
        if self.actor:
            raw["actor"] = self.actor
        if self.ladder is not None:
            raw["ladder"] = self.ladder.to_dict()
        if self.recovers:
            raw["recovers"] = self.recovers
        if self.recovers_parameter:
            raw["recovers_parameter"] = self.recovers_parameter
        if self.follow_up is not None:
            raw["follow_up"] = self.follow_up.to_dict()
        if self.also:
            raw["also"] = [spec.to_dict() for spec in self.also]
        return raw

    @classmethod
    def from_dict(cls, raw: object) -> RollSpec | None:
        """Rebuild a spec from :meth:`to_dict`, or ``None`` for anything unusable.

        Defensive on purpose: this parses data that arrived from another player. It
        never raises — a malformed spec costs the chain on one card, not the history
        panel. (The *server* has already run it through
        :func:`~mm_companion.core.session.protocol.sanitize_spec`, which is what caps
        its size; this only has to survive what gets through.)
        """

        if not isinstance(raw, dict):
            return None
        label = str(raw.get("label", ""))
        if not label:
            return None
        outcomes = raw.get("outcomes")
        targets = raw.get("targets")
        also = raw.get("also")
        return cls(
            label=label,
            modifier=_as_int(raw.get("modifier")) or 0,
            dc=_as_int(raw.get("dc")),
            kind=str(raw.get("kind", "")),
            hint=str(raw.get("hint", "")),
            follow_up=cls.from_dict(raw.get("follow_up")),
            outcomes=tuple(str(o) for o in outcomes) if isinstance(outcomes, list) else (),
            success_outcome=str(raw.get("success_outcome", "")),
            rolled_by_target=bool(raw.get("rolled_by_target", False)),
            trait_key=str(raw.get("trait_key", "")),
            dc_from_total=bool(raw.get("dc_from_total", False)),
            dc_if_resisted=_as_int(raw.get("dc_if_resisted")),
            note_if_resisted=str(raw.get("note_if_resisted", "")),
            follow_up_always=bool(raw.get("follow_up_always", False)),
            targets=(
                tuple(str(t) for t in targets if isinstance(t, str))
                if isinstance(targets, list)
                else ()
            ),
            action_id=str(raw.get("action_id", "")),
            title=str(raw.get("title", "")),
            actor=str(raw.get("actor", "")),
            ladder=SpecLadder.from_dict(raw.get("ladder")),
            recovers=str(raw.get("recovers", "")),
            recovers_parameter=str(raw.get("recovers_parameter", "")),
            also=(
                tuple(s for s in (cls.from_dict(a) for a in also) if s is not None)
                if isinstance(also, list)
                else ()
            ),
        )


# -- trait rolls -------------------------------------------------------------


def ability_roll(char: Character, game_data: GameData, key: str) -> RollSpec:
    """Roll an ability check: its effective value, with any condition penalty applied."""

    ability = next((a for a in game_data.abilities if a.key == key), None)
    name = ability.name if ability else key
    value = effective_ability(char, game_data, key)
    effect = condition_scope_penalty(char, game_data, {key, name})
    return RollSpec(
        label=name,
        modifier=effect.apply(value) if effect.active else value,
        kind=KIND_ABILITY,
        hint=effect.tooltip,
    )


def resistance_roll(char: Character, game_data: GameData, key: str) -> RollSpec:
    """Roll a resistance check: the resistance total, with any condition overlay applied."""

    resistance = next((r for r in game_data.resistances if r.key == key), None)
    name = resistance.name if resistance else key
    value = resistance_total(char, game_data, key)
    effect = resistance_condition_effect(char, game_data, key)
    return RollSpec(
        label=name,
        modifier=effect.apply(value) if effect.active else value,
        kind=KIND_RESISTANCE,
        hint=effect.tooltip,
    )


def skill_row_label(row_id: str) -> str:
    """A readable name for a skill *row id*.

    Row ids are ``"Stealth"``, ``"Expertise: Law"`` (a focus) or
    ``"Stealth::spec::Urban"`` (a specialized pool); only the last needs unpacking.
    """

    base, sep, spec = row_id.partition("::spec::")
    return f"{base} ({spec})" if sep else row_id


def skill_roll(char: Character, game_data: GameData, row_id: str, *, label: str = "") -> RollSpec:
    """Roll a skill check: the row's total, with any condition penalty applied.

    *label* overrides the derived name so a section can pass the exact text it
    rendered in the table.
    """

    mods = skill_modifiers(char, game_data, row_id)
    total = skill_total(char, game_data, row_id)
    effect = mods.condition
    return RollSpec(
        label=label or skill_row_label(row_id),
        modifier=effect.apply(total) if effect.active else total,
        kind=KIND_SKILL,
        hint=effect.tooltip,
    )


def initiative_roll(char: Character, game_data: GameData) -> RollSpec:
    """Roll initiative: the initiative modifier plus any all-checks condition penalty."""

    penalty = condition_check_penalty(char, game_data)
    ability = initiative_ability(char, game_data)
    return RollSpec(
        label="Initiative",
        modifier=initiative_modifier(char, game_data) + penalty,
        kind=KIND_INITIATIVE,
        hint=f"Initiative ({ability})",
    )


# -- power rolls -------------------------------------------------------------


def _rung_text(rung: ResistanceOutcome, effect: PowerEffectInstance, game_data: GameData) -> str:
    """One ladder rung rendered for this effect instance (see :class:`ResistanceOutcome`)."""

    return rung_caption(rung, game_data, effect.config)


def effect_outcome_ladder(
    effect: PowerEffectInstance, game_data: GameData, base_effect: Effect | None = None
) -> tuple[str, ...]:
    """This effect's degree-of-failure ladder, rendered — one entry per degree.

    Empty when the base effect ships no ``resistanceOutcomes``, or when the player
    has not yet chosen the conditions a config-driven ladder reads.
    """

    if base_effect is None:
        base_effect = next((e for e in game_data.effects if e.id == effect.effect_id), None)
    if base_effect is None or not base_effect.resistance_outcomes:
        return ()
    rungs = tuple(_rung_text(rung, effect, game_data) for rung in base_effect.resistance_outcomes)
    return rungs if any(rungs) else ()


def effect_spec_ladder(
    effect: PowerEffectInstance, game_data: GameData, base_effect: Effect | None = None
) -> SpecLadder | None:
    """This effect's ladder as data for its save's spec — ``None`` if it has none.

    Damage's is :data:`LADDER_DAMAGE` (the ruleset's damage effect), every other
    effect's :data:`LADDER_EFFECT`.
    """

    if base_effect is None:
        base_effect = next((e for e in game_data.effects if e.id == effect.effect_id), None)
    if base_effect is None or not base_effect.resistance_outcomes:
        return None
    kind = LADDER_DAMAGE if base_effect.id == game_data.system.damage_effect else LADDER_EFFECT
    success = base_effect.resistance_success
    return SpecLadder(
        kind,
        tuple(
            concrete_rung(rung, game_data, effect.config)
            for rung in base_effect.resistance_outcomes
        ),
        None if success is None else concrete_rung(success, game_data, effect.config),
    )


def effect_success_outcome(
    effect: PowerEffectInstance, game_data: GameData, base_effect: Effect | None = None
) -> str:
    """What *making* this effect's resistance check still costs the target.

    Usually nothing, and then this is ``""``. Damage is the exception the rules make
    a point of: a made Toughness save still leaves a Hit unless the target's
    Toughness is Hardened, Impervious or Impenetrable — a caveat that rides along in
    the rung's note, since this app can only see the *attacker's* sheet.
    """

    if base_effect is None:
        base_effect = next((e for e in game_data.effects if e.id == effect.effect_id), None)
    if base_effect is None or base_effect.resistance_success is None:
        return ""
    return _rung_text(base_effect.resistance_success, effect, game_data)


def resistance_outcome(spec: RollSpec, degree: int | None) -> str:
    """What the save on *spec* did to the target, or ``""`` when nothing did.

    A failure reads off the ladder, whose last rung covers every deeper failure — so
    a four-degree rout on a three-rung ladder still answers. A *success* reads
    :attr:`~RollSpec.success_outcome`, which is how a made Toughness save still
    reports its Hit instead of going silent on the commonest result of an attack. An
    ungraded roll (``degree`` of ``None``, meaning no DC was set) returns ``""``:
    nothing was decided, so there is nothing to state.

    Takes the bare degree rather than a :class:`~mm_companion.core.dice.CheckResult`
    because a roll that came back from a session is a plain dict off the wire, and
    every screen must read the same ladder from the same numbers.
    """

    if degree is None:
        return ""
    if degree > 0:
        return spec.success_outcome
    if not spec.outcomes:
        return ""
    degrees = max(1, abs(degree))
    return spec.outcomes[min(degrees, len(spec.outcomes)) - 1]


def outcome_is_failure(degree: int | None) -> bool:
    """Whether an outcome line describes a failed check (which tints it as harm)."""

    return degree is not None and degree <= 0


def follow_up_offered(spec: RollSpec, degree: int | None) -> bool:
    """Whether *spec*'s follow-up roll is now due.

    A save is only forced by an attack that landed — but an attack rolled with no
    DC set (nobody typed the target's Defense) is *ungraded*, not a miss, and the
    player is the one judging it. So an unknown outcome offers the follow-up too
    and lets them decide; only a roll that visibly failed withholds it.
    """

    if spec.follow_up is None and not spec.also:
        return False
    return spec.follow_up_always or degree is None or degree > 0


def follow_up_for_result(
    spec: RollSpec,
    die: int,
    degree: int | None,
    game_data: GameData,
    total: int | None = None,
    *,
    added_success: bool = True,
) -> RollSpec | None:
    """*spec*'s follow-up as the roll that just happened leaves it, or ``None``.

    The die matters, not just whether the attack hit:

    * a **natural 20** is a critical hit, and the effect it lands gains
      ``critical_effect_bonus`` to the DC its save must beat;
    * a **natural 1** that still hits is a graze, and the *target* resists at
      ``critical_miss_resistance_bonus`` — a bonus on their check rather than a cut
      to the DC, which is the same arithmetic but the honest description.

    Both numbers come from ``system.json``. The adjustment is named in the
    follow-up's label rather than silently folded into its number, so a DC box
    reading 23 where the card said 18 explains itself. A critical states the DC it
    *arrives at* rather than the ``+5`` it added, because the chip already shows a
    modifier of its own and two adjacent ``+5``s would read as one thing twice.

    Deterministic in the die and the degree, which is what lets every client at the
    table compute the same chip from the same broadcast roll without the server
    knowing any of these rules.

    *added_success* false is a **minion's** roll (the record's ``minion``, PDF p25):
    its natural 20 is no critical hit and raises no DC. Its natural 1 is still a graze.
    """

    if not follow_up_offered(spec, degree) or spec.follow_up is None:
        return None
    follow_up = spec.follow_up
    natural_20 = die == 20 and added_success
    if spec.follow_up_always:
        # An area's save after the Dodge check: no critical, no graze — an area
        # effect makes no attack check (p195) — only the rank a made Dodge halves.
        if degree is not None and degree > 0 and follow_up.dc_if_resisted is not None:
            label = follow_up.label
            if follow_up.note_if_resisted:
                label = f"{label} — {follow_up.note_if_resisted}"
            return replace(
                follow_up,
                dc=follow_up.dc_if_resisted,
                dc_if_resisted=None,
                note_if_resisted="",
                label=f"{label} (Dodge made, DC {follow_up.dc_if_resisted})",
            )
        return replace(follow_up, dc_if_resisted=None, note_if_resisted="")
    if follow_up.dc_from_total:
        # An opposed check: this roll's total *is* the DC, and a natural 20 on it is
        # an added degree — the same five points (see :func:`.targeting.opposed_dc`).
        # No graze rule: a natural 1 here is simply a low total.
        if total is None:
            return replace(follow_up, dc_from_total=False)
        dc = opposed_dc(total, die if natural_20 or die != 20 else 0, game_data)
        label = follow_up.label
        if natural_20:
            label = f"{label} — natural 20, DC {dc}"
        return replace(follow_up, dc=dc, dc_from_total=False, label=label)
    if natural_20:
        bonus = game_data.system.critical_effect_bonus
        if bonus and follow_up.dc is not None:
            raised = follow_up.dc + bonus
            return replace(
                follow_up,
                dc=raised,
                label=f"{follow_up.label} — critical hit, DC {raised}",
            )
    elif die == 1:
        bonus = game_data.system.critical_miss_resistance_bonus
        if bonus:
            return replace(
                follow_up,
                modifier=follow_up.modifier + bonus,
                label=f"{follow_up.label} — natural 1, +{bonus} to resist",
            )
    return follow_up


def follow_ups_for_result(
    spec: RollSpec,
    die: int,
    degree: int | None,
    game_data: GameData,
    total: int | None = None,
    *,
    added_success: bool = True,
) -> list[RollSpec]:
    """Every roll *spec* provokes, as the roll that just happened leaves each one.

    :func:`follow_up_for_result` applied to :attr:`~RollSpec.follow_up` and to each of
    :attr:`~RollSpec.also` in turn — so a critical hit with a Linked Damage and
    Affliction raises *both* saves' DCs, as it would have raised either alone.
    """

    results = []
    for follow_up in spec.follow_ups():
        one = follow_up_for_result(
            replace(spec, follow_up=follow_up, also=()),
            die,
            degree,
            game_data,
            total,
            added_success=added_success,
        )
        if one is not None:
            results.append(one)
    return results


def _own_trait_roll(spec: RollSpec, char: Character, game_data: GameData) -> RollSpec | None:
    """This character's own roll of the trait *spec* names, or ``None``.

    The dispatch is on :attr:`~RollSpec.kind`, because a bare key is ambiguous —
    a mod is free to call an ability and a skill the same thing. An unknown kind,
    or a key the ruleset no longer has, answers ``None`` rather than raising: a
    trait that went away costs a fill-in, not the card it is drawn on.

    An **empty** kind means a resistance. Before requested rolls there was only
    one thing a trait key could name, so that is what a spec written by an older
    client — or by anything that builds a save by hand — is saying. Reading it any
    other way would quietly stop filling in Toughness on the one card this whole
    mechanism was built for.
    """

    key = spec.trait_key
    if spec.kind == KIND_BEST_OF:
        return _best_own_roll(key, char, game_data)
    if spec.kind == KIND_WORST_OF:
        return _best_own_roll(key, char, game_data, worst=True)
    if spec.kind == KIND_ABILITY:
        if any(a.key == key for a in game_data.abilities):
            return ability_roll(char, game_data, key)
    elif spec.kind in (KIND_RESISTANCE, KIND_POWER_SAVE, KIND_AREA_DODGE, ""):
        if any(r.key == key for r in game_data.resistances):
            return resistance_roll(char, game_data, key)
        if spec.kind == KIND_POWER_SAVE:
            # A save need not be a resistance: Move Object is resisted by Strength, and
            # Alternate Resistance may name any ability or skill.
            if any(a.key == key for a in game_data.abilities):
                return ability_roll(char, game_data, key)
            base = key.partition("::spec::")[0].partition(": ")[0]
            if any(s.name == base for s in game_data.skills):
                return skill_roll(char, game_data, key)
    elif spec.kind == KIND_SKILL:
        base = key.partition("::spec::")[0].partition(": ")[0]
        if any(s.name == base for s in game_data.skills):
            return skill_roll(char, game_data, key)
    elif spec.kind == KIND_INITIATIVE:
        if any(t.key == key for t in game_data.system.derived_traits):
            return initiative_roll(char, game_data)
    return None


def best_of_key(candidates: list[tuple[str, str]] | tuple[tuple[str, str], ...]) -> str:
    """A :data:`KIND_BEST_OF` trait key naming ``(kind, trait key)`` *candidates*."""

    return BEST_OF_SEPARATOR.join(
        f"{kind}{BEST_OF_KIND_SEPARATOR}{key}" for kind, key in candidates
    )


def parse_best_of_key(key: str) -> tuple[tuple[str, str], ...]:
    """The ``(kind, trait key)`` candidates a :data:`KIND_BEST_OF` key names."""

    parts = []
    for part in key.split(BEST_OF_SEPARATOR):
        kind, sep, trait = part.partition(BEST_OF_KIND_SEPARATOR)
        if sep and trait:
            parts.append((kind, trait))
    return tuple(parts)


def best_own_rolls(key: str, char: Character, game_data: GameData) -> list[RollSpec]:
    """Every candidate a :data:`KIND_BEST_OF` key names, as this character rolls it.

    Candidates the sheet cannot answer are left out, and a candidate that is itself a
    best-of (or a worst-of) is refused rather than followed.
    """

    rolls = []
    for kind, trait in parse_best_of_key(key):
        if kind in (KIND_BEST_OF, KIND_WORST_OF):
            continue
        own = _own_trait_roll(RollSpec(label=trait, kind=kind, trait_key=trait), char, game_data)
        if own is not None:
            rolls.append(replace(own, kind=kind, trait_key=trait))
    return rolls


def _best_own_roll(
    key: str, char: Character, game_data: GameData, *, worst: bool = False
) -> RollSpec | None:
    """The highest of the candidates *key* names on this sheet (the lowest, *worst*);
    ties go to the first."""

    rolls = best_own_rolls(key, char, game_data)
    if not rolls:
        return None
    pick = min if worst else max
    return pick(rolls, key=lambda r: r.modifier)


def localize_spec(spec: RollSpec, char: Character | None, game_data: GameData) -> RollSpec:
    """Fill in what *this* sheet knows about a spec that came from someone else.

    Two specs arrive this way and both are built by somebody who cannot see the
    number they are asking for: the **save** an attack forces (the attacker cannot
    see the target's resistance) and a **requested roll** (the asker is asking
    precisely because it is not their sheet). Both travel with a modifier of 0 and
    a :attr:`~RollSpec.trait_key` naming the trait; clicked on a character's own
    sheet, this swaps in their number, so answering is one click rather than a
    click and a look-up.

    The gate is the **trait key**, not the kind. Every builder in this module
    leaves it empty, so a spec loaded off one's own sheet — where the modifier is
    already this character's — comes back untouched; only a spec built to be
    handed to someone else fills it in. Keying on the kind alone would double a
    double-clicked Initiative readout.

    It **adds** rather than replaces, because the modifier may already carry the
    natural-1 resist bonus :func:`follow_up_for_result` put there. A requested
    spec travels at 0, where adding is the same thing.

    A spec naming no trait, or arriving somewhere with no character (the GM
    window's roller), comes back unchanged — the Bonus slider is then the answer,
    as before.
    """

    if char is None or not spec.trait_key:
        return spec
    own = _own_trait_roll(spec, char, game_data)
    if own is None:
        return spec
    # A Toughness save against Damage: the target's Impervious or Hardened decides what
    # the roll is for before it is made (the DC Penetrating leaves, a second die).
    spec = localized_damage_save(spec, char, game_data)
    if spec.kind in (KIND_BEST_OF, KIND_WORST_OF):
        # Which candidate won is worth saying: "Resist Demoralized (Will)".
        return replace(
            spec, modifier=spec.modifier + own.modifier, label=f"{spec.label} ({own.label})"
        )
    if spec.kind == KIND_AREA_DODGE:
        # Dodging an area takes whatever helps against areas (Evasion's +5) on top.
        bonus = area_dodge_bonus(char, game_data)
        label = f"{spec.label} (+{bonus} Evasion)" if bonus else spec.label
        return replace(spec, modifier=spec.modifier + own.modifier + bonus, label=label)
    return replace(spec, modifier=spec.modifier + own.modifier)


def trait_roll_for(spec: RollSpec, char: Character, game_data: GameData) -> RollSpec | None:
    """The trait *char* would roll for *spec*, as their own sheet rolls it — or ``None``.

    What :func:`localize_spec` adds, unmixed: for a best-of (or worst-of) spec the one
    candidate it picks, carrying its own ``kind`` and ``trait_key``, so a caller can say
    "Will +8" rather than "Resist Demoralized (Will)", and ask whether that trait can be
    used at all.
    """

    if not spec.trait_key:
        return None
    if spec.kind in (KIND_BEST_OF, KIND_WORST_OF):
        rolls = best_own_rolls(spec.trait_key, char, game_data)
        if not rolls:
            return None
        pick = min if spec.kind == KIND_WORST_OF else max
        return pick(rolls, key=lambda r: r.modifier)
    own = _own_trait_roll(spec, char, game_data)
    if own is None:
        return None
    return replace(own, kind=own.kind or spec.kind, trait_key=spec.trait_key)


def _resistance_key(phrase: str, game_data: GameData) -> str:
    """The resistance key a rendered save phrase names, or ``""``.

    The phrase is prose the game-term rows built (``"Toughness vs. 18"``,
    ``"Will vs. DC 16"``), and the resistance it names always leads it — so this
    matches the catalog's names against the front of the string rather than trying
    to parse the sentence. Data-driven: a mod's own resistance is found the same way.
    """

    text = phrase.strip().lower()
    # "Fortitude or Will vs. 18" is a choice between two (Alternate Resistance's best
    # or worst of, Illusion's Insight or Perception), and which one applies depends
    # on the target's sheet and the power. Filling in the first would be a confident
    # wrong number, so the target types it instead.
    if " or " in text.partition(" vs")[0]:
        return ""
    for resistance in game_data.resistances:
        if text.startswith(resistance.name.lower()):
            return resistance.key
    return ""


def _save_key(phrase: str, game_data: GameData) -> str:
    """The trait key a power's save phrase names: a resistance, an ability or a skill.

    :func:`_resistance_key` first, then an ability (Move Object is resisted by
    Strength), then a skill (an Alternate Resistance naming Athletics) — the three
    things :func:`_own_trait_roll` can fill in for a power's save. The area layer keeps
    the narrower lookup, since it rolls an ability or a skill as a best-of candidate.
    """

    key = _resistance_key(phrase, game_data)
    text = phrase.strip().lower()
    if key or " or " in text.partition(" vs")[0]:
        return key
    for ability in game_data.abilities:
        if text.startswith(ability.name.lower()):
            return ability.key
    for skill in game_data.skills:
        if text.startswith(skill.name.lower()):
            return skill.name
    return ""


#: The leading number of a resolved check phrase — ``"8 vs. Defense"``.
_LEADING_BONUS = re.compile(r"^[+-]?\d+ vs\.")
#: The actor of an unresolved check phrase — ``"Attack vs. Defense"``.
_CHECK_ACTOR = re.compile(r"^(Attack|Deflect|Effect) vs\.")
#: How each actor word is named on a roll line.
_ACTOR_NAMES = {"Attack": "Attack", "Deflect": "Deflect", "Effect": "Effect check"}


def _check_label(value: str, modifier: int, effect: PowerEffectInstance, game_data) -> str:
    """A check row's value made into a roll line: ``"Attack +8 vs. Defense"``.

    The terms table reads ``8 vs. Defense`` beside a row already labelled Check; a
    footer line has no such label, and a bare number read as nothing in particular. The
    actor word comes from the phrase before its numbers were filled in, and a phrase a
    Dev-mode override wrote with a number of its own is named for what the effect does.
    """

    if not _LEADING_BONUS.match(value):
        return value
    phrase = effective_effect_stats(effect, game_data).get("check", "")
    actor = _CHECK_ACTOR.match(phrase)
    if actor:
        word = _ACTOR_NAMES[actor.group(1)]
    else:
        word = "Attack" if effect_makes_attack(effect, game_data) else "Check"
    return _LEADING_BONUS.sub(f"{word} {modifier:+d} vs.", value, count=1)


def _check_against(value: str) -> str:
    """What a check row is rolled against — ``"Defense"`` out of ``"8 vs. Defense (…)"``."""

    return value.partition(" vs. ")[2].partition(" (")[0].strip()


@dataclass(frozen=True)
class _EffectRolls:
    """One effect's rolls, in the pieces :func:`power_rolls` arranges.

    ``required`` are Check Required rolls, made before anything else; ``check`` is the
    wielder's own check (an attack, or an effect check), carrying what it provokes;
    ``after`` are the lines that follow it — the save the target makes and an opposed
    check of the effect's own — listed in their own right as well, since either may be
    the one somebody needs.
    """

    required: tuple[RollSpec, ...] = ()
    check: RollSpec | None = None
    after: tuple[RollSpec, ...] = ()


def _required_check_bonus(trait: str, char: Character | None, game_data: GameData) -> int:
    """The wielder's own number for a required check's trait (0: a bare d20, or no sheet)."""

    if not trait or char is None:
        return 0
    # A save's dispatch is the widest one: a resistance, an ability or a skill.
    probe = RollSpec(label=trait, kind=KIND_POWER_SAVE, trait_key=trait)
    own = _own_trait_roll(probe, char, game_data)
    return own.modifier if own is not None else 0


def _damage_measured(
    ladder: SpecLadder | None, effect: PowerEffectInstance, game_data: GameData, dc: int | None
) -> SpecLadder | None:
    """A damage ladder stamped with what a target's Toughness is measured against.

    The rank is read back off the save's DC (DC 10 + rank) rather than recomputed, so it
    is the rank the save is really made against — Strength folded in, a dial turned down.
    """

    if ladder is None or ladder.kind != LADDER_DAMAGE or dc is None:
        return ladder
    base = effect_resistance_dc_base(effect, game_data)
    rank = dc - base if base is not None else 0
    return replace(ladder, rank=max(0, rank), penetrating=penetrating_ranks(effect, game_data))


def _effect_roll_parts(
    effect: PowerEffectInstance, game_data: GameData, char: Character | None, prefix: str
) -> _EffectRolls:
    """The rolls one effect calls for, before :func:`power_rolls` orders them."""

    attack_bonus = effect_attack_skill_bonus(effect, char, game_data)
    rows = {r.key: r for r in effect_stat_rows(effect, game_data, char, attack_bonus)}
    numbers = effect_roll_numbers(effect, game_data, char, attack_bonus)
    base_effect = next((e for e in game_data.effects if e.id == effect.effect_id), None)

    def named(text: str) -> str:
        return f"{prefix}: {text}" if prefix else text

    save: RollSpec | None = None
    resistance_row = rows.get("resistance") or rows.get("effect_dc")
    if resistance_row is not None:
        save = RollSpec(
            label=named(resistance_row.value),
            dc=numbers.dc,
            kind=KIND_POWER_SAVE,
            hint="Rolled by the target — click it on their card.",
            outcomes=effect_outcome_ladder(effect, game_data, base_effect),
            success_outcome=effect_success_outcome(effect, game_data, base_effect),
            rolled_by_target=True,
            trait_key=_save_key(resistance_row.value, game_data),
            ladder=_damage_measured(
                effect_spec_ladder(effect, game_data, base_effect), effect, game_data, numbers.dc
            ),
        )

    opposed: RollSpec | None = None
    if base_effect is not None and base_effect.opposed_check:
        # The effect's *own* opposed check (Nullify's), which the wielder makes at their
        # effective rank. Distinct from the counter roll below: this one is what the
        # effect does, not a tactic it can be turned to.
        bonus, against = effect_opposed_check(effect, game_data, char)
        opposed = RollSpec(
            label=named(f"opposed vs. {against}"),
            modifier=bonus,
            kind=KIND_EFFECT_CHECK,
            hint="The opposed number goes in the DC box.",
        )

    required = tuple(
        RollSpec(
            label=named(check.note),
            modifier=_required_check_bonus(check.trait, char, game_data),
            dc=check.dc,
            kind=KIND_POWER_CHECK,
        )
        for check in required_check_rolls(effect, game_data)
    )
    check: RollSpec | None = None
    check_row = rows.get("check")
    if check_row is not None:
        provoked: list[RollSpec] = []
        if save is not None:
            if base_effect is not None and base_effect.resistance_vs_check:
                # Mind Reading: the target's Will is rolled against the check's *result*
                # (p133), not the effect DC — an opposed check. The listed save keeps
                # the DC, for the end-of-turn checks that shut the reader out.
                head = resistance_row.value.partition(" vs.")[0]
                provoked.append(
                    replace(
                        save,
                        label=named(f"{head} vs. the check result"),
                        dc=None,
                        dc_from_total=True,
                    )
                )
            else:
                provoked.append(save)
        if opposed is not None:
            # Nullify's opposed check is what a hit is *for* (p136): offer it off the hit.
            provoked.append(opposed)
        dc = numbers.check_dc
        against = _check_against(check_row.value)
        hint = "" if dc is not None else f"The target's {against} goes in the DC box."
        check = RollSpec(
            label=named(_check_label(check_row.value, numbers.check_actor, effect, game_data)),
            modifier=numbers.check_actor,
            dc=dc,
            kind=KIND_POWER_CHECK,
            hint=hint,
            follow_up=provoked[0] if provoked else None,
            also=tuple(provoked[1:]),
        )
    after = tuple(spec for spec in (save, opposed) if spec is not None)
    # Using the effect is what Tiring and Distracting charge for, and the first roll the
    # wielder makes is the use: the attack or effect check, else the check required
    # before it. An effect that rolls nothing keeps its flaw as a note.
    costs = effect_use_costs(effect, game_data)
    if costs and check is not None:
        check = replace(check, costs=costs)
    elif costs and required:
        required = (replace(required[0], costs=costs), *required[1:])
    return _EffectRolls(required=required, check=check, after=after)


def _effect_rolls(
    effect: PowerEffectInstance, game_data: GameData, char: Character | None, prefix: str
) -> list[RollSpec]:
    """The ordered chain of rolls one effect calls for.

    A Check Required leads (it is rolled *before* the effect goes off), then the
    attack check, then the save it forces — which is attached to the attack as its
    :attr:`~RollSpec.follow_up` as well as listed in its own right, since the two are
    rolled by different people and either may be the one someone needs.
    """

    parts = _effect_roll_parts(effect, game_data, char, prefix)
    check = (parts.check,) if parts.check is not None else ()
    return [*parts.required, *check, *parts.after]


def _counted_use(
    part: _EffectRolls, where: tuple[str, int], effect: PowerEffectInstance, game_data: GameData
) -> _EffectRolls:
    """*part* with its use roll marked as spending a use of the effect *where* names.

    The use roll is the one :attr:`RollSpec.costs` rides on: the check, else the check
    required before it.
    """

    if not effect_counts_uses(effect, game_data):
        return part
    if part.check is not None:
        return replace(part, check=replace(part.check, spends=(where,)))
    if part.required:
        first = replace(part.required[0], spends=(where,))
        return replace(part, required=(first, *part.required[1:]))
    return part


def _linked_check(power: Power, attack, parts: list[_EffectRolls], prefixes: list[str]) -> RollSpec:
    """The one attack check a Linked group rolls, provoking every member's save."""

    members = [parts[i] for i in attack.indices]
    provoked = [fu for part in members if part.check is not None for fu in part.check.follow_ups()]
    names = " + ".join(prefixes[i] for i in attack.indices if prefixes[i])
    label = f"Attack {attack.bonus:+d} vs. {attack.against}"
    differ = len(set(attack.bonuses)) > 1
    hint = f"The target's {attack.against} goes in the DC box. One roll lands every linked effect."
    if differ:
        hint += " The effects' bonuses differ; this rolls the ruleset's pick of them."
    costs = tuple(
        dict.fromkeys(c for part in members if part.check is not None for c in part.check.costs)
    )
    spends = tuple(i for part in members if part.check is not None for i in part.check.spends)
    return RollSpec(
        label=f"{names}: {label}" if names else label,
        modifier=attack.bonus,
        kind=KIND_POWER_CHECK,
        hint=hint,
        follow_up=provoked[0] if provoked else None,
        also=tuple(provoked[1:]),
        costs=costs,
        spends=spends,
    )


def power_rolls(power: Power, char: Character | None, game_data: GameData) -> list[RollSpec]:
    """One :class:`RollSpec` per die roll *power* calls for, in the order they happen.

    Effect-prefixed for a multi-effect power, so a Linked Damage + Affliction says
    which save is which. An attack check and the save it forces are separate rolls
    made by different people, so they get an entry each rather than sharing one.

    An effect carrying a :attr:`~mm_companion.core.powers.PowerEffectInstance.label`
    is prefixed by *that*, whether or not it has company: a tank's two Damage effects
    are "Cannon" and "Heavy machine gun", and prefixing both with "Damage" would name
    neither. Nothing in the base ruleset's powers sets one, so no power's footer moves.

    A **Linked** power rolls its attacks once (p155): the effects that share a target
    share one attack check, at the bonus :func:`~.powers_terms.linked_attacks` picks,
    and that check provokes every one of their saves. It sits where the first of them
    would have, and each save is still listed after its own effect.
    """

    multi = len(power.effects) > 1
    prefixes: list[str] = []
    for effect in power.effects:
        prefix = effect.label
        if not prefix and multi:
            base = next((e for e in game_data.effects if e.id == effect.effect_id), None)
            prefix = base.name if base else effect.effect_id
        prefixes.append(prefix)
    parts = [
        _counted_use(
            _effect_roll_parts(effect, game_data, char, prefix),
            (power.id, index),
            effect,
            game_data,
        )
        for index, (effect, prefix) in enumerate(zip(power.effects, prefixes, strict=True))
    ]
    shared = {}
    for attack in linked_attacks(power, game_data, char):
        for index in attack.indices:
            shared[index] = attack
    specs: list[RollSpec] = []
    for index, part in enumerate(parts):
        specs.extend(part.required)
        attack = shared.get(index)
        if attack is None:
            if part.check is not None:
                specs.append(part.check)
        elif index == attack.indices[0]:
            specs.append(_linked_check(power, attack, parts, prefixes))
        specs.extend(part.after)
    return specs


def improvised_rolls(
    char: Character, game_data: GameData, plan: ImprovisedPlan, skill_row: str
) -> list[RollSpec]:
    """The two checks improvising an effect calls for, in the order they happen (p102).

    The **preparation** check is made once the preparation time is up — by the GM, in
    secret, which is why the hint says so rather than the spec quietly hiding it. Extra
    time spent is a bonus on this one and only this one. The **use** check is the player's,
    rolled the first time they actually reach for the effect, and may be retried until it
    lands or the scene ends.

    Both are the character's own skill total (:func:`skill_roll`) against a DC the plan
    already knows, so neither needs anything typed into the DC box. ``skill_row`` is one of
    :func:`~.improvised.improvised_skills` — the skill their Improvised Effect advantage
    was taken for.
    """

    base = skill_roll(char, game_data, skill_row)
    name = skill_row_label(skill_row)
    return [
        replace(
            base,
            label=f"Improvise: prepare ({name})",
            modifier=base.modifier + plan.check_bonus,
            dc=plan.prep_dc,
            hint=(
                f"Rolled in secret by the GM after {plan.time_text} of preparation. "
                "Failure means the effect simply does not work."
            ),
        ),
        replace(
            base,
            label=f"Improvise: use ({name})",
            dc=plan.use_dc,
            hint=(
                "Rolled the first time the prepared effect is used. A failure still "
                "spends the action, and may be retried until the end of the scene."
            ),
        ),
    ]


def counter_rolls(power: Power, char: Character | None, game_data: GameData) -> list[RollSpec]:
    """The opposed effect checks this power offers for **countering** another (p107).

    "You and the opposing character make effect checks (d20 + rank). If you win, your two
    powers cancel each other out." So each is the wielder's roll at that effect's effective
    rank, and the *opponent's* result is the number to beat — which is what the DC box is
    for, so the specs deliberately carry no DC.

    One per effect that could actually be readied for it: something the character *uses*
    (it makes an attack or forces a resistance — an always-on Protection is not readied,
    it simply is) and usable as a standard action or less, which is the rules' own
    condition. Both of the book's examples pass — a Blast countered with Move Object,
    Mind Control broken with Nullify — and a Feature or a Flight yields nothing.

    Deliberately **not** part of :func:`power_rolls`. Countering is a tactic a power can be
    turned to on a GM's ruling about descriptors, not something the power calls for, and
    putting it in the footer put a die on every attack card and every weapon in the
    Equipment block. The card's own context menu is where it belongs.
    """

    multi = len(power.effects) > 1
    specs: list[RollSpec] = []
    for effect in power.effects:
        base = next((e for e in game_data.effects if e.id == effect.effect_id), None)
        if base is None:
            continue
        resisted = effect_resistance_dc_base(effect, game_data) is not None
        if not effect_makes_attack(effect, game_data) and not resisted:
            continue
        if not effect_action_at_most(base, COUNTER_ACTION_LIMIT, game_data):
            continue
        prefix = effect.label or (base.name if multi else "")
        rank = effect_live_rank(effect, game_data, char)
        specs.append(
            RollSpec(
                label=f"Counter with {prefix or base.name} (effect check)",
                modifier=rank,
                kind=KIND_EFFECT_CHECK,
                hint=(
                    "Opposed effect check: Ready this effect, then spend your reaction "
                    "when the opponent acts. Their effect check goes in the DC box, and "
                    "winning cancels both powers. The descriptors have to oppose, which "
                    "is the GM's call."
                ),
            )
        )
    return specs


def power_roll_lines(power: Power, char: Character | None, game_data: GameData) -> list[str]:
    """Just the label of every :func:`power_rolls` entry — the card's dice footer text."""

    return [spec.label for spec in power_rolls(power, char, game_data)]

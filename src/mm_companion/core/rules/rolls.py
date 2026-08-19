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

from dataclasses import dataclass, replace

from ..character import Character
from ..data_loader import Effect, GameData, ResistanceOutcome
from ..powers import Power, PowerEffectInstance
from .conditions import (
    condition_check_penalty,
    condition_scope_penalty,
    resistance_condition_effect,
)
from .derived import (
    effective_ability,
    initiative_ability,
    initiative_modifier,
    resistance_total,
    skill_modifiers,
    skill_total,
)
from .improvised import ImprovisedPlan
from .powers_terms import (
    effect_attack_skill_bonus,
    effect_roll_numbers,
    effect_stat_rows,
    required_checks,
)

#: Spec ``kind`` values. The UI reads these only for grouping/telemetry — the roll
#: itself is fully described by the label, modifier and DC.
KIND_ABILITY = "ability"
KIND_RESISTANCE = "resistance"
KIND_SKILL = "skill"
KIND_INITIATIVE = "initiative"
KIND_POWER_CHECK = "power-check"
KIND_POWER_SAVE = "power-save"


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
        if self.follow_up is not None:
            raw["follow_up"] = self.follow_up.to_dict()
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


def _condition_names(game_data: GameData, ids: object) -> list[str]:
    """Display names for one or more condition ids, unknown ids passing through."""

    catalog = game_data.condition_catalog()
    values = ids if isinstance(ids, (list, tuple)) else [ids]
    names = []
    for value in values:
        text = str(value).strip()
        if not text:
            continue
        condition = catalog.get(text)
        names.append(condition.name if condition else text)
    return names


def _rung_text(rung: ResistanceOutcome, effect: PowerEffectInstance, game_data: GameData) -> str:
    """One ladder rung rendered for this effect instance (see :class:`ResistanceOutcome`)."""

    if rung.config_key:
        names = _condition_names(game_data, effect.config.get(rung.config_key, ""))
    elif rung.conditions:
        names = _condition_names(game_data, list(rung.conditions))
    else:
        names = [rung.text] if rung.text else []
    text = " + ".join(n for n in names if n)
    if not text:
        return ""
    return f"{text} ({rung.note})" if rung.note else text


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

    return spec.follow_up is not None and (degree is None or degree > 0)


def follow_up_for_result(
    spec: RollSpec, die: int, degree: int | None, game_data: GameData
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
    """

    if not follow_up_offered(spec, degree):
        return None
    follow_up = spec.follow_up
    if die == 20:
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
    if spec.kind == KIND_ABILITY:
        if any(a.key == key for a in game_data.abilities):
            return ability_roll(char, game_data, key)
    elif spec.kind in (KIND_RESISTANCE, KIND_POWER_SAVE, ""):
        if any(r.key == key for r in game_data.resistances):
            return resistance_roll(char, game_data, key)
    elif spec.kind == KIND_SKILL:
        base = key.partition("::spec::")[0].partition(": ")[0]
        if any(s.name == base for s in game_data.skills):
            return skill_roll(char, game_data, key)
    elif spec.kind == KIND_INITIATIVE:
        if any(t.key == key for t in game_data.system.derived_traits):
            return initiative_roll(char, game_data)
    return None


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
    return replace(spec, modifier=spec.modifier + own.modifier)


def _resistance_key(phrase: str, game_data: GameData) -> str:
    """The resistance key a rendered save phrase names, or ``""``.

    The phrase is prose the game-term rows built (``"Toughness vs. 18"``,
    ``"Will vs. DC 16"``), and the resistance it names always leads it — so this
    matches the catalog's names against the front of the string rather than trying
    to parse the sentence. Data-driven: a mod's own resistance is found the same way.
    """

    text = phrase.strip().lower()
    for resistance in game_data.resistances:
        if text.startswith(resistance.name.lower()):
            return resistance.key
    return ""


def _effect_rolls(
    effect: PowerEffectInstance, game_data: GameData, char: Character | None, prefix: str
) -> list[RollSpec]:
    """The ordered chain of rolls one effect calls for.

    A Check Required leads (it is rolled *before* the effect goes off), then the
    attack check, then the save it forces — which is attached to the attack as its
    :attr:`~RollSpec.follow_up` as well as listed in its own right, since the two are
    rolled by different people and either may be the one someone needs.
    """

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
            trait_key=_resistance_key(resistance_row.value, game_data),
        )

    specs: list[RollSpec] = []
    for note, dc in required_checks(effect, game_data):
        specs.append(RollSpec(label=named(note), dc=dc, kind=KIND_POWER_CHECK))
    check_row = rows.get("check")
    if check_row is not None:
        specs.append(
            RollSpec(
                label=named(check_row.value),
                modifier=numbers.check_actor,
                kind=KIND_POWER_CHECK,
                hint="The target's Defense goes in the DC box.",
                follow_up=save,
            )
        )
    if save is not None:
        specs.append(save)
    return specs


def power_rolls(power: Power, char: Character | None, game_data: GameData) -> list[RollSpec]:
    """One :class:`RollSpec` per die roll *power* calls for, in the order they happen.

    Effect-prefixed for a multi-effect power, so a Linked Damage + Affliction says
    which save is which. An attack check and the save it forces are separate rolls
    made by different people, so they get an entry each rather than sharing one.

    An effect carrying a :attr:`~mm_companion.core.powers.PowerEffectInstance.label`
    is prefixed by *that*, whether or not it has company: a tank's two Damage effects
    are "Cannon" and "Heavy machine gun", and prefixing both with "Damage" would name
    neither. Nothing in the base ruleset's powers sets one, so no power's footer moves.
    """

    multi = len(power.effects) > 1
    specs: list[RollSpec] = []
    for effect in power.effects:
        prefix = effect.label
        if not prefix and multi:
            base = next((e for e in game_data.effects if e.id == effect.effect_id), None)
            prefix = base.name if base else effect.effect_id
        specs.extend(_effect_rolls(effect, game_data, char, prefix))
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


def power_roll_lines(power: Power, char: Character | None, game_data: GameData) -> list[str]:
    """Just the label of every :func:`power_rolls` entry — the card's dice footer text."""

    return [spec.label for spec in power_rolls(power, char, game_data)]

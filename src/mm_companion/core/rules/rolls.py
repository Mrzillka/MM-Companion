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
from ..dice import CheckResult
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
from .powers_terms import effect_roll_numbers, effect_stat_rows, required_checks
from .validation import effect_attack_skill_bonus

#: Spec ``kind`` values. The UI reads these only for grouping/telemetry — the roll
#: itself is fully described by the label, modifier and DC.
KIND_ABILITY = "ability"
KIND_RESISTANCE = "resistance"
KIND_SKILL = "skill"
KIND_INITIATIVE = "initiative"
KIND_POWER_CHECK = "power-check"
KIND_POWER_SAVE = "power-save"


@dataclass(frozen=True)
class RollSpec:
    """One named d20 roll, ready to hand to the dice roller.

    ``modifier`` is the roller's own bonus and ``dc`` a difficulty the spec already
    knows (a power's save DC); ``dc`` of ``None`` means "whatever the roller's DC box
    says", which is the normal case for a trait check.

    ``follow_up`` is the roll this one *provokes* — an attack that lands makes its
    target save, so the attack spec carries the save spec and the roller offers it as
    a chip once the attack succeeds. ``outcomes`` is the degree-of-failure ladder for
    a save (see :func:`resistance_outcome`), which is what turns a failed roll into
    "Incapacitated!" instead of a bare number.
    """

    label: str
    modifier: int = 0
    dc: int | None = None
    kind: str = ""
    hint: str = ""
    follow_up: RollSpec | None = None
    outcomes: tuple[str, ...] = ()

    def with_follow_up(self, follow_up: RollSpec | None) -> RollSpec:
        """A copy of this spec that provokes *follow_up* when it succeeds."""

        return replace(self, follow_up=follow_up)


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


def skill_roll(
    char: Character, game_data: GameData, row_id: str, *, label: str = ""
) -> RollSpec:
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


def _rung_text(
    rung: ResistanceOutcome, effect: PowerEffectInstance, game_data: GameData
) -> str:
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


def resistance_outcome(spec: RollSpec, result: CheckResult | None) -> str:
    """What a failed save on *spec* did to the target, or ``""`` when nothing did.

    The ladder's last rung covers every deeper failure, so a four-degree rout on a
    three-rung ladder still answers. A success, an ungraded roll (no DC) and a spec
    with no ladder all return ``""``.
    """

    if result is None or not spec.outcomes or result.degree > 0:
        return ""
    degrees = max(1, abs(result.degree))
    return spec.outcomes[min(degrees, len(spec.outcomes)) - 1]


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
            hint="The target's own resistance goes in the Bonus slider.",
            outcomes=effect_outcome_ladder(effect, game_data, base_effect),
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
    """

    multi = len(power.effects) > 1
    specs: list[RollSpec] = []
    for effect in power.effects:
        prefix = ""
        if multi:
            base = next((e for e in game_data.effects if e.id == effect.effect_id), None)
            prefix = base.name if base else effect.effect_id
        specs.extend(_effect_rolls(effect, game_data, char, prefix))
    return specs


def power_roll_lines(power: Power, char: Character | None, game_data: GameData) -> list[str]:
    """Just the label of every :func:`power_rolls` entry — the card's dice footer text."""

    return [spec.label for spec in power_rolls(power, char, game_data)]

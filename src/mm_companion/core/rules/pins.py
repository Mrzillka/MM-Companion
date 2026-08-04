"""Pinned parameters: the handful of numbers a GM wants on a card, resolved.

A GM reads the same four or five numbers off a creature every round — its
Defence, what it hits with, what its attack forces a save against — and reads
them off *different* creatures. So the set is per-card and the GM chooses it, and
a :class:`PinRef` is how a choice is written down.

**A pin stores a reference, never a number.** ``PinRef(kind="resistance",
key="DEF")``, not ``12`` — because the character underneath is live: a player's
snapshot lands again every time they touch their sheet, an NPC's model is edited
in the sheet beside it, and a condition can move what the number *should* be. A
frozen value would be right once. (This is the same rule
:mod:`mm_companion.ui.roll_click` states for rolls: the spec is built at click
time, not at wire time.)

**Values come off the roll builders, not the derived ones.** ``resistance_roll``
rather than ``resistance_total``, and so on down the table. That is what folds in
the condition overlay for free — a Weakened creature's chip shows the number the
GM should actually use, with the reason in its hint — while the build math stays
condition-free as it should. It also means every chip carries a
:class:`~mm_companion.core.rules.rolls.RollSpec`, so a click can put it straight
into the roller.

The two ends of the lifetime, and why they differ:

- A pin the GM adds from the picker is **concrete** — it names a power by its
  stable :attr:`~mm_companion.core.powers.Power.id`.
- A *default* pin may be **late-bound** (``select=``), because the defaults are
  written down in settings before the creature they will describe exists. "the
  first Damage power" is the only sensible way to say what an NPC card should
  start with, and it stays true when the GM edits which power that is.

An unresolvable pin comes back with a dash rather than vanishing. A chip that
silently disappears leaves the GM no way to remove it and no clue it was ever
there.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from ..character import Character
from ..data_loader import GameData
from ..powers import Power, PowerEffectInstance
from .derived import (
    defense_class,
    effective_ability,
    initiative_modifier,
    resistance_total,
    skill_total,
)
from .rolls import (
    KIND_POWER_CHECK,
    KIND_POWER_SAVE,
    RollSpec,
    ability_roll,
    initiative_roll,
    power_rolls,
    resistance_roll,
    skill_roll,
    skill_row_label,
)
from .validation import leaf_powers

#: What a pin points at.
PIN_ABILITY = "ability"
PIN_RESISTANCE = "resistance"
PIN_SKILL = "skill"
PIN_INITIATIVE = "initiative"
PIN_POWER = "power"
#: The DC an attacker must beat — ``10 + Defence``. Its own kind rather than a
#: dressed-up resistance pin, because it is a *different number*: the Resistances
#: table (and so the resistance pin) shows the rank, and a chip that quietly
#: showed 10 more than the row it was pinned from would be a trap. It is here at
#: all because this is the number a GM reads off a **player's** card and types
#: into the DC box when a mook swings.
PIN_DEFENSE_CLASS = "defense_class"

PIN_KINDS = (
    PIN_ABILITY,
    PIN_RESISTANCE,
    PIN_SKILL,
    PIN_INITIATIVE,
    PIN_POWER,
    PIN_DEFENSE_CLASS,
)

#: A late-bound power pin: whichever power carries a Damage effect first, and the
#: roll its wielder makes with it. What an NPC card starts with, since the
#: defaults are written before the NPC is.
SELECT_FIRST_DAMAGE = "first_damage"

#: Shown in place of a number a pin can no longer reach.
MISSING_VALUE = "—"

#: The effect id :data:`SELECT_FIRST_DAMAGE` looks for. A data id, not a rule:
#: a ruleset that renames its damage effect renames this with it.
DAMAGE_EFFECT_ID = "damage"


@dataclass(frozen=True)
class PinRef:
    """One pinned parameter, as a stable reference into a character.

    ``key`` is an ability or resistance key, a skill *row id*, or a
    :attr:`~mm_companion.core.powers.Power.id`; ``index`` picks which entry of
    :func:`~mm_companion.core.rules.power_rolls` a power pin means, since one
    power calls for several rolls made by different people. ``select`` overrides
    both, resolving them against the character at render time.

    Plain JSON data, because it is persisted in ``settings.json`` beside the
    other GM-window state.
    """

    kind: str
    key: str = ""
    index: int = 0
    select: str = ""

    def to_dict(self) -> dict:
        """This ref as plain JSON data, omitting anything left at its default."""
        raw: dict = {"kind": self.kind}
        if self.key:
            raw["key"] = self.key
        if self.index:
            raw["index"] = self.index
        if self.select:
            raw["select"] = self.select
        return raw

    @classmethod
    def from_dict(cls, raw: object) -> PinRef | None:
        """Rebuild a ref, or ``None`` for anything unusable.

        Defensive like :meth:`RollSpec.from_dict`, and for a milder reason: this
        parses a settings file a user may have edited by hand, and one bad entry
        should cost that chip, not the card.
        """
        if not isinstance(raw, dict):
            return None
        kind = str(raw.get("kind", ""))
        if kind not in PIN_KINDS:
            return None
        index = raw.get("index", 0)
        return cls(
            kind=kind,
            key=str(raw.get("key", "")),
            index=index if isinstance(index, int) and not isinstance(index, bool) else 0,
            select=str(raw.get("select", "")),
        )


@dataclass(frozen=True)
class PinnedValue:
    """A resolved pin: what to write on the chip, and what a click rolls.

    ``spec`` is ``None`` for a value that is simply *read* rather than rolled (a
    Defence DC is a difficulty, nobody throws it) — such a chip renders normally
    and ignores clicks. ``missing`` is the other thing: the pin found nothing at
    all, so the chip renders muted with :data:`MISSING_VALUE` and is there only to
    be removed. The two are kept apart because conflating them would either grey
    out a perfectly good readout or hide a broken pin.
    """

    ref: PinRef
    label: str
    value: str
    spec: RollSpec | None = None
    hint: str = ""
    missing: bool = False


@dataclass(frozen=True)
class PinGroup:
    """One titled group of pinnable values — a heading in the picker."""

    title: str
    values: tuple[PinnedValue, ...]


# -- resolving ---------------------------------------------------------------


def resolve_pin(
    char: Character | None,
    game_data: GameData,
    ref: PinRef,
    *,
    with_conditions: bool = True,
) -> PinnedValue:
    """What *ref* currently reads on *char*. Never raises, never returns ``None``.

    *with_conditions* is the one thing a **chip** and the **picker** disagree
    about, and only about the number shown — the :class:`RollSpec` is the same
    either way, so nothing about what a chip *rolls* moves. A chip is read
    mid-fight, so it shows the number to actually use, condition penalties and
    all. The picker is a catalogue of the character, so it shows what the
    character *is*: browsing a Vulnerable creature's traits to decide what is
    worth pinning is not the moment to be shown its halved Dodge.
    """
    if char is None:
        return _missing(ref, pin_label(ref, game_data))
    if ref.kind == PIN_ABILITY:
        return _from_ability(char, game_data, ref, with_conditions)
    if ref.kind == PIN_RESISTANCE:
        return _from_resistance(char, game_data, ref, with_conditions)
    if ref.kind == PIN_SKILL:
        return _from_skill(char, game_data, ref, with_conditions)
    if ref.kind == PIN_INITIATIVE:
        return _from_initiative(char, game_data, ref, with_conditions)
    if ref.kind == PIN_POWER:
        # A power's numbers are condition-free already: an effect's rank and the
        # DC it sets are build facts, so there is nothing here to strip out.
        return _from_power(char, game_data, ref)
    if ref.kind == PIN_DEFENSE_CLASS:
        return _from_defense_class(char, game_data, ref)
    return _missing(ref, pin_label(ref, game_data))


def resolve_pins(
    char: Character | None,
    game_data: GameData,
    refs: list[PinRef],
    *,
    with_conditions: bool = True,
) -> list[PinnedValue]:
    """:func:`resolve_pin` over a whole strip, in order."""
    return [resolve_pin(char, game_data, r, with_conditions=with_conditions) for r in refs]


def pin_label(ref: PinRef, game_data: GameData) -> str:
    """What a pin is *called*, without needing a character to read it off.

    Kept apart from resolving so a chip is captioned the same whether or not it
    has a value yet: a player card exists before that player pushes a sheet, and
    "initiative —" (the raw kind, in lower case) is a worse thing to show than
    "Initiative —".
    """
    if ref.kind == PIN_ABILITY:
        ability = next((a for a in game_data.abilities if a.key == ref.key), None)
        return (ability.abbr or ability.key) if ability else ref.key
    if ref.kind == PIN_RESISTANCE:
        resistance = next((r for r in game_data.resistances if r.key == ref.key), None)
        return (resistance.abbr or resistance.name) if resistance else ref.key
    if ref.kind == PIN_DEFENSE_CLASS:
        key = game_data.system.trait_keys.defense
        resistance = next((r for r in game_data.resistances if r.key == key), None)
        return f"{(resistance.abbr or resistance.name) if resistance else key} DC"
    if ref.kind == PIN_SKILL:
        return skill_row_label(ref.key) if ref.key else PIN_SKILL
    if ref.kind == PIN_INITIATIVE:
        return next(
            (t.label for t in game_data.system.derived_traits if t.key == PIN_INITIATIVE),
            "Initiative",
        )
    if ref.select == SELECT_FIRST_DAMAGE:
        # Named rather than left as the flat "Power", because this is the one power
        # pin that is *written down* while unresolved — it is what the defaults
        # editor lists, and what a card shows before a character reaches it.
        return "First Damage"
    return "Power"


def _missing(ref: PinRef, label: str) -> PinnedValue:
    return PinnedValue(
        ref=ref,
        label=label,
        value=MISSING_VALUE,
        hint="No longer on this character",
        missing=True,
    )


def _signed(value: int) -> str:
    return f"{value:+d}"


def _from_ability(
    char: Character, game_data: GameData, ref: PinRef, with_conditions: bool = True
) -> PinnedValue:
    ability = next((a for a in game_data.abilities if a.key == ref.key), None)
    if ability is None:
        return _missing(ref, pin_label(ref, game_data))
    spec = ability_roll(char, game_data, ref.key)
    shown = spec.modifier if with_conditions else effective_ability(char, game_data, ref.key)
    return PinnedValue(
        ref=ref,
        label=ability.abbr or ability.key,
        value=_signed(shown),
        spec=spec,
        hint=_hint(ability.name, spec),
    )


def _from_resistance(
    char: Character, game_data: GameData, ref: PinRef, with_conditions: bool = True
) -> PinnedValue:
    resistance = next((r for r in game_data.resistances if r.key == ref.key), None)
    if resistance is None:
        return _missing(ref, pin_label(ref, game_data))
    spec = resistance_roll(char, game_data, ref.key)
    shown = spec.modifier if with_conditions else resistance_total(char, game_data, ref.key)
    # Unsigned: a defence is a number to beat, not a bonus to add. Writing "+12"
    # on a Defence chip invites it into the wrong side of an attack roll.
    return PinnedValue(
        ref=ref,
        label=resistance.abbr or resistance.name,
        value=str(shown),
        spec=spec,
        hint=_hint(resistance.name, spec),
    )


def _from_defense_class(char: Character, game_data: GameData, ref: PinRef) -> PinnedValue:
    key = game_data.system.trait_keys.defense
    resistance = next((r for r in game_data.resistances if r.key == key), None)
    name = (resistance.abbr or resistance.name) if resistance else key
    value = defense_class(char, game_data)
    return PinnedValue(
        ref=ref,
        label=f"{name} DC",
        value=str(value),
        hint=f"DC {value} to hit — what goes in the roller's DC box",
    )


def _from_skill(
    char: Character, game_data: GameData, ref: PinRef, with_conditions: bool = True
) -> PinnedValue:
    if not _skill_row_exists(char, game_data, ref.key):
        return _missing(ref, pin_label(ref, game_data))
    spec = skill_roll(char, game_data, ref.key)
    shown = spec.modifier if with_conditions else skill_total(char, game_data, ref.key)
    return PinnedValue(
        ref=ref,
        label=spec.label,
        value=_signed(shown),
        spec=spec,
        hint=_hint(spec.label, spec),
    )


def _from_initiative(
    char: Character, game_data: GameData, ref: PinRef, with_conditions: bool = True
) -> PinnedValue:
    spec = initiative_roll(char, game_data)
    shown = spec.modifier if with_conditions else initiative_modifier(char, game_data)
    label = next(
        (t.label for t in game_data.system.derived_traits if t.key == PIN_INITIATIVE),
        spec.label,
    )
    return PinnedValue(
        ref=ref,
        label=label,
        value=_signed(shown),
        spec=spec,
        hint=_hint(label, spec),
    )


def _from_power(char: Character, game_data: GameData, ref: PinRef) -> PinnedValue:
    located = _locate_power(char, game_data, ref)
    if located is None:
        return _missing(ref, pin_label(ref, game_data))
    power, index = located
    specs = power_rolls(power, char, game_data)
    if not 0 <= index < len(specs):
        return _missing(ref, power.name or "Power")
    spec = specs[index]
    # A save the target rolls is a *difficulty*, so it reads as one and carries no
    # spec: the wielder never makes their own target's save. It already reaches the
    # person who does as the follow-up chip on the attack's history card, so a
    # rollable chip here would throw a second, meaningless die. Exactly the rule a
    # power card's dice footer already follows — a 🎲 only on the lines the wielder
    # rolls — now on the card too. Anything the wielder *does* roll stays rollable.
    resisted = spec.rolled_by_target and spec.dc is not None
    return PinnedValue(
        ref=replace(ref, key=power.id, index=index, select=""),
        label=_power_chip_label(power, spec),
        value=f"DC {spec.dc}" if resisted else _signed(spec.modifier),
        spec=None if resisted else spec,
        hint=_hint(power.name or "Power", spec),
    )


def _locate_power(char: Character, game_data: GameData, ref: PinRef) -> tuple[Power, int] | None:
    """The power and roll index *ref* names, resolving a late-bound ``select``."""
    if ref.select == SELECT_FIRST_DAMAGE:
        return _first_damage_roll(char, game_data)
    power = next((p for p in leaf_powers(char.powers) if p.id == ref.key), None)
    return None if power is None else (power, ref.index)


def _first_damage_roll(char: Character, game_data: GameData) -> tuple[Power, int] | None:
    """The first Damage power and the index of the roll its wielder makes.

    The **attack check**, not the save it forces: this is the chip a GM clicks
    when the mook swings, and the save is the target's to roll. An auto-hit
    Damage (Perception range, say) has no check at all, and there the save is the
    only thing left to show — better a difficulty the GM can read than a dash.
    """
    for power in leaf_powers(char.powers):
        if not any(_is_damage(e) for e in power.effects):
            continue
        specs = power_rolls(power, char, game_data)
        index = next((i for i, s in enumerate(specs) if s.kind == KIND_POWER_CHECK), None)
        if index is None:
            index = next((i for i, s in enumerate(specs) if s.kind == KIND_POWER_SAVE), None)
        if index is not None:
            return power, index
    return None


def _is_damage(effect: PowerEffectInstance) -> bool:
    return effect.effect_id == DAMAGE_EFFECT_ID


def _power_chip_label(power: Power, spec: RollSpec) -> str:
    """A chip caption for one of a power's rolls.

    The power's name alone for a single-effect power. A multi-effect power's specs
    are already effect-prefixed by :func:`power_rolls` (``"Damage: Toughness vs.
    16"``), so that prefix is lifted out to say *which* effect — otherwise a
    Linked Damage + Affliction gives two chips reading the same thing.
    """
    name = power.name or "Power"
    if len(power.effects) > 1:
        prefix, sep, _rest = spec.label.partition(": ")
        if sep:
            return f"{name}: {prefix}"
    return name


def _hint(name: str, spec: RollSpec) -> str:
    """The chip's tooltip: what it is, what it reads as, and any condition note."""
    parts = [f"{name} — {spec.label}" if spec.label != name else name]
    if spec.hint:
        parts.append(spec.hint)
    return "\n".join(parts)


def _skill_row_exists(char: Character, game_data: GameData, row_id: str) -> bool:
    """Whether *row_id* names a skill this character actually has a row for.

    A row exists when the character bought ranks in it, or when it is an
    unfocused skill from the catalog (every character can try Perception at +AWE).
    A focus or specialization the character never took is gone, and a pin to it
    should say so rather than quietly reading as the bare ability.
    """
    if not row_id:
        return False
    if row_id in char.skill_ranks:
        return True
    base, sep, _rest = row_id.partition("::")
    if sep:
        return _rest in char.focuses.get(base, []) or _rest.removeprefix("spec::") in (
            char.specializations.get(base, [])
        )
    return any(s.name == row_id and not s.focused for s in game_data.skills)


# -- what can be pinned ------------------------------------------------------


def available_pins(
    char: Character, game_data: GameData, *, with_conditions: bool = True
) -> list[PinGroup]:
    """Every parameter of *char* a GM could pin, grouped for the picker.

    Deliberately the whole surface rather than a curated shortlist: which numbers
    matter is exactly the judgement this feature hands to the GM.
    """
    groups: list[PinGroup] = []

    def read(ref: PinRef) -> PinnedValue:
        return resolve_pin(char, game_data, ref, with_conditions=with_conditions)

    groups.append(
        PinGroup(
            "Abilities",
            tuple(read(PinRef(PIN_ABILITY, a.key)) for a in game_data.abilities),
        )
    )
    groups.append(
        PinGroup(
            "Resistances",
            tuple(read(PinRef(PIN_RESISTANCE, r.key)) for r in game_data.resistances),
        )
    )
    groups.append(
        PinGroup(
            "Derived",
            (
                read(PinRef(PIN_INITIATIVE)),
                read(PinRef(PIN_DEFENSE_CLASS)),
            ),
        )
    )

    skills = tuple(read(PinRef(PIN_SKILL, row_id)) for row_id in _skill_rows(char, game_data))
    if skills:
        groups.append(PinGroup("Skills", skills))

    powers: list[PinnedValue] = []
    for power in leaf_powers(char.powers):
        for index in range(len(power_rolls(power, char, game_data))):
            powers.append(read(PinRef(PIN_POWER, power.id, index)))
    if powers:
        groups.append(PinGroup("Powers", tuple(powers)))

    return groups


def _skill_rows(char: Character, game_data: GameData) -> list[str]:
    """Every skill row this character has, catalog order first then its own.

    The unfocused catalog skills always (anyone may try Perception), plus every
    row the character bought — which is where the focuses and specializations
    come from, since those exist only once taken.
    """
    rows = _catalog_skill_rows(game_data)
    rows.extend(row for row in char.skill_ranks if row not in rows)
    return rows


def _catalog_skill_rows(game_data: GameData) -> list[str]:
    """The skill rows that exist without a character — the unfocused catalog."""
    return [s.name for s in game_data.skills if not s.focused]


def default_pin_choices(game_data: GameData) -> list[PinGroup]:
    """Every parameter a *default* strip may name, grouped for the picker.

    The character-free counterpart of :func:`available_pins`, and it is a smaller
    set for a reason rather than an omission: a default is written down before the
    card it will seed exists, so it cannot name that character's things. A power
    pin in particular is a :attr:`~mm_companion.core.powers.Power.id`, which is
    per-character — the only power a default can name is the late-bound
    :data:`SELECT_FIRST_DAMAGE`, and a picker that offered concrete ones would be
    offering pins that resolve to nothing on every card but the one they came from.

    The values carry no reading (``value=""``): there is no character to read them
    off, and the picker hides that column when it is showing this list.
    """

    def choice(ref: PinRef) -> PinnedValue:
        return PinnedValue(ref=ref, label=pin_label(ref, game_data), value="")

    groups = [
        PinGroup(
            "Abilities",
            tuple(choice(PinRef(PIN_ABILITY, a.key)) for a in game_data.abilities),
        ),
        PinGroup(
            "Resistances",
            tuple(choice(PinRef(PIN_RESISTANCE, r.key)) for r in game_data.resistances),
        ),
        PinGroup(
            "Derived",
            (choice(PinRef(PIN_INITIATIVE)), choice(PinRef(PIN_DEFENSE_CLASS))),
        ),
    ]
    skills = tuple(choice(PinRef(PIN_SKILL, row_id)) for row_id in _catalog_skill_rows(game_data))
    if skills:
        groups.append(PinGroup("Skills", skills))
    groups.append(
        PinGroup("Powers", (choice(PinRef(PIN_POWER, select=SELECT_FIRST_DAMAGE)),)),
    )
    return groups


# -- defaults ----------------------------------------------------------------


def parse_pins(raw: object) -> list[PinRef]:
    """A stored list of pin dicts as refs, skipping anything unreadable."""
    if not isinstance(raw, list):
        return []
    parsed = [PinRef.from_dict(entry) for entry in raw]
    return [ref for ref in parsed if ref is not None]


def default_pins(kind: str, raw: object) -> list[PinRef]:
    """The starting strip for a card of *kind* (``"player"`` / ``"npc"``).

    *raw* is the whole ``gm_default_pins`` setting, so this is one lookup plus
    :func:`parse_pins`. It reads from settings rather than from a constant here
    because the point of the defaults is that a GM can change them, which they do
    on the Settings window's GM Mode page — see :func:`default_pin_choices` for
    what such a strip is allowed to name.
    """
    if not isinstance(raw, dict):
        return []
    return parse_pins(raw.get(kind))

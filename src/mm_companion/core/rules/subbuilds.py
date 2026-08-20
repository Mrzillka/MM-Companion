"""Sub-builds: the whole nested characters a power buys inside itself.

Two places in the rules spend a *character's* worth of points inside one power.
Summon builds its minion on ``effect rank x 15`` power points, "subject to the normal
Power Level limits ... and cannot have minions of their own" (p145); Morph's Metamorph
extra buys "one set of traits per rank", each "the same point total as you and subject
to the same Power Level limits" (p136). The budgets have been printed on the cards since
pass 9 — this is the thing the budget is *for*.

**A sub-build is an ordinary** :class:`~..character.Character`. Not a reduced one, not a
new kind: a minion has abilities, skills, advantages, powers and its own Power Level
caps, and the app already has a sheet that edits exactly that. So the model here is only
about *where the character lives* and *what budget it is held to*.

**It lives in the config dict it is bought from** — the effect's for Summon, the chip's
for Metamorph — as a list of :meth:`Character.to_dict` dicts. That was the open question
§6M recorded, and embedding wins on every count that was raised against it:

* the **save format** already writes ``config`` verbatim as JSON, so a nested character
  round-trips with no migration and no new key;
* **undo** snapshots the whole model as JSON text
  (:func:`mm_companion.ui.undo.snapshot_of`), so editing a minion is an undoable step of
  the sheet it hangs off without one line of work;
* the **session layer** pushes the same ``to_dict``, so a GM sees the minion too;
* nothing **dangles** — the alternative, a reference to a file in the GM's directory,
  couples a player's power to a directory they may not have and breaks when it moves.

The budget is *not* stored. It is stamped onto the character every time one is read
(:func:`sub_build_character`), because it is derived: dial the Summon's rank and the
minion's point pool moves with it, which is precisely the check the rules ask for, shown
by the sheet's own budget readout rather than by a warning bolted beside it.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..character import Character
from ..data_loader import GameData, Modifier, SubBuild
from ..powers import ModifierSelection, Power, PowerEffectInstance
from .costs import power_points_spent
from .powers_terms import note_value
from .validation import POWER_CHECKS, leaf_powers, power_level_violations

__all__ = [
    "SubBuildSlot",
    "effect_sub_build_slots",
    "power_sub_build_slots",
    "sub_build_character",
    "sub_build_characters",
    "store_sub_build",
    "remove_sub_build",
    "new_sub_build",
    "power_sub_build_violations",
]


@dataclass(frozen=True)
class SubBuildSlot:
    """One buyable set of nested characters, resolved against a live power.

    ``spec`` is the record's declaration; everything else is what it works out to
    *here*: how many builds this instance is entitled to, what each is built on, and
    which config dict they are stored in. ``selection`` is the chip a modifier-owned
    slot rides on and ``None`` for an effect-owned one, which is also what tells the two
    apart wherever it matters.

    ``budget`` may be ``None``: Metamorph's is the wielder's own point total, and a
    power built with no character open has no such number. A slot with no budget is
    editable but unchecked — the same bargain every other rule here strikes when it
    cannot see a character.
    """

    spec: SubBuild
    effect: PowerEffectInstance
    modifier: Modifier | None = None
    selection: ModifierSelection | None = None
    count: int = 1
    budget: int | None = None
    power_level: int | None = None
    #: What bought this slot, by display name — the modifier for a chip-owned slot,
    #: the effect for its own. Resolved when the slot is built, so a message naming it
    #: reads "this Summon allows" rather than echoing a raw id.
    owner_name: str = ""

    @property
    def key(self) -> str:
        """The config key the builds are stored under."""
        return self.spec.key

    @property
    def label(self) -> str:
        """What one build is called — "Minion", "Alternate form"."""
        return self.spec.label

    @property
    def config(self) -> dict:
        """The dict the builds live in: the chip's when there is one, else the effect's."""
        return self.effect.config if self.selection is None else self.selection.config


def _slot(
    spec: SubBuild,
    effect: PowerEffectInstance,
    game_data: GameData,
    char: Character | None,
    *,
    owner_name: str = "",
    modifier: Modifier | None = None,
    selection: ModifierSelection | None = None,
) -> SubBuildSlot:
    """Resolve one declaration's count and budget against this instance."""

    def resolve(field: dict) -> int | None:
        if not field:
            return None
        return note_value(
            field,
            modifier=modifier,
            selection=selection,
            effect_rank=effect.rank,
            char=char,
            game_data=game_data,
        )

    count = resolve(spec.count)
    return SubBuildSlot(
        spec=spec,
        effect=effect,
        modifier=modifier,
        selection=selection,
        # No ``count`` declared — or a kind that cannot answer from what this slot has —
        # means the one build Summon buys, never zero: a slot nobody can fill is a
        # button that does nothing.
        count=max(1, count if count is not None else 1),
        budget=resolve(spec.budget),
        power_level=None if char is None else char.power_level,
        owner_name=modifier.name if modifier is not None else owner_name,
    )


def effect_sub_build_slots(
    effect: PowerEffectInstance, game_data: GameData, char: Character | None = None
) -> list[SubBuildSlot]:
    """Every sub-build one effect owns — its own, then its attached modifiers'.

    Both levels in one list because both are edited from the same card: an effect
    declares one (Summon) and any chip on it may declare another (Metamorph), and the
    card that shows the effect is the card that shows all of them.
    """

    slots: list[SubBuildSlot] = []
    by_id = {e.id: e for e in game_data.effects}
    record = by_id.get(effect.effect_id)
    if record is not None and record.sub_build is not None:
        slots.append(_slot(record.sub_build, effect, game_data, char, owner_name=record.name))
    catalog = game_data.modifier_catalog()
    for selection in (*effect.extras, *effect.flaws):
        modifier = catalog.get(selection.modifier_id)
        if modifier is None or modifier.sub_build is None:
            continue
        slots.append(
            _slot(
                modifier.sub_build,
                effect,
                game_data,
                char,
                modifier=modifier,
                selection=selection,
            )
        )
    return slots


def power_sub_build_slots(
    power: Power, game_data: GameData, char: Character | None = None
) -> list[SubBuildSlot]:
    """Every sub-build slot across a whole power's effects, in card order."""

    slots: list[SubBuildSlot] = []
    for effect in power.effects:
        slots.extend(effect_sub_build_slots(effect, game_data, char))
    return slots


def _entries(slot: SubBuildSlot) -> list[dict]:
    """The raw stored dicts, ignoring anything that is not one."""

    stored = slot.config.get(slot.key)
    if not isinstance(stored, list):
        return []
    return [entry for entry in stored if isinstance(entry, dict)]


def sub_build_character(slot: SubBuildSlot, index: int) -> Character | None:
    """The *index*-th build of *slot*, or ``None`` where nothing is built yet.

    The budget and Power Level are **stamped on read**, not stored: both are derived
    from the power and the wielder, so a Summon dialled from rank 4 to rank 6 hands its
    minion 90 points rather than the 60 it was written with. That is what makes the
    sheet's own point-pool readout the budget check.
    """

    entries = _entries(slot)
    if not 0 <= index < len(entries):
        return None
    character = Character.from_dict(entries[index])
    return _stamped(slot, character)


def _stamped(slot: SubBuildSlot, character: Character) -> Character:
    """*character* with the slot's derived budget and Power Level written onto it."""

    if slot.budget is not None:
        character.power_points_total = slot.budget
        character.characteristics["power_points"] = slot.budget
    if slot.power_level is not None:
        character.power_level = slot.power_level
        character.characteristics["power_level"] = slot.power_level
    return character


def sub_build_characters(slot: SubBuildSlot) -> list[Character]:
    """Every build of *slot*, in order, each stamped by :func:`sub_build_character`."""

    return [_stamped(slot, Character.from_dict(entry)) for entry in _entries(slot)]


def new_sub_build(slot: SubBuildSlot, game_data: GameData) -> Character:
    """A blank character to fill the next opening of *slot*, on the slot's budget."""

    character = _stamped(slot, Character.new_default(game_data))
    character.profile["hero_name"] = slot.label
    return character


def store_sub_build(slot: SubBuildSlot, index: int, character: Character) -> None:
    """Write *character* into *slot* at *index*, appending when it is one past the end.

    Appending is how a new build lands, so the caller does not need a second entry
    point for "the first one": the card asks for slot *n* and gets it whether or not
    anything was there.
    """

    entries = _entries(slot)
    payload = character.to_dict()
    if 0 <= index < len(entries):
        entries[index] = payload
    else:
        entries.append(payload)
    slot.config[slot.key] = entries


def remove_sub_build(slot: SubBuildSlot, index: int) -> None:
    """Drop the *index*-th build, and the key entirely once none are left.

    Removing the key rather than leaving an empty list is what keeps a power that never
    had a sub-build byte-for-byte what it was when one is added and taken away again.
    """

    entries = _entries(slot)
    if not 0 <= index < len(entries):
        return
    del entries[index]
    if entries:
        slot.config[slot.key] = entries
    else:
        slot.config.pop(slot.key, None)


def power_sub_build_violations(
    power: Power, game_data: GameData, char: Character | None = None
) -> list[str]:
    """Sub-builds that break what the rules say about them (warnings, never clamps).

    Three things are checked, and each is a rule the picker cannot prevent:

    * **over budget** — a minion built on more than ``rank x 15``, or a Metamorph form
      costing more than its wielder. The budget moves with the power, so this can only
      be a live check;
    * **too many builds** — a Metamorph dropped from rank 3 to rank 1 keeps the forms
      it had, since silently deleting a player's character is not a rounding error;
    * **what a sub-character may not have** — a summoned minion "cannot have minions of
      their own, either from this effect or the Minions advantage" (p145), which is a
      fact about the *nested* build and so unreachable from any picker at all;
    * **over Power Level** — a minion is "subject to the normal Power Level limits", and
      the limit is the wielder's own, which is what the slot stamps onto the build. Its
      own sheet shows a breach the way any sheet does, but opening the minion was the
      only way to find out; the same walk runs here so the power that buys it says so.
    """

    violations: list[str] = []
    for slot in power_sub_build_slots(power, game_data, char):
        builds = sub_build_characters(slot)
        if len(builds) > slot.count:
            violations.append(
                f"{slot.label}: {len(builds)} built where this power buys "
                f"{slot.count} — the extra one(s) cost nothing and do nothing."
            )
        for index, build in enumerate(builds, start=1):
            name = f"{slot.label} {index}" if slot.count > 1 else slot.label
            spent = power_points_spent(build, game_data)
            if slot.budget is not None and spent > slot.budget:
                violations.append(
                    f"{name}: built on {spent} PP, more than the {slot.budget} PP "
                    f"this {slot.owner_name} allows."
                )
            violations.extend(_forbidden(slot, name, build, game_data))
            # The nested sheet's own PL check, run from out here. Each message is
            # prefixed, since the reader is looking at the *power* and a bare
            # "Dodge/Parry 25 exceeds PL cap 20" would read as the wielder's.
            violations.extend(
                f"{name}: {message}" for message in power_level_violations(build, game_data)
            )
    return violations


def _forbidden(slot: SubBuildSlot, name: str, build: Character, game_data: GameData) -> list[str]:
    """What *build* carries that its slot forbids it."""

    messages: list[str] = []
    kind = slot.label.lower()
    if slot.spec.forbids_effects:
        by_id = {e.id: e.name for e in game_data.effects}
        carried = {
            by_id.get(effect.effect_id, effect.effect_id)
            for power in leaf_powers(build.powers)
            for effect in power.effects
            if effect.effect_id in slot.spec.forbids_effects
        }
        for effect_name in sorted(carried):
            messages.append(f"{name}: carries {effect_name}, which a {kind} may not.")
    if slot.spec.forbids_advantages:
        # The record names advantage *ids*, the selection carries the display name, so
        # the ban is resolved through the catalog rather than by string luck.
        by_id = {a.id: a.name for a in game_data.advantages}
        banned = {by_id.get(forbidden, forbidden) for forbidden in slot.spec.forbids_advantages}
        for advantage in build.advantages:
            if advantage.name in banned:
                messages.append(
                    f"{name}: has the {advantage.name} advantage, which a {kind} may not."
                )
    return messages


# A minion over its budget is a build error like any other, so it belongs on both
# surfaces that list them. It is registered from here rather than listed in
# ``validation`` because checking a nested character means walking its powers tree, and
# ``leaf_powers`` is validation's — the import only goes one way.
POWER_CHECKS.register(
    "sub-build over budget",
    lambda power, char, data: power_sub_build_violations(power, data, char),
)

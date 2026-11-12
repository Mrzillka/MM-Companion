"""Shaking a condition off: what put it on, and the check that takes it off again.

Dazed and Stunned *from damage* are rolled off with a DC 10 Stamina check as each turn
ends — their catalog records say so (:mod:`.recovery`). The same conditions put on by an
impression or an Affliction are not: an impression is resisted again against the
original DC (PDF p201), and an Affliction overcome with a new resistance check against
its own DC (p109), one success removing everything it put on. Both books' "Dazed by a
non-damage effect may need a different check" (p26).

So whoever lands such a rung writes a :class:`~..character.ShakeOff` onto each condition
it applies — :func:`impression_shake_off` for an impression, :func:`ladder_shake_off` for
a save whose ladder names an overcome effect — and the condition carries it from then
on: into the save file, onto a player's sheet over the wire, and back to the GM in
their snapshot. A condition carrying one offers no recovery check of its own
(:func:`~.recovery.can_recover`). The GM's End turn reads them off the creature
(:func:`held_shake_offs`) and rolls one check per record, and Recover offers one too
(PDF p206).

The Affliction's own modifiers change overcoming it (p110-111), and the effect's
``overcome`` block in the data says how (:class:`~..data_loader.OvercomeModifier`): the
record keeps the ids of the instance's (``mods``) and how many checks it has failed
(``checks``). :func:`shake_off_cadence` says when its check comes, :func:`shake_off_dc`
at what DC, and :func:`shake_off_failed` what a failed one does. A new use landing
on a creature it is already on goes through :func:`merge_landing` (Cumulative adds the
degrees, otherwise the highest applies), and a maintained one (Concentration, Sustained)
is resisted anew each turn its wielder keeps it up (:func:`maintained_by`,
:func:`reapply_roll`, :func:`reapplied_landing`).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace

from ..character import SHAKE_EFFECT, SHAKE_IMPRESSION, Character, ShakeOff
from ..data_loader import (
    CADENCE_CLEARS,
    CADENCE_SCENE,
    GameData,
    Impression,
    OvercomeModifier,
)
from .conditions import apply_condition, remove_condition
from .impress import TraitChoice, best_choice, resist_choices
from .rolls import (
    LADDER_IMPRESS,
    RollSpec,
    SpecLadder,
    _own_trait_roll,
    overcome_trait,
    trait_ref,
)
from .targeting import concrete_rung, failure_degrees

__all__ = [
    "impression_shake_off",
    "ladder_shake_off",
    "area_shake_off",
    "held_shake_offs",
    "shake_off_each_minute",
    "CADENCE_TURN",
    "CADENCE_MINUTE",
    "CADENCE_SCENE",
    "CADENCE_CLEARS",
    "shake_off_cadence",
    "shake_off_dc",
    "ShakeFailure",
    "shake_off_failed",
    "shake_off_made",
    "Landing",
    "merge_landing",
    "landed_caption",
    "same_effect",
    "shake_off_maintained",
    "maintained_by",
    "shake_off_stopped",
    "reapply_roll",
    "reapply_request",
    "reapplied_landing",
    "shake_off_failed_on",
    "same_origin",
    "shake_off_recover_spec",
    "shake_off_choice",
    "shake_off_roll",
    "shake_off_request",
    "shake_off_hint",
]


def impression_shake_off(
    impression: Impression,
    skill_row: str,
    dc: int,
    *,
    resist: TraitChoice | None = None,
    source: str = "",
    degree: int | None = None,
) -> ShakeOff:
    """The record an impression's conditions carry: resisted again at *dc*.

    *resist* is how the GM had the target resist (``None``: its best option, which
    is what it gets again); *skill_row* the skill the impression was made with, which
    decides what may resist it.
    """

    return ShakeOff(
        kind=SHAKE_IMPRESSION,
        name=impression.name,
        dc=dc,
        ref=impression.id,
        trait=resist.ref if resist is not None else "",
        skill=skill_row,
        source=source,
        degree=failure_degrees(degree),
    )


def ladder_shake_off(
    ladder: SpecLadder | None,
    dc: int | None,
    degree: int | None,
    *,
    trait: str = "",
    source: str = "",
) -> ShakeOff | None:
    """The record a failed save's conditions carry, or ``None`` when they need none.

    Only a ladder naming an overcome effect (an Affliction's) or an impression writes
    one, and only for a save that failed against a known DC. An effect's trait is the
    ladder's own ``overcome`` when the instance chose one, otherwise *trait* — the
    ``kind:key`` the save was resisted with (empty when unknown: the bearer picks, as a
    best-of save does). An impression is resisted again with the bearer's best, as a
    request card's resistance was; its source is the one the ladder names, if any.
    """

    if ladder is None or not ladder.effect or dc is None:
        return None
    failures = failure_degrees(degree)
    if not failures:
        return None
    if ladder.rungs:
        # The last rung covers every deeper failure (a minion's worst lands there).
        failures = min(failures, len(ladder.rungs))
    if ladder.kind == LADDER_IMPRESS:
        return ShakeOff(
            kind=SHAKE_IMPRESSION,
            name=ladder.name or ladder.effect,
            dc=dc,
            ref=ladder.effect,
            skill=ladder.skill,
            source=ladder.source or source,
            degree=failures,
        )
    return ShakeOff(
        kind=SHAKE_EFFECT,
        name=ladder.name or ladder.effect,
        dc=dc,
        ref=ladder.effect,
        trait=ladder.overcome or trait,
        source=ladder.source or source,
        degree=failures,
        mods=ladder.mods,
        rungs=tuple(rung.conditions for rung in ladder.rungs) if ladder.mods else (),
        save=trait if ladder.mods else "",
    )


def area_shake_off(
    effect_id: str,
    config: Mapping[str, object],
    game_data: GameData,
    dc: int | None,
    degree: int | None,
    *,
    name: str,
    resisted_with: str = "",
    source: str = "",
    mods: tuple[str, ...] = (),
) -> ShakeOff | None:
    """:func:`ladder_shake_off` for an area's effect, which travels as its config rather
    than a ladder: the record its failed save's conditions carry, or ``None``.

    *resisted_with* is the trait key the creature saved with (empty when unknown);
    *mods* the instance's modifiers that change overcoming it, when known.
    """

    base = next((e for e in game_data.effects if e.id == effect_id), None)
    if base is None or base.overcome is None:
        return None
    ladder = SpecLadder(
        "",
        tuple(concrete_rung(rung, game_data, config) for rung in base.resistance_outcomes),
        effect=base.id,
        overcome=overcome_trait(base, config, game_data),
        name=name or base.name,
        mods=mods,
    )
    trait = trait_ref(game_data, resisted_with)
    return ladder_shake_off(ladder, dc, degree, trait=trait, source=source)


def held_shake_offs(char: Character) -> list[tuple[ShakeOff, tuple[tuple[str, str | None], ...]]]:
    """Each distinct record on *char*, with the conditions carrying it, in order.

    One rung's conditions share one record, so they are listed — and shaken off —
    together.
    """

    held: dict[ShakeOff, list[tuple[str, str | None]]] = {}
    for applied in char.conditions:
        if applied.provenance is None and applied.shake_off is not None:
            held.setdefault(applied.shake_off, []).append((applied.condition_id, applied.parameter))
    return [(shake, tuple(changes)) for shake, changes in held.items()]


#: When a record's check comes (:func:`shake_off_cadence`): as each of the bearer's
#: turns ends, or once a minute (an Affliction's third degree) — besides the data's
#: :data:`~..data_loader.CADENCE_SCENE` (Prolonged) and
#: :data:`~..data_loader.CADENCE_CLEARS` (Instant Recovery: it clears by itself where
#: the check would come).
CADENCE_TURN = "turn"
CADENCE_MINUTE = "minute"


def _overcome(game_data: GameData, shake: ShakeOff):
    if shake.kind != SHAKE_EFFECT:
        return None
    effect = next((e for e in game_data.effects if e.id == shake.ref), None)
    return effect.overcome if effect is not None else None


def _mods(game_data: GameData, shake: ShakeOff) -> list[OvercomeModifier]:
    """The overcome rules of the modifiers *shake*'s instance carries."""
    rule = _overcome(game_data, shake)
    if rule is None:
        return []
    return [m for m in (rule.modifier(i) for i in shake.mods) if m is not None]


def shake_off_cadence(game_data: GameData, shake: ShakeOff) -> str:
    """When *shake*'s check comes — one of the ``CADENCE_*`` values.

    An impression's comes as each turn ends. An Affliction's too, but once a minute from
    its effect's ``eachMinuteFromDegree`` (the third, p109); a Prolonged one's at the end
    of each scene, and an Instant Recovery one clears by itself where the turn's check
    would come (the minute's still waits).
    """

    rule = _overcome(game_data, shake)
    if rule is None:
        return CADENCE_TURN
    cadences = {m.cadence for m in _mods(game_data, shake)}
    if CADENCE_SCENE in cadences:
        return CADENCE_SCENE
    if rule.each_minute_from and shake.degree >= rule.each_minute_from:
        return CADENCE_MINUTE
    if CADENCE_CLEARS in cadences and not shake_off_maintained(game_data, shake):
        # A maintained one's Instant Recovery waits for its wielder to stop instead.
        return CADENCE_CLEARS
    return CADENCE_TURN


def shake_off_each_minute(game_data: GameData, shake: ShakeOff) -> bool:
    """Whether *shake* is checked once a minute rather than as a turn ends (p109)."""

    return shake_off_cadence(game_data, shake) == CADENCE_MINUTE


def shake_off_dc(game_data: GameData, shake: ShakeOff) -> int:
    """The DC *shake*'s next check is made against.

    The original DC, moved by each of its modifiers' ``dcPerCheck`` for every check it
    has failed — counting the initial resistance check too for one ``fromInitial``
    (Increasing Difficulty: the first overcome check is at +1; Decreasing Difficulty's
    first is at the DC and the next at -1). Not below 0.
    """

    dc = shake.dc
    for mod in _mods(game_data, shake):
        if mod.dc_per_check:
            dc += mod.dc_per_check * (shake.checks + (1 if mod.from_initial else 0))
    return max(0, dc)


@dataclass(frozen=True)
class ShakeFailure:
    """What a failed overcome check does (:func:`shake_off_failed`).

    ``record`` is the record from now on (one more check failed, a degree deeper) — the
    same one when nothing changed. ``worse`` the condition ids that replace the record's
    conditions when it went a degree deeper (Progressive), else ``None``; ``also``
    conditions put on besides, with no record of their own (Fatal's Dying).
    """

    record: ShakeOff
    worse: tuple[str, ...] | None = None
    also: tuple[str, ...] = ()


def shake_off_failed(game_data: GameData, shake: ShakeOff) -> ShakeFailure:
    """What failing *shake*'s check does, by its instance's modifiers (p110).

    With none that cares, nothing: the conditions stay another turn. A failure is
    counted on the record only when a modifier reads the count (Increasing / Decreasing
    Difficulty); one that ``worsens`` takes it a degree deeper, to its last rung at
    most (Progressive); one that ``applies`` a condition from a degree puts it on when
    the check was failed at that degree or deeper (Fatal's Dying, at the third).
    """

    mods = _mods(game_data, shake)
    record = shake
    if any(m.dc_per_check for m in mods):
        record = replace(record, checks=shake.checks + 1)
    worse = None
    if any(m.worsens for m in mods) and shake.rungs and shake.degree < len(shake.rungs):
        worse = shake.rungs[shake.degree]
        record = replace(record, degree=shake.degree + 1)
    also = tuple(
        m.applies
        for m in mods
        if m.applies and m.fail_from_degree and shake.degree >= m.fail_from_degree
    )
    return ShakeFailure(record, worse, also)


def _choice(char: Character, game_data: GameData, ref: str) -> TraitChoice | None:
    kind, _, key = ref.partition(":")
    if not kind or not key:
        return None
    spec = _own_trait_roll(RollSpec(label=key, kind=kind, trait_key=key), char, game_data)
    if spec is None:
        return None
    return TraitChoice(kind, key, spec.label, spec.modifier)


def shake_off_choice(char: Character, game_data: GameData, shake: ShakeOff) -> TraitChoice | None:
    """What *char* resists *shake* with, at its number now — ``None`` if nothing.

    The record's trait when it names one; for an impression that does not, the best of
    what may resist the skill it was made with.
    """

    if shake.trait:
        return _choice(char, game_data, shake.trait)
    if shake.kind == SHAKE_IMPRESSION and shake.skill:
        return best_choice(resist_choices(char, game_data, shake.skill))
    return None


def shake_off_roll(char: Character, game_data: GameData, shake: ShakeOff) -> RollSpec | None:
    """The check *char* (an NPC the GM rolls for) shakes *shake* off with, or ``None``."""

    choice = shake_off_choice(char, game_data, shake)
    if choice is None:
        return None
    return RollSpec(
        label=f"{choice.label} — shaking off {shake.name}",
        modifier=choice.modifier,
        kind=choice.kind,
        dc=shake_off_dc(game_data, shake),
    )


# -- a new use on a creature it is already on (PDF p109-110) ------------------------


def same_effect(a: ShakeOff, b: ShakeOff) -> bool:
    """Whether two records are the same power's, from the same wielder — whatever
    degree each landed at."""
    return (a.kind, a.ref, a.name, a.source) == (b.kind, b.ref, b.name, b.source)


@dataclass(frozen=True)
class Landing:
    """What a rung landing does once what is already on is counted (:func:`merge_landing`).

    ``remove`` comes off first (the old degree's conditions), then ``apply`` goes on
    carrying ``shake``. Both empty: it is no worse than what is already there.
    """

    remove: tuple[tuple[str, str | None], ...] = ()
    apply: tuple[tuple[str, str | None], ...] = ()
    shake: ShakeOff | None = None


def merge_landing(
    char: Character | None,
    game_data: GameData,
    changes,
    shake: ShakeOff | None,
) -> Landing:
    """A rung of an Affliction landing on *char*, counting the same power already on it.

    Afflictions do not stack: only the highest degree applies (p109), so a new use that
    lands no deeper than the one already on changes nothing, and a deeper one replaces
    it. A Cumulative one (``addsDegrees``) adds its degrees to those already on instead,
    to the last rung at most (p110). Anything else — an impression, a first landing, a
    rung with no record — lands as it is.
    """

    changes = tuple(changes)
    if char is None or shake is None or shake.kind != SHAKE_EFFECT:
        return Landing(apply=changes, shake=shake)
    held = next(((s, c) for s, c in held_shake_offs(char) if same_effect(s, shake)), None)
    if held is None:
        return Landing(apply=changes, shake=shake)
    old, old_changes = held
    rungs = shake.rungs or old.rungs
    if any(m.adds_degrees for m in _mods(game_data, shake)):
        degree = old.degree + shake.degree
        if rungs:
            degree = min(degree, len(rungs))
    else:
        degree = max(old.degree, shake.degree)
    if degree <= old.degree:
        return Landing()
    if degree != shake.degree and rungs:
        changes = tuple((cid, None) for cid in rungs[degree - 1])
    return Landing(
        remove=old_changes,
        apply=changes,
        shake=replace(shake, degree=degree, checks=old.checks),
    )


def landed_caption(game_data: GameData, caption: str, changes, landed) -> str:
    """A landed rung's *caption*, saying so when what went on (*landed*) is not what
    the rung itself puts on (*changes*): no worse than it was, or a degree it added up
    to."""

    changes, landed = tuple(changes), tuple(landed)
    if landed == changes:
        return caption
    if not landed:
        return f"{caption} — no worse than it was"
    catalog = game_data.condition_catalog()
    names = ", ".join((catalog[cid].name if cid in catalog else cid) for cid, _parameter in landed)
    return f"{caption} — adds up to {names}"


# -- maintained: Concentration and Sustained (p110) ----------------------------------


def shake_off_maintained(game_data: GameData, shake: ShakeOff) -> bool:
    """Whether *shake*'s Affliction is maintained — resisted anew each turn its wielder
    keeps it up (``reapplies``: Concentration, Sustained)."""
    return any(m.reapplies for m in _mods(game_data, shake))


def maintained_by(wielder: Character | None, shake: ShakeOff) -> bool | None:
    """Whether *wielder* still maintains *shake*'s power: its power of that name is
    switched on (the card's on/off). ``None`` when there is no telling — no sheet, or no
    power by that name."""

    if wielder is None:
        return None
    power = next((p for p in wielder.powers if p.name == shake.name), None)
    if power is None:
        return None
    return bool(
        power.activated
        and power.item_present
        and all(e.toggled_on and not e.suppressed for e in power.effects)
    )


def shake_off_stopped(game_data: GameData, shake: ShakeOff) -> tuple[bool, ShakeOff]:
    """Its wielder stopped maintaining *shake*: whether its conditions clear now (a
    maintained Instant Recovery, ``whenStopped``), and the record from now on — no
    longer maintained, so overcome as an unmaintained one is."""

    mods = _mods(game_data, shake)
    clears = any(m.when_stopped for m in mods)
    dropped = {m.id for m in mods if m.reapplies or m.when_stopped}
    return clears, replace(shake, mods=tuple(m for m in shake.mods if m not in dropped))


def _save_ref(shake: ShakeOff) -> str:
    return shake.save or shake.trait


def reapply_roll(char: Character, game_data: GameData, shake: ShakeOff) -> RollSpec | None:
    """The new resistance check *char* (an NPC the GM rolls for) makes against a
    maintained *shake* — its save, at the original DC — or ``None`` if nothing names it."""

    choice = _choice(char, game_data, _save_ref(shake))
    if choice is None:
        return None
    return RollSpec(
        label=f"{choice.label} — resisting {shake.name} again",
        modifier=choice.modifier,
        kind=choice.kind,
        dc=shake.dc,
    )


def reapply_request(shake: ShakeOff) -> RollSpec | None:
    """:func:`reapply_roll` as a character-free request, for a player to roll."""

    kind, _, key = _save_ref(shake).partition(":")
    if not kind or not key:
        return None
    return RollSpec(
        label=f"Resist {shake.name} again",
        kind=kind,
        trait_key=key,
        dc=shake.dc,
        hint="Its wielder keeps it up: fail and it lands again, as a new use.",
    )


def reapplied_landing(
    char: Character | None, game_data: GameData, shake: ShakeOff, degree: int | None
) -> Landing:
    """What a new resistance check of *degree* against a maintained *shake* lands: the
    rung its failures reach, as a new use (:func:`merge_landing`), or nothing if made."""

    failures = failure_degrees(degree)
    if not failures or not shake.rungs:
        return Landing()
    failures = min(failures, len(shake.rungs))
    changes = tuple((cid, None) for cid in shake.rungs[failures - 1])
    return merge_landing(char, game_data, changes, replace(shake, degree=failures, checks=0))


def shake_off_recover_spec(
    char: Character, game_data: GameData, shake: ShakeOff
) -> RollSpec | None:
    """Recover's new resistance check against *shake* (PDF p206), rolled at *char*'s
    own number — on top of the turn's, and at any cadence (it is how a third-degree
    Affliction is checked before its minute is up). Carries the record it takes off."""

    spec = shake_off_roll(char, game_data, shake)
    if spec is None:
        return None
    return replace(
        spec,
        label=f"Shake off {shake.name} ({spec.label.split(' — ')[0]})",
        shakes_off=shake,
        hint="Make it and it comes off, with everything it put on.",
    )


def shake_off_made(char: Character, shake: ShakeOff) -> list[tuple[str, str | None]]:
    """Take off every condition carrying *shake* — a made check. What came off.

    Nothing, if they went meanwhile (the GM took them off by hand, End turn shook them).
    Matched by where the record came from, not its count: a check rolled before End turn
    counted a failure still takes the conditions off.
    """

    gone = [
        c
        for c in char.conditions
        if c.provenance is None and c.shake_off is not None and same_origin(c.shake_off, shake)
    ]
    for applied in gone:
        remove_condition(char, applied)
    return [(c.condition_id, c.parameter) for c in gone]


def shake_off_failed_on(char: Character, game_data: GameData, shake: ShakeOff) -> bool:
    """Put a failed check against *shake* on *char* itself — a player's own Recover.

    Read from the record the conditions carry now (End turn may have moved it on since
    the check was rolled): :func:`shake_off_failed` says what changes. Whether anything
    did.
    """

    held = [
        c
        for c in char.conditions
        if c.provenance is None and c.shake_off is not None and same_origin(c.shake_off, shake)
    ]
    if not held:
        return False
    current = held[0].shake_off
    failure = shake_off_failed(game_data, current)
    changed = False
    if failure.worse is not None:
        for applied in held:
            remove_condition(char, applied)
        for condition_id in failure.worse:
            apply_condition(char, condition_id, game_data, shake_off=failure.record)
        changed = True
    elif failure.record != current:
        for applied in held:
            applied.shake_off = failure.record
        changed = True
    for condition_id in failure.also:
        apply_condition(char, condition_id, game_data)
        changed = True
    return changed


def same_origin(a: ShakeOff, b: ShakeOff) -> bool:
    """Whether two records came from the same rung landing."""
    return (a.kind, a.ref, a.name, a.source, a.dc) == (b.kind, b.ref, b.name, b.source, b.dc)


def shake_off_request(shake: ShakeOff, game_data: GameData | None = None) -> RollSpec | None:
    """The same check as a character-free request, for a player to roll on their sheet.

    Names the trait by ``kind`` and ``trait_key`` with no modifier, the shape
    :func:`~.rolls.localize_spec` fills in. ``None`` for a record naming no trait — an
    impression's best-of is asked through the Impress layer, which knows its options.
    """

    kind, _, key = shake.trait.partition(":")
    if not kind or not key:
        return None
    dc = shake.dc if game_data is None else shake_off_dc(game_data, shake)
    spec = RollSpec(label=f"Shake off {shake.name}", kind=kind, trait_key=key, dc=dc)
    return replace(
        spec,
        hint="Make it and it comes off; fail it and it lasts another turn.",
    )


def _trait_name(game_data: GameData, ref: str) -> str:
    kind, _, key = ref.partition(":")
    for trait in (*game_data.resistances, *game_data.abilities):
        if trait.key == key:
            return trait.name
    return key if kind else ""


def shake_off_hint(game_data: GameData, shake: ShakeOff) -> str:
    """How *shake* comes off, in one line — what a chip's tooltip says in place of the
    record's own recovery (the damage-caused condition's)."""

    by = f" ({shake.source})" if shake.source else ""
    trait = _trait_name(game_data, shake.trait)
    with_trait = f" with {trait}" if trait else ""
    if shake.kind == SHAKE_IMPRESSION:
        return (
            f"From {shake.name}{by}: resisted again{with_trait} against DC {shake.dc} at the "
            "end of each turn; success removes it."
        )
    cadence = shake_off_cadence(game_data, shake)
    if cadence == CADENCE_CLEARS:
        return f"From {shake.name}{by}: clears by itself at the end of the next turn."
    when = {
        CADENCE_MINUTE: "once a minute",
        CADENCE_SCENE: "at the end of each scene",
    }.get(cadence, "at the end of each turn")
    text = (
        f"From {shake.name}{by}: overcome{with_trait} against DC "
        f"{shake_off_dc(game_data, shake)} {when}; success removes everything it put on."
    )
    failing = []
    for mod in _mods(game_data, shake):
        if mod.worsens and shake.rungs and shake.degree < len(shake.rungs):
            failing.append("goes a degree deeper")
        if mod.applies and mod.fail_from_degree and shake.degree >= mod.fail_from_degree:
            failing.append(f"adds {_condition_name(game_data, mod.applies)}")
    if failing:
        text += f" Failing it {' and '.join(failing)}."
    return text


def _condition_name(game_data: GameData, condition_id: str) -> str:
    record = game_data.condition_catalog().get(condition_id)
    return record.name if record is not None else condition_id

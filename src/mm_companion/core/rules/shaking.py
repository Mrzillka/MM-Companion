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
(:func:`held_shake_offs`) and rolls one check per record.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace

from ..character import SHAKE_EFFECT, SHAKE_IMPRESSION, Character, ShakeOff
from ..data_loader import GameData, Impression
from .impress import TraitChoice, best_choice, resist_choices
from .rolls import RollSpec, SpecLadder, _own_trait_roll, overcome_trait, trait_ref
from .targeting import failure_degrees

__all__ = [
    "impression_shake_off",
    "ladder_shake_off",
    "area_shake_off",
    "held_shake_offs",
    "shake_off_each_minute",
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

    Only a ladder naming an overcome effect (an Affliction's) writes one, and only for
    a save that failed against a known DC. The trait is the ladder's own ``overcome``
    when the instance chose one, otherwise *trait* — the ``kind:key`` the save was
    resisted with (empty when unknown: the bearer picks, as a best-of save does).
    """

    if ladder is None or not ladder.effect or dc is None:
        return None
    failures = failure_degrees(degree)
    if not failures:
        return None
    if ladder.rungs:
        # The last rung covers every deeper failure (a minion's worst lands there).
        failures = min(failures, len(ladder.rungs))
    return ShakeOff(
        kind=SHAKE_EFFECT,
        name=ladder.name or ladder.effect,
        dc=dc,
        ref=ladder.effect,
        trait=ladder.overcome or trait,
        source=source,
        degree=failures,
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
) -> ShakeOff | None:
    """:func:`ladder_shake_off` for an area's effect, which travels as its config rather
    than a ladder: the record its failed save's conditions carry, or ``None``.

    *resisted_with* is the trait key the creature saved with (empty when unknown).
    """

    base = next((e for e in game_data.effects if e.id == effect_id), None)
    if base is None or base.overcome is None:
        return None
    ladder = SpecLadder(
        "",
        base.resistance_outcomes,
        effect=base.id,
        overcome=overcome_trait(base, config, game_data),
        name=name or base.name,
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


def shake_off_each_minute(game_data: GameData, shake: ShakeOff) -> bool:
    """Whether *shake* is checked once a minute rather than as a turn ends.

    An Affliction at its third degree (p109): its effect's ``overcome`` block says from
    which degree. An impression is always checked as a turn ends.
    """

    if shake.kind != SHAKE_EFFECT:
        return False
    effect = next((e for e in game_data.effects if e.id == shake.ref), None)
    rule = effect.overcome if effect is not None else None
    return bool(rule and rule.each_minute_from and shake.degree >= rule.each_minute_from)


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
        dc=shake.dc,
    )


def shake_off_request(shake: ShakeOff) -> RollSpec | None:
    """The same check as a character-free request, for a player to roll on their sheet.

    Names the trait by ``kind`` and ``trait_key`` with no modifier, the shape
    :func:`~.rolls.localize_spec` fills in. ``None`` for a record naming no trait — an
    impression's best-of is asked through the Impress layer, which knows its options.
    """

    kind, _, key = shake.trait.partition(":")
    if not kind or not key:
        return None
    spec = RollSpec(label=f"Shake off {shake.name}", kind=kind, trait_key=key, dc=shake.dc)
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
    when = "once a minute" if shake_off_each_minute(game_data, shake) else "at the end of each turn"
    return (
        f"From {shake.name}{by}: overcome{with_trait} against DC {shake.dc} {when}; "
        "success removes everything it put on."
    )

"""Which attacks, and which areas, a character could pick off the roller's Actions popup.

The popup's Attack tile (and, from the combat maneuvers onward, every action that needs
an attack loaded) asks *which* attack first, off a plain menu: :func:`attack_choices`
is that menu's list. The player's Area tile asks which area: :func:`area_choices`. Both
read the very roll lines the power and gear cards draw (:func:`~.area_attack.
power_rolls_with_area`), so the menu and the cards cannot disagree about what a
character can do.

Pure: the roller's host (``ui/sections/dice.py``) hands the lists to the panel, which
reads no game data.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass

from ..character import Character
from ..data_loader import GameData
from .accessories import item_effective_build
from .area_attack import power_rolls_with_area
from .attacks import targetable_attack
from .rolls import KIND_AREA, RollSpec
from .validation import leaf_powers

__all__ = ["AttackChoice", "area_choices", "attack_choices"]


@dataclass(frozen=True)
class AttackChoice:
    """One entry of an attack (or area) menu: what it reads, and the roll line behind it."""

    caption: str
    spec: RollSpec


def _build_rolls(
    char: Character, game_data: GameData, *, npc: bool
) -> Iterator[tuple[str, list[RollSpec]]]:
    """``(build name, its roll lines)`` for every power, then every piece of gear."""

    for power in leaf_powers(char.powers):
        yield power.name, power_rolls_with_area(power, char, game_data, npc=npc)
    for item in char.equipment:
        if item.build is None:
            continue
        build = item_effective_build(item, game_data)
        yield item.name or build.name, power_rolls_with_area(build, char, game_data, npc=npc)


def _caption(name: str, spec: RollSpec) -> str:
    return f"{name}: {spec.label}" if name else spec.label


def attack_choices(
    char: Character, game_data: GameData, *, npc: bool = False
) -> list[AttackChoice]:
    """Every attack *char* could load: a roll line the wielder makes that forces a save
    (:func:`~.attacks.targetable_attack`). Powers first, then gear, in sheet order.

    An effect with no attack check (Perception range) has no such line — its save is the
    whole attack — so it is not offered: nothing here could change a check it never makes.
    """

    return [
        AttackChoice(_caption(name, spec), spec)
        for name, specs in _build_rolls(char, game_data, npc=npc)
        for spec in specs
        if targetable_attack(spec)
    ]


def area_choices(char: Character, game_data: GameData, *, npc: bool = False) -> list[AttackChoice]:
    """Every area *char*'s cards offer right now (an area bought with the extra, or an
    attack whose free area use is switched on): the cards' area lines, whose
    ``trait_key`` is the area's key for ``DiceSection.start_area``."""

    return [
        AttackChoice(_caption(name, spec), spec)
        for name, specs in _build_rolls(char, game_data, npc=npc)
        for spec in specs
        if spec.kind == KIND_AREA and spec.trait_key
    ]

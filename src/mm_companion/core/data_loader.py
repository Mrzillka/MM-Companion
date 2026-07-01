"""Load MM-Companion game data from the bundled data files.

This module is the single entry point the UI uses to obtain rules *content*
(abilities, resistances, skills, advantages, ...). Nothing here implements game
rules; it only parses the JSON in :mod:`mm_companion.data` into typed records so
the UI never hardcodes that content.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from functools import lru_cache
from importlib import resources

DATA_PACKAGE = "mm_companion"
DATA_FILE = "placeholder.json"


@dataclass(frozen=True)
class Field:
    """A free-text descriptive field (character/hero/player name, hair, ...)."""

    key: str
    label: str


@dataclass(frozen=True)
class Characteristic:
    """A trait that is not bought with power points (size, speed, ...).

    ``options`` is non-empty for enumerated characteristics (e.g. Size), which
    the UI renders as a combo box; otherwise the UI uses a free-text field.
    """

    key: str
    label: str
    options: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Ability:
    """A core ability, bought directly with power points."""

    key: str
    name: str


@dataclass(frozen=True)
class Resistance:
    """A defense/resistance, linked to the ability it derives from."""

    key: str
    name: str
    ability: str


@dataclass(frozen=True)
class Skill:
    """A skill and the ability it adds to. ``focused`` skills have no ranks of
    their own; the character instead buys focused instances (e.g. Close Combat:
    Swords)."""

    name: str
    ability: str
    focused: bool


@dataclass(frozen=True)
class Advantage:
    """An advantage. ``ranked`` advantages can be taken at more than one rank."""

    name: str
    type: str
    ranked: bool


@dataclass(frozen=True)
class GameData:
    """The full parsed contents of the game-data file."""

    profile_fields: list[Field]
    characteristics: list[Characteristic]
    abilities: list[Ability]
    resistances: list[Resistance]
    skills: list[Skill]
    advantages: list[Advantage]


def _read_raw() -> dict:
    source = resources.files(DATA_PACKAGE).joinpath("data", DATA_FILE)
    return json.loads(source.read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def load_game_data() -> GameData:
    """Parse and return the bundled game data (cached after first call)."""

    raw = _read_raw()
    return GameData(
        profile_fields=[Field(**f) for f in raw["profile_fields"]],
        characteristics=[
            Characteristic(key=c["key"], label=c["label"], options=list(c.get("options", [])))
            for c in raw["characteristics"]
        ],
        abilities=[Ability(**a) for a in raw["abilities"]],
        resistances=[Resistance(**r) for r in raw["resistances"]],
        skills=[Skill(**s) for s in raw["skills"]],
        advantages=[Advantage(**a) for a in raw["advantages"]],
    )

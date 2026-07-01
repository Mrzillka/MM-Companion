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
    """A free-text descriptive field (character/hero/player name, hair, ...).

    ``primary`` fields are the few identifying ones the UI always shows (name,
    hero name, player); the rest are secondary details the UI may keep in a
    collapsible group.
    """

    key: str
    label: str
    primary: bool = False


@dataclass(frozen=True)
class Characteristic:
    """A trait that is not bought with power points (size, speed, ...).

    ``kind`` selects how the UI renders the value:

    - ``"text"`` — free-text field (the default).
    - ``"choice"`` — one of ``options``, rendered as a combo box.
    - ``"number"`` — an integer spin box bounded by ``minimum``/``maximum``.
    - ``"pool"`` — a calculated *current* value shown beside an editable
      *total* spin box (e.g. power points current / total).

    ``default`` seeds the initial value (an option string, or a number).
    """

    key: str
    label: str
    kind: str = "text"
    options: list[str] = field(default_factory=list)
    default: str | int | None = None
    minimum: int = 0
    maximum: int = 999


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
class Condition:
    """A status condition that can affect a character (dazed, stunned, ...).

    ``description`` is optional summary text the UI may show as a tooltip.
    """

    name: str
    description: str = ""


@dataclass(frozen=True)
class GameData:
    """The full parsed contents of the game-data file."""

    profile_fields: list[Field]
    characteristics: list[Characteristic]
    abilities: list[Ability]
    resistances: list[Resistance]
    skills: list[Skill]
    advantages: list[Advantage]
    conditions: list[Condition]


def _parse_characteristic(c: dict) -> Characteristic:
    options = list(c.get("options", []))
    # Infer a widget kind when not stated: enumerated -> choice, else text.
    kind = c.get("kind") or ("choice" if options else "text")
    return Characteristic(
        key=c["key"],
        label=c["label"],
        kind=kind,
        options=options,
        default=c.get("default"),
        minimum=int(c.get("min", 0)),
        maximum=int(c.get("max", 999)),
    )


def _read_raw() -> dict:
    source = resources.files(DATA_PACKAGE).joinpath("data", DATA_FILE)
    return json.loads(source.read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def load_game_data() -> GameData:
    """Parse and return the bundled game data (cached after first call)."""

    raw = _read_raw()
    return GameData(
        profile_fields=[Field(**f) for f in raw["profile_fields"]],
        characteristics=[_parse_characteristic(c) for c in raw["characteristics"]],
        abilities=[Ability(**a) for a in raw["abilities"]],
        resistances=[Resistance(**r) for r in raw["resistances"]],
        skills=[Skill(**s) for s in raw["skills"]],
        advantages=[Advantage(**a) for a in raw["advantages"]],
        conditions=[Condition(**c) for c in raw.get("conditions", [])],
    )

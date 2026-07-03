"""The mutable per-character state model.

This is the seam the UI has been missing: a single container that owns a
character's chosen values (ability ranks, skill ranks, advantages, conditions,
...), distinct from the frozen *content* records in :mod:`.data_loader`. It is
plain data — deriving totals, costs, and validation lives in :mod:`.rules`, and
nothing here imports PySide6.

The model is JSON-serializable (:meth:`Character.to_dict` /
:meth:`Character.from_dict`) so save/load can hang off it later.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .data_loader import GameData
from .powers import Power


@dataclass
class AdvantageSelection:
    """A chosen advantage, with a rank for rankable advantages (1 otherwise)."""

    name: str
    rank: int = 1


@dataclass
class Character:
    """All per-character state for one character sheet.

    Trait dicts are keyed by the content keys from :class:`GameData`
    (``abilities``/``resistances`` by their ``key``; ``skill_ranks``/``skill_mods``
    by a *row id* — the skill name, or ``"<Skill>: <focus>"`` for a focused
    instance). Missing keys read as ``0``, so the dicts only carry non-default
    values.
    """

    power_level: int = 10
    power_points_total: int = 150
    image_path: str | None = None
    profile: dict[str, str] = field(default_factory=dict)
    characteristics: dict[str, str | int] = field(default_factory=dict)
    abilities: dict[str, int] = field(default_factory=dict)
    resistances: dict[str, int] = field(default_factory=dict)
    skill_ranks: dict[str, int] = field(default_factory=dict)
    skill_mods: dict[str, int] = field(default_factory=dict)
    focuses: dict[str, list[str]] = field(default_factory=dict)
    advantages: list[AdvantageSelection] = field(default_factory=list)
    conditions: set[str] = field(default_factory=set)
    powers: list[Power] = field(default_factory=list)

    @classmethod
    def new_default(cls, game_data: GameData) -> Character:
        """Build a blank character seeded with defaults from ``game_data``.

        Characteristics take their declared defaults; power level and the
        starting power-point budget are pulled from those defaults when present,
        otherwise derived from ``pp_per_level``. Ability/resistance ranks start
        at 0.
        """

        characteristics: dict[str, str | int] = {
            c.key: c.default for c in game_data.characteristics if c.default is not None
        }
        power_level = int(characteristics.get("power_level", 10))
        default_budget = power_level * game_data.costs.power_level.pp_per_level
        power_points_total = int(characteristics.get("power_points", default_budget))
        return cls(
            power_level=power_level,
            power_points_total=power_points_total,
            characteristics=characteristics,
            abilities={a.key: 0 for a in game_data.abilities},
            resistances={r.key: 0 for r in game_data.resistances},
        )

    def to_dict(self) -> dict:
        """Serialize to plain JSON-friendly types (conditions become a sorted list)."""

        return {
            "power_level": self.power_level,
            "power_points_total": self.power_points_total,
            "image_path": self.image_path,
            "profile": dict(self.profile),
            "characteristics": dict(self.characteristics),
            "abilities": dict(self.abilities),
            "resistances": dict(self.resistances),
            "skill_ranks": dict(self.skill_ranks),
            "skill_mods": dict(self.skill_mods),
            "focuses": {k: list(v) for k, v in self.focuses.items()},
            "advantages": [{"name": a.name, "rank": a.rank} for a in self.advantages],
            "conditions": sorted(self.conditions),
            "powers": [p.to_dict() for p in self.powers],
        }

    @classmethod
    def from_dict(cls, raw: dict) -> Character:
        """Rebuild a character from :meth:`to_dict` output (tolerant of missing keys)."""

        return cls(
            power_level=int(raw.get("power_level", 10)),
            power_points_total=int(raw.get("power_points_total", 150)),
            image_path=raw.get("image_path"),
            profile=dict(raw.get("profile", {})),
            characteristics=dict(raw.get("characteristics", {})),
            abilities=dict(raw.get("abilities", {})),
            resistances=dict(raw.get("resistances", {})),
            skill_ranks=dict(raw.get("skill_ranks", {})),
            skill_mods=dict(raw.get("skill_mods", {})),
            focuses={k: list(v) for k, v in raw.get("focuses", {}).items()},
            advantages=[
                AdvantageSelection(name=a["name"], rank=int(a.get("rank", 1)))
                for a in raw.get("advantages", [])
            ],
            conditions=set(raw.get("conditions", [])),
            powers=[Power.from_dict(p) for p in raw.get("powers", [])],
        )

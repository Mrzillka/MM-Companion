"""The assembled-power model (see ``mm-powers-architecture.md``).

Unlike skills and advantages there is no fixed catalog of powers — a player
builds a :class:`Power` out of parts: one or more :class:`PowerEffectInstance`
(a base effect from ``effects.json`` at a chosen rank), each carrying its own
extras and flaws (:class:`ModifierSelection`, referencing ``modifiers.json``).

This is plain data — point costs are derived in :mod:`.rules`, and nothing here
imports PySide6. The model is JSON-serializable (:meth:`Power.to_dict` /
:meth:`Power.from_dict`) so it can be persisted onto a character later.

Arrays (alternate effects) and linked effects are intentionally not modelled
yet; this is the single-power builder foundation.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ModifierSelection:
    """An extra or flaw applied to an effect, by ``modifiers.json`` id.

    ``rank`` is carried for the ranked modifiers; unranked ones leave it at 1.
    Whether it adds or subtracts, and whether it applies per rank or once, comes
    from the referenced :class:`~mm_companion.core.data_loader.Modifier`.
    """

    modifier_id: str
    rank: int = 1

    def to_dict(self) -> dict:
        return {"modifier_id": self.modifier_id, "rank": self.rank}

    @classmethod
    def from_dict(cls, raw: dict) -> ModifierSelection:
        return cls(modifier_id=raw["modifier_id"], rank=int(raw.get("rank", 1)))


@dataclass
class PowerEffectInstance:
    """One effect within a power: a base effect id at a rank, plus modifiers.

    ``config`` holds effect-specific choices (e.g. which trait an Enhanced Trait
    targets); ``descriptors`` are free-text flavor tags. Both are open-ended and
    unused by cost math this pass.
    """

    effect_id: str
    rank: int = 1
    extras: list[ModifierSelection] = field(default_factory=list)
    flaws: list[ModifierSelection] = field(default_factory=list)
    config: dict = field(default_factory=dict)
    descriptors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "effect_id": self.effect_id,
            "rank": self.rank,
            "extras": [m.to_dict() for m in self.extras],
            "flaws": [m.to_dict() for m in self.flaws],
            "config": dict(self.config),
            "descriptors": list(self.descriptors),
        }

    @classmethod
    def from_dict(cls, raw: dict) -> PowerEffectInstance:
        return cls(
            effect_id=raw["effect_id"],
            rank=int(raw.get("rank", 1)),
            extras=[ModifierSelection.from_dict(m) for m in raw.get("extras", [])],
            flaws=[ModifierSelection.from_dict(m) for m in raw.get("flaws", [])],
            config=dict(raw.get("config", {})),
            descriptors=list(raw.get("descriptors", [])),
        )


@dataclass
class Power:
    """A player-assembled power: a titled, described bundle of effects."""

    name: str = ""
    description: str = ""
    descriptors: list[str] = field(default_factory=list)
    effects: list[PowerEffectInstance] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "descriptors": list(self.descriptors),
            "effects": [e.to_dict() for e in self.effects],
        }

    @classmethod
    def from_dict(cls, raw: dict) -> Power:
        return cls(
            name=raw.get("name", ""),
            description=raw.get("description", ""),
            descriptors=list(raw.get("descriptors", [])),
            effects=[PowerEffectInstance.from_dict(e) for e in raw.get("effects", [])],
        )

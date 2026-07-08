"""The assembled-power model (see ``mm-powers-architecture.md``).

Unlike skills and advantages there is no fixed catalog of powers — a player
builds a :class:`Power` out of parts: one or more :class:`PowerEffectInstance`
(a base effect from ``effects.json`` at a chosen rank), each carrying its own
extras and flaws (:class:`ModifierSelection`, referencing ``modifiers.json``).

This is plain data — point costs are derived in :mod:`.rules`, and nothing here
imports PySide6. The model is JSON-serializable (:meth:`Power.to_dict` /
:meth:`Power.from_dict`) so it can be persisted onto a character later.

A multi-effect power has a :data:`Power.structure` describing how its effects
relate (see ``mm-powers-architecture.md`` §4): ``independent`` effects are just
grouped, ``linked`` ones always fire together, and an ``array`` shares one point
pool where only one effect is active at a time. The structure — not per-effect
modifier chips — is the source of truth; :mod:`.rules` reads it to compute the
composite cost and game-term summary.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# How the effects of a multi-effect power relate to one another (§4).
STRUCTURE_INDEPENDENT = "independent"
STRUCTURE_LINKED = "linked"
STRUCTURE_ARRAY = "array"
STRUCTURES = (STRUCTURE_INDEPENDENT, STRUCTURE_LINKED, STRUCTURE_ARRAY)

# The general-pool modifier ids the composite structures correspond to. The
# constructor applies their cost/semantics automatically from ``structure`` (a
# base power plus flat-cost alternates for an array; a +0 bundle for linked), so
# they are *not* stored as per-effect selections — these ids let cost math and
# the game-terms summary look the records up when they need the flat point value.
ALTERNATE_EFFECT_MODIFIER = "alternate_effect"
LINKED_MODIFIER = "linked"


@dataclass
class ModifierSelection:
    """An extra or flaw applied to an effect, by ``modifiers.json`` id.

    ``rank`` is carried for the ranked modifiers; unranked ones leave it at 1.
    Whether it adds or subtracts, and whether it applies per rank or once, comes
    from the referenced :class:`~mm_companion.core.data_loader.Modifier`.

    ``config`` holds a modifier's own choices for the few extras/flaws that need
    them (a Removable tier, a Side Effect's backfire text and always/on-failure
    toggle, a Triggered/Limited condition — see ``mm-powers-ui-design.md`` §4). It
    is empty for the plain modifiers, and a modifier that discounts by tier
    (Removable, Side Effect) reads its value here rather than from a fixed cost.
    """

    modifier_id: str
    rank: int = 1
    config: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        data = {"modifier_id": self.modifier_id, "rank": self.rank}
        if self.config:
            data["config"] = dict(self.config)
        return data

    @classmethod
    def from_dict(cls, raw: dict) -> ModifierSelection:
        return cls(
            modifier_id=raw["modifier_id"],
            rank=int(raw.get("rank", 1)),
            config=dict(raw.get("config", {})),
        )


@dataclass
class PowerEffectInstance:
    """One effect within a power: a base effect id at a rank, plus modifiers.

    ``config`` holds effect-specific choices (e.g. which trait an Enhanced Trait
    targets); ``descriptors`` are free-text flavor tags. Both are open-ended and
    unused by cost math this pass.

    ``toggled_on`` and ``suppressed`` are the effect's *runtime* state (see
    ``mm-powers-architecture.md`` §5-7), separate from the point build: a
    Sustained/Continuous effect the player has switched off is ``toggled_on=False``,
    and ``suppressed`` is a transient Nullify flag. Both feed
    :func:`mm_companion.core.rules.effect_is_active`; they default to the active
    state so a freshly built or older-format effect reads as on.

    ``attack_skill`` optionally links *this effect's* attack to a Close/Ranged Combat
    focus row on the wielder (a row id like ``"Close Combat::Blades"``, empty for
    none). When set, that focus's total *replaces* the character's bare Attack for
    this effect's attack roll and its Attack PL cap (see
    :func:`mm_companion.core.rules.effect_attack_skill_bonus`).
    """

    effect_id: str
    rank: int = 1
    extras: list[ModifierSelection] = field(default_factory=list)
    flaws: list[ModifierSelection] = field(default_factory=list)
    config: dict = field(default_factory=dict)
    descriptors: list[str] = field(default_factory=list)
    toggled_on: bool = True
    suppressed: bool = False
    attack_skill: str = ""

    def to_dict(self) -> dict:
        return {
            "effect_id": self.effect_id,
            "rank": self.rank,
            "extras": [m.to_dict() for m in self.extras],
            "flaws": [m.to_dict() for m in self.flaws],
            "config": dict(self.config),
            "descriptors": list(self.descriptors),
            "toggled_on": self.toggled_on,
            "suppressed": self.suppressed,
            "attack_skill": self.attack_skill,
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
            toggled_on=bool(raw.get("toggled_on", True)),
            suppressed=bool(raw.get("suppressed", False)),
            attack_skill=raw.get("attack_skill", ""),
        )


@dataclass
class Power:
    """A player-assembled power: a titled, described bundle of effects.

    ``structure`` (one of :data:`STRUCTURES`) governs how the effects combine and
    is only meaningful with two or more of them: ``independent`` (the default) and
    ``linked`` both sum their effects' costs, while ``array`` pays only for the
    costliest effect plus a flat point per alternate.

    ``activated`` and ``item_present`` are whole-power *runtime* state (§7): the
    Activation gate needs ``activated``, and a Removable gate's bonus applies only
    while ``item_present``. Both default to the active state (see
    :func:`mm_companion.core.rules.effect_is_active`).

    An attack-skill link is per-effect now (see
    :attr:`PowerEffectInstance.attack_skill`), not whole-power.
    """

    name: str = ""
    description: str = ""
    descriptors: list[str] = field(default_factory=list)
    effects: list[PowerEffectInstance] = field(default_factory=list)
    structure: str = STRUCTURE_INDEPENDENT
    activated: bool = True
    item_present: bool = True

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "descriptors": list(self.descriptors),
            "effects": [e.to_dict() for e in self.effects],
            "structure": self.structure,
            "activated": self.activated,
            "item_present": self.item_present,
        }

    @classmethod
    def from_dict(cls, raw: dict) -> Power:
        structure = raw.get("structure", STRUCTURE_INDEPENDENT)
        effects = [PowerEffectInstance.from_dict(e) for e in raw.get("effects", [])]
        # Migrate a legacy whole-power ``attack_skill`` (from before the link moved
        # per-effect) onto every effect that doesn't already carry its own.
        legacy = raw.get("attack_skill", "")
        if legacy:
            for effect in effects:
                if not effect.attack_skill:
                    effect.attack_skill = legacy
        return cls(
            name=raw.get("name", ""),
            description=raw.get("description", ""),
            descriptors=list(raw.get("descriptors", [])),
            effects=effects,
            structure=structure if structure in STRUCTURES else STRUCTURE_INDEPENDENT,
            activated=bool(raw.get("activated", True)),
            item_present=bool(raw.get("item_present", True)),
        )

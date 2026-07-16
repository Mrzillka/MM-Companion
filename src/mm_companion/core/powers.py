"""The assembled-power model (see ``docs/mm-powers-architecture.md``).

Unlike skills and advantages there is no fixed catalog of powers — a player
builds a :class:`Power` out of parts: one or more :class:`PowerEffectInstance`
(a base effect from ``effects.json`` at a chosen rank), each carrying its own
extras and flaws (:class:`ModifierSelection`, referencing ``modifiers.json``).

This is plain data — point costs are derived in :mod:`.rules`, and nothing here
imports PySide6. The model is JSON-serializable (:meth:`Power.to_dict` /
:meth:`Power.from_dict`) so it can be persisted onto a character later.

A multi-effect power has a :data:`Power.structure` describing how its effects
relate (see ``docs/mm-powers-architecture.md`` §4): ``independent`` effects are just
grouped, ``linked`` ones always fire together, and an ``array`` shares one point
pool where only one effect is active at a time. The structure — not per-effect
modifier chips — is the source of truth; :mod:`.rules` reads it to compute the
composite cost and game-term summary.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from uuid import uuid4

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
    toggle, a Triggered/Limited condition — see ``docs/mm-powers-ui-design.md`` §4). It
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
    ``docs/mm-powers-architecture.md`` §5-7), separate from the point build: a
    Sustained/Continuous effect the player has switched off is ``toggled_on=False``,
    and ``suppressed`` is a transient Nullify flag. Both feed
    :func:`mm_companion.core.rules.effect_is_active`. Runtime state is **not
    persisted** — it is deliberately left out of :meth:`to_dict`, so a loaded
    character always comes up in its default all-active state.

    ``attack_skill`` optionally links *this effect's* attack to a Close/Ranged Combat
    focus row on the wielder (a row id like ``"Close Combat::Blades"``, empty for
    none). When set, that focus's total *replaces* the character's bare Attack for
    this effect's attack roll and its Attack PL cap (see
    :func:`mm_companion.core.rules.effect_attack_skill_bonus`).

    ``overrides`` holds the constructor's **Dev-mode / homerule** edits to this
    effect's derived game-terms: a mapping ``field_key -> {"value", "order",
    "label"?}``. ``field_key`` is a standard game-term field (``effect_type``,
    ``range``, ``action``, ``duration``, ``check``, ``resistance``), an effect
    readout key, or a fresh ``custom_N`` key for a player-added row; ``order`` is
    ``"before"`` (applied to the base so modifiers still layer on top) or ``"after"``
    (applied last, so the manual value wins). ``label`` is stored only for a custom
    row. Unlike the runtime flags below, this is *build* state, so it is persisted.
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
    overrides: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        data = {
            "effect_id": self.effect_id,
            "rank": self.rank,
            "extras": [m.to_dict() for m in self.extras],
            "flaws": [m.to_dict() for m in self.flaws],
            "config": dict(self.config),
            "descriptors": list(self.descriptors),
            "attack_skill": self.attack_skill,
        }
        if self.overrides:
            data["overrides"] = {k: dict(v) for k, v in self.overrides.items()}
        return data

    @classmethod
    def from_dict(cls, raw: dict) -> PowerEffectInstance:
        return cls(
            effect_id=raw["effect_id"],
            rank=int(raw.get("rank", 1)),
            extras=[ModifierSelection.from_dict(m) for m in raw.get("extras", [])],
            flaws=[ModifierSelection.from_dict(m) for m in raw.get("flaws", [])],
            config=dict(raw.get("config", {})),
            descriptors=list(raw.get("descriptors", [])),
            attack_skill=raw.get("attack_skill", ""),
            overrides={k: dict(v) for k, v in raw.get("overrides", {}).items()},
        )


@dataclass
class Power:
    """A player-assembled power: a titled, described bundle of effects.

    ``structure`` (one of :data:`STRUCTURES`) governs how *this power's own* effects
    combine and is only meaningful with two or more of them: ``independent`` (the
    default) and ``linked`` both sum their effects' costs, while ``array`` pays only
    for the costliest effect plus a flat point per alternate.

    Separately, whole powers relate to *each other* (see
    ``docs/mm-powers-architecture.md`` §4): ``linked_with`` names other powers that switch
    on/off together with this one, and ``alternate_of`` makes this power an Alternate
    Effect of another — sharing one point pool, so only its base pays full and each
    alternate costs a flat point (:func:`mm_companion.core.rules.power_display_cost`).
    Both reference the target power by its stable :attr:`id`, not its (mutable) name.

    ``activated`` and ``item_present`` are whole-power *runtime* state (§7): the
    Activation gate needs ``activated``, and a Removable gate's bonus applies only
    while ``item_present``. ``array_active`` is runtime too — for an array member,
    whether it is the currently-selected active one (only one member of an array is
    active at a time). All three default to the active state (see
    :func:`mm_companion.core.rules.effect_is_active`), so a standalone power and an
    array's base are unaffected. Like the per-effect runtime flags, none of these are
    **persisted** — they are left out of :meth:`to_dict`, so a loaded character comes
    up in its default all-active state.

    An attack-skill link is per-effect now (see
    :attr:`PowerEffectInstance.attack_skill`), not whole-power.

    ``cost_override`` is a Dev-mode / homerule edit: when set it *replaces* the
    power's whole computed point total (see
    :func:`mm_companion.core.rules.power_total_cost`), so it flows into the
    character's power-point spend. ``None`` (the default) leaves the cost fully
    derived. It is *build* state, so it is persisted.
    """

    name: str = ""
    description: str = ""
    descriptors: list[str] = field(default_factory=list)
    effects: list[PowerEffectInstance] = field(default_factory=list)
    structure: str = STRUCTURE_INDEPENDENT
    id: str = field(default_factory=lambda: uuid4().hex)
    linked_with: list[str] = field(default_factory=list)
    alternate_of: str = ""
    activated: bool = True
    item_present: bool = True
    array_active: bool = True
    cost_override: int | None = None

    def to_dict(self) -> dict:
        data = {
            "name": self.name,
            "description": self.description,
            "descriptors": list(self.descriptors),
            "effects": [e.to_dict() for e in self.effects],
            "structure": self.structure,
            "id": self.id,
            "linked_with": list(self.linked_with),
            "alternate_of": self.alternate_of,
        }
        if self.cost_override is not None:
            data["cost_override"] = self.cost_override
        return data

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
        # A power saved before cross-power relationships existed has no id; mint one
        # so it can still be referenced. Older powers carry no references, so nothing
        # dangles from the fresh id.
        power_id = raw.get("id") or uuid4().hex
        raw_cost = raw.get("cost_override")
        return cls(
            name=raw.get("name", ""),
            description=raw.get("description", ""),
            descriptors=list(raw.get("descriptors", [])),
            effects=effects,
            structure=structure if structure in STRUCTURES else STRUCTURE_INDEPENDENT,
            id=power_id,
            linked_with=list(raw.get("linked_with", [])),
            alternate_of=raw.get("alternate_of", ""),
            cost_override=None if raw_cost is None else int(raw_cost),
        )


def power_is_homerule(power: Power) -> bool:
    """Whether a power carries any Dev-mode / homerule override.

    True when the whole-power :attr:`Power.cost_override` is set or any of its
    effects carry a game-term :attr:`PowerEffectInstance.overrides` entry — the sole
    signal the UI uses to badge a card as homerule, so there is no separate flag to
    keep in sync with the overrides themselves.
    """

    return power.cost_override is not None or any(e.overrides for e in power.effects)


@dataclass
class PowerGroup:
    """A group node bundling whole powers (or nested sub-groups) on the sheet.

    Unlike :attr:`Power.structure` (which governs how a *single* power's own effects
    combine), a group relates *whole cards* to one another and can nest arbitrarily
    (a group inside a group), so a character's ``powers`` is a tree of
    :data:`PowerNode` — leaf :class:`Power` cards and :class:`PowerGroup` containers.
    It supersedes the flat cross-power ``alternate_of`` / ``linked_with`` references
    (which are migrated into groups on load; see
    :func:`mm_companion.core.character._migrate_flat_relations`).

    ``mode`` is one of :data:`STRUCTURES`: ``independent`` and ``linked`` sum their
    children's costs; ``array`` pays the costliest child in full plus a flat point per
    other child (only one active at a time). Cost recursion lives in
    :func:`mm_companion.core.rules.node_cost`.

    ``active_child_id`` is *runtime* state (like :attr:`Power.array_active`): for an
    ``array`` group it names the currently-selected live child; empty means the first
    child. :func:`mm_companion.core.rules.power_trait_bonuses` descends only into the
    active branch so an inactive array member's bonuses drop off the sheet. Being
    runtime, it is **not persisted** — it is left out of :meth:`to_dict`, so a loaded
    array defaults to its first child.

    ``name`` is an optional player-given title for the group; when empty the UI falls
    back to a label derived from the :attr:`mode`.
    """

    mode: str = STRUCTURE_INDEPENDENT
    children: list[PowerNode] = field(default_factory=list)
    id: str = field(default_factory=lambda: uuid4().hex)
    active_child_id: str = ""
    name: str = ""

    def to_dict(self) -> dict:
        return {
            "kind": "group",
            "mode": self.mode,
            "children": [c.to_dict() for c in self.children],
            "id": self.id,
            "name": self.name,
        }

    @classmethod
    def from_dict(cls, raw: dict) -> PowerGroup:
        mode = raw.get("mode", STRUCTURE_INDEPENDENT)
        return cls(
            mode=mode if mode in STRUCTURES else STRUCTURE_INDEPENDENT,
            children=[node_from_dict(c) for c in raw.get("children", [])],
            id=raw.get("id") or uuid4().hex,
            name=raw.get("name", ""),
        )


# A node in the character's powers tree: a leaf power card or a nested group.
PowerNode = Power | PowerGroup


def node_from_dict(raw: dict) -> PowerNode:
    """Deserialize one powers-tree node, dispatching group vs leaf power.

    A group dict carries ``"kind": "group"`` (or, for forward tolerance, a
    ``"children"`` list); anything else is a leaf :class:`Power`. Bare power dicts
    from before groups existed have neither key and load as leaves unchanged.
    """

    if raw.get("kind") == "group" or "children" in raw:
        return PowerGroup.from_dict(raw)
    return Power.from_dict(raw)

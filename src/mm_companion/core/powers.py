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

# Which side of the Power Level trade-off a hard-capped effect protects (see
# :attr:`PowerEffectInstance.pl_cap`). ``PL_CAP_EFFECT`` keeps the effect rank and lets
# the attack bonus fall; ``PL_CAP_ATTACK`` keeps the attack bonus and lets the rank fall.
PL_CAP_EFFECT = "effect"
PL_CAP_ATTACK = "attack"
PL_CAPS = (PL_CAP_EFFECT, PL_CAP_ATTACK)


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

    ``applies_from`` / ``applies_to`` are the **rank band** the modifier covers: the
    rules let one apply to part of an effect rather than all of it, so a hero can carry
    a Blast 12 whose top four ranks alone are Tiring and routinely fire the other eight
    for free. ``0``/``0`` — the default — means *every* rank, which is what every
    modifier ever saved before this says, so nothing is migrated and an untouched
    selection serializes byte-for-byte as it did.

    A band only ever changes what a **per-rank** modifier costs. A flat one is charged
    once whatever it covers, so the constructor offers no band for it and the cost math
    ignores one that somehow got stored (see
    :func:`mm_companion.core.rules.effect_total_cost`).
    """

    modifier_id: str
    rank: int = 1
    config: dict = field(default_factory=dict)
    applies_from: int = 0
    applies_to: int = 0

    def to_dict(self) -> dict:
        data = {"modifier_id": self.modifier_id, "rank": self.rank}
        if self.config:
            data["config"] = dict(self.config)
        # Written only when the band says something — see the class docstring.
        if self.applies_from or self.applies_to:
            data["applies_from"] = self.applies_from
            data["applies_to"] = self.applies_to
        return data

    @classmethod
    def from_dict(cls, raw: dict) -> ModifierSelection:
        return cls(
            modifier_id=raw["modifier_id"],
            rank=int(raw.get("rank", 1)),
            config=dict(raw.get("config", {})),
            applies_from=int(raw.get("applies_from", 0)),
            applies_to=int(raw.get("applies_to", 0)),
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
    :func:`mm_companion.core.rules.effect_is_active`. Runtime state **is** persisted:
    a character switched off between sessions comes back switched off, so the sheet
    reopens the way it was left. It is written only when it differs from the
    all-active default, so a save from before this is byte-for-byte unchanged.

    ``attack_skill`` optionally links *this effect's* attack to a Close/Ranged Combat
    focus row on the wielder (a row id like ``"Close Combat::Blades"``, empty for
    none). When set, that focus's total *replaces* the character's bare Attack for
    this effect's attack roll and its Attack PL cap (see
    :func:`mm_companion.core.rules.effect_attack_skill_bonus`).

    ``label`` names *this* effect where the base effect's own name is not enough —
    a vehicle's "Cannon" and "Heavy machine gun" are both Damage, and a dice footer
    listing "Damage" twice says nothing. Empty for the ordinary case, and it is
    cosmetic: nothing about cost, terms or rolls changes, only what they are called
    (:func:`mm_companion.core.rules.power_rolls` prefixes with it).

    ``size_scales_damage`` is the Power Constructor's *Extended settings* switch: while
    it is on — and it is on by default — the wielder's size raises this effect's
    effective rank by the Size Table's damage column, so a giant hits harder
    (:func:`mm_companion.core.rules.effect_size_rank_shift`). It is off for the giant's
    *laser*, which is why this is a switch and not a rule. Only an effect that forces a
    resistance is affected either way. It lives on the effect rather than the power for
    the reason ``attack_skill`` does — that is the level it applies at — even though
    the constructor drives every effect in a power from one checkbox.

    ``current_rank`` is *runtime* too, and the one runtime flag that is a number: how
    far a rank-dialled effect is currently turned up. ``None`` — the default — means
    "all the way", so an effect nobody has dialled behaves exactly as it always did,
    and an effect whose bought rank is later edited stays dialled where it was rather
    than to a rank it no longer has (:func:`mm_companion.core.rules.effect_current_rank`
    clamps). Only the size effects read it today: Growth 3 is a ladder of three steps,
    not a single leap, and the card carries a button per step (see
    :func:`mm_companion.core.rules.size_steps`). Like the flags above it is persisted,
    and it is the one that made the case: a Growth held at Large is a decision about
    the character, and reopening the sheet at Gargantuan silently changed four of its
    numbers.

    ``pl_cap`` is the *Extended settings* **hard Power Level cap**: empty (the default)
    leaves the cap a warning, as it has always been, while ``"effect"`` and ``"attack"``
    make it bite. A capped effect can never resolve above ``attack + rank = 2 × PL``:
    when a boost pushes it over, ``"effect"`` keeps the rank and lowers the attack bonus
    and ``"attack"`` keeps the attack bonus and lowers the rank
    (:func:`mm_companion.core.rules.effect_pl_cap_shift`). Unlike the soft warning the
    hard cap measures against the *unshifted* ``2 × PL``, which is what a player asking
    for a hard cap is asking for: a power that cannot exceed the table's limit however
    large its wielder grows. It changes no point cost — a capped power is worth what it
    was bought at — and it lives on the effect rather than the power for the reason
    ``attack_skill`` and ``size_scales_damage`` do, though the constructor drives every
    effect in a power from one checkbox.

    ``rank_dial`` puts a **rank slider** on the sheet card, so an effect bought at 10 can
    be used at 5 in play. It is a build decision (whether the control exists); how far
    the dial is turned is ``current_rank`` below. The size effects get a dial without
    asking, since a Growth is a ladder whether or not anyone ticked a box.

    ``dynamic`` marks this effect a **Dynamic** member of its power's ``array``
    structure (p101). It is *build* state: a Dynamic alternate costs 2 points instead
    of 1 because it shares the array's point pool and runs alongside the array's other
    Dynamic members at reduced effectiveness, rather than being mutually exclusive with
    them; on the array's *base* effect it instead costs one Alternate Effect rank. It
    means nothing outside an array and is priced by
    :func:`mm_companion.core.rules.power_gross_cost`. Written only when set, so a power
    saved before this is byte-for-byte what it was and still costs what it did.

    ``overrides`` holds the constructor's **Dev-mode / homerule** edits to this
    effect's derived game-terms: a mapping ``field_key -> {"value", "order",
    "label"?}``. ``field_key`` is a standard game-term field (``effect_type``,
    ``range``, ``action``, ``duration``, ``check``, ``resistance``), an effect
    readout key, or a fresh ``custom_N`` key for a player-added row; ``order`` is
    ``"before"`` (applied to the base so modifiers still layer on top) or ``"after"``
    (applied last, so the manual value wins). ``label`` is stored only for a custom
    row. This is *build* state rather than runtime, but both are persisted now.
    """

    effect_id: str
    label: str = ""
    rank: int = 1
    extras: list[ModifierSelection] = field(default_factory=list)
    flaws: list[ModifierSelection] = field(default_factory=list)
    config: dict = field(default_factory=dict)
    descriptors: list[str] = field(default_factory=list)
    toggled_on: bool = True
    suppressed: bool = False
    attack_skill: str = ""
    size_scales_damage: bool = True
    pl_cap: str = ""
    rank_dial: bool = False
    current_rank: int | None = None
    dynamic: bool = False
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
        # Written only when it says something, so an existing power's entry is
        # byte-for-byte what it was. That covers the runtime flags too: an effect
        # nobody has switched off or dialled down writes none of them, so a save from
        # before runtime was persisted is unchanged and still reads as all-active.
        if self.label:
            data["label"] = self.label
        if not self.size_scales_damage:
            data["size_scales_damage"] = False
        if self.pl_cap:
            data["pl_cap"] = self.pl_cap
        if self.rank_dial:
            data["rank_dial"] = True
        if not self.toggled_on:
            data["toggled_on"] = False
        if self.suppressed:
            data["suppressed"] = True
        if self.current_rank is not None:
            data["current_rank"] = self.current_rank
        if self.dynamic:
            data["dynamic"] = True
        if self.overrides:
            data["overrides"] = {k: dict(v) for k, v in self.overrides.items()}
        return data

    @classmethod
    def from_dict(cls, raw: dict) -> PowerEffectInstance:
        current = raw.get("current_rank")
        return cls(
            effect_id=raw["effect_id"],
            label=str(raw.get("label", "")),
            rank=int(raw.get("rank", 1)),
            extras=[ModifierSelection.from_dict(m) for m in raw.get("extras", [])],
            flaws=[ModifierSelection.from_dict(m) for m in raw.get("flaws", [])],
            config=dict(raw.get("config", {})),
            descriptors=list(raw.get("descriptors", [])),
            attack_skill=raw.get("attack_skill", ""),
            size_scales_damage=bool(raw.get("size_scales_damage", True)),
            pl_cap=str(raw.get("pl_cap", "")) if raw.get("pl_cap") in PL_CAPS else "",
            rank_dial=bool(raw.get("rank_dial", False)),
            toggled_on=bool(raw.get("toggled_on", True)),
            suppressed=bool(raw.get("suppressed", False)),
            current_rank=None if current is None else int(current),
            dynamic=bool(raw.get("dynamic", False)),
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
    array's base are unaffected. Like the per-effect runtime flags they are persisted,
    and written only when switched off, so a power nobody has touched adds nothing to
    the file and an older save still loads all-active.

    ``dynamic`` is the whole-power twin of :attr:`PowerEffectInstance.dynamic`, for
    when the array is a :class:`PowerGroup` of whole powers rather than one power's own
    effects: it makes this card a **Dynamic** member of its parent array, costing 2
    points as an alternate (or one Alternate Effect rank as the array's base) in
    exchange for sharing the pool with the other Dynamic members instead of switching
    them off. Build state, and written only when set.

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
    dynamic: bool = False
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
        if self.dynamic:
            data["dynamic"] = True
        if self.cost_override is not None:
            data["cost_override"] = self.cost_override
        # The runtime switches, written only when off — see the class docstring.
        for key, value in (
            ("activated", self.activated),
            ("item_present", self.item_present),
            ("array_active", self.array_active),
        ):
            if not value:
                data[key] = False
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
            activated=bool(raw.get("activated", True)),
            item_present=bool(raw.get("item_present", True)),
            array_active=bool(raw.get("array_active", True)),
            dynamic=bool(raw.get("dynamic", False)),
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
    active branch so an inactive array member's bonuses drop off the sheet. It is
    persisted with the rest of the runtime state, written only once a child has
    actually been picked, so a group saved before this loads on its first child as it
    always did.

    ``dynamic`` marks *this group* a Dynamic member of the array it is nested in — the
    same build flag :attr:`Power.dynamic` carries, so an array's member can be a whole
    sub-group and still be priced as one. It says nothing about this group's own
    children; each of those carries its own.

    ``name`` is an optional player-given title for the group; when empty the UI falls
    back to a label derived from the :attr:`mode`.
    """

    mode: str = STRUCTURE_INDEPENDENT
    children: list[PowerNode] = field(default_factory=list)
    id: str = field(default_factory=lambda: uuid4().hex)
    active_child_id: str = ""
    name: str = ""
    dynamic: bool = False

    def to_dict(self) -> dict:
        data = {
            "kind": "group",
            "mode": self.mode,
            "children": [c.to_dict() for c in self.children],
            "id": self.id,
            "name": self.name,
        }
        if self.active_child_id:
            data["active_child_id"] = self.active_child_id
        if self.dynamic:
            data["dynamic"] = True
        return data

    @classmethod
    def from_dict(cls, raw: dict) -> PowerGroup:
        mode = raw.get("mode", STRUCTURE_INDEPENDENT)
        return cls(
            mode=mode if mode in STRUCTURES else STRUCTURE_INDEPENDENT,
            children=[node_from_dict(c) for c in raw.get("children", [])],
            id=raw.get("id") or uuid4().hex,
            active_child_id=str(raw.get("active_child_id", "")),
            name=raw.get("name", ""),
            dynamic=bool(raw.get("dynamic", False)),
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

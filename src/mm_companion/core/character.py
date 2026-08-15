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

from dataclasses import dataclass, field, fields

from .data_loader import GameData
from .equipment import EquipmentItem
from .powers import STRUCTURE_ARRAY, STRUCTURE_LINKED, Power, PowerGroup, PowerNode, node_from_dict


@dataclass
class AdvantageSelection:
    """A chosen advantage, with a rank for rankable advantages (1 otherwise).

    ``parameter`` carries a per-selection choice an advantage needs (e.g. Alternate
    Initiative's mental ability key); ``""`` for advantages that take no parameter.
    """

    name: str
    rank: int = 1
    parameter: str = ""


@dataclass
class Complication:
    """One narrative complication — a name plus a free-form multiline description.

    Complications are story hooks (Motivation, enemy, secret, …) that earn hero
    points in play; they carry no point cost, so they are plain per-character text
    with no rules math.
    """

    name: str = ""
    description: str = ""

    def to_dict(self) -> dict:
        return {"name": self.name, "description": self.description}

    @classmethod
    def from_dict(cls, raw: dict) -> Complication:
        return cls(name=raw.get("name", ""), description=raw.get("description", ""))


@dataclass
class NotesState:
    """Which notes one Notes block on this character's sheet has open.

    The *contents* half of the Notes block (see :mod:`..notes`): the markdown
    itself lives in the workspace ``notes/`` dir as ordinary ``.md`` files, and a
    character records only which of them its sheet has open and which tab was
    focused. So a note can be open on two characters' sheets at once, and this
    stays a list of references rather than a copy of anybody's text.

    Note that *where* a Notes block sits is not here — that is in the shared
    ``layout`` setting with every other block's position. This is keyed by block
    key, so a sheet with two Notes blocks has two entries.
    """

    files: list[str] = field(default_factory=list)  # note refs, in tab order
    active: str = ""  # the focused tab, "" for the first

    def to_dict(self) -> dict:
        return {
            "files": list(self.files),
            **({"active": self.active} if self.active else {}),
        }

    @classmethod
    def from_dict(cls, raw: object) -> NotesState:
        if not isinstance(raw, dict):
            return cls()
        files = raw.get("files", [])
        return cls(
            files=[str(ref) for ref in files] if isinstance(files, list) else [],
            active=str(raw.get("active", "")),
        )


@dataclass
class AppliedCondition:
    """One condition currently on a character — an entity in the condition tracker.

    The character's condition state is the *flattened* effect set with provenance
    (``docs/mm-conditions-design.md`` §3): an umbrella like Incapacitated is stored as the
    umbrella itself plus a member row per bundled condition, each member carrying
    ``provenance`` = the umbrella's id so removing the umbrella removes its members.

    ``condition_id`` keys the catalog record; ``parameter`` is the chosen subject
    (trait/sense/descriptor/controller) folded into the display name (§6); ``count``
    is the stacking-instance count (1 for the non-stacking majority, >1 only for
    Hit, §5); ``provenance`` is ``None`` for a directly applied condition.
    """

    condition_id: str
    parameter: str | None = None
    count: int = 1
    provenance: str | None = None

    def to_dict(self) -> dict:
        """Serialize, omitting defaulted fields to keep saves compact."""

        data: dict = {"id": self.condition_id}
        if self.parameter is not None:
            data["parameter"] = self.parameter
        if self.count != 1:
            data["count"] = self.count
        if self.provenance is not None:
            data["provenance"] = self.provenance
        return data

    @classmethod
    def from_dict(cls, raw: dict) -> AppliedCondition:
        return cls(
            condition_id=raw["id"],
            parameter=raw.get("parameter"),
            count=int(raw.get("count", 1)),
            provenance=raw.get("provenance"),
        )


@dataclass
class Character:
    """All per-character state for one character sheet.

    Trait dicts are keyed by the content keys from :class:`GameData`
    (``abilities``/``resistances`` by their ``key``; ``skill_ranks`` by a *row id* —
    the skill name, ``"<Skill>::<focus>"`` for a focused instance, or
    ``"<Skill>::spec::<name>"`` for a specialized (half-cost) rank pool). Missing keys
    read as ``0``, so the dicts only carry non-default values.

    Only *bought* ranks are stored. A skill's outside bonuses (from powers and
    advantages) are derived, not state — see :func:`~..rules.skill_bonus`.

    ``focuses`` maps a focused skill's name to its chosen focuses; ``specializations``
    maps *any* skill's name to its narrow specialized pools. Both drive which extra
    rows the sheet renders; the ranks themselves live in ``skill_ranks``.
    """

    power_level: int = 10
    power_points_total: int = 150
    image_path: str | None = None
    profile: dict[str, str] = field(default_factory=dict)
    characteristics: dict[str, str | int] = field(default_factory=dict)
    abilities: dict[str, int] = field(default_factory=dict)
    resistances: dict[str, int] = field(default_factory=dict)
    skill_ranks: dict[str, int] = field(default_factory=dict)
    focuses: dict[str, list[str]] = field(default_factory=dict)
    specializations: dict[str, list[str]] = field(default_factory=dict)
    #: The player's chosen order for the Skills block, by skill name. Empty means the
    #: ruleset's own order (``GameData.skills``); names it does not list trail the ones
    #: it does. A name it lists that the ruleset no longer has is **kept**, not pruned,
    #: so a skill from a mod that is off today returns to where the player put it.
    skill_order: list[str] = field(default_factory=list)
    #: Skills the player has taken off the sheet, by name. Display state, not a build
    #: fact — but removing one *does* drop what was bought on it (the block says so
    #: first), and the block's reset button restores the rows, not the ranks.
    hidden_skills: list[str] = field(default_factory=list)
    advantages: list[AdvantageSelection] = field(default_factory=list)
    complications: list[Complication] = field(default_factory=list)
    conditions: list[AppliedCondition] = field(default_factory=list)
    powers: list[PowerNode] = field(default_factory=list)
    #: Gear, in its own collection and its own currency. Deliberately *not* folded into
    #: ``powers``: equipment is not a power (``docs/mm-equipment-design.md`` §1), it is
    #: bought with Equipment Points rather than Power Points, and a character built
    #: entirely from gear still answers "no powers". See :mod:`..equipment`.
    equipment: list[EquipmentItem] = field(default_factory=list)
    #: The player's chosen order for the Equipment block's automatic groups, by
    #: category id. Empty means the ruleset's own order
    #: (``GameData.equipment_categories``); ids it does not name trail the ones it does.
    equipment_group_order: list[str] = field(default_factory=list)
    #: Homebrew point-cost overrides for the non-power trait rates, keyed by the
    #: ``TraitCosts`` field names plus ``"pp_per_level"``. Only rates the player has
    #: changed from the ruleset default are stored, so a stock build carries an empty
    #: dict. Applied by :func:`~..rules.effective_trait_costs` /
    #: :func:`~..rules.effective_pp_per_level`; a non-default rate marks the character
    #: homebrew (see :func:`~..rules.has_cost_overrides`).
    cost_overrides: dict[str, int] = field(default_factory=dict)
    #: Per-item homebrew cost rates for individual traits, so one ability / resistance /
    #: skill can be priced away from its category rate. Nested ``category -> item key ->
    #: rate`` where category is ``"abilities"`` / ``"resistances"`` / ``"skills"``, the
    #: item key is the ability/resistance ``key`` (or the skill ``name``), and the rate
    #: means PP-per-rank for abilities/resistances or ranks-per-PP for skills. Only items
    #: changed from the applicable category default are stored (see
    #: :func:`~..rules.ability_cost_rate`, :func:`~..rules.resistance_cost_rate`,
    #: :func:`~..rules.skill_cost_rate`, :func:`~..rules.has_cost_overrides`).
    item_cost_overrides: dict[str, dict[str, int]] = field(default_factory=dict)
    #: What each Notes block on the sheet has open, keyed by block key (``"notes"``,
    #: ``"notes#2"``, …). Which notes are open **is** an edit — it is undoable and it
    #: dirties the sheet — while the markdown inside them is not: a note's text
    #: autosaves to its own file, the same split the portrait makes between
    #: ``image_path`` and the pixels. See :class:`NotesState` and :mod:`..notes`.
    notes: dict[str, NotesState] = field(default_factory=dict)

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
        """Serialize to plain JSON-friendly types (conditions become a list of dicts)."""

        return {
            "power_level": self.power_level,
            "power_points_total": self.power_points_total,
            "image_path": self.image_path,
            "profile": dict(self.profile),
            "characteristics": dict(self.characteristics),
            "abilities": dict(self.abilities),
            "resistances": dict(self.resistances),
            "skill_ranks": dict(self.skill_ranks),
            "focuses": {k: list(v) for k, v in self.focuses.items()},
            "specializations": {k: list(v) for k, v in self.specializations.items()},
            "advantages": [
                {
                    "name": a.name,
                    "rank": a.rank,
                    **({"parameter": a.parameter} if a.parameter else {}),
                }
                for a in self.advantages
            ],
            # Both omitted while empty, like equipment_group_order below, so a save
            # written before the Skills block could be reordered round-trips unchanged.
            **({"skill_order": list(self.skill_order)} if self.skill_order else {}),
            **({"hidden_skills": list(self.hidden_skills)} if self.hidden_skills else {}),
            "complications": [c.to_dict() for c in self.complications],
            "conditions": [c.to_dict() for c in self.conditions],
            "powers": [p.to_dict() for p in self.powers],
            # Omitted entirely when the character owns no gear, so a save written
            # before equipment existed round-trips byte-for-byte.
            **({"equipment": [e.to_dict() for e in self.equipment]} if self.equipment else {}),
            **(
                {"equipment_group_order": list(self.equipment_group_order)}
                if self.equipment_group_order
                else {}
            ),
            **({"cost_overrides": dict(self.cost_overrides)} if self.cost_overrides else {}),
            **(
                {
                    "item_cost_overrides": {
                        cat: dict(items) for cat, items in self.item_cost_overrides.items() if items
                    }
                }
                if any(self.item_cost_overrides.values())
                else {}
            ),
            **(
                {
                    "notes": {
                        key: state.to_dict() for key, state in self.notes.items() if state.files
                    }
                }
                if any(state.files for state in self.notes.values())
                else {}
            ),
        }

    @classmethod
    def from_dict(cls, raw: dict) -> Character:
        """Rebuild a character from :meth:`to_dict` output (tolerant of missing keys).

        Power level and the point budget each live in two places — the dedicated
        fields the rules read, and the ``characteristics`` entries the sheet's spin
        boxes seed from — so they are reconciled here rather than left to drift. A
        file that carries both keeps the dedicated field (that is what the last edit
        wrote); one that carries only the characteristic (an older save, a mod, a
        hand-edit) promotes it, so the sheet can't end up displaying one Power Level
        while the PL caps are computed against another.
        """

        characteristics = dict(raw.get("characteristics", {}))
        power_level = int(raw.get("power_level", characteristics.get("power_level", 10)))
        power_points_total = int(
            raw.get("power_points_total", characteristics.get("power_points", 150))
        )
        characteristics["power_level"] = power_level
        characteristics["power_points"] = power_points_total

        return cls(
            power_level=power_level,
            power_points_total=power_points_total,
            image_path=raw.get("image_path"),
            profile=dict(raw.get("profile", {})),
            characteristics=characteristics,
            abilities=dict(raw.get("abilities", {})),
            resistances=dict(raw.get("resistances", {})),
            skill_ranks=dict(raw.get("skill_ranks", {})),
            focuses={k: list(v) for k, v in raw.get("focuses", {}).items()},
            specializations={k: list(v) for k, v in raw.get("specializations", {}).items()},
            skill_order=[str(name) for name in raw.get("skill_order", [])],
            hidden_skills=[str(name) for name in raw.get("hidden_skills", [])],
            advantages=[
                AdvantageSelection(
                    name=a["name"], rank=int(a.get("rank", 1)), parameter=a.get("parameter", "")
                )
                for a in raw.get("advantages", [])
            ],
            complications=[Complication.from_dict(c) for c in raw.get("complications", [])],
            conditions=[
                # Back-compat: an older save stored conditions as a bare list of ids.
                (
                    AppliedCondition(condition_id=c)
                    if isinstance(c, str)
                    else AppliedCondition.from_dict(c)
                )
                for c in raw.get("conditions", [])
            ],
            powers=_migrate_flat_relations([node_from_dict(p) for p in raw.get("powers", [])]),
            equipment=[EquipmentItem.from_dict(e) for e in raw.get("equipment", [])],
            equipment_group_order=[str(c) for c in raw.get("equipment_group_order", [])],
            cost_overrides={k: int(v) for k, v in raw.get("cost_overrides", {}).items()},
            item_cost_overrides={
                cat: {k: int(v) for k, v in items.items()}
                for cat, items in raw.get("item_cost_overrides", {}).items()
            },
            notes={
                str(key): NotesState.from_dict(value)
                for key, value in (raw.get("notes") or {}).items()
            },
        )

    def restore(self, raw: dict, *, keep_runtime: bool = True) -> None:
        """Put this character back to the state *raw* describes, **in place**.

        The same code path as a load — ``from_dict`` parses *raw*, and every field is
        then copied across — but the object itself is kept, because a view over it
        may hold more than the reference. :class:`SkillsSection` aliases three of the
        model's dicts as its own attributes, so rebinding one here would desync the
        block silently; dicts are therefore cleared and refilled and lists are sliced
        in place, never reassigned. Driving that off :func:`~dataclasses.fields`
        rather than naming the fields means one added later is carried for free.

        ``keep_runtime`` carries the flags that :meth:`to_dict` deliberately omits
        (see :func:`capture_runtime`) across the restore, so putting a *build* back
        never re-wears a stowed item. A power's own runtime is in *raw* now, so it is
        restored with the rest of the build rather than held over — which is what makes
        switching a power off an ordinary undoable edit.
        """

        fresh = Character.from_dict(raw)
        runtime = capture_runtime(self) if keep_runtime else None
        for spec in fields(self):
            new = getattr(fresh, spec.name)
            current = getattr(self, spec.name)
            if isinstance(current, dict) and isinstance(new, dict):
                current.clear()
                current.update(new)
            elif isinstance(current, list) and isinstance(new, list):
                current[:] = new
            else:
                setattr(self, spec.name, new)
        if runtime is not None:
            apply_runtime(self, runtime)


def capture_runtime(character: Character) -> dict:
    """Read the runtime flags :meth:`Character.to_dict` omits, keyed by item id.

    That is now a short list. A power's own runtime — switched on, held at a rung,
    which member of an array is live — *is* part of the saved build (see
    :class:`~.powers.Power`), so it round-trips through ``to_dict`` like everything
    else and needs no carrying. What is left is the gear a character is holding right
    now: :attr:`~.equipment.EquipmentItem.worn` and
    :attr:`~.equipment.EquipmentItem.current_speed`, which stay out of the file so a
    loaded character comes up wearing everything.

    This pair is how :meth:`Character.restore` carries those across a restore: what is
    in your hands is not part of the build and must not move when the build is put
    back.
    """

    equipment: dict[str, dict] = {}

    def walk_item(item: EquipmentItem) -> None:
        equipment[item.id] = {"worn": item.worn, "current_speed": item.current_speed}
        for accessory in item.accessories:
            walk_item(accessory)

    for item in character.equipment:
        walk_item(item)
    return {"equipment": equipment}


def apply_runtime(character: Character, captured: dict) -> None:
    """Put the flags :func:`capture_runtime` read back onto *character*.

    An id the capture never saw is left at its dataclass default — worn — which is
    what undoing past an item's purchase should look like.
    """

    equipment = captured.get("equipment", {})

    def walk_item(item: EquipmentItem) -> None:
        state = equipment.get(item.id)
        if state is not None:
            item.worn = state.get("worn", item.worn)
            item.current_speed = state.get("current_speed", item.current_speed)
        for accessory in item.accessories:
            walk_item(accessory)

    for item in character.equipment:
        walk_item(item)


def _migrate_flat_relations(nodes: list[PowerNode]) -> list[PowerNode]:
    """Fold legacy flat cross-power relations into :class:`PowerGroup` nodes.

    Before nested groups existed, whole powers related to each other via
    ``Power.alternate_of`` (an array) and ``Power.linked_with`` (linked). A saved
    character from that era is a flat list of leaf powers carrying those id-references;
    this rebuilds the equivalent tree — an ``array`` group per ``alternate_of`` cluster
    and a ``linked`` group per ``linked_with`` component — clearing the now-dead fields
    so a re-save writes only the group form. Saves already using groups (no leaf carries
    a relation) pass through untouched.
    """

    powers = [n for n in nodes if isinstance(n, Power)]
    by_id = {p.id: p for p in powers}
    if not any(p.alternate_of or p.linked_with for p in powers):
        return nodes

    # Union-Find over powers joined by either relation.
    parent = {p.id: p.id for p in powers}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: str, b: str) -> None:
        parent[find(a)] = find(b)

    for p in powers:
        if p.alternate_of and p.alternate_of in by_id:
            union(p.id, p.alternate_of)
        for other in p.linked_with:
            if other in by_id:
                union(p.id, other)

    # Cluster members in original order; a cluster is an array if any member points at
    # another via alternate_of, else linked.
    clusters: dict[str, list[Power]] = {}
    for p in powers:
        clusters.setdefault(find(p.id), []).append(p)
    is_array: dict[str, bool] = {
        root: any(p.alternate_of in by_id for p in members) for root, members in clusters.items()
    }

    def clear_relations(p: Power) -> Power:
        p.alternate_of = ""
        p.linked_with = []
        p.array_active = True
        return p

    emitted: set[str] = set()
    result: list[PowerNode] = []
    for node in nodes:
        if not isinstance(node, Power):
            result.append(node)
            continue
        root = find(node.id)
        if root in emitted:
            continue
        emitted.add(root)
        members = clusters[root]
        if len(members) == 1:
            result.append(clear_relations(members[0]))
            continue
        array = is_array[root]
        # For an array, the base is the power the others point at (else the first).
        base = next(
            (m for m in members if any(o.alternate_of == m.id for o in members)), members[0]
        )
        result.append(
            PowerGroup(
                mode=STRUCTURE_ARRAY if array else STRUCTURE_LINKED,
                children=[clear_relations(m) for m in members],
                active_child_id=base.id if array else "",
            )
        )
    return result

"""Records for derived readouts, declarative blocks and the actions, and :class:`GameData`,
the one record bundling everything parsed.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from .equipment_records import (
    EquipmentRules,
    InstallationRules,
    VehicleRules,
)
from .system_records import (
    Movement,
    SystemRules,
)

if TYPE_CHECKING:
    from .equipment_records import (
        EquipmentCategory,
        EquipmentEntry,
        PlatformFeature,
        StockInstallation,
        StockVehicle,
        VehicleModifier,
    )
    from .power_records import (
        Effect,
        Modifier,
        PowerConfiguration,
        ResistanceOutcome,
    )
    from .system_records import (
        Costs,
        Measurements,
    )
    from .traits import (
        Ability,
        Advantage,
        Characteristic,
        Condition,
        ConditionCategory,
        ConditionGroup,
        Field,
        Resistance,
        Skill,
    )


# --- Derived readouts & declarative blocks: per-effect Tier-5 readouts and the
#     data-described sheet blocks a data-only mod can add. ---------------------
@dataclass(frozen=True)
class Readout:
    """A derived, display-only Tier-5 readout for an effect (from ``effect_readouts.json``).

    ``kind`` selects how :func:`mm_companion.core.rules.effect_readout_rows` renders it
    (``"size_table"``, ``"state"``, ``"measure_offsets"``, ``"thresholds"``,
    ``"config_flag"``, ``"points_per_rank"``); ``label`` is the row label; ``data``
    holds the kind-specific parameters (the byRank map, the offset rows, ...). These
    are computed information, never editable — see ``docs/mm-powers-ui-design.md`` §2 Tier 5.
    """

    kind: str
    label: str = ""
    data: dict = field(default_factory=dict)


@dataclass(frozen=True)
class BlockFieldSpec:
    """One row of a data-described (declarative) sheet block.

    ``kind`` selects how the generic declarative block renders the row:

    - ``"text"`` — an editable line backed by ``Character.profile[key]``.
    - ``"label"`` — a static, read-only line showing ``text``.

    Unknown kinds fall back to a static label so a mod can add new kinds (with a
    matching UI handler) without the loader rejecting the row.
    """

    key: str = ""
    label: str = ""
    kind: str = "text"
    text: str = ""
    #: Unrecognised JSON keys (e.g. from a mod), retained rather than dropped.
    extra: dict = field(default_factory=dict, compare=False)


@dataclass(frozen=True)
class BlockSpec:
    """A data-described sheet block a mod can add without shipping any Python.

    The UI turns each spec into a generic declarative block (a titled group of
    field/label rows) and registers it through the same block registry as the
    built-in blocks, so it appears on the sheet and can be floated / hidden /
    rearranged like any other. ``row``/``col`` place it in the default
    arrangement; the ``*_width``/``*_height`` bounds feed its size constraints
    (``0``/omitted means unconstrained).
    """

    id: str
    title: str = ""
    row: int = 0
    col: int = 0
    fields: tuple[BlockFieldSpec, ...] = ()
    min_width: int = 0
    min_height: int = 0
    max_width: int = 0
    max_height: int = 0
    #: Unrecognised JSON keys (e.g. from a mod), retained rather than dropped.
    extra: dict = field(default_factory=dict, compare=False)


# --- Aggregate: the single record bundling every parsed list above, with the
#     merged-catalog lookups the resolvers walk. -------------------------------
@dataclass(frozen=True)
class Impression:
    """One kind of impression an Impress action can make (``actions.json``).

    ``skill`` is only the skill preselected when the impression is picked — any
    of :attr:`ImpressRules.skills` may be paired with any impression. An
    ``advantage`` id gates it: only a character with that advantage may make it.
    ``outcomes`` is a degree-of-failure ladder in the same shape as an effect's
    ``resistanceOutcomes``, so the same resolver walks both.
    """

    id: str
    name: str
    skill: str = ""
    advantage: str = ""
    outcomes: tuple[ResistanceOutcome, ...] = ()


@dataclass(frozen=True)
class ActionModifier:
    """One situational modifier a GM sets on an action's check.

    A ``toggle`` is a checkbox worth :attr:`value`; toggles sharing an
    :attr:`exclusive` group cannot both be on. A ``slider`` runs from
    :attr:`minimum` to :attr:`maximum` in steps of :attr:`step` and is worth its
    position. :attr:`label` is the short caption the control wears and :attr:`hint`
    the longer explanation behind it.
    """

    id: str
    label: str
    kind: str = "toggle"
    value: int = 0
    minimum: int = 0
    maximum: int = 0
    step: int = 1
    exclusive: str = ""
    hint: str = ""


@dataclass(frozen=True)
class ResistOptions:
    """What a target may resist an action with — see :func:`~.rules.impress.resist_choices`."""

    same_skill: bool = True
    skills: tuple[str, ...] = ()
    resistances: tuple[str, ...] = ()


@dataclass(frozen=True)
class ImpressRules:
    """The Impress action, from ``actions.json``'s ``impress`` block.

    Empty when a ruleset ships none, which is how a caller knows to offer no
    Impress button at all.
    """

    skills: tuple[str, ...] = ()
    resist: ResistOptions = field(default_factory=ResistOptions)
    group_penalty: int = 0
    impressions: tuple[Impression, ...] = ()
    modifiers: tuple[ActionModifier, ...] = ()

    @property
    def available(self) -> bool:
        """Whether this ruleset can impress at all."""
        return bool(self.skills and self.impressions)

    def impression(self, impression_id: str) -> Impression | None:
        return next((i for i in self.impressions if i.id == impression_id), None)


@dataclass(frozen=True)
class AreaCover:
    """One degree of cover a creature may have from an area's origin — a Dodge bonus."""

    id: str
    label: str
    value: int = 0


@dataclass(frozen=True)
class AreaEvasion:
    """The advantage that helps against areas: a Dodge bonus, then no effect on a save."""

    advantage: str
    dodge_bonus: int = 0
    negate_at_rank: int = 0


@dataclass(frozen=True)
class AreaRules:
    """Area attacks, from ``actions.json``'s ``area`` block.

    ``modifiers`` are the modifier ids that make an effect an area effect; their shape,
    applications and spread config is interpreted by :mod:`~.rules.area`. Empty when a
    ruleset ships no block, which is how a caller knows to offer no area use at all.
    """

    modifiers: tuple[str, ...] = ()
    dodge: str = ""
    dc_base: int = 10
    halve: str = "up"
    base_distance_rank: int = 1
    shapes: dict[str, str] = field(default_factory=dict)
    default_shape: str = ""
    evasion: AreaEvasion | None = None
    free_use_pl_cap: bool = True
    cover: tuple[AreaCover, ...] = ()
    #: ``(modifier id, config key, value)`` choices that make an effect resisted by
    #: either of two traits take the *worse* of them (Alternate Resistance's worst of
    #: two). Without one, a choice between two is the better one.
    worst_of: tuple[tuple[str, str, str], ...] = ()

    @property
    def available(self) -> bool:
        """Whether this ruleset resolves area attacks at all."""
        return bool(self.modifiers and self.dodge)


@dataclass(frozen=True)
class GameData:
    """The full parsed game-data content, aggregated across the data files.

    ``modifiers`` is the general-purpose extra/flaw pool that applies broadly;
    ``effect_modifiers`` maps an effect id to the extras/flaws specific to that one
    effect (from ``effect_modifiers.json``). A power builder offers both pools for a
    given effect; :meth:`modifier_catalog` merges them into a single id lookup for
    cost math and the game-terms summary.

    ``game_term_ladders`` maps a game-term field (``"duration"``, ``"action"``) to
    its ordered values from least to most, so a stepping modifier (Increased
    Duration, Increased Action) can move a value along it without hardcoding the
    order in code.

    ``duration_action_floor`` maps a resulting duration to the minimum action its
    effect must take (a Sustained effect needs at least a free action to toggle on
    and maintain), so :mod:`mm_companion.core.rules` can raise a below-floor action
    in the game-terms summary without hardcoding the rule.
    """

    profile_fields: list[Field]
    characteristics: list[Characteristic]
    abilities: list[Ability]
    resistances: list[Resistance]
    skills: list[Skill]
    advantages: list[Advantage]
    conditions: list[Condition]
    effects: list[Effect]
    modifiers: list[Modifier]
    effect_modifiers: dict[str, list[Modifier]]
    #: The rulebook's named standard power configurations (Blast, Dazzle, Force Field,
    #: …), each a ready-made assembly of the records above. Empty for a ruleset that
    #: ships none.
    configurations: tuple[PowerConfiguration, ...]
    costs: Costs
    measurements: Measurements
    game_term_ladders: dict[str, tuple[str, ...]]
    duration_action_floor: dict[str, str] = field(default_factory=dict)
    effect_readouts: dict[str, tuple[Readout, ...]] = field(default_factory=dict)
    movement: Movement = field(default_factory=Movement)
    system: SystemRules = field(default_factory=SystemRules)
    #: Data-described blocks a mod contributes via ``blocks.json`` (empty for the
    #: base ruleset, whose blocks are built-in Python widgets).
    blocks: tuple[BlockSpec, ...] = ()
    #: How the Conditions block groups its chips, from ``conditions.json``'s
    #: ``_meta.sheetSections`` — in display order.
    condition_categories: tuple[ConditionCategory, ...] = ()
    #: How a "+" menu splits the addable catalog into submenus, from
    #: ``conditions.json``'s ``_meta.conditionGroups`` — in display order. Empty
    #: when a ruleset declares none, which means "offer the flat list".
    condition_groups: tuple[ConditionGroup, ...] = ()
    #: The gear catalog, from ``equipment.json``.
    equipment: tuple[EquipmentEntry, ...] = ()
    #: How the Equipment block groups its cards, from ``equipment.json``'s
    #: ``_meta.equipmentCategories`` — in display order.
    equipment_categories: tuple[EquipmentCategory, ...] = ()
    #: The Equipment Point currency and the no-stacking rule's targets.
    equipment_rules: EquipmentRules = field(default_factory=EquipmentRules)
    #: The printed stock vehicles, from ``equipment.json``'s ``stockVehicles``.
    vehicles: tuple[StockVehicle, ...] = ()
    #: The same vehicles as ordinary catalog entries, so every seam that already
    #: knows how to price, build and draw a piece of gear handles one unchanged.
    #: Derived at parse time by :func:`vehicle_entry` — see :meth:`equipment_catalog`.
    vehicle_entries: tuple[EquipmentEntry, ...] = ()
    #: The Features a platform can carry, from ``vehicleFeatures``.
    vehicle_features: tuple[PlatformFeature, ...] = ()
    #: Durable / Minion / Summonable — the modifiers that price the *advantage*.
    vehicle_modifiers: tuple[VehicleModifier, ...] = ()
    #: The vehicle size table, the class → movement-effect axis, and the §5 constants.
    vehicle_rules: VehicleRules = field(default_factory=VehicleRules)
    #: The printed stock installations, from ``equipment.json``'s ``stockInstallations``.
    installations: tuple[StockInstallation, ...] = ()
    #: The same installations as ordinary catalog entries — see :meth:`equipment_catalog`.
    installation_entries: tuple[EquipmentEntry, ...] = ()
    #: The Features an installation can carry, from ``installationFeatures``.
    installation_features: tuple[PlatformFeature, ...] = ()
    #: The installation size table, its free starting points, and its own PL cap pair.
    installation_rules: InstallationRules = field(default_factory=InstallationRules)
    #: The Impress action, from ``actions.json``.
    impress: ImpressRules = field(default_factory=ImpressRules)
    area: AreaRules = field(default_factory=AreaRules)

    def _catalog(self, key: str, build: Callable[[], dict]) -> dict:
        """Memoize one of the merged ``id ->`` lookups below, per :class:`GameData`.

        Each is a flat merge over tuples that never change (:class:`GameData` is
        frozen; a mod reload builds a new one), so the answer is fixed for the
        instance. Uncached, ``modifier_catalog`` alone was rebuilt three hundred times
        for **one step of an ability spin box**, a third of the cost of the edit.

        Written through ``object.__setattr__`` because the dataclass is frozen; the
        cache is not a field. The returned mapping is **shared**: treat it as
        read-only.

        Unlocked on purpose: two session reader threads racing here build the same
        answer from the same frozen tuples, which is cheaper than a lock on the
        hottest read in the app.
        """

        cache = self.__dict__.get("_catalogs")
        if cache is None:
            cache = {}
            object.__setattr__(self, "_catalogs", cache)
        if key not in cache:
            cache[key] = build()
        return cache[key]

    def modifier_catalog(self) -> dict[str, Modifier]:
        """A single ``id -> Modifier`` lookup over the general and effect-specific pools.

        Effect-specific ids are globally unique and never collide with the general
        pool, so a flat merge is unambiguous.
        """

        def build() -> dict[str, Modifier]:
            catalog: dict[str, Modifier] = {m.id: m for m in self.modifiers}
            for mods in self.effect_modifiers.values():
                for modifier in mods:
                    catalog.setdefault(modifier.id, modifier)
            return catalog

        return self._catalog("modifiers", build)

    def condition_catalog(self) -> dict[str, Condition]:
        """A single ``id -> Condition`` lookup, for the condition resolver in ``rules``."""

        return self._catalog("conditions", lambda: {c.id: c for c in self.conditions})

    def equipment_catalog(self) -> dict[str, EquipmentEntry]:
        """A single ``id -> EquipmentEntry`` lookup, for the equipment rules layer.

        Both kinds of **platform** are in it (:attr:`vehicle_entries`,
        :attr:`installation_entries`): a stock vehicle or installation is bought,
        priced, worn and drawn as one more thing off the catalog, so everything that
        works on an entry works on it. Its platform traits are read from
        :meth:`vehicle_catalog` by the one layer that needs them.

        Items win an id collision: a ruleset that ships a platform and an item under one
        id has a bug, and the item is the older, larger catalog.
        """

        def build() -> dict[str, EquipmentEntry]:
            catalog: dict[str, EquipmentEntry] = {e.id: e for e in self.vehicle_entries}
            catalog.update({e.id: e for e in self.installation_entries})
            catalog.update({e.id: e for e in self.equipment})
            return catalog

        return self._catalog("equipment", build)

    def vehicle_catalog(self) -> dict[str, StockVehicle]:
        """A single ``id -> StockVehicle`` lookup, for the platform traits a card shows."""

        return self._catalog("vehicles", lambda: {v.id: v for v in self.vehicles})

    def vehicle_feature_catalog(self) -> dict[str, PlatformFeature]:
        """A single ``id -> PlatformFeature`` lookup over the vehicle Features."""

        return self._catalog("vehicle_features", lambda: {f.id: f for f in self.vehicle_features})

    def installation_catalog(self) -> dict[str, StockInstallation]:
        """A single ``id -> StockInstallation`` lookup, for the traits a card shows."""

        return self._catalog("installations", lambda: {i.id: i for i in self.installations})

    def installation_feature_catalog(self) -> dict[str, PlatformFeature]:
        """A single ``id -> PlatformFeature`` lookup over the installation Features."""

        return self._catalog(
            "installation_features", lambda: {f.id: f for f in self.installation_features}
        )

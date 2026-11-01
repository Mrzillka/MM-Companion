"""Records for equipment: the gear catalog, vehicles and installations."""

from __future__ import annotations

from dataclasses import dataclass, field


# --- Equipment: the gear catalog, its grouping axis, and the rules constants
#     that govern the second currency (from ``equipment.json``). ---------------
@dataclass(frozen=True)
class EquipmentModifierRef:
    """One extra or flaw a catalog entry applies to its effect.

    ``modifier`` is a bare :class:`Modifier` id — the JSON writes it namespaced
    (``"modifiers:accurate"``) and the loader unqualifies it; ``rank`` is its rank
    where it takes one; ``note`` is the printed qualification the table gives
    ("Ballistic damage only") and is display copy, not a rule.
    """

    modifier: str
    rank: int | None = None
    note: str = ""
    #: The modifier's config choices, as a power's selection would store them (an Area
    #: Effect's ``shape``). Empty for the commonest case, a modifier with no choices.
    config: dict = field(default_factory=dict, compare=False)
    #: The top of the rank band the modifier covers (``appliesTo``), 0 for every rank —
    #: a Rocket Launcher's Area on 7 of its 10 Damage ranks, so the creature it hits
    #: directly takes 10 and everyone else in the blast 7.
    applies_to: int = 0
    #: Unrecognised JSON keys (e.g. from a mod), retained rather than dropped.
    extra: dict = field(default_factory=dict, compare=False)


@dataclass(frozen=True)
class EquipmentEffectRef:
    """One base effect an item is built from, as named by a catalog entry.

    ``effect`` is a bare :class:`Effect` id — the JSON writes it namespaced
    (``"effects:damage"``) and the loader unqualifies it, so the catalog reads as
    the design reference does while the record carries something that indexes
    straight into :attr:`GameData.effects`.

    The remaining fields are the qualities an item's printed line fixes: the
    ``rank`` it comes at, whether its Damage is ``strength_based``, its
    ``descriptors``, and the effect's own configuration — ``config`` for the
    keyed form an effect's config fields take, ``configuration`` for a named
    preset ("stun", "toxin"), plus the ``resistance`` it is checked against and
    the condition ``degrees`` an Affliction inflicts. Nothing is *built* here;
    turning these into a real :class:`~mm_companion.core.powers.Power` is the
    equipment rules layer's job.
    """

    effect: str
    rank: int | None = None
    strength_based: bool = False
    descriptors: tuple[str, ...] = ()
    config: dict = field(default_factory=dict)
    configuration: str = ""
    resistance: str = ""
    degrees: tuple[tuple[str, ...], ...] = ()
    #: What to call *this* effect on a multi-effect item — a tank's "Cannon". Empty
    #: for the ordinary case, where the base effect's own name says everything there
    #: is to say; see :attr:`~mm_companion.core.powers.PowerEffectInstance.label`.
    label: str = ""
    #: Extras and flaws carried by this effect alone, on top of the entry-wide
    #: :attr:`EquipmentEntry.modifiers` every effect gets. An item whose effects each
    #: want their own (a vehicle's two weapons: one Area, one Multiattack) has no other
    #: way to say so.
    modifiers: tuple[EquipmentModifierRef, ...] = ()
    #: Unrecognised JSON keys (e.g. from a mod), retained rather than dropped.
    extra: dict = field(default_factory=dict, compare=False)


@dataclass(frozen=True)
class CriticalProfile:
    """An item's critical-hit profile: the natural rolls that threaten, and the
    ranks of Improved Critical that ``threat_range`` already reflects."""

    threat_range: tuple[int, int] = (20, 20)
    improved_critical_ranks: int = 0


@dataclass(frozen=True)
class EquipmentEntry:
    """One item of the equipment catalog (from ``equipment.json``).

    ``cost`` is the item's printed Equipment Point price — ``None`` for a
    ``cost_kind`` of ``"built"``, which has no single printed number because the
    item is assembled from a trait table. Equipment Points are a *second
    currency*: they are bought by ranks of the Equipment advantage and never mix
    with Power Points (see :class:`EquipmentRules`).

    ``effects``/``modifiers`` are the build the item's printed line describes,
    ``grants`` the advantages it hands its wielder (``{"advantages": (...)}``,
    ids unqualified), ``critical`` its threat range, and ``patterns`` the
    behavioural tags (``"attack_item"``, ``"passive_trait_item"``, …) named in
    ``docs/mm-equipment-design.md`` §2.

    ``implementation`` is deliberately an open bag rather than typed fields: it
    is the per-item mechanical detail the engine grows into over time (ranges,
    charges, attachment hosts, ammo modes), and its keys are as varied as the
    catalog. Reading one is a matter for the phase that needs it; retaining it
    whole is what lets that happen without another data migration.
    """

    id: str
    name: str
    category: str = ""
    subcategory: str = ""
    cost: int | None = None
    cost_kind: str = "fixed"
    cost_note: str = ""
    description: str = ""
    effects: tuple[EquipmentEffectRef, ...] = ()
    modifiers: tuple[EquipmentModifierRef, ...] = ()
    grants: dict = field(default_factory=dict)
    critical: CriticalProfile | None = None
    patterns: tuple[str, ...] = ()
    implementation: dict = field(default_factory=dict)
    #: Unrecognised JSON keys (e.g. from a mod), retained rather than dropped.
    extra: dict = field(default_factory=dict, compare=False)


@dataclass(frozen=True)
class EquipmentCategory:
    """One group the Equipment block sorts its cards into, from
    ``_meta.equipmentCategories`` — in display order.

    ``id`` matches an :class:`EquipmentEntry`'s ``category``; ``title`` is the
    heading shown above that group; ``description`` is the category's own
    one-liner from ``_meta.categoryKey``.

    The same split the conditions layer makes between a rules fact and a display
    axis: the category *is* a rules fact here (a weapon is not armour, and a
    weapon may not be dragged into the armour group), but which order the groups
    come in and what they are called is presentation, so it lives in ``_meta``
    where a mod can add a row rather than having its items folded into someone
    else's group.
    """

    id: str
    title: str
    description: str = ""


@dataclass(frozen=True)
class EquipmentRules:
    """The constants governing equipment as a whole, from ``equipment.json``'s ``_meta``.

    ``points_per_advantage_rank`` × the character's rank of the ``advantage``
    is the Equipment Point budget. ``currency_name``/``currency_abbreviation``
    name that second currency for the UI.

    ``stacking_targets`` are the stats the no-stacking rule governs: equipment
    contributions to one of them resolve as ``max()`` among themselves and
    ``max()`` again against non-equipment sources — never a sum of the two
    maxima. ``cost_kinds`` maps each ``cost_kind`` to its explanation.

    ``material_toughness`` is the breakage table from
    ``_meta.strengthBasedDamage.breakage``: how much Toughness an item made of a
    given material has, which is the most Strength it can carry before it breaks
    in use (``docs/mm-equipment-design.md`` §4). Which material an item is made of
    is its own ``implementation.material``. Empty for a ruleset that declares
    none, which simply means nothing is ever warned about — the warning is a
    courtesy, not a rule the engine enforces. ``breakage_rule`` is that section's
    own prose, shown as the warning's tooltip.
    """

    currency_name: str = "Equipment Point"
    currency_abbreviation: str = "EP"
    advantage: str = "equipment"
    points_per_advantage_rank: int = 5
    stacking_rule: str = ""
    stacking_targets: tuple[str, ...] = ()
    cost_kinds: dict = field(default_factory=dict)
    material_toughness: dict = field(default_factory=dict)
    breakage_rule: str = ""
    #: Unrecognised ``_meta`` keys, retained rather than dropped.
    extra: dict = field(default_factory=dict, compare=False)


# --- Vehicles: the stock platforms, the trait table their sizes come from, and
#     the two catalogs a custom one is assembled out of (from ``equipment.json``). ---
@dataclass(frozen=True)
class VehicleClass:
    """One medium a vehicle travels through, from ``_meta.vehicleClasses``.

    ``effect`` is the :class:`Effect` id a vehicle of this class measures its printed
    Speed rank in — ``flight`` for an aircraft, ``swimming`` for a boat — so a
    vehicle's speed reaches the sheet as the movement effect it actually is rather
    than as a bare number. Empty for a class that names no default (``exotic``): such
    a vehicle carries its own :attr:`StockVehicle.movement` instead.
    """

    id: str
    title: str
    effect: str = ""


@dataclass(frozen=True)
class VehicleSizeRow:
    """One row of the vehicle Size table: the baselines a size rank confers.

    Size is chosen first when a vehicle is built, because it sets ``strength``,
    ``toughness`` and ``defense``; everything above those baselines is what the
    build pays for (``docs/mm-equipment-design.md`` §5).
    """

    size_rank: int
    strength: int
    toughness: int
    defense: int
    examples: tuple[str, ...] = ()


@dataclass(frozen=True)
class VehicleSizeExtension:
    """What each size rank *past* the printed table adds (``_meta.vehicleSizeExtension``).

    The table stops at rank 5 and the book extends it arithmetically rather than
    printing more rows, so the deltas are data and :func:`~mm_companion.core.rules.vehicle_size_row`
    applies them.
    """

    strength: int = 2
    toughness: int = 1
    defense: int = -1
    note: str = ""


@dataclass(frozen=True)
class VehicleWeapon:
    """One weapon a stock vehicle mounts.

    ``name`` is what the table calls it ("Cannon"), which becomes the built effect's
    :attr:`~mm_companion.core.powers.PowerEffectInstance.label` so a tank's two rolls
    are told apart. ``modifiers`` are that weapon's own — the Area rank of a cannon is
    a fact about the cannon, not about the tank.
    """

    name: str
    effect: str
    rank: int = 0
    modifiers: tuple[EquipmentModifierRef, ...] = ()
    note: str = ""


@dataclass(frozen=True)
class StockVehicle:
    """One vehicle off the printed table (``stockVehicles``).

    A vehicle is a *platform*: five traits (Size, Strength, Speed, Defense, Toughness)
    rather than a bundle of effects, which is why its card shows a trait grid where an
    item's shows a game-term table. What it *does* still becomes a real
    :class:`~mm_companion.core.powers.Power` — its movement and its weapons — so its
    speed reaches the Speed readout and its cannon rolls, exactly as a power's would.
    That translation is :func:`vehicle_entry`.

    ``cost`` is the printed Equipment Point price, ``None`` for the two whose
    ``cost_kind`` is ``"built"``; ``cost_formula`` is the printed recipe those give
    instead. ``defenses`` are the modifiers on its Toughness (a tank's Impervious 4),
    which are trait annotations rather than effects and so are drawn on the grid.
    """

    id: str
    name: str
    vehicle_class: str = ""
    size: int = 0
    strength: int = 0
    speed: int | None = None
    defense_modifier: int = 0
    toughness: int = 0
    cost: int | None = None
    cost_kind: str = "fixed"
    cost_formula: str = ""
    defenses: tuple[EquipmentModifierRef, ...] = ()
    weapons: tuple[VehicleWeapon, ...] = ()
    movement: EquipmentEffectRef | None = None
    patterns: tuple[str, ...] = ()
    variants: str = ""
    notes: str = ""
    #: Unrecognised JSON keys (e.g. from a mod), retained rather than dropped.
    extra: dict = field(default_factory=dict, compare=False)


@dataclass(frozen=True)
class PlatformFeature:
    """A named 1-point Feature a platform can carry (``vehicleFeatures``).

    ``repeatable`` marks the ones bought more than once, and a repeatable Feature with
    a defined escalation carries it: ``dc_increase_per_extra_rank`` and
    ``max_dc_increase`` (``docs/mm-equipment-design.md`` §2 pattern K), stored rather
    than branched on per feature name.
    """

    id: str
    name: str
    cost: int = 1
    repeatable: bool = False
    description: str = ""
    dc_increase_per_extra_rank: int = 0
    max_dc_increase: int = 0
    #: Unrecognised JSON keys (e.g. from a mod), retained rather than dropped.
    extra: dict = field(default_factory=dict, compare=False)


@dataclass(frozen=True)
class VehicleModifier:
    """Durable / Minion / Summonable — the rare modifiers that price the *advantage*.

    Structurally unlike every other modifier in the app: they change what a rank of
    the Equipment advantage costs in **Power Points** for the ranks allocated to this
    vehicle, not what the vehicle costs in Equipment Points
    (``docs/mm-equipment-design.md`` §5). That is why they are their own catalog and
    never join :meth:`GameData.modifier_catalog` — folding them in would let a cost
    engine spend one currency as the other.
    """

    id: str
    name: str
    cost: int = 0
    cost_kind: str = "per_rank_flat"
    description: str = ""
    #: Unrecognised JSON keys (e.g. from a mod), retained rather than dropped.
    extra: dict = field(default_factory=dict, compare=False)


@dataclass(frozen=True)
class VehicleRules:
    """The constants a vehicle is read and built against (``equipment.json``'s ``_meta``).

    ``category`` is the equipment category vehicles are filed under, named in the data
    rather than in Python so a ruleset can move them. ``combat`` keeps the §5 Combat
    prose (the moving/stationary Defense Class rules) for the card's tooltips.
    """

    category: str = "vehicle"
    classes: tuple[VehicleClass, ...] = ()
    size_table: tuple[VehicleSizeRow, ...] = ()
    size_extension: VehicleSizeExtension = field(default_factory=VehicleSizeExtension)
    combat: dict = field(default_factory=dict)

    def vehicle_class(self, class_id: str) -> VehicleClass | None:
        """The :class:`VehicleClass` with this id, or ``None``."""

        return next((c for c in self.classes if c.id == class_id), None)


# --- Installations: the second kind of platform, and a simpler one. Size and
#     Toughness off their own table, Features, and effects (from ``equipment.json``). ---
@dataclass(frozen=True)
class InstallationSizeRow:
    """One row of the installation Size table: what a size rank costs.

    Unlike a vehicle's, an installation's size confers no trait baselines — it is
    priced and nothing more (``docs/mm-equipment-design.md`` §6). The pivot is the
    free rank (a house), whose ``cost`` is ``0``; below it the cost is negative and
    the points come *back*.
    """

    size_rank: int
    cost: int = 0
    examples: tuple[str, ...] = ()


@dataclass(frozen=True)
class InstallationRules:
    """What an installation starts with for free, and what more of it costs.

    ``free_size_rank`` and ``free_toughness`` are the starting points every
    installation gets without paying (rank 5, Toughness 6); ``toughness_per_point``
    is how much Toughness one Equipment Point buys above that. ``category`` is the
    equipment category installations file under, named in the data rather than in
    Python for the reason :attr:`VehicleRules.category` is.

    ``toughness_pl_multiple`` and ``impervious_pl_multiple`` are the installation's
    own Power Level cap pair, and it is genuinely a different pair from a character's:
    an installation has no defences other than its Toughness, so that Toughness may
    reach **twice** the series Power Level while Impervious stays capped at the Power
    Level itself.
    """

    category: str = "installation"
    size_table: tuple[InstallationSizeRow, ...] = ()
    free_size_rank: int = 5
    free_toughness: int = 6
    toughness_per_point: int = 2
    toughness_pl_multiple: int = 2
    impervious_pl_multiple: int = 1
    #: Which modifier the Impervious cap governs, named in the data rather than in
    #: Python — the cap is a rule, but *Impervious* is content.
    impervious_modifier: str = "impervious"
    note: str = ""

    def size_row(self, size_rank: int) -> InstallationSizeRow | None:
        """The row for this size rank, or ``None`` when the table does not print one."""

        return next((row for row in self.size_table if row.size_rank == size_rank), None)


@dataclass(frozen=True)
class StockInstallation:
    """One installation off the printed table (``stockInstallations``).

    A platform like a vehicle, with far less to it: Size, Toughness, and a list of
    Features. It carries no movement and no weapons, so the
    :class:`~mm_companion.core.powers.Power` :func:`installation_entry` gives it is
    usually empty — which is exactly right, since what an installation *does* is its
    Features rather than its effects.
    """

    id: str
    name: str
    size: int = 0
    toughness: int = 0
    cost: int | None = None
    cost_kind: str = "fixed"
    cost_formula: str = ""
    features: tuple[str, ...] = ()
    patterns: tuple[str, ...] = ()
    notes: str = ""
    #: Unrecognised JSON keys (e.g. from a mod), retained rather than dropped.
    extra: dict = field(default_factory=dict, compare=False)

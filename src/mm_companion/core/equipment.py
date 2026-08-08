"""The equipment model: a piece of gear as a real power, bought in a second currency.

Equipment is **not** a power (``docs/mm-equipment-design.md`` §1) — a character built
entirely from gear legitimately answers "no powers" — but mechanically an item *is*
a bundle of effects, so an :class:`EquipmentItem` **wraps** a
:class:`~mm_companion.core.powers.Power` in its :attr:`~EquipmentItem.build` rather
than re-describing one. Every function in the powers rules layer
(``power_total_cost``, ``effect_stat_rows``, ``power_rolls``, ``effect_is_active``)
takes ``item.build`` unchanged; that is the whole point of the shape.

The two currencies never mix. Power Points buy *ranks of the Equipment advantage*;
each rank grants :attr:`~mm_companion.core.data_loader.EquipmentRules.points_per_advantage_rank`
Equipment Points, and those buy the items. The cost engine is
:mod:`mm_companion.core.rules.equipment`; nothing here derives a number.

Plain data, JSON-serializable, no PySide6 — the same idiom as :mod:`.powers`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from uuid import uuid4

from .data_loader import StockInstallation, StockVehicle
from .powers import ModifierSelection, Power

# The ``costKind`` vocabulary an :class:`~mm_companion.core.data_loader.EquipmentEntry`
# declares (``equipment.json``'s ``_meta.costKindKey``). A closed vocabulary named in
# Python the way :data:`~mm_companion.core.powers.STRUCTURES` and the ``components``
# patterns are — the *items* are data, but what "per_rank" means to the cost engine is
# engine behaviour.
COST_FIXED = "fixed"  # a flat printed price
COST_RANKED = "ranked"  # priced over a small number of defined ranks
COST_PER_RANK = "per_rank"  # the printed price is per rank
COST_BUILT = "built"  # assembled from components; no single printed price
COST_KINDS = (COST_FIXED, COST_RANKED, COST_PER_RANK, COST_BUILT)

#: The kinds whose printed ``cost`` is a price *per rank* rather than a total.
PER_RANK_COST_KINDS = (COST_RANKED, COST_PER_RANK)

# The two kinds of **platform** — gear bought as traits off a table rather than as a
# bundle of effects (``docs/mm-equipment-design.md`` §5 and §6). A closed vocabulary in
# Python for the reason :data:`COST_KINDS` is: *which* traits a kind is priced on is
# engine behaviour. Which category the sheet files them under stays data
# (:attr:`~mm_companion.core.data_loader.VehicleRules.category`).
PLATFORM_VEHICLE = "vehicle"
PLATFORM_INSTALLATION = "installation"
PLATFORM_KINDS = (PLATFORM_VEHICLE, PLATFORM_INSTALLATION)


@dataclass
class PlatformSpec:
    """The bought traits of a vehicle or an installation, on the character's own item.

    A platform is the one piece of gear that is not a bundle of effects: it is five
    traits off the §5 table (a vehicle) or two off the §6 one (an installation), which
    is why its card shows a trait grid where an item's shows a game-term table. A
    **stock** platform reads those traits off the catalog record it was picked from; the
    moment a player edits them the item carries a spec of its own, and that is what
    makes it custom.

    So this is the same shape either way — a stock record normalises into one
    (:meth:`from_vehicle`, :meth:`from_installation`) so the rules layer reads *one*
    kind of thing (:func:`~mm_companion.core.rules.platforms.item_platform`) rather than
    branching on where the traits came from.

    Two fields are only a vehicle's: ``modifiers`` are the Durable / Minion / Summonable
    ids, which price the **Power Points** of the Equipment advantage funding it rather
    than the platform's own Equipment Points, and ``vehicle_class`` is the medium it
    travels through — which is also what its ``speed`` is a rank *of*, unless
    ``movement_effect`` names one outright (a mole machine burrows). The movement effect
    itself lives in :attr:`EquipmentItem.build` like any other effect, and is kept in
    step with these two by
    :func:`~mm_companion.core.rules.platforms.apply_platform` — which is what makes a
    platform's Speed reach the sheet's Speed readout and cost what a movement effect
    costs.
    """

    kind: str = PLATFORM_VEHICLE
    vehicle_class: str = ""
    size: int = 0
    strength: int = 0
    speed: int | None = None
    defense_modifier: int = 0
    toughness: int = 0
    #: Feature ids, one entry per rank bought — a repeatable Feature appears twice.
    features: list[str] = field(default_factory=list)
    #: Vehicle modifier ids (Durable / Minion / Summonable). See the class docstring.
    modifiers: list[str] = field(default_factory=list)
    #: Modifiers annotating the platform's Toughness (a tank's Impervious 4). They hang
    #: off the trait rather than off an effect, since a platform has no Protection
    #: effect to carry them.
    defenses: list[ModifierSelection] = field(default_factory=list)
    #: The effect its Speed is a rank of, when it is not the one its class implies.
    movement_effect: str = ""
    #: That effect's rank, when it differs from ``speed`` (a platform whose movement is
    #: an Enhanced Movement rather than a Speed rank has no ``speed`` at all).
    movement_rank: int | None = None
    notes: str = ""

    @classmethod
    def from_vehicle(cls, vehicle: StockVehicle) -> PlatformSpec:
        """The spec a printed vehicle's traits amount to — the normalising seam."""

        movement = vehicle.movement
        return cls(
            kind=PLATFORM_VEHICLE,
            vehicle_class=vehicle.vehicle_class,
            size=vehicle.size,
            strength=vehicle.strength,
            speed=vehicle.speed,
            defense_modifier=vehicle.defense_modifier,
            toughness=vehicle.toughness,
            defenses=[
                ModifierSelection(modifier_id=ref.modifier, rank=ref.rank or 1)
                for ref in vehicle.defenses
            ],
            movement_effect="" if movement is None else movement.effect,
            movement_rank=None if movement is None else movement.rank,
            notes=vehicle.notes,
        )

    @classmethod
    def from_installation(cls, installation: StockInstallation) -> PlatformSpec:
        """The spec a printed installation's traits amount to."""

        return cls(
            kind=PLATFORM_INSTALLATION,
            size=installation.size,
            toughness=installation.toughness,
            features=list(installation.features),
            notes=installation.notes,
        )

    def to_dict(self) -> dict:
        """Serialize, writing only what says something so a plain item is unchanged."""

        data: dict = {"kind": self.kind, "size": self.size, "toughness": self.toughness}
        for key, value in (
            ("vehicle_class", self.vehicle_class),
            ("strength", self.strength),
            ("defense_modifier", self.defense_modifier),
            ("movement_effect", self.movement_effect),
            ("notes", self.notes),
        ):
            if value:
                data[key] = value
        if self.speed is not None:
            data["speed"] = self.speed
        if self.movement_rank is not None:
            data["movement_rank"] = self.movement_rank
        if self.features:
            data["features"] = list(self.features)
        if self.modifiers:
            data["modifiers"] = list(self.modifiers)
        if self.defenses:
            data["defenses"] = [d.to_dict() for d in self.defenses]
        return data

    @classmethod
    def from_dict(cls, raw: dict) -> PlatformSpec:
        speed = raw.get("speed")
        movement_rank = raw.get("movement_rank")
        return cls(
            kind=str(raw.get("kind", PLATFORM_VEHICLE)),
            vehicle_class=str(raw.get("vehicle_class", "")),
            size=int(raw.get("size", 0) or 0),
            strength=int(raw.get("strength", 0) or 0),
            speed=None if speed is None else int(speed),
            defense_modifier=int(raw.get("defense_modifier", 0) or 0),
            toughness=int(raw.get("toughness", 0) or 0),
            features=[str(f) for f in raw.get("features", ())],
            modifiers=[str(m) for m in raw.get("modifiers", ())],
            defenses=[ModifierSelection.from_dict(d) for d in raw.get("defenses", ())],
            movement_effect=str(raw.get("movement_effect", "")),
            movement_rank=None if movement_rank is None else int(movement_rank),
            notes=str(raw.get("notes", "")),
        )


@dataclass
class EquipmentItem:
    """One piece of gear on a character sheet.

    ``catalog_id`` names the :class:`~mm_companion.core.data_loader.EquipmentEntry`
    it was picked from (``""`` for a wholly custom item), ``category`` the group the
    Equipment block files it under — copied off the entry at pick time so a card can
    be grouped without a catalog lookup, and so an item whose entry a disabled mod
    took away still has a home.

    ``worn`` is **runtime** state, exactly like a power's
    :attr:`~mm_companion.core.powers.Power.activated`: taking a jacket off is a
    play-time action, not a build edit, so it is deliberately left out of
    :meth:`to_dict` and a loaded character comes up wearing everything.

    ``platform`` is the traits of a **vehicle or installation** the player has bought
    or edited (:class:`PlatformSpec`). ``None`` — the ordinary case — means either that
    the item is not a platform at all, or that it is still exactly the catalog's, whose
    traits are read off the printed record instead. Carrying one is what makes a
    platform *custom*, and it is the seam
    :func:`~mm_companion.core.rules.platforms.item_platform` resolves.

    ``current_speed`` is runtime for the same reason ``worn`` is, and it is the one
    number on the sheet that changes *within* a round: a vehicle's Defense rank is its
    **current** speed rank plus its size modifier, so a parked car and a car doing
    Speed 7 are very different targets (``docs/mm-equipment-design.md`` §5 Combat).
    ``None`` means "as fast as it goes", which is what a card shows before anyone has
    touched the throttle.

    ``rank`` is what a ranked or per-rank item was **bought at**, and it is only
    consulted for gear whose build has no effects to read it off — an Evidence Kit and
    an Armour Cloth are printed at a price per rank while granting nothing mechanical,
    so without a field of their own the rank the picker asked for had nowhere to go and
    was silently discarded. Everything with effects still answers from its first
    effect's rank (:func:`~mm_companion.core.rules.equipment.item_rank`).

    ``stacks`` is build state and the one per-item homerule: equipment bonuses do not
    stack with each other or with powers (``docs/mm-equipment-design.md`` §3), and
    ticking this opts *this* item out of that rule, adding its bonus on top of the
    winner instead of competing with it. See
    :func:`~mm_companion.core.rules.appliers.resolve_contributions`.

    ``ep_override`` is the Equipment-Point twin of
    :attr:`~mm_companion.core.powers.Power.cost_override`: when set it *replaces* the
    item's derived price. ``None`` leaves the cost fully derived
    (:func:`~mm_companion.core.rules.equipment.item_ep_cost`).

    The last three fields are the **accessory** layer (``docs/mm-equipment-design.md``
    §2 pattern I). ``accessories`` holds the items fitted to *this* one — a laser sight
    on a rifle — and an attached accessory lives here rather than in
    :attr:`~mm_companion.core.character.Character.equipment`, which is what makes its
    scope honest: the targeting scope's Improved Aim is a trait of that weapon, not of
    the character, and a loose card could only say otherwise. Its price folds into the
    host's (:func:`~mm_companion.core.rules.equipment.item_ep_cost`), so the budget
    still counts it exactly once.

    On the accessory itself, ``attaches_to`` is the host categories it fits (copied off
    the catalog entry at pick time, for the reason ``category`` is) and ``attachment``
    the modifiers it lends its host — an accessory has no effects of its own, so its
    Accurate or Subtle would otherwise have nothing to hang on. Being an accessory at
    all is having somewhere to attach, so a mod's accessory needs no category of its
    own; ask :func:`~mm_companion.core.rules.equipment.item_attaches_to`, which falls
    back to the catalog for gear saved before any of this existed. Merging them into the
    host is :func:`~mm_companion.core.rules.equipment.item_effective_build`, which is
    derived on demand: the stored build is never rewritten, so detaching is lossless
    and the host stays recognisably the catalog's.
    """

    catalog_id: str = ""
    build: Power = field(default_factory=Power)
    category: str = ""
    platform: PlatformSpec | None = None
    rank: int = 1
    worn: bool = True
    current_speed: int | None = None
    stacks: bool = False
    ep_override: int | None = None
    accessories: list[EquipmentItem] = field(default_factory=list)
    attaches_to: tuple[str, ...] = ()
    attachment: list[ModifierSelection] = field(default_factory=list)
    id: str = field(default_factory=lambda: uuid4().hex)

    @property
    def name(self) -> str:
        """The item's display name — its build's, which the catalog seeded."""

        return self.build.name

    def to_dict(self) -> dict:
        """Serialize the *build* state; runtime (:attr:`worn`) is left out on purpose."""

        data: dict = {
            "catalog_id": self.catalog_id,
            "build": self.build.to_dict(),
            "category": self.category,
            "id": self.id,
        }
        if self.platform is not None:
            data["platform"] = self.platform.to_dict()
        if self.rank > 1:
            data["rank"] = self.rank
        if self.stacks:
            data["stacks"] = True
        if self.ep_override is not None:
            data["ep_override"] = self.ep_override
        # Written only when they say something, so an ordinary item's entry is
        # unchanged and an existing save round-trips byte for byte.
        if self.accessories:
            data["accessories"] = [a.to_dict() for a in self.accessories]
        if self.attaches_to:
            data["attaches_to"] = list(self.attaches_to)
        if self.attachment:
            data["attachment"] = [m.to_dict() for m in self.attachment]
        return data

    @classmethod
    def from_dict(cls, raw: dict) -> EquipmentItem:
        override = raw.get("ep_override")
        platform = raw.get("platform")
        return cls(
            catalog_id=raw.get("catalog_id", ""),
            build=Power.from_dict(raw.get("build", {})),
            category=raw.get("category", ""),
            platform=PlatformSpec.from_dict(platform) if isinstance(platform, dict) else None,
            rank=max(1, int(raw.get("rank", 1) or 1)),
            stacks=bool(raw.get("stacks", False)),
            ep_override=None if override is None else int(override),
            accessories=[cls.from_dict(a) for a in raw.get("accessories", ())],
            attaches_to=tuple(str(c) for c in raw.get("attaches_to", ())),
            attachment=[ModifierSelection.from_dict(m) for m in raw.get("attachment", ())],
            id=raw.get("id") or uuid4().hex,
        )

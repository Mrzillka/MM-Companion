"""Platforms: the gear that is bought as traits rather than as effects.

Two kinds of gear are not a bundle of effects. A **vehicle** is five traits (Size,
Strength, Speed, Defense, Toughness) bought off their own table, with Size chosen first
because it sets the baselines for three of the others; an **installation** is two (Size
and Toughness) plus a list of Features, starting from a free house
(``docs/mm-equipment-design.md`` §5 and §6). Both need a layer of their own, and this
is all of it.

What a platform *does* still runs through the powers pipeline unchanged: its movement
and its weapons are real effects on a real
:class:`~mm_companion.core.powers.Power`, which is why a boat's Speed reaches the
sheet's Speed readout and a tank's cannon rolls without anything here being involved.
What is left over — the bought traits, their baselines, and the moving Defense Class a
vehicle's traits imply — is what this module answers.

**Stock and custom are one shape.** A printed platform's traits are read off its
catalog record; the moment a player edits them the item carries a
:class:`~mm_companion.core.equipment.PlatformSpec` of its own, and every function here
takes that one shape (:func:`item_platform` normalises a printed record into it). So
there is no custom-platform branch: a hand-built jet is priced, drawn, worn and rolled
by exactly the code a printed one is.

Three things it deliberately does not do. It never prices a *stock* platform: the
printed number wins while the item is stock, exactly as it does for every other catalog
entry (:func:`~.equipment.item_own_ep_cost`), and :func:`platform_trait_cost` is
consulted only when there is no printed price to honour or the build was edited. It
never prices **Speed**, which is a movement effect and is paid for as one through the
build — the split that makes the halves add up to a printed price. And
:func:`vehicle_modifier_advantage_cost` computes a **Power Point** number rather than an
Equipment Point one, because that is genuinely what Durable and Minion do — see its
docstring.
"""

from __future__ import annotations

from math import ceil

from ..character import Character
from ..data_loader import (
    GameData,
    PlatformFeature,
    StockInstallation,
    StockVehicle,
    VehicleSizeRow,
)
from ..equipment import PLATFORM_INSTALLATION, PLATFORM_VEHICLE, EquipmentItem, PlatformSpec
from ..powers import PowerEffectInstance
from .powers_terms import EffectStat

__all__ = [
    "apply_platform",
    "installation_pl_violations",
    "installation_size_cost",
    "installation_trait_cost",
    "installation_trait_rows",
    "item_installation",
    "item_platform",
    "item_platform_cost",
    "item_platform_violations",
    "item_vehicle",
    "new_platform",
    "platform_feature_catalog",
    "platform_feature_cost",
    "platform_features",
    "platform_is_stock",
    "platform_movement_effect",
    "platform_movement_index",
    "platform_rules_category",
    "platform_trait_cost",
    "platform_trait_rows",
    "vehicle_defense_class",
    "vehicle_modifier_advantage_cost",
    "vehicle_size_row",
    "vehicle_stationary_dc",
    "vehicle_trait_cost",
    "vehicle_trait_rows",
]

# --- resolving a platform: one shape, whether printed or built ---------------------------


def item_vehicle(item: EquipmentItem, game_data: GameData) -> StockVehicle | None:
    """The printed :class:`~mm_companion.core.data_loader.StockVehicle` behind *item*.

    ``None`` for gear that was not picked from a vehicle entry — including a wholly
    custom vehicle, whose traits are its own
    (:attr:`~mm_companion.core.equipment.EquipmentItem.platform`) rather than any
    printed record's. Ask :func:`item_platform` for the traits themselves; this answers
    the narrower question of which catalog line the item came off.
    """

    return game_data.vehicle_catalog().get(item.catalog_id)


def item_installation(item: EquipmentItem, game_data: GameData) -> StockInstallation | None:
    """The printed :class:`~mm_companion.core.data_loader.StockInstallation` behind *item*."""

    return game_data.installation_catalog().get(item.catalog_id)


def _stock_platform(item: EquipmentItem, game_data: GameData) -> PlatformSpec | None:
    """The spec the item's *catalog entry* amounts to, ignoring any edits to it."""

    vehicle = item_vehicle(item, game_data)
    if vehicle is not None:
        return PlatformSpec.from_vehicle(vehicle)
    installation = item_installation(item, game_data)
    if installation is not None:
        return PlatformSpec.from_installation(installation)
    return None


def item_platform(item: EquipmentItem, game_data: GameData) -> PlatformSpec | None:
    """The bought traits of *item* as a platform, or ``None`` — it is not one.

    The single seam every reader below goes through, and the reason there is no
    custom-platform branch anywhere: an item that carries a
    :class:`~mm_companion.core.equipment.PlatformSpec` of its own has been built or
    edited and that spec is the answer; otherwise a printed record is normalised into
    the same shape. A player who edits a stock jet gets a spec and stops being priced
    at the book's number (:func:`platform_is_stock`), which is the same contract
    editing a stock *item*'s build has always had.
    """

    if item.platform is not None:
        return item.platform
    return _stock_platform(item, game_data)


def platform_is_stock(item: EquipmentItem, game_data: GameData) -> bool:
    """Whether the item's platform traits are still exactly the catalog's.

    True for anything that is not a platform at all, so the caller
    (:func:`~.equipment.item_is_stock`) can ask it of every item unconditionally. An
    item carrying a spec that happens to *equal* the printed one is still stock — a
    no-op pass through the editor must not move an item's price, the way a no-op pass
    through the constructor does not.
    """

    if item.platform is None:
        return True
    stock = _stock_platform(item, game_data)
    return stock is not None and stock == item.platform


def platform_rules_category(kind: str, game_data: GameData) -> str:
    """Which equipment category this kind of platform files under.

    Named in the data on both sides (``_meta.vehicleCategory`` /
    ``_meta.installationCategory``) so a ruleset can move them, which is why this is a
    lookup rather than the ``kind`` string itself.
    """

    if kind == PLATFORM_INSTALLATION:
        return game_data.installation_rules.category
    return game_data.vehicle_rules.category


def new_platform(kind: str, game_data: GameData) -> PlatformSpec:
    """A blank platform of *kind*, at everything its rules give away for free.

    A vehicle starts at size 0 with the baselines that size confers, so it costs
    nothing but the movement effect its one rank of Speed is; an installation starts at
    the free house — size rank 5, Toughness 6 — which the §6 table prices at zero. In
    both cases the opening cost is what the book says an unbought platform costs, so
    the first number the editor shows is honest.
    """

    if kind == PLATFORM_INSTALLATION:
        rules = game_data.installation_rules
        return PlatformSpec(
            kind=PLATFORM_INSTALLATION,
            size=rules.free_size_rank,
            toughness=rules.free_toughness,
        )
    classes = game_data.vehicle_rules.classes
    baseline = vehicle_size_row(0, game_data)
    return PlatformSpec(
        kind=PLATFORM_VEHICLE,
        vehicle_class=classes[0].id if classes else "",
        size=0,
        strength=baseline.strength if baseline else 0,
        toughness=baseline.toughness if baseline else 0,
        defense_modifier=baseline.defense if baseline else 0,
        speed=1,
    )


# --- vehicles: the five traits, their baselines, and the defence they imply --------------


def vehicle_size_row(size_rank: int, game_data: GameData) -> VehicleSizeRow | None:
    """The trait baselines a vehicle of this size gets, extended past the printed table.

    The book stops at size rank 5 and says each further rank adds +2 Strength, +1
    Toughness and −1 Defense rather than printing more rows, so the table is extended
    arithmetically from its last row using
    :attr:`~mm_companion.core.data_loader.VehicleRules.size_extension`. A rank *below*
    the table clamps to its first row. ``None`` only when the ruleset declares no table
    at all, in which case there are no baselines to compare anything against.
    """

    table = game_data.vehicle_rules.size_table
    if not table:
        return None
    rows = sorted(table, key=lambda row: row.size_rank)
    if size_rank <= rows[0].size_rank:
        return rows[0]
    exact = next((row for row in rows if row.size_rank == size_rank), None)
    if exact is not None:
        return exact
    last = rows[-1]
    steps = size_rank - last.size_rank
    extension = game_data.vehicle_rules.size_extension
    return VehicleSizeRow(
        size_rank=size_rank,
        strength=last.strength + extension.strength * steps,
        toughness=last.toughness + extension.toughness * steps,
        defense=last.defense + extension.defense * steps,
    )


def vehicle_trait_cost(vehicle: PlatformSpec | StockVehicle, game_data: GameData) -> int:
    """What a vehicle's five platform traits cost in Equipment Points.

    The §5 table: 1 point per size rank above 0, 1 per +1 of Strength or Toughness
    *above the size baseline*, and 1 per −1 of the size Defense penalty bought off. A
    trait at or below its baseline costs nothing — the baseline is what the size
    already paid for.

    Speed is **not** here: it is a movement effect and is paid for as one, through the
    build (:func:`~.powers_cost.power_total_cost`). Splitting it that way is what makes
    the two halves add up to the printed price — a jumbo jet's 14 points of traits plus
    18 points of Flight 9 is its printed 32. Neither are Features, which are the same
    flat point each on both kinds of platform and are counted for both in
    :func:`platform_feature_cost`.

    A printed :class:`~mm_companion.core.data_loader.StockVehicle` is accepted beside a
    :class:`~mm_companion.core.equipment.PlatformSpec` because the five traits are
    spelled the same on both — which is exactly what :meth:`PlatformSpec.from_vehicle`
    relies on.
    """

    baseline = vehicle_size_row(vehicle.size, game_data)
    cost = max(0, vehicle.size)
    if baseline is not None:
        cost += max(0, vehicle.strength - baseline.strength)
        cost += max(0, vehicle.toughness - baseline.toughness)
        cost += max(0, vehicle.defense_modifier - baseline.defense)
    return cost


def vehicle_defense_class(
    vehicle: PlatformSpec | StockVehicle, game_data: GameData, speed_rank: int
) -> int:
    """The Defense Class of a vehicle **moving** at *speed_rank*.

    ``10 + Defense rank``, where a moving vehicle's Defense rank is its current speed
    rank plus its size's Defense modifier (§5 Combat). This is the one defence in the
    app that is dynamic per round: a parked car and a car at Speed 7 are very different
    targets, which is why the card shows both this and :func:`vehicle_stationary_dc`,
    and why the card's throttle reads it at the speed the driver is actually doing
    rather than only flat out.
    """

    return game_data.system.defense_dc_base + speed_rank + vehicle.defense_modifier


def vehicle_stationary_dc(vehicle: PlatformSpec | StockVehicle, game_data: GameData) -> int:
    """The DC of the routine check that hits a **stationary** vehicle: ``10 + its Defense
    modifier`` (§5 Combat). It is not moving, so its speed rank contributes nothing."""

    return game_data.system.defense_dc_base + vehicle.defense_modifier


def vehicle_modifier_advantage_cost(modifier_ids, advantage_ranks: int, game_data: GameData) -> int:
    """The **Power Point** change Durable / Minion / Summonable make to *advantage_ranks*.

    Structurally unlike every other modifier in the app, and the reason they are their
    own catalog: they do not change what the vehicle costs in Equipment Points, they
    change what the ranks of the Equipment advantage *funding* it cost in Power Points
    (``docs/mm-equipment-design.md`` §5). A returned ``+3`` means three more Power
    Points on the advantage, not three more Equipment Points of gear — mixing the two
    up is the "two currencies" failure this whole layer is built to avoid.

    A custom vehicle chooses them in the platform editor, which shows this number in
    Power Points and **beside** the Equipment Point price rather than inside it.
    """

    catalog = {m.id: m for m in game_data.vehicle_modifiers}
    return sum(
        catalog[modifier_id].cost * max(0, advantage_ranks)
        for modifier_id in modifier_ids
        if modifier_id in catalog
    )


# --- installations: Size, Toughness, Features -------------------------------------------


def installation_size_cost(size_rank: int, game_data: GameData) -> int:
    """What an installation's size rank costs, refunds included.

    The §6 table, which is a straight line through the free rank: a house (rank 5) is
    the pivot at zero, a small town (rank 11) is +6, and a single room (rank 1) hands
    **back** 4 points. A rank the table does not print is extrapolated along that same
    line rather than clamped, since the line is what the table is.
    """

    rules = game_data.installation_rules
    row = rules.size_row(size_rank)
    if row is not None:
        return row.cost
    return size_rank - rules.free_size_rank


def installation_trait_cost(spec: PlatformSpec, game_data: GameData) -> int:
    """What an installation's two bought traits cost in Equipment Points.

    Size off :func:`installation_size_cost`, plus one point per
    :attr:`~mm_companion.core.data_loader.InstallationRules.toughness_per_point` of
    Toughness above the free starting point — rounded **up**, since a point buys up to
    +2 and half a point buys nothing. Toughness *below* the free baseline refunds
    nothing: it is a starting point rather than a purchase, which is also why the one
    printed installation listed under it (the moon-base's Toughness 2) is a recorded
    discrepancy rather than a discount.

    Features are not here, for the reason :func:`vehicle_trait_cost` gives.
    """

    rules = game_data.installation_rules
    above = max(0, spec.toughness - rules.free_toughness)
    return installation_size_cost(spec.size, game_data) + ceil(above / rules.toughness_per_point)


def installation_pl_violations(
    spec: PlatformSpec, char: Character, game_data: GameData
) -> list[str]:
    """Power Level breaches on an installation — a **different cap pair** from a character's.

    An installation has no defences other than its Toughness, so that Toughness may
    reach twice the series Power Level (``docs/mm-equipment-design.md`` §6) where a
    character's is bound by the paired caps in
    :func:`~.validation.power_level_violations`. Impervious is *not* relieved with it:
    it stays capped at the Power Level itself, and both multiples are data
    (``_meta.installationPowerLevel``) rather than the 2 and the 1 they happen to be.

    Warnings, like every other Power Level check in the app — see
    :func:`mm_companion.core.storage.pl_enforcement`.
    """

    rules = game_data.installation_rules
    power_level = char.power_level
    messages = []
    toughness_cap = power_level * rules.toughness_pl_multiple
    if spec.toughness > toughness_cap:
        messages.append(
            f"Toughness {spec.toughness} exceeds the PL {power_level} installation cap "
            f"of {toughness_cap}."
        )
    impervious_cap = power_level * rules.impervious_pl_multiple
    catalog = game_data.modifier_catalog()
    for selection in spec.defenses:
        if selection.modifier_id != rules.impervious_modifier:
            continue
        if selection.rank > impervious_cap:
            modifier = catalog.get(selection.modifier_id)
            name = modifier.name if modifier is not None else selection.modifier_id
            messages.append(
                f"{name} {selection.rank} exceeds the PL {power_level} cap of {impervious_cap}."
            )
    return messages


def item_platform_violations(
    item: EquipmentItem, char: Character, game_data: GameData
) -> list[str]:
    """Platform-level Power Level breaches for *item*, if it is a platform at all.

    The seam a card asks, so that a platform's ``⚠`` covers what its **traits** breach
    as well as what its effects do (:func:`~.validation.power_pl_violations`, which
    knows only about effects and so has nothing to say about a fortress made of
    Toughness). A vehicle has no trait cap of its own: its weapons are capped as any
    gear's are, and a GM-adjudicated vehicle-PL exception is not modelled.
    """

    spec = item_platform(item, game_data)
    if spec is None or spec.kind != PLATFORM_INSTALLATION:
        return []
    return installation_pl_violations(spec, char, game_data)


# --- what both kinds share: Features, the price, and the movement effect -----------------


def platform_feature_catalog(kind: str, game_data: GameData) -> dict[str, PlatformFeature]:
    """The Features a platform of this kind may carry, ``id -> record``.

    Two catalogs rather than one because they *are* two lists in the book — a vehicle's
    Alarm and an installation's Holding Cells are not offered to each other — and each
    is priced by its own record's ``cost`` rather than by a rule here.
    """

    if kind == PLATFORM_INSTALLATION:
        return game_data.installation_feature_catalog()
    return game_data.vehicle_feature_catalog()


def platform_features(spec: PlatformSpec, game_data: GameData) -> list[PlatformFeature]:
    """The Feature records a spec names, in the order it names them.

    An id no loaded ruleset defines is dropped rather than invented — the honest answer
    when the mod that shipped a Feature has been turned off — and a repeatable Feature
    bought twice appears twice, since that is what a rank of it *is*.
    """

    catalog = platform_feature_catalog(spec.kind, game_data)
    return [catalog[feature] for feature in spec.features if feature in catalog]


def platform_feature_cost(spec: PlatformSpec, game_data: GameData) -> int:
    """What a platform's Features cost: each record's own price, one per rank bought."""

    return sum(feature.cost for feature in platform_features(spec, game_data))


def platform_trait_cost(spec: PlatformSpec, game_data: GameData) -> int:
    """What a platform's bought traits and Features cost in Equipment Points.

    The dispatch between the two kinds, and the only place that branches on
    :attr:`~mm_companion.core.equipment.PlatformSpec.kind`. Speed is still not in it:
    it is a movement effect, priced through the build.
    """

    features = platform_feature_cost(spec, game_data)
    if spec.kind == PLATFORM_INSTALLATION:
        return installation_trait_cost(spec, game_data) + features
    return vehicle_trait_cost(spec, game_data) + features


def item_platform_cost(item: EquipmentItem, game_data: GameData) -> int:
    """:func:`platform_trait_cost` for an item that is a platform, else ``0``.

    The seam :func:`~.equipment.item_own_ep_cost` adds to a *derived* price, so a
    platform with no printed number (the dimension hopper's ``"6 + movement effect
    cost"``, or anything the player built) comes out at what its traits and its
    movement cost together rather than at the movement alone.
    """

    spec = item_platform(item, game_data)
    return 0 if spec is None else platform_trait_cost(spec, game_data)


def platform_movement_effect(spec: PlatformSpec, game_data: GameData) -> PowerEffectInstance | None:
    """The movement effect a platform's Speed *is*, as a real effect instance.

    Its own :attr:`~mm_companion.core.equipment.PlatformSpec.movement_effect` when it
    names one (a mole machine burrows), otherwise the effect its
    :class:`~mm_companion.core.data_loader.VehicleClass` measures Speed in — ``flight``
    for an aircraft, ``swimming`` for a boat — so a platform's speed reaches the sheet
    as the movement it actually is rather than as a bare number. ``None`` when it has
    neither, which is the honest answer for an installation (they do not move) and for
    a time machine (whose movement is a plot device rather than a rank).
    """

    effect_id = spec.movement_effect
    rank = spec.movement_rank if spec.movement_rank is not None else spec.speed
    if not effect_id:
        vehicle_class = game_data.vehicle_rules.vehicle_class(spec.vehicle_class)
        effect_id = vehicle_class.effect if vehicle_class else ""
        rank = spec.speed
    if not effect_id or rank is None or rank < 1:
        return None
    return PowerEffectInstance(effect_id=effect_id, rank=rank)


def platform_movement_index(build, spec: PlatformSpec, game_data: GameData) -> int | None:
    """Where a platform's movement effect sits in its build, or ``None`` — it has none.

    **A platform's movement is the first effect in its build that could be one**: an
    effect one of the ruleset's vehicle classes measures Speed in (``speed``,
    ``flight``, ``swimming``, …), or the one the spec names outright. Identifying it by
    what it *is* rather than by remembering where it was put is what makes
    :func:`apply_platform` safe to call with a spec that is already the item's own —
    which is the ordinary case, since the editor edits the live one.

    The cost of the rule: a vehicle whose build carries a second movement effect a
    player added in the constructor has its *first* one taken as the platform's. That
    is the honest reading — a vehicle with two ways of moving has to nominate one, and
    the trait grid can only show a single Speed.
    """

    candidates = {c.effect for c in game_data.vehicle_rules.classes if c.effect}
    if spec.movement_effect:
        candidates.add(spec.movement_effect)
    return next((i for i, e in enumerate(build.effects) if e.effect_id in candidates), None)


def apply_platform(item: EquipmentItem, spec: PlatformSpec, game_data: GameData) -> None:
    """Write *spec* onto *item*, keeping its build's movement effect in step.

    The one writer, and the reason nothing else has to think about where a platform's
    Speed lives. The traits are the spec's; the **movement is a real effect on the
    build**, exactly as it is for a printed vehicle — which is what makes a custom
    jet's Flight reach the Speed readout, cost what Flight costs, and show up in the
    effect list the constructor edits, with no platform branch in any of the three.

    Re-ranking an existing movement effect *edits* it rather than replacing it, so
    anything hanging off it — the dimension hopper's Enhanced Movement mode, extras a
    player added in the constructor — survives a trip through the editor. A vehicle
    whose Speed has gone away entirely loses the effect, since a build that kept it
    would go on paying for it; an installation never removes one, because an effect
    that looks like movement on something that does not move was put there deliberately.
    """

    new_movement = platform_movement_effect(spec, game_data)
    effects = item.build.effects
    index = platform_movement_index(item.build, spec, game_data)

    if new_movement is None:
        if index is not None and spec.kind == PLATFORM_VEHICLE:
            del effects[index]
    elif index is None:
        effects.insert(0, new_movement)
    elif effects[index].effect_id == new_movement.effect_id:
        effects[index].rank = new_movement.rank
    else:
        effects[index] = new_movement
    item.platform = spec


# --- the trait grid a card draws ---------------------------------------------------------


def _defenses_note(spec: PlatformSpec, game_data: GameData) -> str:
    """``"(Impervious 4)"`` — the modifiers printed on a platform's Toughness, or ``""``.

    They are annotations on a trait rather than modifiers on an effect (a platform has
    no Protection effect to hang them off), so they are drawn on the trait grid.
    """

    catalog = game_data.modifier_catalog()
    parts = []
    for selection in spec.defenses:
        modifier = catalog.get(selection.modifier_id)
        name = modifier.name if modifier is not None else selection.modifier_id
        parts.append(f"{name} {selection.rank}" if selection.rank else name)
    return f" ({', '.join(parts)})" if parts else ""


def _speed_text(spec: PlatformSpec, game_data: GameData) -> str:
    """``"Flight 9"`` — the movement a platform's Speed rank actually is.

    A dash when it has none: a time machine's movement is a plot device, not a rank.
    """

    movement = platform_movement_effect(spec, game_data)
    if movement is None:
        return "—"
    base = next((e for e in game_data.effects if e.id == movement.effect_id), None)
    return f"{base.name if base else movement.effect_id} {movement.rank}"


def _compare(value: int, base: int | None) -> tuple[str, str]:
    """A trait row's ``base`` text and tint, against a baseline that may not exist."""

    if base is None:
        return "", ""
    change = "better" if value > base else "worse" if value < base else ""
    return f"{base:+d}" if base < 0 else str(base), change


def _feature_rows(spec: PlatformSpec, game_data: GameData) -> list[EffectStat]:
    """The platform's Features as one row, or nothing when it carries none.

    One row rather than one each: a moon-base has fifteen, and a card that listed them
    down its left-hand column would bury the traits it exists to show. A Feature bought
    twice is written ``"Concealed ×2"`` — the ranks are what its escalation reads off
    (``docs/mm-equipment-design.md`` §2 pattern K).
    """

    features = platform_features(spec, game_data)
    if not features:
        return []
    counts: dict[str, int] = {}
    for feature in features:
        counts[feature.name] = counts.get(feature.name, 0) + 1
    text = ", ".join(name if count == 1 else f"{name} ×{count}" for name, count in counts.items())
    return [EffectStat(key="features", label="Features", base="", value=text)]


def vehicle_trait_rows(item: EquipmentItem, game_data: GameData) -> list[EffectStat]:
    """A vehicle's platform traits as game-term rows, for the card that draws them.

    The same :class:`~.powers_terms.EffectStat` shape an effect's terms table uses, so
    the card renders both through one grid — a platform card differs from an item's in
    *what* it says, not in how it is laid out. Strength, Toughness and Defense carry
    their size baseline as the row's ``base`` and are tinted when they beat it, which
    is exactly what the build paid for.

    Empty for an item that is not a vehicle, which is how a caller asks the question.
    """

    spec = item_platform(item, game_data)
    if spec is None or spec.kind != PLATFORM_VEHICLE:
        return []
    rules = game_data.vehicle_rules
    baseline = vehicle_size_row(spec.size, game_data)
    vehicle_class = rules.vehicle_class(spec.vehicle_class)

    rows = [
        EffectStat(
            key="vehicle_class",
            label="Class",
            base=spec.vehicle_class,
            value=vehicle_class.title if vehicle_class else spec.vehicle_class or "—",
        ),
        EffectStat(key="size", label="Size", base=str(spec.size), value=str(spec.size)),
    ]
    for key, label, value, base in (
        ("strength", "Strength", spec.strength, baseline.strength if baseline else None),
        ("toughness", "Toughness", spec.toughness, baseline.toughness if baseline else None),
    ):
        base_text, change = _compare(value, base)
        suffix = _defenses_note(spec, game_data) if key == "toughness" else ""
        rows.append(
            EffectStat(
                key=key, label=label, base=base_text, value=f"{value}{suffix}", change=change
            )
        )
    base_text, change = _compare(spec.defense_modifier, baseline.defense if baseline else None)
    rows.append(
        EffectStat(
            key="defense",
            label="Defense",
            base=base_text,
            value=f"{spec.defense_modifier:+d}",
            change=change,
        )
    )
    rows.append(EffectStat(key="speed", label="Speed", base="", value=_speed_text(spec, game_data)))
    # The throttle, if the driver has touched it: a vehicle's Defense rank is its
    # *current* speed rank, so the row answers at the speed it is actually doing.
    current = item.current_speed if item.current_speed is not None else spec.speed
    moving = vehicle_defense_class(spec, game_data, current) if current is not None else None
    stationary = vehicle_stationary_dc(spec, game_data)
    rows.append(
        EffectStat(
            key="defense_class",
            label="Defense Class",
            base="",
            value=(
                f"{moving} moving / {stationary} stationary"
                if moving is not None
                else f"{stationary} stationary"
            ),
        )
    )
    rows.extend(_feature_rows(spec, game_data))
    return rows


def installation_trait_rows(item: EquipmentItem, game_data: GameData) -> list[EffectStat]:
    """An installation's bought traits as game-term rows, in the same grid a vehicle uses.

    Far shorter, because an installation is: what it *is* is its Size and its Toughness,
    and what it *does* is its Features. Size carries the table's own examples as its
    baseline text, since "rank 7" means nothing and "Mansion" means everything.
    """

    spec = item_platform(item, game_data)
    if spec is None or spec.kind != PLATFORM_INSTALLATION:
        return []
    rules = game_data.installation_rules
    row = rules.size_row(spec.size)
    rows = [
        EffectStat(
            key="size",
            label="Size",
            base=", ".join(row.examples) if row is not None else "",
            value=str(spec.size),
        )
    ]
    base_text, change = _compare(spec.toughness, rules.free_toughness)
    rows.append(
        EffectStat(
            key="toughness",
            label="Toughness",
            base=base_text,
            value=f"{spec.toughness}{_defenses_note(spec, game_data)}",
            change=change,
        )
    )
    rows.extend(_feature_rows(spec, game_data))
    return rows


def platform_trait_rows(item: EquipmentItem, game_data: GameData) -> list[EffectStat]:
    """The trait grid for whichever kind of platform *item* is — empty when it is neither.

    What a card calls: it asks one question ("has this bought traits to show?") and gets
    one answer, so the block needs no platform branch of its own.
    """

    spec = item_platform(item, game_data)
    if spec is None:
        return []
    if spec.kind == PLATFORM_INSTALLATION:
        return installation_trait_rows(item, game_data)
    return vehicle_trait_rows(item, game_data)

"""Vehicles: the platform traits a card shows, and what they cost.

A vehicle is the one piece of gear that is not a bundle of effects. It is a
**platform** — five traits (Size, Strength, Speed, Defense, Toughness) bought off
their own table, with Size chosen first because it sets the baselines for three of
the others (``docs/mm-equipment-design.md`` §5). So it needs a layer of its own, and
this is all of it.

What a vehicle *does* still runs through the powers pipeline unchanged: its movement
and its weapons became a real :class:`~mm_companion.core.powers.Power` back in
:func:`~mm_companion.core.data_loader.vehicle_entry`, which is why a boat's Speed
reaches the sheet's Speed readout and a tank's cannon rolls without anything here
being involved. What is left over — the five traits, their baselines, and the moving
Defense Class the traits imply — is what this module answers.

Two things it deliberately does not do. It never prices a *stock* vehicle: the
printed number wins while the item is stock, exactly as it does for every other
catalog entry (:func:`~.equipment.item_own_ep_cost`), and :func:`vehicle_trait_cost`
is consulted only when there is no printed price to honour or the build was edited.
And :func:`vehicle_modifier_advantage_cost` computes a **Power Point** number rather
than an Equipment Point one, because that is genuinely what Durable and Minion do —
see its docstring.
"""

from __future__ import annotations

from ..data_loader import GameData, StockVehicle, VehicleSizeRow
from ..equipment import EquipmentItem
from .powers_terms import EffectStat

__all__ = [
    "PLATFORM_PATTERN",
    "item_platform_cost",
    "item_vehicle",
    "vehicle_defense_class",
    "vehicle_modifier_advantage_cost",
    "vehicle_size_row",
    "vehicle_stationary_dc",
    "vehicle_trait_cost",
    "vehicle_trait_rows",
]

#: The ``patterns`` tag that marks an entry a platform (``docs/mm-equipment-design.md``
#: §2 pattern J). Named here rather than in the UI because "is this a platform" is a
#: rules question — it decides how the thing is priced, not only how it is drawn.
PLATFORM_PATTERN = "platform"


def item_vehicle(item: EquipmentItem, game_data: GameData) -> StockVehicle | None:
    """The :class:`~mm_companion.core.data_loader.StockVehicle` behind *item*, or ``None``.

    An item is a vehicle by virtue of the catalog entry it was picked from, so gear
    with no ``catalog_id`` — or one no loaded ruleset defines — is simply not one. That
    is also the honest answer for a custom platform, which Phase 10 owns.
    """

    return game_data.vehicle_catalog().get(item.catalog_id)


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


def vehicle_trait_cost(vehicle: StockVehicle, game_data: GameData) -> int:
    """What a vehicle's five platform traits cost in Equipment Points.

    The §5 table: 1 point per size rank above 0, 1 per +1 of Strength or Toughness
    *above the size baseline*, and 1 per −1 of the size Defense penalty bought off. A
    trait at or below its baseline costs nothing — the baseline is what the size
    already paid for.

    Speed is **not** here: it is a movement effect and is paid for as one, through the
    build (:func:`~.powers_cost.power_total_cost`). Splitting it that way is what makes
    the two halves add up to the printed price — a jumbo jet's 14 points of traits plus
    18 points of Flight 9 is its printed 32.
    """

    baseline = vehicle_size_row(vehicle.size, game_data)
    cost = max(0, vehicle.size)
    if baseline is not None:
        cost += max(0, vehicle.strength - baseline.strength)
        cost += max(0, vehicle.toughness - baseline.toughness)
        cost += max(0, vehicle.defense_modifier - baseline.defense)
    return cost


def item_platform_cost(item: EquipmentItem, game_data: GameData) -> int:
    """:func:`vehicle_trait_cost` for an item that is a vehicle, else ``0``.

    The seam :func:`~.equipment.item_own_ep_cost` adds to a derived price, so a
    platform with no printed number (the dimension hopper's ``"6 + movement effect
    cost"``) comes out at what its own line says instead of at the cost of its
    movement effect alone.
    """

    vehicle = item_vehicle(item, game_data)
    return 0 if vehicle is None else vehicle_trait_cost(vehicle, game_data)


def vehicle_defense_class(vehicle: StockVehicle, game_data: GameData, speed_rank: int) -> int:
    """The Defense Class of a vehicle **moving** at *speed_rank*.

    ``10 + Defense rank``, where a moving vehicle's Defense rank is its current speed
    rank plus its size's Defense modifier (§5 Combat). This is the one defence in the
    app that is dynamic per round: a parked car and a car at Speed 7 are very different
    targets, which is why the card shows both this and :func:`vehicle_stationary_dc`.
    """

    return game_data.system.defense_dc_base + speed_rank + vehicle.defense_modifier


def vehicle_stationary_dc(vehicle: StockVehicle, game_data: GameData) -> int:
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

    Nothing calls this yet: choosing modifiers for a vehicle needs the custom-platform
    builder, which is Phase 10's. It is here so the rule lives beside the records that
    describe it rather than being reconstructed from the data later.
    """

    catalog = {m.id: m for m in game_data.vehicle_modifiers}
    return sum(
        catalog[modifier_id].cost * max(0, advantage_ranks)
        for modifier_id in modifier_ids
        if modifier_id in catalog
    )


def _defenses_note(vehicle: StockVehicle, game_data: GameData) -> str:
    """``"(Impervious 4)"`` — the modifiers printed on a vehicle's Toughness, or ``""``.

    They are annotations on a trait rather than modifiers on an effect (the vehicle has
    no Protection effect to hang them off), so they are drawn on the trait grid.
    """

    catalog = game_data.modifier_catalog()
    parts = []
    for ref in vehicle.defenses:
        modifier = catalog.get(ref.modifier)
        name = modifier.name if modifier is not None else ref.modifier
        parts.append(f"{name} {ref.rank}" if ref.rank else name)
    return f" ({', '.join(parts)})" if parts else ""


def _speed_text(vehicle: StockVehicle, game_data: GameData) -> str:
    """``"Flight 9"`` — the movement the vehicle's Speed rank actually is.

    Named the same way :func:`~mm_companion.core.data_loader.vehicle_entry` builds it:
    the vehicle's own movement block if it carries one (a mole machine burrows),
    otherwise the effect its class measures Speed in. A dash when it has neither — a
    time machine's movement is a plot device, not a rank.
    """

    movement = vehicle.movement
    if movement is not None:
        effect_id, rank = movement.effect, movement.rank
    else:
        vehicle_class = game_data.vehicle_rules.vehicle_class(vehicle.vehicle_class)
        effect_id = vehicle_class.effect if vehicle_class else ""
        rank = vehicle.speed
    if not effect_id or rank is None:
        return "—"
    base = next((e for e in game_data.effects if e.id == effect_id), None)
    return f"{base.name if base else effect_id} {rank}"


def vehicle_trait_rows(item: EquipmentItem, game_data: GameData) -> list[EffectStat]:
    """A vehicle's platform traits as game-term rows, for the card that draws them.

    The same :class:`~.powers_terms.EffectStat` shape an effect's terms table uses, so
    the card renders both through one grid — a vehicle card differs from an item's in
    *what* it says, not in how it is laid out. Strength, Toughness and Defense carry
    their size baseline as the row's ``base`` and are tinted when they beat it, which
    is exactly what the build paid for.

    Empty for an item that is not a vehicle, which is how a caller asks the question.
    """

    vehicle = item_vehicle(item, game_data)
    if vehicle is None:
        return []
    rules = game_data.vehicle_rules
    baseline = vehicle_size_row(vehicle.size, game_data)
    vehicle_class = rules.vehicle_class(vehicle.vehicle_class)

    def compare(value: int, base: int | None) -> tuple[str, str]:
        """The row's ``base`` text and tint, against a size baseline that may not exist."""
        if base is None:
            return "", ""
        change = "better" if value > base else "worse" if value < base else ""
        return f"{base:+d}" if base < 0 else str(base), change

    rows = [
        EffectStat(
            key="vehicle_class",
            label="Class",
            base=vehicle.vehicle_class,
            value=vehicle_class.title if vehicle_class else vehicle.vehicle_class or "—",
        ),
        EffectStat(key="size", label="Size", base=str(vehicle.size), value=str(vehicle.size)),
    ]
    for key, label, value, base in (
        ("strength", "Strength", vehicle.strength, baseline.strength if baseline else None),
        (
            "toughness",
            "Toughness",
            vehicle.toughness,
            baseline.toughness if baseline else None,
        ),
    ):
        base_text, change = compare(value, base)
        suffix = _defenses_note(vehicle, game_data) if key == "toughness" else ""
        rows.append(
            EffectStat(
                key=key,
                label=label,
                base=base_text,
                value=f"{value}{suffix}",
                change=change,
            )
        )
    base_text, change = compare(vehicle.defense_modifier, baseline.defense if baseline else None)
    rows.append(
        EffectStat(
            key="defense",
            label="Defense",
            base=base_text,
            value=f"{vehicle.defense_modifier:+d}",
            change=change,
        )
    )
    rows.append(
        EffectStat(
            key="speed",
            label="Speed",
            base="",
            value=_speed_text(vehicle, game_data),
        )
    )
    moving = (
        vehicle_defense_class(vehicle, game_data, vehicle.speed)
        if vehicle.speed is not None
        else None
    )
    stationary = vehicle_stationary_dc(vehicle, game_data)
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
    return rows

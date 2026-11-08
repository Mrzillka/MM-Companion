"""Parsing: turn the measurements', equipment's, platforms', system rules' and blocks' raw
JSON dicts into their typed records.
"""

from __future__ import annotations

from dataclasses import fields, replace
from typing import TYPE_CHECKING

from .equipment_records import (
    CriticalProfile,
    EquipmentCategory,
    EquipmentEffectRef,
    EquipmentEntry,
    EquipmentModifierRef,
    EquipmentRules,
    InstallationRules,
    InstallationSizeRow,
    PlatformFeature,
    StockInstallation,
    StockVehicle,
    VehicleClass,
    VehicleModifier,
    VehicleRules,
    VehicleSizeExtension,
    VehicleSizeRow,
    VehicleWeapon,
)
from .game_data import (
    BlockFieldSpec,
    BlockSpec,
    Readout,
)
from .system_records import (
    DEFAULT_TRAIT_RANGES,
    QUICK_NPC_STATS,
    Costs,
    DamageDefenses,
    DefeatSystem,
    DerivedTrait,
    ExtraEffortRules,
    ExtraEffortUse,
    GroupCheckRules,
    ImprovisedEffectRules,
    Measurements,
    MinionSystem,
    Movement,
    PairedCap,
    PowerLevelCap,
    PowerLevelRules,
    PushableTrait,
    QuickNPCPair,
    QuickNPCPreset,
    QuickNPCRules,
    ReachRules,
    SizeEffect,
    SizeRow,
    SystemRules,
    TeamAttackRules,
    TraitCosts,
    TraitKeys,
    TraitRange,
)
from .traits import (
    ConditionCategory,
    ConditionGroup,
)

if TYPE_CHECKING:
    from .system_records import (
        RangeDistance,
    )


def _extras(raw: dict, *known: str) -> dict:
    """Any keys of *raw* the engine doesn't recognise, so a mod's extra JSON
    fields are retained on the record rather than silently dropped."""

    return {k: v for k, v in raw.items() if k not in known}


def _parse_measurements(raw: dict) -> Measurements:
    """Flatten ``rankMeasures`` into ``rank -> system -> column -> label``.

    Time is a single column shared by both systems, so it is copied into each.
    """

    by_rank: dict[int, dict[str, dict[str, str]]] = {}
    distance_m_by_rank: dict[int, float] = {}
    distance_ft_by_rank: dict[int, float] = {}
    for row in raw.get("rankMeasures", []):
        rank = int(row["rank"])
        time_label = row.get("time", {}).get("label", "")
        systems: dict[str, dict[str, str]] = {}
        for system in ("imperial", "metric"):
            block = row.get(system, {})
            labels = {
                col: block.get(col, {}).get("label", "") for col in ("mass", "distance", "volume")
            }
            labels["time"] = time_label
            systems[system] = labels
        by_rank[rank] = systems
        metric_distance = row.get("metric", {}).get("distance", {}).get("m")
        if metric_distance is not None:
            distance_m_by_rank[rank] = float(metric_distance)
        imperial_distance = row.get("imperial", {}).get("distance", {}).get("ft")
        if imperial_distance is not None:
            distance_ft_by_rank[rank] = float(imperial_distance)

    size_by_rank: dict[int, SizeRow] = {}
    for row in raw.get("sizeTable", []):
        size_rank = int(row["sizeRank"])
        size_by_rank[size_rank] = SizeRow(
            size_category=row["sizeCategory"],
            size_rank=size_rank,
            spaces=float(row["spaces"]),
            reach=int(row["reach"]),
            defense_mod=int(row["defenseMod"]),
            damage_mod=int(row["damageMod"]),
            toughness_mod=int(row["toughnessMod"]),
            speed_mod=int(row["speedMod"]),
            intimidation_mod=int(row["intimidationMod"]),
            stealth_mod=int(row["stealthMod"]),
        )
    size_effects = tuple(
        SizeEffect(
            column=entry["column"],
            category=entry["category"],
            target=entry["target"],
        )
        for entry in raw.get("sizeEffects", [])
    )
    reach_raw = raw.get("reachRules", {})
    return Measurements(
        by_rank=by_rank,
        size_by_rank=size_by_rank,
        distance_m_by_rank=distance_m_by_rank,
        distance_ft_by_rank=distance_ft_by_rank,
        size_effects=size_effects,
        size_rank_column=raw.get("sizeRankColumn", ""),
        reach_rules=ReachRules(
            space_distance_rank=int(reach_raw.get("spaceDistanceRank", 0)),
            baseline_size_rank=int(reach_raw.get("baselineSizeRank", 0)),
        ),
    )


def _parse_movement(raw: dict) -> Movement:
    return Movement(
        base_ground_speed_rank=int(raw.get("baseGroundSpeedRank", 1)),
        walk_rank_step=int(raw.get("walkRankStep", 0)),
        dash_rank_step=int(raw.get("dashRankStep", 1)),
        run_rank_step=int(raw.get("runRankStep", 2)),
        run_is_ground_only=bool(raw.get("runIsGroundOnly", True)),
        round_seconds=int(raw.get("roundSeconds", 6)),
        ground_mode=raw.get("groundMode", "ground"),
        ground_label=raw.get("groundLabel", "Ground speed"),
    )


def _parse_readouts(raw: dict) -> dict[str, tuple[Readout, ...]]:
    """Parse ``effect_readouts.json`` into ``effect id -> (Readout, ...)``.

    Each readout keeps its ``kind`` and ``label``; everything else on the entry is
    carried in ``data`` for the renderer to interpret per kind.
    """

    result: dict[str, tuple[Readout, ...]] = {}
    for effect_id, items in raw.get("effectReadouts", {}).items():
        result[effect_id] = tuple(
            Readout(
                kind=item["kind"],
                label=item.get("label", ""),
                data={k: v for k, v in item.items() if k not in ("kind", "label")},
            )
            for item in items
        )
    return result


def _unqualify(ref: object) -> str:
    """``"effects:damage"`` -> ``"damage"``; a bare id passes through.

    The equipment catalog names its effects, modifiers and advantages with the
    file they live in, which reads well in the data and is how the design
    reference writes them, but is not how those records are keyed. Splitting on
    the last colon is mechanical, so a mod inventing a namespace of its own gets
    the same treatment without the loader knowing anything about it.
    """

    return str(ref or "").rsplit(":", 1)[-1]


def _parse_equipment_effect_ref(raw: dict) -> EquipmentEffectRef:
    rank = raw.get("rank")
    return EquipmentEffectRef(
        effect=_unqualify(raw.get("effect")),
        rank=None if rank is None else int(rank),
        strength_based=bool(raw.get("strengthBased", False)),
        descriptors=tuple(str(d) for d in raw.get("descriptors", ())),
        config=dict(raw.get("config", {})),
        configuration=str(raw.get("configuration", "")),
        resistance=str(raw.get("resistance", "")),
        degrees=tuple(tuple(str(c) for c in degree) for degree in raw.get("degrees", ())),
        label=str(raw.get("label", raw.get("name", ""))),
        modifiers=tuple(_parse_equipment_modifier_ref(m) for m in raw.get("modifiers", ())),
        extra=_extras(
            raw,
            "effect",
            "rank",
            "strengthBased",
            "descriptors",
            "config",
            "configuration",
            "resistance",
            "degrees",
            "label",
            "name",
            "modifiers",
        ),
    )


def _parse_equipment_modifier_ref(raw: dict) -> EquipmentModifierRef:
    rank = raw.get("rank")
    return EquipmentModifierRef(
        modifier=_unqualify(raw.get("modifier")),
        rank=None if rank is None else int(rank),
        note=str(raw.get("note", "")),
        config=dict(raw["config"]) if isinstance(raw.get("config"), dict) else {},
        applies_to=max(0, int(raw.get("appliesTo", 0) or 0)),
        extra=_extras(raw, "modifier", "rank", "note", "config", "appliesTo"),
    )


def _parse_critical(raw: dict | None) -> CriticalProfile | None:
    if not isinstance(raw, dict):
        return None
    threat = tuple(int(v) for v in raw.get("threatRange", ()))[:2]
    return CriticalProfile(
        threat_range=threat if len(threat) == 2 else (20, 20),
        improved_critical_ranks=int(raw.get("improvedCriticalRanks", 0)),
    )


def _parse_equipment_entry(raw: dict) -> EquipmentEntry:
    cost = raw.get("cost")
    return EquipmentEntry(
        id=str(raw["id"]),
        name=str(raw.get("name", raw["id"])),
        category=str(raw.get("category", "")),
        subcategory=str(raw.get("subcategory") or ""),
        cost=None if cost is None else int(cost),
        cost_kind=str(raw.get("costKind", "fixed")),
        cost_note=str(raw.get("costNote", "")),
        description=str(raw.get("description", "")),
        effects=tuple(_parse_equipment_effect_ref(e) for e in raw.get("effects", ())),
        modifiers=tuple(_parse_equipment_modifier_ref(m) for m in raw.get("modifiers", ())),
        # Values are lists of ids in the same namespaced form as the refs above.
        grants={
            str(key): tuple(_unqualify(v) for v in values)
            for key, values in raw.get("grants", {}).items()
        },
        critical=_parse_critical(raw.get("critical")),
        patterns=tuple(str(p) for p in raw.get("patterns", ())),
        implementation=dict(raw.get("implementation", {})),
        extra=_extras(
            raw,
            "id",
            "name",
            "category",
            "subcategory",
            "cost",
            "costKind",
            "costNote",
            "description",
            "effects",
            "modifiers",
            "grants",
            "critical",
            "patterns",
            "implementation",
        ),
    )


def _parse_equipment_categories(raw: dict) -> tuple[EquipmentCategory, ...]:
    """The Equipment block's groups, from ``_meta.equipmentCategories``.

    Empty when a ruleset declares none — like the condition groups, and unlike
    the condition *categories*, there is nothing sensible to invent, and a block
    handed nothing falls back to grouping by the raw category id.
    """

    meta = raw.get("_meta", {})
    descriptions = meta.get("categoryKey", {})
    entries = meta.get("equipmentCategories")
    if not isinstance(entries, list):
        return ()
    return tuple(
        EquipmentCategory(
            id=str(entry["id"]),
            title=str(entry.get("title", entry["id"])),
            description=str(descriptions.get(entry["id"], "")),
        )
        for entry in entries
        if isinstance(entry, dict) and entry.get("id")
    )


def _parse_equipment_rules(raw: dict) -> EquipmentRules:
    meta = raw.get("_meta", {})
    currency = meta.get("currency", {})
    stacking = meta.get("stackingRule", {})
    breakage = meta.get("strengthBasedDamage", {}).get("breakage", {})
    defaults = EquipmentRules()
    return EquipmentRules(
        currency_name=str(currency.get("name", defaults.currency_name)),
        currency_abbreviation=str(currency.get("abbreviation", defaults.currency_abbreviation)),
        advantage=_unqualify(currency.get("source")) or defaults.advantage,
        points_per_advantage_rank=int(
            currency.get("pointsPerAdvantageRank", defaults.points_per_advantage_rank)
        ),
        stacking_rule=str(stacking.get("rule", "")),
        stacking_targets=tuple(str(t) for t in stacking.get("appliesTo", ())),
        cost_kinds=dict(meta.get("costKindKey", {})),
        material_toughness={
            str(material): int(toughness)
            for material, toughness in breakage.get("materialToughness", {}).items()
        },
        breakage_rule=str(breakage.get("rule", "")),
        extra=_extras(
            meta,
            "currency",
            "categoryKey",
            "equipmentCategories",
            "equipmentCategoriesNote",
            "costKindKey",
            "stackingRule",
            "strengthBasedDamage",
            "description",
            "vehicleCategory",
            "vehicleClasses",
            "vehicleClassesNote",
            "vehicleSizeExtension",
            "vehicleCombat",
            "installationCategory",
            "installationTraits",
            "installationPowerLevel",
        ),
    )


def _parse_vehicle_weapon(raw: dict) -> VehicleWeapon:
    return VehicleWeapon(
        name=str(raw.get("name", "")),
        effect=_unqualify(raw.get("effect")),
        rank=int(raw.get("rank", 0) or 0),
        modifiers=tuple(_parse_equipment_modifier_ref(m) for m in raw.get("modifiers", ())),
        note=str(raw.get("note", "")),
    )


def _parse_stock_vehicle(raw: dict) -> StockVehicle:
    cost = raw.get("cost")
    speed = raw.get("speed")
    movement = raw.get("movement")
    return StockVehicle(
        id=str(raw["id"]),
        name=str(raw.get("name", raw["id"])),
        vehicle_class=str(raw.get("class", "")),
        size=int(raw.get("size", 0) or 0),
        strength=int(raw.get("strength", 0) or 0),
        speed=None if speed is None else int(speed),
        defense_modifier=int(raw.get("defenseModifier", 0) or 0),
        toughness=int(raw.get("toughness", 0) or 0),
        cost=None if cost is None else int(cost),
        cost_kind=str(raw.get("costKind", "fixed")),
        cost_formula=str(raw.get("costFormula", "")),
        defenses=tuple(_parse_equipment_modifier_ref(d) for d in raw.get("defenses", ())),
        weapons=tuple(_parse_vehicle_weapon(w) for w in raw.get("weapons", ())),
        movement=_parse_equipment_effect_ref(movement) if isinstance(movement, dict) else None,
        patterns=tuple(str(p) for p in raw.get("patterns", ())),
        variants=str(raw.get("variants", "")),
        notes=str(raw.get("notes", "")),
        extra=_extras(
            raw,
            "id",
            "name",
            "class",
            "size",
            "strength",
            "speed",
            "defenseModifier",
            "toughness",
            "cost",
            "costKind",
            "costFormula",
            "defenses",
            "weapons",
            "movement",
            "patterns",
            "variants",
            "notes",
        ),
    )


def _parse_platform_feature(raw: dict) -> PlatformFeature:
    return PlatformFeature(
        id=str(raw["id"]),
        name=str(raw.get("name", raw["id"])),
        cost=int(raw.get("cost", 1) or 0),
        repeatable=bool(raw.get("repeatable", False)),
        description=str(raw.get("description", "")),
        dc_increase_per_extra_rank=int(raw.get("dcIncreasePerExtraRank", 0) or 0),
        max_dc_increase=int(raw.get("maxDcIncrease", 0) or 0),
        extra=_extras(
            raw,
            "id",
            "name",
            "cost",
            "repeatable",
            "description",
            "dcIncreasePerExtraRank",
            "maxDcIncrease",
        ),
    )


def _parse_vehicle_modifier(raw: dict) -> VehicleModifier:
    return VehicleModifier(
        id=str(raw["id"]),
        name=str(raw.get("name", raw["id"])),
        cost=int(raw.get("cost", 0) or 0),
        cost_kind=str(raw.get("costKind", "per_rank_flat")),
        description=str(raw.get("description", "")),
        extra=_extras(raw, "id", "name", "cost", "costKind", "description"),
    )


def _parse_vehicle_rules(raw: dict) -> VehicleRules:
    meta = raw.get("_meta", {})
    defaults = VehicleRules()
    extension = meta.get("vehicleSizeExtension", {})
    if not isinstance(extension, dict):  # a ruleset may state it as prose
        extension = {}
    return VehicleRules(
        category=str(meta.get("vehicleCategory", defaults.category)),
        classes=tuple(
            VehicleClass(
                id=str(entry["id"]),
                title=str(entry.get("title", entry["id"])),
                effect=_unqualify(entry.get("effect")),
            )
            for entry in meta.get("vehicleClasses", ())
            if isinstance(entry, dict) and entry.get("id")
        ),
        size_table=tuple(
            VehicleSizeRow(
                size_rank=int(row.get("sizeRank", 0) or 0),
                strength=int(row.get("strength", 0) or 0),
                toughness=int(row.get("toughness", 0) or 0),
                defense=int(row.get("defense", 0) or 0),
                examples=tuple(str(e) for e in row.get("examples", ())),
            )
            for row in raw.get("vehicleSizeTable", ())
            if isinstance(row, dict)
        ),
        size_extension=VehicleSizeExtension(
            strength=int(extension.get("strength", 2)),
            toughness=int(extension.get("toughness", 1)),
            defense=int(extension.get("defense", -1)),
            note=str(extension.get("note", "")),
        ),
        combat=dict(meta.get("vehicleCombat", {})),
    )


def vehicle_entry(vehicle: StockVehicle, rules: VehicleRules) -> EquipmentEntry:
    """A stock vehicle as an ordinary :class:`EquipmentEntry`.

    The one translation the vehicle layer needs, and it is a translation of *shape*
    rather than of rules: a platform's printed line names its movement and its weapons
    in a vehicle-shaped spelling, and this writes the same information in the catalog's.
    What comes out is priced, built, worn, rolled and grouped by the code that already
    exists — see :meth:`GameData.equipment_catalog`.

    Its movement effect is its own ``movement`` block when it has one, and otherwise
    the effect its :class:`VehicleClass` measures Speed in, at the printed speed rank.
    A vehicle whose class names no effect and that carries no block of its own (the
    time machine) has no movement effect. The five platform traits are **not** folded in: they
    are not effects, they cost points off their own table, and they are read straight
    off the :class:`StockVehicle` by the card that shows them.
    """

    effects: list[EquipmentEffectRef] = []
    movement = vehicle.movement
    if movement is None and vehicle.speed is not None:
        vehicle_class = rules.vehicle_class(vehicle.vehicle_class)
        if vehicle_class is not None and vehicle_class.effect:
            movement = EquipmentEffectRef(effect=vehicle_class.effect, rank=vehicle.speed)
    if movement is not None:
        effects.append(movement)
    effects.extend(
        EquipmentEffectRef(
            effect=weapon.effect,
            rank=weapon.rank,
            label=weapon.name,
            modifiers=weapon.modifiers,
        )
        for weapon in vehicle.weapons
    )
    description = " ".join(part for part in (vehicle.notes, vehicle.variants) if part)
    return EquipmentEntry(
        id=vehicle.id,
        name=vehicle.name,
        category=rules.category,
        cost=vehicle.cost,
        cost_kind=vehicle.cost_kind,
        cost_note=vehicle.cost_formula,
        description=description,
        effects=tuple(effects),
        patterns=vehicle.patterns,
    )


def _parse_stock_installation(raw: dict) -> StockInstallation:
    cost = raw.get("cost")
    return StockInstallation(
        id=str(raw["id"]),
        name=str(raw.get("name", raw["id"])),
        size=int(raw.get("size", 0) or 0),
        toughness=int(raw.get("toughness", 0) or 0),
        cost=None if cost is None else int(cost),
        cost_kind=str(raw.get("costKind", "fixed")),
        cost_formula=str(raw.get("costFormula", "")),
        features=tuple(_unqualify(f) for f in raw.get("features", ())),
        patterns=tuple(str(p) for p in raw.get("patterns", ())),
        notes=str(raw.get("notes", "")),
        extra=_extras(
            raw,
            "id",
            "name",
            "size",
            "toughness",
            "cost",
            "costKind",
            "costFormula",
            "features",
            "patterns",
            "notes",
        ),
    )


def _parse_installation_rules(raw: dict) -> InstallationRules:
    meta = raw.get("_meta", {})
    defaults = InstallationRules()
    traits = meta.get("installationTraits", {})
    if not isinstance(traits, dict):  # a ruleset may state it as prose
        traits = {}
    power_level = meta.get("installationPowerLevel", {})
    if not isinstance(power_level, dict):
        power_level = {}
    return InstallationRules(
        category=str(meta.get("installationCategory", defaults.category)),
        size_table=tuple(
            InstallationSizeRow(
                size_rank=int(row.get("sizeRank", 0) or 0),
                cost=int(row.get("cost", 0) or 0),
                examples=tuple(str(e) for e in row.get("examples", ())),
            )
            for row in raw.get("installationSizeTable", ())
            if isinstance(row, dict)
        ),
        free_size_rank=int(traits.get("freeSizeRank", defaults.free_size_rank)),
        free_toughness=int(traits.get("freeToughness", defaults.free_toughness)),
        toughness_per_point=max(
            1, int(traits.get("toughnessPerPoint", defaults.toughness_per_point))
        ),
        toughness_pl_multiple=int(
            power_level.get("toughnessMultiplier", defaults.toughness_pl_multiple)
        ),
        impervious_pl_multiple=int(
            power_level.get("imperviousMultiplier", defaults.impervious_pl_multiple)
        ),
        impervious_modifier=_unqualify(
            power_level.get("imperviousModifier", defaults.impervious_modifier)
        ),
        note=str(traits.get("note", "")),
    )


def installation_entry(installation: StockInstallation, rules: InstallationRules) -> EquipmentEntry:
    """A stock installation as an ordinary :class:`EquipmentEntry`.

    The installation twin of :func:`vehicle_entry`, and simpler for the same reason an
    installation is simpler than a vehicle: it has no movement and no weapons, so the
    entry it yields usually carries no effects at all. What it *is* — Size, Toughness
    and its Features — is read straight off the :class:`StockInstallation` by the one
    layer that needs it, exactly as a vehicle's five traits are. What this buys is
    everything around that: the picker lists it, the budget prices it, a card draws it
    and a save round-trips it, with no installation branch anywhere.
    """

    return EquipmentEntry(
        id=installation.id,
        name=installation.name,
        category=rules.category,
        cost=installation.cost,
        cost_kind=installation.cost_kind,
        cost_note=installation.cost_formula,
        description=installation.notes,
        patterns=installation.patterns,
    )


#: Used when ``conditions.json`` carries no ``_meta.sheetSections`` (an older file, or
#: a mod's), so the Conditions block still groups the base categories sensibly.
_DEFAULT_CONDITION_CATEGORIES = (
    ConditionCategory("condition", "General", addable=True),
    ConditionCategory("damage_condition", "Damage", addable=True),
)


def _parse_condition_categories(raw: dict) -> tuple[ConditionCategory, ...]:
    sections = raw.get("_meta", {}).get("sheetSections")
    if not isinstance(sections, list) or not sections:
        return _DEFAULT_CONDITION_CATEGORIES
    parsed = [
        ConditionCategory(
            category=str(entry["category"]),
            title=str(entry.get("title", entry["category"])),
            addable=bool(entry.get("addable", True)),
        )
        for entry in sections
        if isinstance(entry, dict) and entry.get("category")
    ]
    return tuple(parsed) or _DEFAULT_CONDITION_CATEGORIES


def _parse_condition_groups(raw: dict) -> tuple[ConditionGroup, ...]:
    """The "+" menu's submenus, from ``_meta.conditionGroups``.

    Empty when the file declares none — unlike the categories, there is no
    sensible default to invent here, and a menu builder handed nothing simply
    falls back to the flat alphabetical list it used to show.
    """
    groups = raw.get("_meta", {}).get("conditionGroups")
    if not isinstance(groups, list):
        return ()
    return tuple(
        ConditionGroup(
            group=str(entry["group"]),
            title=str(entry.get("title", entry["group"])),
        )
        for entry in groups
        if isinstance(entry, dict) and entry.get("group")
    )


def _parse_costs(raw: dict) -> Costs:
    # Tolerate unknown keys (e.g. from a mod) so they can't crash the loader.
    trait_fields = {f.name for f in fields(TraitCosts)}
    traits = TraitCosts(**{k: int(v) for k, v in raw["trait_costs"].items() if k in trait_fields})
    pl = raw["power_level"]
    caps = {
        name: PowerLevelCap(mult=int(cap["mult"]), add=int(cap["add"]))
        for name, cap in pl["caps"].items()
    }
    ranges = {
        family: TraitRange(
            min=int(entry.get("min", DEFAULT_TRAIT_RANGES.get(family, TraitRange()).min)),
            max=int(entry.get("max", DEFAULT_TRAIT_RANGES.get(family, TraitRange()).max)),
        )
        for family, entry in raw.get("trait_ranges", {}).items()
        if isinstance(entry, dict)
    }
    return Costs(
        traits=traits,
        power_level=PowerLevelRules(pp_per_level=int(pl["pp_per_level"]), caps=caps),
        trait_ranges=ranges,
    )


def _parse_improvised_effect(raw: object, default: ImprovisedEffectRules) -> ImprovisedEffectRules:
    """The Improvised Effect dials, each falling back to the built-in value."""

    if not isinstance(raw, dict):
        return default
    return ImprovisedEffectRules(
        min_time_rank=int(raw.get("minTimeRank", default.min_time_rank)),
        dc_per_time_rank_saved=int(raw.get("dcPerTimeRankSaved", default.dc_per_time_rank_saved)),
        check_bonus_per_time_rank_spent=int(
            raw.get("checkBonusPerTimeRankSpent", default.check_bonus_per_time_rank_spent)
        ),
    )


def _parse_extra_effort(raw: object, default: ExtraEffortRules) -> ExtraEffortRules:
    """The Extra Effort dials and its list of uses, each falling back to the built-in.

    A ruleset that names no ``uses`` gets the empty tuple rather than an invented list:
    the menus are built from it, and an empty one honestly offers nothing rather than
    offering benefits the ruleset never described.
    """

    if not isinstance(raw, dict):
        return default
    uses = tuple(
        ExtraEffortUse(
            id=str(use["id"]),
            label=str(use.get("label", use["id"])),
            description=str(use.get("description", "")),
            target=str(use.get("target", "")),
        )
        for use in raw.get("uses", [])
        if isinstance(use, dict) and use.get("id")
    )
    return ExtraEffortRules(
        fatigue_ladder=tuple(str(c) for c in raw.get("fatigueLadder", default.fatigue_ladder)),
        rank_increase=int(raw.get("rankIncrease", default.rank_increase)),
        check_bonus=int(raw.get("checkBonus", default.check_bonus)),
        permanent_durations=tuple(
            str(d) for d in raw.get("permanentDurations", default.permanent_durations)
        ),
        determination_advantage=str(
            raw.get("determinationAdvantage", default.determination_advantage)
        ),
        untapped_potential_advantage=str(
            raw.get("untappedPotentialAdvantage", default.untapped_potential_advantage)
        ),
        extraordinary_effort_advantage=str(
            raw.get("extraordinaryEffortAdvantage", default.extraordinary_effort_advantage)
        ),
        uses=uses or default.uses,
        pushable_traits=tuple(
            PushableTrait(
                category=str(t.get("category", "")),
                stat=str(t.get("stat", "")),
                label=str(t.get("label", "")),
                note=str(t.get("note", "")),
            )
            for t in raw.get("pushableTraits", ())
        )
        or default.pushable_traits,
    )


def _parse_range_distance(raw: dict | None, base: RangeDistance) -> RangeDistance | None:
    """A ``rangeDistance`` block laid over ``base``, or ``None`` when there is none.

    Only the keys the block actually names are overridden, so an effect that merely
    reaches further than its rank suggests writes ``{"offset": 2}`` and inherits the
    rest of the system-wide derivation.
    """

    if not raw:
        return None
    return replace(
        base,
        rank_source=raw.get("rankSource", base.rank_source),
        rank=None if raw.get("rank") is None else int(raw["rank"]),
        offset=int(raw.get("offset", base.offset)),
        steps=tuple(int(s) for s in raw["steps"]) if "steps" in raw else base.steps,
        step_labels=(tuple(raw["stepLabels"]) if "stepLabels" in raw else base.step_labels),
        range_value=raw.get("rangeValue", base.range_value),
        multiply_by_rank=bool(raw.get("multiplyByRank", base.multiply_by_rank)),
        distance_value=int(raw.get("distanceValue", base.distance_value)),
    )


def _parse_system(raw: dict) -> SystemRules:
    """Parse ``system.json`` into :class:`SystemRules`, tolerating unknown keys.

    Every field falls back to its dataclass default, so a mod (or a stripped file)
    can override only the keys it cares about.
    """

    sys = raw.get("system", raw)
    defaults = SystemRules()
    tk_raw = sys.get("trait_keys", {})
    trait_keys = TraitKeys(
        attack=tk_raw.get("attack", defaults.trait_keys.attack),
        defense=tk_raw.get("defense", defaults.trait_keys.defense),
        dodge=tk_raw.get("dodge", defaults.trait_keys.dodge),
        toughness=tk_raw.get("toughness", defaults.trait_keys.toughness),
        fortitude=tk_raw.get("fortitude", defaults.trait_keys.fortitude),
        will=tk_raw.get("will", defaults.trait_keys.will),
        attack_check=tk_raw.get("attack_check", defaults.trait_keys.attack_check),
    )
    paired_caps = tuple(
        PairedCap(cap=p["cap"], traits=tuple(p["traits"]), label=p["label"])
        for p in sys.get("paired_caps", [])
    )
    return SystemRules(
        default_initiative_ability=sys.get(
            "default_initiative_ability", defaults.default_initiative_ability
        ),
        defense_dc_base=int(sys.get("defense_dc_base", defaults.defense_dc_base)),
        heroic_budget_divisor=int(sys.get("heroic_budget_divisor", defaults.heroic_budget_divisor)),
        critical_effect_bonus=int(sys.get("criticalEffectBonus", defaults.critical_effect_bonus)),
        critical_miss_resistance_bonus=int(
            sys.get("criticalMissResistanceBonus", defaults.critical_miss_resistance_bonus)
        ),
        trait_keys=trait_keys,
        paired_caps=paired_caps,
        unscoped_scope_values=tuple(
            sys.get("unscoped_scope_values", defaults.unscoped_scope_values)
        ),
        alternate_effect_modifier=sys.get(
            "alternate_effect_modifier", defaults.alternate_effect_modifier
        ),
        linked_modifier=sys.get("linked_modifier", defaults.linked_modifier),
        linked_attack_bonus=str(sys.get("linkedAttackBonus", defaults.linked_attack_bonus)),
        improvised_effect=_parse_improvised_effect(
            sys.get("improvised_effect"), defaults.improvised_effect
        ),
        extra_effort=_parse_extra_effort(sys.get("extra_effort"), defaults.extra_effort),
        damage_effect=sys.get("damage_effect", defaults.damage_effect),
        damage_defenses=_parse_damage_defenses(sys.get("damage_defenses")),
        ranged_distance=_parse_range_distance(sys.get("ranged_distance"), defaults.ranged_distance)
        or defaults.ranged_distance,
        derived_traits=tuple(
            DerivedTrait(key=d["key"], label=d.get("label", d["key"]))
            for d in sys.get("derived_traits", [])
        ),
        quick_npc=_parse_quick_npc(sys.get("quick_npc"), defaults.quick_npc),
        minions=_parse_minions(sys.get("minions"), defaults.minions),
        defeat=_parse_defeat(sys.get("defeat"), defaults.defeat),
        team_attack=_parse_team_attack(sys.get("teamAttack"), defaults.team_attack),
        group_checks=_parse_group_checks(
            sys.get("teamCheck"), sys.get("groupCheck"), defaults.group_checks
        ),
    )


def _parse_group_checks(team: object, group: object, default: GroupCheckRules) -> GroupCheckRules:
    """Parse ``teamCheck`` and ``groupCheck``; a missing key keeps *default*'s."""

    team = team if isinstance(team, dict) else {}
    group = group if isinstance(group, dict) else {}
    return GroupCheckRules(
        helper_dc=int(team.get("helperDc", default.helper_dc)),
        bonus_per_degree=int(team.get("bonusPerDegree", default.bonus_per_degree)),
        success_share=float(group.get("successShare", default.success_share)),
    )


def _parse_team_attack(raw: object, default: TeamAttackRules) -> TeamAttackRules:
    """Parse ``system.json``'s ``teamAttack`` block; a missing key keeps *default*'s."""

    if not isinstance(raw, dict):
        return default
    steps = default.steps
    raw_steps = raw.get("steps")
    if isinstance(raw_steps, list):
        parsed = sorted(
            (int(s["degrees"]), int(s["bonus"]))
            for s in raw_steps
            if isinstance(s, dict) and "degrees" in s and "bonus" in s
        )
        steps = tuple(parsed) or default.steps
    return TeamAttackRules(
        rank_spread=int(raw.get("rankSpread", default.rank_spread)),
        steps=steps,
        critical_bonus=int(raw.get("criticalBonus", default.critical_bonus)),
    )


def _parse_defeat(raw: object, default: DefeatSystem) -> DefeatSystem:
    """Parse ``system.json``'s ``defeat`` block; a missing list keeps *default*'s."""

    if not isinstance(raw, dict):
        return default

    def ids(key: str, fallback: tuple[str, ...]) -> tuple[str, ...]:
        value = raw.get(key)
        if not isinstance(value, list):
            return fallback
        return tuple(str(v) for v in value if isinstance(v, str) and v)

    return DefeatSystem(down=ids("down", default.down), dead=ids("dead", default.dead))


def _parse_minions(raw: object, default: MinionSystem) -> MinionSystem:
    """Parse ``system.json``'s ``minions`` block; a missing key keeps *default*'s."""

    if not isinstance(raw, dict):
        return default
    return MinionSystem(
        impress_bonus=int(raw.get("impressBonus", default.impress_bonus)),
        ceiling=str(raw.get("ceiling", default.ceiling)),
    )


def _parse_damage_defenses(raw: object) -> DamageDefenses:
    """Parse ``system.json``'s ``damage_defenses``; a missing block defends nothing."""

    if not isinstance(raw, dict):
        return DamageDefenses()

    def ids(key: str) -> tuple[str, ...]:
        value = raw.get(key, ())
        return tuple(str(v) for v in value) if isinstance(value, list) else ()

    return DamageDefenses(
        roll_twice=ids("rollTwice"),
        two_degrees=ids("twoDegrees"),
        immune=ids("immune"),
        ignores_penetrating=ids("ignoresPenetrating"),
        penetrating=ids("penetrating"),
    )


def _parse_quick_npc(raw: object, default: QuickNPCRules) -> QuickNPCRules:
    """Parse ``system.json``'s ``quick_npc`` block; a missing one keeps *default*.

    A pair naming a stat outside :data:`QUICK_NPC_STATS` is dropped rather than
    raising, as is a preset with no id — a mod's typo should cost the one entry.
    """
    if not isinstance(raw, dict):
        return default
    high = raw.get("high_share", default.high_share)
    try:
        numerator, denominator = (int(v) for v in high)
        if denominator <= 0 or not 0 <= numerator <= denominator:
            raise ValueError
    except (TypeError, ValueError):
        numerator, denominator = default.high_share
    pairs = []
    for p in raw.get("pairs", []):
        stats = tuple(p.get("stats", ()))
        if len(stats) != 2 or not set(stats) <= set(QUICK_NPC_STATS) or not p.get("cap"):
            continue
        pairs.append(QuickNPCPair(cap=p["cap"], stats=stats, fill=bool(p.get("fill", True))))
    presets = tuple(
        QuickNPCPreset(
            id=str(p["id"]),
            name=str(p.get("name", p["id"])),
            description=str(p.get("description", "")),
            shares=tuple((str(k), str(v)) for k, v in (p.get("shares") or {}).items()),
        )
        for p in raw.get("presets", [])
        if p.get("id")
    )
    return QuickNPCRules(
        default_power_level=int(raw.get("default_power_level", default.default_power_level)),
        high_share=(numerator, denominator),
        pairs=tuple(pairs),
        presets=presets,
    )


def _parse_block_field(f: dict) -> BlockFieldSpec:
    return BlockFieldSpec(
        key=f.get("key", ""),
        label=f.get("label", ""),
        kind=f.get("kind", "text"),
        text=f.get("text", ""),
        extra=_extras(f, "key", "label", "kind", "text"),
    )


def _parse_block_spec(b: dict) -> BlockSpec:
    return BlockSpec(
        id=b["id"],
        title=b.get("title", ""),
        row=int(b.get("row", 0)),
        col=int(b.get("col", 0)),
        fields=tuple(_parse_block_field(f) for f in b.get("fields", [])),
        min_width=int(b.get("min_width", 0)),
        min_height=int(b.get("min_height", 0)),
        max_width=int(b.get("max_width", 0)),
        max_height=int(b.get("max_height", 0)),
        extra=_extras(
            b,
            "id",
            "title",
            "row",
            "col",
            "fields",
            "min_width",
            "min_height",
            "max_width",
            "max_height",
        ),
    )

"""Load MM-Companion game data from the bundled data files.

This module is the single entry point the UI uses to obtain rules *content*
(abilities, resistances, skills, advantages, conditions, point costs, ...).
Nothing here implements game rules; it only parses the JSON in
:mod:`mm_companion.data` into typed records so the UI never hardcodes that
content.

The records live in topic modules (:mod:`.traits`, :mod:`.power_records`,
:mod:`.system_records`, :mod:`.equipment_records`, :mod:`.game_data`), the parsers
in :mod:`.parsing_content` and :mod:`.parsing_rules`, the mod merge in
:mod:`.merge`; everything is re-exported here, so callers import from
:mod:`mm_companion.core.data_loader` as before.

Content is aggregated from several files: the core traits live in their own
files (``profile.json``, ``characteristics.json``, ``abilities.json``,
``resistances.json``), the richer 4e catalogs come from theirs (``skills.json``,
``advantages.json``, ``conditions.json``), the gear catalog from
``equipment.json``, and the point-cost constants from ``costs.json``.
"""

from __future__ import annotations

from .. import mods as mods_module
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
    ActionModifier,
    AreaCover,
    AreaEvasion,
    AreaRules,
    BlockFieldSpec,
    BlockSpec,
    CombatAction,
    CombatCheck,
    CombatEffect,
    CombatMenuSection,
    CombatOption,
    CombatRules,
    ConditionChange,
    DegreeOutcome,
    GameData,
    Impression,
    ImpressRules,
    KillRule,
    ManeuverLimits,
    OpposedStage,
    ReactionRule,
    Readout,
    RecoveryRule,
    ResistOptions,
    SlamRules,
    TargetedRules,
)
from .merge import (
    _deep_merge,
    _merge_content,
)
from .parsing_content import (
    _parse_ability,
    _parse_advantage,
    _parse_area,
    _parse_characteristic,
    _parse_combat,
    _parse_condition,
    _parse_configuration,
    _parse_duration_action_floor,
    _parse_effect,
    _parse_effect_modifiers,
    _parse_field,
    _parse_impress,
    _parse_ladders,
    _parse_modifier,
    _parse_resistance,
    _parse_skill,
)
from .parsing_rules import (
    _parse_block_spec,
    _parse_condition_categories,
    _parse_condition_groups,
    _parse_costs,
    _parse_equipment_categories,
    _parse_equipment_entry,
    _parse_equipment_rules,
    _parse_installation_rules,
    _parse_measurements,
    _parse_movement,
    _parse_platform_feature,
    _parse_readouts,
    _parse_stock_installation,
    _parse_stock_vehicle,
    _parse_system,
    _parse_vehicle_modifier,
    _parse_vehicle_rules,
    installation_entry,
    vehicle_entry,
)
from .power_records import (
    AllocationOption,
    BaseCostBy,
    ConfigOption,
    ConfiguredEffect,
    ConfiguredModifier,
    Effect,
    EffectConfigField,
    EffectReach,
    Measure,
    Modifier,
    Overcome,
    PowerConfiguration,
    RepeatableColumn,
    ResistanceGrant,
    ResistanceOutcome,
    SpeedRank,
    SubBuild,
)
from .system_records import (
    DEFAULT_TRAIT_RANGES,
    QUICK_NPC_STATS,
    AttackRanges,
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
    RangeDistance,
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
    Ability,
    Advantage,
    AmountMod,
    AttackMods,
    Characteristic,
    Condition,
    ConditionCategory,
    ConditionGroup,
    ConditionParameter,
    ConditionParameterOption,
    Debilitation,
    DefenseMod,
    Field,
    OpposingRule,
    ParameterSpec,
    RandomActionRow,
    RecoveryCheck,
    Resistance,
    ResistanceMod,
    Skill,
    StackingRule,
)

__all__ = [
    "Ability",
    "ActionModifier",
    "Advantage",
    "AmountMod",
    "AllocationOption",
    "AreaCover",
    "AreaEvasion",
    "AreaRules",
    "AttackMods",
    "BaseCostBy",
    "BlockFieldSpec",
    "BlockSpec",
    "Characteristic",
    "CombatAction",
    "CombatCheck",
    "CombatEffect",
    "CombatMenuSection",
    "CombatOption",
    "CombatRules",
    "ConditionChange",
    "DegreeOutcome",
    "OpposingRule",
    "OpposedStage",
    "ReactionRule",
    "TargetedRules",
    "Condition",
    "ConditionCategory",
    "ConditionGroup",
    "ConditionParameter",
    "ConditionParameterOption",
    "ConfigOption",
    "ConfiguredEffect",
    "ConfiguredModifier",
    "Costs",
    "CriticalProfile",
    "DATA_PACKAGE",
    "DEFAULT_TRAIT_RANGES",
    "DamageDefenses",
    "Debilitation",
    "AttackRanges",
    "DefeatSystem",
    "DefenseMod",
    "DerivedTrait",
    "Effect",
    "EffectConfigField",
    "EffectReach",
    "EquipmentCategory",
    "EquipmentEffectRef",
    "EquipmentEntry",
    "EquipmentModifierRef",
    "EquipmentRules",
    "ExtraEffortRules",
    "ExtraEffortUse",
    "Field",
    "GameData",
    "GroupCheckRules",
    "ImpressRules",
    "Impression",
    "ImprovisedEffectRules",
    "InstallationRules",
    "InstallationSizeRow",
    "KillRule",
    "ManeuverLimits",
    "RecoveryRule",
    "SlamRules",
    "Measure",
    "Measurements",
    "MinionSystem",
    "Modifier",
    "Movement",
    "PairedCap",
    "ParameterSpec",
    "PlatformFeature",
    "PowerConfiguration",
    "PowerLevelCap",
    "PowerLevelRules",
    "PushableTrait",
    "QUICK_NPC_STATS",
    "QuickNPCPair",
    "QuickNPCPreset",
    "QuickNPCRules",
    "RandomActionRow",
    "RangeDistance",
    "ReachRules",
    "Readout",
    "Overcome",
    "RecoveryCheck",
    "RepeatableColumn",
    "ResistOptions",
    "Resistance",
    "ResistanceGrant",
    "ResistanceMod",
    "ResistanceOutcome",
    "SizeEffect",
    "SizeRow",
    "Skill",
    "SpeedRank",
    "StackingRule",
    "StockInstallation",
    "StockVehicle",
    "SubBuild",
    "SystemRules",
    "TeamAttackRules",
    "TraitCosts",
    "TraitKeys",
    "TraitRange",
    "VehicleClass",
    "VehicleModifier",
    "VehicleRules",
    "VehicleSizeExtension",
    "VehicleSizeRow",
    "VehicleWeapon",
    "_deep_merge",
    "clear_game_data_cache",
    "installation_entry",
    "load_game_data",
    "vehicle_entry",
]

DATA_PACKAGE = "mm_companion"


def _build_game_data(content: dict[str, dict]) -> GameData:
    """Parse already-merged raw content into the typed :class:`GameData` record."""
    profile_raw = content.get("profile.json", {})
    characteristics_raw = content.get("characteristics.json", {})
    abilities_raw = content.get("abilities.json", {})
    resistances_raw = content.get("resistances.json", {})
    skills_raw = content.get("skills.json", {})
    advantages_raw = content.get("advantages.json", {})
    conditions_raw = content.get("conditions.json", {})
    effects_raw = content.get("effects.json", {})
    modifiers_raw = content.get("modifiers.json", {})
    effect_modifiers_raw = content.get("effect_modifiers.json", {})
    configurations_raw = content.get("configurations.json", {})
    effect_readouts_raw = content.get("effect_readouts.json", {})
    costs_raw = content.get("costs.json", {})
    system_raw = content.get("system.json", {})
    measurements_raw = content.get("measurements.json", {})
    movement_raw = content.get("movement.json", {})
    equipment_raw = content.get("equipment.json", {})
    blocks_raw = content.get("blocks.json", {})
    actions_raw = content.get("actions.json", {})

    # Parsed first: an effect's own ``rangeDistance`` block overrides only the keys it
    # names, so it needs the system-wide default to lay itself over.
    system = _parse_system(system_raw)
    # Likewise: a stock vehicle becomes a catalog entry against its own rules record
    # (which class means which movement effect, which category they file under).
    vehicle_rules = _parse_vehicle_rules(equipment_raw)
    vehicles = tuple(_parse_stock_vehicle(v) for v in equipment_raw.get("stockVehicles", []))
    installation_rules = _parse_installation_rules(equipment_raw)
    installations = tuple(
        _parse_stock_installation(i) for i in equipment_raw.get("stockInstallations", [])
    )

    return GameData(
        profile_fields=[_parse_field(f) for f in profile_raw.get("profile_fields", [])],
        characteristics=[
            _parse_characteristic(c) for c in characteristics_raw.get("characteristics", [])
        ],
        abilities=[_parse_ability(a) for a in abilities_raw.get("abilities", [])],
        resistances=[_parse_resistance(r) for r in resistances_raw.get("resistances", [])],
        skills=[_parse_skill(s) for s in skills_raw.get("skills", [])],
        advantages=[_parse_advantage(a) for a in advantages_raw.get("advantages", [])],
        conditions=[_parse_condition(c) for c in conditions_raw.get("conditions", [])],
        condition_categories=_parse_condition_categories(conditions_raw),
        condition_groups=_parse_condition_groups(conditions_raw),
        effects=[_parse_effect(e, system.ranged_distance) for e in effects_raw.get("effects", [])],
        modifiers=[_parse_modifier(m) for m in modifiers_raw.get("modifiers", [])],
        effect_modifiers=_parse_effect_modifiers(effect_modifiers_raw),
        configurations=tuple(
            _parse_configuration(c) for c in configurations_raw.get("configurations", [])
        ),
        costs=_parse_costs(costs_raw),
        measurements=_parse_measurements(measurements_raw),
        game_term_ladders=_parse_ladders(modifiers_raw),
        duration_action_floor=_parse_duration_action_floor(modifiers_raw),
        effect_readouts=_parse_readouts(effect_readouts_raw),
        movement=_parse_movement(movement_raw),
        equipment=tuple(_parse_equipment_entry(e) for e in equipment_raw.get("equipment", [])),
        equipment_categories=_parse_equipment_categories(equipment_raw),
        equipment_rules=_parse_equipment_rules(equipment_raw),
        vehicles=vehicles,
        vehicle_entries=tuple(vehicle_entry(v, vehicle_rules) for v in vehicles),
        vehicle_features=tuple(
            _parse_platform_feature(f) for f in equipment_raw.get("vehicleFeatures", [])
        ),
        vehicle_modifiers=tuple(
            _parse_vehicle_modifier(m) for m in equipment_raw.get("vehicleModifiers", [])
        ),
        vehicle_rules=vehicle_rules,
        installations=installations,
        installation_entries=tuple(
            installation_entry(i, installation_rules) for i in installations
        ),
        installation_features=tuple(
            _parse_platform_feature(f) for f in equipment_raw.get("installationFeatures", [])
        ),
        installation_rules=installation_rules,
        system=system,
        blocks=tuple(_parse_block_spec(b) for b in blocks_raw.get("blocks", [])),
        impress=_parse_impress(actions_raw),
        area=_parse_area(actions_raw),
        combat=_parse_combat(actions_raw),
    )


# Cache keyed on the active mod stack's fingerprint, so the base ruleset (and each
# distinct set of enabled mods) is parsed once per process. Replaces the former
# ``@lru_cache`` — clear it via :func:`clear_game_data_cache` after changing mods.
_game_data_cache: dict[tuple[str, ...], GameData] = {}


def load_game_data() -> GameData:
    """Parse and return the merged game data for the active mod stack (cached).

    With no mods enabled this is the bundled base ruleset, identical to before the
    mod pipeline existed. Enabled workspace mods (``enabled_mods`` setting) are
    deep-merged on top, later/higher-priority mods overriding earlier records.
    """
    mods = mods_module.active_mods()
    key = tuple(mod.fingerprint() for mod in mods)
    cached = _game_data_cache.get(key)
    if cached is None:
        cached = _build_game_data(_merge_content(mods))
        _game_data_cache[key] = cached
    return cached


def clear_game_data_cache() -> None:
    """Drop the cached game data; the next :func:`load_game_data` re-parses.

    Call after enabling/disabling a mod or editing a mod's files so the change is
    picked up (the cache key does not track file contents).
    """
    _game_data_cache.clear()

"""The equipment catalog: its records, its references, and its mod extensibility.

Phase 1 of the Equipment feature parses ``equipment.json`` into typed records. The
catalog is *data* — every mechanical field an item carries has to point at something
the rest of the game data actually defines, or the build layer that grows on top of
it in later phases has nothing to resolve.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mm_companion.core import mods, storage
from mm_companion.core.data_loader import (
    EquipmentEntry,
    clear_game_data_cache,
    load_game_data,
)


def test_catalog_loads_with_unique_ids() -> None:
    data = load_game_data()
    assert data.equipment
    ids = [item.id for item in data.equipment]
    assert len(ids) == len(set(ids))
    assert all(isinstance(item, EquipmentEntry) and item.name for item in data.equipment)


def test_equipment_catalog_is_an_id_lookup() -> None:
    """Items and vehicles, in one lookup.

    A stock vehicle enters the rules layer as one more catalog entry (Phase 9), which
    is what lets it be picked, priced, worn and drawn by the code that already exists.
    """
    data = load_game_data()
    catalog = data.equipment_catalog()
    assert len(catalog) == len(data.equipment) + len(data.vehicle_entries)
    assert catalog["crossbow"].name == "Crossbow"
    assert catalog["tank"].name == "Tank"


def test_item_effects_and_modifiers_name_real_records() -> None:
    """The catalog's build refs resolve into the powers layer.

    This is what makes an item a real ``Power`` under the skin in Phase 3: an
    unresolvable id would only surface there, as an item that silently builds
    into nothing.
    """
    data = load_game_data()
    effects = {effect.id for effect in data.effects}
    modifiers = set(data.modifier_catalog())
    advantages = {advantage.id for advantage in data.advantages}

    for item in data.equipment:
        for ref in item.effects:
            assert ref.effect in effects, f"{item.id} names unknown effect {ref.effect!r}"
        for ref in item.modifiers:
            assert ref.modifier in modifiers, f"{item.id} names unknown modifier {ref.modifier!r}"
        for granted in item.grants.get("advantages", ()):
            assert granted in advantages, f"{item.id} grants unknown advantage {granted!r}"


def test_references_are_unqualified() -> None:
    """The JSON writes ``"effects:damage"``; the record carries ``"damage"``."""
    data = load_game_data()
    for item in data.equipment:
        assert ":" not in item.id
        for ref in item.effects:
            assert ":" not in ref.effect
        for ref in item.modifiers:
            assert ":" not in ref.modifier
        for granted in item.grants.get("advantages", ()):
            assert ":" not in granted


def test_every_item_lands_in_a_declared_category() -> None:
    """The categories are the block's grouping axis, so an item outside them
    would have nowhere to be drawn."""
    data = load_game_data()
    assert data.equipment_categories
    declared = {category.id for category in data.equipment_categories}
    assert all(category.title for category in data.equipment_categories)
    for item in data.equipment:
        assert item.category in declared, f"{item.id} is in undeclared category {item.category!r}"


def test_categories_carry_their_description_from_the_category_key() -> None:
    data = load_game_data()
    weapons = next(c for c in data.equipment_categories if c.id == "close_weapon")
    assert weapons.title == "Close Weapons"
    assert weapons.description


def test_cost_kinds_are_declared_and_priced_consistently() -> None:
    data = load_game_data()
    kinds = data.equipment_rules.cost_kinds
    assert kinds
    for item in data.equipment:
        assert item.cost_kind in kinds
        # Only an item assembled from a trait table may go without a printed price.
        if item.cost is None:
            assert item.cost_kind == "built", f"{item.id} has no cost but is {item.cost_kind!r}"
        # Anything not a flat price has to say how the price is worked out.
        if item.cost_kind != "fixed":
            assert item.cost_note, f"{item.id} is {item.cost_kind!r} with no costNote"


def test_equipment_rules_describe_the_second_currency() -> None:
    """Equipment Points are bought with ranks of a real advantage, 5 per rank."""
    data = load_game_data()
    rules = data.equipment_rules
    assert rules.points_per_advantage_rank == 5
    assert rules.advantage in {advantage.id for advantage in data.advantages}
    assert rules.currency_abbreviation == "EP"
    # The no-stacking rule names the stats it governs; the resolver reads these.
    assert rules.stacking_targets
    assert rules.stacking_rule


def test_item_mechanics_are_retained() -> None:
    """A weapon's whole printed line survives the parse."""
    crossbow = load_game_data().equipment_catalog()["crossbow"]
    damage = crossbow.effects[0]
    assert (damage.effect, damage.rank) == ("damage", 3)
    assert damage.descriptors == ("piercing",)
    assert [ref.modifier for ref in crossbow.modifiers] == ["ranged"]
    assert crossbow.grants["advantages"] == ("improved_critical",)
    assert crossbow.critical is not None
    assert crossbow.critical.threat_range == (19, 20)
    assert crossbow.critical.improved_critical_ranks == 1
    assert "attack_item" in crossbow.patterns
    # ``implementation`` stays an open bag — the engine grows into its keys.
    assert crossbow.implementation["range"] == "ranged"


def test_an_affliction_item_keeps_its_degree_ladder() -> None:
    taser = load_game_data().equipment_catalog()["taser"]
    affliction = taser.effects[0]
    assert affliction.effect == "affliction"
    assert affliction.resistance == "Fortitude"
    assert affliction.degrees == (("dazed",), ("stunned",), ("incapacitated",))


# --- Vehicles ---------------------------------------------------------------


def test_stock_vehicles_parse_with_their_platform_traits() -> None:
    data = load_game_data()
    assert data.vehicles
    ids = [vehicle.id for vehicle in data.vehicles]
    assert len(ids) == len(set(ids))

    tank = data.vehicle_catalog()["tank"]
    assert (tank.size, tank.strength, tank.speed) == (2, 10, 6)
    assert (tank.defense_modifier, tank.toughness) == (-2, 12)
    assert tank.vehicle_class == "ground"
    assert tank.patterns == ("platform",)


def test_a_vehicles_weapons_and_defenses_resolve() -> None:
    """Every reference a platform makes has to name something the game data defines."""
    data = load_game_data()
    effects = {effect.id for effect in data.effects}
    modifiers = set(data.modifier_catalog())

    for vehicle in data.vehicles:
        for weapon in vehicle.weapons:
            assert weapon.name, f"{vehicle.id} has an unnamed weapon"
            assert weapon.effect in effects, f"{vehicle.id} names unknown effect"
            for ref in weapon.modifiers:
                assert ref.modifier in modifiers, f"{vehicle.id} names unknown modifier"
        for ref in vehicle.defenses:
            assert ref.modifier in modifiers
        if vehicle.movement is not None:
            assert vehicle.movement.effect in effects


def test_a_weapons_own_modifiers_carry_their_own_ranks() -> None:
    """The tank's cannon is Area 6; its machine gun is not Area at all.

    The design file wrote these as sibling ``areaRank``/``homingRank`` keys; the
    shipped schema puts the rank on the modifier it belongs to, which is what lets a
    per-effect modifier list exist at all.
    """
    tank = load_game_data().vehicle_catalog()["tank"]
    cannon, machine_gun = tank.weapons
    assert [(m.modifier, m.rank) for m in cannon.modifiers] == [
        ("ranged", None),
        ("area_effect", 6),
    ]
    assert [m.modifier for m in machine_gun.modifiers] == ["ranged", "multiattack"]


def test_a_vehicle_class_names_the_movement_effect_its_speed_is() -> None:
    """A plane's Speed is Flight and a boat's is Swimming — an axis in the data."""
    rules = load_game_data().vehicle_rules
    assert rules.category == "vehicle"
    assert {c.id: c.effect for c in rules.classes} == {
        "ground": "speed",
        "water": "swimming",
        "air": "flight",
        "space": "flight",
        "exotic": "",
    }


def test_vehicle_entries_carry_the_movement_and_the_named_weapons() -> None:
    data = load_game_data()
    entry = data.equipment_catalog()["tank"]
    assert entry.category == "vehicle"
    assert entry.cost == 76
    assert [(ref.effect, ref.rank, ref.label) for ref in entry.effects] == [
        ("speed", 6, ""),
        ("damage", 10, "Cannon"),
        ("damage", 7, "Heavy machine gun"),
    ]


def test_an_exotic_vehicle_uses_its_own_movement_block() -> None:
    """Its class names no default, so what it carries is the only answer."""
    data = load_game_data()
    mole = data.equipment_catalog()["mole_machine"]
    assert [(ref.effect, ref.rank) for ref in mole.effects] == [("burrowing", 6)]
    # And one with neither gets no movement effect rather than an invented one.
    assert load_game_data().equipment_catalog()["time_machine"].effects == ()


def test_the_vehicle_size_table_and_its_extension_parse() -> None:
    rules = load_game_data().vehicle_rules
    assert [row.size_rank for row in rules.size_table] == [0, 1, 2, 3, 4, 5]
    top = rules.size_table[-1]
    assert (top.strength, top.toughness, top.defense) == (12, 10, -5)
    assert (
        rules.size_extension.strength,
        rules.size_extension.toughness,
        rules.size_extension.defense,
    ) == (2, 1, -1)


def test_vehicle_features_and_modifiers_parse() -> None:
    data = load_game_data()
    features = data.vehicle_feature_catalog()
    assert features["alarm"].cost == 1 and features["alarm"].repeatable is True
    assert features["caltrops"].repeatable is False

    modifiers = {m.id: m for m in data.vehicle_modifiers}
    assert modifiers["durable"].cost == 1
    # A vehicle modifier prices the *advantage*, so it must never join the modifier
    # catalog the powers cost engine walks.
    assert "durable" not in data.modifier_catalog()


# --- Mod extensibility ------------------------------------------------------


@pytest.fixture()
def _home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv(storage.HOME_ENV_VAR, str(tmp_path))
    clear_game_data_cache()
    yield tmp_path
    clear_game_data_cache()


def _write_mod(payload: dict) -> None:
    """Drop a one-file data-only mod carrying *payload* as ``equipment.json``."""
    root = storage.ensure_workspace().mods_dir / "gear-mod"
    root.mkdir(parents=True)
    (root / "mod.json").write_text(
        json.dumps(
            {
                "id": "gear-mod",
                "name": "Gear Mod",
                "version": "1",
                "priority": 10,
                "files": ["equipment.json"],
            }
        ),
        encoding="utf-8",
    )
    (root / "equipment.json").write_text(json.dumps(payload), encoding="utf-8")
    mods.set_mod_enabled("gear-mod", True)
    clear_game_data_cache()


def test_a_mod_adds_an_item_and_a_category(_home: Path) -> None:
    _write_mod(
        {
            "_meta": {
                "categoryKey": {"relic": "An artefact of uncertain provenance"},
                "equipmentCategories": [{"id": "relic", "title": "Relics"}],
            },
            "equipment": [
                {
                    "id": "moon_shard",
                    "name": "Moon Shard",
                    "category": "relic",
                    "cost": 4,
                    "costKind": "fixed",
                    "description": "A sliver of cold light.",
                    "effects": [{"effect": "effects:damage", "rank": 2}],
                    "patterns": ["attack_item"],
                }
            ],
        },
    )

    data = load_game_data()
    catalog = data.equipment_catalog()
    assert catalog["moon_shard"].effects[0].effect == "damage"
    # The base catalog is extended, not replaced.
    assert "crossbow" in catalog
    ids = [category.id for category in data.equipment_categories]
    assert "close_weapon" in ids  # the shipped axis survives
    assert ids[-1] == "relic"  # a mod's group lands after the shipped ones
    relic = next(c for c in data.equipment_categories if c.id == "relic")
    assert relic.title == "Relics"
    assert relic.description == "An artefact of uncertain provenance"


def test_a_mod_overrides_one_field_of_a_base_item(_home: Path) -> None:
    _write_mod({"equipment": [{"id": "crossbow", "cost": 9}]})

    crossbow = load_game_data().equipment_catalog()["crossbow"]
    assert crossbow.cost == 9
    # Everything the mod didn't restate survives the merge.
    assert crossbow.effects[0].rank == 3
    assert crossbow.critical is not None and crossbow.critical.threat_range == (19, 20)


def test_a_mod_adds_a_vehicle_and_its_class(_home: Path) -> None:
    """The catalog extension the deep merge gives for free reaches platforms too."""
    _write_mod(
        {
            "_meta": {"vehicleClasses": [{"id": "aether", "title": "Aether", "effect": "flight"}]},
            "stockVehicles": [
                {
                    "id": "sky_barge",
                    "name": "Sky Barge",
                    "class": "aether",
                    "size": 3,
                    "strength": 9,
                    "speed": 5,
                    "defenseModifier": -3,
                    "toughness": 9,
                    "cost": 20,
                    "costKind": "fixed",
                    "patterns": ["platform"],
                }
            ],
        }
    )

    data = load_game_data()
    barge = data.vehicle_catalog()["sky_barge"]
    assert barge.speed == 5
    assert "tank" in data.vehicle_catalog()  # the shipped platforms survive
    entry = data.equipment_catalog()["sky_barge"]
    assert entry.category == "vehicle"
    assert [(ref.effect, ref.rank) for ref in entry.effects] == [("flight", 5)]


def test_a_mod_overrides_one_field_of_a_stock_vehicle(_home: Path) -> None:
    _write_mod({"stockVehicles": [{"id": "tank", "toughness": 14}]})

    tank = load_game_data().vehicle_catalog()["tank"]
    assert tank.toughness == 14
    assert len(tank.weapons) == 2  # what the mod didn't restate survives

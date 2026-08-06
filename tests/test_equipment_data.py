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
    data = load_game_data()
    catalog = data.equipment_catalog()
    assert len(catalog) == len(data.equipment)
    assert catalog["crossbow"].name == "Crossbow"


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

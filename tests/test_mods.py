"""The mod pipeline: discovery, load order, and content deep-merge.

The base ruleset is itself a mod (``data/mod.json``); a workspace mod under the
``mods/`` dir layers on top, overriding records by id and adding new ones.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mm_companion.core import mods, storage
from mm_companion.core.data_loader import (
    _deep_merge,
    clear_game_data_cache,
    load_game_data,
)


@pytest.fixture(autouse=True)
def _home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv(storage.HOME_ENV_VAR, str(tmp_path))
    # The game-data cache is process-global; keep tests isolated from each other.
    clear_game_data_cache()
    yield tmp_path
    clear_game_data_cache()


def _write_mod(root: Path, mod_id: str, files: dict[str, dict], *, priority: int = 0) -> Path:
    """Create a workspace mod dir with a manifest plus the given content files.

    *files* maps content filename (e.g. ``"effects.json"``) to its parsed payload.
    """
    mod_dir = root / storage.MODS_DIRNAME / mod_id
    mod_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "id": mod_id,
        "name": mod_id.title(),
        "version": "1.0",
        "priority": priority,
        "files": list(files),
    }
    (mod_dir / mods.MANIFEST_FILENAME).write_text(json.dumps(manifest), encoding="utf-8")
    for filename, payload in files.items():
        (mod_dir / filename).write_text(json.dumps(payload), encoding="utf-8")
    return mod_dir


# --- workspace layout -------------------------------------------------------


def test_ensure_workspace_creates_the_mods_dir(_home: Path) -> None:
    ws = storage.ensure_workspace()
    assert ws.mods_dir.is_dir()


# --- deep_merge unit behavior ----------------------------------------------


def test_deep_merge_records_by_id_override_and_append() -> None:
    base = {"items": [{"id": "a", "v": 1}, {"id": "b", "v": 2}]}
    override = {"items": [{"id": "b", "v": 99}, {"id": "c", "v": 3}]}
    merged = _deep_merge(base, override)
    assert merged["items"] == [
        {"id": "a", "v": 1},
        {"id": "b", "v": 99},  # overridden in place, order preserved
        {"id": "c", "v": 3},  # appended
    ]


def test_deep_merge_overrides_only_supplied_fields_of_a_record() -> None:
    base = {"items": [{"id": "a", "keep": 1, "change": 2}]}
    override = {"items": [{"id": "a", "change": 20}]}
    merged = _deep_merge(base, override)
    assert merged["items"] == [{"id": "a", "keep": 1, "change": 20}]


def test_deep_merge_replaces_non_record_lists_wholesale() -> None:
    base = {"options": ["x", "y", "z"]}
    override = {"options": ["only"]}
    assert _deep_merge(base, override) == {"options": ["only"]}


# --- discovery & load order -------------------------------------------------


def test_base_mod_reads_bundled_content() -> None:
    base = mods.base_mod()
    assert base.id == mods.BASE_MOD_ID
    assert base.is_base
    assert "effects.json" in base.files
    assert base.read("effects.json")["effects"]  # bundled content parses


def test_discover_and_order_workspace_mods(_home: Path) -> None:
    _write_mod(_home, "low", {"advantages.json": {"advantages": []}}, priority=1)
    _write_mod(_home, "high", {"advantages.json": {"advantages": []}}, priority=10)
    found = {m.id for m in mods.discover_workspace_mods()}
    assert found == {"low", "high"}
    # active_mods: base first, then enabled ordered by priority (higher applies last).
    active = mods.active_mods(enabled=["high", "low"])
    assert [m.id for m in active] == ["base", "low", "high"]


def test_active_mods_ignores_unknown_and_disabled(_home: Path) -> None:
    _write_mod(_home, "present", {"advantages.json": {"advantages": []}})
    active = mods.active_mods(enabled=["absent"])  # 'present' not enabled, 'absent' unknown
    assert [m.id for m in active] == ["base"]


def test_malformed_manifest_is_skipped(_home: Path) -> None:
    bad = _home / storage.MODS_DIRNAME / "broken"
    bad.mkdir(parents=True)
    (bad / mods.MANIFEST_FILENAME).write_text("{ not json", encoding="utf-8")
    assert mods.discover_workspace_mods() == []


# --- end-to-end merge through load_game_data --------------------------------


def test_mod_overrides_effect_cost_and_adds_advantage(_home: Path) -> None:
    base_data = load_game_data()
    base_damage = next(e for e in base_data.effects if e.id == "damage")
    assert base_damage.base_cost_value == 1
    assert not any(a.id == "brand_new_advantage" for a in base_data.advantages)

    _write_mod(
        _home,
        "testmod",
        {
            "effects.json": {"effects": [{"id": "damage", "baseCostValue": 3}]},
            "advantages.json": {
                "advantages": [
                    {
                        "id": "brand_new_advantage",
                        "name": "Brand New Advantage",
                        "types": ["Fortune"],
                        "ranked": False,
                        "maxRank": None,
                        "maxRankKind": "none",
                        "focused": False,
                        "description": "Added by a mod.",
                    }
                ]
            },
        },
    )
    storage.update_settings(enabled_mods=["testmod"])
    clear_game_data_cache()

    data = load_game_data()
    damage = next(e for e in data.effects if e.id == "damage")
    assert damage.base_cost_value == 3  # overridden by the mod
    assert damage.name == base_damage.name  # untouched fields survive the merge
    new_adv = next(a for a in data.advantages if a.id == "brand_new_advantage")
    assert new_adv.name == "Brand New Advantage"
    # Base advantages remain present alongside the added one.
    assert len(data.advantages) == len(base_data.advantages) + 1


def test_disabled_mods_leave_base_content_identical(_home: Path) -> None:
    _write_mod(
        _home, "testmod", {"effects.json": {"effects": [{"id": "damage", "baseCostValue": 99}]}}
    )
    # No enabled_mods set -> base only.
    data = load_game_data()
    damage = next(e for e in data.effects if e.id == "damage")
    assert damage.base_cost_value == 1

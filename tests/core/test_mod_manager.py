"""Mod-manager core seams: manifest options, user-order loading, options, import.

Covers the additions that back the Mod Manager window — pure ``core`` behaviour,
exercised against a temp workspace (``MM_COMPANION_HOME``). GUI is a thin driver
over these.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mm_companion.core import mods, storage
from mm_companion.core.data_loader import clear_game_data_cache


@pytest.fixture(autouse=True)
def _home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv(storage.HOME_ENV_VAR, str(tmp_path))
    clear_game_data_cache()
    yield tmp_path
    clear_game_data_cache()


def _write_mod(home: Path, manifest: dict) -> Path:
    """Create a workspace mod directory holding *manifest* as its mod.json."""
    dest = storage.ensure_workspace().mods_dir / manifest["id"]
    dest.mkdir(parents=True)
    (dest / mods.MANIFEST_FILENAME).write_text(json.dumps(manifest), encoding="utf-8")
    return dest


# --- manifest parsing -------------------------------------------------------


def test_description_and_options_parse() -> None:
    mod = mods._mod_from_manifest(
        {
            "id": "m",
            "description": "A helpful mod.",
            "options": [
                {"id": "hard", "label": "Hard mode", "type": "bool", "default": True},
                {"id": "count", "type": "number", "default": 3},
                {
                    "id": "flavor",
                    "type": "choice",
                    "default": "spicy",
                    "choices": ["mild", "spicy"],
                },
            ],
        },
        is_base=False,
        root=None,
    )
    assert mod.description == "A helpful mod."
    assert [o.id for o in mod.options] == ["hard", "count", "flavor"]
    hard, count, flavor = mod.options
    assert hard.label == "Hard mode" and hard.type == "bool" and hard.default is True
    assert count.label == "count"  # defaults to id
    assert flavor.type == "choice" and flavor.choices == ("mild", "spicy")


def test_absent_fields_default_cleanly() -> None:
    mod = mods._mod_from_manifest({"id": "m"}, is_base=False, root=None)
    assert mod.description == ""
    assert mod.options == ()


def test_unknown_option_type_falls_back_to_text() -> None:
    mod = mods._mod_from_manifest(
        {"id": "m", "options": [{"id": "x", "type": "bogus"}]}, is_base=False, root=None
    )
    assert mod.options[0].type == "text"


# --- user-order-wins loading ------------------------------------------------


def test_active_mods_orders_by_mod_order_ignoring_priority(_home: Path) -> None:
    _write_mod(_home, {"id": "a", "priority": 100})
    _write_mod(_home, {"id": "b", "priority": 1})
    # Priority would put b before a; the user order says otherwise.
    active = mods.active_mods(enabled=["a", "b"], order=["a", "b"])
    assert [m.id for m in active] == ["base", "a", "b"]

    active = mods.active_mods(enabled=["a", "b"], order=["b", "a"])
    assert [m.id for m in active] == ["base", "b", "a"]


def test_active_mods_appends_unordered_by_priority(_home: Path) -> None:
    _write_mod(_home, {"id": "a", "priority": 100})
    _write_mod(_home, {"id": "b", "priority": 1})
    _write_mod(_home, {"id": "c", "priority": 50})
    # Only "a" is placed by order; b and c trail it, seeded by ascending priority.
    active = mods.active_mods(enabled=["a", "b", "c"], order=["a"])
    assert [m.id for m in active] == ["base", "a", "b", "c"]


def test_active_mods_ignores_disabled_and_unknown(_home: Path) -> None:
    _write_mod(_home, {"id": "a"})
    active = mods.active_mods(enabled=["a", "ghost"], order=["ghost", "a"])
    assert [m.id for m in active] == ["base", "a"]


def test_set_mod_order_roundtrip(_home: Path) -> None:
    assert mods.set_mod_order(["b", "a"]) == ["b", "a"]
    assert storage.load_settings()["mod_order"] == ["b", "a"]


# --- per-mod options --------------------------------------------------------


def test_mod_option_values_merges_defaults_and_overrides(_home: Path) -> None:
    mod = mods._mod_from_manifest(
        {
            "id": "m",
            "options": [
                {"id": "hard", "type": "bool", "default": False},
                {"id": "count", "type": "number", "default": 3},
            ],
        },
        is_base=False,
        root=None,
    )
    # No overrides yet -> pure defaults.
    assert mods.mod_option_values("m", mod) == {"hard": False, "count": 3}
    mods.set_mod_options("m", {"hard": True})
    assert mods.mod_option_values("m", mod) == {"hard": True, "count": 3}


def test_mod_option_values_drops_undeclared_overrides(_home: Path) -> None:
    mod = mods._mod_from_manifest(
        {"id": "m", "options": [{"id": "hard", "type": "bool", "default": False}]},
        is_base=False,
        root=None,
    )
    mods.set_mod_options("m", {"hard": True, "stale": 99})
    assert mods.mod_option_values("m", mod) == {"hard": True}


# --- importing a mod folder -------------------------------------------------


def test_import_mod_folder_copies_valid_folder(_home: Path, tmp_path: Path) -> None:
    source = tmp_path / "incoming"
    source.mkdir()
    (source / mods.MANIFEST_FILENAME).write_text(
        json.dumps({"id": "cool", "name": "Cool Mod"}), encoding="utf-8"
    )
    (source / "extra.txt").write_text("hi", encoding="utf-8")

    mod = mods.import_mod_folder(source)
    dest = storage.get_workspace().mods_dir / "cool"
    assert mod.id == "cool"
    assert (dest / mods.MANIFEST_FILENAME).exists()
    assert (dest / "extra.txt").read_text(encoding="utf-8") == "hi"


def test_import_mod_folder_rejects_missing_manifest(tmp_path: Path) -> None:
    source = tmp_path / "empty"
    source.mkdir()
    with pytest.raises(mods.ModImportError):
        mods.import_mod_folder(source)


def test_import_mod_folder_rejects_missing_id(tmp_path: Path) -> None:
    source = tmp_path / "noid"
    source.mkdir()
    (source / mods.MANIFEST_FILENAME).write_text(json.dumps({"name": "x"}), encoding="utf-8")
    with pytest.raises(mods.ModImportError):
        mods.import_mod_folder(source)


def test_import_mod_folder_rejects_reserved_id(tmp_path: Path) -> None:
    source = tmp_path / "base"
    source.mkdir()
    (source / mods.MANIFEST_FILENAME).write_text(json.dumps({"id": "base"}), encoding="utf-8")
    with pytest.raises(mods.ModImportError):
        mods.import_mod_folder(source)


def test_import_mod_folder_rejects_duplicate(_home: Path, tmp_path: Path) -> None:
    _write_mod(_home, {"id": "dup"})
    source = tmp_path / "dup-src"
    source.mkdir()
    (source / mods.MANIFEST_FILENAME).write_text(json.dumps({"id": "dup"}), encoding="utf-8")
    with pytest.raises(mods.ModImportError):
        mods.import_mod_folder(source)


# --- removing a mod ---------------------------------------------------------


def test_remove_mod_deletes_folder_and_cleans_settings(_home: Path) -> None:
    root = _write_mod(_home, {"id": "gone"})
    mods.set_mod_enabled("gone", True)
    mods.set_mod_trusted("gone", True)
    mods.set_mod_order(["gone", "other"])
    mods.set_mod_options("gone", {"x": 1})

    removed = mods.remove_mod("gone")

    assert removed == root
    assert not root.exists()
    settings = storage.load_settings()
    assert settings["enabled_mods"] == []
    assert settings["trusted_mods"] == []
    assert settings["mod_order"] == ["other"]
    assert "gone" not in settings["mod_options"]


def test_remove_mod_absent_is_noop(_home: Path) -> None:
    # Cleans settings even when the folder was already gone; returns None.
    mods.set_mod_enabled("ghost", True)
    assert mods.remove_mod("ghost") is None
    assert storage.load_settings()["enabled_mods"] == []

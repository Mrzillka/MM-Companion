"""Data-described (declarative) sheet blocks: a data-only mod adds a block.

Covers the whole path: a workspace mod ships ``blocks.json``; the loader parses it
into :attr:`GameData.blocks`; the block registry turns each spec into a
:class:`DeclarativeBlock` descriptor; and the block appears on the sheet and edits
the shared :class:`Character`.
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication

from mm_companion.core import mods, storage
from mm_companion.core.character import Character
from mm_companion.core.data_loader import (
    BlockFieldSpec,
    BlockSpec,
    clear_game_data_cache,
    load_game_data,
)
from mm_companion.ui.blocks import (
    DeclarativeBlock,
    block_descriptors,
    sync_declarative_blocks,
    unregister_block,
)
from mm_companion.ui.character_sheet import CharacterSheet


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def _home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv(storage.HOME_ENV_VAR, str(tmp_path))
    clear_game_data_cache()
    yield tmp_path
    # Registry is process-global; empty the declarative blocks this test synced in
    # so other test files see the base block set. Reset to base first (home is still
    # patched here) so the re-sync loads no mod blocks.
    storage.update_settings(enabled_mods=[])
    clear_game_data_cache()
    sync_declarative_blocks(load_game_data())
    unregister_block("x")


NOTES_BLOCK = {
    "id": "notes",
    "title": "Notes",
    "row": 7,
    "col": 0,
    "min_width": 300,
    "fields": [
        {"key": "notes_general", "label": "General", "kind": "text"},
        {"key": "notes_gm", "label": "GM Only", "kind": "text"},
        {"label": "Reminder", "kind": "label", "text": "Stay in character."},
    ],
}


def _write_blocks_mod(root: Path, mod_id: str = "notesmod") -> None:
    mod_dir = root / storage.MODS_DIRNAME / mod_id
    mod_dir.mkdir(parents=True, exist_ok=True)
    manifest = {"id": mod_id, "files": ["blocks.json"], "priority": 0}
    (mod_dir / mods.MANIFEST_FILENAME).write_text(json.dumps(manifest), encoding="utf-8")
    (mod_dir / "blocks.json").write_text(json.dumps({"blocks": [NOTES_BLOCK]}), encoding="utf-8")
    storage.update_settings(enabled_mods=[mod_id])
    clear_game_data_cache()


# --- core: parsing blocks.json into GameData.blocks -------------------------


def test_base_ruleset_has_no_declarative_blocks() -> None:
    assert load_game_data().blocks == ()


def test_mod_blocks_json_parses_into_block_specs(_home: Path) -> None:
    _write_blocks_mod(_home)
    data = load_game_data()
    assert len(data.blocks) == 1
    spec = data.blocks[0]
    assert isinstance(spec, BlockSpec)
    assert (spec.id, spec.title, spec.row, spec.min_width) == ("notes", "Notes", 7, 300)
    assert spec.fields[0] == BlockFieldSpec(key="notes_general", label="General", kind="text")
    assert spec.fields[2].kind == "label"
    assert spec.fields[2].text == "Stay in character."


def test_unknown_field_keys_are_retained_in_extra(_home: Path) -> None:
    block = {"id": "x", "fields": [{"key": "k", "label": "L", "custom": 42}]}
    mod_dir = _home / storage.MODS_DIRNAME / "xmod"
    mod_dir.mkdir(parents=True)
    (mod_dir / mods.MANIFEST_FILENAME).write_text(
        json.dumps({"id": "xmod", "files": ["blocks.json"]}), encoding="utf-8"
    )
    (mod_dir / "blocks.json").write_text(json.dumps({"blocks": [block]}), encoding="utf-8")
    storage.update_settings(enabled_mods=["xmod"])
    clear_game_data_cache()

    spec = load_game_data().blocks[0]
    assert spec.fields[0].extra == {"custom": 42}
    unregister_block("x")


# --- registry: specs become additive descriptors ---------------------------


def test_sync_registers_a_descriptor_per_spec(_home: Path) -> None:
    _write_blocks_mod(_home)
    data = load_game_data()
    sync_declarative_blocks(data)
    keys = [d.key for d in block_descriptors()]
    assert "notes" in keys
    notes = next(d for d in block_descriptors() if d.key == "notes")
    assert notes.title == "Notes"
    assert notes.size.min_width == 300


def test_sync_is_idempotent_and_never_clobbers_a_base_block(_home: Path) -> None:
    _write_blocks_mod(_home)
    data = load_game_data()
    sync_declarative_blocks(data)
    sync_declarative_blocks(data)  # second sync must not duplicate 'notes'
    keys = [d.key for d in block_descriptors()]
    assert keys.count("notes") == 1
    # A spec colliding with a base key is skipped (can't restore a base block).
    collide = BlockSpec(id="skills", title="Hijack")
    sync_declarative_blocks(dataclasses.replace(data, blocks=(collide,)))
    skills = next(d for d in block_descriptors() if d.key == "skills")
    assert skills.title == "Skills"  # untouched by the collision


def test_syncing_empty_blocks_drops_previously_synced_ones(_home: Path) -> None:
    _write_blocks_mod(_home)
    sync_declarative_blocks(load_game_data())
    assert "notes" in {d.key for d in block_descriptors()}
    # Re-sync with base (no blocks): the declarative block should disappear.
    storage.update_settings(enabled_mods=[])
    clear_game_data_cache()
    sync_declarative_blocks(load_game_data())
    assert "notes" not in {d.key for d in block_descriptors()}


# --- UI: the block appears on the sheet and edits the model -----------------


def test_declarative_block_widget_seeds_and_writes_the_model(qapp: QApplication) -> None:
    data = load_game_data()
    character = Character.new_default(data)
    character.profile["notes_general"] = "seeded"
    spec = BlockSpec(
        id="notes",
        title="Notes",
        fields=(BlockFieldSpec(key="notes_general", label="General"),),
    )
    block = DeclarativeBlock(data, character, spec)
    edit = block._edits["notes_general"]
    assert edit.text() == "seeded"  # seeded from the model

    edited = []
    block.edited.connect(lambda: edited.append(True))
    edit.setText("changed")
    assert character.profile["notes_general"] == "changed"  # written back
    assert edited  # a user edit was reported


def test_declarative_block_appears_on_the_sheet(qapp: QApplication, _home: Path) -> None:
    _write_blocks_mod(_home)
    sheet = CharacterSheet(load_game_data())
    assert "notes" in sheet.block_keys()
    block = sheet._sections_by_key["notes"]
    assert isinstance(block, DeclarativeBlock)
    # It edits the sheet's shared character and can lock like any block.
    block._edits["notes_general"].setText("field note")
    assert sheet.character.profile["notes_general"] == "field note"
    sheet.set_locked(True)  # must not raise

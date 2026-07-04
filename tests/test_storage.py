"""The workspace bootstrap should create its layout and preserve user settings."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mm_companion.core import storage
from mm_companion.core.storage import (
    DEFAULT_SETTINGS,
    LAYOUT_FIXED,
    LAYOUT_FLEXIBLE,
    ensure_workspace,
    get_workspace,
    layout_mode,
    load_settings,
    save_settings,
    update_settings,
)


@pytest.fixture(autouse=True)
def _home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv(storage.HOME_ENV_VAR, str(tmp_path))
    return tmp_path


def test_ensure_workspace_creates_the_layout(_home: Path) -> None:
    ws = ensure_workspace()

    assert ws.root == _home
    assert ws.characters_dir.is_dir()
    assert ws.gm_characters_dir.is_dir()
    assert ws.images_dir.is_dir()
    assert json.loads(ws.settings_file.read_text(encoding="utf-8")) == DEFAULT_SETTINGS


def test_ensure_workspace_preserves_edited_settings(_home: Path) -> None:
    ensure_workspace()
    get_workspace().settings_file.write_text('{"theme": "dark"}', encoding="utf-8")

    ensure_workspace()  # a second launch must not clobber user settings

    assert load_settings() == {"theme": "dark"}


def test_home_env_var_overrides_the_root(_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    custom = _home / "custom"
    monkeypatch.setenv(storage.HOME_ENV_VAR, str(custom))
    assert get_workspace().root == custom


def test_save_settings_replaces_the_file(_home: Path) -> None:
    save_settings({"theme": "dark", "layout": {"dock_state": "abc123"}})

    assert load_settings() == {"theme": "dark", "layout": {"dock_state": "abc123"}}


def test_update_settings_merges_and_persists_a_layout(_home: Path) -> None:
    ensure_workspace()

    result = update_settings(layout={"window_geometry": "geo", "dock_state": "state"})

    # The new key is stored alongside the untouched defaults, and it round-trips.
    assert result["layout"] == {"window_geometry": "geo", "dock_state": "state"}
    assert result["theme"] == DEFAULT_SETTINGS["theme"]
    assert load_settings()["layout"]["dock_state"] == "state"


def test_layout_mode_defaults_to_flexible(_home: Path) -> None:
    assert DEFAULT_SETTINGS["layout_mode"] == LAYOUT_FLEXIBLE
    assert layout_mode() == LAYOUT_FLEXIBLE  # no settings file yet


def test_layout_mode_reads_the_saved_value(_home: Path) -> None:
    update_settings(layout_mode=LAYOUT_FIXED)
    assert layout_mode() == LAYOUT_FIXED

    update_settings(layout_mode="nonsense")  # unrecognized falls back to flexible
    assert layout_mode() == LAYOUT_FLEXIBLE

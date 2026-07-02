"""The workspace bootstrap should create its layout and preserve user settings."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mm_companion.core import storage
from mm_companion.core.storage import (
    DEFAULT_SETTINGS,
    ensure_workspace,
    get_workspace,
    load_settings,
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

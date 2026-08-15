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


# -- reading a setting an older workspace has never heard of -----------------


def _drop_key(key: str) -> None:
    """Make the workspace look like one written before *key* existed."""
    settings = load_settings()
    settings.pop(key, None)
    save_settings(settings)


def test_gm_default_pins_survives_a_workspace_that_predates_the_key(_home: Path) -> None:
    """The bug this accessor exists for.

    ``load_settings`` returns the file verbatim — it does **not** merge
    ``DEFAULT_SETTINGS`` — so every workspace created before the pins feature
    answers ``None`` for this key. Read directly, that gave every GM card an empty
    strip; the whole feature looked unimplemented.
    """
    ensure_workspace()
    _drop_key("gm_default_pins")
    assert load_settings().get("gm_default_pins") is None

    assert storage.gm_default_pins() == DEFAULT_SETTINGS["gm_default_pins"]


def test_gm_default_pins_prefers_what_the_gm_stored(_home: Path) -> None:
    ensure_workspace()
    update_settings(gm_default_pins={"npc": [{"kind": "initiative"}]})

    pins = storage.gm_default_pins()

    assert pins["npc"] == [{"kind": "initiative"}]
    # Filled in per *kind*: a file naming only "npc" still gets the shipped player
    # strip rather than nothing.
    assert pins["player"] == DEFAULT_SETTINGS["gm_default_pins"]["player"]


def test_gm_default_pins_hands_back_a_copy(_home: Path) -> None:
    """The caller mutating what it got must not rewrite the shipped defaults."""
    ensure_workspace()

    storage.gm_default_pins()["npc"].clear()

    assert storage.gm_default_pins()["npc"] == DEFAULT_SETTINGS["gm_default_pins"]["npc"]


def test_gm_default_pins_ignores_a_hand_edited_mess(_home: Path) -> None:
    ensure_workspace()
    update_settings(gm_default_pins="nonsense")
    assert storage.gm_default_pins() == DEFAULT_SETTINGS["gm_default_pins"]

    update_settings(gm_default_pins={"npc": "not a list"})
    assert storage.gm_default_pins() == DEFAULT_SETTINGS["gm_default_pins"]


def test_setting_one_kind_of_default_pin_leaves_the_other_alone(_home: Path) -> None:
    ensure_workspace()

    storage.set_gm_default_pins({"npc": [{"kind": "initiative"}]})

    assert storage.gm_default_pins()["npc"] == [{"kind": "initiative"}]
    assert storage.gm_default_pins()["player"] == DEFAULT_SETTINGS["gm_default_pins"]["player"]


def test_an_empty_default_strip_is_stored_rather_than_read_as_unset(_home: Path) -> None:
    """A GM who wants a card to start bare, versus a caller who said nothing.

    The reader merges per kind, so the two have to be told apart: an empty list is
    an answer and survives, a missing key is not and takes the shipped strip.
    """
    ensure_workspace()

    storage.set_gm_default_pins({"player": []})

    assert storage.gm_default_pins()["player"] == []
    assert storage.gm_default_pins()["npc"] == DEFAULT_SETTINGS["gm_default_pins"]["npc"]


def test_clearing_the_card_pins_sends_every_card_back_to_the_defaults(_home: Path) -> None:
    ensure_workspace()
    update_settings(gm_pins={"npc:goon.json": [{"kind": "initiative"}]})

    storage.clear_gm_card_pins()

    assert load_settings()["gm_pins"] == {}


# -- the dice roller's layout ------------------------------------------------


def test_the_three_layouts_round_trip(_home: Path) -> None:
    ensure_workspace()

    for layout in storage.DICE_LAYOUTS:
        storage.set_dice_layout(layout)
        assert storage.dice_layout() == layout


def test_an_unknown_layout_reads_and_writes_as_auto(_home: Path) -> None:
    """The same fallback :func:`pl_enforcement` has, at both ends of the setting."""
    ensure_workspace()

    storage.set_dice_layout("sideways")
    assert load_settings()["dice_layout"] == storage.DICE_LAYOUT_AUTO

    update_settings(dice_layout="sideways")  # or hand-edited into the file
    assert storage.dice_layout() == storage.DICE_LAYOUT_AUTO


def test_the_layout_survives_a_workspace_that_predates_the_key(_home: Path) -> None:
    ensure_workspace()
    _drop_key("dice_layout")

    assert storage.dice_layout() == storage.DICE_LAYOUT_AUTO

"""Writing user themes into the workspace: snapshot, save, delete, unique id.

Headless — the store is plain JSON and pathlib, nothing here needs Qt.
"""

from __future__ import annotations

import json
from dataclasses import replace

import pytest

from mm_companion.core import storage
from mm_companion.ui import theme
from mm_companion.ui.theme import loader


@pytest.fixture(autouse=True)
def isolated_workspace(tmp_path, monkeypatch):
    monkeypatch.setenv(storage.HOME_ENV_VAR, str(tmp_path))
    theme.reset()
    yield tmp_path
    theme.reset()


def classic():
    return loader.available_themes()["classic"]


# -- the snapshot format --------------------------------------------------------


def test_a_snapshot_is_self_contained() -> None:
    payload = loader.theme_to_dict(classic())

    assert "extends" not in payload
    assert payload["chrome"] == {"mode": "system", "focus_ring": True}
    for group in ("colors", "metrics", "typography", "blocks", "assets"):
        assert group in payload
    assert payload["colors"]["tint.worse"] == "#d15b5b"


def test_a_snapshot_always_states_its_chrome() -> None:
    """``_build`` reads chrome from the preset's own raw dict, never a parent's.

    A styled theme that omitted it would come back as ``system`` and undress
    itself the next time it was loaded.
    """
    payload = loader.theme_to_dict(loader.available_themes()["slate-dark"])

    assert payload["chrome"]["mode"] == "styled"


def test_a_snapshot_carries_no_comment_keys() -> None:
    payload = loader.theme_to_dict(classic())

    assert not [key for key in payload["colors"] if key.startswith("_")]


def test_a_saved_snapshot_round_trips_to_the_same_tokens() -> None:
    source = replace(classic(), id="mine", name="Mine")

    loader.save_workspace_theme(source)
    theme.reset()
    reloaded = loader.available_themes()["mine"]

    assert reloaded.name == "Mine"
    assert dict(reloaded.colors) == dict(source.colors)
    assert dict(reloaded.metrics) == dict(source.metrics)
    assert dict(reloaded.typography) == dict(source.typography)
    assert dict(reloaded.assets) == dict(source.assets)
    assert reloaded.chrome == source.chrome


def test_a_saved_file_is_readable_json_in_the_workspace(isolated_workspace) -> None:
    path = loader.save_workspace_theme(replace(classic(), id="mine", name="Mine"))

    assert path == isolated_workspace / "themes" / "mine.json"
    assert json.loads(path.read_text(encoding="utf-8"))["id"] == "mine"
    assert path.read_text(encoding="utf-8").endswith("\n")


def test_saving_works_when_the_themes_dir_was_never_created(isolated_workspace) -> None:
    """A workspace written before themes/ existed must not fail the first save."""
    assert not (isolated_workspace / "themes").exists()

    loader.save_workspace_theme(replace(classic(), id="mine"))

    assert (isolated_workspace / "themes" / "mine.json").is_file()


# -- classifying a theme --------------------------------------------------------


def test_bundled_ids_are_the_shipped_presets() -> None:
    assert {"classic", "slate-dark", "parchment-light"} <= loader.bundled_ids()


def test_a_bundled_preset_is_not_editable_and_not_a_shadow() -> None:
    assert not loader.is_workspace_theme("classic")
    assert not loader.shadows_bundled("classic")


def test_a_saved_theme_is_editable() -> None:
    loader.save_workspace_theme(replace(classic(), id="mine"))

    assert loader.is_workspace_theme("mine")
    assert not loader.shadows_bundled("mine")  # its own theme, not an override


def test_a_workspace_file_over_a_bundled_id_reads_as_a_shadow() -> None:
    loader.save_workspace_theme(replace(classic(), id="classic", name="My Classic"))

    assert loader.shadows_bundled("classic")


# -- deleting -------------------------------------------------------------------


def test_deleting_a_bundled_id_with_no_file_is_a_no_op() -> None:
    assert loader.delete_workspace_theme("classic") is False
    assert "classic" in loader.available_themes()


def test_deleting_a_saved_theme_removes_it() -> None:
    loader.save_workspace_theme(replace(classic(), id="mine"))
    theme.reset()
    assert "mine" in loader.available_themes()

    assert loader.delete_workspace_theme("mine") is True
    theme.reset()

    assert "mine" not in loader.available_themes()


def test_deleting_a_shadow_hands_the_built_in_back() -> None:
    loader.save_workspace_theme(replace(classic(), id="classic", name="My Classic"))
    theme.reset()
    assert loader.available_themes()["classic"].name == "My Classic"

    loader.delete_workspace_theme("classic")
    theme.reset()

    assert loader.available_themes()["classic"].name == "Classic"


# -- minting an id --------------------------------------------------------------


def test_a_display_name_becomes_a_slug() -> None:
    assert loader.unique_theme_id("My Theme") == "my-theme"
    assert loader.unique_theme_id("  Loud!! Colours  ") == "loud-colours"


def test_a_nameless_theme_still_gets_an_id() -> None:
    assert loader.unique_theme_id("!!!") == "custom-theme"


def test_a_bundled_id_counts_as_taken() -> None:
    """A duplicate must never accidentally shadow a built-in preset."""
    assert loader.unique_theme_id("Classic") == "classic-2"


def test_a_taken_id_gets_the_next_free_suffix() -> None:
    loader.save_workspace_theme(replace(classic(), id="mine"))
    loader.save_workspace_theme(replace(classic(), id="mine-2"))
    theme.reset()

    assert loader.unique_theme_id("Mine") == "mine-3"

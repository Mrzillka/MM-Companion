"""Saving, loading, and listing characters on disk."""

from __future__ import annotations

from pathlib import Path

import pytest

from mm_companion.core import library, storage
from mm_companion.core.character import AdvantageSelection, Character
from mm_companion.core.data_loader import load_game_data


@pytest.fixture(autouse=True)
def _home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv(storage.HOME_ENV_VAR, str(tmp_path))
    return tmp_path


def _sample_character() -> Character:
    char = Character.new_default(load_game_data())
    char.profile["hero_name"] = "Iron Man"
    char.abilities["STR"] = 4
    char.advantages.append(AdvantageSelection("Close Attack", 2))
    char.conditions.add("dazed")
    return char


def test_save_derives_a_filename_from_the_name(_home: Path) -> None:
    path = library.save_character(_sample_character())

    assert path.parent == storage.get_workspace().characters_dir
    assert path.name == "iron-man.json"
    assert path.is_file()


def test_save_then_load_round_trips_the_character(_home: Path) -> None:
    char = _sample_character()
    path = library.save_character(char)

    restored = library.load_character(path)
    assert restored == char


def test_save_with_explicit_path_overwrites_in_place(_home: Path) -> None:
    char = _sample_character()
    path = library.save_character(char)

    char.abilities["STR"] = 9
    again = library.save_character(char, path=path)

    assert again == path
    assert library.load_character(path).abilities["STR"] == 9


def test_save_without_path_avoids_clobbering_a_same_named_character(_home: Path) -> None:
    first = library.save_character(_sample_character())
    second = library.save_character(_sample_character())

    assert first != second
    assert second.name == "iron-man-2.json"


def test_list_saved_characters_summarizes_each_file(_home: Path) -> None:
    char = _sample_character()
    char.image_path = "portrait.png"
    library.save_character(char)

    summaries = library.list_saved_characters()

    assert len(summaries) == 1
    summary = summaries[0]
    assert summary.name == "Iron Man"
    assert summary.power_level == char.power_level
    assert summary.image_path == "portrait.png"
    assert summary.path is not None and summary.path.is_file()


def test_list_saved_characters_skips_unreadable_files(_home: Path) -> None:
    library.save_character(_sample_character())
    bad = storage.get_workspace().characters_dir / "broken.json"
    bad.write_text("{ not valid json", encoding="utf-8")

    summaries = library.list_saved_characters()

    assert [s.name for s in summaries] == ["Iron Man"]


def test_list_is_empty_when_the_directory_is_missing(_home: Path) -> None:
    # No workspace has been created yet, so the characters dir does not exist.
    assert library.list_saved_characters() == []


def test_display_name_falls_back_to_a_placeholder() -> None:
    assert library.display_name(Character()) == library.UNNAMED


def test_delete_character_removes_the_file(_home: Path) -> None:
    path = library.save_character(_sample_character())
    library.delete_character(path)
    assert not path.exists()
    library.delete_character(path)  # deleting again is a no-op

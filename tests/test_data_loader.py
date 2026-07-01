"""Tests for the game-data loader."""

from __future__ import annotations

from mm_companion.core.data_loader import GameData, load_game_data


def test_load_game_data_returns_populated_sections() -> None:
    data = load_game_data()
    assert isinstance(data, GameData)
    assert data.profile_fields
    assert data.characteristics
    assert data.abilities
    assert data.resistances
    assert data.skills
    assert data.advantages


def test_resistances_link_to_known_abilities() -> None:
    data = load_game_data()
    ability_keys = {a.key for a in data.abilities}
    for resistance in data.resistances:
        assert resistance.ability in ability_keys


def test_skills_link_to_known_abilities() -> None:
    data = load_game_data()
    ability_keys = {a.key for a in data.abilities}
    for skill in data.skills:
        assert skill.ability in ability_keys


def test_some_skills_are_focused() -> None:
    data = load_game_data()
    assert any(skill.focused for skill in data.skills)


def test_load_game_data_is_cached() -> None:
    assert load_game_data() is load_game_data()

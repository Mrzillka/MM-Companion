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
        if resistance.derived:  # combat stats (e.g. Defence) link to no ability
            continue
        assert resistance.ability in ability_keys


def test_skills_link_to_known_abilities() -> None:
    data = load_game_data()
    ability_keys = {a.key for a in data.abilities}
    for skill in data.skills:
        assert skill.ability in ability_keys


def test_some_skills_are_focused() -> None:
    data = load_game_data()
    assert any(skill.focused for skill in data.skills)


def test_focused_skills_expose_focuses() -> None:
    data = load_game_data()
    focused = [s for s in data.skills if s.focused]
    assert focused
    assert all(s.focuses for s in focused)


def test_advantages_carry_type_tags() -> None:
    data = load_game_data()
    for advantage in data.advantages:
        assert advantage.types  # at least one category tag
        assert advantage.type == advantage.types[0]  # legacy single-type accessor


def test_condition_graph_references_known_ids() -> None:
    data = load_game_data()
    condition_ids = {c.id for c in data.conditions}
    for condition in data.conditions:
        for ref in (*condition.includes, *condition.supersedes):
            assert ref in condition_ids


def test_costs_are_loaded() -> None:
    data = load_game_data()
    assert data.costs.traits.ability_per_rank == 2
    assert data.costs.power_level.pp_per_level > 0
    assert "skill_modifier" in data.costs.power_level.caps


def test_load_game_data_is_cached() -> None:
    assert load_game_data() is load_game_data()

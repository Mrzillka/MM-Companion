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


def test_effects_and_modifiers_are_loaded() -> None:
    data = load_game_data()
    assert len(data.effects) == 42
    assert len(data.modifiers) == 61


def test_effect_carries_numeric_base_cost_and_integration() -> None:
    data = load_game_data()
    by_id = {e.id: e for e in data.effects}
    damage = by_id["damage"]
    assert isinstance(damage.base_cost_value, int)
    assert damage.base_cost_value == 1
    # The nested statIntegration object is flattened onto the record.
    assert damage.stat_pattern == "instant_action"
    assert damage.stat_affects == "none"
    # Enhanced Trait is the configurable-target effect.
    assert by_id["enhanced_trait"].configurable_target is True


def test_modifiers_are_categorised_with_numeric_cost() -> None:
    data = load_game_data()
    for modifier in data.modifiers:
        assert modifier.category in {"extra", "flaw"}
        assert isinstance(modifier.cost_value, int)
        assert modifier.cost_value >= 0  # magnitude only; sign comes from category
    categories = {m.category for m in data.modifiers}
    assert categories == {"extra", "flaw"}


def test_load_game_data_is_cached() -> None:
    assert load_game_data() is load_game_data()

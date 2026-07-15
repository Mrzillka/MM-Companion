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


def test_resistances_link_to_known_traits() -> None:
    data = load_game_data()
    # A resistance derives from an ability (Toughness ← Stamina) or, for Dodge, from
    # the Defense combat trait, which is itself a (derived) resistance.
    ability_keys = {a.key for a in data.abilities}
    resistance_keys = {r.key for r in data.resistances}
    for resistance in data.resistances:
        if resistance.derived:  # combat stats (e.g. Defence) link to no base
            continue
        assert resistance.ability in ability_keys | resistance_keys


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
    valid_kinds = {"fixed", "power_level", "power_level_half", "heroic_budget", "none"}
    for advantage in data.advantages:
        assert advantage.types  # at least one category tag
        assert advantage.type == advantage.types[0]  # legacy single-type accessor
        assert advantage.max_rank_kind in valid_kinds
        # A fixed cap must give the number it points at; the others carry none.
        if advantage.max_rank_kind == "fixed":
            assert advantage.max_rank is not None


def test_movement_and_measurement_conversions_load() -> None:
    data = load_game_data()
    assert data.movement.base_ground_speed_rank == 1
    assert data.movement.round_seconds == 6
    # The numeric metric distance backs the km/h conversion (distance rank 2 = 8 m).
    assert data.measurements.distance_m(2) == 8.0
    assert data.measurements.size_rank_for_category("Medium") == 0


def test_initiative_advantages_carry_their_mechanics() -> None:
    data = load_game_data()
    by_name = {a.name: a for a in data.advantages}
    assert by_name["Improved Initiative"].initiative_bonus_per_rank == 4
    assert by_name["Alternate Initiative"].initiative_ability_choice == ("INT", "AWE", "PRE")


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
    assert len(data.modifiers) == 63


def test_effect_carries_numeric_base_cost_and_integration() -> None:
    data = load_game_data()
    by_id = {e.id: e for e in data.effects}
    damage = by_id["damage"]
    assert isinstance(damage.base_cost_value, int)
    assert damage.base_cost_value == 1
    # The nested statIntegration object is parsed into a typed component: an
    # instant-action effect that boosts no trait.
    assert damage.integration.pattern == "instant_action"
    assert damage.integration.trait_boost is None
    # Enhanced Trait is the configurable-target booster.
    enhanced = by_id["enhanced_trait"]
    assert enhanced.integration.trait_boost is not None
    assert enhanced.integration.trait_boost.configurable is True


def test_modifiers_are_categorised_with_numeric_cost() -> None:
    data = load_game_data()
    for modifier in data.modifiers:
        assert modifier.category in {"extra", "flaw"}
        assert isinstance(modifier.cost_value, int)
        assert modifier.cost_value >= 0  # magnitude only; sign comes from category
    categories = {m.category for m in data.modifiers}
    assert categories == {"extra", "flaw"}


def test_effect_specific_modifiers_are_loaded_and_categorised() -> None:
    data = load_game_data()
    assert len(data.effect_modifiers) == 36  # effects with their own extras/flaws
    total = sum(len(mods) for mods in data.effect_modifiers.values())
    assert total == 231
    for mods in data.effect_modifiers.values():
        for modifier in mods:
            # Category is injected from the extras/flaws array, not stored per entry.
            assert modifier.category in {"extra", "flaw"}
            assert modifier.cost_value >= 0

    damage_specific = {m.id: m for m in data.effect_modifiers["damage"]}
    assert damage_specific["strength_based"].category == "extra"
    assert damage_specific["strength_based"].cost_value == 0  # "+0 points per rank"
    # Strength-Based folds the wielder's Strength into the effect's effective rank.
    assert damage_specific["strength_based"].adds_ability == "STR"


def test_effect_specific_modifiers_retain_mechanical_fields() -> None:
    # The catalog re-audit added new effect-specific modifiers; each must still carry
    # the machine-readable cost/override fields the cost engine and summary consume,
    # not just prose (a bare id/name/costFormula entry would silently cost 0 points).
    data = load_game_data()
    by_id = {m.id: m for mods in data.effect_modifiers.values() for m in mods}
    # A newly-added Move Object extra: Perception range auto-hits and forces the range.
    perception = by_id["perception_move_object"]
    assert perception.cost_value == 1
    assert perception.drops_check is True
    assert perception.overrides == {"range": "Perception"}
    # A newly-added Regeneration extra priced at +0 that still forces Sustained duration.
    sustained_regen = by_id["sustained_regeneration"]
    assert sustained_regen.cost_value == 0
    assert sustained_regen.overrides == {"duration": "Sustained"}


def test_modifier_catalog_merges_general_and_effect_specific_pools() -> None:
    data = load_game_data()
    catalog = data.modifier_catalog()
    assert len(catalog) == 63 + 231  # ids are globally unique, so no collisions
    assert catalog["ranged"].category == "extra"  # general pool
    assert catalog["strength_based"].category == "extra"  # effect-specific pool


def test_effect_implicit_modifiers_resolve_in_the_catalog() -> None:
    data = load_game_data()
    catalog = data.modifier_catalog()
    # An implicit id that resolves to nothing would silently drop an effect's attack
    # roll, so every one an effect declares must exist.
    for effect in data.effects:
        for modifier_id in effect.implicit_modifiers:
            assert modifier_id in catalog, f"{effect.id} declares unknown '{modifier_id}'"
    # The attacking effects carry the +0 Attack extra that supplies their check.
    damage = next(e for e in data.effects if e.id == "damage")
    assert damage.implicit_modifiers == ("attack",)
    assert catalog["attack"].grants_attack is True
    assert catalog["attack"].cost_value == 0
    # It stays draggable, so any other effect can take it.
    assert catalog["attack"].hidden is False


def test_modifier_overrides_normalize_camelcase_keys() -> None:
    data = load_game_data()
    # overrides keys are camelCase in the JSON like every other key there; the loader
    # maps effectType onto the effect_type the stat dicts use, or it would no-op.
    assert data.modifier_catalog()["attack"].overrides == {
        "effect_type": "Attack",
        "check": "Attack vs. Defense",
    }


def test_load_game_data_is_cached() -> None:
    assert load_game_data() is load_game_data()


def _advantage(data: GameData, name: str):
    return next(a for a in data.advantages if a.name == name)


def test_advantage_parameter_is_parsed() -> None:
    data = load_game_data()
    skill_mastery = _advantage(data, "Skill Mastery")
    assert skill_mastery.parameter is not None
    assert skill_mastery.parameter.kind == "choice"
    assert skill_mastery.parameter.options_from == "skills"

    benefit = _advantage(data, "Benefit")
    assert benefit.parameter is not None
    assert benefit.parameter.kind == "text"

    dazing = _advantage(data, "Dazing Interaction")
    assert dazing.parameter is not None
    assert dazing.parameter.options == ("Deception", "Intimidation", "Persuasion")


def test_alternate_initiative_parameter_is_synthesised() -> None:
    # Alternate Initiative carries no explicit ``parameter`` block; the loader
    # synthesises one from its legacy ``initiativeAbilityChoice`` so the same field
    # keeps driving both the picker and the initiative math.
    data = load_game_data()
    alt = _advantage(data, "Alternate Initiative")
    assert alt.parameter is not None
    assert alt.parameter.kind == "choice"
    assert alt.parameter.options_from == "abilities"
    assert alt.parameter.options == ("INT", "AWE", "PRE")


def test_every_subject_taking_advantage_has_a_parameter() -> None:
    # focused advantages (plus the free-form Equipment/Sidekick) each attach to a
    # chosen subject, so the UI must have a spec describing what to ask for.
    data = load_game_data()
    for advantage in data.advantages:
        if advantage.focused or advantage.name in {"Equipment", "Sidekick"}:
            assert advantage.parameter is not None, advantage.name


def test_plain_advantage_has_no_parameter() -> None:
    data = load_game_data()
    assert _advantage(data, "Agile Grab").parameter is None

"""The ``combat`` block of ``actions.json``: the Actions popup's menu and its actions."""

from __future__ import annotations

from mm_companion.core.data_loader import CombatRules, load_game_data
from mm_companion.core.data_loader.game_data import (
    COMBAT_KIND_ATTACK,
    COMBAT_KIND_BUILTIN,
    COMBAT_KIND_LOADER,
    COMBAT_KIND_MANEUVER,
    COMBAT_KIND_SELF,
    COMBAT_KIND_TARGETED,
)
from mm_companion.core.data_loader.parsing_content import _parse_combat

KINDS = {
    COMBAT_KIND_MANEUVER,
    COMBAT_KIND_SELF,
    COMBAT_KIND_TARGETED,
    COMBAT_KIND_LOADER,
    COMBAT_KIND_ATTACK,
    COMBAT_KIND_BUILTIN,
}


def test_the_menu_names_only_actions_it_defines() -> None:
    combat = load_game_data().combat
    assert [s.title for s in combat.menu] == [
        "Attack Maneuvers",
        "Dice",
        "Actions",
        "More Maneuvers",
        "More Dice",
        "More Actions",
        "Group",
    ]
    defined = {a.id for a in combat.actions}
    listed = [i for s in combat.menu for i in s.items]
    assert set(listed) <= defined
    # Each action is on the menu exactly once.
    assert len(listed) == len(set(listed)) == len(defined)


def test_the_menu_opens_on_the_few_and_keeps_the_rest_behind_show_more() -> None:
    # The user's grouping (2026-11-13): four maneuvers, Ask and Reroll, then the six
    # actions reached for every round; everything else is behind "Show more".
    combat = load_game_data().combat
    shown = [s.items for s in combat.menu if not s.more]
    assert shown == [
        ("accurate_attack", "all_out_attack", "defensive_attack", "power_attack"),
        ("request", "reroll"),
        ("attack", "aim", "defend", "grab", "impress", "area"),
    ]
    more = {i for s in combat.menu if s.more for i in s.items}
    assert {"finishing_attack", "surprise_attack", "take_ten", "recover"} <= more


def test_every_action_is_named_drawn_and_explained() -> None:
    for action in load_game_data().combat.actions:
        assert action.name and action.icon and action.tooltip, action.id
        assert action.kind in KINDS, action.id


def test_the_four_maneuvers_shift_between_two_values_and_last_where_the_book_says() -> None:
    data = load_game_data()
    combat = data.combat
    shifts = {
        a.id: (a.bonus, a.penalty, a.lasting)
        for a in combat.actions
        if a.kind == COMBAT_KIND_MANEUVER
    }
    assert shifts == {
        "accurate_attack": ("attack_check", "effect_rank", ""),
        "all_out_attack": ("attack_check", "defense", "all_out_attack"),
        "defensive_attack": ("defense", "attack_check", "defensive_attack"),
        "power_attack": ("effect_rank", "attack_check", ""),
    }
    catalog = data.condition_catalog()
    for _bonus, _penalty, lasting in shifts.values():
        assert not lasting or lasting in catalog
    assert all(combat.action(i).needs_attack for i in shifts)
    assert combat.limits.max_shift == 5 and combat.limits.floor == 0
    assert combat.limits.double_cap


def test_the_group_actions_are_the_gms_alone() -> None:
    combat = load_game_data().combat
    assert {a.id for a in combat.actions if a.gm_only} == {
        "team_attack",
        "team_check",
        "group_check",
    }


def test_a_tile_caption_falls_back_to_the_name() -> None:
    combat = load_game_data().combat
    assert combat.action("all_out_attack").tile_caption == "All-Out"
    assert combat.action("aid").tile_caption == "Aid"


def test_a_ruleset_without_the_block_has_no_combat_actions() -> None:
    assert _parse_combat({}) == CombatRules()
    assert _parse_combat({"combat": []}) == CombatRules()
    # A malformed record is skipped rather than fatal.
    parsed = _parse_combat({"combat": {"menu": [{"title": "no id"}], "actions": [{"name": "x"}]}})
    assert parsed.menu == () and parsed.actions == ()

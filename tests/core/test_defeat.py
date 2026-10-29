"""Whether a creature is out of the fight: the data behind a card's dimmed look."""

from __future__ import annotations

import pytest

from mm_companion.core.character import Character
from mm_companion.core.data_loader import load_game_data
from mm_companion.core.rules import (
    DEFEAT_DEAD,
    DEFEAT_DOWN,
    DEFEAT_NONE,
    apply_condition,
    character_defeat_state,
    defeat_state,
)


@pytest.fixture(scope="module")
def data():
    return load_game_data()


def test_a_fresh_creature_is_in_the_fight(data) -> None:
    assert character_defeat_state(Character.new_default(data), data) == DEFEAT_NONE


@pytest.mark.parametrize("condition", ["incapacitated", "dying"])
def test_incapacitated_or_dying_is_down(data, condition: str) -> None:
    char = Character.new_default(data)
    apply_condition(char, condition, data)
    assert character_defeat_state(char, data) == DEFEAT_DOWN


def test_dead_is_dead(data) -> None:
    char = Character.new_default(data)
    apply_condition(char, "dying", data)
    apply_condition(char, "dead", data)
    assert character_defeat_state(char, data) == DEFEAT_DEAD


def test_bare_ids_off_the_wire_expand_their_includes(data) -> None:
    # A Scene entry may carry Dying without its Incapacitated member row.
    assert defeat_state(["dying"], data) == DEFEAT_DOWN
    assert defeat_state(["dazed", "dead"], data) == DEFEAT_DEAD
    assert defeat_state(["dazed", "unknown-mod-condition"], data) == DEFEAT_NONE


def test_the_lists_are_data(data) -> None:
    assert data.system.defeat.down == ("incapacitated",)
    assert data.system.defeat.dead == ("dead",)

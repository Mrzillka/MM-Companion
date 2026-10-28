"""Team checks and group checks (PDF p18), headless."""

from __future__ import annotations

import pytest

from mm_companion.core.data_loader import load_game_data
from mm_companion.core.rules import (
    GroupCheckMember,
    group_check_result,
    shift_degree,
    team_check_bonus,
)


@pytest.fixture(scope="module")
def data():
    return load_game_data()


def test_helpers_add_their_degrees_of_success(data) -> None:
    assert data.system.group_checks.helper_dc == 10
    assert team_check_bonus([1, 2, -1], data) == 3
    assert team_check_bonus([-2, -1], data) == 0


def test_the_degree_ladder_has_no_zero() -> None:
    assert shift_degree(-1, 1) == 1
    assert shift_degree(1, -1) == -1
    assert shift_degree(2, -3) == -2
    assert shift_degree(3, 0) == 3


def test_half_succeeding_carries_everyone_at_the_lowest_success(data) -> None:
    members = [
        GroupCheckMember("A", 25),  # +4 degrees vs 15... margin 10: 3
        GroupCheckMember("B", 16),  # margin 1: 1
        GroupCheckMember("C", 9),  # margin -6: -2
        GroupCheckMember("D", 12),  # margin -3: -1
    ]
    result = group_check_result(members, 15, data)
    assert result.successes == 2 and result.success and result.degree == 1


def test_fewer_than_half_fails_everyone_at_the_least_failure(data) -> None:
    members = [GroupCheckMember("A", 20), GroupCheckMember("B", 9), GroupCheckMember("C", 13)]
    result = group_check_result(members, 15, data)
    assert result.successes == 1 and not result.success and result.degree == -1


def test_added_successes_move_the_whole_group(data) -> None:
    # A failure by one, but a natural 20 among them turns it into a success.
    members = [
        GroupCheckMember("A", 12, added=1),
        GroupCheckMember("B", 13),
        GroupCheckMember("C", 16),
    ]
    result = group_check_result(members, 15, data)
    assert result.base_degree == -1 and result.added == 1 and result.degree == 1
    # And a natural 1 can drag a success down to a failure.
    dragged = group_check_result(
        [GroupCheckMember("A", 16, added=-1), GroupCheckMember("B", 17)], 15, data
    )
    assert dragged.base_degree == 1 and dragged.degree == -1


def test_nobody_rolling_is_no_result(data) -> None:
    assert group_check_result([], 15, data) is None

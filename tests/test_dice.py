"""Tests for the dice / check resolution engine."""

from __future__ import annotations

import random

from mm_companion.core.dice import degrees, resolve_check, roll_d20


def test_degree_bands() -> None:
    # Success bands: +1 at margin 0-4, +2 at 5-9, +3 at 10-14.
    assert degrees(0) == 1
    assert degrees(4) == 1
    assert degrees(5) == 2
    assert degrees(9) == 2
    assert degrees(10) == 3
    # Failure bands: -1 at -1..-5, -2 at -6..-10.
    assert degrees(-1) == -1
    assert degrees(-5) == -1
    assert degrees(-6) == -2
    assert degrees(-10) == -2


def test_plain_success_and_failure() -> None:
    hit = resolve_check(5, 15, roll=10)  # 10 + 5 = 15 == DC
    assert hit.total == 15
    assert hit.degree == 1
    assert hit.success

    miss = resolve_check(0, 15, roll=10)  # 10 vs 15
    assert miss.degree == -1
    assert not miss.success


def test_routine_check_uses_ten() -> None:
    result = resolve_check(8, 15, roll=10)
    assert result.die_roll == 10
    assert result.success  # 18 vs 15


def test_natural_twenty_flips_a_one_degree_failure_to_success() -> None:
    # 20 + 0 = 20 vs DC 21 is a 1-degree failure; the nat 20 lifts it to success.
    result = resolve_check(0, 21, roll=20)
    assert result.critical
    assert result.degree == 1  # -1 failure stepped up across the zero gap to +1
    assert result.success


def test_natural_twenty_adds_a_degree_to_a_hit() -> None:
    crit = resolve_check(5, 15, roll=20)  # 25 vs 15, margin 10 -> 3 degrees
    assert crit.degree == 4  # +1 for the natural 20
    assert crit.critical


def test_natural_one_flips_a_one_degree_success_to_failure() -> None:
    # 1 + 20 = 21 vs 20 is a 1-degree success; the nat 1 drags it to a failure.
    result = resolve_check(20, 20, roll=1)
    assert result.critical
    assert result.degree == -1  # +1 success stepped down across the zero gap to -1
    assert not result.success


def test_ungraded_check_ignores_crit_adjustment() -> None:
    result = resolve_check(0, 21, roll=20, graded=False)
    assert result.degree == -1  # ungraded: still a miss despite the natural 20
    assert result.critical  # crit flag is still reported for callers


def test_seeded_rng_is_deterministic() -> None:
    a = resolve_check(0, 10, rng=random.Random(1234))
    b = resolve_check(0, 10, rng=random.Random(1234))
    assert a.die_roll == b.die_roll


def test_roll_d20_in_range() -> None:
    rng = random.Random(0)
    for _ in range(200):
        assert 1 <= roll_d20(rng) <= 20

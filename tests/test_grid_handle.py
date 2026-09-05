"""The divider's detent: a recommended size you cannot cross by accident.

Pure arithmetic, no Qt — the same split :mod:`mm_companion.ui.reflow` and
:mod:`mm_companion.ui.sections.column_flow` make between a decision and the
widgets that act on it.
"""

from __future__ import annotations

from mm_companion.ui.grid_handle import snap_to_detent


def test_a_position_near_a_recommended_size_is_pulled_onto_it() -> None:
    assert snap_to_detent(305, [300], 12) == 300
    assert snap_to_detent(291, [300], 12) == 300


def test_a_position_exactly_a_band_away_still_sticks() -> None:
    # Inclusive, so the band is the whole distance a user can be sloppy by.
    assert snap_to_detent(312, [300], 12) == 300
    assert snap_to_detent(288, [300], 12) == 300


def test_a_deliberate_pull_goes_straight_past() -> None:
    # Which is the point: the recommendation is advice, not a floor.
    assert snap_to_detent(313, [300], 12) == 313
    assert snap_to_detent(500, [300], 12) == 500
    assert snap_to_detent(0, [300], 12) == 0


def test_the_nearest_target_wins() -> None:
    # Two recommendations closer together than the band cannot fight over the
    # handle: one of them is simply nearer.
    assert snap_to_detent(304, [300, 306], 12) == 306
    assert snap_to_detent(302, [300, 306], 12) == 300


def test_a_block_with_no_recommendation_has_nothing_to_stick_at() -> None:
    assert snap_to_detent(305, [], 12) == 305


def test_a_preset_can_turn_the_detent_off() -> None:
    # "grid.detent": 0 means a divider that moves exactly where it is dragged.
    assert snap_to_detent(305, [300], 0) == 305
    assert snap_to_detent(305, [300], -5) == 305


def test_the_detent_never_invents_a_position_of_its_own() -> None:
    # Whatever comes back is either the input or one of the stated targets, so a
    # detent can only ever move a handle somewhere a block actually wanted.
    for position in range(0, 600, 7):
        result = snap_to_detent(position, [180, 300, 420], 12)
        assert result == position or result in (180, 300, 420)

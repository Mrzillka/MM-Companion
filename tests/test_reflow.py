"""The reflow decision: which axis a fixed set of parts should sit on.

Pure arithmetic, no Qt — the same split
:mod:`mm_companion.ui.sections.column_flow` makes between the decision and the
widgets that act on it. The hysteresis tests are the point of the module: without
a dead-band the axis flips back and forth, because flipping changes the widget's
height, which toggles a scrollbar, which changes the width back.
"""

from __future__ import annotations

from mm_companion.ui.reflow import prefers_row


def test_a_row_is_chosen_once_there_is_room_for_one() -> None:
    assert prefers_row(500, 400) is True
    assert prefers_row(400, 400) is True  # exactly enough still fits


def test_a_column_is_chosen_when_a_row_would_not_fit() -> None:
    assert prefers_row(399, 400) is False


def test_an_unlaid_out_widget_asks_for_a_column() -> None:
    # Before the first layout there is no width to judge by, so the safe column is
    # the answer and the real one arrives on the first resizeEvent.
    assert prefers_row(0, 400) is False
    assert prefers_row(-10, 400) is False
    assert prefers_row(500, 0) is False


def test_growing_into_a_row_needs_the_band_as_well_as_the_room() -> None:
    # A column at 410px with a 400px row minimum: there is room, but not the room
    # plus the band, so it stays a column until it is clear of the boundary.
    assert prefers_row(410, 400, currently_row=False, hysteresis=24) is False
    assert prefers_row(424, 400, currently_row=False, hysteresis=24) is True


def test_falling_back_to_a_column_needs_the_band_too() -> None:
    # A row at 390px is already below its minimum, but not by a full band, so it
    # holds its shape rather than flipping at the first pixel over the line.
    assert prefers_row(390, 400, currently_row=True, hysteresis=24) is True
    assert prefers_row(376, 400, currently_row=True, hysteresis=24) is False


def test_the_band_is_a_dead_zone_both_arrangements_survive_in() -> None:
    # The whole purpose: inside the band the answer is whatever it already was, so a
    # width hovering at the boundary cannot make the layout oscillate.
    for width in range(377, 424):
        assert prefers_row(width, 400, currently_row=True, hysteresis=24) is True
        assert prefers_row(width, 400, currently_row=False, hysteresis=24) is False

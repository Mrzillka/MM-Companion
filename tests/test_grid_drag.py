"""Dragging a divider with real mouse events, end to end.

`tests/test_grid_resize.py` drives the *model* — it calls ``setSizes`` and
``_on_dragged`` directly, which is how it can state what a resize means without a
window. That left the gesture itself completely untested, and both halves of it
shipped broken:

* every horizontal drag raised ``TypeError`` out of ``mouseMoveEvent``, because
  ``closestLegalPosition`` was called with two arguments and takes one. A Python
  exception raised inside a Qt override does not go to the console and carry on —
  **it takes the process with it**. So the divider did nothing and the app died.
* every vertical drag treated its row as zero pixels tall, because the method
  that told the grip the row's real height was never called by anybody. Dragging
  down set the row to however far the mouse had moved; dragging up set it to
  zero, which read as the row snapping back.

Hence this file: the gestures, driven through ``QMouseEvent`` the way a hand
drives them, asserting the block actually ends up the size it was dragged to.
"""

from __future__ import annotations

import pytest
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QApplication

from mm_companion.core.data_loader import load_game_data
from mm_companion.ui.character_sheet import CharacterSheet


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


@pytest.fixture
def sheet(qapp: QApplication) -> CharacterSheet:
    built = CharacterSheet(load_game_data())
    built.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
    built.resize(1400, 900)
    built.show()
    _settle(qapp)
    yield built
    built.hide()
    built.deleteLater()


def _settle(qapp: QApplication, rounds: int = 8) -> None:
    for _ in range(rounds):
        qapp.processEvents()


def _send(widget, kind, local: QPoint, *, held: bool) -> None:
    """One mouse event at *local*, in *widget*'s own coordinates.

    Sent rather than posted so an exception raised in the override surfaces here,
    which is the whole point: Qt would otherwise take the interpreter down with it
    and the test would report as a crashed worker rather than a failure.
    """
    buttons = Qt.MouseButton.LeftButton if held else Qt.MouseButton.NoButton
    event = QMouseEvent(
        kind,
        QPointF(local),
        QPointF(widget.mapToGlobal(local)),
        Qt.MouseButton.LeftButton,
        buttons,
        Qt.KeyboardModifier.NoModifier,
    )
    QApplication.sendEvent(widget, event)


def drag(qapp, widget, start: QPoint, total: QPoint, steps: int = 4) -> None:
    """Press at *start*, move by *total* over *steps*, release.

    The moves are given in the *widget's* coordinates at the moment of the press,
    and the widget moves under them — which is exactly what a real pointer does,
    since a real pointer reports where it is on screen rather than where it is
    relative to the thing it is dragging. Recomputing against the widget's live
    position instead would compound the movement and measure nothing.
    """
    anchor = widget.mapToGlobal(start)
    _send(widget, QMouseEvent.Type.MouseButtonPress, start, held=True)
    for step in range(1, steps + 1):
        moved = QPoint(total.x() * step // steps, total.y() * step // steps)
        here = widget.mapFromGlobal(anchor + moved)
        _send(widget, QMouseEvent.Type.MouseMove, here, held=True)
        _settle(qapp, 2)
    _send(
        widget,
        QMouseEvent.Type.MouseButtonRelease,
        widget.mapFromGlobal(anchor + total),
        held=False,
    )
    _settle(qapp)


class TestDraggingAHandle:
    def test_a_horizontal_drag_moves_the_divider(self, sheet, qapp) -> None:
        row = sheet.canvas._row_widgets[0]
        handle = row.handle(1)
        before = row.sizes()

        drag(qapp, handle, QPoint(3, 20), QPoint(80, 0))

        after = row.sizes()
        assert after != before, "the divider did not move at all"
        assert after[0] > before[0], "the block left of the divider did not grow"
        assert after[1] < before[1], "its neighbour did not give the width up"

    def test_the_row_keeps_its_total_width(self, sheet, qapp) -> None:
        """A row is the viewport's width: a horizontal drag is zero-sum."""
        row = sheet.canvas._row_widgets[0]
        before = sum(row.sizes())

        drag(qapp, handle_of(row, 1), QPoint(3, 20), QPoint(70, 0))

        assert sum(row.sizes()) == before

    def test_dragging_back_the_other_way_undoes_it(self, sheet, qapp) -> None:
        row = sheet.canvas._row_widgets[0]
        start = row.sizes()

        drag(qapp, handle_of(row, 1), QPoint(3, 20), QPoint(90, 0))
        widened = row.sizes()
        drag(qapp, handle_of(row, 1), QPoint(3, 20), QPoint(-90, 0))

        assert widened[0] > start[0]
        assert row.sizes()[0] < widened[0]

    def test_a_drag_lands_where_it_was_dropped(self, sheet, qapp) -> None:
        """Within the detent's pull, which is the only thing allowed to move it."""
        from mm_companion.ui import theme

        row = sheet.canvas._row_widgets[0]
        before = row.sizes()[0]
        distance = 100

        drag(qapp, handle_of(row, 1), QPoint(3, 20), QPoint(distance, 0))

        band = int(theme.metric("grid.detent"))
        assert abs(row.sizes()[0] - (before + distance)) <= band + 2


def handle_of(row, index: int):
    return row.handle(index)


class TestDraggingARowGrip:
    def test_a_vertical_drag_makes_the_row_taller(self, sheet, qapp) -> None:
        stack = sheet.canvas._stack
        grip = stack._grips[0]
        before = stack._holders[0].height()

        drag(qapp, grip, QPoint(40, 3), QPoint(0, 120))

        assert stack._holders[0].height() > before
        assert stack.heights()[0] > 0, "the height was never recorded"

    def test_it_starts_from_the_rows_real_height_not_from_zero(self, sheet, qapp) -> None:
        """The bug this file exists for. Nothing armed the grip, so a drag treated
        the row as 0px tall and set its height to however far the mouse moved."""
        stack = sheet.canvas._stack
        before = stack._holders[0].height()
        assert before > 60, "the fixture's first row is too short to tell anything"

        drag(qapp, stack._grips[0], QPoint(40, 3), QPoint(0, 40))

        assert stack.heights()[0] >= before, "the row collapsed to the drag distance"

    def test_dragging_up_shortens_the_row_rather_than_collapsing_it(self, sheet, qapp) -> None:
        """Dragging up used to clamp to zero, which cleared the height and let the
        row spring back to its content — the "snaps back" nobody could explain."""
        stack = sheet.canvas._stack
        drag(qapp, stack._grips[0], QPoint(40, 3), QPoint(0, 150))
        tall = stack.heights()[0]

        drag(qapp, stack._grips[0], QPoint(40, 3), QPoint(0, -60))

        assert 0 < stack.heights()[0] < tall

    def test_the_page_does_not_shrink_while_the_grip_is_held(self, sheet, qapp) -> None:
        """The bottom-row complaint, in one assertion.

        Every mouse-move used to re-sum the page, so shortening a row shortened
        the *page*; the scroll value was clamped to the smaller maximum, the
        content slid down inside the viewport, and the divider stood still on
        screen while the hand dragging it walked away. The stack refuses to get
        shorter until the grip is let go.
        """
        stack = sheet.canvas._stack
        drag(qapp, stack._grips[0], QPoint(40, 3), QPoint(0, 200))  # room to lose
        tall = stack.height()

        grip = stack._grips[0]
        anchor = grip.mapToGlobal(QPoint(40, 3))
        _send(grip, QMouseEvent.Type.MouseButtonPress, QPoint(40, 3), held=True)
        _send(
            grip, QMouseEvent.Type.MouseMove, grip.mapFromGlobal(anchor - QPoint(0, 150)), held=True
        )
        _settle(qapp, 2)

        assert stack._holders[0].height() < tall, "the row itself did not shrink"
        assert stack.height() >= tall, "the page shrank under the pointer"

        _send(grip, QMouseEvent.Type.MouseButtonRelease, QPoint(40, 3), held=False)
        _settle(qapp)
        assert stack.minimumHeight() == 0, "the page never got its own height back"

    def test_the_freeze_is_released_even_if_the_page_is_rebuilt_under_it(self, sheet, qapp) -> None:
        stack = sheet.canvas._stack
        grip = stack._grips[0]
        _send(grip, QMouseEvent.Type.MouseButtonPress, QPoint(40, 3), held=True)
        assert stack.minimumHeight() > 0  # positive control: it did freeze

        sheet.canvas._relayout()
        _settle(qapp)

        assert stack.minimumHeight() == 0

    def test_a_grip_dragged_past_the_window_keeps_growing_the_row(self, sheet, qapp) -> None:
        """Auto-scroll, and the reason it extends the drag as well as scrolling.

        The pointer stops moving once it reaches the bottom of the window, so
        without this the row can only be made as much taller as there was room
        left below it, and the drag has to be let go and started again.
        """
        canvas = sheet.canvas
        stack = canvas._stack
        grip = stack._grips[0]

        _send(grip, QMouseEvent.Type.MouseButtonPress, QPoint(40, 3), held=True)
        stack.arm_grip(0)  # the press already did; harmless, and states the intent
        before = stack.heights()[0]

        viewport = canvas._scroll_area.viewport()
        below = viewport.mapToGlobal(QPoint(40, viewport.height() + 60))
        canvas._maybe_grip_autoscroll(below)
        assert canvas._grip_timer.isActive(), "the pointer was outside the window"
        for _ in range(5):
            canvas._grip_autoscroll_tick()
        _settle(qapp)

        assert stack.heights()[0] > before

        _send(grip, QMouseEvent.Type.MouseButtonRelease, QPoint(40, 3), held=False)
        _settle(qapp)
        assert not canvas._grip_timer.isActive(), "the auto-scroll outlived the drag"

    def test_the_rows_below_keep_their_own_heights(self, sheet, qapp) -> None:
        """Not zero-sum: the page grows instead of robbing the next row."""
        stack = sheet.canvas._stack
        below = stack.heights()[1:]

        drag(qapp, stack._grips[0], QPoint(40, 3), QPoint(0, 120))

        assert stack.heights()[1:] == below


def test_neither_gesture_raises_out_of_an_override(sheet, qapp) -> None:
    """The failure mode that made this worth a file of its own.

    A Python exception inside a Qt override is not a console message the app
    carries on past — it propagates out and takes the process with it. So a
    one-character mistake in ``mouseMoveEvent`` is not a broken divider, it is a
    dead application, and nothing short of driving the real events catches it.
    """
    row = sheet.canvas._row_widgets[0]
    stack = sheet.canvas._stack

    drag(qapp, row.handle(1), QPoint(3, 20), QPoint(60, 0))
    drag(qapp, stack._grips[0], QPoint(40, 3), QPoint(0, 60))
    drag(qapp, row.handle(1), QPoint(3, 20), QPoint(-200, 0))
    drag(qapp, stack._grips[0], QPoint(40, 3), QPoint(0, -400))

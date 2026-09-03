"""Stop nested widgets from hijacking the scroll, and send the wheel where it helps.

Spin boxes, combo boxes, and the inner tables all consume mouse-wheel events by
default, so wheeling over them changes a value or scrolls the inner table instead
of moving the sheet. A guarded widget only reacts to the wheel once it has
keyboard focus (i.e. after a click); otherwise the wheel is redirected to
whichever enclosing scroll area can actually use it.

**Which one that is, is the whole of this module's judgement.** It used to be the
outermost — the page — and that was right while a block was a plain frame with
nothing between it and the page. A block is a scroll area itself now
(:class:`~mm_companion.ui.block_frame._InnerScroll`), so the outermost answer sent
every wheel straight past a block the user had squashed: the block sat there with
a scrollbar it could not be scrolled by, and the page moved instead. So the target
is the **nearest ancestor that still has somewhere to go on this axis**, and the
outermost only when nothing does — which reads as "scroll the block until it runs
out, then the page", and needs no explaining to anybody using it.

:func:`can_scroll` is that test on its own, because the block's own scroll area
asks exactly the same question about itself before deciding to decline a wheel it
cannot use. Two spellings of one rule is how they drift apart.
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import QAbstractScrollArea, QApplication, QScrollBar, QWidget


def _bar_for(area: QAbstractScrollArea, event: QWheelEvent) -> tuple[QScrollBar, int]:
    """The scrollbar *event* is aimed at, and how far it pushes.

    A wheel with no vertical component is a horizontal one (a tilt wheel, or a
    trackpad swipe); anything else is judged against the vertical bar, which is
    the axis every scrolling surface in the app actually offers.
    """
    angle = event.angleDelta()
    if angle.y() == 0 and angle.x() != 0:
        return area.horizontalScrollBar(), angle.x()
    return area.verticalScrollBar(), angle.y()


def can_scroll(area: QAbstractScrollArea, event: QWheelEvent) -> bool:
    """Whether *area* can still move in the direction *event* is pushing.

    False for an area with no range at all, and false at the end of one — a
    surface already at its bottom has no more use for a downward wheel than one
    that never scrolled, and treating the two the same is what lets a gesture
    carry on out to whatever encloses it.
    """
    bar, delta = _bar_for(area, event)
    if bar.minimum() == bar.maximum():
        return False
    if delta > 0 and bar.value() == bar.minimum():
        return False
    if delta < 0 and bar.value() == bar.maximum():
        return False
    return True


class _WheelGuard(QObject):
    """Event filter that redirects the wheel to a scroll area that can use it,
    unless the guarded widget is focused."""

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        if event.type() != QEvent.Type.Wheel:
            return False

        widget = self._guarded_widget(obj)
        if widget is None or widget.hasFocus():
            return False  # let the widget scroll / adjust normally

        target = self._target_scroll_area(widget, event)
        if target is not None:
            QApplication.sendEvent(target.viewport(), event)
            return True  # consumed for the original widget

        return False

    @staticmethod
    def _guarded_widget(obj: QObject) -> QWidget | None:
        """The widget whose focus governs the wheel.

        Scroll areas handle the wheel on their viewport, so when the filter sits
        on a viewport the widget we care about is the parent scroll area.
        """

        if not isinstance(obj, QWidget):
            return None
        parent = obj.parent()
        if isinstance(parent, QAbstractScrollArea) and parent.viewport() is obj:
            return parent
        return obj

    @staticmethod
    def _target_scroll_area(widget: QWidget, event: QWheelEvent) -> QAbstractScrollArea | None:
        """The nearest enclosing scroll area that can use this wheel.

        Falls back to the **outermost** — the page — when none of them can, which
        is both the old behaviour and the right last resort: a gesture nobody can
        act on should still reach the surface the user thinks of as scrolling,
        rather than being swallowed by the widget it started over.
        """

        outermost: QAbstractScrollArea | None = None
        nearest: QAbstractScrollArea | None = None
        parent = widget.parentWidget()
        while parent is not None:
            if isinstance(parent, QAbstractScrollArea):
                outermost = parent
                if nearest is None and can_scroll(parent, event):
                    nearest = parent
            parent = parent.parentWidget()
        return nearest or outermost


_guard = _WheelGuard()


def guard_wheel(*widgets: QWidget) -> None:
    """Guard each widget so it ignores the wheel until it is focused."""

    for widget in widgets:
        widget.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        if isinstance(widget, QAbstractScrollArea):
            widget.viewport().installEventFilter(_guard)
        else:
            widget.installEventFilter(_guard)

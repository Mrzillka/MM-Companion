"""Stop nested widgets from hijacking the character sheet's page scroll.

Spin boxes, combo boxes, and the inner tables all consume mouse-wheel events by
default, so wheeling over them changes a value or scrolls the inner table
instead of moving the page. A guarded widget only reacts to the wheel once it
has keyboard focus (i.e. after a click); otherwise the wheel is redirected to
the enclosing page scroll area so the whole sheet scrolls as expected.
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtWidgets import QAbstractScrollArea, QApplication, QWidget


class _WheelGuard(QObject):
    """Event filter that redirects the wheel to the page unless the guarded
    widget is focused."""

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        if event.type() != QEvent.Type.Wheel:
            return False

        widget = self._guarded_widget(obj)
        if widget is None or widget.hasFocus():
            return False  # let the widget scroll / adjust normally

        page = self._page_scroll_area(widget)
        if page is not None:
            QApplication.sendEvent(page.viewport(), event)
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
    def _page_scroll_area(widget: QWidget) -> QAbstractScrollArea | None:
        """The outermost enclosing scroll area (the page).

        We walk all the way up rather than stopping at the nearest one: the skill
        rank/mod spins live inside a table (itself a scroll area), but the wheel
        should still reach the page behind it.
        """

        page: QAbstractScrollArea | None = None
        parent = widget.parentWidget()
        while parent is not None:
            if isinstance(parent, QAbstractScrollArea):
                page = parent
            parent = parent.parentWidget()
        return page


_guard = _WheelGuard()


def guard_wheel(*widgets: QWidget) -> None:
    """Guard each widget so it ignores the wheel until it is focused."""

    for widget in widgets:
        widget.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        if isinstance(widget, QAbstractScrollArea):
            widget.viewport().installEventFilter(_guard)
        else:
            widget.installEventFilter(_guard)

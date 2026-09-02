"""Dragging a tab *off* its bar, wherever that bar is.

Two tab bars in the app want the same gesture and would otherwise each grow their
own copy of it: the Notes block's, where dragging a tab out makes a new Notes
block holding that note, and a tab group's, where dragging a tab out takes that
whole block back onto the page. What is being carried differs; the gesture does
not.

A tab bar already owns a left/right drag — that is how tabs reorder — so the two
are told apart by direction and distance. Moving *along* the bar stays a reorder
and Qt handles it; moving :data:`SPLIT_THRESHOLD` px clear of the bar is a split.

What makes it feel like one gesture rather than two is that the bar keeps the
**mouse grab** through all of it (an implicit grab, from the press it already
saw). So once the split is requested this goes on filtering the bar's events and
forwards them as moves and a release; the host hands those straight to the
canvas's ordinary drag controller, and what was dragged out docks, stacks, pins,
merges or stays floating exactly as a block dragged by its title bar would.
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QEvent, QObject, QPoint, Qt
from PySide6.QtWidgets import QTabBar

#: How far off the bar the pointer has to go before a drag stops being a reorder.
SPLIT_THRESHOLD = 24


class TabSplitGesture(QObject):
    """Watches *tab_bar* and reports a tab dragged clear of it.

    The host supplies three callbacks rather than signals, so this stays usable by
    a plain widget as readily as by a ``QObject`` with a signal for each: begin
    (which may refuse, by returning False), move, and release.
    """

    def __init__(
        self,
        tab_bar: QTabBar,
        *,
        begin: Callable[[int, QPoint], bool],
        moved: Callable[[QPoint], None],
        released: Callable[[QPoint], None],
    ) -> None:
        super().__init__(tab_bar)
        self._bar = tab_bar
        self._begin = begin
        self._moved = moved
        self._released = released
        self._index = -1
        self._armed = False
        self._splitting = False
        tab_bar.installEventFilter(self)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802 - Qt override
        if watched is not self._bar:
            return False
        kind = event.type()
        if kind == QEvent.Type.MouseButtonPress:
            if event.button() == Qt.MouseButton.LeftButton:
                self._index = self._bar.tabAt(event.position().toPoint())
                self._armed = self._index >= 0
                self._splitting = False
        elif kind == QEvent.Type.MouseMove:
            if self._splitting:
                self._moved(event.globalPosition().toPoint())
                return True
            if self._armed and self._off_the_bar(event.position().toPoint()):
                return self._start(event.globalPosition().toPoint())
        elif kind == QEvent.Type.MouseButtonRelease:
            splitting, self._splitting = self._splitting, False
            self._armed = False
            self._index = -1
            if splitting:
                self._released(event.globalPosition().toPoint())
                return True
        return False

    def _off_the_bar(self, point: QPoint) -> bool:
        """Whether the pointer has left the bar far enough to mean a split.

        Vertically only: sideways *is* the reorder gesture, however far it goes.
        """
        rect = self._bar.rect()
        return (
            point.y() < rect.top() - SPLIT_THRESHOLD or point.y() > rect.bottom() + SPLIT_THRESHOLD
        )

    def _start(self, global_pos: QPoint) -> bool:
        index, self._armed = self._index, False
        self._index = -1
        if index < 0 or not self._begin(index, global_pos):
            return False
        self._splitting = True
        return True


__all__ = ["SPLIT_THRESHOLD", "TabSplitGesture"]

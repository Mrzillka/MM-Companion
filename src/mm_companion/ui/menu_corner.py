"""The strip of widgets at the right-hand end of a window's menu bar.

A ``QMenuBar`` lays its corner widget out to the right of **every** action on the
bar, so an action can never sit right of it — which is the whole reason this
module exists. Anything that belongs in that far-right cluster has to be a widget
*inside* one container that is itself the corner widget: today the compact-mode
toggle and the connection indicator, in that order.

The one subtlety is that the two are installed in **different orders** by the two
windows — a character sheet builds its Session menu (and with it the indicator)
before the toggle, the GM window after — so :meth:`MenuCorner.add` orders by an
explicit *slot* rather than by arrival. Whoever gets there first does not decide
the layout.

A window with nothing to put here never gets a container at all: a GM's read-only
view of a player sheet keeps its deliberately bare bar.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QWidget

from mm_companion.ui import theme

#: Slots, lowest (leftmost) first. Named constants rather than bare numbers so the
#: order is stated in one place instead of being an accident of two call sites.
SLOT_COMPACT = 10
SLOT_INDICATOR = 20


class MenuCorner(QWidget):
    """The container the menu bar's corner holds, ordering its children by slot."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._slots: dict[int, QWidget] = {}
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(int(theme.metric("space.xs")))

    def add(self, widget: QWidget, *, slot: int) -> None:
        """Put *widget* in the corner at *slot*, lowest slot leftmost.

        Re-inserts every child in slot order, so the result does not depend on
        which of them was installed first.
        """
        self._slots[slot] = widget
        layout = self.layout()
        for index, key in enumerate(sorted(self._slots)):
            layout.insertWidget(index, self._slots[key])

    def widget_at(self, slot: int) -> QWidget | None:
        """Whatever occupies *slot*, or ``None`` — what a test asks."""
        return self._slots.get(slot)


def corner_area(window) -> MenuCorner:
    """The corner strip of *window*'s menu bar, installing one on first use.

    Lazy on purpose: a window that puts nothing here keeps a bare bar rather than
    an empty container, which is what a GM's read-only sheet view relies on.
    """
    corner = window.menuBar().cornerWidget(Qt.Corner.TopRightCorner)
    if isinstance(corner, MenuCorner):
        return corner
    corner = MenuCorner(window)
    window.menuBar().setCornerWidget(corner, Qt.Corner.TopRightCorner)
    return corner

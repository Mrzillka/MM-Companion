"""A wrapping flow of cards that accepts a dragged card, and shows where it lands.

Three boards want the same thing now — the NPC roster, the player roster and the
Scene — and they want it for one reason: a card is dragged *between* them. That is
what forced this out of the GM window, where the NPC grid's version used to live
as a pseudo-drag: the card tracked the pointer itself and emitted a preview, which
works beautifully inside one container and cannot leave it, because nothing ever
crosses a widget boundary. A real :class:`~PySide6.QtGui.QDrag` does, so the
gesture had to become one, and once it is one the *target* has to be a real drop
target rather than the source guessing where it is.

One MIME (:data:`~mm_companion.ui.card_chips.SCENE_MIME`) and one payload for every
board, because the drop is the same question everywhere — *put this reference
here* — and the answer differs only in whose list is being edited. The owner is
told ``(ref, index)`` and decides what that means; nothing here knows what a
reference is.

The index is measured against the **laid-out geometry**, not against anyone's
list, because the flow wraps: which card is "before" the pointer is a question
about rows and columns. A card whose left half the pointer is over takes its own
index; its right half is the slot after it.
"""

from __future__ import annotations

from PySide6.QtCore import QRect, Qt, Signal

from mm_companion.ui.card_chips import SCENE_MIME
from mm_companion.ui.drop_feedback import DropFeedback, DropIndicator
from mm_companion.ui.flow_layout import FlowContainer, FlowLayout

#: How wide the bar between two cards is drawn.
INDICATOR_WIDTH = 3


class CardDropFlow(FlowContainer):
    """A :class:`FlowContainer` of cards that takes a dragged card reference."""

    #: ``(ref, index)`` — a card was dropped at *index* among these cards.
    dropped = Signal(str, int)

    def __init__(self, name: str, parent=None) -> None:
        super().__init__(parent)
        self.flow = FlowLayout(self)
        self.setAcceptDrops(True)
        # Scoped by object name, never a bare QWidget: an unscoped rule is
        # inherited by every card inside and would repaint each of them. And a
        # plain QWidget paints no stylesheet background without this attribute.
        self.setObjectName(name)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._feedback = DropFeedback(self, f"#{name}")
        self._indicator = DropIndicator(self)

    # -- adding and taking away --------------------------------------------

    def add_card(self, widget) -> None:
        """Put one card at the end of the flow."""
        self.flow.addWidget(widget)

    def clear(self) -> None:
        """Take every card out and destroy it — what a rebuilt board starts with."""
        while self.flow.count():
            item = self.flow.takeAt(0)
            widget = item.widget() if item is not None else None
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()

    def count(self) -> int:
        """How many cards are in the flow."""
        return self.flow.count()

    # -- the drop ----------------------------------------------------------

    def dragEnterEvent(self, event) -> None:  # noqa: N802 (Qt override)
        if self._ref(event):
            event.acceptProposedAction()
            self._feedback.show_accept()
        else:
            # Never a bare ignore: an ancestor may accept, and the refusal is then
            # invisible exactly where someone is looking for it.
            self._feedback.show_reject()

    def dragMoveEvent(self, event) -> None:  # noqa: N802 (Qt override)
        if not self._ref(event):
            return
        event.acceptProposedAction()
        self.show_indicator(self.drop_index(event.position().toPoint()))

    def dragLeaveEvent(self, event) -> None:  # noqa: N802 (Qt override)
        self.clear_feedback()
        super().dragLeaveEvent(event)

    def dropEvent(self, event) -> None:  # noqa: N802 (Qt override)
        ref = self._ref(event)
        self.clear_feedback()
        if not ref:
            return
        event.acceptProposedAction()
        self.dropped.emit(ref, self.drop_index(event.position().toPoint()))

    def clear_feedback(self) -> None:
        """Take the highlight and the bar down — the drag left, or landed."""
        self._feedback.clear()
        self._indicator.hide_indicator()

    def drop_index(self, point) -> int:
        """Which slot in the laid-out flow *point* falls in."""
        for index in range(self.flow.count()):
            item = self.flow.itemAt(index)
            widget = item.widget() if item is not None else None
            if widget is None:
                continue
            geo = widget.geometry()
            if point.y() < geo.bottom() and point.x() < geo.center().x():
                return index
        return self.flow.count()

    def show_indicator(self, index: int) -> None:
        """Put the bar before the card at *index*, or after the last one."""
        count = self.flow.count()
        if count == 0:
            self._indicator.hide_indicator()
            return
        index = max(0, min(index, count))
        if index >= count:
            geo = self.flow.itemAt(count - 1).widget().geometry()
            rect = QRect(geo.right() + 1, geo.top(), INDICATOR_WIDTH, geo.height())
        else:
            geo = self.flow.itemAt(index).widget().geometry()
            rect = QRect(geo.left() - INDICATOR_WIDTH, geo.top(), INDICATOR_WIDTH, geo.height())
        self._indicator.move_to(rect)

    @staticmethod
    def _ref(event) -> str:
        """The dragged reference, or ``""`` for a payload this flow does not take."""
        if not event.mimeData().hasFormat(SCENE_MIME):
            return ""
        return bytes(event.mimeData().data(SCENE_MIME)).decode("utf-8")

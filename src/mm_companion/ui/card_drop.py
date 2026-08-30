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

An **empty** flow is still a drop target, which is the whole of
:meth:`CardDropFlow.set_placeholder`. It has to be: the first card of a session
lands on a board that holds nothing, and a target that only exists once it has
something in it can never be given its first thing.
"""

from __future__ import annotations

from PySide6.QtCore import QRect, Qt, Signal
from PySide6.QtWidgets import QLabel

from mm_companion.ui import theme
from mm_companion.ui.card_chips import SCENE_MIME
from mm_companion.ui.drop_feedback import DropFeedback, DropIndicator
from mm_companion.ui.flow_layout import FlowContainer, FlowLayout
from mm_companion.ui.widgets import discard_widget, muted_style

#: How wide the bar between two cards is drawn.
INDICATOR_WIDTH = 3


class CardDropFlow(FlowContainer):
    """A :class:`FlowContainer` of cards that takes a dragged card reference."""

    #: ``(ref, index)`` — a card was dropped at *index* among these cards.
    dropped = Signal(str, int)

    def __init__(self, name: str, parent=None, *, accepts=None) -> None:
        super().__init__(parent)
        #: An optional narrowing of what this flow will take, given the dragged
        #: reference. Without one every board takes every card, which is right for
        #: the two that reorder — but the Players block accepts a drop only so a
        #: player's own card dragged out and back reads as a cancelled drag, and it
        #: was lighting up green for an NPC and then silently dropping it.
        self._accepts = accepts
        self.flow = FlowLayout(self)
        self.setAcceptDrops(True)
        # Scoped by object name, never a bare QWidget: an unscoped rule is
        # inherited by every card inside and would repaint each of them. And a
        # plain QWidget paints no stylesheet background without this attribute.
        self.setObjectName(name)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._feedback = DropFeedback(self, f"#{name}")
        self._indicator = DropIndicator(self)
        #: What the flow says while it holds nothing. See :meth:`set_placeholder`.
        self._placeholder: QLabel | None = None

    # -- adding and taking away --------------------------------------------

    def add_card(self, widget) -> None:
        """Put one card at the end of the flow."""
        self.flow.addWidget(widget)
        self._sync_placeholder()

    def set_cards(self, widgets) -> None:
        """Show exactly *widgets*, in that order, keeping the ones already here.

        The reordering counterpart of :meth:`clear` + :meth:`add_card`, and what a
        board that *reuses* its cards rebuilds through. Every card is taken out of the
        flow first and then put back in the wanted order, so a card that survives the
        rebuild is only moved — never destroyed and made again, which is the whole
        saving. Anything no longer wanted is discarded on the way past.

        Taking them all out first rather than shuffling in place is deliberate: the
        flow is an index-ordered list, so working out the minimal set of moves would be
        more code than it saves for a board of a dozen cards.
        """
        wanted = list(widgets)
        keep = set(map(id, wanted))
        while self.flow.count():
            item = self.flow.takeAt(0)
            widget = item.widget() if item is not None else None
            if widget is not None and id(widget) not in keep:
                discard_widget(widget)
        for widget in wanted:
            self.flow.addWidget(widget)
        self._sync_placeholder()

    def clear(self) -> None:
        """Take every card out and destroy it — what a rebuilt board starts with."""
        self.set_cards(())

    def count(self) -> int:
        """How many cards are in the flow."""
        return self.flow.count()

    # -- the empty state ---------------------------------------------------

    def set_placeholder(self, text: str) -> None:
        """What this flow says while it holds no cards.

        A child **of the flow host**, never a sibling of it, and that is the point
        rather than a detail. The host is the widget that accepts the drop, so the
        sentence inviting a drag has to live inside its rectangle. The first
        version of the Scene put the label beside the flow and hid the flow while
        the board was empty — so the one thing on screen saying "drag one here"
        was the one widget in the block that could not take a drop, and the first
        drop of every session was refused. Qt sends no drag events to a hidden
        widget, and the refusal looked exactly like a broken gesture.

        Outside the :class:`FlowLayout` as well as inside the widget: a label in
        the flow would be an item :meth:`drop_index` has to count, and it would
        wrap and re-flow like a card. It is positioned by hand instead, and made
        transparent to the mouse so it can never eat the drag it is asking for.
        """
        if self._placeholder is None:
            self._placeholder = QLabel(self)
            self._placeholder.setWordWrap(True)
            self._placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._placeholder.setStyleSheet(muted_style(italic=True))
            self._placeholder.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._placeholder.setText(text)
        self._sync_placeholder()

    def placeholder_text(self) -> str:
        """The empty-state sentence, or ``""`` if this flow never set one."""
        return "" if self._placeholder is None else self._placeholder.text()

    def _sync_placeholder(self) -> None:
        """Show the sentence over an empty flow, and keep a band to drop it on.

        The minimum row height is what stops an empty flow collapsing to nothing:
        :meth:`FlowLayout.minimumSize` answers ``QSize(0, 0)`` with no items, and a
        drop target nought pixels tall is not one.

        The band a drop needs is *more* than the sentence needs, so the extra is
        claimed only by a flow that actually takes drops. A player's Scene board is
        the same widget with the drops off and it lives in the **pinned strip**,
        where the lines stack and every block's minimum is added to the next — so
        a dozen free pixels there is the strip growing the scrollbar it exists to
        avoid, bought for a gesture that screen does not have.
        """
        if self._placeholder is None:
            return
        empty = self.flow.count() == 0
        self._placeholder.setVisible(empty)
        if not empty:
            self.set_minimum_row_height(0)
            return
        self._placeholder.setGeometry(self.rect())
        self._placeholder.raise_()
        band = int(theme.metric("space.md")) * 2 if self.acceptDrops() else 0
        width = max(1, self.width())
        self.set_minimum_row_height(self._placeholder.heightForWidth(width) + band)

    def resizeEvent(self, event) -> None:  # noqa: N802 (Qt override)
        super().resizeEvent(event)
        self._sync_placeholder()

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

    def _ref(self, event) -> str:
        """The dragged reference, or ``""`` for a payload this flow does not take."""
        if not event.mimeData().hasFormat(SCENE_MIME):
            return ""
        ref = bytes(event.mimeData().data(SCENE_MIME)).decode("utf-8")
        if ref and self._accepts is not None and not self._accepts(ref):
            return ""
        return ref

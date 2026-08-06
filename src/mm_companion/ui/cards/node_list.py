"""The board the cards sit on: one level of the tree, plus a group's title bar.

Both are drop targets, and between them they carry the whole drag vocabulary — drop
into a *gap* to reorder or move, drop *onto* a card (or a group's bar) to combine.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QDragEnterEvent, QDragMoveEvent, QDropEvent
from PySide6.QtWidgets import QFrame, QVBoxLayout, QWidget

from mm_companion.ui import theme
from mm_companion.ui.cards.drag import NODE_MIME


class GroupHeader(QWidget):
    """A group's title bar, which also acts as a drop target that *wraps* the group.

    Dropping a card onto a group's bar groups the whole group with the dragged node
    into a new parent group (the way to nest a group beside a peer). Joining a group
    as another member is done by dropping into its body instead (handled by the
    group's inner :class:`NodeList`).
    """

    powerDropped = Signal(str)  # the dropped node's id

    def __init__(self, parent: QWidget | None = None, *, mime: str = NODE_MIME) -> None:
        super().__init__(parent)
        self._mime = mime
        self.setObjectName("groupHeader")
        self.setAcceptDrops(True)

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if event.mimeData().hasFormat(self._mime):
            event.acceptProposedAction()
            # Scoped to this widget by object name: a selector-less stylesheet applies
            # to every child too, so the wash would also tint the group's name and its
            # ✎/✕ and mode buttons rather than just the bar behind them.
            self.setStyleSheet(
                f"#groupHeader {{ background: {theme.wash('accent.dice', 0.25)};"
                f" border-radius: {int(theme.metric('radius.header'))}px; }}"
            )

    def dragMoveEvent(self, event: QDragMoveEvent) -> None:
        if event.mimeData().hasFormat(self._mime):
            event.acceptProposedAction()

    def dragLeaveEvent(self, event: object) -> None:  # noqa: ARG002
        self.setStyleSheet("")

    def dropEvent(self, event: QDropEvent) -> None:
        self.setStyleSheet("")
        if not event.mimeData().hasFormat(self._mime):
            return
        source = bytes(event.mimeData().data(self._mime)).decode("ascii")
        event.acceptProposedAction()
        self.powerDropped.emit(source)


class NodeList(QWidget):
    """A vertical stack of cards for one level of the tree; a drop target for both.

    Renders the ordered nodes of one list — the character's top-level ``powers``
    (``parent_id`` empty) or a group's children (``parent_id`` = the group id). A drag
    over a card's *body* offers to **combine** (the target card is highlighted); a drag
    near a gap offers to **reorder/move** (a thin insertion line). Dropping emits the
    matching request for the section to apply against the model.
    """

    combineRequested = Signal(str, str)  # source id, target (drop-on) id
    moveRequested = Signal(str, str, int)  # source id, parent id (""=top), gap index

    def __init__(
        self, parent_id: str = "", parent: QWidget | None = None, *, mime: str = NODE_MIME
    ) -> None:
        super().__init__(parent)
        self.parent_id = parent_id
        self._mime = mime
        self.setAcceptDrops(True)
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._entries: list[tuple[str, QWidget]] = []
        self._indicator = QFrame(self)
        self._indicator.setFrameShape(QFrame.Shape.HLine)
        self._indicator.setFixedHeight(2)
        self._indicator.setStyleSheet(f"background: {theme.color('accent.dice')}; border: none;")
        self._indicator.hide()
        # A translucent outline laid over a card to mark it as the combine target.
        self._highlight = QFrame(self)
        self._highlight.setStyleSheet(
            f"border: {int(theme.metric('border.width.emphasis'))}px solid"
            f" {theme.color('accent.dice')};"
            f" border-radius: {int(theme.metric('radius.group'))}px;"
            f" background: {theme.wash('accent.dice', 0.15)};"
        )
        self._highlight.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._highlight.hide()

    def clear(self) -> None:
        """Remove every card (keeping the reusable indicator/highlight overlays)."""
        for index in reversed(range(self._layout.count())):
            widget = self._layout.itemAt(index).widget()
            if widget is not None and widget is not self._indicator:
                self._layout.takeAt(index)
                widget.setParent(None)
                widget.deleteLater()
        self._entries = []
        self._clear_hints()

    def add_entry(self, node_id: str, widget: QWidget) -> None:
        self._entries.append((node_id, widget))
        self._layout.addWidget(widget)

    # -- drop handling ----------------------------------------------------
    def _target(self, y: int) -> tuple[str, int, str, QWidget | None]:
        """Resolve a drop at vertical position *y* to a combine or reorder target.

        Returns ``("combine", pos, node_id, widget)`` for the body of an entry, or
        ``("reorder", gap_index, "", None)`` near a boundary / below all entries.
        """
        for pos, (node_id, widget) in enumerate(self._entries):
            top = widget.y()
            height = widget.height()
            if y < top + height * 0.25:
                return ("reorder", pos, "", None)
            if y < top + height * 0.75:
                return ("combine", pos, node_id, widget)
        return ("reorder", len(self._entries), "", None)

    def _show_reorder(self, index: int) -> None:
        self._highlight.hide()
        self._layout.removeWidget(self._indicator)
        self._layout.insertWidget(index, self._indicator)
        self._indicator.show()

    def _show_combine(self, widget: QWidget) -> None:
        self._indicator.hide()
        self._layout.removeWidget(self._indicator)
        self._highlight.setGeometry(widget.geometry())
        self._highlight.show()
        self._highlight.raise_()

    def _clear_hints(self) -> None:
        self._indicator.hide()
        self._layout.removeWidget(self._indicator)
        self._highlight.hide()

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if event.mimeData().hasFormat(self._mime):
            event.acceptProposedAction()

    def dragMoveEvent(self, event: QDragMoveEvent) -> None:
        if not event.mimeData().hasFormat(self._mime):
            return
        event.acceptProposedAction()
        kind, index, _node_id, widget = self._target(event.position().toPoint().y())
        if kind == "combine" and widget is not None:
            self._show_combine(widget)
        else:
            self._show_reorder(index)

    def dragLeaveEvent(self, event: object) -> None:  # noqa: ARG002
        self._clear_hints()

    def dropEvent(self, event: QDropEvent) -> None:
        if not event.mimeData().hasFormat(self._mime):
            return
        source = bytes(event.mimeData().data(self._mime)).decode("ascii")
        kind, index, node_id, _widget = self._target(event.position().toPoint().y())
        self._clear_hints()
        event.acceptProposedAction()
        if kind == "combine":
            self.combineRequested.emit(source, node_id)
        else:
            self.moveRequested.emit(source, self.parent_id, index)

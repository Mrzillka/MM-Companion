"""The pinned strip: blocks parked on one edge of the window, outside the page.

Everything on the character sheet normally lives on one scrolling page, so a
block you want permanently in view — Conditions during a fight, the hero points —
scrolls away as soon as you read further down. The strip is the place that
doesn't move: a region along one edge of the window (left, right, top or bottom)
holding blocks that stay put while the page scrolls behind them.

The strip is a small canvas, not a single stack. It holds **lines** along its
length, each line holding one or more blocks **across** it, so two pinned blocks
can sit beside each other exactly as two docked blocks share a row. Splitters
give both directions: drag the handle between two lines, or between two blocks
within a line.

Four widgets, in the order they nest:

* :class:`PinnedBoard` — what a host puts in its layout in place of the bare page
  scroll area. A splitter holding the page and the strip; its orientation and the
  order of those two children *are* the strip's edge, and dragging its handle
  sets the strip's thickness.
* :class:`PinnedPanel` — the strip itself: the pin handle plus a splitter of
  lines. The handle is always there — it is the icon an empty strip shows, the
  thing the whole strip is dragged to another edge by, and the button that opens
  its menu.
* :class:`_PinnedLine` — one line: a splitter of blocks across the strip.
* :class:`_PinnedSlot` — one pinned block, placed within its cell by the current
  alignment.

Two invariants shape the code:

* **A pinned block always shows all of its content.** Every splitter is
  non-collapsible and every :class:`~mm_companion.ui.block_frame.BlockFrame`
  already reports its full content size as its minimum, so a handle can be
  dragged only as far as the neighbouring block's content allows, and
  :meth:`PinnedPanel.minimumSizeHint` propagates the total up so it holds the
  *window* open rather than clipping. That minimum is capped at the screen, past
  which the strip scrolls — a valve so pinning one block too many can never leave
  a window too large to fit the display.
* **The canvas owns the model.** This module holds no arrangement state; it
  renders what :class:`~mm_companion.ui.block_canvas.BlockCanvas` hands it and
  reports drags and menu choices back, exactly as
  :class:`~mm_companion.ui.block_canvas.RowWidget` does for a docked row.
"""

from __future__ import annotations

from typing import Protocol

from PySide6.QtCore import QPoint, QRect, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QGridLayout,
    QMenu,
    QScrollArea,
    QSplitter,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from mm_companion.ui import layout_tree as lt
from mm_companion.ui import theme
from mm_companion.ui.block_frame import BlockFrame
from mm_companion.ui.block_sizes import UNBOUNDED
from mm_companion.ui.drop_feedback import DropFeedback, DropIndicator
from mm_companion.ui.grid_view import GridSplitter, build_node
from mm_companion.ui.pinned import (
    DEFAULT_EDGE,
    DEFAULT_EXTENT,
    PIN_EDGES,
    PinSlot,
    is_vertical_strip,
)
from mm_companion.ui.widgets import discard_widget

#: Floor for the strip's thickness while nothing is pinned. The handle's own size
#: hint wins when it is bigger, so a preset with larger type can't clip its icon.
EMPTY_EXTENT = 34

#: Width of the drop-zone band along each edge of the board while the strip is
#: being dragged to a new edge.
EDGE_BAND = 56

#: Fallback for the usable screen size when no screen resolves (headless tests).

EDGE_LABELS = {"left": "Left", "right": "Right", "top": "Top", "bottom": "Bottom"}


class PinHost(Protocol):
    """What the strip needs from its controller (the block canvas)."""

    def set_pin_edge(self, edge: str) -> None: ...
    def unpin_all(self) -> None: ...
    def pin_edge(self) -> str: ...


class PinnedHandle(QToolButton):
    """The strip's pin icon: its drop target, its drag handle, and its menu.

    Always visible, empty strip or not, because it is the only thing that is
    *always* the strip — a block can be dragged onto it, the whole strip is
    dragged to another edge by it, and a click on it opens the strip's menu. The
    drag only starts once the cursor has travelled the platform's drag distance,
    so a plain click still reads as a click.
    """

    drag_started = Signal()
    drag_moved = Signal(QPoint)
    drag_finished = Signal(QPoint)
    menu_requested = Signal(QPoint)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("pinnedHandle")
        self.setText("📌")
        self.setAutoRaise(True)
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.setToolTip(
            "The pinned strip — blocks here stay put while the page scrolls.\n"
            "Drag a block onto it to pin it, drag this pin to move the strip to\n"
            "another edge, or click for options."
        )
        self._press: QPoint | None = None
        self._dragging = False

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._press = event.globalPosition().toPoint()
            self._dragging = False
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._press is None:
            super().mouseMoveEvent(event)
            return
        position = event.globalPosition().toPoint()
        if not self._dragging:
            if (position - self._press).manhattanLength() < QApplication.startDragDistance():
                super().mouseMoveEvent(event)
                return
            self._dragging = True
            self.drag_started.emit()
        self.drag_moved.emit(position)
        event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self._dragging:
            self._dragging = False
            self._press = None
            self.setDown(False)  # ending a drag is not a click
            self.drag_finished.emit(event.globalPosition().toPoint())
            event.accept()
            return
        self._press = None
        super().mouseReleaseEvent(event)

    def contextMenuEvent(self, event) -> None:  # noqa: ANN001 - Qt signature
        self.menu_requested.emit(event.globalPos())
        event.accept()


class PinnedPanel(QFrame):
    """The strip: its pin handle, then the lines of pinned blocks."""

    #: Forwarded from the handle so the board can run the edge-change gesture.
    edge_drag_started = Signal()
    edge_drag_moved = Signal(QPoint)
    edge_drag_finished = Signal(QPoint)

    def __init__(self, host: PinHost, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("pinnedPanel")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._host = host
        self._edge = DEFAULT_EDGE
        # What is currently rendered, so an unrelated re-render is a no-op. None
        # means "assume nothing": the next render rebuilds (see invalidate).
        self._rendered: lt.Node | None = None
        self._root: QWidget | None = None
        self._frames: dict[str, BlockFrame] = {}

        self._handle = PinnedHandle(self)
        self._handle.drag_started.connect(self.edge_drag_started)
        self._handle.drag_moved.connect(self.edge_drag_moved)
        self._handle.drag_finished.connect(self.edge_drag_finished)
        self._handle.menu_requested.connect(self.open_menu)
        self._handle.clicked.connect(lambda: self.open_menu(self._handle_menu_pos()))

        # What the strip's blocks are rendered into. One widget, rebuilt from the
        # region tree by the same build_node the page uses, so a block in the strip
        # sits in the same kind of splitter with the same dividers and the same
        # detents as one on the page. The strip used to have its own two container
        # classes for this; the whole point of the rework is that it does not need
        # them.
        self._host_widget = QWidget()
        self._host_widget.setObjectName("pinnedHost")
        self._host_layout = QVBoxLayout(self._host_widget)
        self._host_layout.setContentsMargins(0, 0, 0, 0)
        self._host_layout.setSpacing(0)

        # The last-resort scroll. It is genuinely a last resort now: the strip
        # demands no room of the window, so a strip too short for its blocks
        # squashes them and they reflow, and only once every one of them is at its
        # own floor does anything here have somewhere to scroll to. Both axes stay
        # on `AsNeeded` for that case, where a bar beats clipping a block.
        self._scroll = QScrollArea(self)
        self._scroll.setObjectName("pinnedScroll")
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setWidget(self._host_widget)

        # A grid rather than a box layout so the edge can be changed by re-placing
        # two widgets: a QWidget's layout can't be swapped out once installed.
        self._grid = QGridLayout(self)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setSpacing(0)

        self._indicator = DropIndicator(self)
        self._drops = DropFeedback(self, "#pinnedPanel", radius="radius.canvas")
        self._drops.set_idle("")

        self._apply_edge()
        self._apply_empty_state()

    # -- rendering (driven by the canvas) ------------------------------------

    def invalidate(self) -> None:
        """Force the next render to rebuild even if the tree looks unchanged.

        Much less load-bearing than it was: the comparison is the whole tree now,
        sizes included, so a restore that changes only the proportions is noticed
        on its own — where the old keys-only comparison would silently keep what
        was on screen. It stays for the case the comparison genuinely cannot see,
        which is a *frame* being replaced under an unchanged key.
        """
        self._rendered = None

    def set_blocks(
        self,
        root: lt.Node | None,
        frames: dict[str, BlockFrame],
        edge: str,
    ) -> bool:
        """Render *root* in the strip, skipping the rebuild when nothing moved.

        The canvas re-renders on every structural change, most of which are about
        the page rather than the strip; rebuilding regardless would throw away the
        proportions the user dragged each time they reordered a docked row.

        Answers **whether it rebuilt**, so the board only re-asserts the strip's
        thickness when the strip itself actually changed.

        The tree is compared whole, sizes included, which is simpler than the old
        keys-only comparison and strictly more correct: a restore that changes only
        the proportions is a real change, and used to need an explicit
        :meth:`invalidate` to be noticed at all.
        """
        if root == self._rendered and edge == self._edge and self._root is not None:
            return False

        edge_changed = edge != self._edge
        self._rendered = root
        self._edge = edge
        self._frames = dict(frames)
        self._clear()
        if edge_changed:
            self._apply_edge()

        if root is not None:
            self._root = build_node(root, self._build_leaf, self._host_widget)
            self._host_layout.addWidget(self._root)
            for key in lt.keys(root):
                frame = self._frames.get(key)
                if frame is not None:
                    frame.show()
        self._apply_empty_state()
        # The strip only scrolls when its blocks genuinely don't fit, and its
        # contents have just changed — so start from the top rather than leaving
        # the offset the old arrangement was scrolled to, which would show the new
        # one decapitated.
        self._scroll.verticalScrollBar().setValue(0)
        self._scroll.horizontalScrollBar().setValue(0)
        return True

    def _build_leaf(self, leaf: lt.Leaf) -> QWidget:
        """One cell of the strip. A tab group there is the host's business, not
        the strip's, so a multi-key leaf shows the tab it has active."""
        return self._frames[leaf.active_key()]

    def split_paths(self) -> list[tuple[tuple[int, ...], QWidget]]:
        """Every splitter in the strip, with the tree path that reaches it.

        What the canvas needs to copy the live sizes back into the region tree —
        the same walk it does over the page, so both sides read their handles home
        through one function.
        """
        found: list[tuple[tuple[int, ...], QWidget]] = []

        def walk(widget: QWidget | None, path: tuple[int, ...]) -> None:
            if isinstance(widget, GridSplitter):
                found.append((path, widget))
                for index in range(widget.count()):
                    walk(widget.widget(index), path + (index,))

        walk(self._root, ())
        return found

    def splitters(self) -> list[QWidget]:
        """Every divider-bearing splitter in the strip, outermost first."""
        return [widget for _path, widget in self.split_paths()]

    def _clear(self) -> None:
        """Empty the strip, handing back every block that is still ours.

        Only the ones still parented into the strip: by the time a re-render gets
        here, a block being dragged out has *already* been moved into its floating
        window, and taking it back would empty that window out (the canvas guards
        its own row teardown the same way). And only the *containers* are shed — a
        cell holding one block **is** that block's frame, so discarding it would
        destroy a live block, exactly as it did on the page.
        """
        root, self._root = self._root, None
        if root is None:
            return
        # Asked of the widget tree, not of the frame dict this panel was last
        # handed. That dict is a snapshot, and a block destroyed since it was taken
        # (a Notes copy closed, say) is a dangling C++ object that raises the
        # moment anything asks it a question — and an exception here would take the
        # application down, not merely fail to tidy up.
        for frame in [root, *root.findChildren(BlockFrame)]:
            if isinstance(frame, BlockFrame):
                frame.hide()
                frame.setParent(self)
        self._host_layout.removeWidget(root)
        # A cell holding one block **is** that block's frame, and it has just been
        # rescued above; anything else is a container we made.
        if not isinstance(root, BlockFrame):
            discard_widget(root)

    def _apply_edge(self) -> None:
        """Put the handle at the strip's leading corner and the lines beside it."""
        vertical = is_vertical_strip(self._edge)
        for widget in (self._handle, self._scroll):
            self._grid.removeWidget(widget)
        content_row, content_col = (1, 0) if vertical else (0, 1)
        self._grid.addWidget(self._handle, 0, 0, alignment=Qt.AlignmentFlag.AlignCenter)
        self._grid.addWidget(self._scroll, content_row, content_col)
        for index in (0, 1):
            self._grid.setRowStretch(index, 1 if index == content_row else 0)
            self._grid.setColumnStretch(index, 1 if index == content_col else 0)

    def _apply_empty_state(self) -> None:
        """Collapse to the handle when nothing is pinned, expand when something is."""
        empty = self._root is None
        self._scroll.setVisible(not empty)
        self.setMinimumSize(0, 0)
        self.setMaximumSize(UNBOUNDED, UNBOUNDED)
        if empty:
            # A strip the user could drag wide while it holds nothing would just be
            # a dead margin, so an empty one is pinned to the size of its handle.
            if is_vertical_strip(self._edge):
                self.setFixedWidth(self.empty_extent())
            else:
                self.setFixedHeight(self.empty_extent())
        self.updateGeometry()

    def empty_extent(self) -> int:
        """How thick the strip is with nothing pinned: its handle, and no more."""
        hint = self._handle.sizeHint()
        along_thickness = hint.width() if is_vertical_strip(self._edge) else hint.height()
        return max(EMPTY_EXTENT, along_thickness + 4)

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt override
        """Its handle, and nothing about what is pinned in it.

        This used to report the strip's whole content — capped at the usable
        screen — precisely so that the *window* would be held open rather than the
        strip growing a scrollbar. That was the right answer while a block's
        minimum was its content and squashing one meant clipping it. It is the
        wrong answer now, and the last place in the app where the content could
        still push the window around: a pinned block reflows and, past that,
        scrolls inside its own frame, so the strip has no width it has to demand.

        The handle stays, because the strip has to remain findable and droppable
        however narrow it is dragged.
        """
        base = super().minimumSizeHint()
        hint = self._handle.sizeHint()
        vertical = is_vertical_strip(self._edge)
        return QSize(
            max(base.width(), hint.width() if vertical else 0),
            max(base.height(), hint.height() if not vertical else 0),
        )

    # -- queries the canvas and the board ask ---------------------------------

    def is_empty(self) -> bool:
        return self._root is None

    def edge(self) -> str:
        return self._edge

    def region(self) -> lt.Node | None:
        """The tree currently rendered."""
        return self._rendered

    def frames(self) -> list[BlockFrame]:
        """Every block in the strip, in reading order."""
        return [self._frames[key] for key in lt.keys(self._rendered) if key in self._frames]

    # -- drop target ----------------------------------------------------------

    #: How far inside a block's edges a drop has to be to mean "beside" rather
    #: than "at this end of the strip". The same band the page uses at a row edge,
    #: and it is what keeps both answers reachable.
    EDGE_BAND = 12

    def drop_slot(self, global_pos: QPoint) -> PinSlot | None:
        """Where a block dropped at *global_pos* would land, or None if off the strip.

        The same four-way reading the page makes, minus the merge: a drop names the
        block it lands beside and which side of it to take. A strip with nothing in
        it answers with no target at all, which the canvas reads as "you are the
        first thing here".
        """
        if not self.isVisible():
            return None
        local = self.mapFromGlobal(global_pos)
        if not self.rect().contains(local):
            return None
        frame = self._frame_under(global_pos)
        if frame is None:
            return PinSlot()
        return PinSlot(frame.key, self._side_of(frame, global_pos))

    def _frame_under(self, global_pos: QPoint) -> BlockFrame | None:
        for frame in self.frames():
            if not frame.isVisible():
                continue
            if frame.rect().contains(frame.mapFromGlobal(global_pos)):
                return frame
        return None

    def _side_of(self, frame: BlockFrame, global_pos: QPoint) -> str:
        """Which side of *frame* the pointer is nearest, along the strip first.

        Along the strip is the axis a drop most often means — a strip is a column
        of blocks, and putting one above or below another is the ordinary gesture —
        so the bands at either end of the block are checked before the sides.
        """
        local = frame.mapFromGlobal(global_pos)
        rect = frame.rect()
        vertical = is_vertical_strip(self._edge)
        if vertical:
            band = min(self.EDGE_BAND * 2, max(1, rect.height() // 3))
            if local.y() < rect.top() + band:
                return "top"
            if local.y() > rect.bottom() - band:
                return "bottom"
            return "left" if local.x() < rect.center().x() else "right"
        band = min(self.EDGE_BAND * 2, max(1, rect.width() // 3))
        if local.x() < rect.left() + band:
            return "left"
        if local.x() > rect.right() - band:
            return "right"
        return "top" if local.y() < rect.center().y() else "bottom"

    def show_drop(self, slot: PinSlot) -> None:
        """Light the strip up and mark where the block would land."""
        self._drops.show_accept()
        rect = self._indicator_rect(slot)
        if rect is None:
            self._indicator.hide_indicator()
            return
        self._indicator.move_to(rect)

    def hide_drop(self) -> None:
        self._drops.clear()
        self._indicator.hide_indicator()

    def _indicator_rect(self, slot: PinSlot) -> QRect | None:
        """The insert line, along whichever edge of the target block was named."""
        if slot.target is None:
            return None
        frame = self._frames.get(slot.target)
        if frame is None or not frame.isVisible():
            return None
        geo = QRect(self.mapFromGlobal(frame.mapToGlobal(QPoint(0, 0))), frame.size())
        thick = 3
        if slot.side == "left":
            return QRect(geo.left() - 1, geo.top(), thick, geo.height())
        if slot.side == "right":
            return QRect(geo.right() - 1, geo.top(), thick, geo.height())
        if slot.side == "top":
            return QRect(geo.left(), geo.top() - 1, geo.width(), thick)
        return QRect(geo.left(), geo.bottom() - 1, geo.width(), thick)

    # -- the menu -------------------------------------------------------------

    def contextMenuEvent(self, event) -> None:  # noqa: ANN001 - Qt signature
        self.open_menu(event.globalPos())
        event.accept()

    def open_menu(self, global_pos: QPoint) -> None:
        """Position / alignment / unpin — the menu route to what the drags do."""
        self.build_menu().exec(global_pos)

    def build_menu(self) -> QMenu:
        """The strip's menu, built fresh so it always shows the live state."""
        menu = QMenu(self)
        position = menu.addMenu("Position")
        for edge in PIN_EDGES:
            action = position.addAction(EDGE_LABELS[edge])
            action.setCheckable(True)
            action.setChecked(edge == self._host.pin_edge())
            action.triggered.connect(lambda _checked=False, e=edge: self._host.set_pin_edge(e))
        menu.addSeparator()
        unpin = menu.addAction("Unpin all")
        unpin.setEnabled(self._root is not None)
        unpin.triggered.connect(lambda: self._host.unpin_all())
        return menu

    def _handle_menu_pos(self) -> QPoint:
        return self._handle.mapToGlobal(self._handle.rect().bottomLeft())


class EdgeZoneOverlay(QWidget):
    """The four drop zones shown while the strip is dragged to another edge.

    Transparent to the mouse: the gesture is driven by the handle's own move
    events (the button is still held down over there), so the overlay must never
    become the widget under the cursor.
    """

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("pinnedEdgeOverlay")
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._hover: str | None = None
        self._zones = {edge: QFrame(self) for edge in PIN_EDGES}
        for zone in self._zones.values():
            zone.setObjectName("pinnedEdgeZone")
        self._restyle()

    def edge_at(self, global_pos: QPoint) -> str | None:
        """Which edge band *global_pos* falls in, or None (outside, or the middle)."""
        local = self.mapFromGlobal(global_pos)
        if not self.rect().contains(local):
            return None
        for edge, zone in self._zones.items():
            if zone.geometry().contains(local):
                return edge
        return None

    def hovered_edge(self) -> str | None:
        return self._hover

    def set_hover(self, edge: str | None) -> None:
        if edge == self._hover:
            return
        self._hover = edge
        self._restyle()

    def _restyle(self) -> None:
        idle = theme.wash("accent", 0.08)
        active = theme.wash("accent", 0.28)
        width = int(theme.metric("border.width.emphasis"))
        accent = theme.color("accent")
        for edge, zone in self._zones.items():
            lit = edge == self._hover
            zone.setStyleSheet(
                f"#pinnedEdgeZone {{ background: {active if lit else idle};"
                f" border: {width}px solid {accent if lit else 'transparent'}; }}"
            )

    def resizeEvent(self, event) -> None:  # noqa: ANN001 - Qt signature
        """Lay the four bands along the edges (the corners go to the side bands)."""
        super().resizeEvent(event)
        width, height = self.width(), self.height()
        band = min(EDGE_BAND, max(1, min(width, height) // 3))
        self._zones["left"].setGeometry(0, 0, band, height)
        self._zones["right"].setGeometry(width - band, 0, band, height)
        self._zones["top"].setGeometry(band, 0, max(0, width - 2 * band), band)
        self._zones["bottom"].setGeometry(band, height - band, max(0, width - 2 * band), band)


class PinnedBoard(QWidget):
    """The page and the pinned strip side by side; the strip's edge *is* the layout.

    A host swaps this in for its bare page scroll area and is otherwise unchanged:
    it still builds the scroll area, still hands it to the canvas, and reads it
    back through :meth:`page_scroll_area`.
    """

    def __init__(self, page: QWidget, host: PinHost, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("pinnedBoard")
        self._host = host
        self._page = page
        self._edge = DEFAULT_EDGE
        self._extent = DEFAULT_EXTENT
        self._overlay: EdgeZoneOverlay | None = None

        self.panel = PinnedPanel(host, self)
        self.panel.edge_drag_started.connect(self._edge_drag_started)
        self.panel.edge_drag_moved.connect(self._edge_drag_moved)
        self.panel.edge_drag_finished.connect(self._edge_drag_finished)

        self._splitter = QSplitter(Qt.Orientation.Horizontal, self)
        self._splitter.setObjectName("pinnedBoardSplitter")
        self._splitter.setChildrenCollapsible(False)
        # The one place the strip's thickness is *chosen*: this signal fires for a
        # dragged handle and never for setSizes, so content that forces the strip
        # open is not mistaken for a preference (see _remember_dragged_extent).
        self._splitter.splitterMoved.connect(self._remember_dragged_extent)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._splitter)

        # A rebuilt strip's minimum is not known until Qt has re-laid it out, and a
        # splitter clamps the sizes it is given against the minimum *of the moment* —
        # so an extent applied during the rebuild gets pinned to a stale, larger one
        # (an edge change is the clear case: the strip keeps the old axis's minimum)
        # and nothing brings it back down. So when a pass doesn't get the thickness
        # it asked for, it tries again on a later turn — a few times, then gives up,
        # because a minimum that is genuinely larger will never yield.
        self._settle_tries = 0
        self._settle = QTimer(self)
        self._settle.setSingleShot(True)
        self._settle.timeout.connect(self._apply_extent)

        self._apply_edge()

    # -- the host's seam ------------------------------------------------------

    def page_scroll_area(self) -> QWidget:
        return self._page

    # -- what the canvas drives ----------------------------------------------

    def set_blocks(
        self,
        root: lt.Node | None,
        frames: dict[str, BlockFrame],
        edge: str,
    ) -> bool:
        """Render the strip, re-laying the board out first when the edge changed.

        Answers whether the strip actually rebuilt, which the canvas needs: the
        dividers are new widgets on a rebuild and it has to follow the new ones,
        and following the old ones twice would close a block twice.

        The thickness is only re-asserted when the strip actually rebuilt. The canvas
        re-renders the strip on every structural change, and most of them are about
        the page — so settling a thickness nothing asked to change meant five
        ``setSizes`` in 80ms fighting a minimum that was already satisfied, on every
        single drop. The retries exist for a *stale* minimum, which is only possible
        just after a rebuild; a minimum that is genuinely larger never yields, and on
        a stock sheet it never can (the default extent is 320 against the Dice
        block's 360 floor), so the loop ran to its cap every time. That was the
        jitter when rearranging blocks.
        """
        if edge != self._edge:
            self._edge = edge
            self._fresh_settle()
            self._apply_edge()  # ends in _apply_extent, as it always has
        rebuilt = self.panel.set_blocks(root, frames, edge)
        if rebuilt:
            self._fresh_settle()
            self._apply_extent()
        return rebuilt

    def invalidate(self) -> None:
        """Make the next render rebuild the strip (see :meth:`PinnedPanel.invalidate`)."""
        self.panel.invalidate()

    def set_extent(self, extent: int) -> None:
        """Set the strip's thickness — a restored layout, not a live drag."""
        if extent > 0:
            self._extent = extent
        self._fresh_settle()
        self._apply_extent()

    def extent(self) -> int:
        """The strip's live thickness, or 0 while it is empty.

        This can be *more* than :meth:`desired_extent` when the pinned blocks force
        the strip open — which is why it is not what gets remembered.
        """
        if self.panel.is_empty():
            return 0
        sizes = self._splitter.sizes()
        index = self._panel_index()
        return sizes[index] if index < len(sizes) else 0

    def desired_extent(self) -> int:
        """The thickness the strip has been *asked* for, dragged or restored.

        The value worth persisting. Reading the live thickness instead would bake
        in whatever a block happened to force at the time, and the strip would keep
        that width forever — leaving dead space beside a block that can't fill it
        once the block that needed the room has moved or gone.
        """
        return self._extent

    def drop_slot(self, global_pos: QPoint) -> PinSlot | None:
        return self.panel.drop_slot(global_pos)

    def show_drop(self, slot: PinSlot) -> None:
        self.panel.show_drop(slot)

    def hide_drop(self) -> None:
        self.panel.hide_drop()

    def edge(self) -> str:
        return self._edge

    # -- layout ---------------------------------------------------------------

    def _panel_index(self) -> int:
        """Which side of the splitter the strip sits on."""
        return 0 if self._edge in ("left", "top") else 1

    def _apply_edge(self) -> None:
        """Re-orient the splitter and put the strip on the page's chosen side.

        ``insertWidget`` both adds and *moves*, so the two children are re-ordered
        without ever being detached — a widget briefly parented to nothing would
        flash up as a window of its own.
        """
        self._splitter.setOrientation(
            Qt.Orientation.Horizontal if is_vertical_strip(self._edge) else Qt.Orientation.Vertical
        )
        panel_first = self._panel_index() == 0
        order = [self.panel, self._page] if panel_first else [self._page, self.panel]
        for index, widget in enumerate(order):
            self._splitter.insertWidget(index, widget)
            widget.show()
        # The page absorbs a window resize; the strip keeps the thickness it was
        # dragged to.
        self._splitter.setStretchFactor(0 if panel_first else 1, 0)
        self._splitter.setStretchFactor(1 if panel_first else 0, 1)
        self._apply_extent()

    def _remember_dragged_extent(self, _pos: int = 0, _index: int = 0) -> None:
        """Take the thickness the user just dragged the handle to as the wanted one.

        Only a real drag reaches here: ``splitterMoved`` is emitted from the handle,
        not from ``setSizes``. That distinction is the whole point — the strip is
        also pushed wider by a block that needs the room, and treating *that* as a
        choice left it stuck at that width afterwards.
        """
        if self.panel.is_empty():
            return  # an empty strip is its handle; there is no thickness to choose
        live = self.extent()
        if live > 0:
            self._extent = live

    def _apply_extent(self) -> None:
        """Give the splitter the strip's thickness and the page the rest.

        Asks again on a later turn when it doesn't get the thickness it asked for,
        which is how a size clamped against a not-yet-updated minimum converges
        instead of sticking (see the ``_settle`` timer in ``__init__``).
        """
        total = self._splitter.width() if is_vertical_strip(self._edge) else self._splitter.height()
        if total <= 0:
            # Not laid out yet; any ratio will do.
            total = max(self._extent, self.panel.empty_extent()) * 3
        # An empty strip is its handle and nothing else. It has to be re-split
        # explicitly: the panel pinning its own thin size doesn't move a splitter
        # that has already been given sizes, so the strip would keep the width it
        # had when its last block left until the handle was nudged.
        wanted = self.panel.empty_extent() if self.panel.is_empty() else self._extent
        strip = max(1, min(wanted, total - 1))
        sizes = [total - strip, strip]
        if self._panel_index() == 0:
            sizes.reverse()
        self._splitter.setSizes(sizes)
        if not self.panel.is_empty() and self.extent() != strip:
            self._ask_again()

    #: How many turns the extent may spend converging, and how long each waits.
    SETTLE_TRIES = 5
    SETTLE_MS = 16

    def _ask_again(self) -> None:
        """Re-apply the extent on a later turn, a bounded number of times."""
        if self._settle_tries >= self.SETTLE_TRIES:
            return
        self._settle_tries += 1
        self._settle.start(self.SETTLE_MS)

    def _fresh_settle(self) -> None:
        """A new intent for the thickness: allow the retries again."""
        self._settle_tries = 0

    # -- the edge-change gesture ----------------------------------------------

    def _edge_drag_started(self) -> None:
        overlay = EdgeZoneOverlay(self)
        overlay.setGeometry(self.rect())
        overlay.show()
        overlay.raise_()
        self._overlay = overlay

    def _edge_drag_moved(self, global_pos: QPoint) -> None:
        if self._overlay is not None:
            self._overlay.set_hover(self._overlay.edge_at(global_pos))

    def _edge_drag_finished(self, global_pos: QPoint) -> None:
        overlay, self._overlay = self._overlay, None
        if overlay is None:
            return
        edge = overlay.edge_at(global_pos)
        overlay.hide()
        overlay.deleteLater()
        if edge is not None:
            self._host.set_pin_edge(edge)

    def resizeEvent(self, event) -> None:  # noqa: ANN001 - Qt signature
        super().resizeEvent(event)
        if self._overlay is not None:
            self._overlay.setGeometry(self.rect())

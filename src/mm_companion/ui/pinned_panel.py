"""The pinned strip: blocks parked on one edge of the window, outside the page.

Everything on the character sheet normally lives on one scrolling page, so a
block you want permanently in view — Conditions during a fight, the hero points —
scrolls away as soon as you read further down. The strip is the place that
doesn't move: a region along one edge of the window (left, right, top or bottom)
holding blocks that stay put while the page scrolls behind them.

Three widgets, in the order they nest:

* :class:`PinnedBoard` — what a host puts in its layout in place of the bare page
  scroll area. A splitter holding the page and the strip; its orientation and the
  order of those two children *are* the strip's edge, and dragging its handle
  sets the strip's thickness.
* :class:`PinnedPanel` — the strip itself. A grip (drag it to another edge,
  right-click it for the menu) beside a splitter of pinned blocks. Empty, it
  collapses to a thin bar showing one small pin icon, which is the drop target
  that gets the first block in.
* :class:`_PinnedSlot` — one pinned block, positioned across the strip according
  to the current alignment.

Two invariants shape the code:

* **A pinned block always shows all of its content.** The splitter is
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

from PySide6.QtCore import QPoint, QRect, QSize, Qt, Signal
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QMenu,
    QScrollArea,
    QSplitter,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from mm_companion.ui import theme
from mm_companion.ui.block_frame import BlockFrame
from mm_companion.ui.block_sizes import UNBOUNDED
from mm_companion.ui.drop_feedback import DropFeedback, DropIndicator
from mm_companion.ui.pinned import (
    DEFAULT_ALIGN,
    DEFAULT_EDGE,
    DEFAULT_EXTENT,
    PIN_ALIGNMENTS,
    PIN_EDGES,
    is_vertical_strip,
)

#: Thickness of the strip while nothing is pinned — just enough for its icon.
EMPTY_EXTENT = 34

#: Thickness of the grip bar the strip is dragged to another edge by.
GRIP_EXTENT = 12

#: Width of the drop-zone band along each edge of the board while the strip is
#: being dragged to a new edge.
EDGE_BAND = 56

#: Fallback for the usable screen size when no screen resolves (headless tests).
FALLBACK_SCREEN = QSize(1920, 1080)

EDGE_LABELS = {"left": "Left", "right": "Right", "top": "Top", "bottom": "Bottom"}
ALIGN_LABELS = {"fill": "Fill", "start": "Start", "center": "Center", "end": "End"}


class PinHost(Protocol):
    """What the strip needs from its controller (the block canvas)."""

    def set_pin_edge(self, edge: str) -> None: ...
    def set_pin_align(self, align: str) -> None: ...
    def unpin_all(self) -> None: ...
    def pin_edge(self) -> str: ...
    def pin_align(self) -> str: ...


def _cross_alignment(vertical_strip: bool, align: str) -> Qt.AlignmentFlag | None:
    """The layout flag putting a block at *align* across the strip (None = fill).

    "Across" is the axis the strip is thin in: the horizontal one for a left/right
    strip, the vertical one for a top/bottom strip. Only that axis is constrained,
    so a block still fills the strip along its length — which is the axis the
    splitter handles govern.
    """
    if align == "fill":
        return None
    if vertical_strip:
        flags = {
            "start": Qt.AlignmentFlag.AlignLeft,
            "center": Qt.AlignmentFlag.AlignHCenter,
            "end": Qt.AlignmentFlag.AlignRight,
        }
    else:
        flags = {
            "start": Qt.AlignmentFlag.AlignTop,
            "center": Qt.AlignmentFlag.AlignVCenter,
            "end": Qt.AlignmentFlag.AlignBottom,
        }
    return flags.get(align)


def _usable_screen(widget: QWidget) -> QSize:
    """The screen area a window can occupy, or a generous fallback when headless."""
    screen = widget.screen()
    if screen is None:
        return FALLBACK_SCREEN
    return screen.availableGeometry().size()


class _PinnedSlot(QWidget):
    """One pinned block, aligned across the strip.

    A wrapper rather than putting the frame in the splitter directly: a splitter
    always stretches its children to the full thickness of the strip, which is
    exactly what the ``fill`` alignment wants and exactly what the other three
    do not.
    """

    def __init__(
        self,
        frame: BlockFrame,
        vertical_strip: bool,
        align: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.frame = frame
        layout = QHBoxLayout(self) if vertical_strip else QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        flags = _cross_alignment(vertical_strip, align) or Qt.AlignmentFlag(0)
        # A block that pins its own size along the strip (Abilities caps its
        # height) can't take the space its splitter band gives it, and a box layout
        # would centre it there — a block adrift with a gap above and below. Anchor
        # it to the start of its band instead; every other block still fills, so a
        # handle drag visibly resizes it.
        capped = (frame.maximumHeight() if vertical_strip else frame.maximumWidth()) < UNBOUNDED
        if capped:
            flags |= Qt.AlignmentFlag.AlignTop if vertical_strip else Qt.AlignmentFlag.AlignLeft
        if flags:
            layout.addWidget(frame, alignment=flags)
        else:
            layout.addWidget(frame)
        frame.show()

    def release_frame(self) -> BlockFrame:
        """Hand the block back before this slot is destroyed.

        Without it the frame is still a child of the slot when the slot is freed,
        and Qt destroys the block's C++ object along with it — the same rescue the
        canvas performs when it tears a row down.
        """
        layout = self.layout()
        if layout is not None:
            layout.removeWidget(self.frame)
        return self.frame


class PinnedGrip(QFrame):
    """The strip's handle: drag it to another edge, right-click it for the menu.

    Deliberately dumb, like a block's title bar: it forwards the gesture to the
    panel, which forwards it to the board — the only widget that knows where the
    window's edges are. The drag only *starts* once the cursor has travelled the
    platform's drag distance, so a plain click opens nothing.
    """

    drag_started = Signal()
    drag_moved = Signal(QPoint)
    drag_finished = Signal(QPoint)
    menu_requested = Signal(QPoint)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("pinnedGrip")
        # A raised panel so the handle is visible even under a preset that dresses
        # nothing (Classic paints the native frame and no stylesheet reaches here).
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setFrameShadow(QFrame.Shadow.Raised)
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.setToolTip("Drag to move the pinned strip to another edge")
        self._press: QPoint | None = None
        self._dragging = False

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._press = event.globalPosition().toPoint()
            self._dragging = False
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._press is None:
            super().mouseMoveEvent(event)
            return
        position = event.globalPosition().toPoint()
        if not self._dragging:
            if (position - self._press).manhattanLength() < QApplication.startDragDistance():
                event.accept()
                return
            self._dragging = True
            self.drag_started.emit()
        self.drag_moved.emit(position)
        event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self._press is not None:
            dragging, self._dragging, self._press = self._dragging, False, None
            if dragging:
                self.drag_finished.emit(event.globalPosition().toPoint())
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def contextMenuEvent(self, event) -> None:  # noqa: ANN001 - Qt signature
        self.menu_requested.emit(event.globalPos())
        event.accept()


class PinnedPanel(QFrame):
    """The strip: a grip, then the pinned blocks — or one icon while it is empty."""

    #: Forwarded from the grip so the board can run the edge-change gesture.
    edge_drag_started = Signal()
    edge_drag_moved = Signal(QPoint)
    edge_drag_finished = Signal(QPoint)

    def __init__(self, host: PinHost, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("pinnedPanel")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._host = host
        self._edge = DEFAULT_EDGE
        self._align = DEFAULT_ALIGN
        self._keys: list[str] = []
        self._slots: list[_PinnedSlot] = []

        self._grip = PinnedGrip(self)
        self._grip.drag_started.connect(self.edge_drag_started)
        self._grip.drag_moved.connect(self.edge_drag_moved)
        self._grip.drag_finished.connect(self.edge_drag_finished)
        self._grip.menu_requested.connect(self.open_menu)

        # The splitter is what makes a pinned block resizable: non-collapsible,
        # and each frame's minimum is its whole content, so a handle stops where
        # the content does.
        self._splitter = QSplitter(Qt.Orientation.Vertical, self)
        self._splitter.setObjectName("pinnedSplitter")
        self._splitter.setChildrenCollapsible(False)

        # The last-resort scroll (see the module docstring): with the strip's
        # minimum holding the window open, the content fits and no bar ever shows.
        self._scroll = QScrollArea(self)
        self._scroll.setObjectName("pinnedScroll")
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setWidget(self._splitter)

        self._empty = QToolButton(self)
        self._empty.setObjectName("pinnedEmpty")
        self._empty.setText("📌")
        self._empty.setAutoRaise(True)
        self._empty.setToolTip(
            "The pinned strip — drag a block onto this icon to keep it in view "
            "while the page scrolls. Click for options."
        )
        self._empty.clicked.connect(lambda: self.open_menu(self._empty_menu_pos()))

        # A grid rather than a box layout so the edge can be changed by re-placing
        # three widgets: a QWidget's layout can't be swapped out once installed.
        self._grid = QGridLayout(self)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setSpacing(0)

        self._indicator = DropIndicator(self)
        self._drops = DropFeedback(self, "#pinnedPanel", radius="radius.canvas")
        self._drops.set_idle("")

        self._apply_edge()
        self._apply_empty_state()

    # -- rendering (driven by the canvas) ------------------------------------

    def set_blocks(self, frames: list[BlockFrame], edge: str, align: str, sizes: list[int]) -> None:
        """Render *frames* along the strip, skipping the rebuild when nothing moved.

        The canvas re-renders on every structural change, most of which are about
        the page rather than the strip; rebuilding regardless would throw the
        proportions the user dragged away each time they reordered a docked row.
        """
        keys = [frame.key for frame in frames]
        if keys == self._keys and edge == self._edge and align == self._align:
            return

        edge_changed = edge != self._edge
        self._keys = keys
        self._edge = edge
        self._align = align
        self._clear()
        if edge_changed:
            self._apply_edge()

        vertical = is_vertical_strip(edge)
        self._splitter.setOrientation(
            Qt.Orientation.Vertical if vertical else Qt.Orientation.Horizontal
        )
        for frame in frames:
            slot = _PinnedSlot(frame, vertical, align, self._splitter)
            self._splitter.addWidget(slot)
            self._slots.append(slot)
        if len(sizes) == len(frames) and all(size > 0 for size in sizes):
            self._splitter.setSizes(sizes)
        self._apply_empty_state()

    def _clear(self) -> None:
        """Empty the splitter, handing every block back before its slot is freed."""
        for slot in self._slots:
            frame = slot.release_frame()
            frame.setParent(self)
            frame.hide()
            slot.setParent(None)
            slot.deleteLater()
        self._slots = []

    def _apply_edge(self) -> None:
        """Put the grip on the strip's leading side and the content beside it."""
        vertical = is_vertical_strip(self._edge)
        for widget in (self._grip, self._scroll, self._empty):
            self._grid.removeWidget(widget)
        if vertical:
            self._grip.setFixedHeight(GRIP_EXTENT)
            self._grip.setMinimumWidth(0)
            self._grip.setMaximumWidth(UNBOUNDED)
            content_row, content_col = 1, 0
        else:
            self._grip.setFixedWidth(GRIP_EXTENT)
            self._grip.setMinimumHeight(0)
            self._grip.setMaximumHeight(UNBOUNDED)
            content_row, content_col = 0, 1
        self._grid.addWidget(self._grip, 0, 0)
        # The scroll area and the empty-state icon share the content cell; only
        # one of them is ever visible, and a hidden widget takes no part in the
        # layout.
        self._grid.addWidget(self._scroll, content_row, content_col)
        self._grid.addWidget(
            self._empty, content_row, content_col, alignment=Qt.AlignmentFlag.AlignCenter
        )
        for row in (0, 1):
            self._grid.setRowStretch(row, 1 if row == content_row else 0)
            self._grid.setColumnStretch(row, 1 if row == content_col else 0)

    def _apply_empty_state(self) -> None:
        """Collapse to the icon when nothing is pinned, expand when something is."""
        empty = not self._slots
        self._scroll.setVisible(not empty)
        self._empty.setVisible(empty)
        self.setMinimumSize(0, 0)
        self.setMaximumSize(UNBOUNDED, UNBOUNDED)
        if empty:
            # A strip the user could drag wide while it holds nothing would just be
            # a dead margin, so an empty one is pinned to the width of its icon.
            if is_vertical_strip(self._edge):
                self.setFixedWidth(EMPTY_EXTENT)
            else:
                self.setFixedHeight(EMPTY_EXTENT)
        self.updateGeometry()

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt override
        """The room the pinned blocks need, so the *window* keeps them whole.

        A scroll area's own minimum is tiny — left alone it would answer a window
        too small for the strip by growing a scrollbar, which is the one thing the
        strip is not for. Reporting the content's minimum instead pushes back on
        the window. It is capped at the usable screen so that pinning a very tall
        block can't demand a window bigger than the display; past that cap the
        scroll area does take over.
        """
        base = super().minimumSizeHint()
        if not self._slots:
            return base
        content = self._splitter.minimumSizeHint()
        vertical = is_vertical_strip(self._edge)
        width = content.width() + (0 if vertical else GRIP_EXTENT)
        height = content.height() + (GRIP_EXTENT if vertical else 0)
        cap = _usable_screen(self)
        return QSize(
            max(base.width(), min(width, cap.width())),
            max(base.height(), min(height, cap.height())),
        )

    # -- queries the canvas and the board ask ---------------------------------

    def is_empty(self) -> bool:
        return not self._slots

    def edge(self) -> str:
        return self._edge

    def align(self) -> str:
        return self._align

    def block_sizes(self) -> list[int]:
        """The splitter's current sizes — the proportions the user dragged."""
        return list(self._splitter.sizes()) if self._slots else []

    def frames(self) -> list[BlockFrame]:
        return [slot.frame for slot in self._slots]

    # -- drop target ----------------------------------------------------------

    def drop_index(self, global_pos: QPoint) -> int | None:
        """Which slot a block dropped at *global_pos* would take, or None if off it."""
        if not self.isVisible():
            return None
        local = self.mapFromGlobal(global_pos)
        if not self.rect().contains(local):
            return None
        if not self._slots:
            return 0
        vertical = is_vertical_strip(self._edge)
        point = local.y() if vertical else local.x()
        for index, slot in enumerate(self._slots):
            center = slot.mapTo(self, slot.rect().center())
            if point < (center.y() if vertical else center.x()):
                return index
        return len(self._slots)

    def show_drop(self, index: int) -> None:
        """Light the strip up and mark where the block would land."""
        self._drops.show_accept()
        if not self._slots:
            self._indicator.hide_indicator()
            return
        self._indicator.move_to(self._boundary_rect(index))

    def hide_drop(self) -> None:
        self._drops.clear()
        self._indicator.hide_indicator()

    def _boundary_rect(self, index: int) -> QRect:
        """The insert line between two pinned blocks, in panel coordinates."""
        geoms = [QRect(slot.mapTo(self, QPoint(0, 0)), slot.size()) for slot in self._slots]
        vertical = is_vertical_strip(self._edge)
        if index <= 0:
            position = geoms[0].top() if vertical else geoms[0].left()
        elif index >= len(geoms):
            position = geoms[-1].bottom() if vertical else geoms[-1].right()
        elif vertical:
            position = (geoms[index - 1].bottom() + geoms[index].top()) // 2
        else:
            position = (geoms[index - 1].right() + geoms[index].left()) // 2
        if vertical:
            return QRect(2, position - 1, max(0, self.width() - 4), 3)
        return QRect(position - 1, 2, 3, max(0, self.height() - 4))

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
        alignment = menu.addMenu("Alignment")
        for align in PIN_ALIGNMENTS:
            action = alignment.addAction(ALIGN_LABELS[align])
            action.setCheckable(True)
            action.setChecked(align == self._host.pin_align())
            action.triggered.connect(lambda _checked=False, a=align: self._host.set_pin_align(a))
        menu.addSeparator()
        unpin = menu.addAction("Unpin all")
        unpin.setEnabled(bool(self._slots))
        unpin.triggered.connect(lambda: self._host.unpin_all())
        return menu

    def _empty_menu_pos(self) -> QPoint:
        return self._empty.mapToGlobal(self._empty.rect().bottomLeft())


class EdgeZoneOverlay(QWidget):
    """The four drop zones shown while the strip is dragged to another edge.

    Transparent to the mouse: the gesture is driven by the grip's own move events
    (the button is still held down over there), so the overlay must never become
    the widget under the cursor.
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

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._splitter)
        self._apply_edge()

    # -- the host's seam ------------------------------------------------------

    def page_scroll_area(self) -> QWidget:
        return self._page

    # -- what the canvas drives ----------------------------------------------

    def set_blocks(self, frames: list[BlockFrame], edge: str, align: str, sizes: list[int]) -> None:
        """Render the strip, re-laying the board out first when the edge changed."""
        if edge != self._edge:
            self._edge = edge
            self._apply_edge()
        self.panel.set_blocks(frames, edge, align, sizes)
        self._apply_extent()

    def set_extent(self, extent: int) -> None:
        """Set the strip's thickness (an empty strip keeps its own thin width)."""
        if extent > 0:
            self._extent = extent
        self._apply_extent()

    def extent(self) -> int:
        """The strip's live thickness, or 0 while it is empty."""
        if self.panel.is_empty():
            return 0
        sizes = self._splitter.sizes()
        index = self._panel_index()
        return sizes[index] if index < len(sizes) else 0

    def block_sizes(self) -> list[int]:
        return self.panel.block_sizes()

    def drop_index(self, global_pos: QPoint) -> int | None:
        return self.panel.drop_index(global_pos)

    def show_drop(self, index: int) -> None:
        self.panel.show_drop(index)

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

    def _apply_extent(self) -> None:
        """Give the splitter the strip's thickness and the page the rest."""
        if self.panel.is_empty():
            return  # the panel pins its own thin width; there is nothing to divide
        total = self._splitter.width() if is_vertical_strip(self._edge) else self._splitter.height()
        if total <= 0:
            total = self._extent * 3  # not laid out yet; any ratio will do
        strip = max(1, min(self._extent, total - 1))
        sizes = [total - strip, strip]
        if self._panel_index() == 0:
            sizes.reverse()
        self._splitter.setSizes(sizes)

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

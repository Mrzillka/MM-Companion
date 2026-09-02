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

from PySide6.QtCore import QEvent, QObject, QPoint, QRect, QSize, Qt, QTimer, Signal
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
    PinSlot,
    is_vertical_strip,
)
from mm_companion.ui.widgets import discard_widget

#: Floor for the strip's thickness while nothing is pinned. The handle's own size
#: hint wins when it is bigger, so a preset with larger type can't clip its icon.
EMPTY_EXTENT = 34

#: Band along a line's edge where a drop makes a *new* line instead of joining it.
LINE_GAP = 10

#: Width of the drop-zone band along each edge of the board while the strip is
#: being dragged to a new edge.
EDGE_BAND = 56

#: Fallback for the usable screen size when no screen resolves (headless tests).

EDGE_LABELS = {"left": "Left", "right": "Right", "top": "Top", "bottom": "Bottom"}
ALIGN_LABELS = {"fill": "Fill", "start": "Start", "center": "Center", "end": "End"}


class PinHost(Protocol):
    """What the strip needs from its controller (the block canvas)."""

    def set_pin_edge(self, edge: str) -> None: ...
    def set_pin_align(self, align: str) -> None: ...
    def unpin_all(self) -> None: ...
    def pin_edge(self) -> str: ...
    def pin_align(self) -> str: ...


def _cell_alignment(vertical_strip: bool, align: str) -> Qt.AlignmentFlag | None:
    """The flag placing a block at *align* within its cell (None = fill).

    The axis is the one a *line* runs along — horizontal on a side strip, vertical
    on a top/bottom one. It only bites when the block cannot fill its cell (a
    fixed-width block in a wide strip); anything growable fills either way, which
    is what makes the splitter handles worth dragging.
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


class _PinnedSlot(QWidget):
    """One pinned block, placed within its cell.

    A wrapper rather than putting the frame in the splitter directly: a splitter
    always stretches its children to the whole cell, which is exactly what the
    ``fill`` alignment wants and exactly what the other three do not.
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
        flags = _cell_alignment(vertical_strip, align) or Qt.AlignmentFlag(0)
        # There used to be more here: a block that pinned its own size could not
        # take the cell the splitters gave it, so it was anchored to the start
        # rather than left adrift in the middle of one. No block pins its size any
        # more — the user does — so every block simply fills its cell unless an
        # alignment was asked for.
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


class _PinnedLine(QSplitter):
    """One line of the strip: blocks side by side across it, each resizable."""

    def __init__(self, vertical_strip: bool, parent: QWidget | None = None) -> None:
        # A line runs *across* the strip: horizontally on a side strip.
        super().__init__(
            Qt.Orientation.Horizontal if vertical_strip else Qt.Orientation.Vertical, parent
        )
        self.setObjectName("pinnedLine")
        self.setChildrenCollapsible(False)
        self.slots: list[_PinnedSlot] = []

    def add_slot(self, slot: _PinnedSlot) -> None:
        self.addWidget(slot)
        self.slots.append(slot)


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
        self._align = DEFAULT_ALIGN
        # What is currently rendered, so an unrelated re-render is a no-op. None
        # means "assume nothing": the next render rebuilds (see invalidate).
        self._keys: list[list[str]] | None = []
        self._lines: list[_PinnedLine] = []

        self._handle = PinnedHandle(self)
        self._handle.drag_started.connect(self.edge_drag_started)
        self._handle.drag_moved.connect(self.edge_drag_moved)
        self._handle.drag_finished.connect(self.edge_drag_finished)
        self._handle.menu_requested.connect(self.open_menu)
        self._handle.clicked.connect(lambda: self.open_menu(self._handle_menu_pos()))

        # The outer splitter runs along the strip and holds the lines.
        self._splitter = QSplitter(Qt.Orientation.Vertical, self)
        self._splitter.setObjectName("pinnedSplitter")
        self._splitter.setChildrenCollapsible(False)
        # And the strip has to hear when its content's minimum moves, because the
        # scroll area below sits between the two and a QScrollArea's own minimum
        # does not depend on its child — so Qt's invalidation stops there and
        # nothing ever asks :meth:`minimumSizeHint` again. That is not cosmetic:
        # this widget's answer is what holds the *window* open, so a block that
        # grew (the roller showing its spec chip) would find the window still at
        # the minimum computed before it grew, and the strip would answer the
        # shortfall with the scrollbar it exists to avoid. A LayoutRequest reaches
        # the splitter whenever a block below it invalidates.
        self._splitter.installEventFilter(self)

        # The last-resort scroll (see the module docstring): with the strip's
        # minimum holding the window open, the content fits and no bar ever shows.
        # Both axes stay on `AsNeeded` — the minimum is capped at the usable screen,
        # and where the room genuinely is not there a bar beats clipping a block.
        # What makes a bar rare is that the minimum is re-reported when the content
        # changes (see :meth:`eventFilter`); it was not, and the Dice block showing
        # its spec chip was enough to bring one up on a window that had plenty of
        # room to grow into.
        self._scroll = QScrollArea(self)
        self._scroll.setObjectName("pinnedScroll")
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setWidget(self._splitter)

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
        """Force the next render to rebuild even if the blocks look unchanged.

        A restored layout carries sizes as well as blocks, and those sizes are only
        read on a rebuild. Without this, restoring a layout whose strip holds the
        same blocks it already does would keep the proportions on screen and
        silently drop the ones being restored.
        """
        self._keys = None

    def set_blocks(
        self,
        lines: list[list[BlockFrame]],
        edge: str,
        align: str,
        sizes: list[int],
        line_sizes: list[list[int]],
    ) -> bool:
        """Render *lines* along the strip, skipping the rebuild when nothing moved.

        The canvas re-renders on every structural change, most of which are about
        the page rather than the strip; rebuilding regardless would throw the
        proportions the user dragged away each time they reordered a docked row.

        Answers **whether it rebuilt**, so the board only re-asserts the strip's
        thickness when the strip itself actually changed (see
        :meth:`PinnedBoard.set_blocks`).

        ``sizes``/``line_sizes`` are deliberately not compared. They move on their
        own only when a layout is *restored*, and a restore says so by calling
        :meth:`invalidate` first — see ``BlockCanvas.apply_arrangement``. Everything
        else that reshuffles the strip changes the key list in the same breath.
        """
        keys = [[frame.key for frame in line] for line in lines]
        if keys == self._keys and edge == self._edge and align == self._align:
            return False

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
        for index, frames in enumerate(lines):
            line = _PinnedLine(vertical, self._splitter)
            for frame in frames:
                line.add_slot(_PinnedSlot(frame, vertical, align, line))
            # A line whose every block pins its own size along the strip can't use
            # more room than the longest of them; saying so stops the splitter
            # handing it a band it would only leave empty, and gives the space to a
            # line that can grow instead.
            self._splitter.addWidget(line)
            # Every line can use whatever room it is given: a block's size is the
            # user's answer now, so there is no line that cannot grow.
            self._splitter.setStretchFactor(index, 1)
            self._lines.append(line)
            within = line_sizes[index] if index < len(line_sizes) else []
            if len(within) == len(frames) and all(size > 0 for size in within):
                line.setSizes(within)
        if len(sizes) == len(lines) and all(size > 0 for size in sizes):
            self._splitter.setSizes(sizes)
        self._apply_empty_state()
        # The strip only scrolls when its blocks genuinely don't fit, and its
        # contents have just changed — so start from the top rather than leaving
        # the offset the old arrangement was scrolled to, which would show the new
        # one decapitated.
        self._scroll.verticalScrollBar().setValue(0)
        self._scroll.horizontalScrollBar().setValue(0)
        return True

    def _clear(self) -> None:
        """Empty the strip, handing back every block that is still ours.

        Only the ones still parented to their slot: by the time a re-render gets
        here, a block being dragged out has *already* been moved into its floating
        window, and taking it back would empty that window out (the canvas guards
        its own row teardown the same way).
        """
        for line in self._lines:
            for slot in line.slots:
                if slot.frame.parentWidget() is slot:
                    frame = slot.release_frame()
                    frame.setParent(self)
                    frame.hide()
                discard_widget(slot)
            discard_widget(line)
        self._lines = []

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
        empty = not self._lines
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

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802 - Qt override
        """Re-report the strip's minimum whenever its content's own changes.

        See the ``installEventFilter`` in ``__init__`` for why this is needed at
        all: the scroll area between this widget and the blocks breaks Qt's own
        invalidation chain, so without this the window keeps a minimum computed
        before the block grew.
        """
        if watched is self._splitter and event.type() == QEvent.Type.LayoutRequest:
            self.updateGeometry()
        return super().eventFilter(watched, event)

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
        return not self._lines

    def edge(self) -> str:
        return self._edge

    def align(self) -> str:
        return self._align

    def line_extents(self) -> list[int]:
        """The outer splitter's sizes — how the lines share the strip's length."""
        return list(self._splitter.sizes()) if self._lines else []

    def block_sizes(self) -> list[list[int]]:
        """Each line's own splitter sizes — how its blocks share its thickness."""
        return [list(line.sizes()) for line in self._lines]

    def frames(self) -> list[list[BlockFrame]]:
        return [[slot.frame for slot in line.slots] for line in self._lines]

    # -- drop target ----------------------------------------------------------

    def drop_slot(self, global_pos: QPoint) -> PinSlot | None:
        """Where a block dropped at *global_pos* would land, or None if off the strip.

        Mirrors the page canvas's hit test: inside a line's core it joins that
        line beside its blocks, and in the band at either end of a line it makes a
        new one — so a block goes beside another as easily as under it.
        """
        if not self.isVisible():
            return None
        local = self.mapFromGlobal(global_pos)
        if not self.rect().contains(local):
            return None
        if not self._lines:
            return PinSlot(True, 0, 0)

        vertical = is_vertical_strip(self._edge)
        along = local.y() if vertical else local.x()
        geoms = [self._line_geometry(line) for line in self._lines]
        for index, geo in enumerate(geoms):
            low, high = (geo.top(), geo.bottom()) if vertical else (geo.left(), geo.right())
            if low + LINE_GAP <= along <= high - LINE_GAP:
                return PinSlot(False, index, self._slot_in_line(index, local))

        boundaries = self._line_boundaries(geoms)
        nearest = min(range(len(boundaries)), key=lambda i: abs(along - boundaries[i]))
        return PinSlot(True, nearest, 0)

    def _line_geometry(self, line: _PinnedLine) -> QRect:
        return QRect(line.mapTo(self, QPoint(0, 0)), line.size())

    def _line_boundaries(self, geoms: list[QRect]) -> list[int]:
        """The insert positions between lines, along the strip."""
        vertical = is_vertical_strip(self._edge)
        low = [geo.top() if vertical else geo.left() for geo in geoms]
        high = [geo.bottom() if vertical else geo.right() for geo in geoms]
        bounds = [low[0]]
        bounds += [(high[i - 1] + low[i]) // 2 for i in range(1, len(geoms))]
        bounds.append(high[-1])
        return bounds

    def _slot_in_line(self, index: int, local: QPoint) -> int:
        """Which place within line *index* a point falls at (by block midpoints)."""
        line = self._lines[index]
        vertical = is_vertical_strip(self._edge)
        across = local.x() if vertical else local.y()
        for slot_index, slot in enumerate(line.slots):
            center = slot.mapTo(self, slot.rect().center())
            if across < (center.x() if vertical else center.y()):
                return slot_index
        return len(line.slots)

    def show_drop(self, slot: PinSlot) -> None:
        """Light the strip up and mark where the block would land."""
        self._drops.show_accept()
        if not self._lines:
            self._indicator.hide_indicator()
            return
        self._indicator.move_to(self._indicator_rect(slot))

    def hide_drop(self) -> None:
        self._drops.clear()
        self._indicator.hide_indicator()

    def _indicator_rect(self, slot: PinSlot) -> QRect:
        """The insert line: across the strip for a new line, along it within one."""
        vertical = is_vertical_strip(self._edge)
        geoms = [self._line_geometry(line) for line in self._lines]
        if slot.new_line:
            position = self._line_boundaries(geoms)[max(0, min(slot.line, len(geoms)))]
            if vertical:
                return QRect(2, position - 1, max(0, self.width() - 4), 3)
            return QRect(position - 1, 2, 3, max(0, self.height() - 4))

        line = self._lines[max(0, min(slot.line, len(self._lines) - 1))]
        geo = geoms[self._lines.index(line)]
        position = self._slot_boundary(line, slot.slot, geo)
        if vertical:
            return QRect(position - 1, geo.top(), 3, geo.height())
        return QRect(geo.left(), position - 1, geo.width(), 3)

    def _slot_boundary(self, line: _PinnedLine, slot: int, geo: QRect) -> int:
        """Where the insert line sits within *line*, in panel coordinates."""
        vertical = is_vertical_strip(self._edge)
        if not line.slots or slot <= 0:
            return geo.left() if vertical else geo.top()
        if slot >= len(line.slots):
            return geo.right() if vertical else geo.bottom()
        corner = line.slots[slot].mapTo(self, QPoint(0, 0))
        return corner.x() if vertical else corner.y()

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
        unpin.setEnabled(bool(self._lines))
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
        lines: list[list[BlockFrame]],
        edge: str,
        align: str,
        sizes: list[int],
        line_sizes: list[list[int]],
    ) -> None:
        """Render the strip, re-laying the board out first when the edge changed.

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
        if self.panel.set_blocks(lines, edge, align, sizes, line_sizes):
            self._fresh_settle()
            self._apply_extent()

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

    def line_extents(self) -> list[int]:
        return self.panel.line_extents()

    def block_sizes(self) -> list[list[int]]:
        return self.panel.block_sizes()

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

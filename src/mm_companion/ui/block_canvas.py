"""The scrollable, free-form canvas that arranges the character-sheet blocks.

`BlockCanvas` is the single source of truth for how the seven blocks are laid out.
It models the arrangement as an ordered list of *rows*, each an ordered list of
block keys, plus a set of *floating* blocks (torn out into their own windows) and
a set of *hidden* blocks (closed, reopenable from the View menu — each remembers
an :class:`Anchor` so it comes back where it was). It renders the
rows top-to-bottom (a `RowWidget` per row) inside the sheet's page scroll area,
so the whole sheet scrolls as one page while each block shows its full content.

Rearrangement is a single manual-drag gesture (no Qt docking): pressing and
dragging a block's title bar tears it out into a `BlockWindow` that follows the
cursor; a drop indicator shows where it will land (a new row, or beside a block
in an existing row); releasing over the canvas re-docks it there, releasing
outside leaves it floating. Dragging a floating window's title bar back onto the
canvas re-docks it the same way. The canvas owns that drag controller, the drop
indicator, and edge auto-scroll.
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import (
    QAbstractAnimation,
    QEasingCurve,
    QPoint,
    QPropertyAnimation,
    QRect,
    QTimer,
    Signal,
)
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import (
    QAbstractScrollArea,
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from mm_companion.ui.block_frame import BlockFrame, BlockWindow
from mm_companion.ui.block_sizes import UNBOUNDED, BlockSize

# Bumped whenever the persisted arrangement schema changes, so a layout saved by
# an older version is rejected and the default applies.
SCHEMA_VERSION = 5


@dataclass(frozen=True)
class DropSlot:
    """Where a dragged block would land: a new row, or a slot inside a row."""

    new_row: bool
    row: int
    slot: int


@dataclass(frozen=True)
class Anchor:
    """Where a closed block should reappear, recorded as *what it sat next to*.

    A raw ``(row, slot)`` index goes stale as soon as anything else moves — hiding
    a lone block collapses its row, shifting every later row up. Naming the
    neighbour instead survives that, and degrades cleanly: if the neighbour is no
    longer docked, the anchor simply doesn't resolve and the caller falls back.
    """

    neighbour: str | None  # the block it sat beside, or the row above it
    in_row: bool  # True: same row as the neighbour; False: its own row
    before: bool  # insert before the neighbour rather than after

    def to_dict(self) -> dict:
        return {"neighbour": self.neighbour, "in_row": self.in_row, "before": self.before}

    @classmethod
    def from_dict(cls, value: object) -> Anchor | None:
        """Parse a persisted anchor, or None when it is missing/malformed."""
        if not isinstance(value, dict):
            return None
        neighbour = value.get("neighbour")
        if neighbour is not None and not isinstance(neighbour, str):
            return None
        return cls(neighbour, bool(value.get("in_row")), bool(value.get("before")))


class DropIndicator(QFrame):
    """A thin accent line showing where a dragged block will drop.

    It slides between drop slots instead of teleporting: :meth:`move_to` animates
    the geometry over a short hop when the target stays the same orientation, and
    snaps instantly on first appearance or when it flips between a row-boundary bar
    (horizontal) and an in-row bar (vertical), where a slide would just morph oddly.
    """

    SLIDE_MS = 120

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("dropIndicator")
        self.setStyleSheet("#dropIndicator { background-color: palette(highlight); }")
        self._slide = QPropertyAnimation(self, b"geometry", self)
        self._slide.setDuration(self.SLIDE_MS)
        self._slide.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.hide()

    def move_to(self, rect: QRect) -> None:
        """Show the indicator at *rect*, sliding there when it makes sense."""
        self._slide.stop()
        current = self.geometry()
        # Orientation = which dimension is the thin one; only slide within the same.
        same_axis = (current.width() < current.height()) == (rect.width() < rect.height())
        if self.isVisible() and same_axis and current != rect:
            self._slide.setStartValue(current)
            self._slide.setEndValue(rect)
            self._slide.start()
        else:
            self.setGeometry(rect)
        self.show()
        self.raise_()

    def hide_indicator(self) -> None:
        self._slide.stop()
        self.hide()


class RowWidget(QWidget):
    """One horizontal row of blocks.

    Fixed-width blocks (abilities/resistances) keep their width; growable blocks
    stretch to share the row. A row with only fixed blocks gets a trailing
    stretch so its blocks left-align and the leftover width stays empty.
    """

    SPACING = 6  # px between side-by-side blocks in a row

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(self.SPACING)
        self._frames: list[BlockFrame] = []
        self._has_growable = False
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)

    def add_frame(self, frame: BlockFrame, growable: bool) -> None:
        self._layout.addWidget(frame, stretch=1 if growable else 0)
        self._frames.append(frame)
        self._has_growable = self._has_growable or growable

    def finalize(self) -> None:
        """Add a trailing stretch when nothing in the row absorbs slack width."""
        if not self._has_growable:
            self._layout.addStretch(1)

    def frames(self) -> list[BlockFrame]:
        return list(self._frames)


class BlockCanvas(QWidget):
    """Free-form, scrollable arrangement of the sheet's blocks (see module doc)."""

    arrangement_changed = Signal()
    block_visibility_changed = Signal(str, bool)

    def __init__(
        self,
        panels: list[tuple[str, str, QWidget]],
        block_sizes: dict[str, BlockSize],
        default_rows: list[list[str]],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("blockCanvas")

        self._sizes = block_sizes
        self._default_rows = default_rows
        # One frame per block, created once and reparented as it moves.
        self._frames: dict[str, BlockFrame] = {}
        for key, title, section in panels:
            size = block_sizes.get(key, BlockSize())
            frame = BlockFrame(key, title, section, size, self, parent=self)
            frame.hide()  # shown once _relayout places it in a row
            self._frames[key] = frame

        self._windows: dict[str, BlockWindow] = {}
        self._rows: list[list[str]] = []
        self._hidden: set[str] = set()
        # Where each hidden block was closed from, so reopening restores it there.
        self._anchors: dict[str, Anchor] = {}
        self._row_widgets: list[RowWidget] = []

        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(6, 6, 6, 6)
        self._layout.setSpacing(8)

        self._indicator = DropIndicator(self)

        # Drag state.
        self._scroll_area: QAbstractScrollArea | None = None
        self._drag_key: str | None = None
        self._drag_active = False
        self._press_global = QPoint()
        self._grab_offset = QPoint()
        self._autoscroll_velocity = 0
        self._autoscroll_timer = QTimer(self)
        self._autoscroll_timer.setInterval(16)
        self._autoscroll_timer.timeout.connect(self._autoscroll_tick)

        self.apply_arrangement(self.default_arrangement())

    # -- wiring from the sheet ----------------------------------------------

    def set_scroll_area(self, scroll: QAbstractScrollArea) -> None:
        """Give the canvas its enclosing page scroll area (for hit-test bounds
        and edge auto-scroll during a drag)."""
        self._scroll_area = scroll

    def block_keys(self) -> list[str]:
        return list(self._frames)

    def block_frame(self, key: str) -> BlockFrame:
        return self._frames[key]

    def content_minimum_width(self) -> int:
        """The widest docked row's minimum width, including the canvas margins.

        The page must never shrink narrow enough to clip a row's blocks (the
        fixed-width Abilities/Resistances grids can't compress), so the sheet
        uses this to pin its own minimum width. Only docked rows constrain it —
        a floated or hidden block has its own window and doesn't hold the page
        open. Each block contributes its :meth:`BlockFrame.minimumSizeHint`
        width (a column-flow block reports a single column), summed with the
        inter-block spacing :class:`RowWidget` uses.
        """
        best = 0
        for row in self._rows:
            keys = [k for k in row if k in self._frames and k not in self._windows]
            if not keys:
                continue
            total = sum(self._frames[k].minimumSizeHint().width() for k in keys)
            total += RowWidget.SPACING * (len(keys) - 1)
            best = max(best, total)
        margins = self._layout.contentsMargins()
        return best + margins.left() + margins.right()

    # -- rendering -----------------------------------------------------------

    def _is_growable(self, key: str) -> bool:
        """A block grows to fill its row unless its width is pinned (abilities/
        resistances have ``min_width == max_width``)."""
        size = self._sizes.get(key)
        if size is None:
            return True
        return not (size.max_width < UNBOUNDED and size.max_width == size.min_width)

    def _relayout(self) -> None:
        """Rebuild the row widgets from ``_rows`` (empty rows collapse away)."""
        old = self._row_widgets
        self._row_widgets = []
        # Detach every current layout item; frames are moved into the new rows
        # below (addWidget reparents them), so the old rows end up empty.
        while self._layout.count():
            self._layout.takeAt(0)

        for row_keys in self._rows:
            keys = [k for k in row_keys if k in self._frames]
            if not keys:
                continue
            row = RowWidget(self)
            for key in keys:
                frame = self._frames[key]
                row.add_frame(frame, self._is_growable(key))
                frame.show()
            row.finalize()
            self._layout.addWidget(row)
            self._row_widgets.append(row)

        self._layout.addStretch(1)

        # Free the old rows. Any frame not moved into a new row above (a block now
        # hidden or floating) is still parented to its old row; rescue it to the
        # canvas first so deleting the row doesn't destroy its C++ object.
        for row in old:
            for frame in row.frames():
                if frame.parentWidget() is row:
                    frame.setParent(self)
                    frame.hide()
            row.setParent(None)
            row.deleteLater()

        self._indicator.raise_()

    # -- arrangement model ---------------------------------------------------

    def default_arrangement(self) -> dict:
        """The default layout as a persistence model (see the block registry's ``default_rows``)."""
        present = set(self._frames)
        rows: list[list[str]] = []
        used: set[str] = set()
        for row in self._default_rows:
            keys = [k for k in row if k in present]
            used.update(keys)
            if keys:
                rows.append(keys)
        rows.extend([k] for k in self._frames if k not in used)
        return {"version": SCHEMA_VERSION, "rows": rows, "floating": {}, "hidden": []}

    def arrangement(self) -> dict:
        """A snapshot of the current arrangement as a persistence model.

        ``hidden_anchors`` is an *optional* addition: it is read tolerantly on the
        way back in, so it needed no schema bump and a layout saved without it
        still restores (its blocks just reopen at their default position).
        """
        return {
            "version": SCHEMA_VERSION,
            "rows": [list(row) for row in self._rows],
            "floating": {key: self._window_geometry(key) for key in self._windows},
            "hidden": sorted(self._hidden),
            "hidden_anchors": {
                key: anchor.to_dict()
                for key, anchor in self._anchors.items()
                if key in self._hidden
            },
        }

    def _window_geometry(self, key: str) -> dict:
        geo = self._windows[key].geometry()
        return {"x": geo.x(), "y": geo.y(), "w": geo.width(), "h": geo.height()}

    def apply_arrangement(self, model: dict) -> bool:
        """Replace the arrangement with *model*; returns False (leaving the current
        arrangement) if it is invalid."""
        parsed = self._validate(model)
        if parsed is None:
            return False
        rows, floating, hidden, anchors = parsed

        for key in list(self._windows):
            self._destroy_window(key)
        self._rows = rows
        self._hidden = set(hidden)
        self._anchors = anchors
        self._relayout()
        for key, geom in floating.items():
            self._make_floating(key, geom)

        self.arrangement_changed.emit()
        return True

    def _validate(self, model: object):
        """Parse/validate a persistence model → (rows, floating, hidden, anchors) or None.

        Enforces the invariant that every known block appears exactly once across
        rows, floating, and hidden. The optional ``hidden_anchors`` is parsed
        leniently — anything unusable is dropped rather than rejecting the whole
        layout, since a missing anchor only costs a reopened block its remembered
        spot.
        """
        if not isinstance(model, dict) or model.get("version") != SCHEMA_VERSION:
            return None
        rows = model.get("rows")
        floating = model.get("floating")
        hidden = model.get("hidden")
        if not (isinstance(rows, list) and isinstance(floating, dict) and isinstance(hidden, list)):
            return None

        known = set(self._frames)
        seen: list[str] = []

        clean_rows: list[list[str]] = []
        for row in rows:
            if not isinstance(row, list):
                return None
            keys = []
            for key in row:
                if key not in known:
                    return None
                keys.append(key)
                seen.append(key)
            if keys:
                clean_rows.append(keys)

        for key, geom in floating.items():
            if key not in known or not self._valid_geometry(geom):
                return None
            seen.append(key)

        for key in hidden:
            if key not in known:
                return None
            seen.append(key)

        if sorted(seen) != sorted(known):
            return None

        anchors: dict[str, Anchor] = {}
        raw_anchors = model.get("hidden_anchors")
        if isinstance(raw_anchors, dict):
            for key, value in raw_anchors.items():
                anchor = Anchor.from_dict(value)
                if key in known and anchor is not None:
                    anchors[key] = anchor

        return clean_rows, floating, list(hidden), anchors

    @staticmethod
    def _valid_geometry(geom: object) -> bool:
        return isinstance(geom, dict) and all(
            isinstance(geom.get(axis), int) and not isinstance(geom.get(axis), bool)
            for axis in ("x", "y", "w", "h")
        )

    # -- structural operations (the drag gesture and tests drive these) ------

    def _detach(self, key: str) -> None:
        """Remove *key* from wherever it currently lives (a row or a window),
        rescuing its frame so it survives. Does not re-place it.

        The frame is reparented to the canvas (hidden) so it is never a child of a
        row widget that :meth:`_relayout` is about to delete — otherwise Qt would
        destroy the frame's C++ object when the old row is freed.
        """
        if key in self._windows:
            self._destroy_window(key)
            return
        for row in self._rows:
            if key in row:
                row.remove(key)
                break
        self._rows = [row for row in self._rows if row]
        frame = self._frames[key]
        frame.setParent(self)
        frame.hide()

    def _destroy_window(self, key: str) -> None:
        window = self._windows.pop(key, None)
        if window is None:
            return
        frame = self._frames[key]
        frame.setParent(self)  # rescue the frame before the window is destroyed
        frame.hide()
        window.hide()
        window.deleteLater()

    def float_block(self, key: str, pos: QPoint | None = None) -> None:
        """Tear *key* out into its own :class:`BlockWindow`."""
        if key in self._windows:
            return
        frame = self._frames[key]
        old_global = frame.mapToGlobal(QPoint(0, 0))
        old_size = frame.size()
        self._detach(key)
        self._hidden.discard(key)

        window = BlockWindow(key, self, self.window())
        window.set_frame(frame)
        frame.show()
        self._apply_window_min_width(window, frame)
        width = max(old_size.width(), frame.sizeHint().width(), frame.minimumWidth())
        height = max(old_size.height(), frame.sizeHint().height(), frame.minimumHeight())
        # A block taller than the screen (e.g. a full Powers list) would open past
        # the bottom of the display with no way to see the rest; cap the window to
        # the available height so its scroll area takes over instead.
        height = min(height, self._available_height(window))
        if pos is None:
            pos = QPoint(old_global.x() + 24, old_global.y() + 24)
        window.setGeometry(pos.x(), pos.y(), width, height)
        self._windows[key] = window
        window.show()

        self._relayout()
        self.arrangement_changed.emit()

    @staticmethod
    def _apply_window_min_width(window: BlockWindow, frame: BlockFrame) -> None:
        """Stop a floated block's window from shrinking narrow enough to clip it.

        The window's scroll area never scrolls horizontally, so without a minimum a
        narrow window would cut off the frame's right edge. Pin the window to the
        frame's own minimum plus room for the vertical scrollbar the tall content
        may show.
        """
        extent = window.verticalScrollBar_extent()
        window.setMinimumWidth(frame.minimumSizeHint().width() + extent + 4)

    @staticmethod
    def _available_height(window: BlockWindow) -> int:
        """The usable screen height for a floated window (falls back generously
        when no screen is resolvable, e.g. headless tests)."""
        screen = window.screen()
        if screen is None:
            return UNBOUNDED
        return screen.availableGeometry().height()

    def _make_floating(self, key: str, geom: dict) -> None:
        """Restore *key* as a floating window at *geom* (used by apply_arrangement)."""
        frame = self._frames[key]
        window = BlockWindow(key, self, self.window())
        window.set_frame(frame)
        frame.show()
        self._apply_window_min_width(window, frame)
        window.setGeometry(geom["x"], geom["y"], geom["w"], geom["h"])
        self._windows[key] = window
        window.show()

    def _place(self, key: str, slot: DropSlot) -> None:
        """Insert *key* into ``_rows`` at *slot* (indices clamped). Assumes *key*
        has already been detached."""
        row = max(0, min(slot.row, len(self._rows)))
        if slot.new_row or not self._rows:
            self._rows.insert(row, [key])
            return
        row = min(row, len(self._rows) - 1)
        target = self._rows[row]
        index = max(0, min(slot.slot, len(target)))
        target.insert(index, key)

    def dock_block(self, key: str, row: int, slot: int, new_row: bool = False) -> None:
        """Dock *key* into the arrangement at (row, slot), creating a new row when
        *new_row* is set. Detaches it from its current place first."""
        self._hidden.discard(key)
        self._detach(key)
        self._place(key, DropSlot(new_row, row, slot))
        self._relayout()
        self.arrangement_changed.emit()

    # -- remembering where a closed block came from --------------------------

    @staticmethod
    def _anchor_for(key: str, rows: list[list[str]]) -> Anchor | None:
        """Derive an anchor for *key* from a rows model, or None if it isn't in one.

        Used both on ``_rows`` when a block is closed and on ``_default_rows`` as
        the fallback when the remembered anchor no longer resolves.
        """
        for r, row in enumerate(rows):
            if key not in row:
                continue
            slot = row.index(key)
            if len(row) > 1:  # it had a row-mate: come back beside it
                if slot == 0:
                    return Anchor(row[1], in_row=True, before=True)
                return Anchor(row[slot - 1], in_row=True, before=False)
            if r > 0:  # alone in its row: come back as a row below the one above
                return Anchor(rows[r - 1][0], in_row=False, before=False)
            if len(rows) > 1:
                return Anchor(rows[1][0], in_row=False, before=True)
            return Anchor(None, in_row=False, before=True)
        return None

    def _resolve_anchor(self, anchor: Anchor | None) -> DropSlot | None:
        """Turn an anchor into a drop slot against the *current* rows, or None when
        its neighbour is no longer docked (floated, hidden, or gone)."""
        if anchor is None:
            return None
        if anchor.neighbour is None:
            return DropSlot(True, 0, 0)
        for r, row in enumerate(self._rows):
            if anchor.neighbour not in row:
                continue
            slot = row.index(anchor.neighbour)
            if anchor.in_row:
                return DropSlot(False, r, slot if anchor.before else slot + 1)
            return DropSlot(True, r if anchor.before else r + 1, 0)
        return None

    def hide_block(self, key: str) -> None:
        """Close *key* (removed from the sheet, reopenable from the View menu)."""
        if key in self._hidden:
            return
        anchor = self._anchor_for(key, self._rows)  # before _detach mutates _rows
        self._detach(key)
        self._hidden.add(key)
        if anchor is not None:
            self._anchors[key] = anchor
        self._relayout()
        self.block_visibility_changed.emit(key, False)
        self.arrangement_changed.emit()

    def show_block(self, key: str) -> None:
        """Reopen a hidden block where it was closed from.

        Falls back to its default position when the remembered anchor no longer
        resolves (its neighbour has since been floated or hidden), and to a new
        row at the end when that fails too.
        """
        if key not in self._hidden:
            return
        self._hidden.discard(key)
        slot = self._resolve_anchor(self._anchors.pop(key, None))
        if slot is None:
            slot = self._resolve_anchor(self._anchor_for(key, self._default_rows))
        if slot is None:
            slot = DropSlot(True, len(self._rows), 0)
        self._place(key, slot)
        self._relayout()
        self._fade_in(self._frames[key])
        self.block_visibility_changed.emit(key, True)
        self.arrangement_changed.emit()

    @staticmethod
    def _fade_in(frame: BlockFrame) -> None:
        """Gently fade a just-reopened block in so it doesn't pop.

        A one-shot opacity effect that is dropped on completion — leaving the
        effect attached would force the block's heavyweight child widgets (tables,
        spin boxes) to keep painting through an offscreen buffer. Hiding stays
        instant on purpose: fading a block out while the row collapses under it
        reads worse than a clean removal.
        """
        effect = QGraphicsOpacityEffect(frame)
        frame.setGraphicsEffect(effect)
        anim = QPropertyAnimation(effect, b"opacity", frame)
        anim.setDuration(160)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        anim.finished.connect(lambda: frame.setGraphicsEffect(None))
        anim.start(QAbstractAnimation.DeletionPolicy.DeleteWhenStopped)

    def is_hidden(self, key: str) -> bool:
        return key in self._hidden

    def reset(self) -> None:
        self.apply_arrangement(self.default_arrangement())

    # -- drag controller (called by the block title bars) --------------------

    def title_bar_pressed(self, key: str, global_pos: QPoint) -> None:
        self._drag_key = key
        self._drag_active = False
        self._press_global = global_pos

    def title_bar_moved(self, key: str, global_pos: QPoint) -> None:
        if self._drag_key != key:
            return
        if not self._drag_active:
            if (global_pos - self._press_global).manhattanLength() < self._start_distance():
                return
            frame = self._frames[key]
            top_left = frame.mapToGlobal(QPoint(0, 0))
            self._grab_offset = global_pos - top_left
            self.float_block(key, pos=top_left)
            self._drag_active = True

        window = self._windows.get(key)
        if window is not None:
            window.move(global_pos - self._grab_offset)
        self.update_drag(global_pos)

    def title_bar_released(self, key: str, global_pos: QPoint) -> None:
        active = self._drag_active and self._drag_key == key
        self._end_drag()
        if not active:
            return
        slot = self._hit_test(global_pos)
        if slot is not None:
            self.dock_block(key, slot.row, slot.slot, new_row=slot.new_row)

    def request_float(self, key: str) -> None:
        self.float_block(key)

    def request_hide(self, key: str) -> None:
        self.hide_block(key)

    @staticmethod
    def _start_distance() -> int:
        from PySide6.QtWidgets import QApplication

        return QApplication.startDragDistance()

    def _end_drag(self) -> None:
        self._drag_key = None
        self._drag_active = False
        self._autoscroll_velocity = 0
        self._autoscroll_timer.stop()
        self._indicator.hide_indicator()

    def update_drag(self, global_pos: QPoint) -> None:
        """Refresh the drop indicator and edge auto-scroll for a cursor position."""
        self._show_indicator(self._hit_test(global_pos))
        self._maybe_autoscroll(global_pos)

    # -- hit testing / indicator geometry ------------------------------------

    _GAP = 12  # px band around a row where a drop makes a new row instead

    def _hit_test(self, global_pos: QPoint) -> DropSlot | None:
        """Which slot a drop at *global_pos* targets, or None if off the page."""
        if self._scroll_area is not None:
            viewport = self._scroll_area.viewport()
            if not viewport.rect().contains(viewport.mapFromGlobal(global_pos)):
                return None

        p = self.mapFromGlobal(global_pos)
        rows = self._row_widgets
        if not rows:
            return DropSlot(True, 0, 0)

        geoms = [row.geometry() for row in rows]
        for i, geo in enumerate(geoms):
            if geo.top() + self._GAP <= p.y() <= geo.bottom() - self._GAP:
                return DropSlot(False, i, self._row_slot(rows[i], p.x()))

        # Not inside any row's core → a new row at the nearest boundary.
        boundaries = [geoms[0].top()]
        boundaries += [(geoms[i - 1].bottom() + geoms[i].top()) / 2 for i in range(1, len(geoms))]
        boundaries.append(geoms[-1].bottom())
        nearest = min(range(len(boundaries)), key=lambda b: abs(p.y() - boundaries[b]))
        return DropSlot(True, nearest, 0)

    def _row_slot(self, row: RowWidget, x: int) -> int:
        """The insert column within *row* for canvas x-coordinate *x*."""
        for i, frame in enumerate(row.frames()):
            mid = row.mapToParent(frame.geometry().center()).x()
            if x < mid:
                return i
        return len(row.frames())

    def _show_indicator(self, slot: DropSlot | None) -> None:
        if slot is None:
            self._indicator.hide_indicator()
            return
        if slot.new_row:
            y = self._row_boundary_y(slot.row)
            rect = QRect(4, int(y) - 1, self.width() - 8, 3)
        else:
            row = self._row_widgets[slot.row]
            x = self._row_slot_x(row, slot.slot)
            geo = row.geometry()
            rect = QRect(int(x) - 1, geo.top(), 3, geo.height())
        self._indicator.move_to(rect)

    def _row_boundary_y(self, index: int) -> float:
        geoms = [row.geometry() for row in self._row_widgets]
        if not geoms:
            return 4
        if index <= 0:
            return geoms[0].top()
        if index >= len(geoms):
            return geoms[-1].bottom()
        return (geoms[index - 1].bottom() + geoms[index].top()) / 2

    def _row_slot_x(self, row: RowWidget, slot: int) -> int:
        frames = row.frames()
        geo = row.geometry()
        if not frames or slot <= 0:
            return geo.left()
        if slot >= len(frames):
            return row.mapToParent(frames[-1].geometry().topRight()).x()
        return row.mapToParent(frames[slot].geometry().topLeft()).x()

    # -- auto-scroll ---------------------------------------------------------

    _HOT = 40  # px band at the viewport edges that triggers auto-scroll

    def _maybe_autoscroll(self, global_pos: QPoint) -> None:
        if self._scroll_area is None:
            return
        viewport = self._scroll_area.viewport()
        y = viewport.mapFromGlobal(global_pos).y()
        velocity = 0
        if y < self._HOT:
            velocity = -max(4, (self._HOT - y) // 3)
        elif y > viewport.height() - self._HOT:
            velocity = max(4, (y - (viewport.height() - self._HOT)) // 3)

        self._autoscroll_velocity = velocity
        if velocity and not self._autoscroll_timer.isActive():
            self._autoscroll_timer.start()
        elif not velocity and self._autoscroll_timer.isActive():
            self._autoscroll_timer.stop()

    def _autoscroll_tick(self) -> None:
        if not self._autoscroll_velocity or self._scroll_area is None:
            self._autoscroll_timer.stop()
            return
        bar = self._scroll_area.verticalScrollBar()
        bar.setValue(bar.value() + self._autoscroll_velocity)
        self.update_drag(QCursor.pos())

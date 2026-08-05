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
from typing import TYPE_CHECKING

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
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from mm_companion.ui.block_frame import BlockFrame, BlockWindow
from mm_companion.ui.block_sizes import UNBOUNDED, BlockSize
from mm_companion.ui.drop_feedback import DropIndicator
from mm_companion.ui.pinned import (
    DEFAULT_ALIGN,
    DEFAULT_EDGE,
    DEFAULT_EXTENT,
    PIN_ALIGNMENTS,
    PIN_EDGES,
    PinSlot,
)

if TYPE_CHECKING:  # the board is the canvas's *view* for pinned blocks, not a dependency
    from mm_companion.ui.pinned_panel import PinnedBoard

# Bumped whenever the persisted arrangement schema changes, so a layout saved by
# an older version is rejected and the default applies.
SCHEMA_VERSION = 6

#: Whether a block popped out of the app stays above other applications unless
#: told otherwise. It does: a floated block's whole purpose is to be read beside
#: somebody else's window, and one that sinks behind that window the moment it is
#: clicked is a block nobody can use.
DEFAULT_ON_TOP = True


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


def default_pin_model() -> dict:
    """An empty pinned strip: the default every fresh layout starts from."""
    return {
        "edge": DEFAULT_EDGE,
        "lines": [],
        "align": DEFAULT_ALIGN,
        "sizes": [],
        "line_sizes": [],
        "extent": DEFAULT_EXTENT,
    }


def _int_list(value: object) -> list[int]:
    """A list of positive ints, or [] for anything else (a cosmetic field)."""
    if not isinstance(value, list):
        return []
    if not all(isinstance(item, int) and not isinstance(item, bool) for item in value):
        return []
    return [int(item) for item in value]


@dataclass(frozen=True)
class _PinModel:
    """The parsed ``pinned`` section of a persisted arrangement."""

    edge: str
    lines: list[list[str]]
    align: str
    sizes: list[int]
    line_sizes: list[list[int]]
    extent: int

    @property
    def keys(self) -> list[str]:
        return [key for line in self.lines for key in line]

    @classmethod
    def parse(cls, value: object, known: set[str]) -> _PinModel | None:
        """Parse the ``pinned`` sub-model, or None when it is unusable.

        Strict about what changes *where a block lives* — an unknown key, edge or
        alignment rejects the whole layout, the same as a malformed row would,
        since guessing would silently move a block. Lenient about the cosmetic
        numbers: bad sizes or extent fall back to the defaults, which only costs
        the strip its remembered proportions.
        """
        if value is None:  # a layout written before the strip existed
            value = {}
        if not isinstance(value, dict):
            return None

        edge = value.get("edge", DEFAULT_EDGE)
        align = value.get("align", DEFAULT_ALIGN)
        if edge not in PIN_EDGES or align not in PIN_ALIGNMENTS:
            return None

        raw_lines = value.get("lines", [])
        if not isinstance(raw_lines, list):
            return None
        lines: list[list[str]] = []
        for raw_line in raw_lines:
            if not isinstance(raw_line, list) or any(key not in known for key in raw_line):
                return None
            if raw_line:  # an empty line is nothing to render, not a reason to reject
                lines.append(list(raw_line))

        raw_line_sizes = value.get("line_sizes", [])
        line_sizes = (
            [_int_list(entry) for entry in raw_line_sizes]
            if isinstance(raw_line_sizes, list)
            else []
        )
        raw_extent = value.get("extent", DEFAULT_EXTENT)
        extent = (
            int(raw_extent)
            if isinstance(raw_extent, int) and not isinstance(raw_extent, bool) and raw_extent > 0
            else DEFAULT_EXTENT
        )
        return cls(edge, lines, align, _int_list(value.get("sizes", [])), line_sizes, extent)


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
        *,
        fill_last: bool = False,
        default_pinned: list[list[str]] | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("blockCanvas")

        self._sizes = block_sizes
        self._default_rows = default_rows
        # Blocks the default arrangement parks in the strip rather than on the page
        # (the sheet's Dice block). A host that offers no strip passes none.
        self._default_pinned = default_pinned or []
        # When set, the bottom row's growable blocks stretch to fill leftover
        # height instead of a trailing spacer holding empty space beneath them.
        # Used by boards with only a few blocks (GM Mode) where a top-aligned
        # stack would leave a large dead gap at the bottom of the page.
        self._fill_last = fill_last
        # One frame per block, created once and reparented as it moves.
        self._frames: dict[str, BlockFrame] = {}
        for key, title, section in panels:
            size = block_sizes.get(key, BlockSize())
            frame = BlockFrame(key, title, section, size, self, parent=self)
            frame.hide()  # shown once _relayout places it in a row
            self._frames[key] = frame

        self._windows: dict[str, BlockWindow] = {}
        # Which floated blocks are pinned above other applications. Kept here, by
        # block key, rather than on the window: dragging a block out and docking it
        # back destroys and rebuilds the window, so anything held on the window
        # itself is lost the first time the user moves it.
        #
        # A *mapping* and not a set, because absence has to mean "never asked"
        # rather than "no": a block is popped out precisely to sit beside somebody
        # else's window, so on top is the useful default (DEFAULT_ON_TOP) and only
        # an explicit choice can say otherwise. See :meth:`_wants_on_top`.
        self._on_top: dict[str, bool] = {}
        # Whether the floated windows are currently stood down (compact mode).
        self._windows_suspended = False
        self._rows: list[list[str]] = []
        self._hidden: set[str] = set()
        # Where each hidden block was closed from, so reopening restores it there.
        self._anchors: dict[str, Anchor] = {}
        self._row_widgets: list[RowWidget] = []

        # The pinned strip: blocks parked on one edge of the window, outside this
        # scrolling page (see mm_companion.ui.pinned_panel). Modelled the same way
        # the page is — lines of block keys, each line holding one or more blocks
        # side by side — with the sizes the user dragged alongside. The canvas
        # stays the single source of truth; the board is only the view it renders
        # them through, and stays None for a host that doesn't offer the strip.
        self._pinned: list[list[str]] = []
        self._pin_edge = DEFAULT_EDGE
        self._pin_align = DEFAULT_ALIGN
        # The proportions the user dragged, as live pixel sizes. They only mean
        # anything against the shape they were measured in, so a block arriving
        # somewhere clears the sizes on that axis rather than trying to slot a
        # value in beside them (see pin_block).
        self._pin_sizes: list[int] = []  # how the lines share the strip's length
        self._pin_line_sizes: list[list[int]] = []  # within each line
        self._pin_extent = DEFAULT_EXTENT
        self._board: PinnedBoard | None = None

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

    def set_pinned_board(self, board: PinnedBoard) -> None:
        """Give the canvas the board hosting the pinned strip, and render into it.

        Optional: a host that never calls this simply has no strip, and the pinned
        list stays empty (nothing can be dropped into a board that doesn't exist).
        """
        self._board = board
        self._render_pinned()

    def block_keys(self) -> list[str]:
        return list(self._frames)

    def block_frame(self, key: str) -> BlockFrame:
        return self._frames[key]

    def block_window(self, key: str):
        """The :class:`BlockWindow` block *key* is floated in, or ``None`` if docked."""
        return self._windows.get(key)

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
        """Rebuild the strip and the row widgets from the model (empty rows collapse)."""
        self._render_pinned()  # first: see _render_pinned for why the order matters
        old = self._row_widgets
        self._row_widgets = []
        # Detach every current layout item; frames are moved into the new rows
        # below (addWidget reparents them), so the old rows end up empty.
        while self._layout.count():
            self._layout.takeAt(0)

        # Every block starts flush to its content; the fill block (if any) is
        # promoted to Expanding below. Reset first so a block that used to be the
        # bottom one — before a reorder or a float — doesn't stay stretchy.
        for frame in self._frames.values():
            frame.set_vertical_fill(False)

        built: list[list[str]] = []
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
            built.append(keys)

        if self._fill_last and self._row_widgets:
            # The bottom row soaks up the slack: its row widget grows, and its
            # growable blocks grow inside it, so nothing is left empty below.
            self._row_widgets[-1].setSizePolicy(
                QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
            )
            for key in built[-1]:
                if self._is_growable(key):
                    self._frames[key].set_vertical_fill(True)
        else:
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

    def _render_pinned(self) -> None:
        """Push the pinned blocks into the board (a no-op without one).

        Called at the *top* of :meth:`_relayout`, before the rows are rebuilt: the
        strip lets go of any block that is no longer pinned, so the row build that
        follows is the last thing to claim a frame. The other order loses — the row
        would place the block and the strip would then take it straight back, which
        is exactly what left Reset Layout needing two goes.

        The board itself skips the rebuild when nothing about the strip actually
        changed, so a row reorder doesn't reflow the pinned blocks or discard the
        sizes the user dragged.
        """
        if self._board is None:
            return
        lines = [[self._frames[key] for key in line] for line in self._pinned]
        self._board.set_blocks(
            lines, self._pin_edge, self._pin_align, self._pin_sizes, self._pin_line_sizes
        )

    def _sync_from_board(self) -> None:
        """Read the strip's live proportions back out of the board.

        The splitter handles are dragged by the user, not by us, so the sizes and
        the strip's thickness are only true on the widgets; pull them in before
        anything snapshots or rebuilds the arrangement. Anything whose shape no
        longer matches the model is dropped rather than kept — a stale size list
        would be applied to the wrong blocks.
        """
        if self._board is None:
            return
        extents = self._board.line_extents()
        self._pin_sizes = extents if len(extents) == len(self._pinned) else []
        sizes = self._board.block_sizes()
        matches = len(sizes) == len(self._pinned) and all(
            len(within) == len(line) for within, line in zip(sizes, self._pinned, strict=False)
        )
        self._pin_line_sizes = sizes if matches else []
        # The thickness the strip was *asked* for, not the one its blocks forced on
        # it — see PinnedBoard.desired_extent.
        extent = self._board.desired_extent()
        if extent > 0:
            self._pin_extent = extent

    # -- arrangement model ---------------------------------------------------

    def default_arrangement(self) -> dict:
        """The default layout as a persistence model.

        The page's rows come from the block registry's ``default_rows`` and the
        strip's lines from its ``default_pin_lines`` (see
        :mod:`mm_companion.ui.blocks.registry`). A block named in neither trails as
        a row of its own, so a mod block that declares no position still appears —
        but a *pinned* block must be excluded from that sweep as well, or it would
        be placed twice and the exactly-once check in :meth:`_validate` would reject
        the whole arrangement.
        """
        present = set(self._frames)
        pinned = [[k for k in line if k in present] for line in self._default_pinned]
        pinned = [line for line in pinned if line]
        used: set[str] = {key for line in pinned for key in line}
        rows: list[list[str]] = []
        for row in self._default_rows:
            keys = [k for k in row if k in present and k not in used]
            used.update(keys)
            if keys:
                rows.append(keys)
        rows.extend([k] for k in self._frames if k not in used)
        return {
            "version": SCHEMA_VERSION,
            "rows": rows,
            "floating": {},
            "hidden": [],
            "pinned": default_pin_model() | {"lines": pinned},
        }

    def arrangement(self) -> dict:
        """A snapshot of the current arrangement as a persistence model.

        ``hidden_anchors`` is an *optional* addition: it is read tolerantly on the
        way back in, so it needed no schema bump and a layout saved without it
        still restores (its blocks just reopen at their default position).
        """
        self._sync_from_board()
        return {
            "version": SCHEMA_VERSION,
            "rows": [list(row) for row in self._rows],
            "floating": {key: self._window_geometry(key) for key in self._windows},
            "hidden": sorted(self._hidden),
            # Kept for a block that is *off* the page — hidden or pinned — since
            # both come back through the same anchor.
            "hidden_anchors": {
                key: anchor.to_dict()
                for key, anchor in self._anchors.items()
                if key in self._hidden or self.is_pinned(key)
            },
            "pinned": {
                "edge": self._pin_edge,
                "lines": self.pinned_lines(),
                "align": self._pin_align,
                "sizes": list(self._pin_sizes),
                "line_sizes": [list(sizes) for sizes in self._pin_line_sizes],
                "extent": self._pin_extent,
            },
        }

    def _window_geometry(self, key: str) -> dict:
        """Where a floated block's window is, and whether it stays on top.

        ``on_top`` is an *optional* addition read tolerantly on the way back in, so
        it needed no schema bump and a layout saved without it still restores — the
        same terms ``hidden_anchors`` joined on. It is written **both ways**, not
        only when true: absence means the default, and the default is on, so a
        block deliberately let fall behind has to say so or it comes back on top.
        """
        geo = self._windows[key].geometry()
        return {
            "x": geo.x(),
            "y": geo.y(),
            "w": geo.width(),
            "h": geo.height(),
            "on_top": self._wants_on_top(key),
        }

    def apply_arrangement(self, model: dict) -> bool:
        """Replace the arrangement with *model*; returns False (leaving the current
        arrangement) if it is invalid.

        Wholesale replacement moves blocks in and out of hiding just as
        :meth:`hide_block`/:meth:`show_block` do, so it announces those changes the
        same way — otherwise a restored layout leaves the View menu's checkmarks
        describing the arrangement this one replaced.
        """
        parsed = self._validate(model)
        if parsed is None:
            return False
        rows, floating, hidden, anchors, pinned = parsed

        for key in list(self._windows):
            self._destroy_window(key)
        # A restored layout is authoritative about what stays on top; only
        # *moving* a block keeps that choice across a dock (see set_block_on_top).
        self._on_top.clear()
        was_hidden = self._hidden
        self._rows = rows
        self._hidden = set(hidden)
        self._anchors = anchors
        self._pinned = pinned.lines
        self._pin_edge = pinned.edge
        self._pin_align = pinned.align
        self._pin_sizes = pinned.sizes
        self._pin_line_sizes = pinned.line_sizes
        self._pin_extent = pinned.extent
        if self._board is not None:
            # The model is authoritative here, not the widgets: force the strip to
            # rebuild so the restored proportions are actually applied.
            self._board.invalidate()
        self._relayout()
        if self._board is not None:
            # A restored thickness, not one the user dragged — the only place the
            # board is told what it should be rather than asked what it is.
            self._board.set_extent(self._pin_extent)
        for key, geom in floating.items():
            self._make_floating(key, geom)

        for key in sorted(was_hidden ^ self._hidden):
            self.block_visibility_changed.emit(key, key not in self._hidden)
        self.arrangement_changed.emit()
        return True

    def _validate(self, model: object):
        """Parse/validate a persistence model → (rows, floating, hidden, anchors, pinned) or None.

        Enforces the invariant that every known block appears exactly once across
        rows, floating, hidden, and the pinned strip. The optional
        ``hidden_anchors`` is parsed leniently — anything unusable is dropped
        rather than rejecting the whole layout, since a missing anchor only costs
        a reopened block its remembered spot.
        """
        if not isinstance(model, dict) or model.get("version") != SCHEMA_VERSION:
            return None
        rows = model.get("rows")
        floating = model.get("floating")
        hidden = model.get("hidden")
        if not (isinstance(rows, list) and isinstance(floating, dict) and isinstance(hidden, list)):
            return None

        known = set(self._frames)
        pinned = _PinModel.parse(model.get("pinned"), known)
        if pinned is None:
            return None
        seen: list[str] = list(pinned.keys)

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

        return clean_rows, floating, list(hidden), anchors, pinned

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
        if self.is_pinned(key):
            self._sync_from_board()  # keep the other pinned blocks' dragged sizes
            for index, line in enumerate(self._pinned):
                if key not in line:
                    continue
                slot = line.index(key)
                line.pop(slot)
                if index < len(self._pin_line_sizes) and slot < len(self._pin_line_sizes[index]):
                    self._pin_line_sizes[index].pop(slot)
                if not line:  # the line emptied out; it collapses away with its size
                    self._pinned.pop(index)
                    if index < len(self._pin_sizes):
                        self._pin_sizes.pop(index)
                    if index < len(self._pin_line_sizes):
                        self._pin_line_sizes.pop(index)
                break
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
        # Back to meaning "pin to the strip": the block is about to have one again.
        # The *choice* is kept in `_on_top`, so popping it out later restores it.
        frame.title_bar.set_floating(False)
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

        on_top = self._wants_on_top(key)
        window = BlockWindow(key, self, self.window())
        window.set_frame(frame)
        frame.title_bar.set_floating(True, on_top=on_top)
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
        # Before the first show(), which is what makes the flag free — see
        # :func:`~mm_companion.ui.frameless.apply_window_flags`.
        window.set_on_top(on_top)
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
        # Absent means the default, which is on — see :meth:`_wants_on_top`.
        on_top = bool(geom.get("on_top", DEFAULT_ON_TOP))
        self._on_top[key] = on_top
        window = BlockWindow(key, self, self.window())
        window.set_frame(frame)
        frame.title_bar.set_floating(True, on_top=on_top)
        frame.show()
        self._apply_window_min_width(window, frame)
        window.setGeometry(geom["x"], geom["y"], geom["w"], geom["h"])
        self._windows[key] = window
        window.set_on_top(on_top)
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

    # -- the pinned strip ----------------------------------------------------

    def pin_block(
        self, key: str, line: int | None = None, slot: int = 0, new_line: bool = True
    ) -> None:
        """Move *key* into the pinned strip at (*line*, *slot*).

        Defaults to a new line at the end — what the title bar's pin button does.
        A drop passes the place its :class:`~mm_companion.ui.pinned.PinSlot` named,
        so a block can join an existing line beside another block just as it can
        start a new one.

        Does nothing without a board: a host that offers no strip has nowhere to
        put the block, and losing it off the page would be worse than refusing.

        A block arriving **forgets the dragged proportions along the axis it joins**,
        leaving the splitter to lay that axis out from the blocks' own size hints.
        The remembered sizes are live pixel values, true only of the shape they were
        measured in: a line that was alone in the strip is recorded as the strip's
        whole length, and slotting a newcomer's natural size in beside that number
        hands it a sliver of the space. Sizes taken away are still kept (they are
        all live values from one layout, so the survivors' proportions hold and the
        freed space is shared out between them).
        """
        if self._board is None:
            return
        # Read the live proportions in first: a block arriving from the *page*
        # never touches the strip's own bookkeeping on its way, so without this the
        # rebuild below would restore whatever sizes predated the last handle drag.
        self._sync_from_board()
        self._hidden.discard(key)
        # Remember where it sat, so unpinning can put it back there.
        anchor = self._anchor_for(key, self._rows)
        # Taking the block out can collapse the line it was in, shifting every
        # line after it up one — so a target line named against the strip as it
        # looks *now* has to move with it.
        before = len(self._pinned)
        from_line = next((i for i, existing in enumerate(self._pinned) if key in existing), None)
        self._detach(key)  # also syncs the live sizes out of the board
        if anchor is not None:
            self._anchors[key] = anchor
        if line is not None and from_line is not None and len(self._pinned) < before:
            if from_line < line:
                line -= 1
        at = len(self._pinned) if line is None else max(0, min(line, len(self._pinned)))
        # Arriving somewhere forgets the sizes on that axis — see _forget_sizes.
        if new_line or line is None or not self._pinned:
            self._pinned.insert(at, [key])
            self._pin_sizes = []
            if len(self._pin_line_sizes) == len(self._pinned) - 1:
                self._pin_line_sizes.insert(at, [])  # its blocks will lay themselves out
            else:
                self._pin_line_sizes = []
        else:
            at = min(at, len(self._pinned) - 1)
            target = self._pinned[at]
            index = max(0, min(slot, len(target)))
            target.insert(index, key)
            if at < len(self._pin_line_sizes):
                self._pin_line_sizes[at] = []
        self._relayout()
        self.arrangement_changed.emit()

    def unpin_block(self, key: str) -> None:
        """Take *key* out of the strip and dock it back onto the page.

        It returns where it was pinned from if that still resolves, else its
        default position — the same fallback ladder :meth:`show_block` walks, so a
        block dragged off the page into the strip tends to come back to the spot
        it left.
        """
        if not self.is_pinned(key):
            return
        slot = self._resolve_anchor(self._anchors.pop(key, None))
        if slot is None:
            slot = self._resolve_anchor(self._anchor_for(key, self._default_rows))
        if slot is None:
            slot = DropSlot(True, len(self._rows), 0)
        self._detach(key)
        self._place(key, slot)
        self._relayout()
        self.arrangement_changed.emit()

    def unpin_all(self) -> None:
        """Empty the strip, docking every pinned block back onto the page."""
        for key in self.pinned_keys():
            self.unpin_block(key)

    def is_pinned(self, key: str) -> bool:
        return any(key in line for line in self._pinned)

    def pinned_keys(self) -> list[str]:
        """Every pinned block, in strip order (lines, then within each line)."""
        return [key for line in self._pinned for key in line]

    def pinned_lines(self) -> list[list[str]]:
        return [list(line) for line in self._pinned]

    def pin_edge(self) -> str:
        return self._pin_edge

    def pin_align(self) -> str:
        return self._pin_align

    def set_pin_edge(self, edge: str) -> None:
        """Park the strip on another side of the page."""
        if edge not in PIN_EDGES or edge == self._pin_edge:
            return
        self._sync_from_board()
        self._pin_edge = edge
        # Every remembered size was measured along the old axis and means nothing
        # on the new one — a strip dragged 600px wide down the side would become a
        # 600px-deep floor. Start from the default thickness and let the blocks'
        # own minimums push it out from there.
        self._pin_sizes = []
        self._pin_line_sizes = []
        self._pin_extent = DEFAULT_EXTENT
        self._render_pinned()
        if self._board is not None:
            self._board.set_extent(self._pin_extent)
        self.arrangement_changed.emit()

    def set_pin_align(self, align: str) -> None:
        """Set how pinned blocks sit across the strip (fill / start / center / end)."""
        if align not in PIN_ALIGNMENTS or align == self._pin_align:
            return
        self._sync_from_board()
        self._pin_align = align
        self._render_pinned()
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
        pin_at = self._pin_hit_test(global_pos)
        self._end_drag()
        if not active:
            return
        if pin_at is not None:
            self.pin_block(key, pin_at.line, pin_at.slot, new_line=pin_at.new_line)
            return
        slot = self._hit_test(global_pos)
        if slot is not None:
            self.dock_block(key, slot.row, slot.slot, new_row=slot.new_row)

    def request_float(self, key: str) -> None:
        self.float_block(key)

    def request_hide(self, key: str) -> None:
        self.hide_block(key)

    def request_pin(self, key: str) -> None:
        """The title bar's pin button: into the strip, or back onto the page."""
        if self.is_pinned(key):
            self.unpin_block(key)
        else:
            self.pin_block(key)

    def request_on_top(self, key: str, on_top: bool) -> None:
        """The same pin button, in a window: above other applications, or not."""
        self.set_block_on_top(key, on_top)

    # -- floated windows -----------------------------------------------------

    def _wants_on_top(self, key: str) -> bool:
        """Whether *key* would float above other applications, asked or not.

        The default is on: a block is popped out of the app to be read *beside*
        something else, and one that immediately disappears behind the window it
        was meant to accompany is no use. Falling behind is the exception, and
        only :meth:`set_block_on_top` records it.
        """
        return self._on_top.get(key, DEFAULT_ON_TOP)

    def set_block_on_top(self, key: str, on_top: bool) -> None:
        """Keep floated block *key* above other applications, or let it fall behind.

        Remembered whether or not the block is currently floating, so popping it
        out again puts it back where the user left it — the answer is about this
        block, not about the particular window it happens to be in right now.
        Only a decision is recorded — here, or by a restored layout that carried
        one — which is what keeps "never asked" (and so the default) tellable
        apart from "asked for off".
        """
        self._on_top[key] = bool(on_top)
        window = self._windows.get(key)
        if window is not None:
            window.set_on_top(on_top)
            self._frames[key].title_bar.set_floating(True, on_top=on_top)
        self.arrangement_changed.emit()

    def is_block_on_top(self, key: str) -> bool:
        """Whether block *key* stays above other applications while it is floated.

        Answers for a docked block too — as "it would" — since the choice belongs
        to the block rather than to any window it is in at the time.
        """
        return self._wants_on_top(key)

    def set_windows_suspended(self, suspended: bool) -> None:
        """Take the floated windows off the screen while the host is compact.

        Compact mode means "put the app out of the way", and a scatter of loose
        block windows left behind is exactly the thing it was asked to clear. The
        ones pinned on top stay: keeping a block beside the mini roller is what
        pinning it *for*, and hiding it would make the pin meaningless.
        """
        suspended = bool(suspended)
        if suspended == self._windows_suspended:
            return
        self._windows_suspended = suspended
        for key, window in self._windows.items():
            if self._wants_on_top(key):
                continue
            window.setVisible(not suspended)

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
        if self._board is not None:
            self._board.hide_drop()

    def update_drag(self, global_pos: QPoint) -> None:
        """Refresh the drop indicator and edge auto-scroll for a cursor position."""
        pin_at = self._pin_hit_test(global_pos)
        if pin_at is not None:
            # Over the strip: it owns the feedback, and the page shows none — two
            # insert lines at once would be a lie about where the block lands.
            self._indicator.hide_indicator()
            self._board.show_drop(pin_at)  # type: ignore[union-attr] - set with pin_at
            return
        if self._board is not None:
            self._board.hide_drop()
        self._show_indicator(self._hit_test(global_pos))
        self._maybe_autoscroll(global_pos)

    def _pin_hit_test(self, global_pos: QPoint) -> PinSlot | None:
        """Where in the pinned strip a drop at *global_pos* would land, if at all."""
        if self._board is None:
            return None
        return self._board.drop_slot(global_pos)

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

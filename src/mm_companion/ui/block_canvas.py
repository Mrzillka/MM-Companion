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

from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
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
    QMenu,
    QSplitter,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

from mm_companion.ui import layout_tree as lt
from mm_companion.ui import theme
from mm_companion.ui.block_frame import BlockFrame, BlockWindow
from mm_companion.ui.block_sizes import UNBOUNDED, RecommendedSize
from mm_companion.ui.blocks.base import instance_template
from mm_companion.ui.drop_feedback import DropIndicator, DropRegion
from mm_companion.ui.grid_view import GridSplitter, RowStack, build_node
from mm_companion.ui.pinned import (
    DEFAULT_EDGE,
    DEFAULT_EXTENT,
    PIN_EDGES,
    PinSlot,
    is_vertical_strip,
)
from mm_companion.ui.tab_group import TabGroupFrame
from mm_companion.ui.widgets import discard_widget

if TYPE_CHECKING:  # the board is the canvas's *view* for pinned blocks, not a dependency
    from mm_companion.ui.pinned_panel import PinnedBoard

# Bumped whenever the persisted arrangement schema changes. A layout saved by an
# older version is normally rejected and the default applies; version 7 is the
# exception, and is *migrated* — see layout_tree.migrate_v7. Every row and every
# pinned line of a v7 layout has an exact reading as a tree, so there was no
# reason to throw away a page somebody had arranged.
SCHEMA_VERSION = 8

#: How a host builds a block a restored layout names but the registry does not
#: hold: it answers with ``(title, section, size)``, or None for a key it does
#: not recognise. Only a host with multi-instance blocks supplies one.
InstanceFactory = Callable[[str], tuple[str, QWidget, RecommendedSize] | None]

#: Whether a block popped out of the app stays above other applications unless
#: told otherwise. It does: a floated block's whole purpose is to be read beside
#: somebody else's window, and one that sinks behind that window the moment it is
#: clicked is a block nobody can use.
DEFAULT_ON_TOP = True

#: The fastest the page auto-scrolls, in pixels per tick. There is no natural
#: ceiling in the curve below — a pointer dragged well past the bottom of the
#: window, which is exactly what somebody making a row taller does, would
#: otherwise scroll faster the further out of the window their hand went.
MAX_SCROLL_STEP = 32


def edge_velocity(y: int, height: int, hot: int) -> int:
    """How far to scroll for a pointer *y* pixels down a viewport *height* tall.

    Zero away from the edges, and away from zero — faster the deeper in, capped —
    once the pointer is inside the *hot* band at either end or has left the
    viewport altogether. Kept as a plain function for the reason
    :func:`~mm_companion.ui.grid_handle.snap_to_detent` is: it is the whole of a
    decision two different drags make, and it is worth being able to ask it a
    question without a window.
    """
    if y < hot:
        return -min(MAX_SCROLL_STEP, max(4, (hot - y) // 3))
    if y > height - hot:
        return min(MAX_SCROLL_STEP, max(4, (y - (height - hot)) // 3))
    return 0


def drop_side(point: QPoint, rect: QRect, *, merge_share: float) -> str | None:
    """Which edge of *rect* a drop at *point* belongs to, or None for its middle.

    The block is read in *shares* of itself rather than in pixels, and that is the
    whole change: the bands used to be 28px of merge inset and a 24px stack band,
    which left "beside this block" as a 28px strip down each edge. Placing a block
    second in a row meant hitting that strip, and the merge was never advertised at
    all — there is no way to discover a gesture whose target is the part of a block
    you were already over.

    Now the middle ninth (a third across by a third down) means *merge*, and
    everything outside it belongs to whichever edge is nearest as a share of the
    block — so the four sides are clean diagonal quadrants, every corner has one
    answer, and the region grows with the block instead of staying a hairline on a
    big one.

    Kept out of Qt like :func:`~mm_companion.ui.grid_handle.snap_to_detent`: it is
    the whole of a decision, and worth asking without a window.
    """
    width = max(1, rect.width())
    height = max(1, rect.height())
    across = (point.x() - rect.left()) / width
    down = (point.y() - rect.top()) / height
    margin = (1 - merge_share) / 2
    if margin <= across <= 1 - margin and margin <= down <= 1 - margin:
        return None
    distances = {"left": across, "right": 1 - across, "top": down, "bottom": 1 - down}
    return min(distances, key=lambda side: distances[side])


@dataclass(frozen=True)
class DropSlot:
    """Where a dragged block lands.

    Two vocabularies, because two kinds of caller need it. A drag names a
    ``target`` block and the ``side`` of it to land on, which is the only way to
    say "underneath that one" — the thing the page could not express while a row
    was the only container. Everything else (an anchor resolving, a block being
    reopened or unpinned) still names a ``row`` and a ``slot`` in it, which is all
    those callers have ever known, and :meth:`BlockCanvas._place` translates.

    ``onto`` names a block the dragged one would merge into rather than sit
    beside, making the two of them a tab group.
    """

    new_row: bool
    row: int
    slot: int
    onto: str | None = None
    target: str | None = None
    side: str = "right"


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


def _widget_tree(roots: list[QWidget]) -> set[int]:
    """Every widget at or under *roots*, by identity.

    Held as ``id()`` rather than the widgets themselves: this is compared against
    widgets from the *previous* render, and a ``QWidget`` whose C++ half has gone
    raises from ``__eq__`` and ``__hash__`` alike. Identity is the one question
    that is always safe to ask.
    """
    seen: set[int] = set()

    def walk(widget: QWidget | None) -> None:
        if widget is None or id(widget) in seen:
            return
        seen.add(id(widget))
        for child in widget.findChildren(QWidget):
            seen.add(id(child))

    for root in roots:
        walk(root)
    return seen


def _forget_sizes(node: lt.Node | None) -> lt.Node | None:
    """*node* with every remembered proportion dropped, keeping its shape."""
    if node is None or isinstance(node, lt.Leaf):
        return node
    children = tuple(_forget_sizes(child) for child in node.children)
    return lt.Split(node.orientation, tuple(c for c in children if c is not None), ())


def _rotate(node: lt.Node | None) -> lt.Node | None:
    """*node* with every split turned through a right angle, and its sizes dropped.

    What moving the strip from a side to the top or bottom has to do to its
    contents. A strip is a column of blocks down the right and a row of them along
    the bottom; carrying the tree across unturned would leave a right-edge strip's
    column *still* a column, stacked inside a band a few hundred pixels tall.
    Sizes go with the turn, being live pixel measurements of the axis that just
    stopped existing.
    """
    if node is None or isinstance(node, lt.Leaf):
        return node
    flipped = lt.HORIZONTAL if node.orientation == lt.VERTICAL else lt.VERTICAL
    children = tuple(_rotate(child) for child in node.children)
    return lt.Split(flipped, tuple(c for c in children if c is not None), ())


class BlockCanvas(QWidget):
    """Free-form, scrollable arrangement of the sheet's blocks (see module doc)."""

    arrangement_changed = Signal()
    block_visibility_changed = Signal(str, bool)
    #: A multi-instance block was built or destroyed at runtime, so a host's
    #: View menu can grow and lose an entry with it.
    block_added = Signal(str)
    block_removed = Signal(str)
    #: A block was dropped *onto* another (source, target). The canvas only
    #: reports it: what merging two blocks means is the host's business.
    merge_requested = Signal(str, str)
    #: One arrangement gesture has *finished* — a drop landed, a divider was let
    #: go, a block was pinned or closed. Distinct from ``arrangement_changed``,
    #: which fires for every intermediate state of a drag as well: this is the
    #: moment a layout history has something worth keeping.
    gesture_finished = Signal()

    def __init__(
        self,
        panels: list[tuple[str, str, QWidget]],
        block_sizes: dict[str, RecommendedSize],
        default_rows: list[list[str]],
        parent: QWidget | None = None,
        *,
        default_pinned: list[list[str]] | None = None,
        instance_factory: InstanceFactory | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("blockCanvas")

        self._sizes = block_sizes
        self._default_rows = default_rows
        # Blocks the default arrangement parks in the strip rather than on the page
        # (the sheet's Dice block). A host that offers no strip passes none.
        self._default_pinned = default_pinned or []
        # One frame per block, created once and reparented as it moves.
        self._frames: dict[str, BlockFrame] = {}
        for key, title, section in panels:
            size = block_sizes.get(key, RecommendedSize())
            frame = BlockFrame(key, title, section, size, self, parent=self)
            frame.hide()  # shown once _relayout places it in a row
            self._frames[key] = frame

        # How a key in a restored layout that has no frame yet becomes one: the
        # host answers with (title, section, size), or None if it doesn't know the
        # key. Only a host with multi-instance blocks supplies it — the GM window
        # passes none, so nothing there can be conjured out of a layout file.
        self._instance_factory = instance_factory
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
        # The page, as a tree of splits and leaves (see ui/layout_tree.py). The
        # rows are its top-level children; anything nested inside one of those is
        # a block stacked beside or under another.
        self._page: lt.Split = lt.Split(lt.VERTICAL, ())
        self._hidden: set[str] = set()
        # Where each hidden block was closed from, so reopening restores it there.
        self._anchors: dict[str, Anchor] = {}
        self._row_widgets: list[QWidget] = []
        # The page's view. Rows with a height the user dragged keep it; the rest
        # are as tall as their content, exactly as every row used to be.
        self._stack = RowStack(self)
        # Live tab groups, keyed by exactly the blocks in them. Kept across a
        # rebuild so a group is not destroyed and remade — it holds its members'
        # frames, and remaking it would take live blocks with it.
        self._groups: dict[tuple[str, ...], TabGroupFrame] = {}

        # The pinned strip: blocks parked on one edge of the window, outside this
        # scrolling page (see mm_companion.ui.pinned_panel). Modelled the same way
        # the page is — lines of block keys, each line holding one or more blocks
        # side by side — with the sizes the user dragged alongside. The canvas
        # stays the single source of truth; the board is only the view it renders
        # them through, and stays None for a host that doesn't offer the strip.
        # The strip, as a tree exactly like the page's. It used to be lines of
        # keys with two parallel lists of pixel sizes beside it; the sizes live in
        # the tree now, where they cannot drift out of step with the blocks they
        # describe.
        self._region: lt.Node | None = None
        self._pin_edge = DEFAULT_EDGE
        self._pin_extent = DEFAULT_EXTENT
        self._board: PinnedBoard | None = None

        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(6, 6, 6, 6)
        self._layout.setSpacing(8)

        self._indicator = DropIndicator(self)
        # The line says *where* the seam falls; this says how much of the target
        # the newcomer takes. Named for the mark and not for the strip's own
        # ``_region`` tree, which is a different thing entirely.
        self._region_mark = DropRegion(self)
        # Coalesces a run of handle movements into one finished gesture.
        self._size_settle = QTimer(self)
        self._size_settle.setSingleShot(True)
        self._size_settle.setInterval(self.SIZE_SETTLE_MS)
        self._size_settle.timeout.connect(self._flush_sizes)
        self._stack.heightsChanged.connect(self._on_sizes_settled)

        # Drag state.
        self._scroll_area: QAbstractScrollArea | None = None
        self._drag_key: str | None = None
        self._drag_active = False
        # The frame currently washed as a merge target, so it can be cleared.
        self._merge_hint: str | None = None
        self._press_global = QPoint()
        self._grab_offset = QPoint()
        self._autoscroll_velocity = 0
        self._autoscroll_timer = QTimer(self)
        self._autoscroll_timer.setInterval(16)
        self._autoscroll_timer.timeout.connect(self._autoscroll_tick)
        # The same idea for a row grip dragged to the edge of the window. Its own
        # timer rather than a mode on the one above: the two gestures can never
        # run at once, but one of them tearing the other's velocity down mid-drag
        # is the sort of thing a shared flag invites.
        self._grip_velocity = 0
        self._grip_timer = QTimer(self)
        self._grip_timer.setInterval(16)
        self._grip_timer.timeout.connect(self._grip_autoscroll_tick)
        self._stack.gripDragMoved.connect(self._maybe_grip_autoscroll)
        self._stack.gripDragFinished.connect(self._stop_grip_autoscroll)
        self._stack.rowCollapsing.connect(self._on_row_collapsing)
        self._stack.rowCollapsed.connect(self._on_row_collapsed)

        self.apply_arrangement(self.default_arrangement())

    # -- the page tree, and the flat view of it the older code still speaks ---

    @property
    def _rows(self) -> list[list[str]]:
        """The page's top-level rows, each as a flat list of block keys.

        A *view* over :attr:`_page`, not a second model. Most of this class only
        ever needs to know which blocks share a row and in what order — anchors,
        reopening a hidden block, the default arrangement — and that question has
        the same answer whether or not the row has a stack nested inside it. The
        drag gesture, which does care, works on the tree directly.
        """
        return [lt.keys(child) for child in self._page.children]

    def _set_page(self, node: lt.Node | None) -> None:
        """Replace the page tree, keeping it canonical and always a page."""
        self._page = lt.as_page(node)

    def page_tree(self) -> lt.Split:
        """The page as a tree — the seam tests and the layout history read."""
        return self._page

    def _row_index_of(self, key: str) -> int | None:
        for index, row in enumerate(self._rows):
            if key in row:
                return index
        return None

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

    # -- blocks that come and go --------------------------------------------

    def add_block(
        self,
        key: str,
        title: str,
        section: QWidget,
        size: RecommendedSize,
        *,
        near: str | None = None,
    ) -> None:
        """Build a frame for *key* and place it on the page.

        The frames were built once, in the constructor, for as long as the block
        set was fixed; a multi-instance block (Notes) is what makes one arrive
        later. It lands beside *near* when that block is in a row — so a Notes
        block split off another appears next to it rather than at the bottom of a
        long sheet — and otherwise in a new row at the end.
        """
        if key in self._frames:
            return
        self._sizes[key] = size
        frame = BlockFrame(key, title, section, size, self, parent=self)
        frame.hide()
        self._frames[key] = frame
        beside = near if near and lt.find(self._page, near) is not None else None
        if beside is None:
            self._set_page(lt.append_row(self._page, key))
        else:
            self._set_page(lt.insert_beside(self._page, key, beside, "right"))
        self._relayout()
        self.block_added.emit(key)
        self._settled()

    def remove_block(self, key: str) -> None:
        """Destroy block *key*'s frame and forget it everywhere.

        The caller owns the *section* inside it — the sheet drops it from its own
        tables and stops listening — so this only takes the frame apart. Every
        dict keyed by block has to be swept, or a later ``arrangement()`` writes
        out a key nothing can build.
        """
        if key not in self._frames:
            return
        if self._drag_key == key:
            self._end_drag()
        self._detach(key)
        frame = self._frames.pop(key)
        discard_widget(frame)
        self._sizes.pop(key, None)
        self._hidden.discard(key)
        self._anchors.pop(key, None)
        self._on_top.pop(key, None)
        self._relayout()
        self.block_removed.emit(key)
        self._settled()

    def _row_of(self, key: str) -> int | None:
        for index, row in enumerate(self._rows):
            if key in row:
                return index
        return None

    def block_window(self, key: str):
        """The :class:`BlockWindow` block *key* is floated in, or ``None`` if docked."""
        return self._windows.get(key)

    def content_minimum_width(self) -> int:
        """How narrow the page may be made. A constant, and deliberately so.

        This used to be the widest docked row — every block's whole content added
        up — and the sheet pinned its own minimum width to it, which is what made
        the *window* refuse to be made smaller than the blocks inside it. A page
        the user resizes cannot work that way: a block that is too narrow reflows,
        and past that it scrolls inside its own frame, so there is no width at
        which the page is broken and none it has to refuse.

        It stays a method rather than becoming nothing because the sheet, the GM
        window and their tests all ask the question; the honest answer is now the
        same one every time.
        """
        margins = self._layout.contentsMargins()
        return int(theme.metric("block.min-extent")) + margins.left() + margins.right()

    # -- rendering -----------------------------------------------------------

    def _relayout(self) -> None:
        """Rebuild the page from the tree.

        Every frame is reparented on the way, which is why nothing here may leave
        one visible while it is between homes: a parentless visible widget *is* a
        top-level window, and a page of a dozen blocks rebuilding would flash a
        dozen of them (see ``tests/test_window_flash.py``). Frames are hidden
        before they are let go and shown once they are placed; containers are shed
        through ``discard_widget``, which hides before it unparents.
        """
        self._render_pinned()  # first: see _render_pinned for why the order matters
        old_rows = self._row_widgets

        # Anything the tree no longer places — a block now hidden, floating or
        # pinned — is rescued to the canvas before its old container goes, or
        # deleting that container would take the frame's C++ half with it.
        #
        # Only the ones still inside the page's own stack. _render_pinned has just
        # run (see below for why it goes first), so a block that has moved to the
        # strip is already parented there and rescuing it would take it straight
        # back off — which is exactly the bug the render order exists to prevent,
        # arrived at from the other direction.
        placed = set(lt.keys(self._page))
        for key, frame in self._frames.items():
            if key in placed or not self._is_at_or_within(frame, self._stack):
                continue
            frame.hide()
            frame.setParent(self)

        # Shed any group the tree no longer asks for *before* building, so a
        # group left over from an arrangement that has been replaced hands its
        # members back rather than holding frames nothing can reach.
        wanted = {leaf.keys for _, leaf in lt.iter_leaves(self._page) if leaf.tabbed}
        for keys in [keys for keys in self._groups if keys not in wanted]:
            self._release_group(keys)

        rows: list[QWidget] = []
        for child in self._page.children:
            row = build_node(child, self._build_leaf, self._stack)
            rows.append(row)
            self._watch_splitters(row)
        self._row_widgets = rows
        self._stack.set_rows(rows, self._row_heights())

        for key in placed:
            self._frames[key].show()

        # Only the containers we made *this* time round are shed, and only the ones
        # nothing is still using. Two widgets survive a rebuild and both were
        # destroyed by a naive sweep here:
        #
        # * a row holding a single block **is** that block's frame (build_node
        #   returns the frame itself for a lone leaf), so shedding it destroyed a
        #   live block;
        # * a row holding a tab group **is** that group, and groups are cached
        #   across a rebuild — so the very widget just handed back for reuse was
        #   then deleted, and it took its members' frames down with it. That one
        #   killed the application a moment later, when anything asked those frames
        #   a question.
        #
        # Asking "is this in the new tree" rather than "is this a kind of widget I
        # recognise" is what makes it safe against the next widget that learns to
        # survive a rebuild.
        kept = _widget_tree(rows)
        for row in old_rows:
            if id(row) not in kept and not isinstance(row, BlockFrame):
                discard_widget(row)

        if self._layout.indexOf(self._stack) < 0:
            self._layout.addWidget(self._stack)
        self._region_mark.raise_()
        self._indicator.raise_()  # last, so the seam reads over the wash

    def _settled(self) -> None:
        """One gesture is over: tell anyone laying out, and anyone remembering."""
        self.arrangement_changed.emit()
        self.gesture_finished.emit()

    #: How long after the last handle movement a divider drag counts as over.
    #: A splitter reports every frame of one, and a divider pulled across the page
    #: would otherwise be fifty entries in the layout history rather than one.
    SIZE_SETTLE_MS = 250

    def _on_sizes_settled(self) -> None:
        """A divider moved. Wait for it to stop before calling that a gesture."""
        self._size_settle.start()

    def _flush_sizes(self) -> None:
        """The dividers have stopped: take the sizes into the model and say so."""
        self._remember_sizes()
        self._settled()

    # No ``minimumSizeHint`` here either, for the reason written out on
    # :class:`~mm_companion.ui.grid_view.RowStack`: the page's own layout already
    # sums its rows, and asking a widget's sizeHint from inside its minimumSizeHint
    # is how a scroll area ends up chasing its own scrollbar.

    def _watch_splitters(self, widget: QWidget) -> None:
        """Follow every divider under *widget*, so a drag becomes a settled gesture."""
        if isinstance(widget, QSplitter):
            widget.sizesSettled.connect(self._on_sizes_settled)
            self._watch_collapses(widget)
            for index in range(widget.count()):
                self._watch_splitters(widget.widget(index))

    def _watch_collapses(self, splitter: QSplitter) -> None:
        """Follow one divider for a block being squashed out of existence.

        Split out from :meth:`_watch_splitters` because the pinned strip wants
        this half and not the other: its sizes reach the model through
        :meth:`_sync_from_board` on demand rather than through ``sizesSettled``,
        and giving its dividers the page's settle timer as well would be a second
        route for the same numbers.
        """
        splitter.paneCollapsing.connect(self._on_pane_collapsing)
        splitter.paneCollapsed.connect(self._on_pane_collapsed)

    def _row_heights(self) -> list[int]:
        """The height stated for each row; zero where the user has not set one."""
        sizes = self._page.usable_sizes()
        return list(sizes) if sizes else [0] * len(self._page.children)

    def _build_leaf(self, leaf: lt.Leaf) -> QWidget:
        """The widget for one cell: a block's frame, or a group of them in tabs.

        Groups are **kept across a rebuild** when their membership has not
        changed, keyed by the tuple of blocks in them. Rebuilding one every
        relayout would reparent every member twice a drag and throw away which tab
        was showing — and, since the group holds the frames, would be a good way to
        destroy a live block.
        """
        keys = leaf.keys
        if len(keys) == 1:
            self._release_group(keys)
            return self._frames[keys[0]]

        group = self._groups.get(keys)
        if group is None:
            self._dissolve_groups(keys)
            group = TabGroupFrame(
                [self._frames[key] for key in keys], leaf.active, self, parent=self
            )
            group.splitRequested.connect(self._on_tab_split)
            group.splitMoved.connect(self.update_drag)
            group.splitReleased.connect(self._on_tab_split_released)
            group.activeChanged.connect(self._on_tab_activated)
            self._groups[keys] = group
        else:
            group.refresh_titles()
        return group

    def _release_group(self, keys: tuple[str, ...]) -> None:
        """Hand a group's members back and shed it.

        A group *owns* its members' frames, so one going away has to give them up
        before it is deleted or it takes live blocks with it — the same rule the
        old rows had, and the same way it is broken.
        """
        group = self._groups.pop(keys, None)
        if group is None:
            return
        for key in list(group.keys):
            frame = group.release(key)
            if frame is not None:
                frame.hide()
                frame.setParent(self)
        discard_widget(group)

    def _dissolve_groups(self, wanted: tuple[str, ...]) -> None:
        """Shed every group but *wanted*, whose members are about to be reused."""
        for keys in [keys for keys in self._groups if keys != wanted]:
            self._release_group(keys)

    def group_for(self, key: str):
        """The tab group *key* is currently in, if any."""
        for keys, group in self._groups.items():
            if key in keys:
                return group
        return None

    def _on_tab_activated(self, key: str) -> None:
        """Remember which tab of a group is showing, so a restore brings it back."""
        self._set_page(lt.set_active(self._page, key))
        self._settled()

    def _on_tab_split(self, key: str, global_pos: QPoint) -> None:
        """A tab was dragged clear of its bar: take that block out of the group.

        From here the gesture is indistinguishable from one begun on a title bar —
        the tab bar still holds the mouse grab, so its moves and its release are
        forwarded into the same drag controller and the block can dock, stack,
        pin, merge or stay floating.
        """
        self._set_page(lt.split_out(self._page, key))
        self._relayout()
        self.adopt_drag(key, global_pos)

    def _on_tab_split_released(self, global_pos: QPoint) -> None:
        if self._drag_key is not None:
            self.title_bar_released(self._drag_key, global_pos)

    def _remember_sizes(self) -> None:
        """Read the live splitter sizes back into the tree.

        The handles are dragged by the user, not by us, so the sizes are only true
        on the widgets until somebody asks. Pulling them in here is what makes a
        resize survive a save, an undo step, or the next rebuild.
        """
        heights = self._stack.heights()
        page = self._page
        if len(heights) == len(page.children):
            page = replace(page, sizes=tuple(heights))
        for index, row in enumerate(self._row_widgets):
            page = self._absorb_sizes(page, (index,), row)
        self._page = lt.as_page(page)

    def _absorb_sizes(self, page: lt.Node, path: tuple[int, ...], widget: QWidget) -> lt.Node:
        """Copy *widget*'s splitter sizes (and its children's) into the tree."""
        if not isinstance(widget, QSplitter):
            return page
        node = lt.at(page, path)
        if isinstance(node, lt.Split) and widget.count() == len(node.children):
            page = lt.set_sizes(page, path, widget.sizes()) or page
            for index in range(widget.count()):
                page = self._absorb_sizes(page, path + (index,), widget.widget(index))
        return page

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
        if self._board.set_blocks(self._region, self._frames, self._pin_edge):
            # Only when the strip actually rebuilt: its dividers are new widgets
            # then, and connecting the same ones twice would close a block twice.
            for splitter in self._board.panel.splitters():
                self._watch_collapses(splitter)

    def _sync_from_board(self) -> None:
        """Read the strip's live proportions back into the region tree.

        The handles are dragged by the user, not by us, so the sizes are only true
        on the widgets until somebody asks; pull them in before anything snapshots
        or rebuilds. The strip used to keep two parallel lists of pixel sizes for
        this, which could and did drift out of step with the blocks they described;
        the sizes are in the tree now, and this walks the same splitters the page
        walks with the same function.
        """
        if self._board is None:
            return
        region = self._region
        for path, widget in self._board.panel.split_paths():
            region = self._absorb_sizes(region, path, widget) if region is not None else region
        self._region = lt.normalize(region)
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
            "instances": [],
            "page": lt.to_dict(lt.rows_to_page(rows)),
            "region": {
                "edge": DEFAULT_EDGE,
                "extent": DEFAULT_EXTENT,
                "root": lt.to_dict(lt.lines_to_region(pinned, DEFAULT_EDGE)),
            },
            "floating": {},
            "hidden": [],
        }

    def arrangement(self) -> dict:
        """A snapshot of the current arrangement as a persistence model.

        ``hidden_anchors`` is an *optional* addition: it is read tolerantly on the
        way back in, so it needed no schema bump and a layout saved without it
        still restores (its blocks just reopen at their default position).
        """
        self._sync_from_board()
        self._remember_sizes()
        return {
            "version": SCHEMA_VERSION,
            # Which blocks *exist*, before where any of them sits. Only the ones
            # nothing else could rebuild: a multi-instance block beyond the first
            # is created at runtime, so a restore has to be told to make it before
            # the placement below can name it (see _reconcile_instances).
            "instances": [
                {"key": key, "title": frame.base_title}
                for key, frame in self._frames.items()
                if instance_template(key) != key
            ],
            "page": lt.to_dict(self._page),
            "region": {
                "edge": self._pin_edge,
                "extent": self._pin_extent,
                "root": lt.to_dict(self._region),
            },
            "floating": {key: self._window_geometry(key) for key in self._windows},
            "hidden": sorted(self._hidden),
            # Kept for a block that is *off* the page — hidden or pinned — since
            # both come back through the same anchor.
            "hidden_anchors": {
                key: anchor.to_dict()
                for key, anchor in self._anchors.items()
                if key in self._hidden or self.is_pinned(key) or key in self._windows
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
        # Before anything is validated: make the frames the model expects to
        # exist. _validate demands that the layout's keys and the live frames
        # match *exactly*, so an instance named in a saved layout has to be built
        # first or the whole layout is rejected and the user loses their page.
        self._reconcile_instances(model)
        parsed = self._validate(model)
        if parsed is None:
            return False
        page, region, floating, hidden, anchors = parsed
        edge, extent, region_root = region

        for key in list(self._windows):
            self._destroy_window(key)
        # A restored layout is authoritative about what stays on top; only
        # *moving* a block keeps that choice across a dock (see set_block_on_top).
        self._on_top.clear()
        was_hidden = self._hidden
        self._set_page(page)
        self._hidden = set(hidden)
        self._anchors = anchors
        self._region = region_root
        self._pin_edge = edge
        self._pin_extent = extent
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
        self._settled()
        return True

    def _reconcile_instances(self, model: object) -> None:
        """Make the multi-instance frames *model* names, and destroy the rest.

        The one place a block is built from a layout file rather than from the
        registry. It is narrow on purpose: only a key whose
        :func:`instance_template` differs from itself is touched, so a base block
        can never be conjured up or swept away by an edited settings file, and a
        host with no ``instance_factory`` (the GM window) reconciles nothing at
        all. A key the factory does not recognise is simply not built — the
        layout then fails validation and the default applies, which is the right
        answer for a layout naming a block this version no longer has.
        """
        if self._instance_factory is None or not isinstance(model, dict):
            return
        raw = model.get("instances")
        wanted: dict[str, str] = {}
        if isinstance(raw, list):
            for entry in raw:
                if isinstance(entry, dict) and isinstance(entry.get("key"), str):
                    key = entry["key"]
                    if instance_template(key) != key:
                        wanted[key] = str(entry.get("title") or key)

        for key in [k for k in self._frames if instance_template(k) != k and k not in wanted]:
            self.remove_block(key)
        for key, title in wanted.items():
            if key in self._frames:
                continue
            built = self._instance_factory(key)
            if built is not None:
                self.add_block(key, built[0] or title, built[1], built[2])

    def _validate(self, model: object):
        """Parse a persistence model → (page, region, floating, hidden, anchors) or None.

        Enforces the invariant that has held since the arrangement was rows: every
        known block appears **exactly once** across the page, the region, the
        floating windows and the hidden set. A layout that half-describes where
        somebody's blocks are is worse than no layout, so anything short of that
        falls back to the default.

        Strict about *where a block lives* — an unknown key or edge rejects the
        whole thing, because guessing would silently move one. Lenient about the
        cosmetic numbers: the sizes inside the tree, the strip's thickness and the
        optional ``hidden_anchors`` all degrade to defaults, since a lost
        proportion only costs a remembered shape.

        A **version-7 layout is migrated rather than rejected** (see
        :func:`~mm_companion.ui.layout_tree.migrate_v7`). Every row and every
        pinned line of one has an exact reading as a tree, and discarding a page
        somebody arranged because the file format moved on would be a poor trade
        for forty lines.
        """
        if not isinstance(model, dict):
            return None
        known = set(self._frames)
        if model.get("version") == 7:
            migrated = lt.migrate_v7(model, known)
            if migrated is None:
                return None
            model = migrated | {"version": SCHEMA_VERSION}
        if model.get("version") != SCHEMA_VERSION:
            return None

        floating = model.get("floating")
        hidden = model.get("hidden")
        if not (isinstance(floating, dict) and isinstance(hidden, list)):
            return None

        page = lt.from_dict(model.get("page"), known)
        if page is None and model.get("page") not in (None, {}):
            return None

        raw_region = model.get("region")
        raw_region = raw_region if isinstance(raw_region, dict) else {}
        edge = raw_region.get("edge", DEFAULT_EDGE)
        if edge not in PIN_EDGES:
            return None
        region = lt.from_dict(raw_region.get("root"), known)
        if region is None and raw_region.get("root") not in (None, {}):
            return None
        extent = raw_region.get("extent")
        if not (isinstance(extent, int) and not isinstance(extent, bool) and extent > 0):
            extent = DEFAULT_EXTENT

        seen: list[str] = lt.keys(page) + lt.keys(region)
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

        return page, (edge, extent, region), floating, list(hidden), anchors

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
            # Keep the other pinned blocks' dragged sizes: taking one out frees its
            # room for its siblings, and the tree drops the sizes that described a
            # run this block was in.
            self._sync_from_board()
            self._region = lt.remove(self._region, key)
        self._set_page(lt.remove(self._page, key))
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
        """Tear *key* out into its own :class:`BlockWindow`.

        It *opens* at the block's natural size, so popping a block out never
        changes how it reads. What it may then be dragged down to is another
        matter: the window scrolls both ways and its floor is a theme metric, not
        the block's content (see :class:`~mm_companion.ui.block_frame.BlockWindow`).
        """
        if key in self._windows:
            return
        frame = self._frames[key]
        old_global = frame.mapToGlobal(QPoint(0, 0))
        old_size = frame.size()
        # Where it sat, so docking it back puts it there — the same note pinning
        # and closing both take, and for the same reason. Recorded before
        # ``_detach`` mutates the rows out from under it.
        anchor = self._anchor_for(key, self._rows)
        self._detach(key)
        self._hidden.discard(key)
        if anchor is not None:
            self._anchors[key] = anchor

        on_top = self._wants_on_top(key)
        window = BlockWindow(key, self, self.window())
        window.set_frame(frame)
        frame.title_bar.set_floating(True, on_top=on_top)
        frame.show()
        width = max(old_size.width(), frame.sizeHint().width(), frame.minimumSizeHint().width())
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
        self._settled()

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
        window.setGeometry(geom["x"], geom["y"], geom["w"], geom["h"])
        self._windows[key] = window
        window.set_on_top(on_top)
        window.show()

    def _place(self, key: str, slot: DropSlot) -> None:
        """Insert *key* into the page at *slot*. Assumes it has been detached.

        A slot names a row and a position in it, which is all the older callers —
        an anchor resolving, a block being unpinned or reopened — have ever known
        how to say. It is translated into the tree's own vocabulary here: land
        *beside* the block currently in that position, on whichever side puts it
        where the index asked for.

        A drag says more than this (it names a target block and a side, so it can
        stack one block under another), and takes :meth:`place_beside` instead.
        """
        rows = self._rows
        row = max(0, min(slot.row, len(rows)))
        if slot.new_row or not rows:
            self._set_page(lt.append_row(self._page, key, row))
            return
        target_row = rows[min(row, len(rows) - 1)]
        index = max(0, min(slot.slot, len(target_row)))
        if index < len(target_row):
            self.place_beside(key, target_row[index], "left")
        else:
            self.place_beside(key, target_row[-1], "right")

    def place_beside(self, key: str, target: str, side: str) -> None:
        """Put *key* on the given side of *target*. Assumes it has been detached.

        The one structural move a drag makes, and the only one that can put a
        block *under* another rather than next to it.
        """
        self._set_page(lt.insert_beside(self._page, key, target, side))

    def drop_block(self, key: str, slot: DropSlot) -> None:
        """Land *key* where *slot* says — beside a block, under one, or in a new row.

        The drag's own move, and the only one that can say "under that block".
        Falls back to :meth:`dock_block`'s row-and-index vocabulary when the slot
        names no target, which is what a drop in the gap between two rows does.
        """
        if slot.target is None or slot.target not in self._frames or slot.new_row:
            self.dock_block(key, slot.row, slot.slot, new_row=slot.new_row)
            return
        if slot.target == key:
            return
        self._hidden.discard(key)
        self._detach(key)
        self.place_beside(key, slot.target, slot.side)
        self._relayout()
        self._settled()

    def merge_blocks(self, key: str, target: str) -> None:
        """Put *key* into *target*'s cell, so the two of them share a tab bar."""
        if key == target or key not in self._frames or target not in self._frames:
            return
        self._hidden.discard(key)
        if lt.find(self._page, target) is None:
            # The target is not on the page (it is pinned, or floating), so there
            # is no cell to join. Better to place the block than to lose the drop.
            self.dock_block(key, len(self._rows), 0, new_row=True)
            return
        if lt.find(self._page, key) is None:
            self._detach(key)
            self.place_beside(key, target, "right")
        self._set_page(lt.merge_into(self._page, key, target))
        self._relayout()
        self._settled()

    def dock_block(self, key: str, row: int, slot: int, new_row: bool = False) -> None:
        """Dock *key* into the arrangement at (row, slot), creating a new row when
        *new_row* is set. Detaches it from its current place first."""
        self._hidden.discard(key)
        self._detach(key)
        self._place(key, DropSlot(new_row, row, slot))
        self._relayout()
        self._settled()

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
        beside = self._pin_target(key, line, slot)
        self._detach(key)
        if anchor is not None:
            self._anchors[key] = anchor
        self._place_in_region(key, beside, "bottom" if new_line or beside is None else "right")
        self._relayout()
        self._settled()

    def pin_at(self, key: str, target: str | None, side: str) -> None:
        """Pin *key* beside *target*, on the given side of it.

        The strip's own drag move, and the counterpart of :meth:`place_beside` on
        the page. A *target* of None puts the block in an empty strip.
        """
        if self._board is None:
            return
        self._sync_from_board()
        self._hidden.discard(key)
        anchor = self._anchor_for(key, self._rows)
        self._detach(key)
        if anchor is not None:
            self._anchors[key] = anchor
        self._place_in_region(key, target if target != key else None, side)
        self._relayout()
        self._settled()

    def _pin_target(self, key: str, line: int | None, slot: int) -> str | None:
        """Translate the old line/slot vocabulary into a block to land beside.

        The strip has no lines any more, but plenty of callers — an anchor
        resolving, the pin picker, the title bar's ``🖈`` — still speak in them,
        and every one of those really means "somewhere sensible in the strip".
        A line names the run of blocks it used to hold; a slot names a place in
        that run.
        """
        lines = self.pinned_lines()
        lines = [[k for k in existing if k != key] for existing in lines]
        lines = [existing for existing in lines if existing]
        if not lines:
            return None
        if line is None:
            return lines[-1][-1]
        run = lines[max(0, min(line, len(lines) - 1))]
        return run[max(0, min(slot, len(run) - 1))]

    def _place_in_region(self, key: str, target: str | None, side: str) -> None:
        """Put *key* into the strip's tree. Assumes it has been detached."""
        if self._region is None or target is None or lt.find(self._region, target) is None:
            self._region = lt.normalize(
                lt.Leaf((key,))
                if self._region is None
                else lt.Split(self._along_axis(), (self._region, lt.Leaf((key,))))
            )
            return
        self._region = lt.normalize(lt.insert_beside(self._region, key, target, side))

    def _along_axis(self) -> str:
        """The axis the strip's blocks stack along, which is its own long one."""
        return lt.VERTICAL if is_vertical_strip(self._pin_edge) else lt.HORIZONTAL

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
        self._settled()

    def unpin_all(self) -> None:
        """Empty the strip, docking every pinned block back onto the page."""
        for key in self.pinned_keys():
            self.unpin_block(key)

    def is_pinned(self, key: str) -> bool:
        return lt.find(self._region, key) is not None

    def pinned_keys(self) -> list[str]:
        """Every pinned block, in reading order."""
        return lt.keys(self._region)

    def pinned_lines(self) -> list[list[str]]:
        """The strip's blocks as the lines it used to hold them in.

        Derived, not stored. The strip is a tree now and a run of cells is only a
        "line" if you squint at it the way the old code did — but plenty of callers
        and tests still ask the question in those terms, and the answer is honest
        enough (see :func:`~mm_companion.ui.layout_tree.region_lines`).
        """
        return lt.region_lines(self._region, self._pin_edge)

    def pin_region(self) -> lt.Node | None:
        """The strip's tree — the seam a layout history or a test reads."""
        return self._region

    def pin_edge(self) -> str:
        return self._pin_edge

    def set_pin_edge(self, edge: str) -> None:
        """Park the strip on another side of the page."""
        if edge not in PIN_EDGES or edge == self._pin_edge:
            return
        self._sync_from_board()
        previous, self._pin_edge = self._pin_edge, edge
        # A strip that changed *axis* takes its blocks round with it: a column down
        # the right edge is a row along the bottom, not a column squeezed into a
        # short band. A move between two side edges is only a change of side, so the
        # shape stays and only its live pixel sizes go — they measured a layout on
        # the other side of the page.
        turned = is_vertical_strip(previous) != is_vertical_strip(edge)
        self._region = _rotate(self._region) if turned else _forget_sizes(self._region)
        # And the thickness with them: a strip dragged 600px wide down the side
        # would otherwise become a 600px-deep floor. Start from the default and let
        # the blocks push it out from there.
        self._pin_extent = DEFAULT_EXTENT
        self._render_pinned()
        if self._board is not None:
            self._board.set_extent(self._pin_extent)
        self._settled()

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

    def _keys_in(self, widget: QWidget) -> list[str]:
        """Every block rendered inside *widget* - which may be one, or a dozen.

        A pane of a splitter is a block's frame, a tab group holding several, or
        another splitter holding either; asking the frames which of them ended up
        under it answers all three without a case for each.
        """
        # Not filtered by visibility: the inactive tabs of a group are hidden and
        # are still going with it. Nothing else can match — a closed or floated
        # block's frame is parented to the canvas, never under a divider's pane.
        return [key for key, frame in self._frames.items() if self._is_at_or_within(frame, widget)]

    def _on_pane_collapsing(self, widget: QWidget, closing: bool) -> None:
        self._warn_closing(self._keys_in(widget), closing)

    def _on_pane_collapsed(self, widgets: list) -> None:
        keys: list[str] = []
        for widget in widgets:
            keys.extend(key for key in self._keys_in(widget) if key not in keys)
        self.close_blocks(keys)

    def _on_row_collapsing(self, index: int, closing: bool) -> None:
        rows = self._rows
        self._warn_closing(rows[index] if 0 <= index < len(rows) else [], closing)

    def _on_row_collapsed(self, index: int) -> None:
        rows = self._rows
        self.close_blocks(rows[index] if 0 <= index < len(rows) else [])

    def _warn_closing(self, keys: Sequence[str], closing: bool) -> None:
        """Dress the blocks a release would close, and name them under the cursor.

        The wash says *something* is about to go; the tooltip says which, which
        matters when a whole row is going at once and the blocks in it have been
        squashed too small to read their own title bars.
        """
        titles = []
        for key in keys:
            frame = self._frames.get(key)
            if frame is None:
                continue
            frame.set_closing(closing)
            titles.append(frame.base_title)
        if closing and titles:
            QToolTip.showText(QCursor.pos(), f"Release to close {', '.join(titles)}")
        elif not closing:
            QToolTip.hideText()

    def close_blocks(self, keys: Sequence[str]) -> None:
        """Close several blocks at once, as one gesture.

        :meth:`hide_block` relayouts and settles per block, which for a row
        squashed to nothing would be three redraws and three entries in the layout
        history for one flick of the wrist. This is the same bookkeeping - the
        anchor that remembers where each block was, so reopening puts it back -
        done once for all of them.
        """
        wanted = [key for key in keys if key in self._frames and key not in self._hidden]
        if not wanted:
            return
        for key in wanted:
            self._frames[key].set_closing(False)
            anchor = self._anchor_for(key, self._rows)  # before _detach mutates _rows
            self._detach(key)
            self._hidden.add(key)
            if anchor is not None:
                self._anchors[key] = anchor
        self._relayout()
        for key in wanted:
            self.block_visibility_changed.emit(key, False)
        self._settled()

    def hide_block(self, key: str) -> None:
        """Close *key* (removed from the sheet, reopenable from the View menu)."""
        self.close_blocks([key])

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
        self._settled()

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

    def adopt_drag(self, key: str, global_pos: QPoint) -> None:
        """Take over a drag another widget started, with *key* already under way.

        How a tab dragged off a Notes block becomes a block drag: the tab bar
        still holds the mouse grab, so it goes on forwarding moves and the
        release to :meth:`title_bar_moved` / :meth:`title_bar_released`, and from
        here the gesture is indistinguishable from one begun on a title bar.
        """
        self._drag_key = key
        self._press_global = global_pos
        self._drag_active = True
        frame = self._frames[key]
        # Grabbed in the middle of its title bar, which is where a hand that has
        # just dragged a tab expects the block to hang from.
        self._grab_offset = QPoint(frame.width() // 2, frame.title_bar.height() // 2)
        self.float_block(key, pos=global_pos - self._grab_offset)
        window = self._windows.get(key)
        if window is not None:
            window.move(global_pos - self._grab_offset)
        self.update_drag(global_pos)

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
        if not self.accepts_drops():
            # Compact: the block was only ever being moved, so leave it floating.
            self._end_drag()
            return
        pin_at = self._pin_hit_test(global_pos)
        # What the drag last *showed*, taken before _end_drag clears it. The drop
        # does what the highlight promised rather than asking again: re-deriving
        # it here is how the merge came to never fire at all — _end_drag had
        # already dropped `_drag_key`, which `_merge_target` needs to know whose
        # drop it is judging.
        onto = self._merge_hint
        slot = self._hit_test(global_pos) if pin_at is None else None
        self._end_drag()
        if not active:
            return
        if pin_at is not None:
            self.pin_at(key, pin_at.target, pin_at.side)
            return
        if slot is None:
            return
        if onto is not None and onto in self._frames and onto != key:
            # Merging is a move in its own right — the block goes *into* a cell
            # rather than beside one — so it does not dock first. Doing both would
            # place the block and then immediately take it somewhere else, which
            # is a visible double move and, when the dock collapsed the target's
            # row, the wrong somewhere else.
            self.merge_blocks(key, onto)
            return
        self.drop_block(key, slot)

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

    def block_menu(self, key: str) -> QMenu:
        """Everything a block can be told to do from its own title bar.

        Built rather than shown, so it can be asked what it offers without an
        event loop putting it on screen — the entries differ by where the block
        is, which is exactly the part worth being able to test.

        Every item routes to a method that already existed. The menu is not new
        behaviour; it is the first place several pieces of existing behaviour can
        be *found* without knowing they are there.
        """
        menu = QMenu(self)
        frame = self._frames.get(key)
        if frame is None:
            return menu
        floating = key in self._windows
        menu.addAction("Fit to content", lambda: self.fit_block(key))
        menu.addSeparator()
        if self.is_pinned(key):
            menu.addAction("Send back to the page", lambda: self.unpin_block(key))
        elif not floating:
            menu.addAction("Pin to the strip", lambda: self.pin_block(key))
        if floating:
            menu.addAction("Dock back on the page", lambda: self.dock_block_back(key))
            on_top = menu.addAction("Keep above other windows")
            on_top.setCheckable(True)
            on_top.setChecked(self._wants_on_top(key))
            on_top.toggled.connect(lambda wanted: self.set_block_on_top(key, wanted))
        else:
            menu.addAction("Pop out into its own window", lambda: self.float_block(key))
        menu.addSeparator()
        menu.addAction("Close", lambda: self.hide_block(key))
        return menu

    def request_menu(self, key: str, global_pos: QPoint) -> None:
        """Show :meth:`block_menu` where the right-click landed."""
        menu = self.block_menu(key)
        if not menu.isEmpty():
            menu.exec(global_pos)

    def dock_block_back(self, key: str) -> None:
        """Bring a floated block back to where it was popped out from.

        The same fallback ladder :meth:`show_block` and :meth:`unpin_block` walk —
        the anchor it left, then its default position, then a new row at the end —
        so "dock" means the same thing however the block got out.
        """
        if key not in self._windows:
            return
        slot = self._resolve_anchor(self._anchors.pop(key, None))
        if slot is None:
            slot = self._resolve_anchor(self._anchor_for(key, self._default_rows))
        if slot is None:
            slot = DropSlot(True, len(self._rows), 0)
        self._detach(key)
        self._place(key, slot)
        self._relayout()
        self._settled()

    def fit_block(self, key: str) -> None:
        """Give one block the size it reads well at, in both directions.

        Its width out of its neighbour in the row, and its row back to the height
        of its content — the two halves of "just make it right", which the
        detents could mark but nothing could actually *do*.
        """
        frame = self._frames.get(key)
        if frame is None:
            return
        widget = self.group_for(key) or frame
        parent = widget.parentWidget()
        if isinstance(parent, GridSplitter):
            parent.fit_pane(parent.indexOf(widget))
        for index, row in enumerate(self._row_widgets):
            if self._is_at_or_within(widget, row):
                self._stack.fit_row(index)
                break

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
        self._settled()

    def is_block_on_top(self, key: str) -> bool:
        """Whether block *key* stays above other applications while it is floated.

        Answers for a docked block too — as "it would" — since the choice belongs
        to the block rather than to any window it is in at the time.
        """
        return self._wants_on_top(key)

    def set_windows_suspended(self, suspended: bool) -> None:
        """Take the floated windows off the screen while the host is compact.

        The ones pinned on top stay, and be clear about what that means in
        practice: on top is the **default** (:data:`DEFAULT_ON_TOP`), so for anyone
        who has not gone out of their way this hides nothing at all. That is the
        intended reading of the two rules together — a block popped out of the app
        was popped out to sit beside something, so it goes on doing that beside the
        mini roller too, and ``✕`` is how you close one you are done with. What this
        clears is the narrower case it says: the blocks a user has explicitly sent
        behind, which are the ones not being read right now.

        The flag itself matters more than what it hides. It is what
        :meth:`accepts_drops` reads, and that guard is on whenever the host is
        compact — it is what stops a dragged block docking into a page nobody can
        see.
        """
        suspended = bool(suspended)
        if suspended == self._windows_suspended:
            return
        self._windows_suspended = suspended
        for key, window in self._windows.items():
            if self._wants_on_top(key):
                continue
            window.setVisible(not suspended)
        if suspended:
            # A drag in flight when the window shrank would be aiming at a page
            # that is no longer there; see :meth:`accepts_drops`.
            self._end_drag()

    def accepts_drops(self) -> bool:
        """Whether a dragged block may land on the page or the pinned strip now.

        Not while the host is compact. The page and the strip are hidden behind
        the mini roller, but **a hidden widget keeps its last geometry**, so a hit
        test still happily reports a slot under the cursor — and the block docks
        into a page nobody can see and is simply gone, with no way to get it back
        but the View menu. While compact, dragging a floated block just moves it.
        """
        return not self._windows_suspended

    @staticmethod
    def _start_distance() -> int:
        from PySide6.QtWidgets import QApplication

        return QApplication.startDragDistance()

    def _show_merge(self, key: str | None) -> None:
        """Wash the frame a drop would merge into, and clear the last one."""
        if key == self._merge_hint:
            return
        if self._merge_hint is not None and self._merge_hint in self._frames:
            self._frames[self._merge_hint].set_merge_target(False)
        self._merge_hint = key
        if key is not None:
            self._frames[key].set_merge_target(True)

    def _end_drag(self) -> None:
        self._show_merge(None)
        self._drag_key = None
        self._drag_active = False
        self._stop_autoscroll()
        self._indicator.hide_indicator()
        self._region_mark.hide_indicator()
        if self._board is not None:
            self._board.hide_drop()

    def update_drag(self, global_pos: QPoint) -> None:
        """Refresh the drop indicator and edge auto-scroll for a cursor position."""
        if not self.accepts_drops():
            # No insert line and no auto-scroll either: both would be promising a
            # landing place that the drop itself is going to refuse.
            self._indicator.hide_indicator()
            self._region_mark.hide_indicator()
            self._stop_autoscroll()
            if self._board is not None:
                self._board.hide_drop()
            return
        pin_at = self._pin_hit_test(global_pos)
        if pin_at is not None:
            # Over the strip: it owns the feedback, and the page shows none — two
            # insert lines at once would be a lie about where the block lands.
            #
            # And the page stops scrolling. The velocity outlives the cursor leaving
            # the page, and :meth:`_autoscroll_tick` calls straight back in here — so
            # an early return that does not clear it leaves the page scrolling under a
            # gesture that has gone, until the drop. The hot band is measured from the
            # viewport's *y* alone, so the strip beside it shares the band: dragging
            # up the right-hand edge into the strip is exactly where this fired.
            self._indicator.hide_indicator()
            self._region_mark.hide_indicator()
            self._stop_autoscroll()
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

    #: The share of a block, in each dimension, that means "merge into this"
    #: rather than "sit beside it" — the middle ninth. A share and not a pixel
    #: inset, because the four placements around it have to stay reachable on a
    #: block of any size; see :func:`drop_side`.
    _MERGE_SHARE = 1 / 3

    #: The band above and below a whole row where a drop makes a *new* row. Still
    #: a pixel band, and deliberately: it guards the *seam* between two rows,
    #: which is a line-shaped target and is marked by a line.
    _GAP = 12

    def _hit_test(self, global_pos: QPoint) -> DropSlot | None:
        """What a drop at *global_pos* would do, or None if it is off the page.

        Four answers, from the middle of a block outwards: merge into it, stack
        above or below it, sit beside it, or — past the row entirely — start a new
        row. The old page could only say the last two, because a row was the only
        container there was.
        """
        if self._scroll_area is not None:
            viewport = self._scroll_area.viewport()
            if not viewport.rect().contains(viewport.mapFromGlobal(global_pos)):
                return None

        rows = self._row_widgets
        if not rows:
            return DropSlot(True, 0, 0)

        point = self.mapFromGlobal(global_pos)
        geoms = [self._row_geometry(row) for row in rows]
        for index, geo in enumerate(geoms):
            if geo.top() + self._GAP <= point.y() <= geo.bottom() - self._GAP:
                return self._slot_in_row(index, rows[index], global_pos)

        # Not inside any row's core → a new row at the nearest boundary.
        boundaries = [geoms[0].top()]
        boundaries += [(geoms[i - 1].bottom() + geoms[i].top()) / 2 for i in range(1, len(geoms))]
        boundaries.append(geoms[-1].bottom())
        nearest = min(range(len(boundaries)), key=lambda b: abs(point.y() - boundaries[b]))
        return DropSlot(True, nearest, 0)

    def _row_geometry(self, row: QWidget) -> QRect:
        """*row*'s rectangle in canvas coordinates.

        Mapped through the screen rather than with ``mapTo``: a row is several
        widgets deep in the stack now, and ``mapTo`` warns and returns nonsense the
        moment the two are not actually in one hierarchy — which happens for a
        frame caught mid-rehoming.
        """
        return QRect(self.mapFromGlobal(row.mapToGlobal(QPoint(0, 0))), row.size())

    def _slot_in_row(self, index: int, row: QWidget, global_pos: QPoint) -> DropSlot:
        """What a drop inside *row* means, judged against the block it is over."""
        frame = self._frame_under(row, global_pos)
        if frame is None or frame.key == self._drag_key:
            keys = self._rows[index] if index < len(self._rows) else []
            return DropSlot(False, index, len(keys))

        local = frame.mapFromGlobal(global_pos)
        rect = frame.rect()
        side = drop_side(local, rect, merge_share=self._MERGE_SHARE)
        if side is None:
            onto = self._merge_target(frame.key)
            if onto is not None:
                return DropSlot(False, index, 0, onto=onto, target=frame.key, side="right")
            # Nothing to merge into (a block over itself). The middle then means
            # the nearer side, so the gesture still lands somewhere sensible
            # rather than being refused for a reason nobody can see.
            side = "left" if local.x() < rect.center().x() else "right"
        return DropSlot(False, index, 0, target=frame.key, side=side)

    def _frame_under(self, row: QWidget, global_pos: QPoint) -> BlockFrame | None:
        """The block frame under the pointer inside *row*, if any.

        ``row`` may *be* the frame: a row holding one block is that block, with no
        wrapper around it (see :func:`~mm_companion.ui.grid_view.build_node`). That
        is why the test is "at or under" and not "under" — searching only the
        descendants found nothing for every single-block row on the page, which is
        most of them, and quietly made those blocks impossible to drop onto.

        Only visible frames count, which is what keeps a tab group honest: its
        hidden members are behind a ``QStackedWidget``, so the one the pointer is
        really over is the only one that can answer.
        """
        for frame in self._frames.values():
            if not frame.isVisible() or not self._is_at_or_within(frame, row):
                continue
            if frame.rect().contains(frame.mapFromGlobal(global_pos)):
                return frame
        return None

    @staticmethod
    def _is_at_or_within(widget: QWidget, ancestor: QWidget) -> bool:
        parent: QWidget | None = widget
        while parent is not None:
            if parent is ancestor:
                return True
            parent = parent.parentWidget()
        return False

    def _merge_target(self, key: str) -> str | None:
        """Whether a drop onto *key* really is a merge.

        Every block takes every merge now — a tab group holds whole blocks, so
        there is nothing for a block to have an opinion about. The check that is
        left is only that a block is not being merged into itself.
        """
        if self._drag_key is None or key == self._drag_key:
            return None
        return key if key in self._frames else None

    def _show_indicator(self, slot: DropSlot | None) -> None:
        """Put up the two marks a drop deserves: the seam, and the room it takes.

        Three states, and between them they are what makes the placements
        discoverable. Over the middle of a block the whole frame washes — *this
        block and the dragged one become tabs*. Over a side, half the block washes
        and a line sits on the seam — *the newcomer takes that half*. Over the gap
        between rows, the line alone — a new row has no room marked out for it
        until it exists. Dragging across one block therefore shows all three in
        turn, which is the only way anybody was ever going to find the merge.
        """
        if slot is not None and slot.onto is not None:
            # A merge has no *place* to mark, so the target itself is dressed:
            # an insert line here would promise the block lands beside it.
            self._indicator.hide_indicator()
            self._region_mark.hide_indicator()
            self._show_merge(slot.onto)
            return
        self._show_merge(None)
        if slot is None:
            self._indicator.hide_indicator()
            self._region_mark.hide_indicator()
            return
        region = self._region_rect(slot)
        if region is None:
            self._region_mark.hide_indicator()
        else:
            self._region_mark.move_to(region)
        rect = self._indicator_rect(slot)
        if rect is None:
            self._indicator.hide_indicator()
            return
        self._indicator.move_to(rect)

    def _region_rect(self, slot: DropSlot) -> QRect | None:
        """The space the dragged block would take, in canvas coordinates.

        Half of the block it is dropped beside, on the named side — which is
        exactly what ``insert_beside`` does to the tree, so the mark is a promise
        the drop keeps. None for a new row (there is nothing to halve yet) and for
        a merge (the target's own wash says the block takes all of it).
        """
        if slot.new_row or slot.target is None or slot.onto is not None:
            return None
        frame = self._frames.get(slot.target)
        if frame is None or not frame.isVisible():
            return None
        geo = self._row_geometry(frame)
        if slot.side == "left":
            return QRect(geo.left(), geo.top(), max(1, geo.width() // 2), geo.height())
        if slot.side == "right":
            half = max(1, geo.width() // 2)
            return QRect(geo.right() - half + 1, geo.top(), half, geo.height())
        if slot.side == "top":
            return QRect(geo.left(), geo.top(), geo.width(), max(1, geo.height() // 2))
        half = max(1, geo.height() // 2)
        return QRect(geo.left(), geo.bottom() - half + 1, geo.width(), half)

    def _indicator_rect(self, slot: DropSlot) -> QRect | None:
        """Where the insert line goes for *slot*, in canvas coordinates.

        A line *across* the page for a new row, and one along the edge of the
        block the drop names for everything else — so the mark is always the seam
        the block will actually land on.
        """
        thickness = max(2, int(theme.metric("grid.handle")))
        rows = self._row_widgets
        if slot.new_row or slot.target is None:
            if not rows:
                return QRect(0, 0, max(1, self.width()), thickness)
            index = max(0, min(slot.row, len(rows) - 1))
            geo = self._row_geometry(rows[index])
            y = geo.top() if slot.new_row and slot.row <= index else geo.bottom()
            return QRect(geo.left(), y - thickness // 2, geo.width(), thickness)

        frame = self._frames.get(slot.target)
        if frame is None or not frame.isVisible():
            return None
        geo = self._row_geometry(frame)
        if slot.side == "left":
            return QRect(geo.left() - thickness // 2, geo.top(), thickness, geo.height())
        if slot.side == "right":
            return QRect(geo.right() - thickness // 2, geo.top(), thickness, geo.height())
        if slot.side == "top":
            return QRect(geo.left(), geo.top() - thickness // 2, geo.width(), thickness)
        return QRect(geo.left(), geo.bottom() - thickness // 2, geo.width(), thickness)

    # -- auto-scroll ---------------------------------------------------------

    _HOT = 40  # px band at the viewport edges that triggers auto-scroll

    def _edge_velocity(self, global_pos: QPoint) -> int:
        """How fast the page should scroll for a pointer at *global_pos*, or zero.

        Shared by the two gestures that need it — dragging a *block* near the
        edge of the page, and dragging a row *grip* there — because they want the
        identical curve and had no business being two of them.
        """
        if self._scroll_area is None:
            return 0
        viewport = self._scroll_area.viewport()
        return edge_velocity(viewport.mapFromGlobal(global_pos).y(), viewport.height(), self._HOT)

    def _stop_autoscroll(self) -> None:
        """Stand the edge auto-scroll down, wherever the gesture has got to."""
        self._autoscroll_velocity = 0
        self._autoscroll_timer.stop()

    def _maybe_autoscroll(self, global_pos: QPoint) -> None:
        velocity = self._edge_velocity(global_pos)
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

    # -- auto-scroll while a row grip is being dragged ------------------------

    def _stop_grip_autoscroll(self) -> None:
        self._grip_velocity = 0
        self._grip_timer.stop()

    def _maybe_grip_autoscroll(self, global_pos: QPoint) -> None:
        """Follow a row grip dragged into the band at the edge of the window."""
        velocity = self._edge_velocity(global_pos)
        self._grip_velocity = velocity
        if velocity and not self._grip_timer.isActive():
            self._grip_timer.start()
        elif not velocity and self._grip_timer.isActive():
            self._grip_timer.stop()

    def _grip_autoscroll_tick(self) -> None:
        """Grow the row, *then* scroll — in that order, and it matters.

        A row dragged past the bottom of the window is usually already at the end
        of the page's scroll range, so scrolling first would move nothing and the
        drag would simply stop. Extending the row makes the page taller, which is
        what gives the scrollbar somewhere to go; scrolling by the same amount
        afterwards keeps the grip exactly under the pointer.
        """
        if not self._grip_velocity or self._scroll_area is None:
            self._grip_timer.stop()
            return
        self._stack.extend_active_drag(self._grip_velocity)
        bar = self._scroll_area.verticalScrollBar()
        bar.setValue(bar.value() + self._grip_velocity)

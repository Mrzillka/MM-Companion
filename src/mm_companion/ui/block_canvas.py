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

from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from PySide6.QtCore import (
    QAbstractAnimation,
    QEasingCurve,
    QPoint,
    QPropertyAnimation,
    QRect,
    QSize,
    QTimer,
    Signal,
)
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import (
    QAbstractScrollArea,
    QGraphicsOpacityEffect,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from mm_companion.ui import layout_tree as lt
from mm_companion.ui import theme
from mm_companion.ui.block_frame import BlockFrame, BlockWindow
from mm_companion.ui.block_sizes import UNBOUNDED, RecommendedSize
from mm_companion.ui.blocks.base import instance_template
from mm_companion.ui.drop_feedback import DropIndicator
from mm_companion.ui.grid_view import RowStack, build_node
from mm_companion.ui.pinned import (
    DEFAULT_ALIGN,
    DEFAULT_EDGE,
    DEFAULT_EXTENT,
    PIN_ALIGNMENTS,
    PIN_EDGES,
    PinSlot,
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

        # Only the containers we made are shed. A row holding a single block *is*
        # that block's frame — build_node returns the frame itself for a lone leaf
        # — and discarding one would destroy a live block rather than a wrapper.
        for row in old_rows:
            if not isinstance(row, BlockFrame):
                discard_widget(row)

        if self._layout.indexOf(self._stack) < 0:
            self._layout.addWidget(self._stack)
        self._indicator.raise_()

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

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt override
        """The page's own shape rule: as tall as its rows, as narrow as you like.

        This is the whole of "width tiles, height scrolls", and it is the *inverse*
        of what the canvas used to say. It used to report the widest row's content
        as a minimum width, which held the window open; it reports almost none now,
        so every row can be dragged narrow and its blocks reflow. The height goes
        the other way: stating the rows' full height is what makes the page
        overflow the viewport and scroll, rather than squashing every row to fit a
        window nobody sized for them.
        """
        return QSize(int(theme.metric("block.min-extent")), self.sizeHint().height())

    def _watch_splitters(self, widget: QWidget) -> None:
        """Follow every divider under *widget*, so a drag becomes a settled gesture."""
        if isinstance(widget, QSplitter):
            widget.sizesSettled.connect(self._on_sizes_settled)
            for index in range(widget.count()):
                self._watch_splitters(widget.widget(index))

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
            "instances": [],
            "page": lt.to_dict(lt.rows_to_page(rows)),
            "region": {
                "edge": DEFAULT_EDGE,
                "align": DEFAULT_ALIGN,
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
                "align": self._pin_align,
                "extent": self._pin_extent,
                "root": lt.to_dict(lt.lines_to_region(self.pinned_lines(), self._pin_edge)),
            },
            "floating": {key: self._window_geometry(key) for key in self._windows},
            "hidden": sorted(self._hidden),
            # Kept for a block that is *off* the page — hidden or pinned — since
            # both come back through the same anchor.
            "hidden_anchors": {
                key: anchor.to_dict()
                for key, anchor in self._anchors.items()
                if key in self._hidden or self.is_pinned(key)
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
        edge, align, extent, region_root = region

        for key in list(self._windows):
            self._destroy_window(key)
        # A restored layout is authoritative about what stays on top; only
        # *moving* a block keeps that choice across a dock (see set_block_on_top).
        self._on_top.clear()
        was_hidden = self._hidden
        self._set_page(page)
        self._hidden = set(hidden)
        self._anchors = anchors
        self._pinned = lt.region_lines(region_root, edge)
        self._pin_edge = edge
        self._pin_align = align
        # The strip's own proportions do not survive the move to a tree: they
        # described a layout engine that no longer exists, and a wrong remembered
        # size is worse than none. Its blocks lay themselves out once, from their
        # own hints, and the next drag is remembered properly.
        self._pin_sizes = []
        self._pin_line_sizes = []
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
        align = raw_region.get("align", DEFAULT_ALIGN)
        if align not in PIN_ALIGNMENTS:
            return None

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

        return page, (edge, align, extent, region), floating, list(hidden), anchors

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
        self._detach(key)
        self._hidden.discard(key)

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
        self._settled()

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
        self._settled()

    def set_pin_align(self, align: str) -> None:
        """Set how pinned blocks sit across the strip (fill / start / center / end)."""
        if align not in PIN_ALIGNMENTS or align == self._pin_align:
            return
        self._sync_from_board()
        self._pin_align = align
        self._render_pinned()
        self._settled()

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
        self._settled()

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
            self.pin_block(key, pin_at.line, pin_at.slot, new_line=pin_at.new_line)
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
        if self._board is not None:
            self._board.hide_drop()

    def update_drag(self, global_pos: QPoint) -> None:
        """Refresh the drop indicator and edge auto-scroll for a cursor position."""
        if not self.accepts_drops():
            # No insert line and no auto-scroll either: both would be promising a
            # landing place that the drop itself is going to refuse.
            self._indicator.hide_indicator()
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

    #: How far inside a block's edges the pointer has to be for a drop to mean
    #: "merge into this" rather than "sit beside it". The outer bands keep the
    #: ordinary placement, so a block can always be put *next* to another.
    _MERGE_INSET = 28

    #: How wide the band along a block's edge is that means "and above/below it"
    #: rather than "and beside it". Only the top and bottom bands are needed: a
    #: drop that is neither a merge nor a stack is a placement to the left or the
    #: right, which is the answer everywhere else in the block.
    _STACK_BAND = 24

    #: The band above and below a whole row where a drop makes a *new* row.
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
        inner = rect.adjusted(
            self._MERGE_INSET, self._MERGE_INSET, -self._MERGE_INSET, -self._MERGE_INSET
        )
        onto = self._merge_target(frame.key)
        if onto is not None and inner.isValid() and inner.contains(local):
            return DropSlot(False, index, 0, onto=onto, target=frame.key, side="right")

        # The stack bands sit *inside* the row's core, which is already inset by
        # _GAP. Measuring them from the frame's own edge would put them under the
        # band that means "a new row" — the pointer can never be there — and a
        # block could only ever be stacked by accident.
        band = min(self._STACK_BAND, max(1, rect.height() // 3))
        top_band = rect.top() + self._GAP + band
        bottom_band = rect.bottom() - self._GAP - band
        if local.y() < top_band:
            side = "top"
        elif local.y() > bottom_band:
            side = "bottom"
        else:
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
        if slot is not None and slot.onto is not None:
            # A merge has no *place* to mark, so the target itself is dressed:
            # an insert line here would promise the block lands beside it.
            self._indicator.hide_indicator()
            self._show_merge(slot.onto)
            return
        self._show_merge(None)
        if slot is None:
            self._indicator.hide_indicator()
            return
        rect = self._indicator_rect(slot)
        if rect is None:
            self._indicator.hide_indicator()
            return
        self._indicator.move_to(rect)

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

    def _stop_autoscroll(self) -> None:
        """Stand the edge auto-scroll down, wherever the gesture has got to."""
        self._autoscroll_velocity = 0
        self._autoscroll_timer.stop()

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

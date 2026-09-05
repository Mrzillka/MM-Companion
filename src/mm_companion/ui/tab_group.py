"""Several blocks sharing one cell, behind a tab bar.

What a :class:`~mm_companion.ui.layout_tree.Leaf` holding more than one key looks
like. Merging is a drop into the middle of a block, and this is what the two of
them become: one cell of the grid, one set of title-bar buttons, and a tab per
block.

The whole thing is a **reuse of the frames**, not a second kind of block. Each
member keeps its own :class:`~mm_companion.ui.block_frame.BlockFrame` — its
section, its size, its lock state, its live caption — and only lends its title bar
to the group for as long as it is in one. That is why a block behaves identically
inside a group and out of it: it *is* the same widget, with one row of chrome
hidden.

Dragging a tab off the bar takes that block back out, through the same gesture
(:mod:`mm_companion.ui.tab_drag`) and the same drag controller as a title bar, so
the block that leaves can dock, stack, pin, float or merge somewhere else without
this module knowing any of it happened.

The **strip of bar to the right of the last tab** is the group's own handle
(:class:`_GroupHandle`), and it is to a group what a title bar is to a block:
drag it and the whole cell moves, right-click it and the group's menu opens. The
two gestures cannot be confused because they are on different widgets — a tab is
a tab, and everything past the last one is the cell.

One thing a group deliberately cannot do is **float**. A block dragged by its
title bar tears out into a window that follows the cursor; a group being moved
stays where it is and the page marks where it will land. That is not an omission
waiting to be filled in: a ``BlockWindow`` hosts one block, ``floating`` in a
saved layout is a geometry per block key, and "a tab group in a window of its own"
would be a fourth place a block can live and a schema to go with it. Dragging one
tab out is how a member of a group gets into a window, which is the same gesture
it always was.
"""

from __future__ import annotations

from PySide6.QtCore import QPoint, QSignalBlocker, QSize, Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QSizePolicy,
    QStackedWidget,
    QTabBar,
    QVBoxLayout,
    QWidget,
)

from mm_companion.ui import theme
from mm_companion.ui.block_frame import BlockFrame, DragHost
from mm_companion.ui.block_sizes import RecommendedSize
from mm_companion.ui.tab_drag import TabSplitGesture

#: Longest tab caption before it elides. A tab bar that can grow without bound is
#: a cell that can never be dragged narrow, which is the one thing the grid may
#: not allow.
MAX_TAB_CHARS = 18


class TabGroupFrame(QFrame):
    """One grid cell holding several blocks, one showing at a time.

    Renders as a tab bar with the active block's pin/float/close buttons at the
    right of it, over the active block's frame. It answers the same few questions
    a :class:`BlockFrame` does — :meth:`recommended_size`, :meth:`set_locked` — so
    the splitters, the detents and the sheet can hold one without knowing which
    they have.
    """

    #: A tab was dragged clear of the bar: (block key, where the pointer was).
    splitRequested = Signal(str, QPoint)
    splitMoved = Signal(QPoint)
    splitReleased = Signal(QPoint)
    #: The user brought a different tab to the front.
    activeChanged = Signal(str)
    #: The whole cell is being dragged by its handle: pressed, moved, released.
    #: The canvas drives these exactly as it drives a title bar's.
    groupDragStarted = Signal(QPoint)
    groupDragMoved = Signal(QPoint)
    groupDragReleased = Signal(QPoint)
    #: Right-clicked: on a tab (that block's menu) or on the handle (the group's).
    tabMenuRequested = Signal(str, QPoint)
    groupMenuRequested = Signal(QPoint)

    def __init__(
        self,
        frames: list[BlockFrame],
        active: int,
        host: DragHost,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("blockFrame")  # dressed as one block, because it is one cell
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self._frames = list(frames)
        self._host = host

        self._bar = QTabBar()
        self._bar.setObjectName("blockTabBar")
        self._bar.setDrawBase(False)
        self._bar.setExpanding(False)
        self._bar.setMovable(True)
        self._bar.setUsesScrollButtons(True)
        # An eliding tab bar, for the same reason a block's title elides: a long
        # caption must never become a width the cell can no longer be dragged under.
        self._bar.setElideMode(Qt.TextElideMode.ElideRight)
        self._bar.currentChanged.connect(self._on_current_changed)

        self._stack = QStackedWidget()
        self._stack.setObjectName("blockTabStack")

        # The bar takes only the room its tabs need, and the handle takes the
        # rest. It used to be the bar that stretched, which left the strip past
        # the last tab belonging to the QTabBar — so a press there was a press on
        # a bar with no tab under it, and telling that apart from a tab drag would
        # have been a rule about coordinates rather than about widgets.
        self._handle = _GroupHandle(self)
        header = QWidget()
        header_row = QHBoxLayout(header)
        header_row.setContentsMargins(0, 0, 0, 0)
        header_row.setSpacing(0)
        header_row.addWidget(self._bar)
        header_row.addWidget(self._handle, stretch=1)
        self._buttons = _GroupButtons(self, host)
        header_row.addWidget(self._buttons)

        column = QVBoxLayout(self)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(0)
        column.addWidget(header)
        column.addWidget(self._stack, stretch=1)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        for frame in self._frames:
            # The member lends its title bar to the group for as long as it is in
            # one: two rows of chrome for one cell is one too many, and the group's
            # buttons act on whichever block is showing anyway.
            frame.set_tabbed(True)
            self._stack.addWidget(frame)
            self._bar.addTab(_elide(frame.base_title))
            frame.title_bar.set_title(frame.title)  # keep its own caption current
        self._bar.setCurrentIndex(max(0, min(active, len(self._frames) - 1)))

        self._gesture = TabSplitGesture(
            self._bar,
            begin=self._begin_split,
            moved=self.splitMoved.emit,
            released=self.splitReleased.emit,
        )
        self._bar.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._bar.customContextMenuRequested.connect(self._tab_menu_at)

    # -- what the grid asks of any cell --------------------------------------

    @property
    def keys(self) -> list[str]:
        return [frame.key for frame in self._frames]

    @property
    def key(self) -> str:
        """The block a gesture on this cell's chrome means: the one showing."""
        return self.active_key()

    def active_key(self) -> str:
        index = max(0, min(self._bar.currentIndex(), len(self._frames) - 1))
        return self._frames[index].key

    def active_index(self) -> int:
        return max(0, min(self._bar.currentIndex(), len(self._frames) - 1))

    def recommended_size(self) -> RecommendedSize:
        """The roomiest of the group's members, in each dimension separately.

        A tab group is as big as the most demanding thing in it: sizing it to the
        block showing would make the cell jump every time a tab was clicked, which
        is exactly the sort of thing a layout must never do on its own.
        """
        widths = [frame.recommended_size().width for frame in self._frames]
        heights = [frame.recommended_size().height for frame in self._frames]
        return RecommendedSize(max(widths, default=0), max(heights, default=0))

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt override
        """``block.min-extent`` and a header, and nothing about what is in it.

        The same answer :meth:`~mm_companion.ui.block_frame.BlockFrame.minimumSizeHint`
        gives, and it has to be: a cell holding one block could be dragged to a
        sliver and the *same* cell holding two refused at whatever its tab bar and
        buttons happened to add up to — which was about 218px, and meant a group
        could never be squashed, never be closed by being squashed, and behaved
        unlike every other cell on a page whose whole premise is that how small a
        thing gets is the user's call.

        Inherited from the layout before this, which is to say it was a minimum
        its own content decided — the one thing nothing on the page may report.
        Past this the header clips, exactly as a squashed block's title bar does.
        """
        extent = int(theme.metric("block.min-extent"))
        chrome = self._bar.minimumSizeHint().height() + 2 * self.frameWidth()
        return QSize(extent, chrome + extent)

    def release(self, key: str) -> BlockFrame | None:
        """Take *key* out of the group and hand its frame back, title bar restored.

        The bar is silenced while the tab goes, and that is a fix rather than
        tidiness. Removing a tab makes Qt pick another one and announce it, which
        this reports as ``activeChanged`` — indistinguishable, from the canvas's
        side, from the user clicking a tab. So dissolving a group raised a second
        "one gesture finished", and a menu click that took three blocks apart
        landed in the layout history twice. Which tab a *surviving* group shows is
        decided by the tree (``layout_tree.remove`` keeps the user looking at what
        they were looking at) and re-read when it is rebuilt, so there is nothing
        here worth announcing either way.
        """
        for index, frame in enumerate(self._frames):
            if frame.key != key:
                continue
            self._frames.pop(index)
            blocker = QSignalBlocker(self._bar)
            self._bar.removeTab(index)
            del blocker
            self._stack.removeWidget(frame)
            frame.set_tabbed(False)
            return frame
        return None

    def set_active_index(self, index: int) -> None:
        """Show tab *index* because the **tree** says so, not because it was clicked.

        A group is kept across a rebuild (it owns its members' frames, and remaking
        one would take live blocks with it), so the tab it happens to be showing is
        the only thing about it a rebuild does not re-read. That left the one
        arrangement the tree records about a group — which tab is active — unable to
        be restored into a group that already existed: undoing a tab click, or
        applying a saved layout over a live page, moved nothing at all.

        Silent for the same reason :meth:`release` is. ``setCurrentIndex`` makes Qt
        announce the change, which this reports as ``activeChanged`` and the canvas
        cannot tell from a click — so restoring a tab would write itself back into
        the tree and land in the layout history as a fresh gesture. The stack and
        the buttons are moved by hand instead, which is all
        :meth:`_on_current_changed` would have done.
        """
        if not 0 <= index < len(self._frames) or index == self._bar.currentIndex():
            return
        blocker = QSignalBlocker(self._bar)
        self._bar.setCurrentIndex(index)
        del blocker
        self._stack.setCurrentWidget(self._frames[index])
        self._buttons.follow(self._frames[index])

    def set_locked(self, locked: bool) -> None:
        for frame in self._frames:
            frame.set_locked(locked)

    def refresh_titles(self) -> None:
        """Re-read each member's live caption onto its tab."""
        for index, frame in enumerate(self._frames):
            self._bar.setTabText(index, _elide(frame.base_title))
            self._bar.setTabToolTip(index, frame.title)

    # -- internals ------------------------------------------------------------

    def _on_current_changed(self, index: int) -> None:
        if 0 <= index < len(self._frames):
            self._stack.setCurrentIndex(index)
            self._buttons.follow(self._frames[index])
            self.activeChanged.emit(self._frames[index].key)

    def _tab_menu_at(self, point: QPoint) -> None:
        """Right-click on a tab: that block's own menu, wherever the block is.

        Falls through to the group's menu past the last tab, which cannot normally
        happen — the handle owns that strip — but a bar narrow enough to be
        scrolling its tabs can still report a click on nothing.
        """
        index = self._bar.tabAt(point)
        where = self._bar.mapToGlobal(point)
        if 0 <= index < len(self._frames):
            self.tabMenuRequested.emit(self._frames[index].key, where)
            return
        self.groupMenuRequested.emit(where)

    def _begin_split(self, index: int, global_pos: QPoint) -> bool:
        if not 0 <= index < len(self._frames) or len(self._frames) < 2:
            # A cell's only tab *is* that cell: dragging it out would leave an
            # empty group behind and a new cell holding what the old one held.
            return False
        self.splitRequested.emit(self._frames[index].key, global_pos)
        return True


class _GroupHandle(QFrame):
    """The strip past the last tab: a group's drag handle and menu button.

    What a :class:`~mm_companion.ui.block_frame.TitleBar` is to a block, minus the
    caption (the tabs are the caption) and the buttons (they are next door). Left-
    drag moves the whole cell; right-click opens the group's menu.

    A widget of its own rather than a rule about where on the tab bar the pointer
    is, because "a tab" and "not a tab" then stop being coordinates and start being
    two different things that can each answer for themselves.
    """

    def __init__(self, group: TabGroupFrame) -> None:
        super().__init__(group)
        self._group = group
        self.setObjectName("blockTabHandle")
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.setToolTip("Drag to move these blocks together; right-click for more")

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt override
        """Never narrower than something you can put a pointer on.

        The bar takes the room its tabs want and this takes the rest, so on a
        narrow cell with several tabs there would otherwise be no rest — and a
        group would quietly become the one cell on the page that cannot be moved.
        The bar is the one that gives way instead; it already elides its captions
        and scrolls them, which is exactly what that is for. ``block.min-extent``
        is the same "you have to be able to grab it" number the frame's own floor
        is, and it is read here rather than kept, so a preset can change it.
        """
        return QSize(int(theme.metric("block.min-extent")), 0)

    def mousePressEvent(self, event) -> None:  # noqa: ANN001, N802 - Qt override
        if event.button() != Qt.MouseButton.LeftButton:
            super().mousePressEvent(event)
            return
        self._group.groupDragStarted.emit(event.globalPosition().toPoint())
        event.accept()

    def mouseMoveEvent(self, event) -> None:  # noqa: ANN001, N802 - Qt override
        if not event.buttons() & Qt.MouseButton.LeftButton:
            super().mouseMoveEvent(event)
            return
        self._group.groupDragMoved.emit(event.globalPosition().toPoint())
        event.accept()

    def mouseReleaseEvent(self, event) -> None:  # noqa: ANN001, N802 - Qt override
        if event.button() != Qt.MouseButton.LeftButton:
            super().mouseReleaseEvent(event)
            return
        self._group.groupDragReleased.emit(event.globalPosition().toPoint())
        event.accept()

    def contextMenuEvent(self, event) -> None:  # noqa: ANN001, N802 - Qt override
        self._group.groupMenuRequested.emit(event.globalPos())
        event.accept()


class _GroupButtons(QWidget):
    """The pin / float / close buttons at the right of a group's tab bar.

    They act on the block *showing*, which is the only reading that makes sense:
    a tab group is not a thing to be pinned or popped out, its members are.
    """

    def __init__(self, group: TabGroupFrame, host: DragHost) -> None:
        super().__init__(group)
        self._host = host
        self._key: str | None = None
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, int(theme.metric("space.sm")), 0)
        row.setSpacing(int(theme.metric("space.xxs")))
        self._buttons = {
            "pin": _tool("🖈", "Pin the block showing to the fixed strip"),
            "float": _tool("↗", "Pop the block showing out into its own window"),
            "close": _tool("✕", "Hide the block showing (reopen from the View menu)"),
        }
        self._buttons["pin"].clicked.connect(lambda: self._act(host.request_pin))
        self._buttons["float"].clicked.connect(lambda: self._act(host.request_float))
        self._buttons["close"].clicked.connect(lambda: self._act(host.request_hide))
        for button in self._buttons.values():
            row.addWidget(button)

    def follow(self, frame: BlockFrame) -> None:
        self._key = frame.key

    def _act(self, action) -> None:  # noqa: ANN001 - a bound host method
        if self._key is not None:
            action(self._key)


def _tool(glyph: str, tip: str):
    from PySide6.QtWidgets import QToolButton

    button = QToolButton()
    button.setText(glyph)
    button.setAutoRaise(True)
    button.setToolTip(tip)
    button.setCursor(Qt.CursorShape.ArrowCursor)
    return button


def _elide(text: str) -> str:
    return text if len(text) <= MAX_TAB_CHARS else text[: MAX_TAB_CHARS - 1] + "…"


__all__ = ["MAX_TAB_CHARS", "TabGroupFrame"]

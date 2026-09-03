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

One honest limitation: there is no gesture for moving a *whole* group at once —
each tab is dragged out on its own. A group of one collapses back into a plain
block, so nothing gets stuck; it is simply more clicks than it might be.
"""

from __future__ import annotations

from PySide6.QtCore import QPoint, QSize, Qt, Signal
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

        header = QWidget()
        header_row = QHBoxLayout(header)
        header_row.setContentsMargins(0, 0, 0, 0)
        header_row.setSpacing(0)
        header_row.addWidget(self._bar, stretch=1)
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
        """Take *key* out of the group and hand its frame back, title bar restored."""
        for index, frame in enumerate(self._frames):
            if frame.key != key:
                continue
            self._frames.pop(index)
            self._bar.removeTab(index)
            self._stack.removeWidget(frame)
            frame.set_tabbed(False)
            return frame
        return None

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

    def _begin_split(self, index: int, global_pos: QPoint) -> bool:
        if not 0 <= index < len(self._frames) or len(self._frames) < 2:
            # A cell's only tab *is* that cell: dragging it out would leave an
            # empty group behind and a new cell holding what the old one held.
            return False
        self.splitRequested.emit(self._frames[index].key, global_pos)
        return True


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

"""One character-sheet block: a section wrapped in a draggable frame.

Each block is a :class:`BlockFrame` — a title bar (the drag handle, plus pin,
float and close buttons) above one of the ``sections`` widgets. The frame never
wraps its section in a scroll area, so the block is sized to its content and
never scrolls on its own; the whole sheet scrolls as one page instead (see
:class:`~mm_companion.ui.block_canvas.BlockCanvas`).

A frame lives in one of three places: inside the canvas, inside the pinned strip
that doesn't scroll with the page (see
:class:`~mm_companion.ui.pinned_panel.PinnedPanel`), or — when floated out —
inside a :class:`BlockWindow` (a top-level window). Dragging the title bar runs
the same gesture wherever it is, driven by the canvas's drag controller, so
float-out, reorder, pin, and drag-back-to-dock are one interaction.

The frame is deliberately dumb: it forwards title-bar mouse events and button
clicks to a *controller* (the :class:`BlockCanvas`) and applies its size
constraints. All arrangement logic lives in the canvas.
"""

from __future__ import annotations

from typing import Protocol

from PySide6.QtCore import QEvent, QPoint, QSize, Qt, QTimer
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QScrollArea,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from mm_companion.ui import theme
from mm_companion.ui.block_sizes import UNBOUNDED, BlockSize
from mm_companion.ui.frameless import apply_window_flags, describe_on_top, size_grip_row
from mm_companion.ui.widgets import ElidingLabel


class DragHost(Protocol):
    """What a :class:`TitleBar` needs from its controller (the canvas)."""

    def title_bar_pressed(self, key: str, global_pos: QPoint) -> None: ...
    def title_bar_moved(self, key: str, global_pos: QPoint) -> None: ...
    def title_bar_released(self, key: str, global_pos: QPoint) -> None: ...
    def request_float(self, key: str) -> None: ...
    def request_hide(self, key: str) -> None: ...
    def request_pin(self, key: str) -> None: ...
    def request_on_top(self, key: str, on_top: bool) -> None: ...


class TitleBar(QFrame):
    """A block's header: the drag handle plus pin, float and close buttons.

    Left-drag on the bar drives the canvas drag gesture; the buttons park the
    block in the pinned strip, pop it out into its own window, or hide it. Clicks
    on the buttons are consumed by them, so they never start a drag.

    The ``🖈`` button **means different things in different homes**, which is not a
    trick but the honest reading of one glyph: on the page or in the strip it pins
    the block *to the strip*, and in its own window — where the strip is not
    somewhere it can go without ceasing to be a window — it pins the window *above
    other applications*. Pinning is what the glyph says; what it pins to is
    whatever the block is currently beside. :meth:`set_floating` swaps the two.
    """

    def __init__(self, key: str, title: str, host: DragHost, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._key = key
        self._host = host
        self.setObjectName("blockTitleBar")
        self.setCursor(Qt.CursorShape.OpenHandCursor)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 2, 4, 2)
        layout.setSpacing(2)

        # An eliding label, so a block's width comes from its content and not from
        # the length of its caption: a section's live title grows ("Abilities — 24
        # PP"), and a plain label would make that string a minimum width — pushing
        # a fixed-width block past its own max_width, or thickening the pinned
        # strip beyond the min_width that is supposed to set it.
        self._label = ElidingLabel(title)
        self._label.setObjectName("blockTitleLabel")
        layout.addWidget(self._label, stretch=1)

        # Whether this block is in a window of its own, which is what the pin
        # button means by "pin" — see the class docstring.
        self._floating = False
        self._pin_button = QToolButton()
        # U+1F588 black pushpin, not U+1F4CC: the plain symbol keeps the title bar
        # monochrome like its ↗ and ✕ neighbours, where the emoji pushpin would put
        # a colour glyph in every block's header.
        self._pin_button.setText("🖈")
        self._pin_button.setAutoRaise(True)
        self._pin_button.setCursor(Qt.CursorShape.ArrowCursor)
        self._pin_button.clicked.connect(self._pin_clicked)
        layout.addWidget(self._pin_button)

        self._float_button = QToolButton()
        self._float_button.setText("↗")  # north-east arrow: pop out
        self._float_button.setAutoRaise(True)
        self._float_button.setToolTip("Pop this block out into its own window")
        self._float_button.setCursor(Qt.CursorShape.ArrowCursor)
        self._float_button.clicked.connect(lambda: self._host.request_float(self._key))
        layout.addWidget(self._float_button)

        self._close_button = QToolButton()
        self._close_button.setText("✕")  # multiplication x: close/hide
        self._close_button.setAutoRaise(True)
        self._close_button.setToolTip("Hide this block (reopen from the View menu)")
        self._close_button.setCursor(Qt.CursorShape.ArrowCursor)
        self._close_button.clicked.connect(lambda: self._host.request_hide(self._key))
        layout.addWidget(self._close_button)

        # Last, once every button it dresses exists.
        self.set_floating(False)

    def set_floating(self, floating: bool, *, on_top: bool = False) -> None:
        """Say which home this block is in, which decides what its ``🖈`` pins.

        In a window of its own the pin is a **toggle** — the window stays above
        other applications, or it does not — so it is checkable there and a plain
        action everywhere else. The float button goes with it: a window already is
        popped out, and ``↗`` on it would do nothing.
        """
        self._floating = bool(floating)
        self._float_button.setVisible(not self._floating)
        self._pin_button.setCheckable(self._floating)
        if self._floating:
            self._pin_button.setChecked(bool(on_top))
            self._pin_button.setToolTip(describe_on_top(on_top))
            return
        self._pin_button.setChecked(False)
        self._pin_button.setToolTip(
            "Pin this block to the fixed strip (or drag it there); "
            "click again to send it back to the page"
        )

    def _pin_clicked(self) -> None:
        if self._floating:
            on_top = self._pin_button.isChecked()
            self.set_floating(True, on_top=on_top)  # refresh the tooltip
            self._host.request_on_top(self._key, on_top)
            return
        self._host.request_pin(self._key)

    def set_title(self, text: str) -> None:
        """Update the drag handle's caption (a section reports its live title here)."""
        self._label.setText(text)

    def title_text(self) -> str:
        """The caption in full, even when the bar is too narrow to show all of it."""
        return self._label.text()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._host.title_bar_pressed(self._key, event.globalPosition().toPoint())
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if event.buttons() & Qt.MouseButton.LeftButton:
            self._host.title_bar_moved(self._key, event.globalPosition().toPoint())
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._host.title_bar_released(self._key, event.globalPosition().toPoint())
            event.accept()
            return
        super().mouseReleaseEvent(event)


class BlockFrame(QFrame):
    """One block: a :class:`TitleBar` above a section, sized to its content.

    Applies the block's :class:`BlockSize` constraints (min size always; a max
    bound only when the JSON pins that dimension), matching the old dock
    semantics. Abilities and Resistances are fixed-width (``min_width ==
    max_width``); the other blocks grow wider than their min.
    """

    #: How many turns the inner layout may spend recovering after a re-homing.
    RELAYOUT_TRIES = 5
    RELAYOUT_MS = 16

    def __init__(
        self,
        key: str,
        title: str,
        section: QWidget,
        size: BlockSize,
        host: DragHost,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.key = key
        self.title = title
        # The block's plain name, kept apart from `title` because a section may
        # replace the latter with a live, priced caption ("Abilities — 24 PP").
        # Anything naming the block rather than reporting on it (the View menu)
        # wants this one, which never goes stale.
        self.base_title = title
        self.section = section
        self._size = BlockSize()
        self.setObjectName("blockFrame")
        self.setFrameShape(QFrame.Shape.StyledPanel)

        self.title_bar = TitleBar(key, title, host, self)

        # Re-homing this block leaves its inner layout stale; see _refresh_layout.
        self._relayout_tries = 0
        self._relayout = QTimer(self)
        self._relayout.setSingleShot(True)
        self._relayout.timeout.connect(self._refresh_layout)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.title_bar)
        layout.addWidget(section, stretch=1)

        # A section may own a live caption (its running point cost); show it in the
        # title bar rather than duplicating it inside the block. See TitledSection.
        title_changed = getattr(section, "titleChanged", None)
        if title_changed is not None:
            title_changed.connect(self._set_title)
        block_title = getattr(section, "block_title", None)
        if callable(block_title) and block_title():
            self._set_title(block_title())

        self._apply_size(size)

    def _set_title(self, text: str) -> None:
        """Reflect a section's live title in both the title bar and window title.

        A floated block's window title is snapshotted at float time, so it has to be
        refreshed here too or it keeps whatever point subtotal was current when the
        block was dragged out.
        """
        self.title = text
        self.title_bar.set_title(text)
        window = self.window()
        if isinstance(window, BlockWindow):
            window.setWindowTitle(text)

    def _apply_size(self, size: BlockSize) -> None:
        """Pin the block's size from its :class:`BlockSize` (see class docstring)."""
        self._size = size
        self.setMinimumWidth(size.min_width)
        if size.max_width < UNBOUNDED:
            self.setMaximumWidth(size.max_width)
        if size.max_height < UNBOUNDED:
            self.setMaximumHeight(size.max_height)
        # A block whose width is pinned (abilities/resistances) shouldn't stretch;
        # the others expand to share their row's width. Vertically a block never
        # shrinks below its own content (see minimumSizeHint), so the page scrolls
        # when the blocks don't all fit instead of squashing them.
        fixed_width = size.max_width < UNBOUNDED and size.max_width == size.min_width
        h_policy = QSizePolicy.Policy.Fixed if fixed_width else QSizePolicy.Policy.Expanding
        self.setSizePolicy(h_policy, QSizePolicy.Policy.Minimum)

    def changeEvent(self, event) -> None:  # noqa: ANN001, N802 - Qt override
        """Re-run the block's own layout after it is re-homed (see :meth:`_refresh_layout`)."""
        if event.type() == QEvent.Type.ParentChange:
            self._relayout.start(0)
        super().changeEvent(event)

    def _refresh_layout(self) -> None:
        """Recompute the block's inner layout, and again until it is not degenerate.

        A block moves between three hosts — a row on the page, a line of the pinned
        strip, and its own floating window — and on the way it is briefly given
        *zero height* by a container that has not been sized yet. Its layout
        activates against that and caches a geometry a few pixels **negative**; the
        block then reaches its real size while hidden, so no resize event follows,
        Qt has no reason to run the layout again, and the block draws as an empty
        framed box. Only nudging its size brought it back — which is why resizing
        the strip appeared to fix it.

        Re-activating on the turn *after* a re-homing catches it, once the container
        has handed out real geometry; if the layout still looks degenerate (the
        container needed more than one turn to settle), it tries again, bounded so
        it cannot spin.
        """
        layout = self.layout()
        if layout is None:
            return
        layout.invalidate()
        layout.activate()
        self.updateGeometry()
        degenerate = layout.geometry().height() <= 0 < self.height()
        if degenerate and self._relayout_tries < self.RELAYOUT_TRIES:
            self._relayout_tries += 1
            self._relayout.start(self.RELAYOUT_MS)
        else:
            self._relayout_tries = 0

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt override
        """Never let a block shrink below its full content height.

        The JSON ``min_height`` is only a floor; the block's real minimum is its
        content, so every block always shows *all* of its content and the page
        scrolls when they don't all fit — rather than the layout squashing a
        block down to the floor and clipping it (e.g. Base Info's image). Capped
        at ``max_height`` when a block pins that dimension.
        """
        hint = super().minimumSizeHint()
        height = max(self._size.min_height, self.sizeHint().height())
        if self._size.max_height < UNBOUNDED:
            height = min(height, self._size.max_height)
        return QSize(max(hint.width(), self.minimumWidth()), height)

    def set_vertical_fill(self, fill: bool) -> None:
        """Let this block grow past its content to fill slack (or stop it).

        The default vertical policy is ``Minimum`` — a block is exactly as tall as
        its content and the page scrolls when blocks don't all fit. A canvas that
        wants its bottom block to soak up leftover height (see
        :class:`~mm_companion.ui.block_canvas.BlockCanvas` ``fill_last``) flips the
        fill block to ``Expanding`` instead, so it stretches down to the window's
        edge rather than leaving dead space below it.
        """
        policy = self.sizePolicy()
        target = QSizePolicy.Policy.Expanding if fill else QSizePolicy.Policy.Minimum
        if policy.verticalPolicy() == target:
            return
        policy.setVerticalPolicy(target)
        self.setSizePolicy(policy)

    def set_locked(self, locked: bool) -> None:
        """Forward read-only view mode to the section; the title bar stays live."""
        self.section.set_locked(locked)


class BlockWindow(QWidget):
    """A top-level window hosting a floated-out :class:`BlockFrame`.

    Owned by the sheet (so it closes with it and isn't garbage-collected) and
    flagged as a tool window. Its title bar reuses the same drag gesture, so the
    user can drag it back onto the sheet to re-dock. Closing it via the window
    chrome hides the block rather than losing it.

    The frame lives inside a :class:`QScrollArea` so a block that doesn't fit
    scrolls *within its window* — unlike when it is docked, where the whole sheet
    scrolls as one page and each block shows all of its content. It scrolls **both
    ways**, and that is what lets the window go as small as it is dragged: a
    :class:`QScrollArea` does not pass its child's minimum on, so the only floor is
    ``float.min-width``/``float.min-height``, exactly as the mini roller's is
    ``compact.min-*`` (see :mod:`mm_companion.ui.compact`). A block popped out to
    sit beside somebody else's application is one whose window someone will want to
    shove into a corner, and clipping it there is not the alternative — scrolling
    is.

    It is **frameless**, and that is a trade rather than a decoration: a popped-out
    block spends its life beside somebody else's application, where the OS title
    bar is most of what makes it read as a document rather than a tool. What the
    frame was doing is supplied instead by the block's own title bar (drag, and the
    ``✕`` that hides it) and a :class:`QSizeGrip` in the corner. The flags are set
    here, before the first ``show()``, because changing them on a visible window
    costs a hide/recreate cycle (see
    :func:`~mm_companion.ui.frameless.apply_window_flags`).
    """

    def __init__(self, key: str, host: DragHost, parent: QWidget | None = None) -> None:
        super().__init__(parent, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint)
        self._key = key
        self._host = host
        self.setObjectName("blockWindow")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self._layout = layout

        self._scroll = QScrollArea(self)
        self._scroll.setObjectName("blockWindowScroll")
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        layout.addWidget(self._scroll)
        layout.addWidget(size_grip_row(self))
        self.setMinimumSize(
            int(theme.metric("float.min-width")), int(theme.metric("float.min-height"))
        )

    def set_on_top(self, on_top: bool) -> None:
        """Keep this window above other applications, or let it fall behind."""
        apply_window_flags(self, frameless=True, on_top=bool(on_top))

    def set_frame(self, frame: BlockFrame) -> None:
        """Host *frame*, giving the window the frame's title as its window title."""
        self.setWindowTitle(frame.title)
        self._scroll.setWidget(frame)

    def closeEvent(self, event) -> None:  # noqa: ANN001 - Qt signature
        """Closing the window hides the block instead of destroying it."""
        self._host.request_hide(self._key)
        event.ignore()

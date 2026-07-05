"""One character-sheet block: a section wrapped in a draggable frame.

Each block is a :class:`BlockFrame` — a title bar (the drag handle, plus float
and close buttons) above one of the ``sections`` widgets. The frame never wraps
its section in a scroll area, so the block is sized to its content and never
scrolls on its own; the whole sheet scrolls as one page instead (see
:class:`~mm_companion.ui.block_canvas.BlockCanvas`).

A frame lives either inside the canvas or, when floated out, inside a
:class:`BlockWindow` (a top-level window). Dragging the title bar in either place
runs the same gesture, driven by the canvas's drag controller — so float-out,
reorder, and drag-back-to-dock are one interaction.

The frame is deliberately dumb: it forwards title-bar mouse events and button
clicks to a *controller* (the :class:`BlockCanvas`) and applies its size
constraints. All arrangement logic lives in the canvas.
"""

from __future__ import annotations

from typing import Protocol

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from mm_companion.ui.block_sizes import UNBOUNDED, BlockSize


class DragHost(Protocol):
    """What a :class:`TitleBar` needs from its controller (the canvas)."""

    def title_bar_pressed(self, key: str, global_pos: QPoint) -> None: ...
    def title_bar_moved(self, key: str, global_pos: QPoint) -> None: ...
    def title_bar_released(self, key: str, global_pos: QPoint) -> None: ...
    def request_float(self, key: str) -> None: ...
    def request_hide(self, key: str) -> None: ...


class TitleBar(QFrame):
    """A block's header: the drag handle plus float and close buttons.

    Left-drag on the bar drives the canvas drag gesture; the buttons pop the
    block out into its own window or hide it. Clicks on the buttons are consumed
    by them, so they never start a drag.
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

        self._label = QLabel(title)
        self._label.setObjectName("blockTitleLabel")
        layout.addWidget(self._label, stretch=1)

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
        self.section = section
        self.setObjectName("blockFrame")
        self.setFrameShape(QFrame.Shape.StyledPanel)

        self.title_bar = TitleBar(key, title, host, self)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.title_bar)
        layout.addWidget(section, stretch=1)

        self._apply_size(size)

    def _apply_size(self, size: BlockSize) -> None:
        """Pin the block's size from its :class:`BlockSize` (see class docstring)."""
        self.setMinimumSize(size.min_width, size.min_height)
        if size.max_width < UNBOUNDED:
            self.setMaximumWidth(size.max_width)
        if size.max_height < UNBOUNDED:
            self.setMaximumHeight(size.max_height)
        # A block whose width is pinned (abilities/resistances) shouldn't stretch;
        # the others expand to share their row's width. Height always hugs content.
        fixed_width = size.max_width < UNBOUNDED and size.max_width == size.min_width
        h_policy = QSizePolicy.Policy.Fixed if fixed_width else QSizePolicy.Policy.Expanding
        self.setSizePolicy(h_policy, QSizePolicy.Policy.Preferred)

    def set_locked(self, locked: bool) -> None:
        """Forward read-only view mode to the section; the title bar stays live."""
        self.section.set_locked(locked)


class BlockWindow(QWidget):
    """A top-level window hosting a floated-out :class:`BlockFrame`.

    Owned by the sheet (so it closes with it and isn't garbage-collected) and
    flagged as a tool window. Its title bar reuses the same drag gesture, so the
    user can drag it back onto the sheet to re-dock. Closing it via the window
    chrome hides the block rather than losing it.
    """

    def __init__(self, key: str, host: DragHost, parent: QWidget | None = None) -> None:
        super().__init__(parent, Qt.WindowType.Tool)
        self._key = key
        self._host = host
        self.setObjectName("blockWindow")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self._layout = layout

    def set_frame(self, frame: BlockFrame) -> None:
        """Host *frame*, giving the window the frame's title as its window title."""
        self.setWindowTitle(frame.title)
        self._layout.addWidget(frame)

    def take_frame(self) -> QWidget | None:
        """Detach and return the hosted frame (before re-docking it)."""
        item = self._layout.takeAt(0)
        return item.widget() if item else None

    def closeEvent(self, event) -> None:  # noqa: ANN001 - Qt signature
        """Closing the window hides the block instead of destroying it."""
        self._host.request_hide(self._key)
        event.ignore()

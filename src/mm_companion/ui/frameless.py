"""What a window has to supply for itself once it drops the OS frame.

Two of them do: the compact mini roller (:mod:`mm_companion.ui.compact`) and a
block popped out into its own window (:class:`~mm_companion.ui.block_frame.
BlockWindow`). Both trade the platform's title bar for their own — which is worth
it, because a small utility window spends most of its life beside somebody else's
application and the OS chrome is most of what makes it feel like a document — and
both then owe the user the two things that chrome was doing: a way to move the
window, and a way to resize it.

Moving is each window's own business (both already have a drag handle: a block's
title bar, the mini roller's strip). Resizing and the flags are the same job
twice, so they live here.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QSizeGrip, QWidget


def apply_window_flags(window: QWidget, *, frameless: bool, on_top: bool) -> None:
    """Set *window*'s frameless / always-on-top flags, keeping it where it is.

    Qt **hides** a window whose flags change, and the platform destroys and
    recreates the native window behind it — which loses the geometry too. So this
    re-shows and re-applies it, and it must never be called while a geometry
    animation is running (see :mod:`mm_companion.ui.compact`).

    A window that was never shown is left hidden: a test that only constructs one
    must not have it thrown onto the screen.
    """
    visible = window.isVisible()
    geometry = window.geometry()
    window.setWindowFlag(Qt.WindowType.FramelessWindowHint, frameless)
    window.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, on_top)
    if visible:
        window.show()
        window.setGeometry(geometry)


def size_grip_row(parent: QWidget | None = None) -> QWidget:
    """A right-aligned :class:`QSizeGrip` on a row of its own.

    A row rather than an overlay in the corner: over the content it would sit on
    top of whatever is in that corner — a history card's buttons — and take the
    clicks meant for it.
    """
    row = QWidget(parent)
    box = QHBoxLayout(row)
    box.setContentsMargins(0, 0, 0, 0)
    box.addStretch()
    box.addWidget(QSizeGrip(row))
    return row

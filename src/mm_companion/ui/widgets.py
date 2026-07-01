"""Small shared widget factories used across the character-sheet sections.

These keep widget construction consistent (and wheel-guarded) in one place,
rather than each section rolling its own spin boxes and table items.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QSpinBox, QTableWidgetItem

from mm_companion.ui.wheel_guard import guard_wheel


def make_spin_box(
    minimum: int,
    maximum: int,
    *,
    value: int | None = None,
    buttons: bool = True,
    max_width: int | None = None,
    guarded: bool = True,
) -> QSpinBox:
    """Build a range-bounded spin box.

    ``buttons=False`` hides the up/down arrows, ``max_width`` caps the width for
    the compact stat/skill columns, and ``guarded`` (the default) installs the
    wheel guard so the box only reacts to the wheel once focused. Pass
    ``guarded=False`` when the caller guards the box itself in a batch.
    """
    spin = QSpinBox()
    spin.setRange(minimum, maximum)
    if value is not None:
        spin.setValue(value)
    if not buttons:
        spin.setButtonSymbols(QSpinBox.NoButtons)
    if max_width is not None:
        spin.setMaximumWidth(max_width)
    if guarded:
        guard_wheel(spin)
    return spin


def readonly_item(text: str, *, center: bool = False) -> QTableWidgetItem:
    """A table item that displays *text* but cannot be edited in place."""
    item = QTableWidgetItem(text)
    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
    if center:
        item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
    return item


def hline_separator() -> QFrame:
    """A sunken horizontal rule used to divide primary from derived stats."""
    line = QFrame()
    line.setFrameShape(QFrame.Shape.HLine)
    line.setFrameShadow(QFrame.Shadow.Sunken)
    return line

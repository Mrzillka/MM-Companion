"""Small shared widget factories and style snippets used across the sections.

These keep widget construction consistent (and wheel-guarded) in one place,
rather than each section rolling its own spin boxes and table items. The style
helpers at the bottom do the same for the two or three inline stylesheets the
sheet repeats everywhere — a muted secondary line, a tinted emphasis — so those
resolve their theme tokens in one place instead of a dozen f-strings.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QSpinBox, QTableWidgetItem

from mm_companion.ui import theme
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


def title_with_cost(title: str, points: int) -> str:
    """A group-box title annotated with its running power-point cost, e.g.
    ``"Abilities — 24 PP"`` — kept in one place so every section reads the same."""

    return f"{title} — {points} PP"


def hline_separator() -> QFrame:
    """A sunken horizontal rule used to divide primary from derived stats."""
    line = QFrame()
    line.setFrameShape(QFrame.Shape.HLine)
    line.setFrameShadow(QFrame.Shadow.Sunken)
    return line


# -- inline style snippets ------------------------------------------------------
# Built fresh on each call rather than cached in a module constant, so a theme
# switch reaches a widget the next time it is styled.


def muted_style(*, italic: bool = False) -> str:
    """A secondary line — a description, a role note — that recedes without vanishing.

    Reads on light and dark alike: the ``text.muted`` token is a ``palette()``
    role in the system preset, and a chosen shade in a styled one.
    """
    tail = " font-style: italic;" if italic else ""
    return f"color: {theme.color('text.muted')};{tail}"


def tinted_style(token: str, *, bold: bool = True) -> str:
    """Foreground in the semantic colour token *token*, bold by default.

    The sheet's one way of saying "this number is not the plain one": green for a
    boost, red for a penalty or breach, amber for a warning, blue for a homerule.
    """
    tail = " font-weight: bold;" if bold else ""
    return f"color: {theme.color(token)};{tail}"


#: "This label is the important one." A bare weight with no colour, so it works
#: on any surface — the one style snippet with nothing to theme.
BOLD_STYLE = "font-weight: bold;"

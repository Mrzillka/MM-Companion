"""One row of the theme editor that edits a colour token.

A swatch you can click to open the platform colour picker, beside the value as
text — because a colour token is not always a colour. ``classic`` states
``"palette(mid)"`` for its card border and ``"gray"`` for muted rich text, which
is exactly how it follows the OS light/dark setting, and Qt resolves both at paint
time. Neither can be produced by a colour picker, so the text field is the one
that can say everything and the swatch is the convenient case.
"""

from __future__ import annotations

import re

from PySide6.QtCore import QSize, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QColorDialog, QHBoxLayout, QLabel, QLineEdit, QPushButton, QWidget

from mm_companion.ui import theme
from mm_companion.ui.widgets import muted_style, tinted_style

#: A Qt stylesheet ``palette(role)`` expression, resolved against the installed
#: palette when the widget paints rather than by us here.
_PALETTE_EXPRESSION = re.compile(r"^palette\(\s*[a-z][a-z-]*\s*\)$")

#: How much bigger than the text the swatch sits, in the theme's own spacing steps.
_SWATCH_STEPS = 3


def is_valid_color_value(value: str) -> bool:
    """Whether *value* is something Qt can paint: a literal, a name, or ``palette()``."""
    value = value.strip()
    if not value:
        return False
    if theme.is_literal_color(value) or _PALETTE_EXPRESSION.match(value):
        return True
    return QColor.isValidColorName(value)


class ColorRow(QWidget):
    """A swatch plus the value as text, for one colour token."""

    #: The value changed to something paintable. A rejected edit is silent — the
    #: row shows why and the old value stays in force.
    valueChanged = Signal(str)

    def __init__(self, value: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._value = value

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(int(theme.metric("space.sm")))

        self._swatch = QPushButton()
        side = int(theme.metric("space.lg")) * _SWATCH_STEPS
        self._swatch.setFixedSize(QSize(side, side))
        self._swatch.setToolTip("Pick a colour")
        self._swatch.clicked.connect(self._pick)
        layout.addWidget(self._swatch)

        self._field = QLineEdit(value)
        self._field.setPlaceholderText("#rrggbb")
        self._field.editingFinished.connect(self._commit_text)
        layout.addWidget(self._field, stretch=1)

        self._warning = QLabel()
        self._warning.setWordWrap(True)
        layout.addWidget(self._warning, stretch=1)

        self._restyle()

    # -- value ------------------------------------------------------------------

    def value(self) -> str:
        return self._value

    def set_value(self, value: str) -> None:
        """Show *value* without emitting — for seeding, or for rejecting an edit."""
        self._value = value
        self._field.setText(value)
        self._restyle()

    def set_warning(self, text: str, token: str = "tint.warning") -> None:
        """Say what is wrong with the current value, or clear it with ``""``."""
        self._warning.setText(text)
        self._warning.setStyleSheet(tinted_style(token, bold=False) if text else muted_style())
        self._warning.setToolTip(text)

    def editable_widgets(self) -> list[QWidget]:
        """The widgets a locked (read-only) view has to neutralise."""
        return [self._field, self._swatch]

    # -- interaction ------------------------------------------------------------

    def _pick(self) -> None:
        start = QColor(self._value) if theme.is_literal_color(self._value) else QColor()
        chosen = QColorDialog.getColor(start, self, "Pick a colour")
        if chosen.isValid():
            self._value = chosen.name()
            self._field.setText(self._value)
            self._restyle()
            self.valueChanged.emit(self._value)

    def _commit_text(self) -> None:
        text = self._field.text().strip()
        if text == self._value:
            return
        if not is_valid_color_value(text):
            self.set_warning("Not a colour Qt can paint.", "tint.worse")
            self._field.setText(self._value)
            return
        self._value = text
        self._restyle()
        self.valueChanged.emit(text)

    def _restyle(self) -> None:
        """Fill the swatch with whatever the value is — including a ``palette()`` one.

        Those resolve in a stylesheet just as well as a hex literal, so the swatch
        shows the real thing rather than a placeholder. Only a *wash* cannot be
        derived from one, which the editor warns about separately.
        """
        border = theme.color("border.card")
        width = int(theme.metric("border.width"))
        radius = int(theme.metric("radius.card"))
        self._swatch.setStyleSheet(
            f"background: {self._value}; border: {width}px solid {border};"
            f" border-radius: {radius}px;"
        )

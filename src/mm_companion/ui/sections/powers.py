"""Section 4: powers.

The most complex part of a character. Intentionally left as empty space for now
— just a titled placeholder to reserve the layout slot.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QGroupBox, QLabel, QVBoxLayout, QWidget


class PowersSection(QGroupBox):
    """Placeholder for the powers builder."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("Powers", parent)

        layout = QVBoxLayout(self)
        placeholder = QLabel("Powers — coming soon")
        placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        placeholder.setMinimumHeight(120)
        placeholder.setEnabled(False)
        layout.addWidget(placeholder)

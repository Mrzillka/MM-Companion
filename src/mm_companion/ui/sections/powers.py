"""Section 4: powers.

The most complex part of a character. For now this section is the entry point to
the standalone :class:`~mm_companion.ui.power_constructor.PowerConstructorWindow`:
an "Add Power" button opens the brick-builder in its own window. Wiring the
finished power back onto the character sheet is deferred, so this section holds no
character state yet — but it follows the standard section contract (``data`` +
``character`` constructor, ``changed`` signal, ``set_locked``) so it slots into the
sheet like the others.
"""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QGroupBox, QLabel, QPushButton, QVBoxLayout, QWidget

from mm_companion.core.character import Character
from mm_companion.core.data_loader import GameData
from mm_companion.ui.power_constructor import PowerConstructorWindow


class PowersSection(QGroupBox):
    """Powers section: launches the Power Constructor."""

    changed = Signal()

    def __init__(
        self,
        data: GameData,
        character: Character,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__("Powers", parent)
        self._data = data
        self._character = character
        # Keep constructor windows referenced so Qt doesn't garbage-collect them
        # the moment the click handler returns.
        self._windows: list[PowerConstructorWindow] = []

        layout = QVBoxLayout(self)
        self._empty = QLabel("No powers yet")
        self._empty.setEnabled(False)
        layout.addWidget(self._empty)

        self._add_button = QPushButton("Add Power")
        self._add_button.clicked.connect(self._open_constructor)
        layout.addWidget(self._add_button)

    def _open_constructor(self) -> None:
        window = PowerConstructorWindow(self._data)
        window.closed.connect(lambda w=window: self._on_window_closed(w))
        self._windows.append(window)
        window.show()

    def _on_window_closed(self, window: PowerConstructorWindow) -> None:
        if window in self._windows:
            self._windows.remove(window)

    def set_locked(self, locked: bool) -> None:
        """In read-only view mode, hide the editing entry point."""
        self._add_button.setVisible(not locked)

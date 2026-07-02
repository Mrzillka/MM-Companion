"""Top-level application window hosting the character sheet."""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QMainWindow, QMenu, QWidget

from mm_companion.ui.character_sheet import CharacterSheet


class MainWindow(QMainWindow):
    """Main window; currently shows a single character sheet.

    Emits :attr:`closed` when the window is closed so a launcher can reappear.
    """

    closed = Signal()

    def __init__(self, parent: QWidget | None = None, *, locked: bool = True) -> None:
        super().__init__(parent)
        self.setWindowTitle("MM-Companion — Character Sheet")
        self.resize(900, 800)
        self._sheet = CharacterSheet()
        self._build_menu_bar(locked)
        self.setCentralWidget(self._sheet)

        # New characters open unlocked for editing; otherwise the sheet is a
        # read-only view.
        self._sheet.set_locked(locked)

    def _build_menu_bar(self, locked: bool) -> None:
        """Build the top menu bar. Everything but Lock is a disabled placeholder."""
        menu_bar = self.menuBar()

        file_menu = menu_bar.addMenu("&File")
        self._add_placeholder_actions(file_menu, ["New", "Open...", "Save", "Save As..."])
        file_menu.addSeparator()
        # Exit closes the sheet; closing brings the launcher back (see closeEvent).
        file_menu.addAction("Exit").triggered.connect(self.close)

        settings_menu = menu_bar.addMenu("&Settings")
        self._add_placeholder_actions(settings_menu, ["Rules", "Theme"])

        self._lock_action = settings_menu.addAction("Lock")
        self._lock_action.setCheckable(True)
        self._lock_action.setChecked(locked)
        self._lock_action.toggled.connect(self._sheet.set_locked)

    def closeEvent(self, event: QCloseEvent) -> None:
        """Announce the close so a launcher can reappear, then close normally."""
        self.closed.emit()
        super().closeEvent(event)

    @staticmethod
    def _add_placeholder_actions(menu: QMenu, labels: list[str]) -> None:
        """Add disabled placeholder actions to *menu*, one per label."""
        for label in labels:
            action = menu.addAction(label)
            action.setEnabled(False)

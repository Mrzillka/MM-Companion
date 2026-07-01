"""Top-level application window hosting the character sheet."""

from __future__ import annotations

from PySide6.QtWidgets import QMainWindow, QMenu, QWidget

from mm_companion.ui.character_sheet import CharacterSheet


class MainWindow(QMainWindow):
    """Main window; currently shows a single character sheet."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("MM-Companion — Character Sheet")
        self.resize(900, 800)
        self._sheet = CharacterSheet()
        self._build_menu_bar()
        self.setCentralWidget(self._sheet)

        # The sheet starts locked: a read-only view rather than an editor.
        self._sheet.set_locked(True)

    def _build_menu_bar(self) -> None:
        """Build the top menu bar. Everything but Lock is a disabled placeholder."""
        menu_bar = self.menuBar()

        file_menu = menu_bar.addMenu("&File")
        self._add_placeholder_actions(file_menu, ["New", "Open...", "Save", "Save As...", "Exit"])

        settings_menu = menu_bar.addMenu("&Settings")
        self._add_placeholder_actions(settings_menu, ["Rules", "Theme"])

        self._lock_action = settings_menu.addAction("Lock")
        self._lock_action.setCheckable(True)
        self._lock_action.setChecked(True)
        self._lock_action.toggled.connect(self._sheet.set_locked)

    @staticmethod
    def _add_placeholder_actions(menu: QMenu, labels: list[str]) -> None:
        """Add disabled placeholder actions to *menu*, one per label."""
        for label in labels:
            action = menu.addAction(label)
            action.setEnabled(False)

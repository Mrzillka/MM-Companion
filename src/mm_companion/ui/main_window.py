"""Top-level application window hosting the character sheet."""

from __future__ import annotations

from PySide6.QtWidgets import QMainWindow, QWidget

from mm_companion.ui.character_sheet import CharacterSheet


class MainWindow(QMainWindow):
    """Main window; currently shows a single character sheet."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("MM-Companion — Character Sheet")
        self.resize(900, 800)
        self.setCentralWidget(CharacterSheet())

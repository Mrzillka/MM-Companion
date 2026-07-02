"""Top-level application window hosting the character sheet."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Signal
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QFileDialog, QMainWindow, QMenu, QWidget

from mm_companion.core import library, storage
from mm_companion.core.character import Character
from mm_companion.ui.character_sheet import CharacterSheet

CHARACTER_FILTER = "Character files (*.json)"


class MainWindow(QMainWindow):
    """Main window; currently shows a single character sheet.

    Emits :attr:`closed` when the window is closed so a launcher can reappear,
    and :attr:`saved` after a character is written to disk so a launcher can
    refresh its library.
    """

    closed = Signal()
    saved = Signal()

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        character: Character | None = None,
        path: Path | None = None,
        locked: bool = True,
    ) -> None:
        super().__init__(parent)
        self.resize(900, 800)
        # The file this sheet is saved to, or None until the first save.
        self._path: Path | None = Path(path) if path else None
        # Windows opened from this one (via Open) kept referenced so they aren't
        # garbage-collected the moment the handler returns.
        self._child_windows: list[MainWindow] = []

        self._sheet = CharacterSheet(character=character)
        self._build_menu_bar(locked)
        self.setCentralWidget(self._sheet)
        self._update_title()

        # New characters open unlocked for editing; otherwise the sheet is a
        # read-only view.
        self._sheet.set_locked(locked)

    def _build_menu_bar(self, locked: bool) -> None:
        """Build the top menu bar."""
        menu_bar = self.menuBar()

        file_menu = menu_bar.addMenu("&File")
        self._add_placeholder_actions(file_menu, ["New"])
        file_menu.addAction("Open...").triggered.connect(self._open)
        file_menu.addAction("Save").triggered.connect(self._save)
        file_menu.addAction("Save As...").triggered.connect(self._save_as)
        file_menu.addSeparator()
        # Exit closes the sheet; closing brings the launcher back (see closeEvent).
        file_menu.addAction("Exit").triggered.connect(self.close)

        settings_menu = menu_bar.addMenu("&Settings")
        self._add_placeholder_actions(settings_menu, ["Rules", "Theme"])

        self._lock_action = settings_menu.addAction("Lock")
        self._lock_action.setCheckable(True)
        self._lock_action.setChecked(locked)
        self._lock_action.toggled.connect(self._sheet.set_locked)

    # -- persistence ---------------------------------------------------------

    def _save(self) -> None:
        """Overwrite the character's file, or prompt for one on first save."""
        if self._path is None:
            self._save_as()
            return
        self._write(self._path)

    def _save_as(self) -> None:
        """Prompt for a destination and write the character there."""
        directory = storage.get_workspace().characters_dir
        directory.mkdir(parents=True, exist_ok=True)
        suggested = directory / library.suggested_filename(self._sheet.character)
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Character", str(suggested), CHARACTER_FILTER
        )
        if path:
            self._write(Path(path))

    def _write(self, path: Path) -> None:
        """Persist the character to *path* and remember it as the current file."""
        saved_path = library.save_character(self._sheet.character, path=path)
        self._path = saved_path
        self._update_title()
        self.statusBar().showMessage(f"Saved to {saved_path}", 5000)
        self.saved.emit()

    def _open(self) -> None:
        """Load a saved character into a new, read-only window."""
        directory = storage.get_workspace().characters_dir
        path, _ = QFileDialog.getOpenFileName(
            self, "Open Character", str(directory), CHARACTER_FILTER
        )
        if not path:
            return
        character = library.load_character(Path(path))
        window = MainWindow(character=character, path=Path(path), locked=True)
        self._child_windows.append(window)
        window.show()

    def _update_title(self) -> None:
        name = library.display_name(self._sheet.character)
        self.setWindowTitle(f"MM-Companion — {name}")

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

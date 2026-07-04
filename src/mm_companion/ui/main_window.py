"""Top-level application window hosting the character sheet."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QByteArray, Signal
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QFileDialog, QMainWindow, QMenu, QMessageBox, QWidget

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
        # Whether the sheet has unsaved edits since the last save/load.
        self._dirty = False
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
        # Track unsaved changes only after the initial seed/lock has settled.
        self._sheet.edited.connect(self._on_edited)

        # Restore the remembered window size and dock arrangement, if any.
        self._restore_layout()

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

        view_menu = menu_bar.addMenu("&View")
        # A show/hide toggle per dock, so a panel closed with its × can be
        # reopened, plus a reset back to the default arrangement.
        for dock in self._sheet.docks.values():
            view_menu.addAction(dock.toggleViewAction())
        view_menu.addSeparator()
        view_menu.addAction("Reset Layout").triggered.connect(self._sheet.reset_layout)

        settings_menu = menu_bar.addMenu("&Settings")
        self._add_placeholder_actions(settings_menu, ["Rules", "Theme"])

        self._lock_action = settings_menu.addAction("Lock")
        self._lock_action.setCheckable(True)
        self._lock_action.setChecked(locked)
        self._lock_action.toggled.connect(self._sheet.set_locked)

    # -- persistence ---------------------------------------------------------

    def _save(self) -> bool:
        """Overwrite the character's file, or prompt for one on first save.

        Returns whether the character was actually written (False if the user
        backed out of the Save As dialog).
        """
        if self._path is None:
            return self._save_as()
        return self._write(self._path)

    def _save_as(self) -> bool:
        """Prompt for a destination and write the character there."""
        directory = storage.get_workspace().characters_dir
        directory.mkdir(parents=True, exist_ok=True)
        suggested = directory / library.suggested_filename(self._sheet.character)
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Character", str(suggested), CHARACTER_FILTER
        )
        if not path:
            return False
        return self._write(Path(path))

    def _write(self, path: Path) -> bool:
        """Persist the character to *path* and remember it as the current file."""
        saved_path = library.save_character(self._sheet.character, path=path)
        self._path = saved_path
        self._dirty = False
        self._update_title()
        self.statusBar().showMessage(f"Saved to {saved_path}", 5000)
        self.saved.emit()
        return True

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

    # -- layout persistence --------------------------------------------------

    def _restore_layout(self) -> None:
        """Restore the remembered window geometry and dock arrangement.

        The layout is a global UI preference stored in settings.json; a missing or
        incompatible entry simply leaves the default arrangement in place.
        """
        layout = storage.load_settings().get("layout") or {}
        geometry = layout.get("window_geometry")
        if isinstance(geometry, str) and geometry:
            self.restoreGeometry(QByteArray.fromBase64(geometry.encode("ascii")))
        self._sheet.restore_layout(layout.get("dock_state"))

    def _persist_layout(self) -> None:
        """Save the window geometry and dock arrangement as a global preference."""
        geometry = bytes(self.saveGeometry().toBase64()).decode("ascii")
        storage.update_settings(
            layout={"window_geometry": geometry, "dock_state": self._sheet.save_layout()}
        )

    def _on_edited(self) -> None:
        """Mark the sheet dirty on the first user edit since the last save."""
        if not self._dirty:
            self._dirty = True
            self._update_title()

    def _update_title(self) -> None:
        name = library.display_name(self._sheet.character)
        marker = "*" if self._dirty else ""
        self.setWindowTitle(f"MM-Companion — {marker}{name}")

    def closeEvent(self, event: QCloseEvent) -> None:
        """Guard unsaved changes, announce the close, then close normally."""
        if self._dirty and not self._confirm_close():
            event.ignore()
            return
        self._persist_layout()
        self.closed.emit()
        super().closeEvent(event)

    def _confirm_close(self) -> bool:
        """Prompt to save/discard unsaved changes; return True if OK to close."""
        choice = QMessageBox.question(
            self,
            "Unsaved changes",
            f"Save changes to {library.display_name(self._sheet.character)}?",
            QMessageBox.StandardButton.Save
            | QMessageBox.StandardButton.Discard
            | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Save,
        )
        if choice == QMessageBox.StandardButton.Save:
            return self._save()  # a cancelled Save As leaves the window open
        return choice == QMessageBox.StandardButton.Discard

    @staticmethod
    def _add_placeholder_actions(menu: QMenu, labels: list[str]) -> None:
        """Add disabled placeholder actions to *menu*, one per label."""
        for label in labels:
            action = menu.addAction(label)
            action.setEnabled(False)

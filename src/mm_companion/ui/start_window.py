"""The launcher: the first window the user sees.

Four action buttons on the left (Create New Character, Open Existing, Open GM
Mode, Exit) beside a scrollable area of character cards on the right. Cards are
read from :func:`~mm_companion.core.library.list_saved_characters`; clicking a
card — or "Open Existing" — loads that character into a read-only sheet. The
library refreshes whenever a sheet is saved or closed, so newly saved characters
appear without restarting.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from mm_companion.core import library, storage
from mm_companion.core.library import CharacterSummary, list_saved_characters
from mm_companion.ui.flow_layout import FlowLayout
from mm_companion.ui.main_window import MainWindow

CARD_IMAGE_SIZE = 120
CHARACTER_FILTER = "Character files (*.json)"


class CharacterCard(QFrame):
    """A single saved character rendered as a clickable card: image, name, PL.

    Left-click opens the character; right-click offers to delete it.
    """

    clicked = Signal(object)
    deleteRequested = Signal(object)

    def __init__(self, summary: CharacterSummary, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._summary = summary

        layout = QVBoxLayout(self)

        image = QLabel()
        image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        image.setFixedSize(CARD_IMAGE_SIZE, CARD_IMAGE_SIZE)
        image.setFrameShape(QLabel.Shape.Box)
        resolved = library.resolve_image_path(summary.image_path)
        pixmap = QPixmap(resolved) if resolved else QPixmap()
        if pixmap.isNull():
            image.setText("No image")
        else:
            image.setPixmap(
                pixmap.scaled(
                    CARD_IMAGE_SIZE,
                    CARD_IMAGE_SIZE,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            )
        layout.addWidget(image, alignment=Qt.AlignmentFlag.AlignHCenter)

        name = QLabel(summary.name)
        name.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(name)

        pl = QLabel(f"PL {summary.power_level}")
        pl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(pl)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 (Qt override)
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(event.pos()):
            self.clicked.emit(self._summary)
        super().mouseReleaseEvent(event)

    def contextMenuEvent(self, event) -> None:  # noqa: N802 (Qt override)
        menu = QMenu(self)
        menu.addAction(
            f"Delete {self._summary.name}",
            lambda: self.deleteRequested.emit(self._summary),
        )
        menu.exec(event.globalPos())


class StartWindow(QMainWindow):
    """Application launch point: action buttons beside the character library."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("MM-Companion")
        self.resize(720, 480)
        # Sheet windows opened from here are kept referenced so they aren't
        # garbage-collected (and thus closed) the moment the handler returns.
        self._child_windows: list[MainWindow] = []
        # The mod manager window, kept referenced while open for the same reason.
        self._mods_window: QWidget | None = None

        central = QWidget()
        layout = QHBoxLayout(central)

        layout.addLayout(self._build_button_column(), stretch=1)
        layout.addWidget(self._build_library(), stretch=2)

        self.setCentralWidget(central)

    def _build_button_column(self) -> QVBoxLayout:
        """The left column of action buttons."""
        column = QVBoxLayout()

        create_button = QPushButton("Create New Character")
        create_button.clicked.connect(self._create_new_character)
        column.addWidget(create_button)

        open_button = QPushButton("Open Existing")
        open_button.clicked.connect(self._open_existing)
        column.addWidget(open_button)

        gm_button = QPushButton("Open GM Mode")
        gm_button.clicked.connect(self._not_implemented)
        column.addWidget(gm_button)

        mods_button = QPushButton("Manage Mods")
        mods_button.clicked.connect(self._manage_mods)
        column.addWidget(mods_button)

        exit_button = QPushButton("Exit")
        exit_button.clicked.connect(self.close)
        column.addWidget(exit_button)

        column.addStretch()
        return column

    def _build_library(self) -> QScrollArea:
        """The right-hand scroll area holding one card per saved character."""
        self._cards_container = QWidget()
        self._cards_flow = FlowLayout(self._cards_container)

        self._empty_label = QLabel("No characters yet")
        self._empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_label.setEnabled(False)

        self._library = QScrollArea()
        self._library.setWidgetResizable(True)
        self._populate_cards()
        return self._library

    def _populate_cards(self) -> None:
        """(Re)fill the library from the store, or show the empty state."""
        # Clear any existing cards so a refresh doesn't stack duplicates.
        while self._cards_flow.count():
            item = self._cards_flow.takeAt(0)
            widget = item.widget() if item else None
            if widget is not None:
                widget.deleteLater()

        summaries = list_saved_characters()
        if not summaries:
            self._library.setWidget(self._empty_label)
            return

        for summary in summaries:
            card = CharacterCard(summary)
            card.clicked.connect(self._open_summary)
            card.deleteRequested.connect(self._delete_summary)
            self._cards_flow.addWidget(card)
        self._library.setWidget(self._cards_container)

    def _create_new_character(self) -> None:
        """Open a fresh, editable character sheet, hiding the launcher behind it."""
        self._open_sheet(MainWindow(locked=False))

    def _open_existing(self) -> None:
        """Pick a saved character file and open it as a read-only sheet."""
        directory = storage.get_workspace().characters_dir
        path, _ = QFileDialog.getOpenFileName(
            self, "Open Character", str(directory), CHARACTER_FILTER
        )
        if path:
            self._open_character(Path(path))

    def _open_summary(self, summary: CharacterSummary) -> None:
        """Open the character a clicked card refers to."""
        if summary.path is not None:
            self._open_character(Path(summary.path))

    def _delete_summary(self, summary: CharacterSummary) -> None:
        """Confirm and delete the character a card refers to, then refresh."""
        if summary.path is None:
            return
        confirm = QMessageBox.question(
            self,
            "Delete character",
            f"Delete “{summary.name}”? This cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if confirm == QMessageBox.StandardButton.Yes:
            library.delete_character(Path(summary.path))
            self._populate_cards()

    def _open_character(self, path: Path) -> None:
        """Load a character from *path* into a read-only sheet."""
        character = library.load_character(path)
        self._open_sheet(MainWindow(character=character, path=path, locked=True))

    def _open_sheet(self, window: MainWindow) -> None:
        """Show a sheet window, hide the launcher, and track it for cleanup."""
        window.closed.connect(lambda w=window: self._on_sheet_closed(w))
        window.saved.connect(self._populate_cards)
        self._child_windows.append(window)
        self.hide()
        window.show()

    def _on_sheet_closed(self, window: MainWindow) -> None:
        """Drop a closed sheet, refresh the library, and bring the launcher back."""
        if window in self._child_windows:
            self._child_windows.remove(window)
        self._populate_cards()
        self.show()
        self.raise_()
        self.activateWindow()

    def _manage_mods(self) -> None:
        """Open the Mod Manager window."""
        from mm_companion.ui.mods_window import ModsWindow

        window = ModsWindow()
        self._mods_window = window
        window.show()

    def _not_implemented(self) -> None:
        """Placeholder for the not-yet-wired GM mode button."""

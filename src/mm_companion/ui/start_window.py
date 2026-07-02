"""The launcher: the first window the user sees.

Four action buttons on the left (Create New Character, Open Existing, Open GM
Mode, Exit) beside a scrollable area of character cards on the right. Cards are
read from :func:`~mm_companion.core.library.list_saved_characters`, which is
empty until save/load exists — so today the card area shows an empty state and
only Exit is wired.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from mm_companion.core.library import CharacterSummary, list_saved_characters
from mm_companion.ui.flow_layout import FlowLayout
from mm_companion.ui.main_window import MainWindow

CARD_IMAGE_SIZE = 120


class CharacterCard(QFrame):
    """A single saved character rendered as a card: image, name, power level."""

    def __init__(self, summary: CharacterSummary, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.StyledPanel)

        layout = QVBoxLayout(self)

        image = QLabel()
        image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        image.setFixedSize(CARD_IMAGE_SIZE, CARD_IMAGE_SIZE)
        image.setFrameShape(QLabel.Shape.Box)
        pixmap = QPixmap(summary.image_path) if summary.image_path else QPixmap()
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


class StartWindow(QMainWindow):
    """Application launch point: action buttons beside the character library."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("MM-Companion")
        self.resize(720, 480)
        # Sheet windows opened from here are kept referenced so they aren't
        # garbage-collected (and thus closed) the moment the handler returns.
        self._child_windows: list[MainWindow] = []

        central = QWidget()
        layout = QHBoxLayout(central)

        layout.addLayout(self._build_button_column(), stretch=1)
        layout.addWidget(self._build_library(), stretch=2)

        self.setCentralWidget(central)

    def _build_button_column(self) -> QVBoxLayout:
        """The left column of action buttons; only Exit is wired for now."""
        column = QVBoxLayout()

        create_button = QPushButton("Create New Character")
        create_button.clicked.connect(self._create_new_character)
        column.addWidget(create_button)

        for label in ("Open Existing", "Open GM Mode"):
            button = QPushButton(label)
            button.clicked.connect(self._not_implemented)
            column.addWidget(button)

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
        summaries = list_saved_characters()
        if not summaries:
            self._library.setWidget(self._empty_label)
            return

        for summary in summaries:
            self._cards_flow.addWidget(CharacterCard(summary))
        self._library.setWidget(self._cards_container)

    def _create_new_character(self) -> None:
        """Open a fresh, editable character sheet in its own window."""
        window = MainWindow(locked=False)
        self._child_windows.append(window)
        window.show()

    def _not_implemented(self) -> None:
        """Placeholder for the not-yet-wired buttons (open existing / GM mode)."""

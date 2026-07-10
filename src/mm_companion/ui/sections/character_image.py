"""The Character Image block: a user-loadable portrait.

Split out of the former base-info block so the portrait can be arranged
independently. The chosen image path is written to the shared :class:`Character`
(``save_character`` later copies it into the workspace and rewrites it to a bare
filename); :attr:`edited` fires on a user change for unsaved-change tracking.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QFileDialog,
    QGroupBox,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from mm_companion.core.character import Character
from mm_companion.core.data_loader import GameData
from mm_companion.core.library import resolve_image_path
from mm_companion.ui.sections.titled_section import strip_groupbox_caption

IMAGE_SIZE = 160


class CharacterImageSection(QGroupBox):
    """A character portrait with a load button, backed by the shared :class:`Character`."""

    edited = Signal()

    def __init__(self, data: GameData, character: Character, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        strip_groupbox_caption(self)

        self._loading = True
        self._data = data
        self._character = character
        self._image_path: str | None = None
        self._locked = False

        layout = QVBoxLayout(self)
        layout.addLayout(self._build_image_column())

        # Reflect any image a loaded character already carries.
        if self._character.image_path:
            self._show_image(resolve_image_path(self._character.image_path))
        self._loading = False

    def _build_image_column(self) -> QVBoxLayout:
        column = QVBoxLayout()

        self._image_label = QLabel("No image")
        self._image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._image_label.setFixedSize(IMAGE_SIZE, IMAGE_SIZE)
        self._image_label.setFrameShape(QLabel.Shape.Box)
        column.addWidget(self._image_label, alignment=Qt.AlignmentFlag.AlignHCenter)

        self._load_button = QPushButton("Load Image…")
        self._load_button.clicked.connect(self._load_image)
        column.addWidget(self._load_button, alignment=Qt.AlignmentFlag.AlignHCenter)

        column.addStretch()
        return column

    def _load_image(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Select character image",
            "",
            "Images (*.png *.jpg *.jpeg *.bmp *.gif *.webp)",
        )
        if path:
            self._set_image(path)

    def _set_image(self, path: str) -> None:
        """Record a newly chosen image on the model and display it (a user edit)."""
        if not self._show_image(path):
            return
        self._image_path = path
        self._character.image_path = path
        if not self._loading:
            self.edited.emit()

    def _show_image(self, path: str | None) -> bool:
        """Render *path* in the image label; return whether it was a valid image."""
        pixmap = QPixmap(path) if path else QPixmap()
        if pixmap.isNull():
            self._image_label.setText("Invalid image" if path else "No image")
            return False

        self._image_label.setPixmap(
            pixmap.scaled(
                IMAGE_SIZE,
                IMAGE_SIZE,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )
        return True

    def set_locked(self, locked: bool) -> None:
        """Hide the image loader while locked; the portrait stays visible."""
        self._locked = locked
        self._load_button.setVisible(not locked)

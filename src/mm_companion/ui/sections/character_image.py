"""The Character Image block: a user-loadable portrait.

Split out of the former base-info block so the portrait can be arranged
independently. The image fills the whole block and rescales with it — keeping its
aspect ratio, so a non-square picture letterboxes rather than crops. The chosen
image path is written to the shared :class:`Character` (``save_character`` later
copies it into the workspace and rewrites it to a bare filename); :attr:`edited`
fires on a user change for unsaved-change tracking.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPixmap, QResizeEvent
from PySide6.QtWidgets import (
    QFileDialog,
    QGroupBox,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from mm_companion.core.character import Character
from mm_companion.core.data_loader import GameData
from mm_companion.core.library import resolve_image_path
from mm_companion.ui.sections.titled_section import strip_groupbox_caption

MIN_IMAGE_SIZE = 120


class ScalingImageLabel(QLabel):
    """A label that scales its source pixmap to fill itself, keeping aspect ratio.

    Holds the original pixmap and re-scales it to the label's current size on every
    resize, so the portrait grows and shrinks with the block. ``Ignored`` size policy
    lets it take whatever space the layout offers without the pixmap's own size
    feeding back into the layout.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._source = QPixmap()
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMinimumSize(MIN_IMAGE_SIZE, MIN_IMAGE_SIZE)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)

    def set_source(self, pixmap: QPixmap) -> None:
        """Show a pixmap, scaled to the current size."""
        self._source = pixmap
        self._rescale()

    def clear_source(self, text: str) -> None:
        """Drop any image and show placeholder text instead."""
        self._source = QPixmap()
        self.setText(text)

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802 - Qt override
        super().resizeEvent(event)
        self._rescale()

    def _rescale(self) -> None:
        if self._source.isNull():
            return
        self.setPixmap(
            self._source.scaled(
                self.size(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )


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
        self._image_label = ScalingImageLabel()
        self._image_label.clear_source("No image")
        self._image_label.setFrameShape(QLabel.Shape.Box)
        layout.addWidget(self._image_label, stretch=1)

        self._load_button = QPushButton("Load Image…")
        self._load_button.clicked.connect(self._load_image)
        layout.addWidget(self._load_button, alignment=Qt.AlignmentFlag.AlignHCenter)

        # Reflect any image a loaded character already carries.
        if self._character.image_path:
            self._show_image(resolve_image_path(self._character.image_path))
        self._loading = False

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
            self._image_label.clear_source("Invalid image" if path else "No image")
            return False
        self._image_label.set_source(pixmap)
        return True

    def set_locked(self, locked: bool) -> None:
        """Hide the image loader while locked; the portrait stays visible."""
        self._locked = locked
        self._load_button.setVisible(not locked)

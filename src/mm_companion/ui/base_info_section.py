"""Section 1: descriptive base information and non-purchasable characteristics."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from mm_companion.core.data_loader import GameData

IMAGE_SIZE = 160


class BaseInfoSection(QGroupBox):
    """Descriptive fields, characteristics that can't be bought with power
    points (size, speed, ...), and a user-loadable character image."""

    def __init__(self, data: GameData, parent: QWidget | None = None) -> None:
        super().__init__("Base Information", parent)

        self._profile_fields: dict[str, QLineEdit] = {}
        self._characteristics: dict[str, QWidget] = {}
        self._image_path: str | None = None

        layout = QHBoxLayout(self)

        # Left: descriptive profile fields.
        profile_form = QFormLayout()
        for f in data.profile_fields:
            edit = QLineEdit()
            self._profile_fields[f.key] = edit
            profile_form.addRow(f"{f.label}:", edit)
        layout.addLayout(profile_form, stretch=2)

        # Middle: non-purchasable characteristics.
        char_form = QFormLayout()
        for c in data.characteristics:
            if c.options:
                widget: QWidget = QComboBox()
                widget.addItems(c.options)
            else:
                widget = QLineEdit()
            self._characteristics[c.key] = widget
            char_form.addRow(f"{c.label}:", widget)
        layout.addLayout(char_form, stretch=1)

        # Right: character image with a load button.
        layout.addLayout(self._build_image_column(), stretch=1)

    def _build_image_column(self) -> QVBoxLayout:
        column = QVBoxLayout()

        self._image_label = QLabel("No image")
        self._image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._image_label.setFixedSize(IMAGE_SIZE, IMAGE_SIZE)
        self._image_label.setFrameShape(QLabel.Shape.Box)
        column.addWidget(self._image_label, alignment=Qt.AlignmentFlag.AlignHCenter)

        load_button = QPushButton("Load Image…")
        load_button.clicked.connect(self._load_image)
        column.addWidget(load_button, alignment=Qt.AlignmentFlag.AlignHCenter)

        column.addStretch()
        return column

    def _load_image(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Select character image",
            "",
            "Images (*.png *.jpg *.jpeg *.bmp *.gif *.webp)",
        )
        if not path:
            return

        pixmap = QPixmap(path)
        if pixmap.isNull():
            self._image_label.setText("Invalid image")
            return

        self._image_path = path
        self._image_label.setPixmap(
            pixmap.scaled(
                IMAGE_SIZE,
                IMAGE_SIZE,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )

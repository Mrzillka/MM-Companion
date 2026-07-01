"""Section 1: descriptive base information and non-purchasable characteristics."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QPushButton,
    QSpinBox,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from mm_companion.core.data_loader import Characteristic, Field, GameData
from mm_companion.ui.flow_layout import FlowLayout
from mm_companion.ui.wheel_guard import guard_wheel

IMAGE_SIZE = 160
CONDITIONS_ROW_HEIGHT = 44


class BaseInfoSection(QGroupBox):
    """Descriptive fields, characteristics that can't be bought with power
    points (size, speed, ...), and a user-loadable character image."""

    def __init__(self, data: GameData, parent: QWidget | None = None) -> None:
        super().__init__("Base Information", parent)

        self._profile_fields: dict[str, QLineEdit] = {}
        self._characteristics: dict[str, QWidget] = {}
        self._pool_current: dict[str, QLabel] = {}
        self._condition_names: list[str] = [c.name for c in data.conditions]
        self._conditions: dict[str, QWidget] = {}
        self._image_path: str | None = None

        layout = QHBoxLayout(self)

        # Left: descriptive profile fields — the few identifying ones stay
        # visible; the rest live in a collapsible group split across two
        # columns so they don't dominate the section.
        layout.addLayout(self._build_profile_column(data), stretch=2)

        # Middle: non-purchasable characteristics.
        char_form = QFormLayout()
        for c in data.characteristics:
            char_form.addRow(f"{c.label}:", self._build_characteristic(c))
        layout.addLayout(char_form, stretch=1)

        # Right: character image with a load button.
        layout.addLayout(self._build_image_column(), stretch=1)

    def _build_characteristic(self, c: Characteristic) -> QWidget:
        """Build the editor for one characteristic, keyed by its ``kind``."""
        if c.kind == "choice":
            combo = QComboBox()
            combo.addItems(c.options)
            if isinstance(c.default, str) and c.default in c.options:
                combo.setCurrentText(c.default)
            guard_wheel(combo)
            self._characteristics[c.key] = combo
            return combo

        if c.kind == "number":
            spin = self._make_spin_box(c)
            self._characteristics[c.key] = spin
            return spin

        if c.kind == "pool":
            # Current (calculated later) shown beside an editable total.
            container = QWidget()
            row = QHBoxLayout(container)
            row.setContentsMargins(0, 0, 0, 0)
            current = QLabel("—")
            current.setToolTip("Spent — calculated from the build")
            total = self._make_spin_box(c)
            total.setToolTip("Total available")
            row.addWidget(current)
            row.addWidget(QLabel("/"))
            row.addWidget(total)
            row.addStretch()
            self._pool_current[c.key] = current
            self._characteristics[c.key] = total
            return container

        edit = QLineEdit()
        if c.default is not None:
            edit.setText(str(c.default))
        self._characteristics[c.key] = edit
        return edit

    @staticmethod
    def _make_spin_box(c: Characteristic) -> QSpinBox:
        spin = QSpinBox()
        spin.setRange(c.minimum, c.maximum)
        if isinstance(c.default, int):
            spin.setValue(c.default)
        guard_wheel(spin)
        return spin

    def set_pool_current(self, key: str, value: object) -> None:
        """Update a pool characteristic's calculated *current* value.

        Placeholder hook for when ``core`` computes spent points; the UI does
        not calculate rules itself.
        """
        label = self._pool_current.get(key)
        if label is not None:
            label.setText(str(value))

    def _add_profile_field(self, form: QFormLayout, field: Field) -> None:
        edit = QLineEdit()
        self._profile_fields[field.key] = edit
        form.addRow(f"{field.label}:", edit)

    def _build_profile_column(self, data: GameData) -> QVBoxLayout:
        column = QVBoxLayout()

        primary = [f for f in data.profile_fields if f.primary]
        secondary = [f for f in data.profile_fields if not f.primary]

        # Always-visible identifying fields.
        primary_form = QFormLayout()
        for f in primary:
            self._add_profile_field(primary_form, f)
        column.addLayout(primary_form)

        # Collapsible group for the remaining details, split into two columns.
        # The fields live in an inner body widget whose visibility we toggle so
        # the group actually collapses (a checkable group only disables).
        self._details_group = QGroupBox("Details")
        self._details_group.setCheckable(True)

        group_layout = QVBoxLayout(self._details_group)
        self._details_body = QWidget()
        body_layout = QHBoxLayout(self._details_body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        left_form = QFormLayout()
        right_form = QFormLayout()
        body_layout.addLayout(left_form)
        body_layout.addLayout(right_form)
        group_layout.addWidget(self._details_body)

        split = (len(secondary) + 1) // 2
        for i, f in enumerate(secondary):
            self._add_profile_field(left_form if i < split else right_form, f)

        self._details_group.toggled.connect(self._details_body.setVisible)
        self._details_group.setChecked(False)  # starts collapsed

        column.addWidget(self._details_group)

        # Under the profile fields: active conditions, added via a "+" button.
        column.addWidget(self._build_conditions_group())
        column.addStretch()
        return column

    def _build_conditions_group(self) -> QGroupBox:
        group = QGroupBox("Conditions")
        outer = QVBoxLayout(group)

        header = QHBoxLayout()
        self._add_condition_button = QToolButton()
        self._add_condition_button.setText("+")
        self._add_condition_button.setToolTip("Add a condition")
        self._add_condition_button.clicked.connect(self._show_condition_menu)
        header.addWidget(self._add_condition_button)
        header.addStretch()
        outer.addLayout(header)

        chips = QWidget()
        # Reserve a row's worth of height so the frame doesn't jump when the
        # first chip is added.
        chips.setMinimumHeight(CONDITIONS_ROW_HEIGHT)
        self._conditions_flow = FlowLayout(chips)
        outer.addWidget(chips)
        return group

    def _show_condition_menu(self) -> None:
        menu = QMenu(self)
        available = [n for n in self._condition_names if n not in self._conditions]
        if available:
            for name in available:
                menu.addAction(name, lambda checked=False, n=name: self._add_condition(n))
        else:
            menu.addAction("All conditions added").setEnabled(False)
        button = self._add_condition_button
        menu.exec(button.mapToGlobal(button.rect().bottomLeft()))

    def _add_condition(self, name: str) -> None:
        if name in self._conditions:
            return

        chip = QFrame()
        chip.setFrameShape(QFrame.Shape.StyledPanel)
        chip_layout = QHBoxLayout(chip)
        chip_layout.setContentsMargins(6, 1, 2, 1)
        chip_layout.setSpacing(2)
        chip_layout.addWidget(QLabel(name))

        remove = QToolButton()
        remove.setText("×")
        remove.setAutoRaise(True)
        remove.setToolTip(f"Remove {name}")
        remove.clicked.connect(lambda: self._remove_condition(name))
        chip_layout.addWidget(remove)

        self._conditions[name] = chip
        self._conditions_flow.addWidget(chip)

    def _remove_condition(self, name: str) -> None:
        chip = self._conditions.pop(name, None)
        if chip is None:
            return
        self._conditions_flow.removeWidget(chip)
        chip.deleteLater()

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

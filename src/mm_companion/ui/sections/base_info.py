"""Section 1: descriptive base information and non-purchasable characteristics."""

from __future__ import annotations

from PySide6.QtCore import QSignalBlocker, Qt, Signal
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
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from mm_companion.core.character import Character
from mm_companion.core.data_loader import Characteristic, Field, GameData
from mm_companion.core.library import resolve_image_path
from mm_companion.core.rules import (
    power_level_for_points,
    reconcile_points_to_level,
)
from mm_companion.ui.lock import set_widget_locked
from mm_companion.ui.sections.titled_section import strip_groupbox_caption
from mm_companion.ui.wheel_guard import guard_wheel
from mm_companion.ui.widgets import make_spin_box

IMAGE_SIZE = 160


class BaseInfoSection(QGroupBox):
    """Descriptive fields, characteristics that can't be bought with power
    points (size, speed, ...), and a user-loadable character image.

    Field and characteristic edits are written to the shared :class:`Character`.
    Emits :attr:`changed` when an edit affects the point build (power level /
    power points) so the sheet can recompute. Conditions now live in their own
    :class:`~mm_companion.ui.sections.conditions.ConditionsSection` block.
    """

    changed = Signal()
    edited = Signal()

    def __init__(self, data: GameData, character: Character, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        strip_groupbox_caption(self)

        # While seeding from a (possibly loaded) character, edits are programmatic,
        # not the user's, so they must not mark the sheet dirty.
        self._loading = True
        self._data = data
        self._character = character
        self._profile_fields: dict[str, QLineEdit] = {}
        self._characteristics: dict[str, QWidget] = {}
        self._pool_current: dict[str, QLabel] = {}
        self._image_path: str | None = None
        self._locked = False

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

        # Reflect any state a loaded character already carries.
        self._seed_from_model()
        self._loading = False

    def _seed_from_model(self) -> None:
        """Render the image from a (possibly loaded) character.

        Profile fields and characteristics seed themselves as they are built; the
        image is populated here since it has no fixed set of widgets to seed. Runs
        while ``_loading`` is set, so it does not mark the sheet dirty.
        """
        if self._character.image_path:
            self._show_image(resolve_image_path(self._character.image_path))

    def _emit_edited(self) -> None:
        """Signal a user edit, unless we're still seeding from the model."""
        if not self._loading:
            self.edited.emit()

    def _build_characteristic(self, c: Characteristic) -> QWidget:
        """Build the editor for one characteristic, keyed by its ``kind``.

        Each editor is seeded from the shared character model (falling back to
        the content default) and writes its value back to it, so a loaded
        character shows its saved characteristics.
        """
        seed = self._seed_value(c)

        if c.kind == "choice":
            combo = QComboBox()
            combo.addItems(c.options)
            if isinstance(seed, str) and seed in c.options:
                combo.setCurrentText(seed)
            combo.currentTextChanged.connect(
                lambda text, key=c.key: self._on_characteristic_changed(key, text)
            )
            guard_wheel(combo)
            self._characteristics[c.key] = combo
            return combo

        if c.kind == "number":
            spin = self._make_spin_box(c, seed)
            spin.valueChanged.connect(
                lambda value, key=c.key: self._on_characteristic_changed(key, value)
            )
            self._characteristics[c.key] = spin
            return spin

        if c.kind == "pool":
            # Current (spent, calculated) shown beside an editable total.
            container = QWidget()
            row = QHBoxLayout(container)
            row.setContentsMargins(0, 0, 0, 0)
            current = QLabel("—")
            current.setToolTip("Spent — calculated from the build")
            total = self._make_spin_box(c, seed)
            total.setToolTip("Total available")
            total.valueChanged.connect(
                lambda value, key=c.key: self._on_characteristic_changed(key, value)
            )
            row.addWidget(current)
            row.addWidget(QLabel("/"))
            row.addWidget(total)
            row.addStretch()
            self._pool_current[c.key] = current
            self._characteristics[c.key] = total
            return container

        edit = QLineEdit()
        if seed is not None:
            edit.setText(str(seed))
        edit.textChanged.connect(lambda text, key=c.key: self._on_characteristic_changed(key, text))
        self._characteristics[c.key] = edit
        return edit

    def _seed_value(self, c: Characteristic) -> object:
        """The value to seed a characteristic editor from: the model's stored
        value if present, otherwise the content default."""
        value = self._character.characteristics.get(c.key)
        return value if value is not None else c.default

    def _on_characteristic_changed(self, key: str, value: object) -> None:
        """Write a characteristic edit back to the model.

        ``power_level`` and the ``power_points`` pool total also update their
        dedicated model fields and signal a build change so spent points refresh.
        The two are linked: Power Level sets the minimum budget, and raising the
        budget past a level's border raises Power Level (see :meth:`_link_pl_pp`).
        """
        self._character.characteristics[key] = value
        if key == "power_level" and isinstance(value, int):
            self._character.power_level = value
            self._link_pl_pp(edited="power_level")
            self.changed.emit()
        elif key == "power_points" and isinstance(value, int):
            self._character.power_points_total = value
            self._link_pl_pp(edited="power_points")
            self.changed.emit()
        self._emit_edited()

    def _link_pl_pp(self, *, edited: str) -> None:
        """Reconcile Power Level and the power-point budget after one of them changed.

        Editing Power Level snaps the budget to that level's band (its minimum, unless
        the budget already sits within the band); editing the budget re-derives Power
        Level from it. Only the *other* field is updated, and silently, so the two
        never fight in a signal loop.
        """
        if edited == "power_level":
            new_pp = reconcile_points_to_level(
                self._character.power_level, self._character.power_points_total, self._data
            )
            if new_pp != self._character.power_points_total:
                self._character.power_points_total = self._set_spin_silently("power_points", new_pp)
        else:
            new_pl = power_level_for_points(self._character.power_points_total, self._data)
            if new_pl != self._character.power_level:
                self._character.power_level = self._set_spin_silently("power_level", new_pl)

    def _set_spin_silently(self, key: str, value: int) -> int:
        """Set a characteristic spin box without re-triggering its change handler.

        Returns the value the widget actually holds (spin-box range may clamp it) and
        keeps the model's ``characteristics`` dict in step. A no-op if the widget is
        missing or not a spin box.
        """
        widget = self._characteristics.get(key)
        if not isinstance(widget, QSpinBox):
            return value
        with QSignalBlocker(widget):
            widget.setValue(value)
            actual = widget.value()
        self._character.characteristics[key] = actual
        return actual

    @staticmethod
    def _make_spin_box(c: Characteristic, seed: object) -> QSpinBox:
        value = seed if isinstance(seed, int) else None
        return make_spin_box(c.minimum, c.maximum, value=value)

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
        edit.setText(self._character.profile.get(field.key, ""))
        edit.textChanged.connect(lambda text, key=field.key: self._on_profile_changed(key, text))
        self._profile_fields[field.key] = edit
        form.addRow(f"{field.label}:", edit)

    def _on_profile_changed(self, key: str, text: str) -> None:
        self._character.profile[key] = text
        self._emit_edited()

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
        column.addStretch()
        return column

    def set_locked(self, locked: bool) -> None:
        """Turn the editable fields into read-only labels (locked) or back, and
        hide the image loader.
        """
        self._locked = locked
        for edit in self._profile_fields.values():
            set_widget_locked(edit, locked)
        for widget in self._characteristics.values():
            set_widget_locked(widget, locked)
        self._load_button.setVisible(not locked)

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
        self._emit_edited()

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

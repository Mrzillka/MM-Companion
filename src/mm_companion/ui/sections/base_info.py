"""The Name & Details block: a character's descriptive profile fields.

The system characteristics (power level, points, size, speed, initiative, hero
points) live in :class:`~mm_companion.ui.sections.system_info.SystemInfoSection`
and the portrait in
:class:`~mm_companion.ui.sections.character_image.CharacterImageSection`; this block
is now just the identifying and descriptive text fields. Edits are written to the
shared :class:`Character` and surfaced via :attr:`edited` for unsaved-change
tracking (they don't affect the point build, so there is no ``changed`` here).
"""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLineEdit,
    QVBoxLayout,
    QWidget,
)

from mm_companion.core.character import Character
from mm_companion.core.data_loader import Field, GameData
from mm_companion.ui.lock import set_widget_locked
from mm_companion.ui.sections.titled_section import strip_groupbox_caption


class BaseInfoSection(QGroupBox):
    """Descriptive profile fields backed by the shared :class:`Character`.

    Emits :attr:`edited` on any user edit so the sheet can track unsaved changes.
    """

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
        self._locked = False

        layout = QVBoxLayout(self)
        layout.addLayout(self._build_profile_column(data))
        self._loading = False

    def _emit_edited(self) -> None:
        """Signal a user edit, unless we're still seeding from the model."""
        if not self._loading:
            self.edited.emit()

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
        """Turn the editable fields into read-only labels (locked) or back."""
        self._locked = locked
        for edit in self._profile_fields.values():
            set_widget_locked(edit, locked)

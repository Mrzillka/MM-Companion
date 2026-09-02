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

from PySide6.QtCore import QSignalBlocker, Signal
from PySide6.QtWidgets import (
    QBoxLayout,
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
from mm_companion.ui.widgets import ReflowingForm


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

    def resizeEvent(self, event) -> None:  # noqa: ANN001, N802 - Qt override
        """Stack the captions above their fields, and put the details in one column.

        Two reflows on the same measurement. The forms wrap their rows once there
        is no room for a caption beside a field; below that the Details group's two
        side-by-side columns become one, because two columns of a wrapped form in a
        narrow block is two slivers rather than one readable list.
        """
        super().resizeEvent(event)
        margins = self.layout().contentsMargins() if self.layout() else None
        inset = (margins.left() + margins.right()) if margins is not None else 0
        available = self.width() - inset
        changed = False
        for form in (self._primary_form, self._left_form, self._right_form):
            changed = form.sync_wrap(available) or changed
        stacked = self._primary_form.wrapped
        if stacked != (self._details_body_layout.direction() == QBoxLayout.Direction.TopToBottom):
            self._details_body_layout.setDirection(
                QBoxLayout.Direction.TopToBottom if stacked else QBoxLayout.Direction.LeftToRight
            )
            changed = True
        if changed:
            self.updateGeometry()

    def _build_profile_column(self, data: GameData) -> QVBoxLayout:
        column = QVBoxLayout()

        primary = [f for f in data.profile_fields if f.primary]
        secondary = [f for f in data.profile_fields if not f.primary]

        # Always-visible identifying fields.
        primary_form = self._primary_form = ReflowingForm()
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
        left_form = self._left_form = ReflowingForm()
        right_form = self._right_form = ReflowingForm()
        body_layout.addLayout(left_form)
        body_layout.addLayout(right_form)
        group_layout.addWidget(self._details_body)

        split = (len(secondary) + 1) // 2
        for i, f in enumerate(secondary):
            self._add_profile_field(left_form if i < split else right_form, f)

        self._details_body_layout = body_layout
        self._details_group.toggled.connect(self._details_body.setVisible)
        self._details_group.setChecked(False)  # starts collapsed

        column.addWidget(self._details_group)
        column.addStretch()
        return column

    def reseed(self) -> None:
        """Restate every field from the model — the sheet put an earlier state back.

        Blocked at each field rather than run under ``_loading``, which only ever
        gated the *signal*: ``_on_profile_changed`` writes to ``profile`` before it
        looks at the flag, so a plain ``setText`` here re-added every key the
        restored state had left out, as ``""``. A reseed must not write the model —
        and that write was not harmless, because ``at_saved_state()`` compares
        canonical JSON, so a character byte-equivalent to its own file read dirty
        for good and every save accreted another empty key.
        """
        for key, edit in self._profile_fields.items():
            blocker = QSignalBlocker(edit)
            edit.setText(self._character.profile.get(key, ""))
            del blocker

    def set_locked(self, locked: bool) -> None:
        """Turn the editable fields into read-only labels (locked) or back."""
        self._locked = locked
        for edit in self._profile_fields.values():
            set_widget_locked(edit, locked)

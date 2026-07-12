"""A generic, data-described sheet block built from a :class:`BlockSpec`.

The built-in blocks are bespoke Python widgets, but a mod can add a block with no
Python at all: it ships a ``blocks.json`` describing a titled group of field/label
rows (see :class:`~mm_companion.core.data_loader.BlockSpec`), and the loader turns
each entry into one of these widgets. The registry binds the spec to the standard
``(data, character)`` block factory (see
:func:`~mm_companion.ui.blocks.registry.sync_declarative_blocks`), so a declarative
block appears on the sheet, floats/hides/rearranges, and locks like any other.

Editable ``"text"`` rows are backed by ``Character.profile[key]`` — the same
free-form string store the Name & Details block writes to — so their values
round-trip through save/load without any new persistence. ``"label"`` rows are
static text. Unknown field kinds render as a static label rather than raising, so
a mod can introduce a new kind (with a matching handler here) incrementally.
"""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QFormLayout, QGroupBox, QLabel, QLineEdit, QVBoxLayout, QWidget

from mm_companion.core.character import Character
from mm_companion.core.data_loader import BlockFieldSpec, BlockSpec, GameData
from mm_companion.ui.lock import set_widget_locked
from mm_companion.ui.sections.titled_section import strip_groupbox_caption


class DeclarativeBlock(QGroupBox):
    """A titled group of field/label rows rendered from a :class:`BlockSpec`.

    Emits :attr:`edited` on any user edit so the sheet can track unsaved changes,
    matching the built-in blocks' contract (plus :meth:`set_locked`).
    """

    edited = Signal()

    def __init__(
        self,
        data: GameData,
        character: Character,
        spec: BlockSpec,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        strip_groupbox_caption(self)

        # Edits made while seeding from the (possibly loaded) model are programmatic,
        # not the user's, so they must not mark the sheet dirty.
        self._loading = True
        self._data = data
        self._character = character
        self._spec = spec
        self._edits: dict[str, QLineEdit] = {}

        layout = QVBoxLayout(self)
        form = QFormLayout()
        for field in spec.fields:
            self._add_row(form, field)
        layout.addLayout(form)
        layout.addStretch()
        self._loading = False

    def _add_row(self, form: QFormLayout, field: BlockFieldSpec) -> None:
        if field.kind == "text":
            edit = QLineEdit()
            edit.setText(self._character.profile.get(field.key, ""))
            edit.textChanged.connect(lambda text, key=field.key: self._on_text_changed(key, text))
            self._edits[field.key] = edit
            form.addRow(f"{field.label}:", edit)
        else:
            # "label" and any unrecognised kind render as static text.
            form.addRow(f"{field.label}:" if field.label else "", QLabel(field.text))

    def _on_text_changed(self, key: str, text: str) -> None:
        self._character.profile[key] = text
        if not self._loading:
            self.edited.emit()

    def set_locked(self, locked: bool) -> None:
        """Turn the editable fields into read-only labels (locked) or back."""
        for edit in self._edits.values():
            set_widget_locked(edit, locked)

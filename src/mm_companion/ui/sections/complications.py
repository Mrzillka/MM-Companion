"""The Complications block: free-form narrative hooks the player can list.

Complications (Motivation, enemy, secret, responsibility, …) are per-character
story hooks that earn hero points in play; they carry no point cost, so this
block is plain text over the shared :class:`Character` model — a name plus a
multiline description per complication, with as many rows as the player wants.

The description box **auto-grows to fit its content** (via
:class:`_AutoHeightTextEdit`) so it honours the sheet's "each block shows all its
content and never scrolls on its own" rule — the block and page grow instead of an
inner scrollbar appearing. Edits are written to the shared :class:`Character` and
surfaced via :attr:`edited` for unsaved-change tracking. Locking turns every field
into plain read-only text rather than a disabled input.
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import (
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from mm_companion.core.character import Character, Complication
from mm_companion.core.data_loader import GameData
from mm_companion.ui.lock import set_widget_locked
from mm_companion.ui.sections.titled_section import strip_groupbox_caption
from mm_companion.ui.wheel_guard import guard_wheel

#: Fewest text lines the description box shows before it starts growing.
MIN_DESC_LINES = 2


class _AutoHeightTextEdit(QTextEdit):
    """A multiline box that reports its full content height so it never scrolls.

    Mirrors the auto-sizing trick the advantages table uses: the vertical
    scrollbar is off and the size hint is the document's content height (clamped to
    a couple of lines), so the box — and the enclosing block — grow as the player
    types instead of the box scrolling internally.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
        self.document().documentLayout().documentSizeChanged.connect(
            lambda _size: self.updateGeometry()
        )

    def _content_height(self) -> int:
        doc = self.document()
        margins = self.contentsMargins()
        frame = 2 * self.frameWidth()
        line = self.fontMetrics().lineSpacing()
        content = doc.documentLayout().documentSize().height() + 2 * doc.documentMargin()
        minimum = MIN_DESC_LINES * line + 2 * doc.documentMargin()
        return int(max(content, minimum)) + margins.top() + margins.bottom() + frame

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt override
        return QSize(super().sizeHint().width(), self._content_height())

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt override
        return QSize(super().minimumSizeHint().width(), self._content_height())

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt override
        # A width change re-wraps the text, changing its height, so re-measure.
        super().resizeEvent(event)
        self.updateGeometry()


@dataclass
class _ComplicationRow:
    """The widgets of one rendered row, mapped back to its backing model object."""

    widget: QWidget
    name: QLineEdit
    description: _AutoHeightTextEdit
    remove: QPushButton
    complication: Complication


class ComplicationsSection(QGroupBox):
    """A growable list of name + multiline-description complications.

    A view over ``Character.complications``: rows seed from the model and write
    straight back to it. Emits :attr:`edited` on any user edit for unsaved-change
    tracking (complications have no point cost, so there is no ``changed``).
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
        self._locked = False
        # Each rendered row keeps references to its widgets and backing Complication
        # so removal is by identity (not position) and locking can reach every field.
        self._row_refs: list[_ComplicationRow] = []

        outer = QVBoxLayout(self)

        self._add_button = QPushButton("Add Complication")
        self._add_button.clicked.connect(self._add_complication)
        add_row = QHBoxLayout()
        add_row.addWidget(self._add_button)
        add_row.addStretch(1)
        outer.addLayout(add_row)

        # The list of complication rows lives in its own container so it can be
        # cleared and rebuilt wholesale.
        self._rows_container = QWidget()
        self._rows_layout = QVBoxLayout(self._rows_container)
        self._rows_layout.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(self._rows_container)

        # An empty-state hint shown when there are no complications yet.
        self._empty_label = QLabel("No complications yet.")
        self._empty_label.setStyleSheet("color: palette(mid);")
        outer.addWidget(self._empty_label)

        outer.addStretch(1)

        self._rebuild()
        self._loading = False

    # -- rebuild / row construction -----------------------------------------

    def _clear_rows(self) -> None:
        self._row_refs.clear()
        while self._rows_layout.count():
            item = self._rows_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def _rebuild(self) -> None:
        """Re-render one row per complication from the model."""
        self._clear_rows()
        for complication in self._character.complications:
            self._render_row(complication)
        self._empty_label.setVisible(not self._character.complications)

    def _render_row(self, complication: Complication) -> None:
        row = QWidget()
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        fields = QVBoxLayout()
        name = QLineEdit(complication.name)
        name.setPlaceholderText("Complication (e.g. Enemy, Motivation, Secret)")
        name.textChanged.connect(lambda text, c=complication: self._on_name_changed(c, text))
        fields.addWidget(name)

        description = _AutoHeightTextEdit()
        description.setPlaceholderText("Description")
        description.setPlainText(complication.description)
        description.textChanged.connect(
            lambda edit=description, c=complication: self._on_description_changed(c, edit)
        )
        guard_wheel(description)
        fields.addWidget(description)
        row_layout.addLayout(fields, stretch=1)

        remove = QPushButton("✕")
        remove.setToolTip("Remove this complication")
        remove.setFixedWidth(28)
        remove.clicked.connect(lambda _checked=False, c=complication: self._remove_complication(c))
        row_layout.addWidget(remove, alignment=Qt.AlignmentFlag.AlignTop)

        self._rows_layout.addWidget(row)
        ref = _ComplicationRow(row, name, description, remove, complication)
        self._row_refs.append(ref)
        if self._locked:
            self._apply_row_lock(ref, locked=True)

    # -- model writes --------------------------------------------------------

    def _on_name_changed(self, complication: Complication, text: str) -> None:
        complication.name = text
        self._emit_edited()

    def _on_description_changed(
        self, complication: Complication, edit: _AutoHeightTextEdit
    ) -> None:
        complication.description = edit.toPlainText()
        self._emit_edited()

    def _add_complication(self) -> None:
        complication = Complication()
        self._character.complications.append(complication)
        self._rebuild()
        self._emit_edited()

    def _remove_complication(self, complication: Complication) -> None:
        self._character.complications = [
            c for c in self._character.complications if c is not complication
        ]
        self._rebuild()
        self._emit_edited()

    def _emit_edited(self) -> None:
        """Signal a user edit, unless we're still seeding from the model."""
        if not self._loading:
            self.edited.emit()

    # -- lock ----------------------------------------------------------------

    def _apply_row_lock(self, ref: _ComplicationRow, *, locked: bool) -> None:
        set_widget_locked(ref.name, locked)
        set_widget_locked(ref.description, locked)
        ref.remove.setVisible(not locked)

    def set_locked(self, locked: bool) -> None:
        """Turn every field into plain read-only text (locked) or back, and hide
        the add/remove controls while locked."""
        self._locked = locked
        self._add_button.setVisible(not locked)
        for ref in self._row_refs:
            self._apply_row_lock(ref, locked=locked)

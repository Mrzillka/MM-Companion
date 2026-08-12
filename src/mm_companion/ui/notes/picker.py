"""Browse the workspace's notes and open one into a Notes block.

Modeless, for the reason :class:`~mm_companion.ui.sections.equipment_picker.EquipmentPickerDialog`
is: opening several notes in a row is the normal case, and a modal dialog makes
that one round trip per note.

The list is *every* note in the workspace, not this character's — a note belongs
to no character (see :mod:`mm_companion.core.notes`), so the same one can be
opened on two sheets. Notes already open in the asking block are marked rather
than hidden, since a block that silently offered fewer notes than the workspace
holds would read as notes having gone missing.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from mm_companion.core import notes
from mm_companion.ui.widgets import muted_style

HELP_TEXT = (
    "Notes are markdown files in your workspace. One can be open on more than one "
    "character's sheet, and any of them can be edited outside the app."
)

_REF_ROLE = Qt.ItemDataRole.UserRole


class NotePickerDialog(QDialog):
    """A list of every note in the workspace, with rename and delete."""

    #: The note reference the user picked.
    noteChosen = Signal(str)
    #: A note's file was renamed (old ref, new ref); anyone holding the old one
    #: should follow, since it no longer resolves.
    noteRenamed = Signal(str, str)
    #: A note's file was deleted; anyone holding it should close it.
    noteDeleted = Signal(str)

    def __init__(self, parent: QWidget | None = None, *, open_refs: tuple[str, ...] = ()) -> None:
        super().__init__(parent)
        self.setWindowTitle("Open a Note")
        self.resize(360, 460)
        self._open_refs = set(open_refs)

        layout = QVBoxLayout(self)

        help_label = QLabel(HELP_TEXT)
        help_label.setWordWrap(True)
        help_label.setStyleSheet(muted_style())
        layout.addWidget(help_label)

        self._filter = QLineEdit()
        self._filter.setPlaceholderText("Filter…")
        self._filter.setClearButtonEnabled(True)
        self._filter.textChanged.connect(self._apply_filter)
        layout.addWidget(self._filter)

        self._list = QListWidget()
        self._list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._list.customContextMenuRequested.connect(self._show_row_menu)
        self._list.itemDoubleClicked.connect(self._choose)
        layout.addWidget(self._list, stretch=1)

        self._empty = QLabel("No notes yet — “New” makes one.")
        self._empty.setStyleSheet(muted_style(italic=True))
        layout.addWidget(self._empty)

        row = QHBoxLayout()
        open_button = QPushButton("Open")
        open_button.clicked.connect(lambda: self._choose(self._list.currentItem()))
        row.addWidget(open_button)
        new_button = QPushButton("New Note")
        new_button.clicked.connect(self._create)
        row.addWidget(new_button)
        row.addStretch(1)
        layout.addLayout(row)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.close)
        layout.addWidget(buttons)

        self.reload()

    # -- content -------------------------------------------------------------

    def set_open_refs(self, refs: tuple[str, ...]) -> None:
        """Tell the picker which notes the asking block already has open."""
        self._open_refs = set(refs)
        self.reload()

    def reload(self) -> None:
        """Re-read the workspace. Cheap, and the notes dir is editable outside us."""
        self._list.clear()
        summaries = notes.list_notes()
        for summary in summaries:
            label = summary.title
            if summary.ref in self._open_refs:
                label = f"{label}  (open)"
            item = QListWidgetItem(label)
            item.setData(_REF_ROLE, summary.ref)
            item.setToolTip(summary.ref)
            self._list.addItem(item)
        self._empty.setVisible(not summaries)
        self._apply_filter(self._filter.text())

    def _apply_filter(self, text: str) -> None:
        needle = text.strip().lower()
        for index in range(self._list.count()):
            item = self._list.item(index)
            hay = f"{item.text()} {item.data(_REF_ROLE)}".lower()
            item.setHidden(bool(needle) and needle not in hay)

    # -- actions -------------------------------------------------------------

    def _choose(self, item: QListWidgetItem | None) -> None:
        if item is not None:
            self.noteChosen.emit(str(item.data(_REF_ROLE)))

    def _create(self) -> None:
        title, ok = QInputDialog.getText(self, "New Note", "Title:", text="Untitled Note")
        if not ok:
            return
        ref = notes.create_note(title)
        self.reload()
        self.noteChosen.emit(ref)

    def _show_row_menu(self, pos) -> None:
        item = self._list.itemAt(pos)
        if item is None:
            return
        ref = str(item.data(_REF_ROLE))
        menu = QMenu(self._list)
        menu.addAction("Open").triggered.connect(lambda: self._choose(item))
        menu.addSeparator()
        menu.addAction("Rename…").triggered.connect(lambda: self._rename(ref))
        menu.addAction("Delete…").triggered.connect(lambda: self._delete(ref))
        menu.exec(self._list.mapToGlobal(pos))

    def _rename(self, ref: str) -> None:
        title, ok = QInputDialog.getText(self, "Rename Note", "Title:", text=notes.note_title(ref))
        if not ok or not title.strip():
            return
        new_ref = notes.rename_note(ref, title)
        self.reload()
        if new_ref != ref:
            # Whoever has it open is holding the old filename, which no longer
            # resolves; the block listens for this and follows the rename.
            self.noteRenamed.emit(ref, new_ref)

    def _delete(self, ref: str) -> None:
        confirm = QMessageBox.question(
            self,
            "Delete Note",
            f"Delete “{notes.note_title(ref)}”?\n\nThe file is removed from your workspace. "
            "Any sheet with it open will show it as missing.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return
        notes.delete_note(ref)
        self.reload()
        self.noteDeleted.emit(ref)

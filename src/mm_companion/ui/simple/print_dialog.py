"""What to print: one checkbox per block of the simple sheet, before a print or a PDF.

Asked every time rather than hidden in Settings, because it is decided per print —
notes for the GM's copy, no notes for the table — but **remembered**, so the answer is
already ticked the next time and printing the usual way is two clicks. Each block
defaults to its own ``printable`` (the roller and the Scene off, everything else on);
only the choices a player actually changes are stored
(:func:`~mm_companion.core.storage.set_simple_print_choices`).
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from mm_companion.core import storage
from mm_companion.ui.blocks.base import instance_template
from mm_companion.ui.simple.printing import print_candidates, prints_by_default
from mm_companion.ui.widgets import muted_style


class PrintChoiceDialog(QDialog):
    """Pick the blocks a print carries. :meth:`keys` is the answer once accepted."""

    def __init__(self, sheet, parent: QWidget | None = None, *, action: str = "Print") -> None:
        super().__init__(parent)
        self.setWindowTitle("What to print")
        self._boxes: dict[str, QCheckBox] = {}

        layout = QVBoxLayout(self)
        note = QLabel(
            "The simple sheet, in its current layout. Blocks with nothing in them are left out."
        )
        note.setWordWrap(True)
        note.setStyleSheet(muted_style(italic=True))
        layout.addWidget(note)
        for key in print_candidates(sheet):
            # "&" doubled: a checkbox reads a single one as a shortcut marker, and
            # "Name & Details" came out as "Name  Details".
            box = QCheckBox(sheet.block_frame(key).base_title.replace("&", "&&"))
            box.setChecked(prints_by_default(key))
            box.toggled.connect(self._sync_ok)
            layout.addWidget(box)
            self._boxes[key] = box

        quick = QHBoxLayout()
        for label, state in (("All", True), ("None", False)):
            button = QPushButton(label)
            button.clicked.connect(lambda _checked=False, s=state: self._set_all(s))
            quick.addWidget(button)
        quick.addStretch()
        layout.addLayout(quick)

        self._buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self._buttons.button(QDialogButtonBox.StandardButton.Ok).setText(action)
        self._buttons.accepted.connect(self.accept)
        self._buttons.rejected.connect(self.reject)
        layout.addWidget(self._buttons)
        self._sync_ok()

    def keys(self) -> list[str]:
        """The ticked blocks, in the sheet's order."""
        return [key for key, box in self._boxes.items() if box.isChecked()]

    def _set_all(self, checked: bool) -> None:
        for box in self._boxes.values():
            box.setChecked(checked)

    def _sync_ok(self) -> None:
        """Nothing ticked is nothing to print — the button says so by standing down."""
        ok = self._buttons.button(QDialogButtonBox.StandardButton.Ok)
        ok.setEnabled(any(box.isChecked() for box in self._boxes.values()))

    def accept(self) -> None:
        """Remember the answer for the next print, by kind of block."""
        storage.set_simple_print_choices(
            {instance_template(key): box.isChecked() for key, box in self._boxes.items()}
        )
        super().accept()


def ask_what_to_print(sheet, parent: QWidget | None = None, *, action: str = "Print"):
    """Show the choice; the ticked keys, or ``None`` if the player cancelled."""
    dialog = PrintChoiceDialog(sheet, parent, action=action)
    if dialog.exec() != QDialog.DialogCode.Accepted:
        return None
    return dialog.keys()

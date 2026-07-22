"""Dialogs for getting into someone else's session.

The join dialog asks for the three things a player supplies: the **join code**
the GM sent them, the **name** they want on the GM's card, and **which saved
character** they are bringing. The code is validated here — a typo draws "that
join code has a typo in it" from
:func:`~mm_companion.core.session.discovery.decode_join_code` rather than a
connection attempt against a wrong address — so a caller that gets an accepted
dialog gets a real :class:`~mm_companion.core.session.discovery.JoinCode`.

The name and the code are remembered in settings (``session_player_name`` /
``session_recent_codes``) so a table that plays weekly retypes neither.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QVBoxLayout,
    QWidget,
)

from mm_companion.core import storage
from mm_companion.core.library import list_saved_characters
from mm_companion.core.session import discovery
from mm_companion.ui import theme

#: How many codes the dialog offers back; the newest is pre-filled.
RECENT_CODES = 5
#: The picker entry for joining without a character (a spectator, or a player
#: whose sheet is not built yet). The GM's card then shows the name alone.
NO_CHARACTER = "(no character)"


class JoinSessionDialog(QDialog):
    """Join code, display name, and the character to bring."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Join Session")
        self._code: discovery.JoinCode | None = None

        settings = storage.load_settings()
        recent = [str(c) for c in settings.get("session_recent_codes", []) if c]

        layout = QVBoxLayout(self)
        form = QFormLayout()

        self._code_edit = QComboBox()
        self._code_edit.setEditable(True)
        self._code_edit.addItems(recent[:RECENT_CODES])
        self._code_edit.setCurrentText(recent[0] if recent else "")
        self._code_edit.lineEdit().setPlaceholderText("the code your GM sent you")
        form.addRow("Join code", self._code_edit)

        self._name_edit = QLineEdit(str(settings.get("session_player_name", "")))
        self._name_edit.setPlaceholderText("the name the GM sees")
        form.addRow("Your name", self._name_edit)

        self._character_box = QComboBox()
        self._character_box.addItem(NO_CHARACTER, None)
        for summary in list_saved_characters():
            if summary.path is not None:
                self._character_box.addItem(f"{summary.name} (PL {summary.power_level})", summary)
        form.addRow("Character", self._character_box)
        layout.addLayout(form)

        self._problem = QLabel("")
        self._problem.setWordWrap(True)
        self._problem.setStyleSheet(f"color: {theme.TINT_WORSE};")
        self._problem.hide()
        layout.addWidget(self._problem)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Join")
        buttons.accepted.connect(self._try_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self._buttons = buttons

    # -- what the caller reads back ----------------------------------------

    def join_code(self) -> discovery.JoinCode:
        """The decoded code. Only meaningful once the dialog was accepted."""
        if self._code is None:
            raise RuntimeError("no valid join code was entered")
        return self._code

    def display_name(self) -> str:
        return self._name_edit.text().strip()

    def character_path(self) -> Path | None:
        """The chosen character's file, or ``None`` for "no character"."""
        summary = self._character_box.currentData()
        return Path(summary.path) if summary is not None and summary.path else None

    # -- validation --------------------------------------------------------

    def _try_accept(self) -> None:
        """Validate before closing, so a bad code is corrected here, not later."""
        name = self.display_name()
        if not name:
            self._show_problem("Enter the name you want the GM to see.")
            return
        text = self._code_edit.currentText().strip()
        if not text:
            self._show_problem("Paste the join code your GM sent you.")
            return
        try:
            self._code = discovery.decode_join_code(text)
        except discovery.JoinCodeError as exc:
            self._show_problem(str(exc))
            return
        self._remember(name, text)
        self.accept()

    def _show_problem(self, message: str) -> None:
        self._problem.setText(message)
        self._problem.show()

    @staticmethod
    def _remember(name: str, code: str) -> None:
        """Keep the name and the code for next time; failing to is not worth raising."""
        settings = storage.load_settings()
        recent = [str(c) for c in settings.get("session_recent_codes", []) if c and c != code]
        try:
            storage.update_settings(
                session_player_name=name,
                session_recent_codes=[code, *recent][:RECENT_CODES],
            )
        except OSError:
            pass

"""Dialogs for starting or getting into a session.

:class:`HostSessionDialog` is the GM's "start a session" step: a session name and
one plain question — *how do players connect?* — with the networking knobs (port,
relay, a typed tunnel address) tucked behind an **Advanced** group most tables
never open. It only *collects* the choices; the GM window does the hosting and
shows the join code, so the dialog stays a short form rather than a live console.

:class:`JoinSessionDialog` asks for the three things a player supplies: the **join
code** the GM sent them, the **name** they want on the GM's card, and **which saved
character** they are bringing. The code is validated here — a typo draws "that
join code has a typo in it" from
:func:`~mm_companion.core.session.discovery.decode_join_code` rather than a
connection attempt against a wrong address — so a caller that gets an accepted
dialog gets a real :class:`~mm_companion.core.session.discovery.JoinCode`.

The name and the code are remembered in settings (``session_player_name`` /
``session_recent_codes``) so a table that plays weekly retypes neither.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLayout,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

from mm_companion.core import storage
from mm_companion.core.library import list_saved_characters
from mm_companion.core.session import discovery, store
from mm_companion.core.session.net import DEFAULT_PORT
from mm_companion.ui import theme
from mm_companion.ui.widgets import make_spin_box

#: How many codes the dialog offers back; the newest is pre-filled.
RECENT_CODES = 5
#: The picker entry for joining without a character (a spectator, or a player
#: whose sheet is not built yet). The GM's card then shows the name alone.
NO_CHARACTER = "(no character)"

#: The three ways a player can reach the GM, in the order the dialog offers them.
CONNECT_AUTOMATIC = "automatic"
CONNECT_RELAY = "relay"
CONNECT_TUNNEL = "tunnel"


@dataclass(frozen=True)
class HostOptions:
    """The choices :class:`HostSessionDialog` collects, for the GM window to host with."""

    name: str
    port: int
    tunnel: str
    relay: str
    use_relay: bool


class HostSessionDialog(QDialog):
    """The GM's "start a session" form: a name and how players connect.

    A short, opinionated form — *Automatic* is the default and needs nothing else
    filled in. Choosing a relay or a typed tunnel address reveals only the one field
    that method needs; the port lives under **Advanced**. Read the result back with
    :meth:`options` after the dialog is accepted.
    """

    def __init__(self, parent: QWidget | None = None, *, session_name: str = "Session") -> None:
        super().__init__(parent)
        self.setWindowTitle("Start a session")
        self.setMinimumWidth(360)

        layout = QVBoxLayout(self)
        # Grow and shrink to fit as the tunnel row appears/disappears, so revealing
        # it never overlaps the Advanced group below (the dialog is re-fitted in
        # _sync_method).
        layout.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)

        form = QFormLayout()
        self._name_edit = QLineEdit(session_name)
        self._name_edit.setPlaceholderText("what your players see the session called")
        form.addRow("Name", self._name_edit)
        layout.addLayout(form)

        layout.addWidget(QLabel("How do players connect?"))
        self._method = QButtonGroup(self)
        self._automatic = QRadioButton("Automatically (recommended)")
        self._automatic.setChecked(True)
        self._via_relay = QRadioButton("Through a relay")
        self._via_tunnel = QRadioButton("I have a tunnel or public address")
        for index, button in enumerate((self._automatic, self._via_relay, self._via_tunnel)):
            self._method.addButton(button, index)
            layout.addWidget(button)

        hint = QLabel(
            "Automatic tries your router, then falls back to a relay if this machine "
            "cannot be reached — most tables need nothing more. Either way you get a "
            "join code to send your players."
        )
        hint.setWordWrap(True)
        hint.setEnabled(False)
        layout.addWidget(hint)

        method_form = QFormLayout()
        self._tunnel_edit = QLineEdit()
        self._tunnel_edit.setPlaceholderText("e.g. 147.185.221.23:12345 — what your tunnel shows")
        self._tunnel_row = _FormRow(method_form, "Tunnel address", self._tunnel_edit)
        layout.addLayout(method_form)

        # An "Advanced" group, unchecked (so its body is greyed) by default: the port
        # and a relay address are for the rare table that needs them, out of the way
        # of the one question that matters above.
        advanced = QGroupBox("Advanced")
        advanced.setCheckable(True)
        advanced.setChecked(False)
        advanced_form = QFormLayout(advanced)
        self._port_spin = make_spin_box(0, 65535, value=DEFAULT_PORT)
        self._port_spin.setSpecialValueText("automatic")
        advanced_form.addRow("Port", self._port_spin)
        self._relay_edit = QLineEdit(storage.relay_url())
        self._relay_edit.setPlaceholderText("e.g. relay.example.net — a relay to fall back to")
        advanced_form.addRow("Relay address", self._relay_edit)
        layout.addWidget(advanced)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Start hosting")
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._method.idToggled.connect(lambda *_: self._sync_method())
        self._sync_method()

    def _sync_method(self) -> None:
        """Show only the field the chosen method needs (the tunnel address, or none)."""
        self._tunnel_row.set_visible(self._via_tunnel.isChecked())
        # Re-fit now that a row appeared or vanished, or the freed/needed space would
        # leave the layout stale and the fields overlapping.
        self.adjustSize()

    def _on_accept(self) -> None:
        """Persist the relay for next time, then close; the GM window does the hosting."""
        try:
            storage.update_settings(session_relay_url=self._relay_edit.text().strip())
        except OSError:
            pass
        self.accept()

    def connect_method(self) -> str:
        """Which of :data:`CONNECT_AUTOMATIC` / ``_RELAY`` / ``_TUNNEL`` is chosen."""
        if self._via_tunnel.isChecked():
            return CONNECT_TUNNEL
        if self._via_relay.isChecked():
            return CONNECT_RELAY
        return CONNECT_AUTOMATIC

    def options(self) -> HostOptions:
        """The chosen host options; meaningful once the dialog was accepted.

        A typed tunnel address is taken at the GM's word, so no relay fallback is
        armed for it; the automatic and relay methods both keep the relay as the last
        rung of the ladder.
        """
        method = self.connect_method()
        tunnel = self._tunnel_edit.text().strip() if method == CONNECT_TUNNEL else ""
        return HostOptions(
            name=self._name_edit.text().strip() or "Session",
            port=self._port_spin.value(),
            tunnel=tunnel,
            relay=self._relay_edit.text().strip(),
            use_relay=method != CONNECT_TUNNEL,
        )


class _FormRow:
    """A show/hide handle over one :class:`QFormLayout` row (its label and field)."""

    def __init__(self, form: QFormLayout, label: str, field: QWidget) -> None:
        self._label = QLabel(label)
        self._field = field
        form.addRow(self._label, field)

    def set_visible(self, visible: bool) -> None:
        self._label.setVisible(visible)
        self._field.setVisible(visible)


class JoinSessionDialog(QDialog):
    """Join code, display name, and (optionally) the character to bring.

    ``pick_character=False`` drops the character picker for the caller that already
    has a character open — joining from a sheet brings *that* sheet, so choosing one
    would be a second, contradictory choice.
    """

    def __init__(self, parent: QWidget | None = None, *, pick_character: bool = True) -> None:
        super().__init__(parent)
        self.setWindowTitle("Join Session")
        # A comfortable floor so the join-code combo and character names are not
        # clipped and the dialog never opens cramped.
        self.setMinimumWidth(420)
        self._code: discovery.JoinCode | None = None
        self._character_box: QComboBox | None = None

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

        if pick_character:
            self._character_box = QComboBox()
            self._character_box.addItem(NO_CHARACTER, None)
            for summary in list_saved_characters():
                if summary.path is not None:
                    self._character_box.addItem(
                        f"{summary.name} (PL {summary.power_level})", summary
                    )
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
        """The chosen character's file, or ``None`` for "no character" / no picker."""
        if self._character_box is None:
            return None
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


class SessionPickerDialog(QDialog):
    """Pick one of the GM's previous sessions to re-run, or delete one.

    Reads the workspace's saved sessions (``store.list_sessions``) and hands back
    the chosen id through :meth:`chosen_id`; deletion happens in place through
    ``store.delete_session`` so the list a GM sees is always the disk truth.
    """

    def __init__(self, *, current_id: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Previous sessions")
        self.resize(460, 320)
        self._current_id = current_id
        self._chosen_id: str | None = None

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Choose a session to run as GM:"))

        self._list = QListWidget()
        self._list.itemDoubleClicked.connect(lambda _item: self._accept_selection())
        layout.addWidget(self._list)

        row = QHBoxLayout()
        self._delete_button = QPushButton("Delete")
        self._delete_button.clicked.connect(self._delete_selected)
        row.addWidget(self._delete_button)
        row.addStretch()
        layout.addLayout(row)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Open | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._accept_selection)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._reload()

    def chosen_id(self) -> str | None:
        """The id the GM chose to open, or ``None`` if they cancelled."""
        return self._chosen_id

    def _reload(self) -> None:
        """(Re)fill the list from disk, keeping the current session marked."""
        self._list.clear()
        for summary in store.list_sessions():
            when = summary.updated_at[:16].replace("T", " ")
            mark = "  — current" if summary.id == self._current_id else ""
            label = (
                f"{summary.name or 'Session'}"
                f"   ({when} · {summary.player_count} player(s) · {summary.roll_count} roll(s))"
                f"{mark}"
            )
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, summary.id)
            self._list.addItem(item)
        if self._list.count():
            self._list.setCurrentRow(0)
        self._delete_button.setEnabled(self._list.count() > 0)

    def _selected_id(self) -> str | None:
        item = self._list.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item is not None else None

    def _accept_selection(self) -> None:
        session_id = self._selected_id()
        if session_id:
            self._chosen_id = session_id
            self.accept()

    def _delete_selected(self) -> None:
        session_id = self._selected_id()
        if not session_id:
            return
        confirm = QMessageBox.question(
            self,
            "Delete session",
            "Delete this session and its roll history? This cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return
        store.delete_session(session_id)
        self._reload()

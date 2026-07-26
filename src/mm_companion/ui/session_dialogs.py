"""Dialogs for starting or getting into a session.

:class:`HostOptionsForm` is the GM's connection choices — a session name and one
plain question, *how do players connect?*, with the networking knobs (port, relay,
a typed tunnel address) tucked behind an **Advanced** group most tables never open.
It only *collects* the choices; the caller does the hosting. :class:`HostSessionDialog`
wraps it as a standalone dialog, and :class:`GMSessionLaunchDialog` — the launcher's
**Open GM Mode** pre-stage — pairs it with a table of previous sessions, so the GM
picks which session to run (or starts a new one) and how to host it *before* the GM
window opens; the window itself carries only a **Copy join code** action.

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
    QAbstractItemView,
    QButtonGroup,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLayout,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from mm_companion.core import storage
from mm_companion.core.library import list_saved_characters
from mm_companion.core.session import discovery, store
from mm_companion.core.session.net import DEFAULT_PORT
from mm_companion.ui import theme
from mm_companion.ui.widgets import hline_separator, make_spin_box

#: How many codes the dialog offers back; the newest is pre-filled.
RECENT_CODES = 5
#: The picker entry for joining without a character (a spectator, or a player
#: whose sheet is not built yet). The GM's card then shows the name alone.
NO_CHARACTER = "(no character)"

#: The three ways a player can reach the GM, in the order the dialog offers them.
CONNECT_AUTOMATIC = "automatic"
CONNECT_RELAY = "relay"
CONNECT_TUNNEL = "tunnel"

#: How many joined sessions the player-side history keeps.
HISTORY_LIMIT = 12


def load_session_history() -> list[dict]:
    """The player's joined-session history, most recent first.

    Reads the rich ``session_history`` list, and folds in any legacy
    ``session_recent_codes`` (code-only) that are not already represented, so a
    user upgrading from the old flat list still sees their previous codes.
    """
    settings = storage.load_settings()
    history: list[dict] = []
    seen: set[str] = set()
    for entry in settings.get("session_history", []):
        if isinstance(entry, dict) and entry.get("code") and entry["code"] not in seen:
            history.append(dict(entry))
            seen.add(entry["code"])
    for code in settings.get("session_recent_codes", []):
        code = str(code)
        if code and code not in seen:
            history.append({"code": code, "session_name": "", "display_name": ""})
            seen.add(code)
    return history


def record_session_history(
    *,
    code: str,
    session_id: str = "",
    session_name: str = "",
    display_name: str = "",
    player_id: str = "",
    player_token: str = "",
) -> None:
    """Remember a joined session (newest first, one row per code), for the picker.

    The ``player_id`` / ``player_token`` are what let a return visit reclaim the
    same roster slot. Keyed by ``code``: rejoining the same code updates its row
    rather than adding a second. Failing to persist is not worth raising over.
    """
    from datetime import datetime, timezone

    if not code:
        return
    settings = storage.load_settings()
    rest = [
        entry
        for entry in settings.get("session_history", [])
        if isinstance(entry, dict) and entry.get("code") and entry["code"] != code
    ]
    entry = {
        "code": code,
        "session_id": session_id,
        "session_name": session_name,
        "display_name": display_name,
        "player_id": player_id,
        "player_token": player_token,
        "last_joined": datetime.now(timezone.utc).isoformat(),
    }
    try:
        storage.update_settings(session_history=[entry, *rest][:HISTORY_LIMIT])
    except OSError:
        pass


def remove_session_history(code: str) -> None:
    """Drop one joined session (by its code) from the player's history."""
    settings = storage.load_settings()
    rest = [
        entry
        for entry in settings.get("session_history", [])
        if isinstance(entry, dict) and entry.get("code") and entry["code"] != code
    ]
    # Also strip the legacy convenience list, so a deleted code does not linger.
    recent = [str(c) for c in settings.get("session_recent_codes", []) if str(c) != code]
    try:
        storage.update_settings(session_history=rest, session_recent_codes=recent)
    except OSError:
        pass


@dataclass(frozen=True)
class HostOptions:
    """The choices :class:`HostSessionDialog` collects, for the GM window to host with."""

    name: str
    port: int
    tunnel: str
    relay: str
    use_relay: bool


class HostOptionsForm(QWidget):
    """The GM's connection choices: a session name and how players connect.

    A short, opinionated form — *Automatic* is the default and needs nothing else
    filled in. Choosing a relay or a typed tunnel address reveals only the one field
    that method needs; the port lives under **Advanced**. It only *collects* the
    choices (:meth:`options`); the caller does the hosting. Shared by
    :class:`HostSessionDialog` and :class:`GMSessionLaunchDialog`.
    """

    def __init__(self, parent: QWidget | None = None, *, session_name: str = "Session") -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        # Grow and shrink to fit as the tunnel row appears/disappears, so revealing
        # it never overlaps the Advanced group below (re-fitted in _sync_method).
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

        self._method.idToggled.connect(lambda *_: self._sync_method())
        self._sync_method()

    def set_name(self, name: str) -> None:
        """Fill the name field (e.g. from a chosen previous session)."""
        self._name_edit.setText(name)

    def _sync_method(self) -> None:
        """Show only the field the chosen method needs (the tunnel address, or none)."""
        self._tunnel_row.set_visible(self._via_tunnel.isChecked())
        # Re-fit the enclosing dialog now that a row appeared or vanished, or the
        # freed/needed space would leave the layout stale and the fields overlapping.
        window = self.window()
        if window is not None:
            window.adjustSize()

    def persist_relay(self) -> None:
        """Remember the relay address for next time; failing to is not worth raising."""
        try:
            storage.update_settings(session_relay_url=self._relay_edit.text().strip())
        except OSError:
            pass

    def connect_method(self) -> str:
        """Which of :data:`CONNECT_AUTOMATIC` / ``_RELAY`` / ``_TUNNEL`` is chosen."""
        if self._via_tunnel.isChecked():
            return CONNECT_TUNNEL
        if self._via_relay.isChecked():
            return CONNECT_RELAY
        return CONNECT_AUTOMATIC

    def options(self) -> HostOptions:
        """The chosen host options.

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


class HostSessionDialog(QDialog):
    """The GM's "start a session" form as a standalone dialog around
    :class:`HostOptionsForm`. Read the result back with :meth:`options`.
    """

    def __init__(self, parent: QWidget | None = None, *, session_name: str = "Session") -> None:
        super().__init__(parent)
        self.setWindowTitle("Start a session")
        self.setMinimumWidth(360)

        layout = QVBoxLayout(self)
        layout.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
        self._form = HostOptionsForm(self, session_name=session_name)
        layout.addWidget(self._form)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Start hosting")
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _on_accept(self) -> None:
        """Persist the relay for next time, then close; the caller does the hosting."""
        self._form.persist_relay()
        self.accept()

    def connect_method(self) -> str:
        return self._form.connect_method()

    def options(self) -> HostOptions:
        return self._form.options()


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
        self.setMinimumWidth(440)
        self._code: discovery.JoinCode | None = None
        self._character_box: QComboBox | None = None
        # The slot to reclaim if a history row is chosen and its code is unchanged.
        self._reclaim: tuple[str, str] = ("", "")
        self._reclaim_code = ""

        settings = storage.load_settings()
        history = load_session_history()

        layout = QVBoxLayout(self)

        # The sessions this player has joined before — pick one to rejoin
        # (reclaiming the same seat), or forget one that has gone stale. A table so
        # the session, when it was last joined, and the name used all read cleanly.
        self._history_table: QTableWidget | None = None
        self._history = history
        if history:
            layout.addWidget(QLabel("Rejoin a previous session:"))
            self._history_table = QTableWidget(len(history), 3)
            self._history_table.setHorizontalHeaderLabels(["Session", "Last joined", "Your name"])
            self._history_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
            self._history_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
            self._history_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
            self._history_table.verticalHeader().setVisible(False)
            table_header = self._history_table.horizontalHeader()
            table_header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
            table_header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
            table_header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
            for row, entry in enumerate(history):
                self._fill_history_row(row, entry)
            self._history_table.itemSelectionChanged.connect(self._on_history_selected)
            self._history_table.itemDoubleClicked.connect(lambda _item: self._try_accept())
            self._history_table.setMaximumHeight(160)
            layout.addWidget(self._history_table)

            delete_row = QHBoxLayout()
            delete_row.addStretch()
            self._forget_button = QPushButton("Forget")
            self._forget_button.setToolTip("Remove the selected session from this list")
            self._forget_button.clicked.connect(self._forget_selected)
            delete_row.addWidget(self._forget_button)
            layout.addLayout(delete_row)

        form = QFormLayout()

        newest_code = history[0]["code"] if history else ""
        self._code_edit = QLineEdit(newest_code)
        self._code_edit.setPlaceholderText("the code your GM sent you")
        # Typing a fresh code means this is not a return to the selected session,
        # so the reclaimed seat no longer applies.
        self._code_edit.textEdited.connect(lambda _text: self._clear_reclaim())
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

    def code_text(self) -> str:
        """The raw join-code text entered — the key the history is recorded under."""
        return self._code_edit.text().strip()

    def reclaim_ids(self) -> tuple[str, str]:
        """``(player_id, player_token)`` to reclaim a seat, or ``("", "")``.

        Only meaningful when a history row was chosen and its code is unchanged —
        those ids belong to that one session and would be refused by any other.
        """
        if self.code_text() == self._reclaim_code:
            return self._reclaim
        return ("", "")

    # -- previous sessions -------------------------------------------------

    def _fill_history_row(self, row: int, entry: dict) -> None:
        """Lay one previous session across the table's three columns."""
        name = entry.get("session_name") or "(unnamed session)"
        when = str(entry.get("last_joined", ""))[:16].replace("T", " ")
        who = str(entry.get("display_name", ""))
        for col, text in enumerate((name, when, who)):
            item = QTableWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, entry)  # the row's whole entry
            self._history_table.setItem(row, col, item)

    def _selected_history_entry(self) -> dict | None:
        if self._history_table is None:
            return None
        rows = self._history_table.selectionModel().selectedRows()
        if not rows:
            return None
        item = self._history_table.item(rows[0].row(), 0)
        return item.data(Qt.ItemDataRole.UserRole) if item is not None else None

    def _on_history_selected(self) -> None:
        """Fill the code and name from a chosen previous session, and arm reclaim."""
        entry = self._selected_history_entry()
        if entry is None:
            return
        code = str(entry.get("code", ""))
        self._code_edit.setText(code)
        if entry.get("display_name"):
            self._name_edit.setText(str(entry["display_name"]))
        self._reclaim = (str(entry.get("player_id", "")), str(entry.get("player_token", "")))
        self._reclaim_code = code

    def _clear_reclaim(self) -> None:
        self._reclaim = ("", "")
        self._reclaim_code = ""

    def _forget_selected(self) -> None:
        """Remove the selected previous session from the table and from settings."""
        if self._history_table is None:
            return
        rows = self._history_table.selectionModel().selectedRows()
        if not rows:
            return
        row = rows[0].row()
        entry = self._selected_history_entry() or {}
        remove_session_history(str(entry.get("code", "")))
        self._history_table.removeRow(row)

    # -- validation --------------------------------------------------------

    def _try_accept(self) -> None:
        """Validate before closing, so a bad code is corrected here, not later."""
        name = self.display_name()
        if not name:
            self._show_problem("Enter the name you want the GM to see.")
            return
        text = self.code_text()
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


class GMSessionLaunchDialog(QDialog):
    """The GM's pre-stage, opened from the launcher's **Open GM Mode**.

    Combines the list of previous sessions with the host form: the GM continues a
    stored session (or starts a new one) and chooses how players connect, all
    before the GM window opens. On accept the launcher opens the window on the
    chosen session and starts hosting straight away — the window itself carries no
    session controls, only a **Copy join code** action.

    Read back :meth:`chosen_session_id` (``None`` for a new session) and
    :meth:`options` after the dialog is accepted.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Open GM Mode")
        self.resize(500, 560)
        self._chosen_id: str | None = None
        self._summaries: list[store.SessionSummary] = []

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Continue a previous session, or start a new one:"))

        self._table = QTableWidget(0, 4)
        self._table.setHorizontalHeaderLabels(["Session", "Last updated", "Players", "Rolls"])
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.verticalHeader().setVisible(False)
        header = self._table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for col in (1, 2, 3):
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
        self._table.itemSelectionChanged.connect(self._on_row_selected)
        self._table.itemDoubleClicked.connect(lambda _item: self._on_accept())
        layout.addWidget(self._table, stretch=1)

        buttons_row = QHBoxLayout()
        self._new_button = QPushButton("New session")
        self._new_button.setToolTip("Start a fresh session instead of continuing one")
        self._new_button.clicked.connect(self._start_new)
        buttons_row.addWidget(self._new_button)
        self._delete_button = QPushButton("Delete")
        self._delete_button.setToolTip("Delete the selected session and its roll history")
        self._delete_button.clicked.connect(self._delete_selected)
        buttons_row.addWidget(self._delete_button)
        buttons_row.addStretch()
        layout.addLayout(buttons_row)

        layout.addWidget(hline_separator())
        self._form = HostOptionsForm(self)
        layout.addWidget(self._form)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Open")
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._reload()

    # -- what the caller reads back ----------------------------------------

    def chosen_session_id(self) -> str | None:
        """The stored session to resume, or ``None`` to start a fresh one."""
        return self._chosen_id

    def options(self) -> HostOptions:
        """How to host, from the embedded :class:`HostOptionsForm`."""
        return self._form.options()

    # -- the previous-sessions table ---------------------------------------

    def _reload(self) -> None:
        self._summaries = store.list_sessions()
        self._table.setRowCount(len(self._summaries))
        for row, summary in enumerate(self._summaries):
            when = summary.updated_at[:16].replace("T", " ")
            cells = [
                summary.name or "Session",
                when,
                str(summary.player_count),
                str(summary.roll_count),
            ]
            for col, text in enumerate(cells):
                item = QTableWidgetItem(text)
                item.setData(Qt.ItemDataRole.UserRole, summary.id)
                self._table.setItem(row, col, item)
        self._delete_button.setEnabled(bool(self._summaries))

    def _selected_summary(self) -> store.SessionSummary | None:
        rows = self._table.selectionModel().selectedRows()
        if not rows:
            return None
        index = rows[0].row()
        return self._summaries[index] if 0 <= index < len(self._summaries) else None

    def _on_row_selected(self) -> None:
        """Continuing a session pre-fills the host form with its name."""
        summary = self._selected_summary()
        if summary is not None:
            self._form.set_name(summary.name or "Session")

    def _start_new(self) -> None:
        """Drop any selection and name the form afresh — Open then makes a new session."""
        self._table.clearSelection()
        self._form.set_name("Session")

    def _delete_selected(self) -> None:
        summary = self._selected_summary()
        if summary is None:
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
        store.delete_session(summary.id)
        self._reload()

    def _on_accept(self) -> None:
        """Take the selected session (or none), remember the relay, and close."""
        summary = self._selected_summary()
        self._chosen_id = summary.id if summary is not None else None
        self._form.persist_relay()
        self.accept()

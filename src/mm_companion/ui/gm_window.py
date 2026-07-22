"""GM Mode: the window that hosts the table's online session.

The GM's control panel — start and stop hosting, the join code to share, and a
live card per connected player (name, character, PL, hero points, conditions,
and their sheet one click away). NPCs and the shared roll history land in later
phases; what is here first is the part everything else needs, which is getting
players *connected*.

Most of the surface is about that last problem being genuinely hard. A home
connection is often not reachable from the internet at all — carrier-grade NAT,
or an ISP router the user does not control — and the failure is silent unless
someone says so. So:

- :func:`~mm_companion.core.session.discovery.publish_session` returns finished
  prose in ``Reachability.advice``, and it is rendered **verbatim** under the
  join code rather than being summarised or hidden behind a details toggle.
- "Reachable over the internet" and "only on this network" are visibly different
  states, not a silently-LAN address that looks like success until nobody can
  join.
- A GM who runs a tunnel (playit.gg, ngrok, Tailscale) pastes the address it gave
  them into **Tunnel address**, and the join code carries that instead. That is
  the whole tunnel path, and until the relay ships it is what makes the app
  usable over the internet.

All the networking is in :mod:`mm_companion.core.session`; this window only talks
to :class:`~mm_companion.ui.session_bridge.SessionBridge`.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QCloseEvent, QFont, QShowEvent
from PySide6.QtWidgets import (
    QApplication,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from mm_companion.core.character import Character
from mm_companion.core.data_loader import GameData, load_game_data
from mm_companion.core.session import discovery
from mm_companion.core.session.model import SessionState, new_session
from mm_companion.core.session.net import DEFAULT_PORT
from mm_companion.ui import theme
from mm_companion.ui.flow_layout import FlowContainer, FlowLayout
from mm_companion.ui.player_card import PlayerCard
from mm_companion.ui.session_bridge import SessionBridge, last_session, set_active_session
from mm_companion.ui.widgets import make_spin_box

#: What the listening socket binds to. Every interface, so a player on the LAN
#: reaches it whichever adapter they come in on; a test overrides it to loopback.
BIND_ADDRESS = "0.0.0.0"

TUNNEL_PLACEHOLDER = "e.g. 147.185.221.23:12345 — what your tunnel shows"

NO_PLAYERS = "Nobody has joined yet — send the join code above to your players."


class GMWindow(QMainWindow):
    """Host controls, the join code, and the connectivity story around both."""

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        bind: str = BIND_ADDRESS,
        data: GameData | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("GM Mode")
        self.resize(760, 680)

        self._bind = bind
        self._data = data or load_game_data()
        self._bridge = SessionBridge(self)
        # One card per seat, keyed by player id, and the last snapshot each player
        # pushed. The roster and the snapshots arrive on separate signals (a roster
        # entry deliberately carries no character), so both are held here and the
        # card is fed from whichever lands.
        self._cards: dict[str, PlayerCard] = {}
        self._snapshots: dict[str, dict] = {}
        # Read-only sheets opened from a card, kept referenced while open.
        self._player_windows: dict[str, QMainWindow] = {}
        set_active_session(self._bridge)
        # Resume the session this app was last in; a fresh one when there is none
        # (or its files have gone), so the window always has a session to host.
        self._state: SessionState = last_session() or new_session("Session")

        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addWidget(self._build_session_box())
        layout.addWidget(self._build_connection_box())
        layout.addWidget(self._build_players_box(), stretch=1)
        layout.addWidget(self._build_notice())

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(page)
        self.setCentralWidget(scroll)

        self._connect_bridge()
        self._refresh_idle_status()

    # -- construction ------------------------------------------------------

    def _build_session_box(self) -> QGroupBox:
        box = QGroupBox("Session")
        layout = QVBoxLayout(box)

        form = QFormLayout()
        self._name_edit = QLineEdit(self._state.name)
        self._name_edit.editingFinished.connect(self._rename_session)
        form.addRow("Name", self._name_edit)

        self._port_spin = make_spin_box(0, 65535, value=DEFAULT_PORT)
        # 0 means "let the OS pick a free one" — useful when the default port is
        # already taken, though a fixed port is friendlier to firewall rules.
        self._port_spin.setSpecialValueText("automatic")
        form.addRow("Port", self._port_spin)

        self._tunnel_edit = QLineEdit()
        self._tunnel_edit.setPlaceholderText(TUNNEL_PLACEHOLDER)
        form.addRow("Tunnel address", self._tunnel_edit)
        layout.addLayout(form)

        hint = _wrapped(
            "Leave the tunnel address empty to try your router automatically. If "
            "that cannot reach the internet, run a tunnel (playit.gg, ngrok, "
            "Tailscale) and paste the address it gives you here — players then "
            "need nothing but the join code."
        )
        hint.setEnabled(False)
        layout.addWidget(hint)

        buttons = QHBoxLayout()
        self._host_button = QPushButton("Start hosting")
        self._host_button.clicked.connect(self._toggle_hosting)
        buttons.addWidget(self._host_button)

        self._new_button = QPushButton("New session")
        self._new_button.clicked.connect(self._new_session)
        buttons.addWidget(self._new_button)
        buttons.addStretch()
        layout.addLayout(buttons)
        return box

    def _build_connection_box(self) -> QGroupBox:
        box = QGroupBox("Players join with")
        layout = QVBoxLayout(box)

        self._status_label = _wrapped("")
        font = self._status_label.font()
        font.setBold(True)
        self._status_label.setFont(font)
        layout.addWidget(self._status_label)

        code_row = QHBoxLayout()
        self._code_edit = QLineEdit()
        self._code_edit.setReadOnly(True)
        self._code_edit.setPlaceholderText("the join code appears here once you host")
        code_font = QFont(self._code_edit.font())
        code_font.setStyleHint(QFont.StyleHint.Monospace)
        code_font.setPointSize(code_font.pointSize() + 2)
        self._code_edit.setFont(code_font)
        code_row.addWidget(self._code_edit, stretch=1)

        self._copy_button = QPushButton("Copy")
        self._copy_button.setEnabled(False)
        self._copy_button.clicked.connect(self._copy_code)
        code_row.addWidget(self._copy_button)
        layout.addLayout(code_row)

        # One label per advice string, so each is rendered exactly as
        # ``discovery`` wrote it. They are player-facing sentences, not log lines.
        self._advice_layout = QVBoxLayout()
        layout.addLayout(self._advice_layout)
        return box

    def _build_players_box(self) -> QGroupBox:
        box = QGroupBox("Players")
        layout = QVBoxLayout(box)

        self._no_players = _wrapped(NO_PLAYERS)
        self._no_players.setEnabled(False)
        layout.addWidget(self._no_players)

        self._cards_container = FlowContainer()
        self._cards_flow = FlowLayout(self._cards_container)
        layout.addWidget(self._cards_container)
        layout.addStretch()
        return box

    def _build_notice(self) -> QLabel:
        self._notice = _wrapped("")
        self._notice.hide()
        return self._notice

    def _connect_bridge(self) -> None:
        self._bridge.started.connect(self._on_started)
        self._bridge.stopped.connect(self._on_stopped)
        self._bridge.published.connect(self._on_published)
        self._bridge.rosterChanged.connect(self._show_roster)
        self._bridge.snapshotReceived.connect(self._on_snapshot)
        self._bridge.playerJoined.connect(self._on_player_joined)
        self._bridge.refused.connect(self._on_refused)
        self._bridge.error.connect(self._on_error)

    # -- hosting -----------------------------------------------------------

    @property
    def bridge(self) -> SessionBridge:
        """The session this window drives — the seam later phases attach to."""
        return self._bridge

    def _toggle_hosting(self) -> None:
        if self._bridge.hosting:
            self._stop_hosting()
        else:
            self._start_hosting()

    def _start_hosting(self) -> None:
        tunnel = self._tunnel_edit.text().strip()
        manual_host, manual_port = "", 0
        if tunnel:
            try:
                manual_host, manual_port = discovery.parse_address(tunnel)
            except discovery.AddressError as exc:
                QMessageBox.warning(self, "Tunnel address", str(exc))
                return

        self._rename_session()
        # A resumed session already carries each seat's last snapshot, so the cards
        # come up populated rather than blank until everyone reconnects and pushes.
        self._snapshots = {
            player_id: dict(slot.character)
            for player_id, slot in self._state.players.items()
            if slot.character
        }
        try:
            self._bridge.host(
                self._state,
                port=self._port_spin.value(),
                bind=self._bind,
                gm_name="GM",
            )
        except OSError as exc:
            QMessageBox.warning(
                self,
                "Could not host",
                f"The session could not start listening: {exc}\n\n"
                "Another program may already be using that port — try a "
                "different one, or set the port to automatic.",
            )
            return

        self._set_hosting_widgets(True)
        self._set_status("Working out how players can reach you…", theme.ACCENT)
        self._clear_advice()
        self._bridge.publish(manual_host=manual_host, external_port=manual_port)

    def _stop_hosting(self) -> None:
        self._bridge.stop()
        self._set_hosting_widgets(False)
        self._code_edit.clear()
        self._copy_button.setEnabled(False)
        self._clear_advice()
        self._clear_cards()
        self._refresh_idle_status()

    def _set_hosting_widgets(self, hosting: bool) -> None:
        self._host_button.setText("Stop hosting" if hosting else "Start hosting")
        # The port and the tunnel address decide what the join code says, so they
        # are fixed for as long as a code is out in the world.
        self._port_spin.setEnabled(not hosting)
        self._tunnel_edit.setEnabled(not hosting)
        self._new_button.setEnabled(not hosting)

    def _new_session(self) -> None:
        self._state = new_session("Session")
        self._name_edit.setText(self._state.name)
        self._clear_cards()
        self._refresh_idle_status()

    def _rename_session(self) -> None:
        name = self._name_edit.text().strip() or "Session"
        self._name_edit.setText(name)
        self._state.name = name
        if self._bridge.hosting and self._bridge.server is not None:
            self._bridge.server.set_session_name(name)
        self.setWindowTitle(f"GM Mode — {name}")

    # -- bridge signals ----------------------------------------------------

    def _on_started(self, host: str, port: int) -> None:
        self._rename_session()
        self._notice.hide()

    def _on_stopped(self) -> None:
        self._set_hosting_widgets(False)

    def _on_published(self, reachability: discovery.Reachability) -> None:
        self._code_edit.setText(self._bridge.join_code())
        self._copy_button.setEnabled(bool(self._code_edit.text()))

        if reachability.method == discovery.METHOD_MANUAL:
            self._set_status(
                f"This code points at {reachability.host}:{reachability.port}. "
                "Anyone who can reach that address can join.",
                theme.ACCENT,
            )
        elif reachability.internet_reachable:
            self._set_status(
                "Players anywhere can join with this code.",
                theme.TINT_BETTER,
            )
        else:
            self._set_status(
                "Only players on this network can join with this code — "
                "nothing outside it can reach this machine yet.",
                theme.TINT_WORSE,
            )
        self._show_advice(reachability.advice)

    def _on_player_joined(self, payload: dict) -> None:
        player = payload.get("player", {})
        name = str(player.get("display_name", "")) or "A player"
        self._show_notice(f"{name} joined.", theme.TINT_BETTER)

    def _on_refused(self, payload: dict) -> None:
        self._show_notice(
            f"A connection was refused: {payload.get('message') or payload.get('code', '')}",
            theme.TINT_WORSE,
        )

    def _on_error(self, code: str, message: str) -> None:
        self._show_notice(f"{message or code}", theme.TINT_WORSE)

    # -- player cards ------------------------------------------------------

    def _show_roster(self, players: list) -> None:
        """Reconcile the card grid against the roster, in place.

        Updated rather than rebuilt: the roster is re-broadcast on every join
        *and* every snapshot, so tearing every card down each time would make the
        grid flicker whenever anybody edited their sheet.
        """
        seen: set[str] = set()
        for entry in players:
            player_id = str(entry.get("player_id", ""))
            if not player_id:
                continue
            seen.add(player_id)
            card = self._cards.get(player_id)
            if card is None:
                card = PlayerCard(self._data)
                card.openSheetRequested.connect(self._open_player_sheet)
                self._cards[player_id] = card
                self._cards_flow.addWidget(card)
            card.set_roster(entry)
            snapshot = self._snapshots.get(player_id)
            if snapshot:
                card.set_character(snapshot)

        for player_id in [p for p in self._cards if p not in seen]:
            self._drop_card(player_id)
        self._no_players.setVisible(not self._cards)

    def _on_snapshot(self, player_id: str, character: object) -> None:
        """A player pushed their live sheet: remember it and restate their card."""
        if not isinstance(character, dict) or not character:
            return
        self._snapshots[player_id] = character
        card = self._cards.get(player_id)
        if card is not None:
            card.set_character(character)

    def _open_player_sheet(self, player_id: str) -> None:
        """Show a player's character in a read-only sheet.

        Built fresh from the newest snapshot every time, so clicking again on a
        card whose player has since changed something re-reads it — the sheet
        itself is a view of the moment it was opened, while the card stays live.
        """
        snapshot = self._snapshots.get(player_id)
        if not snapshot:
            return
        from mm_companion.ui.main_window import MainWindow

        previous = self._player_windows.pop(player_id, None)
        if previous is not None:
            previous.close()
        window = MainWindow(character=Character.from_dict(snapshot), locked=True)
        self._player_windows[player_id] = window
        window.show()
        window.raise_()

    def _drop_card(self, player_id: str) -> None:
        """Remove one card from the grid (the seat itself is gone, not just offline)."""
        card = self._cards.pop(player_id, None)
        if card is None:
            return
        for index in range(self._cards_flow.count()):
            item = self._cards_flow.itemAt(index)
            if item is not None and item.widget() is card:
                self._cards_flow.takeAt(index)
                break
        card.setParent(None)
        card.deleteLater()

    def _clear_cards(self) -> None:
        for player_id in list(self._cards):
            self._drop_card(player_id)
        self._snapshots.clear()
        self._no_players.setVisible(True)

    # -- small view helpers ------------------------------------------------

    def _refresh_idle_status(self) -> None:
        """What the status line says while nothing is being hosted."""
        rolls = len(self._state.rolls)
        seats = sum(1 for slot in self._state.players.values() if not slot.is_gm)
        if rolls or seats:
            detail = f" — “{self._state.name}” already has {seats} player(s) and {rolls} roll(s)"
        else:
            detail = ""
        self._set_status(f"Not hosting{detail}.", "")
        self.setWindowTitle(f"GM Mode — {self._state.name}")

    def _set_status(self, text: str, colour: str) -> None:
        self._status_label.setText(text)
        self._status_label.setStyleSheet(f"color: {colour};" if colour else "")

    def _show_advice(self, advice: tuple[str, ...]) -> None:
        self._clear_advice()
        for line in advice:
            self._advice_layout.addWidget(_wrapped(f"• {line}"))

    def _clear_advice(self) -> None:
        while self._advice_layout.count():
            item = self._advice_layout.takeAt(0)
            widget = item.widget() if item is not None else None
            if widget is not None:
                widget.deleteLater()

    def _show_notice(self, text: str, colour: str) -> None:
        self._notice.setText(text)
        self._notice.setStyleSheet(f"color: {colour};" if colour else "")
        self._notice.setVisible(bool(text))

    def _copy_code(self) -> None:
        code = self._code_edit.text()
        if not code:
            return
        clipboard = QApplication.clipboard()
        if clipboard is not None:
            clipboard.setText(code)
        self._show_notice("Join code copied — send it to your players.", theme.ACCENT)

    # -- lifecycle ---------------------------------------------------------

    def showEvent(self, event: QShowEvent) -> None:  # noqa: N802 (Qt override)
        """Re-arm as the process-wide session — the window is reusable after a close."""
        set_active_session(self._bridge)
        super().showEvent(event)

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802 (Qt override)
        """Stop hosting and give the router its port back before going away.

        The window itself survives (the launcher keeps it, and reopening GM Mode
        shows this one again) so the session it was working on — its name, its
        roster, its rolls — is still here rather than replaced by a blank one.
        """
        for window in self._player_windows.values():
            window.close()
        self._player_windows.clear()
        self._stop_hosting()
        set_active_session(None)
        super().closeEvent(event)


def _wrapped(text: str) -> QLabel:
    """A word-wrapping, selectable label — advice is prose, and gets read."""
    label = QLabel(text)
    label.setWordWrap(True)
    label.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
    return label

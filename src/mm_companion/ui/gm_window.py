"""GM Mode: the window that hosts the table's online session.

The GM's control panel — start and stop hosting, the join code to share, and a
live card per connected player (name, character, PL, hero points, conditions,
and their sheet one click away). A card's "+" applies a condition straight onto
that player's live sheet, and a chip's "×" takes one off again. Below the players
sit the session's **NPCs** (ordinary characters in the GM's own folder, on a
simplified sheet) and the table's shared **roll history** beside the GM's roller,
which can roll hidden.

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
  them into **Tunnel address**, and the join code carries that instead.
- Failing all of that, the session falls back to a **relay**: the GM's app dials
  *out* to a public box, as every player does, so nothing has to be reachable at
  all. Direct is always tried first — a direct connection costs the relay
  nothing — so the relay is the last rung of the ladder, never the default.

All the networking is in :mod:`mm_companion.core.session`; this window only talks
to :class:`~mm_companion.ui.session_bridge.SessionBridge`.
"""

from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import QByteArray, Qt
from PySide6.QtGui import QCloseEvent, QFont, QShowEvent
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFileDialog,
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

from mm_companion.core import library, storage
from mm_companion.core.character import AppliedCondition, Character
from mm_companion.core.data_loader import GameData, load_game_data
from mm_companion.core.session import discovery, store
from mm_companion.core.session.model import SessionState, new_session
from mm_companion.core.session.net import DEFAULT_PORT
from mm_companion.ui import theme
from mm_companion.ui.block_canvas import BlockCanvas
from mm_companion.ui.block_sizes import BlockSize
from mm_companion.ui.dice_roller import DiceRollerPanel
from mm_companion.ui.flow_layout import FlowContainer, FlowLayout
from mm_companion.ui.npc_window import NPCWindow
from mm_companion.ui.player_card import PlayerCard
from mm_companion.ui.roll_history import RollHistoryPanel
from mm_companion.ui.sections.conditions import condition_display_name
from mm_companion.ui.sections.titled_section import strip_groupbox_caption
from mm_companion.ui.session_bridge import SessionBridge, last_session, set_active_session
from mm_companion.ui.session_dialogs import HostOptions, HostSessionDialog
from mm_companion.ui.start_window import CharacterCard

#: What the listening socket binds to. Every interface, so a player on the LAN
#: reaches it whichever adapter they come in on; a test overrides it to loopback.
BIND_ADDRESS = "0.0.0.0"

NO_PLAYERS = "Nobody has joined yet — send the join code above to your players."

NO_NPCS = "No NPCs in this session yet — create one, or add one you have already written."


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
        # NPC sheets opened from this window, keyed by the file they came from
        # (an unsaved new NPC by a placeholder key), likewise kept referenced.
        self._npc_windows: dict[str, QMainWindow] = {}
        # One relay attempt per hosting run: the fallback republishes, and a
        # second attempt off that would loop.
        self._relay_attempted = False
        # Ids for rolls made before hosting: negative so they never collide with a
        # session's positive seqs, and still removable from the local view.
        self._offline_seq = 0
        # The connection choices the current/most-recent host run used; the dialog
        # replaces this each time hosting starts. A sane default lets the relay
        # fallback and the port read something before the first run.
        self._host_options = HostOptions(
            name="Session", port=DEFAULT_PORT, tunnel="", relay=storage.relay_url(), use_relay=True
        )
        set_active_session(self._bridge)
        # Resume the session this app was last in; a fresh one when there is none
        # (or its files have gone), so the window always has a session to host.
        self._state: SessionState = last_session() or new_session("Session")

        # The blocks are draggable / hideable / reorderable the same way the
        # character sheet's are — a shared BlockCanvas rather than a fixed stack.
        # Players and NPCs get a growable width just wider than one card, so their
        # FlowLayout keeps at least one card per row and fits more as the window
        # widens. The connection knobs are gone from here (see HostSessionDialog),
        # so the Session block is compact.
        panels = [
            ("session", "Session", self._build_session_box()),
            ("players", "Players", self._build_players_box()),
            ("npcs", "NPCs", self._build_npcs_box()),
            ("rolls", "Rolls", self._build_rolls_box()),
        ]
        for _key, _title, box in panels:
            strip_groupbox_caption(box)  # the block's title bar carries the name now
        sizes = {
            "session": BlockSize(min_width=380, min_height=110),
            "players": BlockSize(min_width=250, min_height=130),
            "npcs": BlockSize(min_width=250, min_height=150),
            "rolls": BlockSize(min_width=660, min_height=260),
        }
        default_rows = [["session"], ["players"], ["npcs"], ["rolls"]]
        self._canvas = BlockCanvas(panels, sizes, default_rows)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._scroll.setWidget(self._canvas)
        self._canvas.set_scroll_area(self._scroll)

        central = QWidget()
        central_layout = QVBoxLayout(central)
        central_layout.setContentsMargins(0, 0, 0, 0)
        central_layout.addWidget(self._scroll, stretch=1)
        central_layout.addWidget(self._build_notice())
        self.setCentralWidget(central)

        self._canvas.arrangement_changed.connect(self._update_min_width)
        self._update_min_width()
        self._build_menu()
        self._restore_layout()

        self._connect_bridge()
        self._refresh_idle_status()
        self._refresh_rolls()
        self._refresh_npcs()

    def _update_min_width(self) -> None:
        """Pin the page's min width to the widest docked row (blocks never squash)."""
        bar = self._scroll.verticalScrollBar()
        extra = bar.sizeHint().width() if bar is not None else 0
        self._scroll.setMinimumWidth(self._canvas.content_minimum_width() + extra + 2)

    def _build_menu(self) -> None:
        """A View menu: one show/hide toggle per block, plus Reset Layout."""
        view_menu = self.menuBar().addMenu("&View")
        self._block_actions: dict[str, object] = {}
        for key in self._canvas.block_keys():
            action = view_menu.addAction(self._canvas.block_frame(key).title)
            action.setCheckable(True)
            action.setChecked(not self._canvas.is_hidden(key))
            action.toggled.connect(lambda visible, k=key: self._on_block_toggled(k, visible))
            self._block_actions[key] = action
        view_menu.addSeparator()
        view_menu.addAction("Reset Layout").triggered.connect(self._reset_layout)
        self._canvas.block_visibility_changed.connect(self._on_block_visibility_changed)

    def _on_block_toggled(self, key: str, visible: bool) -> None:
        if visible:
            self._canvas.show_block(key)
        else:
            self._canvas.hide_block(key)

    def _on_block_visibility_changed(self, key: str, visible: bool) -> None:
        action = self._block_actions.get(key)
        if action is None or action.isChecked() == visible:
            return
        action.blockSignals(True)
        action.setChecked(visible)
        action.blockSignals(False)

    def _reset_layout(self) -> None:
        self._canvas.reset()
        for key, action in self._block_actions.items():
            action.blockSignals(True)
            action.setChecked(not self._canvas.is_hidden(key))
            action.blockSignals(False)

    # -- layout persistence ------------------------------------------------

    def _restore_layout(self) -> None:
        """Restore the remembered geometry and block arrangement (its own settings key)."""
        layout = storage.load_settings().get("gm_layout") or {}
        geometry = layout.get("window_geometry") if isinstance(layout, dict) else None
        if isinstance(geometry, str) and geometry:
            self.restoreGeometry(QByteArray.fromBase64(geometry.encode("ascii")))
        state = layout.get("dock_state") if isinstance(layout, dict) else None
        if isinstance(state, str) and state:
            try:
                self._canvas.apply_arrangement(json.loads(state))
            except (ValueError, TypeError):
                pass

    def _persist_layout(self) -> None:
        """Save the GM window's geometry and block arrangement as a global preference."""
        geometry = bytes(self.saveGeometry().toBase64()).decode("ascii")
        try:
            storage.update_settings(
                gm_layout={
                    "window_geometry": geometry,
                    "dock_state": json.dumps(self._canvas.arrangement()),
                }
            )
        except OSError:
            pass

    # -- construction ------------------------------------------------------

    def _build_session_box(self) -> QGroupBox:
        """The compact session block: one button to start, the join code once hosting.

        The connection knobs (port, relay, a typed tunnel address) live in
        :class:`~mm_companion.ui.session_dialogs.HostSessionDialog`, reached through
        the **Start a session…** button, so the always-visible surface is just a
        status line, the join code and its Copy button, and the advice underneath.
        """
        box = QGroupBox("Session")
        layout = QVBoxLayout(box)

        self._status_label = _wrapped("")
        font = self._status_label.font()
        font.setBold(True)
        self._status_label.setFont(font)
        layout.addWidget(self._status_label)

        code_row = QHBoxLayout()
        self._code_edit = QLineEdit()
        self._code_edit.setReadOnly(True)
        self._code_edit.setPlaceholderText("your join code appears here once you start hosting")
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

        buttons = QHBoxLayout()
        self._host_button = QPushButton("Start a session…")
        self._host_button.clicked.connect(self._toggle_hosting)
        buttons.addWidget(self._host_button)

        self._new_button = QPushButton("New session")
        self._new_button.clicked.connect(self._new_session)
        buttons.addWidget(self._new_button)
        buttons.addStretch()
        layout.addLayout(buttons)
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

    def _build_npcs_box(self) -> QGroupBox:
        """The session's cast of NPCs, and the two ways to add one.

        An NPC is an ordinary character saved in the workspace ``gm_characters/``
        dir, so the GM's bestiary outlives any one session; what belongs to *this*
        session is which of them are in it (``SessionState.npc_paths``). Hence two
        buttons: write a new one, or bring an existing one in.
        """
        box = QGroupBox("NPCs")
        layout = QVBoxLayout(box)

        buttons = QHBoxLayout()
        create = QPushButton("Create NPC")
        create.clicked.connect(self._create_npc)
        buttons.addWidget(create)
        add = QPushButton("Add existing…")
        add.clicked.connect(self._add_existing_npc)
        buttons.addWidget(add)
        buttons.addStretch()
        layout.addLayout(buttons)

        self._no_npcs = _wrapped(NO_NPCS)
        self._no_npcs.setEnabled(False)
        layout.addWidget(self._no_npcs)

        self._npc_container = FlowContainer()
        self._npc_flow = FlowLayout(self._npc_container)
        layout.addWidget(self._npc_container)
        layout.addStretch()
        return box

    def _build_rolls_box(self) -> QGroupBox:
        """The GM's own roller beside the table's shared history.

        The same :class:`~mm_companion.ui.dice_roller.DiceRollerPanel` a player
        uses, with the one thing only a GM gets: a **Hidden roll** switch. A
        hidden roll is recorded and shown here, marked, and never put on the wire
        — so it is not hidden by the players' apps agreeing to ignore it, it
        simply never reaches them.
        """
        box = QGroupBox("Rolls")
        layout = QHBoxLayout(box)

        self._roller = DiceRollerPanel(hidden_option=True)
        # While hosting, a roll made here goes through the server like everyone
        # else's and comes back on the shared feed. Before that there is no
        # session to record it in, so it is shown as a card and nothing more.
        self._roller.localRoll.connect(self._show_offline_roll)
        layout.addWidget(self._roller)

        self._history = RollHistoryPanel(gm=True)
        self._history.saveRequested.connect(self._roller.save_quick_roll)
        self._history.rollRemovedLocally.connect(self._on_local_roll_removed)
        self._history.setMinimumHeight(240)
        # Hold the GM's own roll until its die stops tumbling; the roller cues it.
        self._history.set_defer_own(True)
        self._roller.sessionRollRevealed.connect(self._history.release_roll)
        layout.addWidget(self._history, stretch=1)
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
            self.stop_hosting()
        else:
            self._prompt_and_host()

    def _prompt_and_host(self) -> None:
        """Ask how to host (name + connection), then start with the chosen options."""
        dialog = HostSessionDialog(self, session_name=self._state.name)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self.start_hosting(dialog.options())

    def start_hosting(self, options: HostOptions) -> None:
        """Begin hosting with *options* (from :class:`HostSessionDialog`) and publish.

        The connection choices are held for the run so the relay fallback and the
        reachability probe read the same values the GM picked, rather than any
        always-visible field.
        """
        manual_host, manual_port = "", 0
        if options.tunnel:
            try:
                manual_host, manual_port = discovery.parse_address(options.tunnel)
            except discovery.AddressError as exc:
                QMessageBox.warning(self, "Tunnel address", str(exc))
                return

        self._host_options = options
        self._relay_attempted = False
        self._state.name = options.name
        self._rename_session()
        # A resumed session already carries each seat's last snapshot, so the cards
        # come up populated rather than blank until everyone reconnects and pushes.
        self._snapshots = {
            player_id: dict(slot.character)
            for player_id, slot in self._state.players.items()
            if slot.character
        }
        if not self._begin_hosting():
            return

        self._set_hosting_widgets(True)
        self._set_status("Working out how players can reach you…", theme.ACCENT)
        self._clear_advice()
        self._bridge.publish(manual_host=manual_host, external_port=manual_port)

    def _begin_hosting(self, relay: str = "") -> bool:
        """Start the server (directly, or through *relay*), reporting failure once."""
        try:
            self._bridge.host(
                self._state,
                port=self._host_options.port,
                bind=self._bind,
                gm_name="GM",
                relay_url=relay,
            )
        except OSError as exc:
            if relay:
                # The relay is a fallback, so its failure is a notice and a
                # return to the direct connection, not a dead end.
                self._show_notice(f"{discovery.ADVICE_RELAY_UNREACHABLE} ({exc})", theme.TINT_WORSE)
                return False
            QMessageBox.warning(
                self,
                "Could not host",
                f"The session could not start listening: {exc}\n\n"
                "Another program may already be using that port — try a "
                "different one, or set the port to automatic.",
            )
            return False
        except ValueError as exc:  # an unreadable relay address
            self._show_notice(f"That relay address cannot be used: {exc}", theme.TINT_WORSE)
            return False
        # Not on the ``started`` signal: the server emits that from inside its own
        # ``start()``, before the bridge has taken ownership of it, so ``hosting``
        # is still False there and the history would attach to nothing.
        self._refresh_rolls()
        return True

    def _fall_back_to_relay(self) -> None:
        """Second rung of the ladder: re-host through the relay and republish.

        Direct hosting is stopped first — the session can only be reached one way
        at a time, and the join code has to name the one that works. Nobody has
        joined yet at this point (the code is seconds old), so no player is being
        dropped in the swap.
        """
        self._relay_attempted = True
        relay = self._host_options.relay
        self._set_status("Trying the relay…", theme.ACCENT)
        self._bridge.stop()
        if not self._begin_hosting(relay) and not self._begin_hosting():
            self._set_hosting_widgets(False)
            self._refresh_idle_status()
            return
        self._set_hosting_widgets(True)
        self._bridge.publish()

    def stop_hosting(self) -> None:
        """Stop hosting, clear the join code and cards, and return to the idle state."""
        self._relay_attempted = False
        self._bridge.stop()
        self._set_hosting_widgets(False)
        self._code_edit.clear()
        self._copy_button.setEnabled(False)
        self._clear_advice()
        self._clear_cards()
        self._refresh_idle_status()

    def _set_hosting_widgets(self, hosting: bool) -> None:
        self._host_button.setText("Stop hosting" if hosting else "Start a session…")
        # A new session cannot be started on top of a live one.
        self._new_button.setEnabled(not hosting)

    def _new_session(self) -> None:
        self._state = new_session("Session")
        self._clear_cards()
        self._refresh_idle_status()
        self._refresh_rolls()
        self._refresh_npcs()

    def _refresh_rolls(self) -> None:
        """Point the history at the live session, or at the one on disk.

        While hosting it follows the server. Between sessions there is nothing to
        follow, but the state loaded from the workspace still carries the log —
        so reopening GM Mode shows last night's rolls rather than a blank panel.
        """
        if self._bridge.hosting:
            self._history.attach(self._bridge)
            return
        self._history.detach()
        self._history.set_rolls([roll.to_dict() for roll in self._state.rolls])

    def _show_offline_roll(self, roll: object) -> None:
        """Show a roll the GM made before hosting started.

        It is never persisted — there is no session running to record it in — but it
        gets a negative ``seq`` so the GM can still strike it from the panel. Starting
        to host re-seeds the panel from the real log, at which point these are gone.
        """
        if not isinstance(roll, dict):
            return
        result = roll.get("result")
        self._offline_seq -= 1
        self._history.add_roll(
            {
                "seq": self._offline_seq,
                "player_name": self._name_of_gm(),
                "die": roll["die"],
                "bonus": roll["bonus"],
                "penalty": roll["penalty"],
                "dc": roll["dc"],
                "degree": None if result is None else result.degree,
                "critical": bool(result is not None and result.critical),
            }
        )

    def _on_local_roll_removed(self, seq: int) -> None:
        """Persist a roll the GM struck while not hosting.

        A live session removes on the server (and this never fires). Off the air the
        panel shows the resumed session's persisted log, so drop the roll from the
        state and save it too; a pre-hosting offline roll is not in the state, so
        this is a no-op for those.
        """
        if self._bridge.hosting:
            return
        if self._state.remove_roll(seq) is not None:
            try:
                store.save_session(self._state, write_rolls=True)
            except OSError:
                pass

    def _name_of_gm(self) -> str:
        slot = next((s for s in self._state.players.values() if s.is_gm), None)
        return slot.display_name if slot is not None else "GM"

    def _rename_session(self) -> None:
        name = (self._state.name or "").strip() or "Session"
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
        self._refresh_rolls()

    def _on_published(self, reachability: discovery.Reachability) -> None:
        if self._should_try_relay(reachability):
            self._fall_back_to_relay()
            return

        self._code_edit.setText(self._bridge.join_code())
        self._copy_button.setEnabled(bool(self._code_edit.text()))

        if reachability.method == discovery.METHOD_RELAY:
            self._set_status(
                "Players anywhere can join with this code — this session is going "
                "through the relay.",
                theme.TINT_BETTER,
            )
        elif reachability.method == discovery.METHOD_MANUAL:
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

    def _should_try_relay(self, reachability: discovery.Reachability) -> bool:
        """Is this the moment for the last rung — and has it not been tried already?

        Only after a *direct* attempt has come back unreachable. A tunnel address
        the GM typed is taken at their word (there is nothing to probe and nothing
        to improve on), and a relay is never tried twice for one hosting run.
        """
        return (
            not self._relay_attempted
            and self._host_options.use_relay
            and bool(self._host_options.relay)
            and reachability.method not in (discovery.METHOD_MANUAL, discovery.METHOD_RELAY)
            and not reachability.internet_reachable
        )

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
            # The GM is not a player on their own board: conditions and rolls for
            # the GM happen on the GM's own sheet and roller, not through a card.
            if entry.get("is_gm"):
                continue
            seen.add(player_id)
            card = self._cards.get(player_id)
            if card is None:
                card = PlayerCard(self._data)
                card.openSheetRequested.connect(self._open_player_sheet)
                card.applyConditionRequested.connect(self._apply_condition)
                card.removeConditionRequested.connect(self._remove_condition)
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
        window = MainWindow(character=self._character_from_snapshot(snapshot), gm_view=True)
        self._player_windows[player_id] = window
        window.show()
        window.raise_()

    @staticmethod
    def _character_from_snapshot(snapshot: dict) -> Character:
        """Rebuild a character from a snapshot, restoring its portrait.

        The portrait travels as a base64 thumbnail (image_path was stripped on the
        wire); decode it to a temp file so the read-only sheet's image block — which
        reads a path — shows the picture.
        """
        from mm_companion.ui.session_portrait import portrait_to_tempfile

        character = Character.from_dict(snapshot)
        path = portrait_to_tempfile(snapshot.get("portrait"))
        if path is not None:
            character.image_path = path
        return character

    # -- fast-apply conditions ---------------------------------------------

    def _apply_condition(self, player_id: str, condition_id: str, parameter: object) -> None:
        self._send_condition("apply", player_id, condition_id, parameter)

    def _remove_condition(self, player_id: str, condition_id: str, parameter: object) -> None:
        self._send_condition("remove", player_id, condition_id, parameter)

    def _send_condition(
        self, action: str, player_id: str, condition_id: str, parameter: object
    ) -> None:
        """Order one condition change on a player's live sheet.

        The GM never edits the player's character here — the command goes down
        their connection and their app applies it through
        :func:`~mm_companion.core.rules.apply_condition`, exactly as their own "+"
        would. The chips on this card only move once the snapshot comes back, so
        a command that quietly failed is visible rather than assumed.
        """
        server = self._bridge.server
        subject = str(parameter) if parameter else None
        name = self._condition_name(condition_id, subject)
        who = self._player_name(player_id)
        sent = False
        if server is not None:
            send = server.apply_condition if action == "apply" else server.remove_condition
            sent = send(player_id, condition_id, subject)
        if not sent:
            self._show_notice(
                f"{who} is not connected, so “{name}” was not sent.", theme.TINT_WORSE
            )
            return
        verb = "Applied" if action == "apply" else "Removed"
        self._show_notice(f"{verb} “{name}” on {who}.", theme.ACCENT)

    def _condition_name(self, condition_id: str, parameter: str | None) -> str:
        """The condition as the GM picked it, named the way a chip names it."""
        record = next((c for c in self._data.conditions if c.id == condition_id), None)
        return condition_display_name(
            AppliedCondition(condition_id=condition_id, parameter=parameter), record
        )

    def _player_name(self, player_id: str) -> str:
        slot = self._state.players.get(player_id)
        return slot.display_name if slot is not None else "That player"

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

    # -- NPCs ---------------------------------------------------------------

    def _npc_dir(self) -> Path:
        """Where NPCs are saved — apart from the player characters, and never listed
        in the launcher's library."""
        return storage.get_workspace().gm_characters_dir

    def _npc_summaries(self) -> list[library.CharacterSummary]:
        """This session's NPCs, in the order they were added.

        Reads the whole ``gm_characters/`` dir through the same seam the launcher
        uses and keeps the ones this session names. A file deleted behind the app's
        back is quietly dropped from the session rather than left as a card that
        opens nothing.
        """
        wanted = list(self._state.npc_paths)
        if not wanted:
            return []
        by_name = {
            summary.path.name: summary
            for summary in library.list_saved_characters(self._npc_dir(), estimate_pl=True)
            if summary.path is not None
        }
        alive = [name for name in wanted if name in by_name]
        if alive != wanted:
            self._set_npc_paths(alive)
        return [by_name[name] for name in alive]

    def _set_npc_paths(self, paths: list[str]) -> None:
        """Record the session's cast, persisting it wherever the session lives now.

        While hosting, the state belongs to the server's lock — writing it from
        here would race its own saves — so the change goes through the server. Off
        the air the window owns the state and writes it itself; either way the
        cast survives a restart.
        """
        server = self._bridge.server
        if server is not None:
            server.set_npc_paths(paths)
            return
        self._state.npc_paths = list(paths)
        self._state.touch()
        try:
            store.save_session(self._state)
            storage.update_settings(session_last_id=self._state.id)
        except OSError:
            pass  # an unwritable workspace is not worth a dialog mid-session

    def _refresh_npcs(self) -> None:
        """Rebuild the NPC grid from the session's cast."""
        while self._npc_flow.count():
            item = self._npc_flow.takeAt(0)
            widget = item.widget() if item is not None else None
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()

        summaries = self._npc_summaries()
        for summary in summaries:
            card = CharacterCard(summary, removable=True)
            card.clicked.connect(self._open_npc)
            card.removeRequested.connect(self._remove_npc)
            card.deleteRequested.connect(self._delete_npc)
            self._npc_flow.addWidget(card)
        self._no_npcs.setVisible(not summaries)

    def _create_npc(self) -> None:
        """Write a new NPC: an editable, simplified sheet that saves into the cast."""
        self._track_npc_window(NPCWindow(locked=False))

    def _add_existing_npc(self) -> None:
        """Bring an NPC written for another session into this one."""
        directory = self._npc_dir()
        directory.mkdir(parents=True, exist_ok=True)
        chosen, _ = QFileDialog.getOpenFileName(
            self, "Add NPC", str(directory), "Character files (*.json)"
        )
        if not chosen:
            return
        path = Path(chosen)
        if path.parent != directory:
            QMessageBox.warning(
                self,
                "Add NPC",
                "An NPC has to live in the GM characters folder:\n"
                f"{directory}\n\nCopy the file there and try again.",
            )
            return
        self._register_npc(path)

    def _register_npc(self, path: Path) -> None:
        """Put *path* in the session's cast (no-op if it is already there) and redraw."""
        if path.name not in self._state.npc_paths:
            self._set_npc_paths([*self._state.npc_paths, path.name])
        self._refresh_npcs()

    def _open_npc(self, summary: library.CharacterSummary) -> None:
        """Open an NPC's sheet, or raise the one already open for it.

        Not replaced the way a player's read-only sheet is: this one is editable,
        so throwing it away could throw away work the GM has not saved yet.
        """
        if summary.path is None:
            return
        path = Path(summary.path)
        existing = self._window_for(path)
        if existing is not None:
            existing.show()
            existing.raise_()
            existing.activateWindow()
            return
        self._track_npc_window(NPCWindow(character=library.load_character(path), path=path))

    def _track_npc_window(self, window: NPCWindow) -> None:
        """Show an NPC sheet and keep it alive, watching for saves and its close."""
        key = id(window)
        self._npc_windows[key] = window
        window.saved.connect(lambda w=window: self._on_npc_saved(w))
        window.closed.connect(lambda k=key: self._npc_windows.pop(k, None))
        window.show()
        window.raise_()

    def _on_npc_saved(self, window: NPCWindow) -> None:
        """A saved NPC joins the cast (a new one) and restates its card (an old one)."""
        if window.path is not None:
            self._register_npc(Path(window.path))

    def _window_for(self, path: Path) -> QMainWindow | None:
        return next(
            (w for w in self._npc_windows.values() if w.path is not None and Path(w.path) == path),
            None,
        )

    def _remove_npc(self, summary: library.CharacterSummary) -> None:
        """Take an NPC out of this session, leaving the file where it is."""
        if summary.path is None:
            return
        name = Path(summary.path).name
        self._set_npc_paths([n for n in self._state.npc_paths if n != name])
        self._refresh_npcs()
        self._show_notice(f"“{summary.name}” is no longer in this session.", theme.ACCENT)

    def _delete_npc(self, summary: library.CharacterSummary) -> None:
        """Delete an NPC's file for good, once the GM confirms it."""
        if summary.path is None:
            return
        confirm = QMessageBox.question(
            self,
            "Delete NPC",
            f"Delete “{summary.name}”? This cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return
        library.delete_character(Path(summary.path))
        self._remove_npc(summary)

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
        # An NPC sheet with unsaved edits prompts, and may refuse to close; it
        # stays open and tracked, which is fine — this window is reusable.
        for npc_window in list(self._npc_windows.values()):
            npc_window.close()
        self._persist_layout()
        self.stop_hosting()
        set_active_session(None)
        super().closeEvent(event)


def _wrapped(text: str) -> QLabel:
    """A word-wrapping, selectable label — advice is prose, and gets read."""
    label = QLabel(text)
    label.setWordWrap(True)
    label.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
    return label

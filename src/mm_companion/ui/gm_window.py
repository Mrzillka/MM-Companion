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
import re
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QByteArray, QEasingCurve, QPropertyAnimation, QRect, Qt, QTimer
from PySide6.QtGui import QCloseEvent, QShowEvent
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFileDialog,
    QFrame,
    QGraphicsOpacityEffect,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from mm_companion.core import library, storage
from mm_companion.core.character import AppliedCondition, Character
from mm_companion.core.data_loader import GameData, load_game_data
from mm_companion.core.npc import quick_npc
from mm_companion.core.rules import apply_condition, decrement_condition
from mm_companion.core.session import discovery, store
from mm_companion.core.session.model import PlayerSlot, SessionState, new_session
from mm_companion.core.session.net import DEFAULT_PORT
from mm_companion.ui import theme
from mm_companion.ui.block_canvas import BlockCanvas
from mm_companion.ui.block_sizes import BlockSize, load_block_sizes
from mm_companion.ui.connection_indicator import install_connection_indicator
from mm_companion.ui.dice_roller import DiceRollerPanel
from mm_companion.ui.drop_feedback import DropIndicator
from mm_companion.ui.flow_layout import FlowContainer, FlowLayout
from mm_companion.ui.npc_card import NPCCard
from mm_companion.ui.npc_quick_dialog import QuickNPCDialog
from mm_companion.ui.npc_window import NPCWindow
from mm_companion.ui.pinned_panel import PinnedBoard
from mm_companion.ui.player_card import PlayerCard
from mm_companion.ui.roll_history import RollHistoryPanel
from mm_companion.ui.sections.conditions import condition_display_name, matching_condition
from mm_companion.ui.sections.titled_section import strip_groupbox_caption
from mm_companion.ui.session_bridge import SessionBridge, last_session, set_active_session
from mm_companion.ui.session_dialogs import HostOptions

#: What the listening socket binds to. Every interface, so a player on the LAN
#: reaches it whichever adapter they come in on; a test overrides it to loopback.
BIND_ADDRESS = "0.0.0.0"

NO_PLAYERS = "Nobody has joined yet — send your players the join code (Session ▸ Copy join code)."

NO_NPCS = "No NPCs in this session yet — create one, or add one you have already written."


@dataclass
class _NpcEntry:
    """One NPC in the session's cast, with its loaded model and runtime state.

    Keyed by file name. The model comes from disk (so a saved condition edit is
    reflected on the next refresh); ``initiative`` is transient — rolled at the
    table, not part of the character — so it lives here rather than in the file.
    """

    path: Path
    summary: library.CharacterSummary
    character: Character
    initiative: int | None = None
    card: NPCCard | None = None


def _next_copy_name(source_name: str, existing: set[str]) -> str:
    """The next free ``"<base>-<n>"`` for a copy of *source_name*.

    A trailing ``-<digits>`` on the source is stripped to find the base, so
    copying "Goon" gives "Goon-2" and copying "Goon-2" gives the next free
    "Goon-N" rather than "Goon-2-2". ``n`` starts at 2 and steps up until the
    name is free among *existing*.
    """
    match = re.match(r"^(.*?)-(\d+)$", source_name)
    base = match.group(1) if match else source_name
    n = 2
    while f"{base}-{n}" in existing:
        n += 1
    return f"{base}-{n}"


class GMWindow(QMainWindow):
    """The GM's board: player cards, NPCs, the shared roll log, and a status strip.

    Which session to run and how to host it are chosen *before* this window opens
    (the launcher's :class:`~mm_companion.ui.session_dialogs.GMSessionLaunchDialog`),
    so there are no host controls here — the window comes up already hosting the
    chosen session. The join code is copied from **Session ▸ Copy join code**; the
    connectivity story is told in the status strip along the bottom.
    """

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        bind: str = BIND_ADDRESS,
        data: GameData | None = None,
        state: SessionState | None = None,
        host_options: HostOptions | None = None,
        autohost: bool = False,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("GM Mode")
        self.resize(760, 680)

        self._bind = bind
        self._data = data or load_game_data()
        self._bridge = SessionBridge(self)
        #: The current join code, or "" while not published. Copied from the menu.
        self._join_code = ""
        # One card per seat, keyed by player id, and the last snapshot each player
        # pushed. The roster and the snapshots arrive on separate signals (a roster
        # entry deliberately carries no character), so both are held here and the
        # card is fed from whichever lands.
        self._cards: dict[str, PlayerCard] = {}
        self._snapshots: dict[str, dict] = {}
        #: The server this session lives on, for the status line; empty when this
        #: app is hosting the session itself.
        self._server_label = ""
        # Read-only sheets opened from a card, kept referenced while open.
        self._player_windows: dict[str, QMainWindow] = {}
        # NPC sheets opened from this window, keyed by the file they came from
        # (an unsaved new NPC by a placeholder key), likewise kept referenced.
        self._npc_windows: dict[str, QMainWindow] = {}
        # The session's cast as live cards need it: one entry per file name, with
        # the loaded model and its transient initiative. Rebuilt on every refresh
        # from disk, carrying the runtime state across.
        self._npc_state: dict[str, _NpcEntry] = {}
        # The manual (un-rolled) order of the cast, by file name. Rolled NPCs sort
        # above this by initiative; dragging a card sets its place here.
        self._manual_order: list[str] = []
        # One relay attempt per hosting run: the fallback republishes, and a
        # second attempt off that would loop.
        self._relay_attempted = False
        # Ids for rolls made before hosting: negative so they never collide with a
        # session's positive seqs, and still removable from the local view.
        self._offline_seq = 0
        # The connection choices this window hosts with, picked in the launch dialog
        # before the window opened. A sane default lets the relay fallback and the
        # port read something even when a caller passes none (e.g. the test fixture).
        self._host_options = host_options or HostOptions(
            name="Session", port=DEFAULT_PORT, tunnel="", relay=storage.relay_url(), use_relay=True
        )
        set_active_session(self._bridge)
        # The session to run was chosen in the launch dialog; fall back to the last
        # one this app hosted (or a fresh one) so the window always has a session.
        self._state: SessionState = state or last_session() or new_session("Session")

        # The blocks are draggable / hideable / reorderable the same way the
        # character sheet's are — a shared BlockCanvas rather than a fixed stack.
        # Players and NPCs get a growable width just wider than one card, so their
        # FlowLayout keeps at least one card per row and fits more as the window
        # widens.
        panels = [
            ("players", "Players", self._build_players_box()),
            ("npcs", "NPCs", self._build_npcs_box()),
            ("rolls", "Rolls", self._build_rolls_box()),
        ]
        for _key, _title, box in panels:
            strip_groupbox_caption(box)  # the block's title bar carries the name now
        # The GM blocks' bounds live in block_sizes.json alongside the sheet's, under
        # gm_-prefixed keys, so a theme can retune them the same way (the canvas keys
        # them without the prefix, since the GM window has its own block namespace).
        shipped = load_block_sizes()
        sizes = {key: shipped.get(f"gm_{key}", BlockSize()) for key, _title, _box in panels}
        default_rows = [["players"], ["npcs"], ["rolls"]]
        # Only a handful of blocks, so a top-aligned stack would leave a wide gap
        # under the last one; let the bottom block (the rolls board's history)
        # stretch to fill the page instead.
        self._canvas = BlockCanvas(panels, sizes, default_rows, fill_last=True)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._scroll.setWidget(self._canvas)
        self._canvas.set_scroll_area(self._scroll)

        # The page and the pinned strip beside it — the GM keeps a block (the
        # roll history, say) in view while the rest of the board scrolls.
        self._board = PinnedBoard(self._scroll, self._canvas)
        self._canvas.set_pinned_board(self._board)

        central = QWidget()
        central_layout = QVBoxLayout(central)
        central_layout.setContentsMargins(0, 0, 0, 0)
        central_layout.addWidget(self._board, stretch=1)
        # A persistent strip along the bottom, not a draggable block: the hosting
        # status and the reachability advice, then a transient notice line.
        central_layout.addWidget(self._build_status_strip())
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

        # Hosting was chosen in the launch dialog, so start it now — the window
        # comes up already reachable, with the join code a menu click away.
        if autohost:
            self.start_hosting(self._host_options)

    def _update_min_width(self) -> None:
        """Pin the page's min width to the widest docked row (blocks never squash)."""
        bar = self._scroll.verticalScrollBar()
        extra = bar.sizeHint().width() if bar is not None else 0
        self._scroll.setMinimumWidth(self._canvas.content_minimum_width() + extra + 2)

    def _build_menu(self) -> None:
        """A Session menu (copy the join code) and a View menu (show/hide blocks)."""
        session_menu = self.menuBar().addMenu("&Session")
        self._copy_code_action = session_menu.addAction("Copy join code")
        self._copy_code_action.setEnabled(False)
        self._copy_code_action.triggered.connect(self._copy_code)

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

        # The notice strip below fades after ten seconds by design; this does not.
        # It is the only place a GM can look to see whether their table is still
        # reachable — including the case where hosting is fine but the relay
        # registration died and no new player can get in.
        install_connection_indicator(self).set_bridge(self._bridge)

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

    def _build_status_strip(self) -> _Notice:
        """The bottom strip: the hosting status line and the reachability advice.

        Not a draggable block — the session controls moved to the launch dialog, so
        all that stays on the board is this feedback: whether players can reach the
        game, and the verbatim advice from ``discovery`` when they may not. The join
        code itself is copied from **Session ▸ Copy join code**. It rides in a
        dismissible :class:`_Notice` so it can be closed and fades on its own once
        read, rather than sitting on the board for the whole session.
        """
        notice = _Notice()

        self._status_label = _wrapped("")
        font = self._status_label.font()
        font.setBold(True)
        self._status_label.setFont(font)
        notice.add_widget(self._status_label)

        # One label per advice string, so each is rendered exactly as
        # ``discovery`` wrote it. They are player-facing sentences, not log lines.
        self._advice_layout = QVBoxLayout()
        notice.add_layout(self._advice_layout)
        self._status_notice = notice
        return notice

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
        session is which of them are in it (``SessionState.npc_paths``). Hence three
        buttons: write a new one from a blank sheet, throw one together from five
        numbers, or bring an existing one in.
        """
        box = QGroupBox("NPCs")
        layout = QVBoxLayout(box)

        buttons = QHBoxLayout()
        quick = QPushButton("Quick NPC…")
        quick.setToolTip("A mook from five numbers — name, attack, effect, defence, toughness.")
        quick.clicked.connect(self._quick_npc)
        buttons.addWidget(quick)
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
        # A thin accent bar shown between cards while one is dragged, so the GM sees
        # where it will land before letting go (the same widget the block canvas uses).
        self._npc_drop_indicator = DropIndicator(self._npc_container)
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
        self._history.saveToggled.connect(self._roller.toggle_quick_roll)
        self._history.rollFollowUp.connect(self._roller.roll_spec)
        self._history.rollRemovedLocally.connect(self._on_local_roll_removed)
        # A card's star shows whether that roll is already in the roller's strip, so
        # it has to hear about every chip that comes or goes — and once up front, since
        # the strip is restored from settings with whatever was saved last time.
        self._roller.quickRollsChanged.connect(self._sync_quick_roll_state)
        self._sync_quick_roll_state()
        self._history.setMinimumHeight(240)
        # Hold the GM's own roll until its die stops tumbling; the roller cues it.
        self._history.set_defer_own(True)
        self._roller.sessionRollRevealed.connect(self._history.release_roll)
        layout.addWidget(self._history, stretch=1)
        return box

    def _sync_quick_roll_state(self) -> None:
        """Push the roller's quick-roll strip into the history's stars."""
        self._history.set_quick_roll_state(
            self._roller.quick_roll_keys(), not self._roller.quick_rolls_full()
        )

    def _build_notice(self) -> _Notice:
        self._notice = _Notice()
        self._notice_label = _wrapped("")
        self._notice.add_widget(self._notice_label)
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
        # The other way of being in a session: dialled in to one hosted on a
        # server rather than hosting it here.
        self._bridge.connected.connect(self._on_joined)
        self._bridge.disconnected.connect(self._on_left)

    # -- hosting -----------------------------------------------------------

    @property
    def bridge(self) -> SessionBridge:
        """The session this window drives — the seam later phases attach to."""
        return self._bridge

    def connect_to_server(self, entry: dict, server_label: str = "") -> bool:
        """Take the GM's seat on a session hosted elsewhere.

        *entry* is one row of the hub's catalog: it carries the join code and the
        session's gm token, which are the two things this app cannot work out for
        itself. Returns whether the connection was made; the caller reports why
        not, since it owns the dialog the GM is standing in.
        """
        try:
            code = discovery.decode_join_code(entry.get("join_code", ""))
        except discovery.JoinCodeError as exc:
            self._show_notice(str(exc), theme.color("tint.worse"))
            return False
        # Both are set before the join, not after: connecting raises `connected`
        # synchronously, and _on_joined writes the status line that names them.
        self._server_label = server_label
        # The catalog handed us the code; the GM should not have to go back and
        # ask the server for the one thing they need to invite anyone.
        self._join_code = str(entry.get("join_code", ""))
        try:
            self._bridge.join(
                code,
                display_name="GM",
                gm_token=str(entry.get("gm_token", "")),
            )
        except Exception as exc:  # noqa: BLE001 - every failure is one message
            self._server_label = ""
            self._join_code = ""
            self._show_notice(f"Could not reach the session: {exc}", theme.color("tint.worse"))
            return False
        self._copy_code_action.setEnabled(bool(self._join_code))
        return True

    def _on_joined(self, welcome: dict) -> None:
        """Seed the window from the Welcome of a session hosted elsewhere.

        The GM window is written against a SessionState, and a session on a server
        is not ours to own — so what we keep is a **mirror**, filled from the wire
        and never saved to disk. Every read in this window goes on working; only
        the writes were rerouted through the bridge, which knows whose session it
        is.
        """
        if not self._bridge.is_gm:
            # Refused the GM seat but let in as a player: possible only if the
            # token went stale between listing the catalog and connecting. Say so
            # rather than presenting a console whose buttons all quietly fail.
            self._show_notice(
                "This app is connected as a player, not the GM — its GM controls will "
                "not work. Reopen GM Mode to pick the session up again.",
                theme.color("tint.worse"),
            )
        self._state.id = str(welcome.get("session_id", "")) or self._state.id
        self._state.name = str(welcome.get("session_name", "")) or self._state.name
        self._state.npc_paths = [str(p) for p in welcome.get("npc_paths", [])]
        self._mirror_roster(welcome.get("roster", []))
        where = f" on {self._server_label}" if self._server_label else ""
        self._set_status(f"Connected to “{self._state.name}”{where}", theme.color("accent"))
        self._clear_advice()
        self._show_advice(
            (
                "Players join with this session's join code, whether or not you are here.",
                "Closing this window leaves the table running.",
            )
        )
        self.setWindowTitle(f"GM Mode — {self._state.name}")
        self._refresh_rolls()
        self._refresh_npcs()
        self._refresh_idle_status()

    def _on_left(self, reason: str) -> None:
        self._set_status(f"Disconnected ({reason})", theme.color("tint.worse"))

    def _mirror_roster(self, roster: list) -> None:
        """Rebuild the mirrored roster from a wire roster, keeping snapshots.

        Roster entries carry no character (they never do — the table's combined
        sheets would not fit in one message), so the snapshots this window has
        collected separately are preserved rather than blanked.
        """
        players: dict[str, PlayerSlot] = {}
        for entry in roster:
            if not isinstance(entry, dict):
                continue
            slot = PlayerSlot.from_dict(entry)
            slot.character = self._snapshots.get(slot.player_id, {})
            players[slot.player_id] = slot
        self._state.players = players

    def start_hosting(self, options: HostOptions) -> None:
        """Begin hosting with *options* (from the launch dialog) and publish.

        The connection choices are held for the run so the relay fallback and the
        reachability probe read the same values the GM picked in the launch dialog.
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

        self._set_status("Working out how players can reach you…", theme.color("accent"))
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
                self._show_notice(
                    f"{discovery.ADVICE_RELAY_UNREACHABLE} ({exc})", theme.color("tint.worse")
                )
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
            self._show_notice(
                f"That relay address cannot be used: {exc}", theme.color("tint.worse")
            )
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
        self._set_status("Trying the relay…", theme.color("accent"))
        self._bridge.stop()
        if not self._begin_hosting(relay) and not self._begin_hosting():
            self._refresh_idle_status()
            return
        self._bridge.publish()

    def stop_hosting(self) -> None:
        """Leave the session, clear the join code and cards, and go back to idle.

        Symmetrical for both ways of being in one: this app's own server is shut
        down, while a session hosted on a server is only *disconnected from* — it
        goes on running with its players in it, which is the point of putting it
        there.
        """
        self._relay_attempted = False
        self._bridge.stop()
        self._join_code = ""
        self._copy_code_action.setEnabled(False)
        self._clear_advice()
        self._clear_cards()
        self._refresh_idle_status()

    def _refresh_rolls(self) -> None:
        """Point the history at the live session, or at the one on disk.

        In a session it follows the bridge, whether this app is hosting or dialled
        in to a session on a server. Between sessions there is nothing to follow,
        but the state loaded from the workspace still carries the log — so
        reopening GM Mode shows last night's rolls rather than a blank panel.
        """
        if self._bridge.hosting or self._bridge.joined:
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
                "label": roll.get("label", ""),
                # Local-only, as everywhere: it carries the chain this card offers.
                "spec": roll.get("spec"),
            }
        )

    def _on_local_roll_removed(self, seq: int) -> None:
        """Persist a roll the GM struck while not hosting.

        A live session removes it wherever the session lives (and this never
        fires). Off the air the panel shows the resumed session's persisted log,
        so drop the roll from the state and save it too; a pre-hosting offline
        roll is not in the state, so this is a no-op for those.
        """
        if self._bridge.hosting or self._bridge.joined:
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
        self._bridge.set_session_name(name)
        self.setWindowTitle(f"GM Mode — {name}")

    # -- bridge signals ----------------------------------------------------

    def _on_started(self, host: str, port: int) -> None:
        self._rename_session()
        self._notice.dismiss()

    def _on_stopped(self) -> None:
        self._copy_code_action.setEnabled(False)
        self._refresh_rolls()

    def _on_published(self, reachability: discovery.Reachability) -> None:
        if self._should_try_relay(reachability):
            self._fall_back_to_relay()
            return

        self._join_code = self._bridge.join_code()
        self._copy_code_action.setEnabled(bool(self._join_code))

        if reachability.method == discovery.METHOD_RELAY:
            self._set_status(
                "Players anywhere can join with this code — this session is going "
                "through the relay.",
                theme.color("tint.better"),
            )
        elif reachability.method == discovery.METHOD_MANUAL:
            self._set_status(
                f"This code points at {reachability.host}:{reachability.port}. "
                "Anyone who can reach that address can join.",
                theme.color("accent"),
            )
        elif reachability.internet_reachable:
            self._set_status(
                "Players anywhere can join with this code.",
                theme.color("tint.better"),
            )
        else:
            self._set_status(
                "Only players on this network can join with this code — "
                "nothing outside it can reach this machine yet.",
                theme.color("tint.worse"),
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
        # A returning player is not news the same way a new one is, and saying
        # "joined" for both is what made a reconnect look like a second arrival.
        if payload.get("adopted") or not payload.get("new", True):
            self._show_notice(f"{name} rejoined.", theme.color("tint.better"))
            return
        self._show_notice(f"{name} joined.", theme.color("tint.better"))

    def _on_refused(self, payload: dict) -> None:
        self._show_notice(
            f"A connection was refused: {payload.get('message') or payload.get('code', '')}",
            theme.color("tint.worse"),
        )

    def _on_error(self, code: str, message: str) -> None:
        self._show_notice(f"{message or code}", theme.color("tint.worse"))

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
                card.setHeroPointsRequested.connect(self._set_hero_points)
                card.removePlayerRequested.connect(self._remove_player)
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

    # -- removing a player --------------------------------------------------

    def _remove_player(self, player_id: str) -> None:
        """Kick a player out of the session, once the GM confirms it.

        The kick drops the seat and closes the socket; the server broadcasts a new
        roster without it, so :meth:`_show_roster` reconciles the card away. Any
        read-only sheet the GM had open for that player is closed too.
        """
        card = self._cards.get(player_id)
        name = card.display_name() if card is not None else "this player"
        # Do not promise to disconnect someone who already is: a GM clearing a
        # seat after the session wants to be told what actually happens, and
        # "they will be disconnected" reads as a warning not to.
        online = card is not None and card.connected
        detail = (
            "They will be disconnected."
            if online
            else "They are already offline; this clears their seat."
        )
        confirm = QMessageBox.question(
            self,
            "Remove player" if online else "Remove seat",
            f"Remove {name} from the session? {detail}",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return
        if not self._bridge.kick(player_id):
            self._show_notice(f"{name} could not be removed.", theme.color("tint.worse"))
            return
        window = self._player_windows.pop(player_id, None)
        if window is not None:
            window.close()
        self._show_notice(f"{name} was removed from the session.", theme.color("accent"))

    # -- fast-apply conditions ---------------------------------------------

    def _apply_condition(self, player_id: str, condition_id: str, parameter: object) -> None:
        self._send_condition("apply", player_id, condition_id, parameter)

    def _remove_condition(self, player_id: str, condition_id: str, parameter: object) -> None:
        self._send_condition("remove", player_id, condition_id, parameter)

    def _set_hero_points(self, player_id: str, value: int) -> None:
        """Order a player's hero-point total changed on their live sheet.

        Like a condition, the GM only asks: the command goes down the player's
        connection, their app writes the value, and the card's pips move only once
        the snapshot comes back — so a command that did not land stays visible.
        """
        who = self._player_name(player_id)
        if not self._bridge.set_hero_points(player_id, value):
            self._show_notice(
                f"{who} is not connected, so their hero points were not changed.",
                theme.color("tint.worse"),
            )

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
        subject = str(parameter) if parameter else None
        name = self._condition_name(condition_id, subject)
        who = self._player_name(player_id)
        send = self._bridge.apply_condition if action == "apply" else self._bridge.remove_condition
        if not send(player_id, condition_id, subject):
            self._show_notice(
                f"{who} is not connected, so “{name}” was not sent.", theme.color("tint.worse")
            )
            return
        verb = "Applied" if action == "apply" else "Removed"
        self._show_notice(f"{verb} “{name}” on {who}.", theme.color("accent"))

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

        In a live session the state belongs to whoever is hosting — this app's own
        server lock, or a box across the internet — so the change goes through the
        bridge, which knows which. Off the air the window owns the state and writes
        it itself; either way the cast survives a restart.
        """
        self._state.npc_paths = list(paths)
        if self._bridge.set_npc_paths(paths):
            return
        self._state.touch()
        try:
            store.save_session(self._state)
            storage.update_settings(session_last_id=self._state.id)
        except OSError:
            pass  # an unwritable workspace is not worth a dialog mid-session

    def _refresh_npcs(self) -> None:
        """Rebuild the NPC grid from the session's cast.

        Each NPC's model is loaded from disk, so a saved edit shows up here; the
        transient initiative is carried over from the previous state by file name.
        """
        while self._npc_flow.count():
            item = self._npc_flow.takeAt(0)
            widget = item.widget() if item is not None else None
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()

        summaries = self._npc_summaries()
        previous = self._npc_state
        self._npc_state = {}
        for summary in summaries:
            if summary.path is None:
                continue
            name = summary.path.name
            character = library.load_character(summary.path)
            initiative = previous[name].initiative if name in previous else None
            self._npc_state[name] = _NpcEntry(
                path=Path(summary.path),
                summary=summary,
                character=character,
                initiative=initiative,
            )

        # Keep the manual order in step with the cast: drop the departed, append
        # newcomers at the end of the un-rolled zone.
        self._manual_order = [n for n in self._manual_order if n in self._npc_state]
        self._manual_order += [n for n in self._npc_state if n not in self._manual_order]

        for name in self._ordered_npcs():
            entry = self._npc_state[name]
            card = NPCCard(entry.character, entry.summary, self._data, initiative=entry.initiative)
            card.openRequested.connect(self._open_npc)
            card.removeRequested.connect(self._remove_npc)
            card.deleteRequested.connect(self._delete_npc)
            card.applyConditionRequested.connect(self._apply_npc_condition)
            card.removeConditionRequested.connect(self._remove_npc_condition)
            card.initiativeRolled.connect(self._on_npc_initiative)
            card.copyRequested.connect(self._copy_npc)
            card.reorderRequested.connect(self._reorder_npc)
            card.reorderPreview.connect(self._show_npc_drop_indicator)
            card.reorderPreviewEnded.connect(self._npc_drop_indicator.hide_indicator)
            entry.card = card
            self._npc_flow.addWidget(card)
        self._no_npcs.setVisible(not self._npc_state)

    def _ordered_npcs(self) -> list[str]:
        """The cast in render order: rolled NPCs highest-initiative first, then the
        un-rolled ones in their manual order."""
        base = [n for n in self._manual_order if n in self._npc_state]
        base += [n for n in self._npc_state if n not in base]
        rolled = sorted(
            (n for n in base if self._npc_state[n].initiative is not None),
            key=lambda n: self._npc_state[n].initiative,  # type: ignore[arg-type,return-value]
            reverse=True,
        )
        unrolled = [n for n in base if self._npc_state[n].initiative is None]
        return rolled + unrolled

    def _on_npc_initiative(self, name: str, total: int) -> None:
        """Remember an NPC's rolled initiative and re-sort the grid around it."""
        entry = self._npc_state.get(name)
        if entry is None:
            return
        entry.initiative = total
        self._refresh_npcs()

    def _show_npc_drop_indicator(self, name: str, target_index: int) -> None:
        """Position the drop bar before the card at *target_index* (after the last
        when the drop is past every card)."""
        count = self._npc_flow.count()
        if count == 0:
            self._npc_drop_indicator.hide_indicator()
            return
        index = max(0, min(target_index, count))
        if index >= count:
            item = self._npc_flow.itemAt(count - 1)
            geo = item.widget().geometry()
            rect = QRect(geo.right() + 1, geo.top(), 3, geo.height())
        else:
            item = self._npc_flow.itemAt(index)
            geo = item.widget().geometry()
            rect = QRect(geo.left() - 3, geo.top(), 3, geo.height())
        self._npc_drop_indicator.move_to(rect)

    def _reorder_npc(self, name: str, target_index: int) -> None:
        """Move an NPC to a dropped slot, in the manual (un-rolled) zone.

        A drag always drops into the manual zone, so the NPC's rolled initiative
        (if any) is cleared; it then takes the target position among the un-rolled
        cards. A drop inside the rolled block clamps to the top of the un-rolled
        zone, since rolled NPCs always sort above it.

        Dropping an *un-rolled* NPC in front of a rolled one is the GM saying it
        should act before that NPC — but with no initiative to sort by, that is
        impossible while the other keeps its roll. So both are dropped into the
        manual zone: the neighbour loses its initiative too, and the GM orders the
        pair by hand.
        """
        entry = self._npc_state.get(name)
        if entry is None:
            return
        order = self._ordered_npcs()
        neighbour = order[target_index] if 0 <= target_index < len(order) else None
        if (
            entry.initiative is None
            and neighbour is not None
            and neighbour != name
            and self._npc_state[neighbour].initiative is not None
        ):
            self._npc_state[neighbour].initiative = None
        entry.initiative = None
        order = [n for n in self._ordered_npcs() if n != name]
        order.insert(max(0, min(target_index, len(order))), name)
        self._manual_order = order
        self._refresh_npcs()

    def _create_npc(self) -> None:
        """Write a new NPC: an editable, simplified sheet that saves into the cast."""
        self._track_npc_window(NPCWindow(locked=False))

    def _quick_npc(self) -> None:
        """Build a mook from five numbers, then open it like any other NPC.

        Saved immediately rather than handed to an unsaved sheet: the wizard has
        already collected everything the roster needs, so the creature can be in the
        cast — and rollable against — before the GM decides whether to fill anything
        else in. The sheet that opens afterwards is the ordinary one.
        """
        dialog = QuickNPCDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        entered = dialog.value()
        npc = quick_npc(
            load_game_data(),
            name=entered.name,
            attack=entered.attack,
            effect=entered.effect,
            defence=entered.defence,
            toughness=entered.toughness,
            image_path=entered.image_path,
        )
        path = library.save_character(npc, directory=self._npc_dir())
        self._register_npc(path)
        self._track_npc_window(NPCWindow(character=npc, path=path, locked=False))

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

    def _copy_npc(self, name: str) -> None:
        """Duplicate an NPC into a new one, named ``Goon → Goon-2``.

        A deep copy of the model (through its own serialization), renamed to the
        next free ``<base>-<n>``, saved as its own file, and added to the cast.
        """
        entry = self._npc_state.get(name)
        if entry is None:
            return
        copy = Character.from_dict(entry.character.to_dict())
        existing = {other.summary.name for other in self._npc_state.values()}
        copy.profile["hero_name"] = _next_copy_name(entry.summary.name, existing)
        path = library.save_character(copy, directory=self._npc_dir())
        self._register_npc(path)

    def _open_npc(self, name: str) -> None:
        """Open an NPC's sheet, or raise the one already open for it.

        Not replaced the way a player's read-only sheet is: this one is editable,
        so throwing it away could throw away work the GM has not saved yet.
        """
        entry = self._npc_state.get(name)
        if entry is None:
            return
        path = entry.path
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

    def _remove_npc(self, name: str) -> None:
        """Take an NPC out of this session, leaving the file where it is."""
        entry = self._npc_state.get(name)
        display = entry.summary.name if entry is not None else name
        self._set_npc_paths([n for n in self._state.npc_paths if n != name])
        self._refresh_npcs()
        self._show_notice(f"“{display}” is no longer in this session.", theme.color("accent"))

    def _delete_npc(self, name: str) -> None:
        """Delete an NPC's file for good, once the GM confirms it."""
        entry = self._npc_state.get(name)
        if entry is None:
            return
        confirm = QMessageBox.question(
            self,
            "Delete NPC",
            f"Delete “{entry.summary.name}”? This cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return
        library.delete_character(entry.path)
        self._remove_npc(name)

    # -- NPC conditions -----------------------------------------------------

    def _apply_npc_condition(self, name: str, condition_id: str, parameter: object) -> None:
        """Apply a condition straight onto the NPC's local model, and persist it."""
        entry = self._npc_state.get(name)
        if entry is None:
            return
        subject = str(parameter) if parameter else None
        apply_condition(entry.character, condition_id, self._data, parameter=subject)
        self._after_npc_condition_change(entry, condition_id, subject, applying=True)

    def _remove_npc_condition(self, name: str, condition_id: str, parameter: object) -> None:
        """Take one condition off the NPC's model again."""
        entry = self._npc_state.get(name)
        if entry is None:
            return
        subject = str(parameter) if parameter else None
        applied = matching_condition(entry.character, condition_id, subject)
        if applied is not None:
            decrement_condition(entry.character, applied)
        self._after_npc_condition_change(entry, condition_id, subject, applying=False)

    def _after_npc_condition_change(
        self, entry: _NpcEntry, condition_id: str, parameter: str | None, *, applying: bool
    ) -> None:
        """Restate the NPC's card and persist the change.

        Unlike a player, an NPC is local — so the change is applied to the model
        here rather than sent over the wire. If a sheet for this NPC is open, route
        the same change through its conditions block so the open sheet stays in
        sync and owns its own save; otherwise write the model to its file now.
        """
        window = self._window_for(entry.path)
        if window is not None:
            section = getattr(window.sheet, "conditions", None)
            if section is not None:
                if applying:
                    section.apply_condition_by_id(condition_id, parameter)
                else:
                    section.remove_condition_by_id(condition_id, parameter)
        else:
            try:
                library.save_character(entry.character, path=entry.path)
            except OSError:
                pass  # an unwritable workspace is not worth a dialog mid-session
        if entry.card is not None:
            entry.card.refresh_conditions()

    # -- small view helpers ------------------------------------------------

    def _refresh_idle_status(self) -> None:
        """What the status line says while nothing is being hosted.

        A session on a server is not idle just because this app is not hosting it,
        so leave the connected status where it is rather than overwriting it with
        "Not hosting" — which would be true and wholly misleading.
        """
        if self._bridge.joined:
            return
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
        self._status_notice.poke()

    def _show_advice(self, advice: tuple[str, ...]) -> None:
        self._clear_advice()
        for line in advice:
            self._advice_layout.addWidget(_wrapped(f"• {line}"))
        # The advice belongs to the same card as the status it explains; bring it
        # back to full opacity so a slow-appearing relay note isn't already fading.
        self._status_notice.poke()

    def _clear_advice(self) -> None:
        while self._advice_layout.count():
            item = self._advice_layout.takeAt(0)
            widget = item.widget() if item is not None else None
            if widget is not None:
                widget.deleteLater()

    def _show_notice(self, text: str, colour: str) -> None:
        self._notice_label.setText(text)
        self._notice_label.setStyleSheet(f"color: {colour};" if colour else "")
        if text:
            self._notice.poke()
        else:
            self._notice.dismiss()

    def _copy_code(self) -> None:
        if not self._join_code:
            return
        clipboard = QApplication.clipboard()
        if clipboard is not None:
            clipboard.setText(self._join_code)
        self._show_notice("Join code copied — send it to your players.", theme.color("accent"))

    # -- lifecycle ---------------------------------------------------------

    def showEvent(self, event: QShowEvent) -> None:  # noqa: N802 (Qt override)
        """Re-arm as the process-wide session — the window is reusable after a close."""
        set_active_session(self._bridge)
        super().showEvent(event)

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802 (Qt override)
        """Leave the session and give the router its port back before going away.

        The window itself survives (the launcher keeps it, and reopening GM Mode
        shows this one again) so the session it was working on — its name, its
        roster, its rolls — is still here rather than replaced by a blank one. A
        session hosted on a server survives rather more thoroughly: closing this
        window disconnects the GM and leaves the table playing.
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


class _Notice(QFrame):
    """A dismissible message card that clears itself out after a dwell.

    The GM board's feedback — the hosting status, the reachability advice, and the
    one-off notices ("Join code copied", "A player joined") — used to sit pinned to
    the bottom forever. Each of these is now shown in a ``_Notice`` instead: it
    carries an ``✕`` to dismiss it immediately, and if left alone it fades itself
    out :data:`DWELL_MS` after it was last poked, with a slow tail so it dissolves
    rather than blinks off. Updating its contents and calling :meth:`poke` again
    brings it back to full opacity and restarts the countdown.
    """

    #: How long a message stays fully visible before it starts to fade.
    DWELL_MS = 10_000
    #: How long the fade-out itself takes — deliberately slow, so it eases away.
    FADE_MS = 1_500

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("gmNotice")

        row = QHBoxLayout(self)
        row.setContentsMargins(8, 2, 2, 2)
        row.setSpacing(4)
        self._body = QVBoxLayout()
        self._body.setContentsMargins(0, 0, 0, 0)
        self._body.setSpacing(0)
        row.addLayout(self._body, stretch=1)

        self._close = QToolButton()
        self._close.setText("✕")
        self._close.setAutoRaise(True)
        self._close.setToolTip("Dismiss this message")
        self._close.setCursor(Qt.CursorShape.ArrowCursor)
        self._close.clicked.connect(self.dismiss)
        row.addWidget(self._close, alignment=Qt.AlignmentFlag.AlignTop)

        # Opacity is driven by an effect so the whole card (text and ✕ alike) can
        # ease out together; it stays attached, which is fine for a small strip.
        self._effect = QGraphicsOpacityEffect(self)
        self._effect.setOpacity(1.0)
        self.setGraphicsEffect(self._effect)
        self._fade = QPropertyAnimation(self._effect, b"opacity", self)
        self._fade.setDuration(self.FADE_MS)
        self._fade.setEasingCurve(QEasingCurve.Type.InCubic)
        self._fade.finished.connect(self._settle)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(self.DWELL_MS)
        self._timer.timeout.connect(self._begin_fade)
        self.hide()

    def add_widget(self, widget: QWidget) -> None:
        self._body.addWidget(widget)

    def add_layout(self, layout: QVBoxLayout) -> None:
        self._body.addLayout(layout)

    def poke(self) -> None:
        """Show the card at full opacity and restart the dwell-then-fade timer."""
        self._fade.stop()
        self._effect.setOpacity(1.0)
        self.setVisible(True)
        self._timer.start()

    def dismiss(self) -> None:
        """Retire the card at once — the ✕ button, or a caller clearing it."""
        self._timer.stop()
        self._fade.stop()
        self._effect.setOpacity(1.0)
        self.setVisible(False)

    def _begin_fade(self) -> None:
        self._fade.stop()
        self._fade.setStartValue(self._effect.opacity())
        self._fade.setEndValue(0.0)
        self._fade.start()

    def _settle(self) -> None:
        if self._effect.opacity() <= 0.01:
            self.setVisible(False)

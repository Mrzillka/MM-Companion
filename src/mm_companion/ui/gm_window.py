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

from PySide6.QtCore import QByteArray, QEasingCurve, QEvent, QPropertyAnimation, Qt, QTimer
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
from mm_companion.core.dice import roll_d20
from mm_companion.core.npc import quick_npc
from mm_companion.core.rules import (
    KIND_INITIATIVE,
    PinRef,
    RollSpec,
    apply_condition,
    apply_damage_step,
    damage_step_summary,
    damage_steps,
    decrement_condition,
    default_pins,
    initiative_modifier,
    parse_pins,
    requested_roll_choices,
)
from mm_companion.core.session import discovery, store
from mm_companion.core.session.model import (
    KIND_REQUEST,
    KIND_ROLL,
    PlayerSlot,
    SessionState,
    new_id,
    new_session,
)
from mm_companion.core.session.net import DEFAULT_PORT
from mm_companion.ui import theme
from mm_companion.ui.block_canvas import BlockCanvas
from mm_companion.ui.block_sizes import BlockSize, load_block_sizes
from mm_companion.ui.card_drop import CardDropFlow
from mm_companion.ui.compact import CompactController
from mm_companion.ui.connection_indicator import install_connection_indicator
from mm_companion.ui.dice_roller import DiceRollerView
from mm_companion.ui.npc_card import NPCCard
from mm_companion.ui.npc_quick_dialog import QuickNPCDialog
from mm_companion.ui.npc_window import NPCWindow
from mm_companion.ui.pin_picker import PinPickerDialog
from mm_companion.ui.pinned_panel import PinnedBoard
from mm_companion.ui.player_card import PlayerCard
from mm_companion.ui.roll_history import RollHistoryPanel
from mm_companion.ui.scene_board import NO_SCENE_GM, SceneBoard
from mm_companion.ui.sections.conditions import condition_display_name, matching_condition
from mm_companion.ui.sections.titled_section import strip_groupbox_caption
from mm_companion.ui.session_bridge import SessionBridge, last_session, set_active_session
from mm_companion.ui.session_dialogs import HostOptions
from mm_companion.ui.session_portrait import encode_scene_portrait, shrink_portrait
from mm_companion.ui.undo import absorbing

#: What the listening socket binds to. Every interface, so a player on the LAN
#: reaches it whichever adapter they come in on; a test overrides it to loopback.
BIND_ADDRESS = "0.0.0.0"

NO_PLAYERS = "Nobody has joined yet — send your players the join code (Session ▸ Copy join code)."

NO_NPCS = "No NPCs in this session yet — create one, or add one you have already written."

#: The two captions of the one collapse-all button. Which one it wears says what
#: clicking it will do *and* what the board currently looks like.
COLLAPSE_ALL = "Collapse all"
EXPAND_ALL = "Expand all"

#: How an entry's source is written in the GM's private ref → source map. A kind
#: and an identity, because the two kinds are told apart nowhere else: what a ref
#: points at is a file name or a player id, and those are not distinguishable by
#: looking at them.
SCENE_NPC = "npc"
SCENE_PLAYER = "player"


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
    #: Whether this card shows its short form. Held here as well as in settings
    #: because :meth:`GMWindow._refresh_npcs` destroys and rebuilds every card —
    #: anything kept on the widget is lost the first time an initiative is rolled.
    collapsed: bool = False


@dataclass
class _SceneEntry:
    """One place on the shared board, and what the GM knows it to be.

    The public half of this — a name, an initiative, some conditions — is derived
    fresh on every push, because it lives on the NPC's model or in the player's
    latest snapshot and would be a lie the moment either changed. What is held
    here is only what *cannot* be derived: which creature this place belongs to.

    ``ref`` is minted here and is deliberately opaque. It is the only thing about
    an entry that reaches a player, so it must say nothing: an NPC's file name can
    be a spoiler outright, and a scene is exactly where a GM would notice that too
    late.
    """

    ref: str
    kind: str  # SCENE_NPC | SCENE_PLAYER
    source: str  # the NPC's file name, or the player's public id

    def wire_source(self) -> str:
        """How the private map records it: ``"npc:<file>"`` / ``"player:<id>"``."""
        return f"{self.kind}:{self.source}"

    @classmethod
    def from_wire_source(cls, ref: str, raw: str) -> _SceneEntry | None:
        """Rebuild from the map a welcome handed back, or ``None`` if unreadable."""
        kind, _, source = str(raw).partition(":")
        if kind not in (SCENE_NPC, SCENE_PLAYER) or not source:
            return None
        return cls(ref=ref, kind=kind, source=source)


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


def _npc_key(file_name: str) -> str:
    """A pin-store key for an NPC. Its file name is its identity everywhere else."""
    return f"npc:{file_name}"


def _player_key(player_id: str) -> str:
    """A pin-store key for a seat.

    Keyed by the *public* player id, which is what survives a reconnect
    (:meth:`~mm_companion.core.session.model.SessionState.player_by_id_if_free`).
    A player who ends up in a fresh seat starts from the defaults again — pins are
    a GM's private scratch note, and the honest alternative is session state on
    the server for something no player should see.
    """
    return f"player:{player_id}"


def _load_pins() -> dict[str, list[PinRef]]:
    """Every card's saved pin strip, from settings."""
    raw = storage.load_settings().get("gm_pins", {})
    if not isinstance(raw, dict):
        return {}
    return {str(key): parse_pins(value) for key, value in raw.items()}


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
        # What each card has pinned, keyed ``"npc:<file>"`` / ``"player:<id>"``.
        # Loaded once here rather than read per card: a card is rebuilt on every
        # refresh, and re-reading settings each time would make a strip's contents
        # depend on how recently the file was written.
        self._pins: dict[str, list[PinRef]] = _load_pins()
        # Which cards the GM has shrunk, by the same key as the pins above.
        self._collapsed_cards: dict[str, bool] = storage.gm_collapsed_cards()
        # Pin pickers open on a card, keyed the same way, kept referenced while up.
        self._pin_pickers: dict[str, PinPickerDialog] = {}
        # Sheets opened from a card, so a change to that card's strip can be pushed
        # into their row menus. A list per card: an NPC's sheet and a player's are
        # both singular in practice, but nothing here needs to insist on it.
        self._pin_sheets: dict[str, list] = {}
        # The Settings window, kept referenced while open like the sheets above.
        self._settings_window: QWidget | None = None
        # The manual (un-rolled) order of the cast, by file name. Rolled NPCs sort
        # above this by initiative; dragging a card sets its place here.
        self._manual_order: list[str] = []
        # The shared board: which creatures are on it, in the GM's own arrangement.
        # A list rather than a dict because the order *is* half the state — the
        # un-rolled half, which nothing else records.
        self._scene: list[_SceneEntry] = []
        # A player's rolled initiative, by player id. An NPC's lives on its
        # ``_NpcEntry`` and is deliberately not duplicated here: one number with
        # one owner is what stops the card badge and the board disagreeing.
        self._player_initiative: dict[str, int] = {}
        # What was last *sent* for each entry's picture, by ref. Portraits travel
        # apart from the scene and are not re-sent with it, so this is how a push
        # knows which ones are new — and it is keyed by the encoded payload rather
        # than by a flag so a player changing their portrait mid-session is caught.
        self._scene_portraits: dict[str, str] = {}
        # Whether joining puts a player on the board by itself. Read once here
        # through its accessor; the Settings page's change reaches an open window
        # on the next roster.
        self._scene_auto_players = storage.gm_scene_auto_players()
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
            # The Scene first: it is what a GM watches through a fight, while the
            # two rosters under it are where they go to change something.
            ("scene", "Scene", self._build_scene_box()),
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
        # The Rolls block starts in the strip rather than on the page, for the reason
        # the sheet's Dice block does: a roller that scrolls away with the board is
        # no use mid-fight. Both boards use the same seam, and the strip's default
        # edge is the right-hand one.
        default_rows = [["scene"], ["players"], ["npcs"]]
        # Only a handful of blocks, so a top-aligned stack would leave a wide gap
        # under the last one; let the bottom block (the NPC cards) stretch to fill
        # the page instead.
        self._canvas = BlockCanvas(
            panels, sizes, default_rows, fill_last=True, default_pinned=[["rolls"]]
        )

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

        # Everything that is the GM window proper, so compact mode can hide it in
        # one move and the window is free to shrink to the roller alone.
        self._full = QWidget()
        full_layout = QVBoxLayout(self._full)
        full_layout.setContentsMargins(0, 0, 0, 0)
        full_layout.addWidget(self._board, stretch=1)
        # A persistent strip along the bottom, not a draggable block: the hosting
        # status and the reachability advice, then a transient notice line.
        full_layout.addWidget(self._build_status_strip())
        full_layout.addWidget(self._build_notice())

        # The same compact mode a player's sheet has, over the same roller — and
        # now over the same DiceRollerView, so this window's release_roller /
        # restore_roller / compact_anchor are one line each, straight through to it.
        # The mini window's caption follows `windowTitleChanged`, so it picks up
        # the session name this window retitles itself with on its own.
        self._compact = CompactController(self, self, self._full)

        central = QWidget()
        central_layout = QVBoxLayout(central)
        central_layout.setContentsMargins(0, 0, 0, 0)
        central_layout.addWidget(self._full, stretch=1)
        central_layout.addWidget(self._compact.page)
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
        """Session (copy the join code), Settings, and View (show/hide blocks)."""
        session_menu = self.menuBar().addMenu("&Session")
        self._copy_code_action = session_menu.addAction("Copy join code")
        self._copy_code_action.setEnabled(False)
        self._copy_code_action.triggered.connect(self._copy_code)

        settings_menu = self.menuBar().addMenu("&Settings")
        settings_menu.addAction("Preferences...").triggered.connect(self._open_settings)

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
        """Save the GM window's geometry and block arrangement as a global preference.

        A window closed while compact is remembered at the size it was *before* it
        shrank; the mini window's own size is kept separately (see
        :mod:`mm_companion.ui.compact`).
        """
        self._compact.remember_size()
        state = self._compact.saved_geometry() or self.saveGeometry()
        geometry = bytes(state.toBase64()).decode("ascii")
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

    def _build_scene_box(self) -> QGroupBox:
        """The shared board, and the two things only the GM can do to it.

        Deliberately thin: the board is the same widget a player's sheet shows,
        and everything here is either a control a player has no business having or
        a readout of what the GM's own cards already say.
        """
        box = QGroupBox("Scene")
        layout = QVBoxLayout(box)

        buttons = QHBoxLayout()
        roll = QPushButton("Roll initiative")
        roll.setToolTip(
            "Roll for every NPC on the Scene, and ask each player for theirs in the "
            "shared roll log."
        )
        roll.clicked.connect(self._roll_scene_initiative)
        buttons.addWidget(roll)
        clear = QPushButton("New scene")
        clear.setToolTip("Clear the board and every initiative on it.")
        clear.clicked.connect(self._new_scene)
        buttons.addWidget(clear)
        buttons.addStretch()
        layout.addLayout(buttons)

        self._scene_board = SceneBoard(self._data, gm=True)
        self._scene_board.set_placeholder(NO_SCENE_GM)
        self._scene_board.dropped.connect(self._drop_on_scene)
        self._scene_board.removeRequested.connect(self._remove_from_scene)
        self._scene_board.initiativeCleared.connect(self._clear_scene_initiative)
        layout.addWidget(self._scene_board)
        layout.addStretch()
        return box

    def _build_players_box(self) -> QGroupBox:
        box = QGroupBox("Players")
        layout = QVBoxLayout(box)

        self._no_players = _wrapped(NO_PLAYERS)
        self._no_players.setEnabled(False)
        layout.addWidget(self._no_players)

        # A drop target only so a card dragged *out* of it and back reads as a
        # cancelled drag rather than a refusal; there is no order to edit here.
        self._cards_container = CardDropFlow("gmPlayerFlow")
        self._cards_flow = self._cards_container.flow
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
        # One button rather than two, and its caption is the action it will take —
        # which makes it a readout of the board as well as a control. Shrinking a
        # dozen mooks one caret at a time is the case the collapse exists for.
        self._collapse_all_button = QPushButton(COLLAPSE_ALL)
        self._collapse_all_button.clicked.connect(self._toggle_collapse_all)
        buttons.addWidget(self._collapse_all_button)
        layout.addLayout(buttons)

        self._no_npcs = _wrapped(NO_NPCS)
        self._no_npcs.setEnabled(False)
        layout.addWidget(self._no_npcs)

        # A drop target as well as a flow: the reorder gesture is a real drag now
        # (it had to become one to reach the Scene), so the bar showing where a card
        # will land is drawn by the container it will land in rather than guessed at
        # by the card being dragged.
        self._npc_container = CardDropFlow("gmNpcFlow")
        self._npc_flow = self._npc_container.flow
        self._npc_container.dropped.connect(self._drop_on_npcs)
        layout.addWidget(self._npc_container)
        layout.addStretch()
        return box

    def _build_rolls_box(self) -> QGroupBox:
        """The GM's own roller beside the table's shared history.

        The same :class:`~mm_companion.ui.dice_roller.DiceRollerView` a player's
        Dice block holds — so it reflows to the room it is given and follows the
        Normal / Compact / Extended preference exactly as theirs does — with the
        two things only a GM gets: a **Hidden roll** switch, and this window's own
        ``gm=True`` history in place of the private/shared pair the view would
        otherwise swap between. A hidden roll is recorded and shown here, marked,
        and never put on the wire — so it is not hidden by the players' apps
        agreeing to ignore it, it simply never reaches them.

        The history is this window's because it follows the bridge while there is
        one and the *workspace's* saved log when there is not (see
        :meth:`_refresh_rolls`), which is knowledge the view has no business
        carrying. Everything else about the arrangement is the view's.
        """
        # Held so compact mode can take the roller out and put it back, and so the
        # shrink button has something to float over; see :meth:`release_roller`
        # and :meth:`compact_anchor`.
        self._rolls_box = box = QGroupBox("Rolls")
        layout = QVBoxLayout(box)
        layout.setContentsMargins(0, 0, 0, 0)

        self._history = RollHistoryPanel(gm=True)
        self._history.rollRemovedLocally.connect(self._on_local_roll_removed)
        self._view = DiceRollerView(hidden_option=True, history=self._history)
        # Still the panel, so every card that loads or throws a spec into it (a
        # player's, an NPC's) reaches it by the same name it always did.
        self._roller = self._view.panel
        # While hosting, a roll made here goes through the server like everyone
        # else's and comes back on the shared feed. Before that there is no
        # session to record it in, so it is shown as a card and nothing more.
        self._roller.localRoll.connect(self._show_offline_roll)
        # The Request row, on the same terms a player's block gives it: the traits
        # come from the ruleset (a request is answered on someone else's sheet, so
        # they are character-free) and this window decides where the ask goes.
        self._roller.set_roll_choices(requested_roll_choices(self._data))
        self._roller.rollRequested.connect(self._request_roll)
        layout.addWidget(self._view)
        return box

    def _request_roll(self, spec: object) -> None:
        """Ask the table to roll something — the Request row's handler.

        The twin of :meth:`~mm_companion.ui.sections.dice.DiceSection.request_roll`,
        and it has to be written out here for the reason
        :meth:`_show_offline_roll` does: this window owns its history, so the
        view's own off-air fallback stands down and there would otherwise be no
        card at all before hosting starts — a button that silently does nothing.
        """
        if not isinstance(spec, RollSpec):
            return
        if self._bridge.prompt_roll(spec.to_dict()):
            return
        self._offline_seq -= 1
        self._history.add_roll(
            {
                "seq": self._offline_seq,
                "player_name": self._name_of_gm(),
                "die": 0,
                "kind": KIND_REQUEST,
                "label": spec.label,
                "dc": spec.dc,
                "spec": spec.to_dict(),
            }
        )

    # -- compact mode --------------------------------------------------------

    def release_roller(self) -> tuple[QWidget, QWidget]:
        """Lend the roller and the shared history to compact mode.

        The view answers the borrow, exactly as the sheet's Dice block does — these
        are the live widgets, still attached to the table, so the GM's hidden-roll
        switch and the ✕ on every card carry into the mini window unchanged.
        """
        return self._view.release_roller()

    def compact_anchor(self) -> QWidget:
        """What the shrink button floats over: the roller, history and all."""
        return self._view

    def suspend_windows(self, suspended: bool) -> None:
        """Stand this window's floated blocks down while it is compact (pinned ones stay)."""
        self._canvas.set_windows_suspended(suspended)

    def sync_dice_layout(self) -> None:
        """Re-read the roller's layout preference (see ``MainWindow.sync_dice_layout``)."""
        self._view.sync_dice_layout()

    def restore_roller(self) -> None:
        """Take the roller and the history back into the Rolls block."""
        self._view.restore_roller()

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
        # How a player's initiative reaches the board. No message of its own: a
        # roll already carries the spec that says what it was.
        self._bridge.rollAdded.connect(self._on_roll_added)
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
                card.pinsChanged.connect(lambda pid, refs: self._store_pins(_player_key(pid), refs))
                card.loadRequested.connect(self._roller.load_spec)
                card.rollRequested.connect(self._roller.roll_spec)
                card.pinPickerRequested.connect(self._open_player_pin_picker)
                card.sceneToggled.connect(lambda pid, on: self._set_in_scene(SCENE_PLAYER, pid, on))
                card.pins.set_pins(self._pins_for(_player_key(player_id), "player"))
                self._cards[player_id] = card
                self._cards_flow.addWidget(card)
            card.set_roster(entry)
            snapshot = self._snapshots.get(player_id)
            if snapshot:
                card.set_character(snapshot)

        for player_id in [p for p in self._cards if p not in seen]:
            self._drop_card(player_id)
        self._no_players.setVisible(not self._cards)
        self._sync_scene_players(seen)

    def _sync_scene_players(self, seated: set[str]) -> None:
        """Keep the board's player entries in step with the roster.

        A seat that has gone leaves the board whichever way the preference is set —
        there is nothing left to show. Whether a seat *arrives* on it is the
        preference: on, joining the table is enough; off, the GM puts them there by
        hand and their card grows an eye to do it with.

        Turning the preference off does **not** clear whoever is already on the
        board. "Put every player in the Scene automatically", unticked, says *from
        now on I decide* — not *throw out the fight in progress*. What it changes
        at once is that the eyes appear, so the GM can decide.

        The preference is re-read here rather than once at startup. This runs on
        every roster — a join, a leave, and every snapshot a player pushes — and
        :meth:`changeEvent` catches the one case that would otherwise wait for one:
        a GM who changes it in Settings and comes straight back to a quiet table.
        """
        self._scene_auto_players = storage.gm_scene_auto_players()
        before = list(self._scene)
        self._scene = [e for e in self._scene if e.kind != SCENE_PLAYER or e.source in seated]
        for player_id in [p for p in self._cards if p in seated]:
            if self._scene_auto_players and self._scene_entry_for(SCENE_PLAYER, player_id) is None:
                self._scene.append(_SceneEntry(ref=new_id(8), kind=SCENE_PLAYER, source=player_id))
        for entry in before:
            if entry.kind == SCENE_PLAYER and entry not in self._scene:
                self._player_initiative.pop(entry.source, None)
        self._push_scene()

    def _on_snapshot(self, player_id: str, character: object) -> None:
        """A player pushed their live sheet: remember it and restate their card."""
        if not isinstance(character, dict) or not character:
            return
        self._snapshots[player_id] = character
        card = self._cards.get(player_id)
        if card is not None:
            card.set_character(character)
        # Their conditions and their picture are on the board too, and a snapshot
        # is the only warning that either moved.
        if self._scene_entry_for(SCENE_PLAYER, player_id) is not None:
            self._push_scene()

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
        window = MainWindow(
            character=self._character_from_snapshot(snapshot),
            gm_view=True,
            pin_target=True,
        )
        card = self._cards.get(player_id)
        if card is not None:
            self._attach_pin_sheet(window, card)
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
        """What to call one seat.

        The **card** first: it is fed from the live roster, while ``_state`` is
        this window's own copy of the session — authoritative when hosting, and a
        snapshot from join time when the session lives on a server. Asking the
        state first put a stale name (or "That player") on the Scene for a remote
        GM, which is the one place a wrong name is read by the whole table.
        """
        card = self._cards.get(player_id)
        if card is not None and card.display_name():
            return card.display_name()
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
        self._forget_pins(_player_key(player_id))

    def _clear_cards(self) -> None:
        for player_id in list(self._cards):
            self._drop_card(player_id)
        self._snapshots.clear()
        self._no_players.setVisible(True)

    # -- pinned parameters ---------------------------------------------------

    def _pins_for(self, key: str, kind: str) -> list[PinRef]:
        """This card's strip, seeding it from the defaults the first time.

        Seeded and *stored* on first sight rather than left to fall through to the
        defaults on every read: once a card exists, its strip is the GM's, and a
        later change to ``gm_default_pins`` must not silently rearrange the cards
        already on the board.
        """
        if key not in self._pins:
            self._pins[key] = default_pins(kind, storage.gm_default_pins())
            self._persist_pins()
        return self._pins[key]

    def reseed_pins_from_defaults(self) -> None:
        """Throw every card's own strip away and seed them all from the defaults.

        The deliberate exception to the rule :meth:`_pins_for` keeps — a card's
        strip is the GM's once the card exists — so nothing calls this but the GM
        Mode settings page, on an explicit, confirmed ask.

        Cleared first, then re-seeded card by card: what that drops is the entries
        for cards *not* on the board (a player who left, an NPC file not loaded),
        which is the point. Those are gone from the settings file, so they seed
        from the new defaults the next time they are seen.
        """
        defaults = storage.gm_default_pins()
        self._pins.clear()
        cards = [(_player_key(pid), "player", card) for pid, card in self._cards.items()]
        cards += [
            (_npc_key(name), "npc", entry.card)
            for name, entry in self._npc_state.items()
            if entry.card is not None
        ]
        for card_key, kind, card in cards:
            refs = default_pins(kind, defaults)
            # set_pins is silent by design (a load, not an edit), so the strip is
            # stored explicitly — which is also what restates an open picker or sheet.
            card.pins.set_pins(refs)
            self._store_pins(card_key, refs)
        self._persist_pins()

    def _store_pins(self, card_key: str, refs: object) -> None:
        """A card's strip was edited — remember it, and restate it everywhere.

        Both a picker and an opened sheet offer to pin *and* to unpin, and each
        picks which from what it was last told is on the card. So a change made in
        any of the three has to reach the other two, or a menu ends up offering to
        pin something that is already there.
        """
        self._pins[card_key] = list(refs) if isinstance(refs, list) else []
        self._persist_pins()
        picker = self._pin_pickers.get(card_key)
        if picker is not None:
            picker.set_pinned(self._pins[card_key])
        for window in self._pin_sheets.get(card_key, ()):
            window.sheet.set_pinned(self._pins[card_key])

    def _persist_pins(self) -> None:
        """Write every card's strip, **including the empty ones**.

        An empty list is not the absence of an answer: it is a GM who took every
        chip off that card on purpose, and :meth:`_pins_for` seeds the defaults
        for a key it has never seen. Dropping the empty ones to keep the settings
        file tidy meant a cleared strip was back at four chips after a restart.
        """
        storage.update_settings(
            gm_pins={key: [ref.to_dict() for ref in refs] for key, refs in self._pins.items()}
        )

    def _open_player_pin_picker(self, player_id: str) -> None:
        card = self._cards.get(player_id)
        if card is None or card.character is None:
            return
        self._open_pin_picker(_player_key(player_id), card, card.character, card.display_name())

    def _open_npc_pin_picker(self, name: str) -> None:
        entry = self._npc_state.get(name)
        if entry is None or entry.card is None:
            return
        self._open_pin_picker(_npc_key(name), entry.card, entry.character, entry.summary.name)

    def _open_pin_picker(self, card_key: str, card, character: Character, title: str) -> None:
        """Open (or raise) the picker for one card, wired to that card's strip.

        Modeless, so the GM can pin several things in a row and watch the card
        fill in — which means the dialog outlives this call and has to be kept
        referenced, and dropped again when it closes.
        """
        existing = self._pin_pickers.get(card_key)
        if existing is not None:
            existing.show()
            existing.raise_()
            existing.activateWindow()
            return
        picker = PinPickerDialog(character, self._data, card.pins.pins, self, title=title)
        picker.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        picker.pinRequested.connect(card.pins.add_pin)
        picker.unpinRequested.connect(card.pins.remove_ref)
        picker.destroyed.connect(lambda *_: self._pin_pickers.pop(card_key, None))
        self._pin_pickers[card_key] = picker
        picker.show()

    def _attach_pin_sheet(self, window, card) -> None:
        """Wire a sheet opened from *card* to that card's strip, both directions.

        The sheet's rows pin and unpin onto the card; the card tells the sheet what
        is on it, now and on every later change, so a sheet left open beside the
        card never goes stale.
        """
        card_key = self._card_key(card)
        window.pinRequested.connect(card.pins.add_pin)
        window.unpinRequested.connect(card.pins.remove_ref)
        window.sheet.set_pinned(card.pins.pins)
        sheets = self._pin_sheets.setdefault(card_key, [])
        sheets.append(window)
        window.destroyed.connect(lambda *_: self._drop_pin_sheet(card_key, window))

    def _drop_pin_sheet(self, card_key: str, window) -> None:
        sheets = self._pin_sheets.get(card_key)
        if sheets is None:
            return
        self._pin_sheets[card_key] = [w for w in sheets if w is not window]

    @staticmethod
    def _card_key(card) -> str:
        """A card's pin-store key, from whichever identity that card carries."""
        name_key = getattr(card, "name_key", "")
        return _npc_key(name_key) if name_key else _player_key(card.player_id)

    def _forget_pins(self, card_key: str) -> None:
        """Drop a card's strip for good — the seat or the NPC is gone."""
        picker = self._pin_pickers.pop(card_key, None)
        if picker is not None:
            picker.close()
        self._pin_sheets.pop(card_key, None)
        if self._pins.pop(card_key, None) is not None:
            self._persist_pins()

    # -- NPCs ---------------------------------------------------------------

    # -- the scene ---------------------------------------------------------
    #
    # The GM authors the board and the server only stores and rebroadcasts it, so
    # everything below is about deriving one payload correctly and often. It is
    # derived rather than kept because every field on it lives somewhere else and
    # is live there: an NPC's conditions are on its model, a player's are in their
    # last snapshot, and a copy of either would be right exactly once.

    def _scene_entry(self, ref: str) -> _SceneEntry | None:
        """The entry answering to *ref*, or ``None``."""
        return next((entry for entry in self._scene if entry.ref == ref), None)

    def _scene_entry_for(self, kind: str, source: str) -> _SceneEntry | None:
        """The entry standing for one creature, or ``None`` if it is not on the board."""
        return next((e for e in self._scene if e.kind == kind and e.source == source), None)

    def _set_in_scene(self, kind: str, source: str, on: bool) -> None:
        """Put one creature on the board, or take it off. The one way in.

        Adding appends: a creature nobody has rolled for sorts into the un-rolled
        zone, and the end of it is the only honest place for something no one has
        said anything about yet.
        """
        entry = self._scene_entry_for(kind, source)
        if on and entry is None:
            self._scene.append(_SceneEntry(ref=new_id(8), kind=kind, source=source))
        elif not on and entry is not None:
            self._scene.remove(entry)
            if kind == SCENE_PLAYER:
                self._player_initiative.pop(source, None)
        else:
            return
        self._push_scene()

    def _remove_from_scene(self, ref: str) -> None:
        """Take one entry off the board, by ref (a scene card's right-click)."""
        entry = self._scene_entry(ref)
        if entry is not None:
            self._set_in_scene(entry.kind, entry.source, False)

    def _drop_on_scene(self, ref: str, index: int) -> None:
        """A card was dropped at *index* — a move if we know the ref, else an add.

        A drop always lands in the **manual** zone, so the dragged entry loses any
        initiative it had; and dropping in front of a *rolled* entry clears that
        one too. Both are the rule :meth:`_reorder_npc` already spells out, for the
        reason it gives: putting an un-rolled creature before a rolled one is the
        GM saying it acts first, which is impossible while the other keeps a number
        to sort by. One of the two has to go, and taking both into the manual zone
        is the answer that does what was asked.
        """
        entry = self._scene_entry(ref) or self._adopt_dropped(ref)
        if entry is None:
            return
        order = self._scene_board.ordered_refs()
        neighbour_ref = order[index] if 0 <= index < len(order) else ""
        neighbour = self._scene_entry(neighbour_ref) if neighbour_ref != entry.ref else None
        if (
            self._entry_initiative(entry) is None
            and neighbour is not None
            and self._entry_initiative(neighbour) is not None
        ):
            self._clear_entry_initiative(neighbour)
        self._clear_entry_initiative(entry)

        # Rebuilt against the *rendered* order, which is what the index was
        # measured in; its un-rolled tail is the arrangement being edited.
        rest = [r for r in self._scene_board.ordered_refs() if r != entry.ref]
        rest.insert(max(0, min(index, len(rest))), entry.ref)
        by_ref = {e.ref: e for e in self._scene}
        self._scene = [by_ref[r] for r in rest if r in by_ref]
        self._push_scene()

    def _adopt_dropped(self, ref: str) -> _SceneEntry | None:
        """Make an entry for a card dragged in from one of the rosters.

        Such a card carries its *own* identity (``"npc:<file>"`` /
        ``"player:<id>"``) rather than a scene ref, which is how one drop handler
        serves both gestures: a ref the board already knows is a move, and this is
        everything else. A ref naming a creature this window has never heard of is
        refused rather than invented — it did not come from either roster.
        """
        kind, _, source = ref.partition(":")
        known = (kind == SCENE_NPC and source in self._npc_state) or (
            kind == SCENE_PLAYER and source in self._cards
        )
        if not known:
            return None
        existing = self._scene_entry_for(kind, source)
        if existing is not None:
            return existing
        entry = _SceneEntry(ref=new_id(8), kind=kind, source=source)
        self._scene.append(entry)
        return entry

    def _entry_initiative(self, entry: _SceneEntry) -> int | None:
        """One entry's rolled initiative, read from wherever that number lives."""
        if entry.kind == SCENE_NPC:
            npc = self._npc_state.get(entry.source)
            return None if npc is None else npc.initiative
        return self._player_initiative.get(entry.source)

    def _clear_entry_initiative(self, entry: _SceneEntry) -> None:
        """Put one entry back in the un-rolled zone, wherever its number lives."""
        if entry.kind == SCENE_NPC:
            npc = self._npc_state.get(entry.source)
            if npc is not None and npc.initiative is not None:
                npc.initiative = None
        else:
            self._player_initiative.pop(entry.source, None)

    def _clear_scene_initiative(self, ref: str) -> None:
        """A scene card's right-click: take this entry back out of the rolled zone.

        Routed through :meth:`_refresh_npcs` for an NPC rather than pushing from
        here, because the cast's own grid sorts by that same number: clearing it on
        the board alone would leave the two boards disagreeing about one value.
        """
        entry = self._scene_entry(ref)
        if entry is None:
            return
        self._clear_entry_initiative(entry)
        if entry.kind == SCENE_NPC:
            self._refresh_npcs()
        else:
            self._push_scene()

    # -- publishing --------------------------------------------------------

    def _scene_payload(self) -> list[dict]:
        """The board as it goes on the wire, derived fresh from the live models.

        A **name, an initiative and the conditions**, and nothing else. There is no
        filtering step here that could be got wrong later: the other fields are
        simply never read, and what is read is checked again by ``sanitize_scene``
        on the way through the server.
        """
        payload: list[dict] = []
        for entry in self._scene:
            if entry.kind == SCENE_NPC:
                npc = self._npc_state.get(entry.source)
                if npc is None:
                    continue
                item: dict = {"ref": entry.ref, "name": npc.summary.name}
                conditions = [c.to_dict() for c in npc.character.conditions]
            else:
                if entry.source not in self._cards:
                    continue
                item = {
                    "ref": entry.ref,
                    "name": self._player_name(entry.source),
                    "player_id": entry.source,
                }
                raw = (self._snapshots.get(entry.source) or {}).get("conditions")
                conditions = (
                    [c for c in raw if isinstance(c, dict)] if isinstance(raw, list) else []
                )
            initiative = self._entry_initiative(entry)
            if initiative is not None:
                item["initiative"] = initiative
            if conditions:
                # ``provenance`` is a detail of the sender's own tracker — which
                # umbrella bundled this condition — and means nothing on a card.
                item["conditions"] = [
                    {k: v for k, v in c.items() if k != "provenance"} for c in conditions
                ]
            payload.append(item)
        return payload

    def _push_scene(self) -> None:
        """Publish the board, restate the GM's own copy, and send any new pictures.

        Called from every mutation *and* from everywhere a creature's visible state
        can change under it — a condition, a damage step, a snapshot — since those
        are the changes a player is watching the board for.
        """
        entries = self._scene_payload()
        self._scene_board.set_manual_order([e.ref for e in self._scene])
        self._scene_board.set_scene(entries)
        self._refresh_scene_eyes()
        if self._bridge.in_session:
            self._bridge.set_scene(entries, {e.ref: e.wire_source() for e in self._scene})
        self._push_scene_portraits()

    def _push_scene_portraits(self) -> None:
        """Send a thumbnail for anything on the board that has not had one sent.

        Keyed by the encoded payload rather than by a "sent" flag, so a player who
        changes their portrait mid-session gets a new one — and a scene re-pushed
        thirty times through a fight sends none of them again.
        """
        live = {entry.ref for entry in self._scene}
        for ref in [r for r in self._scene_portraits if r not in live]:
            del self._scene_portraits[ref]
        for entry in self._scene:
            portrait = self._scene_portrait_for(entry)
            if self._scene_portraits.get(entry.ref) == portrait:
                continue
            self._scene_portraits[entry.ref] = portrait
            # The GM's own board takes it directly; the wire only when hosting.
            self._scene_board.set_portrait(entry.ref, portrait)
            if self._bridge.in_session:
                self._bridge.set_scene_portrait(entry.ref, portrait)

    def _scene_portrait_for(self, entry: _SceneEntry) -> str:
        """One entry's scene-sized thumbnail, or ``""`` when it has no picture.

        The two kinds come from different places and neither can be passed straight
        on: an NPC's picture is a file this app can read, and a player's has already
        been shrunk once for their *sheet* portrait — still big enough that a
        board's worth would be stored per session and replayed to every joiner.
        """
        if entry.kind == SCENE_NPC:
            npc = self._npc_state.get(entry.source)
            return "" if npc is None else encode_scene_portrait(npc.summary.image_path)
        return shrink_portrait((self._snapshots.get(entry.source) or {}).get("portrait"))

    def _refresh_scene_eyes(self) -> None:
        """Restate every card's eye from the board.

        Silently, like :meth:`NPCCard.set_collapsed`: a card being told what the
        window already decided must not come back as a fresh request.
        """
        on_board = {(e.kind, e.source) for e in self._scene}
        for name, npc in self._npc_state.items():
            if npc.card is not None:
                npc.card.set_in_scene((SCENE_NPC, name) in on_board)
        for player_id, card in self._cards.items():
            card.set_scene_controls(not self._scene_auto_players)
            card.set_in_scene((SCENE_PLAYER, player_id) in on_board)

    # -- initiative --------------------------------------------------------

    def _roll_scene_initiative(self) -> None:
        """Roll for every NPC on the board, and ask every player for theirs.

        The NPC rolls stay **local**, as the initiative badge's own docstring
        insists: a dozen mook rolls in the shared log would bury the one line the
        table is waiting for, and what the players need is the *result*, which is
        the board. The players are asked through the request the log already has —
        each client's ``localize_spec`` fills in their own modifier, so the number
        that comes back is theirs rather than one this window guessed at.
        """
        rolled = 0
        for entry in list(self._scene):
            if entry.kind != SCENE_NPC:
                continue
            npc = self._npc_state.get(entry.source)
            if npc is None:
                continue
            npc.initiative = roll_d20() + initiative_modifier(npc.character, self._data)
            rolled += 1
        if rolled:
            # Rebuilds the cast in its new order and restates every badge; the
            # scene is pushed from the state that rebuild settles on.
            self._refresh_npcs()
        else:
            self._push_scene()
        self._ask_players_for_initiative()

    def _ask_players_for_initiative(self) -> None:
        """Put one "roll initiative" request in the shared log, for everyone.

        One request rather than one per seat: the log's request card is already
        addressed to the table, and a player who is not in this fight can simply
        not click it — a better answer than making the GM say who is.
        """
        spec = self._initiative_request_spec()
        if spec is not None and self._bridge.in_session:
            self._request_roll(spec)

    def _initiative_request_spec(self) -> RollSpec | None:
        """The character-free Initiative template, from the list requests use.

        Taken from :func:`requested_roll_choices` rather than built here so it is
        the same object a GM asking by hand would send: one shape of request,
        however it was asked for.
        """
        for group in requested_roll_choices(self._data):
            for value in group.values:
                if value.spec is not None and value.spec.kind == KIND_INITIATIVE:
                    return value.spec
        return None

    def _on_roll_added(self, roll: object) -> None:
        """Watch the shared log for a player's initiative and put it on the board.

        No new message for this: a player's roll already reaches every seat
        carrying the spec that describes it, so the only thing needed is to notice
        the ones that say ``initiative``. That catches both routes at once — a
        player answering the request card, and a player rolling Initiative off
        their own sheet — because the two produce the same record.
        """
        if not isinstance(roll, dict) or roll.get("kind", KIND_ROLL) != KIND_ROLL:
            return
        spec = roll.get("spec")
        if not isinstance(spec, dict) or spec.get("kind") != KIND_INITIATIVE:
            return
        entry = self._scene_entry_for(SCENE_PLAYER, str(roll.get("player_id", "")))
        if entry is None:
            return
        # ``RollRecord.to_dict`` writes the parts, not the sum.
        self._player_initiative[entry.source] = (
            int(roll.get("die", 0)) + int(roll.get("bonus", 0)) - int(roll.get("penalty", 0))
        )
        self._push_scene()

    def _new_scene(self) -> None:
        """Clear the board and every initiative on it.

        Confirmed only when there is something to lose. The players stay if the GM
        has them joining automatically: clearing them would be undone by the next
        roster anyway, and a button that visibly does not do what it says is worse
        than one that does less.
        """
        if self._scene and not self._confirm_new_scene():
            return
        for entry in list(self._scene):
            self._clear_entry_initiative(entry)
        self._player_initiative.clear()
        self._scene = (
            [e for e in self._scene if e.kind == SCENE_PLAYER] if self._scene_auto_players else []
        )
        # Through the cast's own refresh, since clearing the NPCs' initiative has
        # just changed the order their grid sorts in; it pushes the scene after.
        self._refresh_npcs()

    def _confirm_new_scene(self) -> bool:
        kept = " The players stay on it." if self._scene_auto_players else ""
        answer = QMessageBox.question(
            self,
            "New scene",
            f"Clear the Scene and every initiative on it?{kept}",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return answer == QMessageBox.StandardButton.Yes

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
                collapsed=self._collapsed_cards.get(_npc_key(name), False),
            )

        # Keep the manual order in step with the cast: drop the departed, append
        # newcomers at the end of the un-rolled zone.
        self._manual_order = [n for n in self._manual_order if n in self._npc_state]
        self._manual_order += [n for n in self._npc_state if n not in self._manual_order]

        for name in self._ordered_npcs():
            entry = self._npc_state[name]
            card = NPCCard(
                entry.character,
                entry.summary,
                self._data,
                initiative=entry.initiative,
                collapsed=entry.collapsed,
            )
            card.openRequested.connect(self._open_npc)
            card.removeRequested.connect(self._remove_npc)
            card.deleteRequested.connect(self._delete_npc)
            card.applyConditionRequested.connect(self._apply_npc_condition)
            card.removeConditionRequested.connect(self._remove_npc_condition)
            card.initiativeRolled.connect(self._on_npc_initiative)
            card.initiativeCleared.connect(self._on_npc_initiative_cleared)
            card.sceneToggled.connect(lambda n, on: self._set_in_scene(SCENE_NPC, n, on))
            card.copyRequested.connect(self._copy_npc)
            card.pinsChanged.connect(lambda n, refs: self._store_pins(_npc_key(n), refs))
            card.loadRequested.connect(self._roller.load_spec)
            card.rollRequested.connect(self._roller.roll_spec)
            card.pinPickerRequested.connect(self._open_npc_pin_picker)
            card.collapsedChanged.connect(self._set_npc_collapsed)
            card.damageRequested.connect(self._apply_npc_damage)
            card.pins.set_pins(self._pins_for(_npc_key(name), "npc"))
            entry.card = card
            self._npc_flow.addWidget(card)
        self._no_npcs.setVisible(not self._npc_state)
        self._refresh_collapse_all()
        # An NPC that has left the cast has left the board with it, and the fresh
        # cards need their eyes told. Every refresh ends here, which makes this the
        # one push point for anything routed through one — a rolled initiative, a
        # condition, a damage step.
        self._scene = [e for e in self._scene if e.kind != SCENE_NPC or e.source in self._npc_state]
        self._push_scene()

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

    def _on_npc_initiative_cleared(self, name: str) -> None:
        """Take an NPC back out of the order, into the un-rolled zone.

        The twin of the above, and it re-sorts the same way: an NPC with no
        initiative sorts below every NPC that has one, in the manual order the
        drags have built up.
        """
        entry = self._npc_state.get(name)
        if entry is None:
            return
        entry.initiative = None
        self._refresh_npcs()

    def _drop_on_npcs(self, ref: str, index: int) -> None:
        """A card was dropped on the NPC grid.

        Only an NPC's own card means anything here: a *player* dragged onto the
        cast is not a thing that can be done, and refusing it quietly is better
        than inventing an interpretation. An NPC's is the reorder this grid has
        always had, unchanged below.
        """
        kind, _, source = ref.partition(":")
        if kind == SCENE_NPC and source in self._npc_state:
            self._reorder_npc(source, index)

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
        # The duplicate is the same creature under a new name, so it starts with
        # the same strip rather than back at the defaults — and, for the same
        # reason, shrunk if its original was. Copying a mook is how a GM makes the
        # fourth guard, and the fourth guard wants the third guard's card.
        pins = self._pins.get(_npc_key(name))
        if pins:
            self._pins[_npc_key(path.name)] = list(pins)
            self._persist_pins()
        if self._collapsed_cards.get(_npc_key(name)):
            self._collapsed_cards[_npc_key(path.name)] = True
            storage.set_gm_collapsed_cards(self._collapsed_cards)
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
        window = NPCWindow(character=library.load_character(path), path=path, pin_target=True)
        if entry.card is not None:
            self._attach_pin_sheet(window, entry.card)
        self._track_npc_window(window)

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
        # Only a *deletion* forgets the card's own state. Taking an NPC out of the
        # session leaves its file, and a GM who adds it back next week should find
        # their pins — and their shrunk card — where they left them.
        self._forget_pins(_npc_key(name))
        if self._collapsed_cards.pop(_npc_key(name), None) is not None:
            storage.set_gm_collapsed_cards(self._collapsed_cards)
        self._remove_npc(name)

    # -- collapsing a card --------------------------------------------------

    def _toggle_collapse_all(self) -> None:
        """Shrink every NPC card, or open every one.

        Which way round comes from the board: anything still open means "collapse",
        and only once they are all shut does the button offer to expand. Each card
        is told **silently** — :meth:`NPCCard.set_collapsed` does not echo — and the
        whole decision is written once at the end rather than per card.
        """
        if not self._npc_state:
            return
        collapsed = not all(entry.collapsed for entry in self._npc_state.values())
        for name, entry in self._npc_state.items():
            entry.collapsed = collapsed
            self._collapsed_cards[_npc_key(name)] = collapsed
            if entry.card is not None:
                entry.card.set_collapsed(collapsed)
        storage.set_gm_collapsed_cards(self._collapsed_cards)
        self._refresh_collapse_all()

    def _refresh_collapse_all(self) -> None:
        """Restate the button from the board — a caption that lies is worse than none."""
        entries = list(self._npc_state.values())
        self._collapse_all_button.setEnabled(bool(entries))
        all_collapsed = bool(entries) and all(entry.collapsed for entry in entries)
        self._collapse_all_button.setText(EXPAND_ALL if all_collapsed else COLLAPSE_ALL)

    def _set_npc_collapsed(self, name: str, collapsed: bool) -> None:
        """Remember that the GM shrank (or reopened) a card.

        The card has already changed shape — it emitted this — so there is nothing
        to redraw. What is recorded is the *judgement*: this creature is one of the
        ones being tracked rather than read, and it should still be next week.
        """
        entry = self._npc_state.get(name)
        if entry is None:
            return
        entry.collapsed = collapsed
        self._collapsed_cards[_npc_key(name)] = collapsed
        storage.set_gm_collapsed_cards(self._collapsed_cards)
        self._refresh_collapse_all()

    # -- NPC conditions -----------------------------------------------------

    def _apply_npc_condition(self, name: str, condition_id: str, parameter: object) -> None:
        """Apply a condition straight onto the NPC's local model, and persist it."""
        entry = self._npc_state.get(name)
        if entry is None:
            return
        subject = str(parameter) if parameter else None
        apply_condition(entry.character, condition_id, self._data, parameter=subject)
        self._after_npc_condition_change(entry, [(condition_id, subject)], applying=True)

    def _remove_npc_condition(self, name: str, condition_id: str, parameter: object) -> None:
        """Take one condition off the NPC's model again."""
        entry = self._npc_state.get(name)
        if entry is None:
            return
        subject = str(parameter) if parameter else None
        applied = matching_condition(entry.character, condition_id, subject)
        if applied is not None:
            decrement_condition(entry.character, applied)
        self._after_npc_condition_change(entry, [(condition_id, subject)], applying=False)

    def _apply_npc_damage(self, name: str, step_index: int) -> None:
        """Walk a rung of the damage ladder onto an NPC.

        The GM clicked a degree of failure; what that *means* — which conditions,
        and whether an already-Dazed target is Stunned instead — is
        :mod:`mm_companion.core.rules.damage`'s answer, resolved once here against
        this creature's current state. The ids it decided on are then what gets
        replayed onto an open sheet, so the two copies of the character cannot
        disagree about an escalation each would otherwise resolve for itself.
        """
        entry = self._npc_state.get(name)
        if entry is None:
            return
        steps = damage_steps(self._data)
        step = next((s for s in steps if s.index == step_index), None)
        if step is None:
            return
        # Read *before* the apply: the summary resolves escalation against the
        # creature's current state, so asking afterwards would describe what the
        # next click would do rather than what this one just did.
        landed = damage_step_summary(entry.character, step, self._data)
        applied = apply_damage_step(entry.character, step, self._data)
        self._after_npc_condition_change(
            entry, [(condition_id, None) for condition_id in applied], applying=True
        )
        self._show_notice(f"“{entry.summary.name}” — {landed}", theme.color("tint.worse"))

    def _after_npc_condition_change(
        self,
        entry: _NpcEntry,
        changes: list[tuple[str, str | None]],
        *,
        applying: bool,
    ) -> None:
        """Restate the NPC's card and persist one or more condition changes.

        Unlike a player, an NPC is local — so the change is applied to the model
        here rather than sent over the wire. If a sheet for this NPC is open, route
        the same changes through its conditions block so the open sheet stays in
        sync and owns its own save; otherwise write the model to its file now —
        **once**, however many conditions a damage rung brought with it.
        """
        window = self._window_for(entry.path)
        section = getattr(window.sheet, "conditions", None) if window is not None else None
        if section is not None:
            # Absorbed rather than recorded, and for a sharper reason than the
            # player's: the card's entry and the open sheet are two different
            # Character objects, kept in step by replaying the settled ids. An undo
            # on the sheet would roll one back and not the other — exactly the
            # disagreement the replay exists to prevent.
            with absorbing(window.sheet):
                for condition_id, parameter in changes:
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
            # And the pinned numbers, which a condition moves: a Vulnerable NPC's
            # Defence chip must read what the GM should actually use. The chips
            # resolve from the model, so this is only a matter of asking them to.
            # (A player's card gets this for free — a condition there comes back as
            # a fresh snapshot, which restates the whole card.)
            entry.card.pins.refresh()
        # And the table, if this creature is on the board. Conditions are half of
        # what a scene card shows, and this method is the *only* path that changes
        # them without going through _refresh_npcs — the GM's "+", the right-click
        # that sheds one, and every rung of the damage ladder all land here. Without
        # this a GM could daze a creature and watch their own card update while the
        # players went on looking at an undazed one.
        if self._scene_entry_for(SCENE_NPC, entry.path.name) is not None:
            self._push_scene()

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

    def _open_settings(self) -> None:
        """Open the Settings window on the GM page (Settings ▸ Preferences).

        The same window the character sheet opens, landing on the page this window
        is the one that cares about. Imported here rather than at module scope, the
        way the sheet's opener does it, and kept referenced so it is not collected
        the moment this method returns.
        """
        from mm_companion.ui.settings import GMPage, SettingsWindow

        window = SettingsWindow(page=GMPage.title)
        self._settings_window = window
        window.show()

    # -- lifecycle ---------------------------------------------------------

    def changeEvent(self, event) -> None:  # noqa: N802 (Qt override)
        """Coming back to this window is when a Settings change should land.

        The Scene's one preference is otherwise re-read on the roster, which is
        frequent in a live session and never at a quiet table — so a GM who
        unticked it and came straight back would find the player cards still
        without their eyes. Guarded on the value actually moving, so an ordinary
        alt-tab costs a settings read and nothing else.
        """
        if event.type() == QEvent.Type.ActivationChange and self.isActiveWindow():
            self._reload_scene_preference()
        super().changeEvent(event)

    def _reload_scene_preference(self) -> None:
        """Re-read ``gm_scene_auto_players`` and apply it if it has moved."""
        wanted = storage.gm_scene_auto_players()
        if wanted == self._scene_auto_players:
            return
        self._scene_auto_players = wanted
        self._sync_scene_players({pid for pid, card in self._cards.items() if card is not None})

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

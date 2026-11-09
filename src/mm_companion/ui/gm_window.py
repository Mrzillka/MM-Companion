"""GM Mode: the window that hosts the table's online session.

The GM's control panel: a live card per connected player, the session's NPCs, the
Scene the players see, and the shared roll history beside the GM's roller.

:class:`GMWindow` is the window's frame (its blocks, menus, layout, compact mode and
lifecycle); everything else is a mixin under :mod:`mm_companion.ui.gm`, one per thing
the window looks after:

- :mod:`~mm_companion.ui.gm.hosting`: the session, the bridge and the relay fallback
- :mod:`~mm_companion.ui.gm.players`: the player cards
- :mod:`~mm_companion.ui.gm.scene`: the Scene and initiative
- :mod:`~mm_companion.ui.gm.npc_board`, :mod:`~mm_companion.ui.gm.npc_groups`,
  :mod:`~mm_companion.ui.gm.npc_conditions`: the NPCs
- :mod:`~mm_companion.ui.gm.rolls`: rolling and answering the history, minion rules
- :mod:`~mm_companion.ui.gm.actions`: what Impress, area and aimed attacks call back
- :mod:`~mm_companion.ui.gm.pins`: the parameters pinned to each card

The mixins share the window's state through ``self``; :mod:`~mm_companion.ui.gm.model`
holds the entries they keep.
"""

from __future__ import annotations

import json

from PySide6.QtCore import QByteArray, QEvent, Qt, QTimer
from PySide6.QtGui import QCloseEvent, QShowEvent
from PySide6.QtWidgets import (
    QApplication,
    QGroupBox,
    QMainWindow,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from mm_companion.core import npc_groups, storage
from mm_companion.core.data_loader import GameData, load_game_data
from mm_companion.core.npc_groups import NPCGroup
from mm_companion.core.rules import (
    PinRef,
    requested_roll_choices,
)
from mm_companion.core.session.model import (
    SessionState,
    new_session,
)
from mm_companion.core.session.net import DEFAULT_PORT
from mm_companion.ui import loading, theme
from mm_companion.ui.area_gm import AreaController
from mm_companion.ui.attack_gm import AttackController
from mm_companion.ui.block_canvas import BlockCanvas
from mm_companion.ui.block_sizes import RecommendedSize, load_block_sizes
from mm_companion.ui.blocks.gm_registry import (
    GMBlockDescriptor,
    gm_block_descriptors,
    gm_default_pin_lines,
    gm_default_rows,
    register_gm_block,
)
from mm_companion.ui.card_drop import CardDropFlow
from mm_companion.ui.compact import CompactController
from mm_companion.ui.connection_indicator import install_connection_indicator
from mm_companion.ui.flow_layout import FlowContainer, FlowLayout
from mm_companion.ui.gm.actions import GMActionsMixin
from mm_companion.ui.gm.combat import GMCombatMixin
from mm_companion.ui.gm.hosting import GMHostingMixin
from mm_companion.ui.gm.model import (
    BIND_ADDRESS,
    COLLAPSE_ALL,
    NO_NPCS,
    NO_PLAYERS,
    SCENE_NPC,
    SCENE_PLAYER,
    _Answering,
    _is_npc_ref,
    _is_player_ref,
    _load_pins,
    _NpcEntry,
    _Recovering,
    _SceneEntry,
    _wrapped,
)
from mm_companion.ui.gm.npc_board import GMNpcBoardMixin
from mm_companion.ui.gm.npc_conditions import GMNpcConditionsMixin
from mm_companion.ui.gm.npc_groups import GMNpcGroupsMixin
from mm_companion.ui.gm.pins import GMPinsMixin
from mm_companion.ui.gm.players import GMPlayersMixin
from mm_companion.ui.gm.rolls import GMRollsMixin
from mm_companion.ui.gm.scene import GMSceneMixin
from mm_companion.ui.impress_gm import ImpressController
from mm_companion.ui.npc_group import NPCGroupCard
from mm_companion.ui.npc_window import NPCWindow
from mm_companion.ui.pin_picker import PinPickerDialog
from mm_companion.ui.pinned_panel import PinnedBoard, attach_strip_menu
from mm_companion.ui.player_card import PlayerCard
from mm_companion.ui.roll_history import RollHistoryPanel
from mm_companion.ui.roller.view import DiceRollerView
from mm_companion.ui.scene_board import NO_SCENE_GM, SceneBoard
from mm_companion.ui.sections.titled_section import strip_groupbox_caption
from mm_companion.ui.session_bridge import SessionBridge, last_session, set_active_session
from mm_companion.ui.session_dialogs import HostOptions
from mm_companion.ui.targeted_gm import TargetedController
from mm_companion.ui.team_call import TeamCallController
from mm_companion.ui.toasts import ToastCard, ToastStack
from mm_companion.ui.widgets import (
    ConfirmButton,
)

__all__ = ["SCENE_NPC", "SCENE_PLAYER", "GMWindow", "register_base_gm_blocks"]


class GMWindow(
    GMRollsMixin,
    GMCombatMixin,
    GMActionsMixin,
    GMHostingMixin,
    GMPlayersMixin,
    GMPinsMixin,
    GMSceneMixin,
    GMNpcBoardMixin,
    GMNpcGroupsMixin,
    GMNpcConditionsMixin,
    QMainWindow,
):
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
        # An NPC sheet built ahead of time and never shown, waiting to be handed the
        # next creature a card opens (see _prime_spare_npc). One timer, so however
        # many things ask for a new spare in a burst, one gets built.
        self._spare_npc: NPCWindow | None = None
        # Set once a spare turns out unable to take a creature in place (a mod block
        # with no way to restate itself — see NPCWindow.can_load). Mods only change
        # with a restart, so nothing un-sets it.
        self._spare_unsupported = False
        self._spare_timer = QTimer(self)
        self._spare_timer.setSingleShot(True)
        self._spare_timer.timeout.connect(self._prime_spare_npc)
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
        #: The GM's NPC groups (squads), by file name — see core.npc_groups.
        self._npc_groups: list[NPCGroup] = npc_groups.groups_from_raw(storage.gm_npc_groups())
        #: The group cards on the board now, by group id.
        self._group_cards: dict[str, NPCGroupCard] = {}
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
        # Each entry's *encoded* picture and the input it was made from, by ref — so
        # an unchanged picture is never re-encoded. See :meth:`_scene_portrait_for`.
        self._portrait_encodings: dict[str, tuple[str, str]] = {}
        # Everything the last push said, so a push that would say the same thing again
        # can do nothing at all. See :meth:`_push_scene` for what is in it and why.
        self._pushed_scene: object = None
        # Whether joining puts a player on the board by itself. Read once here
        # through its accessor; the Settings page's change reaches an open window
        # on the next roster.
        self._scene_auto_players = storage.gm_scene_auto_players()
        # Which seats the last roster said were connected, so a seat is put on the
        # board as it *comes* online rather than on every roster (see
        # _sync_scene_players).
        self._connected_seats: set[str] = set()
        # One relay attempt per hosting run: the fallback republishes, and a
        # second attempt off that would loop.
        self._relay_attempted = False
        # Ids for rolls made before hosting: negative so they never collide with a
        # session's positive seqs, and still removable from the local view.
        self._offline_seq = 0
        # The history roll an NPC is answering right now (see _roll_as), and which
        # creatures have answered each card, by its seq — so the menu can tick them.
        self._answering: _Answering | None = None
        self._answered: dict[int, set[str]] = {}
        # An NPC's recovery check, rolled and waiting for its result (see
        # _roll_npc_recovery).
        self._recovering: _Recovering | None = None
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

        # The same BlockCanvas the sheet uses, built from the registry rather than a
        # literal list so a mod can add a GM block the way it adds one to the sheet.
        # The base panels register at this module's import (register_base_gm_blocks);
        # their order, sizes and default places travel on their descriptors.
        descriptors = gm_block_descriptors()
        # A step per block, the canvas, and the board's first fill — what a loading
        # screen over this build moves by (ui/loading).
        loading.expect(len(descriptors) + 2)
        panels = []
        for d in descriptors:
            panels.append((d.key, d.title, d.factory(self)))
            loading.step()
        for _key, _title, box in panels:
            strip_groupbox_caption(box)  # the block's title bar carries the name
        sizes = {d.key: d.size for d in descriptors}
        default_rows = gm_default_rows()
        self._canvas = BlockCanvas(
            panels, sizes, default_rows, default_pinned=gm_default_pin_lines()
        )
        loading.step()

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
        # The hosting status and the reachability advice, the "cannot reach the
        # table" card, then a transient notice line — floating over the board's
        # bottom-right corner rather than laid out under it, so a message coming or
        # going never moves a block (see ui/toasts.py).
        self._notices = ToastStack(self._full)
        self._notices.add_card(self._build_status_strip())
        self._notices.add_card(self._build_trouble())
        self._notices.add_card(self._build_notice())

        # The same compact mode a player's sheet has, over the same DiceRollerView.
        # The mini window's caption follows ``windowTitleChanged``, so it picks up
        # the session name on its own.
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
        # Runs every Impress this window resolves — a player's, or an NPC's from the
        # roller's Actions popup (see mm_companion.ui.impress_gm).
        self._impress = ImpressController(self._data, self._bridge, self, self)
        # And every area attack: a player's declaration lands in the history with a
        # Resolve button, and an NPC's starts from its sheet (mm_companion.ui.area_gm).
        self._area = AreaController(self._data, self._bridge, self, self)
        # And every aimed attack: a player's lands with Accept / Decline, the GM's own
        # (a card's tile, an NPC sheet) runs straight through (mm_companion.ui.attack_gm).
        self._attack = AttackController(self._data, self._bridge, self, self)
        # And every team attack called to roll: anyone joins with their next attack,
        # and the GM resolves it off its card (mm_companion.ui.team_call).
        self._team_calls = TeamCallController(self._data, self._bridge, self, self._attack, self)
        # And every Aid, Feint and Trick: a player's lands with Accept / Decline, the GM's
        # own runs straight through (mm_companion.ui.targeted_gm).
        self._targeted = TargetedController(self._data, self._bridge, self, self)
        self._refresh_idle_status()
        self._refresh_trouble()
        self._refresh_rolls()
        self._refresh_npcs()
        # After the cast, so a creature whose file went away is simply not found.
        self._restore_scene()
        loading.step()

        # Hosting was chosen in the launch dialog, so start it now — the window
        # comes up already reachable, with the join code a menu click away.
        if autohost:
            self.start_hosting(self._host_options)

    def _update_min_width(self) -> None:
        """Hold the board open by a scrollbar's width and very little else.

        This used to track the widest docked row, which is what made the GM window
        refuse to be narrower than the blocks in it. Its blocks reflow and then
        scroll inside themselves like every other, so there is no width the board
        has to refuse — see :meth:`BlockCanvas.content_minimum_width`.
        """
        bar = self._scroll.verticalScrollBar()
        extra = bar.sizeHint().width() if bar is not None else 0
        self._scroll.setMinimumWidth(self._canvas.content_minimum_width() + extra + 2)

    def _build_menu(self) -> None:
        """Session (copy the join code, reconnect), Settings, View (show/hide blocks)."""
        session_menu = self.menuBar().addMenu("&Session")
        self._copy_code_action = session_menu.addAction("Copy join code")
        self._copy_code_action.setEnabled(False)
        self._copy_code_action.triggered.connect(self._copy_code)
        # The affordance that always works: a notice can be dismissed, the corner
        # read-out cannot be clicked, and the failure can arrive with no cue at all
        # (a UPnP mapping the router quietly dropped raises nothing). So there is
        # always one place to go, reachable from the keyboard.
        self._reconnect_action = session_menu.addAction("Reconnect")
        self._reconnect_action.setEnabled(False)
        self._reconnect_action.triggered.connect(self.reconnect_session)

        settings_menu = self.menuBar().addMenu("&Settings")
        settings_menu.addAction("Preferences...").triggered.connect(self._open_settings)
        # The open session's own rules (the minions'), which belong to it alone.
        settings_menu.addAction("Session settings...").triggered.connect(self.open_session_settings)

        view_menu = self.menuBar().addMenu("&View")
        self._block_actions: dict[str, object] = {}
        for key in self._canvas.block_keys():
            action = view_menu.addAction(self._canvas.block_frame(key).title)
            action.setCheckable(True)
            action.setChecked(not self._canvas.is_hidden(key))
            action.toggled.connect(lambda visible, k=key: self._on_block_toggled(k, visible))
            self._block_actions[key] = action
        view_menu.addSeparator()
        attach_strip_menu(view_menu.addMenu("Pinned Strip"), self._board.panel)
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
        layout = storage.sheet_layout("gm_layout")
        geometry = layout["window_geometry"]
        if geometry:
            self.restoreGeometry(QByteArray.fromBase64(geometry.encode("ascii")))
        state = layout["dock_state"]
        if state:
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
            storage.set_sheet_layout("gm_layout", geometry, json.dumps(self._canvas.arrangement()))
        except OSError:
            pass

    # -- construction ------------------------------------------------------

    def _build_status_strip(self) -> ToastCard:
        """The bottom strip: the hosting status line and the reachability advice.

        Whether players can reach the game, and ``discovery``'s advice verbatim when
        they may not. It rides in a dismissible :class:`ToastCard` that fades once
        read, rather than sitting on the board all session.
        """
        notice = ToastCard()

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

        # A wrapping row, for the reason Equipment's three "Add…" buttons wrap:
        # side by side they are wider than the block ever needs to be, and a row
        # that cannot wrap is a width the block can never be dragged under.
        button_row = FlowContainer()
        buttons = FlowLayout(button_row, spacing=int(theme.metric("space.sm")))
        roll = QPushButton("Roll initiative")
        roll.setToolTip(
            "Roll for every NPC on the Scene, and ask each player for theirs in the "
            "shared roll log."
        )
        roll.clicked.connect(self._roll_scene_initiative)
        buttons.addWidget(roll)
        # Arms itself rather than opening a dialog. Clearing the board is not
        # reversible and is done mid-round, which is the one case a modal is worst
        # for: it stops the table to say what the button can say in its own caption.
        self._new_scene_button = ConfirmButton("New scene")
        self._new_scene_button.setToolTip("Clear the board and every initiative on it.")
        self._new_scene_button.confirmed.connect(self._new_scene)
        buttons.addWidget(self._new_scene_button)
        layout.addWidget(button_row)

        self._scene_board = SceneBoard(self._data, gm=True)
        self._scene_board.set_placeholder(NO_SCENE_GM)
        self._scene_board.dropped.connect(self._drop_on_scene)
        self._scene_board.removeRequested.connect(self._remove_from_scene)
        self._scene_board.initiativeCleared.connect(self._clear_scene_initiative)
        self._scene_board.initiativeRollRequested.connect(self._roll_entry_initiative)
        self._scene_board.dispositionChanged.connect(self._set_disposition)
        self._scene_board.actionRequested.connect(self._on_scene_action)
        self._scene_board.removeConditionRequested.connect(self._remove_scene_condition)
        self._scene_board.recoveryRequested.connect(self._roll_scene_recovery)
        self._scene_board.clearTemporaryRequested.connect(self._clear_scene_temporary)
        self._scene_board.set_recovery_probe(self._scene_can_recover)
        # Every pixel of the box below the buttons, and no trailing stretch: the
        # board's drop target fills what it is given, so what a GM aims a dragged
        # card at is the whole block rather than the band its cards sit in.
        layout.addWidget(self._scene_board, stretch=1)
        return box

    def _build_players_box(self) -> QGroupBox:
        box = QGroupBox("Players")
        layout = QVBoxLayout(box)

        self._no_players = _wrapped(NO_PLAYERS)
        self._no_players.setEnabled(False)
        layout.addWidget(self._no_players)

        # A drop target only so a player's own card dragged out and back reads as a
        # cancelled drag; there is no order to edit here. Anything else is refused
        # visibly — it used to light up green for an NPC and then silently drop it.
        self._cards_container = CardDropFlow("gmPlayerFlow", accepts=_is_player_ref)
        self._cards_flow = self._cards_container.flow
        # A flow re-wraps on its own; the shared card width has to be re-measured,
        # since what it may be depends on the room the block has (_sync_card_widths).
        self._cards_container.widthChanged.connect(self._sync_card_widths)
        layout.addWidget(self._cards_container)
        layout.addStretch()
        return box

    def _build_npcs_box(self) -> QGroupBox:
        """The session's cast of NPCs, and the three ways to add one.

        An NPC is an ordinary character saved in the workspace ``gm_characters/``
        dir, so the GM's bestiary outlives any one session; what belongs to *this*
        session is which of them are in it (``SessionState.npc_paths``). So: throw
        one together from five numbers, write one on a blank sheet, or bring one in.
        """
        box = QGroupBox("NPCs")
        layout = QVBoxLayout(box)

        button_row = FlowContainer()
        buttons = FlowLayout(button_row, spacing=int(theme.metric("space.sm")))
        quick = QPushButton("Quick NPC…")
        quick.setToolTip("A mook from five numbers — name, attack, effect, defense, toughness.")
        quick.clicked.connect(self._quick_npc)
        buttons.addWidget(quick)
        create = QPushButton("Create NPC")
        create.clicked.connect(self._create_npc)
        buttons.addWidget(create)
        add = QPushButton("Add existing…")
        add.clicked.connect(self._add_existing_npc)
        buttons.addWidget(add)
        # One button rather than two, and its caption is the action it will take —
        # which makes it a readout of the board as well as a control. Shrinking a
        # dozen mooks one caret at a time is the case the collapse exists for.
        self._collapse_all_button = QPushButton(COLLAPSE_ALL)
        self._collapse_all_button.clicked.connect(self._toggle_collapse_all)
        buttons.addWidget(self._collapse_all_button)
        layout.addWidget(button_row)

        self._no_npcs = _wrapped(NO_NPCS)
        self._no_npcs.setEnabled(False)
        layout.addWidget(self._no_npcs)

        # A drop target as well as a flow: reordering is a real drag (it had to be,
        # to reach the Scene), so the bar showing where a card lands is drawn by the
        # container. Only an NPC's card, and visibly only that — it used to light up
        # green for a player or a scene card and then drop it on the floor.
        self._npc_container = CardDropFlow(
            "gmNpcFlow", accepts=_is_npc_ref, merges=self._can_merge_npcs
        )
        self._npc_flow = self._npc_container.flow
        self._npc_container.dropped.connect(self._drop_on_npcs)
        # Dropped onto the middle of another card: the two become a group.
        self._npc_container.merged.connect(self._merge_npcs)
        self._npc_container.widthChanged.connect(self._sync_card_widths)
        layout.addWidget(self._npc_container)
        layout.addStretch()
        return box

    def _build_rolls_box(self) -> QGroupBox:
        """The GM's own roller beside the table's shared history.

        The same :class:`~mm_companion.ui.roller.view.DiceRollerView` a player's
        Dice block holds, with two GM-only things: a **Hidden roll** switch, and this
        window's ``gm=True`` history in place of the view's private/shared pair. A
        hidden roll is never put on the wire, so it does not rely on the players'
        apps ignoring it.

        The history is this window's because it follows the bridge while there is
        one and the workspace's saved log when there is not (see
        :meth:`_refresh_rolls`).
        """
        self._rolls_box = box = QGroupBox("Rolls")
        # As on the sheet's Dice block: the history grows into whatever height the
        # block has, so the roller takes it all (see
        # :meth:`~mm_companion.ui.block_frame.InnerScroll.set_section`). It cannot be
        # derived — a ``QGroupBox`` is ``Preferred``; it is the history inside that
        # wants the room. Without it the pinned roller stopped at ~650px with the
        # rest of the strip empty.
        box.fills_height = True
        layout = QVBoxLayout(box)
        layout.setContentsMargins(0, 0, 0, 0)

        self._history = RollHistoryPanel(gm=True)
        self._history.rollRemovedLocally.connect(self._on_local_roll_removed)
        self._history.rollRevealedLocally.connect(self._on_local_roll_revealed)
        self._view = DiceRollerView(hidden_option=True, history=self._history)
        # Still the panel, so every card that loads or throws a spec into it (a
        # player's, an NPC's) reaches it by the same name it always did.
        self._roller = self._view.panel
        # A save or a request clicked in this history is answered for a creature the GM
        # plays — whose number goes in is asked first (see _on_history_roll). A
        # right-click rolls it as written, for nobody.
        self._history.rollFollowUp.disconnect(self._roller.roll_spec)
        self._history.rollAnswer.connect(self._on_history_roll)
        self._history.rollPlain.connect(self._roller.roll_spec)
        # A team attack's call joined here: one of the GM's NPCs joins it.
        self._history.teamJoinRequested.connect(self._gm_team_join)
        # The roll an NPC answered with, once it is back: what failing did goes on it.
        self._roller.sessionRollRevealed.connect(self._on_answer_back)
        # And a recovery check an NPC rolled: what making it sheds comes off it.
        self._roller.sessionRollRevealed.connect(self._on_recovery_back)
        # While hosting, a roll made here goes through the server like everyone
        # else's and comes back on the shared feed. Before that there is no
        # session to record it in, so it is shown as a card and nothing more.
        self._roller.localRoll.connect(self._show_offline_roll)
        # The Request row, on the same terms a player's block gives it: the traits
        # come from the ruleset (a request is answered on someone else's sheet, so
        # they are character-free) and this window decides where the ask goes.
        self._roller.set_roll_choices(requested_roll_choices(self._data))
        self._roller.rollRequested.connect(self._request_roll)
        # The Actions popup: the GM impresses, spreads an attack over an area, or has
        # a group act, for anybody — an NPC of theirs or a player — from the same
        # button a player's sheet has (see GMRollsMixin.action_menu).
        self._roller.set_action_menu(self.action_menu)
        self._roller.actionRequested.connect(self._on_roller_action)
        # The maneuvers and self actions, taken as the NPC each pick asks for.
        self._setup_combat()
        layout.addWidget(self._view)
        return box

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

    def end_gestures(self) -> None:
        """Stop any drag on the GM board before compact mode hides it."""
        self._canvas.end_gestures()

    def sync_dice_layout(self) -> None:
        """Re-read the roller's layout preference (see ``MainWindow.sync_dice_layout``)."""
        self._view.sync_dice_layout()

    def restore_roller(self) -> None:
        """Take the roller and the history back into the Rolls block."""
        self._view.restore_roller()

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
        self._status_notice.set_accent(colour or None)
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
        self._notice.set_accent(colour or None)
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
        """Open the Settings window on the GM page (Settings ▸ Preferences)."""
        from mm_companion.ui.settings import GMPage

        self._show_settings(GMPage.title)

    def open_session_settings(self) -> None:
        """Open the Settings window on the Session page (Settings ▸ Session settings)."""
        from mm_companion.ui.settings.session_page import SESSION_PAGE_TITLE

        self._show_settings(SESSION_PAGE_TITLE)

    def _show_settings(self, page: str) -> None:
        """The Settings window, landing on *page*, with this window's Session page.

        The Session page is built bound to this window's session, which is why only
        this window offers it: a sheet's Preferences has no session to set rules for.
        """
        from mm_companion.ui.settings import SessionPage, SettingsWindow

        bridge = self._bridge
        window = SettingsWindow(
            page=page, extra_pages=(lambda parent: SessionPage(parent, bridge=bridge),)
        )
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
        self._sync_scene_players(
            {pid for pid, card in self._cards.items() if card is not None}, all_connected=True
        )

    def showEvent(self, event: QShowEvent) -> None:  # noqa: N802 (Qt override)
        """Arm this window as the process-wide session."""
        set_active_session(self._bridge)
        super().showEvent(event)
        self._schedule_spare_npc()

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802 (Qt override)
        """Leave the session and give the router its port back before going away.

        Closing ends this GM Mode; opening it again goes back through the launch
        dialog. A session on this machine is stored as it goes; one on a server
        keeps playing with the GM disconnected.

        An NPC sheet with unsaved edits asks first. If the GM cancels there, this
        window stays open too: it is what keeps those sheets alive.
        """
        for window in self._player_windows.values():
            window.close()
        self._player_windows.clear()
        for npc_window in list(self._npc_windows.values()):
            npc_window.close()
        if any(window.isVisible() for window in self._npc_windows.values()):
            event.ignore()
            return
        self._discard_spare_npc()
        self._persist_layout()
        self.stop_hosting()
        set_active_session(None)
        super().closeEvent(event)


# --------------------------------------------------------------------------
# The base GM blocks
#
# Stated as descriptors so the window builds itself from the registry a mod
# extends. Registered at import, like ``register_base_blocks`` on the sheet side:
# a mod's Python runs at startup (``mods.initialize_mods``) and must find the base
# set already there, whether or not a GM window has been opened.
# --------------------------------------------------------------------------

# (key, title, the GMWindow method that builds it, row, col, pinned). The Scene
# comes first: it is what a GM watches through a fight.
_BASE_GM_BLOCKS = [
    ("scene", "Scene", "_build_scene_box", 0, 0, False),
    ("players", "Players", "_build_players_box", 1, 0, False),
    ("npcs", "NPCs", "_build_npcs_box", 2, 0, False),
    # The Rolls block starts in the strip rather than on the page, for the reason
    # the sheet's Dice block does: a roller that scrolls away with the board is no
    # use mid-fight. Both boards use the same seam, and the strip's default edge
    # is the right-hand one.
    ("rolls", "Rolls", "_build_rolls_box", 3, 0, True),
]


def register_base_gm_blocks(*, replace: bool = False) -> None:
    """Register the four base GM blocks (called once at import)."""
    # The GM blocks' bounds live in block_sizes.json alongside the sheet's, under
    # gm_-prefixed keys, so a theme can retune them the same way — the canvas keys
    # them without the prefix, since the GM window is its own block namespace.
    shipped = load_block_sizes()
    for key, title, builder, row, col, pinned in _BASE_GM_BLOCKS:
        register_gm_block(
            GMBlockDescriptor(
                key,
                title,
                # Bound at build time to the window being constructed, which is
                # what lets a descriptor name an instance method it cannot hold.
                (lambda name: lambda window: getattr(window, name)())(builder),
                shipped.get(f"gm_{key}", RecommendedSize()),
                row,
                col,
                pinned,
            ),
            replace=replace,
        )


register_base_gm_blocks(replace=True)

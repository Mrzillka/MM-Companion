"""Top-level application window hosting the character sheet."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QByteArray, Signal
from PySide6.QtGui import QAction, QCloseEvent, QKeySequence
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QMainWindow,
    QMenu,
    QMessageBox,
    QVBoxLayout,
    QWidget,
)

from mm_companion.core import library, storage
from mm_companion.core.character import Character
from mm_companion.ui.character_sheet import CharacterSheet
from mm_companion.ui.compact import CompactController
from mm_companion.ui.connection_indicator import install_connection_indicator
from mm_companion.ui.undo import UndoController

CHARACTER_FILTER = "Character files (*.json)"

#: The lock toggle's two faces. It sits on the menu bar rather than inside a menu
#: because it is a play-time view switch, not a preference — and because a glyph
#: that *shows* the current state is worth more there than a tick two clicks deep.
#: Emoji text on the action, matching the block title bars' 🖈 ↗ ✕, so it needs no
#: artwork and stays legible on every preset's bar.
LOCK_GLYPH_LOCKED = "🔒"
LOCK_GLYPH_UNLOCKED = "🔓"

#: Undo and redo, on the bar for the same reason the lock is: they are reached
#: constantly while building, and a button that is *there* is worth more than an
#: entry two clicks into a menu. Same plain-glyph bargain as the lock.
UNDO_GLYPH = "↶"
REDO_GLYPH = "↷"


class MainWindow(QMainWindow):
    """Main window; currently shows a single character sheet.

    Emits :attr:`closed` when the window is closed so a launcher can reappear,
    and :attr:`saved` after a character is written to disk so a launcher can
    refresh its library.

    Three small seams make this reusable for a character that is not a player's:
    :attr:`TITLE`, :meth:`storage_dir` (where the File dialogs open) and
    :meth:`_new_child` (what Open builds). :class:`~mm_companion.ui.npc_window.NPCWindow`
    is the same window pointed at the GM's ``gm_characters/`` dir.
    """

    #: What the window title is prefixed with.
    TITLE = "MM-Companion"

    closed = Signal()
    saved = Signal()
    #: A row of this sheet was right-clicked and pinned. Carries a
    #: :class:`~mm_companion.core.rules.pins.PinRef`, relayed straight from the
    #: sheet. Only ever raised on a window opened with ``pin_target=True``, which
    #: today means one a GM opened from a card.
    pinRequested = Signal(object)
    #: The same, for a row that was already pinned.
    unpinRequested = Signal(object)

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        character: Character | None = None,
        path: Path | None = None,
        locked: bool = True,
        gm_view: bool = False,
        npc: bool = False,
        pin_target: bool = False,
    ) -> None:
        super().__init__(parent)
        # A GM's read-only view of a player's snapshot: force-locked, with only the
        # View menu — no File/Settings/Session and no way to unlock or save.
        self._gm_view = gm_view
        # An NPC sheet: the GM's working material, not a player's finished build.
        # It keeps File/Settings but drops the play-time menus that only make sense
        # for a player at the table — Session and Cost config.
        self._npc = npc
        if gm_view:
            locked = True
        # Comfortably fits Base Information's natural width; the blocks stay well
        # inside this, so the sheet only ever scrolls vertically inside the central
        # scroll area when the blocks are taller than the window (see below).
        self.resize(1000, 860)
        # The file this sheet is saved to, or None until the first save.
        self._path: Path | None = Path(path) if path else None
        # Whether the sheet has unsaved edits since the last save/load.
        self._dirty = False
        # Windows opened from this one (via Open) kept referenced so they aren't
        # garbage-collected the moment the handler returns.
        self._child_windows: list[MainWindow] = []
        # The mod manager window, kept referenced while open for the same reason.
        self._mods_window: QWidget | None = None
        # The settings window, likewise kept referenced while open.
        self._settings_window: QWidget | None = None

        self._sheet = CharacterSheet(character=character)
        # Opened from a GM card: its rows offer "Pin to GM card", and what they pin
        # is relayed back out to whoever opened this window.
        self._sheet.set_pin_target(pin_target)
        self._sheet.pinRequested.connect(self.pinRequested)
        self._sheet.unpinRequested.connect(self.unpinRequested)
        # Compact mode: the whole window collapsed to just the dice roller. The
        # controller owns the mini page, borrows the roller out of the sheet when
        # it is asked for, and floats its own round shrink button over the roller
        # — so there is nothing to add to the menu bar; see
        # :mod:`mm_companion.ui.compact`.
        self._compact = CompactController(self, self._sheet, self._sheet)
        # The sheet's undo history. Not for a GM's read-only view of a player's
        # sheet: nothing there can be edited, and the GM opens one per click on a
        # card, so it would only cost a snapshot.
        self._undo: UndoController | None = None
        if not self._gm_view:
            self._undo = UndoController(self._sheet, parent=self)
            self._undo.stateChanged.connect(self._on_undo_state)
        self._build_menu_bar(locked)
        # The sheet is a scrolling page in its own right (it owns its scroll area),
        # so the only thing this wrapper is for is having the compact page beside
        # it: one of the two is always hidden, and a hidden widget is left out of
        # the layout's minimum, which is what lets the window shrink to the roller.
        central = QWidget()
        central_layout = QVBoxLayout(central)
        central_layout.setContentsMargins(0, 0, 0, 0)
        central_layout.addWidget(self._sheet)
        central_layout.addWidget(self._compact.page)
        self.setCentralWidget(central)
        self._update_title()

        # New characters open unlocked for editing; otherwise the sheet is a
        # read-only view.
        self._sheet.set_locked(locked)
        # Track unsaved changes only after the initial seed/lock has settled.
        self._sheet.edited.connect(self._on_edited)

        # Restore the remembered window size and block arrangement, if any.
        self._restore_layout()

    @property
    def sheet(self) -> CharacterSheet:
        """The character sheet this window hosts — the seam a session attaches to."""
        return self._sheet

    @property
    def path(self) -> Path | None:
        """The file this sheet is saved to, or ``None`` until the first save."""
        return self._path

    def sync_dice_layout(self) -> None:
        """Re-read the dice roller's layout preference.

        Found by name rather than by type: the Settings window walks the open
        windows looking for anything that answers this, so it needs no import of
        the sheet or the GM window to reach either of them.
        """
        self._sheet.sync_dice_layout()

    def storage_dir(self) -> Path:
        """The workspace directory this window's Open/Save dialogs start in."""
        return storage.get_workspace().characters_dir

    def _new_child(self, character: Character, path: Path) -> MainWindow:
        """The window File ▸ Open builds — the same kind as this one."""
        return MainWindow(character=character, path=path, locked=True)

    def _build_menu_bar(self, locked: bool) -> None:
        """Build the top menu bar.

        Menus first, then the lock toggle as the bar's last item — it is a widget
        on the bar, not an entry inside a menu (see :data:`LOCK_GLYPH_LOCKED`).

        A GM's read-only view (:attr:`_gm_view`) returns before either, so it gets
        only the **View** menu — no File/Settings/Session and no lock — and a
        player's snapshot can be looked at and rearranged but never edited, saved,
        or unlocked.
        """
        menu_bar = self.menuBar()

        if not self._gm_view:
            file_menu = menu_bar.addMenu("&File")
            self._add_placeholder_actions(file_menu, ["New"])
            file_menu.addAction("Open...").triggered.connect(self._open)
            file_menu.addAction("Save").triggered.connect(self._save)
            file_menu.addAction("Save As...").triggered.connect(self._save_as)
            file_menu.addSeparator()
            # Exit closes the sheet; closing brings the launcher back (see closeEvent).
            file_menu.addAction("Exit").triggered.connect(self.close)

        self._build_view_menu(menu_bar)

        if self._gm_view:
            # Not even the compact toggle: this bar is deliberately bare, and the
            # GM has their own roller in the GM window. Compact mode still *works*
            # here — the roller's own ⤡ is on every panel — it just gets no chrome.
            return

        settings_menu = menu_bar.addMenu("&Settings")
        self._add_placeholder_actions(settings_menu, ["Rules"])
        settings_menu.addAction("Preferences...").triggered.connect(self._open_settings)
        settings_menu.addAction("Mods...").triggered.connect(self._manage_mods)
        # Homebrew the non-power PP-cost rates for this character. Stays available even
        # in a locked (read-only) view — it is a config action, not a build edit.
        # An NPC has no point build, so there is nothing to configure the cost of.
        if not self._npc:
            self._cost_config_action = settings_menu.addAction("Cost config...")
            self._cost_config_action.triggered.connect(self._open_cost_config)

        # Joining a session is something a *player* does at the table; an NPC sheet
        # is the GM's prep material, driven from the GM window instead. (Rolling
        # dice used to have a Tools menu here; it is the Dice block now — see
        # mm_companion.ui.sections.dice.)
        # The one thing on this bar that does not fade: a sheet in a session says
        # so for as long as it is, and a sheet that is not says "Offline".
        #
        # Only on a sheet that could *be* in one, which is the same rule as the
        # Session menu above and for the same reason. A GM opens an NPC sheet (or
        # a player's read-only snapshot, which returned above) while hosting, and
        # those windows never join anything — so an indicator there would sit at
        # "Offline" throughout a perfectly healthy session, which is exactly the
        # false reading this widget exists to prevent.
        if not self._npc:
            session_menu = menu_bar.addMenu("&Session")
            session_menu.addAction("Join session...").triggered.connect(self._join_session)
            install_connection_indicator(self)

        self._build_undo_actions(menu_bar)

        # Last on the bar, and on the bar rather than in a menu: locking is how a
        # sheet is read *and* how it is written, so it is reached constantly. An
        # action added straight to a QMenuBar with no submenu behaves as a button —
        # one click, and the glyph is the state read-out.
        self._lock_action = menu_bar.addAction(LOCK_GLYPH_LOCKED)
        self._lock_action.setCheckable(True)
        self._lock_action.setChecked(locked)
        self._lock_action.toggled.connect(self._sheet.set_locked)
        self._lock_action.toggled.connect(self._show_lock_state)
        self._show_lock_state(locked)

    def _build_undo_actions(self, menu_bar) -> None:
        """Add the ↶ / ↷ buttons and their shortcuts, just before the lock.

        Two things worth knowing. The shortcuts hang off the actions rather than off
        a bare :class:`QShortcut`, so one object carries the key, the button and the
        enabled state — and ``Ctrl+Z`` inside a text field still reaches that field's
        own undo first, which is what every other application does and what the
        coalescing window makes harmless anyway.

        And each action is *also* added to the window, because compact mode hides the
        menu bar and a shortcut is inactive while the widget owning it is hidden. An
        action may belong to several widgets; the window is always visible.
        """
        if self._undo is None:
            return
        self._undo_action = menu_bar.addAction(UNDO_GLYPH)
        self._undo_action.setShortcut(QKeySequence.StandardKey.Undo)
        self._undo_action.setToolTip("Undo (Ctrl+Z)")
        self._undo_action.triggered.connect(self._undo.undo)
        self._redo_action = menu_bar.addAction(REDO_GLYPH)
        self._redo_action.setShortcuts([QKeySequence("Ctrl+Shift+Z"), QKeySequence("Ctrl+Y")])
        self._redo_action.setToolTip("Redo (Ctrl+Shift+Z)")
        self._redo_action.triggered.connect(self._undo.redo)
        for action in (self._undo_action, self._redo_action):
            self.addAction(action)
        self._on_undo_state()

    def _on_undo_state(self) -> None:
        """Follow the history: what the two buttons offer, and whether we are dirty.

        The dirty flag is *re-derived* here rather than only set: an undo back to the
        state last written to disk really is clean, and the ``*`` should go away.
        """
        if self._undo is None:
            return
        if hasattr(self, "_undo_action"):
            self._undo_action.setEnabled(self._undo.can_undo)
            self._redo_action.setEnabled(self._undo.can_redo)
        # Only ever *clears* the flag: setting it is _on_edited's job, and
        # at_saved_state() is False for a character that has never been written, so
        # a brand-new sheet is not declared clean before it has anywhere to be clean
        # against.
        if self._dirty and self._undo.at_saved_state():
            self._dirty = False
            self._update_title()

    def _show_lock_state(self, locked: bool) -> None:
        """Put the current lock state on the bar's glyph and its tooltip."""
        self._lock_action.setText(LOCK_GLYPH_LOCKED if locked else LOCK_GLYPH_UNLOCKED)
        self._lock_action.setToolTip(
            "Locked — click to edit this sheet"
            if locked
            else "Unlocked — click to make this sheet read-only"
        )

    def _build_view_menu(self, menu_bar) -> None:
        """The View menu: one show/hide toggle per block, plus Reset Layout."""
        self._view_menu = menu_bar.addMenu("&View")
        # A show/hide toggle per block, so a block closed with its × can be
        # reopened, plus a reset back to the default arrangement.
        self._block_actions: dict = {}
        for key in self._sheet.block_keys():
            self._add_block_action(key)
        # Everything below the separator acts on the menu rather than being one of
        # its toggles, so the separator is held onto: a toggle added later has to
        # be inserted *above* it, or "Reset Layout" stops being the last thing.
        self._view_tail = self._view_menu.addSeparator()
        for descriptor in self._sheet.multi_templates():
            action = self._view_menu.addAction(f"New {descriptor.title} Block")
            action.triggered.connect(
                lambda _checked=False, t=descriptor.key: self._sheet.add_block_instance(t)
            )
        self._view_menu.addAction("Reset Layout").triggered.connect(self._reset_layout)
        # Keep the View toggles in sync when a block is hidden/shown elsewhere
        # (its × button, a drag, or Reset Layout).
        self._sheet.canvas.block_visibility_changed.connect(self._on_block_visibility_changed)
        # …and in step with the block set itself, which a multi-instance block
        # (Notes) makes something that changes while the window is open.
        self._sheet.canvas.block_added.connect(self._on_block_added)
        self._sheet.canvas.block_removed.connect(self._on_block_removed)

    def _join_session(self) -> None:
        """Join a GM's session, bringing the character already open in this window.

        Unlike the launcher's Join, this skips the character picker — the sheet in
        front of the player *is* the character they are bringing, and the pusher
        reads it live from :attr:`sheet`.
        """
        from mm_companion.core.session.client import SessionClientError
        from mm_companion.ui.session_bridge import SessionBridge, active_session, set_active_session
        from mm_companion.ui.session_dialogs import JoinSessionDialog, record_session_history
        from mm_companion.ui.session_player import attach_player_session

        if active_session() is not None:
            QMessageBox.information(
                self,
                "Already in a session",
                "This app is already in a session. Close that window first.",
            )
            return

        dialog = JoinSessionDialog(self, pick_character=False)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        bridge = SessionBridge()
        player_id, player_token = dialog.reclaim_ids()
        try:
            client = bridge.join(
                dialog.join_code(),
                dialog.display_name(),
                player_id=player_id,
                player_token=player_token,
            )
        except SessionClientError as exc:
            QMessageBox.warning(self, "Could not join", str(exc))
            return
        set_active_session(bridge)

        def remember(*_args: object) -> None:
            """Write the seat down on every connect, not only the first.

            A reconnect can come back with a different token, and the copy on disk
            is what a *later launch* reclaims with — so recording it once at join
            time leaves the next session starting as a stranger.
            """
            record_session_history(
                code=dialog.code_text(),
                session_id=client.session_id,
                session_name=client.session_name,
                display_name=dialog.display_name(),
                player_id=client.player_id,
                player_token=client.player_token,
            )

        remember()
        bridge.connected.connect(remember)
        attach_player_session(self, bridge)

    def _open_cost_config(self) -> None:
        """Open the per-character homebrew cost-config editor (Settings ▸ Cost config)."""
        self._sheet.system_info.open_cost_config()

    def _manage_mods(self) -> None:
        """Open the Mod Manager window."""
        from mm_companion.ui.mods_window import ModsWindow

        window = ModsWindow()
        self._mods_window = window
        window.show()

    def _open_settings(self) -> None:
        """Open the Settings window (Settings ▸ Preferences)."""
        from mm_companion.ui.settings import SettingsWindow

        window = SettingsWindow()
        self._settings_window = window
        window.show()

    # -- persistence ---------------------------------------------------------

    def _save(self) -> bool:
        """Overwrite the character's file, or prompt for one on first save.

        Returns whether the character was actually written (False if the user
        backed out of the Save As dialog).
        """
        if self._path is None:
            return self._save_as()
        return self._write(self._path)

    def _save_as(self) -> bool:
        """Prompt for a destination and write the character there."""
        directory = self.storage_dir()
        directory.mkdir(parents=True, exist_ok=True)
        suggested = directory / library.suggested_filename(self._sheet.character)
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Character", str(suggested), CHARACTER_FILTER
        )
        if not path:
            return False
        return self._write(Path(path))

    def _write(self, path: Path) -> bool:
        """Persist the character to *path* and remember it as the current file."""
        # Land any coalescing edit as its own step *before* the write, because the
        # write itself edits the model — save_character rewrites an external
        # image_path to a workspace filename — and that is the app tidying up after
        # the user, not a step for them to walk back through.
        if self._undo is not None:
            self._undo.flush()
        saved_path = library.save_character(self._sheet.character, path=path)
        self._path = saved_path
        if self._undo is not None:
            self._undo.mark_saved()
        self._dirty = False
        self._update_title()
        self.statusBar().showMessage(f"Saved to {saved_path}", 5000)
        self.saved.emit()
        return True

    def _open(self) -> None:
        """Load a saved character into a new, read-only window."""
        directory = self.storage_dir()
        path, _ = QFileDialog.getOpenFileName(
            self, "Open Character", str(directory), CHARACTER_FILTER
        )
        if not path:
            return
        character = library.load_character(Path(path))
        window = self._new_child(character, Path(path))
        self._child_windows.append(window)
        window.show()

    # -- layout persistence --------------------------------------------------

    def _restore_layout(self) -> None:
        """Restore the remembered window geometry and block arrangement.

        The layout is a global UI preference stored in settings.json; a missing or
        incompatible entry simply leaves the default arrangement in place. The
        ``dock_state`` value is now the sheet's JSON arrangement (see
        ``CharacterSheet.save_layout``); the key name is kept for continuity.
        """
        layout = storage.load_settings().get("layout") or {}
        geometry = layout.get("window_geometry")
        if isinstance(geometry, str) and geometry:
            self.restoreGeometry(QByteArray.fromBase64(geometry.encode("ascii")))
        self._sheet.restore_layout(layout.get("dock_state"))

    def _persist_layout(self) -> None:
        """Save the window geometry and block arrangement as a global preference.

        A window closed while compact remembers what it was **before** it shrank:
        this key is shared by every sheet, so persisting 380x560 here would open
        every character that size from then on. The mini window's own size is
        remembered separately, by the controller.
        """
        self._compact.remember_size()
        state = self._compact.saved_geometry() or self.saveGeometry()
        geometry = bytes(state.toBase64()).decode("ascii")
        storage.update_settings(
            layout={"window_geometry": geometry, "dock_state": self._sheet.save_layout()}
        )

    def _add_block_action(self, key: str) -> None:
        """Give block *key* its show/hide toggle, above the menu's own actions.

        ``base_title``, not ``title``: the live one carries a point subtotal that
        was current when the menu was built and is never re-labelled.
        """
        if key in self._block_actions:
            return
        action = QAction(self._sheet.block_frame(key).base_title, self)
        action.setCheckable(True)
        action.setChecked(not self._sheet.is_block_hidden(key))
        action.toggled.connect(lambda visible, k=key: self._on_block_toggled(k, visible))
        tail = getattr(self, "_view_tail", None)
        if tail is None:
            self._view_menu.addAction(action)
        else:
            self._view_menu.insertAction(tail, action)
        self._block_actions[key] = action

    def _on_block_added(self, key: str) -> None:
        self._add_block_action(key)

    def _on_block_removed(self, key: str) -> None:
        action = self._block_actions.pop(key, None)
        if action is not None:
            self._view_menu.removeAction(action)

    def _on_block_toggled(self, key: str, visible: bool) -> None:
        """Show or hide a block from its View-menu toggle."""
        if visible:
            self._sheet.show_block(key)
        else:
            self._sheet.hide_block(key)

    def _on_block_visibility_changed(self, key: str, visible: bool) -> None:
        """Keep a block's View-menu toggle in sync when it's hidden/shown elsewhere."""
        action = self._block_actions.get(key)
        if action is None or action.isChecked() == visible:
            return
        action.blockSignals(True)
        action.setChecked(visible)
        action.blockSignals(False)

    def _reset_layout(self) -> None:
        """Restore the default arrangement.

        The View-menu toggles resync themselves: ``reset_layout`` goes through
        ``BlockCanvas.apply_arrangement``, which announces every block whose
        visibility changed over ``block_visibility_changed``.
        """
        self._sheet.reset_layout()

    def _on_edited(self) -> None:
        """Mark the sheet dirty and refresh the title on any user edit.

        The title carries the character's name, which an edit can *be* — so it is
        rebuilt every time, not only on the first edit that flips the dirty flag.
        """
        self._dirty = True
        self._update_title()

    def _update_title(self) -> None:
        name = library.display_name(self._sheet.character)
        marker = "*" if self._dirty else ""
        self.setWindowTitle(f"{self.TITLE} — {marker}{name}")
        # The mini window is frameless, so its strip is the only place the title
        # shows at all — including the unsaved-changes marker.
        self._compact.page.strip.set_title(f"{marker}{name}")

    def closeEvent(self, event: QCloseEvent) -> None:
        """Guard unsaved changes, announce the close, then close normally."""
        if self._dirty and not self._confirm_close():
            event.ignore()
            return
        self._persist_layout()
        self.closed.emit()
        super().closeEvent(event)

    def _confirm_close(self) -> bool:
        """Prompt to save/discard unsaved changes; return True if OK to close."""
        choice = QMessageBox.question(
            self,
            "Unsaved changes",
            f"Save changes to {library.display_name(self._sheet.character)}?",
            QMessageBox.StandardButton.Save
            | QMessageBox.StandardButton.Discard
            | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Save,
        )
        if choice == QMessageBox.StandardButton.Save:
            return self._save()  # a cancelled Save As leaves the window open
        return choice == QMessageBox.StandardButton.Discard

    @staticmethod
    def _add_placeholder_actions(menu: QMenu, labels: list[str]) -> None:
        """Add disabled placeholder actions to *menu*, one per label."""
        for label in labels:
            action = menu.addAction(label)
            action.setEnabled(False)

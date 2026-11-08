"""The NPC board: the cards, creating and opening NPCs, the spare sheet, the requests an
NPC sheet makes, and collapsing cards.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFileDialog,
    QMainWindow,
    QMessageBox,
)

from mm_companion.core import library, npc_groups, storage
from mm_companion.core.character import Character
from mm_companion.core.data_loader import load_game_data
from mm_companion.core.npc import quick_npc
from mm_companion.core.rules import (
    RollSpec,
    area_capable_builds,
    area_key_index,
    area_key_power_id,
    find_area_power,
)
from mm_companion.core.session import store
from mm_companion.core.session.model import (
    KIND_NOTE,
)
from mm_companion.ui import loading, npc_icons, theme
from mm_companion.ui.blocks.bus import (
    AREA_REQUESTED,
    ATTACK_REQUESTED,
    BONUS_REQUESTED,
    LOAD_REQUESTED,
    NOTE_REQUESTED,
    ROLL_REQUESTED,
)
from mm_companion.ui.gm.model import (
    COLLAPSE_ALL,
    EXPAND_ALL,
    SCENE_NPC,
    _next_copy_name,
    _npc_key,
    _NpcEntry,
)
from mm_companion.ui.main_window import opening_text
from mm_companion.ui.npc_card import NPCCard
from mm_companion.ui.npc_group import GROUP_KIND, NPCGroupCard
from mm_companion.ui.npc_quick_dialog import QuickNPCDialog
from mm_companion.ui.npc_window import NPCWindow
from mm_companion.ui.widgets import (
    discard_widget,
    enclosing_scroll_areas,
    resurface,
)


class GMNpcBoardMixin:
    """Part of :class:`~mm_companion.ui.gm_window.GMWindow`; see the module docstring."""

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
                discard_widget(widget)

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

        self._group_cards = {}
        shown = npc_groups.shown_groups(self._npc_groups, self._npc_state)
        drawn: set[str] = set()
        for name in self._ordered_npcs():
            if name in drawn:
                continue
            group = npc_groups.group_of(shown, name)
            if group is not None:
                # The whole squad is drawn where its first member stands in the order.
                rows = [self._build_npc_card(member, row=True) for member in group.members]
                drawn.update(group.members)
                group_card = NPCGroupCard(group, rows)
                group_card.renameRequested.connect(self._rename_group)
                group_card.ungroupRequested.connect(self._ungroup)
                group_card.sceneRequested.connect(self._group_to_scene)
                group_card.teamAttackRequested.connect(self._team_attack)
                group_card.checkRequested.connect(self._group_check)
                self._group_cards[group.id] = group_card
                self._npc_flow.addWidget(group_card)
                continue
            drawn.add(name)
            self._npc_flow.addWidget(self._build_npc_card(name))
        self._no_npcs.setVisible(not self._npc_state)
        self._sync_card_widths()
        self._refresh_collapse_all()
        # An NPC that has left the cast has left the board with it, and the fresh
        # cards need their eyes told. Every refresh ends here, which makes this the
        # one push point for anything routed through one — a rolled initiative, a
        # condition, a damage step.
        self._scene = [e for e in self._scene if e.kind != SCENE_NPC or e.source in self._npc_state]
        self._push_scene()
        # A cast with someone in it is a cast whose sheets will be opened.
        if self._npc_state:
            self._schedule_spare_npc()

    def _build_npc_card(self, name: str, *, row: bool = False) -> NPCCard:
        """One NPC's card (or a group's row of it), wired to this window."""
        entry = self._npc_state[name]
        card = NPCCard(
            entry.character,
            entry.summary,
            self._data,
            collapsed=entry.collapsed,
            row=row,
        )
        card.openRequested.connect(self._open_npc)
        card.removeRequested.connect(self._remove_npc)
        card.deleteRequested.connect(self._delete_npc)
        card.applyConditionRequested.connect(self._apply_npc_condition)
        card.removeConditionRequested.connect(self._remove_npc_condition)
        card.recoveryRequested.connect(self._roll_npc_recovery)
        card.sceneToggled.connect(lambda n, on: self._set_in_scene(SCENE_NPC, n, on))
        card.copyRequested.connect(self._copy_npc)
        card.pinsChanged.connect(lambda n, refs: self._store_pins(_npc_key(n), refs))
        card.loadRequested.connect(lambda s, n=name: self._npc_roll(n, s, load=True))
        card.rollRequested.connect(lambda s, n=name: self._npc_roll(n, s))
        card.pinPickerRequested.connect(self._open_npc_pin_picker)
        card.collapsedChanged.connect(self._set_npc_collapsed)
        card.damageRequested.connect(self._apply_npc_damage)
        card.minionToggled.connect(self._set_npc_minion)
        card.summonedToggled.connect(self._set_npc_summoned)
        card.set_minion_rules(self.minion_rules())
        card.areaRequested.connect(self._start_card_area)
        card.impressRequested.connect(self._start_card_impress)
        card.pins.set_pins(self._pins_for(_npc_key(name), "npc"))
        # Connected after the initial load, so seeding the strip does not ask
        # the window to re-measure a grid it is halfway through building.
        card.pins.widthHintChanged.connect(self._sync_card_widths)
        card.leaveGroupRequested.connect(self._leave_group)
        card.aim_targets = lambda n=name: self._aim_targets_for(n)
        card.attackRequested.connect(self._aim_from_card)
        entry.card = card
        return card

    def _ordered_npcs(self) -> list[str]:
        """The cast in render order: the GM's own arrangement, and nothing else.

        It used to sort rolled NPCs to the top by initiative, exactly as the Scene
        does. That was one ordering shown on two boards, and the cost was that the
        cast re-arranged itself under the GM's hands every time a mook rolled — in
        the middle of a round, on the grid they were reaching into. The turn order
        lives on the Scene now; this is a cast list, and it stays where it is put.
        """
        base = [n for n in self._manual_order if n in self._npc_state]
        base += [n for n in self._npc_state if n not in base]
        return base

    def _drop_on_npcs(self, ref: str, index: int) -> None:
        """A card was dropped on the NPC grid.

        Only an NPC's own card means anything here: a *player* dragged onto the
        cast is not a thing that can be done, and refusing it quietly is better
        than inventing an interpretation. An NPC's is the reorder this grid has
        always had, unchanged below.
        """
        kind, _, source = ref.partition(":")
        if kind == GROUP_KIND and source in self._group_cards:
            # A whole group moves as one, keeping its rows' order.
            self._reorder_npcs(self._group_cards[source].members, index)
            return
        if kind == SCENE_NPC and source in self._npc_state:
            # A group's row dropped *between* cards has left its group.
            if npc_groups.group_of(self._npc_groups, source) is not None:
                self._npc_groups = npc_groups.leave(self._npc_groups, source)
                self._persist_groups()
            self._reorder_npc(source, index)

    def _board_blocks(self) -> list[list[str]]:
        """The board as it is laid out: one list of file names per card, a group's
        members together, in flow order."""
        blocks: list[list[str]] = []
        for index in range(self._npc_flow.count()):
            item = self._npc_flow.itemAt(index)
            widget = item.widget() if item is not None else None
            if isinstance(widget, NPCGroupCard):
                blocks.append(widget.members)
            elif isinstance(widget, NPCCard):
                blocks.append([widget.name_key])
        return blocks

    def _reorder_npcs(self, names: list[str], target_index: int) -> None:
        """Move *names* together to the dropped slot *target_index* among the board's
        cards (counted the way :meth:`_reorder_npc` always has: without the mover)."""
        moving = [n for n in names if n in self._npc_state]
        if not moving:
            return
        blocks = [[n for n in block if n not in moving] for block in self._board_blocks()]
        blocks = [block for block in blocks if block]
        blocks.insert(max(0, min(target_index, len(blocks))), moving)
        laid = [n for block in blocks for n in block]
        self._manual_order = laid + [n for n in self._ordered_npcs() if n not in laid]
        self._refresh_npcs()

    def _create_npc(self) -> None:
        """Write a new NPC: an editable, simplified sheet that saves into the cast."""
        with loading.loading(self, opening_text(None)):
            window = NPCWindow(locked=False)
        self._track_npc_window(window)

    def _quick_npc(self) -> None:
        """Build a mook from a Power Level and a preset and put it straight in the cast.

        Saved immediately rather than handed to an unsaved sheet: the wizard has
        everything the roster needs, so the creature is in the cast, and rollable
        against, the moment the dialog closes.

        **Nothing opens.** It used to open the new NPC's full sheet, but a GM making
        five mooks wants five cards, not five windows to close. It lands expanded,
        like every new card; the sheet is one click on its portrait away.
        """
        dialog = QuickNPCDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        entered = dialog.value()
        npc = quick_npc(
            load_game_data(),
            name=entered.name,
            power_level=entered.power_level,
            attack=entered.attack,
            effect=entered.effect,
            defence=entered.defence,
            toughness=entered.toughness,
            fortitude=entered.fortitude,
            will=entered.will,
            initiative=entered.initiative,
            # A bare workspace filename, shared by every creature with this icon.
            image_path=npc_icons.store_icon(entered.icon),
        )
        npc.minion = entered.minion
        if entered.count <= 1:
            path = library.save_character(npc, directory=self._npc_dir())
            self._register_npc(path)
            return
        self._make_squad(npc, entered.count)

    def _make_squad(self, npc: Character, count: int) -> None:
        """Save *count* copies of *npc* as a squad: "Thug 1…N", grouped on the board.

        Every file is written before the cast changes, and the cast and the board
        change once — not *count* times over, each a full rebuild. Not put on the
        Scene: the GM brings a squad in when the fight reaches it.
        """
        taken = {entry.summary.name for entry in self._npc_state.values()}
        names = npc_groups.numbered_names(library.display_name(npc), count, taken)
        files: list[str] = []
        for name in names:
            copy = Character.from_dict(npc.to_dict())
            copy.profile["hero_name"] = name
            files.append(library.save_character(copy, directory=self._npc_dir()).name)
        self._set_npc_paths([*self._state.npc_paths, *files])
        self._group_npcs(files, npc_groups.base_name(names[0]))
        self._refresh_npcs()

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
        # A copy of one of a squad is one more of that squad.
        if npc_groups.group_of(self._npc_groups, name) is not None:
            self._npc_groups = npc_groups.join(self._npc_groups, [path.name], name)
            self._persist_groups()
            if name in self._manual_order:
                at = self._manual_order.index(name) + 1
                self._manual_order[at:at] = [path.name]
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
            resurface(existing)
            return
        character = library.load_character(path)
        # A spare sheet is handed the creature in a moment; building one fresh is
        # most of a second, behind a loading screen over this window (ui/loading).
        with loading.loading(self, opening_text(character)):
            window = self._take_spare_npc(character, path)
        if entry.card is not None:
            self._attach_pin_sheet(window, entry.card)
        self._track_npc_window(window)

    def _track_npc_window(self, window: NPCWindow) -> None:
        """Show an NPC sheet and keep it alive, watching for saves and its close."""
        key = id(window)
        self._npc_windows[key] = window
        # Its attack lines aim off this window's own Scene, offline too, and never at
        # the creature itself (``attack_targets.targets_for``).
        window.aim_targets = lambda w=window: self._aim_targets_for_window(w)
        window.requestUnserved.connect(
            lambda topic, payload, w=window: self._on_npc_request(topic, payload, w)
        )
        window.saved.connect(lambda w=window: self._on_npc_saved(w))
        window.closed.connect(lambda k=key: self._npc_windows.pop(k, None))
        # Closing a sheet saves the NPC arrangement, which the spare should open with.
        window.closed.connect(self._refresh_spare_layout)
        window.show()
        window.raise_()

    # -- the spare NPC sheet -------------------------------------------------

    #: How long after the window shows, after the first creature joins the cast, or
    #: after a spare is used, the next one is built. Building one blocks the GUI
    #: thread for a fifth of a second (most of a second the first time, while Qt
    #: warms up), so not *immediately*: not under the pointer of a GM still settling
    #: into the window, and not straight after a sheet has opened, while they are
    #: reading it. ``None`` builds none at all (the test suite, which opens hundreds
    #: of GM windows and wants none of them building a sheet behind its back).
    SPARE_NPC_DELAY_MS: int | None = 750

    def _schedule_spare_npc(self) -> None:
        """Arm the timer for a spare, unless one is ready, coming, or pointless."""
        if (
            self.SPARE_NPC_DELAY_MS is None
            or self._spare_npc is not None
            or self._spare_unsupported
            or self._spare_timer.isActive()
        ):
            return
        self._spare_timer.start(self.SPARE_NPC_DELAY_MS)

    def _prime_spare_npc(self) -> None:
        """Build the NPC sheet the next card will open, before anyone asks for it.

        Opening a creature is almost all *building a sheet*, and none of that depends
        on which creature it is — so it is done here, in idle time, and the click only
        hands the sheet a model (:meth:`NPCWindow.load`) and shows it. The first one
        built also pays Qt's one-off warm-up. Never shown and never parented: a
        top-level window that has not been opened yet.

        Not for an empty cast (:meth:`_refresh_npcs` asks again when one joins), and
        not under an open dialog: the timer fires inside its event loop too, and a
        freeze while the GM types a Quick NPC's name is where it shows.
        """
        if self._spare_npc is not None or self._spare_unsupported or not self.isVisible():
            return
        if not self._npc_state:
            return
        if QApplication.activeModalWidget() is not None:
            if self.SPARE_NPC_DELAY_MS is not None:
                self._spare_timer.start(self.SPARE_NPC_DELAY_MS)
            return
        spare = NPCWindow(pin_target=True)
        if not spare.can_load():
            # A block on it cannot be handed another creature; every sheet is built
            # fresh, as it always was, rather than one showing stale numbers.
            self._spare_unsupported = True
            spare.deleteLater()
            return
        self._spare_npc = spare

    def _take_spare_npc(self, character: Character, path: Path) -> NPCWindow:
        """The sheet for *character*: the spare if one is ready, a new one if not."""
        spare, self._spare_npc = self._spare_npc, None
        if spare is None:
            window = NPCWindow(character=character, path=path, pin_target=True)
        else:
            spare.load(character, path)
            window = spare
        self._schedule_spare_npc()
        return window

    def _refresh_spare_layout(self) -> None:
        """An NPC sheet closed and saved its arrangement: the spare takes it up.

        Re-applied rather than rebuilt — re-reading a layout is a twentieth of the
        cost of a sheet, and this runs on every close.
        """
        if self._spare_npc is not None:
            self._spare_npc.reapply_saved_layout()
        else:
            self._schedule_spare_npc()

    def _discard_spare_npc(self) -> None:
        """Drop the spare without closing it: closing a sheet saves its layout."""
        self._spare_timer.stop()
        spare, self._spare_npc = self._spare_npc, None
        if spare is not None:
            spare.deleteLater()

    def _on_npc_request(self, topic: str, payload: object, window=None) -> None:
        """A roll (or a line for the history) asked for on an NPC sheet.

        An NPC sheet has no roller of its own, so it lands here — on this window's,
        exactly where a roll clicked on the creature's card lands, which is the
        roller a GM is already watching. A bad payload costs the roll, not the
        window: a mod block can publish on these topics too.
        """
        if topic == ROLL_REQUESTED and isinstance(payload, RollSpec) and payload.recovers:
            # A recovery check off the sheet's own condition chip: rolled as the
            # card's 🎲 rolls it, so a made one sheds the condition here too.
            path = Path(window.path) if window is not None and window.path else None
            key = next((n for n, e in self._npc_state.items() if e.path == path), "")
            if key:
                self._roll_npc_recovery(key, payload.recovers, payload.recovers_parameter)
            else:
                self._roller.roll_spec(payload)
        elif topic == ROLL_REQUESTED and isinstance(payload, RollSpec):
            # Rolled as the sheet's creature, so a minion's natural 20 is no critical.
            sheet = window.sheet.character if window is not None else None
            self._roller.roll_spec(payload, minion=self.rolls_as_minion(sheet))
        elif topic == ATTACK_REQUESTED and window is not None:
            # An attack line aimed at someone on the Scene: the GM's own, so no card.
            self._aim_from_sheet(window, payload)
        elif topic == AREA_REQUESTED and isinstance(payload, str) and window is not None:
            # Not a die: the NPC uses a power on an area, and the GM picks who is in it.
            self._start_npc_area(window, payload)
        elif topic == LOAD_REQUESTED and isinstance(payload, RollSpec):
            sheet = window.sheet.character if window is not None else None
            self._roller.load_spec(payload, minion=self.rolls_as_minion(sheet))
        elif topic == BONUS_REQUESTED and type(payload) is int and payload > 0:
            self._roller.add_bonus(payload)
        elif topic == NOTE_REQUESTED and isinstance(payload, str) and payload:
            self._post_note(payload)

    def _start_npc_area(self, window: NPCWindow, area_key: str) -> None:
        """Open the area window for the build *power_id* names on an NPC sheet.

        The NPC is the cast entry the sheet's file is, never one found by its display
        name: two Thugs share a name, and the wrong one would be left out of the targets
        while the attacker could be ticked into its own area.
        """
        character = window.sheet.character
        power = find_area_power(character, self._data, area_key)
        if power is None:
            return
        path = Path(window.path) if window.path is not None else None
        key = next((n for n, entry in self._npc_state.items() if entry.path == path), "")
        self._area.start_for_npc(
            character,
            power,
            key,
            name=library.display_name(character),
            effect_index=area_key_index(area_key),
        )

    def _start_card_area(self, key: str, area_key: str) -> None:
        """An NPC card's Area button: that creature uses the area *area_key* names."""
        entry = self._npc_state.get(key)
        if entry is None:
            return
        wanted = area_key_power_id(area_key)
        power = next(
            (b for b in area_capable_builds(entry.character, self._data) if b.id == wanted),
            None,
        )
        if power is None:
            return
        self._area.start_for_npc(
            entry.character,
            power,
            key,
            name=library.display_name(entry.character),
            effect_index=area_key_index(area_key),
        )

    def _start_card_impress(self, key: str) -> None:
        """An NPC card's Impress button: that creature impresses somebody."""
        self._impress.open_new(source_key=key)

    def _post_note(self, text: str) -> None:
        """A line in the history: the table's while hosting, this window's off the air.

        Written out here for :meth:`_request_roll`'s reason — this window owns its
        history, so the view's own off-air fallback stands down.
        """
        if self._bridge.post_note(text):
            return
        self._offline_seq -= 1
        self._history.add_roll(
            {
                "seq": self._offline_seq,
                "player_name": self._name_of_gm(),
                "kind": KIND_NOTE,
                "text": text,
            }
        )

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
        # A deleted creature is in no squad any more, in any session.
        if npc_groups.group_of(self._npc_groups, name) is not None:
            self._npc_groups = npc_groups.leave(self._npc_groups, name)
            self._persist_groups()
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
        # The cards have all just shed (or regrown) a 96px portrait, which is the
        # one thing that moves the block's width.
        self._sync_card_widths()
        self._refresh_collapse_all()

    def _sync_card_widths(self) -> None:
        """Give every card in a block one width: the widest any card asked for,
        capped at what the block can actually give it.

        A card knows what *it* needs; only the block knows what its columns need.
        The wrapping :class:`~mm_companion.ui.flow_layout.FlowLayout` lays items out
        at their own size hint, so cards of differing widths would not line up, and
        would re-flow every time a pin was added.

        The **cap** is the block's own width. Without it a block dragged narrower
        than one card grew a scrollbar and the GM read their mooks through a
        letterbox. The pinned strip gives up its slack first (down to
        ``gm.pin-strip.min``: a narrower strip elides its captions and loses least),
        then the body follows. Past that, the block scrolls.

        Measured per block: the NPC grid and the player roster have no columns in
        common, and a board of pinless players should not be held open by a GM's
        fattest mook. It is also what makes **Collapse all** narrow the board: with
        every card's portrait shed at once, the shared width falls with them.
        """
        npc_cards = [e.card for e in self._npc_state.values() if e.card is not None]
        for container, cards in (
            (self._npc_container, npc_cards),
            (self._cards_container, list(self._cards.values())),
        ):
            if not cards:
                continue
            # A group's rows share the board's tile width, but not the loose cards'
            # body width: a group takes a line of the board to itself.
            loose = [card for card in cards if not getattr(card, "row", False)]
            strip = max(card.pin_width_hint() for card in cards)
            # How a card gives way to a narrow block is its own business — a player's
            # strip gives up its slack, an NPC's tiles wrap into fewer columns.
            room = self._card_room(container)
            if loose:
                body = max(card.body_width_hint() for card in loose)
                for card in loose:
                    card.apply_width(body, strip, room)
            if container is self._npc_container:
                for group_card in self._group_cards.values():
                    group_card.apply_width(strip, room)

    @staticmethod
    def _card_room(container) -> int:
        """The width one card may occupy, measured from the **block's viewport**.

        Not from the container, which is the trap: a card is a fixed width, so it
        sets the flow's minimum, so the block's scroll area gives the flow exactly
        that — and asking the container how wide it is returns the width the card
        forced in the first place, and the cap never bites (it did not, until this
        read the viewport).

        Zero before the block is realized, which is read as "no cap yet": the first
        paint uses the cards' own hints and the real answer arrives on the first
        ``widthChanged``.
        """
        areas = enclosing_scroll_areas(container)
        if not areas:
            return 0
        layout = container.layout()
        margins = layout.contentsMargins() if layout is not None else None
        inset = (margins.left() + margins.right()) if margins is not None else 0
        # The innermost is the block's own; anything further out is the page.
        area = areas[0]
        if area.viewport().width() <= 0:
            return 0
        # Measured as though the vertical scroll bar were always showing, rather than
        # off the viewport as it stands. Otherwise the answer feeds itself: a card
        # that collapses makes the block short enough to drop its scroll bar, which
        # widens the viewport, which widens every card — and the next taller card
        # brings the bar back. A few pixels of slack is the price of one answer.
        bar = 0
        if area.verticalScrollBarPolicy() != Qt.ScrollBarPolicy.ScrollBarAlwaysOff:
            bar = area.verticalScrollBar().sizeHint().width()
        width = area.width() - 2 * area.frameWidth() - bar
        return max(0, width - inset)

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
        self._sync_card_widths()
        self._refresh_collapse_all()

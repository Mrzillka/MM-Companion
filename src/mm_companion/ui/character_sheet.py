"""The character sheet: the registry's blocks on a scrollable, free-form canvas.

The sheet is a scrolling page: a :class:`QScrollArea` hosting a
:class:`~mm_companion.ui.block_canvas.BlockCanvas` that arranges the blocks the
registry supplies (Base Information, Abilities, Resistances, Conditions,
Advantages, Complications, Skills, Powers, …), plus the ones it declares
``default_pinned`` — the Dice block — parked in the strip beside the page. The user
can drag a block to reorder it, put blocks side by side, tear one out into its
own window, and drag that window back to re-dock it — all while the whole page
scrolls vertically and each block shows its full content (no per-block scroll).

It owns the shared :class:`Character` model that the blocks read and write, and
recomputes derived values (spent power points) whenever a block reports a build
change. The cross-block wiring (abilities feed skills and resistances, powers
feed the enhanced totals, the build facts re-derive the power cards) runs over a
topic :class:`~mm_companion.ui.blocks.bus.SignalBus`: each block's descriptor
declares the topics it publishes and subscribes, and this sheet connects them
generically, so the wiring keeps working across floated windows and a mod block
plugs in without editing this module. Emits :attr:`edited` on any user edit so a
host window can track unsaved changes.
"""

from __future__ import annotations

import json
from dataclasses import replace

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QScrollArea, QVBoxLayout, QWidget

from mm_companion.core.character import Character
from mm_companion.core.data_loader import GameData, load_game_data
from mm_companion.core.rules import power_points_spent
from mm_companion.ui.block_canvas import BlockCanvas
from mm_companion.ui.block_frame import BlockFrame
from mm_companion.ui.blocks import (
    INSTANCE_SEPARATOR,
    SignalBus,
    block_descriptors,
    default_pin_lines,
    default_rows,
    instance_key,
    instance_template,
    sync_declarative_blocks,
)
from mm_companion.ui.blocks.bus import (
    BUILD_CHANGED,
    EDITED,
    PIN_REQUESTED,
    QUIET_REQUESTS,
    RESEED_TOPICS,
    UNPIN_REQUESTED,
)
from mm_companion.ui.pinned_panel import PinnedBoard


class CharacterSheet(QWidget):
    """Scrollable, free-form canvas of the sheet's blocks over a shared model."""

    edited = Signal()
    #: A block's row was right-clicked and pinned. Carries a
    #: :class:`~mm_companion.core.rules.pins.PinRef`, and goes *out* of the sheet —
    #: what it names belongs on a GM card, which is not part of this window. The
    #: sheet is the only thing serving ``pin-requested``, so the topic reaches here
    #: whichever block raised it and a mod block joins on the same terms.
    pinRequested = Signal(object)
    #: The same, for a row that was already pinned.
    unpinRequested = Signal(object)

    def __init__(
        self,
        data: GameData | None = None,
        character: Character | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._data = data or load_game_data()
        self.character = character or Character.new_default(self._data)
        self._npc = False
        self._locked = False
        # True only while :meth:`reseed` is pushing a restored model back into the
        # widgets. Putting an earlier state back is not a user edit, so the sheet
        # must not report one while it happens.
        self._restoring = False
        self._undo = None

        # Register any data-described (declarative) blocks the active mods contribute
        # via blocks.json, so they join the registry before we iterate it below.
        sync_declarative_blocks(self._data)

        # Build every block from the registry (single source of truth for the block
        # set). Each block is exposed as an attribute under its key (self.abilities,
        # self.skills, …) so the cross-block wiring can reach it by name. The
        # descriptor carries the dock title and size constraints; its default_row/col
        # feed the canvas's default arrangement.
        self._descriptors = block_descriptors()
        self._sections_by_key: dict[str, QWidget] = {}
        # Every descriptor the sheet is currently running, keyed by block. Not the
        # same list as the registry's: a multi-instance block (Notes) has one
        # registered *template* and as many live descriptors as the user has made
        # blocks, and those extras exist only here.
        self._descriptor_by_key = {d.key: d for d in self._descriptors}
        panels = []
        sizes = {}
        for descriptor in self._descriptors:
            section = descriptor.factory(self._data, self.character)
            setattr(self, descriptor.key, section)
            self._sections_by_key[descriptor.key] = section
            panels.append((descriptor.key, descriptor.title, section))
            sizes[descriptor.key] = descriptor.size
        self._canvas = BlockCanvas(
            panels,
            sizes,
            default_rows(),
            default_pinned=default_pin_lines(),
            instance_factory=self._build_instance,
        )

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._scroll.setWidget(self._canvas)
        self._canvas.set_scroll_area(self._scroll)

        # The scrolling page and the pinned strip beside it. The strip starts with
        # whatever the registry declared `default_pinned` — the Dice block — and
        # collapses to a thin bar with one icon once the user unpins everything.
        self._board = PinnedBoard(self._scroll, self._canvas)
        self._canvas.set_pinned_board(self._board)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._board)

        # Pin the page's minimum width to the widest docked row so it can never
        # shrink narrow enough to clip a block (the fixed-width Abilities /
        # Resistances grids can't compress). Recomputed whenever the arrangement
        # changes — floating or hiding a block frees up the constraint.
        # The block a tab was just split into, while its drag is still in flight.
        self._split_key: str | None = None
        self._canvas.merge_requested.connect(self._on_merge_requested)
        self._canvas.arrangement_changed.connect(self._update_min_width)
        self._update_min_width()

        self._wire_sections()

    def _update_min_width(self) -> None:
        """Track the widest docked row as the page's minimum width."""
        extent = self._scroll.verticalScrollBar().sizeHint().width()
        self._scroll.setMinimumWidth(self._canvas.content_minimum_width() + extent + 2)

    # -- layout model / persistence -----------------------------------------

    def block_keys(self) -> list[str]:
        """Every block key, in construction order."""
        return self._canvas.block_keys()

    def block_frame(self, key: str) -> BlockFrame:
        """The :class:`BlockFrame` wrapping the block *key* (size constraints live here)."""
        return self._canvas.block_frame(key)

    def page_scroll_area(self) -> QScrollArea:
        """The outer page scroll area (the wheel guard redirects the wheel here)."""
        return self._scroll

    def float_block(self, key: str) -> None:
        """Tear a block out into its own window."""
        self._canvas.float_block(key)

    def dock_block(self, key: str, row: int, slot: int, new_row: bool = False) -> None:
        """Dock a block into the arrangement at (row, slot)."""
        self._canvas.dock_block(key, row, slot, new_row=new_row)

    def pin_block(
        self, key: str, line: int | None = None, slot: int = 0, new_line: bool = True
    ) -> None:
        """Park a block in the strip that doesn't scroll with the page.

        Defaults to a new line at the end of the strip; pass a line and slot to put
        it beside a block already there.
        """
        self._canvas.pin_block(key, line, slot, new_line=new_line)

    def unpin_block(self, key: str) -> None:
        """Send a pinned block back onto the scrolling page."""
        self._canvas.unpin_block(key)

    def is_block_pinned(self, key: str) -> bool:
        return self._canvas.is_pinned(key)

    def show_block(self, key: str) -> None:
        self._canvas.show_block(key)

    def hide_block(self, key: str) -> None:
        self._canvas.hide_block(key)

    def is_block_hidden(self, key: str) -> bool:
        return self._canvas.is_hidden(key)

    def arrangement(self) -> dict:
        """The current arrangement as a plain dict (rows / floating / hidden)."""
        return self._canvas.arrangement()

    @property
    def canvas(self) -> BlockCanvas:
        return self._canvas

    @property
    def board(self) -> PinnedBoard:
        """The page-plus-pinned-strip container the canvas renders into."""
        return self._board

    @property
    def bus(self) -> SignalBus:
        """The topic bus carrying the cross-block reactivity."""
        return self._bus

    def save_layout(self) -> str:
        """The block arrangement as a JSON string (for settings.json)."""
        return json.dumps(self._canvas.arrangement())

    def restore_layout(self, state: str | None) -> bool:
        """Restore an arrangement saved by :meth:`save_layout`.

        Returns whether it applied — a missing, malformed, or incompatible state
        returns False so the caller keeps the default arrangement.
        """
        if not state:
            return False
        try:
            model = json.loads(state)
        except (ValueError, TypeError):
            return False
        return self._canvas.apply_arrangement(model)

    def reset_layout(self) -> None:
        """Return the blocks to the default arrangement (un-float and un-hide)."""
        self._canvas.reset()

    # -- multi-instance blocks ----------------------------------------------

    def multi_templates(self) -> list:
        """The registered descriptors a second copy can be made of (Notes)."""
        return [d for d in block_descriptors() if d.multi]

    def add_block_instance(self, template: str, *, near: str | None = None) -> str:
        """Build another copy of the multi-instance block *template*.

        Returns the new block's key. The number is the first one free rather than
        one past the highest, so closing the middle of three and adding a fourth
        reuses the gap instead of counting forever.
        """
        descriptor = next((d for d in self.multi_templates() if d.key == template), None)
        if descriptor is None:
            raise KeyError(f"{template!r} is not a multi-instance block")
        key = self._free_instance_key(template)
        title = f"{descriptor.title} {int(key.split(INSTANCE_SEPARATOR)[-1])}"
        self._install_instance(descriptor, key, title, near=near)
        return key

    def remove_block_instance(self, key: str) -> None:
        """Destroy a multi-instance block. The first instance is never removed.

        The template's own key is the block every other sheet has, and closing it
        is what the View menu's checkbox is for; only the copies a user made are
        destroyable, which is also what keeps the default arrangement coherent.
        """
        if instance_template(key) == key or key not in self._sections_by_key:
            return
        section = self._sections_by_key.pop(key)
        self._descriptor_by_key.pop(key, None)
        self._descriptors = [d for d in self._descriptors if d.key != key]
        flush = getattr(section, "flush", None)
        if callable(flush):
            flush()  # land any unwritten note before the widget goes
        self._canvas.remove_block(key)
        # The block is gone, so its entry in the model is nobody's. Left behind it
        # would be an empty NotesState the next instance to reuse this key would
        # inherit — and `notes#2` is reused as soon as the middle of three closes.
        self.character.notes.pop(key, None)
        section.setParent(None)
        section.deleteLater()
        attribute = key.replace(INSTANCE_SEPARATOR, "_")
        if hasattr(self, attribute):
            delattr(self, attribute)

    def _free_instance_key(self, template: str) -> str:
        number = 2
        while instance_key(template, number) in self._sections_by_key:
            number += 1
        return instance_key(template, number)

    def _build_instance(self, key: str):
        """The canvas's hook: build the block a restored layout names.

        Answers ``None`` for a key whose template is not a live multi-instance
        block, which is what makes a layout naming a block this version no longer
        has fail validation and fall back to the default rather than half-apply.
        """
        template = instance_template(key)
        descriptor = next((d for d in self.multi_templates() if d.key == template), None)
        if descriptor is None or key in self._sections_by_key:
            return None
        number = key.split(INSTANCE_SEPARATOR)[-1]
        title = f"{descriptor.title} {number}" if number.isdigit() else descriptor.title
        section = self._make_instance_section(descriptor, key)
        return title, section, descriptor.size

    def _make_instance_section(self, descriptor, key: str):
        """Build and register one instance's section, wired onto the bus."""
        section = descriptor.instance_factory(key)(self._data, self.character)
        setattr(self, key.replace(INSTANCE_SEPARATOR, "_"), section)
        self._sections_by_key[key] = section
        instance = replace(descriptor, key=key)
        self._descriptor_by_key[key] = instance
        self._descriptors.append(instance)
        self._wire_section(instance)
        section.set_locked(self._locked)
        return section

    def _install_instance(self, descriptor, key: str, title: str, *, near: str | None) -> None:
        section = self._make_instance_section(descriptor, key)
        self._canvas.add_block(key, title, section, descriptor.size, near=near)

    # -- merging and splitting ----------------------------------------------

    def _connect_instance(self, section) -> None:
        """Wire a block's split gesture, if it has one.

        Called from :meth:`_wire_section` so the block built at startup and the
        copies made later go through one path — connecting only the copies is
        exactly the bug where a tab could be dragged out of every Notes block but
        the first. Duck-typed like every other fan-out here, so a mod's
        multi-instance block joins on the same terms Notes does.
        """
        for name, handler in (
            ("splitRequested", self._on_split_requested),
            ("splitMoved", self._on_split_moved),
            ("splitReleased", self._on_split_released),
        ):
            signal = getattr(section, name, None)
            if signal is not None:
                signal.connect(lambda *args, s=section, h=handler: h(s, *args))

    def _on_split_requested(self, section, ref: str, global_pos) -> None:
        """A tab was dragged off its bar: give it a block of its own, mid-drag.

        The new block is floated under the cursor and handed to the canvas's
        ordinary drag controller, so the rest of the gesture is a block drag like
        any other — it can be docked into a row, pinned to the strip, dropped
        onto another Notes block to merge, or just let go to stay floating.
        """
        key = self._key_of(section)
        if key is None:
            return
        new_key = self.add_block_instance(instance_template(key), near=key)
        self._sections_by_key[new_key].adopt([ref], active=ref)
        section.release(ref)
        self._split_key = new_key
        self._canvas.adopt_drag(new_key, global_pos)

    def _on_split_moved(self, _section, global_pos) -> None:
        if self._split_key is not None:
            self._canvas.title_bar_moved(self._split_key, global_pos)

    def _on_split_released(self, _section, global_pos) -> None:
        key, self._split_key = self._split_key, None
        if key is not None:
            self._canvas.title_bar_released(key, global_pos)

    def _on_merge_requested(self, source: str, target: str) -> None:
        """A block was dropped onto another: move its content over and drop it.

        The canvas asks rather than does, because the *sections* are the sheet's:
        it knows nothing about tabs, and this is the only place that does.
        """
        giver = self._sections_by_key.get(source)
        taker = self._sections_by_key.get(target)
        if giver is None or taker is None or giver is taker:
            return
        refs = list(giver.open_refs())
        for ref in refs:
            giver.release(ref)
        taker.adopt(refs, active=refs[-1] if refs else "")
        # The template's own key is the block every sheet has and every layout
        # names, so it stays where the drop put it, now empty; only a copy the
        # user made is destroyed by being merged away.
        if instance_template(source) != source:
            self.remove_block_instance(source)

    def _key_of(self, section) -> str | None:
        for key, candidate in self._sections_by_key.items():
            if candidate is section:
                return key
        return None

    # -- signal wiring -------------------------------------------------------

    def _wire_sections(self) -> None:
        """Wire the cross-block reactivity over the topic signal bus.

        Every block's descriptor declares which of its Qt signals publish which
        bus topics (``publishes``) and which topics route to which of its methods
        (``subscribes``); this loop connects the two, so the whole web is data on
        the descriptors rather than hand-wired here. A mod block joins it just by
        declaring the same tables — no edit to this method. The sheet subscribes
        its own two build-wide concerns. See :mod:`mm_companion.ui.blocks.bus` for
        the topic table and the exact fan-out it reproduces.
        """
        self._bus = SignalBus()
        # Sheet-level subscribers: recompute spent power points on any build change,
        # and surface any user edit for unsaved-change tracking. (Toggling a power
        # on/off publishes BUILD_CHANGED but not EDITED, so a runtime toggle
        # re-derives without marking the character dirty.)
        self._bus.subscribe(BUILD_CHANGED, self._recompute_derived)
        self._bus.subscribe(EDITED, self._relay_edited)

        for descriptor in self._descriptors:
            self._wire_section(descriptor)

        # A request is no use to a block the user has closed, so the sheet reveals
        # whichever block answers it. Named by *what it serves*, not by its key, so
        # a mod that ships its own roller is revealed on the same terms. A quiet
        # topic is exempt: it is raised as a side effect of something else the user
        # was doing, and would open a window they never asked for (see bus.py).
        # The requests no *block* answers: a pin's destination is a GM card, so
        # the sheet takes them and passes them on to whoever opened the sheet.
        self._bus.serve(PIN_REQUESTED, self.pinRequested.emit)
        self._bus.serve(UNPIN_REQUESTED, self.unpinRequested.emit)

        for topic in {t for d in self._descriptors for t in d.serves} - QUIET_REQUESTS:
            self._bus.serve(topic, lambda _payload, t=topic: self._reveal_servers(t))

        # No block emits at construction (each seeds its own view from the model as
        # it is built), so seed the one build-wide readout the sheet owns — the
        # spent-power-points pool label — once now.
        self._recompute_derived()

    def _wire_section(self, descriptor) -> None:
        """Connect one block's four bus tables.

        Its own method rather than the body of the constructor's loop, because a
        multi-instance block joins the bus at *runtime* — a Notes block added from
        the View menu has to be wired exactly as one built at startup, and one
        copy of this is the only way those cannot drift.
        """
        section = self._sections_by_key[descriptor.key]
        for signal_name, topics in descriptor.publishes.items():
            signal = getattr(section, signal_name)
            for topic in topics:
                signal.connect(self._bus.make_publisher(topic))
        for topic, method_name in descriptor.subscribes.items():
            self._bus.subscribe(topic, getattr(section, method_name))
        # The payload channel: the same two tables, one topic further out.
        for signal_name, topics in descriptor.requests.items():
            signal = getattr(section, signal_name)
            for topic in topics:
                signal.connect(self._bus.make_requester(topic))
        for topic, method_name in descriptor.serves.items():
            self._bus.serve(topic, getattr(section, method_name))
        self._connect_instance(section)

    def _reveal_servers(self, topic: str) -> None:
        """Make sure every block serving *topic* is somewhere the user can see it.

        A closed block is only hidden, never destroyed, so a request to it still
        works — it just happens off screen, which reads as the app ignoring the
        click. Reopening it is the honest answer. A block floated into its own
        window is raised instead, since it is already visible in principle but may
        be behind the sheet.
        """
        for descriptor in self._descriptors:
            if topic not in descriptor.serves:
                continue
            if self._canvas.is_hidden(descriptor.key):
                self._canvas.show_block(descriptor.key)
            window = self._canvas.block_window(descriptor.key)
            if window is not None:
                window.raise_()

    def _sections(self) -> tuple:
        return tuple(self._sections_by_key.values())

    def _recompute_derived(self) -> None:
        """Refresh values the model derives from the build (spent power points).

        NPCs carry no point budget, so the sheet skips the spent-PP total for them
        and refreshes the System block's estimated Power Level (from traits) instead.
        """
        if self._npc:
            self.system_info.refresh_estimated_pl()
        else:
            spent = power_points_spent(self.character, self._data)
            self.system_info.set_pool_current("power_points", spent)

    @property
    def locked(self) -> bool:
        """Whether the sheet is in read-only view mode."""
        return self._locked

    def set_locked(self, locked: bool) -> None:
        """Toggle read-only view mode across every block (incl. floated ones)."""
        self._locked = locked
        for section in self._sections():
            section.set_locked(locked)

    def release_roller(self) -> tuple[QWidget, QWidget] | None:
        """Lend the dice roller out to a compact window, or ``None`` if there is none.

        The sheet is what a window can see, so it is the sheet that answers "give me
        your roller" — but it does not name the Dice block to do it. Duck-typed over
        the blocks like :meth:`sync_session`, first one wins; a sheet whose roller
        block a mod replaced (or a user closed the feature out of) answers ``None``
        and the window simply has no compact mode.
        """
        for section in self._sections():
            handler = getattr(section, "release_roller", None)
            if callable(handler):
                return handler()
        return None

    def restore_roller(self) -> None:
        """Take the dice roller back into its block."""
        for section in self._sections():
            handler = getattr(section, "restore_roller", None)
            if callable(handler):
                handler()
                return

    def compact_anchor(self) -> QWidget | None:
        """The widget compact mode's shrink button floats over, or ``None``.

        The third of the duck-typed set above, and swept for the same way: the
        sheet does not name the Dice block, it asks each block in turn, and a
        sheet whose roller a mod replaced with something that offers none answers
        ``None`` — no button, and no compact mode, exactly as lending no roller
        already meant.

        Closing the Dice block from the View menu is not that case and needs no
        code: the button is a child of the roller, so it goes wherever the roller
        goes, including out of sight. Which is the honest answer too — compact
        mode would have nothing to show.
        """
        for section in self._sections():
            handler = getattr(section, "compact_anchor", None)
            if callable(handler):
                return handler()
        return None

    def suspend_windows(self, suspended: bool) -> None:
        """Take this sheet's floated block windows off the screen, or bring them back.

        What compact mode asks for on the way in and out: the blocks pinned above
        other applications stay, the rest go with the sheet — and since on top is
        the default, in practice that usually means they all stay. See
        :meth:`~mm_companion.ui.block_canvas.BlockCanvas.set_windows_suspended`.
        """
        self._canvas.set_windows_suspended(suspended)

    def _relay_edited(self) -> None:
        """Surface a block's edit — unless the sheet is the one that caused it.

        The one chokepoint that catches every block *and* any mod block: six of the
        base sections carry no ``_loading`` guard of their own, and a block's widgets
        write the model and re-emit as they are re-seeded.
        """
        if not self._restoring:
            self.edited.emit()

    @property
    def undo(self):
        """This sheet's undo controller, or ``None`` — see :mod:`mm_companion.ui.undo`.

        Held here rather than on the window because the two things that push changes
        in from outside (a GM's session command, a replayed NPC damage rung) hold the
        *sheet*, never the window that owns it.
        """
        return self._undo

    def set_undo(self, controller) -> None:
        """Give this sheet the controller that records and restores its states."""
        self._undo = controller

    def reseed(self) -> None:
        """Restate every block from the model, which changed underneath them.

        The other half of an undo (see :mod:`mm_companion.ui.undo`): the model has
        already been put back, and this is what makes the widgets agree with it. Two
        passes, and the split between them is the rule for adding a block —

        * a block whose widgets hold model values directly exposes ``reseed()``, found
          by name and duck-typed exactly like :meth:`sync_session`, so a mod block
          joins on the same terms;
        * everything a topic already restates is left to the topics. Powers and
          Equipment have no ``reseed()`` for that reason: their ``refresh()`` *is* the
          ``facts-changed`` handler, and a method here would rebuild the two costliest
          card trees a second time.

        Order-free, and deliberately so: the model is restored first and every refresh
        reads it, so no block depends on another's widgets having been re-seeded yet.
        ``edited`` is suppressed throughout — a restore is not an edit — and
        ``RESEED_TOPICS`` is every notification topic but that one.
        """
        self._restoring = True
        try:
            for section in self._sections():
                handler = getattr(section, "reseed", None)
                if callable(handler):
                    handler()
            self._bus.publish_all(RESEED_TOPICS)
        finally:
            self._restoring = False

    def sync_dice_layout(self) -> None:
        """Re-read the roller's layout preference, fanned out like :meth:`sync_session`."""
        for section in self._sections():
            handler = getattr(section, "sync_dice_layout", None)
            if callable(handler):
                handler()

    def set_pinned(self, refs) -> None:
        """Tell every block which parameters are already on the GM card.

        What turns a row's menu from "Pin to GM card" into "Unpin from GM card".
        Pushed rather than pulled — a block cannot see the card — and pushed again
        on every change, so a sheet left open beside the card never offers to pin
        something that is already there.
        """
        for section in self._sections():
            handler = getattr(section, "set_pinned", None)
            if callable(handler):
                handler(refs)

    def set_pin_target(self, enabled: bool) -> None:
        """Whether this sheet's rows offer "Pin to GM card".

        Off by default, so a player's own sheet is exactly as it was — the action
        would have nowhere to go. A GM window turns it on when it opens a sheet
        from a card, and connects :attr:`pinRequested` to that card's strip.

        Duck-typed and fanned out like :meth:`sync_session`: a mod block that
        offers pinnable rows exposes the same method and is switched with the rest.
        """
        for section in self._sections():
            handler = getattr(section, "set_pin_target", None)
            if callable(handler):
                handler(enabled)

    def sync_session(self) -> None:
        """Tell any block that cares that the session changed (joined, or ended).

        Duck-typed and fanned out like :meth:`set_npc_mode`, so this needs no
        knowledge of *which* blocks care: the Dice block swaps its private roll
        history for the table's shared one, and a mod block can join in by exposing
        the same method. Called by
        :func:`~mm_companion.ui.session_player.attach_player_session` at both ends
        of a session — the blocks are built with the sheet, so nothing about them is
        re-shown when a player joins a table.
        """
        for section in self._sections():
            handler = getattr(section, "sync_session", None)
            if callable(handler):
                handler()

    def set_npc_mode(self, npc: bool) -> None:
        """Simplify the sheet for a GM's NPC: no point budget, an estimated PL.

        Fanned out after construction, the same way :meth:`set_locked` is — the
        blocks come from the registry and are built from ``(data, character)``
        alone, so a mode is something the sheet turns on afterwards rather than a
        constructor argument threaded through every factory.
        """
        self._npc = npc
        self.system_info.set_npc_mode(npc)
        for section in self._sections():
            setter = getattr(section, "set_npc_mode", None)
            if callable(setter) and section is not self.system_info:
                setter(npc)
        self._recompute_derived()

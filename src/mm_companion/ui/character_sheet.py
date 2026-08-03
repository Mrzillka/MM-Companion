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

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QScrollArea, QVBoxLayout, QWidget

from mm_companion.core.character import Character
from mm_companion.core.data_loader import GameData, load_game_data
from mm_companion.core.rules import power_points_spent
from mm_companion.ui.block_canvas import BlockCanvas
from mm_companion.ui.block_frame import BlockFrame
from mm_companion.ui.blocks import (
    SignalBus,
    block_descriptors,
    default_pin_lines,
    default_rows,
    sync_declarative_blocks,
)
from mm_companion.ui.blocks.bus import BUILD_CHANGED, EDITED, PIN_REQUESTED, QUIET_REQUESTS
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
        panels = []
        sizes = {}
        for descriptor in self._descriptors:
            section = descriptor.factory(self._data, self.character)
            setattr(self, descriptor.key, section)
            self._sections_by_key[descriptor.key] = section
            panels.append((descriptor.key, descriptor.title, section))
            sizes[descriptor.key] = descriptor.size
        self._canvas = BlockCanvas(
            panels, sizes, default_rows(), default_pinned=default_pin_lines()
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
        self._bus.subscribe(EDITED, self.edited.emit)

        for descriptor in self._descriptors:
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

        # A request is no use to a block the user has closed, so the sheet reveals
        # whichever block answers it. Named by *what it serves*, not by its key, so
        # a mod that ships its own roller is revealed on the same terms. A quiet
        # topic is exempt: it is raised as a side effect of something else the user
        # was doing, and would open a window they never asked for (see bus.py).
        # The one request no *block* answers: a pin's destination is a GM card, so
        # the sheet takes it and passes it on to whoever opened the sheet.
        self._bus.serve(PIN_REQUESTED, self.pinRequested.emit)

        for topic in {t for d in self._descriptors for t in d.serves} - QUIET_REQUESTS:
            self._bus.serve(topic, lambda _payload, t=topic: self._reveal_servers(t))

        # No block emits at construction (each seeds its own view from the model as
        # it is built), so seed the one build-wide readout the sheet owns — the
        # spent-power-points pool label — once now.
        self._recompute_derived()

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

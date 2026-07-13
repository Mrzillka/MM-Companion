"""The character sheet: seven blocks on a scrollable, free-form canvas.

The sheet is a scrolling page: a :class:`QScrollArea` hosting a
:class:`~mm_companion.ui.block_canvas.BlockCanvas` that arranges the seven blocks
(Base Information, Abilities, Resistances, Conditions, Advantages, Skills, Powers).
The user
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
    default_rows,
    sync_declarative_blocks,
)
from mm_companion.ui.blocks.bus import BUILD_CHANGED, EDITED


class CharacterSheet(QWidget):
    """Scrollable, free-form canvas of the sheet's seven blocks over a shared model."""

    edited = Signal()

    def __init__(
        self,
        data: GameData | None = None,
        character: Character | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._data = data or load_game_data()
        self.character = character or Character.new_default(self._data)

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
        self._canvas = BlockCanvas(panels, sizes, default_rows())

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._scroll.setWidget(self._canvas)
        self._canvas.set_scroll_area(self._scroll)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._scroll)

        self._wire_sections()

    # -- layout model / persistence -----------------------------------------

    def block_keys(self) -> list[str]:
        """The seven block keys, in construction order."""
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

        # No block emits at construction (each seeds its own view from the model as
        # it is built), so seed the one build-wide readout the sheet owns — the
        # spent-power-points pool label — once now.
        self._recompute_derived()

    def _sections(self) -> tuple:
        return tuple(self._sections_by_key.values())

    def _recompute_derived(self) -> None:
        """Refresh values the model derives from the build (spent power points)."""
        spent = power_points_spent(self.character, self._data)
        self.system_info.set_pool_current("power_points", spent)

    def set_locked(self, locked: bool) -> None:
        """Toggle read-only view mode across every block (incl. floated ones)."""
        for section in self._sections():
            section.set_locked(locked)

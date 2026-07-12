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
feed the enhanced totals, the build facts re-derive the power cards) works across
windows unchanged — Qt signals don't care which window a block lives in. Emits
:attr:`edited` on any user edit so a host window can track unsaved changes.
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
from mm_companion.ui.blocks import block_descriptors, default_rows


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

        # Build every block from the registry (single source of truth for the block
        # set). Each block is exposed as an attribute under its key (self.abilities,
        # self.skills, …) so the cross-block wiring can reach it by name. The
        # descriptor carries the dock title and size constraints; its default_row/col
        # feed the canvas's default arrangement.
        descriptors = block_descriptors()
        self._sections_by_key: dict[str, QWidget] = {}
        panels = []
        sizes = {}
        for descriptor in descriptors:
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
        """Reproduce the cross-block wiring; see the class docstring for the shape."""
        # Skill totals follow the ability spin boxes.
        self.skills.set_ability_values(self.abilities.ability_values())
        self.abilities.abilityChanged.connect(self.skills.set_ability_value)
        # A moved ability re-seeds the resistances derived from it.
        self.abilities.abilityChanged.connect(lambda *_: self.resistances.follow_ability_change())

        # Recompute derived values whenever any block reports a build change. The
        # name/details and image blocks don't touch the build, so they carry no
        # `changed` signal.
        for section in self._sections():
            if hasattr(section, "changed"):
                section.changed.connect(self._recompute_derived)
        self._recompute_derived()

        # A power change can add or drop a trait boost, so refresh the enhanced
        # ability/resistance totals and the skill totals that read them.
        self.powers.changed.connect(self.abilities.refresh_enhancements)
        self.powers.changed.connect(self.resistances.refresh_enhancements)
        self.powers.changed.connect(self.skills.refresh_totals)

        # A condition change overlays penalties on the stat rows (a scoped Impaired on
        # a skill, Hit on Toughness, a halved defense), so refresh the same views. A
        # Debilitated condition can also name an advantage or a power, which those
        # sections strike through as effectively lost.
        self.conditions.conditionsChanged.connect(self.abilities.refresh_enhancements)
        self.conditions.conditionsChanged.connect(self.resistances.refresh_enhancements)
        self.conditions.conditionsChanged.connect(self.skills.refresh_totals)
        self.conditions.conditionsChanged.connect(self.advantages.refresh_conditions)
        self.conditions.conditionsChanged.connect(self.powers.refresh)

        # And the reverse: a power's displayed numbers derive from character facts,
        # so editing an ability/resistance/advantage or the Power Level re-derives
        # the power cards. `refresh` only reads the model, so it never loops back.
        self.abilities.changed.connect(self.powers.refresh)
        self.resistances.changed.connect(self.powers.refresh)
        self.advantages.changed.connect(self.powers.refresh)
        self.system_info.changed.connect(self.powers.refresh)
        # A power can link a Close/Ranged Combat focus as its attack skill, so a skill
        # rank/mod edit re-derives that power's attack bonus and PL check.
        self.skills.changed.connect(self.powers.refresh)
        # The Heroic-advantage budget is floor(PL/2), so a Power Level edit reshapes
        # the advantage rank caps and the budget display.
        self.system_info.changed.connect(self.advantages.refresh_limits)

        # The system block's derived readouts (speed, initiative, effective size) read
        # abilities, advantages, and active powers, so re-derive them when any change.
        self.abilities.abilityChanged.connect(lambda *_: self.system_info.refresh_derived())
        self.abilities.changed.connect(self.system_info.refresh_derived)
        self.advantages.changed.connect(self.system_info.refresh_derived)
        self.powers.changed.connect(self.system_info.refresh_derived)
        self.conditions.conditionsChanged.connect(self.system_info.refresh_derived)

        # Surface any user edit for unsaved-change tracking. The stats/skills
        # `changed` signals already fire on every edit; base_info (name), the image,
        # system fields, and conditions have edits that don't affect the build, so they
        # carry `edited`.
        self.base_info.edited.connect(self.edited)
        self.character_image.edited.connect(self.edited)
        self.system_info.edited.connect(self.edited)
        self.conditions.edited.connect(self.edited)
        self.abilities.changed.connect(self.edited)
        self.resistances.changed.connect(self.edited)
        self.advantages.changed.connect(self.edited)
        self.skills.changed.connect(self.edited)
        self.powers.changed.connect(self.edited)

        # Toggling a power on/off is runtime state, not part of the point build, and is
        # not persisted — so it drives the same live-refresh fan-out as `powers.changed`
        # above (a trait boost drops in or out of the sheet) but deliberately does *not*
        # connect to `edited`, so it never marks the character dirty.
        self.powers.runtimeChanged.connect(self._recompute_derived)
        self.powers.runtimeChanged.connect(self.abilities.refresh_enhancements)
        self.powers.runtimeChanged.connect(self.resistances.refresh_enhancements)
        self.powers.runtimeChanged.connect(self.skills.refresh_totals)
        self.powers.runtimeChanged.connect(self.system_info.refresh_derived)

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

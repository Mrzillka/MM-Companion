"""The Dice block: the d20 roller, on the sheet rather than in its own window.

Rolling dice used to mean a separate top-level window opened from ``Tools ▸ Dice
Roller…``. It is a sheet block now, which means it gets everything the other
blocks get for free — drag to reorder, pop out into its own window, hide and
reopen from the View menu — and, most of the point, it can be **parked in the
pinned strip**, the one region that does not scroll with the page. Its descriptor
sets ``default_pinned``, so a fresh sheet opens with the die already beside the
page instead of scrolling away under it.

The block is a thin wrapper: all of the behaviour is
:class:`~mm_companion.ui.dice_roller.DiceRollerView` (the roll column plus the
private history, swapped for the table's shared one in a session), stacked
vertically to suit a narrow strip.

Two things about it are deliberately unlike its neighbours:

* It is **not a view over the character**. It takes the usual
  ``(data, character)`` block arguments and uses neither: the roller drives
  :mod:`mm_companion.core.dice` directly and keeps its quick rolls in
  :mod:`mm_companion.core.storage`, so there is nothing of the character in it.
  It therefore publishes nothing on the bus — a roll is not an edit and must
  never mark the sheet dirty. It does **serve** one topic, though: every other
  block sends what it wants rolled to :meth:`DiceSection.perform_roll` over the
  bus's payload channel, which is the one thing here that faces the sheet.
* :meth:`DiceSection.set_locked` does nothing. Rolling is a mid-play action, not
  a build edit, so it stays available in the locked read-only view — the same
  reasoning that keeps a power's on/off switch live there.
"""

from __future__ import annotations

from PySide6.QtWidgets import QGroupBox, QVBoxLayout, QWidget

from mm_companion.core.character import Character
from mm_companion.core.data_loader import GameData
from mm_companion.core.rules import RollSpec
from mm_companion.ui.dice_roller import DiceRollerView
from mm_companion.ui.sections.titled_section import strip_groupbox_caption


class DiceSection(QGroupBox):
    """A d20 roller as a sheet block (see the module docstring)."""

    def __init__(self, data: GameData, character: Character, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        strip_groupbox_caption(self)
        # Accepted for the uniform block contract and unused: the roller owns no
        # character state.
        self._data = data
        self._character = character

        self.view = DiceRollerView()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.view)

    @property
    def panel(self):
        """The roll column itself (settings, die, readout, quick rolls)."""
        return self.view.panel

    def perform_roll(self, spec: object) -> None:
        """Roll what another block asked for — the ``roll-requested`` topic's handler.

        Anything that isn't a :class:`~mm_companion.core.rules.RollSpec` is ignored
        rather than raising: the payload comes off a bus a mod block can publish on
        too, and a bad payload should cost a roll, not the sheet.
        """
        if isinstance(spec, RollSpec):
            self.view.panel.roll_spec(spec)

    def sync_session(self) -> None:
        """Re-check whether there is a session, and show the matching history.

        The sheet fans this out (see
        :meth:`~mm_companion.ui.character_sheet.CharacterSheet.sync_session`) at
        both ends of a session, since this block is built with the sheet and is
        never re-shown when a player joins a table.
        """
        self.view.sync_session()

    def set_locked(self, locked: bool) -> None:
        """No-op: rolling is a play action, available in the read-only view too."""

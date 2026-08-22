"""The sheet a power's nested character is built on.

A Summon's minion and a Metamorph's alternate form are whole characters
(:mod:`mm_companion.core.rules.subbuilds`), so this is the ordinary character sheet
again — the third window pointed at it, after :class:`~.main_window.MainWindow` and
:class:`~.npc_window.NPCWindow`, and for the same reason as the second: there is
nothing about a minion that is not a character, so a second sheet would be a second
place to fix every bug.

**Two things make it different, and both come from the build being someone else's.**

*It is not saved to a file.* It is a piece of the power that bought it, so File ▸ Save
writes it back into that power rather than to disk, and every edit writes through
anyway — the window never goes dirty and never asks on close. The power is what gets
saved, by the sheet that owns it.

*Its budget is not its own.* The point total and Power Level are handed down by the
power (``rank x 15``, or the wielder's own total) and restamped whenever the build is
reopened, so both rows are shown read-only via
:meth:`~.sections.system_info.SystemInfoSection.set_budget_fixed`. They are still
*shown*: watching the spent-against-budget number is the whole reason this window has a
point pool at all, where an NPC's would be noise.

Deliberately **not** in NPC mode, for exactly that reason — an NPC swaps the pool for an
estimated Power Level, and a minion is the one GM-side character the rules do budget.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QWidget

from mm_companion.core import storage
from mm_companion.core.character import Character
from mm_companion.ui.main_window import MainWindow


class SubBuildWindow(MainWindow):
    """One nested character, edited in place and written back on every change."""

    #: The nested character changed. The owner re-reads ``sheet.character`` and stores it.
    committed = Signal()

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        character: Character,
        label: str = "Sub-build",
    ) -> None:
        # ``npc=True`` for the *menus* only — it drops Session and Cost config, which a
        # character inside a power has no use for. The sheet itself stays in ordinary
        # mode (see the module docstring): the budget row is the point.
        super().__init__(parent, character=character, locked=False, npc=True)
        # The slot's own label ("Minion", "Alternate form 2"): the title line already
        # ends with the build's name, so naming the power that bought it as well would
        # read "Minion of Summon — Minion".
        self.TITLE = label
        self.sheet.system_info.set_budget_fixed(True)
        self._update_title()

    def storage_dir(self) -> Path:
        """Where File ▸ Save As starts — a minion is GM-side material like any NPC."""
        return storage.get_workspace().gm_characters_dir

    def _on_edited(self) -> None:
        """Write the change back into the power instead of going dirty.

        There is nowhere else for it to go: the power holds this build, and a window
        that hoarded edits until Save would lose them to a stray close and would have to
        prompt about a file that does not exist.
        """
        self.committed.emit()
        self._update_title()

    def _save(self) -> bool:
        """File ▸ Save says so out loud; the edits are already in the power."""
        self.committed.emit()
        self.statusBar().showMessage("Saved into the power", 5000)
        return True

"""A GM's NPC: the ordinary character sheet with the accounting taken out.

An NPC *is* a :class:`~mm_companion.core.character.Character` — same model, same
blocks, same rules — so this is deliberately not a second sheet. It is
:class:`~mm_companion.ui.main_window.MainWindow` pointed at the GM's
``gm_characters/`` dir with :meth:`~mm_companion.ui.character_sheet.CharacterSheet.set_npc_mode`
turned on, which swaps the power-point pool for an estimated Power Level. Nobody
budgets points for a thug; what a GM wants to know is roughly how tough the thing
they just wrote actually is.

NPCs open **unlocked**. A saved player character opens read-only because it is
finished and worth protecting; an NPC is working material the GM is usually still
changing, often mid-session. The lock toggle is still on the menu bar.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import QWidget

from mm_companion.core import storage
from mm_companion.core.character import Character
from mm_companion.ui.main_window import MainWindow


class NPCWindow(MainWindow):
    """The simplified sheet, saving to the workspace ``gm_characters/`` dir."""

    TITLE = "NPC"

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        character: Character | None = None,
        path: Path | None = None,
        locked: bool = False,
    ) -> None:
        super().__init__(parent, character=character, path=path, locked=locked, npc=True)
        self.sheet.set_npc_mode(True)

    def storage_dir(self) -> Path:
        """NPCs live apart from the player characters, and are never in the library."""
        return storage.get_workspace().gm_characters_dir

    def _new_child(self, character: Character, path: Path) -> MainWindow:
        """File ▸ Open from an NPC window opens another NPC, not a character sheet."""
        return NPCWindow(character=character, path=path)

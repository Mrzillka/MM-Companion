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

They are also **smaller**: the blocks that hold no trait (see
:func:`~mm_companion.ui.blocks.registry.npc_hidden_keys` — the Dice roller, the
Scene, Notes and Complications) are not built at all, because a GM opening a thug
wants its numbers, not a second dice roller and the Scene board they already have
in the GM window. They used to be built and then closed, which cost a fifth of the
time it took a sheet to open and bought nothing a GM used. A roll clicked on the
sheet goes out through :attr:`~MainWindow.requestUnserved` to whoever opened it —
the GM window's roller, where a card's rolls already land.

And they remember their arrangement under their own settings key. Sharing the
character sheet's ``layout`` key would have meant a mook's missing blocks went
missing from every hero too.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import QWidget

from mm_companion.core import storage
from mm_companion.core.character import Character
from mm_companion.ui.blocks import npc_hidden_keys
from mm_companion.ui.main_window import MainWindow


class NPCWindow(MainWindow):
    """The simplified sheet, saving to the workspace ``gm_characters/`` dir."""

    TITLE = "NPC"

    #: Its own arrangement, not the character sheet's — see the module docstring.
    LAYOUT_KEY = "npc_layout"

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        character: Character | None = None,
        path: Path | None = None,
        locked: bool = False,
        pin_target: bool = False,
    ) -> None:
        super().__init__(
            parent,
            character=character,
            path=path,
            locked=locked,
            npc=True,
            pin_target=pin_target,
            exclude=npc_hidden_keys(),
        )
        self.sheet.set_npc_mode(True)

    def load(self, character: Character, path: Path | None) -> None:
        """Put *character* (saved at *path*) in this window, in place of what it held.

        What lets the GM window keep a sheet built ahead of time (see
        ``GMWindow._prime_spare_npc``): building one is most of the cost of opening a
        creature, and none of it depends on *which* creature. The model is replaced
        the way an undo replaces it — :meth:`Character.restore` keeps the object every
        block holds, and :meth:`CharacterSheet.reseed` restates the blocks from it —
        and then the window forgets everything it knew about the blank one it was:
        the undo history, the dirty flag, the file.
        """
        self.sheet.character.restore(character.to_dict(), keep_runtime=False)
        self.sheet.reseed()
        self._path = Path(path) if path else None
        if self._undo is not None:
            self._undo.reset()
        self._layout_history.rebase()
        self._dirty = False
        self._update_title()

    def storage_dir(self) -> Path:
        """NPCs live apart from the player characters, and are never in the library."""
        return storage.get_workspace().gm_characters_dir

    def _new_child(self, character: Character, path: Path) -> MainWindow:
        """File ▸ Open from an NPC window opens another NPC, not a character sheet."""
        return NPCWindow(character=character, path=path)

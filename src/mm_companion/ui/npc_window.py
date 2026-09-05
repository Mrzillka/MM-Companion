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

They also arrange themselves differently, and remember that arrangement under
their own settings key: the blocks that hold no trait start **closed** here (see
:func:`~mm_companion.ui.blocks.registry.npc_hidden_keys`), because a GM opening a
thug wants its numbers, not a second dice roller and the Scene board they already
have in the GM window. Sharing the character sheet's ``layout`` key would have
meant closing them on a mook also closed them on every hero.
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
        )
        self.sheet.set_npc_mode(True)

    def _restore_layout(self) -> bool:
        """Restore the NPC arrangement, or seed the default one with the prose closed.

        Only when nothing was remembered: once a GM has reopened the roller on an
        NPC, that is the arrangement they get back, exactly as on a character sheet.
        Runs from ``MainWindow.__init__`` before :meth:`set_npc_mode`, which is fine
        — which blocks a GM wants open is not a question about the mode being on.
        """
        restored = super()._restore_layout()
        if not restored:
            for key in npc_hidden_keys():
                self.sheet.hide_block(key)
        return restored

    def storage_dir(self) -> Path:
        """NPCs live apart from the player characters, and are never in the library."""
        return storage.get_workspace().gm_characters_dir

    def _new_child(self, character: Character, path: Path) -> MainWindow:
        """File ▸ Open from an NPC window opens another NPC, not a character sheet."""
        return NPCWindow(character=character, path=path)

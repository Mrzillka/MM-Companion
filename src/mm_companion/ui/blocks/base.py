"""The block descriptor and the contract every character-sheet block satisfies.

A :class:`BlockDescriptor` is the data-first description of one block: its key
(names it for the layout model and the size table), its dock title (shown in the
block frame's title bar), the *factory* that builds its widget, its size
constraints, and where it lands in the default arrangement. The character sheet
and the block canvas iterate a registry of these descriptors rather than
hardcoding the block set in three places, so a mod can add a block by registering
one more descriptor.

Every block widget already follows a uniform contract — construct with
``(data, character, parent=None)`` and expose ``set_locked(bool)`` — captured by
the :class:`Block` protocol. Cross-block reactivity signals (``changed`` /
``edited`` / the section-specific ones) are still wired by name in
:mod:`mm_companion.ui.character_sheet`; a topic signal bus that lets a mod block
plug into that wiring is a later step.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from PySide6.QtWidgets import QWidget

from mm_companion.core.character import Character
from mm_companion.core.data_loader import GameData
from mm_companion.ui.block_sizes import BlockSize

# A block widget is built from the shared game data + character model. Sections
# accept an optional parent, but the sheet always constructs them parentless (the
# block frame reparents them), so the factory is called with just the two.
BlockFactory = Callable[[GameData, Character], QWidget]


@runtime_checkable
class Block(Protocol):
    """The minimal contract a sheet block widget satisfies.

    Sections are ``QGroupBox`` subclasses constructed as ``Section(data, character)``
    and toggled between edit and read-only view with :meth:`set_locked`. This
    protocol documents that shared shape (it is not enforced at construction).
    """

    def set_locked(self, locked: bool) -> None: ...


@dataclass(frozen=True)
class BlockDescriptor:
    """A data-first description of one character-sheet block.

    ``default_row``/``default_col`` place the block in the default arrangement:
    blocks sharing a ``default_row`` sit side by side in that row, ordered by
    ``default_col``; rows stack in ascending ``default_row`` order.
    """

    key: str
    title: str
    factory: BlockFactory
    size: BlockSize = BlockSize()
    default_row: int = 0
    default_col: int = 0

"""Data-first descriptions of the character-sheet blocks.

A :class:`~mm_companion.ui.blocks.base.BlockDescriptor` describes one sheet block
— its key, dock title, widget factory, size constraints, and default layout slot.
:mod:`mm_companion.ui.blocks.registry` holds the ordered registry of descriptors
that the character sheet and the block canvas iterate instead of hardcoding the
block set. The base blocks register at import; a mod's Python module can add a
block by calling :func:`~mm_companion.ui.blocks.registry.register_block`.
"""

from __future__ import annotations

from mm_companion.ui.blocks.base import Block, BlockDescriptor, BlockFactory
from mm_companion.ui.blocks.bus import SignalBus
from mm_companion.ui.blocks.registry import (
    BLOCKS,
    block_descriptors,
    default_rows,
    register_block,
    unregister_block,
)

__all__ = [
    "Block",
    "BlockDescriptor",
    "BlockFactory",
    "BLOCKS",
    "SignalBus",
    "block_descriptors",
    "default_rows",
    "register_block",
    "unregister_block",
]

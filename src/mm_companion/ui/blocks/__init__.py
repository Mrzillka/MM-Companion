"""Data-first descriptions of the character-sheet blocks.

A :class:`~mm_companion.ui.blocks.base.BlockDescriptor` describes one sheet block
— its key, dock title, widget factory, size constraints, and default layout slot.
:mod:`mm_companion.ui.blocks.registry` holds the ordered registry of descriptors
that the character sheet and the block canvas iterate instead of hardcoding the
block set. The base blocks register at import; a mod's Python module can add a
block by calling :func:`~mm_companion.ui.blocks.registry.register_block`.
"""

from __future__ import annotations

from mm_companion.ui.blocks.base import (
    INSTANCE_SEPARATOR,
    Block,
    BlockDescriptor,
    BlockFactory,
    instance_key,
    instance_template,
)
from mm_companion.ui.blocks.bus import SignalBus
from mm_companion.ui.blocks.declarative import DeclarativeBlock
from mm_companion.ui.blocks.registry import (
    BLOCKS,
    block_descriptors,
    default_pin_lines,
    default_rows,
    register_block,
    sync_declarative_blocks,
    unregister_block,
)

__all__ = [
    "INSTANCE_SEPARATOR",
    "Block",
    "BlockDescriptor",
    "BlockFactory",
    "BLOCKS",
    "DeclarativeBlock",
    "SignalBus",
    "block_descriptors",
    "default_pin_lines",
    "default_rows",
    "instance_key",
    "instance_template",
    "register_block",
    "sync_declarative_blocks",
    "unregister_block",
]

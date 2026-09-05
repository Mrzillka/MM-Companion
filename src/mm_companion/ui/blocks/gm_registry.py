"""The ordered registry of **GM window** block descriptors.

The GM window has always had blocks — Scene, Players, NPCs, Rolls — but they were
a literal list inside :class:`~mm_companion.ui.gm_window.GMWindow`, the way the
sheet's block set used to be spelled out in three hardcoded places. This is the
same fix applied to the other surface: one ordered registry the window iterates,
so a mod can put a block in front of the GM without a ``gm_window.py`` edit.

**Why a second registry rather than a field on
:class:`~mm_companion.ui.blocks.base.BlockDescriptor`.** The two surfaces look
alike and are not. A sheet block is built from ``(GameData, Character)`` and
takes its place on the character sheet's signal bus through ``publishes`` /
``subscribes`` / ``requests`` / ``serves``; a GM panel is built from the *window*,
has no character at all, and has no bus to join. Adding a ``surfaces`` flag to one
descriptor would have left half its fields meaningless whichever surface a block
chose, and a mod author reading it could not tell which half applied to them.

They do share the things that are genuinely shared: the generic
:class:`~mm_companion.core.registry.Registry` (insertion order, duplicate keys
refused unless ``replace=True``) and :func:`~mm_companion.ui.block_sizes.load_block_sizes`,
whose GM entries are ``gm_``-prefixed in ``block_sizes.json`` while the canvas
keys them unprefixed — the GM window is its own block namespace.

One consequence worth knowing, and it is the same one the Scene block's own note
records: adding or removing a block invalidates a stored arrangement, because the
canvas requires every known block to appear exactly once. So enabling or
disabling a mod that registers a GM block resets ``gm_layout`` once.
:meth:`~mm_companion.ui.block_canvas.BlockCanvas.restore_layout` already answers
``False`` for an arrangement it cannot use and falls back to the defaults, so this
degrades rather than breaking — but a GM who had rearranged their board will find
it back as it shipped.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass

from PySide6.QtWidgets import QWidget

from mm_companion.core.registry import Registry
from mm_companion.ui.block_sizes import RecommendedSize

#: A GM panel is built from the window that will host it — it needs the window's
#: model (the session bridge, the cast, the roller) the way a sheet block needs
#: the character. Typed as ``QWidget`` rather than ``GMWindow`` to keep this
#: module importable without pulling the window in, which would be a cycle: the
#: window imports the registry to build itself.
GMBlockFactory = Callable[[QWidget], QWidget]


@dataclass(frozen=True)
class GMBlockDescriptor:
    """A data-first description of one GM-window block.

    ``default_row`` / ``default_col`` place it in the default arrangement, and
    ``default_pinned`` moves it into the strip that does not scroll instead — the
    same three fields, with the same meanings, as a sheet block's, because a GM
    laying out their board is doing the same thing a player laying out a sheet is.

    There is deliberately no bus wiring here. The GM window has no
    :class:`~mm_companion.ui.blocks.bus.SignalBus`: its panels talk to the window
    directly and to the table through
    :class:`~mm_companion.ui.session_bridge.SessionBridge`, which is a better seam
    for a mod anyway — it is the one that reaches the other end of the wire.
    """

    key: str
    title: str
    factory: GMBlockFactory
    size: RecommendedSize = RecommendedSize()
    default_row: int = 0
    default_col: int = 0
    default_pinned: bool = False


#: The live registry. Ordered — insertion order is block construction order.
GM_BLOCKS: Registry[GMBlockDescriptor] = Registry("gm_blocks")


def register_gm_block(descriptor: GMBlockDescriptor, *, replace: bool = False) -> GMBlockDescriptor:
    """Add *descriptor* to the registry (raises on a duplicate key unless *replace*)."""
    GM_BLOCKS.register(descriptor.key, descriptor, replace=replace)
    return descriptor


def unregister_gm_block(key: str) -> None:
    """Drop the GM block *key* if present (no error when it is absent)."""
    GM_BLOCKS.unregister(key)


def gm_block_descriptors() -> list[GMBlockDescriptor]:
    """Every registered GM block descriptor, in registration order."""
    return [GM_BLOCKS.get(key) for key in GM_BLOCKS.keys()]


def gm_default_rows() -> list[list[str]]:
    """The default arrangement as rows of block keys, derived from the descriptors.

    Grouped by ``default_row`` and ordered within a row by ``default_col``, the
    way :func:`~mm_companion.ui.blocks.registry.default_rows` does it. A
    ``default_pinned`` block is not in a row at all.
    """
    rows: dict[int, list[GMBlockDescriptor]] = defaultdict(list)
    for descriptor in gm_block_descriptors():
        if descriptor.default_pinned:
            continue
        rows[descriptor.default_row].append(descriptor)
    return [[d.key for d in sorted(rows[row], key=lambda d: d.default_col)] for row in sorted(rows)]


def gm_default_pin_lines() -> list[list[str]]:
    """The GM blocks that start in the pinned strip, one per line along it.

    The complement of :func:`gm_default_rows`: together the two cover every
    registered block exactly once, which is what the arrangement model requires.
    """
    pinned = [d for d in gm_block_descriptors() if d.default_pinned]
    pinned.sort(key=lambda d: (d.default_row, d.default_col))
    return [[d.key] for d in pinned]

"""The vocabulary of the pinned strip: its edges, and where a drop on it lands.

Kept in a module of its own, with no Qt in it, because both sides of the feature
need it and neither should import the other: the arrangement *model* lives in
:mod:`mm_companion.ui.block_canvas` (which validates an edge coming back out of
settings.json) and the *view* in :mod:`mm_companion.ui.pinned_panel` (which lays
widgets out from it).

The strip's blocks are a :mod:`~mm_companion.ui.layout_tree` node like the page's
now, rendered by the same splitters with the same dividers and the same detents.
Two consequences worth stating here, since this is where the vocabulary used to
live:

* the strip's **lines are gone as a concept**. There is no "line along the strip
  holding blocks across it" any more, only cells that split however the user
  dragged them; ``region_lines`` still derives the old shape for anything that
  wants to read the strip in those terms.
* **alignment is gone with them.** ``fill``/``start``/``center``/``end`` existed
  because a block that pinned its own size could not fill the cell it was given
  and would otherwise sit adrift in the middle of one. No block pins its own size
  now — the user does — so every block fills its cell and the choice had nothing
  left to decide.
"""

from __future__ import annotations

from dataclasses import dataclass

#: Which side of the page the strip is parked on.
PIN_EDGES = ("left", "right", "top", "bottom")

DEFAULT_EDGE = "right"

#: The strip's thickness in pixels before the user has dragged it.
DEFAULT_EXTENT = 320


def is_vertical_strip(edge: str) -> bool:
    """Whether *edge* runs down the side of the page (rather than along it)."""
    return edge in ("left", "right")


@dataclass(frozen=True)
class PinSlot:
    """Where a block dropped on the strip lands.

    Deliberately the same shape as the page's ``DropSlot``, because the strip is
    the same kind of thing now: a drop names the block it lands beside and which
    ``side`` of it to take, which is the only way to say "underneath that one".
    A ``target`` of ``None`` means the strip is empty and the block is simply the
    first thing in it.
    """

    target: str | None = None
    side: str = "bottom"

"""The vocabulary of the pinned strip: its edges, alignments and defaults.

Kept in a module of its own, with no Qt in it, because both sides of the feature
need it and neither should import the other: the arrangement *model* lives in
:mod:`mm_companion.ui.block_canvas` (which validates an edge/alignment coming
back out of settings.json) and the *view* in
:mod:`mm_companion.ui.pinned_panel` (which lays widgets out from it).
"""

from __future__ import annotations

#: Which side of the page the strip is parked on.
PIN_EDGES = ("left", "right", "top", "bottom")

#: How a pinned block sits across the strip (the axis the strip is *thin* in).
#: ``fill`` stretches it to the strip's full thickness; the others leave it at
#: its natural size and push it to one side.
PIN_ALIGNMENTS = ("fill", "start", "center", "end")

DEFAULT_EDGE = "right"
DEFAULT_ALIGN = "fill"

#: The strip's thickness in pixels before the user has dragged it.
DEFAULT_EXTENT = 320


def is_vertical_strip(edge: str) -> bool:
    """Whether *edge* stacks its blocks top-to-bottom (a left/right strip)."""
    return edge in ("left", "right")

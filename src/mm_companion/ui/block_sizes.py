"""Per-block size constraints for the character-sheet dock panels.

The min/max width and height of each block live in ``block_sizes.json`` (loaded as
package data) so they can be tweaked without touching code. A block with a
``max_width`` can't be stretched horizontally; one with a ``max_height`` can't be
stretched vertically. Bounds are in pixels; an omitted, null, or zero bound means
"no constraint" — 0 for a minimum, Qt's ``QWIDGETSIZE_MAX`` for a maximum.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from importlib.resources import files

# Qt's "no maximum" sentinel (QWIDGETSIZE_MAX). Hardcoded so this stays a plain
# config loader and does not pull in Qt just for a constant.
UNBOUNDED = 16777215

RESOURCE_PACKAGE = "mm_companion.ui"
RESOURCE_NAME = "block_sizes.json"


@dataclass(frozen=True)
class BlockSize:
    """Size constraints for one block, in pixels."""

    min_width: int = 0
    min_height: int = 0
    max_width: int = UNBOUNDED
    max_height: int = UNBOUNDED


@lru_cache(maxsize=1)
def load_block_sizes() -> dict[str, BlockSize]:
    """Parse ``block_sizes.json`` into a ``BlockSize`` per block key.

    Cached — one parse per process. Keys starting with ``_`` (e.g. ``_comment``)
    are ignored so the file can carry inline documentation.
    """
    text = files(RESOURCE_PACKAGE).joinpath(RESOURCE_NAME).read_text(encoding="utf-8")
    raw = json.loads(text)
    return {
        key: BlockSize(
            min_width=spec.get("min_width") or 0,
            min_height=spec.get("min_height") or 0,
            max_width=spec.get("max_width") or UNBOUNDED,
            max_height=spec.get("max_height") or UNBOUNDED,
        )
        for key, spec in raw.items()
        if not key.startswith("_")
    }

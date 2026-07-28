"""Per-block size constraints for the character-sheet dock panels.

The min/max width and height of each block live in ``block_sizes.json`` (loaded as
package data) so they can be tweaked without touching code. A block with a
``max_width`` can't be stretched horizontally; one with a ``max_height`` can't be
stretched vertically. Bounds are in pixels; an omitted, null, or zero bound means
"no constraint" — 0 for a minimum, Qt's ``QWIDGETSIZE_MAX`` for a maximum.

The shipped file is the *baseline*. The active theme may override any bound
through its ``blocks`` map (see :mod:`mm_companion.ui.theme`), because how much
room a block needs is a function of the look: a denser preset with tighter
padding and smaller type fits the same content in less space. Overrides are
merged per bound, so a preset that only wants Abilities narrower says exactly
that and inherits the rest.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from importlib.resources import files
from typing import Any

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


def _baseline() -> dict[str, dict[str, Any]]:
    """The shipped bounds, keyed by block.

    Keys starting with ``_`` (e.g. ``_comment``) are ignored so the file can carry
    inline documentation.
    """
    text = files(RESOURCE_PACKAGE).joinpath(RESOURCE_NAME).read_text(encoding="utf-8")
    return {key: spec for key, spec in json.loads(text).items() if not key.startswith("_")}


def _as_size(spec: dict[str, Any]) -> BlockSize:
    return BlockSize(
        min_width=spec.get("min_width") or 0,
        min_height=spec.get("min_height") or 0,
        max_width=spec.get("max_width") or UNBOUNDED,
        max_height=spec.get("max_height") or UNBOUNDED,
    )


@lru_cache(maxsize=4)
def _load_for_theme(theme_id: str) -> dict[str, BlockSize]:
    """The bounds for one theme id, cached per id.

    Keyed on the theme rather than cached outright so switching preset re-reads
    instead of serving the previous look's sizes. Four entries is more than the
    handful of presets a session realistically visits.
    """
    from mm_companion.ui import theme

    merged = {key: dict(spec) for key, spec in _baseline().items()}
    for key, override in theme.active_theme().blocks.items():
        if isinstance(override, dict):
            merged.setdefault(key, {}).update(override)
    return {key: _as_size(spec) for key, spec in merged.items()}


def load_block_sizes() -> dict[str, BlockSize]:
    """A ``BlockSize`` per block key: the shipped bounds under the theme's overrides."""
    from mm_companion.ui import theme

    return _load_for_theme(theme.active_theme().id)


def clear_block_size_cache() -> None:
    """Drop the cached bounds, so the next read picks up a theme change."""
    _load_for_theme.cache_clear()

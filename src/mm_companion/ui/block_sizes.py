"""How big each sheet block would *like* to be — a recommendation, not a bound.

These numbers used to be constraints: ``min_width``/``min_height`` were floors a
layout could not go under, and ``max_width``/``max_height`` pinned a block so it
could not be stretched. That made sense while the page arranged itself and the
user had no say — a block was exactly as big as its content, and the window grew
to fit.

The page is user-resizable now, so a floor would be a refusal. What is left is a
**recommendation**: the size at which a block reads well, which the grid uses for
exactly three things —

* the size a block opens at, before anyone has dragged anything;
* the soft detent a divider sticks at on its way past
  (:mod:`mm_companion.ui.grid_handle`);
* the mark shown during a drag, and the "fit to content" it snaps back to.

and for **nothing** in any layout minimum. Drag a block to a single pixel if you
like; it will reflow as far as it can and then scroll inside its own frame.

The numbers live in ``block_sizes.json`` (loaded as package data) so they can be
retuned without touching code, and the active theme may override any of them
through its ``blocks`` map — how much room a block needs is a function of the
look, and a denser preset with tighter padding and smaller type fits the same
content in less space. Overrides merge per bound, so a preset that only wants
Abilities narrower says exactly that and inherits the rest.

The old ``min_*``/``max_*`` key names are still read: ``min_*`` as the
recommendation it always effectively was, ``max_*`` ignored. That is for the mods
in the sibling repository, which pin an engine version and ship
``blocks.json`` files written against the old names.

This is UI config, **not** game content, so it lives under ``ui/`` (bundled via
the ``ui/*.json`` package-data entry) and not the OGL ``data/`` dir.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from importlib.resources import files
from typing import Any

# Qt's "no maximum" sentinel (QWIDGETSIZE_MAX). Nothing here imposes it any more,
# but the settings editor still needs an upper bound for its spin boxes, and
# hardcoding it keeps this a plain config loader that does not import Qt.
UNBOUNDED = 16777215

RESOURCE_PACKAGE = "mm_companion.ui"
RESOURCE_NAME = "block_sizes.json"

#: The fields a block may state, in the order the settings editor shows them.
BOUNDS = ("recommended_width", "recommended_height")

#: What each field used to be called. Read for the mods that still write them.
_ALIASES = {"recommended_width": "min_width", "recommended_height": "min_height"}


@dataclass(frozen=True)
class RecommendedSize:
    """The size one block reads well at, in pixels. Zero means "no opinion"."""

    width: int = 0
    height: int = 0

    def __bool__(self) -> bool:
        """Whether this block states a recommendation at all."""
        return bool(self.width or self.height)


def _baseline() -> dict[str, dict[str, Any]]:
    """The shipped recommendations, keyed by block.

    Keys starting with ``_`` (e.g. ``_comment``) are ignored so the file can carry
    inline documentation.
    """
    text = files(RESOURCE_PACKAGE).joinpath(RESOURCE_NAME).read_text(encoding="utf-8")
    return {key: spec for key, spec in json.loads(text).items() if not key.startswith("_")}


def _as_size(spec: dict[str, Any]) -> RecommendedSize:
    """One block's recommendation, accepting the old ``min_*`` names as well."""

    def read(field: str) -> int:
        value = spec.get(field)
        if value is None:
            value = spec.get(_ALIASES[field])
        return int(value or 0)

    return RecommendedSize(width=read("recommended_width"), height=read("recommended_height"))


@lru_cache(maxsize=4)
def _load_for_theme(theme_id: str) -> dict[str, RecommendedSize]:
    """The recommendations for one theme id, cached per id.

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


def load_block_sizes() -> dict[str, RecommendedSize]:
    """A :class:`RecommendedSize` per block: the shipped numbers under the theme's."""
    from mm_companion.ui import theme

    return _load_for_theme(theme.active_theme().id)


def clear_block_size_cache() -> None:
    """Drop the cached recommendations, so the next read picks up a theme change."""
    _load_for_theme.cache_clear()

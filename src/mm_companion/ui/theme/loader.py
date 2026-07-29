"""Discover and parse theme presets.

Presets come from two places, merged into one ``id -> Theme`` map:

* **Bundled** — ``ui/theme/themes/*.json``, read through ``importlib.resources``
  (not a filesystem path) so they survive being installed as a package, exactly
  as :mod:`mm_companion.ui.block_sizes` and :mod:`mm_companion.core.data_loader`
  do for their own data.
* **Workspace** — ``<workspace>/themes/*.json``, so a user can drop a preset in
  without touching the install. A workspace preset with the same id as a bundled
  one replaces it.

A malformed preset is skipped, never fatal — one bad file in the workspace must
not stop the app from starting, matching how
:func:`mm_companion.core.mods.discover_workspace_mods` treats a bad manifest.

A preset may name a parent with ``extends``, whose token maps are merged
underneath it key by key. That is what lets a designed preset state only what it
changes about ``classic`` instead of restating a hundred tokens it agrees with.
"""

from __future__ import annotations

import json
from functools import lru_cache
from importlib.resources import files
from typing import Any

from mm_companion.core import storage
from mm_companion.ui.theme.tokens import CHROME_MODES, Chrome, Theme

RESOURCE_PACKAGE = "mm_companion.ui.theme"
RESOURCE_DIR = "themes"

#: The preset the app falls back to: today's look, in the native platform style.
DEFAULT_THEME_ID = "classic"

#: ``settings.json`` shipped ``"theme": "system"`` before presets existed. Treat it
#: as a request for the native look rather than as a missing preset, so an existing
#: workspace keeps working untouched.
_LEGACY_IDS = {"system": DEFAULT_THEME_ID, "": DEFAULT_THEME_ID}

#: The token groups a preset may carry, each a flat ``name -> value`` map.
_TOKEN_GROUPS = ("colors", "metrics", "typography", "blocks")


def _parse(raw: Any, theme_id: str) -> dict[str, Any]:
    """Validate one preset's raw JSON, raising ``ValueError`` on anything unusable."""
    if not isinstance(raw, dict):
        raise ValueError(f"theme {theme_id!r}: top level must be a JSON object")
    chrome = raw.get("chrome") or {}
    if not isinstance(chrome, dict):
        raise ValueError(f"theme {theme_id!r}: 'chrome' must be an object")
    mode = chrome.get("mode", "system")
    if mode not in CHROME_MODES:
        raise ValueError(
            f"theme {theme_id!r}: chrome.mode {mode!r} is not one of {list(CHROME_MODES)}"
        )
    for group in _TOKEN_GROUPS:
        value = raw.get(group)
        if value is not None and not isinstance(value, dict):
            raise ValueError(f"theme {theme_id!r}: {group!r} must be an object")
    return raw


def _load_sources() -> dict[str, dict[str, Any]]:
    """Every discoverable preset as raw JSON, keyed by id; workspace wins on a clash."""
    sources: dict[str, dict[str, Any]] = {}
    for name, text in _bundled_texts():
        _add_source(sources, name, text, fatal=True)
    for name, text in _workspace_texts():
        _add_source(sources, name, text, fatal=False)
    return sources


def _add_source(sources: dict[str, dict[str, Any]], name: str, text: str, *, fatal: bool) -> None:
    """Parse one preset into *sources*; a workspace file's failure is swallowed.

    A bundled preset that fails to parse is a packaging bug and raises; a
    workspace one is user-supplied and is skipped instead, so a hand-edited file
    with a stray comma can't lock the user out of their app.
    """
    try:
        raw = _parse(json.loads(text), name)
    except (json.JSONDecodeError, ValueError):
        if fatal:
            raise
        return
    sources[raw.get("id") or name] = raw


def _bundled_texts() -> list[tuple[str, str]]:
    directory = files(RESOURCE_PACKAGE).joinpath(RESOURCE_DIR)
    return [
        (entry.name.removesuffix(".json"), entry.read_text(encoding="utf-8"))
        for entry in sorted(directory.iterdir(), key=lambda e: e.name)
        if entry.name.endswith(".json")
    ]


def _workspace_texts() -> list[tuple[str, str]]:
    directory = storage.get_workspace().themes_dir
    if not directory.is_dir():
        return []
    found: list[tuple[str, str]] = []
    for path in sorted(directory.glob("*.json")):
        try:
            found.append((path.stem, path.read_text(encoding="utf-8")))
        except OSError:
            continue  # unreadable file: skip it, same as a malformed one
    return found


def _merged_tokens(theme_id: str, sources: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Flatten a preset's ``extends`` chain into one set of token maps.

    Walks parent-first so a child's key wins, and stops on a cycle rather than
    recursing forever — a preset that (directly or transitively) extends itself
    is treated as extending nothing.
    """
    chain: list[dict[str, Any]] = []
    seen: set[str] = set()
    current: str | None = theme_id
    while current and current in sources and current not in seen:
        seen.add(current)
        raw = sources[current]
        chain.append(raw)
        parent = raw.get("extends")
        current = parent if isinstance(parent, str) else None
    merged: dict[str, Any] = {group: {} for group in _TOKEN_GROUPS}
    for raw in reversed(chain):  # parents first, so the child overwrites
        for group in _TOKEN_GROUPS:
            merged[group].update(_without_comments(raw.get(group) or {}))
    return merged


def _without_comments(group: dict[str, Any]) -> dict[str, Any]:
    """One token map with its ``_``-prefixed comment keys dropped.

    A preset file documents itself inline — ``classic.json`` explains why
    ``tint.warning`` was retuned in a ``"_tint.warning"`` key sitting right next to
    it. Without this those notes arrive as tokens whose value is a paragraph of
    prose, which anything iterating the map (the Settings editor, most obviously)
    would render as a real setting. Same convention as
    :func:`mm_companion.ui.block_sizes._baseline`.
    """
    return {key: value for key, value in group.items() if not key.startswith("_")}


def _build(theme_id: str, sources: dict[str, dict[str, Any]]) -> Theme:
    raw = sources[theme_id]
    tokens = _merged_tokens(theme_id, sources)
    chrome_raw = raw.get("chrome") or {}
    return Theme(
        id=theme_id,
        name=raw.get("name") or theme_id,
        description=raw.get("description") or "",
        chrome=Chrome(
            mode=chrome_raw.get("mode", "system"),
            focus_ring=bool(chrome_raw.get("focus_ring", True)),
        ),
        colors=tokens["colors"],
        metrics=tokens["metrics"],
        typography=tokens["typography"],
        blocks=tokens["blocks"],
    )


@lru_cache(maxsize=1)
def available_themes() -> dict[str, Theme]:
    """Every discoverable preset, fully resolved, keyed by id.

    Cached — one scan per process, like :func:`load_game_data`. Call
    :func:`clear_theme_cache` after anything that changes what is on disk.
    """
    sources = _load_sources()
    return {theme_id: _build(theme_id, sources) for theme_id in sources}


def resolve_id(theme_id: str | None) -> str:
    """Map a stored setting to a real preset id, falling back to the default."""
    candidate = _LEGACY_IDS.get(theme_id or "", theme_id or DEFAULT_THEME_ID)
    themes = available_themes()
    if candidate in themes:
        return candidate
    return DEFAULT_THEME_ID if DEFAULT_THEME_ID in themes else next(iter(themes), DEFAULT_THEME_ID)


def clear_theme_cache() -> None:
    """Drop the cached scan so the next read picks up new or edited presets."""
    available_themes.cache_clear()

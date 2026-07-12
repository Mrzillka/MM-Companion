"""Mod discovery and load-order resolution for the data-first constructor.

A *mod* is a bundle of game-content JSON that layers on top of — or overrides
records in — the base ruleset. The base ruleset is itself a mod: the bundled
``data/`` directory, described by ``data/mod.json``, so the same merge pipeline
loads everything (dogfood). Workspace mods live one-per-directory under the
workspace ``mods/`` dir, each with its own ``mod.json`` manifest and its content
files alongside it.

Pure Python, no PySide6 (respects ``ui -> core -> data``). This module only
*discovers* and *orders* mods; the actual deep-merge of their content happens in
:mod:`mm_companion.core.data_loader`.

Manifest (``mod.json``) schema::

    {
      "id": "my-mod",              # unique id (required)
      "name": "My Mod",            # display name (defaults to id)
      "version": "1.0",            # free-form version string
      "priority": 10,              # higher applies later / wins (default 0)
      "requires": ["base"],        # optional ids this mod depends on
      "files": ["effects.json"],   # content files this mod ships
      "python_module": "my_mod"    # optional importable module (Phase 6)
    }
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from importlib import resources
from pathlib import Path

from . import storage

DATA_PACKAGE = "mm_companion"
BASE_MOD_ID = "base"
MANIFEST_FILENAME = "mod.json"


@dataclass(frozen=True)
class Mod:
    """One resolved mod: its manifest metadata plus how to read its content.

    The base ruleset (``is_base=True``) reads its files from the bundled package
    via :mod:`importlib.resources`; a workspace mod reads them from ``root`` on
    disk. Either way :meth:`read` returns the parsed JSON for a content file the
    mod ships, or ``None`` if it does not ship that file.
    """

    id: str
    name: str
    version: str
    priority: int
    files: tuple[str, ...]
    is_base: bool = False
    root: Path | None = None
    python_module: str | None = None
    requires: tuple[str, ...] = ()

    def read(self, filename: str) -> dict | None:
        """Parse one of this mod's content files, or ``None`` if absent."""
        if filename not in self.files:
            return None
        if self.is_base:
            source = resources.files(DATA_PACKAGE).joinpath("data", filename)
            return json.loads(source.read_text(encoding="utf-8"))
        if self.root is None:
            return None
        path = self.root / filename
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def fingerprint(self) -> str:
        """A stable key identifying this mod for the game-data cache."""
        return f"{self.id}@{self.version}#{self.priority}"


def _mod_from_manifest(manifest: dict, *, is_base: bool, root: Path | None) -> Mod:
    return Mod(
        id=manifest["id"],
        name=manifest.get("name", manifest["id"]),
        version=str(manifest.get("version", "")),
        priority=int(manifest.get("priority", 0)),
        files=tuple(manifest.get("files", [])),
        is_base=is_base,
        root=root,
        python_module=manifest.get("python_module"),
        requires=tuple(manifest.get("requires", [])),
    )


def base_mod() -> Mod:
    """The bundled base ruleset, described by ``data/mod.json``."""
    source = resources.files(DATA_PACKAGE).joinpath("data", MANIFEST_FILENAME)
    manifest = json.loads(source.read_text(encoding="utf-8"))
    return _mod_from_manifest(manifest, is_base=True, root=None)


def discover_workspace_mods(workspace: storage.Workspace | None = None) -> list[Mod]:
    """All mods found under the workspace ``mods/`` dir (unfiltered, unordered-by-priority).

    A directory qualifies as a mod when it holds a readable ``mod.json``; malformed
    or unreadable manifests are skipped rather than raising, so one bad mod can't
    stop the app from loading.
    """
    workspace = workspace or storage.get_workspace()
    mods_dir = workspace.mods_dir
    result: list[Mod] = []
    if not mods_dir.exists():
        return result
    for child in sorted(mods_dir.iterdir()):
        if not child.is_dir():
            continue
        manifest_path = child / MANIFEST_FILENAME
        if not manifest_path.exists():
            continue
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, KeyError):
            continue
        try:
            result.append(_mod_from_manifest(manifest, is_base=False, root=child))
        except KeyError:
            continue  # manifest missing a required key (id)
    return result


def active_mods(
    enabled: list[str] | None = None,
    workspace: storage.Workspace | None = None,
) -> list[Mod]:
    """The ordered mod stack to load: base first, then enabled workspace mods.

    *enabled* is the list of workspace-mod ids to apply (defaults to the
    ``enabled_mods`` setting). Enabled mods are ordered by ``priority`` (higher
    applies later and therefore wins), ties broken by their order in *enabled*.
    Unknown ids in *enabled* are ignored.
    """
    if enabled is None:
        enabled = list(storage.load_settings().get("enabled_mods", []))
    mods = [base_mod()]
    if enabled:
        available = {m.id: m for m in discover_workspace_mods(workspace)}
        chosen = [available[mid] for mid in enabled if mid in available]
        chosen.sort(key=lambda m: m.priority)  # stable: preserves enabled order on ties
        mods.extend(chosen)
    return mods

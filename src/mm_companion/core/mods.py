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
      "description": "…",          # optional prose shown in the mod manager
      "priority": 10,              # seeds a newly-added mod's initial load slot
      "requires": ["base"],        # optional ids this mod depends on
      "files": ["effects.json"],   # content files this mod ships
      "python_module": "my_mod",   # optional importable module (Phase 6)
      "options": [                 # optional per-mod configurable options
        {
          "id": "difficulty",      # unique within the mod (required)
          "label": "Difficulty",   # shown in the options form (defaults to id)
          "type": "choice",        # bool | number | text | choice
          "default": "normal",     # value used until the user overrides it
          "choices": ["easy", "normal", "hard"],  # for type == "choice"
          "description": "…"       # optional field help
        }
      ]
    }

The user-facing load order lives in settings (``mod_order``), not the manifest —
the manifest ``priority`` only decides where a *newly added* mod first lands.
"""

from __future__ import annotations

import importlib
import json
import shutil
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path

from . import storage

DATA_PACKAGE = "mm_companion"
BASE_MOD_ID = "base"
MANIFEST_FILENAME = "mod.json"

OPTION_TYPES = ("bool", "number", "text", "choice")


class ModImportError(Exception):
    """Raised by :func:`import_mod_folder` when a folder can't be installed as a mod."""


@dataclass(frozen=True)
class ModOption:
    """One configurable option a mod declares in its manifest.

    ``type`` is one of :data:`OPTION_TYPES`; ``choices`` is only meaningful for
    ``"choice"``. ``default`` is the value used until the user overrides it in the
    mod manager (stored under ``mod_options`` in settings).
    """

    id: str
    label: str
    type: str
    default: object = None
    choices: tuple[str, ...] = ()
    description: str = ""


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
    description: str = ""
    options: tuple[ModOption, ...] = field(default_factory=tuple)

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


def _option_from_dict(raw: dict) -> ModOption:
    opt_type = raw.get("type", "text")
    return ModOption(
        id=raw["id"],
        label=raw.get("label", raw["id"]),
        type=opt_type if opt_type in OPTION_TYPES else "text",
        default=raw.get("default"),
        choices=tuple(raw.get("choices", [])),
        description=raw.get("description", ""),
    )


def _mod_from_manifest(manifest: dict, *, is_base: bool, root: Path | None) -> Mod:
    options = tuple(
        _option_from_dict(opt)
        for opt in manifest.get("options", [])
        if isinstance(opt, dict) and "id" in opt
    )
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
        description=manifest.get("description", ""),
        options=options,
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
    order: list[str] | None = None,
) -> list[Mod]:
    """The ordered mod stack to load: base first, then enabled workspace mods.

    *enabled* is the set of workspace-mod ids to apply (defaults to the
    ``enabled_mods`` setting). Load order is the user-defined *order* (defaults to
    the ``mod_order`` setting): enabled mods present in *order* apply in that order
    (later applies later and therefore wins). Any enabled mod not yet listed in
    *order* is appended after, seeded by ascending manifest ``priority``. Unknown
    ids are ignored.
    """
    if enabled is None:
        enabled = list(storage.load_settings().get("enabled_mods", []))
    if order is None:
        order = list(storage.load_settings().get("mod_order", []))
    mods = [base_mod()]
    if not enabled:
        return mods

    available = {m.id: m for m in discover_workspace_mods(workspace)}
    enabled_set = [mid for mid in enabled if mid in available]

    ordered = [mid for mid in order if mid in enabled_set]
    remaining = [mid for mid in enabled_set if mid not in ordered]
    remaining.sort(key=lambda mid: available[mid].priority)  # stable seed for un-ordered mods

    mods.extend(available[mid] for mid in ordered + remaining)
    return mods


# --- Python-module loading (the code-executing half of a mod) ----------------


def load_mod_python_modules(
    mods: list[Mod],
    *,
    trusted: set[str],
    importer: Callable[[str], object] = importlib.import_module,
) -> list[str]:
    """Import the ``python_module`` of each *trusted* mod so its ``register_*`` runs.

    A mod's data is always safe to merge, but its Python module executes arbitrary
    code on import — so a module is loaded **only** when the mod's id is in
    *trusted* (the base ruleset is implicitly trusted). The mod's ``root`` is put on
    ``sys.path`` first so a workspace module resolves by its declared name. Import
    failures are swallowed (one broken mod can't stop startup); the ids actually
    imported are returned, in load order.
    """
    loaded: list[str] = []
    for mod in mods:
        if not mod.python_module:
            continue
        if not mod.is_base and mod.id not in trusted:
            continue  # data enabled, code not trusted — skip the module
        root = mod.root
        if root is not None:
            root_str = str(root)
            if root_str not in sys.path:
                sys.path.insert(0, root_str)
        try:
            importer(mod.python_module)
        except Exception:  # noqa: BLE001 — a bad mod must not crash the app
            continue
        loaded.append(mod.id)
    return loaded


def initialize_mods(workspace: storage.Workspace | None = None) -> list[str]:
    """Startup hook: import trusted enabled mods' Python modules.

    Call once after :func:`mm_companion.core.storage.ensure_workspace` and **before**
    the first :func:`mm_companion.core.data_loader.load_game_data`, so a mod's
    ``register_*`` handlers are in place before any content is parsed or rendered.
    Reads ``enabled_mods`` and ``trusted_mods`` from settings. Returns the mod ids
    whose modules were imported.
    """
    settings = storage.load_settings()
    enabled = list(settings.get("enabled_mods", []))
    trusted = set(settings.get("trusted_mods", []))
    mods = active_mods(enabled, workspace)
    return load_mod_python_modules(mods, trusted=trusted)


# --- settings seams for enabling / trusting mods (UI wires to these later) ----


def _update_id_list(key: str, mod_id: str, present: bool) -> list[str]:
    settings = storage.load_settings()
    ids = list(settings.get(key, []))
    if present and mod_id not in ids:
        ids.append(mod_id)
    elif not present and mod_id in ids:
        ids = [i for i in ids if i != mod_id]
    storage.update_settings(**{key: ids})
    return ids


def set_mod_enabled(mod_id: str, enabled: bool) -> list[str]:
    """Add/remove *mod_id* from the ``enabled_mods`` setting; returns the new list.

    Disabling a mod also drops it from ``trusted_mods`` (trust without enable is
    meaningless). Callers should :func:`data_loader.clear_game_data_cache` afterward
    so the change is picked up.
    """
    ids = _update_id_list("enabled_mods", mod_id, enabled)
    if not enabled:
        _update_id_list("trusted_mods", mod_id, False)
    return ids


def set_mod_trusted(mod_id: str, trusted: bool) -> list[str]:
    """Add/remove *mod_id* from the ``trusted_mods`` setting; returns the new list.

    Trusting lets the mod's Python module run at the next startup. Only meaningful
    for a mod that is also enabled.
    """
    return _update_id_list("trusted_mods", mod_id, trusted)


def set_mod_order(ids: list[str]) -> list[str]:
    """Persist the user-defined load order (``mod_order`` setting); returns it.

    *ids* is the full drag order of workspace mods (enabled or not). Later entries
    apply later and win. See :func:`active_mods`.
    """
    order = list(ids)
    storage.update_settings(mod_order=order)
    return order


# --- per-mod options ----------------------------------------------------------


def mod_option_values(mod_id: str, mod: Mod | None = None) -> dict:
    """The effective option values for *mod_id*: declared defaults + stored overrides.

    *mod* supplies the option declarations (defaults / valid ids); when omitted the
    result is just the raw stored overrides. Values not declared by the mod are
    dropped so a stale override can't leak in.
    """
    stored = storage.load_settings().get("mod_options", {})
    overrides = stored.get(mod_id, {}) if isinstance(stored, dict) else {}
    if mod is None:
        return dict(overrides)
    values: dict = {}
    for option in mod.options:
        values[option.id] = overrides.get(option.id, option.default)
    return values


def set_mod_options(mod_id: str, values: dict) -> dict:
    """Persist option overrides for *mod_id*; returns the full ``mod_options`` map."""
    settings = storage.load_settings()
    stored = dict(settings.get("mod_options", {}) or {})
    stored[mod_id] = dict(values)
    storage.update_settings(mod_options=stored)
    return stored


# --- installing a mod from a folder -------------------------------------------


def import_mod_folder(source: Path, workspace: storage.Workspace | None = None) -> Mod:
    """Copy the mod folder *source* into the workspace ``mods/`` dir; return the mod.

    Validates that *source* holds a parseable ``mod.json`` with an ``id`` that does
    not collide with the base ruleset or an already-installed mod, then copies the
    whole tree to ``mods/<id>``. Raises :class:`ModImportError` on any problem.
    """
    source = Path(source)
    manifest_path = source / MANIFEST_FILENAME
    if not manifest_path.exists():
        raise ModImportError(f"No {MANIFEST_FILENAME} found in {source}")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ModImportError(f"Could not read {manifest_path}: {exc}") from exc
    mod_id = manifest.get("id")
    if not mod_id:
        raise ModImportError(f'{manifest_path} is missing an "id"')
    if mod_id == BASE_MOD_ID:
        raise ModImportError(f'"{mod_id}" is reserved for the base ruleset')

    workspace = workspace or storage.get_workspace()
    workspace.mods_dir.mkdir(parents=True, exist_ok=True)
    destination = workspace.mods_dir / mod_id
    if destination.exists():
        raise ModImportError(f'A mod with id "{mod_id}" is already installed')

    shutil.copytree(source, destination)
    return _mod_from_manifest(manifest, is_base=False, root=destination)

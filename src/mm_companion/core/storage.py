"""The on-disk workspace: where MM-Companion keeps settings and saved characters.

Pure Python, no PySide6 (respects ``ui -> core -> data``). On launch the app
calls :func:`ensure_workspace` to create the user data directory and its default
contents; it is idempotent, so subsequent launches are a no-op.

The location follows each platform's convention — ``%APPDATA%`` on Windows,
``~/Library/Application Support`` on macOS, ``$XDG_DATA_HOME`` or
``~/.local/share`` elsewhere — and can be overridden with the
``MM_COMPANION_HOME`` environment variable, which points at the workspace root
directly (handy for tests and portable installs).
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path

APP_NAME = "MM-Companion"
HOME_ENV_VAR = "MM_COMPANION_HOME"

SETTINGS_FILENAME = "settings.json"
CHARACTERS_DIRNAME = "characters"
GM_CHARACTERS_DIRNAME = "gm_characters"

DEFAULT_SETTINGS: dict[str, object] = {
    "version": 1,
    "theme": "system",
    "ruleset": "4e",
}


@dataclass(frozen=True)
class Workspace:
    """Resolved paths for one workspace root (does not touch the filesystem)."""

    root: Path

    @property
    def settings_file(self) -> Path:
        return self.root / SETTINGS_FILENAME

    @property
    def characters_dir(self) -> Path:
        return self.root / CHARACTERS_DIRNAME

    @property
    def gm_characters_dir(self) -> Path:
        return self.root / GM_CHARACTERS_DIRNAME


def _platform_data_root() -> Path:
    """The per-platform user data directory for the app (no override applied)."""
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming"
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share"
    return Path(base) / APP_NAME


def workspace_root() -> Path:
    """The workspace root, honoring the ``MM_COMPANION_HOME`` override."""
    override = os.environ.get(HOME_ENV_VAR)
    return Path(override) if override else _platform_data_root()


def get_workspace() -> Workspace:
    """The workspace paths, without creating anything on disk."""
    return Workspace(workspace_root())


def ensure_workspace() -> Workspace:
    """Create the workspace and its default contents if missing; idempotent.

    Directories are created (parents included); the settings file is written
    only when absent, so a user's edited settings are never clobbered.
    """
    workspace = get_workspace()
    workspace.characters_dir.mkdir(parents=True, exist_ok=True)
    workspace.gm_characters_dir.mkdir(parents=True, exist_ok=True)
    if not workspace.settings_file.exists():
        workspace.settings_file.write_text(
            json.dumps(DEFAULT_SETTINGS, indent=2) + "\n", encoding="utf-8"
        )
    return workspace


def load_settings() -> dict:
    """Read the settings file, falling back to defaults if missing or invalid."""
    workspace = get_workspace()
    try:
        return json.loads(workspace.settings_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return dict(DEFAULT_SETTINGS)

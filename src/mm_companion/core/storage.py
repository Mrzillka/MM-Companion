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
IMAGES_DIRNAME = "images"
MODS_DIRNAME = "mods"
SESSIONS_DIRNAME = "sessions"

# How the builder reacts to a power that breaks a Power Level cap. ``warn`` flags
# it but still lets it through; ``block`` refuses the save. There is no settings UI
# yet, so this rides on the default below — change the default (or, later, the
# saved setting) to switch the whole app between warning and enforcing.
PL_ENFORCE_WARN = "warn"
PL_ENFORCE_BLOCK = "block"

DEFAULT_SETTINGS: dict[str, object] = {
    "version": 1,
    "theme": "system",
    "ruleset": "4e",
    "pl_enforcement": PL_ENFORCE_WARN,
    # Ids of workspace mods to layer on top of the base ruleset, in the order they
    # should apply (later entries win). Empty by default, so a fresh install runs
    # the base ruleset only. See :mod:`mm_companion.core.mods`.
    "enabled_mods": [],
    # Ids of enabled mods whose declared ``python_module`` may be imported at
    # startup. Enabling a mod loads only its *data* (safe); trusting it in addition
    # lets its Python code run (``register_*`` hooks). A separate, explicit opt-in
    # because importing a mod module executes arbitrary code. Empty by default.
    "trusted_mods": [],
    # User-defined load order of *all* known workspace mods (drag order in the mod
    # manager), independent of which are enabled. Later entries apply later and win;
    # a mod's manifest ``priority`` only seeds where a newly-added mod first lands.
    "mod_order": [],
    # Per-mod option overrides: ``{mod_id: {option_id: value}}``. A mod declares its
    # options in its manifest; only values that differ from the declared defaults are
    # stored here. See :mod:`mm_companion.core.mods`.
    "mod_options": {},
    # Saved dice-roller quick rolls, each a plain dict
    # ``{"bonus": int, "penalty": int, "dc": int | None}``. Written by the Dice
    # Roller window; empty by default. See :mod:`mm_companion.ui.dice_roller`.
    "quick_rolls": [],
    # The online session the app was last attached to, so reopening resumes it
    # instead of starting a fresh one. ``None`` until a session is hosted or joined.
    # See :mod:`mm_companion.core.session`.
    "session_last_id": None,
    # The display name this user last joined a session under, pre-filled in the
    # join dialog so it does not have to be retyped every time.
    "session_player_name": "",
    # Join codes recently connected to, most recent first, as a convenience list in
    # the join dialog. Plain strings; the code itself carries host/port/token.
    "session_recent_codes": [],
    # The player-side history of joined sessions, most recent first — the join
    # dialog's "previous sessions" list. Each entry is a dict
    # ``{"code", "session_id", "session_name", "display_name", "player_id",
    # "player_token", "last_joined"}``: the code reconnects, and the player_id /
    # player_token reclaim the same roster slot. See
    # :mod:`mm_companion.ui.session_dialogs`.
    "session_history": [],
    # The relay to fall back to when this machine cannot be reached directly —
    # ``relay.example.net``, ``host:port``, or a full ``mmrelay://…`` URL. Empty
    # by default: there is no blessed public instance yet, and a GM who runs their
    # own (``python -m mm_companion.relay``) points at it here. See
    # :mod:`mm_companion.core.session.relay`.
    "session_relay_url": "",
    # The GM window's block arrangement + geometry, mirroring ``layout`` for the
    # character sheet: ``{"window_geometry": base64, "dock_state": json}``. Its own
    # key because the GM window has a different block set. See
    # :mod:`mm_companion.ui.gm_window`.
    "gm_layout": {},
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

    @property
    def images_dir(self) -> Path:
        return self.root / IMAGES_DIRNAME

    @property
    def mods_dir(self) -> Path:
        return self.root / MODS_DIRNAME

    @property
    def sessions_dir(self) -> Path:
        return self.root / SESSIONS_DIRNAME


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
    workspace.images_dir.mkdir(parents=True, exist_ok=True)
    workspace.mods_dir.mkdir(parents=True, exist_ok=True)
    workspace.sessions_dir.mkdir(parents=True, exist_ok=True)
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


def save_settings(settings: dict) -> None:
    """Write *settings* to the settings file, creating the workspace if needed.

    Unlike :func:`ensure_workspace` (which only writes defaults when the file is
    absent), this replaces the file wholesale — use it to persist edited settings.
    The stored dict is opaque to ``core``; the UI keeps things like the window
    ``layout`` (base64 strings) here so no Qt types leak into this layer.
    """
    workspace = ensure_workspace()
    workspace.settings_file.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")


def update_settings(**changes: object) -> dict:
    """Merge *changes* into the saved settings and persist them; returns the result."""
    settings = load_settings()
    settings.update(changes)
    save_settings(settings)
    return settings


def pl_enforcement() -> str:
    """How the builder should treat a Power Level cap breach — ``warn`` or ``block``.

    The single seam the UI consults so warn-vs-block is one switch. Reads the
    ``pl_enforcement`` setting, defaulting to :data:`PL_ENFORCE_WARN` when unset or
    unrecognized; a settings UI can later write the other value here.
    """
    value = load_settings().get("pl_enforcement", PL_ENFORCE_WARN)
    return value if value in (PL_ENFORCE_WARN, PL_ENFORCE_BLOCK) else PL_ENFORCE_WARN


def relay_url() -> str:
    """The configured session relay, or ``""`` when there is none.

    The one seam for "is a relay available to fall back to", so the connection
    ladder (direct first, relay only if that fails) has a single place to ask.
    """
    value = load_settings().get("session_relay_url", "")
    return value.strip() if isinstance(value, str) else ""

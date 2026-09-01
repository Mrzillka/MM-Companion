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

import copy
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
#: Where a mod keeps its own runtime state, one JSON file per mod id. Beside
#: ``mods/`` rather than inside it, because what a mod *is* and what a mod has
#: *done* have different lifetimes: removing a mod deletes the first, and a mod
#: reinstalled later should still find the second.
MOD_STATE_DIRNAME = "mod_state"
NOTES_DIRNAME = "notes"
SESSIONS_DIRNAME = "sessions"
THEMES_DIRNAME = "themes"

# How the builder reacts to a power that breaks a Power Level cap. ``warn`` flags
# it but still lets it through; ``block`` refuses the save. There is no settings UI
# yet, so this rides on the default below — change the default (or, later, the
# saved setting) to switch the whole app between warning and enforcing.
PL_ENFORCE_WARN = "warn"
PL_ENFORCE_BLOCK = "block"

# The same switch for a build that spends more Equipment Points than its Equipment
# advantage grants. Kept a *separate* setting rather than folded into the one above:
# the two are different currencies and a table may well police one and not the other.
EQUIPMENT_ENFORCE_WARN = "warn"
EQUIPMENT_ENFORCE_BLOCK = "block"

# How the dice roller lays itself out as a sheet block: reflowing to whatever room
# it is given, or pinned to one of the two shapes worth choosing outright. See
# ``dice_layout`` below.
DICE_LAYOUT_AUTO = "auto"
DICE_LAYOUT_COMPACT = "compact"
DICE_LAYOUT_EXTENDED = "extended"
#: The whole vocabulary, and the only place it is spelled out — both the reader and
#: the writer below validate against this, so a fourth shape is one entry here.
DICE_LAYOUTS = (DICE_LAYOUT_AUTO, DICE_LAYOUT_COMPACT, DICE_LAYOUT_EXTENDED)

DEFAULT_SETTINGS: dict[str, object] = {
    "version": 1,
    # Id of the visual theme preset (see :mod:`mm_companion.ui.theme`). "classic"
    # is the native platform look. An older default of "system" means the same
    # thing and is still accepted, so an existing workspace needs no migration.
    "theme": "classic",
    "ruleset": "4e",
    "pl_enforcement": PL_ENFORCE_WARN,
    "equipment_enforcement": EQUIPMENT_ENFORCE_WARN,
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
    # The session server this GM's tables live on — a box running
    # ``python -m mm_companion.server --hub``. Seeded with the public default the
    # app ships with, and freely editable: a group running their own puts its
    # address here. See :mod:`mm_companion.core.session.hub_client`.
    "session_server_url": "",
    # The server's *operator* secret, for whoever runs the box. Empty for
    # everyone else — creating a session needs no credential at all. It grants the
    # full session list and the right to delete any of them, which is how
    # abandoned sessions get cleaned up.
    "session_admin_secret": "",
    # **The GM's own sessions**, one entry per session they created:
    # ``{"server", "id", "name", "join_code", "gm_token"}``.
    #
    # This list lives here rather than on the server on purpose. A server-side
    # "list all sessions" is exactly the thing that would hand every table's join
    # code to whoever asked, now that anyone may create one — so a session belongs
    # to whoever holds its gm token, and this is where that token is kept. Lose
    # this file and the session is still running; you simply can no longer GM it.
    "session_my_sessions": [],
    # The GM window's block arrangement + geometry, mirroring ``layout`` for the
    # character sheet: ``{"window_geometry": base64, "dock_state": json}``. Its own
    # key because the GM window has a different block set. See
    # :mod:`mm_companion.ui.gm_window`.
    "gm_layout": {},
    # The same pair again for a GM's NPC sheet. The blocks are the character
    # sheet's, but the ones that hold no trait start closed there (see
    # :mod:`mm_companion.ui.npc_window`) — so an NPC arrangement written under
    # ``layout`` would close the roller on every hero too. Both are read with an
    # inline ``or {}`` fallback rather than off a bare ``load_settings``, since a
    # workspace created before this key has neither.
    "npc_layout": {},
    # What a GM card's pinned-parameter strip starts with, per card kind. Each
    # entry is a ``PinRef.to_dict()`` (see :mod:`mm_companion.core.rules.pins`).
    # Read through :func:`gm_default_pins`, never straight off ``load_settings``:
    # an existing workspace's file predates this key entirely.
    #
    # These live in settings rather than as a constant in code because the whole
    # point is that a GM changes them: the Settings window's GM Mode page reads
    # this through :func:`gm_default_pins` and writes it through
    # :func:`set_gm_default_pins`, and this dict is what its "Restore shipped"
    # button puts back.
    # The NPC damage entry is deliberately *late-bound* — the defaults are written
    # down long before the NPC they will describe exists, so "the first Damage
    # power" is the only way to say it, and it stays true when the GM edits which
    # power that is. It resolves to the **attack roll** that power makes: the chip
    # a GM clicks when the mook swings. The save it forces is the target's to roll
    # and arrives on their side as the attack's follow-up.
    #
    # Defence and Toughness lead both lists because they are the pair a GM reads
    # together — how hard to hit, how hard to hurt.
    "gm_default_pins": {
        "player": [
            {"kind": "resistance", "key": "DEF"},
            {"kind": "resistance", "key": "TOUGHNESS"},
            {"kind": "initiative"},
            {"kind": "skill", "key": "Perception"},
        ],
        "npc": [
            {"kind": "resistance", "key": "DEF"},
            {"kind": "resistance", "key": "TOUGHNESS"},
            {"kind": "ability", "key": "ATK"},
            {"kind": "power", "select": "first_damage"},
        ],
    },
    # Per-card pin strips the GM has since edited, keyed ``"npc:<file name>"`` or
    # ``"player:<player_id>"``; a card with no entry here starts from
    # ``gm_default_pins``. Same plain-dict idiom as ``quick_rolls``.
    #
    # A player id is session-scoped, so someone who rejoins in a fresh seat starts
    # from the defaults again. That is the cheap version on purpose: the honest
    # alternative is per-session state on the server, and pins are a GM's private
    # scratch note, not table state.
    "gm_pins": {},
    # Which GM cards are shrunk to their short form, keyed the way ``gm_pins`` is
    # (``"npc:<file name>"``). Read through :func:`gm_collapsed_cards`.
    #
    # Persisted, unlike the sheet's lock or compact mode, and the difference is
    # what the choice is *about*: those are one window's current view, while this
    # is a standing judgement about one creature — the mooks stay shrunk and the
    # villain stays open, and a GM should not have to say so again next week. Only
    # the shrunk ones are stored, so a fresh workspace and a fresh NPC both start
    # expanded.
    "gm_collapsed": {},
    # Whether every player at the table is on the Scene automatically. On by
    # default because that is what a fight looks like: the players are in it, and
    # a GM who has to add five of them before every scene is doing bookkeeping the
    # app already knows the answer to.
    #
    # Off is for the GM who runs the Scene as a deliberate spotlight — the two
    # heroes in this room and not the three upstairs. With it off, players are put
    # on by the eye or by a drag exactly as NPCs are, and their cards grow the eye
    # to do it with. Read through :func:`gm_scene_auto_players`.
    "gm_scene_auto_players": True,
    # Compact mode — the mini dice roller a window collapses to (see
    # :mod:`mm_companion.ui.compact`). Read through :func:`compact_settings`.
    #
    # Note what is *not* here: whether a window is currently compact. That is a
    # play-time view switch like the sheet's lock, not a preference, and reopening
    # into a dice-only window would leave someone hunting for the rest of the app.
    # ``width``/``height`` of 0 mean "take the theme's compact.width/height", so a
    # preset with tighter padding still opens at its own size until the user drags
    # the mini window to one they prefer.
    "compact": {"on_top": True, "width": 0, "height": 0},
    # How the dice roller arranges itself as an ordinary sheet block.
    #
    # ``"auto"`` lets it reflow to whatever space it is given — a column in the
    # narrow side strip, one row in a wide bottom one. The other two pin a shape
    # everywhere and always, whatever the room: ``"compact"`` the one compact mode
    # uses (the roll settings across the top, the quick rolls beside a smaller die,
    # the history under both) and ``"extended"`` the roll controls as a column
    # beside a history filling the rest. Both are preferences rather than modes
    # because each turned out to be a good roller in its own right — the first was
    # only a way to fit a mini window, the second only what GM Mode happened to be
    # built as.
    "dice_layout": DICE_LAYOUT_AUTO,
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
    def mod_state_dir(self) -> Path:
        return self.root / MOD_STATE_DIRNAME

    @property
    def notes_dir(self) -> Path:
        return self.root / NOTES_DIRNAME

    @property
    def sessions_dir(self) -> Path:
        return self.root / SESSIONS_DIRNAME

    @property
    def themes_dir(self) -> Path:
        return self.root / THEMES_DIRNAME


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
    workspace.mod_state_dir.mkdir(parents=True, exist_ok=True)
    workspace.notes_dir.mkdir(parents=True, exist_ok=True)
    workspace.sessions_dir.mkdir(parents=True, exist_ok=True)
    workspace.themes_dir.mkdir(parents=True, exist_ok=True)
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


def equipment_enforcement() -> str:
    """How the builder should treat an Equipment Point overspend — ``warn`` or ``block``.

    The equipment twin of :func:`pl_enforcement`, and the same seam: a red budget bar
    and a ``⚠`` today, one setting away from refusing the save. Read through here and
    never off :func:`load_settings`, which returns the file verbatim and so answers
    ``None`` for any key added after a workspace was created.
    """
    value = load_settings().get("equipment_enforcement", EQUIPMENT_ENFORCE_WARN)
    return (
        value
        if value in (EQUIPMENT_ENFORCE_WARN, EQUIPMENT_ENFORCE_BLOCK)
        else EQUIPMENT_ENFORCE_WARN
    )


def theme_name() -> str:
    """The id of the visual theme preset to use, or ``""`` to take the default.

    The one seam the UI consults for "which look is in force". Deliberately does
    not validate the id — ``core`` has no idea what presets exist, since those
    are a ``ui`` concern (bundled files plus anything in the workspace ``themes``
    dir); :func:`mm_companion.ui.theme.loader.resolve_id` maps an unknown or
    legacy value onto a real preset.
    """
    value = load_settings().get("theme", "")
    return value if isinstance(value, str) else ""


def gm_default_pins() -> dict:
    """What a GM card's pinned-parameter strip starts with, per card kind.

    The one seam for that question, and it exists because of a bug worth stating:
    :func:`load_settings` returns the settings file **verbatim**, it does not
    merge :data:`DEFAULT_SETTINGS` in. So a workspace created before this key
    existed — which is every workspace that predates the feature — answers
    ``None``, and reading it directly gave every card an empty strip. Every other
    setting is read through a fallback for the same reason; this is that fallback.
    """
    stored = load_settings().get("gm_default_pins")
    default = DEFAULT_SETTINGS["gm_default_pins"]
    if not isinstance(stored, dict):
        return copy.deepcopy(default)  # type: ignore[arg-type]
    # Per *kind*, not wholesale: a settings file that names only "npc" should
    # still get the shipped player strip rather than nothing.
    merged = copy.deepcopy(default)  # type: ignore[arg-type]
    merged.update({key: value for key, value in stored.items() if isinstance(value, list)})
    return merged


def set_gm_default_pins(pins: dict) -> None:
    """Write the starting strips, merging *pins* over what is stored per kind.

    The write half of :func:`gm_default_pins`, and it merges for the same reason
    that one does: a caller naming only ``"npc"`` means "leave the player strip
    alone", not "reset it to shipped". Note the asymmetry that follows — an
    **empty list** is a real answer (a GM who wants a card to start bare) and is
    stored as one, while a *missing* kind is no answer at all.
    """
    merged = gm_default_pins()
    merged.update({key: value for key, value in pins.items() if isinstance(value, list)})
    update_settings(gm_default_pins=merged)


def gm_collapsed_cards() -> dict[str, bool]:
    """Which GM cards are shrunk, keyed ``"npc:<file name>"``.

    Read through here rather than off :func:`load_settings`, for the reason spelled
    out on :func:`gm_default_pins`: the settings file comes back verbatim, so a
    workspace older than this key answers ``None``. Unreadable entries are dropped
    rather than raising — the worst a wrong answer here can do is open a card.
    """
    stored = load_settings().get("gm_collapsed")
    if not isinstance(stored, dict):
        return {}
    return {key: bool(value) for key, value in stored.items() if isinstance(key, str)}


def set_gm_collapsed_cards(collapsed: dict[str, bool]) -> None:
    """Record which cards are shrunk, keeping only the ones that are.

    Wholesale rather than merged, unlike :func:`set_gm_default_pins`: the caller is
    the GM window, which holds every card there is, so what it passes *is* the
    answer. Dropping the expanded ones keeps the file to the exceptions, and costs
    nothing — absent means expanded, which is where a card starts.
    """
    update_settings(gm_collapsed={key: True for key, value in collapsed.items() if value})


def gm_scene_auto_players() -> bool:
    """Whether joining a session puts a player on the Scene by itself.

    Read through here rather than off :func:`load_settings`, for the reason spelled
    out on :func:`gm_default_pins`: the file comes back verbatim, so a workspace
    older than this key answers ``None`` — which is falsy, and would have silently
    turned the *default* off for every existing GM.
    """
    stored = load_settings().get("gm_scene_auto_players")
    if stored is None:
        return bool(DEFAULT_SETTINGS["gm_scene_auto_players"])
    return bool(stored)


def set_gm_scene_auto_players(enabled: bool) -> None:
    """Record whether players join the Scene by themselves."""
    update_settings(gm_scene_auto_players=bool(enabled))


def clear_gm_card_pins() -> None:
    """Forget every card's own strip, so each seeds from the defaults again.

    The deliberate opposite of the rule :meth:`GMWindow._pins_for` normally keeps
    — a card's strip is the GM's once the card exists — so it happens only when a
    GM asks for it outright, from the GM Mode settings page.
    """
    update_settings(gm_pins={})


def dice_layout() -> str:
    """How the dice roller arranges itself as a block — one of :data:`DICE_LAYOUTS`.

    The one seam the UI consults, defaulting to :data:`DICE_LAYOUT_AUTO` when unset
    or unrecognized — the same shape :func:`pl_enforcement` has, and needed for the
    same reason: :func:`load_settings` returns the file verbatim, so a workspace
    older than this key answers ``None``.
    """
    value = load_settings().get("dice_layout", DICE_LAYOUT_AUTO)
    return value if value in DICE_LAYOUTS else DICE_LAYOUT_AUTO


def set_dice_layout(layout: str) -> None:
    """Choose how the dice roller arranges itself; an unknown value means ``auto``."""
    update_settings(dice_layout=layout if layout in DICE_LAYOUTS else DICE_LAYOUT_AUTO)


def compact_settings() -> dict:
    """The mini dice roller's remembered size and its always-on-top preference.

    The same fallback :func:`gm_default_pins` exists for, and for the same reason:
    :func:`load_settings` hands the file back verbatim, so a workspace older than
    this key answers ``None``. Merged per key rather than wholesale, so a file
    naming only ``on_top`` still gets the shipped size.
    """
    stored = load_settings().get("compact")
    merged = copy.deepcopy(DEFAULT_SETTINGS["compact"])  # type: ignore[arg-type]
    if not isinstance(stored, dict):
        return merged
    if isinstance(stored.get("on_top"), bool):
        merged["on_top"] = stored["on_top"]
    for key in ("width", "height"):
        value = stored.get(key)
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            merged[key] = value
    return merged


def set_compact_settings(**changes: object) -> None:
    """Write some of the compact-mode preferences, leaving the rest alone.

    The write half of :func:`compact_settings`. Merges for the write half's usual
    reason: dragging the mini window to a new size must not also reset whether it
    stays on top.
    """
    merged = compact_settings()
    merged.update({key: value for key, value in changes.items() if key in merged})
    update_settings(compact=merged)


def local_mod_state(mod_id: str) -> dict:
    """One mod's own runtime state, read from ``mod_state/<mod_id>.json``.

    The seam a mod uses to remember what it was doing between launches — a GM's
    timers, say, set up before anyone arrived. Distinct from two things it is easy
    to confuse it with:

    - :func:`~mm_companion.core.mods.mod_option_values` is *configuration* the
      user set in the Mod Manager. This is state the mod itself wrote.
    - :attr:`~mm_companion.core.session.model.SessionState.mod_state` is the
      **shared** copy, authored by the GM and seen by the table. This one is
      local, private, and survives having no session at all.

    A file, not a settings key, so a mod cannot grow ``settings.json`` without
    bound or collide with an app setting — and so the
    :func:`load_settings` hazard (a key added after a workspace was created reads
    back as ``None``) simply does not arise.

    Answers ``{}`` for a missing, unreadable, malformed or non-object file rather
    than raising. A mod's own saved state must never be able to stop the app
    starting, and there is nothing useful a caller could do with the exception
    anyway.
    """
    path = _mod_state_file(mod_id)
    if path is None:
        return {}
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def set_local_mod_state(mod_id: str, state: dict) -> None:
    """Write one mod's runtime state, replacing whatever was there.

    A no-op for a *mod_id* that is not a plausible one — see
    :func:`_mod_state_file`. Silent rather than raising, to match the reader:
    between them, a mod that misbehaves here loses its own memory and breaks
    nothing else.
    """
    path = _mod_state_file(mod_id)
    if path is None:
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
    except (OSError, TypeError, ValueError):
        # TypeError/ValueError: a mod handed us something json cannot render.
        # Losing the write is the right failure — the alternative is a half-written
        # file that reads back as {} next launch anyway.
        return


def _mod_state_file(mod_id: str) -> Path | None:
    """The path one mod's state lives at, or ``None`` if the id is not one.

    The id becomes a **filename**, and it can have come from a mod manifest a user
    downloaded. :func:`~mm_companion.core.session.protocol.sanitize_mod_id` is the
    one definition of what a mod id may look like — reused here rather than
    restated, so a ``"../settings"`` cannot reach the filesystem from either
    direction and the two checks cannot drift apart.
    """
    from mm_companion.core.session.protocol import sanitize_mod_id

    checked = sanitize_mod_id(mod_id)
    if not checked:
        return None
    return get_workspace().mod_state_dir / f"{checked}.json"


def relay_url() -> str:
    """The configured session relay, or ``""`` when there is none.

    The one seam for "is a relay available to fall back to", so the connection
    ladder (direct first, relay only if that fails) has a single place to ask.
    """
    value = load_settings().get("session_relay_url", "")
    return value.strip() if isinstance(value, str) else ""


def session_server() -> tuple[str, str]:
    """The configured session server and operator secret; the secret is usually ``""``.

    The one seam for "which server does this app use", so the launcher and GM
    Mode ask in one place rather than each reading settings. The server falls
    back to the public default the app ships with, so a fresh install can host a
    game without being configured first.
    """
    from mm_companion.core.session.hub_client import DEFAULT_SERVER

    settings = load_settings()
    url = settings.get("session_server_url", "")
    secret = settings.get("session_admin_secret", "")
    return (
        (url.strip() if isinstance(url, str) else "") or DEFAULT_SERVER,
        secret.strip() if isinstance(secret, str) else "",
    )


def session_server_chosen() -> bool:
    """True once the user has actually settled on a server.

    :func:`session_server` falls back to the bundled default so a fresh install
    has an address to offer, but "there is a default in the box" is not the same
    as "this user uses a server" — and only the second should make the app reach
    for the network on its own. So a first run pre-fills the field and waits to be
    told to connect; every run after that connects by itself.
    """
    settings = load_settings()
    url = settings.get("session_server_url", "")
    return bool(isinstance(url, str) and url.strip()) or bool(my_sessions())


def my_sessions(server: str = "") -> list[dict]:
    """The sessions this app created, newest first, optionally on one server.

    Each is ``{"server", "id", "name", "join_code", "gm_token"}``. This is the
    *only* record that a session is ours — the server offers no way to list what
    somebody made, precisely so it cannot be asked to list what everyone made.
    """
    stored = load_settings().get("session_my_sessions", [])
    if not isinstance(stored, list):
        return []
    rows = [dict(row) for row in stored if isinstance(row, dict) and row.get("id")]
    return [row for row in rows if not server or row.get("server") == server]


def remember_my_session(server: str, session: dict) -> None:
    """Record a session we created (or refresh what we know of one)."""
    session_id = str(session.get("id", ""))
    if not session_id:
        return
    rest = [row for row in my_sessions() if row.get("id") != session_id]
    entry = {
        "server": server,
        "id": session_id,
        "name": str(session.get("name", "")),
        "join_code": str(session.get("join_code", "")),
        "gm_token": str(session.get("gm_token", "")),
    }
    update_settings(session_my_sessions=[entry, *rest])


def forget_my_session(session_id: str) -> None:
    """Drop a session from our list — deleted on the server, or swept."""
    update_settings(
        session_my_sessions=[row for row in my_sessions() if row.get("id") != session_id]
    )

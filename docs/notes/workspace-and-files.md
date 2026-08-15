# The workspace, the launcher and saved characters

Matters when touching startup, settings, saving, loading or the character library.

Working notes for MM-Companion, split out of [CLAUDE.md](../../CLAUDE.md).

- On launch, `__main__.main()` shows a splash and calls
  `core.storage.ensure_workspace()` to create the per-user workspace on first
  run: a platform data directory (`%APPDATA%\MM-Companion` on Windows, XDG /
  Application Support elsewhere; override with `MM_COMPANION_HOME`) holding
  `settings.json`, a `characters/` dir, a `gm_characters/` dir, and a `notes/` dir. It is
  idempotent and never clobbers edited settings. `core.storage` is pure Python
  (no Qt) and computes paths itself so it works headless in CI. `save_settings`/
  `update_settings` write the file back (e.g. the UI's window `layout`, stored as
  opaque base64 strings so no Qt types leak into `core`); `load_settings` tolerates
  unknown keys.
- The app launches into `StartWindow` (`ui/start_window.py`), a standalone
  launcher: seven action buttons (Create New Character, Open Existing, Open GM
  Mode, Join Session, Manage Mods, Settings, Exit) beside a scrollable library of
  `CharacterCard`s (image, name, PL). The cards come from
  `core.library.list_saved_characters()` — the single seam
  for saved characters; it scans the workspace `characters/` dir, so the library
  shows a "No characters yet" state only when nothing is saved. "Create New
  Character" opens a `MainWindow` (`locked=False`, editable) as its own window,
  kept referenced in `_child_windows`, and **hides the launcher** behind it.
  Clicking a `CharacterCard` or "Open Existing" (a file picker) loads a saved
  character into a `locked=True` read-only sheet the same way. `MainWindow` emits
  a `closed` signal (from `closeEvent`) and a `saved` signal (after a write);
  `StartWindow` refreshes the library on both and re-shows the launcher on close.
  Right-clicking a card offers to delete it (confirmed, then the file is removed
  via `core.library.delete_character` and the library refreshes). The launcher's
  own Exit closes the app. "Open GM Mode" runs a `GMSessionLaunchDialog` and then
  opens the `GMWindow` it configured — kept in `_gm_window`, since that window
  owns the hosted session, so a second click raises it rather than building a
  second one (skipping the dialog).
- Persistence lives in `core.library` (pure Python, no Qt): `save_character`
  writes a `Character.to_dict()` as JSON into the workspace `characters/` dir —
  overwriting an explicit `path` for a plain "Save", or deriving a non-colliding
  filename from the character's name for a first save / "Save As". `load_character`,
  `delete_character`, and `display_name` (hero name → character name → "Unnamed
  Character") round it out. `MainWindow` tracks the current file and wires File →
  Open / Save / Save As through these. The **sections seed from the loaded
  model**, so opening a character repopulates characteristics, conditions, the
  image, and the advantage table (abilities/resistances/skills/profile already
  seeded).
- Character images are made self-contained: on save, `save_character` copies any
  external image into the workspace `images/` dir and rewrites `Character.image_path`
  to a bare filename; `core.library.resolve_image_path` turns that back into an
  absolute path for display (absolute paths — a just-loaded, unsaved image — pass
  through unchanged). So a saved character keeps its picture even if the original
  file moves or is deleted.
- Unsaved-change tracking: `CharacterSheet` emits `edited` on any user edit
  (`BaseInfoSection.edited` covers the profile fields, `CharacterImageSection.edited`
  the portrait, `SystemInfoSection.edited` size/hero-points/PL/PP, and
  `ConditionsSection.edited` conditions; stats/skills reuse their `changed` signal).
  `MainWindow` flags the title with `*` while dirty, clears it on save, and prompts
  Save/Discard/Cancel from `closeEvent` — a cancelled Save (or Save As dialog) leaves
  the window open. Seeding a loaded character does **not** mark it dirty (a `_loading`
  guard in the sections, plus the fact that section signals connect after
  construction).

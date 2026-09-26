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
- **The version lives under the launcher's buttons, not in its title.** A
  `VersionBadge` (`ui/version_badge.py`) shows `v<version>` in muted small print
  (`size.version`). Once the launcher is up, `__main__` calls
  `StartWindow.check_for_update()`, which runs `core.updates.check_for_update` on a
  daemon thread: one request to GitHub's `releases/latest` for this repo (which
  already skips drafts and pre-releases), its `v`-prefixed tag compared
  numerically against `__version__`. When the tag is newer, the badge says
  `v<new> available` and shows an **Update** button. Every failure — offline,
  rate-limited, a garbled reply — is silently `None`, the same as being up to date.
  The check is started from `__main__` and **never from the constructor**, so the
  many tests that build a `StartWindow` never touch the network.
- **Update installs in place** when `core.updates.can_self_update()`: a frozen
  Windows build with Inno's `unins*.exe` beside it, in the folder the registry
  records as *the* install. The installer always upgrades the registered folder, so
  anything else (a source checkout, a second install, a portable exe carried off
  somewhere) just opens the release page. The `UpdateDialog`
  (`ui/update_dialog.py`) then:
  1. downloads `MM-Companion-Setup-<ver>.exe` to `%TEMP%\MM-Companion-update\`,
     showing the app's own bar, and keeps it only if its size and SHA-256 match the
     `size`/`digest` GitHub publishes for the asset (an intact copy is reused, any
     older one deleted). Any error at all lands in the dialog — a worker that dies
     on an exception nobody named would leave the bar frozen;
  2. checks it is safe to close: a running session (GM Mode) is only ended with a
     yes, and **another copy of the app** — found by exe path in the process list,
     `core.updates.other_instances` — stops the update before anything is closed,
     because the installer only waits for the copy that asked;
  3. closes every other **parentless** window through its own `close()`, so an
     unsaved sheet prompts as usual — a Cancel there stops the update, it does not
     lose the work. Owned windows are left to their owners: a block popped out of a
     sheet is a window, and closing it directly takes the block *off the sheet*
     and refuses the close. Refusal is read as "still visible afterwards", and
     sheets go first so an unsaved NPC sheet is not asked about twice (GM Mode
     closes its NPC sheets itself and swallows their refusal). A restart a closing
     window asks for (Settings, after a theme change) is suppressed — it would
     start a second copy holding the files;
  4. starts the installer with `/SILENT /NOCANCEL /LOG=… /READYFILE=… /CANCELFILE=…
     /WAITPID=… /RELAUNCH=1` and waits. The installer's manifest is `asInvoker`
     and Inno elevates itself, so the process started here is the unelevated one,
     alive for the whole install: the ready file appearing means the permission
     prompt was accepted, the process ending without it that it was declined — the
     app stays open and says so. Cancel still works while Windows asks: it writes
     the cancel file, which the installer checks *before* writing the ready file,
     so a ready file turning up within a second of it means the installer was
     already past the check, and the app hands over after all;
  5. leaves `logs/pending-update.json` in the workspace (the version it expects to
     come back as, and the log's path) and quits with `QApplication.exit` — not
     `quit`, which in Qt 6 asks every window first. The installer waits out the
     listed processes (for a one-file portable build that includes PyInstaller's
     bootloader, the parent that holds the exe open) and replaces nothing if one
     outlives its 30 seconds; shows Inno's own progress window, without a Cancel
     button, since a cancelled install is a half-replaced one; and **relaunches the
     app whether or not the install worked**, as the *original* user, not the
     elevated one.

  The relaunched app reads the note (`StartWindow.run_startup_checks`, from
  `__main__`): coming back as the expected version is a success (the badge says
  "updated from …" and the installer is deleted), anything else a failure, shown
  with the installer's log — `logs/update-<from>-to-<to>.log`, the newest ten
  kept. `installer/mm_companion.iss` reads all those switches: the app and the
  script are one contract, and the update path only works from a release whose
  installer knows them. The check itself can be turned off (Settings → General,
  `storage.check_for_updates`). `MM_COMPANION_UPDATE_FEED` points the check at
  another "latest release" JSON, which is how the whole path is tried without
  publishing (see `docs/packaging.md`).
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
- **Two rules changes repriced saved characters, with no migration and no warning.** The
  Enhanced Senses tier corrections and Removable moving to its real per-5-points formula
  both moved what an existing build costs when the app was next opened on it. Removable's
  direction is one-way — the discount scales with the power instead of being a flat 1/2/4,
  so a Removable power *above* 5 points got cheaper (a 98-point armour by 20 rather than by
  1) and one at or below 5 points did not move at all. Both were applied deliberately, since
  the old numbers were wrong; a "your build changed" notice on load is still the thing that
  does not exist, and is what a third such correction should build.
- **One thing is deliberately left out of the file.** A *power stunt* — a temporary
  alternate effect bought with Extra Effort (see [the powers notes](powers.md)) — is
  scoped to the scene it was invented in, so `save_character` runs the serialized powers
  through `strip_stunts` before writing. `Character.to_dict()` itself still emits them,
  because it is also what undo snapshots and what a session pushes to the GM; the file is
  the only place a stunt does not belong.
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

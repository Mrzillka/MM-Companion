# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

MM-Companion is a desktop dice roller and character creator for the *Mutants &
Masterminds* TTRPG (3rd/4th edition), built with Python + PySide6 (Qt). It is in
early development: it has a character-sheet UI, a data loader, and a headless
`core` rules layer — d20 resolution, a mutable character model, character math,
point-cost accounting, and Power Level validation. Characters save to and load
from the per-user workspace as JSON (via `core.library`), wired into the File
menu and the launcher; powers are not yet modelled.

## Commands

Requires Python 3.10+. Install editable with dev deps first:

```bash
pip install -e ".[dev]"
```

- Run the app: `python -m mm_companion` (or `python run.py`, or the
  `mm-companion` console script). `run.py` is a convenience wrapper for IDE Run
  buttons — all three are equivalent.
- Run tests: `pytest`
- Run a single test: `pytest tests/test_data_loader.py::test_load_game_data_is_cached`
- Format: `black .` (line length 100)
- Lint: `ruff check .`

CI runs `ruff check .`, `black --check .`, and `xvfb-run -a pytest` across
Python 3.10–3.13. GUI tests need a display server; CI provides one via
`xvfb-run`. Run all three locally before pushing.

## Architecture: the core / data / ui split

The single most important convention. The package is layered and the
dependency direction is strictly **`ui` → `core` → `data`**:

- `src/mm_companion/core/` — rules engine. Pure Python, **no PySide6 imports**,
  must not import `ui`: `data_loader.py` (game content), `dice.py` (d20
  resolution / degrees of success), `character.py` (the mutable `Character`
  state model), `rules.py` (derived math, point costs, PL validation).
- `src/mm_companion/data/` — game *content* as JSON/YAML data files, no code.
- `src/mm_companion/ui/` — PySide6 interface. Depends on `core`; never
  implements game rules itself.

**Do not hardcode game rules content in Python.** Ability costs, skill lists,
advantage effects, power parameters, tables — all belong in data files under
`src/mm_companion/data/`, and `core/` should interpret them generically. If you
find yourself writing a big `if`/`elif` chain over skill/power names in `core/`,
that content belongs in a data file. This also keeps the licensing boundary
clean (see Licensing below).

## How data flows into the UI

- `core/data_loader.py` is the *only* entry point for game content. It parses
  the bundled JSON into frozen dataclasses (`Field`, `Characteristic`,
  `Ability`, `Resistance`, `Skill`, `Advantage`, `Condition`, and a `Costs`
  record of point costs / PL caps) aggregated in a `GameData` record.
  `load_game_data()` is `lru_cache`d — one parse per process.
- Content is aggregated from several files, loaded via `importlib.resources`
  (not filesystem paths) so it works when installed as a package: core traits
  from `placeholder.json`; the rich 4e catalogs from `skills.json`,
  `advantages.json`, and `conditions.json`; point costs and PL caps from
  `costs.json`. (`effects.json`/`modifiers.json` exist for powers but aren't
  loaded yet.)
- On launch, `__main__.main()` shows a splash and calls
  `core.storage.ensure_workspace()` to create the per-user workspace on first
  run: a platform data directory (`%APPDATA%\MM-Companion` on Windows, XDG /
  Application Support elsewhere; override with `MM_COMPANION_HOME`) holding
  `settings.json`, a `characters/` dir, and a `gm_characters/` dir. It is
  idempotent and never clobbers edited settings. `core.storage` is pure Python
  (no Qt) and computes paths itself so it works headless in CI. `save_settings`/
  `update_settings` write the file back (e.g. the UI's window `layout`, stored as
  opaque base64 strings so no Qt types leak into `core`); `load_settings` tolerates
  unknown keys.
- The app launches into `StartWindow` (`ui/start_window.py`), a standalone
  launcher: four action buttons (Create New Character, Open Existing, Open GM
  Mode, Exit) beside a scrollable library of `CharacterCard`s (image, name, PL).
  The cards come from `core.library.list_saved_characters()` — the single seam
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
  own Exit closes the app. "Open GM Mode" is still a placeholder.
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
  (`BaseInfoSection.edited` covers name/conditions/image, which don't affect the
  point build; stats/skills reuse their `changed` signal). `MainWindow` flags the
  title with `*` while dirty, clears it on save, and prompts Save/Discard/Cancel
  from `closeEvent` — a cancelled Save (or Save As dialog) leaves the window open.
  Seeding a loaded character does **not** mark it dirty (a `_loading` guard in
  `BaseInfoSection`, plus the fact that section signals connect after construction).
- UI construction: `MainWindow` → `CharacterSheet` (a **nested `QMainWindow`**
  used as a dock host) → six blocks, each a `QGroupBox` wrapped in a scrollable
  `QDockWidget`: `BaseInfoSection`, `AbilitiesSection`, `ResistancesSection`,
  `AdvantagesSection`, `SkillsSection`, `PowersSection`. The user can drag a block
  to re-dock, split, or tab it, resize it, or tear it out into its own floating
  window. Abilities/Resistances/Advantages were split out of the former
  `StatsSection`; Abilities and Resistances share the grid helpers in
  `ui/sections/stat_grid.py`. The data-driven blocks take the `GameData` and build
  widgets by iterating over the data lists — no hardcoded ability/skill names.
  Each dock has a stable `objectName` (required for `saveState`/`restoreState`),
  and `CharacterSheet.docks` maps those names to the docks. The default
  arrangement lives in `CharacterSheet._apply_default_layout()` (Base Info across
  the top, Abilities | Resistances | Advantages, then Skills, then Powers).
- Window layout persists globally: `MainWindow` saves its geometry and the sheet's
  dock arrangement (`CharacterSheet.save_layout()`, a base64 `saveState`) to the
  `layout` key in `settings.json` on close via `storage.update_settings`, and
  restores them on open (`_restore_layout`). A **View** menu offers a show/hide
  toggle per dock (`dock.toggleViewAction()`) and a **Reset Layout** action
  (`CharacterSheet.reset_layout()`). `LAYOUT_VERSION` guards `saveState`, so an
  arrangement saved before the dock set changes is rejected and the default
  applies. Because Qt signals are object-to-object, the cross-block wiring keeps
  working even when a block is floated into a separate window.
- A **Fixed Layout** toggle (Settings menu) pins the blocks in the classic
  stacked arrangement — the way the sheet looked before docking. It calls
  `CharacterSheet.set_rearrangeable(False)`, which strips each dock's
  features (no drag/float/close) and its title bar (an empty title-bar widget)
  and snaps the blocks back to the default layout; the View menu is disabled while
  fixed. The mode persists as the `layout_mode` setting (`storage.layout_mode()`,
  `flexible`/`fixed`) and is applied on open.
- `CharacterSheet` owns the mutable per-character state as a single
  `core.character.Character` and passes it to each data-driven section. The
  sections are **views over that model**: widgets seed from it and write back to
  it (abilities/resistances, skill ranks/mods/focuses, advantages, conditions)
  rather than holding character state themselves. Derived values are computed in
  `core.rules`, never in the widgets — e.g. skill totals come from
  `rules.skill_total`, not an inline formula.
- Cross-section reactivity uses Qt signals over that shared model. Ability spin
  boxes emit `AbilitiesSection.abilityChanged(key, value)` →
  `SkillsSection.set_ability_value` (refreshes skill totals) and →
  `ResistancesSection.follow_ability_change` (re-seeds bases derived from that
  ability). Each block also emits a generic `changed` signal; `CharacterSheet`
  connects them to recompute build-wide derived values (currently
  `rules.power_points_spent`, pushed into the power-points pool label via
  `BaseInfoSection.set_pool_current`). Follow this pattern — write to the model
  and emit a signal — rather than sections reaching into each other.

## Shared UI utilities and view modes (matters when adding widgets)

The `ui/` package has a small support layer that section code is expected to go
through rather than reinvent. When building new sheet widgets, use it:

- `ui/widgets.py` — shared factories (`make_spin_box`, `readonly_item`,
  `hline_separator`) that keep construction consistent and wheel-guarded. Build
  spin boxes and read-only table cells through these, not by hand.
- `ui/wheel_guard.py` — `guard_wheel(*widgets)` stops nested spin boxes, combo
  boxes, and inner tables from hijacking the page scroll: a guarded widget only
  reacts to the wheel once it has keyboard focus, otherwise the wheel is
  redirected to the enclosing page. `make_spin_box` guards by default. Each block
  is wrapped in its own `QScrollArea` inside its dock, so the guard finds a
  per-panel page to scroll.
- `ui/lock.py` — `set_widget_locked(widget, locked)` implements the read-only
  **view** mode. Locking is *not* `setEnabled(False)` (which greys a control
  out); a locked field keeps showing its value but sheds its input chrome
  (frame, spin buttons, dropdown arrow) so it reads like a label. Combo boxes
  have no native read-only mode, so it installs an event-filter interaction
  blocker.
- `ui/flow_layout.py` — a reflowing layout for wrapping widget rows.

The Lock pattern is threaded top-down: `MainWindow` owns the checkable Lock menu
action, `CharacterSheet.set_locked(bool)` fans out to each section's
`set_locked`, and sections call `set_widget_locked` on their editable widgets.
The sheet **starts locked** (a read-only viewer, not an editor). Any new section
with editable widgets should expose `set_locked` and be wired into
`CharacterSheet.set_locked`.

## Licensing boundary (matters when adding game data)

- Source code is MIT. Game data under `src/mm_companion/data/` is Open Game
  Content under the OGL 1.0a (see `LICENSE-CONTENT.md`, `docs/open_game_license.md`).
- When adding data derived from the M&M SRD: ensure it is Open Game Content,
  record provenance for the OGL Section 15, and do **not** add Product Identity
  (product names, trade dress, logos).

## Conventions

- Branches off `main`: `feature/…`, `fix/…`, `docs/…`. Commit messages in
  imperative mood.
- `.idea/` (PyCharm) is intentionally not committed. In PyCharm, mark `src/` as
  Sources Root so `import mm_companion` resolves.

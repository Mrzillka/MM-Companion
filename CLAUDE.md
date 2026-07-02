# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

MM-Companion is a desktop dice roller and character creator for the *Mutants &
Masterminds* TTRPG (3rd/4th edition), built with Python + PySide6 (Qt). It is in
early development: currently only a base character-sheet UI and a data loader
exist; there is no rules engine, dice roller, or save/load yet.

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

- `src/mm_companion/core/` — rules engine. Pure Python (dice, character math,
  cost calc, validation). **No PySide6 imports.** Must not import `ui`.
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
  `Ability`, `Resistance`, `Skill`, `Advantage`) aggregated in a `GameData`
  record. `load_game_data()` is `lru_cache`d — one parse per process.
- Data is loaded via `importlib.resources` from the `mm_companion` package
  (`data/placeholder.json` today), not by filesystem path, so it works when
  installed as a package.
- UI construction: `MainWindow` → `CharacterSheet` (a `QScrollArea`) → four
  stacked sections: `BaseInfoSection`, `StatsSection`, `SkillsSection`,
  `PowersSection`. The data-driven sections take the `GameData` and build
  widgets by iterating over the data lists — no hardcoded ability/skill names.
  (`PowersSection` takes no data yet — it is still a placeholder.)
- Cross-section reactivity uses Qt signals. `StatsSection` emits
  `abilityChanged(key, value)`; `CharacterSheet` wires it to
  `SkillsSection.set_ability_value` so skill totals stay in sync with ability
  spin boxes. Follow this signal-based pattern for section-to-section updates
  rather than sections reaching into each other.

## Shared UI utilities and view modes (matters when adding widgets)

The `ui/` package has a small support layer that section code is expected to go
through rather than reinvent. When building new sheet widgets, use it:

- `ui/widgets.py` — shared factories (`make_spin_box`, `readonly_item`,
  `hline_separator`) that keep construction consistent and wheel-guarded. Build
  spin boxes and read-only table cells through these, not by hand.
- `ui/wheel_guard.py` — `guard_wheel(*widgets)` stops nested spin boxes, combo
  boxes, and inner tables from hijacking the page scroll: a guarded widget only
  reacts to the wheel once it has keyboard focus, otherwise the wheel is
  redirected to the enclosing page. `make_spin_box` guards by default.
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

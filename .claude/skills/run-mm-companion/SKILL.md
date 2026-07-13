---
name: run-mm-companion
description: Build, launch, drive, and screenshot the MM-Companion PySide6 desktop app (Mutants & Masterminds character creator / dice roller). Use when asked to run, start, open, or screenshot the app, the character sheet, the launcher, or the Power Constructor, or to confirm a UI change works in the real running app.
---

# Run MM-Companion

MM-Companion is a **PySide6 (Qt) desktop GUI** — a launcher (`StartWindow`), an
editable character sheet (`MainWindow`), and a standalone Power Constructor. The
human entry point (`python -m mm_companion`) opens a window and blocks on
`app.exec()`, so it's useless for an agent. Drive it instead with the committed
**`driver.py`**, which builds a window, pumps the Qt event loop, saves a PNG, and
exits — no blocking, no manual clicking.

All paths below are relative to the repo root (`<unit>/`). The driver lives at
`.claude/skills/run-mm-companion/driver.py`.

## Prerequisites

- Python 3.10+ (verified on 3.13). A real display: works directly on Windows;
  on headless Linux, prefix commands with `xvfb-run -a`.
- Install the package editable with dev deps (once):

```bash
pip install -e ".[dev]"
```

`PySide6>=6.6` is the only runtime dep and comes with that install. No build
step — it's pure Python.

## Run (agent path) — the driver

Launch and screenshot any UI surface with one command:

```bash
python .claude/skills/run-mm-companion/driver.py all
```

`all` writes `_driver_shots/start.png`, `sheet.png`, and `constructor.png`.
Single surfaces:

```bash
python .claude/skills/run-mm-companion/driver.py start        # the launcher
python .claude/skills/run-mm-companion/driver.py sheet        # blank editable character sheet
python .claude/skills/run-mm-companion/driver.py constructor  # the Power Constructor
```

**Then open the PNG and actually look at it** — a blank or error frame means you
are not done.

### Driving a real flow (not just the initial frame)

`sheet-demo` shows the interaction pattern: it sets ability spin boxes through
the real section API, so the derived math cascades exactly as under a mouse.

```bash
python .claude/skills/run-mm-companion/driver.py sheet-demo
```

The resulting `_driver_shots/sheet-demo.png` shows STR 4 / STA 6 / AGL 8 →
**Abilities — 36 PP**, **Power Points 36 / 150**, **Initiative +8 (AGL)**, and
STA-derived Fortitude/Toughness of 6. To drive a different flow, add a branch in
`build()` that constructs the window and pokes its widgets before the screenshot
(sections are exposed on `win._sheet` by key, e.g. `win._sheet.abilities`,
`win._sheet.base_info`).

**Notes:**
- Output dir: `--out <dir>` (default `_driver_shots/`, git-ignored).
- The driver redirects the workspace to a throwaway temp dir
  (`MM_COMPANION_HOME`) so it never touches your real `%APPDATA%\MM-Companion`.
  Pass `--keep-home` to use the real workspace.

## Run (human path)

Only useful with a real display and a human to close the window; it blocks:

```bash
python -m mm_companion   # or: python run.py  /  mm-companion  (all equivalent)
```

Two dev shortcuts skip the launcher: `python run_character_sheet.py` (straight
to an editable sheet) and `python run_power_constructor.py` (straight to the
constructor).

## Test

```bash
pytest                          # full suite (GUI tests included)
pytest tests/test_ui_wiring.py  # a fast UI-wiring slice (~3.4s, 18 tests)
```

On headless Linux, prefix with `xvfb-run -a`. CI runs `ruff check .`,
`black --check .`, and `xvfb-run -a pytest` on Python 3.10–3.13.

## Gotchas

- **`python -m mm_companion` blocks forever** on `app.exec()`. That's the human
  path — never use it to "check" the app programmatically. Use `driver.py`.
- **`.grab()`, not `app.exec()`, for screenshots.** The driver renders a widget
  to a `QPixmap` via `widget.grab()` after pumping the event loop a few times so
  it fully paints. A single `processEvents()` isn't enough — deferred layout /
  timers leave the frame half-painted; the driver's `_pump()` loops 8×.
- **Ability keys are uppercase** (`STR`, `STA`, `AGL`, `INT`, `AWE`, `PRE`,
  `ATK`) — from the game data, not lowercase. `sheet.abilities._abilities["str"]`
  raises `KeyError`; use `"STR"`.
- **`StartWindow` needs `initialize_mods()` first.** The driver calls it (after
  `ensure_workspace()`) so trusted mods' `register_*` hooks fire before any game
  data is parsed. `MainWindow` / `PowerConstructorWindow` don't need it.
- **`MainWindow` starts locked (read-only) by default.** The driver builds it
  with `locked=False` so fields are editable — otherwise setting values no-ops.
- One known Windows-offscreen test flake exists (a block-sizes font test); it
  passes on CI under xvfb. Unrelated to launching the app.

## Troubleshooting

- `KeyError: 'str'` when driving abilities → keys are uppercase (see Gotchas).
- Screenshot is blank / half-painted → increase `_pump(app, rounds=…)` in
  `driver.py`; the window needs more event-loop turns to settle.
- `ModuleNotFoundError: mm_companion` → run `pip install -e ".[dev]"` from the
  repo root, or mark `src/` as a sources root in your IDE.

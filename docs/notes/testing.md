# Testing

Read before adding a test file, changing a `conftest.py`, or touching how tests run
locally or on CI.

## Layout

`tests/` mirrors `src/mm_companion/`, so the tests for a piece of code sit where you
would look for them:

| Folder | What is in it | Qt? |
| --- | --- | --- |
| `tests/core/` | the rules layer: character, powers, equipment, dice, data loading, mods, storage | no |
| `tests/session/` | `core/session`, the headless server, the relay, TLS | no |
| `tests/app/` | packaging, the portable-home entry point | partly: the entry point loads it |
| `tests/ui/<area>/` | widgets, by area: `canvas` (page, grid, pinned strip, wheel), `sheet` (blocks and sections), `powers` (constructor, powers and equipment boards), `dice`, `simple`, `theme` (presets, settings), `session`, `gm` (GM window, NPCs, actions), `widgets`, `windows` | yes |

Put a new test file in the folder of the code it exercises. A test that needs a
widget belongs under `tests/ui/`, because that is where the widget fixtures are.

Every folder is a package (`__init__.py`), so a helper can be imported across files
as `from tests.session.test_session_server import wait_for` — `test_scene.py` does.

## The two conftests

- **`tests/conftest.py`** never imports PySide6. It pins the Qt platform (below),
  gives every test a throwaway workspace, and holds `relay_box` and `tls_cert`.
  Because of that, `pytest -p no:pytest-qt tests/core` runs with no Qt loaded at all.
- **`tests/ui/conftest.py`** holds every widget fixture: the theme-cache reset,
  zeroed card/compact animations, suppressed roll notifications, no spare NPC
  sheet, a fresh Quick NPC memory, and the teardown that deletes leftover
  top-level windows. Its docstrings say why each one exists.

## Every machine runs the same Qt

Before anything imports PySide6, the root conftest sets — unless you already set
them yourself —

- `QT_QPA_PLATFORM=offscreen:configfile=tests/qt/screens.json`: Qt's offscreen
  platform on one 1280×1024 screen, and
- `QT_QPA_FONTDIR=tests/qt/fonts`: DejaVu Sans and Sans Mono 2.37, the only fonts
  that platform then knows.

CI uses exactly the same, so it no longer runs under `xvfb-run` or installs a fonts
package. **Why:** until 2026-10-29 local runs used the real Windows platform, which
opens real windows on the developer's desktop, has the OS clamp a window to the
monitor, and measures text in that machine's fonts. Five geometry tests failed on
every local run for weeks while passing on CI (four sized a window taller than the
screen; one GM-window test read a clamped window), and the opposite also happened
— a test passing locally while CI failed. The pins make "it fails on my machine"
mean the code, not the machine.

Two details that are easy to get wrong:

- The screen config path is **relative** (to the directory pytest runs in). Qt
  splits the platform string on `:`, so a Windows path's drive letter cuts it in
  half and Qt crashes before the first test.
- With **no** font directory, offscreen Qt on Windows has no fonts at all and
  measures text about twice as wide. If text widths look absurd, check that
  `QT_QPA_FONTDIR` reached the process.

To watch a test on the real desktop, set `QT_QPA_PLATFORM=windows` (or `xcb`)
yourself; the pins then mostly stop applying, and geometry tests may fail for the
old reasons.

What the pins do **not** cover is the **PySide6 version**. `pip install -e ".[dev]"`
installs the newest one the cap allows, so CI can be on a newer Qt than a developer
who installed weeks ago. When CI fails where local passes, compare
`pip show PySide6` with the version in the CI log first.

`tls_cert` finds `openssl` on PATH or, on Windows, the copy Git for Windows ships,
so the TLS tests run locally too.

## Running only what a change affects

The full suite takes about 16 minutes. The `affected-tests` skill
(`.claude/skills/affected-tests/`) runs only the tests a change can reach:
pytest-testmon for Python code, and its own fingerprints for game data, block
sizes, theme presets, icons and the sample mods, which testmon cannot see. Its
`SKILL.md` gives the selection rules.

**The rule:** affected tests while working; **a full run before a feature is
called done or merged into `develop`** — `pytest` locally, or CI on the branch.
A subset run can miss a test that depends on order or on a file nobody told it
about.

`.testmondata` and `.affected-tests/` are per machine and gitignored. Delete them
or pass `--full` to start over.

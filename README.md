# MM-Companion

A desktop **character creator** and **dice roller** for the *Mutants &
Masterminds* tabletop RPG (3rd / 4th edition), built with Python and PySide6.

## Status

🚧 **Early development (pre-alpha, `0.1.0`) — but functional.** The character
creator is real and usable today: you can build a character point-by-point,
assemble powers in a drag-and-drop constructor, track conditions, and save/load
your work. The rules engine, powers layer, conditions, save/load, and mod support
all work. **Dice rolling and GM Mode are not yet implemented** (the "dice roller"
half of the name is still on the roadmap — see [Future plans](#future-plans)).
Expect breaking changes between versions.

## Features (available now)

**Launcher.** The app opens on a standalone start window: create a new character,
open an existing one, or pick from a scrollable library of saved-character cards
(portrait, name, Power Level). Right-click a card to delete it. (An "Open GM Mode"
button exists but is currently a placeholder.)

**Character sheet.** The whole sheet is one scrollable page of rearrangeable
blocks — drag them around, float a block into its own window, redock it, or
show/hide blocks from the **View** menu; your layout persists between sessions.
The blocks:

- **Name & Details** and **Character Image** — profile fields and portrait.
- **System / Power Level** — Power Level, the power-point pool, size, speed,
  initiative, and hero points, with derived readouts (e.g. speed and initiative
  recompute as abilities, advantages, and powers change).
- **Abilities** and **Resistances** — point-buy grids that drive the rest of the
  sheet.
- **Conditions** — an applied-condition chip tracker.
- **Advantages** and **Skills** — data-driven tables from the 4e catalogs.
- **Powers** — your built powers as stat-block cards.

A read-only **locked** viewer and an editable mode share the same sheet; unsaved
changes are flagged in the title and prompt you on close.

**Rules engine.** A headless, pure-Python `core` layer handles d20 resolution and
degrees of success, the mutable character model, derived character math,
point-cost accounting, and Power Level validation. Game *content* — ability costs,
skills, advantages, conditions, effects, modifiers, tables — lives in editable
JSON data files, not hardcoded in Python.

**Powers.** There is no fixed catalog of powers. You assemble one in the
drag-and-drop **Power Constructor**: combine base effects with extras and flaws,
set a rank, and (for multi-effect powers) choose a structure — *independent*,
*linked*, or *array*. The engine derives the point cost, a full game-term stat
block, effective ranks, runtime on/off state, and per-power PL validation. An
active power's trait boosts flow through the entire sheet (e.g. Enhanced Strength
raises your effective Strength everywhere it matters).

**Conditions.** Apply and remove conditions from a chip tracker that understands
umbrella bundling, supersession, Hit stacking, and debilitation cascades.

**Save / load.** Characters persist to a per-user workspace as JSON. Portraits are
copied into the workspace so a saved character keeps its picture even if the
original image moves. Saving, Save As, opening, and deleting are wired through the
File menu and the launcher.

**Mods.** The app is data-first and moddable: the base ruleset loads through the
same pipeline as user-installed mods, and an in-app **Mod Manager** lets you
enable, order, and configure both data-only mods and data+Python mods. See
[`docs/modding.md`](docs/modding.md).

**Cross-platform core; Windows installer today.** The app is Python + PySide6 and
runs from source on Windows, macOS, and Linux. A packaged one-click installer
currently exists for **Windows only**.

## Install

### Windows (installer)

**[⬇ Download the latest installer](https://github.com/Mrzillka/MM-Companion/releases/latest)**
— grab `MM-Companion-Setup-<version>.exe` from the **Assets** of the newest
release, then run it. No Python is required. During setup you can add a desktop
shortcut and optionally choose a **Portable** install (a single folder that keeps
its data beside itself).

User data — settings, saved characters, and installed mods — lives in the per-user
workspace at `%APPDATA%\MM-Companion` (or a `data\` folder beside the exe for a
Portable install), so it is never overwritten by an upgrade. See
[`docs/packaging.md`](docs/packaging.md) for how the installer is built and what it
does.

### From source (all platforms)

Requires **Python 3.10+**.

```bash
# 1. Clone
git clone https://github.com/Mrzillka/MM-Companion.git
cd MM-Companion

# 2. Create and activate a virtual environment
python -m venv .venv
# Windows (PowerShell):
.venv\Scripts\Activate.ps1
# macOS / Linux:
source .venv/bin/activate

# 3. Install the package with dev dependencies (editable install)
pip install -e ".[dev]"

# 4. Run the app
python -m mm_companion   # or: python run.py   or: mm-companion

# 5. Run the test suite
pytest
```

#### PyCharm

1. Open the project folder in PyCharm.
2. Point the project interpreter at the `.venv` created above.
3. Mark **`src/`** as the *Sources Root*
   (right-click `src` → *Mark Directory as* → *Sources Root*) so imports like
   `import mm_companion` resolve correctly.
4. The `.idea/` folder is intentionally **not** committed (see `.gitignore`).

## Future plans

Direction, not commitments — roughly in priority order:

- **Dice rolling** — an integrated roller wired into the sheet and powers (attack
  and resistance checks, degrees of success), completing the "dice roller" half of
  the app.
- **GM Mode** — the currently-placeholder GM entry point, for running characters,
  NPCs, and encounters from the GM's side.
- **Online play** — a live connection between players and a GM so a table can share
  characters and rolls in real time.
- **More** — richer character exports, more of the rules surface flowing into the
  displayed sheet numbers, and continued expansion of the moddable data catalogs.

## Project layout

```
src/mm_companion/
  core/   # rules engine — dice, character model, powers, rules math,
          # conditions, data loading, workspace storage & library (no Qt)
  data/   # game data as JSON (Open Game Content — see LICENSE-CONTENT.md)
  ui/     # PySide6 user interface (launcher, sheet, power constructor)
tests/    # pytest / pytest-qt tests
docs/     # documentation, incl. modding guide and Open Game License text
installer/# Windows installer pipeline (PyInstaller + Inno Setup)
```

See [`CONTRIBUTING.md`](CONTRIBUTING.md) for the rationale behind the `core` /
`data` / `ui` split.

## Documentation

- [`CONTRIBUTING.md`](CONTRIBUTING.md) — architecture conventions and how to
  contribute.
- [`docs/modding.md`](docs/modding.md) — authoring data-only and data+Python mods.
- [`docs/mm-powers-architecture.md`](docs/mm-powers-architecture.md) — the powers model.
- [`docs/mm-conditions-design.md`](docs/mm-conditions-design.md) — the conditions system.
- [`docs/packaging.md`](docs/packaging.md) — building the Windows installer.

## License

- **Source code:** MIT — see [`LICENSE`](LICENSE).
- **Game data** under `src/mm_companion/data/`: distributed under the Open Game
  License 1.0a — see [`LICENSE-CONTENT.md`](LICENSE-CONTENT.md) and
  [`docs/open_game_license.md`](docs/open_game_license.md).

## Disclaimer

MM-Companion is an **unofficial, non-commercial fan project**. It is **not
affiliated with, sponsored by, or endorsed by Green Ronin Publishing**.
*Mutants & Masterminds* is a trademark of Green Ronin Publishing, LLC.

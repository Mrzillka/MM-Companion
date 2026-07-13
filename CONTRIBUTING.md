# Contributing to MM-Companion

Thanks for your interest in contributing! This is an early-stage, open-source
fan project. Please read the guidelines below before opening a pull request.

## Architecture: `core` / `data` / `ui`

The package is deliberately split into three layers, and keeping them separate is
the single most important convention in this project:

- **`src/mm_companion/core/`** — the rules engine. Pure Python: dice mechanics,
  the mutable character model, powers, character math, cost calculations,
  validation, conditions, and workspace storage. No PySide6 imports here.
- **`src/mm_companion/data/`** — the game *content* as data files (JSON):
  abilities, skills, advantages, conditions, effects, modifiers, costs, tables,
  etc.
- **`src/mm_companion/ui/`** — the PySide6 user interface. Depends on `core`;
  never implements game rules directly.

Dependency direction: **`ui` → `core` → `data`**. `core` must not import `ui`,
and `data` contains no code.

## Game rules content belongs in data, not in Python

**Do not hardcode game rules content in Python.** Numbers, tables, and
definitions — ability costs, skill lists, advantage effects, power parameters —
belong in JSON files under `src/mm_companion/data/`. The code in `core/` should
*interpret* that data generically.

Why:

- It keeps rules content editable without touching code.
- It keeps the licensing boundary clean: Open Game Content lives in `data/`
  under the OGL, while the code stays under MIT (see
  [`LICENSE-CONTENT.md`](LICENSE-CONTENT.md)).
- It makes the rules auditable and testable in isolation.

If you find yourself writing a big `if`/`elif` chain over skill or power names in
`core/`, that content probably belongs in a data file instead.

## How the pieces fit (extension seams)

A few subsystems have their own design docs — read the relevant one before
touching that area:

- **Mod pipeline** — the base ruleset loads through the same loader as
  user-installed mods, and mods can add data-only content or ship a Python module
  that registers new engine behaviour. Start with [`docs/modding.md`](docs/modding.md).
- **Sheet blocks** — the character sheet's blocks come from a registry
  (`src/mm_companion/ui/blocks/`), so a block can be added without editing the
  sheet; a data-only mod can even add a block via `blocks.json`.
- **Powers** — the most complex subsystem, modelled the same `core` / `data` / `ui`
  way. See [`docs/mm-powers-architecture.md`](docs/mm-powers-architecture.md).
- **Conditions** — a non-roll state tracker; see
  [`docs/mm-conditions-design.md`](docs/mm-conditions-design.md).

## Licensing of contributions

- Code contributions are made under the project's **MIT** license.
- When adding game data derived from the *Mutants & Masterminds* SRD, ensure it
  is **Open Game Content**, and record its provenance so it can be listed in the
  OGL's Section 15 (see [`docs/open_game_license.md`](docs/open_game_license.md)).
  Do **not** add Product Identity (product names, trade dress, logos, etc.).

## Development workflow

1. Set up your environment as described in the
   [README](README.md#from-source-all-platforms) (`pip install -e ".[dev]"`).
2. Create a topic branch off `main`:
   - `feature/<short-description>` for new features
   - `fix/<short-description>` for bug fixes
   - `docs/<short-description>` for documentation
3. Make your change with tests where practical. GUI tests use `pytest-qt` and
   need a display server; CI provides one via `xvfb-run`.
4. Run the three CI gates locally before pushing:
   ```bash
   ruff check .
   black --check .
   pytest
   ```
5. Write clear commit messages (imperative mood, e.g. "Add d20 roll helper").
6. Open a pull request against `main` describing **what** changed and **why**.
   Keep PRs focused and reasonably small.

## Code style

- Formatting: **black** (line length 100).
- Linting: **ruff**.
- Both are configured in [`pyproject.toml`](pyproject.toml) and run in CI across
  Python 3.10–3.13.

# Contributing to MM-Companion

Thanks for your interest in contributing! This is an early-stage, open-source
fan project. Please read the guidelines below before opening a pull request.

## Architecture: `core` / `data` / `ui`

The package is deliberately split into three layers, and keeping them separate is
the single most important convention in this project:

- **`src/mm_companion/core/`** — the rules engine. Pure Python: dice mechanics,
  character math, cost calculations, validation. No PySide6 imports here.
- **`src/mm_companion/data/`** — the game *content* as data files (JSON/YAML):
  abilities, skills, advantages, powers, costs, tables, etc.
- **`src/mm_companion/ui/`** — the PySide6 user interface. Depends on `core`;
  never implements game rules directly.

Dependency direction: **`ui` → `core` → `data`**. `core` must not import `ui`,
and `data` contains no code.

## Game rules content belongs in data, not in Python

**Do not hardcode game rules content in Python.** Numbers, tables, and
definitions — ability costs, skill lists, advantage effects, power parameters —
belong in JSON/YAML files under `src/mm_companion/data/`. The code in `core/`
should *interpret* that data generically.

Why:

- It keeps rules content editable without touching code.
- It keeps the licensing boundary clean: Open Game Content lives in `data/`
  under the OGL, while the code stays under MIT (see
  [`LICENSE-CONTENT.md`](LICENSE-CONTENT.md)).
- It makes the rules auditable and testable in isolation.

If you find yourself writing a big `if`/`elif` chain over skill or power names in
`core/`, that content probably belongs in a data file instead.

## Licensing of contributions

- Code contributions are made under the project's **MIT** license.
- When adding game data derived from the *Mutants & Masterminds* SRD, ensure it
  is **Open Game Content**, and record its provenance so it can be listed in the
  OGL's Section 15 (see [`docs/open_game_license.md`](docs/open_game_license.md)).
  Do **not** add Product Identity (product names, trade dress, logos, etc.).

## Development workflow

1. Set up your environment as described in the
   [README](README.md#development-setup) (`pip install -e ".[dev]"`).
2. Create a topic branch off `main`:
   - `feature/<short-description>` for new features
   - `fix/<short-description>` for bug fixes
   - `docs/<short-description>` for documentation
3. Make your change with tests where practical (`pytest`, `pytest-qt` for UI).
4. Format and lint before committing:
   ```bash
   black .
   ruff check .
   ```
5. Write clear commit messages (imperative mood, e.g. "Add d20 roll helper").
6. Open a pull request against `main` describing **what** changed and **why**.
   Keep PRs focused and reasonably small.

## Code style

- Formatting: **black** (line length 100).
- Linting: **ruff**.
- Both are configured in [`pyproject.toml`](pyproject.toml) and run in CI.

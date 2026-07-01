# MM-Companion

A desktop **dice roller** and **character creator** for the *Mutants &
Masterminds* tabletop RPG (3rd / 4th edition), built with Python and PySide6.

## Status

🚧 **Early development.** The project is currently structure and tooling only —
no rules engine, game data, or UI has been implemented yet. Expect breaking
changes.

## Features (planned)

- Dice roller for the *Mutants & Masterminds* d20-based system.
- Point-buy character creator with live cost / power-point tracking.
- Data-driven rules content (abilities, skills, advantages, powers) loaded from
  editable JSON/YAML files rather than hardcoded.
- Save, load, and export characters.
- Cross-platform desktop UI (Windows, macOS, Linux) via PySide6.

## Installation

> Not yet published. For now, install from source (see **Development setup**).

## Development setup

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

# 4. Run the test suite
pytest
```

### PyCharm

1. Open the project folder in PyCharm.
2. Point the project interpreter at the `.venv` created above.
3. Mark **`src/`** as the *Sources Root*
   (right-click `src` → *Mark Directory as* → *Sources Root*) so imports like
   `import mm_companion` resolve correctly.
4. The `.idea/` folder is intentionally **not** committed (see `.gitignore`).

## Project layout

```
src/mm_companion/
  core/   # rules engine (dice, character math, validation)
  data/   # game data as JSON/YAML (Open Game Content — see LICENSE-CONTENT.md)
  ui/     # PySide6 user interface
tests/    # pytest / pytest-qt tests
docs/     # documentation, incl. Open Game License text
```

See [`CONTRIBUTING.md`](CONTRIBUTING.md) for the rationale behind this split.

## License

- **Source code:** MIT — see [`LICENSE`](LICENSE).
- **Game data** under `src/mm_companion/data/`: distributed under the Open Game
  License 1.0a — see [`LICENSE-CONTENT.md`](LICENSE-CONTENT.md) and
  [`docs/open_game_license.md`](docs/open_game_license.md).

## Disclaimer

MM-Companion is an **unofficial, non-commercial fan project**. It is **not
affiliated with, sponsored by, or endorsed by Green Ronin Publishing**.
*Mutants & Masterminds* is a trademark of Green Ronin Publishing, LLC.

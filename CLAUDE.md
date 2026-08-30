# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

MM-Companion is a desktop dice roller and character creator for the *Mutants &
Masterminds* TTRPG (3rd/4th edition), built with Python + PySide6 (Qt). It is in
early development: it has a character-sheet UI, a data loader, and a headless
`core` rules layer — d20 resolution, a mutable character model, character math,
point-cost accounting, and Power Level validation. Characters save to and load
from the per-user workspace as JSON (via `core.library`), wired into the File
menu and the launcher. Powers *are* modelled now: a player assembles a `Power`
out of base effects, extras, and flaws in a drag-and-drop **Power Constructor**;
`core` derives the point cost, game-term summary, effective ranks, runtime
on/off state, and per-power PL validation, and an active power's trait boosts
flow through the whole sheet (see [the powers notes](docs/notes/powers.md)). **Equipment** rides on
that same pipeline: gear is *chosen* from a catalog rather than assembled, bought in
a second currency (Equipment Points), worn on and off by clicking a card, and rolled
like an attack power — including vehicles and installations, which are bought as
traits off their own tables (see [the equipment notes](docs/notes/equipment.md)).

## Commands

Requires Python 3.10+. Install editable with dev deps first:

```bash
pip install -e ".[dev]"
```

- Run the app: `python -m mm_companion` (or `python run.py`, or the
  `mm-companion` console script). `run.py` is a convenience wrapper for IDE Run
  buttons — all three are equivalent.
- Host a session headless: `python -m mm_companion.server` (or the
  `mm-companion-server` console script; `--help` for options). Run the public
  relay: `python -m mm_companion.relay`. Both are Qt-free and stdlib-only.
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
  state model, now carrying a `powers` list), `powers.py` (the assembled-power
  data model — `Power`, `PowerEffectInstance`, `ModifierSelection`),
  `components.py` (frozen behaviour components for effects — `Integration`,
  `TraitBoost`, and the pattern/gate constants), `rules.py` (derived math, point
  costs, PL validation, *and* all the powers math — effect/power cost, effective
  ranks, trait bonuses, game-term summaries, runtime gating).
- `src/mm_companion/data/` — game *content* as JSON/YAML data files, no code.
- `src/mm_companion/ui/` — PySide6 interface. Depends on `core`; never
  implements game rules itself.

**Do not hardcode game rules content in Python.** Ability costs, skill lists,
advantage effects, power parameters, tables — all belong in data files under
`src/mm_companion/data/`, and `core/` should interpret them generically. If you
find yourself writing a big `if`/`elif` chain over skill/power names in `core/`,
that content belongs in a data file. This also keeps the licensing boundary
clean (see Licensing below).

## Where the detail lives

**Everything below this line used to be in this file.** It is now one notes file
per subsystem under `docs/notes/`, so this file stays short enough to be read in
full. Those notes are *not* loaded automatically: **open the relevant one before
changing that area** — each records why the code is shaped the way it is, and
several of them are the only record of a bug that shaped it.

| Working notes | Read before touching |
| --- | --- |
| [Data loading and game content](docs/notes/data-and-content.md) | `core/data_loader.py`, `GameData`, or adding a content file |
| [Conditions and the damage ladder](docs/notes/conditions-and-damage.md) | conditions, damage resolution, or the display overlay they drive |
| [The workspace, the launcher and saved characters](docs/notes/workspace-and-files.md) | startup, settings, saving/loading, images, the character library |
| [The character sheet: blocks, canvas and layout](docs/notes/sheet-and-blocks.md) | adding a block, the page, the pinned strip, layout persistence |
| [The dice roller](docs/notes/dice-and-rolling.md) | the roller, the roll history, compact mode, or making something rollable |
| [Undo and redo](docs/notes/undo-and-redo.md) | adding a block, or a field to the model |
| [The powers layer](docs/notes/powers.md) | powers |
| [The equipment layer](docs/notes/equipment.md) | equipment, gear, vehicles or installations |
| [Size and movement](docs/notes/size-and-movement.md) | size, speed, or a movement effect |
| [The Notes block](docs/notes/notes-block.md) | Notes, or adding a block there can be more than one of |
| [The session layer](docs/notes/session.md) | GM Mode or online play |
| [The theme layer](docs/notes/theme.md) | writing any colour, size, radius or border |
| [The table blocks](docs/notes/table-blocks.md) | Abilities, Resistances, Advantages or Skills |
| [Shared UI utilities and view modes](docs/notes/ui-utilities.md) | adding widgets, or the lock |
| [The mod pipeline](docs/notes/mods.md) | data loading, startup, or a `register_*` seam |
| [Accepted debts](docs/notes/debts.md) | picking up agreed work that is not done yet |

Alongside them, `docs/` holds the longer-form documents the notes refer back to:
the rules design (`mm-core-mechanics`, `mm-skills-design`, `mm-advantages-design`,
`mm-conditions-design`, `mm-actions-adventure`, `mm-equipment-design`), the
subsystem architectures (`mm-powers-architecture`, `mm-equipment-architecture`,
`mm-session-architecture`), the UI designs (`mm-powers-ui-design`,
`mm-modifiers-ui-design`), the player-facing `mm-session-networking`, `packaging`,
and the mod authoring guide (`modding`, `modding-tutorial`, `sample-mods/`).

## Standing rules

These hold everywhere, whichever area you are in. Each is stated in full in the
notes file that owns it.

- **No game rules content in Python** — see the layering rule above.
- **Never hardcode a colour, radius, border width, column minimum or font size in
  widget code.** All of them are named **tokens** read from the active theme
  preset: `theme.color(…)`, `theme.metric(…)`, `theme.font_size(…)`,
  `theme.wash(…)`, `theme.box(…)`, `theme.asset(…)`. Read a token where you use
  it — never cache one in a module constant, since a preset switch would not
  reach it.
- **Widgets never compute game numbers.** Derived values come from `core.rules`;
  the sections are **views** over the one shared `core.character.Character`.
  Write to the model and emit a signal rather than having one section reach into
  another.
- **Go through the shared UI layer** rather than reinventing it: the `ui/widgets.py`
  factories, `guard_wheel`, `set_widget_locked` (locking is *not*
  `setEnabled(False)`), a `FlowLayout` hosted in a `FlowContainer`,
  `DropFeedback`/`DropIndicator` for drag targets, `discard_widget` to shed a child
  and `rebuilding` around a redraw that sheds several. Any section with editable
  widgets exposes `set_locked` and is wired into `CharacterSheet.set_locked`.
- **A widget must never be visible while it has no parent.** A parentless widget *is*
  a top-level window, so showing one flashes a real window on screen and is slow to
  realize. Add it to a layout before setting its visibility, only ever `hide()` in a
  constructor that may run before parenting, and shed it with `discard_widget`, which
  hides before it unparents. This has bitten twice; `tests/test_window_flash.py`
  watches for it.
- **A block shows all of its content and never scrolls on its own** — the page
  scrolls instead. The only deliberate exceptions are the roll histories and the
  Notes block.
- **Read a setting through its accessor in `core.storage`**, never off
  `load_settings()`: that returns the settings file *verbatim* and does not merge
  `DEFAULT_SETTINGS`, so any key added after a workspace was created reads back as
  `None`. A new setting needs an accessor or an inline fallback, or it is silently
  dead for every existing user.
- **The session layer stays Qt-free** (Qt lives only in `ui/`), takes **no new
  dependencies** (PySide6 + the stdlib), and adds **nothing under `data/`** — it is
  MIT code, not OGL content.
- **Extend an engine registry rather than editing a list** — readout kinds,
  condition mechanisms, stat appliers, config-field types and widgets, and sheet
  blocks all have a `register_*` hook a mod's Python module can call.
- **The core rulebook is a gitignored local reference, read by grep.**
  `reference/core-book.pdf` and the per-page text extracted from it under
  `reference/core-book/` are ignored by git. Answer rules questions from `docs/`
  and `docs/notes/` first; when the answer genuinely is not there, grep the
  extracted text — see the `rulebook` skill. Never `Read` the PDF itself without
  asking: it renders pages as images at ~2k tokens each. The book informs what a
  mechanic does and is never transcribed into `data/` or `docs/` (see Licensing
  boundary).

## Licensing boundary (matters when adding game data)

- Source code is MIT. Game data under `src/mm_companion/data/` is Open Game
  Content under the OGL 1.0a (see `LICENSE-CONTENT.md`, `docs/open_game_license.md`).
- When adding data derived from the M&M SRD: ensure it is Open Game Content,
  record provenance for the OGL Section 15, and do **not** add Product Identity
  (product names, trade dress, logos).

## Conventions

- Git flow: **always branch off `develop`** — `feature/…`, `fix/…`, `docs/…` —
  and merge back **into `develop`** with a `--no-ff` merge commit ("Merge
  feature/X into develop"). `main` is the release branch; only a release merge
  reaches it. Never branch from or merge into `main` for ordinary work. Commit
  messages in imperative mood.
- **Never commit directly on `main` or `develop`** — both receive merges only.
  `main` takes a merge from `develop` only on a version bump; `develop` takes
  merges only from work branches. All work happens on a work branch, over as many
  commits as it takes, and is merged into `develop` **only when the user says the
  feature is done** (the branch may then be deleted). In rare cases a branch may
  come off another work branch rather than `develop`.
- **One feature, one branch.** Don't spread a single piece of work across several
  branches — stay in the same branch until the user considers it done. Start an
  additional branch only when the task genuinely turns into something else.
- **Do not open pull requests.** When starting work, automatically switch to an
  appropriate existing branch or create a new one off `develop` (named per the
  convention above) rather than committing on `develop`/`main`. Integrate by
  merging locally, not through a PR.
- `.idea/` (PyCharm) is intentionally not committed. In PyCharm, mark `src/` as
  Sources Root so `import mm_companion` resolves.

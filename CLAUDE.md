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
flow through the whole sheet (see "The powers layer" below). **Equipment** rides on
that same pipeline: gear is *chosen* from a catalog rather than assembled, bought in
a second currency (Equipment Points), worn on and off by clicking a card, and rolled
like an attack power — including vehicles and installations, which are bought as
traits off their own tables (see "The equipment layer" below).

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

## How data flows into the UI

- `core/data_loader.py` is the *only* entry point for game content. It parses
  the bundled JSON into frozen dataclasses (`Field`, `Characteristic`,
  `Ability`, `Resistance`, `Skill`, `Advantage`, `Condition` + its mechanical
  sub-records (`ConditionParameter`, `Debilitation`, `DefenseMod`, `AttackMods`,
  `ResistanceMod`, `StackingRule`, `RecoveryCheck`, `RandomActionRow`); the powers
  records `Effect`, `Modifier`, `EffectConfigField` + its option/column helpers,
  `Measure`, `Readout`; the `Measurements`/`SizeRow` conversion tables; and a
  `Costs` record of point costs / PL caps) aggregated in a `GameData` record.
  `GameData.modifier_catalog()` merges the general and effect-specific modifier
  pools into one `id -> Modifier` lookup for cost math and summaries;
  `GameData.condition_catalog()` is the `id -> Condition` lookup the condition
  resolver walks.
  `load_game_data()` is `lru_cache`d — one parse per process.
- Content is aggregated from several files, loaded via `importlib.resources`
  (not filesystem paths) so it works when installed as a package: core traits
  from `profile.json`, `characteristics.json`, `abilities.json`,
  `resistances.json` and `system.json`; the rich 4e catalogs from `skills.json`,
  `advantages.json`, and `conditions.json`; point costs and PL caps from
  `costs.json`; rank → real-world measurement tables and the Size Table from
  `measurements.json`; and the powers layer from `effects.json` (base effects,
  each with a `statIntegration` and configurable qualities), `modifiers.json`
  (the general extra/flaw pool + game-term ladders), `effect_modifiers.json`
  (effect-specific extras/flaws, keyed by effect id), and `effect_readouts.json`
  (per-effect derived Tier-5 readouts). The powers rules and UI are documented in
  `docs/mm-powers-architecture.md`, `docs/mm-powers-ui-design.md`, and
  `docs/mm-modifiers-ui-design.md`. The gear catalog — items, stock vehicles and
  installations, their Features, and the two size tables — is `equipment.json`,
  documented in `docs/mm-equipment-architecture.md` and `docs/mm-equipment-design.md`.
- Conditions are a small state-tracker, not a build cost. `conditions.json` is the
  single consolidated catalog (short `tooltip` copy + `includes`/`supersedes` graph
  + `mechanisms`/`parameter`/`debilitates` and typed penalty/mod fields), documented
  in `docs/mm-conditions-design.md`. A character's applied conditions live on
  `Character.conditions` as a list of `AppliedCondition` (id + chosen `parameter` +
  stacking `count` + `provenance` — the flattened set with back-refs). The non-roll
  resolver in `core/rules/conditions.py` (`apply_condition`/`remove_condition`,
  `expand_includes`) bundles umbrellas, applies per-part/trait-scoped supersession,
  stacks Hit, and cascades debilitation; queryable accessors (`condition_check_penalty`,
  `condition_defense_mods`, `hit_stack_penalty`, …) compute the mods. These flow into
  the sheet as a **display-only overlay** (the build/derived math itself stays
  condition-free): the ability/resistance tables re-skin their Total column via
  `condition_scope_penalty`/`resistance_condition_effect` (`apply_stat_effects`), the
  Skills block folds the scoped penalty into its "+" column, Advantages/Powers strike
  through a `debilitated_traits` trait, and the System block's derived Speed and
  Initiative readouts overlay `condition_speed_rank_mod` (a slowed/immobilised ground
  line) and `condition_check_penalty` (an all-checks penalty on initiative), tinted red.
  `ConditionsSection` (its own block) drives it: the "+" menu applies a condition (a
  `ConditionParameterDialog` first when it needs a subject) and renders one chip per
  `AppliedCondition`; its `conditionsChanged` fans out over the signal bus so every
  overlay refreshes. That menu is built by the shared
  `conditions.build_condition_menu`, which all three "+" buttons use (this block's,
  and the GM's fast-apply on a player and an NPC card) so a condition is in the
  same place in all of them. It splits the catalog into submenus by each record's
  `group`, titled and ordered by `_meta.conditionGroups` — an *ergonomic* axis,
  orthogonal to `category`: a flat list of 36 is slow to search mid-round, while a
  category is a rules fact and stays the axis the applied chips are grouped by. An
  untagged condition is offered flat below the submenus and a ruleset declaring no
  groups gets the flat menu back, so both are purely additive. Note
  `QMenu.addMenu(title)` hands ownership *back* to the caller, so each submenu is
  constructed with the menu as its parent or it is collected out from under the
  open menu. Recovery and turn economy are out of scope for now.
- **The damage ladder is walked, not just rendered** (`core/rules/damage.py`). An
  effect's `resistanceOutcomes` was only ever *text* — the roll history said
  "Incapacitated!" and left the GM to find three conditions in a menu of 39.
  `damage_steps(data)` turns the rungs into steps (index 0 the made save, which
  still costs a Hit; 1..n the degrees of failure), `resolve_damage_step` says what
  one would put on *this* creature without applying it — which is what lets a
  button's tooltip promise it — and `apply_damage_step` puts it there through the
  ordinary `apply_condition`. Which effect is *the* damage ladder is
  `system.damage_effect`, so nothing here names an effect, a condition or a degree.
  Two rules make a rung more than a list of ids, and both read the condition graph:
  **escalation**, a rung's `escalates` map (the data form of "Stunned instead of
  Dazed if already Dazed"), chained so a rung naming `incapacitated -> dying` and
  `dying -> dead` walks a target one rung further with each failure — and gated on
  `_at_least`, "has it **or** has something that supersedes it", not on a plain
  `_has`. That distinction is the whole of a bug worth remembering: escalating
  *removes* what it escalated from (Stunned supersedes Dazed), so a plain "do they
  have Dazed?" answered no on the very next click, restarted the chain at the
  bottom, and put a Dazed back under the Stunned — the rung flickering on and off
  as the GM clicked it. And **order**,
  because a rung's printed ids are not always applicable in that order — rung 2
  reads `hit, stunned, staggered`, and applying Staggered *after* Stunned re-adds
  the Dazed inside its bundle with nothing left to supersede it, so a sibling that
  supersedes anything in another's expanded set is applied second. `ui/damage_row.py`
  is the four round buttons; the NPC card carries them in **both** states and the GM
  window's `_apply_npc_damage` resolves once and replays the settled ids onto an
  open sheet, so the two copies of the character cannot disagree about an escalation.
  Note `dead` supersedes `staggered` for this: it is the terminal rung, and without
  it the third click left a corpse Staggered.
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
  (`BaseInfoSection.edited` covers the profile fields, `CharacterImageSection.edited`
  the portrait, `SystemInfoSection.edited` size/hero-points/PL/PP, and
  `ConditionsSection.edited` conditions; stats/skills reuse their `changed` signal).
  `MainWindow` flags the title with `*` while dirty, clears it on save, and prompts
  Save/Discard/Cancel from `closeEvent` — a cancelled Save (or Save As dialog) leaves
  the window open. Seeding a loaded character does **not** mark it dirty (a `_loading`
  guard in the sections, plus the fact that section signals connect after
  construction).
- The whole sheet scrolls as **one page**, and the blocks are rearranged on a
  **custom scrollable canvas** (not Qt docking). A `QMainWindow` dock host can't
  live inside a `QScrollArea` — its drag-drop and layout break — so scroll +
  free-form drag/float/redock is done by hand instead. Each block shows **all** of
  its content and never scrolls on its own; the page scrolls vertically when the
  blocks don't all fit. `MainWindow` opens at 1000×860.
- Beside that page is the **pinned strip** (`ui/pinned_panel.py`), the one place
  that does *not* scroll: blocks parked there stay in view while the page moves
  behind them. `PinnedBoard` is what the host puts in its layout in place of the
  bare page scroll area — a splitter holding the page and a `PinnedPanel`, whose
  orientation and child order *are* the strip's edge (left/right/top/bottom) and
  whose handle sets the strip's thickness. The strip is a **small canvas, not a
  stack**: it holds `_PinnedLine`s along its length, each an inner splitter of
  blocks *across* it, so two pinned blocks sit side by side as readily as one
  under the other (a drop names a `PinSlot(new_line, line, slot)`, mirroring the
  page's `DropSlot`). Every splitter is non-collapsible and a `BlockFrame`'s
  minimum is its whole content, so a handle drag can never squash a block;
  `PinnedPanel.minimumSizeHint` reports that content minimum so it holds the
  *window* open rather than clipping (capped at the usable screen, past which the
  strip scrolls as a last resort). `align` (`fill`/`start`/`center`/`end`) places
  a block within its cell — a block that can't fill anchors to the start, the way
  a docked row left-aligns its fixed-width blocks. The remembered proportions are
  **live pixel sizes, true only of the shape they were measured in**, so a block
  *arriving* clears them along the axis it joins and lets the splitter lay that
  axis out from the blocks' own hints; sizes are kept when a block leaves (the
  survivors' values still came from one layout). Mixing a live size with a
  newcomer's natural hint is what once handed a moved block a sliver of the strip. The **`PinnedHandle` (📌) is
  always visible**: it is the empty strip's whole content, the drop target that
  gets the first block in, the grip the strip is dragged to another edge by
  (lighting four `EdgeZoneOverlay` bands), and the button that opens the strip's
  Position/Alignment/Unpin-all menu. A block also pins from its title bar's 🖈.
  **The canvas still owns the model** — the panel is a view, like `RowWidget`,
  and holds no arrangement state; `_relayout` renders the strip *first* so the
  rows are the last to claim a frame. The GM window gets the same strip, since it
  hosts the same canvas.
- UI construction: `MainWindow` → `CharacterSheet` (a `QWidget` that owns a
  `QScrollArea` → `BlockCanvas`) → twelve blocks, each a section `QGroupBox` wrapped
  in a `BlockFrame`: `BaseInfoSection`, `SystemInfoSection`, `CharacterImageSection`,
  `AbilitiesSection`, `ResistancesSection`, `ConditionsSection`, `AdvantagesSection`,
  `ComplicationsSection`, `SkillsSection`, `PowersSection`, `EquipmentSection`,
  `DiceSection`. The block set is **not** hardcoded in the sheet:
  it comes from the **block registry** (`ui/blocks/`) — one `BlockDescriptor` per
  block (key, dock title, widget factory, `BlockSize`, default row/col, and
  `default_pinned` for a block that starts in the strip instead of a row), held in an
  ordered `Registry` (`ui/blocks/registry.py`, reusing `core/registry.py`). The twelve
  base descriptors register at import; `CharacterSheet` iterates `block_descriptors()`
  to build each section (exposing it as an attribute under its key so the name-based
  cross-block wiring still reaches it) and passes `default_rows()` plus
  `default_pin_lines()` to the canvas — the page and the strip, which between them
  must cover every block exactly once. A
  mod's Python module can `register_block(BlockDescriptor)` to add a block without
  editing the sheet. A **data-only** mod can add a block with no Python at all: it
  ships a `blocks.json` (parsed into `GameData.blocks` as `BlockSpec`/`BlockFieldSpec`
  records — a titled group of field/label rows), and `CharacterSheet` calls
  `sync_declarative_blocks(data)` before iterating the registry, turning each spec
  into a generic `DeclarativeBlock` (`ui/blocks/declarative.py`) descriptor. Editable
  `"text"` rows are backed by `Character.profile[key]` (the same free-form string
  store `BaseInfoSection` uses), so they round-trip through save/load. Declarative
  blocks are strictly additive — a spec whose id collides with an existing block is
  skipped, never clobbering a base block. The base ruleset ships no `blocks.json`, so
  `GameData.blocks` is empty and the block set is unchanged. `CharacterSheet` is the central widget directly
  (no outer wrapper — the sheet's own `QScrollArea` is the page the wheel guard
  targets). The former single base-info block was split three ways: `BaseInfoSection`
  keeps the descriptive **profile** fields (name & details), `CharacterImageSection`
  holds the portrait, and `SystemInfoSection` holds the non-purchasable
  characteristics — Power Level, the power-point pool, size, speed, initiative, and
  hero points. Abilities/Resistances/Advantages were split out of the former
  `StatsSection`; Abilities and Resistances are `QTableWidget`s built through
  `ui/sections/stat_table.py` (Trait | ABL | Rank | Total, a spanned rule before the
  derived traits), which is also where the *stat-family* pieces they share with the
  Skills table live: `ROLL_ROLE`, `pin_menu_contributor`, `tint_item`, and the two
  tint tokens. Everything that is about a **table** rather than a stat is one layer
  down in `ui/sections/row_table.py` — see "The table blocks" below. The
  data-driven blocks take the `GameData` and build widgets by iterating over the
  data lists — no hardcoded ability/skill names.
- `SystemInfoSection` shows several **derived** readouts computed in `core.rules`, never
  in the widget: `speed_lines`/`speed_columns` (a base ground line plus one per active
  movement power — Flight, Speed, … — each rank expanded to walk/dash/run distances,
  with a ft-per-round ↔ km/h toggle), `initiative_modifier` (effective initiative
  ability + Improved Initiative's +4/rank; Alternate Initiative swaps the ability via a
  per-selection `AdvantageSelection.parameter`), and `effective_size` (the bought size
  shifted by an active Growth/Shrinking). It exposes `refresh_derived()` for the sheet to
  call when abilities/advantages/powers/conditions change. Movement constants live in
  `data/movement.json`; the km/h conversion reads `Measurements.distance_m`. Hero points
  render as five pips — a lit medallion for a held point, a grey one for a spent one —
  and each is **its own switch**: light the fourth alone if you like. The character
  carries a *count*, so which pips are lit is cosmetic and `HeroPointsWidget.set_value`
  (a load, a GM's command) **reconciles** to the number rather than redrawing from it.
  Every change — a click or a GM's command — funnels through
  `SystemInfoSection._on_hero_points_changed`, which is why the `note-requested` topic
  is raised there and a point can never move silently (see "Rolling from the sheet").
  `HeroPointsWidget` is shared with GM Mode's `PlayerCard`, so its pip size
  (`column.hero-point`) has to suit both.
- `ui/svg_assets.py` renders the bundled SVG artwork — the d20 and the hero-point pips —
  to pixmaps. **Eagerly**, at the screen's device pixel ratio: `QIcon` reads an SVG path
  lazily, at paint time, and `importlib.resources.as_file`'s extraction of a zipped
  install is gone by then. It also does the aspect-ratio fitting itself, since
  `QSvgRenderer` stretches to whatever rectangle it is handed and the d20 is not square.
  Each drawing comes in **variants**, held in the `DIE_VARIANTS` / `HERO_POINT_VARIANTS`
  registries, and **which one is a theme token** — `theme.asset("die")` /
  `theme.asset("hero-point")`, read at the call site (`d20_pixmap`,
  `HeroPointsWidget._render`) and never kept in a constant. The split is deliberate: this
  module owns *what drawings exist*, the preset owns *which*, so a hand-edited theme file
  names a variant id and can never point the renderer at an arbitrary path. An unknown id
  falls back rather than raising — this resolves inside a paint path. Adding a drawing is
  a file in `ui/assets/` plus an entry in the registry; the Settings combo lists it on its
  own (a friendly name in `token_editor._VARIANT_LABELS` is optional).
- `DiceSection` (`ui/sections/dice.py`) is the d20 roller **as a block**, and it is the
  one block whose descriptor sets `default_pinned` — a die that scrolls away with the
  page is no use mid-fight, so it starts in the strip. There is no standalone roller
  window any more (no `Tools` menu, no launcher button): `ui/dice_roller.py` now offers
  `DiceRollerPanel` (the roll controls — GM Mode embeds one with `hidden_option=True`),
  `LocalRollHistory` (the private list of one's own rolls), and `DiceRollerView` (a
  panel plus **whichever history is right** — the private one alone, the table's shared
  `RollHistoryPanel` in a session), which is what the block hosts. Two ways it is
  unlike its neighbours: it is **not a view over the character** (it drives `core.dice`
  and `core.storage` directly), so it publishes nothing on the bus — a roll must never
  mark the sheet dirty, though it does **serve** `roll-requested`, see "Rolling from
  the sheet" below — and its `set_locked` is a **no-op**, since rolling is a
  mid-play action like a power's on/off switch. **Its history is the only thing in the
  block allowed to scroll** — the block itself never does, and neither does the strip
  around it (see "The Dice block's height" below). That history keeps an inner scroll
  area, the same deliberate exception the GM window's `gm_rolls` block makes, with a
  `MIN_HISTORY_WIDTH` floor (a scroll area asks for nothing on its own, so without it a
  history is squeezed to an unreadable sliver). A session can be joined long
  after the block was built, so `CharacterSheet.sync_session()` fans a duck-typed
  `sync_session` out to the blocks and `attach_player_session` calls it at both ends of
  a session.
- **A history holds notes as well as rolls.** A `NoteCard` is a line nobody rolled —
  "spent a hero point — 2 left" — written by the `note-requested` topic (see "Rolling
  from the sheet"). In a session it goes to the server and comes back through the shared
  feed like any other entry; off the air `DiceSection.post_note` puts it in the private
  history instead. Both cards descend from `HistoryCard`, which is what lets
  `RollHistoryPanel.remove_roll` and the GM's ✕ strike either kind. Two things a note is
  deliberately *not*: it is never **deferred** (deferral waits on a die's tumble and a
  note has none, so holding one would hold it forever), and `_on_session_roll` **ignores
  it** — a note from one's own seat landing mid-tumble would otherwise be taken for the
  answer and settle the d20 on zero.
- **Quick rolls** are capped at `MAX_QUICK_ROLLS` (6). The cap is a layout constraint,
  not a preference: the strip shares the block with the controls and the die, so an
  unbounded chip list ratchets the block — and the pinned strip holding it — ever
  taller. A history card carries no "Save" button, only a `QuickRollStar`: `☆` muted
  (not saved), `★` washed in `accent.dice` (saved — a click takes it *out* again), or
  `☆` disabled (the strip is full). Three consequences worth knowing. The star is a
  **two-way switch**, so a card reports the click on `saveToggled` and the panel —
  which owns the strip — decides whether that was a save or an unsave
  (`toggle_quick_roll`). Identity is `quick_roll_key` (`bonus`/`penalty`/`dc` alone,
  **ignoring `name`**), so one star answers for a chip however it was later renamed;
  comparing whole entries is the old bug where a named chip and its unnamed twin were
  two rolls. And because a card cannot reach the panel (`roll_history` is the *lower*
  module — `dice_roller` imports it, never the reverse), the state is **pushed down**:
  `quickRollsChanged` → `set_quick_roll_state(keys, room)`, which both histories
  remember so a card built later starts out agreeing. Naming moved off the save path
  onto the chip's own right-click ▸ Rename… — saving is one click with no dialog.
- The Dice block **reflows to the shape of the space it is given** (`ui/reflow.py`),
  which is what lets one block work both in the tall narrow right-hand strip and in a
  short wide **bottom** one. Two nested levels, each deciding from its own width:
  `DiceRollerView` turns its splitter (roll panel vs history) and `DiceRollerPanel`
  flips its `QBoxLayout` (settings / die+readout / quick rolls). That yields three
  shapes as the room grows — one column of four, then `[panel][history]`, then one row
  of four. Three things make it work, and all three are load-bearing: `prefers_row`
  carries a **hysteresis** dead-band (a flip changes the height, which toggles a
  scrollbar, which changes the width back — an endless relayout otherwise);
  `init_reflow` sets `SetNoConstraint` on the layout, because a layout otherwise
  *imposes* its minimum on its widget and a widget in a row could then never be made
  narrow enough to leave it; and `minimumSizeHint` reports the **column** width always,
  so the widget can shrink by reflowing instead of pinning the window open. It is the
  sibling of `ui/sections/column_flow.py` (a *variable* number of panels for a list)
  and borrows that module's lessons; reuse it for any block with the same problem.
  Note `DiceRollerView._row_sizes`: the panel carries no splitter stretch, so what the
  view hands it decides whether *it* can reflow too — hence the deferred `_divide_row`
  re-run, since the strip converges its thickness over several turns and a division
  computed mid-flight is stale with no further resize coming.
- That same zero stretch is why **a change in what the panel contains must re-divide the
  splitter explicitly** (`_redivide`, which handles either axis via `_row_sizes` /
  `_column_sizes`). A splitter child with no stretch keeps the pixels it was given: when
  the panel's minimum *shrinks* the splitter simply leaves it where it was, and Qt sends
  no resize — a minimum going down provokes nothing. Removing a quick roll used to leave
  a permanent gap above the strip for exactly this reason. So anything that changes what
  the panel holds emits **`contentChanged`** and calls `updateGeometry` (what makes the
  `BlockFrame` ask again), and the view re-divides **twice** — now, and on the next turn,
  since dropped chips are `deleteLater`'d and the size hint only tells the truth once
  they are gone. A split the user dragged is still left alone (`_user_sized`).
  `quickRollsChanged` is the *sibling* topic and stays about what is *in* the strip (the
  histories' stars); conflating the two is how `load_spec` came to change the panel's
  height while telling nobody.
- A block's `min_width` in `ui/block_sizes.json` is what sets the **strip's** thickness
  for a pinned block (a page row is wide enough for anything), so `dice` is 360 — under
  that its column arrangement clips in a side strip.

### The Dice block's height (the rule: it never scrolls, and neither does the strip)

The block's minimum is *exactly* its content — no more, and no less — and the roll
history is the one thing inside it that scrolls. Four separate things make that true,
and the bug that motivated them was a single click: loading a stat into the roller
shows the spec chip, the panel grows ~50px, and the strip answered with a scrollbar.
- **The panel says when it changed size** (`contentChanged`, above). Without it the
  splitter kept handing the panel its old share *plus* the chip.
- **The history is the elastic part**, and its two heights are deliberately different
  numbers: `MIN_HISTORY_HEIGHT` (200) is what it *asks* for, `HISTORY_FLOOR_HEIGHT` (90)
  is the hard `setMinimumHeight`. It is a list with its own scroll area, so shortening it
  is the honest way to find room; pinning 200 as a minimum meant it could never give a
  pixel back.
- **A history's `sizeHint` is capped** at those two cards. A `QScrollArea` otherwise
  reports its inner widget's hint, and a block's minimum is its content's *preferred*
  height — so the block's minimum used to climb with every roll, all session. Both halves
  are one thing and live in one place: `roll_history.size_history_scroll(scroll)` plus a
  `sizeHint` returning `HISTORY_SIZE_HINT`, taken by both `RollHistoryPanel` and
  `LocalRollHistory` — as far as a layout is concerned those two *are* the same widget,
  and differ only in where their cards come from.
- **The strip re-reports its minimum** when the content below it changes
  (`PinnedPanel.eventFilter` on a `LayoutRequest` from its splitter). This is the one Qt
  cannot do itself: a `QScrollArea`'s own minimum does not depend on its child, so the
  invalidation chain frame → strip → board → window is broken exactly there, and the
  window kept a minimum computed before the block grew.

Both of the strip's scrollbars stay on `AsNeeded`. They are the documented valve for a
strip asked to hold more than the display can show — `PinnedPanel.minimumSizeHint` is
capped at the usable screen, and past that a bar beats clipping a block. What the four
fixes above buy is that the valve stops firing when the window had room all along.

## Compact mode (matters when touching the roller or either window)

The pinned strip's argument taken one step further: mid-fight the only part of the app
anyone touches is the roller, so `ui/compact.py` collapses the **whole window** to a
small, frameless, always-on-top mini roller — the Roll box across the top, the quick
rolls beside a smaller die, the history filling the rest. A **round `⤡` button floating
over the roller's bottom-right corner** enters, as does **`Ctrl+Shift+D`**; `⤢` on the
mini strip, `Esc`, or that same button leaves.
- **The button rides the roller, not the menu bar.** It sat in the bar's right-hand
  corner once and was close to invisible there — a thin grey glyph at the far end of a
  bar nobody looks at, saying nothing about dice. So `CompactOverlayButton` floats over
  the thing it acts on, the way a meeting app parks its controls over the video. It
  covers a corner of one history card, which is the price and a cheap one. Four
  consequences. It is **in no layout** — a plain child of its host, placed by hand from
  an event filter on the host's `Resize`/`Show` and `raise_()`d, so the roller is laid
  out as though it were not there (and `attach(None)` parks it back on the *window*,
  never on nothing: re-parenting to `None` promotes a widget to a top-level one, which
  both the General settings page and the tests' teardown then sweep up). It is styled by
  a **stylesheet on the widget itself**
  from tokens every preset defines (`accent`, `text.on-badge`, `border.card`) — Classic
  emits no `QToolButton` rules at all, so a theme-QSS rule would exist under some presets
  and not others, the same bargain `ui/lock.py` strikes. It sits **flush in the corner**,
  which is a choice between two overlaps rather than a way of avoiding one — the
  bottom-right of a scrolling list of cards is never empty. There it clips the tail of
  the history's own scroll bar (the down-arrow and the last of the trough), which costs
  nothing anyone reaches for since the wheel and the thumb are untouched; insetting by a
  scroll bar's width to spare it was tried and is **worse**, landing the button squarely
  on a card's `✕`, a discrete control with no other way to hit it. And
  **closing the Dice block closes the way in** — `enter()` checks
  `compact_anchor().isVisibleTo(window)`, which it has to now that a shortcut exists: the
  button being a child of the block used to enforce that on its own.
- The two ways in are **not the same affordance**. The button is the discoverable one,
  which is the whole reason it left the menu bar; `Ctrl+Shift+D` (the only shortcut in
  `src/`) is what makes compact mode reachable without a mouse at all, and it is the only
  way at all into the GM's read-only view of a player sheet, whose menu bar is
  deliberately bare.
- There is **one** button, re-parented between the roller and the mini page's scroll area
  — the same rule the roller itself follows below. Two would be two states to keep in
  step, and the one showing the wrong glyph would be whichever was off screen. The mini
  page hands over its *scroll area* rather than itself, so the button never lands on the
  `QSizeGrip` that is the only way to resize a frameless window.
- The **roll panel** carries no shrink button, and the button's host is the
  `DiceRollerView` rather than the panel for that reason: among the roll controls it cost
  a row of the panel's height in every window it appeared in — including the pinned
  strip, where height is the scarce thing. Over the view it lands on the history, which
  has room. A window's menu bar is back to holding nothing but the connection indicator.
- **The mini roller is the roller, moved.** Nothing builds a second one: the controller
  asks its *surface* to hand the live panel and history over and gives them back on the
  way out, so a loaded spec, the quick-roll strip, a tumble in flight and — the one that
  would really show — the session history and its attachment to the table all carry
  across. A second roller would have been a second seat at the table and every roll
  twice. Same borrow-and-return the canvas performs moving a block between the page, the
  strip and a floating window, including the rule that a widget still parented to a
  container Qt is about to free goes with it.
- A **surface** is anything with `release_roller()` (the panel and the history widget,
  or `None`), `restore_roller()`, and `compact_anchor()` (the widget the round button
  floats over). `CharacterSheet` duck-types over its blocks for all three like
  `sync_session`, so a mod's roller joins on the same terms; `GMWindow` answers for
  itself, but only by handing each straight to the `DiceRollerView` its Rolls block
  holds — the same view a sheet's Dice block holds, since GM Mode's roller is no longer
  a bare panel of its own (see "The GM's roller" below). A surface that lends
  nothing simply has no compact mode — including no button, since there is nowhere to put
  one. Note this is now the *only* gate: a GM's read-only view of a player sheet has a
  roller, so it has compact mode too, where the old menu-bar toggle denied it one.
- The compact arrangement is a **third shape** beside the reflow's row and column, and
  while it is in force `sync_reflow` stands down — it is chosen, not derived from the
  room available. It is also **not only a mode**: it turned out to be a good roller in
  its own right, so `dice_layout` in settings pins it everywhere. Hence two flags on the
  panel, `_window_compact` and `_prefer_compact` (now `_preference`), resolved by one
  `_apply_shape()`: leaving compact mode must not undo the preference, which a single
  flag did the first time anyone expanded the window. The page applies it
  live by walking the open top-level windows for anything answering `sync_dice_layout` —
  duck-typed, so it imports neither the sheet nor the GM window. `DiceRollerView` stands
  its reflow down the same way while its parts are out on loan (`_lent`), and
  `restore_roller` ends with the usual two-pass
  `updateGeometry` → `_redivide` → `_settle.start(0)`.
- **`dice_layout` names three shapes, not two** (`storage.DICE_LAYOUTS` — `auto`,
  `compact`, `extended`; read through `storage.dice_layout()`, edited on the Settings
  window's General page, defaulting to `auto`). Both non-auto values are the same
  bargain — a shape that was only ever a side effect of *where* the roller happened to
  be, promoted to something you can ask for anywhere. **Compact** is the mini window's,
  and is the **panel's** business (its three parts). **Extended** is the roll controls
  as a column beside a history filling the rest, which is what GM Mode always looked
  like, and is the **view's** (it pins the splitter's axis). So the preference is set in
  one place — `DiceRollerView.set_layout` — and reaches both halves from there; the
  panel's own `set_layout_preference` takes the layout *string*, not a compact flag.
  Three consequences. A chosen shape stands its reflow down at **both** levels
  (`_compact` or `_column_locked` on the panel, `_row_locked()` on the view) via
  `ReflowBox.force_reflow`, which is guarded on the current axis so calling it every
  resize costs nothing. `_row_sizes` needs an Extended branch: the panel is offered its
  **column** width, never the row-of-three width the auto branch measures. And the
  view's `minimumSizeHint` reports the **row** width while locked — the one place
  `ReflowBox`'s "always report the column, you can always narrow by reflowing" rule has
  to be turned around, because a chosen shape cannot narrow out of itself, so it holds
  the block (and through it the strip and the window) open at what it really needs.
  Compact still wins over Extended: the window shrinking beats the preference, and while
  the parts are lent the view is not locked at all.
- **The GM's roller is the sheet's roller.** GM Mode's Rolls block holds a
  `DiceRollerView(hidden_option=True, history=…)` rather than a hand-built panel beside
  a history in a fixed `QHBoxLayout`, so it reflows, splits and follows the preference
  exactly as a player's does — and the shape it used to be stuck in is now the Extended
  one anybody can pick. The `history=` keyword is the whole seam: a host that owns its
  history hands it in, and everything session-shaped in the view stands down (no
  private/shared swap, no `localRoll` card, no `detach`), because the GM's panel follows
  the bridge while there is one and the *workspace's* saved log when there is not
  (`GMWindow._refresh_rolls`) — knowledge the view has no business carrying. `_roller`
  still names the panel, so every player and NPC card reaches it unchanged. Note the
  history carries **no `setMinimumHeight`** any more: that fought the view's history
  discipline (`HISTORY_FLOOR_HEIGHT` as the hard floor, a capped `sizeHint`), which is
  what stops a block's minimum climbing with every roll — see "The Dice block's height".
- Three things the window has to get right. **Hiding the outgoing content is what frees
  it to shrink** — a hidden widget is left out of its layout's minimum, and both
  `CharacterSheet._update_min_width` and `PinnedPanel.minimumSizeHint` otherwise hold it
  open. **Window flags change before the animation, never during it**: Qt hides a window
  whose flags change and the platform recreates it, so the geometry is re-applied right
  after the `show()`. And a **maximized** window cannot be resized, so it is dropped to
  normal on the way in and re-maximized on the way out.
- A **transition is atomic**: `enter`/`leave` both open with `_settle_animation`, which
  lands whatever is still easing — geometry *and* its pending `on_finished` — before the
  next one starts. Both toggles read and write the window's geometry (`saveGeometry`
  going in, `remember_size` coming out) and a frame of an ease is neither of the two
  sizes anyone chose: toggling inside the 180 ms wrote a half-grown rectangle into the
  **shared** `layout` key, so every character sheet opened at it. Note Qt does *not* emit
  `finished` for an animation merely stopped, which is why the finisher is held on the
  controller and run by hand.
- The mini window is frameless, so it supplies what the OS frame would have: the strip
  is the drag handle and carries the title — the **only** place a caption shows, so it
  follows `windowTitleChanged` rather than being seeded once (the GM window retitles
  itself with the session it is hosting); a host wanting something shorter calls
  `set_title` *after* `setWindowTitle`, which is what `MainWindow._update_title` does to
  show `*Name` rather than the whole window title. A `QSizeGrip` is the only way to
  resize.
  Both halves live in `ui/frameless.py` (`apply_window_flags`, `size_grip_row`) because a
  **floated block makes exactly the same trade** — see below. It goes **as small as it is
  dragged** — the roller lives in a `QScrollArea`, which does not pass its child's
  minimum on, so the only floor is `compact.min-width/height` and under that the die and
  history scroll.
- **Floated blocks mostly stay.** A `BlockWindow` is a child of the *window*, not the
  sheet, so hiding the content never touched one; the controller asks its surface to
  `suspend_windows(True)`, which hides every floated block **except the ones pinned on
  top**. Read that with the other rule in hand, because together they are not what the
  sentence sounds like: on top is a floated block's **default**, so for anyone who has
  not gone out of their way, entering compact mode hides *nothing*. That is intended, and
  it is the right reading of both rules — a block popped out is a block wanted beside
  things, so it goes on sitting beside the mini roller too, and `✕` is how you say
  otherwise. What `suspend_windows` actually clears is the narrower case: the blocks a
  user explicitly sent behind, which are the ones not being read. Do not "fix" the
  test at `tests/test_compact_mode.py` that calls `set_block_on_top(…, False)` first —
  that call is the point of it.
- The **flag** matters more than what it hides, and that is the other reason
  `suspend_windows` is called on every entry regardless: `_windows_suspended` is what
  `accepts_drops` reads (next bullet), and that guard has to be on whenever the page is
  behind the mini roller.
- The blocks that stay are still **draggable**, and that is where `BlockCanvas.accepts_drops`
  earns its keep: while suspended, a drag is a plain move and nothing may land. A hidden
  widget keeps its last geometry, so `_hit_test` over where the page *used to be* still
  reports a perfectly good `DropSlot` — and the block docked into a page nobody could see
  and vanished, recoverable only from the View menu. `update_drag` shows no insert line
  and does not auto-scroll for the same reason (both would promise a landing the drop
  refuses), and entering compact mid-drag ends the gesture rather than letting it finish
  against a page that has gone.
- Persistence splits in two, and must: `compact` in settings holds the *mini* window's
  size and the always-on-top pin (read through `storage.compact_settings()`, never off
  `load_settings()`), while `saved_geometry()` hands `_persist_layout` the blob captured
  **before** the shrink — the `layout` key is shared by every sheet, so writing 380×560
  there would open every character that size. Being compact is deliberately *not*
  persisted: it is a play-time view switch like the lock, and relaunching into a
  dice-only window would leave someone hunting for the rest of the app.
- `ANIMATION_MS` is a class attribute so `tests/conftest.py` can zero it, exactly as it
  does `PowersSection.TRANSITION_MS`; a window that is not on screen jumps rather than
  eases, so a test asserts on the resting state without an event loop.

## Rolling from the sheet (matters when touching the roller or a stat block)

Any stat line on the sheet can be rolled: **click** an ability, resistance, skill or
the Initiative readout to load it into the roller's chip, **double-click** to throw
it; click a line of a power card's dice footer to roll it outright. The same rule as
everywhere applies — *the widget never computes the number*.

One click loading rather than rolling is what makes the sliders and the DC box
usable: you name the trait, then set the situational extras, then throw. A
double-click necessarily fires the single click first, and that is left alone rather
than deferred by the double-click interval — rolling loads the same spec anyway, so
the pair is load → (load + roll) on one spec, and deferring would make a plain click
feel a beat late. A power card's roll line is the deliberate exception: it is an
explicit "roll this" affordance rather than a number being read off the sheet.

- `core/rules/rolls.py` is the layer that answers "what does rolling X look like":
  a frozen `RollSpec` (`label`, `modifier`, `dc`, `kind`, `hint`, `follow_up`,
  `outcomes`) plus one builder per trait — `ability_roll`, `resistance_roll`,
  `skill_roll`, `initiative_roll`, `power_rolls`. Each folds in the **displayed**
  (condition-adjusted) number, so what is rolled always matches what the sheet
  shows, while the build math itself stays condition-free. Pure Python — provable
  without a display (`tests/test_roll_specs.py`).
- Two things a spec deliberately does *not* know, because this character's sheet
  cannot see them: an attack's **DC** (the target's Defense — `dc` stays `None` and
  the roller's DC box supplies it) and a save's **modifier** (the target's
  resistance — `modifier` is 0 and the Bonus slider supplies it).
- `power_rolls` replaced the card footer's prose-only `roll_lines`, which built
  strings like `"8 vs. Defense"` and threw the numbers away. It reads them from
  `powers_terms.effect_roll_numbers` (`check_actor`/`dc`/`attack`, factored out of
  `effect_stat_rows` so both share one computation) rather than regexing them back
  out of the sentence. `PowersSection.roll_lines` is now `[spec.label for spec in …]`,
  so what is written on a card and what its 🎲 rolls cannot drift apart.
- **Routing** goes over a second, payload-carrying channel on the block bus
  (`ui/blocks/bus.py`): `publish_request`/`serve`/`make_requester`, kept separate
  from the argless notification channel so a handler on one is never fed the
  other's arguments. A `BlockDescriptor` declares `requests` (this block asks) and
  `serves` (this block answers) alongside `publishes`/`subscribes`; five sections
  emit `rollRequested(object)` answered by `DiceSection.perform_roll`, and the four
  stat blocks also emit `loadRequested(object)` on the sibling `load-requested`
  topic, answered by `DiceSection.load_roll`. No block
  names another, and a mod block joins on the same terms. `CharacterSheet` also
  serves the topic itself, to **reopen** a closed roller (named by what it serves,
  not by its key) rather than roll where nobody can see it.
- The channel carries a third topic, `note-requested` (a `str`): the sentence for a
  hero point spent or gained, answered by `DiceSection.post_note`. It is the *only*
  entry in `bus.QUIET_REQUESTS`, which is the one thing that sets it apart from the
  two roll topics — a note is a **side effect** of an edit the user was making
  elsewhere on the sheet, so reopening a closed Dice block for one would be the app
  grabbing the screen unasked. The note reaches the session either way. `load-requested`
  is deliberately *not* quiet: someone who clicked a stat line asked to see it loaded.
- `DiceRollerPanel.roll_spec(spec)` / `load_spec(spec)` are the public way in. The
  loaded trait is **sticky**: it shows as a chip above the sliders and survives the
  roll, so the sliders can be nudged and the die thrown again. The sliders always
  **add on top** (`net = spec.modifier + bonus − penalty`, split back into a
  non-negative pair for the wire) rather than being overwritten — they are the
  situational extras, and a trait bonus can exceed their 0-20 range anyway.
- Every such roll now travels **named**: `RollRequest.label` has existed end to end
  since the session layer landed and nothing filled it; `_request_session_roll`
  passes `spec.label`, so the table sees *what* was rolled. A named quick-roll chip
  passes its name the same way. Rolling stays available in the locked read-only
  sheet and emits neither `changed` nor `edited` — it is a play action, like a
  power's on/off switch.
- **The chain, and why it is on the wire.** An attack spec carries the save it
  forces as its `follow_up`, so the history card for a hit offers a
  `🎲 Toughness vs. 18` button, and a resolved save states its outcome. Both are
  built by `roll_history.chain_widgets` — **on every card, not just one's own**,
  which is the entire point: the player rolls the attack, and the *target's* player,
  reading the same shared history, clicks the save straight off it. So the spec
  travels: `RollSpec.to_dict()` → `RollRequest.spec` → `RollRecord.spec` → every
  client. An earlier version kept it local "because it's derived data", which left
  the chip in front of the one person who had no use for it.
- The server stays **rules-free**. It validates the spec's *shape*
  (`protocol.sanitize_spec` — a key whitelist, text/ladder caps, a `follow_up` depth
  cap, since this is client-supplied data rendered on other people's screens) and
  records it opaquely. The crit adjustment and the ladder lookup happen **client-side**
  from the broadcast `die`/`degree`, which are deterministic — so every screen
  derives the same chip and the same sentence, and `python -m mm_companion.server`
  still needs no game data. Never import `core.rules` into `core/session/`.
- **Criticals** (`follow_up_for_result`): a natural 20 raises the forced save's DC by
  `system.critical_effect_bonus`; a natural 1 that still hits gives the *target*
  `system.critical_miss_resistance_bonus` on their check (a bonus to them, not a cut
  to the DC — same arithmetic, honest description). The reason is written into the
  follow-up's label, so a DC box reading 23 where the card said 18 explains itself.
- **Auto-fill.** A save spec carries `trait_key`, so clicking the chip on someone
  else's card rolls with *your own* resistance already in (`localize_spec`, installed
  on the panel via `set_localizer` — one seam, so the bus path and the chip path both
  get it; GM Mode's roller has no sheet and installs none). Off on your **own** card:
  you are not the target of your own attack, and `chain_widgets(localize=False)` drops
  the trait key rather than presenting a confident wrong number.
- Outcome ladders are **data**: an effect's optional `resistanceOutcomes` in
  `effects.json` (parsed into `ResistanceOutcome` records), one rung per degree of
  failure, the last rung covering every deeper one — plus an optional `success` rung,
  because a *made* Toughness save is not "nothing happened": the target still takes a
  Hit unless Hardened/Impervious/Impenetrable, a caveat only the rung's `note` can
  carry since this app cannot see the target's sheet. A rung either names
  `conditions` (ids from `conditions.json` — Damage's `hit`/`dazed`/… ladder) or a
  `configKey` reading the ids off the *instance* (Affliction's `degree1`/`2`/`3`,
  which the player chose when building the power). No degree ladder in Python.
- A power card puts a 🎲 only on the lines **the wielder rolls**. A resistance line
  (`RollSpec.rolled_by_target`) is written down and indented but unbuttoned — the
  wielder never makes their own target's save, and that roll reaches the person who
  does as the follow-up chip.
- **A table row rolls through `cellDoubleClicked`**, resolving what to roll from the
  payload stashed on that row's Total cell under `ROLL_ROLE` (`ui/sections/stat_table.py`)
  — a trait key for Abilities/Resistances, a `(row_id, display)` tuple for Skills. A row
  with nothing there (a spanned separator, a focused skill's group header) is simply not
  rollable. The Rank column never arrives: its spin box is a cell widget and eats the
  double-click, which unlocked is what selects the number for retyping — stealing that
  would make editing hostile.
- `ui/roll_click.py::attach_roll_click(widget, factory, sink, *, enabled=…)` is the
  one way a *loose* widget (the Initiative readout) becomes double-clickable; use it
  rather than open-coding an event filter. The factory builds the spec **at click
  time** (a spec captured when the row was built would be stale after any edit). Its
  one subtlety: a spin box is watched through `lineEdit()` as well as itself, and the
  `enabled` guard is how a caller says "only while locked".
- `ui/block_frame.py`: a `BlockFrame` wraps one section — a `TitleBar` (the drag
  handle, plus pin `🖈`, float `↗` and close `✕` buttons) above the section, no
  inner scroll area, sized to its content. A floated block moves into a
  `BlockWindow` (a top-level window owned by the sheet); its title bar reuses the
  same drag gesture, so you drag it back onto the page to re-dock.
  A floated `BlockWindow` is **frameless and can be pinned above other
  applications**, the same trade the mini roller makes and through the same
  `ui/frameless.py` helpers: a popped-out block spends its life beside somebody
  else's window, where the OS title bar is most of what makes it read as a document.
  The block's own title bar was already the drag handle and its `✕` already hides
  the block, so all the frame owed was a `QSizeGrip`. The `🖈` **means what the
  block's current home makes it mean** — pin *to the strip* on the page, pin *on
  top* in a window — and `TitleBar.set_floating` swaps the two (checkable there,
  a plain action elsewhere; `↗` hides, since a window is already popped out).
  That is one glyph honestly read, not a pun: pinning is what it says, and what it
  pins to is whatever the block is beside. In a window it opens **already lit** —
  staying on top is the default, see the canvas bullet below. It also **goes as
  small as it is dragged**, the other half of the same trade: the frame sits in a
  scroll area that scrolls *both* ways, so the only floor is `float.min-width` /
  `float.min-height` and past that the block scrolls rather than being clipped —
  exactly as the mini roller's floor is `compact.min-*`. It still *opens* at the
  block's natural size, so popping one out never changes how it reads.
- `ui/block_canvas.py`: the `BlockCanvas` is the single source of truth for the
  arrangement — `_rows` (an ordered list of rows, each an ordered list of block
  keys), `_windows` (floated blocks), `_hidden` (closed blocks), and `_pinned`
  (the strip's lines, with its `_pin_edge`/`_pin_align`/sizes). It renders a
  `RowWidget` per row (fixed-width blocks keep their size, growable blocks stretch)
  and owns the drag controller: `title_bar_pressed/moved/released` run one manual
  gesture (float-out at drag start, `_hit_test` → a `DropIndicator`, dock-on-drop,
  pin-on-drop over the strip, or leave-floating), plus edge auto-scroll. Structural
  ops `float_block`, `dock_block`, `show_block`/`hide_block`,
  `pin_block`/`unpin_block`/`set_pin_edge`/`set_pin_align`, `set_block_on_top`,
  `set_windows_suspended`, `arrangement`,
  `apply_arrangement`, `default_arrangement` are the headless-testable seams (drag
  outcomes without synthetic mouse events). **Which floated blocks stay on top lives
  in `_on_top`, keyed by block** and not on the `BlockWindow`: dragging a block out
  and docking it back destroys and rebuilds that window, so anything held on the
  window is lost the first time it moves. **A popped-out block stays on top by
  default** (`DEFAULT_ON_TOP`) — it was popped out to be read *beside* something, and
  one that sinks behind that window the moment it is clicked is no use — which is why
  `_on_top` is a `dict[str, bool]` and not a set: absence has to mean "never asked"
  rather than "no", and only an explicit `set_block_on_top` (or a restored layout
  carrying one) records the exception. Ask `_wants_on_top`, never `key in _on_top`. It
  persists in the `floating` entry as `on_top`, read tolerantly and so needing no
  `SCHEMA_VERSION` bump — the `hidden_anchors` precedent — but written **both ways**,
  since absence now means the default. Note the asymmetry: *moving* a block keeps the
  choice across a dock (popping it out again puts it back the way it was left), while
  *restoring a layout* is authoritative and clears it. The default arrangement is supplied by
  the sheet from the block registry's `default_rows()` (grouping descriptors by
  their default row/col): the Name & Details block beside the Character Image, then
  the System / Power Level block full width, the Abilities | Resistances pair, then
  Conditions, Advantages, Complications, Skills, Powers, Equipment — plus the
  registry's
  `default_pin_lines()`, which parks the **Dice Roller** block in the strip on the
  right. The GM window does the same for its **Rolls** block, through the same
  `default_pinned=` argument and for the same reason — a roller that scrolls away with
  the board is no use mid-fight — so its page holds only Players and NPCs, and
  `fill_last` now stretches the NPC cards. A block is in *either* the rows or the strip,
  never both: the arrangement
  model requires every block exactly once, so `default_arrangement()` excludes the
  pinned keys from the rows (including its trailing sweep over unplaced blocks).
- Layout persists globally as **JSON** (not Qt `saveState`): `MainWindow` saves its
  geometry and `CharacterSheet.save_layout()` (`json.dumps` of `arrangement()` —
  `{version, rows, floating, hidden, hidden_anchors, pinned{edge, lines, align,
  sizes, line_sizes, extent}}`) to the `layout` key
  in `settings.json` on close, and restores on open (`_restore_layout`).
  `restore_layout` validates (schema `SCHEMA_VERSION`; every block placed exactly
  once across rows/floating/hidden/pinned) and returns False to fall back to the
  default. Where a block *lives* is validated strictly (an unknown key or edge
  rejects the whole layout); the cosmetic numbers — the strip's sizes and
  thickness, a hidden block's anchor — degrade to defaults instead. A **View** menu has a checkable show/hide toggle per
  block (kept in sync via `BlockCanvas.block_visibility_changed`) and a **Reset
  Layout** action (`CharacterSheet.reset_layout()`). Cross-block wiring is
  object-to-object Qt signals, so it keeps working when a block is floated out.
- Each block's min/max size lives in `ui/block_sizes.json` (loaded by
  `ui/block_sizes.py::load_block_sizes`, keyed by block: `abilities`,
  `resistances`, …) and is applied to the `BlockFrame` in `block_frame.py`. A
  `max_width == min_width` pins a block's width so it can't stretch; the content
  blocks grow to fill their row. **A bound here is only ever a floor worth
  stating** — every block already reports its own content as its effective minimum
  (`BlockFrame.minimumSizeHint`), so a block that says nothing is sized entirely by
  what is in it. That is why the width floor is stated *only* in `minimumSizeHint`
  and never as a `setMinimumWidth`: an explicit minimum does not **raise** a
  widget's layout minimum, it **replaces** it (`qSmartMinSize` ends with
  `if (minSize.width() > 0) s.setWidth(minSize.width())`). A block whose content
  needed more than its JSON number therefore told every enclosing layout it did
  not — invisible on the page, where the row has slack and
  `content_minimum_width` already asks the hint, but not in the pinned strip,
  whose own minimum *is* its splitter's, so it squashed the block to the number.
  Equipment was already wider than its floor; the Extended roller is what made it
  show. Abilities and Resistances say nothing: they used to share a
  hardcoded `300×340` in both dimensions, a number compensating for the tables
  measuring themselves once at build time, and one that a denser or roomier preset
  made wrong in both directions — their tables report their real rows and columns
  now (see `AutoHeightTable`). Tweak the JSON to retune — no code change. This is UI
  config, **not** game content, so it lives under `ui/` (bundled via the
  `ui/*.json` `package-data` entry), not the OGL `data/` dir.
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
  `SystemInfoSection.set_pool_current`). Follow this pattern — write to the model
  and emit a signal — rather than sections reaching into each other.
- Powers participate in the same web both ways. A `PowersSection.changed`
  re-runs the enhancement refresh on Abilities/Resistances/Skills (an active
  trait-boosting power raises their *effective* values), and conversely an
  Abilities/Resistances/Advantages/Base-Info `changed` calls
  `PowersSection.refresh` to re-derive the power cards' numbers (a Strength-Based
  Damage folds in Strength; every attack cap tracks the character's PL/Attack).
  `refresh` only reads the model, so it never emits `changed` — no signal loop.

## The powers layer (matters when touching powers)

Powers are the most complex part, and are split the same core/data/ui way. Read
`docs/mm-powers-architecture.md` for the full model; the shape:

- There is **no fixed catalog of powers** — a player assembles a
  `core.powers.Power` (a titled, described bundle) out of parts: one or more
  `PowerEffectInstance` (a base `Effect` from `effects.json` at a chosen rank),
  each carrying its own `ModifierSelection` extras and flaws (referencing
  `modifiers.json` / `effect_modifiers.json`). This is plain, JSON-serializable
  data (`to_dict`/`from_dict`); it holds no costs — those are derived in `rules`.
- A multi-effect power has a `structure` (`independent`, `linked`, or `array`).
  The structure is the source of truth, **not** per-effect modifier chips:
  independent and linked sum their effects' costs (linked is a +0 bundle), an
  array pays the costliest effect in full plus a flat point per alternate. Cost
  math and the game-terms summary read `structure` to decide.
- `core.components.py` is an ECS-style split: effect *instances* are entities;
  the frozen **components** describing behaviour are the base effect's parsed
  `Integration` (a `statIntegration` `pattern` — `passive_permanent`,
  `passive_toggle`, `instant_action`, `resource_pool` — plus an optional
  `TraitBoost` for Enhanced-Trait / Protection), and per-instance **gate kinds**
  derived from a flaw's `gate` tag (`activation`, `removable`, `toggle`,
  `limited`). The *systems* reading these — `effect_is_active`,
  `power_trait_bonuses`, `effective_ability`, … — live in `rules`.
- **Cost** (`rules`): `effect_total_cost` = `ceil` of net per-rank cost × rank
  (with M&M's sub-1-PP/rank fraction rule) plus flat modifiers; `power_total_cost`
  folds in the structure. `effect_cost_formula` renders the human-readable
  breakdown. All numbers are data-driven (`base_cost_value`, modifier
  `cost_value`, config `cost_value` overrides) — never hardcoded.
- **Effective vs. bought**: `effect_effective_rank` adds an ability a modifier
  folds in (Strength-Based Damage → Strength) to the bought rank — this is the
  rank that sets save DCs and PL caps, while cost counts only the bought rank.
  A power's active `TraitBoost` feeds `effective_ability` / `resistance_total` /
  `skill_total`, so an Enhanced-Trait boost flows through the whole sheet; the
  power pays for it, so the boosted trait's own point cost is unchanged.
- **Runtime state** (separate from the point build): `effect.toggled_on` /
  `effect.suppressed` and `power.activated` / `power.item_present` gate whether a
  passive bonus currently applies (`effect_is_active`). The UI drives all of a
  power's gates from one "Active" switch.
- **Game-term summary**: `effect_stat_rows` / `effect_game_terms` /
  `power_game_terms` render each effect's Type/Range/Action/Duration/Check/
  Resistance with modifier and config overrides applied, tinting a field an extra
  improved (better) or a flaw limited (worse), resolving check/DC phrases to real
  numbers, and appending measures, configured qualities, trait-boost lines, and
  the Tier-5 `effect_readout_rows`.
- **Validation** (warnings for now): `power_pl_violations` (per-power attack +
  effect-rank / auto-hit rank caps, read against the wielder),
  `power_allocation_violations` (a Tier-4 effect over-spending its rank pool),
  and `power_linked_range_violations`. Whether a PL breach merely warns or blocks
  the save is the single app-wide seam `core.storage.pl_enforcement()`
  (`"warn"` / `"block"`), so it can become a settings toggle later.
- **UI**: `PowersSection` ("Add Power") launches the standalone
  `ui/power_constructor/window.py::PowerConstructorWindow` — a drag-and-drop
  brick-builder (a palette of Effect/Extra/Flaw bricks → an effect-card canvas,
  a `PowerModeBar` for the structure once ≥2 effects). It hands the finished
  `Power` back via `powerSaved`; the section appends it to the shared `Character`
  and renders a stat-block **card** (header with cost and ⚠ PL-breach marker;
  description; per effect, its extras/flaws in a column *beside* that effect's
  full game-term table, always visible, in small muted type; then a dice footer).
  The footer lists **one roll per line** (`_rolls_lines` — an attack check and the
  save it forces are separate rolls), and a power that rolls nothing gets no
  footer and no rule above it rather than a "nothing to roll" placeholder. Cards
  carry edit (reopens the constructor on a deep copy, replaced in place on save)
  and remove buttons. The constructor always gets costs from `rules`, never inline.
- The **card is the on/off switch** — there is no "Active" checkbox. Clicking a
  card's body toggles a runtime-gated power, flips a Linked group (from its group
  card), or picks an array's live alternate; `_activation_role(node, parent)`
  decides which, `""` meaning "not clickable — let the click bubble to the
  enclosing card", which is how a Linked group's members are driven by their
  group. A switched-off card *shows* it: dimmed (`QGraphicsOpacityEffect`) and a
  notch smaller, never `setEnabled(False)` (which would kill the click and grey
  the text out). That look is a **continuous** quantity —
  `_DraggableCard.set_off_progress(0..1)` interpolates opacity, type size and
  padding together — so a flip *eases* over `PowersSection.TRANSITION_MS` instead
  of cutting. Every runtime setter ends in `_rebuild_list()` (flipping one power
  can restate another card's numbers), so no card survives a toggle: the section
  instead remembers each node's on-screen progress in `_card_off` and the
  replacement card eases on from there, the running animation writing that
  progress back per frame so an interrupted flip resumes rather than snaps. Tests
  zero `TRANSITION_MS` via an autouse fixture in `tests/conftest.py`. A clickable
  card also advertises itself — a standing accent left edge, plus an accent border
  on hover (`_DraggableCard._restyle`); an inert card stays flat. Only a **leaf**
  card adds a background wash: a stylesheet background paints behind every child,
  so a filled group card would flood its whole subtree. And **exactly one card is
  lit at a time** — Qt sends no Leave to a widget the pointer merely moved deeper
  into, so `enterEvent` stands every enclosing card down and `leaveEvent` hands the
  highlight back to an ancestor still under the cursor (read from `QCursor.pos()`,
  not `underMouse()`, whose flag is already stale by then). Any
  label with an explicit size must set it on its `QFont`, **not** in a stylesheet:
  a stylesheet `font-size` outranks the card's font and would sit the transition
  out. Runtime toggling stays available in the locked read-only view — it is a
  mid-play action, not a build edit, so it emits `runtimeChanged`, not `changed`.

## The equipment layer (matters when touching equipment, gear or vehicles)

Equipment is the powers layer used a second way, not a parallel one. The full map is
`docs/mm-equipment-architecture.md`, the rules themselves are
`docs/mm-equipment-design.md`; the shape:

- **An item wraps a power.** `core.equipment.EquipmentItem` holds a real
  `core.powers.Power` in its `build`, plus `catalog_id`, `category`, `platform`,
  `accessories`, `worn`, `stacks` and `ep_override`. So `power_total_cost`,
  `effect_stat_rows`, `power_rolls`, `effect_is_active` and `power_pl_violations` all
  take `item.build` **unchanged** — a rifle is priced, drawn, rolled and validated by
  the code a Blast power is, which is why the equipment work moved no existing sheet's
  numbers. `Character.equipment` is its own list beside `Character.powers`, because
  equipment is *not* a power: a character built entirely from gear legitimately answers
  "no powers".
- **Two currencies, and they never mix.** Power Points buy ranks of the Equipment
  advantage; each rank grants `points_per_advantage_rank` (5) Equipment Points, and
  those buy the items (`equipment_budget` / `equipment_points_spent` /
  `equipment_points_remaining` in `core/rules/equipment.py`). An item's price is never a
  term in `power_points_spent` and never the reverse. Nothing raises when this breaks —
  the build total is just wrong — so both totals are asserted in
  `tests/test_equipment.py`.
- **The Removable discount is never reapplied.** The book prices gear at what its
  effects would cost *undiscounted*, precisely because the advantage already granted the
  points. `build_item_from_entry` never attaches a removable-gated flaw, and
  `item_own_ep_cost` strips one (`_undiscounted`) before pricing a build carrying one
  anyway. If the cost engine sees `removable` on an item, something has double-counted —
  and it is silent, every item simply being cheap.
- **A price has three answers, in order** (`item_own_ep_cost`): an `ep_override`; the
  catalog's **printed** price while the item is still stock (`item_is_stock` compares a
  signature of the build against what the entry would produce); otherwise the derived
  cost plus, for a platform, its bought traits. So **editing a stock item re-prices it,
  and it can go down** — the printed number sometimes bundles what the generic table
  does not price. `item_ep_cost` adds everything fitted to it and is what the budget
  counts.
- **Stat effects are data** (`core/rules/appliers.py`, the layer below `runtime`). A
  stat effect is a record (the `TraitBoost` parsed from `statIntegration`) plus an
  `apply` **kind** naming what it means; each kind is a registered `StatApplier` handed
  an `ApplyContext` and yielding `TraitContribution`s. Five ship — `bonus`, `speed`,
  `sense`, `penalty_removed`, `penalty_replaced` — with `register_stat_applier` the mod
  hook (the third instance of the `PATTERN_BEHAVIOURS` / `GATE_KINDS` pattern). The
  amount is `flat + rank × per_rank`, all data, defaulting to M&M's rule that the bonus
  *is* the rank, so an effect declaring none of it behaves exactly as it always did. An
  **unregistered** kind yields nothing rather than raising.
- **A contribution carries an `origin`** — the granting item's id — beside its `source`
  name, and `item_superseded` matches on it. Two copies of one armour share a name and an
  amount, so matching by those had *both* cards claiming to have lost while the bonus
  actually on the sheet was disowned by both. **Powers pass none**, deliberately: a
  power's card explains itself from its own build, and `tests/test_stat_appliers.py`
  asserts whole-dataclass equality on a power's contribution.
- **Gear does not stack, and that is a resolver, not an applier.**
  `build_contributions(power, char, data, stacking=, group=)` gathers a power *and* an
  item — one function, so the two can't drift — and the two keywords are the
  **granter's** terms travelling onto every contribution (a power `STACK_SUM` in
  `GROUP_POWERS`, a worn item `STACK_MAX` in `GROUP_EQUIPMENT`).
  `resolve_contributions` then nets one trait under two different rules: **within** a
  group the `sum` contributions add and the largest `max` one joins them; **between**
  groups the larger total wins outright, never the sum of the two maxima. Powers keep
  summing among themselves, which is why no existing number moved;
  `tests/test_derived_stats.py` is the guard and passes unedited. Whatever lost is
  reported in `TraitBonus.superseded` and `item_superseded`, so an outclassed item's
  card says *what* beat it — a silently inert bonus reads as a bug. The per-item
  `stacks` checkbox opts one item out and badges it homerule.
- **`worn` is runtime**, exactly like a power's `activated`: left out of `to_dict()`, a
  loaded character comes up wearing everything, and a card toggle emits `runtimeChanged`
  (not `changed`), so it works in the locked sheet and never marks it dirty. Three
  things deliberately ignore it: the **price** (sheathing a sword refunds nothing), **PL
  validation** (`offensive_builds` yields every item — a sheet that passed by sheathing
  its sword would validate nothing), and a **GM's pinned chips** (a strip must not
  rearrange itself mid-fight).
- **Accessories live on their host**, and their whole layer is `core/rules/accessories.py`
  — **below** both `rules/equipment.py` and `rules/runtime.py`, because pricing reaches
  `derived` and `derived` reads what runtime gathers, so the two cannot import each other
  and both need the merged build. A scope is a trait of the rifle, not of the character,
  so an attached accessory sits in `EquipmentItem.accessories` and leaves the loose list.
  `item_attaches_to` says where it fits, `attachment` is what it lends (both editable in
  the constructor's gear row; `ui/attachment_dialog.py` is the modifier checklist), and
  `item_effective_build` merges them **on demand** — the stored build is never rewritten,
  so detaching is lossless. It also carries the accessory's **own effects** across,
  labelled with the accessory's name, and `equipment_contributions` reads that effective
  build so a fitted trait boost is granted at all — the `origin` stays the *host's* id,
  since the host's card is what has to explain it. The catalog fallback on both fields
  needs the item to carry **neither**, which is what "saved before they existed" means:
  an empty `attachment` on an item that names somewhere to attach is a decision, not a
  gap. Its price folds into the host's, which is the only way the budget counts it at
  all; note `item_own_ep_cost` prices `item.build` and not the effective build, or the
  accessory's modifiers would be charged twice.
- **Platforms are bought as traits, not effects** (`core/rules/platforms.py`): a vehicle
  is five (Size first — it sets three baselines), an installation is two plus Features.
  `PlatformSpec` is the shape, and **stock and custom are one shape** — `item_platform`
  resolves the item's own spec or normalises the printed record into the same one, so
  there is no custom-platform branch. Carrying a spec is what makes a platform custom
  (`platform_is_stock`); a spec equal to the printed one is still stock. **Speed is
  spelled twice on purpose**: the trait on the spec, the movement as a *real effect on
  the build*, kept in step by the single writer `apply_platform` — which is what puts a
  hand-built jet's Flight on the System block's Speed readout at a Flight's price.
  `current_speed` is runtime and the one number that changes *within* a round (a moving
  vehicle's Defense Class). `vehicle_modifier_advantage_cost` is deliberately a **Power
  Point** number and is shown, never totalled.
- **UI.** `ui/cards/` is the card machinery extracted out of the Powers block and shared
  by both — the draggable card and its eased off-progress, the terms grid, the node
  list, the dice footer — knowing nothing about *what* is drawn; each drag payload's
  MIME format is a keyword argument, so two boards can't accept each other's drags.
  `ui/sections/equipment.py` is the block (budget bar; Add Equipment / Create Custom
  Item / Create Platform; auto-grouped cards whose group is the item's `category`, a
  rules fact — so a cross-group drop is refused *visibly* with
  `DropFeedback.show_reject()`, since a bare `ignore()` reads like a target that didn't
  notice). The catalog is `ui/sections/equipment_picker.py`, modeless for the reason
  `pin_picker` is. Custom gear reuses the **Power Constructor in gear mode** (an item's
  build *is* a power, so there was never a second builder), and a platform's ✎ opens a
  menu — Traits… (`ui/platform_editor.py`) or Effects… (the constructor) — because a
  platform is two editable things. The editor computes no price: it applies the spec to
  a working item and asks `item_ep_cost`, so it and the card can never disagree.
- Budget breaches **warn, never block** (`equipment_violations`, a red bar and a ⚠).
  `core.storage.equipment_enforcement()` is the one seam that could change that, beside
  `pl_enforcement()` — read it through the accessor, never off `load_settings()`.

## The session layer (matters when touching GM mode / online play)

GM Mode and online play are a self-contained subsystem, split the usual
`ui → core → data` way. The full map is `docs/mm-session-architecture.md`; the
player-facing networking/troubleshooting guide is `docs/mm-session-networking.md`.
The shape:

- `src/mm_companion/core/session/` — **pure Python, no PySide6**: `protocol.py`
  (the wire vocabulary — newline-JSON messages, `PROTOCOL_VERSION`, size caps),
  `model.py` (`SessionState`/`PlayerSlot`/`RollRecord`, two token layers),
  `store.py` (`sessions/<id>/session.json` + an appended `rolls.jsonl`), `net.py`
  (`Connection`, the `Transport`/`Listener` ABCs, `TcpTransport`), `server.py`
  (`SessionServer` — accept + reader threads, **the server rolls**, hidden rolls
  never broadcast), `client.py` (`SessionClient`), `discovery.py` (join codes,
  UPnP, `publish_session` → a `Reachability` with verbatim `advice`, and the
  `transports` registry a relay plugs into), `relay.py` (`RelayTransport`
  registered under `mmrelay://` so a relay join code just works).
- **A link is kept warm, and a dropped one comes back.** `Ping`/`Pong` existed
  from the start and nothing sent one, so a table that was merely *roleplayed*
  moved no bytes and the relay reaped it after two minutes (the deployment
  worked around that with a 4 h idle timeout). Now a client's reader thread pings
  after `net.KEEPALIVE_INTERVAL` of silence and **either** end drops a peer after
  `net.PEER_TIMEOUT` — which is also what makes a half-open link visible at all,
  since `recv` otherwise just times out forever and the GM's roster shows a ghost
  as connected. One exchange warms a whole relayed pair (the relay stamps
  `last_active` on writes too), so there is no server heartbeat. `SessionClient`
  then runs a state machine (`STATE_*`, published as `EVENT_STATE`) and redials
  along `RECONNECT_DELAYS` for `RECONNECT_WINDOW`, re-presenting the
  `player_id`/`player_token` it holds so a blip lands back in the same seat.
  **`EVENT_DISCONNECTED` means the session is over**, not that a packet went
  missing — a blip raises `EVENT_STATE` and nothing else, which is what stops a
  two-second Wi-Fi drop tearing the shared roll history out of the Dice block.
  A server that stops says so (`REASON_SESSION_CLOSED`), or a deliberate end and
  a sleeping laptop are indistinguishable. Protocol **v7** exists for this: a v6
  client never pings and would be reaped, so it is refused at the door.
- **A returning player gets their own seat back**, three ways. The client's
  redial carries the token; `JoinSessionDialog.reclaim_ids` resolves a saved seat
  from the *code text* (it used to need a click on a history row nobody knew to
  make); and on a token miss `SessionState.player_by_id_if_free` hands back the
  seat the public `player_id` names — but only an **empty**, non-GM one. The
  trade-off is written out on that method.
- `src/mm_companion/ui/` — Qt: `session_bridge.py` (`SessionBridge`, the **only**
  place core `on_event` callbacks become signals; module-level
  `active_session()`/`live_session()`), `connection_indicator.py`, `gm_window.py`,
  `npc_window.py`, `player_card.py`, `roll_history.py`, and
  `session_player.py`/`session_dialogs.py`. `SessionBridge.joined` follows the
  *socket* (False mid-blip, so a send fails honestly); `in_session` follows the
  *session*, and `live_session()` asks that one — asking `joined` meant a roll
  made during a blip rolled locally into the private history, a roll the table
  never saw and the player thought it had. `ConnectionIndicator` is the menu
  bar's corner widget (`setCornerWidget`, installed by `MainWindow` and
  `GMWindow`), and it exists because every other disconnect cue **fades** after
  ten seconds; it recomputes its whole state from the bridge on every signal
  rather than mapping signals to states one by one.
- **The two cards are one card.** A player card and an NPC card differ in what
  they may *do* (a player's is a remote snapshot the GM can only ask to change;
  an NPC's is the GM's own model, edited in place) but not in what a GM *reads*:
  both open their sheet from the **portrait** alone and both hover the same
  abilities/resistances/powers summary out of `ui/card_summary.py`. Opening used
  to be a click anywhere on an NPC card, which fought that card's own
  drag-to-reorder gesture — the two were told apart only by how far the pointer
  had moved.
- **Pinned parameters** are the strip down a card's right side: the four or five
  numbers *this* GM wants off *this* creature. `core/rules/pins.py` is the model
  — a `PinRef` names an ability, resistance, skill, initiative, defence DC or one
  of a power's rolls, and `resolve_pin` turns it into a caption, a reading and a
  `RollSpec`. **A reference, never a number**: the character underneath is live,
  so a frozen value would be right once.
- Three things a pin's *reading* has to get right. Values come off the **roll**
  builders (`resistance_roll`, not `resistance_total`), which folds condition
  overlays in for free while the build math stays condition-free — and
  `with_conditions=False` takes them back out again for the picker, which is a
  catalogue of the creature rather than a combat readout (the `RollSpec` is the
  same either way, so nothing about what a chip *rolls* moves). A **defence DC**
  is its own kind rather than a dressed-up resistance: the sheet's table shows
  the rank, and a chip quietly showing ten more would be a trap. And a pin that
  no longer resolves reads as a dash rather than vanishing — a chip that
  disappears leaves no way to remove it — which is why `PinnedValue.missing` is a
  field of its own and not `spec is None`: a **forced save** carries no spec
  either (the wielder never rolls their own target's save; it reaches the person
  who does as the attack's follow-up chip) but is perfectly well resolved.
- `ui/pin_panel.py` is the strip: **click loads** into the GM's roller and
  **double-click rolls** — the sheet's own bargain — plus drag to reorder and
  right-click to remove. A click is a release with *no* drag started and *no*
  double-click just handled, which is what keeps the four apart.
  `ui/pin_picker.py` is the modeless browser the "+" opens; it unpins as well as
  pins, and its `set_pinned` is a no-op when it already agrees — without that
  guard the card's echo rebuilds the tree *during* a toggle, deleting the row
  being restated. The chips sit in a `_ChipScroll` whose height is **set, never
  asked for** (`PinPanel._apply_cap` works it out from a real chip's height and
  `set_max_visible`, which a collapsed card caps at four): a scroll area reports a
  size hint unrelated to its content *and* is elastic in a panel that ends with a
  stretch, so left to negotiate the two split the room and a rebuilt strip
  collapsed to nothing with its chips laid out inside it, invisible. The drop
  coordinates are mapped through the scrolled host (`_drop_index`,
  `_indicator_rect`) — chip geometries are the host's, drop events are the
  panel's, and the indicator is the panel's child.
- **An NPC card collapses** (`ui/npc_card.py`). Expanded it is a good roster entry
  and a bad combat readout — a 96px portrait, a PL and two buttons, times a dozen
  mooks, is two cards on screen. Collapsed it keeps only what a GM reads mid-round:
  a thumbnail (still the *only* thing that opens the sheet), the name, an
  initiative badge, the pinned strip, the conditions and the damage row. Width is
  unchanged in both states, so cards stay column-aligned in the wrapping flow — the
  win is height. Three things worth knowing. The two name labels share **one slot**,
  exactly one showing: collapsed it elides (a wrapped name would need a second line
  the thumbnail's row height hasn't got), expanded it wraps, and giving the wrapped
  one a row of its own left a near-empty strip above the card's own name. The
  **initiative badge is a `QLabel`**, not a `QToolButton`, for the reason
  `PortraitButton` is one — a tool button wraps a word in forty pixels of chrome —
  and it swallows its press so clicking it can't start the card's drag-to-reorder.
  It is also the *only* control for initiative: left-click rolls, right-click
  clears (`initiativeCleared` → `_on_npc_initiative_cleared`, the twin of the roll
  handler), and the explicit "Initiative" button is gone rather than exist on one
  state only. And `set_collapsed` is **silent** like `PinPanel.set_pins`; only the
  caret emits `collapsedChanged`, since the owner telling a card what it already
  decided must not have the window save what it just read.
- **Right-click means "take that away"**, and the specific answer always wins over
  the general one. A condition chip sheds its condition, the initiative badge
  clears its roll, and the card itself offers Remove/Delete — so the first two
  **consume** the event rather than letting it reach the third. The chips' gesture
  is `widgets.attach_context_removal`, an event filter on a `QObject` parented to
  the chip (so it dies with it), used by both GM cards' `_ConditionChip` *and* the
  character sheet's `ConditionsSection` chips: one gesture wherever a chip appears.
  It replaced a visible `×`, which is the trade — a third of the width of a caption
  like "Hit ×3" back, at the cost of an affordance you cannot see, so the helper
  writes "Right-click to remove" into the tooltip. A chip that *cannot* be removed
  (an offline player's) still swallows the click: falling through to the card's
  "Remove player" is not what someone aiming at a chip asked for.
- The NPC block's header carries **one** Collapse all / Expand all button, whose
  caption is the action it will take — which makes it a readout of the board too.
  Anything still open means "collapse"; only a wholly shut board offers to expand.
  It tells each card **silently** and writes the whole decision once
  (`_toggle_collapse_all`), and `_refresh_collapse_all` restates it from the board
  after any change, since a caption that lies is worse than no button.
- A card's **hover summary sits on the name**, not on the card. A tooltip on the
  card fires wherever the pointer rests, so it landed over the pinned chip or the
  degree button a GM was lining up. The wrapped name takes it directly; the
  collapsed card's `ElidingLabel` takes it through `set_hover_text`, because that
  label owns its own tooltip (it shows the full caption there when the caption is
  clipped) and a plain `setToolTip` would be wiped by its next resize.
- Which cards are shrunk persists in `gm_collapsed`, keyed like `gm_pins`
  (`npc:<file name>`) and read through **`storage.gm_collapsed_cards()`** — never
  off `load_settings()`, for the reason spelled out on `gm_default_pins`. Only the
  shrunk ones are stored, so absent means expanded. It is also held on `_NpcEntry`,
  because `_refresh_npcs` destroys and rebuilds every card and anything kept on the
  widget is lost the first time an initiative is rolled. Unlike the sheet's lock and
  compact mode — deliberately *not* persisted — this is a standing judgement about
  one creature rather than one window's current view: the mooks stay shrunk and the
  villain stays open. A copy inherits it (the fourth guard wants the third guard's
  card); only a deletion forgets it.
- A GM also pins from the sheet opened off that card, by right-clicking a row.
  Two bus topics, `pin-requested` and `unpin-requested`, both served by the
  **sheet** rather than any block, since a pin's destination is outside the sheet
  entirely. Which of the two a row offers comes from `stat_table.PinMenuState`,
  fed by `CharacterSheet.set_pinned` — pushed from the card on open *and* on
  every change, so a sheet left open never offers to pin what is already there.
  The menu appears at all only once `MainWindow(pin_target=True)` says there is a
  card.
- Strips persist per card in `gm_pins`, seeded from `gm_default_pins` — the
  settings key the Settings window's **GM Mode page** edits, and the reason the NPC
  damage default is *late-bound* ("the first Damage power", resolving to the
  **attack roll** it makes — the save it forces belongs to the target), since the
  defaults are written long before the NPC is. Read it through
  **`storage.gm_default_pins()`**,
  never off `load_settings()`: that returns the settings file *verbatim* and does
  not merge `DEFAULT_SETTINGS`, so any key added after a workspace was created
  reads back as `None`. Every setting in that module has an accessor or an inline
  fallback for this reason; a new one needs the same or it is silently dead for
  every existing user. An **empty strip is persisted** rather than dropped to keep
  the file tidy: a missing key is what seeds the defaults, so skipping the empty
  ones handed a GM back the four chips they had just taken off.
- That page (`ui/settings/gm_page.py`, reached from the GM window's own
  `Settings ▸ Preferences…`) is two reorderable lists, one per card kind, and its
  shape follows from *when* a default is written. A default cannot name a power —
  a `Power.id` belongs to one character — so the picker it opens is the
  character-free `default_pin_choices(game_data)` rather than `available_pins`,
  offering the traits plus the one late-bound `select="first_damage"` row; the same
  `PinPickerDialog` serves both, with a `None` character putting it in that mode and
  hiding its "Now" column. Writing goes through `storage.set_gm_default_pins`, which
  merges per kind for the same reason the reader does, and the page always writes
  **both** kinds — an omitted kind reads back as the shipped strip, while an
  explicit `[]` is honoured. Editing the defaults deliberately leaves the board
  alone (`_pins_for` made each card's strip its own on first sight), so
  **Apply to cards on the board** is a separate, confirmed button:
  `storage.clear_gm_card_pins()` plus `GMWindow.reseed_pins_from_defaults()` on
  every open GM window — in that order, or a window still holding its old strips
  writes them back out and undoes the clear.
- `src/mm_companion/server/` and `src/mm_companion/relay/` — the two Qt-free,
  stdlib-only entrypoints (`python -m mm_companion.server` / `.relay`), each a
  thin `cli.py` around the core session server / a `selectors` byte-pump.
  `server/hub.py` is the **session hub** (`--hub`): every session in the
  workspace hosted at once, so a table outlives the GM's laptop.
- **Sessions can live on a server**, and that cost almost nothing because a relay
  join code *already* carries the session id
  (`mmrelay://host:port/<session-id>`): each session on the hub registers by
  dialling out to a relay exactly as a GM's app does — no inbound port, no new
  transport, no join-code change, and a player cannot tell the difference.
- **Creating a session needs no credential** — the server is a public utility and
  anyone who installs the app can host on it. What that costs is a rule about
  *ownership*, and it is the whole design: **creating is open, everything else
  needs the session's own `gm_token`**, handed back by the create and held by
  nobody else. Three secrets, and keeping them straight matters: the
  **`host_token`** is in the join code and everyone at the table has it; the
  **`gm_token`** claims the GM's *seat* and owns the session (a wrong one is
  **refused**, never quietly downgraded to a player — that failure "works" right
  until a hidden roll reaches the table); the **operator secret** in the server's
  `/etc` is the caretaker's override, granting the full list and deleting
  anything. Two rules fall out: **there is no way to list other people's
  sessions** (a GM's own live in `session_my_sessions` in *their app*, because a
  server-side list would hand every join code to whoever asked), and **a wrong
  token and an unknown id give the identical refusal**, or the endpoint becomes an
  oracle for which ids exist. A public box also needs brakes: a global ceiling, a
  per-connection create limit, and a sweep of sessions untouched for 30 days
  (`updated_at` moves on every join/roll/rename, so a live campaign is safe).
  A remote GM needed
  three things a local one got free: player sheets (the roster carries no
  characters, so `PlayerSnapshot` forwards each to the GM seat alone), the result
  of their own hidden roll (never broadcast), and kick/rename/cast (no wire
  message existed). `core/session/hub_client.py` is the app's side, deliberately
  unlike `client.py`: no reader thread, no events — connect, ask, read, close.
  It also carries `DEFAULT_SERVER`, the address a fresh install points at so
  someone can host without knowing anyone; it is a default, not a hardcoding.
- An idle session stays registered and joinable but sheds its roll history after
  ten minutes. The reload hangs on `SessionServer`'s `on_activate`, called
  **before** the handshake, not after: the `Welcome` carries the history, and
  sequence numbers come from the tail of that list, so reloading late would
  restart the numbering and corrupt the log.
- Deployment lives in `deploy/` (systemd units, `deploy.sh`, runbook) — tracked
  and secret-free. The operator's addresses and key paths are in a gitignored
  `SERVER.md`.
- Standing constraints: session networking stays **Qt-free and
  headless-testable** (Qt only in `ui/`), **no new dependencies** (PySide6 + the
  stdlib), and **nothing new under `data/`** — the session layer is MIT code, not
  OGL content. Verify with the fast, window-free files (`tests/test_session_*.py`,
  `tests/test_headless_server.py`); the GUI ones need `QT_QPA_PLATFORM=offscreen`.

## The theme layer (matters whenever you write a colour or a size)

**Never hardcode a colour, radius, border width, column minimum or font size in
widget code.** All of them are named **tokens** read from the active theme
preset — the same rule for the *look* that "no game rules in Python" is for the
*content*. The shape:

- `src/mm_companion/ui/theme/` — `tokens.py` (the `Theme`/`Chrome` records, plus
  `rgba`/`contrast_ratio`/`measurement_backdrops`; pure data, no Qt), `loader.py`
  (discovery, `extends` resolution, caching, *and* the workspace store —
  `theme_to_dict`/`save_workspace_theme`/`delete_workspace_theme`/
  `unique_theme_id`/`is_workspace_theme`/`shadows_bundled`), `qss.py` (builds the
  app stylesheet), `palette.py` (builds the `QPalette`), `token_meta.py` +
  `token_meta.json` (friendly labels/hints/grouping for the editor — presentation
  only, never a gate on which tokens exist), and `__init__.py` (the API).
- **Read a token where you use it**: `theme.color("tint.worse")`,
  `theme.metric("radius.card")`, `theme.font_size("size.card-name")`,
  `theme.wash("accent", 0.10)` (a translucent fill of the same hue),
  `theme.box("card.margins")` (a 4-tuple), `theme.asset("die")` (which bundled
  drawing — see `ui/svg_assets.py`). Never cache one in a module constant
  — a preset switch would not reach it. An unknown name raises `UnknownToken`
  with a did-you-mean.
- Repeated snippets live in `ui/widgets.py`: `muted_style(italic=…)`,
  `tinted_style(token, bold=…)`, `BOLD_STYLE`.
- **Presets** are JSON: bundled in `ui/theme/themes/*.json`, plus anything in the
  workspace `themes/` dir (which wins on an id clash, and whose parse failure is
  skipped, never fatal). A preset may `extends` another and restate only what it
  changes. `classic` is the default and reproduces the historical native look;
  `slate-dark`, `parchment-light` and `crimson-gold` (built around the medallion
  artwork) are `chrome.mode: "styled"`. A `_`-prefixed
  key inside a token map is an inline comment and is dropped, not parsed as a
  token. A preset the Settings window writes is a **full snapshot** (every
  resolved token, no `extends`) so it stays portable; the price is that it cannot
  inherit a token added later, which `theme._lookup` pays by falling back to the
  default preset's value before raising.
- **Three mechanisms, and which does what.** `theme.apply(app)` installs all
  three, and mixing them up is the main way to break this:
  1. **Palette** (`styled` only) — colour for ordinary widgets, reaching them
     through Qt's own inheritance. It is also what any `palette(role)` token
     value resolves against. A `system` preset installs none, so the app keeps
     following the OS light/dark setting.
  2. **Application font** — the family only. Sizes never go here.
  3. **Stylesheet** — geometry, the object-named block chrome, and the menu/tab
     classes the native Windows style paints from the *system* theme and which
     therefore ignore the palette.
- **Four rules, each guarded by a test in `tests/test_theme_qss.py`:**
  1. Never select a bare `QFrame`/`QLabel`/`QGroupBox`/`QScrollArea` — every
     nested separator and label inherits it. Use an object name or a class that
     names a whole component.
  2. Never set `font-size` in a stylesheet. It outranks the widget's `QFont`,
     which is what the powers cards animate through; use
     `QFont.setPointSizeF(theme.font_size(...))`.
  3. A semantic tint must clear **3.0:1** against its background — for a
     `system` preset that means against *both* a light and a dark window, since
     it cannot know which it is on. `tests/test_theme.py` enforces it per preset.
  4. **State a complex widget's box, give its arrow column back.** One
     `border`/`padding`/`background` on a `QSpinBox` or a `QComboBox` makes
     `QStyleSheetStyle` compute `SC_SpinBoxEditField` from the box's own padding
     rect, which knows nothing about the arrows — so the edit field spanned the
     whole widget and the `QLineEdit` was laid over both arrow buttons. The arrows
     still painted, still looked right, and the child under the cursor was the
     line edit, which took every click and ignored it. `qss._arrow_column_rules`
     restores a `padding-right` and is emitted for **every** preset, because the
     focus ring alone is box enough to trigger it (Classic was fine until a field
     was clicked into). Two things not to redo: placing the buttons yourself stops
     the platform drawing its indicator inside them (working arrows nobody can
     see), and Qt renders each border edge as a rectangle rather than mitring
     them, so the CSS-triangle substitute comes out square. **How wide that column
     is belongs to the style, not the theme** — 50px under `windows11`, 15px under
     `Fusion` — so `theme.arrow_columns(app)` measures it once and `qss.build`
     takes it; there is deliberately no token. A `make_spin_box(buttons=False)` box
     carries `theme.ARROWLESS_PROPERTY` so the sheet hands the room straight back.
     `tests/test_input_arrow_columns.py` clicks a real arrow, per preset, across
     every base style Qt ships.
- A plain `QWidget` ignores a stylesheet `background` unless it sets
  `WA_StyledBackground` (a `QFrame` honours it natively). If a wash you applied
  doesn't paint, that is why.
- **A checkable `QPushButton` must say what "checked" looks like itself.** The
  sheet states a push button's box (`_chrome_rules`) but emits no
  `QPushButton:checked` — and once a box is stated, `QStyleSheetStyle` stops
  painting the platform's sunken panel, so a lit segment paints exactly like an
  unlit one. Adding the rule app-side would not fix it either, since Classic emits
  no widget chrome at all. So a segmented control carries its own widget-level
  stylesheet from tokens *every* preset defines — `_mode_toggle_style` in
  `ui/sections/powers.py` is the worked example, the same bargain `ui/lock.py` and
  `CompactOverlayButton` strike. `QToolButton:checked` *is* in the sheet; push
  buttons are the gap.
- `ui/drop_feedback.py` — `DropFeedback` gives one drop target its idle / accept
  / **reject** styling from tokens. Use it in a `dragEnterEvent` instead of
  open-coding a highlight, and call `show_reject()` on the else branch: a bare
  `event.ignore()` is invisible whenever an ancestor accepts the drag. Its
  counterpart `DropIndicator` (same module) is the thin accent insert line —
  dress the *target* with the first, mark the *place* with the second.
- `ui/block_sizes.json` is the *baseline* for block bounds; a preset's `blocks`
  map overrides any bound. The GM window's blocks live there too, under `gm_`
  keys.
- The look is changed in the **Settings window** (`ui/settings/`, opened from a
  sheet's `Settings ▸ Preferences…`, the GM window's, or the launcher's Settings
  button): a `QListWidget` nav over a `QStackedWidget`, whose pages come from
  `window.PAGES` — `GeneralPage`, `ThemePage` and `GMPage`. Adding another area of
  settings is an
  entry in that tuple plus a `SettingsPage` subclass (`page.py`: `title`,
  `is_dirty`, `save`, `discard`, `needs_restart`). Which page it *opens on* is the
  caller's to say (`SettingsWindow(page=GMPage.title)`), which is how the GM window
  lands on its own rather than on the sheet's; a caller that names none gets the
  **first**, so the tuple's order is a decision and not an accident.
- The Themes page separates two things on purpose. **Picking** a preset writes
  through at once (`set_active_theme`), as the old menu did. **Editing** one is a
  draft: `TokenEditor` (`ui/settings/token_editor.py`) generates a form by walking
  the preset's token maps and choosing a widget from the *shape of each value* —
  never a hardcoded list, so a token added later or by a mod appears on its own.
  Each edit previews live through `theme.set_preview(draft, app)`, debounced;
  nothing is written until Save, and `closeEvent` always calls `discard()` so a
  preview never outlives its window. A filter box above the form
  (`TokenEditor.set_filter`) matches every word against the token's own dotted
  name *and* its label and hint, folding away any group box it empties; it
  survives a reload, so picking a preset does not silently widen the form back
  out under a filter that still reads `accent`. Then the usual relaunch offer
  (`ui/app_restart.py`) for widgets that styled themselves in their constructors —
  the same bargain the Mod Manager strikes.
- Bundled presets are shown **locked** (`ui/lock.py`) rather than hidden — they
  are the readable documentation of what each token is for — and Duplicate is how
  you get an editable one. `unique_theme_id` treats bundled ids as taken so a copy
  never accidentally shadows a built-in; a workspace file that deliberately does
  is still supported and the page labels its button "Revert to built-in".
- Two guards worth knowing before adding a token: a colour a `theme.wash(...)` is
  derived from must be a literal (mark it `"washed": true` in `token_meta.json`, or
  the editor will let a `palette(role)` through and it will raise inside a card's
  paint path), and `qss._chrome_rules` requires the `surface.*`/`text.primary`/
  `border.block` colours with no fallback, so flipping a Classic-derived draft to
  `styled` offers to borrow them from a shipped preset rather than raising.
- A whole new **token group** (`assets` is the fifth, after `colors`/`metrics`/
  `typography`/`blocks`) is five edits and **all five are load-bearing**: a field on
  `Theme`, the name in `loader._TOKEN_GROUPS` (which alone buys parse validation,
  `extends` merging and comment-stripping), passing it in `loader._build`, emitting it
  in `loader.theme_to_dict` — miss that one and the group silently vanishes the first
  time the Settings window saves a preset — and an accessor in `theme/__init__.py`. A
  top-level record like `chrome` is the wrong shape for anything inheritable: `_build`
  reads chrome from the preset's *own* raw dict, so it does **not** come down an
  `extends` chain.
- Screenshot it with `driver.py settings` / `settings-demo` (see the
  `run-mm-companion` skill).

## The table blocks (matters when touching Abilities, Resistances, Advantages or Skills)

Those four blocks are the same thing seen four ways — an ordered list of rows in a
`QTableWidget` that shows all of its content and never scrolls on its own — and each
used to answer the three questions a table block asks in its own way. One answer to
each now lives in **`ui/sections/row_table.py`**, which is the layer *below*
`stat_table.py` and knows nothing about stats. Build a new table block out of it
rather than growing a fifth set of answers; a block constructor will assemble
exactly these pieces.

- **`AutoHeightTable`** — reports the header plus its summed row heights as both its
  size hint and its minimum, so the block grows and the table never scrolls.
  *Reported live*, which is the point: the old `fit_table_height` did it once with
  `setFixedHeight`, before the stylesheet had touched a row, and the two stat blocks
  carried a hardcoded height "plus a little slack" to cover for it. Two flags:
  `word_wrap` re-measures wrapped rows on resize, and `fit_width` reports the summed
  column widths too — right for a table that *is* the whole block (the stat grids),
  wrong for one panel of a column flow, whose section caps its own minimum at a
  single panel. Height uses `header.isHidden()`, never `isVisible()`: a table that
  has not been shown has no visible children, and that is exactly when its minimum
  is first asked for.
- **`RowIndex`** — `(table, row, key)` entries in model order. A block that fans its
  rows across side-by-side panels has no positional row → model mapping, and the
  entry order *is* the block's order, so a drag reads its source and target
  positions straight off it. `find` matches by **identity first**: the same
  advantage can be bought twice.
- **`install_row_menu(table, *contributors)` / `build_row_menu`** — one right-click
  menu per table, composed from independent contributors (`pin_menu_contributor` in
  `stat_table.py`, `remove_contributor` here), ruled apart when more than one has
  something to say. A row every contributor passes on shows **no menu at all**.
  `build_row_menu` is the same thing without the modal `exec`, so a test can ask
  what a row offers.
- **`RowReorder`** — drag a row to a new place, *across* panels, marked with the
  shared `DropIndicator` and refused visibly with `DropFeedback.show_reject()`. Each
  block passes its own MIME format, so two blocks can never accept each other's
  rows. It holds no model: the block supplies `on_move(source, target, before)` and
  an `accepts` predicate. `move_within` is the pop/insert with the downward
  correction a drop position needs.
- **`SortControl`** — the sort combo, and the standing rule that a preset mode
  stands the drag down (`SORT_MANUAL` is the shared mode id). Sorting is a
  **permanent rewrite** of the stored order, in both blocks that have one: the new
  order is the one that saves, which makes Skills' "by total" a snapshot rather than
  a live view.

Two things the blocks add on top:

- **Advantages** dropped its ▲/▼ and "Remove" buttons for those gestures. Its picker
  keeps "Add"; removal is a thing done *to a row*, so it is on the row.
- **Skills** owns which rows are shown at all. `Character.skill_order` and
  `Character.hidden_skills` are the player's, resolved against `GameData.skills` by
  `_visible_skills()` with the same three-part rule `EquipmentSection._ordered_categories`
  follows (stored order first, unlisted names trailing in the ruleset's order, a
  stored name the ruleset no longer has *kept* rather than pruned). Both are omitted
  from `to_dict()` while empty, so a save written before this round-trips unchanged.
  Removing a skill drops its ranks, focuses and specializations — it **asks first**
  when there is anything to lose — and the `↺` button restores the rows and the
  order but never the ranks. A drag works at two levels: a skill moves among the
  skills and carries its sub-rows with it, a focus moves only within its own skill.
  The inline `✕` is gone (a focus/spec name cell is a plain item again, so the
  condition overlay can strike it); the `＋` stays, being an *add* affordance with
  nowhere else discoverable to live.
  Two rules keep a long focus name from being clipped, and both are easy to
  re-break. A focused skill's header spans from **`COL_NAME`**, not from
  `COL_ABILITY`: a `ResizeToContents` column measures a spanned cell widget as its
  own content, so parking the "Add focus…" buttons on the Ability column made that
  column as wide as *they* are, and the stretching name column paid for it. And
  `_min_col_width` budgets **every** column — the Ability column was missing from
  that sum, so the flow fitted one panel too many and the name column silently
  absorbed the shortfall. A column left out of that sum is a column the name column
  pays for.

## Shared UI utilities and view modes (matters when adding widgets)

The `ui/` package has a small support layer that section code is expected to go
through rather than reinvent. When building new sheet widgets, use it:

- `ui/widgets.py` — shared factories (`make_spin_box`, `make_double_spin_box`,
  `readonly_item`, `hline_separator`) and the shared inline style snippets
  (`muted_style`, `tinted_style`, `BOLD_STYLE`) that keep construction consistent
  and wheel-guarded. Build spin boxes and read-only table cells through these, not
  by hand.
- `ui/wheel_guard.py` — `guard_wheel(*widgets)` stops nested spin boxes, combo
  boxes, and inner tables from hijacking the page scroll: a guarded widget only
  reacts to the wheel once it has keyboard focus, otherwise the wheel is
  redirected to the enclosing page. `make_spin_box` guards by default. The guard
  walks up to the **outermost** enclosing scroll area, which is the single page
  scroll area that `CharacterSheet` owns around the whole canvas (blocks have no
  inner scroll areas of their own).
- `ui/lock.py` — `set_widget_locked(widget, locked)` implements the read-only
  **view** mode. Locking is *not* `setEnabled(False)` (which greys a control
  out); a locked field keeps showing its value but sheds its input chrome
  (frame, spin buttons, dropdown arrow) so it reads like a label. Combo boxes
  have no native read-only mode, so it installs an event-filter interaction
  blocker.
- `ui/flow_layout.py` — a reflowing layout for wrapping widget rows. **Host a
  `FlowLayout` in a `FlowContainer`, never a bare `QWidget`.** The layout answers
  `hasHeightForWidth` with *no*, on purpose: Qt evaluates that at the parent's
  **hint** width, which for a flow is one item's, so every item claims a row of its
  own and the surplus goes to whatever else shares the page. `FlowContainer` pins
  its `minimumHeight` to what the flow really wraps to at the width it was *given*
  instead, and re-takes that pin as items come and go. A bare host reports one row
  and everything below it is clipped — a `QFormLayout` row showing one of Enhanced
  Senses' twenty check boxes, a `widgetResizable` `QScrollArea` showing the first
  row of the launcher's character library with no scroll bar to reach the rest.
  Neither container asks a second time, which is why the host has to answer right.
- A word-wrapped `QLabel` inside a composite widget will be sized for one line
  and clipped: `heightForWidth` only reaches it if every layout in between agrees
  to ask, and `QFormLayout` does not reliably ask. Pin the column to a width token
  and set the label's minimum height from `label.heightForWidth(width)` — see
  `ui/settings/token_editor.py::_field_label`.
- Tests build real windows, and `conftest.py` tears them down after each one.
  `processEvents()` alone does **not** run deferred deletions, so the teardown also
  sends `QEvent.Type.DeferredDelete` explicitly — without it every window built all
  session stays alive and each new application stylesheet re-polishes all of them,
  which is quietly quadratic.

The Lock pattern is threaded top-down: `MainWindow` owns the checkable lock
action, `CharacterSheet.set_locked(bool)` fans out to each section's
`set_locked`, and sections call `set_widget_locked` on their editable widgets.
The sheet **starts locked** (a read-only viewer, not an editor). Any new section
with editable widgets should expose `set_locked` and be wired into
`CharacterSheet.set_locked`. That action lives **on the menu bar**, not in a
menu — `menu_bar.addAction(…)` with no submenu, so one click toggles it — and its
🔒/🔓 glyph *is* the state read-out (`_show_lock_state`). It is a play-time view
switch reached constantly, not a preference; a GM's read-only `gm_view` window
returns before it is built and so has none.

`set_widget_locked` sheds a field's chrome with a small **widget-level**
stylesheet (`_LOCKED_SPIN_STYLE` / `_LOCKED_COMBO_STYLE`) as well as
`setFrame`/`setButtonSymbols`, because a styled preset's application sheet states
a border, a radius and a padding that outrank both. It is deliberately not a
`[locked="true"]` rule in the theme QSS: Classic emits almost no sheet, so an
app-level rule would exist under some presets and not others.

## The mod pipeline (matters when touching data loading or startup)

The base ruleset is loaded as **the default mod** through the same pipeline that
loads user mods, so game content is fully data-first and moddable. The full
authoring guide is `docs/modding.md`; the shape:

- **Discovery/order** (`core/mods.py`, pure Python): a `Mod` is a manifest
  (`mod.json`: `id`/`name`/`version`/`priority`/`files`/optional `requires`/
  `description`/`options` + `python_module`) plus how to read its content.
  `base_mod()` is the bundled `data/mod.json`; `discover_workspace_mods()` scans
  the workspace `mods/` dir (malformed manifests skipped, never fatal);
  `active_mods()` returns base first, then enabled workspace mods in the
  **user-defined load order** (the `mod_order` setting — set by dragging in the Mod
  Manager; later applies later and wins). Manifest `priority` only *seeds* where a
  newly-added mod first lands (enabled mods not yet in `mod_order` trail the ordered
  ones by ascending `priority`).
- **Merge loader** (`core/data_loader.py`): `load_game_data()` gathers the active
  mods' content in load order and **deep-merges by record id** (`_deep_merge` —
  a later mod overrides only the fields it supplies and appends new ids; plain
  lists like `options` are replaced wholesale), then parses one `GameData`. Cached
  by the mod stack's fingerprint; invalidate with `clear_game_data_cache()` after
  enabling/disabling a mod.
- **Two mod flavors.** A **data-only** mod is pure JSON (override base files by
  reusing their names, or add a declarative sheet block via `blocks.json`). A
  **data+Python** mod also ships one `python_module` whose import-time
  `register_*` calls extend an engine registry (readout kinds, condition
  mechanisms, stat appliers, config-field types/widgets, sheet blocks — see the
  registry table in `docs/modding.md`).
- **Two settings gates** (`core/storage.DEFAULT_SETTINGS`): `enabled_mods` (ids
  whose *data* layers on) and `trusted_mods` (ids whose *Python* may be imported —
  a separate opt-in because importing runs code). `mods.set_mod_enabled` /
  `set_mod_trusted` are the toggles (disabling revokes trust). Two more settings back
  the manager: `mod_order` (the drag load order) and `mod_options`
  (`{mod_id: {option_id: value}}`, read via `mod_option_values` / written via
  `set_mod_options`). The **Mod Manager window** (`ui/mods_window.py`, opened from the
  launcher's "Manage Mods" and a sheet's `Settings ▸ Mods…`) drives all of these seams
  plus `import_mod_folder` (copy a chosen folder into `mods/`); since mods load once at
  startup, it offers an **app relaunch** on close when something changed.
  `mods.initialize_mods()` (called in `__main__.main()` after
  `ensure_workspace()`, before the first `load_game_data()`) imports the
  enabled+trusted mods' Python modules so their `register_*` hooks fire first; the
  base ruleset is implicitly trusted and an import that raises is swallowed.
- Four living examples ship under `docs/sample-mods/`: `campaign-notes` (data-only),
  `flat-bonus-readouts` (data+Python — a new readout kind) and `field-kit`
  (data+Python — a piece of gear and the stat-applier seam), all three exercised
  end-to-end by `tests/test_mod_loading.py`; plus `guardian-kit`, the finished mod
  built step by step in `docs/modding-tutorial.md`.

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

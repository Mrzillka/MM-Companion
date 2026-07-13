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
flow through the whole sheet (see "The powers layer" below).

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
  from `placeholder.json`; the rich 4e catalogs from `skills.json`,
  `advantages.json`, and `conditions.json`; point costs and PL caps from
  `costs.json`; rank → real-world measurement tables and the Size Table from
  `measurements.json`; and the powers layer from `effects.json` (base effects,
  each with a `statIntegration` and configurable qualities), `modifiers.json`
  (the general extra/flaw pool + game-term ladders), `effect_modifiers.json`
  (effect-specific extras/flaws, keyed by effect id), and `effect_readouts.json`
  (per-effect derived Tier-5 readouts). The powers rules and UI are documented in
  `docs/mm-powers-architecture.md`, `docs/mm-powers-ui-design.md`, and
  `docs/mm-modifiers-ui-design.md`.
- Conditions are a small state-tracker, not a build cost. `conditions.json` is the
  single consolidated catalog (short `tooltip` copy + `includes`/`supersedes` graph
  + `mechanisms`/`parameter`/`debilitates` and typed penalty/mod fields), documented
  in `docs/mm-conditions-design.md`. A character's applied conditions live on
  `Character.conditions` as a list of `AppliedCondition` (id + chosen `parameter` +
  stacking `count` + `provenance` — the flattened set with back-refs). The non-roll
  resolver in `core/rules.py` (`apply_condition`/`remove_condition`, `expand_includes`)
  bundles umbrellas, applies per-part/trait-scoped supersession, stacks Hit, and
  cascades debilitation; queryable accessors (`condition_check_penalty`,
  `condition_defense_mods`, `hit_stack_penalty`, …) compute the mods but do **not**
  yet flow into the sheet's displayed numbers. `ConditionsSection` (its own block)
  drives it: the "+" menu applies a condition (a `ConditionParameterDialog` first
  when it needs a subject) and renders one chip per `AppliedCondition`. Dice/recovery/
  turn-economy are out of scope for now.
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
- UI construction: `MainWindow` → `CharacterSheet` (a `QWidget` that owns a
  `QScrollArea` → `BlockCanvas`) → nine blocks, each a section `QGroupBox` wrapped
  in a `BlockFrame`: `BaseInfoSection`, `SystemInfoSection`, `CharacterImageSection`,
  `AbilitiesSection`, `ResistancesSection`, `ConditionsSection`, `AdvantagesSection`,
  `SkillsSection`, `PowersSection`. The block set is **not** hardcoded in the sheet:
  it comes from the **block registry** (`ui/blocks/`) — one `BlockDescriptor` per
  block (key, dock title, widget factory, `BlockSize`, default row/col), held in an
  ordered `Registry` (`ui/blocks/registry.py`, reusing `core/registry.py`). The nine
  base descriptors register at import; `CharacterSheet` iterates `block_descriptors()`
  to build each section (exposing it as an attribute under its key so the name-based
  cross-block wiring still reaches it) and passes `default_rows()` to the canvas. A
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
  `StatsSection`; Abilities and Resistances share the grid helpers in
  `ui/sections/stat_grid.py`. The data-driven blocks take the `GameData` and build
  widgets by iterating over the data lists — no hardcoded ability/skill names.
- `SystemInfoSection` shows several **derived** readouts computed in `core.rules`, never
  in the widget: `speed_lines`/`speed_columns` (a base ground line plus one per active
  movement power — Flight, Speed, … — each rank expanded to walk/dash/run distances,
  with a ft-per-round ↔ km/h toggle), `initiative_modifier` (effective initiative
  ability + Improved Initiative's +4/rank; Alternate Initiative swaps the ability via a
  per-selection `AdvantageSelection.parameter`), and `effective_size` (the bought size
  shifted by an active Growth/Shrinking). It exposes `refresh_derived()` for the sheet to
  call when abilities/advantages/powers/conditions change. Movement constants live in
  `data/movement.json`; the km/h conversion reads `Measurements.distance_m`. Hero points
  render as five clickable circles.
- `ui/block_frame.py`: a `BlockFrame` wraps one section — a `TitleBar` (the drag
  handle, plus float `↗` and close `✕` buttons) above the section, no inner scroll
  area, sized to its content. A floated block moves into a `BlockWindow` (a
  top-level window owned by the sheet); its title bar reuses the same drag gesture,
  so you drag it back onto the page to re-dock.
- `ui/block_canvas.py`: the `BlockCanvas` is the single source of truth for the
  arrangement — `_rows` (an ordered list of rows, each an ordered list of block
  keys), `_windows` (floated blocks), and `_hidden` (closed blocks). It renders a
  `RowWidget` per row (fixed-width blocks keep their size, growable blocks stretch)
  and owns the drag controller: `title_bar_pressed/moved/released` run one manual
  gesture (float-out at drag start, `_hit_test` → a `DropIndicator`, dock-on-drop
  or leave-floating), plus edge auto-scroll. Structural ops `float_block`,
  `dock_block`, `show_block`/`hide_block`, `arrangement`, `apply_arrangement`,
  `default_arrangement` are the headless-testable seams (drag outcomes without
  synthetic mouse events). The default arrangement is supplied by the sheet from the
  block registry's `default_rows()` (grouping descriptors by their default row/col):
  the Name & Details block beside the Character Image, then the System / Power Level
  block full width, the Abilities | Resistances pair, then Conditions, Advantages,
  Skills, Powers.
- Layout persists globally as **JSON** (not Qt `saveState`): `MainWindow` saves its
  geometry and `CharacterSheet.save_layout()` (`json.dumps` of `arrangement()` —
  `{version, rows, floating, hidden}`) to the `layout` key in `settings.json` on
  close, and restores on open (`_restore_layout`). `restore_layout` validates
  (schema `SCHEMA_VERSION`; every block placed exactly once) and returns False to
  fall back to the default. A **View** menu has a checkable show/hide toggle per
  block (kept in sync via `BlockCanvas.block_visibility_changed`) and a **Reset
  Layout** action (`CharacterSheet.reset_layout()`). Cross-block wiring is
  object-to-object Qt signals, so it keeps working when a block is floated out.
- Each block's min/max size lives in `ui/block_sizes.json` (loaded by
  `ui/block_sizes.py::load_block_sizes`, keyed by block: `abilities`,
  `resistances`, …) and is applied to the `BlockFrame` in `block_frame.py`. A
  `max_width == min_width` pins a block's width so it can't stretch (Abilities and
  Resistances are compact grids); the content blocks (Advantages/Skills/Powers)
  grow to fill their row. Tweak the JSON to retune — no code change. This is UI
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
  `ui/power_constructor.py::PowerConstructorWindow` — a drag-and-drop
  brick-builder (a palette of Effect/Extra/Flaw bricks → an effect-card canvas,
  a `PowerModeBar` for the structure once ≥2 effects). It hands the finished
  `Power` back via `powerSaved`; the section appends it to the shared `Character`
  and renders a stat-block **card** (header with cost, ⚠ PL-breach marker, and an
  on/off switch for a gated power; description; per-effect extras/flaws; a roll
  line; the full game-term breakdown on hover). Cards carry edit (reopens the
  constructor on a deep copy, replaced in place on save) and remove buttons. The
  constructor always gets costs from `rules`, never inline.

## Shared UI utilities and view modes (matters when adding widgets)

The `ui/` package has a small support layer that section code is expected to go
through rather than reinvent. When building new sheet widgets, use it:

- `ui/widgets.py` — shared factories (`make_spin_box`, `readonly_item`,
  `hline_separator`) that keep construction consistent and wheel-guarded. Build
  spin boxes and read-only table cells through these, not by hand.
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
- `ui/flow_layout.py` — a reflowing layout for wrapping widget rows.

The Lock pattern is threaded top-down: `MainWindow` owns the checkable Lock menu
action, `CharacterSheet.set_locked(bool)` fans out to each section's
`set_locked`, and sections call `set_widget_locked` on their editable widgets.
The sheet **starts locked** (a read-only viewer, not an editor). Any new section
with editable widgets should expose `set_locked` and be wired into
`CharacterSheet.set_locked`.

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
  mechanisms, config-field types/widgets, sheet blocks — see the registry table in
  `docs/modding.md`).
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
- Two living examples ship under `docs/sample-mods/`: `campaign-notes` (data-only)
  and `flat-bonus-readouts` (data+Python), exercised end-to-end by
  `tests/test_mod_loading.py`.

## Licensing boundary (matters when adding game data)

- Source code is MIT. Game data under `src/mm_companion/data/` is Open Game
  Content under the OGL 1.0a (see `LICENSE-CONTENT.md`, `docs/open_game_license.md`).
- When adding data derived from the M&M SRD: ensure it is Open Game Content,
  record provenance for the OGL Section 15, and do **not** add Product Identity
  (product names, trade dress, logos).

## Conventions

- Branches off `main`: `feature/…`, `fix/…`, `docs/…`. Commit messages in
  imperative mood.
- **Do not open pull requests.** When starting work, automatically switch to an
  appropriate existing branch or create a new one (named per the convention
  above) rather than committing on `main`/`develop`. Integrate by merging
  locally, not through a PR.
- `.idea/` (PyCharm) is intentionally not committed. In PyCharm, mark `src/` as
  Sources Root so `import mm_companion` resolves.

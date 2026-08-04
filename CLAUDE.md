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
  open menu. Dice/recovery/turn-economy are out of scope for now.
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
  `QScrollArea` → `BlockCanvas`) → eleven blocks, each a section `QGroupBox` wrapped
  in a `BlockFrame`: `BaseInfoSection`, `SystemInfoSection`, `CharacterImageSection`,
  `AbilitiesSection`, `ResistancesSection`, `ConditionsSection`, `AdvantagesSection`,
  `ComplicationsSection`, `SkillsSection`, `PowersSection`, `DiceSection`. The block
  set is **not** hardcoded in the sheet:
  it comes from the **block registry** (`ui/blocks/`) — one `BlockDescriptor` per
  block (key, dock title, widget factory, `BlockSize`, default row/col, and
  `default_pinned` for a block that starts in the strip instead of a row), held in an
  ordered `Registry` (`ui/blocks/registry.py`, reusing `core/registry.py`). The eleven
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
  derived traits), which is also where the pieces they share with the Skills table
  live: `ROLL_ROLE`, `fit_table_height`, `tint_item`, and the two tint tokens. The
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
  mid-play action like a power's on/off switch. Its history keeps an inner scroll area,
  the same deliberate exception the GM window's `gm_rolls` block makes, with a
  `MIN_HISTORY_WIDTH` **and** `MIN_HISTORY_HEIGHT` floor (a scroll area asks for nothing
  on its own, so without them a history is squeezed to an unreadable sliver, or to the
  one card that fits in whatever height is left over). A session can be joined long
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
  a permanent gap above the strip for exactly this reason. So `_rebuild_quick_strip`
  emits `quickRollsChanged` and calls `updateGeometry` (what makes the `BlockFrame` ask
  again), and the view re-divides **twice** — now, and on the next turn, since the
  dropped chips are `deleteLater`'d and the size hint only tells the truth once they are
  gone. A split the user dragged is still left alone (`_user_sized`).
- A block's `min_width` in `ui/block_sizes.json` is what sets the **strip's** thickness
  for a pinned block (a page row is wide enough for anything), so `dice` is 360 — under
  that its column arrangement clips in a side strip.

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
- `ui/block_canvas.py`: the `BlockCanvas` is the single source of truth for the
  arrangement — `_rows` (an ordered list of rows, each an ordered list of block
  keys), `_windows` (floated blocks), `_hidden` (closed blocks), and `_pinned`
  (the strip's lines, with its `_pin_edge`/`_pin_align`/sizes). It renders a
  `RowWidget` per row (fixed-width blocks keep their size, growable blocks stretch)
  and owns the drag controller: `title_bar_pressed/moved/released` run one manual
  gesture (float-out at drag start, `_hit_test` → a `DropIndicator`, dock-on-drop,
  pin-on-drop over the strip, or leave-floating), plus edge auto-scroll. Structural
  ops `float_block`, `dock_block`, `show_block`/`hide_block`,
  `pin_block`/`unpin_block`/`set_pin_edge`/`set_pin_align`, `arrangement`,
  `apply_arrangement`, `default_arrangement` are the headless-testable seams (drag
  outcomes without synthetic mouse events). The default arrangement is supplied by
  the sheet from the block registry's `default_rows()` (grouping descriptors by
  their default row/col): the Name & Details block beside the Character Image, then
  the System / Power Level block full width, the Abilities | Resistances pair, then
  Conditions, Advantages, Complications, Skills, Powers — plus the registry's
  `default_pin_lines()`, which parks the **Dice Roller** block in the strip on the
  right. A block is in *either* the rows or the strip, never both: the arrangement
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
  being restated.
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
  `window.PAGES` — `ThemePage` and `GMPage`. Adding another area of settings is an
  entry in that tuple plus a `SettingsPage` subclass (`page.py`: `title`,
  `is_dirty`, `save`, `discard`, `needs_restart`). Which page it *opens on* is the
  caller's to say (`SettingsWindow(page=GMPage.title)`), which is how the GM window
  lands on its own rather than on the sheet's.
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
- `ui/flow_layout.py` — a reflowing layout for wrapping widget rows.
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

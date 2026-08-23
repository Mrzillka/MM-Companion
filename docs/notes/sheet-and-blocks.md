# The character sheet: blocks, canvas and layout

Matters when adding a block, or touching the page, the pinned strip or layout persistence.

Working notes for MM-Companion, split out of [CLAUDE.md](../../CLAUDE.md).

## The page, the canvas and the pinned strip

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

## How the sheet is built

- UI construction: `MainWindow` → `CharacterSheet` (a `QWidget` that owns a
  `QScrollArea` → `BlockCanvas`) → thirteen blocks, each a section `QGroupBox` wrapped
  in a `BlockFrame`: `BaseInfoSection`, `SystemInfoSection`, `CharacterImageSection`,
  `AbilitiesSection`, `ResistancesSection`, `ConditionsSection`, `AdvantagesSection`,
  `ComplicationsSection`, `SkillsSection`, `PowersSection`, `EquipmentSection`,
  `NotesSection`, `DiceSection` — and Notes is the one there can be more than one of
  (see "Blocks there can be two of" below). The block set is **not** hardcoded in the sheet:
  it comes from the **block registry** (`ui/blocks/`) — one `BlockDescriptor` per
  block (key, dock title, widget factory, `BlockSize`, default row/col, and
  `default_pinned` for a block that starts in the strip instead of a row), held in an
  ordered `Registry` (`ui/blocks/registry.py`, reusing `core/registry.py`). The thirteen
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
  characteristics — Power Level, the power-point pool, the homebrew-cost notice, the
  **Power Level limits**, size, reach, speed, movement modes, initiative, hero points
  and Extra Effort. Abilities/Resistances/Advantages were split out of the former
  `StatsSection`; Abilities and Resistances are `QTableWidget`s built through
  `ui/sections/stat_table.py` (Trait | ABL | Rank | Total, a spanned rule before the
  derived traits), which is also where the *stat-family* pieces they share with the
  Skills table live: `ROLL_ROLE`, `pin_menu_contributor`, `tint_item`, and the two
  tint tokens. Everything that is about a **table** rather than a stat is one layer
  down in `ui/sections/row_table.py` — see [The table blocks](table-blocks.md). The
  data-driven blocks take the `GameData` and build widgets by iterating over the
  data lists — no hardcoded ability/skill names.
- `SystemInfoSection` shows several **derived** readouts computed in `core.rules`, never
  in the widget: `condition_speed_lines`/`speed_columns` (one line **per movement mode** — see
  [Size and movement](size-and-movement.md) — each rank expanded to walk/dash/run distances (walk/dash
  off the ground), with a
  ft-per-round ↔ km/h toggle; a **label per row**, since a line is a mode and what
  granted it goes on the hover), `initiative_modifier` (effective initiative
  ability + Improved Initiative's +4/rank; Alternate Initiative swaps the ability via a
  per-selection `AdvantageSelection.parameter`), `movement_mode_lines` (the specialised
  speeds an active power grants), `character_reach`/`reach_is_altered` (see
  [Size and movement](size-and-movement.md)), and `effective_size` (the bought size
  shifted by an active Growth/Shrinking, which drives real trait modifiers now and not
  just a label). It exposes `refresh_derived()` for the sheet to
  call when abilities/advantages/powers/conditions change. Movement constants live in
  `data/movement.json`; the km/h conversion reads `Measurements.distance_m`. Hero points
  render as five pips — a lit medallion for a held point, a grey one for a spent one —
  and each is **its own switch**: light the fourth alone if you like. The character
  carries a *count*, so which pips are lit is cosmetic and `HeroPointsWidget.set_value`
  (a load, a GM's command) **reconciles** to the number rather than redrawing from it.
  Every change — a click or a GM's command — funnels through
  `SystemInfoSection._on_hero_points_changed`, which is why the `note-requested` topic
  is raised there and a point can never move silently (see [The dice roller](dice-and-rolling.md)).
  Five pips is the row's **resting** count, not a cap: `characteristics.json` allows 99
  and the rules put none on a GM handing out a sixth, so the row grows a pip rather than
  clamping — it used to clamp in `set_value` and then write the clamped number back
  through `_on_hero_points_changed`, which *destroyed* the point rather than hiding it.
  `HeroPointsWidget` is shared with GM Mode's `PlayerCard`, so its pip size
  (`column.hero-point`) has to suit both.
- **The block owns Power Level, so it owns what Power Level does.** A `Limits` row
  (`PowerLevelCapsWidget`) states how close the character is to each character-wide cap —
  `Dodge + Toughness 18/20`, `Fortitude + Will 20/20`, `Skills (Stealth) 25/20` — from
  `power_level_cap_summary`, which reduces the per-row skill cap to the row standing
  closest to it. The arithmetic is `power_level_caps`, and `power_level_violations` is
  now derived from the same list, so the readout and the warning cannot be two different
  answers. That function was fully implemented and tested before this and had **no UI
  surface at all**: its only caller was a minion's build, so a character over Power Level
  on their own defences got no mark anywhere while a single power got a ⚠. A line tints
  `tint.warning` only when the build is genuinely past the cap — sitting exactly on one
  is where you are meant to sit — and the spent half of the point pool tints the same way
  when the build has outrun its budget (`_restate_pool_balance`).
- **Four of the block's rows are not always there** — the cost notice, Reach, Movement,
  and (for an NPC) Power Level, Hero Points and the limits. They all go through
  `_set_row_visible`, which calls `QFormLayout.setRowVisible` rather than hiding the field
  and its caption by hand: hiding a widget takes it off the screen and leaves its **row**
  in the layout, spacing and all, so every absent row used to leave a blank band behind
  it. An NPC's Power Level is *estimated* from its traits (`estimated_power_level`), which
  is why the limits go with it — measuring those traits back against a number derived
  from them is a tautology, not a limit.

## Block frames, the canvas API and layout persistence

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
  now (see `AutoHeightTable` in [The table blocks](table-blocks.md)). Tweak the JSON to retune — no code change. This is UI
  config, **not** game content, so it lives under `ui/` (bundled via the
  `ui/*.json` `package-data` entry), not the OGL `data/` dir.

## The model and cross-section signals

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
  `ResistancesSection.follow_ability_change` (restates the readouts of the
  resistances derived from that ability — never their ranks, which are the
  player's). Each block also emits a generic `changed` signal; `CharacterSheet`
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

## Blocks there can be two of (matters when touching the canvas or the View menu)

- A `BlockDescriptor` carrying an **`instance_factory`** is a *template*: further
  instances take a `"notes#2"`-style key, are built by calling it with that key, and are
  held by the **sheet** rather than the registry (which, being keyed by block key, can
  hold one of anything). One field rather than a `multi` flag beside a lookup table, so a
  mod ships a multi-instance block with nothing to register but its descriptor.
  `blocks.base.instance_template` is the one rule for reading a template key back out,
  and every lookup keyed by *kind* of block — `block_sizes.json`, a preset's `blocks`
  overrides, the merge test — goes through it, so a per-instance key never has to appear
  in a config file.
- `BlockCanvas` gained `add_block`/`remove_block` (its `_frames` was write-once in the
  constructor) plus `block_added`/`block_removed`, which both View menus follow —
  `MainWindow._block_actions` is mutable now and inserts above a retained separator so
  "Reset Layout" stays last.
- **Placement is global, contents are per character.** A Notes block lives in the shared
  `layout` settings key like every other block, so where it sits is remembered once; what
  it has open is on the `Character`. The consequence is deliberate: a second Notes block
  exists for *every* character, showing its "No notes open" state where one has nothing
  in it.
- The arrangement model gained an **`"instances"`** section, and `apply_arrangement`
  reconciles `_frames` against it **before `_validate` runs** — that check is a strict
  multiset equality against `set(self._frames)`, so an instance a saved layout names has
  to exist first or the whole layout is rejected and the user loses their page. The
  reconciler is narrow on purpose: only a key whose `instance_template` differs from
  itself is touched, so an edited settings file can never conjure up or sweep away a base
  block, and a host with no `instance_factory` (the GM window) reconciles nothing.
  `SCHEMA_VERSION` is 7 for it.
- **Merge is a drop onto a block, not between two.** `DropSlot` gained `onto`; `_hit_test`
  checks a central band (`_MERGE_INSET`, the outer bands staying an ordinary insert so a
  block can always be placed *beside* another) and asks the target *section* through a
  duck-typed `accepts_merge(other_key)` that defaults absent. That default is why no
  existing block's drag behaves any differently. The mark is a `DropFeedback` wash over
  the whole target frame rather than an insert line — a line says "the block lands here",
  a wash says "the block goes *in* here" — and **`border=False`, `wash=MERGE_WASH`**: a
  stylesheet border would change the frame's box and relayout the page *while the block
  is being dragged over it*, so the target would shift out from under the cursor the
  instant it lit up, and without an outline the fill has to be heavier to read. The
  canvas only emits `merge_requested`; what merging *means* is the sheet's, since the
  sections are.
- **`title_bar_released` hit-tests before `_end_drag`, and takes `onto` from
  `_merge_hint`** — what the drag last *showed* — rather than asking again. Both halves
  are one bug: `_end_drag` clears `_drag_key`, which `_merge_target` needs to know whose
  drop it is judging, so re-deriving the merge afterwards made every drop an ordinary
  dock and the merge never fired at all. The drop now does what the highlight promised.
  Note also that a block scrolled off the page cannot be dropped on — `_hit_test` bounds
  the gesture to the viewport — which is honest, and is why a test has to scroll first.
- **Split is the same drag, adopted.** A tab dragged clear of its bar makes
  `NotesSection` emit `splitRequested`; the sheet builds a new instance holding that one
  note and calls `BlockCanvas.adopt_drag`, and because the **tab bar still holds the mouse
  grab** it goes on forwarding moves and the release as `splitMoved`/`splitReleased`.
  From there the gesture is indistinguishable from one begun on a title bar — dock, pin,
  merge, or stay floating. Those three signals are connected in `_wire_section`, which
  both the block built at startup and the copies made later go through; connecting only
  the copies is exactly the bug where the first block's tabs could not be dragged out.
- The template's own key (`notes`) is never removed — it is the block every sheet has and
  every saved layout names, and closing it is what the View menu's checkbox is for. Only
  a copy the user made is destroyed, by `✕` or by being merged away.
- Screenshot it with `driver.py notes-demo` / `notes-split`.

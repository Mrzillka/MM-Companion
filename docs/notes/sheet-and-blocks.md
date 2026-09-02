# The character sheet: blocks, canvas and layout

Matters when adding a block, or touching the page, the pinned strip or layout persistence.

Working notes for MM-Companion, split out of [CLAUDE.md](../../CLAUDE.md).

## The page is a tree, and the user owns it

- The arrangement is a **tree of splits and leaves** (`ui/layout_tree.py`), not a
  list of rows. A `Leaf` names one or more block keys — **one key is a plain
  block, two or more is a tab group**, which is the whole of the merge feature in
  the model and the reason there is no third node kind for it. A `Split` divides
  its space along one axis between its children, carrying the pixel `sizes` the
  user dragged. **The page itself is a vertical `Split`** whose children are the
  rows: that is not a special case bolted on top, because a vertical split
  directly inside a vertical split *is* just more rows, which is exactly what
  `normalize` collapses. Every structural operation — `insert_beside`, `remove`,
  `merge_into`, `split_out`, `move`, `set_sizes` — is a pure function over frozen
  dataclasses, so the structural half of a drag is tested with no widget, window
  or display server (`tests/test_layout_tree.py`).
- **Width tiles, height scrolls**, and the two containers in `ui/grid_view.py` are
  the difference. A `GridSplitter` renders a split *inside* a row: its children
  share a fixed extent, so a divider drag there is zero-sum — give one block width
  and its neighbour loses exactly that much. A `RowStack` renders the page: its
  sizes are *absolute*, the rows may total more than the viewport, and the page
  scrolls. A drag there therefore **cannot** be zero-sum — pulling the divider
  under row 2 down makes row 2 taller and pushes everything below it down. That is
  the one behaviour a `QSplitter` cannot give (a splitter divides a fixed total,
  and the page has no fixed total to divide), and the only reason the page is not
  simply another splitter.
- **A row nobody has dragged states a height of zero**, which means "be as tall as
  your content". So the sheet behaves exactly as it always did until somebody
  actually resizes something, and adding a skill still makes the Skills block
  taller rather than making it scroll inside a height nobody chose. A zero in a
  split's `sizes` is a real value and not a gap; a run of nothing but zeros is the
  same as no sizes at all and is dropped.
- **Every row is wrapped in a `_RowHolder` the stack owns**, and that is a bug fix
  rather than tidiness. A row holding a single block *is* that block's frame, with
  no container of its own — so anything the stack set on it (a fixed height, a
  size policy) was set on the *block*, and travelled with the block when it was
  later dragged into the pinned strip, where it then refused to be squashed. The
  same special case had already destroyed a live block once, when `_relayout` shed
  an "old row" that was really a frame; `_relayout` still guards that explicitly.
- `ui/grid_handle.py` holds the divider. It is `grid.handle` px wide, painted as
  **nothing at rest** and a soft accent under the pointer — deliberately not Qt's
  own handle furniture, which draws a raised panel with dots and would make a page
  of a dozen blocks a dozen visible gutters. One `paint_divider` serves both
  divider kinds (the splitter handles and the row grips), because they are the same
  affordance and would read as two if drawn twice.
- **The detent** is what makes a recommended size feel like advice rather than a
  wall. `snap_to_detent(position, targets, strength)` pulls a handle onto the
  nearest recommended size within `grid.detent` px; dragging further than the band
  goes straight past, which is the "deliberate extra pull" without a mode or a
  modifier key. A `GridSplitter` offers **two** targets per handle — the block
  before it at its recommended size and the block after it at that block's — so a
  divider between two blocks can settle either without going round the other side.
  A `_DetentMark` overlay shows where they are during the drag. It is built against
  the nearest ancestor that is **not** a splitter: a `QSplitter` adopts every child
  widget into its own list of panes, so an overlay parented to one becomes a pane
  of it, which put an invisible strip of nothing at the left of every row.
- Beside the page is still the **pinned strip** (`ui/pinned_panel.py`), the one
  place that does *not* scroll. `PinnedBoard` is what the host puts in its layout —
  a splitter holding the page and a `PinnedPanel`, whose orientation and child
  order *are* the strip's edge and whose handle sets its thickness. The **`PinnedHandle`
  (📌) is always visible**: the empty strip's whole content, the drop target that
  gets the first block in, the grip the strip is dragged to another edge by
  (lighting four `EdgeZoneOverlay` bands), and the button that opens its menu.
  The canvas still owns the model — the panel is a view and holds no arrangement
  state; `_relayout` renders the strip **first** so the rows are the last to claim a
  frame, and the rescue pass that follows only touches frames still inside the
  page's own stack (rescuing one the strip had just taken would undo the render
  order from the other direction). The strip's model is a region *tree* like the
  page's; `layout_tree.region_lines` is the bridge that hands the strip's own
  widgets the lines they still speak in.
- **Nothing holds the window open any more.** `PinnedPanel.minimumSizeHint` used to
  report the strip's whole content, capped at the usable screen, precisely so the
  *window* would be held open rather than the strip clipping. That was right while
  a squashed block was a clipped one. It reports its handle and no more now, and
  `_usable_screen` and `_scrollbar_allowance` went with it — they existed only to
  serve it. A strip too short for its blocks squashes them, and they scroll inside
  themselves.

## How the sheet is built

- UI construction: `MainWindow` → `CharacterSheet` (a `QWidget` that owns a
  `QScrollArea` → `BlockCanvas`) → thirteen blocks, each a section `QGroupBox` wrapped
  in a `BlockFrame`: `BaseInfoSection`, `SystemInfoSection`, `CharacterImageSection`,
  `AbilitiesSection`, `ResistancesSection`, `ConditionsSection`, `AdvantagesSection`,
  `ComplicationsSection`, `SkillsSection`, `PowersSection`, `EquipmentSection`,
  `NotesSection`, `SceneSection`, `DiceSection` — and Notes is the one there can be more than one of
  (see "Blocks there can be two of" below). The block set is **not** hardcoded in the sheet:
  it comes from the **block registry** (`ui/blocks/`) — one `BlockDescriptor` per
  block (key, dock title, widget factory, `BlockSize`, default row/col, and
  `default_pinned` for a block that starts in the strip instead of a row, and
  `npc_default` for whether it is *open* on a GM's NPC sheet), held in an
  ordered `Registry` (`ui/blocks/registry.py`, reusing `core/registry.py`). The fourteen
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
  (`PowerLevelCapsWidget`) names each character-wide cap the build is **past** —
  `Dodge + Toughness 22/20`, `Skills (Stealth) 25/20` — from `power_level_cap_summary`,
  which reduces the per-row skill cap to the row standing closest to it. The arithmetic
  is `power_level_caps`, and `power_level_violations` is now derived from the same list,
  so the readout and the warning cannot be two different answers. That function was fully
  implemented and tested before this and had **no UI surface at all**: its only caller was
  a minion's build, so a character over Power Level on their own defences got no mark
  anywhere while a single power got a ⚠. Breaches only, and no row for a legal build —
  the same bargain Reach and Movement strike, and for the same reason: a legal build is
  the ordinary case, and three lines of reassurance on every sheet is noise standing
  where a warning has to be noticed. The spent half of the point pool tints the same way
  when the build has outrun its budget (`_restate_pool_balance`).
- **`refresh_limits` is subscribed to three topics, not one.** Its inputs are scattered:
  the level (this block's own spin box, `caps-changed`), every trait any other block edits
  (`facts-changed`), and the powers and conditions (`derived-changed`, which is all it
  had). Hanging it off `refresh_derived` alone left it stale for the two edits that move
  it most directly — typing a Power Level, and typing a Dodge rank.
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
  handle, plus pin `🖈`, float `↗` and close `✕` buttons) above the section **in a
  scroll area of its own** (`_InnerScroll`). That scroll area is the whole reason a
  block can be dragged to any size: a `QScrollArea` does not pass its child's
  minimum on, so the frame is free to report a minimum of almost nothing and let
  the section reflow — and, past what reflow can save, scroll. It **declines a
  wheel it cannot use**, passing the event up when it has no scrollbar on that axis
  or is already at the end of one; otherwise a block a pixel too short would
  swallow the gesture and the page under it would stop scrolling.
- **`minimumSizeHint` is a title bar and `block.min-extent`, and says nothing about
  the content.** It used to be `max(content, the JSON floor)` in both dimensions,
  and that climbed out through the row, the page, the pinned strip and the window
  to hold the whole application open at the sum of every block's content. On a page
  the user drags, a minimum is a refusal: whether a block is too small to read has
  to be the user's call. The floor that is left is about being able to *find* a
  block you squashed — a title bar you can still grab and drag back open.
- **`sizeHint` differs by axis, for a reason rather than an oversight.** A block's
  **width** is *shared* — it and its neighbours divide one row — and what makes that
  division good is a stable declared preference rather than whatever is typed into
  the block today, or a Powers block with nine powers would take the row. A block's
  **height** is *taken*: an undragged row is exactly as tall as what is in it and
  the page scrolls, which is what the sheet has always done.
- Each block's **recommended** size lives in `ui/block_sizes.json` (loaded by
  `ui/block_sizes.py::load_block_sizes` as a `RecommendedSize`). These are no longer
  constraints. A number here is used for exactly three things — the size a block
  opens at, the soft detent a divider sticks at, and the mark shown during a drag —
  and for **nothing** in any layout minimum. There are no maxima: pinning a width
  was how the old page stopped Abilities being stretched, and a page whose columns
  the user drags has no use for it. The old `min_*`/`max_*` key names are still read
  (`min_*` as the recommendation, `max_*` ignored) for the mods in the sibling
  repository, which pin an engine version and ship `blocks.json` files written
  against them. Abilities and Resistances state nothing in either dimension, because
  their tables report their real columns and rows and that beats a number a denser
  preset would make wrong. This is UI config, **not** game content, so it lives
  under `ui/` and not the OGL `data/` dir; the active theme's `blocks` map overrides
  any of it, since how much room a block needs depends on the look's density.
- **The numbers were measured, not inherited.** They began life as *floors*, and a
  number that stopped a block clipping is not the same as the width it reads well
  at. `tests/test_recommended_sizes.py` floats each block out, binary-searches the
  narrowest width at which its content still fits after everything it knows how to
  reflow, and fails if a recommendation sits under that — which is how Advantages
  turned out to be recommending 300 for a block that needs 316. Measured on
  Classic at 100% scale:

  | Block | Recommends | Needs |
  | --- | --- | --- |
  | `advantages` | 330 | 316 |
  | `skills` | 300 | 296 |
  | `system_info` | 340 | 206 |
  | `base_info`, `character_image`, `complications`, `conditions`, `equipment`, `powers` | as shipped | reflow the whole way down |

  The six in the last row have **no measurable comfort width**: their content
  wraps or flows, so they never grow a horizontal scrollbar however narrow they
  get, and what they recommend is a judgement about how their chips and cards read
  rather than a number a scrollbar can answer. The test only checks the "not
  narrower than this" half for them. `dice`, `scene` and `notes` are exempt
  outright — the roller reflows all the way down too, but a die and a history in
  100px is not a roller anybody wants, and a note is as wide as you make it.
- A floated block moves into a `BlockWindow`, **frameless and pinnable above other
  applications** (`ui/frameless.py`), which hosts the frame **directly**. It used to
  wrap it in a scroll area of its own, because that was the only way a floated block
  could go smaller than its content; the frame carries one now and every docked
  block makes the same bargain, so a second would only mean two sets of scrollbars.
  What is left there is the floor — `float.min-width`/`float.min-height` — because a
  window shoved into a corner still has to be findable.
- `ui/block_canvas.py`: the `BlockCanvas` is the single source of truth for the
  arrangement — `_page` (the tree), `_windows` (floated blocks), `_hidden` (closed
  blocks), `_groups` (live tab groups) and the strip's region. `_rows` survives as a
  read-only **view** over the tree, since most of the class only needs to know which
  blocks share a row. Structural ops `drop_block`, `merge_blocks`, `dock_block`,
  `float_block`, `show_block`/`hide_block`, `pin_block`/`unpin_block`,
  `set_block_on_top`, `arrangement`, `apply_arrangement`, `default_arrangement` are
  the headless-testable seams.
- **`minimumSizeHint` on the canvas is the page's shape rule, and it is asymmetric.**
  As narrow as you like (so every row can be dragged in and its blocks reflow) and as
  tall as its rows (so the page overflows the viewport and *scrolls* rather than
  squashing every row into a window nobody sized for them). It is the exact inverse
  of what it used to say.
- **The drag says four things now, where it used to say two.** `_hit_test` reads,
  from the middle of a block outwards: merge into it (`_MERGE_INSET`), stack above
  or below it (`_STACK_BAND`), sit beside it, or — past the row entirely (`_GAP`) —
  start a new row. The stack bands sit *inside* the row's core, which is already
  inset by `_GAP`; measuring them from the frame's own edge would put them under the
  band that means "a new row", where the pointer can never be, and a block could only
  ever be stacked by accident. A `DropSlot` therefore carries a `target` block and a
  `side` as well as the old row-and-index, which is all the older callers (an anchor
  resolving, a block being reopened or unpinned) have ever known how to say.
- Layout persists globally as **JSON** in `settings.json`, under the key named by
  `MainWindow.LAYOUT_KEY` — a class attribute, not a constant, because an `NPCWindow`
  writes `npc_layout` and the GM window `gm_layout`, and an NPC's arrangement written
  under `layout` would close the roller on every hero. All three go through
  `storage.sheet_layout()` / `set_sheet_layout()` now: they each read their key
  straight off `load_settings()` before, which worked only because they all
  remembered to write the fallback the standing rule warns about.
- `SCHEMA_VERSION` is **8**, and the shape is
  `{version, instances, page, region{edge, align, extent, root}, floating, hidden,
  hidden_anchors}`. Both older design rules hold exactly: every known block appears
  **exactly once** across page/region/floating/hidden, and validation is **strict
  about where a block lives** (an unknown key or edge rejects the whole layout,
  because guessing would silently move somebody's block) and **lenient about the
  cosmetic numbers** (sizes, the strip's thickness, a hidden block's anchor all
  degrade to defaults). `_reconcile_instances` still runs *before* validation, or an
  instance a saved layout names would not exist yet and the user would lose their
  page.
- **A version-7 layout is migrated, not rejected** (`layout_tree.migrate_v7`). Every
  row and every pinned line of one has an exact reading as a tree, so there was no
  reason to throw away a page somebody had arranged; the precedent for resetting was
  set by *adding a block*, which is a much smaller disruption than this. What does
  not carry over is the strip's per-line pixel sizes — they described a layout engine
  that no longer exists, and a wrong remembered size is worse than none.
- A **View** menu has a checkable show/hide toggle per block (kept in sync via
  `BlockCanvas.block_visibility_changed`) and a **Reset Layout** action.
- **A GM's NPC opens with the blocks that hold no trait closed** — the roller, the
  Scene, Notes, Complications (`npc_hidden_keys()`, read off
  `BlockDescriptor.npc_default` so a mod block declares its own answer). It is a
  **default, not a mode**: `NPCWindow._restore_layout` seeds it only when nothing was
  remembered, which is the whole reason the NPC layout needed a key of its own.

## Tab groups: several blocks in one cell

- A `Leaf` holding more than one key renders as a `TabGroupFrame`
  (`ui/tab_group.py`): a tab bar with the active block's pin/float/close buttons at
  the right of it, over the active block's frame. **Merging is uniform** — any block
  dropped into the middle of any other makes a group of the two. There is no
  `accepts_merge` any more; a block used to have to opt in, which is why only Notes
  ever merged, and there is nothing left to opt into.
- It is a **reuse of the frames**, not a second kind of block. Each member keeps its
  own `BlockFrame` — its section, its size, its lock state, its live caption — and
  only lends its title bar to the group (`BlockFrame.set_tabbed`) while it is in one,
  because two rows of chrome for one cell is one too many and the group's buttons act
  on whichever block is showing anyway. That is why a block behaves identically inside
  a group and out of it: it *is* the same widget, with one row hidden.
- **A group owns its members' frames**, so one going away has to hand them back
  before it is deleted or it takes live blocks with it (`_release_group`). Groups are
  kept across a rebuild, keyed by exactly the blocks in them; rebuilding one every
  relayout would reparent every member twice a drag and throw away which tab was
  showing.
- Dragging a tab clear of the bar takes that block back out, through the shared
  `ui/tab_drag.py::TabSplitGesture` — the same gesture the Notes block uses to drag a
  *note* out into a new block. The bar keeps the mouse grab through all of it, so
  once the split is requested the moves and the release are forwarded into the
  canvas's ordinary drag controller and the block can dock, stack, pin, merge or stay
  floating exactly as one dragged by its title bar would.
- **The trade, stated plainly.** Two Notes blocks used to merge their *notes* into
  one tab bar; they now stay two blocks sharing a cell, so you get a tab bar of blocks
  each with its own tab bar of notes. That is more chrome than the old answer and it
  is the price of one merge rule instead of a per-block opt-in. The Notes block's own
  `adopt`/`release`/`open_refs`/`accepts_merge` are gone with it; splitting a *note*
  out into a new block is a different feature and stayed.
- **One honest limitation:** there is no gesture for moving a whole group at once —
  each tab is dragged out on its own. A group of one collapses back into a plain
  block, so nothing gets stuck; it is simply more clicks than it might be.

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
- **Every subscriber runs inside a `core.rules.stable_build()` scope**, opened by
  `SignalBus.publish`/`publish_all` (`ui/blocks/bus.py::_refresh`). A refresh is a
  *read* — the block redraws from a model nobody is touching — so within one the
  derived answers cannot change, and the rules layer is allowed to work the build
  out once instead of once per number printed. It mattered a great deal:
  `rules.trait_contributions` gathers the whole character (size, every power,
  advantages, gear, Extra Effort) and sits under every readout, so `skill_total` was
  two full gathers, `skill_modifiers` a third, `resistance_total` two (four for
  Dodge, which derives from Defence) — and the Skills block asks three questions per
  row. One step of an ability spin box cost well over a hundred gathers of the whole
  build. See `core/rules/build_cache.py` for the contract; the short version is
  **do not write the model inside a scope**.
  The scope is per *handler*, not per fan-out, precisely because one handler does
  write: `PowersSection._rebuild_list` normalizes its arrays before drawing, and
  calls `invalidate_build_cache()` the moment it has.
- **The two card trees redraw once a turn, not once a publish.** `PowersSection.refresh`
  and `EquipmentSection.refresh` rebuild their whole card tree — every card destroyed
  and made again — and both answer to `facts-changed`, which every spin box on the
  sheet raises on every step. A rank dragged from 0 to 10 rebuilt them ten times and
  showed the tenth; a drag of twenty-five steps took the best part of a second.
  `BlockDescriptor.coalesces` names them (`registry.py::_COALESCED`), and
  `SignalBus.subscribe(..., coalesce=True)` arms such a handler instead of running it,
  flushing when the turn settles. It is legal only because every subscriber is already
  an idempotent redraw from the model. The price: **anything reading a coalescing
  block's widgets in the same breath as the edit must `sheet.bus.flush()` first** —
  which is only ever a test, since the app's own event loop does it a moment later.
  The timer is parented to the sheet, so a sheet closed with a redraw pending cannot
  fire it into its own torn-down sections; a bus built with no owner (the headless bus
  tests) never coalesces at all.
- **A block signal that publishes a topic another of its signals already publishes
  is a doubled refresh.** `AbilitiesSection` emits `abilityChanged` *and* `changed`
  for one edit; both named `derived-changed` in the descriptor, so the System block
  re-derived twice per spin step — and since `refresh_derived` calls `refresh_limits`,
  which `facts-changed` calls too, the Power Level caps were computed three times for
  one tick of an arrow key. `publish` does not dedup (only `publish_all` does), so
  the descriptor tables are where this has to be got right.

- **A destroyed block comes off the bus.** `remove_block_instance` calls
  `SignalBus.forget(section)`, which drops every subscription, server and armed redraw
  whose handler is a bound method of that section. Without it the handlers stayed on
  the bus holding a section whose C++ half had gone, and the next publish raised from
  inside whoever happened to trigger it — a shape coalescing made worse, since the
  call could then land a turn later with nothing on the stack to say who armed it.
  Notes is the only base block there can be two of and it subscribes nothing, so this
  is for the mod blocks that will.

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
  The `instances` section is why `SCHEMA_VERSION` moved to 7; it is 8 now, for the tree.
- **Merge is a drop into the middle of a block, not between two.** `DropSlot` carries
  `onto`; `_hit_test` checks a central band (`_MERGE_INSET`), the bands around it
  meaning "stack above/below" and "sit beside", so a block can always be placed next
  to another. Every block accepts every merge — see [Tab groups](#tab-groups-several-blocks-in-one-cell)
  for what the two of them become. The mark is a `DropFeedback` wash over the whole
  target frame rather than an insert line — a line says "the block lands here", a wash
  says "the block goes *in* here" — and **`border=False`, `wash=MERGE_WASH`**: a
  stylesheet border would change the frame's box and relayout the page *while the block
  is being dragged over it*, so the target would shift out from under the cursor the
  instant it lit up, and without an outline the fill has to be heavier to read.
- **`title_bar_released` hit-tests before `_end_drag`, and takes `onto` from
  `_merge_hint`** — what the drag last *showed* — rather than asking again. Both halves
  are one bug: `_end_drag` clears `_drag_key`, which `_merge_target` needs to know whose
  drop it is judging, so re-deriving the merge afterwards made every drop an ordinary
  dock and the merge never fired at all. The drop now does what the highlight promised.
  A merge does **not** dock first: the block goes *into* a cell rather than beside one,
  and doing both would place it and then immediately move it somewhere else — visibly,
  and (when the dock collapsed the target's row) to the wrong somewhere else. Note also
  that a block scrolled off the page cannot be dropped on — `_hit_test` bounds the
  gesture to the viewport — which is honest, and is why a test has to scroll first.
- **A row that holds one block *is* that block's frame.** `build_node` returns the frame
  itself for a lone leaf rather than wrapping it, so anything that treats a row as a
  container it owns is wrong twice over: shedding an old row destroyed a live block until
  `_relayout` learned to skip frames, and the row stack setting a height on one changed
  the *block* permanently — which followed it into the pinned strip, where it then
  refused to be squashed. `_RowHolder` exists so the stack never touches a row's own
  widget, and `_frame_under` tests "at or under" rather than "under" so a lone-block row
  can be dropped onto at all.
- **Split is the same drag, adopted.** A tab dragged clear of its bar — a note in the
  Notes block, or a whole block in a tab group — goes through
  `ui/tab_drag.py::TabSplitGesture`, and because the **tab bar still holds the mouse
  grab** it goes on forwarding moves and the release. From there the gesture is
  indistinguishable from one begun on a title bar — dock, stack, pin, merge, or stay
  floating. The Notes half is connected in `_wire_section`, which both the block built at
  startup and the copies made later go through; connecting only the copies is exactly the
  bug where the first block's tabs could not be dragged out.
- The template's own key (`notes`) is never removed — it is the block every sheet has and
  every saved layout names, and closing it is what the View menu's checkbox is for. Only
  a copy the user made is destroyed, by `✕` or by being merged away.
- Screenshot it with `driver.py notes-demo` / `notes-split`.

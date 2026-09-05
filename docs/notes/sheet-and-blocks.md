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
- **What happens to a row somebody else's gesture did not touch: nothing.** Adding a
  row (`append_node_row`) and taking one away (`_remove`) both used to clear the
  whole run's sizes, on the reasoning that numbers describing a run of three say
  nothing about a run of two. That is true of a **row**, whose children share a fixed
  extent — and a splitter renormalises the remembered numbers to its real width, so
  keeping them keeps the proportions the user dragged rather than falling back to the
  blocks' own hints. It is flatly wrong of the **page**, whose sizes are absolute
  heights that owe nothing to each other: closing one block forgot the height of every
  other row on the sheet, and so did dragging a block into a row of its own. Both keep
  what the survivors had now; a newly added row states zero and tracks its content.
- **Every row is wrapped in a `_RowHolder` the stack owns**, and that is a bug fix
  rather than tidiness. A row holding a single block *is* that block's frame, with
  no container of its own — so anything the stack set on it (a fixed height, a
  size policy) was set on the *block*, and travelled with the block when it was
  later dragged into the pinned strip, where it then refused to be squashed. The
  same special case had already destroyed a live block once, when `_relayout` shed
  an "old row" that was really a frame; `_relayout` still guards that explicitly.
- `ui/grid_handle.py` holds the divider. **Two numbers, not one**: `grid.grab` is
  how wide it is *to the mouse* (and therefore the gutter between two blocks), and
  `grid.handle` is the accent painted down the middle of that under the pointer.
  They were one token, which meant the grab target could not be made catchable
  without the gap between blocks growing to match — a hairline is not a grab
  target and a page of visible gutters is not a character sheet, so the two were
  always going to want to differ. At rest a divider is painted as **nothing** —
  deliberately not Qt's own handle furniture, which draws a raised panel with dots
  and would make a page of a dozen blocks a dozen visible gutters. One
  `paint_divider` serves both divider kinds (the splitter handles and the row
  grips), because they are the same affordance and would read as two if drawn
  twice.
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
- **Double-clicking a divider fits.** The detent marks where a block's recommended
  size is and a drag can settle on it, but neither is a way to *ask* for it — the
  one thing the recommendations had no direct route to, and the gesture a splitter
  has answered with a double-click for as long as there have been splitters.
  `GridSplitter.fit_pane` gives the pane its recommendation out of its neighbour
  (zero-sum, like every resize in a row); `RowStack.fit_row` puts a row back to
  its content height, spelled as *forgetting* the height rather than measuring
  one, so the row goes on tracking its content afterwards exactly as an undragged
  row does. A handle fits the pane **before** it — a rule you can predict beats
  one that guesses which side you meant, and every pane has a divider on both
  sides of it.
- **A grip drag freezes the page's height, and this is not the minimum the shape
  rule forbids.** Every mouse-move pins the dragged row's holder to a new height,
  so the stack re-sums and the page gets shorter the instant the row does. Behind
  a `QScrollArea` that is not cosmetic: the scroll value is clamped to the smaller
  maximum, the content slides down inside the viewport, and the divider stands
  still on screen while the hand dragging it walks away — dragging a *bottom* row
  shorter was close to unusable. So `RowStack.arm_grip` records its own height and
  refuses to go under it until the release, and the slack goes into the trailing
  stretch it already had. That number is read off the widget at the instant of a
  press, is not a function of the width, is recomputed by nothing while it stands,
  and is gone on release; a rebuild *under* a live drag cancels the gesture rather
  than committing it. Growing is untouched — and getting *that* right is the whole
  reason the freeze is a floor under `RowStack.minimumSizeHint` rather than a
  `setMinimumHeight`. An explicit minimum does not sit under Qt's answer, it
  replaces it (the same `qSmartMinSize` rule `_pin_content_height` relies on
  further down), and Qt's answer is the rows added up, which inside a `QScrollArea`
  is the *only* way a row dragged taller reaches the page. Spelled as an explicit
  minimum the freeze held the page in both directions: past the point where the
  page filled its viewport the grip stood still under a hand that kept moving, the
  row below was crushed to find the height the dragged one was taking, and both
  came right on release — which is why the fault read as "the divider stops moving
  and the block under it clips, but the result is correct".
- **And a grip dragged into the edge of the window auto-scrolls**, reusing the
  block drag's `edge_velocity` curve. It extends the drag *before* it scrolls: a
  row dragged past the bottom is usually already at the end of the scroll range,
  so scrolling first would move nothing, and growing the row is what gives the
  bar somewhere to go.
- **A block dragged too small to find closes itself**, past `grid.close-extent`
  (48, against `block.min-extent`'s 24, which is the frame's own floor). The frame
  washes in the reject colour, the cursor carries a "Release to close" naming the
  blocks, and letting go closes them — the View menu entry clears and Ctrl+Z puts
  them back where they were. A row squashed to nothing takes every block in it and
  counts as **one** gesture, which is what `BlockCanvas.close_blocks` exists for;
  `hide_block` is now one call into it. **Only a drag may do this**: a restored
  arrangement, a strip mid-rebuild or a window resize can all hand a pane a tiny
  size and none of them is anybody asking for a block to go away, so the check
  hangs off `GridHandle`'s release and the grip's, and off nothing that merely
  notices a size. The strip gets the rule for free — it renders with the page's own
  splitters — once the canvas follows its dividers, which until then it did not.
- **Right-clicking a title bar opens the block's menu** (`BlockCanvas.block_menu`,
  built rather than shown so it can be asked what it offers without an event
  loop): Fit to content, the pin, the pop-out — with *keep above other windows*
  when it already is one — and Close. Every item routes to a method that already
  existed. It is not new behaviour; it is the first place several pieces of
  existing behaviour can be *found* without knowing they are there. A block inside
  a tab group lends its title bar to the group and so has no menu of its own yet.
- Beside the page is the **pinned strip** (`ui/pinned_panel.py`), the one place
  that does *not* scroll — and it is **the same engine as the page**. `PinnedPanel`
  renders a region tree through the very same `build_node`, so a block in the strip
  sits in a `GridSplitter` with the same dividers and the same detents as one on
  the page, and can be split and nested exactly as freely. The strip's own
  `_PinnedLine` and `_PinnedSlot` are gone with the concept of a *line*: there is no
  "line along the strip holding blocks across it" any more, only cells. A drop names
  a block and a `side` of it, like the page's; `layout_tree.region_lines` still
  derives the old shape for anything that wants to read the strip in those terms.
- **Alignment went with the lines.** `fill`/`start`/`center`/`end` existed because a
  block that pinned its own size could not fill the cell it was given and would
  otherwise sit adrift in the middle of one. No block pins its own size now — the
  user does — so every block fills its cell and the choice had nothing left to
  decide. The menu item is gone and the key is out of the schema.
- **Moving the strip to another edge turns its tree.** A column down the right edge
  is a *row* along the bottom, so `_rotate` swaps every split's axis when the edge
  changes axis; a move between two side edges is only a change of side, so the shape
  stays and only its live pixel sizes go. Both drop the sizes, which measured an
  axis that has just stopped existing.
- `PinnedBoard` is what the host puts in its layout — a splitter holding the page and
  the panel, whose orientation and child order *are* the strip's edge and whose handle
  sets its thickness. That handle was the one divider on the sheet with **no detent
  and no mark**: the strip's *internal* dividers are the page's own splitters and
  came with theirs, but the board's was a bare `QSplitter`. It is a `_BoardSplitter`
  (a `GridSplitter`) now, and `PinnedPanel.recommended_size` answers what thickness
  the strip wants **from the region tree** rather than from Qt — a run along the
  thickness adds up and pays for its dividers, a run across it takes the largest,
  and a cell is the roomiest block in it. Only the tree knows which is which: two
  blocks stacked down a left-hand strip both want their own *width*, so the strip
  wants the wider of them; the same two along the bottom want it as thick as the
  taller. It offers **one** target where a divider between two blocks gets two —
  the other pane is the whole page, whose hint is a number about a scroll area
  rather than a width anybody meant the strip to stop at. The **`PinnedHandle` (📌) is always visible**: the empty strip's
  whole content, the drop target that gets the first block in, the grip the strip is
  dragged to another edge by (lighting four `EdgeZoneOverlay` bands), and the button
  that opens its menu. The canvas still owns the model — the panel is a view and holds
  no arrangement state; `_relayout` renders the strip **first** so the rows are the
  last to claim a frame, and the rescue pass that follows only touches frames still
  inside the page's own stack (rescuing one the strip had just taken would undo the
  render order from the other direction).
- **Nothing holds the window open any more.** `PinnedPanel.minimumSizeHint` used to
  report the strip's whole content, capped at the usable screen, precisely so the
  *window* would be held open rather than the strip clipping. That was right while a
  squashed block was a clipped one. It reports its handle and no more now, and
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
  wheel it has no use for** — `wheel_guard.has_scroll_range`, the same test the
  guard applies to it from the other side — passing the event up when it has no
  range on that axis; otherwise a block with nothing to scroll would swallow the
  gesture and the page under it would stop moving. It does **not** pass one up
  for having reached the end of its range: a block that scrolls owns the wheel
  while the pointer is over it, and the guard routes a wheel that started over a
  spin box or a table into it on the same rule. See
  [Shared UI utilities](ui-utilities.md), where that rule lives in full.
- **Keeping the wheel is `event.accept()`, and it is a separate line from
  deciding to keep it.** Whether the page *also* scrolls is not settled by who
  handles the event but by whether it comes back accepted: Qt walks an ignored
  wheel up the parent chain on its own, and the page is on that chain.
  `QAbstractSlider` ignores a wheel that does not change its value, so a block
  already at its bottom scrolled nothing, handed the event back still ignored, and
  the whole sheet moved — with the check above passing every time, because the
  decision to pass the event on is made *after* it, by Qt, on a flag nobody had
  set. `_InnerScroll.wheelEvent` therefore accepts unconditionally once it has
  decided the wheel is its. The tests watch `isAccepted()` for the same reason: a
  wheel delivered with `sendEvent` never runs Qt's propagation loop, so asserting
  on scrollbar values alone cannot see this at all — which is exactly how it
  shipped once.
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
- **A drop takes half of the block it lands beside, and nothing else moves.** The
  mark promises exactly that — the `DropRegion` wash fills half the target — and
  the tree used to break it: the run the arrival joined had its remembered sizes
  *cleared* and was laid out afresh from every cell's own hint, so the block you
  had aimed at frequently came out the size it went in while its neighbour paid
  for the arrival. `insert_beside` now replaces the target's own share with two
  halves of it and leaves every other cell in the run alone. This is not the
  "never mix a remembered size with a newcomer's natural hint" rule being broken:
  every cell in the run ends with an explicit number, and the newcomer's is
  derived from the cell it displaced. A run with nothing remembered still has
  nothing to halve, so the canvas takes the live divider positions into the tree
  (`_capture_sizes`) **before** the drag moves anything — before the `_detach`, in
  particular, since removing a block frees its space and drops the sizes that
  described the run it left. The one measurement the tree cannot make for itself
  is the target cell's live pixel size, which it needs when the cell is *wrapped*
  in a brand-new split (a drop across the parent's axis); the canvas passes it as
  `extent`. A zero in the page's own sizes is not a size but "take your content's",
  so a row nobody has dragged divides into two of itself rather than into two
  noughts.
- **A block fills the height it is given, and the slack goes inside the section.**
  A resizable page is the first thing that can hand a block *more* room than its
  content wants, and `setWidgetResizable` gives all of it to the section — which
  then has to put it somewhere. A `QVBoxLayout` with nothing expanding in it
  spreads the surplus **equally between its items**, so a Powers block dragged
  twice as tall as its cards did not grow a margin at the bottom: it grew a gap
  between every line on every card, and it read as a rendering fault because
  nothing on screen said which of the gaps was the slack.

  The first fix held the section at its content height over a spacer *outside* it.
  That cured the gaps and bought a worse fault: a section is a `QGroupBox` and
  draws a **border**, so a block dragged taller showed that border stopping half
  way down with bare block underneath — which reads as a block that failed to draw,
  not as slack. So the spacer moved in. `_give_trailing_slack` puts a stretch at the
  bottom of the section's own vertical box layout and `_InnerScroll.set_section`
  then hands it the whole viewport: the surplus lands under the last row, inside the
  border. Centrally rather than in each section, because a mod ships a block too and
  the rule that a section fills its block is the page's. A section that already
  expands downwards needs none — one that states **`fills_height`** (Notes' editor,
  the roller's history, the portrait, the Scene board), or one with something in it
  that claims the height (a table that stretches its rows). A **form** takes a
  trailing row of `_Slack` instead, since a `QFormLayout` holds widgets rather than
  items and one expanding row is enough to keep the surplus off its captions; only
  a layout none of that can reach keeps the wrapper.
  `fills_height` stays opt-in rather than derived: a widget's own vertical policy
  says nothing about whether its *children* have a use for the height (the roller's
  `QGroupBox` is `Preferred` and it is the history inside it that wants the room).

  **Who claims the height is asked of the layout's own items, never of
  `expandingDirections`** (`_claims_the_height`), and that is what Powers and
  Equipment were still broken on after every other block was cured. `QLayout`
  answers "both" for any layout that has not overridden the method — a
  `QFormLayout` has not — and the answer *travels*: `QWidgetItem` folds a widget's
  own layout's directions into the widget's, wherever that widget's policy is
  allowed to grow at all. So one form on one power card made the card expansive,
  which made the list of cards expansive, which made the section say it already had
  somewhere deliberate for a tall block's surplus. It had not, and the two blocks
  made of cards were the two that poured a dragged-taller block's whole surplus
  back down into the cards. What counts as a claim now is what a section states
  itself: a **stretch factor** in its own layout, or a child whose **own** size
  policy expands vertically — which is what the tables set on themselves, and why
  Skills still stretches its rows while Powers grows a band of nothing under
  *Add Power*. A hidden widget is passed over, since both blocks keep an
  empty-state label in the layout and hide it once there is anything to show.
- **Height is measured at the width the block has, and nothing else measures it
  right.** `sizeHint` answers with the height the content would take at its own
  *preferred* width, and `minimumSizeHint` at no particular width at all — a
  `QBoxLayout` builds that one by summing its items' unwrapped hints, with no
  height-for-width anywhere in it. For a block of wrapping cards both overstate,
  and they overstated by different amounts: the block took its height from the
  first, Qt decided whether to scroll from the second, and the Powers block ended
  up scrolling 30px inside a frame with nothing in the bottom 30px of it.
  `content_height` asks `heightForWidth` and `_InnerScroll._pin_content_height`
  pins the answer as an explicit `minimumHeight`, which is the one number
  `qSmartMinSize` takes over the hint; `content_size_hint` asks the same question
  for the frame's own hint. It may only ever **lower** what the widget already
  claimed — the point is to correct an overstatement, never to invent a refusal,
  and a block dragged genuinely too short still scrolls.
- **A section may name a height that is not its content's** — `preferred_height`,
  read by `content_size_hint` and nothing else. It exists for a **disclosure**:
  Name & Details' Details group grew the block, which grew the row, which pushed
  the Abilities row and everything under it 71px down the page and pulled it all
  back when the box was unticked. Adding a skill should move the page; ticking a
  box to look at a character's eye colour should not. So the block goes on asking
  for the height it asks for shut, uses the room it already has — a row is as tall
  as its tallest block and this is rarely that block — and scrolls past that.
  Deliberately separate from what the scroll area measures, which still sees the
  whole expanded content: that is what makes the remainder reachable rather than
  clipped.
- **A block whose content changes height has to say so itself.** The inner scroll
  area is a *barrier* — that is the whole reason a block can be dragged smaller
  than its section — and a barrier does not carry a changed **hint** out either.
  So a section that got taller told its viewport and nobody else: the row went on
  using the height it had cached, and the extra content sat scrolled out of sight
  inside a frame with page to spare underneath it. That is what a narrowed
  Advantages block looked like it was doing wrong — its descriptions wrapped, its
  rows grew, the table said so, and the row stayed exactly as tall as it was.
  `BlockFrame` watches the section for `LayoutRequest` (and re-asks on its own
  resize) and calls `updateGeometry` when the number has actually moved. That
  guard is what makes it safe to call from a resize, since `updateGeometry` asks
  the row to lay out again, which resizes the frame. It can still climb once — a
  taller page brings the page's scrollbar out, which narrows every block, which
  wraps more text — but that is monotonic, since the bar does not go away again.
- **The drag says four things, and it reads a block in *shares* of itself.**
  `_hit_test` answers: merge into it, stack above or below it, sit beside it, or —
  past the row entirely (`_GAP`) — start a new row. A `DropSlot` therefore carries
  a `target` block and a `side` as well as the old row-and-index, which is all the
  older callers (an anchor resolving, a block being reopened or unpinned) have ever
  known how to say.

  The geometry was pixels — a 28px merge inset and a 24px stack band, leaving
  "beside" as a **28px strip down each edge**. Placing a block second in a row was
  a target you had to aim at, on a gesture people use constantly, and the merge was
  never advertised at all: there is no discovering a gesture whose target is the
  part of the block you were already standing on. `drop_side` now takes the middle
  ninth (`_MERGE_SHARE`, a third by a third) as *merge*, and outside it the nearest
  edge wins measured as a share in each direction — so the four sides are clean
  diagonal quadrants, every corner has one answer, and the zones grow with the
  block instead of staying a hairline on a big one. `_GAP` stays a pixel band,
  deliberately: it guards the *seam* between two rows, which really is a
  line-shaped target and is marked by a line.
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
- **A group reports the same minimum a block does** — `block.min-extent` and a
  header — and that is a fix rather than a detail. It had none, so it inherited its
  layout's, which was its tab bar plus its buttons: about 218px. A cell holding one
  block could be dragged to a sliver and the *same* cell holding two refused, so a
  group could never be squashed and never be closed by being squashed. A minimum
  its own content decides is the one thing nothing on this page may report. Past it
  the header clips, exactly as a squashed block's title bar does.
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
- **The strip past the last tab is the group's own handle** (`_GroupHandle`), and
  it is to a group what a title bar is to a block: drag it and the whole cell
  moves, right-click it and the cell's menu opens. It is a *widget* rather than a
  rule about where on the tab bar the pointer is — the bar takes only the room its
  tabs need now and the handle takes the rest — so "a tab" and "not a tab" are two
  things that each answer for themselves instead of two readings of one
  coordinate. The handle never shrinks below `block.min-extent`; the bar gives way
  instead, which is what its eliding and its scroll buttons are for. Otherwise a
  group would be the one cell on the page that cannot be moved, and only once it
  was small enough to want moving.
- **The model half is one call, not a sequence.** `layout_tree` grew
  `move_leaf`, `move_leaf_to_row` and `merge_leaf_into`, over
  `insert_node_beside`/`append_node_row` — the general forms that `insert_beside`
  and `append_row` are now one line of. Moving the *blocks* one at a time and
  merging them back together at the far end reaches the same arrangement through
  four intermediate ones the user watches go by. `_detach_leaf` hands back the
  `Leaf` itself rather than rebuilding one from the keys, so which tab was active
  travels with the cell. A row index in `move_leaf_to_row` is re-measured after the
  detach, since removing the cell can take a row out from under the seam the drop
  named.
- **A group does not float, and that is a decision rather than an omission.** A
  block dragged by its title bar tears out into a `BlockWindow` that follows the
  cursor; a group being moved stays put and the page marks where it will land. A
  `BlockWindow` hosts one block and `floating` in a saved layout is a geometry per
  block key, so "a tab group in a window of its own" would be a fourth place a
  block can live plus the schema to carry it. Dragging one tab out is still how a
  member of a group gets into a window. For the same reason the **strip refuses a
  group**: it renders a multi-key cell as whichever tab is active and draws no bar,
  so a group dropped there would arrive with most of itself nowhere.
- **`_drag_keys` is what the hit test asks**, not `_drag_key`: one block for a
  title-bar drag and all of them for a group, so a cell is never offered a place
  inside itself.
- **A tab's right-click is its block's own menu** — the whole of `block_menu`,
  since everything on it still means something for a block in a group — plus *Move
  out of the group*. The handle's is deliberately short and shares nothing with
  it: a group is not pinned, popped out or closed *as a thing*, so what is left is
  the two questions that are only about the cell — *Fit to content*, and *Ungroup*
  / *Close these blocks*. Screenshot it with `driver.py tab-group`.
- **Releasing a tab silences the bar.** `QTabBar.removeTab` makes Qt pick another
  tab and announce it, which the group reports as `activeChanged` —
  indistinguishable, from the canvas's side, from a user clicking a tab. So
  dissolving a group raised a second "one gesture finished" and *Ungroup* landed in
  the layout history twice. Which tab a surviving group shows is the tree's answer
  (`layout_tree.remove` keeps the user looking at what they were looking at) and is
  re-read when it is rebuilt, so there was never anything here worth announcing.
- **The trade, stated plainly.** Two Notes blocks used to merge their *notes* into
  one tab bar; they now stay two blocks sharing a cell, so you get a tab bar of blocks
  each with its own tab bar of notes. That is more chrome than the old answer and it
  is the price of one merge rule instead of a per-block opt-in. The Notes block's own
  `adopt`/`release`/`open_refs`/`accepts_merge` are gone with it; splitting a *note*
  out into a new block is a different feature and stayed.
- A group of one collapses back into a plain block, so nothing can get stuck in
  one.

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
- **Three marks, and between them the placements are discoverable.** Over the
  middle of a block the whole frame washes (*these two become tabs*); over a side,
  a `DropRegion` washes the half the newcomer would take and the thin
  `DropIndicator` sits on the seam (*it takes that half, and the split falls
  here*); over the gap between rows, the line alone, because a new row has no room
  marked out for it until it exists. Dragging across one block therefore shows all
  three in turn, which is the only way anybody was going to find the merge. Both
  overlays share a `_SlidingOverlay` base for the animation and the
  "don't slide across an axis change" rule, are transparent to the mouse, and read
  their colours as they appear rather than caching a token at construction. The
  region is filled from `accent` and outlined in `drop.indicator`, because a wash
  needs channels to dilute and `drop.indicator` is allowed to be a live palette
  role. Screenshot them with `driver.py grid-drop-beside` / `grid-drop-merge`, and
  the close warning with `grid-close`.
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

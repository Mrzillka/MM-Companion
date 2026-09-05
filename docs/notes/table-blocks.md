# The table blocks

Matters when touching Abilities, Resistances, Advantages or Skills.

Working notes for MM-Companion, split out of [CLAUDE.md](../../CLAUDE.md).

Those four blocks are the same thing seen four ways — an ordered list of rows in a
`QTableWidget` that shows all of its content and never scrolls on its own (its
*block* may, once it has been dragged smaller than the table can reflow to) — and each
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
  `word_wrap` keeps wrapped rows fitted to their text, and `fit_width` measures the
  columns across too — right for a table that *is* the whole block (the stat grids),
  wrong for one panel of a column flow, whose section caps its own minimum at a
  single panel. Height uses `header.isHidden()`, never `isVisible()`: a table that
  has not been shown has no visible children, and that is exactly when its minimum
  is first asked for.
- **The spare height goes into the rows, and what the table *reports* does not
  move.** A page the user drags is the first thing that can hand a table more
  height than its rows want, and a table given it simply drew bare viewport under
  the last row — inside its own border, so dragging Abilities taller made the block
  bigger and the table no different, which is the exact shape of "it doesn't
  scale". `sync_row_stretch` shares the surplus out **evenly** over the rows
  instead (spare height is line spacing, and a wrapped two-line row wants the same
  breathing room as a one-line row, not twice as much), so a taller block reads as
  wider line spacing and a shorter one tightens back to its natural rows before the
  block starts scrolling. Abilities and Resistances share a row and therefore a
  height, and the shorter of the two now spends the difference on its own spacing
  rather than sitting half empty beside a full one.

  Three things hold it together, and each was a bug on the way:
  - **`row_heights` is the naturals, and everything reported is built from it.**
    Were the hint to follow the stretched rows, taller rows would mean a taller
    hint, which means a taller block, which means taller rows: the block walks down
    the page on its own. The naturals are recorded the instant before the first
    stretch — when every row still *is* at one — and `setRowCount` puts the rows
    back before it drops them, because it keeps the rows it already has and
    recording a stretched row as natural is the same runaway one step removed.
  - **A row spanned across every column is chrome, not data**, and sits the stretch
    out (`is_rule_row`). It catches both things drawn that way: the rule between
    the bought traits and the derived ones, which would otherwise become a 40px
    band of nothing, and the header a skill's focus buttons sit on. Spanning is the
    only way a table has of drawing across itself, so it needs no register.
  - **A widget in a cell keeps its own height** (`setCellWidget` holds it in a
    `_CentredCell`, `cellWidget` hands it back). The view gives a cell widget the
    cell's whole rectangle, which was invisible while every row was exactly as tall
    as its content: a stretched Abilities row turned every rank spin box into an
    85px pill. Text in a cell is centred in its row and a widget in one now reads
    the same way.
- **The stat tables have no frame of their own.** A table that *is* the whole block
  is framed by its section already, and the 11px band of section between the two
  was a border drawn inside a border: `build_stat_table` sets `NoFrame` and
  Abilities and Resistances zero their layout margins, so the grid runs to the
  block's own edge. It moved the shedding thresholds — 18px more width is most of a
  column at that size — which is why `tests/test_adaptive_blocks.py` names the
  width it narrows to rather than repeating a number.
- **A `fit_width` table's two widths are different questions.** `sizeHint` is the
  **whole** table — every column, header text included — and that is what Abilities
  and Resistances open at, which is what `block_sizes.json` means when it says those
  two state no recommendation. `minimumSizeHint` is the table with everything in
  `set_shed_order` *gone*, and it must not move with what is currently hidden.
  Shedding is how the table gets narrower, so a minimum that counted the columns it
  is willing to drop is refusing the width it knows how to reach — and worse, it is
  a **loop**. A block hands its section the viewport's width or the section's
  minimum, whichever is larger, so while that minimum tracked the shown columns,
  hiding one narrowed the block, which made the column fit again, which widened the
  block, which hid it again. No dead-band can damp that: the swing is the column's
  own width, not a scrollbar's. Each answer is a resize that lays out again inside
  the one before it, so it ended as a **stack overflow** — the app vanishing, exit
  code `0xC00000FD`, the moment the Resistances block was dragged past about 230px.
- **A table narrows by shedding columns, worst first** (`set_shed_order`,
  `sync_shed_columns` on resize). The decision itself is `reflow.parts_to_shed`,
  which this module re-exports as `columns_to_shed` because the same question —
  "which of these, in this order, do I drop to fit" — is asked about a card's
  widgets too (`reflow.ShedBox`), and a second implementation would be a second
  dead-band to get wrong.
  The order names what the block is willing to give up. Column 1 goes first
  everywhere: it repeats what the row already says (an ability's short code) or
  restates a number the Total carries anyway (a resistance's base). **Then Rank**,
  in all four blocks, and that is a real loss taken deliberately: a table squeezed
  to two columns is a trait and its number, which is what a sheet is *read* for,
  and the build is typed once at a width the player chooses while the sheet is read
  at whatever width the page has left. The consequence is that the spin boxes are
  not there to type into at that width — widen the block and they come straight
  back. Abilities' Total column stops being an "and here is what changed" while
  Rank is gone and prints the number outright (`apply_stat_effects(always=…)`,
  driven by `AutoHeightTable.shedChanged`), or a narrow Abilities block would be a
  column of blank cells. Past the order the name wraps, then elides, then the block
  scrolls; nothing is ever lost. Two things the fix above had to get
  right with it, and both were wrong:
  - **The dead-band applies from the first column.** It used to stand down whenever
    nothing was shed yet (`not current`), which is the state every table starts in —
    so the one transition most worth damping was the one that never was.
  - **A hidden column still has to report the width it would take**, or the table
    can never work out that it fits again. Qt cannot answer it: `sectionSizeHint` is
    zero for a hidden section and `sizeHintForColumn` measures the items without the
    header's text, so a hidden column reported roughly a *third* of its real width
    and was restored into a width that could not hold it. `natural_column_widths`
    measures a column while it is showing and remembers the number; a hidden one
    answers with that. Every column is measured before it can ever be shed, so the
    memory is always primed.
  - Both bands are measured against what the arrangement **in force** needs, which
    is what makes them nest: another column goes a band *below* that number and one
    comes back a band *above* it, so there is no width at which both are true.
  **`word_wrap` follows the *header*, not the table's own geometry**, coalesced to
  one `remeasure_wrapped_rows()` a turn on `sectionResized`. How tall a wrapped row
  is depends on the width of the column it wraps in, and that width settles *after*
  the rows are filled: a `ResizeToContents` sibling measures the items it was just
  given, takes its share, and the stretching column shrinks — with no resize of the
  table to notice. Measuring on the table's resize alone therefore fitted every row
  to a column wider than it ended up with, which looks like a wrapped row with its
  last line missing. A block that wants the height right *now*, without waiting for
  the event loop, calls `remeasure_wrapped_rows()` itself — **last**, after whatever
  fills the content-sized columns (`SkillsSection._rebuild` does it after
  `_refresh_totals`, which is what finally writes the ABL/+/Total cells).
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
- **`ColumnFlowPanels` answers two different width questions, and answering them the
  same way is what made a narrowed block clip instead of adapt** (`ui/sections/column_flow.py`).
  `_min_col_width` is what a panel *reads well* at — the widest label present plus
  every column — and it is the right **divisor** for "how many of these fit". It was
  also being reported as `minimumSizeHint`, and there it is a refusal: a panel is not
  stuck at that width, since its table sheds columns and its name column wraps. The
  block hands its section the larger of the viewport and that minimum, so at every
  size below a comfortable panel the section was handed a comfortable panel's width,
  the table never got narrow enough to shed a single column, and whatever the viewport
  could not show was simply cut off — which is exactly what a squeezed Skills or
  Advantages block did.
  `minimumSizeHint` reports **`_panel_floor_width`** instead: the narrowest one panel
  knows how to *reach*. Skills answers with a name and a total, Advantages with both
  columns at their own **header captions** (the same rule `_name_col_width` already
  floors the name at, and the honest floor for a column of prose — `min_desc_width` is
  a comfort number, and using one as a floor is the confusion this split ends). Both
  are metrics and constants, because a floor may move with neither the **lock**
  (`tests/test_lock_geometry.py`: a lock toggle may change a block's height, never its
  width) nor with anything the adaptive decisions themselves change.
  It is still a **ceiling**: the side-by-side tables would otherwise inflate the
  minimum to the full multi-column width, pinning the page wide and forcing at least
  two columns. Asking for a whole panel at all is only safe because `_min_col_width`
  is *bounded* — see the next bullet; while it tracked the widest label without a
  ceiling, a name a player typed would have held the window open at whatever width
  printed it on one line.
  `_init_flow_panels` also sets `SetNoConstraint` on the section's layout, for the
  reason `ReflowBox.init_reflow` does: a layout otherwise *imposes* its
  `totalMinimumSize` on the widget it manages, which beats any override and pinned the
  block at whatever the picker row happened to need.
- **`wrapping_column_width(metrics, texts, *, padding, cap, floor)`** — how wide a
  **first column that wraps** should be. A name column that must not clip has only
  two ways out, grow without limit or break the line, and the two blocks used to
  take opposite wrong turns: Advantages sized column 0 `ResizeToContents`, so a
  player's typed subject (`Benefit 3 (Wealthy — owns a mega-corp)`) grew it without
  limit, the stretching Description column paid, and the block demanded that much
  room wherever it went; Skills left its stretching name column to absorb a long
  focus label and Qt painted `"…"`. Both **break** now: content up to a cap
  (`column.advantage.name-max` / `column.skill.name-max`), wrapping past it, the row
  getting taller — which is the sheet's own bargain, a block shows all of its
  content and the page scrolls. The *floor* deliberately beats the cap, so a header's
  own caption never clips. Because `_min_col_width` is both the flow's panel divisor
  *and* what the section reports as its minimum, capping it is also what stops one long
  label collapsing a block to a single panel. The cap is **hard**, and the one case it
  cannot answer is a single *word* wider than the column: Qt's word wrap breaks between
  words and not inside one, so such a label elides instead. Flooring at the longest word
  was tried and is worse — it hands one typed 25-character word the power to pin the
  block and the page behind it open, which is the complaint the cap exists to answer —
  so the answer to *that* case is the **tooltip** every name cell now carries, the same
  elide-plus-tooltip bargain `ElidingLabel` strikes. (Advantages' `refresh_conditions`
  restores that tooltip rather than clearing it, or a struck-through row loses it.)
- **`SortControl`** — the sort combo, and the standing rule that a preset mode
  stands the drag down (`SORT_MANUAL` is the shared mode id). Sorting is a
  **permanent rewrite** of the stored order, in both blocks that have one: the new
  order is the one that saves, which makes Skills' "by total" a snapshot rather than
  a live view.

Three things the blocks add on top:

- **Resistances** is the one stat table that shows a row as **three numbers**, because
  three different things move a resistance and one total says nothing about which:
  **Ability** (the derived base — an Enhanced Stamina raises this), **Rank** (what was
  bought on top, starting at 0 — Protection and worn armour land past it), and **Total**
  (`resistance_total`, with a condition's overlay painted on). Both readouts come from
  `core.rules` and neither is ever added up in the widget; only the middle one is a
  control, and it holds **exactly what the model stores**.
  It did not always. The spin box used to hold the *total*, with the model storing the
  difference from a base the widget subtracted back out on every edit
  (`value - resistance_base(...)`), and that one trick paid for three separate
  complications: an ability moving had to re-seed a spin box the player might be typing
  in, the resistance range in `costs.json` had to be twice an ability's so a
  high-Stamina character's total would fit, and `set_stat_value` had to exist at all —
  a clamped display there would have rewritten the stored delta from the wrong number
  on the very next edit. Three columns cost none of that and end all of it.
  Column 1 is the seam: the two stat tables keep four columns and the *same indices*,
  and Resistances trades the short-code cell for its base value by passing `base_store`
  to `build_stat_table` (`ui/sections/stat_table.py`, whose module docstring holds the
  rest). Opt-in, because only a derived family has a base to show — and affordable only
  because a resistance's own `abbr` is blank in the base data, so that column stood
  empty in this block for its whole life. Everything that reads a row off either table
  addresses `COL_TOTAL`, so a table that had *shifted* its columns to make room would
  have moved that out from under the roll payload, the pin menu and the tests.
  The Total column is the other half of the trade: unlike Abilities' it is **never
  blank**, since it is now the number the game asks for rather than an "and here is what
  changed" (`apply_value_column`, beside the `apply_stat_effects` that still serves
  Abilities). A condition reaches the Total and nothing else — it is display-only and
  never part of the build, and a base a condition had rewritten would read as something
  the character *has*.

- **Advantages** sheds its **Type** column and *only* its Type column. The
  Description used to go next, and that was the wrong order of business for a column
  of prose: prose has a way of getting narrower that a one-word category does not, so
  it wraps and the row gets taller (`word_wrap`), and only past the point where even
  that cannot help does the cell elide. Losing it outright skipped both. So the order
  is **lose the Type, then break the lines, then crop**. The Advantage column is
  `Fixed` at a width measured from the names, and in a panel narrower than the block
  reads well at that was the whole panel — the stretching Description was left with
  the remainder and broke one word to a line — so `_name_column_for` clamps it to the
  panel, keeping the Description at least its own caption. That clamp is applied on
  every resize (`_sync_name_columns`) past a **dead-band**, and the band is
  load-bearing: every write there re-measures the wrapped rows, which changes the
  block's height, which brings the block's scrollbar out or takes it away, which moves
  the panel's width by the bar's extent and asks again — measured going round at
  exactly twelve pixels a turn, forever.
- **Advantages** dropped its ▲/▼ and "Remove" buttons for those gestures. Its picker
  keeps "Add"; removal is a thing done *to a row*, so it is on the row.
- **A granted advantage is an advantage everywhere except the budget.** An advantage's
  *mechanics* do not care which currency paid for it, so every resolver that reads an
  advantage's data fields reads `all_advantage_selections` — the bought list plus
  `granted_advantage_selections` — rather than `Character.advantages`, which holds only
  what the player bought. Cost and the Heroic budget are the deliberate exception, since
  the power already paid (`granted_advantages`). Initiative was the one place that had
  not been converted: it walked the bought list, so **Enhanced Trait: Improved
  Initiative** showed on this block, changed no number, and gave the player nothing to
  tell them why. The skill side (`skill_bonus_per_rank`) had always been folded in, which
  is exactly why the gap was invisible — those were the only two mechanics an advantage
  record carries. The same conversion reaches the three subsystems that look an advantage
  up **by name** rather than by a record field: Extra Effort's Determination, Untapped
  Potential and Extraordinary Effort (`extra_effort._advantage_ranks`), and Improvised
  Effect / Prepared Effect (`improvised.py`). **Equipment is deliberately left out** —
  `equipment_advantage_rank` still reads the bought list alone, because a Sustained or
  heavily-flawed power granting a *permanent point budget* is a cost loophole the others
  do not have. `granted_advantages` is `build_scoped` for this: initiative asks the
  bare two-argument question twice per readout (once for the ability, once for the
  bonus), and without the memo each of those walked the whole build — inside a scope
  where every other derived number costs nothing. Measured at 0.34 ms a call before the
  memo and 0.008 ms after, against a 0.002 ms baseline.
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
  Some rows are **granted, not bought**: an Enhanced Trait may raise
  `Expertise::Stealth` on a hero with no Expertise row at all, and `granted_skill_rows`
  finds the orphans so `_expand` can grow one indented row per orphan under its base
  skill. Such a row is muted, carries a read-only `—` where its rank spin would be, and
  is deliberately **not** in `_row_refs` — reordering or removing it would promise an
  edit the block cannot make, since the power owns it. It keeps its `ROLL_ROLE` payload,
  because a granted focus is a real skill and is rolled like one. `_split_blocks` and
  `_name_labels` count these rows too: a row left out of either is a row the panel was
  not sized for. `ENHANCEMENTS_CHANGED` routes to `refresh_granted`, not straight to
  `refresh_totals` — a row that does not exist cannot have its total refreshed — and that
  method rebuilds only when the granted *set* moved, falling through to `refresh_totals`
  otherwise, so the common signal does not cost the block its selection.

  "Add focus…" is a `getItem`, not a `getText`: a skill with an enumerable set of focuses
  offers them, editably, and one whose focuses cannot be listed shows its `focus_note` as
  the prompt instead. The Enhanced Trait picker reads the same two fields, so the two
  places a focus is named offer the same thing.

  Three rules keep a long focus name from being clipped, and all three are easy to
  re-break. A focused skill's header spans from **`COL_NAME`**, not from
  `COL_ABILITY`: a `ResizeToContents` column measures a spanned cell widget as its
  own content, so parking the "Add focus…" buttons on the Ability column made that
  column as wide as *they* are, and the stretching name column paid for it.
  `_min_col_width` budgets **every** column — the Ability column was missing from
  that sum, so the flow fitted one panel too many and the name column silently
  absorbed the shortfall. A column left out of that sum is a column the name column
  pays for. And the panel is `word_wrap`, so what is left over after all that
  arithmetic **wraps** rather than eliding: the two sums above are heuristics, the
  section's reported minimum is capped at one panel and can legitimately be *below*
  what that panel wants, and a name silently cut off is the worst of the outcomes.
  Only the indented focus/specialization rows and the locked view are plain items
  and wrap; the two cell-widget name cells (the unlocked `＋` row, the group header)
  hold a bare skill name, which the cap fits comfortably.

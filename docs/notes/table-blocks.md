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
  `columns_to_shed`, `sync_shed_columns` on resize). The order names what the block
  is willing to give up — the stat grids offer only the abbreviation, which repeats
  the name beside it; Rank is what you type and Total is what you read, so a table
  missing either has stopped being the table. Past the order the name elides and
  then the block scrolls; nothing is ever lost. Two things the fix above had to get
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
- **`ColumnFlowPanels.minimumSizeHint` reports exactly one panel** — its floor *and*
  its ceiling (`ui/sections/column_flow.py`). A ceiling because the side-by-side
  tables would otherwise inflate the section's minimum to the full multi-column
  width, pinning the page wide and forcing at least two columns. A **floor** because
  it used to be `min(hint.width(), _min_col_width())`, and whenever the section's own
  layout minimum was the smaller of the two the block asked for *less than a panel
  needs*: the frame's `block_sizes.json` floor applied instead and the stretching name
  column silently absorbed the shortfall, which is what cut a long skill name off. It
  also made the answer depend on the **lock** (a locked section hides its picker and
  asks for less), which is the standing rule in `tests/test_lock_geometry.py` that a
  lock toggle may change a block's height but never its width. Asking for a whole
  panel is only safe because `_min_col_width` is now *bounded* — see the next bullet;
  while it tracked the widest label without a ceiling, a name a player typed would
  have held the window open at whatever width printed it on one line.
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

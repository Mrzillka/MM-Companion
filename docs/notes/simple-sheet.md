# The simple sheet

Matters when touching the play view (View ▸ Simple Sheet, the bar's **Simple sheet**
switch), printing, or adding a block — every block has a look there, even if it is the
default one.

Working notes for MM-Companion, split out of [CLAUDE.md](../../CLAUDE.md).

## What it is

- The edit sheet is a workbench: every block a movable, resizable panel with a title
  bar, every number an input. The **simple sheet** is the same character laid out the
  way a printed sheet is — boxes butted together with a hairline gap, a small-caps
  heading on each, the values a player reads mid-fight large, and what *qualifies* them
  (Ranged · Standard · Instant, "Extras: Accurate") as small translucent italics.
  Nothing that only changes while building is on it: no ranks where there is a total,
  no point costs, no Power Level limits, no pickers, no build markers (⚠, ⌂).
- It lives in `ui/simple/`: `sheet.py` (the page, `SimpleSheet`), `layout.py` (the two
  presets, pure), `registry.py` (how each block is shown), `views.py` (the blocks it
  draws itself), `widgets.py` (stat box, line, column grid), `style.py` (the two
  typographic registers) and `printing.py`. `ui/simple/__init__.py` imports **nothing**,
  on purpose: the card modules reach for `ui.simple.style`, and the views reach for the
  sections those cards belong to, so a package that imported its own parts would import
  itself in a circle.
- `CharacterSheet.set_simple(bool)` is the switch; it builds the `SimpleSheet` on first
  use and keeps it (its bus subscriptions are made once). The sheet's own page (the
  `PinnedBoard`) is hidden while the simple one is up. **The sheet is locked** while it
  is up — every play-time control is one that already survives the lock — and the lock
  is put back as it was on the way out. `MainWindow` disables the 🔒 while simple; the
  bar switch sits *before* ↶ ↷ so the three glyphs stay the bar's closing cluster (a
  test pins that).
- **Whether a window is showing it is not remembered**, on the precedent of the lock
  and compact mode: a view switch, not a preference. **Which preset** it uses is a
  preference and is — `storage.simple_sheet_preset()` (with the usual accessor
  fallback, since `load_settings` is verbatim). A player who plays from it can make it
  a habit instead: Settings ▸ General ▸ *Open saved characters in the simple sheet*
  (`storage.simple_sheet_on_open()`, off by default). Only a character opened
  **locked** — from the library, or a GM's view of a player — goes there: a new one
  opens for editing, and an NPC is the GM's prep sheet.

## Two kinds of box, and why the power cards are borrowed

- A block is shown one of two ways, and `ui/simple/registry.py` is where it says which
  (`register_simple_view` — the extend-a-registry seam, open to mods):
  - **a view of its own** (`views.py`) for a block a player only *reads*: Name &
    Details, the portrait, Power Level & System, Abilities, Resistances, Conditions,
    Skills, Advantages, Complications. Built from `(data, character)` plus a
    `SimpleContext` whose `section` is the block's **live section**;
  - **the block's own section, borrowed** — Powers, Equipment, Notes, the Dice roller,
    the Scene, and anything unregistered (so a mod's block works with nothing to do).
- **Borrowed rather than redrawn, for the play controls.** The power cards carry an
  enormous amount of runtime behaviour: the on/off switch with its Linked-group rules,
  an array's live member, an effect selector, rank dials, a Dynamic array's share dials
  and their pool coordinator, Extra Effort and stunts on the right-click, counters.
  Equipment has wearing and the throttle. Drawing a second version of all that would be
  a second implementation to keep in step, forever. Borrowing makes "every parameter
  that changes during play stays changeable" true by construction. A section with a
  quieter look for this page answers `set_simple(bool)` (Powers, Equipment); any other
  borrowed `QGroupBox` just loses its border (`titled_section.set_simple_frame`).
- **Notes stay writable on the simple sheet** (`NotesSection.set_simple`), although the
  sheet is locked. The lock is off while the simple sheet is up, and a locked note is
  read-only — so without this a player could not write a note during play without
  leaving the view. A note is the one thing on a sheet that is *written* at the table.
- `BlockFrame.lend_section()` / `take_back_section()` are the seam. It **hides before
  it leaves** (a parentless visible widget is a window — the standing rule, watched by
  `test_switching_flashes_no_window`) and drops the explicit `minimumHeight`
  `_InnerScroll` pinned on the section, which was the content height *at the frame's
  width* and would be a refusal anywhere else. A frame the section was wrapped in
  (`_wrap_top_aligned`) keeps its wrapper and gets the section back into it.
- **Anything a view *does* spends through the live section's funnel**, never the
  model directly: a hero point through `SystemInfoSection.set_hero_points` (which writes
  the roll-history note), a condition through the new `ConditionsSection` public
  funnels (`choose_condition`, `shed_condition`, `apply_damage_step`, `roll_confused`),
  a roll through the section's own `rollRequested`/`loadRequested` (so the bus routes
  it to the Dice block — `SimpleContext.roll`/`load`). That is what keeps the undo step,
  the note, the dirty flag and the GM's card identical to the edit page's.
- **A GM can pin from it.** On a sheet opened from a GM card the stat boxes, skill lines
  and Initiative offer the edit rows' "Pin to GM card" / "Unpin" right-click
  (`widgets.make_pinnable`, reading the section's `pin_state` at click time and emitting
  through its own `pinRequested`/`unpinRequested`). `pin_menu` builds the menu without
  showing it — the test seam, since a modal menu headless hangs.
- **Views redraw off the bus.** `SimpleSheet` subscribes one *coalesced* handler to
  every notification topic (`NOTIFICATIONS`, `edited` included), so a GM's command, an
  undo (`reseed` publishes `RESEED_TOPICS`) or a borrowed card's toggle all reach the
  page once per turn. A view's `refresh()` must be an idempotent redraw from the model.
  Hidden while the edit page is up, it does nothing. **A list skips a redraw that would
  draw the same thing** (`_View._unchanged`, a signature of *values*, never of the
  mutable selections): every click on the sheet redraws every view, a hero point moves
  nothing in Skills or Advantages, and rebuilding fifty lines each time cost a quarter of
  a second a click on a well-stocked character. The same measurement is why the hover
  wash is **one stylesheet per view** matched on a `rollable` property rather than one
  per line (a sheet is polished per widget), and why `ColumnGrid` moves lines between
  columns without hiding and re-showing them.
- **Switching is kept cheap on purpose.** The sheet is only re-locked if it was not
  locked (locking rebuilds the card trees, and a sheet opened to play from is locked
  already), the build runs inside `stable_build()`, and a *rebuild* — a preset change,
  a session starting — keeps its borrowed sections lent and dressed rather than giving
  them back and borrowing them again (`_teardown(give_back=False)`), handing back only
  what the new arrangement no longer shows. Each of those halved something measured.
- **A theme switch redraws it** (`changeEvent` on a style or palette change, through the
  same coalesced rebuild): its boxes and views read their tokens when built.
- Floated block windows are hidden while the page is up (they show the *edit* look of
  blocks that are now on the page, and a borrowed one would be an empty frame) and come
  back after. Compact mode re-shows them on its way out, so `CharacterSheet.
  suspend_windows` asks the simple sheet to hide them again. Compact mode itself works
  from the simple sheet unchanged: it borrows the roller *out of the Dice section*,
  wherever that section is, and gives it back there.
- A request for a block the Custom preset does not show (a roll with the roller closed)
  reveals it on the edit page as before and then **rebuilds the simple page**
  (`SimpleSheet.reveal`), rather than raising a floated window that is hidden.

## The page

- A preset is a `layout_tree` node — the same `Split`/`Leaf` tree the canvas keeps —
  rendered by `render_node`: a vertical split stacks, a horizontal one is a
  `SimpleRow`, a multi-key leaf (a tab group on the edit page) shows **every** block in
  it one under another, since tabs are a way of saving screen room and a sheet you read
  or print wants every box on it. Nothing on the page is dragged or resized.
- **Standard** (`STANDARD_PAGE`) is modelled on a printed sheet: portrait · name ·
  level across the top, Abilities beside Resistances, Conditions, Skills beside
  Advantages over Complications, then Powers, Equipment and Notes each a full row, with
  the roller and the Scene in a strip on the right. A block it does not have is left out
  (its row **keeps its weights** — a missing portrait must not hand the rest a proportion
  nobody chose); a block it does not know gets a row at the end; on a **GM's NPC** the
  blocks an NPC sheet opens without (`npc_hidden_keys` — the roller, the Scene, the
  prose) are left out too; `notes#2` sits with
  `notes`. **Custom** is `BlockCanvas.arrangement()` read through: the page tree, the
  strip on its edge at its width, closed blocks left out, floated ones appended. An
  unreadable arrangement falls back to Standard rather than a blank page.
- **Width adapts, height scrolls — on this page too.** A `SimpleRow` divides its width
  by weight and is a `ReflowBox`: once any box's share would be under its comfortable
  width it stacks the boxes, and un-stacks when the room returns — dead-band and
  `@no_reentry` included, for the reason every adaptive decision has both. A box's
  comfortable width is `simple.box-min` unless its content says otherwise
  (`simple_min_width` — the portrait reads at half). Lists deal into balanced,
  **independent** columns (`ColumnGrid`, over `column_count`/`even_split`): a shared
  grid of rows stretched a short advantage to match a long one beside it.
- **`SimplePage.minimumSizeHint` is zero, and that is a fix.** Qt sums a layout's items'
  minimums, and a wrapped label's minimum is its height at its *narrowest*: the
  Advantages box alone claimed three times the height its lines take, the scroll area
  never lets a page be shorter than its minimum, and every row got the difference as
  blank space. The scroll area also asks `heightForWidth` at the viewport's width and
  takes the larger answer, so stating no minimum leaves the honest number deciding.
- The strip opens at its width (360, or the edit strip's own under Custom) but never
  more than 40% of the window (`STRIP_SHARE`), re-applied on every resize until the
  player drags the divider. Sizing it once was sizing it against a half-built window.
- A block may say it has nothing to show (`is_empty()`) and is then left out, on screen
  and on paper, its room going to its neighbours: a portrait never loaded, no powers, no
  gear, no advantages, no complications, a printed Notes block with nothing open, the
  Scene outside a session (`CharacterSheet.sync_session` rebuilds the page when one
  starts or ends). Asked
  of a section **before** it is lent, so an empty one never leaves its frame. Opt-in and
  only for emptiness that is a fact about the character: Conditions is never empty in
  this sense (it is where a condition goes on), nor is the live Notes block (it is where
  a player opens one).

## Typography

- Two registers, in `style.py`: `value_label` (`size.simple-value`, bold) for the number,
  `TermLabel` (`size.simple-term`, italic, at `opacity.term`) for what qualifies it.
  **The translucency is a palette colour, not an effect and not a stylesheet colour**:
  `TermLabel` takes its parent's text colour and applies the alpha, re-deriving on every
  palette or parent change. An opacity effect would print its text as a bitmap; a
  stylesheet colour would be fixed and could not follow the paper palette.
- On a power or item card the effect's rows are split three ways (`cards/effects.py`,
  `simple_effects_block`): the attack and save go to the roll chips; the rows that
  *qualify* the effect — Type, Range, Action, Duration, notes, overcome-by, degrees,
  the PL-cap note — become one term line (`TERM_ROW_KEYS`, by key, never by label);
  every other row is what the effect *does* (a leap distance, "Enhances: Toughness +4")
  and reads at ordinary size, tinted as the edit card tints it. The extras/flaws column
  is not repeated: the `notes` row already names every modifier with no visible effect.
- A roll chip prints the number big and the words small (`split_roll_label`): an attack
  as its signed bonus, a save the target makes as `DC n`. The words are the spec's own
  label with the number taken out, so nothing the rules layer wrote is lost.
- Every size and gap is a token (`simple.*`, `size.simple-*`, `opacity.term`), all in
  Classic and inherited by the other presets through the default-preset fallback.

## Printing

- File ▸ **Print…** (a print preview, Ctrl+P) and **Export as PDF…** print **the simple
  sheet** in the current preset, on the system's paper size (A4 or Letter, a PDF too),
  whichever view is on screen; a strip's printed blocks follow the page as rows.
- **Which blocks is asked every time and remembered** (`print_dialog.PrintChoiceDialog`):
  one checkbox per block, ticked by each block's `printable` (the roller and the Scene
  off) unless the player chose otherwise before — `storage.simple_print_choices()`,
  stored by kind of block and only for what was changed, so a block added later prints
  by its own default. Per print rather than in Settings, because it is decided per
  print (the GM's copy with notes, the table's without).
- It is a **copy** (`PrintDocument`): views built with no section, borrowed blocks built
  afresh by `CharacterSheet.build_detached_section` (locked, simple, NPC-aware, on no
  bus), Notes printed as their text (`NotesPrintView` — an editor prints its first
  screenful). So printing never moves, resizes or re-dresses a widget the player is
  using. The root is a real shown window with `WA_DontShowOnScreen`: the sections reflow
  off their own resize events, which a never-shown widget does not get. Its layout is
  `SetNoConstraint` and it is sized by `heightForWidth` — a window's layout otherwise
  holds it at the summed minimum, the same overstatement as above.
- **The palette has to be set on every widget of the copy** (`PrintDocument.dress`).
  With an application stylesheet installed — every styled preset — Qt marks each widget
  `WA_StyleSheet` and stops propagating a parent's palette to its children; a palette on
  the root reached nothing, and a Slate Dark sheet printed light grey text on white.
  Graphics effects are stripped too: on paper every power is there to be read.
- **A page is two layers: a picture, and the words on it as real text.** A
  `QWidget.render` straight into a `QPrinter` goes through an intermediate pixmap in
  this Qt — even a bare `QLabel` reaches the PDF as a bitmap — at the printer's own
  resolution (a four-page sheet was a 15 MB PDF that took 8 s), and text in a picture
  cannot be searched or selected. So each band is drawn at 300 dpi (`PRINT_DPI`) with
  **every text label silenced** (`_SilenceLabels`, an event filter that eats their
  paint — never an edit to the label, which would re-lay the page out), and the labels'
  text is then drawn over it by the painter (`draw_text` / `_draw_label`): at the
  label's own position, alignment and wrapping, the text it *shows* (`QLabel.text`, so
  an eliding label's shortened caption), Markdown and rich text through a
  `QTextDocument` as a label's own does. Two things are easy to get wrong there: the
  font's point size is rescaled from the screen's density to the device's, since the
  painter's scaling is applied on top; and **a stylesheet `color` never reaches the
  palette**, so the colour, weight and slant are read off the label's own one-line sheet
  (`_sheet_text_style`), or every heading and tint printed black. A bit over 1 s and
  about half a megabyte a page, and the footer is text too.
- **Page breaks fall between things** (`page_breaks`): never inside a label, a control,
  a stat box, a list line; a power card and a small box move whole to the next page if
  they fit on one; a box's heading is never the last thing on a page. Only a page that
  would be left less than `MIN_FILL` full, or a single thing taller than a page, is cut.

## Tests

`tests/test_simple_layout.py` (the presets and the roll-chip split, pure) and
`tests/test_simple_sheet.py` (borrowing and giving back, the lock, every play control on
the page, undo, the bus, compact mode, the floated windows, NPCs, no window flash,
reflow, and printing: palette, page breaks, the copy leaving the sheet alone).
Screenshot it with `driver.py simple-sheet` / `simple-sheet-custom` /
`simple-sheet-narrow`, and a printed page with `driver.py simple-print --theme
slate-dark`.

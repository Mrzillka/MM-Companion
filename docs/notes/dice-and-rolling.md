# The dice roller

Matters when touching the roller, the roll history, compact mode or any stat block that rolls.

Working notes for MM-Companion, split out of [CLAUDE.md](../../CLAUDE.md).

## The Dice block

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
- **And requests**, the third kind (`RequestCard`, `KIND_REQUEST`) — see "Asking the
  table to roll" below. Both of the guards above are therefore written as *"is this a
  die roll"* (`kind == KIND_ROLL`) rather than *"is this not a note"*: a request has
  no die either, and `RollRecord.from_dict` back-fills `KIND_ROLL` for a log line
  written before any of this, so the test is safe on replayed history.
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

## The Dice block's height (the rule: it never scrolls, and neither does the strip)

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

The board **only re-asserts the strip's thickness when the strip actually rebuilt**.
`PinnedPanel.set_blocks` answers whether it did, and `PinnedBoard.set_blocks` settles on
that answer. The canvas re-renders the strip on every structural change and most of them
are about the *page*, so settling regardless meant five `setSizes` over ~80ms fighting a
minimum that was already satisfied — on every dock, drop, hide and show. The retries are
for a *stale* minimum, which only a rebuild leaves behind; a minimum that is genuinely
larger never yields, and on a stock sheet it never can (the default extent is 320 against
the Dice block's 360 floor), so the loop ran to its cap every time. That was the jitter
when rearranging blocks. The paths that legitimately re-apply keep it: `set_extent`,
`_apply_edge`, and the first render.

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
  **floated block makes exactly the same trade** — see [the sheet
  notes](sheet-and-blocks.md). It goes **as small as it is
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
  hero point spent or gained, or for a use of Extra Effort, answered by
  `DiceSection.post_note`. It is in `bus.QUIET_REQUESTS`, which is the one thing that
  sets it apart from the two roll topics — a note is a **side effect** of an edit the
  user was making elsewhere on the sheet, so reopening a closed Dice block for one would
  be the app grabbing the screen unasked. The note reaches the session either way.
  `load-requested` is deliberately *not* quiet: someone who clicked a stat line asked to
  see it loaded. (The other quiet topics are the two pin ones, which no block serves at
  all, and `hero-point-requested`, which is the price of something the user did in
  another block — see the powers notes on Extra Effort.)
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
  `localize_spec` reads the key **together with `kind`**, so it answers an ability, a
  resistance, a skill or initiative — a bare key is ambiguous, a mod may call an
  ability and a skill the same thing, and an *empty* kind still means a resistance
  (which is what a save written by hand or by an older client says). The gate stays
  the **trait key**, never the kind: every builder in `rolls.py` leaves it empty, so a
  trait double-clicked on one's own sheet already carries this character's number and
  localizing on the kind alone would add it twice.
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

## Asking the table to roll (matters when touching the roller or the session log)

The roller's **Request** row — a trait combo, a bare DC spin box, an "Ask" button —
puts a roll in *everyone's* history for somebody else to make. The chain, run
backwards: the save chip exists because an attack landed, and there was no way to
ask for a check nobody had provoked.

- **A request is an ordinary `RollSpec` with `modifier = 0` and a `trait_key`**, so
  nothing about a spec on the wire changed (`sanitize_spec` already whitelists
  `kind` and `trait_key`). Each recipient's Dice block fills in *their* number
  through the localizer that was already installed for saves — which is the whole
  implementation of "it arrives ready to roll".
- **The choices are `rules/pins.requested_roll_choices(data)`** — a third
  character-free list beside `default_pin_choices`, and smaller again: abilities,
  resistances, skills and initiative, being exactly the four kinds `localize_spec`
  can answer. **No Defence DC** (it resolves to no spec — it is a difficulty), and
  **no powers or equipment** (a pin names a power by an id belonging to one
  character, so there is nothing honest to localize on anyone else's sheet). Labels
  are full names rather than `pin_label`'s chip abbreviations: these are read in a
  combo and in a sentence, where `AWE` is a riddle.
- **The panel is handed the answer, not the data** — `set_roll_choices(groups)`
  beside `set_localizer`, and the row **hides itself** (emitting `contentChanged`,
  which is not optional — see "The Dice block's height") when a host supplies none.
  It emits `rollRequested` and neither sends nor rolls: where a request goes is the
  question `post_note` already answers, so `DiceSection.request_roll` is that method
  written twice — session first, private history off the air. The GM window answers
  for itself, because it owns its history and the view's fallback stands down there.
- **The DC box is a plain spin box where 0 means no DC.** The Difficulty Class row
  above it needs a checkbox because the panel has to tell "no DC" from "DC 0" for a
  roll it *grades*; nobody asks for a roll against DC 0. It is also **arrowless**:
  the theme reserves ~50px for the arrows the platform style draws, which took 148px
  of a 210px cell and clipped the trait name to "Trai".
- **The button is on every card, including the asker's**, and this is the one place
  the `chain_widgets(localize=False)` rule is deliberately reversed — you *are* a
  target of your own request. Hence `request_widgets`, a sibling rather than a reuse:
  `chain_widgets` asks a rolled spec what it *provoked* and needs a `follow_up` and a
  passing degree, while a request **is** the roll, sitting in the log waiting.
- Wire: `RollPrompt` → `SessionState.record_request` → a `KIND_REQUEST` record
  needing **no new `RollRecord` field** (`label`/`dc`/`spec` were already there), and
  `PROTOCOL_VERSION` **8**. Not GM-gated — anyone may ask. A spec that does not
  survive `sanitize_spec` is dropped rather than recorded: a card with a dead button
  is worse than no card.

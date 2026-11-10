# The dice roller

Matters when touching the roller, the roll history, compact mode or any stat block that rolls.

Working notes for MM-Companion, split out of [CLAUDE.md](../../CLAUDE.md).

## The Dice block

- `DiceSection` (`ui/sections/dice.py`) is the d20 roller **as a block**, and it is the
  one block whose descriptor sets `default_pinned` — a die that scrolls away with the
  page is no use mid-fight, so it starts in the strip. There is no standalone roller
  window any more (no `Tools` menu, no launcher button): `ui/dice_roller.py` offers
  `DiceRollerPanel` (the roll controls — GM Mode embeds one with `hidden_option=True`;
  its shape, Ask to Roll form and quick rolls are mixins in `ui/roller/`, while the rolling
  stays in `dice_roller.py`, where the test suite patches `roll_d20`), and `ui/roller/`
  holds `LocalRollHistory` (the private list of one's own rolls, in `history.py`) and
  `DiceRollerView` (a panel plus **whichever history is right** — the private one
  alone, the table's shared `RollHistoryPanel` in a session — in `view.py`), which is
  what the block hosts. Two ways it is
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
- **The panel's layout** (reworked 2026-11-10, the user's call after a UI/UX review):
  from the top, the chip slot; **Modifier** — one snug signed box with the
  circumstance steps either side (`−5 −2 [+0] +2 +5`); one **toolbar** — the DC box,
  **Take 10**, **Actions ▾** — in a `FlowContainer` that wraps when narrow; the action
  lane; then the die with its result **beside** it; then the quick-roll pills; then the
  history under a plain heading. What it replaced, and why:
  - **No group boxes.** "Roll", "Quick rolls" and "History" were three frames nested in
    the block's own. The view pads its contents by `space.sm` instead (the Roll box used
    to supply that), and a history is `view.titled_box` — a muted bold heading over the
    list, whose scroll area already draws an edge round the cards.
  - **The steps are data**: `system.circumstanceModifiers` (`[1, 5]` since the play-test
    pass below; it was `[2, 5]`, PDF p17's minor and
    major), parsed to `SystemRules.circumstance_modifiers` and pushed in by the host with
    `set_modifier_steps` beside `set_roll_choices` — the panel reads no game data. A step
    **adds** (`add_bonus`), so +2 twice is +4. They replaced a −30..+30 slider, which is
    no way to hit +2; the box keeps the full ±30.
  - **The DC is one box where 0 (`—`) is no DC** (`dc()` / `set_dc()`). It was a checkbox
    beside a box that sat greyed at 15 while unticked. Nobody grades against DC 0.
  - **Take 10 is a button** (`take_ten`) that makes the check *now* as a routine one —
    whatever is loaded, at the modifier and DC set — rather than a checkbox that changed
    what the next click of the die did. `_routine_next` carries it for that one roll and
    is cleared by the result *and* by `_abandon_roll` (with no box ticked, nothing would
    show a stuck flag). It no longer combines with an aimed attack's roll; it did not
    need to, since the GM's app counts a routine result against a minion by itself
    (`routine_total`, see [the minions notes](minions.md)).
  - **The result sits beside the die** (`_arrange_die`), which is smaller (`die.size`
    120, `size.dice-face` 28): the 160px die with three lines under it was the tallest
    thing in the strip. Compact mode stacks them again, beside the quick rolls. The
    readout no longer repeats the loaded trait (`Rolling Athletics — click the die.`):
    the chip names it and the ring on the die says it is armed, so it just says
    `IDLE_PROMPT`, muted (`text.muted.rich` — a palette role means nothing in HTML).
  - **Beside the die, the reflow counts the die part at its preferred width**
    (`RollerShapeMixin.row_minimum_width`). The readout word-wraps, so its own minimum is
    a word, and taken at that the slimmer panel "fitted" a row in the 360px strip with
    the result squeezed to a sliver. A column still narrows as far as it likes.
  - **And the view counts the panel at its stacked preferred width**
    (`DiceRollerView.row_minimum_width` → `column_natural_width`) before putting the
    history beside it, for the same reason one level up: summing the two minimums put
    the history beside a 114px roller in a 370px strip, with the die cut in half.
  - **Compact stacks the result under the die only while there are pills beside it**
    (`sync_die_arrangement`, re-run by `_rebuild_quick_strip`). Alone in the row, the
    die part takes the row and keeps the result beside it.
  - **A change of readout re-divides the view** (`_readout_changed`, from
    `_update_readout` and `_abandon_roll`), and invalidates the layouts between the
    readout and the panel *first*. Qt passes that invalidation up one level per turn of
    the event loop, so the view's re-division read the panel's old height and kept it:
    the unjudged-attack warning, several lines long, was cut off with the bottom of the
    die (the user's report, 2026-11-10, Compact in the strip).
    `test_a_long_result_is_never_cut_off` fails if either of the last two is undone.
  - **The Actions button stays put**: it is on the toolbar *above* the lane, so a pick
    that adds a line no longer moves it out from under the pointer.
  - **Ask to Roll is a form off the Actions popup** (see "Asking the table to roll").
  - **Quick rolls are pills** that are hidden altogether while there are none; the empty
    private history says how to fill them (`LOCAL_EMPTY_TEXT`). See the quick rolls
    bullet below.
  - The bottom strip used to clip the die below the window (a column of four in a short
    strip); the slimmer panel makes the one row of four, and no longer needs fixing on
    its own.
- **The polish pass** (2026-11-10, the user's call after a second review):
  - **A pill says what it rolls and at what** (`quick_label`): "Perception +7", and an
    unnamed one "d20 +2" (`PLAIN_CHIP`). It used to show the name *or* the numbers.
    `_QuickRollButton.sizeHint` is the caption plus `space.sm` + `focus.width` either
    side: the platform asked ~30px more, which made a short pill a slab.
  - **The die glows with how the roll went** for `OUTCOME_GLOW_MS` (`outcome_token`,
    `_glow_outcome`): green or red when graded, `dice.critical` (gold, a colour token
    of its own) on a natural 20 and red on a natural 1, DC or not. Routine checks and a
    lucky re-roll's lifted die never glow as naturals. The glow outranks the armed ring
    while it lasts and is put out when the next roll starts. A graded **total** wears
    the same tint in the readout.
  - **A result never breaks inside a phrase** (`degree_html`): "Failure (2 degrees)"
    and "— Nat 20!" are each a `white-space:nowrap` span, so a narrow card wraps the
    whole grade to its own line instead of "Failure (2" over "degrees)". Spans rather
    than `&nbsp;`, which changed the words for anything searching the text (a test
    looking for "Nat 20" was the first to notice). `degree_label` stays plain text.
  - **A maneuver line says what its slider trades** (`maneuver_trade`, the lane's
    `describe`): "+3 attack, −3 Defense", muted and elided under the slider.
  - **Keyboard**: `Ctrl+R` rolls (as a click of the die would), `Ctrl+Shift+R`
    rerolls, `Ctrl+T` takes 10 — `WindowShortcut`s on the panel, so they work with the
    pointer on the sheet and go away with the Dice block.
  - **The DC box says "none"** (`DC_NONE`; "not set", muted, since the play-test pass)
    rather than "—", and a set DC is drawn as a
    tinted **capsule** round the label and box (`#dcPair`, `_mark_dc`). Tinting the
    text alone was tried and read as a disabled field; the capsule is the frame's, not
    the spin box's (theme rule 4), and its border is there transparent at rest so it
    costs no pixels to appear.
  - **A card's ✕ shows only under the pointer** (`show_on_hover`, revealed by the
    card's enter/leave), flat, its room kept so the heading never re-wraps — the pills'
    bargain. It is the GM's on a shared card and everyone's on a private one.
  - **A card knows when it was made** (`stamp_card`): its time is the card's tooltip,
    and a card `GAP_SECONDS` (10 min) or more after the one below it opens with a
    muted "⋯ 25 min later" (or the date across a day). The line is *inside* the newer
    card, never a widget between two, so striking, trimming and replaying cannot strand
    one. Session cards read the record's `timestamp`; private ones are stamped now.
  - **In a row, the quick rolls go under the controls** (`_place_quick_rolls`, into the
    settings grid's `QUICK_ROW`): a row is two columns — controls, die — not three. In a
    short wide bottom strip the third column was an empty slab beside a pill or two,
    width the history now gets. `row_minimum_width` / `row_natural_width` count the
    controls column as the wider of the two, whichever layout holds the pills, so the
    reflow decides the same either way. Compact takes them out first.
- **The justified pass** (2026-11-10, the user's calls on a third review; tests in
  `tests/ui/dice/test_roller_justified.py` and `tests/core/test_roll_parts.py`):
  - **A halo, not a ring** (`_apply_die_style`). The armed ring and the outcome glow were
    a rounded square with a tinted fill round the die button, and after a failed roll the
    strip showed a red square that read as an error tile. Now the button is drawn bare in
    every state (no border, no fill, hover included) and a `QGraphicsDropShadowEffect` at
    no offset glows the drawing's own silhouette: the outcome at full strength, a loaded
    trait in `accent.dice`, the pointer over an idle die faintly (an event filter on the
    button). Its spread is the `die.halo` token. `halo_token()` says what is lit. The
    effect paints into the `die.padding` chrome and beyond, so it moves nothing.
  - **The result line is the die, the net bonus or penalty, and the total**:
    `19 + 4 = 23` (`roll_working`), `7 − 2 = 5`, `take 10 + 3 = 13`, and just `18` when
    nothing was added. The history cards, the readout beside the die and the GM's
    attack and action cards (`attack_gm.roll_text`) all say it this way. It replaced
    `23 (d20 19 +4)`, which read as dice notation.
  - **Everything else is on hover** (`roll_breakdown`, `breakdown_lines`): the die (or
    the take-10, the kept die of two, a lucky reroll), each part of the loaded trait's
    modifier, then the modifier box as **Circumstance** (as **Modifier** when nothing was
    loaded), the total and how it went against the DC. On the readout and on every
    card's number line, one's own and everyone else's. The parts come from the spec:
    `RollSpec.parts`, below.
  - **`RollSpec.parts`** — what a spec's modifier is made of, `(name, amount)` pairs:
    "Strength" 4, "Athletics ranks" 5, "Impaired" −2, "All-Out Attack" +3. Core builds
    them where it builds the number (the trait rolls; `skill_parts`, `resistance_parts`;
    a power's attack check off `EffectRollNumbers.check_parts`; the conditions overlay's
    new `ConditionEffect.labels`), and whatever *shifts* a spec does it through
    `add_part` (maneuvers one line each, targeted and self actions, a team join, the
    attacker's natural 1, `localize_spec` filling in the target's trait). **`spec_parts`
    always adds up**: a spec whose parts fell behind gets an `OTHER_PART` line, and one
    with none is one line named for itself. On the wire (protocol v20, still unreleased:
    `sanitize_spec` keeps up to `MAX_SPEC_PARTS`), so every seat's hover is the same.
    Left out of equality, as `marks` is.
  - **Justified controls on one label column.** "Modifier" and "DC" share the settings
    grid's first column, so both rows start their controls at the same x, and each row's
    controls (`_stretch_across`, equal stretch) share out the whole width — they used to
    huddle at the left with a slab of nothing beside them. The toolbar is one `QHBoxLayout`
    now, not a wrapping flow: it is no wider than the modifier's row. **The DC capsule**
    can no longer wrap the label and the box in one frame (they are in different
    columns), so `#dcPair` is a frame *behind* them, created first and `lower()`ed, kept
    on their united rectangle by `_place_dc_capsule` from an event filter (move/resize of
    the label, the box, the toolbar). Both labels sit `_capsule_inset()` in from the
    column's edge: the capsule cannot reach past the panel's, and was clipped there in
    the bottom strip.
  - **More air in the block**: the view's margins are `card.margins`, not `space.sm`;
    the labels and the lane's spin box sat against the frame.
  - **Pills share their line** (`FlowLayout.set_justify`; the last line only as much as
    the one above, since the UX pass below: each line's spare width shared
    between its items; widths only, so the wrapping and the height are what they were) —
    which needs the chip's horizontal policy to be `Preferred`, since a layout clamps a
    `Maximum` item to its hint. The caption is centred, and the `×` **floats** over the
    pill's right end on hover (a child of the chip, outside its layout, `_place_remove`)
    rather than keeping its room while hidden, which left every caption off-centre.
  - **Plain rolls have no title.** A private card with nothing loaded was headed `d20` in
    the accent colour, which read as a link; its number line says it was a d20.
  - **The chip just names the roll** (`CHIP_PREFIX` is the 🎲 alone): "Rolling: Blast:
    Attack +7" had two colons, and the halo says it is armed.
  - **↻ Reroll under the result** (`_reroll_button`, `_sync_reroll_button`): shown only
    under a result Reroll can throw again — not under the prompt, an unanswered session
    roll, a tumbling die or a take-10. The Actions popup's tile was the only way to it.
  - **The splitter handle** between the controls and the history is transparent at rest
    and tinted under the pointer: the style drew it as a dark band, a stray separator.
  - **The Actions popup**: sections sharing a row (a shelf) are whole tile columns wide
    (`_section_columns`, the title's width counted) with the tiles' own gap between them,
    so their tiles stand in the columns of a full row and each title sits over its own
    tiles — "Dice" used to sit over the gap a column short of Reroll. And a section whose
    tiles are **all** greyed says once, under its title, the reason most of them share
    (`section_note`, `GridSection.note`): "This character has no attack to make." over
    the maneuvers, Surprise Attack being greyed for a reason of its own. A section with
    a note keeps a row to itself.
- **The play-test pass** (2026-11-10, the user's list after a session; tests in
  `tests/ui/dice/test_roller_feedback.py`):
  - **±1 and ±5 steps** (`circumstanceModifiers: [1, 5]`): 1 is the unit that adds up
    to anything a click at a time.
  - **Or a slider** (Settings ▸ General ▸ Modifier, `storage.modifier_input()`, buttons by
    default): `MODIFIER_SLIDER_MIN..MAX` = −10..+20, lopsided on the user's word. Both
    inputs are always built and the setting shows one (`_apply_modifier_input`), so a
    Save switches it live through `sync_dice_layout` → `sync_modifier_input`. The box
    keeps ±30; a number past the slider's reach parks it at its end.
  - **An unset DC reads "not set", muted** (`DC_NONE`, `_mark_dc`): "none" in the text
    colour read as a value, or a bug. `color` only on the spin box (theme rule 4).
  - **The DC capsule's foot was clipped**: it stands `space.xs` proud of its box and,
    with the lane empty, the toolbar is the settings grid's last row. The grid has that
    much bottom margin now.
  - **The Actions button closes its popup.** Qt replays the press that closes a popup to
    the widget under it, so the click closed the popup and opened it again.
    `ActionGridPopup.mousePressEvent` sets `WA_NoMouseReplay` for a press on its anchor
    only; elsewhere (the die) the press still lands.
  - **A pill never widens the roller**: `_QuickRollButton` hints at most
    `column.quick-roll-max`, its minimum is an ellipsis, and it elides to its width
    (whole caption in the tooltip, `full_text()`).
  - **A pill saved off a maneuvered attack re-arms its lines** (the user's pick over
    "save the plain attack"). `_start_roll` remembers a **recipe** for every roll with
    lines on it — the loaded spec, the `(key, value)` lines, the modifier box — keyed by
    the roll's quick-roll key (`remember_recipe`, the last `RECIPE_MEMORY`). A star that
    saves that roll stores the recipe on the entry (`spec`, `lines`, `circumstance`); a
    label the server cut short (`MAX_LABEL_CHARS`) still finds it by prefix. A click
    (`_apply_recipe`) clears the lane, loads the attack (fresh off `attack_choices` when
    `same_attack` finds it, else as saved), and asks the host to put each line back:
    `set_line_rearm`, which `CombatRunner.rearm` answers (a maneuver at its value, as far
    as its cap allows; a self action's line; never a team line). Then it rolls, so the
    conditions land again. Quick rolls are global settings, so a recipe pill on another
    character rolls the saved attack against that character's Defense.
  - **A maneuver line at 0 rides on nothing** (`ActionLine.idle`, `applies`): greyed, its
    trade line says so (`ZERO_NOTE`), `active_lines` leaves it out (so no All-Out −0
    condition), and it leaves the lane after `ZERO_GRACE_MS` unless raised again. A line
    greyed for want of room (maximum 0) is not idle and stays, as before.
  - **The caps follow the character.** The Dice block subscribes to `build-`, `facts-`,
    `enhancements-`, `derived-` and `condition-changed` → `refresh_limits` →
    `relimit_lines` (still publishing nothing). `_start_roll` re-limits too, which covers
    the GM's roller, whose NPCs are not on a bus. The user's case: Growth moved Defense
    and All-Out's maximum stayed put.
  - **The slider says why its range is short**: `maneuver_limit` now gives a `reason`
    whenever a cap other than `maxShift` binds ("Your Defense is +3, and it cannot go
    below +0."), shown on the slider and box (`cap_reason`).
  - **The no-DC warning is a flag**: `NO_DC_SHORT` ("⚠ No DC") in its own label under
    the result, `NO_DC_WARNING` its tooltip (`warning_text()`).
  - **Natural 20s and 1s** (`roll_history.natural_kind` / `roll_natural`: a graded roll
    trusts `critical`, an ungraded one its die unless routine or a lucky re-roll):
    `dice.critical` / the new `dice.fumble` token for the halo; a burst over the die
    (`ui/roller/flourish.py`'s `DieBurst`: rays and sparks, or a breaking ring of
    shards), the halo pulsing (20) or the die shaking (1); a 20's numeral in gold; a
    "★ NATURAL 20 ★" banner in the readout. A history card gets a gold or crimson edge and
    wash (`mark_natural`), its number line a "★ Nat 20" badge when ungraded (graded, the
    "— Nat 20!" phrase is bold in that colour), and a toast's edge the same colour.
    Crimson & Gold's `dice.critical` was inherited from Classic and nearly matched its
    own `accent.dice`, the armed halo: it is a brighter yellow there now.
- **The UX pass** (2026-11-11, the user took all nine points of a fresh-eyes review;
  tests in `tests/ui/dice/test_roller_preview.py`):
  - **The readout previews the roll** (`staged_preview`): beside an idle die, what a
    click rolls now — `d20 + 1 vs DC 15`, `best of 2d20 + 4` for Aim — bold over the
    muted `IDLE_PROMPT`, the loaded spec with the lane's lines applied (`with_lines`)
    plus the modifier box; its hover is `staged_breakdown`, the rolled card's table
    without a die. Nothing for a plain d20 with nothing set. The chip still names the
    trait at its own number ("Attack +0"); the preview is the only place the +1 that
    the −2 box and All-Out's +3 came to was ever shown before the throw.
  - **When the preview replaces a result**: the player moving the modifier, the DC or
    the lane (`_inputs_edited`, also from `start_action_line` and `_on_lane_changed`)
    trades the result beside the die for the preview — they are setting up the next
    roll, and the result is in the history, the same reasoning `load_spec` already
    used. **Never** for what a landing roll clears itself: `_wind_down` (the shared
    end of `_finish_roll` and `_reveal_session_roll`) holds `_winding_down` around
    `_settle_spec` / `_spend_extras`, which zero the box, drop the lines and empty a
    spec's DC — without it every roll's number vanished the instant it landed. A line's
    value moving (`ActionLane.edited`, which a limit pulled down after a condition also
    fires) only refreshes a preview already showing (`_refresh_preview`).
  - **The modifier and the DC read as fields.** The steps are flat `QToolButton`s
    (`autoRaise`, lit on hover by the theme's tool-button rule), the box takes twice a
    step's share and reads `+0` at rest (`SignedSpinBox.set_zero_text`); five equal
    boxes made the one field look like a sixth button. The DC box is left-aligned like
    every other field in the app and takes twice the share of Take 10 and Actions: a
    centred muted "not set" between two buttons its own size read as a greyed button.
  - **A lone last pill keeps its size** (`FlowLayout`'s justify): the last of several
    lines grows no more than the line above did. Shared out in full, one leftover pill
    spanned the block and read as the main Roll button.
  - **A recipe pill is captioned short** (`recipe_caption`): "Rumbling +15 · Accurate,
    All-Out" from "Rumbling: Attack +15 vs. Defense (Accurate Attack +5, All-Out Attack
    +5)" — elided, the long name lost the part that tells two pills apart. The tooltip
    keeps the whole name; a name not in that shape (a renamed pill) stays whole.
  - **No DC twice** (`label_names_dc`): a save is named "Toughness vs. 18", so the
    readout, the card's number line and the preview leave out their "vs DC 18" when the
    name has it. The hover breakdown still says it.
  - **The "·" before a grade goes with the grade** (`degree_html(lead=…)`): it is inside
    the grade's first `nowrap` span, so a wrap starts the next line "· Failure (2
    degrees)" instead of leaving "21 vs DC 12 ·" with a dot hanging off it.
  - **No ↻ Reroll under a natural 20** (`_shown_natural`, `_sync_reroll_button`). Reroll
    is still on the Actions popup and `Ctrl+Shift+R`.
  - **A card's ☆ and ✕ are one text line tall** (`fit_to_text_line`, padding 0), so
    heading_row's top alignment puts their glyph level with the first line; at a
    button's own height they sat a few pixels off, up or down by platform.
- **Deferral is a window, not a flag, and that was a bug first.** The shared history
  holds one's *own* roll back so the card lands as the die settles rather than the
  instant the server answers. Only the roller's `sessionRollRevealed` lets a held roll
  go, so a roll held while no die is in the air is held for the rest of the session —
  invisible to the one seat that made it, while every other screen shows it. Both
  flags are therefore load-bearing: `set_defer_own` says *there is a roller to wait
  for*, `set_awaiting_own` (driven by the panel's `awaitingOwnRoll`) says *it is
  waiting right now*, and turning the second off **flushes** whatever is still held.
  Two perfectly ordinary things used to strand a roll. A **reconnect** re-sends the
  whole log — `Welcome` → `historyReplaced` → `set_rolls` — so every one of that
  player's own rolls was deferred again at once and their history lost all of them at
  a stroke, which is why it looked like "some players stop seeing their rolls". And a
  roll the panel **gave up on** at `SESSION_ROLL_TIMEOUT_MS` still exists: the server
  rolled it, the record arrives on the history's own feed a moment later, and nothing
  was left to reveal it. Note what is *not* undone on that second path — the readout
  goes on saying nobody answered, because that was true of the tumble; only the card
  comes back. `set_rolls` also re-reads `own_player_id()` before repainting, since
  `attach` runs once per join and a redial that failed to reclaim the old seat returns
  under a new `player_id`.
- **Everyone else's screen waits for the roller's die.** The server answers the
  moment the die is clicked and broadcasts to every seat at once, so the table used to
  read a number ~1.4 s before the player whose die it was. `SessionBridge` therefore
  holds **another seat's** die roll for `PEER_ROLL_DELAY_MS` (1.8 s: the tumble plus a
  beat) before raising `rollAdded`, and holds whatever arrives behind it — a note, a
  removal, a reveal — behind it, so the log keeps its order (`_on_feed`). It is done
  in the bridge, not the history, because the GM window's attack, area and Impress
  flows listen to `rollAdded` too and must not act on a roll before its card shows.
  Not held: one's own roll (the panel's tumble covers it), a `group` roll (the GM
  rolling for several creatures, with no die tumbling anywhere), and a fresh
  `historyReplaced`, which drops whatever is still waiting since it already holds it.
  One's own roll that lands right behind a held peer roll still waits its turn: the
  die just tumbles a fraction longer, which is cheaper than two screens disagreeing
  about the order of the log.
  The bridge's event callbacks run on socket threads, so all four roll signals go
  through the internal `_feed` signal to reach the GUI thread, where the timer lives.
  Tests turn it off (`_peer_rolls_land_at_once` in `tests/ui/conftest.py`).
- **The readout beside the die does not decide the die part's width.** A word-wrapped
  `QLabel` prefers a width worked out from its text, and beside the quick rolls
  (compact) or in a row the die part takes exactly its preferred width — so every roll
  slid the die sideways and re-wrapped the text, worst with a long session label.
  `ReadoutLabel.sizeHint` reports the width of a **template result** (`readout_html`
  with two-digit numbers and a several-degree natural-1 failure), measured in the same
  markup and fonts, so it follows the theme and never the roll.
- **A card is as narrow as its longest word, and widens only when it has room.** Every
  roll button on a card is a `WrappingButton` (`roll_button`), and the small buttons
  (☆, Show, ↶, ✕) sit beside the card's first line
  (`heading_row`), not down its side. There they take width only from the name line,
  which wraps, and every line below gets the card's whole width. Down the side they
  made the card that wide all the way down. "Show" is only as wide as its word
  (`fit_button`), not the platform's default button width. A reflowing side box (row
  when there is room, stacked when not) was tried first. The card's row layout always
  squeezed the box to its stacked minimum, and making that adaptive would have had the
  card's minimum follow the decision, which the standing rules forbid.
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
- **Quick rolls** are pills (`_QuickChip`): the caption is a flat `QToolButton` — a
  push button asks for the platform's default width, which made every pill a slab —
  that rolls on a click and **drags** to reorder once the pointer passes the drag
  distance (it used to need a `⠿` grip), and a `×` floating over the pill's end while
  the pointer is over it (see "The justified pass"; right-click ▸ Remove does the
  same). The whole part is hidden while the strip is empty. A private history card's
  remove button is `✕` like every other card's (a `−` read as "collapse").
- **Quick rolls** are capped at `MAX_QUICK_ROLLS` (6). The cap is a layout constraint,
  not a preference: the strip shares the block with the controls and the die, so an
  unbounded chip list ratchets the block — and the pinned strip holding it — ever
  taller. A history card carries no "Save" button, only a `QuickRollStar`: `☆` muted
  (not saved), `★` washed in `accent.dice` (saved — a click takes it *out* again), or
  `☆` disabled (the strip is full). Three consequences worth knowing. The star is a
  **two-way switch**, so a card reports the click on `saveToggled` and the panel —
  which owns the strip — decides whether that was a save or an unsave
  (`toggle_quick_roll`). **A chip is saved with the name the roll was made under** and
  identity is `quick_roll_key` — `name`/`bonus`/`penalty` — so "Strength +4" and
  "Dodge +4" are two chips and each card's star lights its own. Saving is still one
  click with no dialog: the name is the one the roll already had (`roll_parameters`
  reads it off `label`), and the chip's right-click ▸ Rename… is for changing it
  afterwards. The price of putting the name in the key is that a rename
  *re-identifies* the chip, so the star on the card it came from goes dark — honest,
  since the chip is not that roll any more, and why `_rename_quick_roll` **refuses a
  name that would collide** with another chip (two chips on one key leaves the second
  unreachable by any star). **No DC is saved**, and `_apply_quick_roll` leaves the DC
  box exactly as it finds it: a quick roll is one you make often, the difficulty is
  the situation's rather than the roll's, and a chip that silently re-armed the box
  graded everything after it against a number nobody had chosen. `_load_quick_rolls`
  therefore migrates — it drops a stored `dc` and **de-duplicates**, since two chips
  that differed only by their DC collapse onto one key. And because a card cannot
  reach the panel (`roll_history` is the *lower* module — `dice_roller` imports it,
  never the reverse), the state is **pushed down**: `quickRollsChanged` →
  `set_quick_roll_state(keys, room)`, which both histories remember so a card built
  later starts out agreeing.
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
  computed mid-flight is stale with no further resize coming. It hands the panel the
  row it asks for and **not one pixel past it**: every surplus is the history's, since
  that is the only part of the block that scrolls and length is worth nothing to a row
  of spin boxes. The panel used to take a quarter of the surplus as well, as "a little
  air", which on a wide strip was a slab of empty groupbox. Watch the floor there —
  an empty quick-roll strip asks for 24px against a 170px minimum, so
  `row_natural_width` under-reports and `row_minimum_width + REFLOW_HYSTERESIS` is
  what the clamp usually settles on. That is the honest natural width for this panel,
  not the clamp misfiring.
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
(The chip no longer grows the panel at all: it sits in `_chip_slot`, a `QStackedWidget`
that shows a dashed empty outline, `#specSlot`, with a muted `CHIP_SLOT_HINT` in it,
until something is loaded, and is as tall as the chip either way. `contentChanged` still
fires for the readout beside the die, and for the action lane, which *does* grow.)
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
- **What the transition costs is re-parenting, so it re-parents once.** The freeze
  anyone actually complains about is not the ease, the flags or the window: it is the
  two `addWidget` calls in `CompactPage.adopt` and the `insertWidget` that puts the
  parts back, because Qt re-polishes **every descendant** of a re-parented widget
  against the application stylesheet, and a roll history is where the descendants
  are — ~5 widgets a card, so ~1000 of them at the 200-card cap. Measured on a real
  sheet: a 400-roll history took 978 ms to shrink and 483 ms to grow back; at 800
  rolls, 1.9 s and 0.9 s. Two fixes, and they are independent. `release_roller` now
  **hides** its parts rather than parking them on the view first — that park was a
  second re-parent per part, for a parent nothing was ever laid out under, and it
  was a third of the cost of going in. Hiding is what stands them out of the
  splitter's layout meanwhile, and they never go parentless, so the no-visible-
  orphan rule still holds; the caller's own `adopt` is the one re-parent, which is
  why `adopt` and `release_roller` each carry half of this note.
  `tests/ui/dice/test_compact_mode.py` counts `ParentChange` events and insists on exactly
  one per part per direction — the property, not the arrangement, which the tests
  either side of it already pin.
- **And the private history is capped, at last.** `LocalRollHistory` had no cap at
  all while `RollHistoryPanel` has always trimmed to `MAX_CARDS`, so an evening's
  play grew the list — and every parent change and every width change over it —
  without bound, which is what turned the paragraph above from 300 ms into seconds.
  Both now read the one constant, and the local trim counts **every** card (notes
  and parked requests included, via `_entries`, not just `cards()`), or the same
  leak comes back wearing a different card. The two caps do not mean quite the same
  thing and that is worth knowing: the shared history is a *view* of a log the
  server holds, so its cap only decides how far back you can scroll, while this
  list **is** the log and its cap does lose the oldest rolls. Unbounded was the
  worse of the two. With both fixes a round trip is ~0.6 s whatever the history
  length; the rest of that is the same re-parent, once, and it is bounded now
  rather than growing all evening. Below it the only lever left is fewer live
  cards — a smaller cap, or building only the cards near the viewport.
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
  like. So the preference is set in one place — `DiceRollerView.set_layout` — and
  reaches both halves from there; the panel's own `set_layout_preference` takes the
  layout *string*, not a compact flag. A chosen shape stands the **panel's** reflow
  down (`_compact` or `_column_locked`) via `ReflowBox.force_reflow`, which is guarded
  on the current axis so calling it every resize costs nothing, and `_row_sizes` gives
  a locked panel its **column** width rather than the row-of-three width the auto
  branch measures. Compact still wins over Extended, and while the parts are lent
  neither is in force at all.
- **Extended does not pin the view's axis, and that was a bug.** It used to: the
  preference forced the splitter into a row whatever the width, which meant
  `DiceRollerView.minimumSizeHint` had to report the **row's** width — a chosen shape
  cannot narrow out of itself — turning `ReflowBox`'s "always report the column, you
  can always narrow by reflowing" rule around in the one place it was allowed to be.
  The cost landed in the **pinned side strip**. That strip's thickness is the `dice`
  block's `min_width` of 360, and a roller demanding ~596 held the strip, and through
  it the window, open at nearly twice that; then `_row_sizes` split the result into
  ~306 of controls with a long empty tail under them beside a ~286 history. Half a
  strip spent holding a shape, charged to the one thing in the block that scrolls.
  So the room decides the axis here exactly as it does for an auto roller (`sync_reflow`
  is now just the `_lent` guard, and the `minimumSizeHint` override is gone), and
  Extended keeps what it was actually chosen for: the *panel* stays a column, so a wide
  roller is controls beside a history rather than auto's one row of four, and
  `_shape_locked` is what `_row_sizes` asks. Where a row will not fit the parts stack —
  the arrangement Extended was showing anyway, minus the wasted width.
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
- **And it takes the height the same way too** — `_build_rolls_box` states
  `fills_height` on its `QGroupBox`, which is the one thing `DiceSection` declares that
  a hand-built box does not get for free. Without it `InnerScroll.set_section` reads a
  box layout with nothing expanding in it and gives the block's surplus to a trailing
  stretch, so the block the GM window pins to the strip *by default* — and therefore
  the tallest one in the app — stopped its roller at ~650px and left the rest of the
  strip bare under it. It cannot be derived from the box: a `QGroupBox` is `Preferred`,
  and it is the history *inside* it that wants the room (see [the sheet
  notes](sheet-and-blocks.md)). Where the height lands once it arrives is the panel's
  own column rule: the trailing stretch holds the quick-roll strip at the bottom, so a
  tall roller is controls and die at the top, quick rolls at the foot, and the history
  beside them taking every pixel of it.
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
  test at `tests/ui/dice/test_compact_mode.py` that calls `set_block_on_top(…, False)` first —
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
- **Either direction is all or nothing.** Entering hides the sheet and the menu bar
  before the mini page exists, so a failure part way used to leave a window with
  nothing in it. `enter` runs the shrink (`_shrink`) inside a `try`; a failure is
  logged and `_undo_enter` puts the frame, the size and the full window back. Leaving
  and undoing share `_restore_full_window`, which runs each step on its own
  (`_attempt`) — a roller that will not re-seat is logged and the menu bar and the
  sheet come back regardless. `_shrink` forgets the last visit's geometry and
  maximized flag before it measures, so an undo never restores stale ones. And
  `enter` asks the surface to `end_gestures()` first: a hand still on a divider is
  about to lose the page it was dragging on (see [the sheet notes](sheet-and-blocks.md#the-page-never-stays-broken-matters-when-touching-_relayout-or-any-gesture)).

## Rolling from the sheet (matters when touching the roller or a stat block)

Any stat line on the sheet can be rolled: **click** an ability, resistance, skill or
the Initiative readout to load it into the roller's chip, **double-click** to throw
it; click a line of a power card's dice footer to roll it outright. The same rule as
everywhere applies — *the widget never computes the number*.

One click loading rather than rolling is what makes the modifier and the DC box
usable: you name the trait, then set the situational extras, then throw. A
double-click necessarily fires the single click first, and that is left alone rather
than deferred by the double-click interval — rolling loads the same spec anyway, so
the pair is load → (load + roll) on one spec, and deferring would make a plain click
feel a beat late. A power card's roll line is the deliberate exception: it is an
explicit "roll this" affordance rather than a number being read off the sheet.

**A load is sticky; a roll is not.** That leading single click is exactly why: the
chip used to survive the roll it started, so a double-clicked stat left the roller
*armed* with a trait the player had asked to throw once, and every later click of the
die quietly folded its modifier — and its DC — into a roll nobody had asked for.
`roll_spec` therefore marks what it loads **transient** and drops it again
(`_settle_spec`) at the very end of both paths that produce a number. It has to load
it at all because the roll is made out of it: `_roll_parameters` folds in the modifier
and the DC, `_roll_label` is the name it travels under, and `localRoll` hands the spec
to the history card — hence "at the very end", after the readout and the history have
had their look. Three things fall out of it:
- **`load_spec` cancels transience** (it is set `False` at the top). An explicit load
  is always a deliberate one, so a spec arriving over the bus mid-tumble is not swept
  away when the die settles.
- **`_abandon_roll` does not settle it.** A session roll that never came back is one
  the player will want to throw again; taking the trait away would send them back to
  the sheet to click the stat a second time.
- **A named quick roll lets go of its chip too**, though it does not go through
  `roll_spec`: `_apply_quick_roll` marks its caption transient itself. It used to keep
  it, as the caption for slider values that stayed set — but the sliders are now spent
  by the roll (below), so a chip left standing would be naming numbers that are gone.

**And the modifier is spent by the roll.** It is **one signed box** (−30..+30,
`MODIFIER_LIMIT`; a snug, arrowless `SignedSpinBox`, `+3` / `-3`, between its
circumstance steps) — it used to be two sliders, a Bonus and a Penalty, then one
two-way slider. It is a circumstance of
*one* check — cover against this attack, Extra Effort's "+2 on a single check" — yet a
value left in it used to ride along on every roll after it, unnoticed.
`_spend_extras` zeroes it (and drops the action lines the roll used, below) beside each
`_settle_spec`, i.e. at the end of both paths that produce a number and **not** in
`_abandon_roll`, for the same reason the chip survives there. While it holds anything,
`_mark_extras` tints its label and spin box (`tint.better` above 0, `tint.worse` below)
so a pending charge is as visible as a loaded trait. **Colour only, never bold:** bold
text is wider, so the label and the box grew, and the roller with them, each time the
value left 0 (`test_the_roller_does_not_widen_when_the_bonus_leaves_zero`). The wire and the history
still speak a non-negative bonus/penalty **pair**, split from the net in
`_roll_parameters`; a quick roll's identity (`quick_roll_key`) is read by that net, so a
chip saved as "+4 and −1" before the change is the same chip as the +3 it now loads. Only `color` goes on the spin box — a `border`, `padding` or
`background` there trips rule 4 of the theme notes. Note what this did to tests that
call `_start_roll()` and then `_finish_roll()` under a zero `ROLL_DURATION_MS`: the
first call already finishes the roll, so that pair is *two* rolls, and the second now
has no bonus — call one or the other.

The other half of the same guard is that a loaded chip is **loud** — an `accent.dice`
fill inside an `accent.dice` border (`#specChip`, scoped to the object name, since a
bare rule on a widget reaches the `✕` button inside it), a caption worded as a state
(`🎲 Athletics +9` — it said "Rolling:" until the halo took that over), a matching
halo on the die (`_apply_die_style`, see "The justified pass"; it costs no layout, so
`contentChanged` is not dragged in for a highlight), and the resting prompt beside it. `_showing_result`
is what keeps that prompt from eating a number: `load_spec` writes it whenever a spec
is loaded, but on a *clear* only while the readout is not holding a result — and
clearing is precisely what `_settle_spec` does one line after the roll wrote one there.

The chip is **one line, always**: its caption is an `ElidingLabel` (the whole caption,
and the spec's hint, in the tooltip; the chip's tooltip names the trait in
full) and its vertical policy is `Maximum`. It used to word-wrap, and a wrapped label
asks for its second line only through height-for-width, which nothing above the Roll
box passes on — so a long trait in a narrow strip got one line's height for two and was
drawn squashed, and a tall compact window stretched it the other way.

- `core/rules/rolls.py` is the layer that answers "what does rolling X look like":
  a frozen `RollSpec` (`label`, `modifier`, `dc`, `kind`, `hint`, `follow_up`,
  `outcomes`) plus one builder per trait — `ability_roll`, `resistance_roll`,
  `skill_roll`, `initiative_roll`, `power_rolls`. Each folds in the **displayed**
  (condition-adjusted) number, so what is rolled always matches what the sheet
  shows, while the build math itself stays condition-free. Pure Python — provable
  without a display (`tests/core/test_roll_specs.py`).
- Two things a spec deliberately does *not* know, because this character's sheet
  cannot see them: an attack's **DC** (the target's Defense — `dc` stays `None` and
  the roller's DC box supplies it) and a save's **modifier** (the target's
  resistance — `modifier` is 0 and the modifier box supplies it).
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
- A fourth, `bonus-requested` (an `int`), carries Extra Effort's "+2 bonus on a single
  check" (p21) into the bonus slider through `DiceSection.add_bonus`. It is the one Extra
  Effort benefit that lands on the *next roll* rather than on the build, and nothing
  tracks which roll that will be — so it goes where the player would have typed it, on
  top of whatever is already set, and the next roll spends it (`_spend_extras`, above);
  dragging the slider back is how it is dropped unused. It is **not** quiet, for `load-requested`'s reason: a player who has just paid
  a rung of fatigue for it is about to roll, and putting it into a Dice block they cannot
  see would charge them for something they never got.
- `DiceRollerPanel.roll_spec(spec)` / `load_spec(spec)` are the public way in. A
  trait that was **loaded** is sticky: it shows as a chip above the sliders and
  survives the roll, so the sliders can be nudged and the die thrown again; one that
  arrived through `roll_spec` is let go once the die settles (above). The sliders always
  **add on top** (`net = spec.modifier + bonus − penalty`, split back into a
  non-negative pair for the wire) rather than being overwritten — they are the
  situational extras, and a trait bonus can exceed their ±20 range anyway.
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
  the chip in front of the one person who had no use for it. A roll may provoke **several**
  (`RollSpec.also`, beside `follow_up`; `follow_ups_for_result` adjusts each): a
  Linked power's one attack lands a save per effect, and each gets its own chip.
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

The roller's **Ask to Roll** form — a trait combo, a bare DC spin box, an "Ask"
button — puts a roll in *everyone's* history for somebody else to make. The chain, run
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
  can answer. **No Defense DC** (it resolves to no spec — it is a difficulty), and
  **no powers or equipment** (a pin names a power by an id belonging to one
  character, so there is nothing honest to localize on anyone else's sheet). Labels
  are full names rather than `pin_label`'s chip abbreviations: these are read in a
  combo and in a sentence, where `AWE` is a riddle.
- **It is a popup, not a row** (`RollerRequestsMixin._build_request_form`, a
  `Qt.Popup` `QFrame` child of the panel, built once so it remembers the last trait).
  It used to be a row of the roller, always on screen: three greyed controls on the
  busiest part of the panel for a verb used a few times a session. The Actions popup's
  **Ask** tile (`ACTION_REQUEST`, the panel's own like Reroll, in `combat.menu`'s Dice
  section) opens it with `open_request(at)` — on the next turn, once the Actions popup
  has closed, since two popups open at once fight over which one a click closes.
  `request_reason()` greys the tile (`NO_REQUEST_TRAITS`) when the host gave no traits.
  Ask emits and hides the form.
- **The panel is handed the answer, not the data** — `set_roll_choices(groups)`
  beside `set_localizer`.
  It emits `rollRequested` and neither sends nor rolls: where a request goes is the
  question `post_note` already answers, so `DiceSection.request_roll` is that method
  written twice — session first, private history off the air. The GM window answers
  for itself, because it owns its history and the view's fallback stands down there.
- **The DC box is a plain spin box where 0 means no DC**, shown as `—`
  (`setSpecialValueText`) — the same rule as the roller's own DC box now. It is also
  **arrowless**: the theme reserves ~50px for the arrows the platform style draws.
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

## Roll notifications, and a history that holds more (matters when touching a history card)

A history in the pinned strip shows three or four cards, and the GM's — beside a board
of NPCs — often fewer. Two answers, and they are independent.

- **The cards are denser.** The degree of success rides on the total's line
  (`roll_history.roll_headline`: `21 (d20 12 +9) vs DC 15 · Success (2 degrees)`)
  rather than a line of its own, and `tighten_card` packs every card's margins. Both
  histories build their number line from the one function — `LocalRollHistory`'s
  `RollCard` converts its roll with `roller.history.local_record` first — so the private
  and the shared card cannot drift apart again.
- **Every new entry pops up in a corner of the screen** — but only while the app is
  behind something else (`toasts.app_focused()`; the user's call, 2026-11-10: with the
  app in front the history is on screen anyway). An entry skipped that way is still
  marked seen, so a second history cannot announce it once the focus goes. Tests stand
  in for `app_focused` (the `live_toasts` fixture). (`ui/toasts.py`,
  `RollToaster`): a frameless, always-on-top window that never takes focus, stacking
  away from the corner the user chose, fading after `DWELL_MS`, and held — the whole
  stack, as Telegram Desktop does it — while the pointer is over any of them. Clicking
  one brings forward the window whose history it came from. On by default; on/off and
  the corner are on the General settings page (`storage.roll_notifications()`,
  `storage.roll_notification_corner()`).
- **Only live entries are announced.** `RollHistoryPanel.set_rolls` is a *replay* — a
  join, a reconnect's fresh `Welcome`, the GM's log off disk — and raises `_replaying`
  around its loop, or every reconnect would flood the screen with the evening's rolls.
  `add_roll` (the bridge's `rollAdded`) and `release_roll` (one's own roll, as its die
  settles) are the news. The private history announces from all three of its `add_*`.
- **Deduplicated by `seq`.** One session can feed several histories in one app (the GM
  window and a sheet, two sheets), and each would announce the same roll. Only a
  positive `seq` is a key: a negative one is a window's own off-air counter.
- A toast is built from the record, never from the card (`toast_widgets`): no star,
  no ✕, no follow-up chip — a notification is read at a glance, and the card in the
  history is where those live. The edge is the outcome's colour (`toast_accent`).
- Over the desktop a toast is a **transparent window holding a styled `QFrame`**: a
  translucent top-level paints no stylesheet background of its own, and a `palette()`
  role reads as see-through there, so its colours are resolved to `#rrggbb`
  (`toasts._solid`) before they are written.
- `tests/conftest.py` sets `toasts.SUPPRESSED` for every test, or every roll a test
  makes would pop a real window on the developer's desktop; `tests/ui/dice/test_toasts.py`
  lifts it for its own.

## The Actions button

On the toolbar, after the DC box and Take 10 — **above** the action lane, so it never
moves when a pick puts a line there — the **Actions ▾** button (the old Action row of
push buttons — Reroll, Impress, Area — is gone). It opens the **Actions popup**
(`ui/action_grid.py`, see [the UI utilities notes](ui-utilities.md)): titled sections of
square icon tiles, built from `actions.json`'s `combat` block by
`ui/action_menu.combat_menu`. The panel reads no game data, so the **host builds the
sections**: `DiceRollerPanel.set_action_menu(build)` takes a callable that is handed the
panel's **own** actions (`own_actions()`: Reroll, mapped to `None` or the reason it can't
run) and returns the sections. It is called at every opening, not once, because what is
available moves (a roll comes back, the GM forms an NPC group).

- **What a host offers** is a map of action id → `None` (it runs) or a reason (greyed,
  the reason in the tooltip). An id the host does not mention is greyed with
  `NOT_BUILT` — the actions later phases of the combat maneuvers plan wire up. `gmOnly`
  actions are left off a player's popup entirely.
- The sheet's Dice block (`DiceSection.action_menu`) offers Impress, **Attack** and
  **Area** (each greyed with `NO_ATTACKS` / `NO_AREAS` on a sheet with nothing to use).
  The GM's Rolls block
  (`GMRollsMixin.action_menu`) offers Impress, Area, **Team check / Group check**
  (`GROUP_ACTIONS`), which need a group: greyed with `NO_NPC_GROUPS` until there is one,
  and a pick asks which group off a plain `QMenu` (skipped when there is only one), then
  opens the same window the group card's menu does. **Team attack** is the call to roll
  (`ui/team_call.py`), greyed off the air or with nobody on the Scene; the NPC-group
  window is on the group's ☰ menu only.
- **Reroll and Ask are the panel's own**: picking Reroll calls `reroll()`, Ask opens
  the Ask to Roll form; neither is reported.
  Every other pick goes out on `actionRequested(key)`, which is what the hosts' Impress
  and Area handlers always listened to. `can_reroll` replaces the old button's enabled
  state as the thing a test reads.
- A ruleset with no `combat.menu` falls back to one section of the host's own
  `(key, caption, tooltip)` triples (`IMPRESS_ACTION`, `AREA_ACTION`) plus Reroll,
  and Ask when it is offered.
- Building the popup is wrapped: a bad menu (a mod's data) is logged
  (`log_exception`) and costs the popup, never the panel.
- The button is in `_input_widgets`, so it is disabled while the die tumbles (the old
  Impress button was not; Reroll was).
- The run-mm-companion driver shoots it: `driver.py actions-popup` /
  `actions-popup-gm` (each also writes `<target>-window.png`), and `request-form` for
  the Ask to Roll form.

See [the actions notes](actions.md), which also cover the two `RollSpec` additions the
action brought (`KIND_BEST_OF` and `dc_from_total`).

### The attack picker, and the Attack and Area tiles

- **The picker** is a plain `QMenu` of the character's attacks: `core.rules.
  attack_choices` (every roll line the wielder makes that forces a save —
  `targetable_attack` — off the very lines the power and gear cards draw, so the menu
  and the cards agree; an area power's card has an attack line too, for its primary).
  The panel reads no game data, so the host hands it in: `set_attack_choices(callable)`,
  read at each opening. `choose_attack()` returns the spec picked; it loads nothing.
  Every chooser opened off the popup goes through `action_menu.choose_from_menu`, the
  one `QMenu` a test stands in for (`action_menu.QMenu`), at `panel.menu_point()`:
  **on the tile just picked, over the still-open popup** (`ActionGridPopup.
  follow_up_point`), like a submenu — closing the popup first and opening a small menu
  under the button read as the popup vanishing. Outside a pick, under the button.
  **A picked attack's label is its caption** ("Blast: Attack +8 vs. Defense"): off its
  card, a bare "Attack +8 vs. Defense" on the chip and in the history could be any of
  the character's attacks. A card's own 🎲 keeps the card's label, under the power's
  name already. `same_attack` compares labels, so the same attack loaded once off its
  card and once off the picker counts as two (the lane resets its values).
- **The Attack tile** (`DiceSection.start_attack`): the picker, then — when the Scene
  has someone to aim at — a target menu whose first line is `SKIP_TARGET`. A target
  goes the aimed-attack road (`aim_attack`: declared to the GM, rolled at once).
  Skipping, or nobody to aim at, **loads** the attack onto the chip: the DC box is the
  Defense. An unaimed attack rolled with no DC is flagged `NO_DC_SHORT` under the result,
  `NO_DC_WARNING` on hover (`_unjudged_attack`) — on any attack, a card's 🎲 included —
  and still rolls.
- **The Area tile** (`DiceSection.start_area_tile`): `core.rules.area_choices` (the
  cards' area lines, whose `trait_key` is the area key) — the only one opens at once,
  several are offered on a menu — then `start_area`, as the card's area line does.

### The action lane

Under the toolbar, `ui/roller/action_lane.py`: an `ActionLane` of `ActionLine`s, one per
action riding on the next roll — its name (in the line's `tone`), a `✕`, then a slider
and spin over `0..X_max` (*configurable*) or a fixed caption. The panel's way in is
`start_action_line(key, name, limit=…, fixed=…, needs_attack=…)`; re-picking an action
replaces its line.

- **It takes no room when empty**: the lane hides. It used to hold one line's height
  open (a hidden placeholder line with `retainSizeWhenHidden`) so the first action never
  resized the roller, but the blank row read as a gap, so **every line grows the roller a
  row** (`contentChanged` covers it). The Actions button rode under the last line for a
  while (2026-11-09) and moved away from the pointer with each pick; it is on the toolbar
  above the lane now (2026-11-10). A fixed line is as
  tall as a spin box (measured off one, then discarded), so every kind of line is the
  same height.
- **The limit is the host's**: `limit(spec) -> (maximum, reason)`, the loaded attack in.
  The panel only decides whether the loaded spec *is* an attack (`loaded_attack`).
- **A line that needs an attack** with none loaded opens the picker first and loads
  what is picked (dismissing it calls the action off). `load_spec` re-binds every line:
  no attack → greyed with `NEEDS_ATTACK`, kept in place; a **different** attack →
  the limit is worked out again and the value **reset to the maximum**; the same one
  (`core.rules.same_attack`: the aimed copy of a line counts as the line) keeps what
  the player set, pulled down only if it no longer fits. A maximum of 0 greys the line
  with the limit's reason.
- **Lines limit each other.** A limit may read the other lines (the maneuvers' caps are
  on the combined shifts), so whenever a value moves, or a line comes or goes, the lane
  re-limits the others (`ActionLine.relimit`): ranges follow, and a value past its new
  maximum is **pulled down, never pushed up** — that is what keeps two lines from
  chasing each other. Passes repeat **until one moves no value**: a pull-down does not
  only loosen the others — Accurate pulled down takes away the attack bonus Power Attack
  was spending, so Power must drop too, which can knock on to All-Out. Two fixed passes
  used to stop short of that and could leave the attack bonus below 0 (attack +3,
  Defense 3: Defensive, Power, All-Out 5, re-pick Power, Defense to 1). Values only
  fall, so the loop ends; the last pass leaves every range matching its cap. A line is
  marked available **before** its value is set, since the other lines read only the
  lines that apply — a fresh line still greyed was left out of their limits. The lane's
  `_settling` flag stops a pass from starting another.
  `tests/ui/dice/test_lane_settles.py` fuzzes the four maneuvers on one lane.
- **Spent by the roll**: `_start_roll` notes the lines that applied (`_rolled_lines`,
  `(key, value)` pairs, and `lines` on the roll Reroll throws again); `_spend_extras`
  removes them and raises **`linesRolled`** with them — the host's cue to land what they
  leave behind. A greyed line did not apply and stays; an abandoned roll keeps them and
  raises nothing; a reroll uses the last roll's, leaves the current ones alone and
  raises nothing. `active_lines()` is `(key, value)` for the ones that apply.
- **What the lines do to the roll is the host's**: `set_line_shift(shift)`,
  `shift(spec, lines) -> spec`. `_start_roll` applies it once (`with_lines`) and the
  roll is **made of** that spec (`_flight`, read by `_roll_parameters`, `_roll_label` and
  `_rolled_spec` through `_made_of`); the chip keeps the loaded one, so the lines can be
  set again for the next throw. A failing shift is logged and the roll goes unshifted.
- **`roll_spec(spec, before=…)`**: *before* is handed the spec the roll will be made of
  just before the die goes, and `False` from it calls the roll off (the chip goes back
  to what it held, through `load_spec(..., localize=False)` — the localizer must not
  run twice). It is how an aimed attack declares exactly what it rolls.

### The attack maneuvers (Accurate, All-Out, Defensive, Power)

The sheet's Dice block offers the four tiles whenever it has an attack (`NO_ATTACKS`
otherwise). A tile is `DiceSection.start_maneuver(id)`: a line whose limit is
`core.rules.maneuver_limit` (the other lines' values in), tinted by the tone of the
condition it leaves (All-Out red, Defensive green, the others green). The shift is
`core.rules.apply_maneuvers`: the attack modifier moves (the label is restated:
`Attack +9 vs. Defense (All-Out Attack +3)`), and the effect-rank shift moves every save
it provokes — the DC (and the number in its label), the ladder's `rank` that Impervious
is measured against, and the local-only `RollSpec.effect_rank` the caps read. On
`linesRolled`, `core.rules.lasting_conditions` goes out on the bus's
**`condition-requested`** topic and the Conditions block's `take_conditions` puts each on
**temporary** with its amount (re-applying replaces it: one All-Out a turn). The rules
and the caps are in [the actions notes](actions.md#combat-maneuvers).

### The self actions (Aim, Block, Charge, Defend, Recover, Drop Prone, Stand, Finishing, Catch)

`DiceSection.start_self_action(id)`, read off the record's `CombatEffect` by
`core/rules/self_actions.py` (no action is named in code). An action with `options` asks
which first, on the plain `choose_from_menu` (Charge / Slam / Slam at full speed; Stand by
Acrobatics, by speed rank, or slowly; Finishing with or without intent to kill). Then one
of three things:

- **A `line`**: a *fixed* lane line keyed `action/option` (`line_key`; a Slam replaces a
  plain Charge, since both are the one action). A fixed line takes a `fits(spec) -> reason`
  (`core.rules.line_fit`): Aim on an attack with no attack check, or a Slam on one that is
  not Damage, greys out with the reason, as a maneuver's 0 maximum does. The panel's shift
  is now `core.rules.apply_actions` — the maneuvers and these lines together, named once in
  the label (`… (All-Out Attack +3, Charge)`). Charge moves the modifier; Finishing sets
  `dc = 10` and `critical_hit` (on the wire: `follow_up_for_result` and the GM's
  `judge_attack(critical_hit=)` treat any hit as a critical) and, to kill, rewrites the
  damage ladder from the third failure to Dead; a Slam moves the Damage save to
  `max(rank + 1, speed rank)` (+1 at full speed) and sets **`RollSpec.then`**: the
  slammer's own save at the halved rank (Resistance, p130), filled in for this sheet.
  **`then` and `roll_twice` are local only** (`compare=False`, never in `to_dict`).
- **`roll_twice`** (Aim) is read by the panel (`_twice`, the last roll's on a reroll):
  locally two dice, the higher kept; in a session the request's **`twice`** and the
  server rolls both. The dropped one is `other_die` on the roll (the record's too), shown
  struck through in the readout and the history headline.
- **`then`** is loaded onto the chip once the roll has settled (`_spend_extras`, with
  `localize=False` — it is already this sheet's number); never for a reroll or an
  abandoned roll.
- **Conditions at once** (Defend, Drop Prone, Stand slowly, Recover's +2): a tuple of
  `ConditionChange` on `conditionsRequested` (`condition-requested`), which
  `take_conditions` now takes beside the maneuvers' `(id, amount)` pairs, plus a history
  line (`self_action_note`) through `post_note`.
- **A `check`** (Catch's Agility, Stand's Acrobatics or speed rank) is loaded onto the
  chip at its DC (`check_spec`, `localize=False`). Its `clears` rides as
  `RollSpec.recovers`, so a made Stand check takes Prone off through the ordinary
  recovery road (`settle_recovery` → `recovery-requested` → `apply_recovery`).

Recover also offers what it may shed (`recover_choices`): a recovery check for the most
severe of its `recovery.from` list on the creature that offers one (rolled at once), a
step down the fatigue ladder, or `RECOVER_DEFENSE_ONLY`; with nothing to shed there is no
menu. "Once per scene" is in its tooltip, not enforced. `requires` (any one of a list)
/ `refusedBy` grey Stand (not Prone), Escape (neither Restrained nor Bound) and Drop
Prone (already Prone); `gmRuns` greys a player's Surprise Attack (the GM grants it,
below).

**Block** (phase 9) is a self action with a `reaction`. Taken while not Blocking it does
its own effect: the green, temporary, stacking **Blocking** chip, and a history line.
Taken again while Blocking it is a block (`CombatRunner._react`): the attack picker, then
`core.rules.reaction_spec` — the attack's bare check (`bare_attack_check`: no save, no
marks, so the maneuvers stay off it), the record's `step` (−2) for each block already
made, and **no DC**: the attacker's result goes in the DC box by hand (the user's call —
the GM's judge does not block for anyone). It is **loaded**, not thrown, and the chip's
count goes up by one (`reaction_changes`), so "Blocking ×2" means one block made. The
start of the next turn ("Clear temporary") resets it. Readying needs no attack, but a
block does: while Blocking, a creature with no attack sees the tile greyed with
`NO_ATTACKS` (the GM's chooser greys it too, `_actor_reason` reading `reacting`).

### One runner for both rollers (`ui/roller/combat_runner.py`)

The maneuvers and self actions are not the Dice block's own code: they are
`CombatRunner`, built on a panel with a `note` callable and a parent for its menus. It
wires the panel's attack choices, line shift and `linesRolled` itself. Everything that
depends on **who acts** is a `CombatActor`: the `character` (whose attacks, caps, speed
and conditions are read), a `name` and `key` (`""` on a player's sheet: its rolls carry no
name, as before), `minion`, `land(requests)` (the `condition-requested` payload) and
`throw(spec, load)` (a check the action rolls or loads; `None` = the panel plainly).

- **The sheet** (`DiceSection.combat`) has one actor for good, landing on the bus.
- **The GM** (`ui/gm/combat.py`) changes actor per pick: a tile asks which NPC acts
  (`_choose_actor` — portraits, groups, "(minion)", greyed with the reason when it
  can't; one NPC asks nothing), then `act_as(actor)`. A named actor's specs are `own`ed
  ("Thug: …", `actor="Thug"`), so the history heads "Thug (GM)". `land` goes through
  `GMWindow._land_npc_requests` (the model, then the open sheet's `take_conditions`, so the
  temporary flag survives the replay); `throw` holds a check that sheds a condition
  (Stand's) as the chip's 🎲 does (`_recovering`).
- **A different actor drops the lane's lines** (`act_as`), and so does a roll off another
  NPC's card or sheet (`_follow_actor`): a line's limit reads its actor's numbers.
- **`ensure_attack`**: a loaded attack that is not the actor's own (`spec.actor` differs —
  one loaded off a card) makes the picker ask again.
- **The GM's Attack tile**: NPC, its attack, then the target (`SKIP_TARGET` first; the
  GM's own Scene less the NPC) → `AttackController.aim_own`, which declares the spec the
  die is made of (`area_roll_attack(before=)`), so the lane's maneuvers move the save.
- **Surprise Attack** (`_grant`, the GM's only): anyone on the Scene, then the record's
  options (vulnerable / defenseless, `temporary: true` in data) → an NPC's model, or a
  player's sheet as `ApplyCondition(temporary=True)`; a line in the history.
- **A team attack's call to roll** (`join_team`, see [the actions
  notes](actions.md#a-call-to-roll-the-gms-team-attack-tile)): Join puts a fixed
  `TEAM_LINE` on the lane (the picker first when the actor has no attack loaded) and holds
  the call as `team_call`. `shift` stamps the next attack with `team_join_spec` **before**
  `apply_actions`, so the maneuvers ride on the entry; `land_lasting` forgets the call once
  the line is spent. Joining again replaces the line. A reroll re-throws the stamped spec,
  so it is still the entry; a line left armed does not stamp a reroll of an earlier roll
  (Reroll never reads the lane).

## Reroll

`DiceRollerPanel.reroll` throws **this panel's last roll** again — the bonus, penalty,
DC, hidden flag, label and spec it was *made* with, not what the inputs say now (a roll
spends its sliders, so the inputs have usually moved on). Only a roll that **came back**
counts: `_start_roll` parks it in `_pending_last` and a result commits it, so a roll the
session never answered is not handed back as a free lucky one. The spec is read from the
record (`_rolled_spec`), never re-loaded onto the chip — loading runs the localizer, which
*adds* this sheet's number, so a re-rolled save came back at double its modifier, and it
also unloaded whatever trait the player had picked since.

The die is `core.dice.reroll_d20`: a 1–10 counts 10 more, so it lands on 11–20 — the Luck
re-roll. It returns `(die, natural)`, because only a **natural** 20 is an added success
(the book says so in as many words): a 10 lifted to 20 is graded as 20 but is not a
critical, so `resolve_check` and `RollRecord.from_check` take `natural`. In a session
the **server** rolls it (`RollRequest.reroll`, protocol v12) and stamps `reroll` on the
record itself — a client's label is not evidence, and every seat should see which dice
were lucky. `reroll_label` adds "(reroll)" wherever a roll is named. It spends nothing —
no Hero Point, no Luck rank — which is the player's to mark.

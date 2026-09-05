# The powers layer

Matters when touching powers.

Working notes for MM-Companion, split out of [CLAUDE.md](../../CLAUDE.md).

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
- **Only one effect of an array runs at a time**, which is the reason it is cheaper than
  the same effects bought independently — so the discount and the restriction are two
  halves of one rule and have to be enforced together. `Power.active_effect` is the
  runtime index of the effect in use (`None` = the base, the costliest, the one paid for
  in full), `active_array_effect_index` clamps it the way `effect_current_rank` clamps a
  dialled rank, and `effect_is_selected` gates `effect_is_active` on it — which is the
  one door every standing contribution goes through, so trait bonuses, movement and size
  are all covered by the single check. The card carries a **Using** picker
  (`_EffectSelector`) and fades the effects that are not running. It is the whole-card
  twin, one level down, of the click that selects an array *group's* live member; the
  default differs on purpose (a group falls back to its first child, since a group's
  children are cards the player ordered themselves). **A split pool takes the picker
  away**, the same way it disarms a member card's click one level up: with every Dynamic
  effect running at once the selection decides nothing, and a picker that still moved
  `active_effect` would be a control with nothing visible behind it. **The header stops
  claiming the restriction too** — `structure_header` in `core.rules.powers_terms` reads
  `live_array_effects` and says `Array (Dynamic: 2 effects sharing the pool)` instead of
  `Array (one effect active at a time)`. It lives in `core` because it is a statement
  about the rules and because there were *three* copies of that sentence: the game-term
  summary, the sheet card's effects block and the constructor's terms view. Two of them
  never learned about the split, so a card whose effects were running together went on
  printing the mutual exclusion above the ranks that disproved it.
- **A Dynamic array is the structure switch's fourth answer.** An ordinary alternate is
  mutually exclusive with its siblings and costs 1 point; a **Dynamic** one shares the
  array's point pool and runs *alongside* the array's other Dynamic members at reduced
  effectiveness, and costs 2 for exactly that reason (p101, p151). Making the array's
  **base** Dynamic instead "requires 1 Alternate Effect rank" — 1 point charged on top
  of its own full cost, not instead of it. It used to be a checkbox on each member
  beside an Independent / Linked / Array strip, which asked the same question twice:
  an array and a Dynamic array are two answers to *how do these combine*, not one
  answer and a modifier on it. So both strips carry a fourth segment now
  (`PowerModeBar._MODES`, `_ModeToggle._MODES`) and the checkboxes are gone.
  **The model did not move, only the control**: `MODE_ARRAY_DYNAMIC` is a *view* over
  "the mode is `array` and every member carries `dynamic`", derived in one place per
  level (`_group_mode`, `PowerCanvas._on_structure_changed`) and never stored, because
  a fourth `STRUCTURES` constant would have to be handled at each of the ~50 places
  that ask `== STRUCTURE_ARRAY`. Per-member is also what the rules price, so
  `array_members_cost` — the one place the pooling arithmetic lives, shared by
  `power_gross_cost` and `node_cost` so the two levels cannot drift — is untouched, and
  both numbers stay data (`costValue` / `dynamicCostValue` on the `alternate_effect`
  record). The segment lights when **any** member is Dynamic rather than all of them, so
  a mixed array saved while the flag was per-member reads as what it is instead of as a
  plain array that quietly costs more; nothing is migrated.
- **`dynamic` is a fact about a node's *place*, so a drag has to re-seat it**
  (`_reseat_dynamic`, called from `_on_move`/`_on_combine` through
  `_after_structural_change`). It was the one piece of build state a drag never touched,
  and it went wrong in both directions: a card dropped into a Dynamic array joined as a
  plain 1-point alternate — no share dial, mutually exclusive with siblings that were
  not, mispriced — while the switch above it went on reading *Dynamic array*, and a card
  dragged *out* kept the flag and its share, so dropping it into some other array turned
  that array Dynamic with nobody saying so. A node landing inside a Dynamic array now
  joins it; one landing anywhere else stops claiming to be in one. Its **share** is only
  ever cleared, never invented. Scoped to the node that moved and run after the tidying
  (a collapsing singleton re-parents its child a second time), because the same rule
  applied to every child on every rebuild would migrate exactly the mixed array the
  paragraph above says nothing migrates. `_set_group_mode` clears the pair for the same
  reason on **every** mode that is not a Dynamic array — it used to clear it only on the
  way to a plain array, so a group sent to Linked kept every share, dead in the file and
  instantly live again the moment anyone chose Dynamic array a second time.
- **The pool a Dynamic member shares is `dynamic_points`** — *runtime* state beside the
  build flag, and now on all three things an array's members can be: `Power` and
  `PowerGroup` for a group of whole cards, `PowerEffectInstance` for a power's own
  effects. It is the free action the rules give a character once per turn: the array's
  points are its base member's cost (`array_pool_points`, or `power_pool_points` one
  level down), and each member is held to what its share buys — `rank x points / full
  cost`, rounded down (`dynamic_rank_share`). A Flight 5 costing 10 given 2 points runs
  at 1 rank, which is the book's own worked example. A share too small for even one rank
  floors at **0** — the one place a rank may be zero — and the member is simply off.
  `live_array_children` and its effect-level twin `live_array_effects` are the other
  half: once anything is split, every member holding a share is live *at the same time*
  and the selected alternate stops deciding, which is exactly what the second point of a
  Dynamic alternate buys. With nothing split an array behaves as it always has, so a
  character saved before this loads unchanged.
- **A Dynamic member has exactly one slider, and it spends points.** The share dial
  *replaces* the rank dial rather than sitting beside it (`_rank_is_shared`). Two of them
  is what a Growth in a Dynamic array used to get, and they deadlocked: the rank one wrote
  a `current_rank` the share then clamped away, and because the clamp was a `min` the
  written-and-clamped value survived as a **floor the share could no longer lift**, so
  raising the share afterwards moved nothing. `effect_current_rank` therefore lets the cap
  *replace* the dialled rank rather than taking the smaller of the two — which is also why
  nothing clears `current_rank`: a Growth dialled to Large keeps its rung stored and gets
  it back the moment the split is cleared. The one slider answers to the rank dial's own
  names (`_share_caption`): "Size" on a Growth, "Rank" on a Flight — **and to the rank
  dial's own words**, so a size effect's notches are the sizes it becomes rather than bare
  ranks (`_notch_name`, sharing `_dial_labels` with the rank dial). Moving a Growth into a
  Dynamic array used to swap Large/Huge/Gargantuan for numbers, so the one control the
  player had left said less than the one it replaced. Price first, rung second:
  `5 PP · Huge`.
- **The groove ends where the pool does.** A share slider's right-hand end *is* the most
  that member can be set to, and it gains a division for every point a sibling hands back:
  a Growth 6 holding all six of a six-point pool leaves an Elongation 3 with one notch,
  and dropping the Growth a rung gives the Elongation two. The index space is the member's
  whole ladder and what is affordable is always a **prefix** of it, so notch *n* means the
  same points however far the end has travelled — which is what lets the end move under
  the eye without the handle changing meaning. `_SplitGroup` is what moves it: the dials
  report every notch they pass (`previewed`), and it re-ends every *other* dial and counts
  the header down (`6/10 PP · 4 left`) **while a handle is still moving**, writing nothing
  until the release so a whole gesture stays one undoable step. A member is never left
  without a slider — it used to vanish outright once its siblings had spent the pool, with
  no way to give it points again.
- **One denominator.** `dynamic_member_cost` is the single answer to "what does this member
  cost for the purpose of rationing it", asked by `dynamic_rank_cap`, by the slider's
  notches and by the label beside them. There were two: the cards priced with the wielder
  and the cap priced without, so a notch could promise `6 PP · Flight 3` while the sheet
  ran Flight 2. It is character-free, and not merely as a tie-break — pricing against the
  wielder reaches `effective_ability`, which asks what the powers contribute, which asks
  the cap, and the loop terminates today only because the cap happens to price char-free.
- **A commit made with a button still down is deferred a turn.** Every commit ends in a
  rebuild that deletes the slider making it, and a **groove click** reaches the commit from
  inside `mousePressEvent` — so it tore the widget down while it still held the mouse grab,
  the rest of the gesture went nowhere, and a queued auto-repeat could re-fire against a
  stale reading of the pool. A release has already cleared the button and a keyboard step
  never had one, so those still commit straight through and the dial stays synchronous
  everywhere it was.
- **The split is made on the members' own sliders, not in a dialog.** It was a
  modal grid of spin boxes behind a *Split points* button, and the dialog kept having to
  explain the thing that makes the slider the right control: a share is only ever
  interesting for the rank it buys. So the notches **are** the ranks and the points are
  what each one spends — `dynamic_share_steps` prices every rank with
  `dynamic_share_points` (the exact inverse of `dynamic_rank_share`) and drops the ranks
  no share can reach, since a 6-rank member costing 3 points climbs two rungs for its
  first point and a notch that quietly landed elsewhere would be a control that lies.
  A member costing 2 points a rank therefore moves the split 2 points a notch and one
  costing 1 moves it by 1, so **every stop is a legal price for every member at once**,
  which is the whole reason the steps are computed per member rather than being a plain
  points slider. Each slider is bounded by what is left of the pool once the *others*
  are paid, so the split can be walked up to the pool and never past it and any member
  can always be turned down to free points for another. Sliding every member to nothing
  is still *a* way back to an ordinary array, and stores `None` rather than `0`, so a file
  is byte-for-byte what it was before anyone split anything — but it is not the only one
  any more, because it was one gesture per member for a decision made once and a split
  array's cards are not clickable, so the way *out* of the pool was the one thing the
  array offered no control for. The group header carries a **↺ hand-back button**
  (`_pool_release`/`_release_pool`) that clears every share and puts the selected
  alternate back on through the same `_set_array_active` a card click would have used. It
  is there only while there is a split to hand back, and it stays live in a **locked**
  sheet, like the share dials themselves: it is the same free action.
  The header also keeps the one number no single slider can show (`_pool_readout`,
  worded by `_SplitGroup.readout_text` so the label built before the sliders exist and the
  one restated mid-drag cannot disagree). It is there **before** the first split too —
  `Pool: 8 PP — not split` — which is the moment it is most needed: it used to appear only
  once something had been assigned, so the pool was invisible for exactly the gesture that
  spends it. That line is also where a Dynamic array says which of its two regimes it is
  in — pick one alternate by clicking a card, or move a slider and run several at once.
  A power's **own** effect-level split gets the same pair on its card header
  (`_effect_pool_readout`, `_effect_pool_release`); an array exists at two levels and so
  does its pool, but only the group level used to say so. A Dynamic array's sliders are
  therefore **not optional**: the Extended-settings box that governs them is forced on
  and made read-only while the array is Dynamic, since taking them away would leave the
  split with no control at all.
- **Zero on a share dial switches its member off, and it has to say so out loud.**
  Handing a share back ordinarily drops the member out of `live_array_children` by
  itself — but not the *last* one: with every share back the array falls back to its
  selected alternate **at full rank**, which is the behaviour an array saved before the
  pool existed needs on load. So a Growth parked on "Off" came straight back on, and a
  Diminutive character read Gargantuan under a slider saying the power was off.
  `_on_share_dialled` therefore flips the member's own master switches too, exactly as a
  click on its card would, and a notch above zero flips them back. **Every switch the card
  flips, and only on this member's own leaves** (`_set_member_running`): raising
  `activated` alone was not enough, because a card click puts a member down through
  `_set_power_active`, which clears `activated`, `item_present` *and* every effect's
  `toggled_on` together — so a share dialled up afterwards spent the points and lit the
  card while `effect_is_active` went on reading the member as off, and a Speed parked in a
  Dynamic array by a card click could not be bought back on. Which of the three a given
  power's gates consult is `effect_is_active`'s business; the dial raises the same three
  the card does and lets it choose, and `_member_is_running` asks the same question back
  (`_power_is_active`) so the commit's no-op check cannot disagree with what it wrote.
  Reaching *wider* is still what is avoided — as `_set_power_active` does, that would let
  one share switch off a Linked group the array happens to sit inside.
  The share itself still stores `None`, so the sentence above stays true. Two corollaries:
  the *dimming* asks the same question after the pool's (`_node_is_inactive`), or the one
  member the fallback woke would be the only undimmed card on a switched-off array; and
  a commit that lands where it started now compares the **switch as well as the share**,
  so a member the fallback woke under an "Off" handle can still be put back down by
  clicking that handle where it already sits.
- **And the handle sits where the member is running, share or no share.** The other end
  of the same lie: an array nobody has split still runs its selected alternate, so
  drawing every one of its sliders on "Off" said the array was doing nothing while the
  sheet showed a Diminutive character at Gargantuan. `_fallback_share` prices what such a
  member is *standing* at through `dynamic_share_points` — the exact inverse of the share
  → rank conversion, so the handle lands on the notch that would buy what it is already
  running, full rank landing on the member's whole cost and a member dialled down
  mid-play on what that rung costs. A member holding several effects is priced whole.
  It is a **reading, not a claim**, and three things follow. The array's `_SplitGroup`
  counts such an entry as nothing while its handle sits where it was drawn (a *phantom*
  entry), or the first split of an untouched array would find the pool already eaten by a
  share nobody assigned — and it becomes real the moment the handle moves. A commit
  that lands back on that seat writes nothing, so leaving the handle alone keeps the array
  unsplit; only dragging it somewhere else splits the pool, and dragging it to zero still
  puts the member down. And **that one notch carries no price on its label** — `Gargantuan`
  rather than `10 PP · Gargantuan` — because moving the handle anywhere else spends the
  points that notch names while the seat spends none, and reading the full pool's price
  under a header saying the array is not split invited exactly the wrong conclusion. Every
  other notch is priced as it always was, and the seat gains its price the moment it is
  moved to.
- **A notch is a share *and* a rank, because a share buys a ceiling.** The two are
  different ladders wherever a member does not cost a round number of points a rank: a
  Growth 6 discounted to 5 PP by a Quirk is rationed six ranks to five points, so 4 PP
  buys four ranks, 5 PP buys all six, and *nothing* buys exactly five — a Diminutive
  wielder could be Large or Gargantuan and never Huge. Pricing the notches
  (`dynamic_share_steps`, which dropped the ranks no share reached) made that a rung the
  player simply could not have, and for a size effect that is not a rounding error:
  bigger is easier to hit and impossible to hide, so the rung is the point. So
  `_share_notches` carries **one notch per rank**, priced at the cheapest share that
  reaches it, and the notch remembers the rank it stops at — the two notches that share a
  price (`5 PP · Huge` beside `5 PP · Gargantuan`) differ only by that. A member holding
  *several* effects still gets price notches and no ranks: one share rations them all
  together, so there is no single rank to stand at.
- **The hold is a pair, and that is what keeps it from biting.** `_hold_member` writes the
  notch's rank into the effect's `current_rank` beside the share it spent, and only when
  it is genuinely **below** what those points buy — so a file gains one only for a member
  deliberately held down. `dynamic_held_rank` reads it back only when the stored share is
  exactly what that rank's notch costs, and `_share_cap` lowers the member's ceiling to it.
  That pairing is the whole rule: it tells a deliberate hold apart from a `current_rank`
  left behind by a rank dial the effect had *before* it joined a pool — which would
  otherwise quietly cap a member nobody had touched, the deadlock of two controls writing
  one number arriving by the back door — and it is what tells the two notches sharing a
  price apart, on the card (`_seat`) and on the sheet alike.
- **The cap reaches rank through an injected hook, not an import.** Working a share out
  needs point costs, and `powers_cost` imports `runtime` rather than the other way
  about — so `powers_cost` *installs* `dynamic_rank_cap` into runtime
  (`set_dynamic_rank_cap`) and `effect_current_rank(effect, game_data, char)` asks it.
  Same bargain as the registries: nothing installed, nothing changes. Reading it there
  rather than at each caller is what makes the whole sheet follow — the save DC, the
  Toughness a Dynamic Protection grants, the speed a Dynamic Flight flies at and the
  card's own title are all one number. Without a wielder (the Power Constructor) no cap
  is asked for at all.
- `core.components.py` is an ECS-style split: effect *instances* are entities;
  the frozen **components** describing behaviour are the base effect's parsed
  `Integration` (a `statIntegration` `pattern` — `passive_permanent`,
  `passive_toggle`, `instant_action`, `resource_pool` — plus an optional
  `TraitBoost` for Enhanced-Trait / Protection), and per-instance **gate kinds**
  derived from a flaw's `gate` tag (`activation`, `removable`, `toggle`,
  `limited`). The *systems* reading these — `effect_is_active`,
  `power_trait_bonuses`, `effective_ability`, … — live in `rules`.
- **The data is checked against the book, by page.** The reference is the **4e Origin
  Edition** core rulebook (`reference/core-book/`, gitignored; printed page = PDF page −
  2). Every effect's type/action/range/duration/check/resistance/base cost, every generic
  extra and flaw, and every per-effect extra and flaw has been diffed against it. Two
  things follow. First, **a base cost is not always a constant** — see the next bullet.
  Second, **do not trust the summary grids** on PDF 108 and 234–235: `pdftotext` reads
  them column-wise and every cell but the name lands on the wrong row. Read the effect's
  own entry (they start at p109).
- **Five effects are priced from their configuration, not from a constant.** Illusion
  costs 1 per rank per sense type it fools (sight counts as two, cap 5); Obscure 1 per
  sense or 2 per sense type (sight double, cap 10); Remote Sensing 5 for the first sense
  type and 1 per further one (cap 10, so sight Remote Sensing is 6); Transmute 2–5 by how
  broad its source and result are; Environment adds up its conditions at 1 or 2 each, with
  no ceiling. Each declares a `baseCostBy` block in `effects.json` and puts a `costValue`
  on the options that drive it, so **the numbers are data** — adding a sense type or an
  Environment condition needs no Python. `effect_base_cost_value` is the one place a
  constant base and a configured one are told apart, and the per-rank cost, the flat total
  and the printed formula all go through it, so the card cannot show one number and
  explain another. It is deliberately *not* a `BASE_COST_KINDS` mode: these effects are
  still charged flat per rank, and only the number varies. `baseCostValue` stays as the
  unconfigured floor — **raising it is not how you fix a price here.**
- **Concealment spends ranks instead, and its senses are metered not priced.** It looks
  like the effects above and is not one of them: its cost is a flat 2 per rank whatever it
  hides you from, and the senses are *bought with the ranks* — 1 rank a sense, 2 a whole
  sense type, and sight double (2 for normal sight, 4 for every sight sense, p115). So it
  carries an `allocation` field like Enhanced Senses, not a `baseCostBy` like Obscure, and
  `power_allocation_violations` is what checks a Concealment 5 spent its five ranks
  legally. **Touch is deliberately absent from the option list**: hiding from touch means
  being incorporeal, which is the Insubstantial effect, and the rules say so outright. The
  *Invisibility* and *Inaudibility* configurations seed the field, so they record which
  sense they hide rather than only naming it in their prose.
- **An opposed effect check is its own kind of roll.** "An effect check is just like any
  other check: d20, plus the effect's rank" (p107), and when one effect is used against
  another both sides make one. It is the **wielder's** roll with no DC of its own — the
  opponent's result is the number to beat — which is why it is a game-term field
  (`opposedCheck`) rather than something squeezed into `resistance`. Nullify is the effect
  the rules build on it, and it had been in the resistance slot: that produced a spec
  marked `rolled_by_target` carrying `modifier=0`, so the roll was attributed to the wrong
  side of the table and had no bonus at all.
- **Countering is a tactic, so it lives on the card's context menu, not its dice footer.**
  Ready an effect, spend your reaction when an opponent uses one with an opposing
  descriptor, and make opposed effect checks; winning cancels both (p107). `counter_rolls`
  offers one per effect that could actually be readied — something the character *uses*
  (it attacks or forces a resistance) and usable as a standard action or less. It is
  deliberately **not** in `power_rolls`: putting it there was tried, and it put a die
  button on every attack card *and* every weapon in the Equipment block, and tripled the
  GM pin picker's Equipment list. The footer is what a power *calls for*; a right-click
  menu costs nothing and is where the app already puts a card-adjacent action.
- **An Improvised Effect is a calculator, not a build.** A character with the advantage can
  rig up a power they have not bought and reach it with a skill check, and the whole thing
  hangs off one number: the effect's Power Point cost sets the preparation time (a *time
  rank* equal to the cost, floored at 3 — one minute), the preparation DC and the use DC
  (p101–102). `core/rules/improvised.py` is that arithmetic, `system.json` holds the dials,
  and the panel lives in the **Power Constructor** rather than on a sheet card for the
  reason improvising exists: an improvised effect is one nobody has bought, and the
  constructor is the only place an unbought power is ever held. The cost it reckons from is
  `power_gross_cost`, because the rules put Removable out of bounds here.
- **A card narrows by giving its effect summary up, terms first.** The effect body is
  a `reflow.ShedBox` (`ui/cards/effects.py::effect_body`) holding what a player bought
  — the extras and flaws — beside what it costs them at the table — the game terms.
  They sit side by side because they are read together and a card is a wide, shallow
  thing; a card on a page the user drags is not always wide, and the terms grid
  re-dealing itself into a single column only goes so far. Past that the card used to
  be cut off down its right-hand edge. So: **the terms go first**, because they are the
  same numbers the Power Constructor prints and the card's own roll footer repeats,
  while a rank bought with three extras and a flaw is a fact about the build that
  appears nowhere else; then the modifiers; then it clips. An effect with nothing
  bought onto it has only the terms in the box, so at the very floor it keeps them and
  they wrap — dropping them there would leave the body empty rather than shorter.
  The power's name in the header is an **`ElidingLabel`** for the same reason a skill
  name is: a plain `QLabel` reports its whole text as a minimum, so the card was held
  open at the length of whatever the power was called, and a block narrower than that
  clipped its own buttons off rather than letting the card adapt. Equipment gets all of
  it for nothing, since an item's card is drawn by the same code.
- **The constructor forwards rolls; it does not make them.** It is a window, not a sheet
  block, so it is not on the block bus — `PowerConstructorWindow.rollRequested` is
  connected by the `PowersSection` that opened it, which hands the request on exactly as
  its own cards do. Its roll buttons are plain buttons rather than the sheet's
  `RollsFooter`: `ui.cards` reaches back into `ui.power_constructor` for the terms grid
  *and* sideways into `ui.sections`, so importing it here closes a loop — at module scope
  it fails outright, and deferred it fails for anyone who opens the constructor without
  the sheet.
- **Four things buy a whole sub-build, and each states its budget as a number.** Summon's
  minion is built on `rank x 15` PP (p145), Variable gives `rank x 5` Variable Power
  Points (p148), Affliction's Empowering remakes the target on `rank x 15` (p110), and
  Morph's Metamorph gives one alternate trait set **per rank**, each worth *the wielder's
  own point total* (p136). The first two are `points_per_rank` readouts in
  `effect_readouts.json`; the last two are modifier `noteTemplate` lines. Metamorph is the
  odd one and the reason `noteValues` exists: its budget is not a multiple of any rank, so
  the number has to come from `power_points_spent` — which is why `costs` now sits above
  `powers_terms` in the rules DAG. Two of the four now hold the build as well as the
  budget — see the next bullet. Variable's pool is a *menu* the book itself suggests
  writing down in advance and Empowering's form is built by the GM for a target, so
  neither has an editor — see the gaps at the end of this file.
- **A sub-build is an ordinary `Character`, stored in the config dict that bought it.**
  `core/rules/subbuilds.py` resolves a `subBuild` declaration — on the *effect* for
  Summon's minion, on the *modifier* for Metamorph's forms — into a `SubBuildSlot`
  carrying how many builds this instance buys and what each is built on. The builds live
  in `effect.config[key]` (or the chip's) as a list of `Character.to_dict()` dicts, which
  is why they need no migration, no new save key and no undo work: the sheet snapshots
  the whole model as JSON already, so editing a minion is an undoable step for free. The
  alternative — a reference to a saved NPC file — was rejected because it couples a
  player's power to the GM's directory and dangles when the file moves.
  **The budget is stamped on read, never stored**: dial the Summon from 4 to 6 and the
  minion's point pool moves from 60 to 90 by itself, which is what makes the *sheet's own*
  spent-against-budget readout the check the rules ask for. `SubBuildWindow` is therefore
  the ordinary sheet, not NPC mode — an NPC swaps the pool for an estimated PL, and a
  minion is the one GM-side character the rules do budget. It writes every edit straight
  back into the power (there is no file for it to save to) and shows Power Level and Power
  Points read-only through `SystemInfoSection.set_budget_fixed`, which is sticky against
  `set_locked` because a derived field was never the player's to unlock.
  `power_sub_build_violations` warns — constructor-only, like the imposed-effect and
  Strength checks beside it — about a build over budget, more builds than the power buys
  (a Metamorph dropped in rank keeps its forms rather than silently binning two
  characters), and what a sub-character may not itself have: a minion "cannot have minions
  of their own, either from this effect or the Minions advantage" (p145), which is
  `forbidsEffects` / `forbidsAdvantages` in the data.
- **`noteValues` answers what a rank cannot.** A modifier's `noteTemplate` could only ever
  interpolate `{n}` (the effect's rank times `notePerRank`); a `noteValues` entry names a
  `kind` from the `NOTE_VALUE_KINDS` registry instead. `doubling` is Multiple Minions,
  whose count doubles per rank *of the extra* rather than growing with the effect; and
  `character_points` is Metamorph's. A handler returning `None` — `character_points` with
  no character open — leaves the placeholder to be stripped, so the sentence reads without
  the number rather than claiming a zero. The registry is reached through one door,
  `note_value(spec, ...)`, because a **sub-build's budget asks the registry the same
  question a note's placeholder does** — which is why `per_rank` and `modifier_rank`
  joined it, and why `NoteValueContext.modifier` is optional: Summon's minion is priced
  off the effect's rank with no chip in sight.
- **A modifier's name carries a priced choice.** `modifier_detail` already qualified a
  modifier with the free text a player typed ("Limited (only at night)"); it now also
  names a chosen `select` option **when that option carries its own `costValue`**. Those
  are exactly the modifiers the rules give two prices to (Variable Type is +1 for a
  general type and +2 for a broad one), and without it two cards costing different amounts
  read identically. A select with no prices on it is a neutral choice and stays out of the
  name.
- **An Affliction's Transformed condition can name the effect it imposes.** The rules let
  it impose "a particular Personal Range effect" — Morphing them, Shrinking them,
  Teleporting them away — which "must have a Power Point cost equal to or less than the
  total cost of the Affliction and require a standard action or less to activate" (p110).
  It is a *configuration*, not a nested build and not a cost: the imposed effect is named
  and ranked in the Affliction's own `config` (`imposedEffect` / `imposedRank`) and adds
  nothing to the price. Two of the three conditions are enforced by the picker **offering
  nothing that breaks them** (`imposable_effects`), the same bargain Concealment's missing
  Touch option strikes; the budget is the one that cannot be, since it moves whenever the
  Affliction is edited, so `power_imposed_effect_violations` warns instead.
  **"Personal Range effect" is not the Range parameter.** Teleport's Range reads `Rank`
  because the rank is how far you go, and the book names Teleport as an example anyway —
  so an effect carries `personal` in the data when its Range parameter disagrees, read
  through `effect_is_personal`. Environment's Range is `Rank` too and it is *not* personal
  (it shapes an area around you), which is exactly why this is data and not a rule about
  the string.
- **Two config-field mechanisms came out of that**, both generic and both usable by a mod
  with no Python. `showWhenField` / `showWhenValue` reveal a field only while a *sibling*
  field holds a given value — the imposed-effect picker appears once a degree reads
  Transformed — and naming a field with no value means "while it holds anything", which
  is how the imposed *rank* waits for an effect to actually be picked instead of printing
  "At rank: 1" for a choice nobody had made. `config_field_gate_open` in **core** answers
  it for the card and the game-terms rows alike. The gated widget is **built and hidden**, never omitted, because rebuilding
  the form from inside the very combo whose change triggered it is how Qt teardown bugs
  start; closing a gate drops the stored value and opening it re-seeds the field's default
  (`_config_seeders`), so the spin box and the model never disagree. Separately, a `select`
  field may name a `source` instead of listing options: `CONFIG_OPTION_SOURCES` lives in
  **core**, not beside the constructor's widgets, so the picker and the game-terms readout
  resolve an id to a name through the one call (`config_source_options`) and cannot drift.
- **A field can be gated on a *modifier*, and its options can be widened by one.**
  `shown_with` is the mirror of `hidden_with`: the field is on the card only while that
  extra is attached. `widen_with` + `widen_source` add options to a `select` while the
  named extra is attached. Affliction is what both exist for: the rules give it four
  defenses to be resisted by (p107 — Toughness included, which the field used to omit),
  and it is **Alternate Resistance** (p150) that makes any other resistance, any ability,
  any skill or Damage legal, plus a second one to take the worst or the best of. Offering
  all thirty-odd of those unconditionally would price the extra at nothing. The split is
  between what is **offered** and what **renders**: `config_field_options` narrows the
  picker, while `config_source_options` folds the widened list in unconditionally, so an
  Affliction saved as resisted by Athletics still prints "Athletics" after the extra comes
  off rather than falling back to a bare id. `config_field_shown` is asked by the card
  *and* by `effect_stat_rows`, so a field the card is not showing prints no readout beside
  it — the same contract `config_field_gate_open` has. And **narrowing never silently
  drops a value**: a stored option the field no longer offers is added back to its own
  combo carrying the modifier that would make it legal again
  (`_unoffered_label` — "Athletics (needs Alternate Resistance)"), because "—" in the
  picker beside a row still reading "resisted by Athletics" is the card contradicting
  itself.
- **The book's ~90 named powers are data, not a catalog.** `configurations.json` holds
  the standard power configurations (Blast, Dazzle, Snare, Force Field, Invisibility, …)
  as ready-made assemblies of records that already exist elsewhere, and
  `power_from_configuration` turns one into an **ordinary, fully editable `Power`**.
  There is deliberately no back-reference: the moment the player changes a rank it is no
  longer that configuration, and a stale label would be worse than none. A configuration's
  `costNote` is the cost the *book* prints — reference text shown on the palette brick,
  never used in the arithmetic, so comparing it against what the built power costs is a
  real check on the recorded build (`test_standard_configurations_cost_what_the_book_prints`
  does exactly that; 77 of the 80 machine-checkable ones match to the point, and the three
  that do not are recorded in the file's own `_meta`). Dropping one **appends** to the
  canvas rather than replacing it, and takes its `structure` only when the canvas was
  empty — a Linked configuration must not silently relink a build the player set up.
- **Cost** (`rules`): *how* an effect is priced is data — its `baseCostMode`
  picks a handler out of the `BASE_COST_KINDS` registry in `powers_cost`, and a
  mod registers another rather than editing that module. The default `flat` is
  `ceil` of net per-rank cost × rank (with M&M's sub-1-PP/rank fraction rule)
  plus flat modifiers; `power_total_cost` folds in the structure.
  `effect_cost_formula` renders the human-readable breakdown. All numbers are
  data-driven (`base_cost_value`, modifier `cost_value`, config `cost_value`
  overrides) — never hardcoded.
- **One modifier is priced against the power, not the effect.** Removable "applies to
  the power as a whole and not to individual effects" and is worth its value **per 5
  points of the power's final cost, rounded up** (PDF p161: a 98-point suit of armour is
  98 ÷ 5 = 19.6 → 20, so −20, down to 78). That number cannot exist until every effect
  has been priced, so the record carries `costScope: "power"` (with `costPerPoints: 5`)
  and every effect-level bucket — `_signed_modifier_cost`, `_modifier_terms`,
  `_banded_rank_terms` — skips it. `power_total_cost` then sums the effects
  (`power_gross_cost`), applies `power_scope_adjustment` once, and floors the result at
  1 PP the way a flat flaw is floored. **Only the costliest selection of each such
  modifier counts**: the constructor attaches modifiers to *effects*, so a five-effect
  device naturally ends up carrying five copies of the one flaw, and summing them would
  quintuple a discount the book charges once. Because the discount belongs to no card,
  `power_cost_formula` renders the working (`98 − 20 Removable`) into the constructor's
  total line — otherwise a power costs visibly less than the cards above it for no
  stated reason. Its *Short-Term Only* option takes 1 off the flaw's own value through
  a new `costDelta` on a config option, which unlike the `costValue`/`flat`/`ranked`
  overrides beside it is **summed across every field** rather than first-wins; the sum
  is floored at 0, because the rules say Short-Term Only may leave no discount at all
  rather than turning a flaw into a bonus.
- **`as_trait`**: Enhanced Trait's base cost is "as trait" — it costs whatever the
  traits it raises cost to buy. Each allocated row is priced at its own
  `trait_rate` and the fractions are summed **unrounded**, then rounded once, so a
  rank of Stealth and a rank of Treatment cost 1 point *together* — the same
  pooling `skill_points_spent` gives the bought skills, and the reason
  `trait_rates` was split out of `costs` (which sits *above* `powers_cost` in the
  DAG and so cannot be imported from it). Per-rank modifiers scale that total
  against the effect's *nominal* rate, since there is no one per-rank price to
  subtract from: the typical −1/rank Limited takes 2 to 1 and so halves it. The
  total is floored at 1 PP. `Reduced Trait` is the same rule with a minus sign —
  a flat flaw whose `costMode` is `as_trait` and whose own rows say what was
  lowered. The card's footer groups the priced rows by *kind*
  (`(Abilities 4 + Skills 4) × 1/2`), because a run of raw `1/2 + 1/2 + 1/2` terms says
  nothing about why they came to a point; `effect_cost_breakdown` returns the per-row
  detail behind those subtotals and the card hangs it off the same label as a tooltip,
  so a number and its workings can never come from two places.
- **A modifier may cover only part of an effect.** The rules allow it — a Blast 12
  whose top four ranks alone are Tiring, routinely fired at eight — and a
  `ModifierSelection` carries `applies_from`/`applies_to` for it. `0`/`0` means every
  rank, which is what every selection ever saved says, so nothing migrated; and
  `selection_band` **clamps on read** rather than trusting the stored pair, since the
  effect's rank can be edited down long after the band was set. Cost stops being one
  rate times one rank: `_rank_runs` groups the ranks into runs of equal net per-rank
  cost and `_flat_base_cost` prices each, summing them **unrounded** and rounding once —
  two half-point bands come to a point together, the same rule the "as trait" rows
  follow. A build with no band has exactly one run and the arithmetic is unchanged,
  which is the regression guarantee. Only a **per-rank** modifier gets a band
  (`modifier_is_per_rank`): a flat one is charged once whatever it covers, so a band
  there would be a control that changes no number, and one stored by any other route is
  ignored rather than quietly repricing. Folded Strength ranks sit above the bought ones
  and are in nobody's band, so `_net_per_rank_modifiers` — which is also the divisor
  `ability_rank_contribution` reads — counts only the unbanded selections. `modifier_label`
  is where the band becomes words (`Tiring (ranks 9–12)`), so card, chip and notes agree.
  **The band is edited in Extended settings, not on the chip.** It was two spin boxes and
  an *Only some ranks* checkbox on the chip itself until a multi-effect power made the set
  of them unreadable — a chip is a cramped label, and a band is a statement about the
  build as a whole. The panel lists one line per per-rank selection
  (`_banded_selections` / `_rebuild_rank_bands`), and the checkbox is gone with it: the
  full range `1..rank` is stored as `0`/`0`, so widening the pair all the way is how a
  band is taken back off and an untouched power still serializes byte-for-byte as it did.
  A line carries an **italic subtitle naming its effect** only when the same modifier is
  attached to two of the power's effects, which is the one case two identical rows cannot
  be told apart; a modifier on one effect needs no such line and does not get one. The
  spins are rebuilt whenever the canvas changes — that is what keeps their ceiling on the
  effect's rank now that the chip no longer does (`sync_effect_rank` is gone) — and
  editing one refreshes rather than rebuilds, or the widget under the player's thumb
  would be destroyed mid-edit. Its explanation is a **hover on the caption** (with a ⓘ so
  the hover is findable) rather than standing prose, because a paragraph in the middle of
  a column of short controls pushes the rows the player came for off the screen. And the
  edit reaches the **cards**: `PowerCanvas.refresh_costs` restates each card's own cost
  formula, which the band used to move for free when it lived on a chip and now has to be
  asked for, since a price change that starts in the window reaches nothing on the canvas
  by itself.
- **A power can be held *hard* to Power Level.** A breach was only ever a ⚠. An effect's
  `pl_cap` (`""` / `"effect"` / `"attack"`) makes it bite: `effect_pl_cap_shift` returns
  the `(rank_cut, attack_cut)` that brings `attack + rank` back inside the cap, keeping
  whichever side the player nominated and spilling onto the other whatever a floor
  (rank 1, attack +0) refuses — so the cap always actually holds. It deliberately
  measures against a **flat** `2 × PL` where `power_pl_violations` raises its limit by
  `effect_size_rank_shift`; that difference *is* the feature, since the soft rule lets a
  giant past and a player asking for a hard cap is asking for one that does not.
  `effect_live_rank` is the rank after both the dial and the cut, and `_roll_numbers` is
  the single funnel the DC, the terms table and the dice footer all take it from.
  Validation **skips** a capped effect outright — it is legal by construction, and a ⚠
  about a number the sheet never shows is noise — so the card states what was shaved in
  a `PL cap` row (`pl_cap_note`) instead. The clamp needs an effect's attack bonus, which
  is why `effect_attack_skill_bonus` and `effect_makes_attack` moved down from
  `validation` into `powers_terms`: it is the one layer that reaches both the effective
  rank below it and the modifier impacts it computes itself.
- **Every attacking effect offers "Use attack skill", whether or not there is one to
  pick.** The row is gated on `effect_makes_attack` (a Perception-Range flaw drops the
  roll, so there is nothing to reskill) and on **nothing else**. It used to be built only
  for a wielder who already had a Close/Ranged Combat focus, which made the option read
  as something certain attacks have rather than something this character has not bought
  yet — and a player with no combat focus never saw it at all, with nowhere to learn why.
  With no focus the checkbox is now there and **disabled**, and its tooltip names the one
  thing that would make it live. `combat_focus_options` is unchanged: nothing here writes
  a skill row, because the constructor edits a deep copy of the *power* and the character
  it is handed is the live one — a focus invented on a cancelled edit would survive it.
- **Rank as a dial.** `effect_effective_rank` reads `effect_current_rank`, so an effect
  turned down is turned down everywhere its rank resolves — the save DC, the measures,
  the trait boost. `effect_build_rank` is the bought rank beside it, and validation reads
  *that*: a Power Level cap is a statement about the build, and a sheet that passed its
  check by turning a dial down would be checking nothing. Cost asks neither. A boost
  follows the dial only down the single-target fallback (`boost_allocations(..., live=True)`)
  — an allocation's rows each carry a rank the player wrote down, and there is no honest
  way to turn "3 ranks of Stealth and 1 of Treatment" half off; the effects that carry
  rows are the ones whose rank *is* their allocation, which is exactly where the
  constructor declines to offer a dial. Which effects get one is `effect_has_rank_dial`,
  the **one** door both the card and the constructor's checkbox go through. `rank_dial`
  on the build is **tri-state** for it: `True`/`False` is the player's decision and
  `None` — the default, and what every save written before this says — hands the question
  to the ruleset, which says yes to anything carrying a size readout
  (`effect_dials_by_default`, see [Size and movement](size-and-movement.md)). A size
  effect used to get its ladder *regardless* of the box, which made the checkbox a
  control that changed nothing on exactly the card it mattered most on; making the
  ruleset supply the default rather than an exemption is what fixed that, and it needed
  no migration because an absent key was already the thing that meant "nobody decided".
  One case overrides the player: a **Dynamic** array splits its points on these sliders,
  so the box is forced on and made read-only while it is Dynamic (`_dial_is_forced`).
- **Extended settings is a *power*-level panel over *effect*-level flags.** All three of
  the above, plus `size_scales_damage`, are stored per effect — that is the level they
  apply at, and it is what lets `core` read them without a `Power` in hand — while the
  constructor drives every effect in the power from one checkbox each. Each row hides
  itself when the build has nothing it could apply to (`_resisted_effects`,
  `_dialable_effects`, `_banded_selections`) and the section hides when every row has. An
  effect dropped on the canvas **inherits** the switches rather than its own defaults, or
  turning one off and then adding an effect would quietly turn it back on. The rank-slider
  box is the exception, and only until it is touched: while `_dial_touched` is false it is
  a *readout* of what the ruleset has decided for the effects on the canvas, since
  inheriting there would take a dropped Growth's ladder away.
  The panel's fourth row is the odd one: the **rank bands** are per *modifier selection*
  rather than per effect, so they are a rebuilt list rather than a checkbox — see the
  band bullet below.
- **Effective vs. bought**: `effect_effective_rank` adds an ability a modifier
  folds in (Strength-Based Damage → Strength) to the bought rank — this is the
  rank that sets save DCs and PL caps, while cost counts only the bought rank.
  A power's active `TraitBoost` feeds `effective_ability` / `resistance_total` /
  `skill_total`, so an Enhanced-Trait boost flows through the whole sheet; the
  power pays for it, so the boosted trait's own point cost is unchanged.
- **One boost, many traits.** A `TraitBoost` yields one contribution *per allocated
  trait*, not one per effect: `boost_allocations` reads the effect's trait-allocation
  config field (a `repeatable` with a `trait` column and an `int` column) and returns
  `(trait, ranks)` pairs. When there are no rows it falls back to a single
  `config["target"]` at the effect's full rank — which is exactly how Protection's
  baked-in target, a shield's authored `{"target": "DEF"}` and every character saved
  before the allocation existed keep working. **Nothing is migrated on load**; the
  fallback is the compatibility story, and deleting it would silently blank old sheets.
- **Advantages are traits too.** `CATEGORY_ADVANTAGE` is a trait category an Enhanced
  Trait can raise, but deliberately *not* one of `NUMERIC_CATEGORIES` — an advantage is
  presence-and-rank, not a number on a printed total. `granted_advantages` is what reads
  them, and the Advantages block shows them as muted, unselectable rows naming the
  granting power. A granted advantage is paid for by that power, so it enters neither
  `advantage_points_spent` nor the shared Heroic budget; both read the *bought*
  `char.advantages` and nothing else. If the advantage itself carries a
  `skill_bonus_per_rank`, `advantage_contributions` chains it through to the skill total,
  so an Enhanced Advantage grants what buying the advantage would have granted.
- **A trait key may be *qualified*** to name one row rather than a whole trait, with
  `::` and the character sheet's own row-id shapes: `Expertise::Law` (a focus),
  `Stealth::spec::Urban` (a specialized pool), `Improved Critical::Sword` (an advantage
  bought for a subject). `split_trait_key` / `trait_key_candidates` (in `appliers`, the
  bottom of the DAG, so everything can reach them) are the one place the halves come
  apart, and *every* resolver walks the same whole-then-base order — `trait_category`,
  `trait_rate`, `trait_display_name`. That order is the invariant: which list a target
  lands in, what it costs and how it prints cannot be decided by three different rules.
  Unqualified keys behave exactly as before, which is why nothing needed migrating.
- **One skill rate, both sides.** `skill_row_rate` prices a skill *row* — homebrew
  override, then the specialized rate for a `spec::` pool or a `specialized_cost` skill,
  then the ordinary rate — and both `skill_points_spent` (bought ranks) and `trait_rate`
  (granted ranks) go through it. They were two rules until a power could name a row, and
  two rules would have priced the same pool differently depending on who paid for it.
- **A granted row may not exist yet.** An Enhanced Trait can name a focus the character
  never bought. `granted_skill_rows` finds those orphans and the Skills block grows a
  muted, un-editable row for each (rollable, but with no rank spin and no entry in
  `_row_refs`), the way the Advantages block already shows a granted advantage. Without
  it the bonus is paid for and invisible, which reads as a power that does nothing.
  A **specialized pool** is granted the same way, and the `TraitPicker` has a "+" beside
  its qualifier that *names* one, because the pool a power invents (*Stealth: Rooftops*)
  is by definition not in any list of what the hero has. It writes nothing to
  `Character.specializations` — the power pays for the pool, the constructor often has no
  character in hand at all, and the orphan row above is what makes it visible. The list
  the picker offers therefore always folds in the pool its *current* value names: a
  closed list rebuilt without it would reselect the whole skill and drop a saved power's
  row the first time its cell committed.
- **Rank as allocation, not budget.** An effect declaring `rankFollowsAllocation`
  (Enhanced Trait alone, in the base data) has its rank *written from* its rows —
  `synced_effect_rank` — and shown read-only; `power_allocation_violations` skips it,
  since there is no budget to overspend. Every other allocation effect keeps the hand-set
  rank and the warning. The one per-trait ceiling that does exist is an advantage's, via
  `trait_rank_cap`: the constructor's rank spin stops there and
  `power_trait_allocation_violations` warns about a stored row that went past it. It
  warns rather than clamps — repricing a row the player can see would leave the cost line
  disagreeing with the rows above it.
- **Runtime state** (separate from the point build): `effect.toggled_on` /
  `effect.suppressed` and `power.activated` / `power.item_present` gate whether a
  passive bonus currently applies (`effect_is_active`); `effect_stands` adds the second
  half that question needs on a card — whether the power is **live on the character** at
  all, which an unpicked array alternate is not. The UI drives all of a power's gates
  from one "Active" switch, and `current_rank` from the card's rank dial.
- **Extra Effort is the one thing that pushes a rank *up*.** A hero can strain past what
  they bought (p20-21), and `PowerEffectInstance.extra_effort` is how many ranks are
  currently pushed into an effect — runtime state like `current_rank` beside it, saved,
  and written only when non-zero. `effect_current_rank` adds it **after** every clamp,
  because the benefit "can even increase your ranks or bonuses beyond the normal Power
  Level limits": a hard PL cap and a Dynamic member's share both bound what was *bought*,
  and Extra Effort is by definition not that. Cost never sees it and validation never
  sees it, so a pushed Blast raises no ⚠ — the card carries an `Extra Effort` row saying
  what was pushed and that it lasts until the end of your turn, which is the same
  courtesy `pl_cap_note` does in the other direction. Which effects can be pushed is a
  **duration** question asked of the *resolved* duration (`effect_allows_extra_effort`):
  Permanent refuses it, which is why the Sustained extra and the Permanent flaw exist.
  What it costs is a rung of the fatigue ladder — Fatigued, then Exhausted, then
  Incapacitated — applied to the character through the ordinary condition resolver by
  `spend_extra_effort`, so it bundles and supersedes like any other condition. The uses,
  the ladder, the ranks and the three advantages that bend them are `system.json`'s
  `extra_effort` block. The **power stunt** is the one use that is a whole power rather
  than a number, and it has a bullet of its own below.
- **A power stunt is a card of its own, costs nothing, and is never saved.** The other
  half of Extra Effort (p20, p101): "you can use a temporary Alternate Effect of a
  non-permanent duration effect you have", bought with effort and a Hero Point instead of
  with points, so a hero does not "fill up character sheets with long lists of minor
  alternate effects". `Power.stunt_of` holds the id of the power it came from, and three
  things follow from it. It **costs 0** — `node_cost` returns nothing for a stunt, so it
  never enters `powers_points_spent`, while `power_total_cost` still says what it *would*
  cost, because that is the number its ceiling is measured against: a stunt is an
  alternate effect, and "an alternate effect can have a total cost no greater than the
  base power" (p98). It is **not saved** — `strip_stunts` takes stunt cards out of the
  serialized tree in `library.save_character` (recursively, since a card can be dragged
  into a group) while `to_dict` still writes them, because undo snapshots the model as
  JSON and a stunt that vanished on the next undo would be worse than one that outlived
  its scene. And it is **its own card**, badged `✦ stunt of Fire Blast` with `Stunt` where
  every other card prints its cost, rather than a member of the source power's array:
  the array topology would drag pooling, base-selection and the point split onto a thing
  that costs nothing and lives for a scene. `power_stunt_violations` is the ⚠ — over the
  ceiling, or a source power that has since been deleted, which warns rather than binning
  a build the player made. **The build comes first and the effort is charged on the way
  back**: the constructor opens from the card menu, and only when something is handed back
  does the cost dialog appear — a player who closes the constructor has changed their
  mind, and charging a rung of fatigue for a stunt that does not exist would be the app
  inventing a rule.
- **Extra Effort is spent in two blocks, because it is paid for in two currencies.**
  `ui/extra_effort.py` is the shared menu and dialog (the way `build_condition_menu` is
  shared by the three "+" buttons): the **card's right-click menu** offers the two uses
  that name one of your own effects, since the effect is what you right-clicked, and the
  **System block** offers the four that name nothing, beside the hero points. Both are
  *play*, not build, so both survive the lock. The fatigue is written straight to the
  shared model and the blocks that draw conditions restate themselves off
  `condition-changed` — which is why the Conditions block now **subscribes** to the topic
  it publishes. The hero point cannot be written that way: the pips, the clamp and the
  sentence for the roll history are one funnel in `SystemInfoSection`, so a card that
  shrugs the fatigue off with a Determination heroic feat *asks* for the point through
  the new `hero-point-requested` topic instead. Both blocks also offer the way back, and
  they are deliberately **two** entries rather than one: a push is over at the end of your
  turn, a stunt at the end of the scene, and a single button for both would bin a stunt
  every time a turn ended.
- **Runtime is saved.** It used to be the other way round — every runtime flag was
  left out of `to_dict()` on the argument that what is switched on is not part of the
  build — and the size ladder is what broke it: a Growth 3 *held* at Large is a
  standing decision about the character that four of the sheet's numbers hang off,
  and reopening the file at Gargantuan silently changed them. So
  `activated`/`item_present`/`array_active`, `PowerGroup.active_child_id`, a Dynamic
  member's `dynamic_points` and each effect's `toggled_on`/`suppressed`/`current_rank`
  all round-trip now. Four things
  make that additive rather than a migration. Each is **written only when it differs
  from the all-active default**, so a power nobody has touched serializes
  byte-for-byte as before and a file saved earlier still loads all-active — there is
  no schema version and no reader that needs one. It is still not **cost**: nothing
  here is a term in `power_total_cost`, and dialling a Growth down refunds nothing.
  `capture_runtime`/`apply_runtime` shed the powers half entirely (the snapshot
  carries it), which is what makes a runtime toggle an ordinary undoable step instead
  of a change `restore` had to hold over. And the Powers block's `runtimeChanged`
  gained `EDITED` on the bus: a state that is saved but never marks the sheet dirty
  shows no `*`, prompts nothing on close, and is lost — the exact bug again, one
  layer up. **Equipment's `worn` is deliberately not part of this** (see [The equipment layer](equipment.md)): what is in your hands this round is not what your powers are set
  to.
- **Game-term summary**: `effect_stat_rows` / `effect_game_terms` /
  `power_game_terms` render each effect's Type/Range/Action/Duration/Check/
  Resistance with modifier and config overrides applied, tinting a field an extra
  improved (better) or a flaw limited (worse), resolving check/DC phrases to real
  numbers, and appending measures, configured qualities, trait-boost lines, and
  the Tier-5 `effect_readout_rows`.
- **What a rank *means* is a readout, and the readout is data.** `effect_readouts.json`
  maps an effect id to entries dispatched by `kind` through the `READOUT_KINDS` registry
  (`size_table`, `state`, `measure_offsets`, `thresholds`, `config_flag`,
  `points_per_rank`, `capped_rank_bonus`, `rank_scaled`, `reach_extension`). Several
  effects had none and so said nothing the book says about them: **Regeneration**'s
  recovery interval and its resurrection rung (p139), **Create**'s object volume, hollow
  volume, Toughness and upkeep (p114), **Morph**'s forms (p134), **Quickness**'s time-rank
  cut (p138), **Lifting** and **Move Object**'s effective Strength (p133/p134),
  **Elongation**'s reach bands (p117) and **Teleport**'s carried mass (p144). Two of the
  kinds are new and generic: `rank_scaled` is for a limit that *is* the rank rather than a
  measurement of one (Create's Toughness — printing "2,000 cft." there would be a
  different number entirely), and `reach_extension` reads the effect's own
  `reach` block. `reach_extension` states the **extension**, never the wielder's total:
  `character_reach` already walks the live powers for exactly this effect, so a card
  restating the total would double the power standing on it — and it reads
  `effect_current_rank`, the same dialled rank `reach_extension_feet` reads, so a
  turned-down Elongation shortens on the card and on the block together. (The older
  kinds read the bought `effect.rank`; that predates this and is left alone.)
- **A modifier is taken once unless the ruleset says otherwise.** `repeatable` in
  `modifiers.json` / `effect_modifiers.json` is what `EffectCard.attach_modifier` reads,
  and only six records carry it: **Limited**, **Quirk**, **Feature**, the **Custom** pair,
  and Affliction's **Limited Degree** (the book: "with two applications of this flaw, the
  Affliction does not impose a condition for two of its degrees" — which is how the
  *Transform* configuration is built). Everything else is refused a second copy, because a
  duplicate double-charges the power while overriding nothing. The old test was "does it
  have config fields", on the reasoning that config tells two copies apart; that was always
  too loose — Removable and Check Required both carry config and neither is repeatable —
  and it got looser as the rules audit gave Ranged, Close, Activation, Affects Others and
  the rest their own dials. Repeatability is a *rules* fact, so it belongs in the data.
- **Validation** (warnings, unless a power opted into the hard cap above):
  `power_pl_violations` (per-power attack + effect-rank / auto-hit rank caps, read
  against the wielder's *build* rank),
  `power_allocation_violations` (a Tier-4 effect over-spending its rank pool),
  and `power_linked_range_violations`. Whether a PL breach merely warns or blocks
  the save is the single app-wide seam `core.storage.pl_enforcement()`
  (`"warn"` / `"block"`), so it can become a settings toggle later.
- **UI**: `PowersSection` ("Add Power") launches the standalone
  `ui/power_constructor/window.py::PowerConstructorWindow` — a drag-and-drop
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
  of cutting. **A dial does not recede with it** (`_DraggableCard.keep_lit`): zero on a
  rank or share dial is off and sliding up wakes the power, so it is the one live
  control on a card that is showing itself switched off, and greying it out said the
  only usable thing there was dead. On a Dynamic member parked at "Off" by its own share
  dial it is the *only* way back, since a split array's cards are not clickable. A
  `QGraphicsEffect` paints its whole subtree through one buffer, so a descendant cannot
  opt out of its ancestor's opacity: a card with something to keep lit therefore dims its
  layout's children one at a time instead of dimming itself, and its own frame — the drag
  target and the click target, both live on a receded card — stays lit with them. Only a
  dial that is genuinely live is kept: inside a switched-off Linked group the members'
  dials are transparent to the mouse, nothing is exempted, and the group card's own
  dimming covers them. Every runtime setter ends in `_rebuild_list()` (flipping one power
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
  mid-play action, not a build edit, so it emits `runtimeChanged`, not `changed`
  (which still marks the sheet unwritten, since the state is saved).
---

## Known gaps and caveats

The powers layer was diffed against the core rulebook over fifteen passes, and these are
what that job deliberately **left**. Each is a decision rather than an oversight, and the
reason is the useful half — a later session that does not know them will either trip over
one or "fix" something that is right. (The working document that tracked the audit was
deleted when it finished; the pass-by-pass record is in the `docs/powers-rules-audit` and
`feature/extra-effort-and-power-stunts` branch histories.)

### Costs and configurations

- **Two standard configurations cannot reach their printed cost.** **Material Mimicry** and
  **Power Mimicry** (the book prints 5/rank, the build comes to 6). The book puts the Close
  Range flaw on **Variable**, a *Personal*-range effect, where p159 gives that flaw no value
  — it is priced only from Ranged and from Perception. The book is loose here, not the app.
  Each is built as the book literally names it rather than gaining an invented second
  modifier to force the number, and both are recorded in `configurations.json`'s own
  `_meta.costNote` and excluded by name from
  `test_standard_configurations_cost_what_the_book_prints`.
- **Four configurations arrive as skeletons** the player must finish — Absorption, Berserker
  Rage, Poltergeist, Power Theft. The book leaves them blank too (which trait is boosted,
  which descriptor is absorbed), so this is faithful rather than incomplete. Worth knowing
  before someone "fixes" them.
- **The equipment-currency configurations build as powers, not gear.** Commlink is "1
  Equipment Point per rank" and still drops onto the power canvas like anything else (see
  [the equipment layer](equipment.md) for the currency it ought to be bought in).
- **A power-scope modifier stops at the `Power`**, which is what the rules mean by "the
  power as a whole" — so a device modelled as a `PowerGroup` of several powers gets one
  discount *per child power*, each rounded up on its own. That is the same arithmetic only
  when every split lands on a multiple of 5. `group_scope_note` **states** the difference
  on the group's cost tooltip and never warns about it: three genuinely separate removable
  devices really are charged three times, and nothing can tell that build from one device
  split across three cards. It is silent unless the two numbers actually differ. The honest
  single-device build is one power with many effects, which is how the book's own armour
  example is written.
- **An array's total shows its working** (`array_cost_formula`) — `23 PP (20 base + 1
  Dynamic base + 2 × 1 alternate)` — on the constructor's cost line and, since a card
  header has no room for a second number, on the tooltip of a group's or a card's cost
  (`_explain_cost`). With Removable in play too, the array's working sits in parentheses
  behind the gross the discount is charged against rather than nested inside the
  subtraction, so the number being read stays the one the −20 applies to.
- **The two-price sweep is a test now**, not an instruction to remember
  (`test_a_modifier_naming_two_prices_offers_a_way_to_pick_between_them`). A `costFormula`
  naming two prices with nothing to dial them is silently charged the cheaper one and
  looks fine while it happens; four things count as a dial — priced `select` options, a
  `points` spin box, the modifier's own rank, or being `hidden` (structural, priced by
  the engine).

### Readouts and warnings

- **An allocation names what its tier bought.** It used to print the tier's *index* —
  a Concealment hiding from every sight sense read "Sight 2" beside a card combo reading
  "4 ranks", two numbers meaning different things. The names are game content
  (`tierLabels` on an `allocOptions` entry, beside the `tierNotes` Enhanced Movement
  already used), and Concealment and Enhanced Senses carry them. An option the ruleset
  does not name falls back to the ranks the tier cost, which is still not an index — so
  Comprehend and Enhanced Movement read "Languages (3 ranks)" untouched. The combo on the
  card renders from the same labels, so the two can no longer disagree.
- **A multiselect says when one tick already covers another.** `supersedes` on an option
  names what it makes redundant — a bare value for a sibling in the same field
  (Environment's *Extreme cold* over its *Intense cold*), `"field:value"` across fields
  (Obscure's whole *Sight* type over the single *sight* sense) — and
  `power_redundant_option_violations` warns. It never unticks: the pair is a legal, merely
  wasteful build, and a build that silently edits itself is worse than one that argues.
- **A Variable Environment's redistribution is unchecked** — nothing verifies that what the
  player redistributes at use time stays inside the per-rank total they paid for.
- **Both surfaces walk one check list.** Every per-power check is registered in
  `POWER_CHECKS` (`validation.py`), so the sheet card's ⚠ and the constructor's warning
  band can never disagree about what is wrong: the card lists every sentence behind its
  single glyph, the constructor groups the same ones under a headline naming which checks
  failed. It is a registry rather than a list so a mod's rule reaches both, and because
  `power_sub_build_violations` lives *above* validation in the import DAG and has to
  register itself. The three checks that **block a save** are deliberately not part of it
  — refusing to save is a different question from warning — and `_save_power` asks those
  directly.
- **Nullify's card reads differently than it used to.** Its `resistance` row was replaced by
  an `opposed` one, so a card that read "Resistance: 8 vs. Will or rank" now reads "Opposed:
  8 vs. targeted rank or Will" and gains a rollable footer line where it had an unrollable
  one. Nothing needs migrating — the row is derived — but it is a visible change to an
  existing character's card.

### Sub-builds

- **A sub-build is held to its wielder's Power Level.** The slot already stamped the PL
  onto the build; `power_sub_build_violations` now runs the same `power_level_violations`
  walk the nested sheet does and prefixes each message with the slot's name, so a breach
  no longer needs the minion opened to be found.
- **A sub-build is not counted anywhere outside its power.** A minion's gear, conditions and
  hero points are real fields on a real `Character` that nothing plays with: the GM cannot
  pin it, damage cannot be applied to it, and a session does not surface it as a combatant.
  It is a *build*, not a creature at the table. "Send this minion to the GM window" is the
  obvious next thing to want.
- **A Variable Type Summon holds a menu of minions.** One build is right for the ordinary
  case — p145: "You always summon the same minion unless you apply the Variable Type
  modifier" — and Multiple Minions doubles how many of that *same* creature appear. With
  Variable Type attached the slot becomes a `menu`: as many builds as the player cares to
  make, each on the same budget, none counted against an allowance (the power still
  summons one at a time, so there is no allowance to raise). Which modifiers do that is
  data — `menuWith` on the slot, because it is the *slot* that changes shape.
  **Which one is summoned is not modelled**: a minion is a build, not a creature at the
  table (see the bullet above), so a runtime selection would decide nothing until that
  changes.
- **A modifier's `subBuild` count reads the chip's rank**, so a *repeatable*
  sub-build-bearing modifier would need thought: two copies would produce two independent
  slots sharing one config key and overwrite each other. Nothing is both today, and
  `test_no_modifier_is_both_repeatable_and_buys_a_sub_build` is the tripwire for the day
  something is — the failure would be silent data loss, so it is worth a test of its own
  rather than being left to the reader.

### The Dynamic point pool

- **Leaving part of a pool unassigned is still legal, and the sliders can say so.** The
  old allocator could not — one handle between two members always spread the whole pool,
  which is why the spin boxes stayed authoritative beside it — but a per-member slider
  can simply be left short. Nothing tints it: a wasteful split is a legal build, and the
  group's `4/10 PP split · 6 left` readout states what is spent rather than what is wrong.
  It reads `Pool: 10 PP — not split` before anyone has spread anything, so the pool is
  visible for the gesture that spends it and the array says which regime it is in.
- **A split array's ordinary members go dark.** `live_array_children` gives the pool
  priority: once any Dynamic member holds a share, the array's *non*-Dynamic alternates are
  not running, since they cannot hold a share and the whole pool is spoken for. That is the
  rules-correct reading, but it does mean the way back to an ordinary alternate is not a
  card click. While the pool is split `_activation_role` therefore returns `""` for every
  member — the click used to be armed and moved `active_child_id` with nothing visible
  happening — and the card keeps the tooltip saying why it has stopped being a control,
  which now **points at the ↺ hand-back button** rather than describing a chore ("slide
  them all to nothing"). The dimming outlives the role: `_node_is_inactive` asks the array
  (`_selectable_array_member`), not the card.
- **The split does not follow a rebuild.** A share is an absolute number of points, so
  editing the array moves the pool underneath a split already made. Nothing renormalises: the
  shares stay put and may now sum to less than the pool (legal, just wasteful) or, if the base
  got cheaper, to more than it. Every slider re-bounds itself the moment the cards are rebuilt,
  so the fix is one gesture; a rebuild that quietly rescaled a player's split would be worse.
  An over-spent split is the one thing the header tints (`tint.warning`), and the ↺ button
  beside it is the other one gesture out.
- **A Dynamic member with no share dial keeps its rank dial.** `_rank_is_shared` is
  necessary but not sufficient: `_share_dial` declines a member the pool cannot ration (a
  pool of nothing, a member costing nothing, a ladder with one rung), and standing the
  rank dial down on the flag alone left that card with neither control and no way to turn
  the power up at all. `_make_card` builds the share dial first and tells `_rank_dials`
  what actually happened.
- **`effect_current_rank` can return 0** — only through the pool, and only for a member
  below its minimum. Every reader has been checked against that: none divides by it, the
  size readout returns early on it, and everywhere else it is a summand or a fallback
  where 0 is the honest answer. A *new* reader still owes the same check.

### Extra Effort and power stunts

- **A trait can be pushed as well as an effect.** The rank increase names an effect, "your
  Strength rank for either Damage or Lifting, or your movement Speed rank in one mode of
  movement you have" (p21). The last two are not `PowerEffectInstance`s, so they are stored
  on the character (`Character.extra_effort`, keyed `"category:stat"`) and reach the sheet
  through `effort_contributions`, which joins `trait_contributions` and `_movement_grants`
  in `GROUP_EFFORT` — a group that is *added* rather than weighed, since the benefit
  explicitly goes "beyond the normal Power Level limits". What may be pushed is data
  (`system.json`'s `pushableTraits`); a movement entry names no stat and is expanded into
  the modes **this character has**, since that is a fact about the sheet. The System block
  offers them as a submenu of the rank increase — neither has a card to be offered on —
  and says beside the button what is currently held up. **Strength is applied as the
  ability**, which is broader than "for Damage or Lifting": the app has no separate lifting
  trait to aim at, which is the same simplification the shipped Lifting *effect* makes.
- **The +2 lands in the roller; three uses still change no number.** "A +2 bonus on a single
  check" is raised on the `bonus-requested` topic and added to the Dice block's bonus
  slider — added, never replacing, since a circumstance bonus the player already dialled in
  is theirs. Nothing tracks *which* check it was meant for, so it is the player's to spend
  or drag off, which is the same bargain the pushed ranks strike. An extra action, a
  renewed attempt and a fresh resistance check remain table business: the app records them
  in the roll history and takes the rung.
- **Nothing expires.** Extra Effort lasts "until the end of your turn" and the fatigue
  arrives "at the start of your next turn"; there is no turn tracker, so the push is cleared
  by a button (per power on its card, per character in the System menu) and the fatigue lands
  immediately. The same bargain the Dynamic split strikes, and the same one the whole
  condition tracker strikes — recovery is out of scope there too.
- **Determination's per-adventure uses are not counted.** The dialog offers the advantage
  route and says how many the sheet has; nothing decrements, because nothing knows when an
  adventure ends. A counter with no reset would lie more confidently than no counter.
- **Extraordinary Effort is modelled as the same benefit twice**, not as two different ones.
  The doubled rank increase and the doubled fatigue are exact; taking an extra action *and* a
  rank increase for one doubled cost means using the menu twice, which charges two rungs
  anyway. The arithmetic agrees; only the wording of the second use is missing.
- **A stunt is refused a group.** It costs 0, so inside an array it would be the cheapest
  member by definition — moving the base, the pool and every other member's flat price —
  and it is never saved, so the group would come back a member short. `_groupable` is
  asked by both mutation seams (`_on_combine`/`_on_move`) and by the group `NodeList`'s
  admission rule, so the refusal is *shown* rather than the drop being accepted and
  dropped on the floor. Reordering a stunt at the top level is untouched. `strip_stunts`
  still recurses, since a file written before this can hold one.
- **Nothing stops a stunt and its source power being live at once.** The rules make an
  alternate effect mutually exclusive with what it is an alternate of, and the array
  machinery enforces that for a bought alternate — but a stunt is a card of its own, so no
  live-selection rule reaches it. It matters only when both are *standing* powers, since an
  instant one is used and gone. Wiring it into `live_powers` was weighed and left: a stunt
  card defaults to active the moment it is created, so the source power would go dark with no
  visible reason the moment a stunt was built.
- **Deleting a power leaves its stunts orphaned**, warning on their own cards rather than
  going with it. Deliberate — a stunt is a build the player made, and binning it silently
  because they removed something else is the failure the ⚠ exists to avoid — but the sheet
  can carry a card whose only fault is what is missing.

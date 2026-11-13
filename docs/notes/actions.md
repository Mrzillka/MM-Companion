# Actions against other characters: Impress, area attacks, and the targeting layer

Matters when touching the Impress action, area attacks, `actions.json`, the roller's
Actions popup, the history's table card, or anything that resolves one check against
several creatures.

Working notes for MM-Companion, split out of [CLAUDE.md](../../CLAUDE.md).

## The shape

- **`data/actions.json`** is the whole action as content: the impressions and their
  degree ladders, the interaction skills, what a target resists with, the group
  penalty, and the GM's situational modifiers. Each block's `_meta` documents its
  keys. Paraphrased labels and mechanics only — the modifier table is the book's
  facts in this app's words, never transcribed.
- **`core/rules/targeting.py`** is the generic half: a DC (fixed, or **opposed** —
  another check's total, plus `system.critical_effect_bonus` on a natural 20), one
  resistance per target, and the rung of a ladder each failure lands on, resolved
  against the target's current conditions and applied. The ladders are the same
  `ResistanceOutcome` rungs an effect's `resistanceOutcomes` uses, and the rung
  walking (escalation, apply order) is `damage.resolve_condition_ids`, factored out
  of `resolve_damage_step` so both share it. A rung may name `parameters` (a
  condition id → the parameter it is applied with); `{source}` is whoever imposed
  it, which is how Demoralized's second degree lands as *Frightened of the Thug*.
- **`core/rules/impress.py`** interprets the Impress block: which impressions a
  character may make (an `advantage` gates Dazed, Fascinated, …), their interaction
  skill rows, their resist options, the modifier sum, and the `RollSpec`s. It also
  holds the two small records that travel between seats while an attempt is set up
  (`ImpressAttempt`, `ResistAnswer`), since the session carries them opaquely and
  their meaning is a rules question.
- **`core/dice.resolve_group`** rolls a group rules-free and seedably, so the session
  server (which must not load game data) and a GM off the air resolve it identically.
- **UI:** both actions are built from the same pieces, and look alike for it.
  - `ui/action_ui.py` is what they share: the table's rule (below), the target picker
    a player's dialog takes scene cards into (`TargetsBox`, `TargetDropField`), the
    GM's `ActionWindow` skeleton, the `RollPromptDialog` a player is asked to roll in,
    and `fit_to_screen`.
  - `ui/action_run.py`'s `ActionRun` rolls one action: NPCs as server-side group lines,
    players **asked**, and every roll gathered again (see *Running one*, below).
  - `ui/impress.py` holds the impresser's `ImpressDialog` and the GM's `ImpressWindow`
    and computes nothing; `ui/impress_gm.py`'s `ImpressController` is the only part
    that touches the session, through the `ImpressHost` surface.
  - `session_player.ActionPromptReceiver` is the player's end of every ask.

## Who sets an action up

- **Two settings decide whether a player gets a window at all.** The GM's *Let players
  set up their own Impress and area attacks* (GM Mode page, **on** by default) and the
  player's *Set up my own Impress and area attacks* (General page, **off** by default).
  A player gets the dialog only when both are on (`sets_up_own_actions`).
  - Otherwise the action goes to the GM **bare**: an `ImpressAttempt` with no
    impression (`attempt.bare`), or an `AreaAttempt` with no primary and no targets. The
    GM picks the impression and the skill off the player's snapshot, or the area's
    shape, primary and who is caught.
  - The GM resolves it either way, and the GM's own sheets never get the dialog — the
    GM's window is where they set things up.
  - Off the air there is no GM, so the dialog always opens.
- **The GM may start either action for anybody**, a player included: the roller's
  Impress and Area tiles (its Actions popup — see [the dice notes](dice-and-rolling.md#the-actions-button)), an NPC card's 🗣 and 💥, and a creature's card on the board
  (right-click: **Impress…**, **Area attack…**). Impress's impresser combo lists the cast
  and the players; the area window grows an **Attacker** combo for one the GM started,
  and switching it reads that creature's builds afresh (a player's off their snapshot —
  `start_for` refuses one whose snapshot has not come). A player picked as the
  attacker is asked for their check or attack exactly as one who declared it.
- **The GM's setting is published, not asked for.** It rides the session's
  shared-state channel as the core app's own entry (`TABLE_MOD_ID`
  `"mm-companion.table"`, key `"actions"`), so it is stored, replayed in a joiner's
  `Welcome`, and read the moment a player clicks. The GM window publishes it when the
  session starts or is joined, and the GM page's Save publishes it again. A table that
  never said (an older GM) lets players choose.
- **A player's declaration waits in the GM's history** as an `ActionCard` — **Resolve
  Impress** or **Resolve Area Attack** — rather than opening a window on top of the GM.
  The GM's own (an NPC's card, the roller's Impress, an NPC sheet) opens straight away.

## How one attempt runs

- **The impression and the skill are chosen independently.** Demoralized with
  Deception is allowed: the impression decides what success does, the skill decides
  what is rolled. An impression's `skill` is only the one preselected when it is
  picked. Advantage-gated impressions are *shown greyed* with the advantage in the
  tooltip rather than hidden — a player should be able to see Dazed exists.
- **The GM always sets the situational modifiers**, whoever is impressing, and a
  **adjustment to the impresser's check** of their own (one signed box, a bonus "+2" or
  a penalty "-2" — it was two boxes, which doubled every row). A player's dialog in
  a session therefore carries no modifiers at all; it declares (impression, skill,
  targets off the scene, mode) and the GM's window opens on it, the player's own
  choices picked (and still the GM's to change). Toggles sharing an `exclusive` group (simple vs. full action)
  turn each other off; a range's `StepSpin` stops only on its `step`, so "earlier
  attempts" moves in fives. The panel is **folded by default** behind a header saying
  what the modifiers add up to ("none", or the tinted total), and unfolds itself when
  `set_values` sets one that counts. Each modifier's `label` is a short caption and its
  `hint` the explanation, shown as the control's tooltip.
- **Folding changes the window's height and nothing else.** `ModifiersPanel.sizeHint`
  is as wide as the unfolded grid even while folded, so the window opens at that width;
  a `StepSpin` is arrowless and `column.stat.spin` wide like the Adjust boxes it sits
  among (with the platform's arrows it was what made the grid wide). `_on_fold` calls
  `updateGeometry` before `fit_to_screen` — a parent's cached hint of the panel is only
  dropped by that, so without it a fold left the window at its unfolded height — and
  `fit_to_screen` invalidates the body's layout before activating it for the same
  reason. The bodies end on a stretch (`ActionWindow._body`, the dialogs' offline
  bodies), so a window taller than its content keeps the slack at the bottom instead of
  spreading it between the sections.
- **A declared impression is the player's pick, not a lock.** The GM window opens on the
  impression and skill they declared, picked out of everything their snapshot offers, and
  the GM may change either (the declared skill keeps the number the player's own sheet
  sent with it). Only the impresser is fixed — it is still their action.
- **Several targets is a group impression**: one check, the group penalty once, each
  target resisting separately. Every target also gets an **adjustment to the check
  against them alone** (its row's **Adjust**) — applied as an offset to the DC that
  target resists,
  which is the same arithmetic as adjusting the check and the only way one die can
  stand against several totals.
- **Resisting is "Highest" by default** — the first entry of each target's one
  combo, followed by every option by name. The candidates are the impresser's skill
  (as the skill — a specialized pool resists as its base skill), then `resist.skills`
  and `resist.resistances`. An NPC resists with the GM window's pick. A targeted
  *player* is asked nothing before the roll: once the check is in, they are asked to
  roll their resistance (`impress_ask_resist_spec`, a `KIND_BEST_OF` spec with the DC
  in), and pick another trait in the prompt's own "Highest"-first combo if they want.
  If the GM picked one trait for them, the ask names that trait instead.
- **Two stages, and the check is the impresser's.** *Resolve* rolls the check first:
  the server rolls an NPC's as a lead-only group; a **player** impressing is asked to
  roll it, with every GM adjustment already in its modifier. Its total, plus
  `critical_effect_bonus` on a natural 20, is the DC. Then each NPC target's
  resistance is a line of one group (`dc_offset` = that DC plus the target's own
  adjustment, from a group DC of 0) and each player target is asked. The rung each
  failure lands on is applied as it arrives: straight onto an NPC's model (through
  `_after_npc_condition_change`, the path every NPC condition change takes), or as
  condition commands to a player's app (escalation resolved against their snapshot
  first, so the GM decides it once). A player's own check used to be rolled by the
  server as the group's lead, which they never saw as theirs.
- **Request only** rolls just the check — the server for an NPC, the player when it
  is theirs — whose spec carries the resistance as a `dc_from_total` follow-up: every
  card offers "🎲 Resist Demoralized vs. DC 23", and clicking it rolls the *clicker's*
  best option (`KIND_BEST_OF`, below).
- The window settles in ``done()``, not ``closeEvent``: **Esc rejects a dialog with no
  close event**. During setup that is a cancel; once rolling it only hides (below).
- **The GM's own sheets** (an NPC's, opened from the cast) have the same Impress
  button, and its declaration must not go on the wire: the server forwards nothing
  from the GM's seat to the GM's seat. ``SessionBridge.send_action_request`` raises it
  locally on ``actionRequested`` instead, and the controller takes the impresser to be
  the cast NPC of the sheet's name — a creature nobody plays, rolled as the GM's.
- **A blip is still a session.** The controller asks ``bridge.in_session``, not
  ``joined``: a roll made while the socket is down fails with a notice rather than
  quietly rolling off the air where the table never sees it.
- **Off the air** a player's Impress is request-only by necessity — there is no GM
  and no board — so the dialog grows the modifiers itself and the check is rolled on
  the sheet's own roller. A GM off the air still resolves fully against their NPCs:
  `resolve_group` runs locally and the records are shown as offline cards.

## Running one: stages, asks and the window that watches them

- **`ActionRun`** rolls every stage. A creature nobody plays is a line of a
  server-side group (`roll_group`; off the air the same group is rolled here by
  `resolve_group` and shown as offline cards). A **player is asked** (`ask`): an
  `ActionPrompt` kind `"roll"` pops a `RollPromptDialog` up on their screen, *and* the
  same spec goes in everyone's history as a request card — anyone may answer it, an
  ally interposing, and the first roll back counts.
  - The ask's spec names the row in `targets` and carries the action id, so the roll
    that comes back is found by `(name, is it an attack)` — an area's attack and the
    primary's save name the same row.
  - When it is answered the run closes the prompt (`"roll-closed"`) and strikes the
    history's request card by the `seq` it saw it arrive with.
  - **Roll for the waiting** rolls every ask still out here, as the GM's own line, off
    the player's snapshot — for a player who has gone quiet.
  - `"outcome"` tells a player what the action did to them, as a notice on their sheet.
- **The GM's window stays.** `ActionWindow` has a setup phase and a running one: once
  rolled, its controls lock and each roll lands in its row's **Result** column as it
  comes back, with who is still being waited for underneath. Closing it then only
  hides it; the history card (**Open**, with a live "Waiting for …" status) brings it
  back. **Call off** stops waiting and withdraws every prompt; **Dismiss** throws a
  player's declaration away before it is rolled. The controllers forget an action once
  it is settled and its window closed.
- **Fits the screen.** Every window and dialog body is a `BodyScroll` (which asks for
  all of its content) and `fit_to_screen` caps the window at 85% of the screen, so a
  whole scene of targets scrolls rather than running off the bottom. The two dialogs
  open at least `DIALOG_CHARS` wide, so the area's summary is not wrapped into three
  lines in a dialog sized to its widest combo. **The GM's window opens as wide as its
  table would be with every scene creature in** (`ActionWindow._widest_width`: the rows
  that are out are shown for the measurement and hidden again, inside the first show),
  so putting someone in from the Scene never makes the table wider than the window
  (the user's report, 2026-11-13). And `regrow` refreshes the scrolled body's layout
  before measuring (`refresh_layouts`, shared with `fit_to_screen`): without it the hint
  was from before the row went in, and the window never grew.
- **The windows show numbers; the rules sit behind a "?".** How an Impress or an area
  resolves, what the modes mean, how a creature is put in the table — each is a
  `HelpButton` (the dialogs' and the GM window's by the buttons, the table's in its
  header row, the primary target's beside its attack button), never a paragraph in
  the window. What stays on screen is what changes: the ladder, the DC, the total and
  what it is made of (the group penalty is a part of that line, not a warning of its
  own), and who is still being waited for. The impress check's total sits under the
  scroll (`add_footer`), and hides once rolled, since the check note above the table
  then says what it came to. The heading says only whose declaration it is, and is
  gone for the GM's own (`set_heading`).

- **Creatures are picked off a copy of the scene, never a name list.**
  `ScenePicker` (`ui/action_ui.py`) sits under an action's boxes, in the player's
  dialogs and the GM's windows alike, drawing the board's own `SceneCard`s (portrait,
  name, conditions; the GM hands its board over through `action_scene()`). A card is
  dragged up into a box, right-clicked for the boxes it may go in, or double-clicked
  into the first. Whoever is already in a box is left out of the copy and comes back
  when taken out. The GM's table shows rows **only for who is in**; the primary target
  of an area is a single-chip drop box (`TargetDropField`), and right-clicking a name
  in the table makes it the primary. Dialogs grow as they fill (`regrow`), up to 85%
  of the screen, and never shrink under the user.

## The wire (protocol v14)

- `ActionRequest` (any seat → the GM alone, stamped with the sender, like
  `ModRequest`), `ActionPrompt` (GM → one named player) and `GroupRollRequest` (GM
  only). Payloads go through `sanitize_mod_payload` and are never read by the server.
  Kinds: `impress` and `area` (a declared attempt, whole or bare); prompts `roll` (roll
  this), `roll-closed` (that ask is settled) and `outcome` (what it did to you). The old
  `resist` / `resist-closed` pre-roll question is gone.
- **No protocol bump** for the asks. Nothing new crosses the server, and an older
  client that ignores a `roll` prompt still has the request card in its history,
  whose roll carries the action id home all the same. (v14 is for the spec's `ladder`
  and `actor`: see *Answering a history roll as an NPC*.)
- **An ask's prompt goes without the spec's ladder** (`ActionRun.ask`). An action's
  payload is nested at most four deep and capped at 4 KB by `sanitize_mod_payload`, so
  a ladder would be cut down without a word or sink the prompt. The player does not
  need it, since the GM's table applies what their save did. The request card keeps
  it, for an NPC the GM answers it with.
- **A group roll lands as ordinary records**, a line per creature, each tagged with
  `RollRecord.group` and attributed to the seat its line names (or the GM's, for a
  creature nobody plays). Nothing about drawing a card changed. The run gathers them
  again by group and finishes the stage once it has as many as it asked for — and
  registers that expectation **before** asking, because a hosting server raises each
  record as an event inside the `group_roll` call.
- **Group ids are `"<action id>.<n>"`**, so `table_key` reads the action id back.
- **A lead with no lines is allowed** — that is request mode. A group with lines and
  neither a lead nor a fixed DC is refused.
- **The roller ignores group records** (`_on_session_roll`): a resistance line bearing
  a player's name would otherwise be taken as the answer to whatever die *they* had
  in the air.

## Two RollSpec additions, both general

- **`KIND_BEST_OF`** — a spec whose `trait_key` lists candidates
  (`best_of_key([("skill", "Insight"), ("resistance", "WILL")])`); `localize_spec`
  rolls whichever is highest on the clicker's sheet and names it in the label. Not
  impress-specific, and the reason it lives in `rolls.py` rather than `impress.py`:
  any "whichever is higher" resistance can use it, and the import graph forbids
  `rolls` reaching up into `impress`. **`KIND_WORST_OF`** is its mirror (the lowest
  candidate), for an area resisted by the worst of two.
- **A single-target power's save carries the same choice in its key and `pick`, not
  its kind.** An attack resisted by "Fortitude or Will" (Alternate Resistance's best or
  worst of two) stays a `KIND_POWER_SAVE` — that kind is how a pin and a team attack
  find the save on a power. Its `trait_key` is the `best_of_key`, and the spec's own
  `pick` field says `"best"` or `"worst"` (`save_choice`, `is_choice_key`,
  `rolls_worst`); a choice save with no pick takes the better. `pick` is protocol
  **v19**. Before it, the save named nothing (`_save_key` refuses an "or"), and its
  button on a history card rolled at +0. The GM's aimed-attack window offers the choice itself as the first
  "Resists with" entry.
- **`dc_from_total`** on a follow-up: its DC is the provoking roll's *total*, filled
  in by `follow_up_for_result(…, total=)`. The natural-1 graze rule does not apply to
  one — a 1 on an opposed check is simply a low total.

## Area attacks

The second action on this layer, following the 4e rules (Area Effect extra, PDF p152):

- An area may be aimed at **one creature with an ordinary attack check**. A hit gives
  that creature no Dodge, and it resists at full rank.
- Or it may be aimed at a point, a routine check nobody rolls.
- Everyone else in the area makes **Dodge vs. 10 + area rank**. Making it halves the
  rank they then resist the effect at, rounded up.
- Evasion is +5 to that Dodge, and at rank 2 a made Dodge means no effect at all.

### The pieces

- **Two modules, split by the import DAG.**
  - `core/rules/area.py` sits *below* `powers_terms` and holds the shape, size, Dodge
    DC, the halving and Evasion. It has to sit there because the card's game-terms
    table states an area's size (the **Area** row, beside Range).
  - `core/rules/area_attack.py` holds the resolution half and sits on `rolls` beside
    `impress`. It defines `AreaUse` (what a power offers, worked out on its wielder's
    sheet), `AreaAttempt` (what travels to the GM) and the specs.
- **The data.**
  - `modifiers.json`'s `area_effect` carries a `shape` select, an `applications`
    points field and a `spread` select. The applications value *is* the cost per rank;
    past the first, each one widens the shape a distance rank or doubles a Shapeable
    area's spaces. Emanation is a `costDelta` of +1 on the spread.
  - `actions.json`'s `area` block names the modifiers that make an area, the Dodge
    key, the DC base, the halving, the size phrase per shape, Evasion and the GM's
    cover options.
  - Gear passes a shape through the new `config` on an equipment modifier ref.
- **Power Level.** An area's rank alone is capped at the PL, attack check or not. Three
  places have to agree on that, and at first only the validator did:
  - `power_pl_violations` checks it;
  - `effect_pl_cap_shift` (a hard cap) cuts an area's *rank* to the PL, as for an
    auto-hit effect. It used to trim attack + rank instead, which let a rank 14 area
    through at PL 10;
  - `estimated_power_level` reads an area's rank as its PL.
- The card's **Area** row says "Dodge DC N for half" only for an effect someone
  resists. An area Healing or Environment is just a size.
- **The free area use.** Any attack may be used as an area at the table.
  - The GM need not flip anything: an NPC card's **Area** button offers every build
    `area_capable_builds` finds (bought areas, and any attack), and
    `power_area_use(..., free_use=True)` reads the switch as on.
  - A checkable "Use as an area attack" on the power card's right-click menu sets
    `Power.area_use`. It is saved, runtime-like, undoable and priced at nothing.
  - The area rank is capped at the Power Level (`freeUsePlCap`), **plus the size
    shift** (`effect_size_rank_shift`), exactly as the validator's rank cap is. A huge
    creature's Strength-Based Damage is the book's own reason for the free use, and
    it once got cut back to the bare PL for it.
  - An NPC's cap is its *estimated* PL (`npc=True` on `power_area_use`), never the
    number on file, which nothing reads as a rule for an NPC. The Powers and Equipment
    blocks learn they are on an NPC sheet through `set_npc_mode` and redraw.
  - The book's version is the size rule (PDF p58). Here it is the GM's call, not a
    gate the app checks.
- **The area line.** `power_rolls_with_area` appends a `KIND_AREA` spec to the
  footer for each area the build makes. It is not a die, and it **never travels as one**.
  - **A linked area is one area per effect.** A build whose effects are several areas
    gets a line for each (`power_area_uses`, the effect's name in the label), and each
    runs as an area of its own: its own Dodge against its own rank, its own save. It
    used to be one Dodge against the first effect's rank with the rest as further
    saves; the user asked for two buttons rather than that. An `AreaUse` carries one
    effect and its `effect_index`, and the line's key is `area_key` —
    `"<build id>#<index>"`, the bare id for the first (`find_area_power` strips it).
  - It goes *last*, so a pin's index keeps naming the roll it was made on. The footer
    offers no pin menu on it.
  - Its `trait_key` is the area key, which `find_area_power` looks up. That lookup
    is why `item_effective_build` now keeps the host's id.
  - Clicking it raises `RollsFooter.areaRequested(build id)`, not `rollRequested`. It
    reaches the bus as its own topic, `area-requested`, which the Dice block serves
    with `start_area`. It was a `RollSpec` on `roll-requested` at first, and that took
    a special case in five places. Any server of that topic that missed one (the GM's
    load path, a mod's roller) would have rolled a +0 d20 labelled "Area: …".
  - `area-requested` is one of `OUTWARD_REQUESTS`. On an NPC sheet it goes to the GM
    window, which finds the NPC **by the sheet's file** and calls
    `AreaController.start_for_npc`.

### Declaring

- `AreaDialog` takes scene cards dropped into a primary slot and an area field
  (`TargetDropField`, a `CardDropFlow` of chips). The cards come from the Scene block
  or from the **scene copy** under the boxes (see below).
- **It is modeless** (`show()`, not `exec()`). The cards are dragged off the Scene
  block on the same window, and a modal dialog swallows the press that starts the drag.
- A player's scene cards are draggable now; only the GM's board takes the drop back.
- A **mode** row (**GM resolves** / **Request only**) rides on `AreaAttempt.mode`, and
  the GM's window opens on it. An area Healing has neither: no primary box and no mode.
- At a table, sending it does three things:
  1. sends the `AreaAttempt` (`ActionRequest` kind `"area"`);
  2. posts a note naming who is caught;
  3. rolls the attack against the primary as an ordinary roll. That roll carries the
     attempt's `action_id` and **no follow-up**, because the primary's save is the GM's.
- **The attempt is flat on the wire.** The server runs every `ActionRequest` payload
  through `sanitize_mod_payload`, which keeps four levels of nesting. Under a `"use"`
  key an effect's config sat at level four and everything in it was dropped. An Area
  Affliction then reached the GM with no conditions on its ladder, and every NPC that
  failed its save was reported as having resisted. So the use's keys sit beside the
  attempt's own, and an effect's config carries only plain values (no list or dict,
  and no ladder reads one).
  - The GM's own seat runs the same sanitizer in `SessionBridge.send_action_request`,
    so a self-looped declaration arrives in the shape a player's does. The bug lived
    only on the wire, and that is why no test saw it.
  - `start_area` refuses an attempt the sanitizer would change (too large) rather than
    send one the GM would resolve differently.

### The GM's side

- **NPC cards carry the actions.** 🗣 (Impress) and 💥 (Area, shown only for a creature
  with an attack) sit in the card's header so a collapsed card keeps them, and both are
  on the right-click menu. Area goes straight to the window for one attack, or offers
  a menu of them. The window then lets the GM switch the **power** (a player's too,
  worked out again off their snapshot).
- `AreaController` puts an `ActionCard` with **Resolve Area Attack** in the GM's
  history. The card is GM-only and not a record, and it is the only way back to the
  attack. So while it is `pending`, `RollHistoryPanel.clear()` keeps it through a
  replay (a reconnect's `Welcome`) and puts it back on top, and `_trim` never drops
  it.
- It pairs the attack roll up with the declaration by `action_id`, through
  `take_roll`. An attack that arrives before its declaration is kept until the
  declaration lands. Off the air an NPC's attack from the window's 🎲 raises no
  `rollAdded`, so the GM window's `_show_offline_roll` hands it to `take_roll` too.
- **Namesakes are numbered.** `impress_parties` calls two NPCs on the board that share
  a name `Thug (1)` and `Thug (2)` (players keep theirs, since their app finds a
  request by its name). The table card's rows, the notices and a request's targets
  are all keyed by display name. Without the numbers, two Thugs share one row and the
  last roll stands for both.
- `AreaWindow` lets the GM edit:
  - the attacker (one the GM started), the **power** — every area of every build the
    attacker's sheet offers, a player's off their snapshot. **No shape**: neither
    window asks for one, since which shape an area is changes nothing the table
    resolves (the user dropped it, 2026-10-17); a free use takes the default and a
    bought one its extra's;
  - an **Adjust** to the area's **rank** (`area_use_adjusted`: the Dodge DC and every
    save DC move with it) and one to the **attack** check. The attack's is added when
    Hit is judged, never to the spec asked for, so an attack the player rolled at
    declaration counts it the same way as one asked for later;
  - who is in the area. A dropped card ticks a row, and a roster card's `kind:source`
    ref works too. The attacker's own row is shown but cannot be ticked.
  - the primary, and whether it was hit. "Hit" is pre-ticked from the attack's total
    against the primary's Defense class.
  - per creature, a cover combo (Dodge only) and an Adjust (both of that creature's
    checks; `AreaTarget.adjust`).
- An NPC attacker gets a 🎲 Roll attack button. A player's gets **🎲 Ask for the
  attack**, which asks them for it like any other roll — how a bare declaration's
  attack gets rolled once the GM has picked a primary. Resolving does not wait on it:
  the GM may tick Hit by hand.
- A player's fresh snapshot reaches open area windows as it does Impress ones
  (`AreaController.windows`). If the primary's Defense changed, the Hit tick is
  re-read, unless the GM set Hit by hand.
- **A rerolled attack is followed until the GM resolves.** A reroll travels with the
  same spec, so it reaches `_take_attack` like the first roll; the window replaces the
  attack, says "(rerolled)" and judges Hit afresh. Once the area is resolved or
  requested, an attack that comes in changes nothing (`declared.resolving` is set).

### Resolving

- **Only the creatures nobody plays are rolled**, in two stages. Each stage is a group
  with **no lead**: every line's whole DC rides as its `dc_offset` from a `dc=0`.
  1. Stage 1 is the Dodge checks, group `"<table>.1"`.
  2. Once they are all back, stage 2 is each creature's save at the rank its Dodge
     left (`area_stage_rank`), group `"<table>.2"`.
- A stage with more than `MAX_GROUP_LINES` lines is split over several groups, and all
  of them are gathered before it moves on.
- Each failed save's rung goes onto the NPC through `impress_apply`.
- **The numbers are core's.** `area_npc_dodge_spec` and `area_npc_save_specs` build each
  NPC's line with its own modifier and DC in, and the controller only turns them into
  group lines. Every save DC comes from `area_save_dc`. Its base is `AreaEffect.dc_base`
  as the wielder's card worked it out (a typed DC override included), so the GM's
  table and a player's request cannot disagree.
- **A choice of resistances.** A card resisted by two traits (Alternate Resistance's
  best or worst of two, Illusion's Insight or Perception), or by a skill or an ability
  in a resistance's place, is carried as `resistance_kind` `KIND_BEST_OF` or
  `KIND_WORST_OF` with a `best_of_key`. Before this, those NPC saves were rolled at
  +0. Which modifier choice means "the worse" is data, `area.worstOf`. A player's
  request uses the same kind, so `localize_spec` fills in their pick.
- **Players are asked, not rolled for — one roll at a time.** Each caught player is
  asked for their Dodge alone (`area_ask_dodge_spec`: no save chained to it). When it
  comes back the controller works out the rank it left (`area_stage_rank`, Evasion
  read off their snapshot) and asks for that save (`area_ask_save_spec`, its DC from
  `area_save_dc`) — one ask per effect, in turn. A hit primary player is asked for the
  full-rank save straight away.
  - `localize_spec` fills in the player's own Dodge and Evasion, or their save.
  - The GM's cover and adjustment ride as the spec's modifier, which the player's own
    number is added to. Cover counts on the Dodge only, the adjustment on the Dodge and
    the save, and the label names both.
  - **The GM applies what the save did**, as for an NPC — condition commands to the
    player's app — and tells them with an `"outcome"`. Players used to apply their own
    off a chained request; off the air they still answer the two request cards.
- **A heavy weapon's two ranks.** An area bought on part of an effect — a rank band on
  the area selection, which an equipment modifier ref gives with `appliesTo` (the Rocket
  Launcher's Area on 7 of its 10 Damage ranks) — is an area of that many ranks
  (`area_band_ranks`, read by `effect_area_rank`, so the card's Area row agrees). The
  creature the attack hits directly resists the whole effect: `AreaEffect.primary_rank`,
  read through `area_primary_rank` by both the NPC saves and the player's ask.
- **No crits on the area half.** An area's Dodge and save follow-ups take no natural-20
  bump and no natural-1 graze, because area effects make no attack check (PDF p195).
  The primary's attack is an ordinary attack and keeps its own.

### Request only

- The GM's **Request** rolls nothing more: `_request` leaves the history two cards per
  effect (`area_request_specs`) — the **Dodge**, for everyone caught but a primary the
  attack hit, and the **save**, offered at full rank and at half through
  `dc_if_resisted`, which `request_widgets` draws as two buttons. A hit primary shares
  the save card (its button the full one), or gets a full-rank card alone when nobody
  else is caught or it takes a different rank. Creatures the GM set the same cover and
  adjustment for share their cards. Off the air the dialog leaves the same two.
- Each card carries the action id, so the answers fill the action's table.
- The GM answers these cards as an NPC — see *Answering a history roll as an NPC*, below.
- **A player's answer lands too.** A save a player rolls off a request card or a chip
  (one no `ActionRun` of the GM's asked for: `action_run.resolved_by_a_run`) is put on
  them by the GM's window when it comes back (`GMWindow._on_player_save`): the rung
  resolved against their snapshot and sent to their sheet with its `ShakeOff`, as an
  aimed one is, under the same "Saves you roll for NPCs" switches. An impression's
  ladder names the impression, its skill and the impresser (`SpecLadder.effect`,
  `skill`, `source`), so a request-only Impress writes the same record a resolved one
  does. A save an attacker rolled off their own card (`own_attack`) is left alone: it
  was rolled for someone it does not name.

### Area Healing

- An area of an effect that is **checked** rather than resisted — Healing, by its
  `checkOutcomes` (DC, and the conditions each degree removes in order) — has no Dodge,
  no primary and no request mode: `area_use_heals`. The wielder makes one check
  (`area_heal_spec`: the rank against the DC), rolled by the server for an NPC and asked
  of a player, and each degree of success takes a condition off everyone in the area,
  least severe first (`targeting.success_removals`, worked on a copy). They come off
  through `action_remove` — an NPC's model, a player's app's remove commands.

## Answering a history roll as an NPC

A trait roll clicked in the GM's history — a request card, or a save chip under a roll
— used to roll at +0, because the GM's roller has no sheet. Now:

- **A click opens a menu of the NPCs on the board** (`GMWindow._on_history_roll`), never
  the cast and never players. The ones the spec names come first, then the board's
  order. Each row is the scene card's picture, the name, and the number it would roll
  (`answer_reading`): a best-of names the trait it picks ("Will +8"). A trained-only
  skill the NPC lacks is marked ⚠ but still rolls, and an NPC that already answered
  this card (by the card's `seq`) is ticked ✓ and may roll again. An empty board shows
  a disabled line rather than quietly rolling +0. It is one NPC per click: the user
  dropped picking several, to keep it a plain context menu.
- **A right-click rolls it as written**, with nobody's number (`roll_button`'s
  `on_plain`, the panel's `rollPlain`). The menu's last line does the same. A player's
  panel connects nothing to it, so there a right-click does nothing.
- **The roll is the roller's**, so its adjustment and Hidden apply. The spec carries
  the NPC as `RollSpec.actor`, and every history heads the card "Mook (GM)" with the
  "Mook: " label prefix dropped (`roll_heading`). `actor` is a v14 spec key. An answer
  to an action's card still carries its `action_id` and title, so it lands as the NPC's
  row of that action's table.
- **A failed save applies itself.** When the roll comes back (`sessionRollRevealed`, or
  `_show_offline_roll` off the air), `_apply_answer` applies the rung of the ladder the
  spec carries (`RollSpec.ladder`, a `SpecLadder`, protocol v14). Whoever built the
  save made it concrete with `concrete_rung`: an Affliction's ids as the wielder picked
  them, a Weaken's scope as a parameter, the damage ladder's escalation, an impression's
  `{source}` template. The builders are `effect_spec_ladder` (a power's save),
  `impress_resist_spec` and `area_resist_spec`. Whose `{source}` it is comes from the
  roll the chip hangs off (`answer_context`). The rung goes on through `impress_apply`.
  The first version read the ladder back out of the outcome *words* to avoid a bump,
  and the user asked for the bump instead.
- **Four GM switches** (GM Mode ▸ *Saves you roll for NPCs*, `gm_auto_apply`, all on by
  default): damage, afflictions and other effects, impressions, and area attacks.
  The switch is the ladder's own `kind`, and an area's save is `area` whatever its
  effect.
- **↶ takes it back.** The card (or the table row) of a roll that applied something
  gets a small ↶ (`RollHistoryPanel.set_undo`, kept by `seq` so a replay rebuilds it).
  It removes what the rung added and puts back what it replaced, by `condition_changes`
  between the NPC's conditions before and after. So a Stunned that escalated from a
  Dazed comes off and the Dazed returns.

## End of turn

- A creature's card on the GM's board offers **End turn**. It does two things. First,
  every impression and every Affliction on that creature is resisted again **against
  the original DC** — read off the creature's own conditions (`held_shake_offs`: the
  `ShakeOff` each one's rung wrote, see [the conditions
  notes](conditions-and-damage.md)), so it is the NPC's model or the player's latest
  snapshot that says what is on them, and a GM restart forgets nothing. The server
  rolls an NPC's (`shake_off_roll`), a player is asked for each in turn (`Shake off
  Demoralized`, `Shake off Sleep Ray`). One that is made comes off with every condition
  it put on; one that is failed stays. An impression is resisted with what the GM chose
  then, or the creature's best option; an Affliction with its "Overcome by" resistance,
  or the one it was resisted with. An Affliction at its third degree is overcome once a
  minute, not as a turn ends (the effect's `overcome.eachMinuteFromDegree`), and a
  Prolonged one at the end of each scene: End turn names it and rolls nothing
  (`shake_off_cadence`). An Instant Recovery one clears with no roll.
- **The Affliction's modifiers** (PDF p110–111) ride on the record (`ShakeOff.mods`, the
  instance's ids the effect's `overcome.modifiers` names, written by `overcome_mods`).
  The check is at `shake_off_dc` (Increasing Difficulty +1 for each check after the
  initial save, Decreasing Difficulty −1 for each after the first, counted in
  `ShakeOff.checks`), and a failed one does what `shake_off_failed` says: Progressive
  replaces the conditions with the next degree's (the record keeps each degree's ids
  in `rungs` for it), Fatal puts Dying on at the third degree, and the record with its
  new count and degree is written back onto the conditions.
- **A new use on a creature already under it** (PDF p109–110) goes through
  `merge_landing`, by way of `GMWindow.land_rung` — every place a save's rung lands (an
  NPC's answer, a player's request save, an aimed attack, an area). Afflictions do not
  stack: only the highest degree applies, so one no deeper than what is on changes
  nothing ("no worse than it was"), and a deeper one replaces it. A **Cumulative** one
  (`addsDegrees`) adds its degrees to those on instead, to the last rung ("adds up to
  Stunned"). The same power from the same wielder is matched by `same_effect` (kind,
  effect, power name, source), whatever its DC or degree.
- **Concentration and Sustained** (`reapplies`): each turn the wielder keeps it up,
  everyone it is on resists it anew, with no attack (p110). The wielder's End turn
  does it first (`ImpressController.keep_up`), in a table of its own ("Thug: keeps
  powers up"): it finds the targets by the records on the board's creatures that name
  the wielder as their source (`maintained`), rolls an NPC's new save at the original
  DC with the trait the record keeps for it (`ShakeOff.save`, `reapply_roll`) and asks
  a player (`reapply_request`). A failure lands as a new use (`reapplied_landing`).
  Whether it is still kept up is the wielder's power card's on/off switch, by name
  (`maintained_by`); switched off, it stops (`shake_off_stopped`) — a maintained
  Instant Recovery one (`whenStopped`) clears, any other loses its maintained
  modifiers and is overcome as usual. The target's own End turn still overcomes it as
  any other.
- **Recover** (the Actions grid) offers a new check against each record on the actor
  too (PDF p206: `shake_off_recover_spec`, at any cadence, so it is how a third-degree
  Affliction is checked before its minute is up). The spec carries the record
  (`RollSpec.shakes_off`); a made one takes off everything carrying it
  (`shake_off_made`) — on a player's sheet through `DiceSection.settle_recovery`, on an
  NPC through `_on_recovery_back`. A failed one runs the modifiers as End turn's does
  (`shake_off_failed_on` on the player's own sheet, `land_shake_failure` on an NPC):
  at the third degree, where End turn rolls nothing, it is the only check Fatal's Dying
  can come from.
- Second, every condition on it whose `recoveryCheck` cadence is `end_of_turn` (a
  damage-caused Dazed or Stunned — `end_of_turn_recoveries`) is rolled off. One an
  impression or an Affliction put on is not offered (`can_recover`): it comes off with
  what put it there, and is not rolled twice. An NPC's
  checks are lines of the same server-rolled group, and a made one sheds through the
  path the chip's 🎲 takes (`GMWindow._shed_npc_recovery`). A player gets the same
  request card the chip's 🎲 sends, and their own app settles it.
- The rolls land in their own table (`"<name>: end of turn"`) and the outcome on the
  notice. What it does not do yet is in the debts.

- **A player can end their own turn too** (2026-11-13): the Conditions block's End turn
  makes the same split on their own sheet and rolls the checks there (see [the
  conditions notes](conditions-and-damage.md)). And every chip with a record has its own
  🎲, which rolls one check against it at any time, outside Recover.

## One table per group (Impress too)

`RollHistoryPanel` draws every record carrying a `group` as **one** `GroupTableCard`:

- **The key** is `table_key(group)`: the group id without a numeric `.n` suffix — the
  action id. That is how an action's stages fill in one table. A roll a *player* made
  for an action (an asked Dodge, their impress check, an area's attack) is an ordinary
  roll with no group, but its spec carries the action id and a title, so
  `action_table_key` puts it in the same table.
- **A row** is the creature a line's spec names in `targets`. The Impress controller
  now sets it, plus a `title` the card is headed by. A roll naming nobody is a row of
  its own, and each row ends on its last roll's outcome.
- **Row order** sorts by `abs(seq)`, because off the air a window counts its own rolls
  *down* from −1.
- **Removing and revealing.** `remove_roll` and `reveal_roll` find a roll's table by
  `seqs`. The GM's ✕ strikes the whole table, and the GM's **Show** — beside it in the
  heading row, there only while the table holds a hidden session roll — shows every
  hidden roll in it, one `reveal_roll` per `seq` (the server reveals them one at a
  time). A table had no Show at first, so a hidden group could never be shown. The
  heading is one label built once, so its buttons survive a redraw; `lines()` still
  reads it first.
- **The exception:** a grouped roll whose spec has a **follow-up** keeps its own card.
  Request-mode Impress is a group of one, and its card is where everyone finds the
  Resist button.

## Minions

A minion target changes three things here. Its failed save is the worst rung, an
Impress against it is routine at +5, and a critical hit on it as an area's primary
needs no save. Its own rolls also score no added success. All of it is switched by
the session's rules. See [the minions notes](minions.md).

## Aimed attacks

A single-target attack that knows its target. Any attack line (a power or gear card's
footer, either sheet, the simple sheet; a pinned tile on an NPC card) keeps its **left
click**: it rolls, as it always did. Its **right click** lists the Scene's creatures
(`ui/attack_targets.py`, `rules.targetable_attack`); picking one rolls the attack *at* it.

- **A player's** aimed attack is declared on the action channel (`KIND_ATTACK`,
  `AttackDeclaration`: the target's Scene ref and name, the aimed spec) and then rolled
  with the declaration's `action_id`, `targets=(name,)` and a "Ana → Thug" title — all
  keys the server already keeps, so **no protocol change**. The roller's
  `aim_attack` serves the bus topic `attack-requested`. **The declaration is sent from
  the roll itself** (`roll_spec(…, before=declare)`), carrying the spec the die is made
  of: the maneuvers on the roller's lane have moved its modifier and its save's DC by
  then, and the GM's app asks for the save off the declaration. A declaration that does
  not go calls the roll off.
- **The GM's** (`ui/attack_gm.py`, `AttackController`) gets an `ActionCard` with
  **Accept / Decline** (its new optional second button). Accept judges the hit against
  the target's **Defense in play** (`rules.defense_class_in_play`: Vulnerable halves it,
  All-Out Attack lowers it, Defending raises it — the area window's primary and the team
  attack judge the same way; before the combat maneuvers it was the build's number) with
  `rules.judge_attack` (the roller's arithmetic, so a counted natural 20 is an added
  degree and a critical) and then:
  - one NPC save: rolled by the server at once, at the GM roller's bonus minus penalty
    (`DiceRollerPanel.adjustment`), landed through `answer_rung`;
  - several (a Linked power): `AttackWindow`, a resistance and an adjustment per save;
  - a player target: asked for each save in turn, as an area attack asks.
  Decline settles the card; nothing else happens.
- **What Aid, Feint and Trick left counts here, and is spent here.** Before judging, the
  controller reads the opposing conditions between the two (`rules.opposing_effects`):
  the attacker's Aided Attack or Trick Advantage naming the target goes on the check,
  the target's Aided Defense on its Defense, and a Feinted target's Defense is judged as
  if Vulnerable (`rules.defense_against`). Each counted instance comes off its bearer,
  hit or miss, through the GM window's `combat_land` (an NPC's model, a player's remove
  command). The card says which ("with Aided Attack (Thug) +5, Thug: Feinted (Ana)").
  The target's **posture** counts here too: given the attack spec's `attack_range`,
  `opposing_effects` adds a prone target's +5 to a close attack or −5 to a ranged one
  ("Thug: +5 Prone"), which is never spent. The attacker's own close −5 is already in
  the rolled bonus (see [conditions](conditions-and-damage.md)).
- **The saves come back through the session.** The controller hands every roll to its
  runs first (`take_roll`), as the area and Impress controllers do. It did not at
  first, so at a live table an NPC's save was rolled by the server and never landed,
  and a player's asked save was never gathered; off the air the group is rolled in
  place, which is all the tests saw. `test_a_hosted_aimed_attacks_save_comes_back_and_lands`
  watches it through a real hosted session.
- **The GM's own** aimed attacks (an NPC card's tile, an NPC sheet's line through
  `ATTACK_REQUESTED` in `OUTWARD_REQUESTS`) skip the card and run once the die is in.
  Offline rolls reach the controller through `_show_offline_roll`, which now marks a
  natural 1 or 20 with no DC as critical, as the server does.
- **Minions:** a non-minion's attack on a minion hits on the routine result too
  (`routine_attacks`); a critical on one lands its worst with no save
  (`critical_answer`, Impervious respected); its failed save is the worst rung.
- **The save is offered once.** An aimed roll joins its attack's table in the history
  (`rules.aimed_attack` makes `_provokes` false), so no card carries a save button
  beside the GM's Accept — answering both used to land the damage twice. A team
  attack's lines carry no follow-up for the same reason.
- **Which die the save reads.** A hit the routine result made reads `ROUTINE_DIE`, so
  a natural 1 rolled anyway is no graze; a 20 raises the save only when the verdict
  called it critical, which a lucky re-roll's lifted 20 is not. The history's chips
  read the record's `critical` the same way (`_counts_twenty`).
- **Things that do not go through.** A player's die still tumbling: nothing is
  declared. A target whose sheet has not reached the GM: Accept is offered again
  rather than the attack settled. Saves the session would not take: the card settles
  saying so. The result names each save rolled ("Toughness 12 vs DC 20: Hit").
- **The GM's NPC sheet** aims off the GM's own Scene, offline too, never at itself: the
  window carries an `aim_targets` callable that `attack_targets.targets_for` finds
  walking up from the roll line; everywhere else it is the live session's Scene.

## Team attack

A group of NPCs combines its attacks on one target (PDF p201). From an NPC group's ☰
menu (`ui/npc_group.py`), **Team attack…** opens `ui/team_attack.py`'s window: the target
(off the Scene, the group's own members left out), the attack the members share
(`rules.team_attack_options`, keyed by power or item name and attack index, so a squad's
copies line up), who joins in (a member who is down starts unticked), and one adjustment
for every check.

- The rules are `core/rules/team_attack.py` and the numbers `system.json`'s `teamAttack`:
  same effect and resistance, ranks within `rankSpread`; the hits' largest rank is the
  primary; the others' total degrees reach a step (+2 at one, +5 at three — the book
  skips two, so two reads as one); any critical among the hits adds `criticalBonus` once;
  misses count for nothing; an effect with no attack check hits for one degree.
- On a rank tie the primary is the hit with the **fewest** degrees: the primary's own
  degrees count for nothing, so that reading counts the most of the team's. The book is
  silent; this is a call.
- `AttackController.team_attack` rolls every check as **one server group** against the
  target's Defense (one table in the history), judges each with `judge_attack` (a minion's
  20 adds nothing; against a minion target the routine result counts), and then resolves
  the target's one save at the raised rank (`team_save_spec`, ladder rank raised too, so
  Impervious is measured against it) on the aimed-attack road: an NPC's rolled at once at
  the GM roller's adjustment, a player asked. A critical on a minion target lands its
  worst unrolled.
- Each roll is matched to its attacker **by position**, never by name: two creatures in
  one group may share a name.

### A call to roll (the GM's Team attack tile)

The Rolls block's **Team attack** tile is not the group window any more (that is on a
group's ☰ menu only, the user's call, 2026-11-09): it **calls** a team attack that anyone
at the table answers with their own roller, maneuvers and all, and the GM resolves.

- **Sending.** `ui/team_call.py`'s `TeamCallWindow` takes the target (the GM's Scene) and
  one signed Bonus/Penalty; **Send** posts `rules.team_call_spec` as an ordinary
  **request record** (`bridge.prompt_roll`), kind `KIND_TEAM_CALL` (`"team-call"`),
  carrying the action id, the title "Team attack → Thug", the target in `targets` and the
  adjustment as `modifier`. **No wire change**: every key is one a request already
  carries. The tile is greyed off the air (`NO_SESSION_FOR_CALL` — nobody could answer)
  and with nobody on the Scene.
- **The table.** `RollHistoryPanel._insert` routes a call's request (`team_call_of`) into
  a `GroupTableCard` keyed by its action id. The call is no row: it is a line under the
  heading with a **Join** button (`join_button`, `joinRequested` →
  `teamJoinRequested`). Entries fill rows of the same table on every screen, by
  `action_table_key`. Striking the request takes the button away and leaves the rows.
- **Joining** arms the presser's roller (`CombatRunner.join_team`: a fixed lane line,
  `TEAM_LINE`, the attack picker first when none of the actor's attacks is loaded). The
  **next** attack thrown is stamped by the runner's shift (`rules.team_join_spec`): the
  call's action id and title, the attacker's name as `targets` (its row), the GM's
  adjustment on the check, "(team attack +2)" in the label. Stamped *before*
  `apply_actions`, so a maneuver restates the bonus already moved. With a target named
  it is an aimed attack by its shape, so no card offers its save. A roll made before
  joining does not count; a **re-roll** of the entry throws the same stamped spec, so it
  replaces the entry. A player's sheet hears Join through `DiceRollerView.teamJoinRequested`;
  the GM's history through `_gm_team_join`, which asks which NPC joins (the target left
  out) and acts as it.
- **Gathering** (`TeamCallController.take_roll`, on `rollAdded`): the request's `seq`, and
  every non-group roll with the call's action id and a save, one entry per
  `(seat, actor)` — a later roll replaces the earlier one. The target's own roll is
  ignored. `AttackController.ignore(action_id)` keeps the aimed-attack controller from
  holding the entries as early rolls. The GM's `ActionCard` (**Resolve** / **Call off**)
  lists each entry judged now against the target's `defense_class_in_play`
  (`AttackController.judge_entry`), and "Now: Primary: …" from `combine_team_attack`.
- **Which entries combine** is `rules.team_fits`: the first entry sets the effect and
  resistance (ladder kind and save trait); a different one is left out, and so is one
  whose rank is more than `rankSpread` below the strongest (a rank of 0 is unknown and
  waives it, as in the group window — `ladder.rank` is on the wire for Damage only).
- **Resolve** strikes the request (the Join buttons go everywhere) and hands the kept
  entries to `AttackController.resolve_team`, which shares `_combine_team` with the group
  window: judge, combine, then the target's one save at the raised rank — rolled for an
  NPC at the GM roller's adjustment, asked of a player, a minion's critical landing its
  worst — under the **call's own action id**, so the save joins the call's table.
  **Call off**, or the GM's ✕ on the table (`rollRemoved` of the request), settles it
  with nothing done. An entry struck from the history leaves the team.

## Team checks and group checks

Two more of a group's ☰ menu items (PDF p18), one window (`ui/group_check.py`,
`GroupCheckWindow`, `MODE_TEAM` / `MODE_GROUP`). The check is any trait the roller's
Ask to Roll form offers (`rules.requested_roll_choices`), rolled at each member's own number
(`localize_spec`; an untrained skill is marked ⚠ but still rolls), with one adjustment on
every roll. A member who is down starts unticked. Nothing lands — the outcome is posted
to the history as a line.

- **Team check:** the helpers roll against `teamCheck.helperDc` (10) as one server
  group; their degrees of success (failures add nothing) times `bonusPerDegree` become
  the leader's bonus (`team_check_bonus`); then the leader rolls, against a DC when the
  GM ticks one, or as a bare lead roll when not.
- **Group check:** everyone rolls against the DC as one group. `group_check_result`: if at
  least `groupCheck.successShare` (half) succeed, the group lands on the lowest degree of
  success among them; then the natural 20s (a minion's does not count) and 1s are summed
  and move that degree along the no-zero ladder (`shift_degree`). A failing group lands on
  its **least severe failure** — the book does not say; this is the mirror reading.
  Each member's ✓ / ✗ in the window is their own total against the DC, before the
  natural 20s and 1s, so the marks agree with "2 of 4 made"; a natural is noted beside it.

**Both windows (and Team Attack's) outlive a close.** They are deleted on close, but the
rolls they sent come back later, so nothing after Roll reads a widget (the choices are
taken when Roll is pressed), every write to one checks `shiboken6.isValid`, and the
outcome goes to the history first. Rolls the session would not take hand the window
back, Roll enabled again. A result that wraps onto more lines grows the window
(`widgets.grow_to_fit`) rather than squeezing the rows above it.

## Combat maneuvers

The roller's Actions popup (see [the roller notes](dice-and-rolling.md#the-actions-button))
offers the combat maneuvers and actions out of `actions.json`'s `combat` block. The
plan they are built to, phase by phase, is `docs/plans/combat-maneuvers.md`.

**The menu's grouping is the user's** (2026-11-13): Attack Maneuvers (Accurate,
All-Out, Defensive, Power), Dice (Ask, Reroll), Actions (Attack, Aim, Defend, Grab,
Impress, Area) — and, behind the popup's **Show more** box (`"more": true` on a
`combat.menu` section, remembered by the app), More Maneuvers, More Dice (Take 10),
More Actions and the GM's Group. See [the roller notes](dice-and-rolling.md#the-actions-button).

**Every tile has a road.** A host maps each action it runs to `None` or the reason it
can't run now (`combat_menu`'s *offered*); one it never mentions is greyed `NOT_BUILT`,
which is now only for a mod's action the app has no road for.
`test_every_tile_a_player_sees_has_a_road` and `test_every_tile_the_gm_sees_has_a_road`
fail if a built-in tile falls back to it, and check every greyed tile says why. Where
each kind is documented: the attack maneuvers below; the self actions, Block and the
shared runner in [the roller notes](dice-and-rolling.md#the-self-actions-aim-block-charge-defend-recover-drop-prone-stand-finishing-catch);
the targeted actions in *Aid, Feint and Trick* and *Grab, Trip, Disarm, Smash and
Escape*; the GM's Team attack tile in *A call to roll*; the temporary conditions they
leave in [the conditions notes](conditions-and-damage.md).

**The attack maneuvers** (PDF p200) are `core/rules/maneuvers.py`, read generically off
each record's `bonus` / `penalty` (`attack_check`, `effect_rank`, `defense` — the
`MANEUVER_*` names) and `lasting`:

- **The caps** are `combat.limits`: X at most `maxShift` (5), the penalised value never
  below `floor` (0), the bonused one never more than doubled (`doubleCap`). The values
  are `maneuver_values`: the attack line's modifier (only when it *is* an attack check —
  its `marks` carry `trait_keys.attack_check`; an effect check takes no attack
  maneuver), the **lowest** `effect_rank` of the saves it provokes (a Linked attack
  moves all of them), and the actor's Defense in play. A save whose rank is unknown
  (Mind Reading's opposed one, no DC) offers no effect rank.
- **Combined**: `maneuver_limit(…, others)` adds the other lines' shifts before reading
  the caps, so two that raise the attack check share the room to double it, and two
  that oppose (Accurate and Power) give each other room. Several lines at once is
  allowed (§2 of the plan).
- **Fixed lines first**: a self action's line with an `attackModifier` (Charge's −2,
  any of its ways) is in the value the caps start from — `maneuver_values(…, lines)`
  adds it, and the reason names it ("Your attack bonus is +0 with Charge: it cannot be
  doubled"). A +2 attack charged is +0, so All-Out cannot raise it, and Power has
  nothing to spend. A Slam's rank is not counted: it reads the actor's speed and the
  rank the maneuvers leave, so it is no fixed shift.
- **The shift** (`apply_maneuvers`) moves the modifier and restates the label's bonus,
  moves each ranked save's DC (and the number in its label), its ladder's `rank` (what
  Impervious is measured against) and `dc_if_resisted`, and names the maneuvers in
  brackets after the label. Defense is not on the attack: it lands as a condition.
- **What stays** (`lasting_conditions`): All-Out's `all_out_attack` (Defense −X) and
  Defensive's `defensive_attack` (Defense +X, attack checks −X), put on **temporary**
  once the die has settled, hit or miss — never on a reroll, never on an abandoned
  roll. Accurate and Power change only the attack being made.
- **`RollSpec.effect_rank`** is local only (like `marks`): the DC and the ladder's rank
  are what travel, and both are already shifted. No new wire key, so the maneuvers
  needed nothing from the protocol.
- **The GM's roller** takes them too, as the NPC each pick asks for (see [the roller
  notes](dice-and-rolling.md#one-runner-for-both-rollers-uirollercombat_runnerpy)), and
  grants Surprise Attack. The NPC-group Team attack window rolls **without** maneuvers
  (its Adjust is the bonus/penalty); a team attack that takes them is a different card —
  a call to roll anyone answers with their own roller, the GM resolving (see *A call to
  roll*, under Team attack).

## Aid, Feint and Trick

The targeted combat actions (PDF p200, p202, p204–205), phase 8 of the combat maneuvers
plan. Each is one check by the actor, read off its `actions.json` record's `targeted`
block (`core/rules/targeted.py`):

- **Aid** — an attack check (one of the actor's, off the attack picker) against DC 10,
  judged as an attack is (`judge_attack`, so a natural 20 adds a degree). One degree
  gives the **ally** Aided Attack or Aided Defense +2 **against the opponent**, three
  +5 — Attack or Defense is the tile's option.
- **Feint** — an attack check (the picker again; −5 as a simple action, an option),
  opposed by the target's best of Close Combat, Ranged Combat, Deception and Insight
  (the user's call: an attack does not record close or ranged, so both combat skills
  count). A win leaves the target **Feinted** against the feinter.
- **Trick** — Deception, opposed by the target's Deception or Insight. A win leaves the
  target **Tricked** (narrative: heedless of the risk of acting against the trickster);
  losing by two or more degrees gives the target **Trick Advantage** (+5) against the
  trickster.

What they leave are the **opposing** conditions (see [the conditions
notes](conditions-and-damage.md)): counted and spent by the next aimed attack between the
two they name (see *Aimed attacks*), never by "Clear temporary" (decision 17).

- **The data.** `targeted`: `check` (`"attack"`, or `{kind, trait}`), `dc` or `resist`
  (the target's candidates, a `KIND_BEST_OF` key), `ally`. `wins` / `losses` (on the
  action or an option, merged as an option's fields are) are ladders of `{degrees,
  applies}`: the deepest rung the actor's degrees of success (or of failure) reach lands.
  Each `applies` entry names `on` (`target` / `ally`) and `against` (`actor` /
  `target`), whose name becomes the parameter (`concrete_changes`). In an opposed check
  the actor's degree is the target's mirrored (`actor_degree`: each degree the target
  failed by is one the actor won by).
- **The check** (`targeted_check_spec`) is the attack's line with what it would force
  taken out — no save, no ladder — and **no `marks`**, so the attack maneuvers neither
  ride on it nor are spent by it (a line on the lane greys while it is on the chip).
- **A player** picks the tile (greyed off the air, `NEEDS_A_TABLE`; with nobody on the
  Scene, `NO_ONE_TO_TARGET`; Aid without two, `NO_ALLY_AND_OPPONENT`), the creature
  (Aid: "Aid Bo", then "… against Thug"), the option and the attack, and
  `DiceSection.start_targeted` declares it (`KIND_TARGETED`, a `TargetedDeclaration` —
  in `ui/attack_targets.py`, the half every seat shares) from the roll itself
  (`before=`), as an aimed attack does. The check's spec carries the action id, the
  title "Feint: Ana → Thug" and the actor as its row.
- **The GM** (`ui/targeted_gm.py`, `TargetedController`) gets an `ActionCard` with
  **Accept / Decline**. Accept judges a fixed DC at once, or has the target resist the
  check's total (`opposed_dc`, a minion's 20 adding nothing): an NPC's line rolled by
  the server at the GM roller's adjustment, a player asked (`targeted_resist_spec`, their
  own sheet picks the best candidate). What lands goes on through `combat_land`, and a
  player bearer is told. The GM's own tile (`GMCombatMixin._gm_targeted`) asks which NPC,
  whom, which way and which attack, and `act_own` rolls the check as that NPC and
  resolves it with no card.
- One table per action in the history: the actor's check and the target's resistance
  are its two rows.

## Grab, Trip, Disarm, Smash and Escape

The rest of the targeted actions (PDF p202–205), phase 9 of the combat maneuvers plan,
on the same road as Aid, Feint and Trick: declared by a player (or the GM's NPC off the
tile), accepted on a card, resolved by `TargetedController`. What is new is all in the
`targeted` block, read generically:

- **`check`** may be a list — the actor rolls the best on their own sheet (Escape:
  Strength or Sleight of Hand) — and a candidate may be `attack` (the attack picked) or
  `damage` (its Damage rank, `attack_damage_rank`). `uses_attack` is whether the picker
  asks.
- **`dc`** may be `"defense"` (`vs_defense`: Grab, Trip, Disarm, Smash) — judged exactly
  as an aimed attack (`_judge_hit`): `defense_against` with the opposing conditions
  between the two counted and spent (a feint makes a grab easier, and is used up), a
  minion target hit on the routine result. A miss ends it. Or `{base, kind, trait}`
  (`dc_from`: Escape's 10 + the grabber's Strength, `targeted_dc_from`), read off the
  target's sheet when the GM accepts; a player target whose sheet has not arrived is
  "accept again", as an aimed attack is.
- **`opposed`** `{check, resist}` is a second stage after a hit (Grab: Strength against
  Strength or Dodge; Trip: close attack bonus or Strength against the same; Disarm: the
  attack's Damage rank against Strength). The actor's half (`targeted_opposed_spec`) is
  worked out **on the actor's own sheet** and carried in the declaration
  (`TargetedDeclaration.opposed`), because the GM's app cannot see a player's Damage rank
  (`effect_rank` is local). The GM's app then rolls it — a lead-only server group for an
  NPC, an ask for a player — and the target resists its total (`_resist`, shared with
  Feint and Trick; `targeted_resist_spec` takes the second stage's candidates when there
  is one). A Disarm with an attack that deals no Damage is refused at the tile
  (`NO_OPPOSED_CHECK`).
- **The outcome** may land on the actor too (`on: "actor"`, `TARGETED_ON_ACTOR`): Grab's
  **Holding** names the target (see [the conditions notes](conditions-and-damage.md)).
  A rung may carry `removes` (Escape: Restrained and Bound off the actor, Holding naming
  the actor off the grabber) and `text` — what the app does not track, said in the
  history with `{actor}` / `{target}` filled in (Disarm's dropped item, Smash's Damage on
  the object, Trip's extra distance, Escape's move). `targeted_outcome_text` names a
  removal only when the bearer bore it.
- **Smash's** two options are its two penalties (−2 worn or carried, −5 hand-held).

The history table: the actor's row holds the attack and the opposed check, the target's
row its resistance. `test_a_hosted_grab_runs_both_stages_through_the_server` runs both
stages through a real hosted session.

## Not done yet

These are in [the debts](debts.md):

- End turn's other checks (Suffocation), the clock a third-degree or Prolonged
  Affliction waits on, Increasing Difficulty's cap, and a maintained Affliction
  re-subjecting a target that has shaken it off;
- the area pieces left out: a Hero Point inside the table, and Nullify and Move Object
  areas;
- the targeted actions off the air, and the opposing conditions in anything but an
  aimed attack or a targeted action's attack (an unaimed roll, an area's primary, a team
  attack);
- the grab rules past the first hold (improving one, size, carrying, squeezing,
  throwing), the Improved Grab / Hold / Trip / Disarm / Smash advantages, and a block
  the GM's judge makes by itself;
- the combat maneuvers' leftovers: no turn clock (temporaries come off by hand), a prone
  target's ±5 counted only where the opposing conditions are, Recover's once per scene
  and Aim's lapse untracked.

Counting earlier Impress attempts automatically was dropped: the GM sets the slider.

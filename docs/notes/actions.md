# Actions against other characters: Impress, area attacks, and the targeting layer

Matters when touching the Impress action, area attacks, `actions.json`, the roller's
Action row, the history's table card, or anything that resolves one check against
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
  **bonus and a penalty on the impresser's check** of their own. A player's dialog in
  a session therefore carries no modifiers at all; it declares (impression, skill,
  targets off the scene, mode) and the GM's window opens on it, the player's own
  choices read-only. Toggles sharing an `exclusive` group (simple vs. full action)
  turn each other off; a slider stops only on its `step`, so "earlier attempts" moves
  in fives.
- **Several targets is a group impression**: one check, the group penalty once, each
  target resisting separately. Every target also gets a **bonus and a penalty on the
  check against them alone** — applied as an offset to the DC that target resists,
  which is the same arithmetic as adjusting the check and the only way one die can
  stand against several totals.
- **Resisting is "Use highest" by default.** The candidates are the impresser's skill
  (as the skill — a specialized pool resists as its base skill), then `resist.skills`
  and `resist.resistances`. An NPC resists with the GM window's pick. A targeted
  *player* is asked nothing before the roll: once the check is in, they are asked to
  roll their resistance (`impress_ask_resist_spec`, a `KIND_BEST_OF` spec with the DC
  in), and pick another trait in the prompt if they want. If the GM unticked "Use
  highest" for them, the ask names that one trait instead.
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
  whole scene of targets scrolls rather than running off the bottom.

## The wire (protocol v13, unchanged)

- `ActionRequest` (any seat → the GM alone, stamped with the sender, like
  `ModRequest`), `ActionPrompt` (GM → one named player) and `GroupRollRequest` (GM
  only). Payloads go through `sanitize_mod_payload` and are never read by the server.
  Kinds: `impress` and `area` (a declared attempt, whole or bare); prompts `roll` (roll
  this), `roll-closed` (that ask is settled) and `outcome` (what it did to you). The old
  `resist` / `resist-closed` pre-roll question is gone.
- **No protocol bump** for the asks. Nothing new crosses the server, and an older
  client that ignores a `roll` prompt still has the request card in its history,
  whose roll carries the action id home all the same.
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
- **The area line.** `power_rolls_with_area` appends one `KIND_AREA` spec to the
  footer. It is not a die, and it **never travels as one**.
  - It goes *last*, so a pin's index keeps naming the roll it was made on. The footer
    offers no pin menu on it.
  - Its `trait_key` is the build's id, which `find_area_power` looks up. That lookup
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
  (`TargetDropField`, a `CardDropFlow` of chips), or picked off an "Add" menu.
- **It is modeless** (`show()`, not `exec()`). The cards are dragged off the Scene
  block on the same window, and a modal dialog swallows the press that starts the drag.
- A player's scene cards are draggable now; only the GM's board takes the drop back.
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
  a menu of them. The window then lets the GM switch the **power** and, for a free
  use, the **shape** (a player's too, worked out again off their snapshot).
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
  - who is in the area. A dropped card ticks a row, and a roster card's `kind:source`
    ref works too.
  - the primary, and whether it was hit. "Hit" is pre-ticked from the attack's total
    against the primary's Defense class.
  - per creature, a cover combo (Dodge only) and a bonus and penalty (both of that
    creature's checks).
- An NPC attacker gets a 🎲 Roll attack button. A player's gets **🎲 Ask for the
  attack**, which asks them for it like any other roll — how a bare declaration's
  attack gets rolled once the GM has picked a primary. Resolving does not wait on it:
  the GM may tick Hit by hand.
- A player's fresh snapshot reaches open area windows as it does Impress ones
  (`AreaController.windows`). If the primary's Defense changed, the Hit tick is
  re-read, unless the GM set Hit by hand.

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
    off a chained request (`area_request_specs`, which off the air still does).
- **No crits on the area half.** An area's Dodge and save follow-ups take no natural-20
  bump and no natural-1 graze, because area effects make no attack check (PDF p195).
  The primary's attack is an ordinary attack and keeps its own.

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
  `seqs`, and the GM's ✕ strikes the whole table.
- **The exception:** a grouped roll whose spec has a **follow-up** keeps its own card.
  Request-mode Impress is a group of one, and its card is where everyone finds the
  Resist button.

## Not done yet

These are in [the debts](debts.md):

- the end-of-turn save to shake an impression off;
- counting earlier attempts automatically;
- the area pieces left out: a Hero Point inside the table, areas that do not resolve
  to conditions, heavy weapons' two ranks, and linked areas.

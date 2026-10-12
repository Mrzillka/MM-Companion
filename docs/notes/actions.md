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
- **UI:** `ui/impress.py` holds the three windows (the impresser's `ImpressDialog`,
  a target's `ResistPromptDialog`, the GM's `ImpressWindow`) and computes nothing;
  `ui/impress_gm.py`'s `ImpressController` is the only part that touches the session,
  and reaches the GM window through the four-method `ImpressHost` surface rather than
  its internals. `session_player.ResistPromptReceiver` is the player's end.

## How one attempt runs

- **The impression and the skill are chosen independently.** Demoralized with
  Deception is allowed: the impression decides what success does, the skill decides
  what is rolled. An impression's `skill` is only the one preselected when it is
  picked. Advantage-gated impressions are *shown greyed* with the advantage in the
  tooltip rather than hidden — a player should be able to see Dazed exists.
- **The GM always sets the situational modifiers**, whoever is impressing. A player's
  dialog in a session therefore carries no modifiers at all; it declares (impression,
  skill, targets off the scene, mode) and the GM's window opens on it, the player's
  own choices read-only. Toggles sharing an `exclusive` group (simple vs. full action)
  turn each other off; a slider stops only on its `step`, so "earlier attempts" moves
  in fives.
- **Several targets is a group impression**: one check, the group penalty once, each
  target resisting separately. Every target also gets a **bonus and a penalty on the
  check against them alone** — applied as an offset to the DC that target resists,
  which is the same arithmetic as adjusting the check and the only way one die can
  stand against several totals.
- **Resisting is "Use highest" by default.** The candidates are the impresser's skill
  (as the skill — a specialized pool resists as its base skill), then `resist.skills`
  and `resist.resistances`. A targeted *player* is asked through a prompt and may pick
  another; their answer comes back with their own numbers, which the GM window then
  trusts over the snapshot's. The GM can override either way, and can roll without
  waiting — an unanswered player resists with their highest off the snapshot.
- **A prompt lives exactly as long as the question does.** Players are asked only in
  *resolve* mode (request mode asks nobody — they roll from the card), and switching
  to resolve asks whoever is ticked. Unticking a player who was asked withdraws their
  prompt (``resist-closed``) and ignores a late answer. Changing the impresser's skill
  voids every answer — the same skill is one of the ways to resist — and asks again.
  The window settles in ``done()``, not ``closeEvent``: **Esc rejects a dialog with no
  close event**, and that once left players staring at a prompt nobody would roll.
- **Two modes.** *Resolve* rolls the check and every resistance as one server-side
  group, and applies the rung each failure lands on: straight onto an NPC's model
  (through `_after_npc_condition_change`, the path every NPC condition change takes),
  or as condition commands to a player's app (escalation resolved against their
  snapshot first, so the GM decides it once). *Request only* rolls just the check,
  whose spec carries the resistance as a `dc_from_total` follow-up — every card offers
  "🎲 Resist Demoralized vs. DC 23", and clicking it rolls the *clicker's* best
  option (`KIND_BEST_OF`, below).
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

## The wire (protocol v11)

- `ActionRequest` (any seat → the GM alone, stamped with the sender, like
  `ModRequest`), `ActionPrompt` (GM → one named player) and `GroupRollRequest` (GM
  only). Payloads go through `sanitize_mod_payload` and are never read by the server.
  Kinds: `impress` (a declared attempt), `resist` (a target's answer, and the GM's
  question), `resist-closed` (the GM rolled or closed the window).
- **A group roll lands as ordinary records**, a line per creature, each tagged with
  `RollRecord.group` and attributed to the seat its line names (or the GM's, for a
  creature nobody plays). Nothing about drawing a card changed. The GM's controller
  gathers them again by group and applies once it has as many as it asked for — and
  registers that expectation **before** asking, because a hosting server raises each
  record as an event inside the `group_roll` call.
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
  `rolls` reaching up into `impress`.
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
- **The free area use.** Any attack may be used as an area at the table.
  - A checkable "Use as an area attack" on the power card's right-click menu sets
    `Power.area_use`. It is saved, runtime-like, undoable and priced at nothing.
  - The area rank is capped at the Power Level (`freeUsePlCap`).
  - The book's version is the size rule (PDF p58). Here it is the GM's call, not a
    gate the app checks.
- **The area line.** `power_rolls_with_area` appends one `KIND_AREA` spec to the
  footer. It is not a die.
  - It goes *last*, so a pin's index keeps naming the roll it was made on. The footer
    offers no pin menu on it.
  - Its `trait_key` is the build's id, which `find_area_power` looks up. That lookup
    is why `item_effective_build` now keeps the host's id.
  - The Dice block's `perform_roll` opens `AreaDialog` on it. The GM window's
    NPC-sheet path calls `AreaController.start_for_npc` instead.

### Declaring

- `AreaDialog` takes scene cards dropped into a primary slot and an area field
  (`TargetDropField`, a `CardDropFlow` of chips), or picked off an "Add" menu.
- A player's scene cards are draggable now; only the GM's board takes the drop back.
- At a table, sending it does three things:
  1. sends the `AreaAttempt` (`ActionRequest` kind `"area"`);
  2. posts a note naming who is caught;
  3. rolls the attack against the primary as an ordinary roll. That roll carries the
     attempt's `action_id` and **no follow-up**, because the primary's save is the GM's.

### The GM's side

- `AreaController` puts an `ActionCard` with **Resolve Area Attack** in the GM's
  history. The card is GM-only and not a record.
- It pairs the attack roll up with the declaration by `action_id`. An attack that
  arrives before its declaration is kept until the declaration lands.
- `AreaWindow` lets the GM edit:
  - who is in the area. A dropped card ticks a row, and a roster card's `kind:source`
    ref works too.
  - the primary, and whether it was hit. "Hit" is pre-ticked from the attack's total
    against the primary's Defense class.
  - per creature, a cover combo (Dodge only) and a bonus and penalty (both of that
    creature's checks).
- An NPC attacker gets a 🎲 Roll attack button.

### Resolving

- **Only the creatures nobody plays are rolled**, in two stages. Each stage is a group
  with **no lead**: every line's whole DC rides as its `dc_offset` from a `dc=0`.
  1. Stage 1 is the Dodge checks, group `"<table>.1"`.
  2. Once they are all back, stage 2 is each creature's save at the rank its Dodge
     left (`area_stage_rank`), group `"<table>.2"`.
- A stage with more than `MAX_GROUP_LINES` lines is split over several groups, and all
  of them are gathered before it moves on.
- Each failed save's rung goes onto the NPC through `impress_apply`.
- **Players are asked, not rolled for.** Caught players get one request
  (`area_request_specs`):
  - It is a `KIND_AREA_DODGE` spec naming them in `targets`, chained to the save.
  - The chain uses `follow_up_always`, because a *failed* Dodge is exactly when the
    save is due.
  - It also carries `dc_if_resisted`, the halved DC, which `follow_up_for_result`
    picks on a made Dodge.
  - Any player may click it (Interpose), and `localize_spec` adds the clicker's
    Evasion.
  - A hit primary player gets a plain full-rank save instead.
  - Players apply their own conditions.
- **No crits on the area half.** An area's Dodge and save follow-ups take no natural-20
  bump and no natural-1 graze, because area effects make no attack check (PDF p195).
  The primary's attack is an ordinary attack and keeps its own.

## One table per group (Impress too)

`RollHistoryPanel` draws every record carrying a `group` as **one** `GroupTableCard`:

- **The key** is `table_key(group)`: the group id without a numeric `.n` suffix. That
  is how an area's two stages fill in one table.
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

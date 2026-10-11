# Actions against other characters: Impress, and the targeting layer

Matters when touching the Impress action, `actions.json`, the roller's Action row,
or anything that resolves one check against several creatures (area damage and
afflictions are meant to be built on the same layer).

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
- **Two modes.** *Resolve* rolls the check and every resistance as one server-side
  group, and applies the rung each failure lands on: straight onto an NPC's model
  (through `_after_npc_condition_change`, the path every NPC condition change takes),
  or as condition commands to a player's app (escalation resolved against their
  snapshot first, so the GM decides it once). *Request only* rolls just the check,
  whose spec carries the resistance as a `dc_from_total` follow-up — every card offers
  "🎲 Resist Demoralized vs. DC 23", and clicking it rolls the *clicker's* best
  option (`KIND_BEST_OF`, below).
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

## Not done yet

The end-of-turn save to shake an impression off, area effects on this layer, and
counting earlier attempts automatically are in [the debts](debts.md).

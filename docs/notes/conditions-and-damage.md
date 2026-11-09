# Conditions and the damage ladder

Matters when touching conditions, damage, or the display overlay they drive.

Working notes for MM-Companion, split out of [CLAUDE.md](../../CLAUDE.md).

- Conditions are a small state-tracker, not a build cost. `conditions.json` is the
  single consolidated catalog (short `tooltip` copy + `includes`/`supersedes` graph
  + `mechanisms`/`parameter`/`debilitates` and typed penalty/mod fields), documented
  in `docs/mm-conditions-design.md`. A character's applied conditions live on
  `Character.conditions` as a list of `AppliedCondition` (id + chosen `parameter` +
  stacking `count` + `provenance` — the flattened set with back-refs). The non-roll
  resolver in `core/rules/conditions.py` (`apply_condition`/`remove_condition`,
  `expand_includes`) bundles umbrellas, applies per-part/trait-scoped supersession,
  stacks Hit, and cascades debilitation; queryable accessors (`condition_check_penalty`,
  `condition_defense_mods`, `hit_stack_penalty`, …) compute the mods. These flow into
  the sheet as a **display-only overlay** (the build/derived math itself stays
  condition-free): the ability/resistance tables re-skin their Total column via
  `condition_scope_penalty`/`resistance_condition_effect` (`apply_stat_effects`), the
  Skills block folds the scoped penalty into its "+" column, Advantages/Powers strike
  through a `debilitated_traits` trait, and the System block's derived Speed and
  Initiative readouts overlay `condition_speed_rank_mod` (a slowed/immobilised ground
  line) and `condition_check_penalty` (an all-checks penalty on initiative), tinted red.
  `ConditionsSection` (its own block) drives it: the "+" menu applies a condition (a
  `ConditionParameterDialog` first when it needs a subject) and renders one chip per
  `AppliedCondition`; its `conditionsChanged` fans out over the signal bus so every
  overlay refreshes. That menu is built by the shared
  `conditions.build_condition_menu`, which all three "+" buttons use (this block's,
  and the GM's fast-apply on a player and an NPC card) so a condition is in the
  same place in all of them. It splits the catalog into submenus by each record's
  `group`, titled and ordered by `_meta.conditionGroups` — an *ergonomic* axis,
  orthogonal to `category`: a flat list of 36 is slow to search mid-round, while a
  category is a rules fact and stays the axis the applied chips are grouped by. An
  untagged condition is offered flat below the submenus and a ruleset declaring no
  groups gets the flat menu back, so both are purely additive. Note
  `QMenu.addMenu(title)` hands ownership *back* to the caller, so each submenu is
  constructed with the menu as its parent or it is collected out from under the
  open menu. Turn economy is out of scope for now; recovery is below.
- **The block is no longer the only writer.** Extra Effort charges a rung of the fatigue
  ladder (`fatigued` -> `exhausted` -> `incapacitated`, named in `system.json` rather than
  in Python) from wherever it was spent, straight onto the shared model through
  `apply_condition` — so the ladder's supersession is the catalog's own doing rather than a
  second rule: Exhausted supersedes Fatigued, and climbing removes the rung climbed from.
  `ConditionsSection` therefore **subscribes** to `condition-changed` as well as publishing
  it, and re-renders its chips off the model like any other subscriber. Its own changes come
  back round the same way, one redundant re-render that costs nothing and keeps the rule
  "the chips are a view over the model" true for every writer.
- **The damage ladder is walked, not just rendered** (`core/rules/damage.py`). An
  effect's `resistanceOutcomes` was only ever *text* — the roll history said
  "Incapacitated!" and left the GM to find three conditions in a menu of 39.
  `damage_steps(data)` turns the rungs into steps (index 0 the made save, which
  still costs a Hit; 1..n the degrees of failure), `resolve_damage_step` says what
  one would put on *this* creature without applying it — which is what lets a
  button's tooltip promise it — and `apply_damage_step` puts it there through the
  ordinary `apply_condition`. Which effect is *the* damage ladder is
  `system.damage_effect`, so nothing here names an effect, a condition or a degree.
  Two rules make a rung more than a list of ids, and both read the condition graph:
  **escalation**, a rung's `escalates` map (the data form of "Stunned instead of
  Dazed if already Dazed"), chained so a rung naming `incapacitated -> dying` and
  `dying -> dead` walks a target one rung further with each failure — and gated on
  `_at_least`, "has it **or** has something that supersedes it", not on a plain
  `_has`. That distinction is the whole of a bug worth remembering: escalating
  *removes* what it escalated from (Stunned supersedes Dazed), so a plain "do they
  have Dazed?" answered no on the very next click, restarted the chain at the
  bottom, and put a Dazed back under the Stunned — the rung flickering on and off
  as the GM clicked it. And **order**,
  because a rung's printed ids are not always applicable in that order — rung 2
  reads `hit, stunned, staggered`, and applying Staggered *after* Stunned re-adds
  the Dazed inside its bundle with nothing left to supersede it, so a sibling that
  supersedes anything in another's expanded set is applied second. `ui/damage_row.py`
  is the four round buttons; the NPC card carries them in **both** states and the GM
  window's `_apply_npc_damage` resolves once and replays the settled ids onto an
  open sheet, so the two copies of the character cannot disagree about an escalation.
  Note `dead` supersedes `staggered` for this: it is the terminal rung, and without
  it the third click left a corpse Staggered.
- **A target's Toughness can refuse the ladder** (`core/rules/defenses.py`). Impervious
  leaves a Damage rank no greater than its own untouched, two degrees of success against
  Hardened, Impervious or Impenetrable leave not even the Hit, and Penetrating cuts back
  through (the target resists the Penetrating rank instead, and two degrees still leave a
  Hit) unless the defence is Impenetrable (p152, p154, p116). It used to be words in the
  success rung's note, so the GM's auto-apply put a Hit on a creature the rules say was
  never touched. Which modifiers play which part is `system.json`'s `damage_defenses`;
  what a defence covers is the ranks of the active Toughness boosts carrying it (a rank
  band covers its band), off powers and worn gear alike (`toughness_defense`). The save
  carries what it is measured against: `SpecLadder.rank` (read back off its DC) and
  `penetrating`, both 0 when unknown, which waives the rank tests. Three readers:
  `answer_rung` (the GM answering an NPC's save), `area_effect_rung` (an area, at the
  rank the creature resisted, read back off its save's DC) and `localize_spec` (the
  target's own sheet: the label says "unaffected: Impervious 10" or "roll twice, keep the
  higher", and Penetrating against Impervious lowers the DC). The relay passes the two
  ladder keys (`_sanitize_ladder`) from protocol v17 on.
- **Recovery checks are rolled** (`core/rules/recovery.py`). A record whose
  `recoveryCheck` names a `trait` and a `dc` offers a 🎲 on its chip — every chip: the
  sheet's, the simple sheet's, both GM cards' and the GM's scene cards. The data
  decides everything: `successDegrees` (Dying needs 2), `becomes` (Dying stabilises to
  Incapacitated), and `blockedBy` (Staggered waits for nothing worse; nothing recovers
  while Dead). Only a **directly applied** condition offers one — a bundled member goes
  when its umbrella does. The roll is the trait's own roll builder against the DC, so
  its number is the sheet's, penalties and all; the spec carries `recovers`, and a
  made one sheds one instance (`decrement_condition`, so a Hit stack peels off one) via
  `apply_recovery`. The **cadence** (end of turn, once a minute) is mostly the table's:
  there is no turn clock, and the die is clicked when the time comes. The exception is
  the GM's **End turn**, which rolls every `end_of_turn` check on the creature (see [the
  actions notes](actions.md#end-of-turn)). Who applies the
  result is whoever owns the creature — see [the session notes](session.md).
- **Some conditions have an amount, and some are temporary** (combat maneuvers). An
  `AppliedCondition` may carry `amount` (All-Out Attack −3) and `temporary` (it lasts
  until the start of the bearer's next turn); both stay out of a save at their defaults,
  so older saves load unchanged, and undo carries them because a step is a whole
  `to_dict`. What an amount *does* is the record's: `amountMods` lists `{trait, sign,
  parameter?}` — a resistance key such as `DEF`, or `system.trait_keys.attack_check` for
  attack checks — and `defaultAmount` sizes an instance applied without one (Defending's
  +2). `tone` (`benefit` / `hindrance`, default hindrance, so nothing old changes colour)
  is the chip's colour and the sign the shown name carries; `temporary: true` makes a
  record temporary from wherever it is applied, and `apply_condition(temporary=True)`
  makes any condition so (a feint's Vulnerable). Re-applying replaces the amount rather
  than stacking, and an instance stays temporary only if both old and new are — a lasting
  Vulnerable is not shortened by a temporary one. The shown name ("All-Out Attack -3",
  "Aided Attack (Thug) +5") is `core.rules.applied_condition_name`, which the UI's
  `condition_display_name` delegates to, so a dot's tooltip names a condition exactly as
  its chip does. Three readers fold the amounts in, all display-only: the Defense row's
  `resistance_condition_effect` (a `delta` after the halve/zero `op`, so Vulnerable +
  Defending on 8 is 6), `defense_class_in_play` (the Defense Class pin; `defense_class`
  stays the build's number), and the attack check `power_rolls` builds
  (`condition_attack_check_mod`, flagged by `EffectRollNumbers.attack_check`, so a
  Perception-range effect with no attack check is untouched, and a Linked group's one
  check takes it once). `temporary_marks` says per trait key whether the temporary ones
  leave it better or worse (an override such as halving is always worse; a net zero is
  not marked), and `clear_temporary` removes every temporary instance, umbrella members
  included, returning what went. The maneuvers' records live in the `maneuver` group, so
  the "+" menu offers them.
- **Some conditions count only against one creature** (`opposing`, phase 8 of the
  combat maneuvers: Aided Attack, Aided Defense, Feinted, Tricked, Trick Advantage). The
  parameter (a `character_ref`, "Against") names that creature, and the record's
  `opposing` block (`OpposingRule`) says what the condition does in an attack between
  the two: `attack` / `defense` times the amount on the bearer's check or Defense,
  `defenseAs` (Feinted judges the bearer's Defense as if Vulnerable), and `spentBy` —
  `attacking` (the bearer's next attack on that creature) or `attacked` (its next attack
  on the bearer). Nothing on a sheet moves for one: no `amountMods`, no dot. The GM's
  aimed-attack judge reads both sides (`core.rules.opposing_effects`,
  `defense_against`) and takes the counted instances off, hit or miss
  (`opposing_spent`, by parameter). They are not temporary — the user's call: a
  start-of-turn clear would take a feint away before the feinter's next attack — so
  "Clear temporary" leaves them. The match is by the **name** the GM's board shows
  (numbered namesakes included), the same names `{source}` uses.
- **Holding** (phase 9) is a grab's mark on the grabber: a lasting (not temporary)
  umbrella of Hindered and Vulnerable whose parameter names the creature held — a hold
  outlasts a turn, so a temporary one would be cleared at the start of the grabber's
  turn, which is when they keep it up. Its ✕ is letting go; the held creature's
  successful Escape takes it off (by parameter) along with their own Restrained or
  Bound. Two holds at once share the members' provenance, so letting go of one takes
  both bundles' Hindered and Vulnerable (a debt).
- **The roller puts the maneuvers' ones on** (phase 5). An attack rolled with All-Out or
  Defensive Attack on the roller's lane raises `condition-requested` with
  `((condition_id, amount), …)` once the die has settled; the Conditions block's
  `take_conditions` applies each temporary, through `apply_condition`, so undo and the
  player's snapshot to the GM come with it. See [the roller notes](dice-and-rolling.md).
  The self actions (phase 6) send `ConditionChange`s on the same topic — Defending,
  Prone (lasting), Stand's temporary Hindered and Prone *off* — which
  `core.rules.apply_condition_changes` applies (a removal takes every directly applied
  instance, with its members). A made Stand check sheds Prone through `recovers`, and a
  record with no `recoveryCheck` needs one degree (`recovery_succeeded`).
- **The GM judges an aimed attack against the Defense in play**
  (`derived.defense_class_in_play`), so Vulnerable, All-Out Attack and Defending all move
  the DC the attack has to beat (the aimed attack, the area window's primary, the team
  attack).
- **The sheet shows which numbers are borrowed** (combat maneuvers, phase 2). Every stat
  a temporary condition moves gets a **dot** (`ui/stat_mark.py`): green when
  `temporary_marks` says the temporaries leave it higher, red when lower, the sources in
  its tooltip, sized by the `mark.size` token. Two forms. A table cell (the Resistances
  and Skills totals) is marked with `mark_item`, which writes `MARK_ROLE`, and
  `StatMarkDelegate` (installed on the Total column) paints the dot in the cell's corner
  *over* its contents, so a dot costs the column no width and moves no text; call it after
  whatever writes the cell's tooltip, since it appends to it. A label readout uses the
  `StatMark` widget, hidden-but-room-keeping (`retainSizeWhenHidden`): the simple sheet's
  stat boxes (the room mirrored on the left, so the number stays centred) and lines, and a
  card's roll line. Which roll line is decided in `core`: `RollSpec.marks` names the trait
  keys whose condition mods a spec folds in (an attack check carries
  `trait_keys.attack_check`; a Perception-range effect's check does not). It is local
  only, never in `to_dict`, and `compare=False`, so a spec rebuilt off the wire still
  equals the roll it was. `RollsFooter(marks=temporary_marks(...))` draws it; the cards
  are rebuilt on every condition change (`FACTS_CHANGED`), so a footer holds no empty
  room for a dot. A skill row is marked by its own key first, then its skill's name.
  **The overlay's colour follows its sign now:** `ConditionEffect.better` (a net rise
  with no halving and no lost trait) tints a total green rather than the penalty red
  every overlay used to be, so Defending's +2 Defense reads as the help it is.
- **Chips wear their tone.** `condition_tone_token(record)` (`tint.better` for a benefit,
  `tint.worse` otherwise) colours every chip: the GM cards' `_ConditionChip(tone=…)`, the
  simple sheet's, and on the sheet's own block any benefit or temporary chip (a lasting
  hindrance keeps the plain panel it always had). A **temporary** chip also gets a visible
  ✕ beside the right-click (the user's call: it is the condition taken off every turn,
  often one at a time), and its tooltip says it is temporary. **"Clear temporary"** is
  shown only while something temporary is on the creature: in the Conditions block's
  header (`clear_temporary_conditions`, one undo step, plus a history line from
  `clear_temporary_note` on `noteRequested`), the simple sheet's Conditions view (through
  the same funnel), and on the GM's NPC, player and Scene cards — **on a line of its own
  under the chips**, never in their flow, because `condition_names()` reads every widget
  in the flow as a condition. An NPC's clear changes the GM's model and replays onto an
  open NPC sheet with `note=False` (the GM window writes one line naming the creature); a
  player's goes to their app as a `ClearTemporary` command (protocol v20), whose sheet
  writes the line. The GM's End turn does not clear them (they last to the *start* of the
  next turn).
- **The "+" menu asks for an amount** when applying one by hand needs it:
  `condition_asks_amount` (has `amountMods`, no `defaultAmount` — All-Out and Defensive
  Attack) makes `prompt_condition_amount` ask, 1 to the record's `maxAmount` (5 for the
  maneuvers; 20 when unstated). Defending's +2 is applied as it comes. The prompt is a
  second call after `prompt_condition_parameter` in every "+" (the sheet's, and the GM's
  on a player and an NPC card), whose `applyConditionRequested` now carries
  `(key, id, parameter, amount)`; `apply_condition_by_id` takes `amount` and `temporary`.


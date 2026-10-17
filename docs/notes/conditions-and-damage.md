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
  `apply_recovery`. The **cadence** (end of turn, once a minute) stays the table's:
  there is no turn clock, and the die is clicked when the time comes. Who applies the
  result is whoever owns the creature — see [the session notes](session.md).

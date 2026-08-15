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
  open menu. Recovery and turn economy are out of scope for now.
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

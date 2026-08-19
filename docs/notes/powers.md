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
- **A member of an array can be `dynamic`.** An ordinary alternate is mutually
  exclusive with its siblings and costs 1 point; a **Dynamic** one shares the array's
  point pool and runs *alongside* the array's other Dynamic members at reduced
  effectiveness, and costs 2 for exactly that reason (p101, p151). Making the array's
  **base** Dynamic instead "requires 1 Alternate Effect rank" — 1 point charged on top
  of its own full cost, not instead of it. So the flag is per **member**, not per array,
  and it exists at both levels an array does: `PowerEffectInstance.dynamic` for a
  power's own effects, `Power.dynamic` / `PowerGroup.dynamic` for a group of whole
  cards. `array_members_cost` is the one place the pooling arithmetic lives, shared by
  `power_gross_cost` and `node_cost` so the two levels cannot drift. Both numbers are
  data (`costValue` / `dynamicCostValue` on the `alternate_effect` record), never
  spelled in Python. It is *build* state, written only when set, so an array saved
  before it loads with every member ordinary and costs what it always did.
  **What is not modelled yet is the pool itself** — how many of the array's points each
  Dynamic member is currently assigned, and the reduced rank that buys. See
  `POWERS-AUDIT.md` §6K for the two designs that were weighed and why it is its own job.
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
- **Four things buy a whole sub-build, and each states its budget as a number.** Summon's
  minion is built on `rank x 15` PP (p145), Variable gives `rank x 5` Variable Power
  Points (p148), Affliction's Empowering remakes the target on `rank x 15` (p110), and
  Morph's Metamorph gives one alternate trait set **per rank**, each worth *the wielder's
  own point total* (p136). The first two are `points_per_rank` readouts in
  `effect_readouts.json`; the last two are modifier `noteTemplate` lines. Metamorph is the
  odd one and the reason `noteValues` exists: its budget is not a multiple of any rank, so
  the number has to come from `power_points_spent` — which is why `costs` now sits above
  `powers_terms` in the rules DAG. **What is still not modelled is the sub-build itself** —
  nothing holds the minion, the alternate form or the configured Variable points, so
  nothing checks a build against the budget it prints. See `POWERS-AUDIT.md` §6M.
- **`noteValues` answers what a rank cannot.** A modifier's `noteTemplate` could only ever
  interpolate `{n}` (the effect's rank times `notePerRank`); a `noteValues` entry names a
  `kind` from the `NOTE_VALUE_KINDS` registry instead. `doubling` is Multiple Minions,
  whose count doubles per rank *of the extra* rather than growing with the effect; and
  `character_points` is Metamorph's. A handler returning `None` — `character_points` with
  no character open — leaves the placeholder to be stripped, so the sentence reads without
  the number rather than claiming a zero.
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
  Transformed. The gated widget is **built and hidden**, never omitted, because rebuilding
  the form from inside the very combo whose change triggered it is how Qt teardown bugs
  start; closing a gate drops the stored value and opening it re-seeds the field's default
  (`_config_seeders`), so the spin box and the model never disagree. Separately, a `select`
  field may name a `source` instead of listing options: `CONFIG_OPTION_SOURCES` lives in
  **core**, not beside the constructor's widgets, so the picker and the game-terms readout
  resolve an id to a name through the one call (`config_source_options`) and cannot drift.
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
- **Rank as a dial.** `effect_effective_rank` reads `effect_current_rank`, so an effect
  turned down is turned down everywhere its rank resolves — the save DC, the measures,
  the trait boost. `effect_build_rank` is the bought rank beside it, and validation reads
  *that*: a Power Level cap is a statement about the build, and a sheet that passed its
  check by turning a dial down would be checking nothing. Cost asks neither. A boost
  follows the dial only down the single-target fallback (`boost_allocations(..., live=True)`)
  — an allocation's rows each carry a rank the player wrote down, and there is no honest
  way to turn "3 ranks of Stealth and 1 of Treatment" half off; the effects that carry
  rows are the ones whose rank *is* their allocation, which is exactly where the
  constructor declines to offer a dial. Which effects get one is `rank_dial` on the
  build, plus every size effect for free (see [Size and movement](size-and-movement.md)).
- **Extended settings is a *power*-level panel over *effect*-level flags.** All three of
  the above, plus `size_scales_damage`, are stored per effect — that is the level they
  apply at, and it is what lets `core` read them without a `Power` in hand — while the
  constructor drives every effect in the power from one checkbox each. Each row hides
  itself when the build has nothing it could apply to (`_resisted_effects`,
  `_dialable_effects`) and the section hides when every row has. An effect dropped on the
  canvas **inherits** the switches rather than its own defaults, or turning one off and
  then adding an effect would quietly turn it back on.
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
- **Runtime is saved.** It used to be the other way round — every runtime flag was
  left out of `to_dict()` on the argument that what is switched on is not part of the
  build — and the size ladder is what broke it: a Growth 3 *held* at Large is a
  standing decision about the character that four of the sheet's numbers hang off,
  and reopening the file at Gargantuan silently changed them. So
  `activated`/`item_present`/`array_active`, `PowerGroup.active_child_id` and each
  effect's `toggled_on`/`suppressed`/`current_rank` all round-trip now. Four things
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
  of cutting. Every runtime setter ends in `_rebuild_list()` (flipping one power
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

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
- `core.components.py` is an ECS-style split: effect *instances* are entities;
  the frozen **components** describing behaviour are the base effect's parsed
  `Integration` (a `statIntegration` `pattern` — `passive_permanent`,
  `passive_toggle`, `instant_action`, `resource_pool` — plus an optional
  `TraitBoost` for Enhanced-Trait / Protection), and per-instance **gate kinds**
  derived from a flaw's `gate` tag (`activation`, `removable`, `toggle`,
  `limited`). The *systems* reading these — `effect_is_active`,
  `power_trait_bonuses`, `effective_ability`, … — live in `rules`.
- **Cost** (`rules`): *how* an effect is priced is data — its `baseCostMode`
  picks a handler out of the `BASE_COST_KINDS` registry in `powers_cost`, and a
  mod registers another rather than editing that module. The default `flat` is
  `ceil` of net per-rank cost × rank (with M&M's sub-1-PP/rank fraction rule)
  plus flat modifiers; `power_total_cost` folds in the structure.
  `effect_cost_formula` renders the human-readable breakdown. All numbers are
  data-driven (`base_cost_value`, modifier `cost_value`, config `cost_value`
  overrides) — never hardcoded.
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
  passive bonus currently applies (`effect_is_active`). The UI drives all of a
  power's gates from one "Active" switch.
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
- **Validation** (warnings for now): `power_pl_violations` (per-power attack +
  effect-rank / auto-hit rank caps, read against the wielder),
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

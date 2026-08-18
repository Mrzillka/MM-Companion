# Size and movement

Matters when touching size, speed, or a movement effect.

Working notes for MM-Companion, split out of [CLAUDE.md](../../CLAUDE.md).

Size used to be a full data model with one live wire (ground speed) and a label. It is a
**trait source** now, and movement reconciles rather than accumulating. Both are
data-first; nothing below names a trait, an effect or a column in Python.

- **`core/rules/size.py` is its own module, and it has to be.** `derived` imports it, and
  `movement` reaches `powers_cost`, which imports `derived` back — so leaving the size
  math in `movement` is an import cycle at startup, not a style question. It may import
  only `..character`, `..data_loader`, `..powers`, `.appliers` and `.runtime`. For the
  same reason `size_shift` reads the **bought** rank rather than the effective one: an
  effective rank asks `effective_ability`, which asks what is standing on the sheet,
  which now includes what size grants. (`build_contributions` uses the bought rank for
  the same reason.) The DAG is `appliers` → `runtime` → **`size`** → `derived` →
  `powers_cost` → …
- **Which trait each Size Table column modifies is `sizeEffects` in
  `measurements.json`** — Defence, Toughness, Intimidation, Stealth. `size_contributions`
  emits one `TraitContribution` per non-zero column into `derived.trait_contributions`,
  so it flows for free into every total, roll, pin, GM chip and PL check. The defense
  column targets **`DEF`, not `DODGE`**: Dodge derives from Defence, so one contribution
  reaches the Dodge row, the Defense DC and the card — and targeting Dodge as well would
  double it.
- **Zero is never emitted, and a Medium character emits nothing at all.** A `TraitBonus`
  is a dataclass instance and therefore always truthy, so `apply_stat_effects`'
  `if not bonus` is always False: a 0 would paint a green `→ N` on every Medium sheet's
  Defence and Toughness, and `SkillModifiers.has_flat_modifier` is `grants is not None`,
  which would switch the Skills block's "+" column on for everybody and force a rebuild.
- **A bonus is no longer always a bonus.** `stat_table.bonus_tint(amount)` picks the
  colour from the sign and both Total columns format `:+d`, or a large character's
  −1 Defence reads green as an improvement. `WORSE_TINT` is its own name beside
  `CONDITION_TINT` even though they resolve to the same token today: one is a passing
  state, the other is what the character is.
- **A Power Level cap moves by exactly what size moved in its inputs**, so being large is
  never paid for twice. `size_resistance_shift` mirrors the resistance *derivation*
  rather than the contribution list — the contribution is on Defence while the cap is
  written against Dodge, and a naïve "sum the size mods on the pair" finds only
  Toughness, raises the cap, and hands a large character a free point of defence. Done
  right, the Dodge + Toughness pair cancels to zero all by itself.
- **The Damage column is not a trait, and not Strength.** It raises the *effective rank*
  of an effect that forces a resistance (`effect_size_rank_shift`, folded into
  `effect_effective_rank`), so it reaches the save DC and the PL cap together and leaves
  carrying capacity and the Strength skills alone. Which column does it is
  `sizeRankColumn`; which effects it reaches is `resistance_dc_base is not None`, the
  same question `power_pl_violations` already asks. Cost is unmoved — nobody pays for
  being large.
- **And it is a switch, because a giant's fist is not a giant's laser.** The Power
  Constructor's **Extended settings** section (`window._build_extended_row`, mounted with
  the other whole-power facts above the cost bar) carries one checkbox, on by default.
  It appears only once the build has an effect it could apply to — the bargain
  `PowerModeBar` already makes — and its note says what *this* wielder's size is worth.
  The flag itself is `PowerEffectInstance.size_scales_damage`, on the effect rather than
  the power: that is the level it applies at, it is the journey `attack_skill` already
  made, and it costs no signatures (`effect_effective_rank` has no power to read, and
  threading one through it, `effect_stat_rows` and `effect_roll_numbers` would touch a
  dozen call sites including the constructor's own live preview). Written only when
  false, so old saves are byte-identical and come up with it on. An effect dropped on the
  canvas **inherits the checkbox**, not its own default, or turning it off and then
  adding an effect would quietly turn it back on.
- **Growth is a ladder, not a leap** — a Growth 3 can be *held* at Large, at Huge or at
  Gargantuan, and which is a mid-round decision. The state is
  `PowerEffectInstance.current_rank`: **runtime**, like `toggled_on` beside it — and
  saved with the build, like `toggled_on` is now (see "Runtime is saved" in [The powers layer](powers.md)). `None` means "all the way up", so
  an effect nobody has dialled behaves exactly as it always did and a bought rank edited
  *down* later re-clamps rather than running at a rank it no longer has — that clamp is
  `rules.effect_current_rank`, which `size_shift` and `_readout_size_table` both read.
  **Cost never asks it**: what a power is worth is what it was bought at, and dialling
  one down mid-fight refunds nothing. The field is generic on the effect, and **so is
  the dial now**: any effect whose build ticks `rank_dial` gets one, and
  `effect_effective_rank` reads `effect_current_rank` rather than the bought rank, so a
  Damage 10 fired at 5 forces a save against 5 (see "Rank as a dial" in
  [The powers layer](powers.md)). Validation reads `effect_build_rank` instead, for the
  reason cost does.
- **The dial's notches are `size_steps`, and they name sizes rather than ranks.** One
  `SizeStep` per rank of a size effect — `category` being what the *wielder becomes*
  there, read against their bought size exactly as the card's readout is, because "Huge"
  is the thing being chosen while "rank 2" is an accounting fact the card already prints.
  An effect has rungs because the ruleset gave it a `size_table` readout
  (`SIZE_READOUT_KIND`), so nothing here names Growth or Shrinking and a mod's own size
  effect gets the dial for free. Two rules that are easy to re-break: ranks the Size
  Table **clamps** fold into the step that first reached them (`last_rank` closes the
  span, so a Colossal character's Growth 4 spends four ranks reading "Awesome"), and
  *current* is gated on the power being **live on the character** as well as
  `effect_is_active` — an array alternate nobody picked answers that second question
  happily, since `array_active` is a flag nothing maintains and only `live_powers` reads
  the array's `active_child_id`. That pair is `effect_stands`, split out of `size_steps`
  because the dial has to position itself by the same answer the ladder lit a rung by.
  The dial spends the span rather than collapsing it: it carries a notch per **rank**,
  labelled from these steps, so the handle can stop wherever the player puts it and
  simply repeats the category where the table ran out.
- **`_RankDial` is the slider on the card** (`ui/sections/powers.py`), one notch per
  rank from `0` upwards, under the effect's term grid and above the dice footer with the
  rest of the mid-play controls. It replaced a strip of checkable buttons, one per rung:
  the strip could not serve an ordinary Damage (ten buttons reading "Rank 1"…"Rank 10"
  is not a control), and the two would have been separate answers to one question. Five
  things it does that a plain slider does not. **Zero is off, and off is where it sits
  while the power is** — the dial says where the power *is*, and off is nowhere, not
  rank 1. **A move does whatever a click on the card body would have done, then lands
  where it was asked**: from zero it wakes the power at that notch (flipping the
  switches, or becoming the array's live alternate), so dormant → Huge is one gesture,
  and **sliding back to zero switches the power off** — the dial is a whole control, not
  one that can only turn a power on. The one exception is an array's *live* member,
  where a card click is deliberately a no-op, so zero there just puts the handle back.
  It **commits on release**, never per tick: every runtime setter ends in
  `_rebuild_list`, so a slider that wrote on each notch would delete itself under the
  player's thumb — `valueChanged` moves only the label while `isSliderDown`, and a
  keyboard or groove step (which leaves the handle up) commits at once. It **stays live
  in the locked sheet** and emits `runtimeChanged`, never `changed`, like every other
  card switch — which, the notch being saved, does now mark the sheet unwritten (see
  "Runtime is saved" in [The powers layer](powers.md)). And a **single-rank effect gets
  no dial at all**: a Growth 1's one notch *is* the card's own on/off switch, and a
  slider of one would be a second way to press it. **How the handle is positioned
  differs by effect**, and has to: a size effect reads `effect_stands`, since a Growth
  that is switched off is nowhere — but an *instant* effect never stands at all
  (`effect_is_active` is False for a Damage by pattern), so asking it there would peg
  every blast card at Off. Those read whether the card is simply switched on. Screenshot
  it with `driver.py size-ladder`, and the notch coming back off disk with `driver.py
  size-ladder-reload`.
- **The slider is `NoFocus`, and `_rebuild_list` runs inside `preserved_scroll`.** Two
  halves of one bug: every runtime setter rebuilds the whole card tree, so the block is
  briefly empty *and* whatever held focus inside it is destroyed — Qt hands focus to the
  next widget in the tab order, which is a table in some other block, and a `QScrollArea`
  scrolls to show a child that has just taken focus. The page jumped away from the card
  under the cursor. `NoFocus` closes the cause (the slider is destroyed by its own
  commit, so focus could never usefully rest there, and the card body it sits on is not
  focusable either) — and it must be set **after** `guard_wheel`, which asks for
  `StrongFocus` so a focused widget keeps its own wheel; `widgets.preserved_scroll` closes the rest — it restores the bar **twice**,
  now and on the next turn of the event loop, because the range is only recomputed on the
  following layout pass and an immediate `setValue` is clamped by the stale one.
  `EquipmentSection._rebuild_list` takes it too: wearing an item is the same rebuild.
- **The card's size readout is relative to the character** (`_readout_size_table`). It
  used to compute an absolute row from `sign × rank` and never look at the wielder, so a
  Small character's Growth 2 printed "Huge" while the sheet correctly said "Large". It
  shows `base_size_rank + sign × rank` and each column as the **delta** against the base
  row — which is also the only reading that stays right past the clamp, where the table
  stops being linear. It reads the **dialled** rank, so the card's Size row and the
  System block's Size line can never name two different categories; in the constructor
  nothing is dialled, so the build preview is unchanged. `READOUT_KINDS` handlers therefore take
  `(readout, effect, game_data, char)`; a three-argument handler from a *workspace* mod
  is retried without the character, guarded on `exc.__traceback__.tb_next is None` so a
  `TypeError` raised *inside* a four-argument handler is never swallowed and re-run.
- **The Speed readout is one line per mode, not per source.** An effect names its own
  `measure.mode` in `effects.json` (defaulting to its id, so an unnamed one is its own
  mode and behaves as before) and Speed names the ground mode, so it feeds the line the
  character walks on rather than sitting beside it. Each grant is lifted into a
  `TraitContribution` keyed by mode and netted by `resolve_contributions` — the *same*
  resolver, which is what keeps the equipment rule intact while summing: powers sum with
  each other, gear maxes within itself, the better group wins. Summing gear flat into a
  power's line would have quietly repealed `GROUP_EQUIPMENT`/`STACK_MAX`.
  `TraitBonus.sources` becomes `SpeedLine.sources`, which is why `SpeedWidget` is a label
  per row now: the caption can only name the mode.
- **Every mode is a sum, the ground one included.** A mode's rank is everything granting
  it added together; ground is that same sum started from `base_ground_speed_rank` (the
  data's base rank, shifted by the Size Table's `speedMod`), so base + Speed + Striding
  is what the character walks at. It was a `max(base, grants)` once — which meant the
  first rank or two of Speed a character bought *did nothing at all*, a Medium walker
  with Speed 1 reading exactly as fast as one with none. The **size** modifier still
  folds into `base_ground_speed_rank` rather than joining as a grant (which is why
  `speedMod` is absent from `sizeEffects`): it is the base the grants add to, and the
  `penalty_removed` cancellation has to reach it there. `lines[0]` stays the ground line
  — `condition_speed_lines` overlays that index.
- **A line names its mode and nothing else** — `Ground speed` (`movement.groundLabel`),
  `Flight`, `Burrowing`. It carried the netted rank once (`"Flight 10"`), which was a
  number belonging to no one source; the ranks that fed it are on `SpeedLine.sources`,
  which is the hover. `ground_speed_rank` is the resolved ground line as a number, and
  is what a mode expressed *relative* to walking (Wall-Crawling's "full ground speed")
  is measured against — `base_ground_speed_rank` would have a speedster crawling at the
  pace they walk without their power.
- **Only the ground mode runs.** `speed_columns(…, ground=False)` drops the run column,
  since running is a ground manoeuvre: a flier moves and dashes and has nothing to put
  in a third one. It is the ruleset's call (`movement.runIsGroundOnly`), which is why
  the result is a variable-length tuple rather than always a triple — unpacking it as
  `walk, dash, run` is the thing that breaks. `MovementModesWidget` renders its
  specialised modes through the same call, so `Swinging: 30 ft / 60 ft` reads like the
  Flight line above it rather than like a different quantity.
- Do **not** route the four movement effects through `STAT_APPLIERS`. That walk uses the
  *effective* rank where an applier gets the bought one, a `TraitContribution` carries no
  rank or effect identity (so `"Glider 6"` could not be reconstructed), and giving them a
  `statIntegration.target` would flip `_affects_movement` and drag them into
  `movement_mode_lines`, a different readout entirely. Two producers, one reducer.
- **Normal Speed is the first thing ever to read `CATEGORY_PENALTY`.** Shrinking's extra
  says "your speed isn't reduced while shrunk", which is a penalty being lifted rather
  than a bonus granted — the whole reason the two appliers are separate.
  `base_ground_speed_rank` clamps the cancellation at zero and applies it only to a
  *negative* modifier, so lifting a penalty leaves you at your normal pace and no faster,
  and a base-Small character's own −1 survives their Shrinking being cancelled.
- Elongation's **Slithering** and **Swinging** are still prose. They grant Enhanced
  Movement *modes*, whose rate comes from an `alloc_option` tier (often relative to
  ground speed), and a flat `TraitContribution` cannot express that. They need a grant
  shape of their own — noted here so it is not silently dropped.
- Screenshot the constructor section with `driver.py constructor-extended`.

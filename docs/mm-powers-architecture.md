# Mutants & Masterminds (4th Edition) — Powers Architecture

> **Note on source & copyright:** Same basis as the other reference files — original,
> paraphrased summary of the *functional* rules (cost formulas, categories, integration
> behavior), not a reproduction of the rulebook's text. This file is the architecture guide;
> `effects.json` holds the 42 base effects, `modifiers.json` holds the 37 general-purpose
> extras + 24 flaws that apply broadly, and `effect_modifiers.json` holds ~194 extras/flaws
> that are specific to one particular effect (e.g. Damage's Strength-Based, Flight's Rocket).
> A power-builder UI should offer both pools together for whichever effect the player picked —
> see `effect_modifiers.json`'s `usageNote` for details. Read `mm-core-mechanics.md` first for
> Power Points/Power Level.

---

## 1. The core idea: a Power is an assembled object, not a catalog entry

Unlike skills and advantages, there's no fixed list of "powers" to pick from — a player builds
a power out of parts:

```
Power
 └─ one or more Effects (from effects.json), each with:
     ├─ rank
     ├─ extras[]   (from modifiers.json, category="extra")
     ├─ flaws[]    (from modifiers.json, category="flaw")
     ├─ descriptors[]   (free-text flavor: "fire", "magic", "technological"...)
     └─ configuration    (effect-specific choices, e.g. which trait Enhanced Trait boosts,
                           which resistance an Affliction targets)
```

So your data model needs exactly three catalogs (this file's companions) plus a **Power**
record type that references them — there's no "powers.json" of named powers, because
"Blast," "Invisibility," and "Super-Strength" are just labels a player puts on a particular
effect+modifier combination.

---

## 2. Cost formula

```
effect_cost_per_rank = base_cost_per_rank + sum(extra costs per rank) - sum(flaw costs per rank)
effect_total_cost    = (effect_cost_per_rank * rank) + sum(flat extra costs) - sum(flat flaw costs)
power_gross_cost     = sum(effect_total_cost for every effect in the power)
power_total_cost     = power_gross_cost + sum(power-scope modifiers, priced from that gross)
```

Minimum cost per rank is 1 Power Point — flaws can't push a per-rank cost below that. This is
the number that gets deducted from the character's Power Point pool (see
`mm-core-mechanics.md` §7) when the power is added to the character.

**A power-scope modifier** is one the rules price against the whole power rather than any one
effect, marked `costScope: "power"` in `modifiers.json`. Removable is the only one: it is worth
its value (1 / 2 / 4 by tier) **per 5 points of the power's final cost, rounded up**, so a
98-point suit of armour is discounted 20 points to 78. Every effect-level bucket skips such a
modifier, and only the costliest selection of it counts however many effects carry a copy —
the flaw applies once to the power. The result is floored at 1 Power Point.

---

## 3. Effect Type categories (behavioral defaults)

Every effect in `effects.json` has an `effectType`. Knowing the type tells you the *default*
action/duration pattern before any modifiers are applied — useful for validating a
user-configured effect or pre-filling sensible defaults in a builder UI.

`effectType` is a **catalog taxonomy**: it groups the Power Constructor's effect palette
and seeds the Type game-term row. It is *not* what makes an effect an attack — the
`attack` modifier is (see below), and that modifier overrides the effective Type row. So
an offensive Control effect (Create, Move Object, Nullify, Transmute) files under Control
in the palette while its card reads Type "Attack".

| Effect Type | Typical action | Typical duration | Typical role |
|---|---|---|---|
| Attack | Standard | Instant | Offensive, carries the `attack` modifier implicitly, always resistible |
| Defense | None (or Free if toggleable) | Permanent (or Sustained) | Personal protection, usually passive |
| Control | Standard | Sustained | Manipulating the environment/objects/others, requires upkeep |
| General | Varies per effect | Varies per effect | Doesn't fit the other categories |
| Movement | Free (to activate) | Sustained | Grants/improves a movement mode; still needs a Move action to actually move |
| Sensory | Free or None | Sustained or Permanent | Enhances, grants, or fools senses |
| Alteration | Free | Sustained (or Permanent) | Transforms the user's body/form |

### Attacking is a modifier, not a type

"This effect resolves with an attack roll" is a **modifier**, not a property of the base
record. The `attack` extra (`modifiers.json`, +0 points) carries `grantsAttack: true` and
overrides `check` to `"Attack vs. Defense"` and `effectType` to `"Attack"`.

An attacking effect lists it in `implicitModifiers`, so it is part of the effect's own
definition rather than a player's choice: `core.rules` folds implicit modifiers into the
effect's **base** game terms, untinted, before any attached modifier. A Damage therefore
reads exactly as if the check were written on the record — no tint, no note, no chip, and
no cost, since an implicit modifier never sits on the instance. Only `overrides` and
`grantsAttack` apply; stepping, check bonuses and check notes are for attached modifiers.

The payoff is modularity: any other effect can take the same extra from the palette and
become deliverable as an attack, gaining the Check row, the attack-skill picker, and the
attack-plus-rank PL cap. `core.rules.effect_makes_attack` reads the resolved
`EffectImpact.grants_attack` rather than sniffing the check prose — so Deflect's
"Deflect vs. Attack" is correctly *not* an attack, and `dropsCheck` (Perception Range)
still cancels the roll.

Note the PL caps remain gated on `resistanceDcBase`: an attack with no save DC has no
resisted rank to cap, so a Flight + Attack stays out of PL scope.

---

## 4. Single-effect, multi-effect, Linked, and Arrays

- **Single-effect power**: one effect, its own extras/flaws. (`Blast` = Damage + Ranged.)
- **Multi-effect power**: several effects under one theme, normally used **independently**
  of each other (a suit of armor's Flight and its Protection don't have to activate together).
- **Linked effects**: two or more effects forced to activate together, on the same action, as
  one combined unit. Linked effects must share the same Range; costs simply add together.
- **Power Array (Alternate Effect)**: a base power plus one or more *alternate* configurations
  that share the same point pool — only one is active at a time, switching is a free action.
  Each alternate effect costs a flat 1-2 points (from `modifiers.json`) regardless of its own
  rank, as long as its own total cost doesn't exceed the base power's cost. Permanent-duration
  effects can't be array members (they can't be switched off).
- **Dynamic Alternate Effects**: an array member that is *not* mutually exclusive with its
  siblings. It shares the array's point pool with the array's other Dynamic members and runs
  **simultaneously** with them at reduced rank, reallocated once per turn as a free action.
  That is what its dearer price buys: a Dynamic alternate costs 2 points rather than 1, and
  making the array's *base* Dynamic costs one Alternate Effect rank on top of its own cost
  (p101, p151). Dynamic is therefore a per-**member** flag, not an array-wide mode:
  `PowerEffectInstance.dynamic` within a power, `Power.dynamic` / `PowerGroup.dynamic` within
  a group, both read by `array_members_cost`. Two Dynamic members are needed before the
  option does anything, which the book says outright and the app leaves to the player.

```
Power (array example: "elemental control")
 ├─ base:      Damage 8, Ranged                (fire bolt)
 ├─ alternate: Environment 4 (Heat), Ranged     (+1 pt flat)
 └─ alternate: Move Object 8, Ranged, Limited to metal   (+1 pt flat)
```

### Two levels: effects within a power, and a tree of powers

Linked and Array exist at **two levels**, both supported side by side:

- **Within a power** — a multi-effect `Power` carries a `structure`
  (`independent`/`linked`/`array`) governing how *its own effects* combine. This is the
  in-constructor mode bar (see `mm-powers-ui-design.md`), unchanged.
- **Between whole powers** — a character's `powers` is a **tree** of `PowerNode`: leaf
  `Power` cards and `PowerGroup` containers, which can **nest arbitrarily** (a group
  inside a group). A `PowerGroup` has a `mode` (`independent`/`linked`/`array`) and an
  ordered `children` list. The player builds the tree on the character sheet by dragging
  one card onto another (or onto a group's title bar) to combine them, and into a gap to
  reorder or move between groups — see `ui/sections/powers.py`.

  Cost recurses over the tree (`rules.node_cost`): an `independent`/`linked` group sums
  its children (linking is a +0 bundle); an `array` group pays its costliest child in
  full plus the flat `array_alternate_cost` per other child, so `powers_points_spent`
  folds in array pooling at any depth. `rules.node_display_cost` gives the per-card
  figure (a non-base array member shows only the flat point). Runtime: an `array` group's
  `active_child_id` names the one live member, and `rules.live_powers` walks the tree
  descending only into the active array branch so an unselected member's bonuses drop off
  the sheet (feeding `power_trait_bonuses`); the per-power on/off switch still drives
  `effect_is_active`, and a Linked group's members toggle together.

  This tree **supersedes** the older flat cross-power references (`Power.alternate_of` /
  `Power.linked_with`). Those fields remain only so a pre-tree save still loads:
  `character._migrate_flat_relations` folds each `alternate_of` cluster into an `array`
  group and each `linked_with` component into a `linked` group on load, then clears the
  dead fields so a re-save is group-only.

---

## 5. The critical part: how effects patch character stats

This is the piece that matters most for your app. Effects fall into a small number of
**integration patterns** based on their `duration` and `effectType`. `effects.json` encodes
this per effect as a `statIntegration` object so your engine doesn't need to hardcode
per-effect logic.

### Pattern A — `passive_permanent`
`duration: "Permanent"`, `action: "None"`. Always contributes its bonus, full stop, as long as
the power exists on the character sheet **and nothing is currently suppressing it**
(see §6). This is the default case for things like **Enhanced Trait**, **Protection**,
**Immunity**, **Enhanced Senses**, **Regeneration**.

### Pattern B — `passive_toggle`
`duration: "Sustained"` or `"Continuous"`. Contributes its bonus only while the character has
chosen to activate/maintain it. In a turn-by-turn combat tracker this means checking an
"active this turn" flag (upkept by the Sustain/Concentrate action); in a character-sheet
builder without round tracking, treat it as an on/off switch the player sets, defaulting to
**on**. Applies to effects like **Flight**, **Growth**, most **Movement** and **Alteration**
effects, and Enhanced Trait *if the Sustained extra was purchased* (see the worked example
below).

### Pattern C — `instant_action`
`duration: "Instant"` (or "Concentration" for a few sensory effects). Doesn't sit on the
character sheet as a standing bonus at all — it's invoked on demand via an action and resolves
immediately (Damage, Affliction, Healing, Teleport, Nullify...). Your engine should expose
these as "usable actions" rather than as passive modifiers to `character.derivedStats`.

### Pattern D — `resource_pool`
Effects like **Variable** don't modify a stat directly; they grant a pool of "sub-budget"
Power Points the player allocates at runtime to *other* effects. Model this as a separate
allocatable pool rather than a stat patch.

---

## 6. Worked example: Enhanced Trait

The case that shaped this whole layer — a power that *conditionally* patches other stats.
Enhanced Trait raises **several traits at once** out of one rank pool, and its base cost is
"As Trait": it costs whatever those traits cost to buy outright.

```json
{
  "effect": "enhanced_trait",
  "rank": 10,
  "config": {
    "traits": [
      { "trait": "STR",                "ranks": 2 },
      { "trait": "Treatment",          "ranks": 6 },
      { "trait": "Expertise::Stealth", "ranks": 2 }
    ]
  },
  "extras": [],
  "flaws": [ { "modifier": "limited_enhanced_trait" } ],
  "cost": 4
}
```

Reasoning — each row is priced at that trait's own rate from `costs.json`:

| Row | Rate | Cost |
| --- | --- | --- |
| Strength 2 | `ability_per_rank` = 2 PP/rank | 4 |
| Treatment 6 | `skill_ranks_per_pp` = 2 ranks/PP | 3 |
| Expertise: Stealth 2 | `skill_ranks_per_pp` = 2 ranks/PP | 1 |
| | | **8** |

### Qualified trait keys

`"Expertise::Stealth"` is a **qualified** trait key: a trait, then the row of it. The
separator and the row-id shapes are the character sheet's own
(`Character.skill_ranks`), so a granted row lands exactly where a bought one would and
nothing new had to be invented:

| Key | What it names |
| --- | --- |
| `STR`, `TOUGHNESS` | an ability or resistance — one row, nothing to qualify |
| `Stealth` | a whole skill, *every* row of it |
| `Expertise::Law` | one focus of a focused skill |
| `Stealth::spec::Urban` | one specialized (narrow, half-cost) rank pool |
| `Improved Critical::Sword` | an advantage bought for a subject |

`split_trait_key` (`core/rules/appliers.py`) is the one place the two halves come apart.
Every resolver over trait keys — `trait_category`, `trait_rate`, `trait_display_name` —
tries the whole key first and its base second (`trait_key_candidates`), so which list a
target lands in, what it is charged, and how it is printed can never be decided by
different rules. Unqualified keys behave exactly as they always did.

A qualified skill is priced by `skill_row_rate`, which is the **same** function
`skill_points_spent` prices the bought ranks with: a specialized pool cannot cost one
thing bought and another granted. An advantage's subject changes no price but does make
the row distinct, so *Improved Critical* can be granted twice for two attacks.

The eight skill ranks are summed as **fractions and rounded once**, exactly as
`skill_points_spent` pools the bought skills — which is what makes 1 PP buy two skill ranks
*split across two different skills* rather than one rank in each of two skills costing a
point apiece. Six ranks of Treatment plus two of Expertise is 4 PP together, never 3 + 1
rounded separately into 5.

Then the `-1 point per rank` Limited flaw **halves** the 8 to **4**. Per-rank modifiers on
an as-trait effect are read against the effect's *nominal* rate — its `baseCostValue`, the
2 PP a trait ordinarily costs a rank — and the result applied to the real total as a ratio,
because there is no single per-rank price to subtract from when Strength costs 2 a rank and
Stealth costs half of one in the same effect. Below 1 PP/rank the ratio follows the same
"1 point per `2 − net` ranks" rule the flat effects use, so a second flaw quarters the cost
rather than zeroing it. Flat modifiers are added after, and the total is floored at 1 PP.

The rank is not a budget here. Enhanced Trait declares `rankFollowsAllocation` in
`effects.json`, so its rank **is** the ranks the rows spend (`synced_effect_rank`), shown
read-only in the constructor and written back as the rows change; there is nothing to
overspend, and `power_allocation_violations` skips it. Enhanced Senses, Enhanced Movement,
Comprehend, Immunity and Feature keep the hand-set budget and the over-allocation warning
— for them the rank is what was *bought*, and the allocation spends it. For Enhanced Trait
the cost comes from the rows, so a second number would only be one more thing to keep level.

The one per-trait ceiling the rules do impose is an advantage's: most are not ranked at
all, a few cap at a fixed `maxRank`. `trait_rank_cap` states it once, the constructor's
rank spin stops there, and `power_trait_allocation_violations` warns if a stored row went
past it anyway. It warns rather than clamps: the row is on screen, and quietly charging for
fewer ranks than it shows would leave the cost line disagreeing with the rows above it.

**What the card shows.** The footer groups the priced rows by *kind* —
`(Abilities 4 + Skills 4) × 1/2 = 4 PP` — because a run of raw `1/2 + 1/2 + 1/2` terms
says nothing about why they came to a point. A subtotal that lands between points keeps its
fraction (`Skills 2 1/2`), since that half is real and pays for the next rank.
`effect_cost_breakdown` returns the per-row detail behind those subtotals, and the card
hangs it off the same label as a tooltip, so the number and its workings come from one
place.

`baseCostMode: "as_trait"` in `effects.json` is what selects this rule, out of the
`BASE_COST_KINDS` registry in `core/rules/powers_cost.py` — a mod registers another mode
rather than editing that module. `Reduced Trait` uses the same machinery from the other
side: it is a *flat* flaw with `costMode: "as_trait"` and its own trait rows, so it
discounts by whatever the lowered ranks would have cost.

**Standard configurations.** The rulebook also names about ninety ready-made powers and
lists what each is made of (PDF p236 by effect, p237 by name). They are shorthand, not new
rules — nobody says "Damage, Ranged", they say *Blast* — so `configurations.json` records
each as an assembly of the records above and `core/rules/configurations.py` turns one into
an ordinary `Power`. Two consequences worth stating: a built configuration is editable like
anything else and carries no marker of where it came from, and it costs whatever its pieces
cost. The `costNote` beside each is the *book's* printed price, kept as reference and as a
test oracle, never as an input to the arithmetic.

**Base cost from configuration.** Five effects have no single points-per-rank: Illusion
costs 1 per sense type it fools, sight counting as two, capped at 5 (PDF p131); Obscure
1 per sense or 2 per whole sense type, sight double, capped at 10 (p139); Remote Sensing
5 for the first sense type and 1 for each after, capped at 10 — sight again counting as
two, which is why sight Remote Sensing is 6 (p142); Transmute 2/3/4/5 by how broad its
source and result are (p147); and Environment, whose sub-effects cost 1 or 2 per rank
*each* and simply add up, with no ceiling (p124–125).

They are **not** a `BASE_COST_KINDS` mode, and the distinction is the point: they are
still charged flat, per rank, exactly like everything else — only *which number* differs.
So the number is what varies. An effect declares a `baseCostBy` block naming the config
fields that drive its price (`{ "fields": [...], "base": 0, "min": 1, "max": 5 }`), each
option in those fields carries its own `costValue`, and the cost is the sum of the chosen
options plus `base`, clamped into `[min, max]` — with `min` doubling as what an
unconfigured effect costs, so a freshly dropped card prices at its floor rather than at
zero. `effect_base_cost_value` in `powers_cost.py` is the single place the constant and
the configured cases are told apart; the per-rank cost, the flat total and the printed
formula all read it, so a configured Illusion cannot cost one thing and explain another.

Adding a sense type, an Environment condition or a whole new configured effect is
therefore a data edit with no Python behind it.

**Dynamic Alternate Effects are priced but the pool is not modelled.** Each member carries
its own `dynamic` flag, and `array_members_cost` charges `dynamicCostValue` (2) for a Dynamic
alternate and one Alternate Effect rank for a Dynamic base, at both levels an array exists
at — so a Dynamic array costs what the book prints. What does not exist is the **runtime
allocation**: how many of the array's points each Dynamic member currently holds, and the
reduced rank that buys ("2 Power Points assigned to it, but their Flight speed is then
limited to 1 rank", p101). Every Dynamic member still behaves at runtime exactly as an
ordinary alternate does. The blocker is that `effect_current_rank` takes only the effect, so
an allocation cannot reach the rank without threading the power (and its parent group) into
it; `POWERS-AUDIT.md` §6K records the two candidate designs.

**Backward compatibility.** An effect with no rows falls back to a single
`config["target"]` at the effect's full rank — which is how Protection's baked-in
`"TOUGHNESS"`, a shield's authored `{"target": "DEF"}` and every character saved before the
allocation existed keep working, unmigrated. See `boost_allocations` in `core/rules/runtime.py`.

A different flaw, a different consequence: the `removable` flaw (equipment tier) discounts
on the power's total cost (see `modifiers.json`) and attaches a condition — **the bonus
only applies while the equipment is present and not disabled.**

At stat-computation time:
```
function computeAttack(character):
    total = character.baseAbilities.attack   # bought normally with Power Points
    for power in character.powers:
        for effect in power.effects:
            if effect.type == "enhanced_trait" and effect.config.target == "combat.attack":
                if isEffectCurrentlyActive(effect, character):
                    total += effect.rank
    return total

function isEffectCurrentlyActive(effect, character):
    if effect.hasFlaw("removable") and not character.equipmentEquipped(effect.sourcePower):
        return false
    if effect.hasFlaw("activation") and not character.powerActivated(effect.sourcePower):
        return false
    if character.hasCondition("nullified", targeting=effect):
        return false
    if effect.duration in ("sustained", "continuous") and not character.powerToggledOn(effect.sourcePower):
        return false
    return true   # permanent, unflagged effects are just always on
```

Name an ability key, a skill name, a resistance key or an **advantage name** in a row and the
same function covers Enhanced Ability, Enhanced Skill, Enhanced Defense, Enhanced Resistance
and Enhanced Advantage uniformly — they are all just **Enhanced Trait** configured
differently, per the source material (`effects.json` keeps them as one effect entry with a
trait allocation rather than five separate effects). A row can raise all five in one power,
which is what the rules' own *Berserker Rage* configuration needs: *Enhanced Advantage:
Fearless 2; Enhanced Strength, Sustained; Reduced Defense 1*.

An advantage is a trait here like any other, but it is not a *number*: it totals nothing, so
`CATEGORY_ADVANTAGE` sits outside `NUMERIC_CATEGORIES` and the Advantages block reads it
(`granted_advantages`) rather than any total adding it. A granted advantage is paid for by
the power, so it never enters `advantage_points_spent` or the shared Heroic budget — the same
rule the ability boosts follow, where the boosted ranks are the power's cost and not the
ability's.

A note on skills: a row names the **base skill**, and a power's boost reaches every row of
that skill — each focus and specialized pool included. That is the documented behaviour of
`skill_bonus`, not an accident of the picker, so "Expertise" raises every Expertise focus
rather than asking which one.

**Power Level note:** the *combined* total (base + all active Enhanced Trait bonuses) must
still respect the Power Level caps from `mm-core-mechanics.md` §7. Validate that at
character-build time, not just at runtime — a Sustained Enhanced Trait requires the character's
un-enhanced rank to already sit far enough below the cap to leave room for the bonus.

---

### Effective rank vs. bought rank

Cost counts the **bought** rank; the resistance DC and the Power Level cap read the
**effective** one, which is the bought rank plus two things that are free at the till
because the character already paid for them elsewhere:

- an ability a modifier folds in (`addsAbility` — Strength-Based Damage picks up the
  wielder's Strength), and
- what the wielder's **size** is worth (`sizeRankColumn` in `measurements.json`), for an
  effect that forces a resistance and has not had `sizeScalesDamage` switched off. A
  giant's fist hits harder; a giant's laser does not, which is why it is a switch and
  not a rule. The Power Level cap shifts by the same amount, so being large is never
  paid for twice.

---

## 7. General suppression/interaction rules to model

A handful of cross-cutting mechanics affect whether *any* effect's bonus should currently
apply, regardless of which effect it is:

- **Nullify**: another character's effect can suppress yours at runtime (an opposed effect
  check, see `mm-core-mechanics.md`-adjacent Powers rules). Model as a temporary "suppressed"
  flag on the effect instance, separate from the character build.
- **Activation flaw**: the whole power must be "switched on" (a simple/standard action) before
  any of its effects — even Permanent ones — are usable. Track a per-power `activated: bool`.
- **Removable / Easily Removable / Equipment flaws**: the power's bonus only applies while an
  associated item is possessed and undamaged. Track a per-power `itemPresent: bool` (and
  optionally `itemCondition`).
- **Side Effect / Tiring / Unreliable flaws**: don't change *whether* the bonus applies, but
  should surface as "using this costs you X" or "this has a chance to fail" warnings in the UI
  rather than being silently ignored.
- **Limited flaw**: the bonus only applies under a specific condition (e.g. "only at night").
  Best modeled as a free-text condition your UI displays rather than something the engine can
  auto-evaluate — flag it for the player to self-apply.

All of these flags are **saved with the character**, written only when they differ from
the all-active default. They are not part of the point build and cost nothing, but which
powers are up and how far a Growth is dialled are decisions a player expects to find
again on reopening the sheet — so a toggle marks the sheet unwritten, and a file saved
before any of this still loads all-active.

---

## 8. Suggested schema summary

```
Effect (from effects.json)
├── id, name, effectType, action, range, duration, check, resistance, baseCost
├── baseCostValue: int                   // points per rank, or (as_trait) the NOMINAL rate
│                                        // the per-rank modifiers are read against
├── rankFollowsAllocation: bool          // the rank IS what the trait allocation spends,
│                                        // not a budget; omitted means a hand-set rank
├── baseCostMode: "flat" | "as_trait"    // how that is charged; omitted means flat. One
│                                        // handler per mode in `BASE_COST_KINDS` (§6)
├── configurableTarget: null | "trait"   // true for Enhanced Trait-style effects
├── personal: bool                       // works on its user alone - the rules' "Personal
│                                        // Range effect". Omitted means `range == "Personal"`;
│                                        // stated only where the Range parameter disagrees
│                                        // (Teleport's Range is "Rank"). Read through
│                                        // `effect_is_personal`
├── config: []                           // the effect's configurable qualities (§9). A
│                                        // `repeatable` field with a `trait` column and an
│                                        // `int` column is a TRAIT ALLOCATION: each row
│                                        // names a trait and the ranks put into it, spent
│                                        // out of the effect's own rank
├── implicitModifiers: []                // modifier ids the effect carries by definition;
│                                        // an attacking effect has ["attack"], which is what
│                                        // supplies its check (so its own `check` is null)
├── rangeDistance: {}                    // optional; overrides how far this effect reaches
│                                        // once its range is Ranged (see §10)
├── subBuild: {}                         // optional; a whole nested Character this effect
│                                        // buys, with its budget (Summon's minion) — §9
└── statIntegration: { pattern: "passive_permanent"|"passive_toggle"|"instant_action"|"resource_pool",
                        affects: "ability"|"skill"|"advantage"|"defense"|"resistance"|
                                 "movement"|"senses"|"none"|"special" }

Modifier (from modifiers.json)
├── id, name, category ("extra"|"flaw"), costFormula, costValue, flat (bool), ranked (bool)
├── costMode: "" | "as_trait"            // "" reads costValue; as_trait computes the
│                                        // magnitude from the modifier's own trait
│                                        // allocation (Reduced Trait, §6)
├── overrides: { range?, action?, duration?, check?, resistance?, effectType? }
├── grantsAttack (bool)                  // gives the effect its attack roll
├── dropsCheck (bool)                    // removes it again (Perception Range)
├── distanceRankBonus (int)              // distance ranks each rank adds to a Ranged
│                                        // reach (Extended Range's 1) — see §10
├── requiresCheck (bool)                 // using the effect calls for an extra roll first;
│                                        // gets its own row and a dice-footer line, with
│                                        // noteTemplate rendering it ("{trait} check, DC {dc}")
├── checkBonus, checkNote, stepField/stepBy, addsAbility, gate, hidden
├── subBuild: {}                        // optional; the same block an effect can carry,
│                                        // for a modifier that buys a nested Character
│                                        // (Morph's Metamorph forms) — §9
├── statIntegration: {}                 // optional, the same shape a base effect carries:
│                                        // what *taking this modifier* grants, read by the
│                                        // same appliers (Striding -> ranks of Speed).
│                                        // Worth its own rank when `ranked`, the host
│                                        // effect's otherwise.
└── description

PowerEffectInstance  (part of a character's Power)
├── effectId, rank, config{}, extras[]{modifierId, rank?}, flaws[]{modifierId, rank?}, descriptors[]
│   // a selection also carries appliesFrom/appliesTo — the rank *band* it covers, 0/0
│   // meaning every rank. Per-rank modifiers only: a flat charge costs the same over
│   // four ranks as over twelve, so a band there would change no number (§2)
│   // config holds a trait allocation as [{trait, ranks}, ...] where each trait is a
│   // key, optionally qualified with "::" to narrow it to one row (see §6); the legacy single
│   // {target: "STR"} shape is still read, at the effect's full rank (§6)
├── sizeScalesDamage (bool, default true) // whether the wielder's size raises this
│                                          // effect's rank (the constructor's Extended
│                                          // settings switch). Only ever reaches an effect
│                                          // that forces a resistance.
├── currentRank (int|null, default null)  // runtime, per §7: the rank the effect is
│                                          // *currently* held at, null meaning full.
│                                          // Feeds `effect_effective_rank`, so the save
│                                          // DC follows it; the card drives it from a
│                                          // slider (`_RankDial`). Never read by cost or
│                                          // by validation, which take the *bought*
│                                          // rank (`effect_build_rank`): dialling down
│                                          // refunds nothing and legalises nothing.
├── rankDial (bool, default false)        // build: whether the card carries that slider
│                                          // at all. A size effect gets one regardless.
├── dynamic (bool, default false)         // build: a Dynamic member of this power's array
│                                          // (§4) - shares the pool and runs alongside the
│                                          // other Dynamic members, for 2 points instead
│                                          // of 1. `Power`/`PowerGroup` carry the same
│                                          // flag for a group-level array
├── plCap ("" | "effect" | "attack")      // build: hold this effect hard to 2 x PL,
│                                          // lowering the side not named
│                                          // (`effect_pl_cap_shift`). Empty leaves the
│                                          // cap the warning it has always been.
└── computedCost

Power
├── id, name (player-chosen label), descriptors[]
├── effects: PowerEffectInstance[]        // Linked ones share a `linkGroup` id
├── alternates: PowerEffectInstance[][]   // array members, if any
└── activated / itemPresent / toggledOn   // runtime state flags per §7
```

---

## 9. Effect configuration fields

An effect's `config` array declares the choices a player makes when building it — which
resistance an Affliction targets, which condition each degree imposes. Each field stores
its value under its `key` in the instance's `config` dict, and the field's `type` picks
both the input widget (`CONFIG_WIDGET_BUILDERS`) and how a stored value reads back
(`CONFIG_DISPLAY_KINDS`). Both are registries, so a mod adds a field type without editing
either module.

Three things a field can do beyond holding a value:

- **`overrides`** names a game-term field the choice replaces, so an Affliction resisted by
  Fortitude says so in its Resistance row rather than in a note beneath it.
- **A `source`** replaces the field's own `options` with a list the *game data* decides.
  `CONFIG_OPTION_SOURCES` lives in `core/rules`, not beside the constructor, because the
  picker and the readout both have to turn an id into the same name;
  `config_source_options` is the single call they share. `personal_effects` is the first
  one: the effects an Affliction's Transformed condition may impose, which is a query over
  `effects.json` and would go stale the moment a mod added an effect.
- **A gate** hides the field until it is relevant. `hiddenWith` hides it while a modifier is
  attached (Variable Conditions defers the degree choices to use-time); `multiselectWith`
  upgrades it to multi-select instead; and `showWhenField` / `showWhenValue` reveal it only
  while a *sibling* field holds a value (the imposed-effect picker, once a degree reads
  Transformed). A gated field is **built and hidden**, not omitted — tearing the form down
  from inside the signal of the combo that changed is how Qt teardown bugs start. Closing a
  gate drops the stored value; opening one re-seeds the field's own default.

---

### Sub-build budgets

Four things in the rules buy a whole sub-character's worth of points, and each states its
budget as a derived number on the effect's card:

| Where | Budget | Where it comes from | Cite |
| --- | --- | --- | --- |
| Summon | one minion on `rank x 15` PP | a `points_per_rank` readout | p145 |
| Variable | `rank x 5` Variable Power Points | a `points_per_rank` readout | p148 |
| Affliction → Empowering | the Transformed form on `rank x 15` PP | `noteTemplate` + `notePerRank` | p110 |
| Morph → Metamorph | one trait set **per rank**, each worth *your own* point total | `noteTemplate` + `noteValues` | p136 |

Summon's count moves too: **Multiple Minions** doubles it per rank of the extra (one rank
for two minions, two for four), which is a `noteValues` entry rather than a multiple of
anything the note already had.

`noteValues` is `{placeholder: {"kind": ..., ...}}` on a modifier, each `kind` a handler in
the `NOTE_VALUE_KINDS` registry taking a `NoteValueContext` (the spec, the modifier, the
chip, the effect's rank, the wielder, the game data). It exists because `noteTemplate`'s
`{n}` is always "the effect's rank times a constant", and two of these budgets are not:
Multiple Minions counts in the *extra's* rank, and Metamorph's is the character's own
total. A handler returns `None` when it cannot answer — `character_points` with no
character in hand — and the placeholder is dropped from the sentence rather than shown as
a zero.

### The sub-builds themselves

Two of the four hold a build as well as a budget, and both are declared in data by a
`subBuild` block — on the **effect** record for Summon's minion, on the **modifier**
record for Metamorph's forms:

```json
"subBuild": {
  "key": "minion",
  "label": "Minion",
  "budget": { "kind": "per_rank", "perRank": 15 },
  "count": { "kind": "modifier_rank" },
  "forbidsEffects": ["summon"],
  "forbidsAdvantages": ["minion"]
}
```

`budget` and `count` are ordinary `NOTE_VALUE_KINDS` specs, resolved through the same
`note_value()` door a `noteValues` placeholder goes through — a sub-build's budget is the
same question a note asks, so `per_rank` and `modifier_rank` joined the registry and
`NoteValueContext.modifier` became optional (Summon's is priced off the *effect's* rank,
with no chip involved). `count` absent means one build.

**A sub-build is an ordinary `Character`**, stored as a list of `to_dict()` dicts under
`key` in the config dict that bought it — the effect's, or the chip's. That choice is what
makes the feature small: `config` is already written verbatim to the save file, undo
already snapshots the whole model as JSON, and the session layer already pushes the same
dict, so a nested character needs no migration, no new key and no new code in any of the
three. A *reference* to a saved NPC file was the alternative and was rejected: it couples a
player's power to the GM's directory and dangles when the file moves.

**The budget is stamped on read, not stored** (`sub_build_character`). Both it and the
Power Level are derived from the power and its wielder, so dialling a Summon from rank 4 to
rank 6 moves its minion's pool from 60 to 90 by itself — which is what lets the *sheet's
own* spent-against-budget readout be the check, instead of a warning bolted beside it.

`core/rules/subbuilds.py` sits below `validation` in the rules DAG (checking a minion means
walking its powers tree, and `leaf_powers` is validation's). It exposes
`power_sub_build_slots` / `effect_sub_build_slots`, the read/write pair
(`sub_build_character`, `store_sub_build`, `remove_sub_build`, `new_sub_build`), and
`power_sub_build_violations` — over budget, more builds than the power buys, or carrying
what the slot forbids.

The editor is `SubBuildWindow`, the ordinary sheet again (**not** NPC mode: an NPC swaps
the point pool for an estimated PL, and a minion is the one GM-side character the rules do
budget). It saves into the power rather than to a file, and shows Power Level and Power
Points read-only via `SystemInfoSection.set_budget_fixed`. `SubBuildPanel` is the strip of
buttons on the effect card that reaches it, showing every slot the card owns — the
effect's own and its chips' alike — and importing the window **inside the handler**,
because at module scope the constructor → main window → sheet → sections → constructor loop
closes.

**Variable's pool and Empowering's form still have no editor**, deliberately. Variable is a
*menu* the book itself suggests writing out in advance — closer to an array of saved
configurations than to one build — and Empowering's form is built by the GM for a target,
not by the player for themselves. Both still print their budget.

---

## 10. How far a Ranged effect reaches

"Ranged" on its own says nothing about distance, so an effect whose *effective* range
resolves to `Ranged` gets two further game-term rows after its Range row:

```
Range:                              Ranged
Distance:                           rank 10          ← tinted better when bought up
Measurements (short / medium / long): 1 mile / 2 miles / 4 miles
```

The reach is a **distance rank**, resolved by `rules.ranged_distance_ranks`:

- the base rank comes from the effect's `rangeDistance` spec — by default the effect's
  own *effective* rank, seeded system-wide from `system.json`'s `ranged_distance`;
- an effect whose reach doesn't scale that way overrides only the keys it needs in its
  own `rangeDistance` block (`{"rank": 4}` pins it, `{"offset": -2}` shifts it);
- every attached modifier with a `distanceRankBonus` adds its ranks on top. That is
  what makes **Extended Range** visible: it now moves the Distance row rather than
  silently costing points, and so it drops out of the trailing Notes row.

The range increments are `steps` further rank shifts from that rank — the default
`[0, 1, 2]` is the ×1/×2/×4 short/medium/long progression, the same idiom
`rules.speed_columns` uses for walk/dash/run. Measurements are imperial for now.

## 11. Out of scope for this file

Consistent with the earlier reference files, these are left for later / project-specific data
rather than reproduced here:

- Full per-effect configuration nuance (e.g. Affliction's three condition tiers, Create's
  volume-by-cost table, Environment's specific hazard sub-types). `effects.json` descriptions
  are intentionally high-level; add per-effect config schemas as you implement each one.
- Power Descriptors as a controlled vocabulary (fire, cold, magic, technology...) — these are
  free text in the source material and best left as free text or a project-defined tag list.
- Equipment-specific rules (Equipment advantage, weapon/armor stat blocks) — covered only
  glancingly here via the Removable flaw's "equipment" tier.
- Vehicle/Installation powers — a specialized extension of this same effect system, not
  covered in `effects.json`.

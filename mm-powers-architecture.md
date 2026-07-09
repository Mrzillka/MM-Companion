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
power_total_cost      = sum(effect_total_cost for every effect in the power)
```

Minimum cost per rank is 1 Power Point — flaws can't push a per-rank cost below that. This is
the number that gets deducted from the character's Power Point pool (see
`mm-core-mechanics.md` §7) when the power is added to the character.

---

## 3. Effect Type categories (behavioral defaults)

Every effect in `effects.json` has an `effectType`. Knowing the type tells you the *default*
action/duration pattern before any modifiers are applied — useful for validating a
user-configured effect or pre-filling sensible defaults in a builder UI.

| Effect Type | Typical action | Typical duration | Typical role |
|---|---|---|---|
| Attack | Standard | Instant | Offensive, requires an attack check, always resistible |
| Defense | None (or Free if toggleable) | Permanent (or Sustained) | Personal protection, usually passive |
| Control | Standard | Sustained | Manipulating the environment/objects/others, requires upkeep |
| General | Varies per effect | Varies per effect | Doesn't fit the other categories |
| Movement | Free (to activate) | Sustained | Grants/improves a movement mode; still needs a Move action to actually move |
| Sensory | Free or None | Sustained or Permanent | Enhances, grants, or fools senses |
| Alteration | Free | Sustained (or Permanent) | Transforms the user's body/form |

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
- **Dynamic Alternate Effects**: a variant array where 2+ ranks of the Alternate Effect extra
  let multiple array members split the pool of points and run **simultaneously** at reduced
  rank, reconfigured freely each turn.

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

## 6. Worked example: Enhanced Trait, exactly as you described

This is the case you called out — a power that should *conditionally* patch another stat.

```json
{
  "effect": "enhanced_trait",
  "rank": 4,
  "config": { "target": "combat.attack" },
  "extras": [],
  "flaws": [
    { "modifier": "removable", "value": "equipment" }
  ],
  "cost": 6
}
```
Reasoning:
- Base rank 4, target = Attack (same per-rank cost as buying Attack ranks directly — 2
  PP/rank, per `mm-core-mechanics.md` §7 — so 8 PP normally).
- The `removable` flaw (equipment tier) discounts the cost based on the power's total cost
  (see `modifiers.json`), dropping it to 6 PP here, but attaches a condition: **this bonus
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

Swap `target` to `"abilities.strength"`, `"skills.stealth"`, `"resistances.toughness"`, etc.
and the same function covers Enhanced Ability, Enhanced Skill, Enhanced Defense, and Enhanced
Resistance uniformly — they're all just **Enhanced Trait** configured differently, per the
source material (`effects.json` keeps them as one effect entry with a `configurableTarget`
field rather than four separate effects).

**Power Level note:** the *combined* total (base + all active Enhanced Trait bonuses) must
still respect the Power Level caps from `mm-core-mechanics.md` §7. Validate that at
character-build time, not just at runtime — a Sustained Enhanced Trait requires the character's
un-enhanced rank to already sit far enough below the cap to leave room for the bonus.

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

---

## 8. Suggested schema summary

```
Effect (from effects.json)
├── id, name, effectType, action, range, duration, check, resistance, baseCost
├── configurableTarget: null | "trait"   // true for Enhanced Trait-style effects
└── statIntegration: { pattern: "passive_permanent"|"passive_toggle"|"instant_action"|"resource_pool",
                        affects: "ability"|"skill"|"defense"|"resistance"|"movement"|"senses"|
                                 "none"|"special" }

Modifier (from modifiers.json)
├── id, name, category ("extra"|"flaw"), costFormula, flat (bool), appliesTo
└── description

PowerEffectInstance  (part of a character's Power)
├── effectId, rank, config{}, extras[]{modifierId, rank?}, flaws[]{modifierId, rank?}, descriptors[]
└── computedCost

Power
├── id, name (player-chosen label), descriptors[]
├── effects: PowerEffectInstance[]        // Linked ones share a `linkGroup` id
├── alternates: PowerEffectInstance[][]   // array members, if any
└── activated / itemPresent / toggledOn   // runtime state flags per §7
```

---

## 9. Out of scope for this file

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

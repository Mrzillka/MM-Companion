# Mutants & Masterminds (4th Edition) — Equipment Design & Implementation Guide

> **Note on source & copyright:** Original, paraphrased summary of the *functional* rules
> (costs, ranks, DCs, formulas, trait tables) for the Equipment chapter, written for implementing
> gear acquisition, cost accounting, and vehicle/installation builds. Not a reproduction of the
> rulebook's prose or flavor text. Companion file to `equipment.json` (short always-visible
> labels) and `equipment-design.json` (this file's data catalog — full mechanics + implementation
> tags). Read `mm-powers-architecture.md` first: equipment is built out of the same effects and
> modifiers, and this file assumes that model.

---

## 1. The one idea that makes equipment tractable

Equipment is **not a new subsystem**. Every item in the chapter is a small power built from
`effects.json` + `modifiers.json`, carrying the `removable` flaw at its harshest tier. The book
makes one simplifying move and everything else follows from it:

> Because the equipment tier of `removable` reduces 5 points of effects to 1 Power Point, the
> Equipment advantage skips the per-item discount entirely. It hands out **5 Equipment Points per
> rank**, and items are priced at their **undiscounted** effect cost.

So there are two currencies in play, and the engine must not confuse them:

| Currency | Comes from | Spent on |
|---|---|---|
| Power Points (PP) | The character's build total | Abilities, skills, advantages, powers — including *ranks of* `advantages:equipment` |
| Equipment Points (EP) | `advantages:equipment`, 5 per rank | Individual items, vehicles, installations |

`_meta.currency.byAdvantageRank` in `equipment-design.json` has the resolved table (rank 1 → 5 EP,
rank 5 → 25 EP, rank 10 → 50 EP …) so nothing needs recomputing at runtime.

Two consequences worth encoding as hard rules:

1. **The discount is not reapplied.** An item's EP cost equals what the same effects would cost in
   PP *without* `removable`. If your cost engine sees `modifiers:removable` on an equipment item,
   something has double-counted.
2. **Equipment is not a power.** `Character.powers` and the character's equipment list are separate
   collections. A character built entirely from equipment legitimately answers "no powers" — which
   matters for Nullify targeting, for settings that gate on powers, and for the sheet's own
   power-point accounting.

The limitations of `removable` still apply in play even though its discount doesn't:
availability, no bonus stacking, material cost, damage and loss, Extra Effort straining the *item*
rather than the character, and technological limits. These are in `_meta.equipmentLimits`.

---

## 2. The implementation patterns

Same approach as `mm-advantages-design.md` §2: rather than 106 bespoke item handlers, sort items
into a small number of patterns, give each pattern one handler, and let every item be data.
An item can carry more than one tag (grenades are `attack_item` + `consumable`).

### Pattern A — `passive_trait_item`
Worn or held; contributes a standing bonus while possessed. This is armour, shields, and most
protective gear.
```
function applyItemBonus(character, item):
    if not character.possesses(item): return
    if item.implementation.requiresWorn and not character.isWearing(item): return
    target = item.implementation.target          # e.g. "resistances.toughness"
    contribute(character, target, item, source="equipment")
```
Crucially, `contribute()` is **not** `+=` — see §3.

### Pattern B — `attack_item`
Weapons and grenades. Doesn't sit on the sheet as a bonus; exposes a usable attack action, exactly
like `mm-powers-architecture.md`'s Pattern C (`instant_action`). The item's `effects[]` and
`modifiers[]` arrays feed the same `effect_game_terms` / `effect_stat_rows` renderers the Power
Constructor already uses.

### Pattern C — `skill_aid`
Grants a bonus to, or removes a penalty from, a named skill or skill use. Two sub-shapes, both
present in the data:
```
SkillAid {
  skill: "skills:athletics" | "*"
  scope: string | null            # "climb", "gather_evidence", "locks_and_security"
  bonus: int | null               # +2 climbing gear, +5 stealth suit
  penaltyRemoved: int | null      # 5 — burglary tools, first-aid kit, mini-computer
  penaltyReplacement: {from, to} | null   # multi-tool: -5 becomes -2
}
```
The `penaltyRemoved` family all cancel the standard −5 improvised-tools penalty. The multi-tool is
the interesting one: it *softens* rather than cancels, and it applies to `"*"` at GM discretion —
so it needs to be a fallback that loses to any item with a specific `skill` match.

### Pattern D — `sense_item` and Pattern E — `movement_item`
Thin wrappers that push an `enhanced_senses` quality or an `enhanced_movement` mode / `flight` /
`speed` effect into the character's sense and movement tables while the item is worn. These reuse
`rules.power_trait_bonuses` unchanged; the only difference is the gate (`requiresWorn` /
`requiresHeld` instead of `power.activated`).

### Pattern F — `utility_effect_item`
Produces a discrete non-attack effect on use: flashlight (counters darkness concealment in a cone),
restraints (DC 20 to slip, Toughness 5 to break), tracer (plant check, notice DC). Not a standing
bonus and not an attack — an action the UI offers with its DCs pre-resolved.

### Pattern G — `consumable`
A depleting counter. Same shape as `mm-advantages-design.md`'s Pattern E resource tracker, but the
reset cadence is item repair/resupply rather than a scene or adventure boundary.
```
ConsumableTracker { chargesRemaining: int, resetsOn: "resupply" | "adventure" }
```
The fire extinguisher (2 charges) is the only item with a hard printed number. Rebreathers,
ammunition, and thrown-weapon supplies are explicitly handled as **complications** rather than
counters — the book says so directly, so don't build a timer for them. Model those as a flag the
GM can trip, not a resource the player watches drain.

### Pattern H — `container_array`
Utility kits, trick arrows, alternate vehicles, alternate installations. All the same rule:
**pay the costliest member in full, then 1 EP per alternate.** This is `modifiers:alternate_effect`
applied at the equipment layer, and the existing `power_total_cost` array branch handles the maths
if you feed it the item list.

The book's own guidance is worth surfacing in the UI: since most utility items cost 1–2 EP, arraying
them saves almost nothing and costs you the ability to use two at once. Arrays pay off for weapons
(where individual costs are 5–27 EP) and for vehicles (10–139 EP). The printed utility kit
demonstrates exactly this — the weapons go in an array, the eight utility gadgets are just bought
outright.

### Pattern I — `accessory`
Modifies a host item rather than standing alone; the cost adds to the host. Four exist
(laser sight, stun ammo, suppressor, targeting scope) and each has an `attachesTo` constraint.
Worth modelling properly rather than as loose items, because the targeting scope's Improved Aim is
scoped to *that weapon only* — a distinction the sheet will otherwise get wrong.

### Pattern J — `platform`
Vehicles and installations. These are sub-builds with their own trait-cost tables and their own
point pools, more like a nested character than like an item. See §5 and §6.

### Pattern K — `feature_slot`
A 1-EP named Feature on a platform. Some are repeatable with a defined escalation (Concealed:
+10 DC, then +5 per extra rank, capped at +30; Security System: DC 20, +5 per rank, capped at 40).
Store the escalation in data — `dcIncreasePerExtraRank` / `maxDcIncrease` — rather than branching
per feature name.

### Pattern L — `gm_adjudicated`
Restricted-equipment availability, Deathtraps, Personnel, Trophy Room, Movable. The trigger is
mechanical, the outcome is a ruling. Surface as a prompt, don't auto-resolve.

---

## 3. The no-stacking rule is the single most important engine behaviour here

> Equipment bonuses do not stack with each other **or with bonuses from other sources**. Only the
> single highest applicable bonus applies.

This is not a footnote. It is the reason the chapter explicitly says tough heroes rarely bother with
armour, and it's the sort of rule that quietly produces wrong sheets if `+=` is used anywhere.

```
function resistanceTotal(character, target):
    innate      = character.base[target]                 # bought ranks
    powerBonus  = max((p.bonus for p in activePowerBoosts(character, target)), default=0)
    equipBonus  = max((e.bonus for e in activeEquipment(character, target)), default=0)
    return innate + max(powerBonus, equipBonus)
```

Note the shape carefully: it's `max()` *within* equipment, and `max()` *again* between equipment and
other bonus sources — not a sum of the two maxima. A character with Protection 8 from a power gains
literally nothing from a bulletproof vest, and the UI should say so rather than silently showing the
vest as inert. A "superseded by *(source)*" annotation on the item card is the right affordance, and
it reuses the same visual language as the `⚠` PL-breach marker on power cards.

`_meta.stackingRule.appliesTo` lists the targets this governs: Toughness, Defense, skill bonuses,
and sense qualities.

**Power Level still binds on top of this.** Toughness from armour counts against
`maxDefenseOrDodgePlusToughness` exactly like bought Toughness, so `power_pl_violations` needs to
see equipment contributions. This is the natural place to hook equipment into the existing
validation seam rather than writing a parallel one.

---

## 4. Strength-Based weapons: a second divisor, distinct from the cost rule

This connects directly to the effective-rank work already flagged for `effect_total_cost`, and it is
easy to conflate the two. They are different rules operating at different stages.

**The existing rule (cost):** per-rank Extras price against the *effective* rank
(purchased + Strength contribution), not the purchased rank alone.

**The new rule (how much Strength even arrives):** when modifiers push a Strength-Based Damage
effect above 1 point per rank, divide the wielder's Strength rank by the effect's cost per rank and
round down. *That* reduced number is what gets added to the Damage rank.

> A compound bow with Ranged Strength-Based Damage costs 2 points per rank. A Strength 5 wielder
> adds `floor(5 / 2) = +2`, not +5.

The two carve-outs matter:

- **Effects costing less than 1 point per rank:** Strength is *not* multiplied. No divisor, no bonus
  either way.
- **Flat modifiers that don't change cost per rank:** the full Strength rank still applies. Only
  per-rank modifiers trigger the divisor.

Order of operations for the engine:

```
function strengthContribution(effect, wielder):
    if not effect.strengthBased: return 0
    perRank = netCostPerRank(effect)              # after extras and flaws
    raw     = wielder.abilities.strength
    if perRank <= 1: contribution = raw          # includes the sub-1 case
    else:            contribution = floor(raw / perRank)
    return min(contribution, weaponToughnessCap(effect.item))

# then, unchanged from the existing design:
effectiveRank = purchasedRank + strengthContribution(...)
#   -> effectiveRank drives save DCs, PL caps, AND per-rank Extra pricing
#   -> purchasedRank alone drives the base cost
```

`weaponToughnessCap` is the last piece: an ordinary weapon can only carry Strength up to its own
Toughness — roughly 4 for wooden weapons, 7–8 for metal. Exert more than that and **the weapon breaks
when used**. This is a genuine gameplay event, not just a cap, so the character generator should warn
at build time ("Strength 10 exceeds this sword's Toughness 7 — it will break on use") rather than
silently clamping.

---

## 5. Vehicles

A vehicle is a nested build with five traits, and **Size must be chosen first** because it sets the
baselines for three of the others.

### Trait costs
| Trait | Equipment Point cost |
|---|---|
| Size | 1 per size rank above 0 |
| Strength | 1 per +1 above the size baseline |
| Toughness | 1 per +1 above the size baseline |
| Defense | 1 per −1 of the size penalty bought off, down to −0 |
| Speed | the movement effect's normal PP cost, paid in EP |
| Features | 1 each |
| Powers | the effect's normal PP cost, paid in EP |

### Size baselines
| Size rank | Examples | STR | TOU | DEF |
|---|---|---|---|---|
| 0 | Motorcycle | 2 | 5 | −0 |
| 1 | Car, truck | 4 | 6 | −1 |
| 2 | Stretch limo, SUV, tank | 6 | 7 | −2 |
| 3 | Semi, yacht, fighter jet | 8 | 8 | −3 |
| 4 | Passenger jet | 10 | 9 | −4 |
| 5 | Space transport | 12 | 10 | −5 |

Each rank beyond 5 adds +2 STR, +1 TOU, −1 DEF (GM option). The table is small enough to extend
arithmetically rather than storing extra rows.

### Combat
- **Moving Defense rank** = *current speed rank* + size Defense modifier. Defense Class is
  `10 + Defense rank`. This is the one place a vehicle's defence is dynamic per round — a parked car
  and a car at Speed 7 are very different targets.
- **Stationary** vehicles are a routine check at DC `10 + defense modifier`.
- **Defensive check:** the pilot spends a standard action and substitutes their Vehicles check result
  (modified by the size Defense modifier) for the vehicle's Defense Class that round. If that comes
  out *lower* than the usual DC, apply +1 to the usual DC instead — so evasive manoeuvres can never
  make you easier to hit.
- Vehicle Weapons is its own focus of Close Combat and Ranged Combat; attacks resolve as
  `Attack + attack advantage + vehicle combat skill`.

### Control checks and crashes
Control checks are DC 15 Vehicles, a **non-action** (but the operator must be able to take at least
free actions). One per round per vehicle; every *additional* check called for that round raises the
DC by +5. Terrain: open −5, close 0, tight +5.

Crash damage builds up additively:

```
damageRank = speedRank(fastest vehicle)
           + 1  if colliding with another vehicle
           + 1  per size rank of difference   (applied to the smaller vehicle)
           + 2  on two degrees of failure
           + 5  on three or more degrees of failure
# same direction: use (higher speed rank - lower speed rank) as the base
```
Occupants roll Dodge against `damageRank + 10` for a +5 bonus to resist; safety harnesses give a
further +5 on that Dodge check.

### Vehicle damage ladder
Vehicles map onto the damage conditions differently from characters, and the mapping is worth
encoding beside the existing `conditions-design.json` cascade rather than hardcoding:

| Condition | Vehicle result |
|---|---|
| Hit | Usual cumulative −1 per condition on Damage resistance checks |
| Dazed / Stunned | Control check required; vehicle is Impaired — cumulative −1 to checks involving it, *or* the loss of one Feature or 1 EP of capability per −1 |
| Staggered | One system fails: weapons, propulsion (−1 speed rank per round until Immobile), or controls (auto-failed control check each round) |
| Incapacitated | Non-functional. Water vehicles sink, air vehicles crash, space vehicles may lose life support |

Repair: 1 degree → DC 15 / 30 min; 2 degrees → DC 20 / 2 hrs; 3 degrees → DC 25 / 8 hrs. Jury-rigging
is +5 DC as a standard action. No tools is the usual −5.

Extra Effort with a vehicle ("redlining") strains the vehicle: Impaired → Disabled → Immobilized,
and a Disabled vehicle is additionally Hindered (−1 speed rank). These persist until maintenance —
they do **not** clear on rest, unlike character Fatigue.

### Vehicle modifiers — a rare case of modifying the *advantage*
`Durable` (+1/rank), `Minion` (−1/rank), and `Summonable` (+1/rank) modify the cost of the
**Equipment advantage ranks allocated to that vehicle**, not the vehicle's EP cost. This is
structurally unusual and needs its own field rather than being folded into the item's modifier list —
`equipment-design.json` keeps them in a separate `vehicleModifiers` array for exactly that reason.
All three are GM-optional.

---

## 6. Installations

Simpler than vehicles: just Size, Toughness, Features, and Effects.

| Trait | Starting point (free) | Cost |
|---|---|---|
| Size | rank 5 | 1 EP per size rank up or down |
| Toughness | 6 | 1 EP per +2 Toughness |
| Features | — | 1 EP each |
| Effects | — | the effect's normal PP cost, in EP |

Size runs from rank 1 (a room, −4 EP — you get points *back*) to rank 11 (a small town, +6 EP), with
house = rank 5 = 0 EP as the pivot. Full table in `_meta.installationSizeTable`.

> ⚠ **Printed inconsistency.** The Installation Trait Cost table gives Size a "starting rank" of 0,
> while the surrounding text and the Structure Size Categories table both put the free starting point
> at rank 5. The size table is internally consistent (rank 11 = 6 EP, rank 1 = −4 EP, i.e. exactly
> `sizeRank − 5`), so this file treats the trait-cost table's `0` as a typo and uses **rank 5**.
> Recorded in `_meta.printedDiscrepancies` alongside the others.

**Power Level.** Player installations are capped by the series PL; NPC installations aren't strictly
capped. Because installations have no other defences, Toughness may reach **twice** the series PL —
but Impervious is still capped at PL. That's a different cap pair from characters and needs its own
validator branch.

36 Features are catalogued. The repeatable ones with defined escalation are the ones worth getting
right in data:

- **Concealed / Secret** — +10 DC, then +5 per extra rank, max +30 (Secret starts from a DC 10 base)
- **Security System** — DC 20, +5 per rank, max DC 40
- **Holding Cells** — one listed benefit per rank, and Impervious requires Hardened first (a genuine
  prerequisite chain, not just a list)
- **Combat Simulator / Defense System / Effect** — bounded by `2 × installationPowerLevel` in effect
  cost, with an extra rank adding PP equal to the PL
- **Temporal Limbo** — ratio doubles per rank (½ or 2×, then ¼ or 4×, …)

Note the Concealed/Isolated distinction, which the book calls out explicitly and which is easy to
collapse: **Concealed is hard to find, Isolated is hard to reach.** They stack.

---

## 7. Suggested schema

```
EquipmentItem (from equipment-design.json)
├── id, name, category, subcategory, cost, costKind: "fixed"|"ranked"|"per_rank"|"built"
├── effects[]      { effect: "effects:<id>", rank?, strengthBased?, descriptors[], configuration?, degrees[] }
├── modifiers[]    { modifier: "modifiers:<id>", rank?, note? }
├── grants         { advantages: ["advantages:<id>", …] }
├── critical       { threatRange: [lo, hi], improvedCriticalRanks: int } | null
├── patterns[]     // one or more of the §2 pattern ids
├── implementation {
│     target?, perRank?, bonus?, penaltyRemoved?,   // A / C
│     range?, action?, dodgeDC?,                    // B
│     requiresWorn?, requiresHeld?, stacking?,      // A
│     charges?, consumedOnUse?, resetsOn?,          // G
│     attachesTo?,                                  // I
│     components? | alternates?                     // H
│   }
└── notes?

StockVehicle
├── id, name, class, size, strength, speed, defenseModifier, toughness, cost
├── defenses[]  { modifier: "modifiers:impervious", rank }
├── weapons[]   { name, effect, rank, modifiers[], areaRank?, homingRank? }
└── movement?, costFormula?, notes?

StockInstallation
└── id, name, size, toughness, cost, features: ["installationFeatures:<id>", …]
```

A character's equipment then lives alongside `Character.powers` as its own list, with a derived
`equipment_points_spent` mirroring `power_points_spent`:

```
EquipmentLoadout
├── budget:   5 × character.advantageRank("equipment")
├── items:    [{ itemId, config{}, accessories[], quantity }]
├── vehicles: [{ vehicleId | customBuild, modifiers[] }]
└── installations: [{ installationId | customBuild, sharedWith[], contributedRanks }]
```

---

## 8. Cross-reference and data-hygiene notes

Following the audit habit from the modifiers pass, three references in the printed weapon tables
don't resolve cleanly against the existing files. They are recorded in
`_meta.unresolvedReferences` rather than silently mapped or dropped:

1. **"Fast Grab"** (sash, whip, and the Wrestling fighting style). There is no `fast_grab` in
   `advantages.json` — the 4e grab family is Agile Grab / Damaging Grab / Defensive Grab / Grabbing
   Block / Grabbing Finesse / Improved Grab / Improved Hold. This looks like surviving 3e
   terminology. `advantages:agile_grab` is the closest fit, but that's a guess, so the data flags it
   instead of asserting it.
2. **"Defensive Attack"** (tonfa, war fan; and the Weapon Advantages text calls it an advantage).
   In 4e this is a combat *manoeuvre*, per `mm-actions-adventure.md` — not an advantage. Model as an
   item-granted manoeuvre benefit.
3. **"Blast"** in the stock-vehicle weapon descriptions. Legacy effect name; normalised to
   `effects:damage` + `modifiers:ranged` in this file's vehicle entries.

Six further printed inconsistencies (whip Reach 3 vs. 2, chain Reach 1–2 vs. 2, the duplicate
javelin, thrown-weapons-and-Strength, the installation starting size, sailboat Defense, Moon-Base
Toughness) are in `_meta.printedDiscrepancies` with the resolution this file took for each. In every
case the **table** wins over the prose, since the table is what carries the cost.

---

## 9. Out of scope for this file

- **Constructs** (robots, androids, golems, undead). Printed in the Equipment chapter but explicitly
  *not* equipment: they act on their own, so they're characters acquired via `advantages:minion`,
  `advantages:sidekick`, or `effects:summon`. They need their own reference covering the
  no-Stamina / no-INT+PRE-or-no-STR+AGL construction rules, the −30/+30 points balance, the
  automaton and immobile-intellect variants, and construct repair DCs.
- **Mounts.** Treated as vehicles for most guidelines but acquired as followers or summons; riding
  uses `skills:athletics` unless the GM adds a Riding skill.
- **Chases and races.** These are check sequences and belong with the challenge rules in
  `mm-actions-adventure.md`, not here. The one equipment-specific piece — the speed-rank differential
  bonus (within 1 rank: no modifier; 2 ranks: +2; 3+ ranks: +5) — is noted here only as a pointer.
- **Fighting-style and archetype equipment loadouts.** Curated presets, same as the fighting-styles
  note in `mm-advantages-design.md` §6.

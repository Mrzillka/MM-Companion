# Mutants & Masterminds (4th Edition) — Power Effect & Modifier UI Design

> **Note on source & copyright:** Original, paraphrased summary of the *functional*
> configuration rules for all 42 base effects and their modifiers — the specific choices,
> menus, and derived values the rulebook defines — written to drive a power-builder UI. Not a
> reproduction of the rulebook's prose or flavor text. Companion to `mm-powers-architecture.md`
> (cost formulas, stat integration), `effects.json`, `modifiers.json`, and `effect_modifiers.json`.
> Read the architecture doc first; this file is specifically about what changes in the
> **configuration screen** for each effect, not how the result patches character stats.

---

## 1. Why this needs its own file

`effects.json` tells you *that* Affliction costs 1/rank and has an Attack-vs-Defense check.
It doesn't tell you that building an actual Affliction power means the player must pick a
resistance type, pick which resistance overcomes it, and pick one condition from each of three
tiered lists — three genuinely different UI widgets, not just a rank slider. Most of the 42
effects are simple (rank number + pick extras/flaws from the two modifier files, done). About a
third have real per-effect configuration surfaces. This file sorts all 42 into five UI
complexity tiers, then gives the specific widget/field list for every effect that isn't Tier 1.

---

## 2. The five UI tiers

### Tier 1 — Rank only
Just a rank stepper, plus the standard extras/flaws multi-select from `modifiers.json` +
`effect_modifiers.json[effectId]`. No bespoke fields.
**Effects:** Deflect (aside from an optional category text field, see Tier 2), Elongation,
Extra Limbs, Fortune Control, Healing, Leaping, Lifting, Mind Reading, Move Object, Nullify
(aside from scope, Tier 2), Postcognition, Precognition, Protection, Quickness, Regeneration,
Speed, Summon (aside from minion template, Tier 2), Swimming, Teleport, Transmute.

### Tier 2 — One configurable target/category field
A single dropdown or text field alongside the rank stepper.
| Effect | Field | Widget |
|---|---|---|
| Enhanced Trait | Which trait this boosts | Dropdown: Ability / Skill / Defense / Resistance / Advantage, then a second dropdown for the specific trait |
| Damage | Strength-Based toggle | Checkbox (adds the Strength-Based extra) |
| Deflect | Limited-to category (if the Limited flaw is taken) | Free text, e.g. "thrown weapons only" |
| Nullify | What it nullifies | Free text/tag, GM-approved (a descriptor, effect type, or named power) — flag broad ones for GM review |
| Immunity | What it's immune to + auto-suggested rank | See Tier 4 (it's really a scoped-rank picker, not a single field) |

### Tier 3 — Multi-part configuration with tiered/degree logic
**Affliction** is the standout example — treat it as its own screen, not a modal field:
1. **Resistance to resist it** — dropdown: Dodge / Fortitude / Will (whichever fits the descriptor).
2. **Resistance to overcome it** — dropdown, defaults to the same as above but can differ (e.g.
   resisted by Dodge, overcome by Will).
3. **Condition picker, one per degree tier**, each a dropdown constrained to that tier's list:
   - *1 degree (mild):* Dazed, Deafened, Disabled (one trait), Fatigued, Figment, Hindered,
     Impaired, Indifferent, Prone, Unaware (one non-accurate sense), or Vulnerable.
   - *2 degrees (moderate):* Blinded, Compelled, Confused, Defenseless, Disabled, Exhausted,
     Favorable, Frightened, Immobile, Phantasm, Stunned, Susceptible, Unaware (one accurate
     sense), or Unfavorable.
   - *3 degrees (major):* Asleep, Controlled, Debilitated, Delusion, Helpful, Hostile,
     Incapacitated, Paralyzed, Transformed, Unaware, or Unconscious.
4. **Optional Extra Condition** (if that extra is taken) — a second condition dropdown for the
   1st/2nd degree tiers specifically.
5. **Optional Variable Conditions** flag — if set, the condition pickers move from build-time
   to *use-time*: instead of locking in a choice now, the UI should show "chosen when used" and
   skip steps 3-4 entirely (with a note that it's still capped by the target's degree of
   failure, and can be scoped to just one tier via the cheaper variant).

This tiered-picker pattern is unique to Affliction; no other effect needs it.

### Tier 4 — Menu of named sub-abilities, each with its own rank cost
These effects don't have "one rank = one bonus." Rank is a currency the player *allocates*
across a fixed menu of named options, each with its own rank requirement (sometimes a single
rank, sometimes a threshold like "2 ranks for X, 4 for Y, 6 for Z"). The UI needs a
multi-select checklist where checking an option consumes ranks from the effect's pool, with
running-total validation (`sum(selected option costs) <= effect.rank`).

| Effect | Menu items (abbreviated) | Notes |
|---|---|---|
| **Enhanced Movement** | Dimensional Travel (2/4/6), Environmental Adaptation (1/environment), Permeate (2/4/6), Safe Fall (1), Slithering (1), Space Travel (2/4/6), Stable (1/mode), Swinging (2), Trackless (1/sense), Water-Walking (1/2) | Some options are themselves tiered (2/4/6 for increasing scope) — model as a sub-radio within the checklist item, not a separate checkbox per tier |
| **Enhanced Senses** | A sense **type** selector (Sight/Hearing/Smell/Taste/Touch/Mental/Radio/Special/etc.) crossed with ability tags: Accurate (2/4), Acute (1/2), Analytical (1/2), Danger Sense (2), Dark-Vision (2), Direction Sense (1), Distance Sense (1), Extended (1/2), Infra-Vision (1), Low-Light Vision (1), Microscopic Vision (1-4), Penetrates Concealment (2/4), Radio (1), Radius (1/2), Ranged (1/2), Rapid (1+), Tracking (1/2), Ultra-Hearing (1), Ultra-Vision (1) | The biggest menu in the game. UI should be a two-axis picker: choose a sense, then choose which abilities apply to it, with per-item rank cost and a running total against the effect's rank |
| **Comprehend** | Animals (1/2), Computers (1/2), Languages (1/2/3/4), Objects (2), Plants (2), Spirits (1/2) | Each category is independently tiered; Languages notably scales furthest (rank 4 grants physically-impossible communication) |
| **Immunity** | Free-form: player names what they're immune to; the app should offer **suggested rank tiers** rather than a fixed list | 1 rank: a single environmental hazard or very rare descriptor. 2 ranks: Critical Hits, or a rare descriptor, or all Suffocation. 5 ranks: an uncommon descriptor (e.g. one Affliction descriptor, one Damage descriptor). 10 ranks: a common descriptor (e.g. Cold, Fire, or "Environmental/Life Support" as a bundle). 20 ranks: a very common descriptor (Bludgeoning/Energy/Piercing/Slashing Damage). 30 ranks: everything resisted by one whole resistance (all Fortitude effects, or all Will effects). Multiple named Immunities on one character add their ranks together for total cost — model as a repeatable list, not a single dropdown. |
| **Feature** | Open-ended: 1 rank buys one minor custom capability, freely defined (with book examples like Battery, Built-in Equipment, Quick Change, Remote) | Not a fixed menu — provide a free-text name + description field per rank purchased, optionally with a few book examples as autocomplete suggestions |
| **Variable** | Not a menu, but a *resource pool*: the rank buys Power Points that get reconfigured at will into other effects | UI should treat this as "grants an N-point sub-budget," then let the player build one or more mini-Powers within that budget at use-time (or Action-time if the relevant extra is taken) — effectively a nested instance of the whole Power Builder, capped at the Variable's point total |

### Tier 5 — Derived/display-only values (not user choices at all)
These effects have rank-dependent behavior that's entirely computed — the UI should surface it
as an **info readout**, not an editable field, because picking a "wrong" value isn't possible.
| Effect | What's computed and shown |
|---|---|
| **Burrowing** | Effective burrow speed rank per terrain: full rank through soil/sand, rank-1 through hard clay/packed earth, rank-2 through rock. Show all three as a small readout table under the rank stepper. |
| **Communication** | Range band per rank (1=same city, 2=same region, 3=same continent, 4=worldwide, 5=unlimited) — plus a required **medium** dropdown (Sight/Hearing/Smell/Touch/Radio/Mental/Special) since that's an actual choice, not derived. |
| **Flight** | Whether the character breaks the sound barrier (rank 11+) or can circle the globe in minutes (rank 20+) — flavor readouts, no mechanical choice. |
| **Growth / Shrinking** | Rank directly maps to a Size Category shift (see `measurements.json`'s `sizeTable`) — display the resulting Defense/Damage/Toughness/Speed/Intimidation/Stealth modifiers as read-only derived stats, recomputed live as the player adjusts rank. This is the same table Growth/Shrinking characters use for their size-based combat modifiers, so reuse that data file rather than re-deriving it. |
| **Insubstantial** | Rank determines physical state (higher ranks = less substantial, up to fully incorporeal) with knock-on effects on what can affect the character — show as a state-name readout keyed off rank rather than a free choice. |
| **Illusion** | Whether maintaining it needs a Concentrate action (moving/interactive) vs. just Sustain (static) — this is actually a player choice about the illusion's *content*, best modeled as a checkbox ("this illusion moves/interacts") that changes the readout, since the Active extra can remove the distinction entirely. |

---

## 3. Effect-by-effect quick index

For fast lookup — every effect, its tier, and a one-line UI note.

| Effect | Tier | UI note |
|---|---|---|
| Affliction | 3 | See Tier 3 above in full — needs its own multi-step config screen |
| Burrowing | 5 | Show per-terrain speed readout |
| Communication | 2+5 | Medium dropdown (choice) + range-band readout (derived) |
| Comprehend | 4 | Category checklist, each independently tiered |
| Concealment | 1 | Standard; note the Partial/Blending/Passive flaws change the readout text meaningfully, worth surfacing in a live preview |
| Create | 1 | Standard, though Increased Volume/Precise/Variable Opacity extras are worth checkboxes with inline explanation text since they change what "create an object" even means |
| Damage | 2 | Strength-Based toggle |
| Deflect | 1-2 | Standard + optional Limited category text |
| Elongation | 1 | Standard |
| Enhanced Movement | 4 | Menu checklist, see Tier 4 |
| Enhanced Senses | 4 | Two-axis menu, see Tier 4 — biggest one in the game |
| Enhanced Trait | 2 | Trait-target dropdown, cascading to a specific-trait picker |
| Environment | 1 (mostly) | Rank buys a hazard intensity; the specific hazard type is largely free-text/GM-set, similar in spirit to Immunity but lower-stakes |
| Extra Limbs | 1 | Standard |
| Feature | 4 | Free-text capability name/description per rank, with example autocomplete |
| Flight | 5 | Standard rank stepper + derived speed-tier readout |
| Fortune Control | 1 | Standard |
| Growth | 5 | Rank stepper + live Size Table readout (reuse `measurements.json`) |
| Healing | 1 | Standard |
| Illusion | 5 | Standard + "this illusion moves/interacts" checkbox affecting the maintenance-action readout |
| Immunity | 4 | Repeatable list of (named scope, suggested rank) pairs; ranks sum for total cost |
| Insubstantial | 5 | Rank stepper + state-name readout |
| Leaping | 1 | Standard |
| Lifting | 1 | Standard |
| Mind Reading | 1 | Standard |
| Morph | 1 | Standard; consider a free-text "forms you can take" list purely for character-sheet flavor, no mechanical effect |
| Move Object | 1 | Standard |
| Nullify | 2 | Scope text field (descriptor/effect/named power), flagged for GM approval if broad |
| Obscure | 1 | Standard |
| Postcognition | 1 | Standard |
| Precognition | 1 | Standard |
| Protection | 1 | Standard |
| Quickness | 1 | Standard |
| Regeneration | 1 | Standard |
| Remote Sensing | 1 | Standard |
| Shrinking | 5 | Same as Growth, opposite direction |
| Speed | 1 | Standard |
| Summon | 2 | Minion/ally template field (name, descriptor, PP budget derived from rank) — effectively "configure an NPC," same UI concern as the Minion/Sidekick advantages |
| Swimming | 1 | Standard |
| Teleport | 1 | Standard |
| Transmute | 1 | Standard |
| Variable | 4 | Nested sub-budget power-builder, see Tier 4 |

---

## 4. Power Modifiers UI — mostly uniform, a few exceptions

The general (`modifiers.json`) and effect-specific (`effect_modifiers.json`) modifier pools are
almost all simple: a checkbox to add the modifier, a rank stepper if `flat: false`, cost
computed automatically. A handful need extra fields:

- **Alternate Effect** (extra): needs a "link to base power" selector, not a rank — it's
  building an array member, which is a structural relationship, not a numeric modifier.
- **Linked** (extra): needs a multi-select of which *other* effect instances in the same Power
  it bundles with.
- **Removable** (flaw): needs a tier selector (equipment / easily-removable / etc.) since the
  discount and the "what has to happen to lose it" condition both depend on tier.
- **Variable Descriptor** (extra): needs a text/tag field for which descriptor pool it can
  shift between, or "any," at GM discretion.
- **Triggered** (extra): needs a trigger-condition text field, same free-text pattern as
  Limited flaws elsewhere.
- **Side Effect** (flaw): needs a text field describing the backfire, plus a toggle for
  "always" vs. "only on failure" (changes cost from -2 to -1 per rank).
- **Affliction's own extras/flaws** (Concentration, Cumulative, Extra Condition, Variable
  Conditions, Decreasing Difficulty, Instant Recovery, Limited Degree, Onset) all interact
  directly with the Tier 3 condition pickers above — e.g. checking Extra Condition should reveal
  a second condition dropdown right next to the first/second-degree pickers, not as a separate
  disconnected control.

---

## 5. Suggested schema extension

Building on `mm-powers-architecture.md` §8's `PowerEffectInstance`, add a free-form `config`
object whose shape depends on the effect's tier:

```
PowerEffectInstance
├── effectId, rank, extras[], flaws[], descriptors[]
└── config: {
      // Tier 2 example (Enhanced Trait)
      target?: { category: "ability"|"skill"|"defense"|"resistance"|"advantage", trait: string }

      // Tier 3 example (Affliction)
      resistedBy?: "dodge"|"fortitude"|"will"
      overcomeBy?: "dodge"|"fortitude"|"will"
      conditions?: { mild: string, moderate: string, major: string, extra?: string }
      variableConditions?: boolean

      // Tier 4 example (Enhanced Senses / Enhanced Movement / Comprehend)
      allocations?: [{ optionId: string, tier: number, rankCost: number }]

      // Tier 4 example (Immunity)
      scopes?: [{ name: string, suggestedRank: number }]

      // Tier 4 example (Variable)
      subBudget?: number
      configuredEffects?: PowerEffectInstance[]   // built at use-time, capped by subBudget

      // Tier 2 example (Nullify / Summon)
      scopeText?: string
      gmApprovalNeeded?: boolean
    }
```

Validate `config` against the effect's tier at both build-time (character creation) and
display-time (character sheet rendering) — a Tier 5 effect should never show an editable
`config`, only a computed readout.

---

## 6. Out of scope for this file

- Exhaustive Enhanced Senses sub-item list (the book's menu runs longer than what's excerpted
  in the table above) — treat that table as a strong starting point and expand it directly from
  the rulebook if you need every last named sense option before shipping the picker.
- The named `CONFIGURATIONS` presets scattered through the effects chapter (Blast, Stun,
  Paralyze, Weaken, Absorption, Telepathic Link, Commlink, and dozens more) — these are
  ready-made effect+modifier bundles for a "quick build" shortcut menu, not part of the core
  configuration surface. Worth a `power-presets.json` later, mirroring the Fighting Styles idea
  flagged in the advantages design doc.
- Environmental Hazard and Damage descriptor controlled vocabularies — free text in the source
  material, same treatment as Power Descriptors generally (see `mm-powers-architecture.md` §9).

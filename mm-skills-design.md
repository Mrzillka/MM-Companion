# Mutants & Masterminds (4th Edition) — Skills Design & Implementation Guide

> **Note on source & copyright:** Original, paraphrased summary of the *functional* rules
> (associated ability, DCs, opposed traits, degree-of-success outcomes, timing, caps) for all
> 21 skills, written for implementing skill-check resolution and character logic. Not a
> reproduction of the rulebook's prose or flavor text. Companion file to `skills.json` (short
> always-visible tooltip copy + core metadata) and `skills-design.json` (this file's data
> catalog — full per-use mechanics, DC tables, and cross-references).

---

## 1. Why skills need a different implementation model than advantages

Advantages (`mm-advantages-design.md`) are 119 unrelated named rules sorted into ~10 *behaviour*
patterns — each pattern is a handler that mutates character state. Skills are the opposite
shape. There are only 21 of them, but each is a small **family of distinct uses** ("sub-checks")
that share an ability and a rank pool but resolve completely differently: Deception's *Bluffing*
is an opposed check, its *Distracting* is an Impress maneuver that imposes a condition, its
*Innuendo* is a static-DC message, its *Resisting* is a resistance-check substitution. Twenty-one
skills expand to 68 distinct uses.

So the practical move here is the inverse of advantages. Advantages: many rows → few handlers.
Skills: few skills → many uses, but those uses collapse into a **small number of resolution
patterns** — each pattern is one function in the check-resolution engine, and each use is just
data (`{ pattern: "...", dc / opposedBy / dcTable, effect: "..." }`). That turns "implement 21
skills" into "implement ~11 resolution patterns, then load 68 rows of parameters."

`skills.json` (already built) carries the short label copy for the UI plus the *core* per-skill
metadata the character sheet needs to price and place a skill: `ability`, `trainedOnly`,
`focused`, `action`, and the flat `specializations`/`focuses` lists. This file's companion,
`skills-design.json`, carries the full per-use mechanical catalog for the *rules engine* — the
part that actually resolves a check at the table. Keep the split: the sheet reads `skills.json`;
the check resolver reads `skills-design.json`.

---

## 2. The resolution patterns

Each `use` in `skills-design.json` names one `pattern`. These are the handler functions in your
check-resolution layer (they'll live alongside `dice.py`'s degree-of-success logic in `core`, and
must never be duplicated per-skill in UI code).

### Pattern 1 — `static_dc`
Roll vs a fixed or table-driven DC set by task difficulty. The most common pattern.
```
function resolveStaticDC(character, skillId, useId, chosenDC):
    bonus = skill_total(character, skillId)        # ability + rank + capped bonuses
    result = d20() + bonus
    return degrees_of_success(result, chosenDC)    # from dice.py
```
The `dc` field is a single number; `dcTable` is a difficulty picker (rows of `{dc, example}`);
`modifiers` is a list of `{value, when}` circumstance adjustments the player toggles. Circumstance
modifiers do **not** count against the skill-bonus PL cap (see §4).
Examples: Acrobatics/Balancing, Athletics/Climbing, Persuasion/Swaying, Technology/Operating.

### Pattern 2 — `opposed_check`
Roll vs a named opposing trait's check result (or its routine `10 + bonus` if the opposition is
passive/unaware). `opposedBy` lists the trait id(s); when more than one is listed, the defender
uses the higher.
```
function resolveOpposed(attacker, defender, skillId, useId):
    a = d20() + skill_total(attacker, skillId)
    d = best_of(defender, use.opposedBy)           # highest of listed traits, or routine
    return degrees(a - d)                           # positive = attacker wins
```
Examples: Deception/Bluffing (vs Deception or Insight), Sleight of Hand/Stealing (vs Perception),
Insight/Evaluate (vs Deception), Persuasion/Negotiating.

### Pattern 3 — `resistance_check`
The skill *substitutes* for a resistance check to avoid or overcome an effect. No new roll type —
it just feeds the skill bonus into the existing resistance-resolution path as the resisting trait.
```
function asResistance(character, skillId, incomingEffectDC):
    return degrees((d20() + skill_total(character, skillId)) - incomingEffectDC)
```
Examples: Insight/Avoid Influence, Deception/Resisting, Intimidation/Resisting.

### Pattern 4 — `impress_check`
An **Impress action** (see `mm-actions-adventure.md`) that imposes a *condition* on a target on
success and escalates it with degrees. The defining trait: the target gets a **new resistance
check at the end of each of its turns** to shed the condition, so this pattern must register the
condition with a recurring-save timer, not just apply a flat state change.
```
ImpressResult {
  condition: "vulnerable" | "defenseless" | "impaired" | "frightened" | "dazed" | ...
  escalatedCondition: string | null   // applied at 2+ degrees of success
  clearsOn: "endOfTargetTurnSave"      // recurring resistance check each turn's end
}
```
Examples: Deception/Distracting (Vulnerable → Defenseless), Intimidation/Demoralizing (Impaired →
Frightened), Intimidation/Coercing (temporary Attitude shift). Several *advantages* extend or
retrigger these (Dazing/Fascinating/Taunting Interaction, Fearsome Presence) — cross-referenced
from the advantages layer, not re-implemented here.

### Pattern 5 — `attack_maneuver`
The skill *feeds* a combat maneuver resolved elsewhere (the actions layer,
`mm-actions-adventure.md`). The skill supplies the check; the maneuver owns the outcome. Model as a
handoff, not a resolution.
Examples: Deception/Tricking → Trick action, Sleight of Hand/Escaping → Escape vs a Grab.

### Pattern 6 — `opposed_hide`
A specialization of `opposed_check` for Stealth: it's opposed by Perception **and** gated by a
precondition (Cover or Concealment must be present, or a distraction/advantage must substitute for
it). The gate check comes first; a failed gate means the use is unavailable, not a failed roll.
```
function resolveHide(character, observer):
    if not hasCoverOrConcealment(character) and not hasDistractionOrHIPS(character):
        return UNAVAILABLE
    return resolveOpposed(character, observer, "stealth", useId)
```
Examples: Stealth/Hiding, Stealth/Hiding via Distraction (−5), Disguise/Create Disguise (Disguise
result becomes the observers' Perception DC).

### Pattern 7 — `attack_bonus`
Not a check at all — a **flat bonus to attack checks** with a configured focus. This is the Close
Combat and Ranged Combat pattern. Two things make it special and both must be enforced:
1. Each focus is an **independent ranked skill instance** (one sheet row per focus), exactly like
   Focused advantages and Focused skills generally (see §5).
2. Its bonus is bounded by the **Attack PL limit** (`attack + effect rank ≤ PL×2`), *not* the
   general skill-bonus limit. `plLimit: "attack"` on the skill flags this.
```
function combatSkillBonus(character, focus):
    return character.combatSkills[focus].rank   # added straight to the attack check
    # validated against power-progression.json's maxAttackPlusEffectRank, not maxSkillModifier
```
Examples: Close Combat (per focus), Ranged Combat (per focus).

### Pattern 8 — `knowledge_check`
Expertise/Magic-Lore question answering. A static-DC check whose distinguishing traits are
**routine eligibility** (`routineEligible: true` → the engine can auto-resolve at `10 + bonus`
without a roll) and **secret GM rolls** (`gmSecret: true` → in a digital toolset, surface the
result to the GM view only). The ability may be swapped from the default per task at GM discretion.
Examples: Expertise/Knowledge, Expertise/Untrained Expertise (capped at DC 15, never routine),
Magic/Lore (trained-only, unlike most knowledge checks).

### Pattern 9 — `extended_construct`
Multi-step build/repair/disable with **build-time ranks** and per-degree time reduction — the
technical-skill core loop. Not a single check: a sequence of DC-15-ish checks, one per degree of
complexity, each success advancing the job and each extra degree cutting the time rank by 1 to a
GM floor; a failure wastes that step's time and restarts it. `complexityTable` on the use carries
the build/repair DCs and time ranks.
```
ConstructionJob {
  complexity: "simple"|"moderate"|"complex"|"advanced"
  stepsRemaining: int                 // = degrees of complexity
  timeRankPerStep: int                // from complexityTable
}
function advanceConstruction(job, checkResult, dc):
    degrees = degrees_of_success(checkResult, dc)
    if degrees < 1: return WASTED_TIME_RESTART_STEP
    job.stepsRemaining -= 1
    reduce_time_rank(job, max(0, degrees - 1))   // extra degrees shave time
    return DONE if job.stepsRemaining == 0 else IN_PROGRESS
```
Examples: Technology/Constructing, Technology/Repairing (incl. jury-rig: −5 DC, standard action,
single fix, lasts to end of scene), Technology/Disabling (secret, security modifiers), Magic/
Technique, Survival/Simple Construction, Expertise/Technical Expertise.

### Pattern 10 — `gm_secret`
A thin pattern for uses whose *entire point* is that the player doesn't learn the raw result
(Insight/Detect Falsehood, Perception/Sight and Hearing when opposing Stealth). Usually layered on
top of `static_dc` or `opposed_check`; the `gmSecret: true` flag is what the UI keys off to route
the result to a GM-only readout.

### Pattern 11 — `narrative`
Resolution is a GM narrative call, not an engine-computed number. Model as a "request GM input" or
"informational" surface, never an auto-resolved effect.
Examples: Languages/Fluency (count of known languages is a lookup, but *which* ones is narrative),
Magic & Technology/Improvised Effects (hand off to the Improvised Effects advantage / Powers).

---

## 3. Schema (skills-design.json)

```
Skill (from skills-design.json.skills[])
├── id, name, ability, trainedOnly, focused
├── categories: ("combat"|"interaction"|"manipulation"|"technical")[]   // rule bundles, see §6
├── action: default action time for the skill
├── summary: string            // one-line mechanical gloss (original paraphrase)
├── specializations[] | focuses[]   // focuses[] only when focused=true
├── plLimit?: "attack"|"none"  // overrides the default skill-bonus cap (§4)
├── rankTable? / complexityTable?   // Languages' doubling table; technical build table
└── uses: Use[]
     ├── id, name
     ├── pattern: one of the §2 pattern ids
     ├── action: action time for THIS use (may differ from the skill default)
     ├── effect: string        // full original mechanical paraphrase of this sub-check
     ├── dc? | dcTable? | dcFormula?
     ├── opposedBy?: traitId[]  // for opposed_* patterns; defender uses the highest
     ├── modifiers?: { value, when }[]   // circumstance adjustments (NOT PL-capped)
     ├── trainedOnly?: bool     // per-use override of the skill-level flag (§7)
     ├── category?: string      // per-use override (e.g. only ONE Investigation use is technical)
     ├── routineEligible?, gmSecret?: bool
     └── crossRefs?: string[]   // "namespace:id" links into other data files (§8)
```

The character sheet keeps reading `skills.json` for placement/pricing; only the check resolver
needs `skills-design.json`. Both are keyed on the same `id`, so a sheet row and its mechanical
catalog join trivially.

---

## 4. The skill-bonus cap — one default, two documented exceptions

Every skill's **total bonus** (ability rank + skill rank + advantage/power bonuses, *excluding*
circumstance modifiers) is capped at **Power Level + 10**. That's `power-progression.json`'s
`maxSkillModifier` column. Validate the sum against it, and — critically — do **not** fold the
`modifiers[].value` circumstance adjustments into the capped total; those are situational and
uncapped.

Two exceptions, both flagged by a `plLimit` field on the skill:

- **`plLimit: "attack"`** — Close Combat and Ranged Combat. Their bonus is governed by the Attack
  PL limit (`maxAttackPlusEffectRank`, i.e. `attack + effect rank ≤ PL×2`), not the skill-bonus
  limit. A validator that checks combat-skill focuses against `maxSkillModifier` will be wrong.
- **`plLimit: "none"`** — Languages. Uncapped except at GM discretion; there's simply no point
  going past rank 8 (128 languages = the cost of Comprehend Languages, which grants all of them).

| Power Level | 1 | 5 | 10 | 15 | 20 |
|---|---|---|---|---|---|
| Max skill modifier (`PL + 10`) | 11 | 15 | 20 | 25 | 30 |
| Max attack + effect rank (`PL × 2`) | 2 | 10 | 20 | 30 | 40 |

---

## 5. Focused skills: one row per configured focus

Skills with `focused: true` (Close Combat, Ranged Combat, Expertise, Languages, Performance) work
exactly like Focused advantages: a character picks a focus when trained, and **each focus is a
separate skill with its own independent rank pool**, stored as its own character-sheet row. Close
Combat: Blades and Close Combat: Unarmed are two rows, not one skill with two sub-targets — and
the Attack PL cap applies to each focus's row independently. `focuses[]` lists *common* examples
only; the GM may approve others, so the config UI should allow a free-text focus, not just a
dropdown of the listed values.

`specializations[]` on a **non-focused** skill are the opposite: they're just illustrative common
uses of the one shared skill (and map loosely onto that skill's `uses[]`), *unless* the player
deliberately buys **specialized ranks** — the half-cost narrow rank pool from §7 — in which case
that specialization becomes its own capped rank pool too.

---

## 6. Skill categories are rule *bundles*, not just tags

The `categories[]` array attaches shared rules that live on the *category*, not the individual
skill. Implement each as a shared modifier the resolver applies before rolling:

- **`combat`** — bonus applies to attack checks; use the Attack PL cap (§4). Close/Ranged Combat.
- **`interaction`** — requires a shared, understood language and a target with mental abilities;
  −5 if the target can't hear/understand you; usable on a group only for a single shared result;
  Immunity can block it. Deception, Intimidation, Performance, Persuasion, Insight.
- **`manipulation`** — requires fine physical manipulation (an AGL and STR rank plus use of limbs,
  or a Precise effect); GM penalty if hands are Impaired/Disabled; characters lacking manipulation
  can still hold ranks and assist via team checks. Sleight of Hand, Vehicles, Treatment,
  Technology, Disguise.
- **`technical`** — requires tools; **−5 without them** (except routine "operate" uses); this is
  the `extended_construct` operate/construct/repair/disable loop. Investigation (partly), Magic,
  Survival (partly), Technology, Treatment, Disguise.

A skill can carry more than one category (Technology is both manipulation and technical), and a
category can apply to only *some* of a skill's uses — hence the per-use `category` override
(Investigation's *Searching* is not technical even though *Gathering/Analyzing Evidence* are).

---

## 7. Trained-only, per-use overrides, and cost

`trainedOnly: true` at the skill level means untrained use auto-fails. But several trained-only
skills have **untrained aspects**, carried as a per-use `trainedOnly: false` override:
Investigation/Searching, Technology/Operating, Vehicles/Operate Vehicle, Expertise/Untrained
Expertise. The resolver checks the use-level flag first, falling back to the skill-level flag.

Cost is data, not code: **1 PP per 2 ranks**, or **1 PP per 4 ranks** for specialized/narrow rank
pools, with each specialization or focus tracked as its own pool. Expertise is inherently priced
at the 4-ranks-per-PP rate because a focus is mandatory (`costNote` on the Expertise entry spells
this out). Don't special-case any of this in `core` — read the rate from the metadata.

---

## 8. Cross-references

`crossRefs` entries are `"namespace:id"` links so the resolver (and a future tooltip/hyperlink
layer) can resolve a use to the rules it touches without string-matching prose. Namespaces in use:
`conditions:` and `advantages:` (existing JSON catalogs), `skills:` (self-references between
skills), `powers:` / `effects:` (the powers layer), `measurements:` (the rank→real-world tables),
`power-progression:` (PL cap columns), and `actions:` / `environmental:` / `equipment:` (the
narrative rules docs — `mm-actions-adventure.md` etc., which are markdown, not JSON, so treat
those as documentation anchors rather than record ids until/unless those chapters get their own
data files).

---

## 9. Out of scope for this file

- **The generic check / degree-of-success math** — that's `dice.py` (`mm-core-mechanics.md`).
  This file only says *which* DC/opposition/outcome each use feeds into it; it never reimplements
  the roll.
- **The Impress, Trick, Feint, Grab, Escape action definitions** — those live in the actions layer
  (`mm-actions-adventure.md`). Skills hand off to them (patterns 4 and 5); they aren't defined
  here.
- **Riding** — technically a separate AGL skill, but by default Athletics stands in for it; not
  modeled as its own skill unless a campaign makes mounts prominent (noted on the Vehicles entry).
- **Vehicle operation specifics and equipment** — the DCs for a Vehicles check under chase/attack
  conditions come from the equipment/vehicles rules, not this file (`crossRef` only).

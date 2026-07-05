# Mutants & Masterminds (4th Edition) — Advantages Design & Implementation Guide

> **Note on source & copyright:** Original, paraphrased summary of the *functional* rules
> (exact bonuses, triggers, caps, formulas) for all 119 advantages, written for implementing
> character logic and combat automation. Not a reproduction of the rulebook's prose or
> flavor text. Companion file to `advantages.json` (short always-visible labels) and
> `advantages-design.json` (this file's data catalog — full mechanics + implementation tags).

---

## 1. Why advantages need a different implementation model than powers

Powers (`mm-powers-architecture.md`) are *assembled* from a small number of effect types with
a handful of stat-integration patterns. Advantages are the opposite: 119 largely unrelated,
individually-named rules. There's no shared cost formula or effect-type taxonomy to hang code
on. Instead, the practical move is to sort them into a **small number of implementation
patterns** — each pattern gets one handler function in your engine, and each advantage is just
data: `{ pattern: "...", params: {...} }`. That turns "implement 119 advantages" into "implement
~10 patterns, then load 119 rows of parameters."

`advantages.json` (already built) has the short label copy for the UI. This file's companion,
`advantages-design.json`, has the full mechanical text plus the pattern tag(s) and parameters
per advantage. An advantage can carry more than one pattern tag (e.g. a Reaction that also
grants a Passive Stat Bonus while active).

---

## 2. The implementation patterns

### Pattern A — `passive_stat_bonus`
A flat, always-on numeric bonus to a named check or trait. No trigger, no GM call needed.
```
function applyPassiveStatBonus(character, advantageInstance):
    target = advantageInstance.params.target        # e.g. "combat.closeAttack"
    amount = advantageInstance.params.perRank * advantageInstance.rank
    character.derivedStats[target] += amount
```
Examples: Close Attack, Ranged Defense, Tough, Great Endurance, Ranged Attack.
Ranked ones just multiply `perRank × rank`; unranked ones use `rank = 1`.

### Pattern B — `conditional_stat_bonus`
Same shape as A, but only applies while a condition holds (a chosen environment, a chosen foe
type, an agility-based Toughness sub, etc.). The condition is usually player-declared or
scene-dependent rather than continuously computed.
```
function isConditionActive(character, advantageInstance):
    # e.g. Favored Environment: player has toggled "in favored environment" on
    # e.g. Favored Foe: current target matches the configured foe category
    return character.flags[advantageInstance.params.conditionFlag] == True
```
Examples: Favored Environment, Favored Foe, Defensive Roll (voided while Vulnerable/
Defenseless/Stunned), Uncanny Dodge (voids Vulnerable-from-Surprise specifically).

### Pattern C — `action_modifier`
Changes the rules of an existing named action from `mm-actions-adventure.md` §1 — usually
removing a penalty or adding a bonus to a specific action's check, or changing what trait
resolves it.
```
function getActionModifier(character, actionName):
    mods = character.advantages.filter(a => a.params.action == actionName)
    return sum(m.params.bonus for m in mods) - sum(m.params.penaltyRemoved for m in mods)
```
Examples: Improved Disarm (+2/+5 to Disarm), Improved Smash (removes Smash's penalty),
Fast Feint (removes Feint's simple-action penalty), Agile Grab (removes Vulnerable-while-
grabbing), Improved Trip (target doesn't choose resistance).

### Pattern D — `reaction_trigger`
Grants a reaction usable off-turn when a specific trigger fires. Model as an event listener
registered on the character; the engine fires it when the trigger condition is detected during
combat resolution.
```
Reaction {
  trigger: "onCloseAttackMissedBySelf" | "onCriticalHitScored" | "onAllyTargetedByAreaOrRanged" | ...
  grantedAction: "block" | "grab" | "trip" | "disarm" | "attack" | ...
  actionPenalty: int (0 or -5, if the reaction version is penalized)
}
```
Examples: Counterattack, Riposte, Redirect, Weapon Bind, Weapon Break, Defensive Grab,
Defensive Throw, Critical Volley, Follow-Up Strike, Snap Shot, Swift Strike, Reflexive Block,
Interpose, Damaging Escape, Reverse Hold, Menacing Attack, Tactical Advance, Dive for Cover.

### Pattern E — `resource_limited_use`
A benefit usable a limited number of times per adventure or scene, tracked as a depleting
counter that resets on a timer. Almost always Heroic-type, and almost always capped by the
character's **total ranks in heroic advantages** (a separate PL-linked budget — see
`mm-core-mechanics.md` §7's Heroic Advantages cap) in addition to (or instead of) its own rank.
```
ResourceTracker {
  usesRemaining: int          # = advantage.rank, or advantage.rank capped by heroicAdvantageBudget
  resetsOn: "adventure" | "scene"
}
function canUse(tracker): return tracker.usesRemaining > 0
function consume(tracker): tracker.usesRemaining -= 1
function resetAll(characters, cadence): # call at adventure/scene boundaries
    for c in characters:
        for t in c.resourceTrackers where t.resetsOn == cadence: t.usesRemaining = t.rank
```
Examples: Determination, Guidance, Luck, Edit Scene, Prepared Effect, Partner Bond,
Well-Equipped, Untapped Potential (modifies Extra Effort math, not a separate counter — see
below), Ultimate Effort (Focused: one tracker per configured check).

### Pattern F — `hero_point_spend`
Requires spending a Hero Point (the character's separate resource — see
`mm-core-mechanics.md`'s Hero Points note) rather than a private per-advantage counter.
```
function useHeroPointAdvantage(character, advantageId):
    if character.heroPoints <= 0: return False
    character.heroPoints -= 1
    applyEffect(advantageId)   # e.g. +5 skill bonus, act first in initiative, guarantee a 20
    return True
```
Examples: Beginner's Luck, Seize Initiative, Leadership, Inspiration (spends 1 HP, no personal
counter), Encouragement, Rush of Victory, Fallen Inspiration (passive — granted to allies, not
spent by the owner), Holding Back, Partner Bond (also resource-limited — both tags apply).

### Pattern G — `skill_usage_rule`
Changes *how* a skill can be used rather than adding a numeric bonus: untrained use, rerolls,
routine-check eligibility, or substituting one skill/trait for another.
```
SkillRule {
  ruleType: "allow_untrained" | "reroll_keep_best" | "always_routine" | "substitute_trait"
  scope: "all_skills" | "one_skill" | "one_specialization"
  substituteFor: string | null   # e.g. Tactical Genius: Intellect substitutes for Presence
}
```
Examples: Jack-of-All-Trades, Know-It-All, Skill Expertise (reroll), Skill Mastery (always
routine), Animal Empathy (removes the −10 penalty), Tactical Genius (Intellect for Presence
in Command-advantage limits), Alternate Initiative (AGL → INT/AWE/PRE for Initiative).

### Pattern H — `npc_follower`
Grants an NPC (Minion or Sidekick) built with its own Power Point budget scaling with the
advantage's rank. This is a full character-generation call, not a stat patch.
```
function buildFollower(advantageInstance, kind):
    pp = advantageInstance.rank * (15 if kind == "minion" else 5)
    return generateNPC(powerPoints=pp, isMinion=(kind=="minion"), attitude="Helpful")
```
Examples: Minion, Sidekick.

### Pattern I — `gm_adjudicated`
The mechanical trigger is clear, but the actual outcome/content is a GM narrative call, not
something the engine computes. Model these as "request GM input" prompts in a digital toolset
rather than auto-resolved effects.
```
function requestGMRuling(advantageId, context): 
    # surfaces a prompt in the UI; does not auto-resolve
    return promptGM(advantageId, context)
```
Examples: Benefit, Connections, Contacts, Well-Informed, Eidetic Memory, Assessment (the
GM secretly rolls opposed Insight vs. Deception and narrates the result), Edit Scene, Guidance,
Trance (partly mechanical — see its own DC formula — partly narrative).

### Pattern J — `command_grant`
A Command action (see `mm-actions-adventure.md` action catalog) that lets the owner extend a
benefit to allies within sight/hearing range, sometimes at a Hero Point cost.
```
function commandGrant(owner, alliesInRange, benefit, cost):
    if cost == "heroPoint" and owner.heroPoints <= 0: return False
    if cost == "heroPoint": owner.heroPoints -= 1
    for ally in alliesInRange: applyTemporaryBenefit(ally, benefit, duration="untilOwnerNextTurn")
```
Examples: Leadership, Inspiration, Up and At 'Em, Dive for Cover, Tactical Advance,
Rush of Victory.

---

## 3. Rank caps — three different flavors, now with real numbers

`advantages-design.json`'s `maxRank` field is `null` unless the book gives a specific number —
but every entry now also carries a `maxRankFormula` field and its `effect` text spells out the
resolved numbers at a few sample Power Levels, so nothing requires cross-referencing another
file at runtime. Three distinct cap styles, each needing different validation code:

### 1. Fixed numeric cap (`maxRankKind: "fixed"`)
A specific number regardless of Power Level. Validate `rank <= maxRank` directly.
`Dive for Cover: 2`, `Elusive Target: 2`, `Evasion: 2`, `Favored Foe: 2`, `Fearless: 2`,
`Improved Block: 2`, `Improved Critical: 4`, `Improved Disarm: 2`, `Inspiration: 5`,
`Takedown: 2`.

### 2. Power-Level-derived cap (`maxRankKind: "power_level"`)
Most "+1 per rank to a Power-Level-limited trait" advantages aren't capped by their own
number — they're capped because they add to a combined total that's already tracked
elsewhere (`power-progression.json`'s `powerLevelLimits`). Each entry's `maxRankFormula`
names the exact column:

| Advantage | Shares a cap with | PL 1 | PL 5 | PL 10 | PL 15 | PL 20 |
|---|---|---|---|---|---|---|
| Close Attack, Ranged Attack | `maxAttackPlusEffectRank` (Attack side) | 2 | 10 | 20 | 30 | 40 |
| Improved Strike, Improvised Weapons, Throwing Mastery | `maxAttackPlusEffectRank` (Damage side) | 2 | 10 | 20 | 30 | 40 |
| Close Defense, Ranged Defense | `maxDefenseOrDodgePlusToughness` (Defense side) | 2 | 10 | 20 | 30 | 40 |
| Defensive Roll, Tough | `maxDefenseOrDodgePlusToughness` (Toughness side) | 2 | 10 | 20 | 30 | 40 |

These are *shared* totals — the advantage's rank stacks with the character's base rank in that
trait and any other bonus sources, all counting against the same PL-derived ceiling.

### 3. Improved Initiative — a standalone formula (`maxRankKind: "power_level_half"`)
The one advantage with its own unique cap, separate from both of the above: `ceil(PowerLevel/2)`
ranks in *this specific advantage*, nothing else.

| Power Level | 1 | 5 | 10 | 15 | 20 |
|---|---|---|---|---|---|
| Max rank | 1 | 3 | 5 | 8 | 10 |

### 4. The shared Heroic-advantage budget (`maxRankKind: "heroic_budget"`)
`floor(PowerLevel/2)` — but crucially this is **one pool shared across every Heroic-type
advantage on the sheet**, not a per-advantage number. Ranked members of the pool
(Determination, Edit Scene, Guidance, Luck, Partner Bond, Prepared Effect, Untapped Potential,
Well-Equipped) draw variable amounts from it; unranked members (Encouragement, Holding Back,
Improvised Effect, Ultimate Effort) each draw a flat 1 per instance.

| Power Level | 1 | 5 | 10 | 15 | 20 |
|---|---|---|---|---|---|
| Total Heroic-advantage ranks available | 0 | 2 | 5 | 7 | 10 |

```
function validateHeroicBudget(character):
    total = sum(a.rank if a.ranked else 1
                for a in character.advantages
                if "Heroic" in a.types)
    budget = floor(character.powerLevel / 2)
    return total <= budget
```
A PL 10 character who takes Edit Scene 2 and Guidance 3 has already spent all 5 available
ranks — Luck, Determination, or any other Heroic advantage would need the budget freed up
elsewhere (or a higher Power Level) before it could be added.


---

## 4. Suggested schema

```
Advantage (from advantages-design.json)
├── id, name, types[], ranked, maxRank, maxRankKind: "fixed"|"power_level"|"heroic_budget"|null
├── effect: string                      // full original mechanical paraphrase
├── patterns: string[]                  // one or more of the Section 2 pattern ids
└── implementation: {
      target?: string,                  // stat path this modifies, e.g. "combat.closeAttack"
      perRank?: number,                 // bonus per rank, for pattern A/B
      action?: string,                  // named action this modifies, for pattern C
      trigger?: string,                 // event name, for pattern D
      resetsOn?: "adventure"|"scene",   // for pattern E
      cost?: "heroPoint"|null,          // for pattern F/J
      followerType?: "minion"|"sidekick", // for pattern H
      notes?: string                    // anything that doesn't fit the fields above
    }
```

---

## 5. Focused advantages: one row per configured instance

Advantages tagged `Focused` (Alternate Feint, Benefit, Close Combat-style ones, Dazing/
Fascinating/Taunting Interaction, Favored Environment, Favored Foe, Favored Environment,
Improved Critical, Improvised Effect, Instant Counter, Minion, Partner Bond, Ricochet, Set-Up
is *not* Focused but Split Attack/Skill Expertise/Skill Mastery/Sidekick/Ultimate Effort are)
must be stored as **one character-sheet row per configured choice**, each independently ranked
and costed — exactly like Focused skills in `skills.json`. A character with Favored Foe:
Ninjas and Favored Foe: Demons has two separate advantage instances, not one Favored Foe
advantage with two "sub-targets."

---

## 6. Out of scope for this file

- **Fighting Styles / Super Fighting Styles** (named bundles of advantages like "Boxing" or
  "Judo") — these are just curated presets, not new mechanics. Worth a `fighting-styles.json`
  preset list later if your character creator offers starter templates, but not modeled here.
- **Leadership Styles** — a similar bundling concept for Command-type advantages, referenced
  at the end of the source chapter but not detailed there either; would need its own extraction
  pass if you build that feature.

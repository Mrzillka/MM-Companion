# Mutants & Masterminds (4th Edition) — Conditions Design & Implementation Guide

> **Note on source & copyright:** Original, paraphrased summary of the *functional* rules
> (penalties, action limits, superseding/bundling logic, recovery checks, debilitation cascades)
> for all conditions. Not a reproduction of the rulebook's prose or flavor text. Companion file to
> `conditions.json` — the single consolidated data catalog (short always-visible `tooltip` copy,
> core `includes`/`supersedes` metadata, and full mechanics + resolution tags).

---

## 1. Why conditions need their own implementation model

Skills and advantages are things a character *has* and *uses*; conditions are things that *happen
to* a character and then have to be **tracked, combined, and cleared** over the course of a fight.
The engine problem isn't "look up what Stunned does" — it's four different problems layered on top
of each other:

1. **Combination.** A character can hold several conditions at once, and their effects stack
   (Fatigued + Prone). Some conditions are umbrellas that *bundle* others (`includes`), and some
   *replace* others of lesser severity (`supersedes`). Applying and removing one condition can
   ripple through the others.
2. **Multiplicity.** A few conditions apply *more than once* to the same character and accumulate
   (Hit's stacking −1). Most don't.
3. **Debilitation.** Some conditions don't just penalize a trait — they *remove* it, and removing
   certain traits cascades into hard conditions (Debilitated Strength → Incapacitated).
4. **Qualification.** Some conditions are meaningless until you say *what* they apply to — which
   trait (Impaired → "Attack Impaired"), which sense (Unaware → "Sight Unaware"), which descriptor
   (Susceptible to "Fire Damage"), or which controller (Controlled by whom). That's a UI input, a
   stored value, and sometimes a scoped mechanical effect.

So the model here isn't a pattern-per-behaviour taxonomy like advantages. It's a **small,
uniform condition record** (severity, what it bundles, what it replaces, whether it stacks, whether
it debilitates a trait, whether it needs a parameter) plus a handful of **engine subsystems** that
read those fields generically. `conditions.json` carries both the structural fields the
character sheet needs (`includes`, `supersedes`) and the mechanical depth the combat tracker
needs (`mechanisms`, `stacking`, `parameter`, `debilitates`, `recoveryCheck`, and the concrete
penalty/mod fields), plus a short `tooltip` line per record.

---

## 2. The condition record (conditions.json)

```
Condition
├── id, name
├── category: "condition" | "damage_condition" | "object_damage_condition" | "meta"
├── includes: string[]        // conditions this one bundles (umbrella) — see §3
├── supersedes: string[]      // less-severe conditions this one replaces — see §3
├── mechanisms: string[]      // which engine subsystems touch this (§4) — the dispatch tags
├── stacking: bool            // true only for conditions that apply multiple times (§5)
├── parameter: null | {       // the combobox / text input when a subject must be named (§6)
│     type: "trait_select" | "sense_select" | "descriptor_text" | "character_ref",
│     required: bool,         // required=false but supplied ⇒ narrative-only scoping
│     label, help, options?   // options[] populates a combobox; absent ⇒ free text
│   }
├── debilitates: null | {     // trait-loss cascade when this removes a trait (§7)
│     cascade: { <traitName>: conditionId[] },
│     notes: string
│   }
├── effect: string            // full original mechanical paraphrase
├── recovery: string          // prose recovery description
├── recoveryCheck?: {...}     // structured recovery for recurring_save conditions (§8)
└── (typed effect fields)     // penalty, speedRankMod, attackMods, defenseMod,
                              // resistanceMod, stackingRule, randomTable — read by §4 subsystems
```

The typed effect fields exist so the engine never parses `effect` prose. `Impaired` carries
`penalty: -2`; `Hindered` carries `speedRankMod: -1`; `Prone` carries an `attackMods` block;
`Vulnerable` carries `defenseMod: { defense: "halve", dodge: "halve" }`; `Hit` carries
`stackingRule`; `Confused` carries `randomTable`. Add a field, not a special case.

---

## 3. Combination: `includes` (bundling) and `supersedes` (replacement)

These are the two structural relationships, and they compose. Model an applied condition set as a
**flattened effect set with provenance**, not a flat list of names.

**`includes` — umbrella bundles.** Applying `Incapacitated` applies `Defenseless`, `Stunned`, and
`Unaware` under it. Store each member with a back-reference to the umbrella that granted it.
- Removing the umbrella removes every member it granted.
- Conversely, if all members are individually cleared, the umbrella *may* be considered cleared
  (a GM's-call flag in a digital toolset, not an automatic collapse).
- Members resolve **individually**: removing `Dazed` from a `Staggered` (Dazed + Hindered)
  character leaves `Hindered`, and the `Staggered` umbrella is now partial.

**`supersedes` — severity replacement.** A more-severe condition replaces the ones it supersedes
rather than stacking. `Stunned` supersedes `Dazed`; `Defenseless` supersedes `Vulnerable`;
`Immobile` supersedes `Hindered`. On apply, drop any superseded conditions currently present; on
removal of the superseding condition, the superseded ones do **not** automatically return (they
were replaced, not suppressed).

**The two interact per-part.** Superseding applies to *parts* of a bundle. Stunning a `Staggered`
character yields `Stunned` (superseding the `Dazed` half) `+ Hindered`. Immobilizing a `Staggered`
character yields `Dazed + Immobile` (superseding the `Hindered` half). So the resolver expands
bundles first, then applies supersession across the flattened set.

```
function applyCondition(state, newCond, provenance):
    expanded = expandIncludes(newCond)          # umbrella → members, tagged with provenance
    for c in expanded:
        removeSuperseded(state, c.supersedes)    # per-part replacement
        addOrStack(state, c, provenance)         # §5 decides add-vs-stack
```

Some supersession is **trait-scoped**: `Disabled` supersedes `Impaired` only where they hit the
same trait (an "Attack Impaired" character who becomes "Perception Disabled" keeps both). The
resolver compares the `parameter` value (§6), not just the id.

---

## 4. Mechanisms: the dispatch tags

`mechanisms[]` names which engine subsystems a condition feeds. Each is one place in the derived-
stats / turn-resolution layer; the condition contributes data, the subsystem does the math. This
keeps condition handling out of per-condition `if` chains.

- **`action_limit`** — caps available actions (Dazed = 1 standard + free, no reactions; Stunned =
  none at all). Read by the turn/action-economy layer.
- **`check_penalty`** — a flat check penalty, all checks or a scoped category (`penalty` field +
  `parameter` scope). Impaired −2, Disabled −5, Frightened −5 (scoped to the fear object).
- **`defense_mod`** — Defense/Dodge alteration (Vulnerable halves both; Defenseless zeroes Defense
  and auto-fails Dodge). Read by `rules` defense derivation.
- **`movement_mod`** — speed-rank alteration (`speedRankMod`: Hindered −1, Immobile/Prone → 0).
- **`perception_mod`** — awareness / auto-failed Perception, whole or per-sense (Unaware, Blind,
  Deaf). Reads the `sense_select` parameter.
- **`resistance_mod`** — scoped resistance penalty (Susceptible, Weakness); `resistanceMod` carries
  the `-floor(effectRank/2)` formula and, for Weakness, the "best outcome = one degree of failure"
  cap.
- **`attack_mod`** — attack checks by/against the character (Prone's `attackMods`).
- **`stacking_penalty`** — accumulates per instance (Hit only; see §5).
- **`recurring_save`** — a recovery check on a cadence (see §8).
- **`debilitate_trait`** — removes a trait and may cascade (see §7).
- **`random_action`** — the turn's action is rolled, not chosen (Confused's `randomTable`).
- **`narrative`** — no computed effect; surface a GM prompt/note (Broken, Transformed's flavor,
  Hallucination content).

A condition usually carries several tags (Incapacitated: `action_limit`, `defense_mod`,
`perception_mod`, `recurring_save`).

---

## 5. Multiplicity: which conditions stack

**`stacking: true` is the rare case — it's `Hit` and nothing else** in the base set. Every other
condition is idempotent: applying Stunned to a Stunned character changes nothing.

Model stacking conditions as an **instance count** on the applied-condition record, not a boolean:

```
AppliedCondition {
  id: "hit"
  count: int                      // stacking conditions only; others treat as 1
  provenance: umbrellaId | source
}
```

`Hit`'s `stackingRule` says `perInstancePenalty: -1`, `appliesTo: damageResistanceChecks`,
`removedPerRecovery: 1`. So five Hits = −5 to further Damage resistance, and each successful
recovery check removes **one** instance (decrement `count`, don't clear). This is the mechanical
reason a fresh attacker grinds a target down: each Hit makes the next resistance check worse.
Objects accumulate Hits identically but have no self-recovery. Contrast the umbrella damage
conditions (Dazed/Stunned/Incapacitated) — those don't stack; they escalate by supersession.

---

## 6. Qualification: the `parameter` block (comboboxes and text inputs)

A condition with a non-null `parameter` **cannot be fully applied until the user names its
subject.** This is the combobox/text-input requirement. Four input types drive the UI:

| `type` | UI control | Stored value | Examples |
|---|---|---|---|
| `trait_select` | combobox (from `options[]`), free-entry allowed | trait/skill/power name | Debilitated, Impaired, Disabled |
| `sense_select` | combobox of senses | sense name | Unaware ("Sight Unaware") |
| `descriptor_text` | free text (no `options`) | descriptor/effect string | Susceptible, Weakness, Frightened, Hallucination degrees, Transformed |
| `character_ref` | picker of characters in the encounter | character id | Compelled, Controlled |

Three behaviours follow from the parameter:

1. **Name folding.** The chosen value folds into the displayed condition name: `Impaired` + `Attack`
   → "Attack Impaired"; `Unaware` + `Sight` → "Sight Unaware". Store the base id + the parameter;
   render the composed name.
2. **Scoping.** For `check_penalty` / `perception_mod` / `resistance_mod` conditions, the parameter
   *scopes the mechanical effect* — "Attack Impaired" penalizes only attack checks. The subsystem
   reads the parameter to decide what the penalty touches, and trait-scoped supersession (§3)
   compares parameters.
3. **`required` vs narrative-only.** `required: true` blocks application until filled (you can't be
   Susceptible to nothing; you can't be Controlled by no one). `required: false` means the
   parameter is optional flavor — e.g. `Hallucinating`/`Figment`/`Phantasm`/`Delusion` take an
   optional `descriptor_text` describing *what* is hallucinated, which is **narrative-only**: the
   mechanical effect (Unaware-of-real-world for Delusion) is fixed, and the text just annotates the
   instance. When `required=false` and a value is supplied, treat it as a note, not a mechanical
   scope, unless the condition also carries a scoping mechanism.

So the UI rule is simple: **null parameter → apply immediately; non-null → show the control;
`required` → gate the apply button on it.**

---

## 7. Debilitation: trait loss and its cascade

`debilitate_trait` conditions don't penalize a trait — they *remove* it, and removing certain
traits triggers hard conditions. This is `Debilitated` (and `Transformed`, which can zero traits).
The cascade is **data**, keyed by the chosen trait (from the condition's `trait_select`
parameter), so the engine never hardcodes a per-condition branch:

| Debilitated trait | Cascade | Extra rule |
|---|---|---|
| Strength | Incapacitated | — |
| Stamina | Dying | stabilization checks at −5; if **Absent** (no Stamina), Dying is treated as Dead |
| Agility | Paralyzed | — |
| Intellect / Awareness / Presence | Unaware | stays Unaware until the ability is restored to ≥ −5 |
| Attack | *(none)* | auto-fails all attack checks |
| Defense | Defenseless | — |
| Initiative | *(none)* | can't roll Initiative; acts last (ties broken high-to-low regular Init) |
| a Skill | *(none)* | reads as untrained (0 ranks) |
| an Advantage | *(none)* | the advantage stops functioning |
| a Power | *(none)* | the power doesn't function at all until restored |

```
function applyDebilitation(state, cond):
    trait = cond.parameter.value
    for c in cond.debilitates.cascade[trait]:      # may be empty
        applyCondition(state, lookup(c), provenance=cond.id)
    markTraitUnusable(state, trait)                 # auto-fail checks requiring it
```

Two engine consequences worth honoring: a character **auto-fails any check requiring a debilitated
or absent trait** (wire this into `dice.py`'s resolution, before rolling), and **debilitated
ability ranks can't be lowered further**. "Absent" traits (rank shown as `–`) behave like
permanently debilitated ones with the Stamina→Dead special case above; the same cascade table
drives both, so model Absent as a fixed-trait flag that reuses this path rather than a separate
condition.

---

## 8. Recovery: the `recurring_save` cadence

Damage conditions and a few others clear via a repeated check. `recoveryCheck` structures it:

```
recoveryCheck {
  trait: "STA" | null            // the resisting ability (null for time-based rest)
  dc: int | null
  cadence: "end_of_turn" | "start_of_turn" | "per_minute" | "time_rank_9_rest" | ...
  condition?: string             // gate, e.g. "damage-caused", "no worse damage condition present"
  outcome?: string               // non-standard result (Dying's degree logic; Hit removes one instance)
}
```

Cadence differences matter to the turn scheduler: Dazed/Stunned check **at the end of the turn**,
Dying checks **at the start of each turn** (with degree-of-failure death tracking), Hit/Incapacitated
check **once per minute**, Staggered checks per minute **only if no worse damage condition is
present** (the `condition` gate), and Fatigued/Exhausted clear on **rest time-ranks**, not checks.
Non-damage causes of the same condition may need a different check entirely — the `condition:
"damage-caused"` gate flags when the STA/DC-10 default applies vs. when the GM sets the recovery.

The damage ladder itself (`hit → dazed → staggered → stunned → incapacitated → dying → dead`) is
recorded in `_meta.damageLadder` for the Damage-effect resolver; conditions here just describe each
rung. Object damage runs the shorter `hit → broken → destroyed` ladder.

---

## 9. Out of scope for this file

- **The Damage effect and resistance-check resolution** that *impose* these conditions — that's the
  powers/damage layer (`mm-powers-architecture.md`) and `dice.py`. This file describes what each
  condition *does* once applied, not how an attack lands it.
- **Actions that clear conditions** (Stand, Escape, the Recover action, Treatment revive) — those
  are defined in the actions/skills layers (`mm-actions-adventure.md`, `mm-skills-design.md`);
  conditions only name them in `recovery`.
- **Afflictions / effect degrees** — the Affliction effect chooses *which* conditions it inflicts
  at each degree from this catalog; the mapping lives with that effect, not here.
- **Minion/menace special-casing** (minions take the worst degree of failure automatically;
  criticals auto-max the effect) — that's combat resolution, not a property of the conditions.

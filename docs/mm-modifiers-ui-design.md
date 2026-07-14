# Mutants & Masterminds (4th Edition) — Power Modifier UI Design

> **Note on source & copyright:** Original, paraphrased summary of the *functional* UI/config
> implications of every extra and flaw — not a reproduction of rulebook prose. Companion to
> `mm-powers-ui-design.md` (same treatment, for base effects), `modifiers.json` (general pool),
> and `effect_modifiers.json` (effect-specific pool, corrected this pass — see §0).

---

## 0. Audit results: what was missing from effect_modifiers.json

You were right to ask. Re-checking every effect's source text against the JSON turned up real
gaps, now fixed:

| Effect | Was in JSON | Actually missing | Status |
|---|---|---|---|
| **Concealment** | Only the `Resistible` flaw | Extras: **Affects Others**, **Precise**. Flaws: **Blending**, **Limited**, **Partial**, **Passive** | Fixed — 6 of 7 modifiers had been dropped |
| **Move Object** | Nothing | Extras: **Damaging**, **Perception**, **Precise**, **Subtle**. Flaws: **Close Range**, **Concentration**, **Limited Direction**, **Limited Material** | Fixed — entire entry was missing |
| **Regeneration** | Nothing | Extras: **Diehard**, **Persistent**, **Sustained**. Flaws: **Not Against [Descriptor]**, **Only When Dead**, **Only When Incapacitated**, **Source** | Fixed — entire entry was missing |
| **Remote Sensing** | Simultaneous, Subtle, Targeting only | Extras: **Communication**, **Dimensional**, **Protected** | Fixed — 3 extras added |
| **Summon** | Multiple Minions, Sacrifice, "Telepathic Command", Variable Type | The 3rd one is actually named **Mental Link**, not "Telepathic Command." Also missing: **Controlled**, **Heroic**, **Horde**, **Memory Merge** | Fixed — renamed + 4 extras added |
| **Enhanced Senses** | Nothing (treated as menu-only) | It has a menu of sub-abilities *and* its own general extras/flaws layered on top: **Affects Others**, **Area**, **Ranged** (extras); **Limited**, **Noticeable**, **Unreliable** (flaws) | Fixed — new entry added |

I re-verified (not just assumed) that **Comprehend, Postcognition, Precognition, Speed, and
Swimming** genuinely have zero effect-specific modifiers in the source text — those empty
entries were correct, not oversights. **Enhanced Movement** genuinely has no extras/flaws
beyond its option menu either (confirmed directly, unlike Enhanced Senses which does).

I did not do a line-by-line re-audit of every remaining effect's entry beyond spot-checking —
if you want full certainty, the same process (grep the effect's name, read straight through to
the next effect header, diff against the JSON) works for the rest, but the six above were the
ones that showed clear signs of incompleteness on inspection.

---

## 1. The core question: does a modifier change the constructor, or just the notes?

Most of the 37 general extras + 24 general flaws + ~150 effect-specific ones are **cosmetic
from a UI perspective**: check a box, maybe set a rank, the cost formula updates, and a
human-readable clause gets appended to the power's description text. Nothing else in the
builder changes shape.

A smaller set **structurally change the constructor** — they add a new input field, remove one,
cross-reference another power on the sheet, or trigger a validation warning. Those are the ones
worth flagging individually, because generic "checkbox + rank" handling will silently produce a
broken or incomplete power if you don't special-case them.

### 1a. Notes-only modifiers (the default — no special UI code needed)
The overwhelming majority. Examples: Accurate, Area Effect (mostly — see §3), Aura, Homing,
Impervious, Increased Mass, Indirect, Insidious, Multiattack, Penetrating, Reach, Ricochet,
Secondary Effect, Subtle, Activation, Distracting, Fades, Feedback, Inaccurate, Increased
Action, Noticeable, Permanent (flaw), Quirk, Resistible, Tiring, Uncontrolled, Unreliable —
and the equivalent effect-specific ones like Damage's Strength-Based *cost formula aside* (the
toggle itself is simple; see §2 for why the cost math isn't). For these, your generic modifier
picker (checkbox/rank-stepper, auto-computed cost, auto-generated notes clause) is sufient.

### 1b. Structural modifiers (need bespoke fields — full list)
| Modifier | Effect scope | What the UI must add |
|---|---|---|
| **Strength-Based** | Damage | Cost formula changes to use effective rank — see §2 |
| **Linked** | General | Multi-select of other effect instances to bundle with — see §3 |
| **Alternate Effect** | General | Combo box picking the base power + cost-ceiling warning — see §3 |
| **Limited Degree** | Affliction | Combo box picking which degree tier to disable, removes that tier's condition picker — see §4 |
| **Variable Conditions** | Affliction | A points spin (1 or 2 /rank). At 2 it defers all three condition pickers to a "chosen at use-time" note; at 1 it shows a "which degree" combo and defers only that one, leaving the other two editable |
| **Extra Condition** | Affliction | Upgrades all three degree pickers to multi-select (a second same-degree condition each) |
| **Onset** | Affliction | Combo picking when the conditions land — one round (flat −1) or one scene (−1 per rank); the choice flips the modifier between the flat and per-rank cost buckets |
| **Empowering** | Affliction | +2/rank; adds a Notes line tallying the transformed form's bonus power points (rank × 15) |
| **Reversible** (Affliction) | Affliction | Combo picking the reversal reach — within the effect's range (+1 flat) or any distance (+2 flat) |
| **Increasing Difficulty** | Affliction | +1/rank; warns unless Cumulative or Progressive is also attached (needs repeated checks to escalate) |
| **Removable** | General | Tier selector (equipment / easily-removable / etc.), changes both discount and the "how do you lose it" condition text |
| **Variable Descriptor** | General | Free-text/tag field for the descriptor pool, or "any" |
| **Triggered** | General | Free-text trigger-condition field |
| **Side Effect** | General | Free-text backfire description + toggle (always vs. only-on-failure, changes cost) |
| **Limited** (any effect) | General | Free-text condition field — appears on ~15 different effects, always the same shape |
| **Affects Others** | General (many effects) | Toggle that unlocks a target-selection step at use-time; if combined with Ranged, also unlocks a range field |
| **Selective** | General (paired with Area Effect) | No build-time field, but flags the power as needing an "who's excluded" prompt at use-time |
| **Area Effect** | General | Needs a shape field (cone/sphere/cube/line) — see §3's note on the Shape sub-extra |
| **Reversible** | General | Toggle only, but changes the power's runtime behavior (owner can end conditions at will) — flag it in the power's action summary, not just notes |
| **Sense-Dependent** | General | Free-text "which sense" field |
| **Enhanced Trait's Reduced Trait** | Enhanced Trait | Second trait-target picker (which trait goes down) alongside the existing "which trait goes up" picker |
| **Multiple Minions / Variable Type / Controlled / Heroic** | Summon | These change what the follow-on "configure the minion" step looks like — see the Minion/Sidekick advantage pattern in `mm-advantages-design.md` |
| **Mental Link (Summon)** | Summon | Toggle only, but should surface a "command minions telepathically" affordance in the combat UI once active |
| **Nullify's scope** | Nullify | Already Tier 2 in the effects UI doc — free text, GM-approval flag |
| **Immunity's scope** | Immunity | Already Tier 4 in the effects UI doc — repeatable (name, suggested rank) list |

---

## 2. Strength-Based Damage — the cost formula the constructor must implement

This is the one place where a modifier changes *arithmetic*, not just adds a field, so it's
worth spelling out exactly. Per your example: base effect cost 1/rank, purchased rank 2,
Strength rank 3, one per-rank extra costing +1/rank.

**Naive (wrong) calculation** — treats Strength-Based as if it did nothing to per-rank extra
pricing:
```
cost = rank * (base_per_rank + extra_per_rank) = 2 * (1 + 1) = 4
```
**Correct calculation** — Strength contributes to the *effective rank* that per-rank extras
price against, because the attack's real potency is (purchased rank + Strength), even though
Strength-Based itself is a free (+0/rank) modifier:
```
effective_rank_for_extras = purchased_rank + strength_rank = 2 + 3 = 5

base_cost  = purchased_rank * base_per_rank            = 2 * 1 = 2
extra_cost = effective_rank_for_extras * extra_per_rank = 5 * 1 = 5

total_cost = base_cost + extra_cost + flat_modifiers = 2 + 5 + 0 = 7
```
Which is exactly your worked example, just regrouped: `2*(1+1) + 3*(1) = 4 + 3 = 7`.

**UI implications:**
- A **Strength rank spin box** appears once Strength-Based is checked. Default it to the
  character's actual current Strength rank (read-only display in normal mode), but make it
  editable in a "what-if" planning mode, since a build tool may want to preview costs before
  the character's Strength is finalized, or price a power meant to scale with a *future*
  Strength increase.
- The cost engine must branch: **flat/one-time extras and all flaws still price off the
  purchased rank only** (unaffected). **Only per-rank extras** get priced against the combined
  effective rank. Get this distinction wrong and either flat modifiers overcharge or per-rank
  ones undercharge.
- Live-recompute the total whenever either the Damage rank stepper *or* the Strength spin box
  changes — this is the one modifier where changing a completely different part of the
  character sheet (Strength) should ripple into a power's displayed cost.
- Validate the Power Level cap using `purchased_rank + strength_rank` as the attack's effective
  rank for the `maxAttackPlusEffectRank` check (`power-progression.json`), not just the
  purchased rank — this was flagged in your project's earlier design notes and belongs in the
  same code path as this cost formula, not a separate check.

---

## 3. Linked and Alternate Effect — cross-referencing other effects/powers

Both of these break the assumption that a modifier only concerns the effect it's attached to.

### Linked (extra)
**What it needs:** a multi-select combo box listing the *other effect instances already added
to the same Power* (Linked effects bundle within one Power and activate together — they must
share the same Range). Selecting one or more creates a `linkGroup` relationship (see
`mm-powers-architecture.md` §8's schema) rather than a numeric value.
- **Validation:** all linked effects must have matching Range; the UI should grey out or warn
  on any effect instance with a different Range than the first one selected.
- **Cost:** Linked itself is +0 — the linked effects' costs simply add together as normal,
  so no separate cost field is needed here, just the relationship.

### Alternate Effect (extra)
**What it needs:** a combo box picking the **base power** this new effect becomes an alternate
for (forming a Power Array), pulled from the character's existing powers.
- **Warning condition — exactly what you flagged:** once a base power is selected, compute the
  new alternate effect's own total cost (rank × per-rank costs + flat modifiers, same formula
  as any effect) and compare it to the base power's total cost:
```
if (alternate.totalCost > basePower.totalCost):
    show_warning("This alternate effect (X points) costs more than the base power "
                 "it's attached to (Y points). Per the rules, an alternate effect's own "
                 "cost can't exceed the base power's cost — raise the base power's rank, "
                 "reduce this alternate's rank/extras, or increase the base power's own "
                 "extras until it's home ranked.")
```
- This should be a **hard validation error**, not just a soft warning, since it's a real rule
  violation (`mm-powers-architecture.md` §4: "as long as its own total cost doesn't exceed the
  base power's cost").
- **Also validate:** the base power can't have Permanent duration (array members must be
  switchable), and the base power itself can't already be an alternate of something else
  (arrays are one level deep, not nested).
- **Cost of the extra itself:** flat 1 or 2 points depending on complexity, same as any flat
  extra — this part *is* just a rank-1-or-2 stepper, the combo box + warning is the special part.

---

## 4. Affliction's Limited Degree — removing a picker, not adding one

This is the inverse of most structural modifiers: instead of adding a field, it **removes
one of the three condition pickers** from the Tier 3 Affliction screen (`mm-powers-ui-design.md`
§2, Tier 3).

**UI flow:**
1. Player checks the **Limited Degree** flaw on an Affliction effect.
2. A combo box appears: **"Which degree has no condition?"** — options: Mild (1st degree),
   Moderate (2nd degree), Major (3rd degree).
3. Once chosen, **hide that tier's condition dropdown entirely** from the Affliction config
   screen — there's nothing to pick, since that degree of failure simply does nothing.
4. **Limited Degree can be taken twice** (per the rules), each application costing -1/rank
   again. If the player adds a second application:
   - Show a **second** "which degree" combo box.
   - **Validate that it targets a different tier than the first application** — disable/grey
     out whichever tier is already selected in the first instance, so the same degree can't be
     disabled twice (that would just be a wasted purchase, and the rules describe it as
     covering two *different* degrees).
   - With both applications, two of the three condition pickers are hidden, leaving only one
     tier configurable.
5. The power's generated notes text should read out the actual effect clearly, e.g. *"This
   Affliction has no effect at 1 degree of failure; imposes [condition] at 2 degrees; imposes
   [condition] at 3+ degrees."* rather than silently having blank tiers.

This same "hide a picker instead of adding one" pattern is worth watching for if you build out
any other effect's tiered configuration later — Affliction is the only one in the base 42 that
needs it today, but the same code path (a modifier that removes rather than adds a field) is
reusable if a homebrew effect ever needs it.

---

## 5. Suggested schema note

Extend the `config` object from `mm-powers-ui-design.md` §5 with a `crossReferences` field for
modifiers that point at other effects/powers, and a `hiddenFields` array for modifiers that
suppress part of the normal config screen:

```
PowerEffectInstance.config additions:
├── crossReferences?: {
      linkedEffectIds?: string[]         // Linked extra
      alternateOfPowerId?: string         // Alternate Effect extra
    }
├── hiddenFields?: string[]               // e.g. ["conditions.mild"] when Limited Degree targets that tier
└── effectiveRankOverrides?: {            // Strength-Based and similar
      perRankExtrasBase: number           // e.g. purchasedRank + strengthRank
    }
```

---

## 6. Other things worth noting (beyond what you flagged)

A few more patterns surfaced during this audit that are worth being aware of, even though they
weren't in your list:

- **Affects Others + Ranged, appearing on ~8 different effects** (Concealment, Immunity,
  Insubstantial, Enhanced Senses, Burrowing, etc.): the pairing always means the same thing —
  "grant this to someone else" (Affects Others) plus optionally "at a distance" (Ranged). Worth
  a single reusable UI component rather than reimplementing per effect.
- **The "Limited" flaw appears on well over a dozen effects** with a free-text condition field
  every time. Same reusable component opportunity.
- **Summon's Controlled/Heroic/Multiple Minions/Variable Type extras all change what the
  "configure your minion" follow-up screen looks like** — this deserves the same attention as
  the Minion/Sidekick advantages' `npc_follower` pattern in the advantages design doc, since
  it's effectively the same "build a sub-character" UI problem appearing in a third place now
  (Minion advantage, Sidekick advantage, Summon effect).
- **Enhanced Trait's Reduced Trait flaw** needs a *second* trait-target picker (which trait
  goes down) in addition to the existing "which trait goes up" one from the Tier 2 effects UI
  doc — I'd missed calling this out explicitly there, worth patching that file too if you want
  full consistency.

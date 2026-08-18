# Powers: what the app is missing against the core rulebook

**Temporary working document.** Written 2026-08-18, on `docs/powers-rules-audit`.
Delete it once the code items below have been scheduled or built.

---

## 1. Method and source

The reference is the **Mutants & Masterminds core rulebook, 4e Origin Edition**, now
extracted to `reference/core-book/p0001.txt … p0242.txt` (gitignored; see the `rulebook`
skill). **Printed page = PDF page − 2.** Every citation below is a PDF page.

What was read in full and diffed:

| Read | Against |
| --- | --- |
| Chapter 6: Powers, PDF p98–163 | `src/mm_companion/data/effects.json` |
| Generic extras p150–158; generic flaws p159–163 | `src/mm_companion/data/modifiers.json` |
| Per-effect extras/flaws, throughout p109–149 | `src/mm_companion/data/effect_modifiers.json` |
| Cost rules, p100 and p150 | `src/mm_companion/core/rules/powers_cost.py` |
| Standard power configurations, p236 (by effect) and p237 (by name) | — nothing to compare |

### A trap worth recording

`pdftotext` reads the wide multi-column summary grids **column-wise, not row-wise**. The
*Power Effects* tables on **PDF 108 and 234–235 are therefore scrambled**: the NAME column
survives, every other cell lands on the wrong row. They read plausibly and are wrong — the
p108 grid, for instance, shows Burrowing as an Attack effect and Illusion at 10 points per
rank. Nothing in this audit is based on them; every value comes from the effect's own entry
in the chapter (those start at p109 and run alphabetically). Now flagged in
`reference/core-book/INDEX.md`.

---

## 2. What is already correct

Stated explicitly so nobody re-audits it.

- **All 42 effect names match the book exactly**, including the Origin-Edition renames
  (*Fortune Control*, not Luck Control; *Enhanced Movement*, not Movement). Nothing is
  missing and nothing is invented.
- **Every effect's type / action / range / duration / check / resistance / base cost
  matched**, bar the two fixed in §3. Verified individually against p109–149.
- **Affliction's three condition lists match the book degree for degree** (p109),
  including *Figment* and *Indifferent* at first degree and *Unfavorable* at second.
- **The generic flaw list is complete**: all 24 of Activation … Unreliable (p159–163).
- **The per-effect extra/flaw lists are complete** for every effect that has any. Five
  effects have no `effect_modifiers` entry and correctly need none — the book lists no
  effect-specific extras or flaws for **Enhanced Movement, Postcognition, Precognition,
  Speed** or **Swimming**.
- **The cost engine already implements the hard parts** of p150: the sub-1-PP-per-rank
  ratio rule (`1 point per (2 − net) ranks`), partial modifiers applied to only some of an
  effect's ranks, flat-vs-per-rank bucketing with flat modifiers applied after the
  multiplication, and array pooling. `docs/notes/powers.md` documents all of it.
- **The Alternate Effect cost ceiling** ("an alternate may not cost more than the base
  power", p100) is satisfied structurally: `array_base_index` makes the *costliest* member
  the base, so the rule cannot be broken. No check needed.
- **Mind Reading's resistance is right as written.** `"Will vs. Effect"` renders as
  `Will vs. <DC>`, which is the p135 shut-out check the target makes at the end of each of
  their turns; the initial contact is the opposed check the `check` field already carries.

---

## 3. Fixed in this pass (data and docs only)

### Wrong effect parameters — `effects.json`

| Effect | Field | Was | Now | Cite |
| --- | --- | --- | --- | --- |
| Comprehend | `action` | `Free` | `None` | p114 |
| Illusion | `resistance` | `Perception vs. Effect` | `Insight or Perception vs. Effect` | p131 |

### Base-cost labels — `effects.json`

Label text only. `baseCostValue` and the cost engine are **unchanged**; these effects are
still charged their floor. The point of the label is to stop the gap being silent on the
card until §4A is built.

| Effect | Was | Now | Cite |
| --- | --- | --- | --- |
| Illusion | `1+ per rank` | `1–5 per rank` | p131 |
| Obscure | `1+ per rank` | `1–10 per rank` | p139 |
| Remote Sensing | `5 per rank` | `5–10 per rank` | p142 |
| Transmute | `2+ per rank` | `2–5 per rank` | p147 |

### Enhanced Senses allocation — `effects.json` (p121–123)

> **⚠ This reprices saved characters.** Any sheet whose Enhanced Senses spent ranks on
> Counters Illusion, Counters Concealment, Penetrates Concealment, Radius, Ranged or Rapid
> may now cost more or allocate differently. There is no migration and no warning — the
> allocation is re-read against the corrected tiers on load. Applied deliberately.

Corrected tiers:

| Option | Was | Now | Why |
| --- | --- | --- | --- |
| Counters Illusion | `[1]` | `[2]` | flatly 2 ranks in the book |
| Counters Concealment | `[2]` | `[2, 5]` | 2 = one Concealment effect, 5 = all of them for a sense type |
| Penetrates Concealment | `[4]` | `[2, 4]` | 2 = a non-accurate sense type, 4 = an accurate sense |
| Radius | `[1]` | `[1, 2]` | 2 = an entire sense type |
| Ranged | `[1]` | `[1, 2]` | 2 = an entire sense type |
| Rapid | `[1, 2, 3]` | `[1, 2]` | +3 time ranks per rank; the second tier is a whole sense type, not more speed |

Descriptions for Rapid and Extended were rewritten — both said "ten times per tier", which
confused *how much faster or further* with *how many senses*.

Added (each 1 rank, all previously absent): **Awareness**, **Detect**, **Time Sense**.

Deliberately **not** added: Enhanced Senses' **Dimensional**, priced at **+1/+2/+3 points
flat** rather than in ranks (p122). A flat-point option does not fit the `tiers` shape —
see §4L.

### Four missing generic extras — `modifiers.json`

| Added | Cost | Cite | Note |
| --- | --- | --- | --- |
| Affects Corporeal | +1 per rank | p150 | existed only as an Insubstantial-specific extra |
| Affects Insubstantial | 1 or 2 points flat | p150–151 | absent entirely; at rank 1 the target still resists with +half the effect rank |
| Alternate Defense | varies | p151 | attack lands vs. Fortitude or Will (that rank + 10) instead of Defense |
| Alternate Resistance | varies | p152 | existed only as a Nullify-specific +0 extra |

The last two are modelled as a four-option `select`: *a generally easier number* (+1),
*no real gain* (+0), *the worst of two* (+1), *the best of two* (**−1**). The negative
option is right — the book makes that configuration a flaw — and the engine handles it,
since `_modifier_config_cost` returns the option's value and the category supplies the sign.

### Ten variable-cost modifiers that could not be dialled — `modifiers.json`

Each stated a range in `costFormula` but had no config field, so all were stuck at their
cheapest value. Both shapes used here already existed in the file (`Subtle` uses a `points`
field, `Removable`/`Side Effect` a `select` carrying `costValue`), so this needed no Python.

| Modifier | Range | Cite | Config added |
| --- | --- | --- | --- |
| Indirect | 1–4 flat | p155 | `points` 1–4, hinted with what each rank buys |
| Dimensional | 1–3 flat | p153 | `points` 1–3 |
| Reversible | 1 or 2 flat | p157 | `points` 1–2 |
| Perception Range | +1 or +2 /rank | p156 | `select`: upgraded from Ranged (1) or Close (2) |
| Ranged | +1 or −1 /rank | p156 | `select`: from Close (+1) or from Perception (**−1**) |
| Affects Objects | +0 or +1 /rank | p151 | `select`: people and objects (1) or objects only (0) |
| Affects Others | +1 or +0 /rank | p151 | `select`: you and others (1) or others only (0) |
| Close | −1 or −2 /rank | p159 | `select`: reduced from Ranged (1) or Perception (2) |
| Activation | −1 or −2 flat | p159 | `select`: an extra simple (1) or standard (2) action |
| Removable | add the Equipment tier | p162 | third option, `costValue: 4` |

Removable's real **per-5-total-points** formula is still not implemented — this only widens
the existing simplified flat discount. See §4D.

### Per-effect corrections — `effect_modifiers.json`

- **Comprehend had no entry at all.** Added its one flaw, **Limited Type**: −1 point flat
  for a broad category, −2 for a narrow one (p115), as a `points` 1–2 field.
- **Insubstantial → Precise** read `"included in configuration"` and cost 0. The book
  prices it at **+1 point flat** (p134).
- **Insubstantial → Reaction** was missing entirely: **+1 point flat**, phase in or out on
  your reaction and change state twice a round rather than once (p134).

### Docs

- `reference/core-book/INDEX.md` — the scrambled-grid warning above, plus 18 landmark pages
  (effect entries begin p109; extras p150–158; flaws p159–163; the two configurations
  tables p236/p237; Enhanced Senses p121–123; Environment p124–125; …).
- `docs/notes/powers.md` — a bullet recording that the data is now verified against the
  book by page, that the base-cost gap in §4A must **not** be "fixed" by editing
  `baseCostValue`, and the scrambled-grid trap.
- `docs/mm-powers-architecture.md` — names the missing `by_configuration` base-cost mode as
  the `BASE_COST_KINDS` registry's reason for existing, and records that Dynamic Alternate
  Effects are described in that document but not modelled.

### Tests touched

`tests/test_data_loader.py` — three count assertions (modifiers 65 → 69, effect-modifier
groups 36 → 37, total effect modifiers 231 → 233, catalog 296 → 301).
`tests/test_power_constructor.py` — the duplicate-attach test used `ranged` as its example
of a config-less modifier; Ranged now has config, so it uses `penetrating` instead. See §4M
for the underlying issue that swap exposes.

---

## 4. Needs code — ranked by impact

### A. Base cost by configuration — the biggest correctness gap

Five effects are priced by *how they are configured*, and every one is charged its floor:

| Effect | Real cost | Cite |
| --- | --- | --- |
| Illusion | 1 per rank per sense type, **sight counts as two**, up to 5 (all types) | p131 |
| Obscure | 1 per sense, 2 per sense type, sight double, up to 10 | p139 |
| Remote Sensing | 5 for one sense type, +1 per further type, 10 for all; sight counts as two (so sight Remote Sensing is 6) | p142 |
| Transmute | 2 / 3 / 4 / 5 by how broad the source and the result are | p147 |
| Environment | each sub-effect 1 or 2 per rank, **added together** | p124–125 |

An all-senses Illusion currently costs a fifth of what it should.

**Shape of the fix.** A new `baseCostMode` — `by_configuration` — registered into
`BASE_COST_KINDS` (`core/rules/powers_cost.py:387`), reading a new config field on each
effect and summing the chosen entries' per-rank costs. The registry seam already exists and
is designed for exactly this; nothing in `powers_cost.py` needs restructuring, and
`register_base_cost_kind` means a mod could add another. Each of the five effects also
needs the config field itself — a multiselect of sense types for Illusion / Obscure /
Remote Sensing, a select for Transmute, an allocation for Environment.

### B. Environment has no configuration at all

`effects.json` gives Environment no `config` and a flat `baseCostValue: 1`. The book
(p124–125) has eight sub-effects, each 1 or 2 points per rank:

Cold · Hazardous Movement · Heat · High Gravity · Hindered Movement · Illumination ·
Low Gravity · Visibility

Multiple sub-effects are combined by *adding* their per-rank costs (or taken as alternate
effects). The **Variable** extra (+1 per rank, already in the data) then lets the player
redistribute up to that per-rank total at will, which is what the *Weather Control*
configuration is built on (Environment, Variable up to 4 per rank, 5 per rank total). Same
handler as A, plus the budget check.

### C. Dynamic Alternate Effects

`array_alternate_cost()` (`powers_cost.py:970`) reads one `costValue` off the
`alternate_effect` record, so every alternate costs 1 point. The book (p101):

- an alternate that is **Dynamic** costs **2**, not 1;
- making the array's **primary** power Dynamic costs **1**;
- Dynamic members **share the array's point pool and run simultaneously** at reduced rank,
  reallocated once per turn as a free action;
- a member needs a minimum allocation to function at all (2 points of Flight = Flight 1).

Needs a per-alternate flag on `Power` / `PowerGroup`, cost changes, and runtime allocation
state alongside the existing `array_active` / `active_child_id`.

### D. Removable's real formula

The book (p161–162) charges **−1 (Removable) / −2 (Easily Removable) / −4 (Equipment) per 5
points of the whole power's final cost, rounded up**, and it applies to the **power**, not
to one effect. Its worked example: a 98-point armour is 98 ÷ 5 = 19.6 → 20, so −20 points,
down to 78.

The app charges a flat −1 / −2 / −4 on a single effect, and the field's own hint admits it.
Fixing it is a genuine structural change: every flat modifier today is priced inside
`effect_total_cost`, and this one needs the power's total before it can be computed — a
second pass in `power_total_cost` / `node_cost`.

Two sub-rules also unmodelled: **Short-Term Only** (−1 flat off the flaw's own value, and it
may reduce that value to 0, p161), and the removal circumstances that distinguish the tiers
(Removable needs you Stunned *and* Defenseless; Easily Removable can be taken with a Disarm
or Grab during action time).

### E. Standard power configurations — the largest missing feature

The book's two appendix tables (p236 by effect, p237 by name) list **~90 named
configurations**, and they are how M&M character write-ups are actually written: nobody says
"Damage, Ranged", they say **Blast**. The app has **no catalog of them at all** — grepping
`data/` for "Blast" hits only `equipment.json`.

Representative entries, all with exact builds in the tables: Blast · Strike · Weapon ·
Damage Aura · Mental Blast · Dazzle · Snare · Stun · Toxin · Paralyze · Suffocation ·
Mind Control · Hallucination · Transform · Weaken · Affliction Aura · Invisibility ·
Inaudibility · Darkness · Silence · Static · Wards · Force Field · Armored Skin ·
Force Constructs · Matter Shaping · Wings · Radar · Sonar · Spatial Sense · True Sight ·
X-Ray Vision · Psychokinesis · Cyclone · Gravity Field · Tether · Energy Tendrils ·
[Matter] Moving · Poltergeist · Duplication · Portal · Scrying · Astral Projection ·
Telepresence · Commlink · Interface · Telepathic Link · Psychic Connection · Gadgets ·
Shapeshift · Animal Mimicry · Power Mimicry · Material Mimicry · Skill Mimicry ·
Power Theft · Trait Boost · Absorption · Berserker Rage · Ageless · Water Breathing ·
Environmental Immunity · Mental Immunity · Fortitude Immunity · Will Immunity ·
[Effect] Resistance · Flashlight · Mist · Weather Control.

Wants a new data file plus a "start from a configuration" entry point in the Power
Constructor — prefilled effect, modifiers and config, then editable like anything else.

Two shortcuts worth knowing. About **25 of them are simply `Feature 1`** (Animal Harmony,
Battery, Built-in Equipment, Charmed Life, Chill, Dimensional Pocket, Display, Higher
Guidance, Insulating Fur, Internal Compartment, Iron Stomach, Light Sleeper, Lucid Dreamer,
Massive, Megaphone, Mimicry, Quick Change, Remote, Shade, Special Effect, Temporal Inertia,
Weatherproof — p127–128) and would slot straight into Feature's existing `repeatable` config
as a picklist. And several depend on §4A being built first: Darkness / Silence / Static are
Obscure configurations, Invisibility / Inaudibility are Concealment ones.

### F. Nested trait budgets

Four places buy a whole sub-character's worth of points, and none of them records it:

| Where | Budget | Cite |
| --- | --- | --- |
| Summon | minion built on **rank × 15** PP, minion characteristics, may not have minions of its own | p145 |
| Morph → Metamorph extra | one complete alternate trait set **per rank**, same point total, same PL limits | p136 |
| Variable | **rank × 5** Variable Power Points, reallocated on a Concentrate action | p148 |
| Affliction → Empowering extra | the third-degree Transformed form is built on **rank × 15** PP | p110 |

Only the last has even a note (`notePerRank: 15` renders a Notes line). Probably one
feature: a nested point-budget editor reusing the character model.

### G. Affliction's imposed-effect budget is not validated

An Affliction that imposes the **Transformed** condition may name a Personal-range effect to
impose (Morph them, Shrink them, Teleport them away). That effect **must cost no more than
the Affliction's total cost** and must take a standard action or less (p110). The app
records the condition but not the imposed effect, and checks neither.

### H. Concealment and Obscure sense bookkeeping

Both effects buy **senses** with their ranks — 1 per sense, 2 per sense type, **sight costs
double** (2 for one sight sense, 4 for all), and Concealment from touch senses is impossible
short of Insubstantial (p115, p139). Neither has a config recording *which* senses, so
nothing can check that a Concealment 5 spent its ranks legally, and the Invisibility /
Inaudibility / Darkness / Silence / Static configurations cannot be expressed. Pairs
naturally with §4A — Obscure needs the sense list for its cost anyway.

### I. Countering effects

p107: take the **Ready** action, then when the opponent acts, spend your reaction and make
an **opposed effect check** (d20 + rank each) between opposed descriptors. Winning cancels
both. Ongoing effects can be countered the same way with a normal use. Nullify counters any
effect of a chosen descriptor. The `Instant Counter` advantage skips the Ready.

There is no roll spec for any of this, so the roller cannot offer it and a Nullify's opposed
check has to be done by hand.

### J. Improvised Effects

`Improvised Effect` and `Prepared Effect` exist in `advantages.json`, but none of the
arithmetic does (p101–102):

- preparation takes **time rank = the effect's PP cost**, minimum time rank 3 (one minute);
- **−1 time rank per +5** added to the check DC, down to that minimum;
- **+2 check bonus per +1 time rank** of extra preparation;
- preparation DC = **10 + PP cost + 5 per time-rank reduction**;
- use DC = **10 + PP cost**; a prepared effect lasts one scene;
- fast-prep for a Hero Point skips preparation entirely.

This is a calculator over an assembled-but-unbought power — the constructor already knows
the cost, so it is a small feature sitting on a number that exists.

### K. Extra Effort and power stunts

Not modelled anywhere (`grep` finds the phrase only in data descriptions). Chapter 1 rather
than Chapter 6, but the Powers chapter leans on it constantly: a **Sustained** effect can be
pushed with Extra Effort and used for stunts, a **Permanent** one cannot (p106, p157); a
power stunt is a temporary alternate effect bought with Extra Effort and a Hero Point
(p101, p106); Regeneration's Sustained extra exists precisely so Extra Effort can reach it
(p142). Several flaws — Tiring, Fades, Short-Term — are written in terms of it.

### L. Enhanced Senses' Dimensional option

The book lists Dimensional among the Enhanced Senses options but prices it at **+1/+2/+3
points flat** (p122), not in ranks. The allocation UI's `tiers` are ranks, so it does not fit
and was left out of §3. Either allocation options need to support a flat-point cost, or
Dimensional becomes an extra on the effect rather than an allocation entry.

### M. "Has config" is a poor proxy for "may be taken twice"

`EffectCard.attach_modifier` (`ui/power_constructor/effect_card.py:829`) refuses a second
copy of a modifier **only when it has no config fields**, on the reasoning that config is
what tells two copies apart (Limited "only at night" beside Limited "only vs. robots").

That was already loose — Removable, Side Effect and Check Required all carry config and none
of them is meaningfully repeatable — and §3 widens the exposure to Ranged, Perception Range,
Close, Activation, Affects Others and Affects Objects, where a second copy would
double-charge while overriding nothing new. It is visible rather than silent (both chips
show on the card and either can be deleted), which is why it was not treated as a blocker.

The fix is small: a `repeatable` (or `unique`) flag in `modifiers.json`, one line in
`_parse_modifier`, and one clause in `attach_modifier` — replacing the proxy with the actual
question.

### N. A deliberate deviation, kept

`modifiers.json` carries an **`Attack` extra costing +0 points**. It does **not** appear in
the 4e Origin Edition modifier list (p150–158) — it is a 3e modifier, retained here as the
engine's `grantsAttack` seam: it is what `effect_makes_attack` reads to decide an effect
rolls to hit, and it is what lets a non-attack effect be delivered as an attack. It is
marked implicit on Affliction and Damage.

**Leave it.** Recorded here so a later audit neither deletes it as non-RAW nor "restores"
some 3e reading of it.

# Powers: auditing the app against the core rulebook

**Working document for a multi-pass job.** Started 2026-08-18 on branch
`docs/powers-rules-audit`. It is both the record of what has been done and the brief for
what is left — a later session should be able to pick up any outstanding item from this
file alone. Delete it when §6 is empty.

---

## 0. Status

| § | Item | State |
| --- | --- | --- |
| §4 | Data corrections across effects, modifiers, per-effect modifiers | **done** — pass 1 |
| §5A | Base cost by configuration (Illusion, Obscure, Remote Sensing, Transmute) | **done** — pass 2 |
| §5B | Environment's configuration | **done** — pass 2 |
| §5C | The 90 standard power configurations | **done** — pass 3 |
| §5D | 18 effect-specific modifiers that could not be dialled | **done** — pass 3 |
| §5E | Flat flaws floored at 1 point | **done** — pass 3 |
| §5F | Removable's real per-5-points formula (was §6B) | **done** — pass 4 |
| §5G | Enhanced Senses' Dimensional option (was §6I) | **done** — pass 5 |
| §5H | A `repeatable` flag replaces "has config" (was §6J) | **done** — pass 5 |
| §6A | Dynamic Alternate Effects | outstanding |
| §6C | Nested trait budgets (Summon / Metamorph / Variable / Empowering) | outstanding |
| §6D | Affliction's imposed-effect budget check | outstanding |
| §6E | Concealment's sense bookkeeping | outstanding |
| §6F | Countering effects | outstanding |
| §6G | Improvised Effects arithmetic | outstanding |
| §6H | Extra Effort and power stunts | outstanding |

Commits so far, all on `docs/powers-rules-audit` off `develop`, **not yet merged**:

```
b0b713d  Correct the powers data against the core rulebook
1594b5b  Record the remaining powers gaps in a working audit
734a45d  Price the configured effects from their configuration
ebac2c9  Add the rulebook's standard power configurations
b880efb  Turn the powers audit into a handover brief
9a10545  Price Removable from the whole power, per 5 points
```

---

## 1. How to work on this

**Branch.** Stay on `docs/powers-rules-audit` until the user says the whole job is done,
then merge into `develop` with `--no-ff`. One feature, one branch (`CLAUDE.md`). Never
commit on `develop` or `main`.

**Verify, every pass:**

```bash
ruff check . && black --check .
python -m pytest -q                 # ~9 min, 2771 tests as of pass 5
python -m pytest tests/test_powers.py tests/test_power_constructor.py \
                tests/test_data_loader.py tests/test_powers_section.py -q
```

CI does **not** run on a work branch automatically — `gh workflow run CI --ref
docs/powers-rules-audit` if a full matrix run is wanted before merging.

**See it in the app** rather than trusting the tests alone — the `run-mm-companion` skill,
or a throwaway script in the scratchpad that imports `driver._pump` / `driver._shoot` from
`.claude/skills/run-mm-companion/driver.py`, builds a `PowerConstructorWindow`, drives the
real widgets and grabs a PNG into `_driver_shots/` (gitignored). Every pass so far found at
least one thing in the screenshot that the tests did not ask about.

**Look a rule up** with the `rulebook` skill: `reference/core-book/INDEX.md` first (it
carries the page offset, a chapter map and the landmarks this job added), then `Grep` the
per-page text. Never `Read` the PDF — it renders pages as images. The whole `reference/`
tree is gitignored, so edits to `INDEX.md` are local-only and will not appear in `git
status`; they still pay for themselves and are worth making.

**Do not reformat the JSON data files.** They are hand-formatted with small objects kept
on one line (`{ "value": "Dodge", "label": "Dodge" }`), and a `json.dump(indent=2)`
round-trip explodes them into thousands of lines of noise. Edit them by anchored string
replacement in a short Python script, then `json.loads` the result to prove it still
parses. `configurations.json` is the one file written by a generator at indent-2
throughout; see §3.

---

## 2. Method and source

The reference is the **Mutants & Masterminds core rulebook, 4e Origin Edition**, extracted
to `reference/core-book/p0001.txt … p0242.txt`. **Printed page = PDF page − 2.** Every
citation in this file is a **PDF** page.

What was read in full and diffed:

| Read | Against |
| --- | --- |
| Chapter 6: Powers, PDF p98–163 | `src/mm_companion/data/effects.json` |
| Generic extras p150–158; generic flaws p159–163 | `src/mm_companion/data/modifiers.json` |
| Per-effect extras/flaws, throughout p109–149 | `src/mm_companion/data/effect_modifiers.json` |
| Cost rules, p100 and p150 | `src/mm_companion/core/rules/powers_cost.py` |
| Standard power configurations, p236 (by effect) and p237 (by name) | `src/mm_companion/data/configurations.json` |
| Sense types, p63 | — used by Illusion / Obscure / Remote Sensing |

### A trap worth recording

`pdftotext` reads the wide multi-column summary grids **column-wise, not row-wise**. The
*Power Effects* tables on **PDF 108 and 234–235 are therefore scrambled**: the NAME column
survives, every other cell lands on the wrong row. They read plausibly and are wrong — the
p108 grid shows Burrowing as an Attack effect and Illusion at 10 points per rank. Nothing
here is based on them; every value comes from the effect's own entry in the chapter (those
start at p109 and run alphabetically). The two *Standard Power Configurations* tables (236,
237) survive intact and are fine. Flagged in `reference/core-book/INDEX.md`.

---

## 3. What is already correct

Stated explicitly so nobody audits it twice.

- **All 42 effect names match the book exactly**, including the Origin-Edition renames
  (*Fortune Control*, not Luck Control; *Enhanced Movement*, not Movement).
- **Every effect's type / action / range / duration / check / resistance / base cost
  matched**, bar the two fixed in §4. Verified individually against p109–149.
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
  multiplication, and array pooling.
- **The Alternate Effect cost ceiling** ("an alternate may not cost more than the base
  power", p100) is satisfied structurally: `array_base_index` makes the *costliest* member
  the base, so the rule cannot be broken. No check needed.
- **Mind Reading's resistance is right as written.** `"Will vs. Effect"` renders as
  `Will vs. <DC>`, which is the p135 shut-out check the target makes at the end of each of
  their turns; the initial contact is the opposed check the `check` field already carries.

### One deliberate deviation, kept

`modifiers.json` carries an **`Attack` extra costing +0 points** that does **not** appear
in the 4e Origin Edition modifier list (p150–158). It is a 3e modifier retained as the
engine's `grantsAttack` seam — what `effect_makes_attack` reads to decide an effect rolls
to hit, and what lets a non-attack effect be delivered as an attack. It is marked implicit
on Affliction and Damage. **Leave it.** Recorded so a later audit neither deletes it as
non-RAW nor "restores" some 3e reading of it.

### How `configurations.json` is maintained

It was written once by a throwaway generator script (scratchpad, **not committed**) and is
**hand-maintained from here on**. Keeping the generator would have meant a second source of
truth that silently diverges the first time someone edits the JSON directly. It is written
at plain `indent=2` — unlike its hand-compacted siblings — precisely because it is a large,
regular file where uniform formatting reads better.

---

## 4. Pass 1 — data corrections (`b0b713d`)

### Wrong effect parameters — `effects.json`

| Effect | Field | Was | Now | Cite |
| --- | --- | --- | --- | --- |
| Comprehend | `action` | `Free` | `None` | p114 |
| Illusion | `resistance` | `Perception vs. Effect` | `Insight or Perception vs. Effect` | p131 |

### Base-cost labels — `effects.json`

Label text only at the time; §5A has since made the cost real, and `baseCostValue` is now
the *unconfigured floor* rather than the whole story.

| Effect | Was | Now | Cite |
| --- | --- | --- | --- |
| Illusion | `1+ per rank` | `1–5 per rank` | p131 |
| Obscure | `1+ per rank` | `1–10 per rank` | p139 |
| Remote Sensing | `5 per rank` | `5–10 per rank` | p142 |
| Transmute | `2+ per rank` | `2–5 per rank` | p147 |

### Enhanced Senses allocation — `effects.json` (p121–123)

> **⚠ This repriced saved characters.** Any sheet whose Enhanced Senses spent ranks on
> Counters Illusion, Counters Concealment, Penetrates Concealment, Radius, Ranged or Rapid
> may now cost more or allocate differently. There is **no migration and no warning** — the
> allocation is re-read against the corrected tiers on load. Applied deliberately, with the
> user's agreement. If a later pass adds a "your build changed" notice, this is the case it
> exists for.

| Option | Was | Now | Why |
| --- | --- | --- | --- |
| Counters Illusion | `[1]` | `[2]` | flatly 2 ranks in the book |
| Counters Concealment | `[2]` | `[2, 5]` | 2 = one Concealment effect, 5 = all of them for a sense type |
| Penetrates Concealment | `[4]` | `[2, 4]` | 2 = a non-accurate sense type, 4 = an accurate sense |
| Radius | `[1]` | `[1, 2]` | 2 = an entire sense type |
| Ranged | `[1]` | `[1, 2]` | 2 = an entire sense type |
| Rapid | `[1, 2, 3]` | `[1, 2]` | +3 time ranks per rank; the second tier is a whole sense type, not more speed |

Rapid's and Extended's descriptions were rewritten — both said "ten times per tier", which
confused *how much faster or further* with *how many senses*. Added (1 rank each, all
previously absent): **Awareness**, **Detect**, **Time Sense**.

These corrections are what made True Sight (7 points) and X-Ray Vision (4 points) come out
right in §5C — a useful independent check that the tiers are now correct.

### Four missing generic extras — `modifiers.json`

| Added | Cost | Cite | Note |
| --- | --- | --- | --- |
| Affects Corporeal | +1 per rank | p150 | existed only as an Insubstantial-specific extra |
| Affects Insubstantial | 1 or 2 points flat | p150–151 | absent entirely; at rank 1 the target still resists with +half the effect rank |
| Alternate Defense | varies | p151 | attack lands vs. Fortitude or Will (that rank + 10) instead of Defense |
| Alternate Resistance | varies | p152 | existed only as a Nullify-specific +0 extra |

The last two are a four-option `select`: *a generally easier number* (+1), *no real gain*
(+0), *the worst of two* (+1), *the best of two* (**−1**). The negative option is right —
the book makes that configuration a flaw — and the engine handles it, since
`_modifier_config_cost` (`powers_cost.py:33`) returns the option's value and the category
supplies the sign.

### Ten generic modifiers that could not be dialled — `modifiers.json`

Each stated a range in `costFormula` but had no config field, so all were stuck at their
cheapest value.

| Modifier | Range | Cite | Config added |
| --- | --- | --- | --- |
| Indirect | 1–4 flat | p155 | `points` 1–4 |
| Dimensional | 1–3 flat | p153 | `points` 1–3 |
| Reversible | 1 or 2 flat | p157 | `points` 1–2 |
| Perception Range | +1 or +2 /rank | p156 | `select`: upgraded from Ranged (1) or Close (2) |
| Ranged | +1 or −1 /rank | p156 | `select`: from Close (+1) or from Perception (**−1**) |
| Affects Objects | +0 or +1 /rank | p151 | `select`: people and objects (1) or objects only (0) |
| Affects Others | +1 or +0 /rank | p151 | `select`: you and others (1) or others only (0) |
| Close | −1 or −2 /rank | p159 | `select`: reduced from Ranged (1) or Perception (2) |
| Activation | −1 or −2 flat | p159 | `select`: an extra simple (1) or standard (2) action |
| Removable | add the Equipment tier | p162 | third option, `costValue: 4` |

### Per-effect corrections — `effect_modifiers.json`

- **Comprehend had no entry at all.** Added its one flaw, **Limited Type**: −1 point flat
  for a broad category, −2 for a narrow one (p115).
- **Insubstantial → Precise** read `"included in configuration"` and cost 0; the book
  prices it at **+1 point flat** (p134).
- **Insubstantial → Reaction** was missing entirely: **+1 point flat** (p134).

---

## 5. Passes 2–5 — what was built

### A. Base cost by configuration (`734a45d`)

Five effects are priced by *how they are configured* and every one was charged its floor —
an all-senses Illusion cost a fifth of what it should.

| Effect | Real cost | Cite |
| --- | --- | --- |
| Illusion | 1 per rank per sense type, **sight counts as two**, cap 5 | p131 |
| Obscure | 1 per sense, 2 per whole sense type, sight double, cap 10 | p139 |
| Remote Sensing | 5 for the first sense type, +1 each after, cap 10; sight counts as two | p142 |
| Transmute | 2 / 3 / 4 / 5 by how broad the source and the result are | p147 |
| Environment | each condition 1 or 2 per rank, **added together**, no ceiling | p124–125 |

**Why not a `BASE_COST_KINDS` mode.** This file originally guessed at a `by_configuration`
handler registered beside `flat` and `as_trait`. Wrong shape: these effects are still
charged *flat, per rank* like everything else and only *which number* differs. A second
pricing kind would have duplicated `_flat_base_cost` and left `effect_per_rank_cost` — which
the Strength-Based divisor reads — still looking at the constant. **Worth remembering: the
registry answers "how is the base charged", not "what is the base".**

### B. Environment's configuration

Environment had no `config` at all. It now carries a 16-option multiselect covering the
book's eight sub-effects at both intensities (p124–125), each lesser version worth 1 point
per rank and each greater worth 2, summed with no ceiling, through the §5A mechanism. The
**Variable** extra then redistributes that per-rank total at will, which is exactly what
*Weather Control* is (4 points of conditions + Variable = 5 per rank) — it falls out of the
arithmetic rather than needing a special case.

### C. The 90 standard power configurations (`ebac2c9`)

All ninety named ready-made powers from p236/p237 are now in
`src/mm_companion/data/configurations.json`, each an assembly of records that already exist
elsewhere. `power_from_configuration` turns one into an **ordinary, editable `Power`** with
no back-reference to where it came from — the moment a rank changes it is no longer that
configuration, and a stale label would be worse than none.

The palette grew a fourth tab grouped by base effect (the way the book's own p236 table is),
searchable and A–Z-sortable. Dropping one **appends** to the canvas rather than replacing
it, titles an *untitled* power after itself, and takes its `structure` only when the canvas
was empty.

**The printed cost is a test oracle.** Each entry carries the cost the *book* prints
(`costNote`) and its page, and neither is used in the arithmetic — so the two agreeing is a
real check on the recorded build. **77 of the 80 machine-checkable configurations match to
the point**; the three that do not are listed in §7.

### D. 18 effect-specific modifiers that could not be dialled

The same gap §4 closed for the *generic* modifiers, one level down: every per-effect
Subtle, Ranged (Burrowing), Weakness (Create), Limited (Deflect), Action and Side Effect
(Fortune Control), Affects Others (Immunity, Insubstantial, Enhanced Senses), Dimensional
(Remote Sensing), Attitude (Summon), Limited Material (Move Object) and Not Against
Descriptor (Regeneration). Poltergeist exposed it — its Subtle 2 was silently charging 1.

### E. A flat flaw could take a cost below zero

The rules are explicit that a flat-value flaw cannot reduce a cost below **1 point** (p150)
and `_flat_base_cost` did not enforce it, so Commlink — a 1-point-per-rank Communication
with an Equipment-tier Removable worth a flat −4 — priced at **−3 PP** and paid the
character back. Now floored at 1, except for an effect with no ranks bought, which still
costs nothing.

### F. Removable priced from the power's total (pass 4)

**The rules (p161).** Removable is worth **−1 (Removable) / −2 (Easily Removable) / −4
(Equipment) per 5 points of the power's final cost, rounded up**, and it "applies to the
power as a whole and not to individual effects". The book's worked example: a 98-point
suit of armour is 98 ÷ 5 = 19.6 → 20, so −20, down to 78. **Short-Term Only** takes a
further 1 off the flaw's own value and may take it to 0, leaving no discount at all
(p160).

It was a flat −1 / −2 / −4 on one effect, which is why Commlink needed the §5E floor and
why **Gadgets** could not reach its printed 5 per rank.

**What was built.** A modifier can now declare *what it is priced against*:

- `costScope: "power"` (+ `costPerPoints: 5`) on the record. Every effect-level bucket —
  `_signed_modifier_cost`, `_modifier_terms`, `_banded_rank_terms` — skips such a
  modifier, so the effect cards' arithmetic is untouched and a Removable chip changes no
  card footer.
- `power_gross_cost` was split out of `power_total_cost` (the 98 in the book's example),
  and `power_total_cost` now applies `power_scope_adjustment` on top of it, floored at 1
  point the way a flat flaw is.
- **Only the costliest selection of each power-scope modifier counts.** This is the rule,
  not a tidy-up: the constructor attaches modifiers to *effects*, so a five-effect device
  naturally carries five copies of the one flaw, and summing them would quintuple a
  discount the book charges once.
- `power_cost_formula` renders the working — `98 − 20 Removable` — into the constructor's
  total line. Without it a power costs visibly less than the cards above it for no stated
  reason, which reads as a bug.
- **Short-Term Only** arrives as a second config field through a new **`costDelta`** on a
  config option: unlike the `costValue` / `flat` / `ranked` overrides beside it, `costDelta`
  is **summed across every field** rather than first-wins, and floored at 0 rather than 1,
  because the rules say it may leave no discount at all rather than turn a flaw into a bonus.
- The tier labels now name the **removal circumstances** the book distinguishes them by
  (Stunned *and* Defenseless; a Disarm or Grab in action time), the other half of §6B that
  was unmodelled.

**Gadgets now prices at the book's 5 per rank**, closing one of the three configuration
gaps in §7. Equipment is unaffected: `item_own_ep_cost` already strips a removable-gated
flaw (`_undiscounted`) before pricing, precisely so an item's price is what its effects
cost *undiscounted* — that guard matters more now that the discount is larger.

### G. Enhanced Senses' Dimensional option (pass 5)

The book lists **Dimensional** among the Enhanced Senses options and then prices it "+1
point flat for a single other dimension, +2 for a group of related dimensions, +3 for any"
(p122) — which is why §4 left it out, the allocation UI metering *ranks* rather than points.
Reading the whole entry settles it: the prose beside that line says "1 rank of Dimensional
allows you to sense into a single other dimension, 2 ranks for a group … 3 ranks for any",
and Enhanced Senses costs **1 point per rank**, so for this effect a rank *is* a point. It
went in as an ordinary `allocOption` with `tiers: [1, 2, 3]`, alphabetically between Detect
and Direction Sense, and needed no engine change at all.

The generic `dimensional` extra still exists and still attaches to anything, so the same
thing is now buyable by two routes for the same price. The option's own description says to
take it here or there and not both; nothing enforces it (§7.3 again).

### H. `repeatable` replaces "has config" (pass 5)

`EffectCard.attach_modifier` refused a second copy of a modifier **only when it had no
config fields**, on the reasoning that config tells two copies apart. That was always loose
— Removable and Check Required carry config and neither is meaningfully repeatable — and
§4/§5D widened it to Ranged, Perception Range, Close, Activation, Affects Others and
Affects Objects, where a second copy double-charges while overriding nothing.

Repeatability is a *rules* fact, so it is now data: `repeatable: true` in `modifiers.json`
and `effect_modifiers.json`, one line in `_parse_modifier`, one clause in
`attach_modifier`. Six records carry it and the book was read for each:

| Repeatable | Why |
| --- | --- |
| Limited | "only at night" beside "only vs. robots" |
| Quirk | p159 — "many are simply 1-point flaws", and a power may have several |
| Feature (as extra) | one minor feature each |
| Custom Extra / Custom Flaw | whatever the player named it |
| Affliction → Limited Degree | p111 — "with two applications of this flaw, the Affliction does not impose a condition for **two** of its degrees" |

That last one is load-bearing rather than decorative: the **Transform** configuration is
built on two Limited Degrees, and `test_the_ruleset_marks_exactly_the_repeatable_modifiers`
pins the whole list so a seventh cannot be added without a reason. A grep of the powers
chapter for the book's repeatability phrasing ("applications of this", "taken twice") turned
up nothing else — every other "each additional …" is a *ranked* modifier, which the engine
already handles.

**Still not done:** `docs/mm-modifiers-ui-design.md` §183 wants a second Limited Degree to
grey out the degree the first one took. Two copies on the same degree are now possible and
merely wasteful.


### Mechanisms now available — reuse these

A later pass should reach for these rather than inventing a parallel one.

| Mechanism | Where | What it gives you |
| --- | --- | --- |
| `baseCostBy` on an effect | `data_loader.py:716` (`BaseCostBy`), resolved by `effect_base_cost_value` (`powers_cost.py:273`) | points-per-rank computed from the effect's own config: `base + Σ chosen costValues`, clamped to `[min, max]`, with `min` as the unconfigured floor |
| `BaseCostContext.base_value` | `powers_cost.py` | the resolved base handed to any `BaseCostKind`, so a mod's own pricing rule never has to ask which sort it holds |
| `BASE_COST_KINDS` registry | `powers_cost.py:445` | *how* a base is charged (`flat`, `as_trait`); a mod registers another |
| Config option `costValue` / `flat` / `ranked` | `ConfigOption`, read by `_modifier_config_cost` (`powers_cost.py:33`) | a modifier or effect option that changes the cost magnitude, or flips per-rank ↔ flat, purely in data |
| `points` and `select` config field types | `common.py` `CONFIG_WIDGET_BUILDERS` | a dialable cost with **no UI code** — this is why §4 and §5D needed no Python |
| `configurations.json` + `core/rules/configurations.py` | `power_from_configuration`, `configuration_by_id`, `configurations_for_effect` | a named build turned into an ordinary `Power` |
| `CONFIGURATION_MIME` + `PowerCanvas.add_configuration` | `common.py:234`, `canvas.py:199` | dropping a prebuilt assembly onto the canvas |
| The 1-PP floor | `_flat_base_cost`, and again in `power_total_cost` | neither a flat flaw nor a power-scope one can produce a negative cost |
| `costScope: "power"` + `costPerPoints` | `power_scope_terms` / `power_scope_adjustment` / `power_gross_cost` | a modifier priced from the **power's** total rather than one effect's, at a rate per N points of it, deduplicated across the effects that carry it |
| `costDelta` on a config option | `_config_cost_delta` (`powers_cost.py`) | a second config choice that *shades* a magnitude another field set, summed across fields and floored at 0 |
| `power_cost_formula` | shown by `PowerConstructorWindow._refresh_cost` | the working behind a total the effect cards cannot explain |
| `repeatable` on a modifier | `EffectCard.attach_modifier` | whether a second copy may be attached — a rules fact, kept in data |

---

## 6. Outstanding work

Each item below states what the rules say, what exists now, what to change, and how to know
it worked.

### A. Dynamic Alternate Effects

**Rules (p101).** An alternate that is **Dynamic** costs **2**, not 1; making the array's
**primary** power Dynamic costs **1**. Dynamic members **share the array's point pool and
run simultaneously** at reduced rank, reallocated once per turn as a free action. A member
needs a minimum allocation to function at all (2 points of Flight = Flight 1).

**Now.** `array_alternate_cost()` (`powers_cost.py:1037`) reads one `costValue` off the
`alternate_effect` record, so every alternate costs 1. There is no per-alternate flag and no
runtime allocation state.

**Change.** A `dynamic` flag on `Power` / `PowerGroup` (build state, persisted the way
`structure` is); `array_alternate_cost` becomes per-member; `power_total_cost` and
`node_cost` (`powers_cost.py:1064`, `1086`) fold in the primary's +1; runtime allocation
state alongside the existing `array_active` / `active_child_id`, persisted the way the other
runtime flags are (**written only when it differs from the default**, so old saves load
unchanged — see `docs/notes/powers.md`).

**Acceptance.** A three-member array where two are Dynamic costs base + 2 + 2 (+1 if the
primary is Dynamic too); allocating points across the Dynamic members changes each one's
effective rank and nothing else; an old saved array still loads and costs the same.

### B. Removable's real formula — **done in pass 4, see §5F**

### C. Nested trait budgets

Four places buy a whole sub-character's worth of points and none records it:

| Where | Budget | Cite |
| --- | --- | --- |
| Summon | minion built on **rank × 15** PP, minion characteristics, may not have minions of its own | p145 |
| Morph → Metamorph extra | one complete alternate trait set **per rank**, same point total, same PL limits | p136 |
| Variable | **rank × 5** Variable Power Points, reallocated on a Concentrate action | p148 |
| Affliction → Empowering extra | the third-degree Transformed form is built on **rank × 15** PP | p110 |

Only the last has even a note (`notePerRank: 15` renders a Notes line). Probably one
feature: a nested point-budget editor reusing the character model. Note `core/npc.py` and
the Quick NPC window already build a reduced character — worth looking at before starting.

### D. Affliction's imposed-effect budget check

**Rules (p110).** An Affliction imposing the **Transformed** condition may name a
Personal-range effect to impose (Morph them, Shrink them, Teleport them away). That effect
**must cost no more than the Affliction's total cost** and must take a standard action or
less.

**Now.** The condition is recorded; the imposed effect is not, and neither rule is checked.

**Change.** A config field on Affliction naming the imposed effect, then a
`power_*_violations` function beside the others in `core/rules/validation.py`. Depends on
§6C if the imposed effect is to be a real nested build rather than a name.

### E. Concealment's sense bookkeeping

**Obscure's half is done** — §5A gave it the sense and sense-type lists its own cost needed.

**Concealment's is not.** Its ranks buy senses the same way — 1 rank per sense, 2 per sense
type, **sight costs double** (2 for one sight sense, 4 for all), and Concealment from touch
senses is impossible short of Insubstantial (p115). The *cost* is a flat 2 per rank so
nothing forced the issue, but with no config recording which senses, nothing can check that
a Concealment 5 spent its ranks legally. The **Invisibility** and **Inaudibility**
configurations therefore ship as Concealment at the right *rank* with the sense named only
in their description — right cost, incomplete record.

**Change.** An `allocation` field metered against rank (as Enhanced Senses already is), not
a `baseCostBy`. Then update those two configurations to seed it.

### F. Countering effects

**Rules (p107).** Take the **Ready** action; when the opponent acts, spend your reaction and
make an **opposed effect check** (d20 + rank each) between opposed descriptors. Winning
cancels both. Ongoing effects can be countered the same way with a normal use. Nullify
counters any effect of a chosen descriptor. The `Instant Counter` advantage skips the Ready.

**Now.** No roll spec, so the roller cannot offer it and a Nullify's opposed check is done
by hand.

**Change.** A roll spec in `core/rules/rolls.py` and an entry point on the power card. Look
at how the existing opposed rolls are specified before designing a new shape.

### G. Improvised Effects

**Rules (p101–102).** `Improvised Effect` and `Prepared Effect` exist in `advantages.json`,
but none of the arithmetic does:

- preparation takes **time rank = the effect's PP cost**, minimum time rank 3 (one minute);
- **−1 time rank per +5** added to the check DC, down to that minimum;
- **+2 check bonus per +1 time rank** of extra preparation;
- preparation DC = **10 + PP cost + 5 per time-rank reduction**;
- use DC = **10 + PP cost**; a prepared effect lasts one scene;
- fast-prep for a Hero Point skips preparation entirely.

**Change.** A calculator over an assembled-but-unbought power. The constructor already knows
the cost and `measurements.json` already has the time-rank table, so this is a small feature
sitting on numbers that exist.

### H. Extra Effort and power stunts

Not modelled anywhere (`grep` finds the phrase only in data descriptions). Chapter 1 rather
than Chapter 6, but the Powers chapter leans on it constantly: a **Sustained** effect can be
pushed with Extra Effort and used for stunts, a **Permanent** one cannot (p106, p157); a
power stunt is a temporary alternate effect bought with Extra Effort and a Hero Point (p101,
p106); Regeneration's Sustained extra exists precisely so Extra Effort can reach it (p142).
Several flaws — Tiring, Fades, Short-Term — are written in terms of it. Wide enough that it
should probably be its own branch after this one merges.

### I. Enhanced Senses' Dimensional option — **done in pass 5, see §5G**

### J. "Has config" is a poor proxy for "may be taken twice" — **done in pass 5, see §5H**

---

## 7. Known debts and caveats

Things a later pass will trip over if it does not know them.

1. **Two configurations cannot reach their printed cost.** Recorded in
   `configurations.json`'s own `_meta.costNote` and excluded by name from
   `test_standard_configurations_cost_what_the_book_prints`: **Material Mimicry** and
   **Power Mimicry** (book 5/rank, built 6) — the book puts the Close Range flaw on
   **Variable**, a *Personal*-range effect, where p159 gives that flaw no value (it is
   priced only from Ranged and from Perception). The book is loose here, not the app. Each
   was built as the book literally names it rather than gaining an invented second modifier
   to force the number. Re-checked when §5F landed and left alone — Removable is not what
   that gap was about. (**Gadgets** was the third; §5F closed it.)
2. **Saved characters were repriced**, twice, with no migration and no warning: by the
   Enhanced Senses tier corrections (§4), and by Removable moving to the per-5-points
   formula (§5F). The direction is one-way there: the discount now scales with the power
   instead of being a flat 1/2/4, so a Removable power **above** 5 points got cheaper
   (proportionally so — a 98-point armour by 20 rather than by 1), and one at or below 5
   points did not move at all. Applied deliberately; a "your build changed" notice is still
   the thing that does not exist.
3. **Nothing enforces mutual exclusion in a multiselect.** A player can tick both "one sight
   sense" and "all sight senses" on Obscure, or both intensities of the same Environment
   condition, and pay for both. Visible on the card, but a warning would be fair.
4. **A Variable Environment's redistribution is unchecked** — nothing verifies that what the
   player redistributes at use time stays inside the per-rank total they paid for.
5. **A power-scope modifier stops at the `Power`.** Removable is charged once per
   `Power`, which is what the rules mean by "the power as a whole". A device modelled as a
   `PowerGroup` of several powers therefore gets one discount *per child power*, each
   priced from that child's own total — the same arithmetic only when every split lands on
   a multiple of 5. Nothing checks for it; the honest build is one power with many effects,
   which is how the book's own armour example is written.
6. **The equipment-currency configurations build as powers, not gear.** Commlink is "1
   Equipment Point per rank" but drops onto the power canvas like anything else.
7. **Four configurations arrive as skeletons** the player must finish — Absorption,
   Berserker Rage, Poltergeist, Power Theft. The book leaves them blank too (which trait is
   boosted, which descriptor is absorbed), so this is faithful rather than incomplete, but
   it is worth knowing before someone "fixes" them.
8. **`docs/notes/powers.md` and `docs/mm-powers-architecture.md` are kept current** with
   each pass. Update them in the same commit as the code, not afterwards — the notes are the
   thing a future session reads first.

---

## 8. Suggested order for the remaining passes

1. ~~**§6B Removable**~~ — done in pass 4 (§5F).
2. ~~**§6J the repeatable flag** and **§6I Dimensional**~~ — done in pass 5 (§5H, §5G).
3. **§6E Concealment senses** — small, and completes the sense-bookkeeping story §5A began.
4. **§6A Dynamic Alternate Effects** — medium, touches cost and runtime state.
5. **§6D Affliction's imposed effect**, then **§6C nested trait budgets** — §6D is a
   thin version of the same problem and will inform the bigger one.
6. **§6F countering** and **§6G Improvised Effects** — feature work on top of a settled base.
7. **§6H Extra Effort and power stunts** — its own branch, after this one merges.

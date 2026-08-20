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
| §5I | Concealment's sense bookkeeping (was §6E) | **done** — pass 6 |
| §5J | Dynamic Alternate Effects — the price (was §6A) | **done** — pass 7 |
| §5K | Affliction's imposed-effect budget check (was §6D) | **done** — pass 8 |
| §5L | Sub-build budgets, and the Summon / Variable data (was §6C) | **done** — pass 9 |
| §5N | Improvised Effects arithmetic (was §6G) | **done** — pass 10 |
| §5O | Countering, and Nullify's opposed check (was §6F) | **done** — pass 11 |
| §5P | The sub-builds themselves (was §6M) | **done** — pass 12 |
| §5Q | Dynamic Alternate Effects — the point pool (was §6K) | **done** — pass 13 |
| §5R | Extra Effort — the uses, the ladder, the rank push (was §6H) | **done** — pass 14 |
| §5S | Power stunts — the temporary alternate effect itself (was §6H) | **done** — pass 15 |

**§6 is empty.** Every item this job set out to check — every cost, every game-term, every
roll, every runtime behaviour, and finally Extra Effort and the power stunt — is done. By
this file's own opening instruction it can be deleted once the branch is merged; §7 below
is the part worth keeping, and its debts should be read (and, where they still matter,
moved into `docs/notes/`) before it goes.

`docs/powers-rules-audit` was merged into `develop` at `bf75e44` with passes 1–13.
Pass 14 onwards is on **`feature/extra-effort-and-power-stunts`**, branched off `develop`
after that merge:

```
bc6020e  Charge Extra Effort, and let it push a rank past the build
0933268  Record pass 14's commit hash in the audit
809a580  Let a hero invent a power stunt at the table
```

The passes that were merged, for reference:

```
b0b713d  Correct the powers data against the core rulebook
1594b5b  Record the remaining powers gaps in a working audit
734a45d  Price the configured effects from their configuration
ebac2c9  Add the rulebook's standard power configurations
b880efb  Turn the powers audit into a handover brief
9a10545  Price Removable from the whole power, per 5 points
6ceb01b  Make repeatability data, and add the Dimensional sense
4c98850  Record which senses a Concealment hides from
96677f1  Charge a Dynamic Alternate Effect what it costs
6ed3d6b  Record pass 7's commit hash in the audit
4799e9b  Let an Affliction name the effect its Transformed condition imposes
db18f2e  Record pass 8's commit hash in the audit
49a81d2  State what a Summon, a Metamorph and a Variable are built on
773dd2a  Record pass 9's commit hash in the audit
7583ee4  Work out what improvising an unbought effect would take
cac4e72  Record pass 10's commit hash in the audit
d350325  Give an opposed effect check to the side that makes it
c53ab92  Record pass 11's commit hash in the audit
82d44dc  Let a power hold the character it buys
6d56b46  Record pass 12's commit hash in the audit
a0ee2eb  Split an array's points across its Dynamic members
```

---

## 1. How to work on this

**Branch.** Passes 1-13 were `docs/powers-rules-audit`, merged into `develop` at
`bf75e44`. Pass 14 onwards is **`feature/extra-effort-and-power-stunts`**, off `develop`:
stay on it until the user says the stunt half is done, then merge with `--no-ff`. One
feature, one branch (`CLAUDE.md`). Never commit on `develop` or `main`.

**Verify, every pass:**

```bash
ruff check . && black --check .
python -m pytest -q                 # 2886 tests as of pass 15
python -m pytest tests/test_powers.py tests/test_power_constructor.py \
                tests/test_data_loader.py tests/test_powers_section.py \
                tests/test_extra_effort.py -q
```

CI does **not** run on a work branch automatically — `gh workflow run CI --ref
feature/extra-effort-and-power-stunts` if a full matrix run is wanted before merging.
`tests/test_gm_window.py::test_copy_puts_the_code_on_the_clipboard` fails locally on
Windows whenever another process is holding the clipboard (`OpenClipboard Failed`); it is
environmental, not yours.

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

**A patch script piped into `python` through the Bash tool loses one level of backslash**,
even from a quoted heredoc — so an anchor containing `"
"` silently becomes a real newline
and the match fails with no clue why. It cost a confused ten minutes in pass 7. Either keep
backslashes out of the anchor, write the script to a file first, or use the `Edit` tool for
those. Non-ASCII (`—`, `•`) survives fine; only backslashes are eaten.

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
| Arrays and Dynamic Alternate Effects, p100–101 and p151 | `array_members_cost`, `alternate_effect` in `modifiers.json` |
| Standard power configurations, p236 (by effect) and p237 (by name) | `src/mm_companion/data/configurations.json` |
| Sense types, p63 | — used by Illusion / Obscure / Remote Sensing |
| Affliction's Imposed Effects, p110 | Affliction's `config`, `imposable_effects` |
| Summon p145–146, Variable p148–149, Metamorph p136 | `effect_readouts.json`, `effect_modifiers.json`, `NOTE_VALUE_KINDS` |
| Improvised Effects, p101–102 | `core/rules/improvised.py`, `system.json` |
| Effect checks and countering, p107; Nullify, p138 | `opposedCheck`, `counter_rolls` |
| Extra Effort and hero points, p20-22; Determination p85, Extraordinary Effort p86, Untapped Potential p94 | `system.json`'s `extra_effort`, `core/rules/extra_effort.py` |

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

## 5. Passes 2–13 — what was built

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


### I. Concealment's sense bookkeeping (pass 6)

**§5A gave Obscure its half of this.** Concealment's ranks buy senses the same way — 1 rank
a sense, 2 a whole sense type, sight double (2 for normal sight, 4 for all of them) — but
its *cost* is a flat 2 per rank however they are spent, so nothing forced the issue and the
effect carried no config at all. A Concealment 5 could claim anything.

It is therefore an **`allocation` field**, like Enhanced Senses, and **not** a `baseCostBy`
like Obscure — the distinction is worth keeping straight, since the two look alike from the
outside. `power_allocation_violations` now checks the spend, and the book's own example
(all sight senses at 4 ranks plus normal hearing at 1, from a Concealment 5) comes out
exactly full.

**Touch is deliberately absent from the option list.** "You cannot have Concealment from
touch senses, since that requires being incorporeal" (p115) — so the rule is enforced by
there being nothing to tick, rather than by a warning after the fact.

*Invisibility* (sight, rank 2, 4 points) and *Inaudibility* (hearing, rank 1, 2 points) now
seed the field, so they record which sense they hide instead of naming it only in prose.
Both still price at the number the book prints.


### J. Dynamic Alternate Effects — the price (pass 7)

**The rules.** "An Alternate Effect costs 1 Power Point, while a Dynamic Alternate Effect
costs 2" (p151). What the second point buys is that a Dynamic member is *not* mutually
exclusive with its siblings: it "can share Power Points with other Dynamic Alternate
Effects, allowing them to operate at the same time, but at reduced effectiveness", split
once per turn as a free action (p101). Making the array's **primary** Dynamic instead
"requires 1 Alternate Effect rank" — 1 point, and the book's own Empyrean example is
explicit that it is charged *on top of* the base's cost, not instead of it.

**Now.** `array_alternate_cost` read one `costValue` off the `alternate_effect` record, so
every alternate cost 1 and a Dynamic array was priced as an ordinary one.

**What was built.**

- **`dynamic` is a per-member flag, not an array-wide mode.** That is the shape the rules
  have — an array can mix ordinary and Dynamic members, and Empyrean's does. It therefore
  exists at **both levels an array does**: `PowerEffectInstance.dynamic` for a power's own
  effects, `Power.dynamic` / `PowerGroup.dynamic` for a group of whole cards (a nested
  group can be a member too, so it carries one as well). Build state, written only when
  set, so an array saved before this loads ordinary and costs exactly what it did.
- **`array_members_cost`** is the one place the pooling arithmetic now lives — it takes
  `(full cost, dynamic)` pairs, picks the costliest as base, and charges each other member
  its own alternate price. `power_gross_cost` and `node_cost` both call it, which is what
  stops the two levels drifting; before, each spelled `max(...) + (n - 1) * flat` itself.
- **Both numbers are data.** `dynamicCostValue: 2` joined `costValue: 1` on the
  `alternate_effect` record. The *primary's* price deliberately did **not** become a third
  number: the book says "1 Alternate Effect rank", so `array_dynamic_primary_cost` returns
  `array_alternate_cost(game_data)` and says why.
- **The switch is per card at both levels**, since the flag is. In the constructor it rides
  on `EffectCard.set_role`, which the canvas already calls whenever the structure changes —
  so it appears for a base or alternate and hides otherwise, *without* clearing the flag
  (flip a power to Linked and back and the members you marked are still marked). On the
  sheet `_dynamic_toggle` puts the same box on a leaf card and a nested group header alike,
  and follows `_ModeToggle` when locked: still readable, no longer a control, click falls
  through to the card that selects the array member.
- The badges and both game-term readouts name it and print the price they were charged
  (`array_member_note`), so `Base · +1 PP Dynamic` and `Alternate · 2 PP` sit on the cards
  and `Flight 5 — Dynamic Alternate Effect, 2 pt` in the summary.

**Checked in the app**, and the book's own worked example comes out right: a Dynamic Create
10 base (20) with a Dynamic Flight 5 and an ordinary Damage 8 beside it is 20 + 1 + 2 + 1 =
**24 PP**, and the sheet splits the same 24 across the cards as 21 / 2 / 1.

**The runtime half was split out as §6K** rather than half-built — see there for why, and
for the two designs already weighed. It landed in pass 13; see §5Q.

### K. Affliction's imposed effect (pass 8)

**The rules (p110).** "The Transformed condition can be configured to impose a particular
Personal Range effect on the target, such as Morphing them, Shrinking them, or having them
Teleport elsewhere ... The imposed effect must have a Power Point cost equal to or less
than the total cost of the Affliction and require a standard action or less to activate."

**Now.** The condition was recorded; the imposed effect was not, and none of the three
constraints was checked.

**It is a configuration, not a build and not a cost.** The imposed effect is *named* and
*ranked* in the Affliction's own config (`imposedEffect` / `imposedRank`) and adds nothing
to the price — the book charges for a point-budget form through the separate **Empowering**
extra, not here. That is what kept this out of §6C's orbit: nothing nested has to exist for
the rule to be modelled and checked.

**Two of the three constraints are enforced by the picker, not by a warning.**
`imposable_effects` offers only Personal-Range effects that take a standard action or less,
so there is nothing to tick that breaks either rule — the same bargain §5I struck by leaving Touch
off Concealment's list. The **cost** constraint is the one that cannot work that way: the
budget moves every time the Affliction's rank or modifiers do, so
`power_imposed_effect_violations` warns live, in the constructor, beside the other checks.
The action limit is *also* checked there, because a stored value outlives the picker that
produced it.

**"Personal Range effect" is not the Range parameter, and that is the whole trap.**
Teleport's Range reads `Rank` — the rank is how far you go — yet the book names Teleport as
an example. Environment's Range is `Rank` too and it is emphatically not personal. So the
fact is data: an effect carries `personal` only where its Range parameter disagrees (three
of them do), read through `effect_is_personal`, which defaults to `range == "Personal"`.
A Python rule about the string would have been wrong in both directions at once.

**Two generic mechanisms fell out of it**, both listed below and both usable from a data
file with no Python:

- **`showWhenField` / `showWhenValue`** — a config field revealed only while a *sibling*
  field holds a value. The gated widget is **built and hidden**, never omitted: rebuilding
  the form from inside the signal of the very combo that changed is how Qt teardown bugs
  start, and the existing `showWhenPoints` gate one level down already works this way.
  Closing a gate drops the stored value; opening one re-seeds the field's default.
- **`CONFIG_OPTION_SOURCES`** — a `select` field naming a `source` instead of listing
  options. It went in **core**, not beside the constructor's widget builders, and that
  placement is the point: the picker and the game-terms readout both have to turn
  `"morph"` into `"Morph"`, and `config_source_options` is the one call they share.

**Three bugs the screenshot found that the tests had not asked about.** A `points` field on
an *effect* (as opposed to a modifier) had no widget builder at all and no display handler,
so the picker rendered as an empty combo and the game-terms line **crashed** on the int;
the field's numeric bounds were spelled `minValue`/`maxValue`/`defaultValue` where the
loader reads `min`/`max`/`default`, so the rank silently defaulted to 0; and the readout
printed the raw id (`morph`) because two of the three `_config_display` call sites had no
`game_data` to resolve a source with. Running it is still worth more than reading it.

### L. Sub-build budgets, and the Summon / Variable data (pass 9)

**§6C said "four places buy a whole sub-character's worth of points and none records it".
Half of that was wrong** — worth knowing, since it is the sort of claim a later pass
inherits without rechecking. Variable already carried a `points_per_rank` readout (rank ×
5, "Power pool") and Empowering already carried `noteTemplate` + `notePerRank: 15`. What
was actually missing was **Summon's** budget and **Metamorph's**, and Metamorph is the
interesting one.

| Where | Budget | Cite | Was |
| --- | --- | --- | --- |
| Summon | one minion on **rank × 15** PP | p145 | nothing |
| Variable | **rank × 5** Variable Power Points | p148 | already right |
| Affliction → Empowering | the Transformed form on **rank × 15** | p110 | already right |
| Morph → Metamorph | one trait set **per rank**, each worth **your own point total** | p136 | nothing |

**Metamorph is why `noteValues` exists.** Every note a modifier could render was
"the effect's rank times a constant" (`{n}` and `notePerRank`), and Metamorph's budget is
not a multiple of any rank — it is `power_points_spent`. So a modifier can now name a
`kind` from the **`NOTE_VALUE_KINDS`** registry for a placeholder, and two kinds went in:
`character_points` for Metamorph, and `doubling` for **Multiple Minions**, whose minion
count doubles per rank *of the extra* rather than growing with the effect. A handler that
cannot answer (`character_points` with no character open) returns `None` and the
placeholder is stripped, so the sentence reads without the number rather than claiming a
zero. This moved `costs` above `powers_terms` in the rules DAG — noted there, and the edge
only goes one way.

**A latent bug came with it.** `_modifier_notes` rendered every Notes-row template
*without* the chip or the game data, so `{rank}`, `{dc}` and any config key silently
vanished. Nothing had noticed because the only templates that used them went through the
Check Required path instead. Both of pass 9's new notes need the chip, so it is fixed.

**Four data corrections, all from p145/p148, and one is a free flaw:**

| Record | Was | Now | Cite |
| --- | --- | --- | --- |
| Summon → **Hostile** | `costValue: 0`, "included with Attitude-style flaws" | **−2 per rank** | p145 |
| Summon → **Multiple Minions** | not `ranked` — stuck at one application | `ranked: true` | p145 |
| Summon → **Variable Type** | stuck at +1 | select: general type (+1) / broad (+2) | p145 |
| Variable → **Action** | stuck at +1 | select: simple action (+1) / free (+2) | p148 |
| Healing → **Repair** | stuck at +1 | select: people and objects (+1) / objects only (+0) | p130 |

Hostile is the one that mattered: a Summon could take "your minions turn on you and you
cannot end the effect" and pay nothing for it. The other three are the §5D gap one more
time — a `costFormula` stating a range with no way to dial it — which suggested §5D's
sweep had missed the *effect-specific* lists.

**So the sweep was redone across both modifier files**, matching a stated range
(`\d\s*(or|-)\s*\d`, or more than one signed term) against a record carrying no `config`.
It turned up exactly one more, **Healing → Repair**, and it is in the table above. The only
other hit is `alternate_effect`, whose "1 or 2 points flat" is pass 7's Dynamic flag rather
than a config, and is right as it stands. That sweep is now clean, and the query is
recorded here so a later pass can rerun it after adding records.

**A modifier's name now carries a priced choice.** `modifier_detail` qualified a modifier
with typed free text only, so a Variable Type (broad) and a Variable Type (general) — which
cost twice and once per rank — read identically on the card. A chosen `select` option now
qualifies the name **when the option carries its own `costValue`**; a select with no prices
on it is a neutral choice and stays out. That was not §6C's subject, but pass 9 added two
more silent selects and the gap was already ~15 records wide.

**Checked in the app.** A Summon 6 with Multiple Minions 2 reads "Minion built on: 90
points" and "up to 4 minions" — both figures the book prints on p145 — and a Morph 3 with
Metamorph 2 reads "2 alternate trait set(s), each built on your own 65 power points" for a
65-point character.

**§6M is what is left**: the sub-builds themselves.

### N. Improvised Effects (pass 10)

**The rules (p101–102).** A character with the **Improvised Effect** advantage rigs up a
power they have not bought and reaches it with a skill check. Every number comes off one:
*the effect's Power Point cost*.

- preparation takes a **time rank equal to the cost**, never below **3** (one minute);
- **−1 time rank per +5** on the preparation DC, down to that minimum;
- **+2 on the check per +1 time rank** spent instead;
- preparation DC = **10 + cost + 5 per rank shaved**, rolled *in secret by the GM*;
- use DC = **10 + cost**, retryable until it lands or the scene ends.

**Now.** The two advantages existed in `advantages.json` and none of the arithmetic did.

**What was built.** `core/rules/improvised.py` — pure functions over a cost — with every
dial in `system.json` (`minTimeRank`, `dcPerTimeRankSaved`, `checkBonusPerTimeRankSpent`)
rather than spelled in Python, and the time rendered off the measurements table that
already existed. Two `RollSpec`s come out of it, both against DCs the plan already knows,
both on the character's **own** skill — because Improvised Effect is a *focused* advantage
and its `parameter` already names the skill it was taken for. That was a nice surprise:
the data needed nothing added to it.

**The cost is `power_gross_cost`, not the total.** "The Removable modifier does not apply
to Improvised Effects, as they are one-use by nature" (p101) — and the gross is exactly the
price before any power-scope discount, which Removable is the only one of. §5F's split paid
off again.

**The panel is in the Power Constructor, and that placement is the point.** An improvised
effect is one the character has *not bought*, and the constructor is the only place in the
app an unbought power is ever held — the sheet only ever shows powers already paid for. It
is collapsed by default and hidden until the power costs something.

**Two things the wiring taught us, both recorded in the notes:**

- The constructor is a **window, not a block**, so it is not on the sheet's roll bus. It
  emits `rollRequested` and the `PowersSection` that opened it hands the request on exactly
  as its own cards do.
- Its roll buttons are **plain buttons rather than the sheet's `RollsFooter`**, because
  `ui.cards` reaches back into `ui.power_constructor` for the terms grid *and* sideways into
  `ui.sections`. Importing it here closes that loop: at module scope it fails outright, and
  deferred it fails for anyone who opens the constructor without the sheet — which the
  driver script did, so this is a tested claim rather than a guess. Two buttons need none
  of what that widget adds, so they are the honest answer and not a fallback.

**Checked in the app**, and the arithmetic is vivid: a Ranged Affliction 8 costs 16 PP, so
improvising it takes **8 hours** by default. Shaving 9 time ranks brings it to 15 minutes
and takes the preparation check to **DC 71** — which is the book working as intended, and
the reason improvisation is for modest effects.

### O. Countering, and Nullify's opposed check (pass 11)

**The rules (p107).** "An effect check is just like any other check: d20, plus the effect's
rank." To counter, take the **Ready** action; when the opponent uses an effect with an
opposing descriptor, spend your reaction and both of you make effect checks. Winning
cancels both powers. The GM is the final arbiter on whether two descriptors oppose.

**The bug this uncovered is the bigger half.** Nullify's second roll is an opposed effect
check the *wielder* makes — "make an opposed check of your Nullify rank and the targeted
effect rank or the target's Will" (p138) — and the app had it in the effect's
``resistance`` slot. That slot builds a spec marked ``rolled_by_target=True`` with
``modifier=0``, so the roll was attributed to the **wrong side of the table** and carried
no bonus at all. It was unrollable as written, and it had been that way since Nullify was
added. Countering needs exactly the same roll, so the two were one job.

**What was built.** ``opposedCheck`` on an effect names what its own effect check is rolled
against; it produces a game-term row (next to the save it is so easily mistaken for) and a
wielder-rolled spec at the effect's **effective** rank with no DC — the opponent's result
is the number to beat, which is what the DC box is for. Nullify declares one and its
``resistance`` is now ``null``, which is the honest reading of the book's own stat block.

**Where the generic counter lives, and why it moved.** ``counter_rolls`` offers one roll
per effect that could actually be readied: something the character *uses* (it attacks or
forces a resistance — an always-on Protection is not readied, it simply is) and usable as a
standard action or less, which is the rules' own condition. Both of the book's examples
pass — a Blast countered with Move Object, Mind Control broken with Nullify.

It is deliberately **not** part of ``power_rolls``. That was tried first, and the test suite
said no in three places at once: every attack power grew a die button, **every weapon in
the Equipment block** grew one, and the GM pin picker started offering "Axe" three times.
The footer is what a power *calls for*; countering is a tactic it can be turned to on a GM's
ruling. So it is on the **power card's right-click menu**, which costs no space and is where
the app already puts a card-adjacent action (the footer's own Pin menu). ``counter_menu``
is split out from the handler that shows it, because a modal ``exec`` headless is a test
that hangs rather than one that passes.

**Checked in the app.** Nullify's card now reads "Opposed: 8 vs. targeted rank or Will" and
offers it as a **rollable** footer line; Move Object and Nullify carry a counter entry on
their menus; a Protection card has neither, and no card gained a line.

### P. The sub-builds themselves (`82d44dc`)

**The rules.** Summon: "Create the summoned character with (effect rank x 15) Power
Points. They are subject to the normal Power Level limits, have the minion
characteristics, and cannot have minions of their own, either from this effect or the
Minions advantage" (p145). Metamorph: "one set of traits per rank ... Your other form(s)
must have the same point total as you and are subject to the same Power Level limits"
(p136).

**Now.** Pass 9 made both budgets real numbers on the card. Nothing held the build the
budget was for, so nothing was ever checked against it.

**The open question §6M recorded was where a sub-build lives, and the answer is
"embedded", because all three objections to it turned out to be false.** A nested
`Character` inside `PowerEffectInstance.config` was said to touch the save format, undo
and the library. It touches none of them: `config` is already written verbatim as JSON,
`snapshot_of` already serialises the whole model as JSON text — so editing a minion is an
undoable step of the sheet for free — and the session layer already pushes the same
`to_dict`. The library is not involved at all, because the minion is not a file. The
alternative, a *reference* to a saved NPC, couples a player's power to the GM's directory
and dangles when the file moves. So: a list of `Character.to_dict()` dicts under a key in
the config dict that bought it — **the effect's** for Summon, **the chip's** for Metamorph.

**The budget is stamped on read, never stored** (`sub_build_character`). It and the Power
Level are both derived from the power and its wielder, so dialling a Summon from rank 4 to
rank 6 moves the minion's pool from 60 to 90 by itself. That is the design's best trick:
it makes the *sheet's own* spent-against-budget readout the check the rules ask for,
rather than a warning bolted beside it.

**Declared in data, and the registry it reuses is `NOTE_VALUE_KINDS`.** A `subBuild` block
names a `key`, a `label`, a `budget` and a `count`, and the last two are ordinary
`noteValues`-shaped specs resolved through one new door, `note_value(spec, ...)`. That was
the right seam because *a sub-build's budget asks the registry exactly what a note's
placeholder asks it*: `character_points` was already there for Metamorph's note and now
prices Metamorph's forms. Two kinds joined it — `per_rank` (Summon's `rank x 15`) and
`modifier_rank` (one form per rank of the extra) — and `NoteValueContext.modifier` became
optional, since Summon's budget is priced off an *effect* with no chip in sight.

**One data correction fell out of reading the entry.** Metamorph's description said each
alternate form "must carry your full Morph effect" — a 3e requirement the Origin Edition
does not state. p136 asks only for the same point total, the same Power Level limits and
"traits suitable to the form you assume", so the sentence now says that instead. It was
never enforced, but it would have been the moment someone built the check from it.

**Summon buys one minion, not many, and that is the book's own reading.** "You always
summon the same minion unless you apply the Variable Type modifier" (p145) — Multiple
Minions doubles how many of the *same* creature appear, which the card has said since pass
9 and which needs no second build.

**Three checks, and each is one no picker could have prevented**
(`power_sub_build_violations`, constructor-only like the imposed-effect and Strength
warnings beside it): over budget; **more builds than the power buys**, warned rather than
truncated, because a Metamorph dropped from rank 3 to rank 1 must not silently bin two
characters a player wrote; and what a sub-character may not itself have — a minion
"cannot have minions of their own, either from this effect or the Minions advantage",
which is a fact about the *nested* build and therefore unreachable from any picker.
`forbidsEffects` / `forbidsAdvantages` put it in data.

**The editor is the ordinary character sheet, for the third time.** `SubBuildWindow` is a
`MainWindow` — deliberately **not** NPC mode, which swaps the point pool for an estimated
Power Level, when a minion is the one GM-side character the rules actually do budget. Two
things differ, and both follow from the build being someone else's: it **saves into the
power** rather than to a file (every edit writes through, so it never goes dirty and never
asks on close), and its Power Level and Power Points rows are **read-only** through a new
`SystemInfoSection.set_budget_fixed` — sticky against `set_locked`, because a derived
field was never the player's to unlock.

`SubBuildPanel` is the strip on the effect card that reaches it, showing **every slot the
card owns** — the effect's own and its chips' alike — because the card is where both are
edited and a second strip inside the chip would be a second place to look. It imports the
window **inside the click handler**: at module scope the constructor → main window → sheet
→ sections → constructor loop closes. That is pass 10's finding from the other side, and
it is the second time this branch has paid for it.

**Checked in the app.** A Summon 6 shows "Minion — 90 PP" and a button reading "Dire Wolf
— 28 / 90 PP"; the minion's own sheet reads "Power Points: 28 / 90" with both budget rows
uneditable; a Morph 2 with Metamorph 3 shows "Alternate forms (3) — 20 PP each" for a
20-point wielder. All three are the numbers the book prints.

**§6K was then the only item left inside this branch.**

### Q. Dynamic Alternate Effects — the point pool (pass 13)

**The rules (p101).** Dynamic members share the array's point pool and "operate at the
same time, at reduced effectiveness"; the split is decided "once per turn as a free
action". The worked example is the acceptance test: "a character can maintain the Dynamic
Alternate Effect for their Flight so long as at least 2 Power Points are assigned to it,
but their Flight speed is then limited to 1 rank of Flight."

**Now.** Pass 7 charged the dearer price and stopped there. A Dynamic member behaved at
runtime exactly like an ordinary alternate: dimmed unless it was the one selected member,
and never at a reduced rank.

**The design §6K said to weigh was "derive at read time" against "push into
`current_rank` on edit", and the first won once the blocker turned out to be avoidable.**
The blocker recorded was that `effect_current_rank(effect)` takes the effect alone, so
threading the array in meant a signature change across ~15 call sites. What that missed is
that the *allocation can live on the member itself*, and that the member can then be
**found** rather than passed: `dynamic_rank_cap` walks the wielder's own powers tree by
identity. So the signature grew by two optional arguments the callers already had in hand
(`game_data`, `char`) and ten call sites gained them; nothing else moved.

**The arithmetic is the audit's own, and the book's example is the test.** `pool` is the
base member's full cost (`array_pool_points`), and a member allocated `n` runs at
`floor(rank x n / its own full cost)` (`dynamic_rank_share`). Flight 5 costs 10, so `n = 2`
gives 1 — exactly what p101 prints. **Zero is a real answer here and nowhere else**: a
member below its minimum is simply off, which needed no special case, and
`effect_current_rank`'s own floor of 1 is untouched for every other caller.

**The layering problem was real and is solved by injection, not by an import.** Working a
share out needs point costs; `powers_cost` imports `runtime`, so `runtime` cannot import it
back. `runtime` therefore declares the hook and `powers_cost` *installs* it at import
(`set_dynamic_rank_cap`). Uninstalled, every rank reads as it did before the pool existed —
the same bargain `PATTERN_BEHAVIOURS` and `GATE_KINDS` already document, and the reason
`effect_current_rank` could stay the single source. That is what makes the whole sheet
follow from one number: the save DC, the Toughness a Dynamic Protection grants, the speed a
Dynamic Flight flies at, the Movement block's own row and the card's title are all it.

**`live_powers` was the other half.** `live_array_children` is new and is where the array's
liveness question now lives: the selected member as before, *unless* any Dynamic member
holds a share, in which case every member holding one runs together and the selection stops
deciding. It reads only whether a share is present, never its size, so it needs no costs;
a share too small to buy a rank leaves a member live at rank 0, which contributes nothing
and reaches the same place. Clearing the split restores the old behaviour exactly, which is
also what an array saved before this does on load.

**The editor is a dialog on the group's own header**, `Split points (n/pool)`. It is a
*free action at the table*, not a build decision, so unlike the Dynamic switch beside it the
button **survives the lock** — the same class of control as the card click that selects an
array's live alternate. Each row's maximum is what the other rows have left, which is how
"the split may not exceed the pool" is enforced without a warning to read; each row names
the rank its share buys ("Flight 1 of 5") rather than a fraction to convert; and a zero row
is stored as *no share at all*, so clearing the split leaves the file byte-for-byte what it
was.

**Two things were deliberately left.** A member is found on the *character*, so a power in
the Power Constructor is never capped — right, since nothing is dialled there either. And
the split exists only at the **group** level: an array of one power's own effects has no
runtime member selection at all today (every effect of an array-structured power is live at
once), so a pool there would be built on sand. Both are recorded in §7.

**Checked in the app, and it found the bug this pass would otherwise have shipped.** The
book's own Empyrean array, split 16/2/2 out of 20: all three cards lit at once rather than
one lit and two dimmed, reading Create 8, Flight 1 and Protection 2 (Toughness +2), with
the header showing `Split points (20/20)`. The Flight card, though, still printed
`Speed: 250 feet/round` — its rank-5 speed beside a title reading "Flight 1". The `measure`
row and the `range: "Rank"` substitution in `effect_stat_rows` were the last two readouts
built from `effect.rank` rather than the live one; both now read `effect_live_rank`, which
also fixes them for an ordinary rank dial, where they had been wrong all along.

### R. Extra Effort (pass 14)

The **whole of §6H except the stunt**, and the first pass on this branch rather than the
audit's own. Extra Effort is Chapter 1 (p20-21) and the Powers chapter leans on it
constantly: a Sustained effect can be pushed and stunted with and a Permanent one cannot
(p104, p155, p159), Regeneration's Sustained extra exists precisely so Extra Effort can
reach it (p142), and three advantages do nothing else (Determination p85, Extraordinary
Effort p86, Untapped Potential p94). None of it was modelled — `grep` found the phrase only
in data descriptions.

**The data.** `system.json` grew an `extra_effort` block: the six uses the book lists (each
saying whether it has to name one of the character's own effects), the fatigue ladder as
condition ids, what a rank increase and a check bonus are worth, which durations refuse it,
and the three advantages by name. `ExtraEffortRules`/`ExtraEffortUse` parse it the way
`ImprovisedEffectRules` does, so a ruleset retunes every one of those without Python — and
a mod adding a seventh use is offered it in the menus for free.

**The price.** `spend_extra_effort` walks the ladder from wherever the character already
stands — nothing → Fatigued → Exhausted → Incapacitated — and applies each rung through the
ordinary `apply_condition`. That is the whole reason it is a list of ids rather than a rule:
Exhausted *supersedes* Fatigued in `conditions.json`, so climbing removes the rung climbed
from without a second rule saying so, and the bundled Impaired/Hindered arrive as they do
for any other condition. `next_fatigue(char, data, steps)` is the same walk as a lookahead,
which is how the dialog promises exactly what taking it will do.

**The benefit.** One of the six changes a number the sheet prints, and it is the interesting
one: `PowerEffectInstance.extra_effort` is how many ranks are pushed in, and
`effect_current_rank` adds them **after** every clamp. That placement is the rule —
"its benefits can even increase your ranks or bonuses beyond the normal Power Level limits"
— so a push goes past the bought rank, past a hard `pl_cap`, and past a Dynamic member's
share of its array's pool. Reading it in that one funnel is what makes the save DC, the
speeds, the trait boosts and the card title all follow, exactly as pass 13 found for the
pool. Cost never asks and validation never asks: both take the *build* rank, and a Power
Level check is a statement about the build. The card grows an `Extra Effort` row
(`+2 ranks, until the end of your turn`) for the same reason `pl_cap_note` exists — a number
that moved with nothing on the page to explain it is worse than no feature.

**Who may be pushed** is a duration question, asked of the effect's *resolved* duration:
`effect_allows_extra_effort` refuses a Permanent one, which means the Sustained extra lifts
the refusal and the Permanent flaw imposes it, both for free and both correct.

**The advantages.** Untapped Potential adds its ranks to the increase (2 at rank 1, "each
additional rank adds 1"); Extraordinary Effort takes the benefit twice for two rungs of the
ladder; Determination shrugs the fatigue off entirely, either as a use of the advantage or
as the Heroic Feat a Hero Point buys (p22).

**Where it is spent.** Two blocks, because it is paid for in two other blocks' currencies.
`ui/extra_effort.py` holds the shared menu and dialog: the **card's right-click menu** (now
`card_menu`, with the counter rolls below a separator) offers the two uses that name an
effect, and the **System block** offers the four that name nothing, beside the hero points.
Both survive the lock — spending Extra Effort is a mid-play action, like clicking a pip or
selecting an array's live alternate. The fatigue is written to the shared model and the
Conditions block **subscribes** to `condition-changed` to follow it, which it never did
before (it only published it); the hero point cannot be written that way, because the pips,
the clamp and the roll-history sentence are one funnel in `SystemInfoSection` — so a new
`hero-point-requested` request topic asks that block for it, the way `note-requested`
already asks the Dice block for a line.

**What the screenshot found, as usual.** The dialog built the "take it twice" checkbox
*above* the sentence it changes, so the benefit line read as a consequence of nothing. The
controls are now built in one order and laid out in another, on purpose.

### S. Power stunts (pass 15)

The last item. A stunt is "a temporary Alternate Effect of a non-permanent duration effect
you have" (p20), bought with Extra Effort and usually a Hero Point — and the reason the
rules give for it is worth quoting, because it is also the reason the app wanted it: "so
you do not have to fill up character sheets with long lists of minor alternate effects a
hero will rarely ever use" (p101).

**The topology was the decision, and it was the user's.** Two were on the table (recorded
in §6H before this pass): a member of the source power's array, which is what the rules
literally call it, or a card of its own holding a back-reference. **The card won**, and the
reasoning holds up in the code: an array member drags pooling, base selection, the Dynamic
point split and the live-alternate machinery onto something that costs nothing and lasts a
scene. `Power.stunt_of` is the back-reference, by id like every other cross-power
relationship on a character.

**Three things follow from that one field**, and nothing else does:

* **It costs nothing.** `node_cost` returns 0 for a stunt, which is what keeps it out of
  `powers_points_spent`; `power_total_cost` still says what it *would* cost, because that
  is the number the ceiling is measured against.
* **It is held to the alternate effect's ceiling.** "An alternate effect can have a total
  cost in Power Points no greater than the base power" (p98) — `power_stunt_violations`,
  which also catches the other thing that goes wrong: a source power deleted out from under
  it. Both warn on the card's own ⚠ rather than deleting anything, since binning a build
  the player made is the worse answer.
* **It is not saved** — the second half of the user's call. `strip_stunts` takes stunt
  cards out of the serialized tree in `library.save_character`, recursively, because
  nothing stops one being dragged into a group. `to_dict` still writes them, and that is
  deliberate: undo snapshots the model as JSON, and a stunt that vanished on the next undo
  would be worse than one that outlived its scene.

**The order of the flow is the interesting UI decision.** Choosing *Power stunt* from a
card opens the Power Constructor, and the Extra Effort dialog appears only when a build
comes back. Charging first would take a rung of fatigue for a stunt that may never exist;
cancelling the cost dialog now drops the build instead, which is the honest way round —
the dialog is the "yes, spend it" step.

Small rules details that fell out: a stunt can be **pushed** (it is a non-permanent effect
the character is using) but cannot be **stunted off** (a stunt is an alternate of a power
you *have*), and the character-wide clear is two entries rather than one, because a push
ends with your turn and a stunt with the scene.

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
| `array_members_cost` | `powers_cost.py` | the whole array-pooling rule from `(cost, dynamic)` pairs — the one place `power_gross_cost` and `node_cost` both go, so the two levels an array exists at cannot drift |
| `dynamic` on a member | `PowerEffectInstance` / `Power` / `PowerGroup` | a per-member array flag; a mod pricing its own array variant reads it beside `structure`/`mode` |
| `showWhenField` / `showWhenValue` | `EffectCard._field_gate_open` / `_refresh_config_gates` | reveal a config field only while a sibling field holds a value — built and hidden, with the default re-seeded when the gate reopens |
| `CONFIG_OPTION_SOURCES` + `source` | `powers_terms.py`, via `config_source_options` | a `select` whose options are a query over the game data; the picker and the readout share one resolver so an id cannot be named two ways |
| `points` as an *effect* config field | `EffectCard._points_widget`, `_config_display_points` | a bounded number on an effect (not just a modifier), seeding and storing its own default |
| `effect_is_personal` / `effect_action_at_most` | `powers_cost.py` | "works on its user alone" and "takes this action or less" — both data-driven, both wanted by any rule that restricts *which* effects something may name |
| `noteValues` + `NOTE_VALUE_KINDS` | `powers_terms.py` | a number a note needs that the effect's rank cannot give — the modifier's own rank compounded, the wielder's point total; returns `None` to drop the placeholder rather than print a zero |
| `points_per_rank` readout | `effect_readouts.json` | a per-rank budget stated on the card, in data with no Python (Summon's minion, Variable's pool) |
| A priced `select` in a modifier's name | `modifier_detail` | any option carrying its own `costValue` qualifies the modifier wherever it is listed, so two differently-priced cards never read alike |
| `improvised_plan` | `core/rules/improvised.py` | preparation time and both DCs from a cost, with the trade between them; every dial in `system.json` |
| `PowerConstructorWindow.rollRequested` | wired by `PowersSection` | how a *window* reaches the roller — it asks, and the block that opened it forwards |
| `opposedCheck` on an effect | `effect_opposed_check`, `KIND_EFFECT_CHECK` | a `d20 + rank` roll the **wielder** makes against another effect, with no DC of its own |
| `counter_rolls` + the card menu | `PowersSection.counter_menu` | a per-power action that costs no card space — the place to put anything a power *can be used for* rather than what it calls for |
| `subBuild` on an effect *or* a modifier | `core/rules/subbuilds.py`, `SubBuildSlot` | a whole nested `Character` bought inside a power, with a budget and a count — both `NOTE_VALUE_KINDS` specs, so a mod prices one in data |
| `note_value(spec, ...)` | `powers_terms.py` | the one door onto `NOTE_VALUE_KINDS`; reach for it whenever a feature needs "a number this record's rank cannot give" |
| A nested `Character` in a `config` dict | `store_sub_build` / `sub_build_character` | persistence, undo and session sync for free — `config` is written verbatim and undo snapshots the whole model as JSON |
| `SystemInfoSection.set_budget_fixed` | `sections/system_info.py` | show Power Level and the point pool read-only for a sheet whose budget is handed to it; sticky against `set_locked` |
| `SubBuildWindow` | `ui/sub_build_window.py` | the ordinary sheet pointed at something that is not a file — it commits to its owner instead of saving |
| `set_dynamic_rank_cap` | `core/rules/runtime.py`, installed by `powers_cost` | an **injected** hook, for a rule the lowest layer must obey but cannot compute — declared where it is read, filled in from the layer that has the numbers, and a no-op until it is |
| `dynamic_rank_share` / `array_pool_points` | `powers_cost.py` | a member's share of a pooled budget turned into a rank, and the pool it draws on — the shape of any "spend points at the table to buy effectiveness now" rule |
| `live_array_children` | `runtime.py` | the one place an array decides *which* of its members are running; anything that changes that answer belongs here rather than in `live_powers` |
| `effect_current_rank(effect, game_data, char)` | `runtime.py` | the single source for "what rank is this running at" — reading a new restriction *there* is what makes the DC, the trait boosts, the speeds, the readouts and the card title follow from one number |
| `DynamicPoolDialog` | `ui/sections/dynamic_pool_dialog.py` | a runtime editor that survives the lock, with each row bounded by what the others left — the pattern for any "hand out a budget at the table" control |
| `extra_effort` on an effect | `PowerEffectInstance`, added by `effect_current_rank` | the one runtime number that reaches **above** the bought rank, past every clamp — the shape of any "spend something at the table to exceed your build" rule |
| `system.json`'s `extra_effort` block | `ExtraEffortRules`, `extra_effort_uses` | a list of *uses* each declaring what it must be pointed at (`target: "effect"`), so both menus are built from the ruleset and a seventh use needs no Python |
| `spend_extra_effort` | `core/rules/extra_effort.py` | grant the benefit, then charge the ladder through `apply_condition` — the pattern for any cost paid in conditions, since supersession then comes from the catalog rather than from a second rule |
| `hero-point-requested` | `ui/blocks/bus.py`, served by `SystemInfoSection.adjust_hero_points` | one block spending another block's currency, without either naming the other — the twin of `note-requested` |
| `ConditionsSection` subscribing to `condition-changed` | `blocks/registry.py` | any block may now write a condition to the shared model and have the chips follow; the resolver stays core's |
| `effect_display_name` | `runtime.py` | "this effect's own label, else the base effect's name" — the idiom three call sites had each spelled out |
| `stunt_of` + `strip_stunts` | `core/powers.py`, applied in `library.save_character` | a power that is **on the sheet but not in the file** — the shape of anything invented at the table: serialized for undo and the session, stripped on the way to disk |
| `power_is_stunt` in `node_cost` | `powers_cost.py` | a card that costs nothing without lying about what it would cost — `power_total_cost` stays honest so a ceiling can be checked against it |
| Build first, charge on the way back | `PowersSection._open_stunt` / `_on_stunt_saved` | the pattern for any cost paid for a thing the player has yet to make: the constructor opens, and the price is asked only when something comes back |

---

## 6. Outstanding work

Each item below states what the rules say, what exists now, what to change, and how to know
it worked.

### A. Dynamic Alternate Effects — the price — **done in pass 7, see §5J**

### B. Removable's real formula — **done in pass 4, see §5F**

### C. Nested trait budgets — **done: the budgets in pass 9 (§5L), the builds in pass 12 (§5P)**

### D. Affliction's imposed-effect budget check — **done in pass 8, see §5K**

### E. Concealment's sense bookkeeping — **done in pass 6, see §5I**

### F. Countering effects — **done in pass 11, see §5O**

### G. Improvised Effects — **done in pass 10, see §5N**

### H. Extra Effort and power stunts — **done: the effort in pass 14 (§5R), the stunt in
pass 15 (§5S)**

*The brief is kept below as it was written, because the two questions it left open were
answered by the user rather than by the code: a stunt is a **card of its own**, and it is
**not saved**.*

What is left of §6H is the stunt: "you can use a temporary Alternate Effect of a
non-permanent duration effect you have" (p20), bought with Extra Effort and usually a Hero
Point, and "that is what the power stunts guidelines are for, after all: so you do not have
to fill up character sheets with long lists of minor alternate effects a hero will rarely
ever use" (p101). Today the app charges the effort and records which effect the stunt was
taken from, and the GM adjudicates the rest; the *alternate effect* is not built.

**Rules.** A stunt is an alternate effect, so it is bounded like one — a build no dearer
than the power it hangs off — but it is temporary, costs no Power Points, and is gone at
the end of the scene. The GM has final say over which stunts make sense (p20), and changing
only a *descriptor* is itself a stunt (p104).

**What that needs.** A `Power` that costs nothing and knows it: the constructor opened from
a card's Extra Effort menu, the result attached to the character as a stunt rather than a
bought power, marked on its card, excluded from `power_points_spent` and from the array
arithmetic, and thrown away by the same button that clears a push. `USE_POWER_STUNT` is
where it starts.

**Two things to settle first.** Whether a stunt is a member of the power's own array (which
is what the rules call it, and which drags the array pooling in) or a card of its own with a
back-reference (simpler, and honest about being temporary). And whether it is saved at all:
runtime state is persisted now, but a stunt is scoped to a scene, and a build that reopens
with three stunts on it is a build nobody meant to keep.

**Acceptance.** A stunt can be assembled from a card, costs 0 PP, shows as a stunt, rolls
like the power it came from, and is cleared in one click; the point total and the Power
Level checks are untouched by it. — *All of it, and the ceiling check besides.*

### I. Enhanced Senses' Dimensional option — **done in pass 5, see §5G**

### J. "Has config" is a poor proxy for "may be taken twice" — **done in pass 5, see §5H**

### K. Dynamic Alternate Effects — the point pool — **done in pass 13, see §5Q**

*Kept below as it was written, because pass 13 answered its open question the other way:
the blocker was avoidable, and the "wide signature change" turned out to be two optional
arguments the callers already held.*

The half of §6A that pass 7 deliberately did **not** build. The price is right; the pool is
not modelled at all, so a Dynamic member still behaves at runtime exactly like an ordinary
alternate — it is dimmed unless it is the one selected member, and its rank never drops.

**Rules (p101).** Dynamic members share the array's point pool and **operate at the same
time, at reduced effectiveness**. You decide the split **once per turn as a free action**. A
member functions only while it holds a minimum: "a character can maintain the Dynamic
Alternate Effect for their Flight so long as at least 2 Power Points are assigned to it, but
their Flight speed is then limited to 1 rank of Flight."

**Why it was split out rather than half-built.** The allocation has to reach an effect's
*effective rank*, and `effect_current_rank(effect)` takes the effect alone — nothing about
the power it sits in, let alone the parent group whose pool it draws on. Threading that
through means a signature change across ~15 call sites in `powers_cost`, `powers_terms`,
`movement`, `size`, `runtime` and the sheet. That is a bigger and riskier job than the
pricing fix it was bundled with, and it deserves its own pass rather than being bolted on.

**Two designs were weighed. Record them so the next pass need not re-derive them.**

1. **Derive at read time.** Store points per member; thread the power (and its parent group)
   into `effect_current_rank` so the reduced rank is computed wherever a rank is read.
   Correct and single-sourced, but it is the wide signature change above.
2. **Push into `current_rank` on edit.** The allocation editor *writes* each effect's
   existing `current_rank` (and the member's live flag), the way `_normalize_arrays` already
   fixes up array state. No read-path change at all — but it stomps a dial the player may
   have set by hand, and derived state written into a stored field goes stale the moment a
   rank is edited elsewhere. Cheap; the trade is real.

**Arithmetic that will be wanted either way**, worked out and checked against the book's own
example: with `pool` = the base member's full cost and a member allocated `n` of it,
`rank' = floor(rank × n / full_cost_of_that_member)`. Flight 5 costs 10, so `n = 2` gives
`floor(5 × 2/10) = 1` — the book's "2 Power Points … limited to 1 rank of Flight", exactly.
A member whose every effect floors to 0 is below the minimum and is simply off, which needs
no special case. Note `effect_current_rank`'s floor is 1, so this cannot reuse it unchanged.

**Also outstanding here:** `live_powers` gives an array exactly one live child, so Dynamic
members that should run *alongside* the selected one do not; and the sheet's click hint
("its siblings switch off") stops being true once they do.

**Acceptance.** Allocating points across an array's Dynamic members changes each one's
effective rank and nothing else; a member below its minimum is off; the split may not exceed
the pool; an old saved array loads with no allocation and behaves exactly as it does today.

### M. The sub-builds themselves — **done in pass 12, see §5P**

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
3. **An allocation readout prints the tier *index*, not what it cost.** A Concealment
   hiding from every sight sense reads "Sight 2" in the game-terms panel — tier 2, which
   costs 4 ranks — while the card's own combo beside it says "4 ranks". The same is true of
   Enhanced Senses' Accurate (tiers 2 and 4). It predates this job and is shared by every
   `allocation` field, so it was left alone rather than changed underneath four effects at
   once; `tier_notes` already exists in the schema (Enhanced Movement uses it for
   Wall-Crawling's caveat) and is the obvious place to hang a per-tier name.
4. **Nothing enforces mutual exclusion in a multiselect.** A player can tick both "one sight
   sense" and "all sight senses" on Obscure, or both intensities of the same Environment
   condition, and pay for both. Visible on the card, but a warning would be fair.
5. **A Variable Environment's redistribution is unchecked** — nothing verifies that what the
   player redistributes at use time stays inside the per-rank total they paid for.
6. **A power-scope modifier stops at the `Power`.** Removable is charged once per
   `Power`, which is what the rules mean by "the power as a whole". A device modelled as a
   `PowerGroup` of several powers therefore gets one discount *per child power*, each
   priced from that child's own total — the same arithmetic only when every split lands on
   a multiple of 5. Nothing checks for it; the honest build is one power with many effects,
   which is how the book's own armour example is written.
7. **The equipment-currency configurations build as powers, not gear.** Commlink is "1
   Equipment Point per rank" but drops onto the power canvas like anything else.
8. **Four configurations arrive as skeletons** the player must finish — Absorption,
   Berserker Rage, Poltergeist, Power Theft. The book leaves them blank too (which trait is
   boosted, which descriptor is absorbed), so this is faithful rather than incomplete, but
   it is worth knowing before someone "fixes" them.
9. **§5D's sweep had missed the effect-specific lists**, and pass 9 redid it across both
   modifier files — see §5L for the query and the four records it caught. It is clean as
   of pass 9; rerun it after adding modifier records, since a `costFormula` naming two
   prices with no `config` beneath it is silently the cheaper one.
10. **Nullify's readout changed shape**, and a saved character will notice. Its
   ``resistance`` game-term row is gone and an ``opposed`` row stands in its place, so a
   card that read "Resistance: 8 vs. Will or rank" now reads "Opposed: 8 vs. targeted rank
   or Will" and gains a rollable footer line where it had an unrollable one. No point cost
   moved and nothing needs migrating — the row is derived — but it is a visible change to
   an existing character's card, and the third such change this branch has made (see 2).
11. **The imposed-effect warning is constructor-only**, like the allocation, linked-range,
   Strength and requirement warnings beside it — the sheet card shows Power Level breaches
   alone. That is the existing convention rather than an oversight (see
   `_strength_violations`, which says so), but it does mean a character loaded from a file
   built under a different ruleset carries an over-budget imposed effect with no marker on
   the sheet until someone opens the constructor.
12. **`docs/mm-powers-architecture.md` had a dangling §9.** The schema block referenced a
   section on effect configuration fields that had never been written, and the numbering
   skipped from 8 to 10. Pass 8 wrote it, since the pass was about exactly that. Worth
   knowing that the doc's cross-references are not all load-bearing — check before trusting
   one.
13. **An array's total is not shown as working.** The constructor prints `Total cost: 24 PP`
   under three cards reading 20, 8 and 10 — the pooling explains it, and each card's badge
   names its own share, but the total line itself does not. §5F built `power_cost_formula`
   for precisely this complaint about Removable and it fires only for a power-scope
   modifier; extending it to render the array working would close the same gap for every
   array, Dynamic or not. Left alone as beyond §5J's scope, not because it is fine.
14. **`docs/notes/powers.md` and `docs/mm-powers-architecture.md` are kept current** with
   each pass. Update them in the same commit as the code, not afterwards — the notes are the
   thing a future session reads first.
15. **A sub-build has no Power Level check of its own.** A minion is "subject to the
   normal Power Level limits" and its sheet shows them the way any sheet does — but the
   *constructor* only warns about the point budget. Opening the minion is the only way to
   see a PL breach in it. Consistent with 11 (every constructor warning is
   constructor-only) but one level deeper, and worth knowing.
16. **A sub-build is not counted anywhere outside its power.** A minion's own gear,
   conditions and hero points are all real fields on a real `Character` that nothing
   plays with: the GM cannot pin it, damage cannot be applied to it, and a session does
   not surface it as a combatant. It is a *build*, not a creature at the table. Turning
   one into a playable NPC — "send this minion to the GM window" — is the obvious next
   thing to want and is out of §6M's scope.
17. **A Summon's minion is a single build even with Variable Type.** The book's own
   reading (p145: "You always summon the same minion unless you apply the Variable Type
   modifier") makes one build right for the ordinary case, and Multiple Minions doubles
   how many of that *same* creature appear. A Variable Type Summon really does want a
   menu of minions, which is §6M's Variable problem in miniature and was left with it.
18. **A modifier's `subBuild` count reads the chip's rank, so a repeatable modifier would
   need thought.** `modifier_rank` asks one `ModifierSelection`; two copies of a
   sub-build-bearing modifier would produce two independent slots sharing one config key
   and overwrite each other. Nothing is repeatable *and* sub-build-bearing today, and
   `test_the_ruleset_marks_exactly_the_repeatable_modifiers` would catch a seventh
   repeatable being added — but not this pairing specifically.

19. **A pooled split lives only at the group level.** `dynamic_points` is on `Power` and
   `PowerGroup`, not on `PowerEffectInstance`, so an array of a *single power's own
   effects* is priced for Dynamic members but cannot split its pool. The reason is one
   level down: an effect-level array has **no runtime member selection at all** — nothing
   checks "is this the selected effect", so every effect of an array-structured power is
   live simultaneously today, Dynamic or not. Building a pool on top of that would ration
   ranks in an array that is already, wrongly, running everything. Fixing the selection
   first is the honest order, and it is a gap that predates this whole job.
20. **A split array's ordinary members go dark.** `live_array_children` gives the pool
   priority: once any Dynamic member holds a share, the array's *non*-Dynamic alternates
   are not running, since they cannot hold a share and the whole pool is spoken for. That
   is the rules-correct reading and the dialog says so in as many words, but it does mean
   the way back to an ordinary alternate is "Clear the split" rather than clicking its
   card — and clicking one still moves `active_child_id`, silently, until the split is
   cleared.
21. **The split does not follow a rebuild.** A share is an absolute number of points, so
   editing the array — changing the base's rank, adding a member — moves the pool
   underneath a split that was already made. Nothing renormalises: the shares stay put and
   may now sum to less than the pool (legal, just wasteful) or, if the base got *cheaper*,
   to more than it. The dialog re-bounds every row the moment it is reopened, so the fix
   is one visit; a rebuild that quietly rescaled a player's split would be worse.
22. **`effect_current_rank` can now return 0.** Only through the pool, and only for a
   member below its minimum — but every reader of it should be checked against that
   before assuming a rank is at least 1. The dial's own floor is unchanged.
23. **Only an *effect* can be pushed.** The rank increase "includes improving your
   Strength rank for either Damage or Lifting, or your movement Speed rank in one mode of
   movement you have" (p21), and neither of those is a `PowerEffectInstance`: they are an
   ability and a derived readout. The seam for them exists — `trait_contributions` is where
   a temporary ability bonus would join size, powers, advantages and gear — but it is a
   second feature with a second control, and pass 14 did the half the Powers chapter needs.
   A Flight or a Speed *effect* is pushable today; unaided ground speed and Strength are not.
24. **Four of the six uses charge the fatigue and change no number.** An extra action, a
   +2 on a check, a renewed attempt and a fresh resistance check are table business: the app
   records them in the roll history and takes the rung. The +2 in particular does *not* reach
   the roller's bonus slider — wiring it there would mean the roller knowing which roll the
   effort was spent on, which nothing tracks.
25. **Nothing expires.** Extra Effort lasts "until the end of your turn" and the fatigue
   arrives "at the start of your next turn"; the app has no turn tracker, so the push is
   cleared by a button (per power on its card, per character in the System menu) and the
   fatigue lands immediately. The same bargain the Dynamic split strikes, and the same one
   the *whole* condition tracker strikes — recovery is out of scope there too.
26. **Determination's per-adventure uses are not counted.** The dialog offers the advantage
   route and says how many the sheet has; nothing decrements, because nothing knows when an
   adventure ends. A counter with no reset would lie more confidently than no counter.
27. **Extraordinary Effort is modelled as the same benefit twice**, not as two different
   ones. "You can gain two of the listed benefits, even stacking two of the same" (p86) —
   the doubled rank increase and the doubled fatigue are exact; taking an extra action *and*
   a rank increase for one doubled cost means using the menu twice, which charges two rungs
   anyway. The arithmetic agrees; only the wording of the second use is missing.
28. **A stunt can be dragged into a group.** The powers tree is drag-and-drop and nothing
   refuses a stunt card a group, so one can end up an array member — costing 0, which
   makes it the array's cheapest alternate and changes the pooling arithmetic under it.
   `strip_stunts` recurses for exactly that reason, so it still never reaches the file,
   and the ⚠ still names the ceiling; but the honest fix is a drop guard in
   `_on_combine`/`_on_move`, and it was not worth one this pass.
29. **Nothing stops a stunt and its source power being live at once.** The rules make an
   alternate effect mutually exclusive with what it is an alternate of, and the array
   machinery enforces exactly that for a bought alternate — but a stunt is a card of its
   own (the user's call, and the right one for a thing that costs nothing and lasts a
   scene), so no live-selection rule reaches it. It matters only when both are
   *standing* powers, since an instant one is used and gone; the card says what it is a
   stunt of, and the table settles the rest. Wiring it into `live_powers` was weighed and
   left: a stunt card defaults to active the moment it is created, so the source power
   would go dark with no visible reason the moment a stunt was built.
30. **Deleting a power leaves its stunts orphaned**, warning on their own cards rather
   than going with it. Deliberate — a stunt is a build the player made, and binning it
   silently because they removed something else is the failure the ⚠ exists to avoid —
   but it does mean the sheet can carry a card whose only fault is what is missing.
31. **The Conditions block re-renders twice for its own changes.** It now subscribes to
   `condition-changed` as well as publishing it (which is what lets Extra Effort's fatigue
   reach the chips), so its own edit renders once directly and once off the bus. Idempotent
   and cheap, and the alternative — a writer that is trusted not to be the block itself —
   is the kind of exception that rots.

---

## 8. Suggested order for the remaining passes

1. ~~**§6B Removable**~~ — done in pass 4 (§5F).
2. ~~**§6J the repeatable flag** and **§6I Dimensional**~~ — done in pass 5 (§5H, §5G).
3. ~~**§6E Concealment senses**~~ — done in pass 6 (§5I).
4. ~~**§6A Dynamic Alternate Effects**~~ — the price is done in pass 7 (§5J); the pool
   became **§6K**.
5. ~~**§6D Affliction's imposed effect**~~ — done in pass 8 (§5K).
6. ~~**§6C nested trait budgets**~~ — the budgets are done in pass 9 (§5L); the builds
   became **§6M**.
7. ~~**§6G Improvised Effects**~~ and ~~**§6F countering**~~ — done in passes 10 and 11
   (§5N, §5O).
8. ~~**§6M the sub-builds**~~ — done in pass 12 (§5P).
9. ~~**§6K the Dynamic point pool**~~ — done in pass 13 (§5Q). **§6 is now empty of
   anything this branch owns**, so the branch is ready to merge into `develop` with
   `--no-ff` whenever the user says the job is done, and this file can go with it.
10. ~~**§6H Extra Effort**~~ — done in pass 14 (§5R), on
    `feature/extra-effort-and-power-stunts` off `develop`.
11. ~~**§6H power stunts**~~ — done in pass 15 (§5S). **§6 is empty**: the branch is
    ready to merge into `develop` with `--no-ff` whenever the user says the job is done,
    and this file goes with it (read §7 first — the debts are the part worth keeping).

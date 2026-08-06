---
name: equipment
description: Resume and continue building MM-Companion's Equipment block (gear catalog, Equipment Points budget, worn items, weapon rolls, vehicles) and the powers-layer refactor it rides on (shared cards, data-driven stat appliers). Use when asked to continue/resume equipment, gear, the equipment block, the EP budget, vehicles, the stat appliers, or the next phase of that work — it picks up from EQUIPMENT_PLAN.md where the last session left off.
---

# Continue the Equipment work

This is a **large, multi-session feature**: an Equipment block that looks and behaves
like the Powers block but is *chosen* from a catalog rather than assembled — auto-grouped
by gear type, an Equipment Point budget, items worn on and off by clicking a card,
weapons that roll like attack powers, and vehicle speed feeding the System block. It is
built out of the powers pipeline, so it also carries a deliberate refactor of that layer.

It is built one phase at a time, tracked in **`EQUIPMENT_PLAN.md`** in the repo root.

**Do exactly one phase per session, then stop.** That file is the source of truth for
what is done and what comes next — read it before anything else.

## Steps

### 1. Read the plan

```bash
cat EQUIPMENT_PLAN.md
```

It carries the locked-in decisions, the architecture, a `Status:` line per phase, and a
progress log. If the file is missing, the feature branch was already merged and deleted —
say so and ask the user what they want instead of guessing.

### 2. Get on the branch

All of this work lives on **one** branch:

```bash
git checkout feature/equipment
```

If it does not exist, create it off `develop` (`git checkout develop && git checkout -b
feature/equipment`). **Never** commit on `develop` or `main`, and do not open a pull
request — see `CLAUDE.md`.

### 3. Pick the phase

Take the **first** phase whose `Status:` is not `done`. Announce which one you are doing.
Do not start the one after it, and do not skip ahead because a later phase looks easier —
the phases are ordered by dependency.

If the user names a specific phase, do that one instead.

### 4. Implement it

Follow the architecture in the plan — it names the modules, what belongs in each, and the
existing code to reuse rather than rebuild. Standing constraints:

- **`ui → core → data`.** Rules in `core/` (pure Python, no PySide6); Qt only in `ui/`.
- **No game content hardcoded in Python.** An `if`/`elif` chain over item names inside
  `core/` means that content belongs in `src/mm_companion/data/equipment.json`.
- **No new dependencies.** PySide6 plus the standard library.
- **Two currencies.** Power Points buy ranks of the Equipment advantage; Equipment Points
  (5 per rank) buy the items. Neither may leak into the other's total.
- **The `removable` discount is never reapplied** to an equipment item's cost.

Three reference documents, in the order they help:

- `EQUIPMENT_PLAN.md` — what to build and what was already decided.
- `docs/mm-equipment-design.md` — the rules: §2 patterns, §3 no-stacking, §4 the
  Strength-Based divisor, §7 the schema.
- `docs/design-data/equipment-design.json` — the rich per-item mechanics that get
  *promoted* into the shipped `src/mm_companion/data/equipment.json` as the engine grows
  to read each field.

Read `CLAUDE.md` for the wider repo conventions (block registry, signal bus, theme
tokens, the mod pipeline, the OGL boundary on `data/`).

### 5. Verify

Core phases are headless and fast — run the smallest sufficient set:

```bash
python -m pytest tests/test_equipment_data.py tests/test_equipment.py \
  tests/test_stat_appliers.py tests/test_derived_stats.py tests/test_powers.py
```

(only the files that exist yet). GUI tests need an offscreen platform:

```bash
QT_QPA_PLATFORM=offscreen python -m pytest tests/test_equipment_section.py \
  tests/test_powers_section.py tests/test_power_constructor.py
```

Then always:

```bash
ruff check . && black .
```

Two rules about the test runs. Do **not** launch several full `pytest` runs at once —
each spawns real Qt windows that stack up. And on the refactor phases (2 and 4) the
powers tests must pass **without being edited**: that is the whole proof that no
existing sheet's numbers or behaviour moved. If one needs changing, stop and say why
before changing it.

One known-environmental failure exists locally (`test_block_sizes`, a font issue on
Windows offscreen); it passes on CI and is not yours to fix.

To see a UI phase working in the real app, use the **`run-mm-companion`** skill.

### 6. Close the phase

Before committing:

1. Flip that phase's `Status:` to `done` in `EQUIPMENT_PLAN.md`.
2. Append a dated entry to the **Progress log** at the bottom — what shipped, which
   files, what was deliberately deferred, and anything the next session would otherwise
   have to rediscover. Be concrete; this is the handoff.
3. Commit to `feature/equipment` in the imperative mood.

Then tell the user what landed and which phase is next. **Do not merge into `develop`** —
that happens only when the user says the whole feature is done, at which point
`EQUIPMENT_PLAN.md` is deleted as part of the merge.

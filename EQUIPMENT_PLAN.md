# Equipment (and the powers refactor it pays for) — working plan

> **Temporary file.** It tracks the multi-session build of the Equipment block and
> the powers-layer refactor that makes it possible, and is deleted when
> `feature/equipment` merges into `develop`.
> Resume the work with the **`/equipment`** skill — it reads this file, picks the
> first phase whose `Status:` is not `done`, and does that one phase.

## What we are building

An **Equipment block** on the character sheet that looks and behaves like the Powers
block, with one governing difference: you *choose* gear from a catalog rather than
assembling it from scratch.

- **Cards like power cards** — a game-terms table per item, an eased dim when the item
  is off, a dice footer on anything that attacks. Same visual language throughout.
- **Automatic groups** by equipment type (Weapons, Armor, Utility, Accessories, …).
  Drag reorders items *within* a group and reorders the groups against each other; a
  weapon cannot be dragged into Armor.
- **Wear on and off** by clicking a card, the way a power toggles — except **every**
  worn item applies at once. There is no array-style exclusivity here.
- **Weapons roll** exactly like an attack power: roll hints, a 🎲 per line, the forced
  save as a follow-up chip.
- **Vehicle speed** reaches the Speed readout in the System & Power Level block.
- **A budget at the top of the block**: Equipment advantage rank × 5 Equipment Points.
- **The powers pipeline builds it.** Items are real `Power` builds under the skin, so
  the constructor edits them, game terms can be overridden, and costs derive.
- **Stat effects are data**, declared as an `apply(bonus, stat, rank)`-shaped record and
  reused when a player creates gear of their own.

## Decisions already taken (do not relitigate)

| Topic | Decision |
| --- | --- |
| Model shape | `EquipmentItem` **wraps** a real `Power` in a `build` field, alongside `catalog_id`, `category`, `worn`, `stacks` and `ep_override`. `Character.equipment` is its own list; `Character.powers` is untouched. Every existing rules function (`power_total_cost`, `effect_stat_rows`, `power_rolls`, `effect_is_active`) works on `item.build` **unchanged** — that is the whole point of the shape, and it is why the powers layer takes near-zero risk. Rejected: an `equipment` facet on `Power` itself (muddies every power with fields most never use), and a fully parallel model (every rules function would need an equipment twin, and they would drift). |
| Equipment is not a power | The design doc is explicit and the model follows it: two separate collections. A character built entirely from gear legitimately answers "no powers" — which matters for Nullify targeting and for the sheet's own power-point accounting. |
| Two currencies | Power Points buy *ranks of the Equipment advantage*; Equipment Points (5 per such rank) buy the items. The engine must never spend one as the other. |
| The discount is not reapplied | An item's EP cost is what the same effects would cost in PP **without** `removable`. The book skips the per-item discount precisely because the advantage already granted 5 points per rank. If the cost engine sees `modifiers:removable` on an equipment item, something has double-counted. |
| No-stacking | **Enforced by default**: `max()` *within* equipment, `max()` *again* between equipment and powers — never a sum of the two maxima. An outclassed item's card says so ("superseded by *Force Field*"), rather than silently showing an inert bonus. A per-item **"stacks with other bonuses" checkbox** in the equipment editor opts that item out, turning the rule into a warning for it and badging the item homerule the way `⌂` already marks a custom modifier. Power-vs-power bonuses keep summing, so **no existing sheet's numbers move**. |
| Budget overspend | Warn, never block: a red budget bar and a `⚠`. Add `storage.equipment_enforcement()` beside the existing `pl_enforcement()` so `"block"` becomes a one-line change later. |
| Platforms | Vehicles arrive as **pickable stock entries** first (Phase 9) — enough for their speed to reach the System block. The custom vehicle builder and installations are Phase 10, deliberately last: they are nested sub-builds with their own trait-cost tables and point pools, closer to a nested character than to an item. |
| PL still binds | Toughness from armour counts against `maxDefenseOrDodgePlusToughness` exactly like bought Toughness. Equipment contributions hook into the **existing** `power_pl_violations` seam rather than a parallel validator. |
| Licence | Everything added under `src/mm_companion/data/` is Open Game Content — provenance recorded in `_meta`, no Product Identity. `docs/design-data/` is reference material and is not shipped. |
| Standing licence to refactor | Reworking the powers layer is **in scope and wanted**, small look changes included, provided `tests/test_powers.py`, `tests/test_powers_section.py` and `tests/test_power_constructor.py` keep passing and the Powers block behaves as it does today. |

## The three source files

| File | What it is |
| --- | --- |
| `src/mm_companion/data/equipment.json` | The **shipped** catalog: 106 items, 34 stock vehicles, 11 vehicle features, 3 vehicle modifiers, 9 stock installations, 36 installation features, and the two size tables. Since Phase 1 each item also carries its mechanics (`effects`/`modifiers`/`grants`/`critical`/`patterns`/`implementation`), and `_meta` carries `currency`, `equipmentCategories` and `stackingRule`. |
| `docs/design-data/equipment-design.json` | The **rich reference**: per item `effects[]`, `modifiers[]`, `grants`, `critical`, `patterns[]`, `implementation{}`, plus a large `_meta` (stacking rule, Strength-Based divisor, vehicle/installation trait tables, printed discrepancies). Not shipped. |
| `docs/mm-equipment-design.md` | The written rules guide — read §2 (patterns), §3 (no-stacking), §4 (Strength-Based divisor), §7 (schema). |

The split follows the established `advantages-design.json` → `advantages.json`
convention: mechanics are **promoted** from the design file into the shipped catalog as
the engine grows to read each field. Phase 1 does the first promotion; later phases
promote what they need.

## Architecture

Strict `ui → core → data` is preserved, and equipment adds no new layer — it reuses the
powers layer wholesale.

### `core/` — pure Python, no PySide6

| Module | Contents |
| --- | --- |
| `core/equipment.py` (new) | `EquipmentItem` — `catalog_id`, `build: Power`, `category`, `worn`, `stacks`, `ep_override`, `id`, plus `accessories` in Phase 8. `to_dict`/`from_dict`, same idiom as `core/powers.py`. Plain data; no costs. |
| `core/rules/appliers.py` (new) | The data-driven stat-effect layer. A `Registry`-backed `StatApplier` keyed by `apply` kind (`bonus`, `penalty_removed`, `penalty_replaced`, `speed`, `sense`), each reading a data record and yielding `TraitContribution`s. `register_stat_applier` is the mod hook. |
| `core/rules/equipment.py` (new) | `equipment_budget`, `item_ep_cost`, `equipment_points_spent`, `equipment_points_remaining`, `equipment_violations`, `build_item_from_entry`, `worn_items`. |
| `core/data_loader.py` | New frozen records: `EquipmentEntry` (catalog), `EquipmentCategory`, `EquipmentRules`, later `StockVehicle`/`VehicleFeature`/`InstallationFeature`/`StockInstallation`/size rows. `GameData.equipment` + `equipment_catalog()`. |
| `core/character.py` | `Character.equipment: list[EquipmentItem]` and `equipment_group_order: list[str]`, both tolerant of absence so every existing save loads unchanged. |
| `core/storage.py` | `equipment_enforcement()` accessor (never read off `load_settings()` — see `CLAUDE.md` on why every setting needs an accessor). |

### `ui/` — Qt

| Module | Contents |
| --- | --- |
| `ui/cards/` (new) | The card machinery extracted out of `ui/sections/powers.py`: the draggable card with its off-progress easing, the roll line, the node list with its drop hints, the group header, the terms grid. Shared by Powers and Equipment. |
| `ui/sections/equipment.py` (new) | `EquipmentSection` — budget bar, catalog picker, auto-grouped cards, drag-reorder, wear toggle. |
| `ui/blocks/registry.py` | One more `_BASE_BLOCKS` row (key `equipment`) with the same bus tables Powers uses, plus a `ui/block_sizes.json` entry. |
| `ui/power_constructor/` | Gains a **gear mode** in Phase 7: EP in the cost readout, the "stacks" checkbox, otherwise identical. |

### Reuse — do not rebuild these

- `core/registry.py` + the `PATTERN_BEHAVIOURS` / `GATE_KINDS` pattern in
  `core/rules/runtime.py` — the stat-applier registry is a third instance of it, not a
  new mechanism.
- `core/rules/powers_cost.py` (`power_total_cost`, `effect_total_cost`,
  `effect_effective_rank`), `core/rules/powers_terms.py` (`effect_stat_rows`,
  `effect_game_terms`, `effect_roll_numbers`), `core/rules/rolls.py` (`power_rolls`,
  `follow_up_for_result`, `localize_spec`), `core/rules/runtime.py`
  (`effect_is_active`, `live_powers`), `core/rules/validation.py`
  (`power_pl_violations`) — all of them take `item.build` as-is.
- `ui/drop_feedback.py` (`DropFeedback.show_reject()` is exactly the cross-group
  rejection cue), `ui/blocks/bus.py`, `ui/widgets.py`, `ui/lock.py`,
  `ui/wheel_guard.py`, `ui/pin_picker.py` (the catalog picker's shape).

## Phases

One phase per session. Each ends by verifying, flipping its `Status:` line, appending a
dated entry to the progress log, and committing to `feature/equipment`.

### Phase 0 — Scaffolding
**Status: done**

The `feature/equipment` branch off `develop`, this file, and the `/equipment` skill.

### Phase 1 — Data layer
**Status: done**

Promote the mechanical fields from `docs/design-data/equipment-design.json` into
`src/mm_companion/data/equipment.json`: per item `effects[]`, `modifiers[]`, `grants`,
`critical`, `patterns[]`, `implementation{}`. Add `_meta.equipmentCategories` — an
ordered `{id, title}` array that *is* the automatic grouping axis, mirroring how
`_meta.conditionGroups` drives the conditions menu (`categoryKey` today is a bare
id → description map with no titles and no order).

Parse in `core/data_loader.py` into frozen records — `EquipmentEntry`,
`EquipmentCategory`, `EquipmentRules` (points-per-advantage-rank, the advantage id, the
stacking rule's targets, the cost kinds) — and expose `GameData.equipment`,
`GameData.equipment_categories`, `GameData.equipment_rules`, `GameData.equipment_catalog()`.
The existing `_deep_merge` gives mods catalog extension for free; confirm with a test.

Leave `stockVehicles` / installations unparsed this phase — Phases 9–10 own them.

*Verify:* `tests/test_equipment_data.py` (new) + `tests/test_data_loader.py`.

### Phase 2 — Data-driven stat appliers (the core powers refactor)
**Status: done**

This is the "all effects that influence stats should be data driven, something like
`apply(bonus, stat, rank)`" ask, and it lands in the powers layer first so equipment
inherits it.

Today `components.Integration.trait_boost` describes a booster and
`rules/runtime.py::power_trait_bonuses` hardcodes what it means ("the bonus is the
effect's rank, added to the target, and multiple powers stack"). Split those apart:

1. `core/rules/appliers.py` — a `StatApplier` `Registry` keyed by `apply` kind, taking a
   data record and a rank and yielding `TraitContribution(amount, stat, source, stacking, kind)`.
   Shipped kinds: `bonus`, `penalty_removed`, `penalty_replaced`, `speed`, `sense`.
   `register_stat_applier` is the mod hook (same shape as `PATTERN_BEHAVIOURS`).
2. Re-express today's `TraitBoost` as a **registered applier**, so `power_trait_bonuses`
   runs through the registry and produces **identical numbers**. `tests/test_derived_stats.py`
   passing untouched is the proof.
3. Add the stacking resolver: `max()` within a stacking group, `max()` between the
   equipment group and the powers group, `sum()` among powers (and among items flagged
   `stacks`). It returns both the winning bonus and the **superseded** sources, so the
   card can name what beat it.
4. Point `effective_ability`, `resistance_total`, `skill_total` and `skill_bonus` at the
   resolver instead of summing inline.

*Verify:* `tests/test_stat_appliers.py` (new), `tests/test_derived_stats.py`,
`tests/test_powers.py`, `tests/test_conditions.py`.

### Phase 3 — Equipment model and cost engine
**Status: done**

`core/equipment.py` with `EquipmentItem`; `Character.equipment` and
`equipment_group_order` wired through `to_dict`/`from_dict` (a save without them loads
fine — no schema bump). `core/rules/equipment.py` with the budget
(`advantage rank × equipment_rules.points_per_rank`), `item_ep_cost` (the catalog's
printed `cost` for an unmodified item, `power_total_cost(item.build)` once it has been
edited — and **never** the `removable` discount), the spend/remaining pair, and
`build_item_from_entry` turning a catalog record's `effects[]`/`modifiers[]` into a real
`Power`. `storage.equipment_enforcement()`.

*Verify:* `tests/test_equipment.py` (new), `tests/test_character.py`, `tests/test_library.py`.

### Phase 4 — Card renderer refactor
**Status: done**

Pure refactor, no feature. Extract from the 1794-line `ui/sections/powers.py` into a
shared `ui/cards/` package: `_DraggableCard` (with `set_off_progress` and the
hover/ancestor highlight rules), `_RollLine`, `_NodeList` (drop hints, reorder vs
combine), `_GroupHeader`, `_terms_grid`. `PowersSection` becomes a consumer.

Three things in that file are load-bearing and must survive intact: font sizes set on
the `QFont` and never in a stylesheet (a stylesheet `font-size` outranks the card font
and would sit the transition out); only a **leaf** card gets a background wash; and
exactly one card is lit at a time (`enterEvent` stands ancestors down, `leaveEvent`
hands the highlight back via `QCursor.pos()`). All three are documented in `CLAUDE.md`.

*Verify:* `tests/test_powers_section.py` and `tests/test_power_constructor.py` pass
**unchanged**, plus eyes on the real app via `run-mm-companion`.

### Phase 5 — The Equipment block
**Status: done**

`ui/sections/equipment.py`, registered in `ui/blocks/registry.py` with a `block_sizes.json`
entry and the bus tables Powers uses (`changed` → `BUILD_CHANGED`/`ENHANCEMENTS_CHANGED`/
`DERIVED_CHANGED`/`EDITED`; `runtimeChanged` omitting `EDITED`, because wearing a
jacket is a play action and must not dirty the sheet).

- **Budget bar** on top: `18 / 25 EP`, red past the budget.
- **"Add Equipment"** opens a searchable catalog picker grouped by category with costs
  shown; take its shape from `ui/pin_picker.py`.
- **Automatic groups** from the item's `category`, titled and ordered by
  `equipment_categories`, with the user's `equipment_group_order` overriding the order.
- **Drag** reorders within a group and reorders groups; a cross-group drop is refused
  with `DropFeedback.show_reject()` (a bare `event.ignore()` is invisible when an
  ancestor accepts).
- **Click to wear/unwear**, the same eased dim, but every worn item applies.
- **Cards** carry the full game-terms table plus the "superseded by *(source)*"
  annotation from Phase 2.

*Verify:* `tests/test_equipment_section.py` (new), `tests/test_block_registry.py`,
`run-mm-companion`.

### Phase 6 — Rolling and derived wiring
**Status: done**

Weapon cards get their dice footer from `power_rolls(item.build, char, data)` — attack
line, the forced save written down but unbuttoned (`rolled_by_target`), the follow-up
chip on the history card. `rules/pins.py` gains an equipment pin kind so a GM can pin a
weapon's attack to an NPC card. `SystemInfoSection`'s Speed readout folds in a new
`movement.equipment_speed_lines`. Equipment contributions reach `power_pl_violations`
so armour Toughness counts against the paired cap.

*Verify:* `tests/test_roll_specs.py`, `tests/test_roll_routing.py`, `tests/test_gm_pins.py`,
`tests/test_derived_stats.py`.

### Phase 7 — Custom items via the constructor
**Status: done**

`PowerConstructorWindow` gains a gear mode: the cost readout reads EP, the title says
Equipment, and the "stacks with other bonuses" checkbox lives here. Game-term overrides
and the dev-mode edits work exactly as they do for powers. Add "Create Custom Item" for
off-catalog gear, and make an edit replace the item in place on a deep copy — the same
contract `PowersSection._on_power_edited` already honours.

*Verify:* `tests/test_power_constructor.py`, `tests/test_equipment_section.py`.

### Phase 8 — Strength-based weapons and accessories
**Status: done**

The §4 divisor, which is **not** the existing effective-rank cost rule and must not be
conflated with it: when modifiers push a Strength-Based Damage effect above 1 point per
rank, the wielder adds `floor(strength / net_per_rank)`, not their full Strength. Two
carve-outs: an effect costing under 1 point per rank adds Strength unmultiplied, and
flat modifiers that don't change cost-per-rank never trigger the divisor. Then the
weapon-Toughness cap — which is a **warning**, not a clamp ("Strength 10 exceeds this
sword's Toughness 7 — it will break on use"), because the break is a real event.

Accessories (`attachesTo`) attach to a host item, fold their cost into the host, and
keep their scope on that weapon — the targeting scope's Improved Aim applies to *that*
weapon only, which the sheet gets wrong if accessories are loose items.

*Verify:* `tests/test_equipment.py`, `tests/test_powers.py`.

### Phase 9 — Stock vehicles
**Status: done**

Parse `stockVehicles`, `vehicleFeatures`, `vehicleModifiers` and `vehicleSizeTable`. A
`vehicle` category whose card shows Size / STR / TOU / DEF / Speed rather than the
standard effect table, and whose speed rank reaches the System block's readout via
Phase 6's seam. Note `vehicleModifiers` (Durable / Minion / Summonable) modify the cost
of the **Equipment advantage ranks** allocated to the vehicle, not the vehicle's EP cost —
a separate field, not a folded-in modifier.

*Verify:* `tests/test_equipment_data.py`, `tests/test_equipment_section.py`.

### Phase 10 — Custom vehicles and installations
**Status: done**

The vehicle trait-cost builder (Size chosen first, since it sets the STR/TOU/DEF
baselines; ranks past 5 extend arithmetically), moving Defense rank
(`current speed rank + size defense modifier`), and the installation builder (Size from
the free rank 5, Toughness at 1 EP per +2, Features at 1 EP each). Installations get
their own PL branch: Toughness may reach **twice** the series PL while Impervious stays
capped at PL — a different cap pair from characters.

*Verify:* `tests/test_equipment.py`, `tests/test_equipment_section.py`.

### Phase 11 — Docs, mod hooks, polish
**Status: done**

`docs/mm-equipment-architecture.md` (the counterpart to `mm-powers-architecture.md`);
the stat-applier registry added to the registry table in `docs/modding.md`; a sample mod
under `docs/sample-mods/` that registers one, exercised end-to-end by
`tests/test_mod_loading.py`; the `CLAUDE.md` section describing the equipment layer;
theme tokens for anything the block introduced. Then delete this file as part of the
merge into `develop`.

## Conventions for this work

- **`ui → core → data`.** The rules live in `core/`, Qt only in `ui/`. No game content
  hardcoded in Python — if an `if`/`elif` chain over item names appears in `core/`, that
  content belongs in `equipment.json`.
- **No new dependencies.** PySide6 plus the standard library.
- **Never commit on `develop` or `main`**, and do not open a pull request. All of this
  lives on `feature/equipment`.
- **One phase per session.** The phases are ordered by dependency; do not skip ahead
  because a later one looks easier.
- Read `CLAUDE.md` for the wider conventions (block registry, signal bus, theme tokens,
  the mod pipeline, the OGL boundary on `data/`).

## Risks to keep visible

- **Phase 2 is the one that can break existing sheets.** It rewrites how every trait
  bonus reaches the sheet. The mitigation is that `TraitBoost` becomes a registered
  applier producing identical numbers, and `tests/test_derived_stats.py` is the guard —
  it must pass **without being edited**.
- **Phase 4 is a 1794-line surgery** on the most animation-sensitive file in the app.
  Three documented invariants (fonts on the `QFont`, leaf-only background wash, one lit
  card at a time) are easy to lose in a move. Same guard: existing tests unedited.
- **Double-counting the removable discount** is the most likely rules bug in the whole
  feature, and it is silent — every item would just be cheap.
- **Two currencies.** An EP leaking into the PP pool (or the reverse) will not raise; it
  will quietly show a wrong build total. Assert on both totals in Phase 3's tests.

## Progress log

### 2026-08-06 — Phase 0
Branched `feature/equipment` off a clean `develop`. Read the three added files and traced
the powers layer end to end (`core/powers.py`, `core/rules/{runtime,powers_cost,powers_terms,rolls,derived,validation}.py`,
`ui/sections/powers.py`, `ui/blocks/{base,registry,bus}.py`, `core/character.py`).
Wrote this plan and the `/equipment` skill. Four decisions taken with the user: the
wrapping model shape, no-stacking enforced by default with a per-item opt-out checkbox,
budget warn-with-a-seam, and stock vehicles before custom platforms. No code changed.
Next: **Phase 1 — Data layer**.

### 2026-08-06 — Phase 1: Data layer

**Shipped.** `src/mm_companion/data/equipment.json` now carries the mechanics, and
`core/data_loader.py` parses them. `equipment.json` was added to `data/mod.json`'s
`files` list — until now it shipped but was never loaded by anything.

*The promotion.* All 106 items gained `effects[]`, `modifiers[]`, `grants`, `critical`,
`patterns[]` and `implementation{}` from `docs/design-data/equipment-design.json`. The
ids and their order were already identical in both files, so it was a clean per-id
merge. Empty values (`"grants": {}`, `"effects": []`, `"critical": null`) are *omitted*
rather than written out — the parser defaults them, and 106 empty lines is noise. The
design file's per-item `notes` were deliberately **not** promoted: they are authoring
commentary ("Explicitly NOT Strength-based — too light"), and the design file remains
the place to read them.

*`_meta` was restructured*, not just appended to:
- `currency` is now the design file's record (`name`/`abbreviation`/`source`/
  `pointsPerAdvantageRank`/`note`/`reconfigure`/`notAPower`) instead of a prose string.
  The `equipmentPointsByAdvantageRank` lookup table was **dropped** — it is
  `rank × pointsPerAdvantageRank`, and two sources of one truth is a bug waiting.
- `equipmentCategories` is the new ordered `{id, title}` axis (11 rows, weapons first,
  platforms last). `categoryKey` stays as the id → description map and the parser folds
  its text into each `EquipmentCategory.description` — exactly the shape
  `conditions.json` uses for `categoryKey` + `sheetSections`.
- `stackingRule` (rule / `appliesTo` / engineNote) replaces the old one-line
  `stackingNote`, which was also **mojibake** — its em dash had been double-encoded
  through cp1251. The file is now clean: U+2014 is the only non-ASCII codepoint in it.
- `strengthBasedDamage` was left in the design file on purpose. Phase 8 promotes it.

*Records* (all frozen, in `data_loader.py` between `Movement` and `Readout`):
`EquipmentEffectRef`, `EquipmentModifierRef`, `CriticalProfile`, `EquipmentEntry`,
`EquipmentCategory`, `EquipmentRules`; `GameData.equipment` / `.equipment_categories` /
`.equipment_rules` / `.equipment_catalog()`. Two decisions worth keeping:
- **References are unqualified at parse time.** The JSON writes `"effects:damage"` and
  `"advantages:improved_critical"` (the design vocabulary, and it reads well); the
  record carries `"damage"`, which indexes straight into `GameData.effects` /
  `modifier_catalog()`. `_unqualify` splits on the last colon and knows nothing about
  which namespaces exist, so a mod inventing one gets the same treatment. Every ref in
  the shipped catalog resolves — there is a test, and it passed first time.
- **`implementation` stays an open dict.** 78 distinct keys across the catalog (ranges,
  charges, ammo modes, `attachesTo`, escape DCs). Typing it now would be inventing a
  schema for fields no phase reads yet; retaining it whole is what lets Phases 6–10 read
  their own keys without another data migration.

*Traps left for later phases, deliberately:*
- **`omni_equipment` carries `modifiers:removable`** and is the one catalog item that
  legitimately does — the design file marks it "Equipment tier, -8 points … bought with
  Power Points, not Equipment Points". Phase 3's rule that seeing `removable` on an
  equipment item means something double-counted needs to except this item (or exclude
  it from the EP path entirely; its `costKind` is `built` and its `cost` is `null`).
- **`cost` is `None` only for `costKind: "built"`, but not every `built` item lacks a
  price** — `utility_kit` is `built` with a printed 25. The test asserts the one-way
  implication, not an equivalence.
- Six items are not `fixed` price (`evidence_kit` ranked; `armor_cloth` and
  `armored_costume` per-rank; `utility_kit`, `trick_arrows`, `omni_equipment` built).
  All six carry a `costNote`, and a test holds that.

*Not done, by design:* `stockVehicles`, `vehicleFeatures`, `vehicleModifiers`,
`installationFeatures`, `stockInstallations` and the two size tables are still in the
shipped file **unparsed** — Phases 9–10 own them. `CLAUDE.md` was not touched; Phase 11
owns the docs.

*Verified:* `tests/test_equipment_data.py` (new, 12 tests — records, referential
integrity, the category axis, cost invariants, the currency, and two mod-merge tests
proving `_deep_merge` gives catalog extension and per-field override for free) plus
`test_data_loader`, `test_mods`, `test_mod_loading`, `test_packaging`, `test_powers`,
`test_derived_stats`, `test_conditions`, `test_character`, `test_library`,
`test_system_rules` — 355 passed. `ruff check .` and `black --check .` clean.

Next: **Phase 2 — Data-driven stat appliers**, the powers-layer refactor. Its guard is
that `tests/test_derived_stats.py` must pass *unedited*.

### 2026-08-06 — Phase 2: Data-driven stat appliers

**Shipped.** `core/rules/appliers.py` (new, the lowest rules layer) now owns both halves
of the split: *what a stat effect means* (a registry of appliers) and *how several of
them net on one trait* (the stacking resolver). `power_trait_bonuses` no longer decides
either. **No existing test was edited** and the whole suite passes (2010), which is the
guard the plan asked for.

*The vocabulary is in `components.py`*, beside `PATTERNS`/`GATE_*` where the other
enum vocabularies live — `APPLY_BONUS`/`PENALTY_REMOVED`/`PENALTY_REPLACED`/`SPEED`/
`SENSE` + `APPLY_KINDS`. `TraitBoost` grew three fields (`apply`, `per_rank`, `flat`)
and *is* the stat-effect data record now; its docstring says so. All three default to
M&M's own rule (a `bonus` worth exactly the rank), so a record stating none of them —
including every mod's — behaves as it always did. `statIntegration` parses `apply` /
`amountPerRank` / `amountFlat`, and `effects.json` gained an `applyNote` + `applyKey`
doc block plus an explicit `"apply": "bonus"` on the two effects that actually carry
one (`enhanced_trait`, `protection`), so an author sees the vocabulary in the data.

*The registry.* `STAT_APPLIERS` keyed by apply kind, `register_stat_applier` the mod
hook, `apply_stat_effect(kind, ctx)` the call — an **unregistered kind yields nothing
rather than raising**, so an effect naming a disabled mod's kind simply grants no
bonus. An applier takes one `ApplyContext` (record, rank, resolved target, source,
game data, plus the granter's `stacking`/`group`) and returns `TraitContribution`s;
`ApplyContext.amount` is the `flat + rank × per_rank` arithmetic in one place.
All five kinds ship. `bonus` is today's behaviour exactly; `speed`/`sense`/the two
penalty kinds land in their own categories that **nothing reads yet** — `movement.py`
is still the movement authority, and the penalty kinds are the Phase 6/8 seam. They
are registered rather than stubbed so a mod or an equipment record can route to them
today.

*The resolver.* `resolve_contributions` nets one trait; `resolve_bonuses` does a whole
sheet. Within a group the `sum` contributions add and the largest `max` one joins
them; between groups the larger total **wins outright** rather than adding. So
powers-plus-advantages keep summing (no sheet's numbers move) while equipment will
`max()` against itself and against powers, and an item flagged "stacks" adds on top of
its group's winner. Everything that lost is returned in `TraitBonus.superseded` as
`SupersededBonus(source, amount, beaten_by)` — that is what Phase 5's card annotation
reads. Ties go to the group seen first, which is always the powers group.

*Four things worth knowing next session:*
- **`power_trait_bonuses` is now the powers-*only* view** and keeps its three fixed
  keys; the sheet-wide number is the new **`derived.trait_bonuses`**, which
  `effective_ability` / `resistance_total` / `skill_bonus` read. Today they differ only
  by the advantages. `AbilitiesSection`/`ResistancesSection` still call
  `power_trait_bonuses` for their enhancement column — **Phase 5 must point them at
  `trait_bonuses`**, or worn gear will raise the total without showing in that column.
- **Advantages became contributions too** (`derived.advantage_contributions`), gathered
  *after* the powers so `skill_bonus`'s source order is unchanged. No shipped advantage
  carries `skillBonusPerRank`, so the test builds a synthetic `Advantage` via
  `dataclasses.replace(data, advantages=[...])` — reuse that trick.
- **`_boost_target` vs `_resolved_trait_target`.** The first is the raw target
  (whatever kind of trait it names) and feeds the appliers; the second is it narrowed
  to numeric traits and still feeds the game-terms "Enhances" row. Do not merge them.
  The old `affects`-must-be-numeric gate moved *into* the `bonus` applier
  (`BOOST_TRAIT_CATEGORIES`), which is why a hand-edited Enhanced Senses naming a skill
  still grants nothing.
- `_trait_category` moved to `appliers.trait_category` (public); `runtime._trait_bonus`
  is gone, replaced by `derived._trait_bonus` over the new resolver.

*Not done, by design:* nothing gathers `GROUP_EQUIPMENT` contributions yet — Phase 3
builds the items, and the applier layer is already waiting for them. Docs are Phase 11's
(`CLAUDE.md` and `docs/mm-powers-architecture.md` still describe the old hardcoded rule;
`docs/modding.md`'s registry table does not list `STAT_APPLIERS` yet).

*Verified:* `tests/test_stat_appliers.py` (new, 25 tests — the registry and its mod
hook, per-rank/flat arithmetic, each shipped kind's category, gathering off a live
power, and eight resolver cases covering sum/max/cross-group/ties/superseded) plus the
**unedited** `test_powers`, `test_derived_stats`, `test_conditions`, `test_data_loader`,
`test_powers_section`, `test_power_constructor`, `test_roll_specs`, `test_roll_routing`,
`test_gm_pins`, `test_ui_wiring`. Full suite: **2010 passed**. `ruff` and `black` clean.

Next: **Phase 3 — Equipment model and cost engine**.

### 2026-08-06 — Phase 3: Equipment model and cost engine

**Shipped.** `core/equipment.py` (the model) and `core/rules/equipment.py` (the budget,
the price, and the catalog → `Power` builder). Gear is **live**: a worn item's bonus
reaches the sheet through the Phase 2 applier layer, does not stack, and says what beat
it. No existing test was edited; the suite is **2043 passed** (was 2010).

*The model.* `EquipmentItem` wraps a real `Power` in `build`, alongside `catalog_id`,
`category`, `worn`, `stacks`, `ep_override`, `id`. Three decisions worth keeping:
- **`worn` is runtime, not persisted** — the same bargain a power's `activated` makes,
  and the reason Phase 5's wear toggle may omit `EDITED` from its bus tables. A loaded
  character comes up wearing everything. `stacks` and `ep_override` *are* build state.
- **`category` is copied off the entry at pick time** rather than looked up, so a card
  still has a home when a disabled mod takes its entry away.
- `Character.equipment` / `equipment_group_order` are **omitted from `to_dict` when
  empty**, so a save written before equipment existed round-trips byte-for-byte. No
  schema bump.

*The builder.* `build_item_from_entry(entry, game_data, rank=1)` turns a catalog record
into a `Power`. Every mapping is found through the *data*, never by naming a key:
- `strengthBased` → the effect's config field that `toggles` a modifier which
  `adds_ability`, so the checkbox is ticked **and** the modifier attached. Nothing in
  Python says "Strength" or "Damage".
- `resistance` → the config field whose own `overrides` is `"resistance"`.
- `degrees[i]` → the key named by `base.resistance_outcomes[i].config_key` (Affliction's
  `resistanceOutcomes` already declares `degree1/2/3`).
- `configuration` ("snare", "stun") has no declared field, so it is kept under a
  `"configuration"` config key rather than dropped.
- Entry modifiers split into extras/flaws by the `Modifier.category` on the record.
All 106 entries build; every fixed-price entry then prices at exactly its printed cost
(there is a test that walks the whole catalog).

*The removable rule, enforced twice.* `_entry_modifier_selections` drops any modifier
whose `gate == "removable"` — which is how the `omni_equipment` trap Phase 1 flagged is
handled, with no per-item special case — and `_undiscounted` strips one off a build
before pricing it, for a hand-edited save or an imported power. Both are tested.

*The price.* `item_ep_cost` answers in three steps: `ep_override`; else the catalog's
printed cost when `item_is_stock` (× rank for the `per_rank`/`ranked` kinds); else
`power_total_cost` of the undiscounted build. `item_is_stock` compares a `_build_signature`
(structure, cost_override, and per effect the id/rank/modifiers/config — **ids, names and
runtime flags excluded**) against a freshly built entry *at this item's own rank*, so a
ranked item is stock at every rank. The cost-kind vocabulary (`COST_FIXED`/`RANKED`/
`PER_RANK`/`BUILT`, `PER_RANK_COST_KINDS`) lives in `core/equipment.py` beside the model,
the same way `STRUCTURES` and the `components` patterns do.

*Two things done slightly ahead of the listed scope, deliberately:*
- **`equipment_contributions` and `worn_items` live in `core/rules/runtime.py`**, not in
  `rules/equipment.py`, and are re-exported from it (that module has an `__all__` now).
  They had to: `rules/equipment.py` imports `powers_cost` → `derived`, and `derived`
  needs the contributions, so defining them there is a cycle. Runtime is the honest home
  anyway — it is the "what is currently live on the sheet" layer.
- **`derived.trait_contributions` now folds equipment in**, third after powers and
  advantages. Without it the model would be inert data and Phase 5 would be doing rules
  work in the UI. Order matters: the first two are the Power-Point group and sum, gear
  arrives last in its own group, and a tie goes to the group seen first — the powers.
  A sheet with no gear contributes nothing, which is why no derived number moved.
- The shared gatherer is the new **`runtime.build_contributions(power, char, data, *,
  stacking, group)`**; `power_contributions` is now a loop over `live_powers` calling it,
  and `equipment_contributions` the same loop over `worn_items` with `GROUP_EQUIPMENT`
  and `STACK_MAX` (or `STACK_SUM` for an item flagged `stacks`). One code path, so gear
  and powers cannot drift.

*Also:* `storage.equipment_enforcement()` + `EQUIPMENT_ENFORCE_WARN`/`_BLOCK` and the
`DEFAULT_SETTINGS` entry — a **separate** setting from `pl_enforcement`, since the two
are different currencies and a table may police one and not the other.

*Deliberately not done:*
- The five accessories with modifiers but no effects (`flashlight`, `sash`,
  `laser_sight`, `suppressor`, `armor_cloth`) build an **empty** power. That is correct
  for now — they have their printed price and grant nothing loose — but attaching them
  to a host weapon is **Phase 8**, and it is what makes their modifiers mean anything.
- `EquipmentEffectRef.extra` (`senseAffected`, `overcome`, `degreeNote`,
  `rankIsPurchased`) is **not** copied into the instance config. It stays on the catalog
  record for the phase that renders it (5/6), rather than being guessed into a config
  key no effect declares.
- Nothing reads `equipment_violations` yet (Phase 5's budget bar), and equipment
  contributions do **not** yet reach `power_pl_violations` — armour Toughness against the
  paired cap is Phase 6, as planned.
- **`AbilitiesSection`/`ResistancesSection` still call `power_trait_bonuses`** for their
  enhancement column, so worn gear raises the *total* without showing in that column.
  This was flagged in the Phase 2 log and remains **Phase 5's job**: point them at
  `derived.trait_bonuses`.

*Verified:* `tests/test_equipment.py` (new, 32 tests — model round-trip and the
non-persisted `worn`, the whole catalog building, the three data-driven config mappings,
the removable rule twice over, printed vs derived vs override pricing, the budget and its
two violations, both directions of currency separation, and the five stacking cases) plus
the **unedited** `test_equipment_data`, `test_stat_appliers`, `test_derived_stats`,
`test_powers`, `test_character`, `test_library`, `test_conditions`, `test_data_loader`.
Full suite: **2043 passed**. `ruff` and `black` clean.

Next: **Phase 4 — Card renderer refactor** (extract `ui/cards/` out of the 1794-line
`ui/sections/powers.py`). Its guard is that `tests/test_powers_section.py` and
`tests/test_power_constructor.py` pass *unedited*.

### 2026-08-06 — Phase 4: Card renderer refactor

**Shipped.** `ui/cards/` is the shared card package; `ui/sections/powers.py` is a
consumer of it and went 1795 → 1002 lines. Pure move — **no test was edited** and the
suite is **2043 passed**, the same number Phase 3 left.

*The five modules*, split by what a piece is rather than by what powers needed:
- `cards/drag.py` — `DragHandle` (the ⠿ grip) and `NODE_MIME`, the drag payload's
  format.
- `cards/card.py` — `DraggableCard` with `set_off_progress` and the hover rules,
  plus `lerp`, `card_ancestors_of` and the new `hand_back_highlight`.
- `cards/node_list.py` — `NodeList` and `GroupHeader`, the two drop targets.
- `cards/rolls.py` — `RollLine` and the new `RollsFooter`.
- `cards/effects.py` — the per-effect body: `effects_block`, `effect_summary`,
  `modifiers_column`, `terms_grid`, `effect_title`, `modifier_names`, `role_note`,
  `structure_header`, `terms_style`, and the two stretch constants.

*Four decisions worth keeping:*
- **The mime type is a parameter, not a constant in shared code.** Every widget that
  reads or writes the payload takes `mime=NODE_MIME` as a keyword-only argument.
  Without that seam, an Equipment block built on the same cards would advertise the
  *same* format and a power card could be dragged into it (and vice versa) — the drop
  would resolve an id the other board has never heard of. Phase 5 passes its own.
- **`_effects_block` and the roll footer were extracted too**, beyond the five names
  the phase listed. Both are the card's *body* and an equipment item wraps a real
  `Power`, so leaving them behind would have had Phase 5 copying ~120 lines that then
  drift. `cards/effects.py` is free functions over `(power/effect, character, data)`
  because the two sections hold their model differently and neither owns the drawing.
- **`RollsFooter` takes a `pin_ref` callback**, `Callable[[int], PinRef]`, instead of
  knowing about `PIN_POWER`. A pin names a roll by *which entry of the card's roll
  list* it is, so the footer only needs the index → ref mapping; Phase 6 passes an
  equipment ref through the same seam. `pins` is the section's own live
  `PinMenuState`, read at menu time, so `set_pin_target`/`set_pinned` keep working
  without rebuilding the cards. A footer given no `pin_ref` installs no context menu
  at all.
- **`_set_hovered` became public `set_hovered`** on the card: the roll line stands its
  enclosing cards down across a module boundary now. The `_hovered` *attribute* is
  untouched — two tests read it directly.

*The three load-bearing invariants survived, and each is checked by an unedited test:*
font sizes on the `QFont` and never in a stylesheet (`test_a_cards_type_scales…`),
leaf-only background wash (`test_a_hovered_group_lights_its_outline_but_does_not_fill`),
one card lit at a time (`test_hovering…` ×2). `hand_back_highlight` is the second half
of that last rule factored out of the two `leaveEvent`s that had it copied.

*The compatibility shim.* `powers.py` keeps `_DraggableCard = DraggableCard` and four
siblings at module level. Four test files import those spellings, and the phase's guard
is that they pass *unedited* — so the old names stay as aliases onto the shared classes
rather than the tests being rewritten. Anything new should import from
`mm_companion.ui.cards`.

*Not moved, deliberately:* `_ModeToggle` (Independent/Array/Linked is a *powers*
structure, not card machinery), the whole tree model (`_locate`, `_on_combine`,
`_on_move`, `_collapse_singletons`, `_normalize_arrays`), and everything about what a
click means (`_activation_role`, `_arm_activation`, `_show_activation`, the runtime
setters). Those are the section's job and Equipment will answer them differently —
every item is worn independently, with no array exclusivity.

*Verified:* `test_powers_section` (55), `test_power_constructor`, `test_roll_routing`,
`test_ui_wiring` — 200 passed, **unedited** — then the full suite at **2043 passed**.
`ruff` and `black` clean. Eyes on the real app via a driver script: a sheet with a leaf
attack card, a switched-off gated card, a Linked group and an Array group all render as
before, including the dim/type-scale/padding of the off card and its accent edge.

*Still outstanding for Phase 5*, carried from the Phase 2 and 3 logs:
**`AbilitiesSection`/`ResistancesSection` still call `power_trait_bonuses`** for their
enhancement column, so worn gear raises the *total* without showing in that column.
Point them at `derived.trait_bonuses`.

Next: **Phase 5 — The Equipment block**.

### 2026-08-06 — Phase 5: The Equipment block

**Shipped.** Gear is on the sheet. `ui/sections/equipment.py` (the block),
`ui/sections/equipment_picker.py` (the catalog), one more row in the block registry, and
the enhancement-column fix carried since Phase 2. Suite: **2074 passed** (was 2043);
`test_powers_section` and `test_power_constructor` pass **unedited**.

*The block.* Budget bar (a `QProgressBar` + a `18 / 25 EP` readout, accent under budget
and `tint.worse` + `⚠` over it), an "Add Equipment" button opening the picker,
auto-grouped cards, and the block title left as a plain `"Equipment"` — `set_priced_title`
is specifically the *Power Point* subtotal and gear spends none, so duplicating the bar
in the title would have been the wrong currency in the wrong place.

*The card is the powers card.* `effects_block(item.build, char, data)` draws the whole
per-effect table, so an item and a power show the same breakdown from the same code —
which is the Phase 3/4 shape paying off. What the section adds is the `⚠` PL marker,
the `⌂` homerule badge (three ways in: a Dev-mode override or custom modifier on the
build, an `ep_override`, **or** `item.stacks` — the no-stacking opt-out is a homerule by
construction), the EP price, and the "superseded by" line.

*Five decisions worth keeping:*
- **Two mime types, and that is the whole cross-board safety.** `EQUIPMENT_MIME` for
  item cards, `EQUIPMENT_GROUP_MIME` for group cards, neither being the powers block's
  `NODE_MIME`. That is exactly the seam Phase 4 built the `mime=` keyword for. A group
  card and its members therefore share one drop stack without a drop ever having to
  guess which kind it was carrying.
- **`NodeList` grew two keyword seams**, both defaulting to today's powers behaviour:
  `combinable=False` drops the combine half of the vocabulary (a drop is always a
  reorder — equipment groups *itself*, a drag may not invent one), and `accepts` is the
  per-group admission rule. A refusal shows: the list owns a `DropFeedback` scoped to
  `#nodeList` with `WA_StyledBackground` set, since a plain `QWidget` paints no
  stylesheet background. `_clear_hints` deliberately does **not** clear the reject wash —
  the two are put up in sequence (hints down, then dress) and clearing one must not undo
  the other.
- **The flat model list is laid out group by group.** `Character.equipment` is flat and
  the groups are derived from `category`, so `_reflow` rewrites the list in group order
  after every move. That is what makes a within-group reorder a plain splice *and* what
  persists an arrangement with no new field. `equipment_group_order` keeps categories the
  character currently owns nothing in, on the end, so emptying and refilling a group puts
  it back where its owner left it.
- **Every item is a switch**, unlike a power (which gets one only if it has something to
  gate). Wearing is a fact about the character whether or not the item grants a bonus, so
  every card is clickable, dims the same eased way, and stays clickable in the **locked**
  read-only sheet — it emits `runtimeChanged`, never `changed`.
- **`item_superseded` is in `core/rules/equipment.py`, not the widget.** It gathers the
  item's own contributions through the same `build_contributions` the sheet uses, then
  looks them up in the resolved sheet-wide bonuses and returns
  `SupersededItemBonus(stat, category, amount, beaten_by)`. A stowed item supersedes
  nothing — it is already dimmed, which is the honest explanation there.

*The carried-forward fix, done:* `AbilitiesSection`/`ResistancesSection` now read
`derived.trait_bonuses(...).get(category, {})` instead of `power_trait_bonuses`, so worn
gear appears in the enhancement column rather than silently raising the total. Two tests
hold it. `power_trait_bonuses` is untouched and still the powers-only view.

*Tests that legitimately changed* (none of them a powers test): the four block-set
enumerations — `test_ui_wiring.test_sheet_exposes_all_blocks`, `test_block_registry`'s
`BASE_KEYS`/`EXPECTED_DEFAULT_ROWS`, `test_block_sizes.SHEET_BLOCKS`, and the hardcoded
arrangement in `test_block_canvas` — all of which spell the block set out and so must
name a twelfth block. `conftest`'s `_instant_power_card_transitions` now zeroes
`EquipmentSection.TRANSITION_MS` too.

*Deliberately not done — Phase 6 owns it:* **no dice footer on a weapon card**, no
`rollRequested`/`pinRequested`, and so no `_ROLLS` in the registry row. The plan assigns
rolling, the equipment pin kind, vehicle speed into `SystemInfoSection`, and equipment
contributions reaching `power_pl_violations` to that phase together. Also deferred: the
✎ edit button (Phase 7 owns the constructor's gear mode, and opening today's PP-labelled
constructor on an item would misprice it on screen), and therefore any way to re-rank a
stock item after adding it — the picker asks for the rank once, for the six non-`fixed`
entries, via a `QInputDialog` with no upper bound (how high a rank may go is a PL
question and the card's `⚠` already answers it).

*Also:* `driver.py` gained an `equipment-demo` target (two categories, one stowed item,
one outclassed piece of armour) — the surface Phases 6–10 will keep screenshotting.

*Verified:* `tests/test_equipment_section.py` (new, 31 tests — grouping and its three
order rules, both drag axes, cross-group refusal down to `DropFeedback.state`, the mime
split, the wear switch and its runtime/locked contracts, the budget bar in both states,
currency separation, the superseded annotation four ways, the enhancement columns, and
the picker) plus the **unedited** `test_powers_section`, `test_power_constructor`,
`test_powers`, `test_derived_stats`, `test_stat_appliers`, `test_equipment`,
`test_equipment_data`, `test_conditions`. Full suite **2074 passed**; `ruff` and `black`
clean. Eyes on the real app under both `classic` (dark, over budget) and
`parchment-light` (under budget).

Next: **Phase 6 — Rolling and derived wiring**.

### 2026-08-06 — Phase 6: Rolling and derived wiring

**Shipped.** Gear rolls, pins, and moves the character. A weapon card now carries the
Powers block's own dice footer, `PIN_EQUIPMENT` puts one of an item's rolls on a GM card,
worn gear reaches the Speed readout, and a rifle counts toward an NPC's estimated Power
Level. Suite: **2098 passed** (was 2074); no existing test was edited beyond one import
and one two-line helper inlining in `test_roll_routing.py`.

*The dice footer is the powers footer.* `EquipmentSection._rolls_block` builds a
`RollsFooter` from `power_rolls(item.build, …)` — the same specs, the same widget, so a
sword and a Damage power roll identically: the attack line is a bordered click target end
to end, the resistance line is written down but unbuttoned, and the save reaches whoever
makes it as the follow-up chip on the attack's history card. An item that rolls nothing
gets neither footer nor rule. The section gained `rollRequested`/`pinRequested`/
`unpinRequested` and the `set_pin_target`/`set_pinned` pair the sheet duck-types over, and
its registry row gained `_ROLLS` — that one word *is* the wiring, since no block names
another.

*Four decisions worth keeping:*
- **A stowed weapon keeps its footer.** The card is dimmed because a stowed item grants
  nothing, but drawing a sheathed sword is one motion and a roll is not a build fact.
  Refusing the die there would only teach people to click the card twice first.
- **`PIN_EQUIPMENT` is its own kind, not a `PIN_POWER` at the item's build.** A ref's
  `key` has to name something the resolver can *find*: gear lives in
  `Character.equipment`, which `leaf_powers` does not walk, and an item's build carries a
  different id that nothing indexes by. That last point is why `_from_build_roll` takes
  `key` as a parameter rather than reading `build.id` — writing the build's id into the
  resolved ref would have produced a chip that could never be resolved again. There is a
  test for exactly that (`test_a_pin_names_the_item_not_its_build`).
- **A pin resolves off *every* item, worn or not.** Wearing is runtime state a GM flips
  constantly and which is not even persisted; a chip that emptied to a dash the moment a
  sword was sheathed would be the strip rearranging itself mid-fight. The picker likewise
  offers only gear that actually *rolls* — a catalogue listing every crowbar would bury
  the two entries a GM came for.
- **`_power_chip_label` became `_build_chip_label`** and `_from_power`'s whole tail became
  `_from_build_roll`, shared by both kinds. Only *where the build was found* differs.

*Speed.* `movement.equipment_speed_lines` is the new seam, and `speed_lines` appends it —
so `condition_speed_lines`, `SystemInfoSection` and everything downstream picked gear up
without a line of UI change. Two things about it: a gear line is named by the **item**
(`"Glider 6"`, not `"Flight 6"`) because that is what the thing *is* and two worn items
granting the same effect would otherwise read identically, whereas a power's own title is
arbitrary and `Flight` is the mechanic — so the powers labels are untouched; and the base
ground line stays **first**, which is what lets the condition overlay keep landing on
`lines[0]`. `_build_speed_lines` is the shared per-build walk, the movement twin of
`build_contributions`.

`movement_mode_lines` was extended over worn gear the same way (`_build_mode_lines`),
beyond the phase's brief: showing gear-granted Flight while hiding gear-granted Swinging
is a distinction the sheet cannot justify. It is **inert against the shipped catalog**
today — `build_item_from_entry` does not populate an `allocation` config, so the swing
line/climbing cable/parachute build an Enhanced Movement with no modes chosen. The path is
tested with a hand-configured item; **Phase 7 or 8 should map an entry's
`implementation` keys onto allocation options** and it will light up.

*Power Level.* Two halves, and only one was missing:
- **Armour Toughness against the paired cap was already correct** as of Phase 3 —
  `power_level_violations` reads `resistance_total`, into which equipment contributions
  already flow. That is now stated in its docstring and held by a test rather than being
  true by accident.
- **`estimated_power_level` did not walk gear**, so a mook whose whole threat was the
  rifle it carried estimated at 0. New `validation.offensive_builds(char)` yields the leaf
  powers *and* every item's build, and the attack-cap loop reads it. **Every** item, not
  only the worn ones: a PL cap is a statement about the build, and a sheet that passed
  validation by sheathing its sword would be validating nothing.

*Deliberately not done:*
- **No late-bound equipment default.** `SELECT_FIRST_DAMAGE` still walks powers only, so
  an NPC whose damage comes from a rifle starts with a dashed chip. Extending it means
  deciding what the resolved ref writes back (a `PIN_POWER` select resolving to an
  equipment key would be unresolvable later), which is a bigger change than the hole
  warrants. A `select="first_weapon"` on `PIN_EQUIPMENT` is the shape if it is wanted.
- The GM card's hover summary (`ui/card_summary.py`) still lists powers only.
- Vehicle speed specifically is **Phase 9** — this phase built the seam it will arrive
  through, and a stock vehicle's speed will reach the readout via `equipment_speed_lines`
  once `stockVehicles` is parsed.
- Docs remain Phase 11's (`CLAUDE.md` still describes neither the Equipment block nor
  its roll routing).

*Verified:* 17 new cases in `tests/test_equipment.py` (the roll shape and its follow-up,
five pin cases including the item-id-not-build-id trap, the speed lines in both wear
states, the movement modes, and four PL cases) and 8 in `tests/test_equipment_section.py`
(the footer's rollable/inert split, no footer on armour, the stowed card keeping one, a
click that rolls without editing or toggling, the pin ref, the no-card guard, and the
wear-to-Speed round trip over the bus), plus one end-to-end routing case in
`tests/test_roll_routing.py`. Full suite **2098 passed**; `ruff` and `black` clean. Eyes
on the real app via `driver.py equipment-demo` — the sword's footer, the stowed
crossbow's dimmed one, and armour with none.

Next: **Phase 7 — Custom items via the constructor**.

### 2026-08-07 — Phase 7: Custom items via the constructor

**Shipped.** Gear is built and edited in the Power Constructor. A **gear mode** on
`PowerConstructorWindow` (`gear=True` for a blank item, `item=` to edit one), a
`CostOverrideTarget` seam on `PowerTermsView`, "Create Custom Item" and a card's `✎` on
the block. Suite: **2126 passed** (was 2098); `test_powers_section`,
`test_power_constructor` and `test_powers` pass **unedited**, and no existing test was
touched at all.

*One builder, not two.* An item wraps a real `Power`, so the palette, the canvas, the
game-term table and every Dev-mode override work on gear untouched — `self.power` simply
*is* `self.item.build`. Four things differ, and each follows from equipment being a
different kind of thing bought in a different currency: the currency, a group combo, the
no-stacking opt-out, and the name rule.

*Five decisions worth keeping:*
- **A hand-set price is stored on the item, in Equipment Points.** The Dev-mode cost row
  now writes through a `CostOverrideTarget` (`unit`/`read`/`write`/`derived`) instead of
  straight at `Power.cost_override`, and gear points it at `EquipmentItem.ep_override`.
  This is the "Two currencies" risk in the flesh: a price typed as EP and stored on the
  power would read back as PP to every function that asks a power what it costs, and
  *nothing would raise*. A test asserts both fields after an override — the EP one set,
  the PP one still `None`.
- **Every cost in the window agrees on the currency.** `_currency` is one property the
  total, the override spin **and each effect card's formula** read; the card's formula
  was the one that got away in the first pass, reading "= 3 PP" directly under "Total
  cost: 3 EP". `EffectCard`/`PowerCanvas` took a `unit="PP"` keyword for it, defaulting
  to today's behaviour.
- **An item is asked for a name where a power is asked for an effect.** Gear with no
  effects is ordinary — the five accessories that only modify a host weapon have none,
  and they still cost points — but an unnamed item is a card reading "Equipment" that
  nothing tells from the next. That is also why the Dev-mode table now renders its cost
  row with **no effects present** (the read-only table still shows only its placeholder):
  an accessory priced by hand is exactly a build with nothing in it.
- **The group combo seeds itself in `_build_gear_row`, not in `_seed_from_power`.** The
  build panel is constructed first, so seeding it later let the combo's opening row write
  `close_weapon` over an edited item's real category on the way past. Found by the test
  that opens a suit of armour and asks what the combo says.
- **`_after_change` normalises the flat list into group order.** `Character.equipment` is
  flat with groups derived from `category`, and an edit can now change an item's category
  outright — which would leave a group's items scattered through the model, the one thing
  `_on_item_moved`'s splice assumes is never true. The drag handlers already reflowed;
  this makes it true after every change.

*Also:* an edit is a deep copy replaced **by id** (an editor closed unsaved is a no-op, and
an item removed while its editor was open is re-added, matching `PowersSection`); locking
the sheet closes any open builder as well as the picker, following the picker's Phase 5
precedent; `driver.py` gained an `equipment-constructor` target.

*Deliberately not done:*
- **The budget is not checked in the constructor.** The block owns the budget bar, and an
  item is not on the character while it is being built — "would this overspend" needs a
  simulation the phase does not need. Overspending warns on the block, as designed.
- **`implementation` → allocation options is still open** (flagged in Phase 6: gear-granted
  Enhanced Movement modes are inert against the shipped catalog because
  `build_item_from_entry` populates no `allocation` config). It is a *catalog-building*
  job rather than a constructor one, so it belongs with Phase 8's reading of
  `implementation` for `attachesTo`.
- No accessory attachment (Phase 8 owns `attachesTo`), no re-rank UI beyond editing the
  build in the constructor, and docs remain Phase 11's.

*Verified:* `tests/test_equipment_constructor.py` (new, 30 tests — the two titles and
save-button texts, the EP total against a stock item's printed price, both currencies
staying put, the group combo in all three seeding cases, the stacks box, five cases on the
hand-set price including the PP-field-stays-empty trap, the name rule in both directions,
`itemSaved`/`powerSaved` never crossing, and the block's create/edit/abandon/reprice/
regroup/locked paths) plus the **unedited** `test_power_constructor`, `test_powers_section`,
`test_equipment_section`, `test_powers`, `test_equipment`. Full suite **2126 passed**;
`ruff` and `black` clean. Eyes on the real app: the gear constructor on a catalog sword and
the block's two-button row.

Next: **Phase 8 — Strength-based weapons and accessories**.

### 2026-08-08 — Phase 8: Strength-based weapons and accessories

**Shipped.** The §4 divisor, the weapon-Toughness warning, and the accessory layer.
Suite: **2156 passed** (was 2126). `ruff` and `black` clean.

**The §4 divisor is now the engine's only answer to "how much ability arrives".** Two
new functions in `core/rules/powers_cost.py` — `effect_per_rank_cost` (base + per-rank
extras − per-rank flaws; flat modifiers excluded, which *is* carve-out 2) and
`ability_rank_contribution(ability, per_rank)` (pass-through at ≤ 1 PP/rank, which *is*
carve-out 1; floor division above it). Both `effect_rank_trait_bonus` (play) and
`effect_rank_trait_bonus_cost` (cost) route through the same one, and that pairing is
the decision worth keeping: the design doc's engineNote says the divisor is applied
*first* and the reduced number feeds both the DC and the per-rank Extra pricing, so
charging extras against ranks a divisor threw away would be paying for nothing.

*This moved numbers, and it had to.* **Two tests in `tests/test_powers.py` were edited**
— the only existing tests touched anywhere in the phase. Both asserted a Ranged
Strength-Based Damage folding in the whole of a Strength 4 (`14` PP); at 2 points per
rank the divisor lands `floor(4/2) = 2`, so it is `12`. They kept their intent (folding
happens; cost is stable across the wielder's current Strength) and only their numbers
moved. Nothing else in the suite noticed, because every other Strength-Based build in
the tests and the whole shipped catalog is at 1 PP/rank — the shipped catalog's 43
Strength-Based items are all plain close weapons, so **no stock item's price changed.**

**Breakage is data, not a table in Python.** `equipment.json` gained
`_meta.strengthBasedDamage.breakage.materialToughness` (`wood`/`leather` 4, `metal` 7,
from the printed "roughly 4 for wood, 7–8 for metal") parsed into
`EquipmentRules.material_toughness`/`breakage_rule`, and 28 weapons gained
`implementation.material`. `item_material_toughness` reads one against the other;
`item_breakage_warnings` compares it with the ability *exerted* — the wielder's own,
**before** the divisor, since halving what arrives does not halve what is going through
the haft. It warns and never clamps, so `effect_effective_rank` is untouched. The same
material tags and table were mirrored back into `docs/design-data/equipment-design.json`
so the reference stays the superset the promotion model assumes.

**An accessory is modifiers looking for a host.** `EquipmentItem` gained three fields —
`accessories` (what is fitted to *this*), and on an accessory `attaches_to` and
`attachment`. All three serialize only when non-empty, so an ordinary item's JSON is
byte-for-byte what it was. Five decisions worth keeping:

- **The merge is derived, never stored.** `item_effective_build(item, data)` returns the
  host's build with each fitted accessory's modifiers added to every effect, and returns
  the host's own build object unchanged when nothing is fitted. Because the stored build
  is never rewritten, the host stays `item_is_stock` (so it keeps its printed price) and
  detaching is lossless. Every reader takes that build: the card's terms table, its PL
  markers, its dice footer, and `pins.py` (both the resolver and `available_pins`) — so a
  pinned rifle attack reads the +2 its laser sight lends it.
- **Pricing takes `item.build`, never the effective one.** `item_own_ep_cost` is the old
  `item_ep_cost` renamed; the new `item_ep_cost` is *that plus every fitted accessory's*.
  Both halves are load-bearing: an attached accessory is off `Character.equipment`, so
  folding it in is the only way `equipment_points_spent` counts it at all — and pricing
  the merged build instead would charge for the same Accurate twice.
- **Being an accessory is having somewhere to attach**, not sitting in the `accessory`
  category — that group is only where the cards are filed. `item_attaches_to` /
  `item_attachment` read the item first and fall back to its catalog entry, which is what
  lets gear saved before this phase attach at all.
- **Advantages an item grants are drawn on its card and never join the Advantages
  block** (`item_granted_advantages`, `GrantedAdvantage(name, source)`). That is the
  printed rule (`_meta.weaponTraits.advantageScope`) and it lit up `grants`, which was
  parsed but read by nothing until now — the axe finally says "Grants Improved Smash". An
  accessory's grant is drawn on the *host* but named after the accessory.
- **Fitting is a menu (`🔗` on a loose accessory's header), not a drag.** A drag would
  have to cross two groups, and a cross-group drop is the one gesture this block refuses
  everywhere else. `_remove_item` now also reaches into hosts, since a fitted accessory
  is not in the flat list for the plain filter to find.

*Deliberately not done:*
- **No accessory editing in the constructor.** `attachment` is populated at pick time and
  there is no UI for it, so a *custom* accessory can be priced but lends nothing. The
  gear constructor is the place for it and it belongs with whatever phase next opens that
  window.
- **`stun_ammo`'s `damageType: nonlethal` and `suppressor`'s `detectDC` are still inert** —
  they carry no modifier, so fitting them costs the point and changes no number. Both need
  a descriptor/quality channel the terms layer does not have yet.
- **The still-open `implementation` → allocation-options job from Phases 6/7 is still
  open.** Phase 8 read `implementation` for `attachesTo` and `material` but not for the
  Enhanced Movement modes; it is a catalog-building job and now has no obvious owner
  before Phase 11.
- Contributions (`equipment_contributions`) still gather off `item.build`, not the
  effective one: accessories carry attack modifiers, never trait boosts, so there is
  nothing there to gather.

*Verified:* `tests/test_equipment.py` (+15: the accessory model, the fit/host rules, the
lend, the identity return, paid-exactly-once, lossless detach, the save round-trip, the
byte-for-byte serialization of an ordinary item, scoped grants both ways, and five on
breakage including warns-never-clamps), `tests/test_powers.py` (+5 on the divisor: the
cheap effect, the compound bow, both carve-outs, and the arithmetic stated once),
`tests/test_equipment_section.py` (+8 on the card and the block's fit/detach/remove
seams). `test_equipment_constructor`, `test_powers_section` and `test_power_constructor`
pass **unedited**.

Next: **Phase 9 — Stock vehicles**.

### 2026-08-08 — Phase 9: Stock vehicles

**Shipped.** All 34 stock vehicles are pickable, priced, worn, rolled and drawn.
Suite: **2184 passed** (was 2156). `ruff` and `black` clean.

**The decision the whole phase turns on: a vehicle enters the rules layer as an
ordinary catalog entry.** `GameData.equipment_catalog()` now merges `equipment` with a
derived `vehicle_entries`, one per stock vehicle, built by the new
`data_loader.vehicle_entry()`. Nothing downstream needed a vehicle branch: the picker
lists them under Vehicles because it iterates the catalog, `build_item_from_entry`
gives them a build, `item_is_stock` keeps them at their printed price, `worn_items`
gates them, `power_rolls` rolls their guns and `equipment_speed_lines` puts their Speed
on the sheet. The alternative — a parallel vehicle path beside `EquipmentItem` — would
have needed a twin of every one of those. Items win an id collision, since the item
catalog is the older and larger one.

*The translation is of shape, not of rules.* `vehicle_entry` writes a platform's printed
line in the catalog's spelling: its **movement** becomes an effect (its own `movement`
block if it has one, else the effect its **class** measures Speed in — `_meta.vehicleClasses`
is the new axis, `air → flight`, `water → swimming`, so a jet's Speed 9 reaches the sheet
as Flight rather than as a bare number), and each **weapon** becomes an effect carrying
its own modifiers. The five platform traits are deliberately *not* folded in: they are
not effects, they are bought off their own table, and `core/rules/vehicles.py` reads them
straight off the `StockVehicle`.

**Two small, general additions to the powers layer paid for that**, both defaulting to
today's behaviour and both written only when non-empty:
- `PowerEffectInstance.label` — what to call *this* effect. `power_rolls` prefixes with
  it whether or not the power is multi-effect, so a tank's footer says "Cannon:" and
  "Heavy machine gun:" rather than "Damage:" twice. No power in the base ruleset sets
  one, so no power's footer moved.
- `EquipmentEffectRef.modifiers` — extras and flaws carried by *one* effect, on top of
  the entry-wide list every effect gets. The cannon is Area 6 and the machine gun is
  Multiattack; neither belongs to the tank. `_entry_modifier_selections` split into a
  reusable `_modifier_selections(refs, data)` for it.

**The trait-cost table is real, and it checks out against the book.** `vehicle_trait_cost`
is §5's table — 1 per size rank, 1 per +1 of Strength/Toughness *above the size baseline*,
1 per −1 of Defense bought off. Speed is excluded on purpose: it is a movement effect and
is priced as one through the build. That split is what makes the halves add up — a jumbo
jet's 14 points of platform plus Flight 9 at 2 a rank is its printed 32, exactly. It is
consulted only when there is no printed price to honour (`item_own_ep_cost` adds
`item_platform_cost` to the *derived* branch), which is what finally prices the two
`built` vehicles: the dimension hopper's own line reads "6 + movement effect cost" and it
now comes out at 6 + 6 = 12.

*Also in `core/rules/vehicles.py`:* `vehicle_size_row` (the table, extended arithmetically
past rank 5 by `_meta.vehicleSizeExtension` — +2 STR / +1 TOU / −1 DEF a rank — and clamped
to the first row below it), `vehicle_defense_class` / `vehicle_stationary_dc` (the one
dynamic defence in the app: a tank at Speed 6 is DC 14, parked it is DC 8), and
`vehicle_trait_rows`, which returns `EffectStat` rows so the card renders them through the
*same* `build_terms_grid` an effect's game terms use — a vehicle card differs from an
item's in what it says, not in how it is laid out. A trait beating its size baseline is
tinted and carries the baseline on its tooltip, which is precisely what the build paid for.

**The card.** A platform shows the trait grid **instead of** the effects block (a
game-term table for Speed 6 restates the grid's own Speed row, and one per weapon buries
the traits the card exists to show) but **keeps** its dice footer, its description, its
price and its ✕. Its click hint reads "board or park" rather than "wear or stow" — the same
switch, honest wording.

**The data promotion** put the design file's vehicle mechanics into
`src/mm_companion/data/equipment.json`: `patterns`, `defenses[]`, `weapons[]`, `movement`,
`costFormula`, `variants`, `notes`. Weapons were **normalised on the way in** — the design
file's sibling `areaRank`/`homingRank` keys became a `rank` on the modifier they belong to,
which is the shape `EquipmentModifierRef` already understood. New `_meta` keys:
`vehicleCategory` (which equipment category platforms file under — named in data, not in
Python), `vehicleClasses` (+ its note), `vehicleSizeExtension`, `vehicleCombat`. The two
engine-facing axes were mirrored back into `docs/design-data/equipment-design.json`, which
the promotion model assumes stays the superset.

*Three data judgements worth keeping:*
- **The time machine gets no movement effect.** The design file names Enhanced Movement
  mode `time_travel`, and `effects.json` has no such mode. Asserting one would be inventing
  content, so the vehicle carries the fact in its `notes` instead. Its Speed row reads a
  dash, which is the honest answer for a plot device.
- **The dimension hopper's mode was resolved**, since `dimensional_travel` does exist:
  `config.modes = [{"id": "dimensional_travel", "tier": 3}]` at rank 6, reading the design
  file's `"rank": 3` as the tier index (tier 3 costs 6 ranks, so it allocates exactly).
  That is the first bite taken out of the still-open `implementation` → allocation job.
- **Stock vehicles carry no Features.** `vehicleFeatures` and `vehicleModifiers` parse and
  are exposed (`PlatformFeature`, `VehicleModifier`, `GameData.vehicle_feature_catalog()`)
  but nothing in the shipped catalog selects any — they are the custom builder's, in
  Phase 10.

**`vehicle_modifier_advantage_cost` is written but wired to nothing, on purpose.** Durable /
Minion / Summonable change what the Equipment advantage ranks *funding* a vehicle cost in
**Power Points**, not what the vehicle costs in Equipment Points — the "two currencies" trap
in its most confusing form. The rule lives beside the records that describe it; choosing
modifiers for a vehicle needs the custom-platform builder, which is Phase 10's. They are
deliberately kept out of `modifier_catalog()` for the same reason, and a test holds that.

*Two existing tests were edited*, both asserting a premise this phase changes on purpose:
`test_equipment_catalog_is_an_id_lookup` (the catalog is items **plus** vehicles) and
`test_the_picker_lists_the_whole_catalog_grouped_and_priced` (so is the picker). Every
powers test — `test_powers`, `test_powers_section`, `test_power_constructor`,
`test_equipment_constructor` — passes **unedited**.

*Deliberately not done:*
- **No custom vehicles and no installations** — Phase 10 owns both, along with the vehicle
  trait-cost *builder* (this phase only reads the table, it does not offer to buy off it),
  moving Defense as a live per-round number, and the installations' own PL branch.
- **`installationFeatures` / `stockInstallations` / `installationSizeTable` are still
  unparsed**, as are the §5 crash, control-check and vehicle-damage-ladder rules
  (`vehicleCrash`, `vehicleControl`, `vehicleDamage` in the design file's `_meta`).
- **A vehicle's weapons are subject to `power_pl_violations` like any other gear.** No
  stock vehicle trips it at PL 10, and warning is the right default when a PC does mount
  something over their cap, but a GM-adjudicated vehicle-PL exception is not modelled.
- **A vehicle cannot be re-ranked or reconfigured from the block** beyond editing its build
  in the constructor, where it is drawn as an ordinary power — the trait grid is read-only.

*Verified:* `tests/test_equipment_data.py` (+10: the platform records, referential integrity
over every weapon/defense/movement ref, per-weapon modifier ranks, the class axis, the
derived entries, the exotic pair, the size table and its extension, the two side catalogs,
and two mod tests proving `_deep_merge` extends and overrides platforms for free),
`tests/test_equipment.py` (+13: the trait table against two vehicles, traits-plus-movement
equalling the printed price, stock pricing, the `built` vehicle, the size extension both
ways, the moving/parked DCs, Speed reaching the readout and a parked vehicle not,
named weapon rolls, per-weapon modifiers, the trait rows, the advantage-currency modifier,
and the PP pool staying untouched), `tests/test_equipment_section.py` (+4 on the card, the
footer, the group and the wording). `driver.py` gained an `equipment-vehicles` target; eyes
on the real app confirm the trait grid, the named dice footer, and "Tank 6" and
"Jumbo Jet 9" both on the System block's Speed readout.

Next: **Phase 10 — Custom vehicles and installations**.

### 2026-08-08 — Phase 10: Custom vehicles and installations

**Shipped.** Both kinds of platform can now be *built* as well as picked: a vehicle off
the §5 trait table, an installation off the §6 one, plus the nine printed installations,
the 36 installation Features, the installations' own PL cap pair, and a throttle that
makes a vehicle's moving Defense Class a live number. Suite: **2224 passed** (was 2184).
`ruff` and `black` clean.

**The decision the phase turns on: stock and custom are one shape.** A new
`core.equipment.PlatformSpec` holds a platform's bought traits, and
`rules.platforms.item_platform(item, data)` resolves it — the item's own spec if it has
one, otherwise the printed record *normalised into the same spec*
(`PlatformSpec.from_vehicle` / `.from_installation`). So there is no custom-platform
branch anywhere: one cost function, one trait grid, one card, one picker. The rejected
alternative was a `CustomVehicle` record beside `StockVehicle`, which would have needed
a twin of every reader and would have drifted the first time one of them changed.

*Carrying a spec is what makes a platform custom*, and `platform_is_stock` is the second
half of `item_is_stock` — without it a jet whose Toughness the player raised would keep
the book's printed price and the points would simply be free. A spec that happens to
**equal** the printed one is still stock, so a no-op pass through the editor moves no
price (the same contract a no-op pass through the constructor has).

**`core/rules/vehicles.py` became `core/rules/platforms.py`** (a `git mv`, every public
name kept, one line changed in `rules/__init__`). Installations are the second platform
and far too small for a module of their own; what they share — resolution, Features,
pricing, the movement effect — is most of the file.

**Where a platform's Speed lives, and why it is spelled twice.** The trait is on the
spec; the **movement is a real effect on the build**, exactly as it is for a printed
vehicle. `apply_platform(item, spec, data)` is the one writer that keeps the two in
step, and that is what makes a hand-built jet's Flight reach the sheet's Speed readout,
cost what Flight costs, and show up in the effect list the constructor edits — with no
platform branch in any of the three. Two subtleties are load-bearing:
- **The movement effect is identified by what it *is*, not by remembering where it was
  put** (`platform_movement_index`: the first effect in the build that one of the
  ruleset's vehicle classes measures Speed in, or the one the spec names). The first
  version diffed the old spec against the new, which is wrong the moment the caller
  passes the item's *own* spec — the ordinary case, since the editor edits the live one.
  A test (`…speed_is_a_real_effect_on_its_build`) holds it.
- **Re-ranking edits the effect rather than replacing it**, so the dimension hopper's
  Enhanced Movement mode survives a trip through the editor.

**The editor** (`ui/platform_editor.py`, a modal dialog on a deep copy, swapped in on
accept like the constructor's). Three things about it are the rules showing through:
- **Size is chosen first**, so it is the top box and the three traits it sets *carry*
  with it — each moves by the amount its baseline moved, so a jet upgraded from size 3
  to 4 keeps its bought Strength and does **not** quietly acquire three points of
  bought-off Defense. Minimums move too, so the floor holds against a typed number. A
  platform *opened* below its baseline (the printed sailboat's Toughness is one under)
  is left exactly as found — correcting it on open would re-price a boat someone opened
  to add a Feature to.
- **It computes no price.** It applies the spec to a working item on every keystroke and
  asks `item_ep_cost`, which is the only way Speed's cost appears at all and the reason
  the dialog and the card can never disagree.
- **The two currencies stay apart, visibly.** Durable / Minion / Summonable get their own
  line, in Power Points, *beside* the EP price — `vehicle_modifier_advantage_cost` was
  written in Phase 9 and wired to nothing; this is what it was waiting for.

**The block.** A third button, **Create Platform**, opens a menu of the kinds the ruleset
declares (titled off their category headings, so a renamed ruleset renames the menu). A
platform card's ✎ opens a menu rather than an editor, because a platform is two editable
things — Traits… (this dialog) and Effects… (the constructor, where its weapons live).
The card's ⚠ now covers `item_platform_violations` as well as `power_pl_violations`: the
effect-shaped check has nothing to say about a fortress made of Toughness.

**The throttle** is the phase's one piece of live state: `EquipmentItem.current_speed`,
runtime like `worn` and deliberately unserialized. A tank at Speed 6 is DC 14 and the
same tank crawling is DC 10 (§5 Combat), and this is the only number in the app that
changes *within* a round. It restates the trait grid **in place** (`_trait_hosts` /
`_refresh_traits`) rather than through `_rebuild_list`, because the rebuild every other
change uses would destroy the spin box under the pointer that asked for it.

**Installations.** `InstallationSizeRow` / `StockInstallation` / `InstallationRules`
parse; `installation_entry()` derives catalog entries the same way `vehicle_entry()`
does, so all nine are pickable, priced, worn and drawn with no new plumbing. Their card
is a much shorter grid (Size, Toughness, one Features row — a moon-base has fifteen and
one row each would bury the two traits the card exists to show), and their click hint is
a third honest wording: you *wear* a jacket, *board* a car and *open* a base.
- Cost: `installation_size_cost` is the §6 line through the free rank (a room refunds 4,
  a small town costs 6, and a rank past the table extrapolates along the same line),
  plus one point per +2 Toughness **rounded up**, plus a point per Feature. Toughness
  *below* the free 6 refunds nothing — it is a starting point, not a purchase, which is
  why the moon-base's printed Toughness 2 stays a recorded discrepancy.
- **Their own PL cap pair** (`installation_pl_violations`): Toughness to **twice** the
  series PL, Impervious still capped at PL. Both multiples *and which modifier
  Impervious is* are data (`_meta.installationPowerLevel`), so a ruleset can move them.

**Data promoted** into `src/mm_companion/data/equipment.json`: `_meta.installationCategory`,
`_meta.installationTraits` (the free rank/Toughness and the per-point step, with the
printed trait-table discrepancy recorded), `_meta.installationPowerLevel` (the two
multiples plus `imperviousModifier`), and each stock installation's `patterns` and
`notes`. The engine-facing keys were mirrored back into
`docs/design-data/equipment-design.json`, which the promotion model assumes stays the
superset. One new theme token, `platform.features.height`, in `classic.json` (+
`token_meta.json`) — every other preset inherits it through the documented
default-preset fallback.

*Two existing tests were edited*, both asserting a premise this phase changes on purpose:
`test_equipment_catalog_is_an_id_lookup` (the catalog is items + vehicles **+
installations**) and the two assertions in the editor test that assumed the old lift
behaviour. Every powers test — `test_powers`, `test_powers_section`,
`test_power_constructor`, `test_equipment_constructor` — passes **unedited**.

*Deliberately not done:*
- **A custom vehicle cannot be given Impervious Toughness from the editor.** The spec
  carries `defenses` (a printed tank's Impervious 4 normalises into it and draws on the
  grid) but only the installation editor offers the control, because that is where the
  cap rule needs it. The §5 table prices no defensive modifier either way.
- **An edited stock platform re-prices to its derived cost, which can go *down*.** A
  tank edited to Toughness 14 comes out at 70 rather than 76: the printed 76 bundles
  things the §5 table does not price (its Impervious 4). That is the documented
  stock-vs-derived contract rather than a bug, but it is the surprising face of it.
- **`vehicle_modifier_advantage_cost` still changes no total.** The editor *shows* the
  Power Point number; nothing adds it to the Equipment advantage's cost, because the
  advantage's own cost lives in the Advantages block and a per-vehicle allocation of
  ranks is not modelled.
- **The §5 crash, control-check and vehicle-damage-ladder rules** (`vehicleCrash`,
  `vehicleControl`, `vehicleDamage` in the design file's `_meta`) are still unparsed, as
  are the installation Features' `implementation` blocks (Concealed's escalating DC,
  Holding Cells' prerequisite chain, Combat Simulator's PL-bounded effect). They are
  parsed as names and prices; what each *does* is prose on its tooltip.
- **A platform is still not a nested character.** No trait-by-trait sub-sheet, no
  crew/passenger tracking, no per-vehicle allocation of Equipment advantage ranks.

*Verified:* `tests/test_equipment_data.py` (+7: the installation records, referential
integrity over every Feature reference, the two separate Feature catalogs, the size
table and the free starting points, the PL pair as data, installation entries as
ordinary catalog entries, and two mod tests proving `_deep_merge` extends and overrides
installations for free), `tests/test_equipment.py` (+19: the one-shape normalisation, a
new platform at its free baselines, the traits-plus-movement split, the movement effect
reaching the Speed readout and surviving a re-rank, editing a stock platform off its
printed price, Feature pricing, the spec round-trip, the throttle, stock installations
as gear, the free house, the size refunds, the rounded-up Toughness, the full
installation price, no movement effect on a base, the cap pair both ways, the dispatch,
and the PP pool staying untouched), `tests/test_equipment_section.py` (+9: the
installation card, the third wording, the throttle's presence and its in-place restate,
the editor's price, the size carry, the untouched sailboat, the two currencies on
screen, the cap warning, Features bought in the editor, and the platform card's two
editors). `driver.py` gained an `equipment-platforms` target; eyes on the real app
confirm the moon-base's short grid, the tank's throttle moving its Defense Class, and a
hand-built Skyhawk whose Flight 9 is on the System block's Speed readout beside the
tank's.

Next: **Phase 11 — Docs, mod hooks, polish**.

### 2026-08-08 — Phase 11: Docs, mod hooks, polish

**Shipped.** The last phase, and the only one that adds no rules: the equipment layer is
now written down where the next person will look for it, and the one seam it added is a
documented, exercised mod hook. Suite: **2226 passed** (was 2224). `ruff` and `black`
clean.

**`docs/mm-equipment-architecture.md`** — the counterpart to `mm-powers-architecture.md`,
twelve sections: the wrapping model and the two shapes rejected for it, the two currencies
and the Removable discount that is never reapplied, the catalog as data, how an entry
becomes a build, the applier/resolver split, what `worn` gates and the three things that
deliberately ignore it, accessories, platforms, a table of every place equipment reaches
the rest of the sheet, the UI, the mod seams, and what is out of scope. It records the
*whys* that are cheapest to lose: why `item_own_ep_cost` prices `item.build` and not the
effective build, why editing a stock item can make it cheaper, why Speed is spelled twice
on a platform, why a cap ignores `worn` while a bonus does not.

**`CLAUDE.md`** gained "The equipment layer" between the powers and session sections,
plus three corrections the earlier phases left behind: the sheet is **twelve** blocks
(`EquipmentSection` was missing from the list and from the default arrangement), the
opening paragraph now says equipment exists, and the data-flow bullet names
`equipment.json`.

**The mod hook.** `STAT_APPLIERS` is now in `docs/modding.md`'s registry table with its
own paragraph, and the two rules a handler has to honour are stated there because both
are silent when broken: **pass `stacking`/`group` through untouched** (they are the
granter's terms — they are what makes one record stack on a power and not stack on a
piece of gear), and **decline with `()`** rather than raising, so a character built on a
disabled mod's effect still loads.

**`docs/sample-mods/field-kit`** is the third sample and the first equipment one:
`effects.json` adds an Ablative Weave whose `statIntegration.apply` names a kind the base
engine does not know, `equipment.json` adds an Ablative Vest to the **stock** `armor`
category built out of it, and `field_kit_mod.py` registers `partial_bonus` (half the
amount, rounded up). The split is the point of the sample — *what the vest is* is data
merged by id; *what "partial" means* is the only thing that needed code — and the README
says which line to edit for each of the three ways to change it, including how to drop
the Python entirely and fall back to the shipped `bonus` kind.

Two tests in `tests/test_mod_loading.py` (10 → 12), and the second is the one worth
having: enabled-but-**untrusted**, the vest still merges, still prices at 3 EP and is
still worn — it simply grants nothing, because an unregistered apply kind yields no
contributions. That is the promise that a mod losing trust does not make a saved
character unloadable.

**Theme tokens: nothing to add.** Audited `ui/sections/equipment.py`,
`equipment_picker.py`, `platform_editor.py` and `ui/cards/` for a hardcoded colour,
radius, padding or point size — there are none; every one goes through
`theme.color`/`metric`/`font_size`, and the single token the block introduced
(`platform.features.height`) landed with Phase 10.

*No source under `src/` changed in this phase* — deliberately. Everything here is docs,
a sample mod and tests, so there was nothing for the existing suites to disagree with.

*Deliberately not done:*
- **`docs/modding-tutorial.md` is untouched.** It builds `guardian-kit` step by step
  around a readout kind; bolting a second mechanic onto the same walk-through would make
  a beginner's tutorial into a survey. `field-kit` is a *reference* sample with its own
  README, the shape `flat-bonus-readouts` already had.
- **No `mm-equipment-ui-design.md`.** The powers layer has one because its constructor is
  a screen-by-screen design; the Equipment block's UI is a catalog picker and the powers
  cards, and §10 of the architecture doc covers what is genuinely its own.
- **`EQUIPMENT_PLAN.md` is still here.** It is deleted as part of the merge into
  `develop`, which happens when the user says the feature is done — not at the end of a
  phase.

Next: **nothing — every phase is `done`.** The feature is ready for the user's call on
merging `feature/equipment` into `develop` (`--no-ff`, deleting this file as part of it).

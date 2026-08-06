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
**Status: todo**

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
**Status: todo**

Weapon cards get their dice footer from `power_rolls(item.build, char, data)` — attack
line, the forced save written down but unbuttoned (`rolled_by_target`), the follow-up
chip on the history card. `rules/pins.py` gains an equipment pin kind so a GM can pin a
weapon's attack to an NPC card. `SystemInfoSection`'s Speed readout folds in a new
`movement.equipment_speed_lines`. Equipment contributions reach `power_pl_violations`
so armour Toughness counts against the paired cap.

*Verify:* `tests/test_roll_specs.py`, `tests/test_roll_routing.py`, `tests/test_gm_pins.py`,
`tests/test_derived_stats.py`.

### Phase 7 — Custom items via the constructor
**Status: todo**

`PowerConstructorWindow` gains a gear mode: the cost readout reads EP, the title says
Equipment, and the "stacks with other bonuses" checkbox lives here. Game-term overrides
and the dev-mode edits work exactly as they do for powers. Add "Create Custom Item" for
off-catalog gear, and make an edit replace the item in place on a deep copy — the same
contract `PowersSection._on_power_edited` already honours.

*Verify:* `tests/test_power_constructor.py`, `tests/test_equipment_section.py`.

### Phase 8 — Strength-based weapons and accessories
**Status: todo**

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
**Status: todo**

Parse `stockVehicles`, `vehicleFeatures`, `vehicleModifiers` and `vehicleSizeTable`. A
`vehicle` category whose card shows Size / STR / TOU / DEF / Speed rather than the
standard effect table, and whose speed rank reaches the System block's readout via
Phase 6's seam. Note `vehicleModifiers` (Durable / Minion / Summonable) modify the cost
of the **Equipment advantage ranks** allocated to the vehicle, not the vehicle's EP cost —
a separate field, not a folded-in modifier.

*Verify:* `tests/test_equipment_data.py`, `tests/test_equipment_section.py`.

### Phase 10 — Custom vehicles and installations
**Status: todo**

The vehicle trait-cost builder (Size chosen first, since it sets the STR/TOU/DEF
baselines; ranks past 5 extend arithmetically), moving Defense rank
(`current speed rank + size defense modifier`), and the installation builder (Size from
the free rank 5, Toughness at 1 EP per +2, Features at 1 EP each). Installations get
their own PL branch: Toughness may reach **twice** the series PL while Impervious stays
capped at PL — a different cap pair from characters.

*Verify:* `tests/test_equipment.py`, `tests/test_equipment_section.py`.

### Phase 11 — Docs, mod hooks, polish
**Status: todo**

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

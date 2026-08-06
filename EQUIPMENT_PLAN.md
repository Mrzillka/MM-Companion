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
| `src/mm_companion/data/equipment.json` | The **shipped** catalog: 106 items, 34 stock vehicles, 11 vehicle features, 3 vehicle modifiers, 9 stock installations, 36 installation features, and the two size tables. Currently the *thin* label form (`id`/`name`/`category`/`subcategory`/`cost`/`costKind`/`description`). |
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
**Status: todo**

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
**Status: todo**

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
**Status: todo**

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
**Status: todo**

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

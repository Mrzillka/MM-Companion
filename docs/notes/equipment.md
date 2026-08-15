# The equipment layer

Matters when touching equipment, gear or vehicles.

Working notes for MM-Companion, split out of [CLAUDE.md](../../CLAUDE.md).

Equipment is the powers layer used a second way, not a parallel one. The full map is
`docs/mm-equipment-architecture.md`, the rules themselves are
`docs/mm-equipment-design.md`; the shape:

- **An item wraps a power.** `core.equipment.EquipmentItem` holds a real
  `core.powers.Power` in its `build`, plus `catalog_id`, `category`, `platform`,
  `accessories`, `worn`, `stacks` and `ep_override`. So `power_total_cost`,
  `effect_stat_rows`, `power_rolls`, `effect_is_active` and `power_pl_violations` all
  take `item.build` **unchanged** — a rifle is priced, drawn, rolled and validated by
  the code a Blast power is, which is why the equipment work moved no existing sheet's
  numbers. `Character.equipment` is its own list beside `Character.powers`, because
  equipment is *not* a power: a character built entirely from gear legitimately answers
  "no powers".
- **Two currencies, and they never mix.** Power Points buy ranks of the Equipment
  advantage; each rank grants `points_per_advantage_rank` (5) Equipment Points, and
  those buy the items (`equipment_budget` / `equipment_points_spent` /
  `equipment_points_remaining` in `core/rules/equipment.py`). An item's price is never a
  term in `power_points_spent` and never the reverse. Nothing raises when this breaks —
  the build total is just wrong — so both totals are asserted in
  `tests/test_equipment.py`.
- **The Removable discount is never reapplied.** The book prices gear at what its
  effects would cost *undiscounted*, precisely because the advantage already granted the
  points. `build_item_from_entry` never attaches a removable-gated flaw, and
  `item_own_ep_cost` strips one (`_undiscounted`) before pricing a build carrying one
  anyway. If the cost engine sees `removable` on an item, something has double-counted —
  and it is silent, every item simply being cheap.
- **A price has three answers, in order** (`item_own_ep_cost`): an `ep_override`; the
  catalog's **printed** price while the item is still stock (`item_is_stock` compares a
  signature of the build against what the entry would produce); otherwise the derived
  cost plus, for a platform, its bought traits. So **editing a stock item re-prices it,
  and it can go down** — the printed number sometimes bundles what the generic table
  does not price. `item_ep_cost` adds everything fitted to it and is what the budget
  counts.
- **Stat effects are data** (`core/rules/appliers.py`, the layer below `runtime`). A
  stat effect is a record (the `TraitBoost` parsed from `statIntegration`) plus an
  `apply` **kind** naming what it means; each kind is a registered `StatApplier` handed
  an `ApplyContext` and yielding `TraitContribution`s. Five ship — `bonus`, `speed`,
  `sense`, `penalty_removed`, `penalty_replaced` — with `register_stat_applier` the mod
  hook (the third instance of the `PATTERN_BEHAVIOURS` / `GATE_KINDS` pattern). The
  amount is `flat + rank × per_rank`, all data, defaulting to M&M's rule that the bonus
  *is* the rank, so an effect declaring none of it behaves exactly as it always did. An
  **unregistered** kind yields nothing rather than raising. `speed` and
  `penalty_removed` shipped for a long time with no producer *and* no consumer; both
  have one now (Elongation's Striding, Shrinking's Normal Speed — see [Size and
  movement](size-and-movement.md)).
- **A modifier can carry a `statIntegration` too**, not only a base effect: an extra
  whose own text promises a number ("longer strides grant ranks of Speed") is a stat
  effect that happens to hang off another effect, read by the same appliers.
  `build_contributions` therefore walks each active effect's extras and flaws, and that
  walk is deliberately **not** gated on the base effect having a boost — Elongation has
  none, so gating on it is exactly how Striding would go on granting nothing. A modifier
  is worth its own rank when `ranked` and the **host effect's** otherwise, which is what
  a per-rank price already says it is charging for; the host's gates take the grant with
  them for free. Striding is `ranked` — "+1 point per rank flat", capped at 5 — so a
  Striding 2 on an Elongation 6 grants two ranks of ground movement and not six. That
  cap is `maxRank` on the record, asked through `rules.modifier_rank_cap` (1 unranked,
  `MODIFIER_RANK_MAX` uncapped) so the constructor's spin box and any later validation
  cannot disagree about it.
- **A contribution carries an `origin`** — the granting item's id — beside its `source`
  name, and `item_superseded` matches on it. Two copies of one armour share a name and an
  amount, so matching by those had *both* cards claiming to have lost while the bonus
  actually on the sheet was disowned by both. **Powers pass none**, deliberately: a
  power's card explains itself from its own build, and `tests/test_stat_appliers.py`
  asserts whole-dataclass equality on a power's contribution.
- **Gear does not stack, and that is a resolver, not an applier.**
  `build_contributions(power, char, data, stacking=, group=)` gathers a power *and* an
  item — one function, so the two can't drift — and the two keywords are the
  **granter's** terms travelling onto every contribution (a power `STACK_SUM` in
  `GROUP_POWERS`, a worn item `STACK_MAX` in `GROUP_EQUIPMENT`).
  `resolve_contributions` then nets one trait under two different rules: **within** a
  group the `sum` contributions add and the largest `max` one joins them; **between**
  groups the larger total wins outright, never the sum of the two maxima. Powers keep
  summing among themselves, which is why no existing number moved;
  `tests/test_derived_stats.py` is the guard and passes unedited. Whatever lost is
  reported in `TraitBonus.superseded` and `item_superseded`, so an outclassed item's
  card says *what* beat it — a silently inert bonus reads as a bug. The per-item
  `stacks` checkbox opts one item out and badges it homerule.
- **A third group does not compete at all.** `GROUP_INTRINSIC` (in
  `ALWAYS_ADDED_GROUPS`) is what the creature *is* rather than what it bought — its
  size — and `resolve_contributions` **adds** it on top of whichever bought group won,
  never weighing the two. Weighing them would let a suit of armour delete a Colossal
  creature's Toughness, and letting size win would delete the armour. It never
  supersedes and is never superseded. Two edge cases the resolver had to learn, and
  both are easy to re-break: the intrinsic contributions must come **out of `groups`
  before** `max(resolved, …)` picks a winner (or an intrinsic-only trait wins and is
  then added twice) and `groups` can then be **empty** — a large character with no
  powers and no gear — which the old `beaten_by = max(groups[winner], …)` raised on.
  Intrinsic amounts are also legitimately **negative** (a small creature's Stealth
  bonus is a large one's penalty), so they are forced `STACK_SUM`: an exclusive
  comparison over signed amounts means nothing here.
- **`worn` is runtime**, and now the *only* kind that is not saved: left out of
  `to_dict()`, a loaded character comes up wearing everything, and a card toggle emits
  `runtimeChanged` (not `changed`), so it works in the locked sheet and never marks it
  dirty. It was modelled on a power's `activated`, which has since gone the other way —
  what is in your hands this round is not what your powers are set to. Three
  things deliberately ignore it: the **price** (sheathing a sword refunds nothing), **PL
  validation** (`offensive_builds` yields every item — a sheet that passed by sheathing
  its sword would validate nothing), and a **GM's pinned chips** (a strip must not
  rearrange itself mid-fight).
- **Accessories live on their host**, and their whole layer is `core/rules/accessories.py`
  — **below** both `rules/equipment.py` and `rules/runtime.py`, because pricing reaches
  `derived` and `derived` reads what runtime gathers, so the two cannot import each other
  and both need the merged build. A scope is a trait of the rifle, not of the character,
  so an attached accessory sits in `EquipmentItem.accessories` and leaves the loose list.
  `item_attaches_to` says where it fits, `attachment` is what it lends (both editable in
  the constructor's gear row; `ui/attachment_dialog.py` is the modifier checklist), and
  `item_effective_build` merges them **on demand** — the stored build is never rewritten,
  so detaching is lossless. It also carries the accessory's **own effects** across,
  labelled with the accessory's name, and `equipment_contributions` reads that effective
  build so a fitted trait boost is granted at all — the `origin` stays the *host's* id,
  since the host's card is what has to explain it. The catalog fallback on both fields
  needs the item to carry **neither**, which is what "saved before they existed" means:
  an empty `attachment` on an item that names somewhere to attach is a decision, not a
  gap. Its price folds into the host's, which is the only way the budget counts it at
  all; note `item_own_ep_cost` prices `item.build` and not the effective build, or the
  accessory's modifiers would be charged twice.
- **Platforms are bought as traits, not effects** (`core/rules/platforms.py`): a vehicle
  is five (Size first — it sets three baselines), an installation is two plus Features.
  `PlatformSpec` is the shape, and **stock and custom are one shape** — `item_platform`
  resolves the item's own spec or normalises the printed record into the same one, so
  there is no custom-platform branch. Carrying a spec is what makes a platform custom
  (`platform_is_stock`); a spec equal to the printed one is still stock. **Speed is
  spelled twice on purpose**: the trait on the spec, the movement as a *real effect on
  the build*, kept in step by the single writer `apply_platform` — which is what puts a
  hand-built jet's Flight on the System block's Speed readout at a Flight's price.
  `current_speed` is runtime and the one number that changes *within* a round (a moving
  vehicle's Defense Class). `vehicle_modifier_advantage_cost` is deliberately a **Power
  Point** number and is shown, never totalled.
- **UI.** `ui/cards/` is the card machinery extracted out of the Powers block and shared
  by both — the draggable card and its eased off-progress, the terms grid, the node
  list, the dice footer — knowing nothing about *what* is drawn; each drag payload's
  MIME format is a keyword argument, so two boards can't accept each other's drags.
  `ui/sections/equipment.py` is the block (budget bar; Add Equipment / Create Custom
  Item / Create Platform; auto-grouped cards whose group is the item's `category`, a
  rules fact — so a cross-group drop is refused *visibly* with
  `DropFeedback.show_reject()`, since a bare `ignore()` reads like a target that didn't
  notice). The catalog is `ui/sections/equipment_picker.py`, modeless for the reason
  `pin_picker` is. Custom gear reuses the **Power Constructor in gear mode** (an item's
  build *is* a power, so there was never a second builder), and a platform's ✎ opens a
  menu — Traits… (`ui/platform_editor.py`) or Effects… (the constructor) — because a
  platform is two editable things. The editor computes no price: it applies the spec to
  a working item and asks `item_ep_cost`, so it and the card can never disagree.
- Budget breaches **warn, never block** (`equipment_violations`, a red bar and a ⚠).
  `core.storage.equipment_enforcement()` is the one seam that could change that, beside
  `pl_enforcement()` — read it through the accessor, never off `load_settings()`.

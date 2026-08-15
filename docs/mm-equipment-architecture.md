# Mutants & Masterminds (4th Edition) — Equipment Architecture

> **Note on source & copyright:** Same basis as the other reference files — an original,
> paraphrased summary of the *functional* rules (cost formulas, stacking behaviour,
> trait tables), not a reproduction of the rulebook's text. This file is the
> architecture guide: how the app models gear, and why it is shaped that way. Its
> companions are `mm-equipment-design.md` (the rules themselves — §2 patterns, §3
> no-stacking, §4 the Strength-Based divisor, §5 vehicles, §6 installations, §7 schema),
> `equipment.json` (the shipped catalog), and `mm-powers-architecture.md`, which this
> layer is built on top of and does not restate.

---

## 1. The one idea: an item **wraps** a power

An `EquipmentItem` is not a parallel model of a power. It holds one:

```
EquipmentItem
 ├─ catalog_id      "rifle"          which EquipmentEntry it was picked from ("" if custom)
 ├─ build: Power    Damage 5, Ranged, Multiattack …
 ├─ category        "ranged_weapon"  the group the block files it under
 ├─ platform        a PlatformSpec, for a vehicle or an installation (§8)
 ├─ accessories[]   items fitted to this one (§7)
 ├─ worn            runtime — is it on the character right now
 ├─ stacks          build — the per-item opt-out of the no-stacking rule (§5)
 └─ ep_override     homerule — replaces the derived price
```

Everything in the powers rules layer therefore works on `item.build` **unchanged**:
`power_total_cost`, `effect_stat_rows`, `power_rolls`, `effect_is_active`,
`power_pl_violations`. That is the whole point of the shape, and it is why adding
equipment moved no existing number on any sheet — a rifle is priced, drawn, rolled and
validated by the code a Blast power is.

Two alternatives were rejected. An `equipment` facet on `Power` itself would put fields
most powers never use on every power. A fully parallel model would need an equipment
twin of every rules function, and the twins would drift the first time one of them
changed.

**Equipment is still not a power.** `Character.equipment` is its own list beside
`Character.powers`, because the distinction is a rules fact (`mm-equipment-design.md`
§1): a character built entirely from gear legitimately answers "no powers", which
matters for what Nullify can target and for the sheet's own Power Point accounting.

---

## 2. Two currencies, and the discount that is never reapplied

```
Power Points  ──buy──>  ranks of the Equipment advantage
                            └──grant──>  5 Equipment Points per rank
                                             └──buy──>  the items
```

- `equipment_budget(char, data)` — the advantage rank × `points_per_advantage_rank`
  (both data, from `equipment.json`'s `_meta`).
- `equipment_points_spent(char, data)` — the sum of `item_ep_cost` over every item,
  worn or not. Taking a jacket off does not refund it: `worn` is runtime state and the
  price is build state.
- `equipment_points_remaining` is the difference, and may go negative.

Neither total is ever a term in the other. An item's price never reaches
`power_points_spent`, and an advantage rank never pays for gear. This is easy to break
silently — nothing raises, the build total is just wrong — so both totals are asserted
in `tests/test_equipment.py`.

### The Removable discount

**An equipment item never pays the Removable flaw's discount.** The book prices gear at
what the same effects would cost *undiscounted*, precisely because the Equipment
advantage already handed out 5 points per rank; charging the flaw again would make every
item quietly cheap. Enforced in two places, because it is the most likely silent rules
bug in the layer:

- `build_item_from_entry` never attaches a `removable`-gated flaw when it turns a
  catalog entry into a build;
- `item_own_ep_cost` strips one (`_undiscounted`) before pricing a build that carries
  one anyway — a hand-built item, or one edited in the constructor.

### What one item costs

`item_own_ep_cost` answers in three steps, in order:

1. `ep_override` when set — the homerule seam, the twin of a power's `cost_override`.
2. The catalog's **printed** price, while the item is still stock (`item_is_stock`
   compares a signature of the build against what the entry would produce). For the
   `per_rank`/`ranked` cost kinds the printed number is a price *per rank*.
3. Otherwise the build's derived cost — `power_total_cost` minus any Removable
   discount — plus, for a platform, its bought traits (§8).

`item_ep_cost` is that plus everything fitted to it (§7), and is what the budget counts.

A consequence worth knowing before it surprises you: **editing a stock item re-prices it
to its derived cost, which can go down.** The printed price sometimes bundles things the
generic cost table does not price. That is the stock-vs-derived contract, not a bug; a
no-op pass through the constructor changes nothing, because the signature still matches.

---

## 3. The catalog is data

`src/mm_companion/data/equipment.json` holds the shipped catalog — items, stock
vehicles, vehicle Features and modifiers, stock installations and their Features, and
the two size tables — parsed into frozen records by `core/data_loader.py`
(`EquipmentEntry`, `EquipmentCategory`, `EquipmentRules`, `StockVehicle`,
`PlatformFeature`, `StockInstallation`, the size rows). `GameData.equipment_catalog()`
is the `id -> entry` lookup, and it includes the platforms: a stock vehicle and a stock
installation each derive an ordinary catalog entry (`vehicle_entry`,
`installation_entry`), so all of them are pickable, priced and drawn with no special
plumbing.

An entry carries its own mechanics — `effects`, `modifiers`, `grants`, `critical`,
`patterns`, and an open `implementation` bag — and `build_item_from_entry` assembles
them into a `Power`. The `implementation` bag is deliberately untyped: it is the
per-item detail the engine grows into over time (ranges, charges, attachment hosts, ammo
modes), and retaining it whole is what lets a later phase read a field without another
data migration.

`_meta` holds the constants: the currency's name and abbreviation, the advantage that
grants it, the points per rank, the display categories (`equipmentCategories` — a
category is a rules fact, but its *title and order* are presentation, so a mod can add a
row instead of having its items folded into someone else's group), the stacking rule and
the stats it governs, the cost-kind vocabulary, and the material breakage table.

The richer `docs/design-data/equipment-design.json` is the **reference superset** and is
not shipped. Mechanics are *promoted* out of it into the catalog as the engine grows to
read each field — the same convention `advantages-design.json` → `advantages.json`
follows.

---

## 4. How a catalog entry becomes a build

`build_item_from_entry(entry, data, rank=…)`:

- resolves each `effects[]` reference to a base effect and instantiates it at its rank,
  seeding the config an entry names (an Affliction's resisted trait, an Enhanced Senses
  sense list, a Strength-Based weapon's ability-folding checkbox);
- attaches each `modifiers[]` reference as an extra or a flaw — **except** a
  removable-gated one (§2);
- copies `category` and `attaches_to` onto the item, so a card can be grouped and an
  accessory can find its hosts without a catalog lookup, and so an item whose entry a
  disabled mod took away still has a home.

There is no `if`/`elif` chain over item names anywhere in `core/`. Everything an item
does is either a reference into `effects.json`/`modifiers.json` or a field the engine
reads generically.

---

## 5. The stat layer: appliers, and the rule that gear does not stack

This is the part of the equipment work that changed the powers layer, and the part most
worth understanding.

### Appliers — what a stat effect is *worth*

`core/rules/appliers.py` splits a stat effect into two halves: a **data record** (the
`TraitBoost` parsed out of an effect's `statIntegration`) and an **apply kind** naming
what that record means. Each kind is a registered `StatApplier`: given an `ApplyContext`
— the record, the rank it stands at, the resolved target, who is granting it — it yields
`TraitContribution`s.

Before the split, "the bonus is the effect's rank, added to the target" was hardcoded
inside `power_trait_bonuses`. Now it is one registered handler among five:

| `apply` | Means |
| --- | --- |
| `bonus` | add the amount to a numeric trait (Enhanced Trait, Protection) — the historical behaviour, unchanged |
| `speed` | grant ranks of a named movement mode |
| `sense` | grant ranks of a named sense quality |
| `penalty_removed` | cancel that much of a penalty standing on a trait |
| `penalty_replaced` | replace a penalty with a smaller one |

The amount is `flat + rank × per_rank`, all three data (`amountFlat`, `amountPerRank`),
defaulting to M&M's own rule — the bonus *is* the rank — so an effect stating none of
them behaves exactly as it did before this existed. `register_stat_applier` is the mod
hook, the third instance of the `PATTERN_BEHAVIOURS` / `GATE_KINDS` pattern; an
**unregistered** kind yields nothing rather than raising, so a character built on a
disabled mod's effect still loads.

### Contributions, and who gathers them

`build_contributions(power, char, data, stacking=…, group=…)` walks one build and runs
each active effect's applier. **The same function gathers a power and an item** — an
item's `build` is a real `Power` — which is exactly what stops the two drifting. What
differs is the two keywords, which are the *granter's* terms and travel onto every
contribution:

- a power contributes `STACK_SUM` in `GROUP_POWERS`;
- a worn item contributes `STACK_MAX` in `GROUP_EQUIPMENT`, unless its `stacks` flag is
  ticked.

### The resolver

`resolve_contributions` nets everything standing on **one** trait into a `TraitBonus`,
under two rules that are not the same rule:

- **Within a group**, the `sum` contributions add up and the largest `max` one joins
  them. Several powers stack, as they always have; several pieces of armour do not.
- **Between groups**, the larger total wins outright — never the sum of the two maxima.
  M&M grants the better of gear and power, not both. Ties go to the powers group.

Everything that did not count is reported in `TraitBonus.superseded`, named against
whichever source won. That is not decoration: an outclassed item is still on the sheet,
and a bonus that is silently inert reads as a bug, so `item_superseded` feeds the card a
line saying *what* beat it. The per-item `stacks` checkbox is the homerule opt-out —
that item's bonus adds on top of the winner instead of competing with it, and its card
is badged the way a custom modifier is.

Because powers keep summing among themselves, **no existing sheet's numbers moved** when
this landed. `tests/test_derived_stats.py` is the guard and passes unedited.

---

## 6. Runtime: worn, and what that gates

`worn` is runtime state — taking a jacket off is a play-time action, not a build edit. So
it is deliberately left out of `EquipmentItem.to_dict()`, a loaded character comes up
wearing everything, and toggling a card emits `runtimeChanged` rather than `changed` (so
it works in the locked read-only sheet and never marks it dirty).

It is *no longer* like a power's `activated`, which it was modelled on: a power's own
runtime — switched on, held at a size rung, which array member is live — **is** saved
with the build now, because a Growth held at Large is a standing decision about the
character rather than what is in its hands this round (see `core/powers.py`). `worn` and
`current_speed` are what `capture_runtime`/`apply_runtime` still carry across a restore
by hand; an item's *build* rides in the snapshot with every other power.

`worn_items(char)` is the gate. **Every** worn item applies at once — there is no
array-style exclusivity here, so nothing switches off because something else switched
on. What gear has instead is the no-stacking rule above.

Three things deliberately ignore `worn`, and each for its own reason:

- **The price.** `equipment_points_spent` counts every item. Sheathing a sword does not
  refund it.
- **Power Level.** `offensive_builds(char, data)` yields every item's *effective*
  build — with whatever is fitted to it folded in, so a laser sight's Accurate counts
  toward the rifle's cap the way the card's own ⚠ already read it. A cap is a statement
  about the build; a sheet that passed validation by sheathing its sword would be
  validating nothing.
- **A GM's pinned chips.** `resolve_pin` reads an item's rolls whether it is worn or
  not, so a card's strip does not rearrange itself mid-fight.

---

## 7. Accessories: gear that goes on other gear

A laser sight is not a thing the character has; it is a thing the *rifle* has
(`mm-equipment-design.md` §2 pattern I). So an attached accessory lives in the host's
`accessories` list rather than in `Character.equipment`, which is what keeps its scope
honest — a loose card in its own group could only imply the Improved Aim was the
character's.

All of it lives in **`core/rules/accessories.py`**, which sits *below* both
`rules/equipment.py` (which prices things) and `rules/runtime.py` (which decides what is
live). It has to: pricing reaches `powers_cost` and so `derived`, and `derived` reads
what runtime gathers, so the two cannot import each other — and both need the merged
build. Nothing in the module prices anything or decides what is on.

- **Being an accessory is having somewhere to attach.** `attaches_to` is the host
  categories it fits, chosen in the constructor's gear row or copied off the entry at
  pick time. A mod's accessory therefore needs no category of its own.
- **What it lends** is `attachment`, a list of modifier selections — a catalog accessory
  has no effects of its own, so its Accurate or Subtle would otherwise have nothing to
  hang on.
- **The catalog fallback needs *neither* field.** `item_attaches_to` and
  `item_attachment` read the entry only for an item carrying no `attaches_to` and no
  `attachment`, which is exactly what "saved before the fields existed" means. An item
  that names somewhere to attach has been through the accessory row, so an empty
  `attachment` there is the player saying *lends nothing* — reading the printed
  modifiers back over it would ignore a decision.
- **Merging is derived, never stored.** `item_effective_build(item, data)` returns the
  host's build with the accessories' modifiers folded in, computed on demand. The stored
  build is never rewritten, so detaching is lossless and the host stays recognisably the
  catalog's.
- **An accessory's own effects come along too**, each labelled with the accessory's name
  so the host's terms table and dice footer say which part of the weapon is doing it. No
  catalog accessory has any — a laser sight is modifiers and nothing else — but a custom
  one may, and its effects are already inside the host's price, so leaving them out
  charged for something that appeared nowhere and did nothing. The lent modifiers are
  deliberately *not* applied to them: what an accessory lends is lent to the host.
- **Contributions read the effective build.** `equipment_contributions` walks
  `worn_items`, and a fitted accessory is not among them, so an accessory carrying a
  trait boost would otherwise grant nothing. The contribution's `origin` stays the
  *host's* id — the host's card is what has to explain the bonus, and a fitted accessory
  has no card of its own to put it on.
- **`removable` is dropped on the way in**, by the same `_split_by_category` that builds
  an entry's modifiers. Enforced on the build path only, an accessory lending a
  removable flaw would have pushed one onto every effect of its host: no cost (the
  merged build is never priced) but a host that switches off whenever it is not
  "present".
- **The price folds into the host's.** `item_ep_cost` recurses; `item_own_ep_cost` is the
  item's own line. That is also the only way the budget can count an accessory at all,
  since it is off the loose list. And `item_own_ep_cost` prices `item.build` rather than
  the effective build — the accessory's printed price already paid for its modifiers.

---

## 8. Platforms: the gear bought as traits rather than effects

Two kinds of gear are not a bundle of effects. A **vehicle** is five traits (Size,
Strength, Speed, Defense, Toughness) off the §5 table, Size chosen first because it sets
the baselines for three of the others; an **installation** is two (Size, Toughness) plus
a list of Features, starting from a free house (§6). `core/rules/platforms.py` is all of
it.

**Stock and custom are one shape.** `PlatformSpec` holds a platform's bought traits, and
`item_platform(item, data)` resolves it: the item's own spec if it has one, otherwise the
printed record *normalised into the same spec* (`PlatformSpec.from_vehicle`,
`.from_installation`). So there is no custom-platform branch anywhere — one cost
function, one trait grid, one card, one picker. **Carrying a spec is what makes a
platform custom** (`platform_is_stock`), and a spec that happens to equal the printed one
is still stock, so a no-op pass through the editor moves no price.

**Speed is spelled twice, on purpose.** The trait is on the spec; the movement is a
**real effect on the build**, exactly as it is for a printed vehicle. `apply_platform` is
the one writer that keeps the two in step, and that is what makes a hand-built jet's
Flight reach the Speed readout, cost what Flight costs, and appear in the effect list the
constructor edits — with no platform branch in any of the three. The movement effect is
identified by *what it is* (`platform_movement_index`: the first effect one of the
ruleset's vehicle classes measures Speed in), never by remembering where it was put, and
re-ranking edits it in place so a dimension hopper's Enhanced Movement survives the
editor.

Prices split the same way: `platform_trait_cost` prices the traits and the Features, the
build prices the movement and the weapons, and the halves add up to the printed number.
Installations bring their own PL cap pair (`installation_pl_violations` — Toughness to
twice the series PL, Impervious still capped at PL), both multiples and which modifier
Impervious is being data.

`current_speed` is the one number in the app that changes *within* a round: a vehicle's
Defense Class is its **current** speed rank plus its size modifier, so a parked car and a
car doing Speed 7 are very different targets. Runtime, like `worn`, and unserialized.

`vehicle_modifier_advantage_cost` (Durable / Minion / Summonable) computes a **Power
Point** number, not an Equipment Point one — those modifiers genuinely price the
advantage funding the vehicle. The editor shows it beside the EP price; nothing adds it
to a total, because a per-vehicle allocation of advantage ranks is not modelled.

---

## 9. Where equipment reaches the rest of the sheet

| Reaches | Through | Note |
| --- | --- | --- |
| Ability / resistance / skill totals | `trait_bonuses` → the resolver (§5) | worn gear only |
| The Speed readout | `equipment_speed_lines` | a glider flies, a bicycle is faster than walking; worn only |
| Power Level validation | `offensive_builds`, `estimated_power_level` | every item's *effective* build, worn or not |
| The dice | `power_rolls(item_effective_build(...))` | a rifle rolls exactly like an attack power, follow-up save chip and all |
| A GM's pinned chips | `PIN_EQUIPMENT` in `core/rules/pins.py` | its own pin kind, because an item's `key` is its `id` on `Character.equipment` |
| A GM's default chips | `SELECT_FIRST_WEAPON` | late-bound, for the mook whose whole threat is its rifle and who therefore owns no powers for `SELECT_FIRST_DAMAGE` to find |
| A GM card's hover summary | `ui/card_summary.py` | every item, worn or not, as its effective build — the same rule the pinned strip beside it follows |
| Granted advantages | `item_granted_advantages` | an entry's `grants` block |
| Breakage warnings | `item_breakage_warnings` | more Strength than the material's Toughness can carry (§4); a courtesy, not enforcement |

Budget breaches are `equipment_violations` — a red bar and a `⚠`, never a block. Whether
that changes is the single seam `storage.equipment_enforcement()` (`"warn"`/`"block"`),
beside the Power Level one.

---

## 10. The UI

- **`ui/cards/`** is the card machinery, extracted out of the Powers block so both use
  it: the draggable card with its eased on/off progress, the game-terms grid, the node
  list and its drop hints, the dice footer where the whole line is the button. It knows
  nothing about *what* is being drawn — no power tree, no equipment catalog. Every
  widget that reads or writes the drag payload takes its MIME format as a keyword, so
  the two boards cannot accept each other's drags.
- **`ui/sections/equipment.py`** is the block: a budget bar, three buttons (Add
  Equipment, Create Custom Item, Create Platform), and auto-grouped cards. The group is
  the item's `category` — a rules fact — so a drag reorders *within* a group and
  reorders the groups against each other, and a cross-group drop is refused **visibly**
  (`DropFeedback.show_reject()`; a bare `ignore()` reads exactly like a target that did
  not notice). Group order lives on the character (`equipment_group_order`) and falls
  back to the ruleset's.
- **`ui/sections/equipment_picker.py`** is the catalog: modeless, filtered, priced, and
  shaped like `pin_picker.py` so the two "pick something off a list" dialogs read the
  same. Someone kitting a character out is picking four things, and a modal dialog would
  make that four round trips.
- **The Power Constructor in gear mode** covers what the catalog cannot: "Create Custom
  Item" and a card's ✎. An item's build *is* a power, so there was never a second builder
  to write — gear mode adds the EP readout, the "stacks" checkbox, and `itemSaved`
  beside `powerSaved`, plus the **accessory pair** — "Fits onto" (a combo, since every
  shipped accessory fits one host category and the model's tuple still admits more for a
  mod) and "Lends to its host", which opens `ui/attachment_dialog.py`. That is a filtered
  checklist rather than the drag palette, because the palette drops a brick onto an
  *effect card* and an accessory has none; it is offered only once the item fits
  somewhere, since an item that fits nowhere lends nothing to anything. Editing works on
  a deep copy swapped in on accept, so closing without saving is a no-op — and
  `_on_item_edited` looks inside hosts as well as the loose list, or editing a fitted
  accessory would take it off its weapon and leave a second copy loose.
- **`ui/platform_editor.py`** is the modal trait editor a platform's ✎ offers beside
  Effects… — a platform is two editable things. It computes no price of its own: it
  applies the spec to a working item on every keystroke and asks `item_ep_cost`, which is
  why the dialog and the card can never disagree.

---

## 11. Modding seams

| Add | How |
| --- | --- |
| Items, categories, stock vehicles/installations, Features | `equipment.json` in a mod folder — merged by id, so a mod extends the catalog or retunes one printed price without restating a record |
| A new *kind* of stat effect | `register_stat_applier(kind, handler)` from a mod's Python module, and `statIntegration.apply` in its `effects.json` |
| A new integration pattern / gate kind | `PATTERN_BEHAVIOURS`, `GATE_KINDS` — unchanged by this layer |

`docs/sample-mods/field-kit` is the worked example: an Ablative Vest in the stock armor
category, an Ablative Weave effect behind it, and the `partial_bonus` applier that makes
the weave grant half its rank. It is exercised end-to-end by `tests/test_mod_loading.py`,
including the untrusted case — the vest still merges, is still priced and still worn, and
simply grants nothing.

---

## 12. Out of scope for this file

- **A platform is not a nested character.** No trait-by-trait sub-sheet, no
  crew/passenger tracking, no per-vehicle allocation of Equipment advantage ranks.
- **The §5 crash, control-check and vehicle-damage ladders**, and the installation
  Features' `implementation` blocks (Concealed's escalating DC, Holding Cells'
  prerequisite chain), are parsed as names and prices; what each *does* is prose on a
  tooltip.
- **Consumables and charges.** `implementation.consumedOnUse` is retained and unread;
  nothing tracks how many doses of antitoxin are left.
- **Wealth, availability and Ranks of Benefit** — a campaign-economy layer the app does
  not model.
- **Two catalog fields are carried and inert**: `stun_ammo`'s `damageType: nonlethal`
  and `suppressor`'s `detectDC`. Both need a descriptor/quality channel the terms layer
  does not have, so fitting either costs its point and changes no number.
- **Gear the engine cannot price says so rather than guessing.** Trick Arrows keeps its
  array in `implementation.alternates`, whose ranks and DCs the catalog never states, so
  it is free with a `⚠` (`item_price_warnings`) and `ep_override` is how a GM settles
  it. Promoting that block into a real array build is content authoring, not plumbing.
- **The `sense`, `penalty` and `movement` applier categories have no reader.** They are
  registered so a mod or a catalog record can route to them, but `movement.py` still
  derives its own lines and nothing consumes a penalty contribution.

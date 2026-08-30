# Data loading and game content

Matters when touching the data loader or adding a content file.

Working notes for MM-Companion, split out of [CLAUDE.md](../../CLAUDE.md).

- `core/data_loader.py` is the *only* entry point for game content. It parses
  the bundled JSON into frozen dataclasses (`Field`, `Characteristic`,
  `Ability`, `Resistance`, `Skill`, `Advantage`, `Condition` + its mechanical
  sub-records (`ConditionParameter`, `Debilitation`, `DefenseMod`, `AttackMods`,
  `ResistanceMod`, `StackingRule`, `RecoveryCheck`, `RandomActionRow`); the powers
  records `Effect`, `Modifier`, `EffectConfigField` + its option/column helpers,
  `Measure`, `Readout`; the `Measurements`/`SizeRow` conversion tables; and a
  `Costs` record of point costs / PL caps) aggregated in a `GameData` record.
  `GameData.modifier_catalog()` merges the general and effect-specific modifier
  pools into one `id -> Modifier` lookup for cost math and summaries;
  `GameData.condition_catalog()` is the `id -> Condition` lookup the condition
  resolver walks.
  `load_game_data()` is `lru_cache`d — one parse per process, and **every
  `*_catalog()` is memoized on the `GameData` it belongs to** (`_catalog`). They are
  flat merges over tuples that never change — the record is frozen and a mod reload
  builds a *new* one — so rebuilding one per call was pure waste, and not a small
  one: `modifier_catalog()` was rebuilt three hundred times for one step of an
  ability spin box, since every power and equipment card re-derives its terms on a
  redraw and every term looks its modifiers up. That was a third of the cost of the
  whole edit. The returned mappings are now shared, so treat them as read-only.
- Content is aggregated from several files, loaded via `importlib.resources`
  (not filesystem paths) so it works when installed as a package: core traits
  from `profile.json`, `characteristics.json`, `abilities.json`,
  `resistances.json` and `system.json`; the rich 4e catalogs from `skills.json`,
  `advantages.json`, and `conditions.json`; point costs and PL caps from
  `costs.json`; rank → real-world measurement tables, the Size Table, and the
  `sizeEffects` / `sizeRankColumn` mapping saying what each of its columns modifies,
  from `measurements.json`; and the powers layer from `effects.json` (base effects,
  each with a `statIntegration`, a `baseCostMode` saying how its base cost is charged,
  and configurable qualities), `modifiers.json`
  (the general extra/flaw pool + game-term ladders), `effect_modifiers.json`
  (effect-specific extras/flaws, keyed by effect id), and `effect_readouts.json`
  (per-effect derived Tier-5 readouts). The powers rules and UI are documented in
  `docs/mm-powers-architecture.md`, `docs/mm-powers-ui-design.md`, and
  `docs/mm-modifiers-ui-design.md`. The gear catalog — items, stock vehicles and
  installations, their Features, and the two size tables — is `equipment.json`,
  documented in `docs/mm-equipment-architecture.md` and `docs/mm-equipment-design.md`.
- **Two keys decide what a record *costs*, and both are dispatched, not hardcoded.**
  An effect's `baseCostMode` (`"flat"`, the default, or `"as_trait"`) and a modifier's
  `costMode` (`""` or `"as_trait"`) name a handler in `rules.BASE_COST_KINDS`; an
  unregistered mode prices as flat, so a mod's unknown value degrades rather than
  raising. `as_trait` is Enhanced Trait's "the cost of the chosen trait" rule — see
  `docs/notes/powers.md` and `docs/mm-powers-architecture.md` §6. Each file's own
  `_meta` block documents its keys (`effects.json` carries `baseCostModeKey`,
  `traitSourceKey`, `affectsKey`, `applyKey`, `rankFollowsAllocationNote`); **extend the
  `_meta` when you add a key**, since that block is what a mod author reads and it is the
  only place the vocabulary is written down.
- An effect's `rankFollowsAllocation` says its rank *is* what its trait allocation spends
  rather than a budget the allocation is metered against. Only meaningful with a trait
  allocation, and only Enhanced Trait sets it: that effect's cost comes from the traits it
  raises, so its rank has no independent meaning and the constructor shows it read-only.
  Omit it and the rank stays hand-set — which is what Enhanced Senses, Enhanced Movement,
  Comprehend, Immunity and Feature want.
- A `repeatable` config field's rows are shaped by its `columns`, each
  `{ key, label, type }` with type `text`, `int` or `trait`. A `trait` column is a
  trait picker whose `source` names which list it offers (`traits`, `boost_traits` —
  advantages included — or `all_traits`, which adds the derived stats you can roll but
  not buy). A field carrying **both** a `trait` column and an `int` column is a *trait
  allocation*: each row names a trait and the ranks put into it, spent out of the
  effect's own rank. Enhanced Trait and its Reduced Trait flaw are the only base
  records built that way. Column kinds are a registry too
  (`ui.power_constructor.REPEATABLE_CELL_KINDS`), so a mod adds one without editing
  the row builder.
- A `trait` column's stored value may be **qualified** with `::` to narrow it to one row
  of that trait: `Expertise::Law` (a skill focus), `Stealth::spec::Urban` (a specialized
  pool), `Improved Critical::Sword` (an advantage bought for a subject). Those are the
  character sheet's own row ids, not a new format, which is what lets a granted row land
  exactly where a bought one would. The picker composes the key from two controls and
  stores one string, so data and every reader still see a single value — see
  `docs/notes/powers.md`.
- Skills split "which focus?" across two keys. `focuses` is a list of suggested focus
  **names**, offered as ready choices wherever a focus is picked and never a closed list;
  a skill whose focuses cannot be enumerated (Expertise's fields of study, Languages)
  leaves it empty and puts the guidance in `focusNote`, which is shown as a hint. Putting
  prose in `focuses` makes a sentence look like something selectable.
- `specializations`/`specializationNote` split the same way, and for the same reason.
  Any of a skill's specializations may be bought as a narrow half-cost rank pool, so the
  list is offered as **names** by both places that name one — the Skills block's *Add
  specialization…* and the Power Constructor's trait picker, where an Enhanced Trait may
  grant a pool nobody bought. "By specific sense, e.g. sight, hearing, smell" is guidance
  about how to choose, so it sits in the note and is shown as the prompt; it was in the
  list until both dialogs started offering it as a pool anyone could pick.

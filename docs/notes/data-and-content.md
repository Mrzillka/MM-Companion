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
  `load_game_data()` is `lru_cache`d — one parse per process.
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
  `traitSourceKey`, `affectsKey`, `applyKey`); **extend the `_meta` when you add a
  key**, since that block is what a mod author reads and it is the only place the
  vocabulary is written down.
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

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
  each with a `statIntegration` and configurable qualities), `modifiers.json`
  (the general extra/flaw pool + game-term ladders), `effect_modifiers.json`
  (effect-specific extras/flaws, keyed by effect id), and `effect_readouts.json`
  (per-effect derived Tier-5 readouts). The powers rules and UI are documented in
  `docs/mm-powers-architecture.md`, `docs/mm-powers-ui-design.md`, and
  `docs/mm-modifiers-ui-design.md`. The gear catalog — items, stock vehicles and
  installations, their Features, and the two size tables — is `equipment.json`,
  documented in `docs/mm-equipment-architecture.md` and `docs/mm-equipment-design.md`.

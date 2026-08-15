# The mod pipeline

Matters when touching data loading or startup.

Working notes for MM-Companion, split out of [CLAUDE.md](../../CLAUDE.md).

The base ruleset is loaded as **the default mod** through the same pipeline that
loads user mods, so game content is fully data-first and moddable. The full
authoring guide is `docs/modding.md`; the shape:

- **Discovery/order** (`core/mods.py`, pure Python): a `Mod` is a manifest
  (`mod.json`: `id`/`name`/`version`/`priority`/`files`/optional `requires`/
  `description`/`options` + `python_module`) plus how to read its content.
  `base_mod()` is the bundled `data/mod.json`; `discover_workspace_mods()` scans
  the workspace `mods/` dir (malformed manifests skipped, never fatal);
  `active_mods()` returns base first, then enabled workspace mods in the
  **user-defined load order** (the `mod_order` setting — set by dragging in the Mod
  Manager; later applies later and wins). Manifest `priority` only *seeds* where a
  newly-added mod first lands (enabled mods not yet in `mod_order` trail the ordered
  ones by ascending `priority`).
- **Merge loader** (`core/data_loader.py`): `load_game_data()` gathers the active
  mods' content in load order and **deep-merges by record id** (`_deep_merge` —
  a later mod overrides only the fields it supplies and appends new ids; plain
  lists like `options` are replaced wholesale), then parses one `GameData`. Cached
  by the mod stack's fingerprint; invalidate with `clear_game_data_cache()` after
  enabling/disabling a mod.
- **Two mod flavors.** A **data-only** mod is pure JSON (override base files by
  reusing their names, or add a declarative sheet block via `blocks.json`). A
  **data+Python** mod also ships one `python_module` whose import-time
  `register_*` calls extend an engine registry (readout kinds, condition
  mechanisms, stat appliers, config-field types/widgets, sheet blocks — see the
  registry table in `docs/modding.md`).
- **Two settings gates** (`core/storage.DEFAULT_SETTINGS`): `enabled_mods` (ids
  whose *data* layers on) and `trusted_mods` (ids whose *Python* may be imported —
  a separate opt-in because importing runs code). `mods.set_mod_enabled` /
  `set_mod_trusted` are the toggles (disabling revokes trust). Two more settings back
  the manager: `mod_order` (the drag load order) and `mod_options`
  (`{mod_id: {option_id: value}}`, read via `mod_option_values` / written via
  `set_mod_options`). The **Mod Manager window** (`ui/mods_window.py`, opened from the
  launcher's "Manage Mods" and a sheet's `Settings ▸ Mods…`) drives all of these seams
  plus `import_mod_folder` (copy a chosen folder into `mods/`); since mods load once at
  startup, it offers an **app relaunch** on close when something changed.
  `mods.initialize_mods()` (called in `__main__.main()` after
  `ensure_workspace()`, before the first `load_game_data()`) imports the
  enabled+trusted mods' Python modules so their `register_*` hooks fire first; the
  base ruleset is implicitly trusted and an import that raises is swallowed.
- Four living examples ship under `docs/sample-mods/`: `campaign-notes` (data-only),
  `flat-bonus-readouts` (data+Python — a new readout kind) and `field-kit`
  (data+Python — a piece of gear and the stat-applier seam), all three exercised
  end-to-end by `tests/test_mod_loading.py`; plus `guardian-kit`, the finished mod
  built step by step in `docs/modding-tutorial.md`.

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
- **A mod can reach the table, and keep its own memory.** Three seams added with
  protocol v10 (the full story is in [the session notes](session.md)):
  `SessionBridge.set_mod_state` / `mod_state` publish and read a keyed, opaque
  payload the GM authors and every seat sees; `send_mod_request` is the player's
  half, reaching the GM alone; `post_mod_note` writes a line into the shared
  history. All three answer `False` with no session rather than raising, because a
  mod runs whether or not there is a table. Beside them,
  `storage.local_mod_state` / `set_local_mod_state` is a mod's **private** memory —
  one JSON file per mod id under the workspace `mod_state/` dir. Three things that
  are easy to confuse and are not the same: `mod_options` is *configuration the
  user set*, `local_mod_state` is *what the mod itself wrote*, and the session's
  `mod_state` is *the shared copy the table sees*.
- **A mod can add a block to the GM window**, not just the sheet:
  `ui/blocks/gm_registry.register_gm_block`. Deliberately a second registry rather
  than a flag on `BlockDescriptor` — a GM panel is built from the *window* and has
  no character and no signal bus, so half of a sheet descriptor's fields would
  have been dead weight. See [the session notes](session.md) for the layout reset
  that adding one costs.
- **A mod installs from a `.zip`**, not only from a folder:
  `mods.import_mod_archive`, wired to the Mod Manager's "Add from Zip…". It hands
  off to `import_mod_folder` once the archive is safely unpacked, so both routes
  are validated by one piece of code. Every member is checked against zip slip
  first — an archive is downloaded from the internet and opened *before* the user
  has decided whether to trust the mod's Python, which is the least trusted moment
  there is. A wrapping folder is tolerated (it is what every zip tool produces),
  but an archive holding two mods is refused rather than guessed at.
- Four living examples ship under `docs/sample-mods/`: `campaign-notes` (data-only),
  `flat-bonus-readouts` (data+Python — a new readout kind) and `field-kit`
  (data+Python — a piece of gear and the stat-applier seam), all three exercised
  end-to-end by `tests/test_mod_loading.py`; plus `guardian-kit`, the finished mod
  built step by step in `docs/modding-tutorial.md`.

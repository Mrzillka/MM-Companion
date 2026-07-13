# Modding MM-Companion

MM-Companion is a **data-first constructor**: the base *Mutants & Masterminds*
ruleset is itself a mod (the bundled `data/` directory, described by
`data/mod.json`), loaded through the same pipeline as everything else. A **mod**
is a folder that layers on top of the base ruleset — overriding records or adding
new ones — and, optionally, ships a Python module that teaches the engine a new
*mechanic*.

- A **data-only mod** is pure JSON. No code runs. Safe by construction.
- A **data + Python mod** also ships one importable module whose import-time
  `register_*` calls add a new mechanic (a new readout kind, condition mechanism,
  config-field type, sheet block, …). Importing runs code, so it is gated behind
  an explicit **trust** opt-in (see *Safety*).

Two working examples live in [`docs/sample-mods/`](sample-mods): `campaign-notes`
(data-only — adds an advantage and a sheet block) and `flat-bonus-readouts`
(data + Python — registers a new power readout kind).

## Where mods live

Workspace mods go one-per-directory under the workspace `mods/` folder:

| Platform | Workspace root |
| --- | --- |
| Windows | `%APPDATA%\MM-Companion` |
| macOS | `~/Library/Application Support/MM-Companion` |
| Linux | `$XDG_DATA_HOME/MM-Companion` (or `~/.local/share/MM-Companion`) |

Override the root with the `MM_COMPANION_HOME` environment variable (handy for
testing). The `mods/` directory is created on first launch.

A mod directory looks like:

```
mods/
  campaign-notes/
    mod.json          <- the manifest (required)
    advantages.json   <- content files this mod ships
    blocks.json
```

## The manifest (`mod.json`)

```json
{
  "id": "campaign-notes",           // unique id (required)
  "name": "Campaign Notes",          // display name (defaults to id)
  "version": "1.0",                  // free-form version string
  "priority": 10,                    // higher applies later / wins (default 0)
  "requires": ["base"],              // optional: ids this mod depends on
  "files": ["advantages.json"],      // content files this mod ships
  "python_module": "my_module"       // optional: importable module (data+Python mods)
}
```

- **`files`** lists the JSON content files in the mod folder. Only listed files
  are read. Use the same filenames as the base ruleset to *override/extend* that
  content (`advantages.json`, `effects.json`, `conditions.json`, …), or
  `blocks.json` to add a declarative sheet block.
- **`priority`** decides load order. The base ruleset is priority `0` and always
  loads first; enabled mods then apply in ascending priority (higher wins). Ties
  are broken by the order in the `enabled_mods` setting.
- A malformed manifest is **skipped**, not fatal — one bad mod can't stop the app.

## How content merges

Content is **deep-merged by record id**, in load order (base first, then mods):

- A list of records (dicts sharing an `id`/`key`/`name`/… field) merges **by id**:
  a later mod overriding an existing id replaces only the *fields it supplies* and
  keeps the rest; a new id is appended.
- A plain list (e.g. an `options` array of strings) is **replaced wholesale**.
- Nested objects merge key-by-key.

So a mod can retune one number without restating a whole record:

```json
// effects.json — make Damage cost 2 pp/rank instead of 1
{ "effects": [ { "id": "damage", "baseCostValue": 2 } ] }
```

Frozen content records keep an `extra` bucket, so JSON keys the engine doesn't yet
understand are **retained**, not dropped — a Python mod can read them later.

## Data-only: add a sheet block (`blocks.json`)

A mod can add a whole sheet block with **no Python** via `blocks.json`. Each block
becomes a generic *declarative block* — a titled group of field/label rows — and
is registered through the same block registry as the built-in blocks, so it floats,
hides, and rearranges like any other.

```json
{
  "blocks": [
    {
      "id": "campaign_notes",
      "title": "Campaign Notes",
      "row": 8, "col": 0,
      "min_height": 120,
      "fields": [
        { "kind": "label", "text": "Notes for this character." },
        { "kind": "text", "key": "campaign_faction", "label": "Faction" }
      ]
    }
  ]
}
```

- `"kind": "text"` rows are editable and backed by `Character.profile[key]`, so
  they round-trip through save/load with no extra work.
- `"kind": "label"` rows are static text.
- A block id that collides with an existing block is **skipped** (additive only —
  a mod can't clobber a base block).

## Data + Python: register a new mechanic

When a mod needs a genuinely new *mechanic* (not just new data), it ships one
Python module named in `python_module`. On import, the module calls one of the
engine's registry seams. The whole contract is *import-time side effects*.

```python
# flat_bonus_mod.py
from mm_companion.core.rules.powers_terms import READOUT_KINDS, EffectStat

def _flat_bonus(readout, effect, game_data):
    amount = int(readout.data.get("amount", 0))
    return [EffectStat("readout", readout.label or "Bonus", "", f"+{amount}", "")]

# replace=True keeps re-import idempotent
READOUT_KINDS.register("flat_bonus", _flat_bonus, replace=True)
```

Its `effect_readouts.json` then uses the new kind, and any matching power renders
it. The module resolves by its bare name — the mod's folder is put on `sys.path`
before import, so `python_module` is the filename without `.py`.

### Registry seams a Python mod can extend

All reuse the generic `mm_companion.core.registry.Registry` (`register(key,
handler, replace=False)`), so extending them is the same call everywhere:

| Registry | Where | Extends |
| --- | --- | --- |
| `rules.powers_terms.READOUT_KINDS` | core | Tier-5 power **readout** kinds |
| `rules.powers_terms.CONFIG_DISPLAY_KINDS` | core | config-field **display** rendering |
| `rules.runtime.PATTERN_BEHAVIOURS` | core | statIntegration **patterns** |
| `rules.runtime.GATE_KINDS` | core | flaw **gate** kinds |
| `rules.conditions.MECHANISM_SCOPES` | core | condition **mechanisms** |
| `ui.power_constructor.CONFIG_WIDGET_BUILDERS` | ui | config-field **input widgets** |
| `ui.blocks.register_block(BlockDescriptor)` | ui | whole **sheet blocks** (Python) |

Core registries are safe to touch from a headless module; the `ui.*` ones import
PySide6 (only import them from a mod that targets the GUI).

## Enabling, trust, and load order (settings)

Mods are controlled by two `settings.json` keys (a settings UI will surface these
later; for now edit the file or use the `core.mods` helpers):

- **`enabled_mods`** — ids of workspace mods to layer on, in apply order. Enabling
  loads a mod's **data**. `mods.set_mod_enabled(id, True/False)`.
- **`trusted_mods`** — ids whose `python_module` may be imported at startup.
  Trusting additionally lets a mod's **code** run. `mods.set_mod_trusted(id, ...)`.

A mod must be **enabled** for its data to load and **enabled + trusted** for its
Python to run. Disabling a mod also revokes its trust.

## Safety

Enabling a mod is safe — only its JSON is merged. **Trusting** a mod imports its
Python module, which executes arbitrary code with the app's privileges. Only trust
mods from sources you would run any other program from. The engine never imports a
mod module unless its id is in `trusted_mods` (the base ruleset is implicitly
trusted); an import that raises is swallowed so a broken mod can't stop startup,
but it still ran up to the point of failure.

## Licensing (OGL boundary)

The bundled base data under `src/mm_companion/data/` is **Open Game Content** under
the OGL 1.0a (see `LICENSE-CONTENT.md`). Your own mod's content is **your own** and
under whatever license you choose. If you redistribute data *derived from the M&M
SRD*, make sure it is Open Game Content, record OGL Section 15 provenance, and do
not include Product Identity (product names, trade dress, logos). App source code
is MIT.
```

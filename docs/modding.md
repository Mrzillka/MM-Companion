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

Three working examples live in [`docs/sample-mods/`](sample-mods):
`campaign-notes` (data-only — adds an advantage and a sheet block),
`flat-bonus-readouts` (data + Python — registers a new power readout kind) and
`field-kit` (data + Python — adds a piece of gear and registers the stat-applier
kind that makes it work).

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
  content (`advantages.json`, `effects.json`, `conditions.json`, `equipment.json`,
  …), or `blocks.json` to add a declarative sheet block.
- **`priority`** decides load order. The base ruleset is priority `0` and always
  loads first; enabled mods then apply in ascending priority (higher wins). Ties
  are broken by the order in the `enabled_mods` setting.
- A malformed manifest is **skipped**, not fatal — one bad mod can't stop the app.

A few data keys worth knowing about, because they let a ruleset retune behaviour
that used to be spelled in Python:

| Key | File | Says |
| --- | --- | --- |
| `sizeEffects` | `measurements.json` | which trait each Size Table column modifies |
| `sizeRankColumn` | `measurements.json` | which column raises the rank of an effect that forces a resistance |
| `measure.mode` | `effects.json` | the movement mode a per-round speed belongs to; sources sharing one are netted into a single line (defaults to the effect's own id) |
| `groundMode` | `movement.json` | which of those modes everybody walks in |
| `statIntegration` | `modifiers.json`, `effect_modifiers.json` | what taking this extra or flaw *grants*, in the same shape a base effect uses |

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

### Built-in power readout kinds

An `effect_readouts.json` entry can use any registered kind without a line of
Python. The base ruleset ships these:

| `kind` | Renders |
| --- | --- |
| `size_table` | a Growth/Shrinking rank as its Size Table row and modifiers |
| `state` | the highest `byRank` entry the rank reaches (Insubstantial's state) |
| `measure_offsets` | one measurement row per listed rank `offset` (Burrowing's terrains) |
| `thresholds` | each row whose `minRank` the rank meets |
| `config_flag` | `trueText`/`falseText` for a boolean config key |
| `points_per_rank` | `rank × perRank` as a point total |
| `capped_rank_bonus` | `+perRank` per rank, stopping at `cap`, applied to the subject named by the `fromConfig` field (Extra Limbs' +1 Grab-or-Stability, max +5). Falls back to `unchosenNote` when that field is deferred to use-time. |

### Config-field `source` values

A `select` field can fill itself from the game data instead of listing `options`:

| `source` | Offers |
| --- | --- |
| `traits` | every trait a power can raise or lower — abilities, non-derived resistances, skills |
| `all_traits` | the same plus the derived numeric stats from `system.json`'s `derived_traits` (Defence, Initiative) — for a field asking what the player *checks*, not what the power changes |

## Data + Python: register a new mechanic

When a mod needs a genuinely new *mechanic* (not just new data), it ships one
Python module named in `python_module`. On import, the module calls one of the
engine's registry seams. The whole contract is *import-time side effects*.

```python
# flat_bonus_mod.py
from mm_companion.core.rules.powers_terms import READOUT_KINDS, EffectStat

def _flat_bonus(readout, effect, game_data, char=None):
    amount = int(readout.data.get("amount", 0))
    return [EffectStat("readout", readout.label or "Bonus", "", f"+{amount}", "")]

# replace=True keeps re-import idempotent
READOUT_KINDS.register("flat_bonus", _flat_bonus, replace=True)
```

A readout handler takes `(readout, effect, game_data, char)`. The fourth argument
is the wielding character, or `None` when the readout is asked about in the
abstract — give it a default. Most readouts have no use for it; a *relative* one
does, which is how Growth's `size_table` readout knows that a Small character
growing 2 ranks is Large rather than Huge. A handler still written against the
older three-argument signature is called again without the character, so an
existing workspace mod keeps working.

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
| `rules.appliers.STAT_APPLIERS` | core | statIntegration **apply** kinds — what a stat effect is *worth* |
| `rules.conditions.MECHANISM_SCOPES` | core | condition **mechanisms** |
| `ui.power_constructor.CONFIG_WIDGET_BUILDERS` | ui | config-field **input widgets** |
| `ui.blocks.register_block(BlockDescriptor)` | ui | whole **sheet blocks** (Python) |

`STAT_APPLIERS` has its own helper, `register_stat_applier(kind, applier)`, and is
the seam between "this effect grants something" and "here is what it grants". An
effect's `statIntegration.apply` names a kind — and so does a **modifier's**: an
extra or flaw may carry a `statIntegration` block of its own, read by the same
appliers, which is how Elongation's *Striding* grants ranks of Speed rather than
only costing points. A modifier is worth its own rank when it is `ranked`, and the
host effect's otherwise. The kind; the handler is given an
`ApplyContext` (the record, the rank it stands at, the trait it resolved against,
who is granting it) and returns `TraitContribution`s. The base ruleset registers
five kinds — `bonus`, `speed`, `sense`, `penalty_removed`, `penalty_replaced` — and
`docs/sample-mods/field-kit` registers a sixth. Two rules for a handler:

- **Pass `stacking` and `group` through untouched.** They are the *granter's* terms,
  and they are what makes the same record stack when a power grants it and obey the
  no-stacking rule when a piece of equipment does.
- **Decline by returning `()`.** An unregistered kind yields nothing rather than
  raising, so an effect whose mod is disabled simply grants no bonus and a character
  built on it still loads.

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

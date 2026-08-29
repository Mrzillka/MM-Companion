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

Beside `mods/` the workspace also holds `mod_state/`, one JSON file per mod id,
for what a mod writes about itself — see *Local state* below. It is deliberately
not *inside* `mods/`: removing a mod deletes what it **is**, and a mod
reinstalled later should still find what it had **done**.

**Installing one.** The Mod Manager (the launcher's *Manage Mods*, or a sheet's
*Settings ▸ Mods…*) offers two routes, and they end in the same place:

- **Add Mod…** picks a folder and copies it in — what you want while writing one.
- **Add from Zip…** takes a `.zip`, which is how a mod normally arrives (a release
  asset). The archive may hold the mod at its root or inside one wrapping folder;
  both work, since the latter is what every zip tool produces. An archive holding
  *two* mods is refused rather than guessed at.

Mods load once, at startup, so the manager offers to relaunch when something
changed.

## The manifest (`mod.json`)

```json
{
  "id": "campaign-notes",           // unique id (required)
  "name": "Campaign Notes",          // display name (defaults to id)
  "version": "1.0",                  // free-form version string
  "priority": 10,                    // where a newly-added mod first lands (default 0)
  "requires": ["base"],              // optional: ids this mod depends on
  "files": ["advantages.json"],      // content files this mod ships
  "python_module": "my_module"       // optional: importable module (data+Python mods)
}
```

- **`files`** lists the JSON content files in the mod folder. Only listed files
  are read. Use the same filenames as the base ruleset to *override/extend* that
  content (`advantages.json`, `effects.json`, `conditions.json`, `equipment.json`,
  …), or `blocks.json` to add a declarative sheet block.
- **`priority`** only *seeds* where a newly-added mod first lands. The real load
  order is the user's, set by dragging in the Mod Manager and stored in the
  `mod_order` setting: the base ruleset is always first, then the enabled mods in
  that order, later applying later and winning. A mod that is enabled but not yet
  in `mod_order` trails the ordered ones by ascending `priority`, which is the
  only thing this number does. So pick a number above any mod you expect to
  override, and know that the user can then move you.
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
| `rules.powers_cost.BASE_COST_KINDS` | core | `baseCostMode` / `costMode` **pricing rules** — how a record's base cost is charged |
| `rules.conditions.MECHANISM_SCOPES` | core | condition **mechanisms** |
| `ui.power_constructor.CONFIG_WIDGET_BUILDERS` | ui | config-field **input widgets** |
| `ui.power_constructor.REPEATABLE_CELL_KINDS` | ui | `repeatable` **column cells** — one row's inputs |
| `ui.blocks.register_block(BlockDescriptor)` | ui | whole **sheet blocks** (Python) |
| `ui.blocks.gm_registry.register_gm_block(GMBlockDescriptor)` | ui | whole **GM-window blocks** |

`BASE_COST_KINDS` has its own helper too, `register_base_cost_kind(mode, kind)`, and
decides *how* a record is priced rather than what it grants. A `BaseCostKind` is a
`price` function and a `formula` function registered together — one returns the points,
the other the breakdown the card's footer shows — so a mode's explanation cannot drift
from the number beside it. Both take a `BaseCostContext` carrying the effect, its base
record, the game data, the character (which may be `None`), and the modifier sums already
bucketed into per-rank, flat and ability-folded ranks. The base ruleset registers two:
`flat` (points per rank, the default for any record that names no mode) and `as_trait`
(Enhanced Trait's "it costs what the trait costs"). An unregistered mode prices as
`flat`, so a record from a disabled mod still costs something sane.

`REPEATABLE_CELL_KINDS` is the same idea one level down from `CONFIG_WIDGET_BUILDERS`: a
`repeatable` config field's rows are shaped by its `columns`, and a column's `type` picks
a `RepeatableCellKind` — a `build` function, a `read` function, and the layout `stretch`
the cell takes. Builders receive a `CellContext` — the game data plus the character, if
there is one — rather than the hosting widget, which is what lets an effect's rows and a
modifier selection's rows be literally the same cells. Treat the character as optional:
the standalone Power Constructor has no build in hand, and a cell that offers something
*this hero* has must degrade to the catalog rather than assume one.

The base ruleset registers `text`, `int` and `trait`; an unregistered type falls back to
`text`. The `trait` cell is a `TraitPicker`: a trait combo plus a *qualifier* control that
appears only when the chosen trait leaves a question open (which focus of Expertise, which
attack for Improved Critical). It reads and writes one composed key — `Expertise::Law` —
so the stored value stays a single string whatever the cell looks like.

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

## Talking to the table, and remembering things

Two seams a mod gets beyond content and widgets. Both are Python, so a mod using
them must be **trusted** as well as enabled.

### The session channel

A mod can put state in front of the whole table. One `SessionBridge` serves it —
`mm_companion.ui.session_bridge.live_session()` hands you the live one, or `None`
when there is no session, and every call below answers `False` in that case rather
than raising. A mod runs whether or not there is a table.

```python
from mm_companion.ui.session_bridge import live_session

bridge = live_session()
if bridge is not None:
    # GM only: publish one keyed entry. Everyone sees it, now and on joining.
    bridge.set_mod_state("my-mod", "timer-1", {"remaining": 90, "running": True})
    # ...and None deletes that entry, for everyone.
    bridge.set_mod_state("my-mod", "timer-1", None)

    # Read it back — never keep a copy a missed signal could leave stale.
    entries = bridge.mod_state("my-mod")

    # Any seat: ask the GM's copy of this mod for something. Nothing comes back.
    bridge.send_mod_request("my-mod", "nudge", {"id": "timer-1"})

    # GM only: write one line into the shared roll history.
    bridge.post_mod_note("my-mod", "Timer Bomb finished")

# Two signals, connected once:
#   bridge.modStateChanged(mod_id, key, payload)   payload None means gone
#   bridge.modRequest(mod_id, topic, player_id, payload)   reaches the GM only
```

Five rules that are not negotiable, because the protocol enforces them:

- **Only the GM authors state.** A player's `set_mod_state` is dropped silently.
  That is what makes the channel worth trusting; a player's mod speaks with
  `send_mod_request`, which obliges the GM's mod to do nothing at all.
- **The payload is opaque and bounded.** Plain JSON only — `str`, `int`, `float`,
  `bool`, `None`, `dict`, `list` — nested at most 4 deep, 64 items wide, strings
  clipped to 200 characters, and ~4 KB per entry with ~64 KB across every mod in
  the session. Over the size cap is **dropped, not trimmed**, and `set_mod_state`
  answers `False`, so check it. The server never interprets any of it.
- **Everything you put there is public.** There is no GM-only half. Keep secrets
  in `local_mod_state` below.
- **Push on change, never on a tick.** A relayed session gets 256 KB/s, and a
  countdown re-sent every second would spend it on nothing. Send a stamp and let
  each client's own clock do the counting.
- **Attribution is the server's.** `player_id` on a request is stamped from the
  sending seat, whatever the sender wrote.

### Local state

For what a mod remembers for *itself* — including anything it must not share:

```python
from mm_companion.core import storage

state = storage.local_mod_state("my-mod")          # {} if nothing was ever written
storage.set_local_mod_state("my-mod", {"items": [...]})
```

One JSON file per mod id under the workspace `mod_state/` dir. A missing,
unreadable or malformed file reads back as `{}` rather than raising — a mod's own
saved state must never be able to stop the app starting. Don't confuse it with
the mod *options* above: those are configuration the **user** set in the Mod
Manager, this is what the mod itself wrote.

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

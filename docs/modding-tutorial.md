# MM-Companion Modding Tutorial

> A hands-on, build-it-from-scratch walkthrough. For the exhaustive reference
> (every manifest field, every registry seam, the merge rules), read
> [`modding.md`](modding.md). This page teaches by building **one complete mod**,
> `guardian-kit`, that uses every technique at once. The finished mod ships under
> [`docs/sample-mods/guardian-kit/`](sample-mods/guardian-kit) — copy it and edit.

---

## 0. The mental model (read this first)

MM-Companion is a **data-first constructor**. The base *Mutants & Masterminds*
ruleset is not hardcoded in Python — it is itself a mod: the bundled
`src/mm_companion/data/` folder, described by `data/mod.json`. Your mod is loaded
through the *same pipeline*, layered on top.

A mod can do four kinds of things, in increasing order of power:

| # | Technique | Needs Python? | What it does |
|---|-----------|:---:|--------------|
| 1 | **Override** an existing record | no | Retune a number or field (e.g. make Damage cost 2 pp/rank) |
| 2 | **Add** a new record | no | New advantage, effect, modifier, skill, condition… |
| 3 | **Add a sheet block** (`blocks.json`) | no | A new titled panel of fields on the character sheet |
| 4 | **Register a new mechanic** | **yes** | Teach the engine a genuinely new *behaviour* |

Techniques 1–3 are **data-only**: pure JSON, no code runs, safe by construction.
Technique 4 ships one Python module and is gated behind an explicit **trust**
opt-in, because importing it runs code. Our `guardian-kit` example does all four.

**The golden rule:** if you catch yourself wanting to write an `if power.name ==
...` chain, that content belongs in a data file, not in code. Only reach for
Python when you need a new *kind of behaviour* the engine has never seen.

---

## 1. Where your mod lives

Workspace mods go one-per-directory under the workspace `mods/` folder:

| Platform | Workspace root |
| --- | --- |
| Windows | `%APPDATA%\MM-Companion\mods\` |
| macOS | `~/Library/Application Support/MM-Companion/mods/` |
| Linux | `$XDG_DATA_HOME/MM-Companion/mods/` (or `~/.local/share/MM-Companion/mods/`) |

Set the `MM_COMPANION_HOME` environment variable to point the whole workspace
somewhere else — the single most useful trick for developing a mod without
touching your real save data:

```bash
# develop against a throwaway workspace
export MM_COMPANION_HOME=/tmp/mm-dev        # macOS/Linux
$env:MM_COMPANION_HOME = "C:\temp\mm-dev"   # PowerShell
```

The `mods/` directory is created for you on first launch.

---

## 2. Scaffold the mod

Create the folder and the one required file, the manifest:

```
mods/
  guardian-kit/
    mod.json
```

```json
{
  "id": "guardian-kit",
  "name": "Guardian Kit (tutorial mod)",
  "version": "1.0",
  "priority": 50,
  "files": ["advantages.json", "effects.json", "blocks.json", "effect_readouts.json"],
  "python_module": "guardian_kit_mod"
}
```

- **`id`** — must be unique. This is what you put in `enabled_mods` / `trusted_mods`.
- **`priority`** — load order. Base ruleset is `0` and always first; higher numbers
  apply *later* and therefore *win* on conflicts. Pick a number above any mod you
  want to override.
- **`files`** — the *only* JSON files that get read. A file present in the folder
  but not listed here is ignored. Reuse a base filename (`effects.json`,
  `advantages.json`, …) to override/extend that content; use `blocks.json` for a
  declarative block.
- **`python_module`** — omit this entirely for a data-only mod. When present, it is
  the module **filename without `.py`** (`guardian_kit_mod` → `guardian_kit_mod.py`).

> A malformed manifest is *skipped*, never fatal. One broken mod cannot stop the app.

---

## 3. Technique 1 & 2 — override and add records (`advantages.json`)

Content is **deep-merged by record id**. A record you supply that reuses an
existing `id` overrides *only the fields you list* and keeps the rest; a new `id`
is appended. Plain lists (like an `options` array of strings) are replaced whole.

Here we add one brand-new advantage. (To *override* an existing one instead, we'd
reuse its id and list only the fields to change.)

```json
{
  "advantages": [
    {
      "id": "guardians_vow",
      "name": "Guardian's Vow",
      "types": ["Heroic"],
      "ranked": false,
      "maxRank": null,
      "maxRankKind": "none",
      "focused": false,
      "description": "You have sworn to protect a person, place, or ideal. Once per scene, invoke the vow to reroll a failed check made in its defense."
    }
  ]
}
```

The engine interprets these fields generically — it never looks for the string
`"Guardian's Vow"` anywhere. That is why no code is required.

> **Overriding a number, minimal restatement.** To make base Damage cost 2 pp/rank
> you would *not* restate the whole Damage record — just:
> ```json
> { "effects": [ { "id": "damage", "baseCostValue": 2 } ] }
> ```

---

## 4. Technique 2 (again) — add a new effect (`effects.json`)

Effects are the building blocks a player drags into the Power Constructor. Adding
one is pure data. We keep it simple and lean on the base `damage` shape; see
`src/mm_companion/data/effects.json` for the full field set an effect can carry.

Our example effect, "Sentinel Field", is a passive protective effect that also
carries a `flat_bonus` readout — a *new readout kind* the engine doesn't know yet.
That readout is what forces us into Technique 4 below.

---

## 5. Technique 3 — a sheet block with no code (`blocks.json`)

A `blocks.json` adds a whole panel to the character sheet as a **declarative
block** — a titled group of field/label rows — registered through the same block
registry as the built-in blocks, so it floats, hides, and rearranges like any
other block.

```json
{
  "blocks": [
    {
      "id": "guardian_oath",
      "title": "Guardian's Oath",
      "row": 9,
      "col": 0,
      "min_height": 110,
      "fields": [
        { "kind": "label", "text": "Record the terms of this character's vow." },
        { "kind": "text", "key": "guardian_ward", "label": "Sworn to protect" },
        { "kind": "text", "key": "guardian_nemesis", "label": "Nemesis" }
      ]
    }
  ]
}
```

- `"kind": "text"` rows are **editable** and backed by `Character.profile[key]`, so
  they round-trip through save/load automatically — no extra wiring.
- `"kind": "label"` rows are static text.
- `row` / `col` place the block in the default layout; `min_height` sizes it.
- A block `id` that collides with an existing block is **skipped** — declarative
  blocks are strictly *additive* and can never clobber a base block.

At this point the mod is fully functional as a **data-only** mod. If you don't need
a new mechanic, stop here, drop `python_module` from the manifest, and skip to §8.

---

## 6. Technique 4 — register a new mechanic (Python)

Sometimes data isn't enough: you want the engine to *do* something it has never
done. The seam for that is a **registry**. Every registry is the same generic
`Registry` object with one method:

```python
some_registry.register(key, handler, replace=False)
```

Your Python module calls `register` **at import time**. That import-time side
effect *is* the entire contract — there is no plugin class to subclass, no entry
point to declare. The registries you can extend:

| Registry (import path) | Extends |
| --- | --- |
| `core.rules.powers_terms.READOUT_KINDS` | Tier-5 power **readout** kinds |
| `core.rules.powers_terms.CONFIG_DISPLAY_KINDS` | config-field **display** rendering |
| `core.rules.runtime.PATTERN_BEHAVIOURS` | statIntegration **patterns** |
| `core.rules.runtime.GATE_KINDS` | flaw **gate** kinds |
| `core.rules.conditions.MECHANISM_SCOPES` | condition **mechanisms** |
| `ui.power_constructor.CONFIG_WIDGET_BUILDERS` | config-field **input widgets** |
| `ui.blocks.register_block(BlockDescriptor)` | whole **sheet blocks** (in Python) |

Core registries are headless-safe. The `ui.*` ones import PySide6 — only touch
them from a mod that targets the GUI.

Our module teaches the engine the `flat_bonus` readout kind that our effect and
`effect_readouts.json` reference:

```python
# guardian_kit_mod.py
from __future__ import annotations

from mm_companion.core.rules.powers_terms import READOUT_KINDS, EffectStat


def _flat_bonus(readout, effect, game_data):
    """Render a `flat_bonus` readout as a single "+N" stat row."""
    amount = int(readout.data.get("amount", 0))
    label = readout.label or "Bonus"
    return [EffectStat("readout", label, "", f"+{amount}", "")]


# replace=True keeps re-import idempotent (won't raise if already registered).
READOUT_KINDS.register("flat_bonus", _flat_bonus, replace=True)
```

And the data that *uses* the new kind:

```json
{
  "effectReadouts": {
    "sentinel_field": [
      { "kind": "flat_bonus", "label": "Sentinel Bonus", "amount": 2 }
    ]
  }
}
```

The handler signature and return type are dictated by the registry — read the base
engine's default handlers (in `core/rules/powers_terms.py`) to see what shape each
registry expects. The pattern is always the same: a pure function of the data
record plus context, returning the engine's value type.

---

## 7. How the app finds and imports your Python

`__main__.main()` calls `mods.initialize_mods()` at startup (after the workspace
exists, before the first `load_game_data()`). For each enabled **and trusted** mod
with a `python_module`, it puts the mod's folder on `sys.path` and imports the
module by its bare name — firing your `register_*` calls before any game data is
parsed. An import that raises is swallowed so a broken mod can't halt startup (but
it *did* run up to the point of failure).

---

## 8. Turn the mod on (settings)

Two `settings.json` keys in the workspace control mods (a settings UI is TODO; for
now edit the file or use the `core.mods` helpers):

- **`enabled_mods`** — ids whose **data** layers on. Enabling is safe.
- **`trusted_mods`** — ids whose `python_module` may be **imported**. Trusting runs
  code.

A mod needs to be **enabled** for its data, and **enabled + trusted** for its
Python. Disabling a mod also revokes its trust.

```python
from mm_companion.core import mods

mods.set_mod_enabled("guardian-kit", True)
mods.set_mod_trusted("guardian-kit", True)   # only because it ships Python
```

Or by hand in `settings.json`:

```json
{
  "enabled_mods": ["guardian-kit"],
  "trusted_mods": ["guardian-kit"]
}
```

After toggling a mod at runtime, invalidate the parse cache:
`data_loader.clear_game_data_cache()`.

---

## 9. Test it headlessly

You don't need the GUI to prove a mod loads. Point the workspace at your mod, load
the merged data, and assert your records are present — the same thing
`tests/test_mod_loading.py` does for the shipped samples:

```python
import os, shutil
os.environ["MM_COMPANION_HOME"] = "/tmp/mm-dev"

from mm_companion.core import storage, mods
from mm_companion.core.data_loader import load_game_data, clear_game_data_cache

storage.ensure_workspace()
# copy docs/sample-mods/guardian-kit -> <workspace>/mods/guardian-kit ...
mods.set_mod_enabled("guardian-kit", True)
mods.set_mod_trusted("guardian-kit", True)
mods.initialize_mods()            # imports the Python, fires register_*
clear_game_data_cache()

data = load_game_data()
assert any(a.id == "guardians_vow" for a in data.advantages)
assert any(e.id == "sentinel_field" for e in data.effects)
```

---

## 10. Safety & licensing (don't skip)

- **Enabling is safe** — only JSON is merged. **Trusting imports code** that runs
  with the app's full privileges. Only trust mods you'd run as any other program.
- The bundled base data is **Open Game Content** under the OGL 1.0a. *Your* mod's
  content is yours, under whatever license you choose. If you redistribute data
  **derived from the M&M SRD**, keep it Open Game Content, record OGL Section 15
  provenance, and include **no Product Identity** (product names, trade dress,
  logos). App source code is MIT.

---

## Checklist

- [ ] `mod.json` with a unique `id`, a `priority`, and a `files` list.
- [ ] Every JSON file you ship is listed in `files`.
- [ ] Overrides reuse an existing `id`; new content uses a fresh `id`.
- [ ] `blocks.json` block ids don't collide with base blocks.
- [ ] Python module name in `python_module` matches the filename (no `.py`).
- [ ] Every custom `kind`/mechanic your data references is `register`ed in Python.
- [ ] Mod is in `enabled_mods` (+ `trusted_mods` if it ships Python).
- [ ] `register(..., replace=True)` so re-import is idempotent.

# guardian-kit — tutorial reference mod

The mod built step-by-step in [`docs/modding-tutorial.md`](../../modding-tutorial.md).
It exercises **all four modding techniques in one package**:

| File | Technique | What it demonstrates |
|------|-----------|----------------------|
| `mod.json` | — | The manifest: id, priority, `files`, `python_module`. |
| `advantages.json` | Add a record | A new `guardians_vow` advantage, pure data. |
| `effects.json` | Add a record | A new `sentinel_field` base effect for the Power Constructor. |
| `blocks.json` | Declarative block | A "Guardian's Oath" sheet panel with no Python. |
| `effect_readouts.json` | Use a custom kind | Attaches a `flat_bonus` readout to `sentinel_field`. |
| `guardian_kit_mod.py` | Register a mechanic | Teaches the engine the `flat_bonus` readout kind. |

## Try it

```python
import os
os.environ["MM_COMPANION_HOME"] = "/tmp/mm-dev"   # throwaway workspace

from mm_companion.core import storage, mods
from mm_companion.core.data_loader import load_game_data, clear_game_data_cache

storage.ensure_workspace()
# copy this folder to <workspace>/mods/guardian-kit, then:
mods.set_mod_enabled("guardian-kit", True)
mods.set_mod_trusted("guardian-kit", True)   # required: the mod ships Python
mods.initialize_mods()
clear_game_data_cache()

data = load_game_data()
assert any(a.id == "guardians_vow" for a in data.advantages)
assert any(e.id == "sentinel_field" for e in data.effects)
```

For a **data-only** variant, delete `guardian_kit_mod.py` and
`effect_readouts.json`, drop `python_module` and `effect_readouts.json` from
`mod.json`, and you no longer need `trusted_mods`.

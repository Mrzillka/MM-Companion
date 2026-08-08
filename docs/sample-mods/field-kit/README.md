# field-kit — sample equipment + stat-applier mod

The equipment-layer counterpart to `flat-bonus-readouts`. It shows the one seam a
mod needs to add gear the engine could not otherwise describe: a **stat applier**.

| File | Technique | What it demonstrates |
|------|-----------|----------------------|
| `mod.json` | — | The manifest: `files`, `python_module`, and why this one needs trust. |
| `effects.json` | Add a record | An `ablative_weave` base effect whose `statIntegration.apply` names a kind the base engine does not know. |
| `equipment.json` | Add a record | An `ablative_vest` in the stock `armor` category, built out of that effect. |
| `field_kit_mod.py` | Register a mechanic | Teaches the engine the `partial_bonus` apply kind. |

The split is the point. *What the vest is* — its price, its category, its rank of
weave — is data, merged into the shipped catalog by id. *What "partial" means* is a
mechanic, and mechanics are the only thing that needs code.

## Try it

```python
import os
os.environ["MM_COMPANION_HOME"] = "/tmp/mm-dev"   # throwaway workspace

from mm_companion.core import storage, mods
from mm_companion.core.data_loader import load_game_data, clear_game_data_cache

storage.ensure_workspace()
# copy this folder to <workspace>/mods/field-kit, then:
mods.set_mod_enabled("field-kit", True)
mods.set_mod_trusted("field-kit", True)   # required: the mod ships Python
mods.initialize_mods()
clear_game_data_cache()

data = load_game_data()
assert "ablative_vest" in data.equipment_catalog()
```

Buy a rank of the Equipment advantage, add the vest from the Equipment block, and
its card reads +2 Toughness — half of its rank 4, rounded up.

## What to change to make it yours

- **A different sum**: edit `_partial_bonus`. `context.amount` is
  `flat + rank × per_rank`, all three of which are data on the effect record
  (`amountFlat` / `amountPerRank`), so a lot of arithmetic needs no code at all.
- **A different trait**: edit the effect's `statIntegration.target`, or set
  `configurableTarget` so the player picks it in the Power Constructor.
- **No Python at all**: drop `apply` from the `statIntegration` and the record falls
  back to the shipped `bonus` kind — a full-rank +4 vest, data-only.

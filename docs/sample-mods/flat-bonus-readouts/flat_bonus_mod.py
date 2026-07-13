"""Sample mod Python module: teach the engine a new readout ``kind``.

A data-only mod can only use the readout kinds the base engine already knows
(``size_table``, ``state``, ``thresholds``, …). This mod ships a *new* kind,
``flat_bonus``, and registers a handler for it so the engine can render it.

The app imports this module at startup **only if the mod is both enabled and
trusted** (importing runs this code). Once imported, the ``register`` call below
adds the handler to the process-wide readout registry, and the matching
``effect_readouts.json`` in this mod attaches a ``flat_bonus`` readout to the
``damage`` effect — so any Damage power now shows a "Signature Bonus" row.

This is the whole contract for a Python mod: import-time side effects that call
one of the engine's ``register_*`` seams. See ``docs/modding.md``.
"""

from __future__ import annotations

from mm_companion.core.rules.powers_terms import READOUT_KINDS, EffectStat


def _flat_bonus(readout, effect, game_data):
    """Render a ``flat_bonus`` readout as a single ``+N`` row."""
    amount = int(readout.data.get("amount", 0))
    return [EffectStat("readout", readout.label or "Bonus", "", f"+{amount}", "")]


# ``replace=True`` so re-importing (or reloading the mod) is idempotent rather
# than raising on the already-registered key.
READOUT_KINDS.register("flat_bonus", _flat_bonus, replace=True)

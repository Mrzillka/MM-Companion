"""guardian-kit — the Python half of the tutorial reference mod.

This module exists for one reason: the mod's ``effect_readouts.json`` attaches a
readout of ``"kind": "flat_bonus"`` to the ``sentinel_field`` effect, but the base
engine has never heard of a ``flat_bonus`` readout. A data-only mod can only use
the readout kinds already built in (``size_table``, ``state``, ``thresholds``, …).
To ship a *new* kind we register a handler for it here.

How the app runs this file
---------------------------
At startup ``mods.initialize_mods()`` imports this module **only if the mod is both
enabled and trusted** (importing runs code — hence the trust gate). The import
itself is the whole contract: the ``register`` call at the bottom adds our handler
to the process-wide readout registry *before* any game data is parsed, so by the
time a Sentinel Field power is rendered the engine knows how to draw its readout.

See ``docs/modding-tutorial.md`` (§6) for the walkthrough and the table of every
registry a Python mod may extend.
"""

from __future__ import annotations

# READOUT_KINDS is the generic Registry the engine walks when rendering a power's
# Tier-5 readouts. EffectStat is the value type a readout handler must return.
from mm_companion.core.rules.powers_terms import READOUT_KINDS, EffectStat


def _flat_bonus(readout, effect, game_data):
    """Render a ``flat_bonus`` readout as a single "+N" stat row.

    A readout handler is a pure function of (the readout record, the owning
    effect instance, the merged game data). It returns a list of ``EffectStat``
    rows the game-terms summary appends verbatim. Here we read the ``amount`` the
    JSON supplied and format it as a plus-prefixed value.
    """
    amount = int(readout.data.get("amount", 0))
    label = readout.label or "Bonus"
    #            group,      label,  left, value,       note
    return [EffectStat("readout", label, "", f"+{amount}", "")]


# replace=True makes re-importing idempotent: if this key is already registered
# (e.g. the mod was reloaded), overwrite it instead of raising.
READOUT_KINDS.register("flat_bonus", _flat_bonus, replace=True)

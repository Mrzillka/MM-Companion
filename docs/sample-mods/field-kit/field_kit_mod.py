"""Sample mod Python module: teach the engine a new stat-applier ``kind``.

A stat effect is a *data record* plus an ``apply`` kind naming what that record
means (see ``docs/mm-equipment-architecture.md`` §5). The base ruleset ships five
kinds — ``bonus``, ``speed``, ``sense``, ``penalty_removed``, ``penalty_replaced``
— and an effect's ``statIntegration.apply`` picks one. This mod ships a *sixth*,
``partial_bonus``, and registers the handler that interprets it.

Everything else is data. ``effects.json`` adds an **Ablative Weave** effect whose
``statIntegration`` names the new kind, and ``equipment.json`` adds an **Ablative
Vest** to the armor catalog built out of it — so the item is pickable, priced,
worn and drawn by the stock Equipment block with no further code.

The app imports this module at startup **only if the mod is both enabled and
trusted** (importing runs this code). Enabled but untrusted, the data still merges
and the vest is still buyable — it simply grants nothing, because an unregistered
apply kind yields no contributions rather than raising.

This is the whole contract for a Python mod: import-time side effects that call one
of the engine's ``register_*`` seams. See ``docs/modding.md``.
"""

from __future__ import annotations

from mm_companion.core.rules.appliers import (
    ApplyContext,
    TraitContribution,
    register_stat_applier,
    trait_category,
)


def _partial_bonus(context: ApplyContext) -> tuple[TraitContribution, ...]:
    """Half the record's amount, rounded up, on the trait it targets.

    ``context.amount`` is what the record is worth at its current rank
    (``flat + rank × per_rank``); :func:`trait_category` says which of the sheet's
    lists the target belongs to, and returns ``""`` for a target the sheet does not
    track — the honest way to decline rather than inventing a row.

    ``stacking`` and ``group`` are the *granter's* terms and must travel through
    untouched: they are what makes this bonus obey the no-stacking rule when it
    arrives on a piece of equipment and stack when it arrives on a power. An applier
    decides what a record is worth, never who it competes with.
    """

    category = trait_category(context.game_data, context.target)
    if not category:
        return ()
    return (
        TraitContribution(
            amount=-(-context.amount // 2),  # ceil, without importing math
            stat=context.target,
            category=category,
            source=context.source,
            stacking=context.stacking,
            group=context.group,
            kind="partial_bonus",
        ),
    )


# ``replace=True`` so re-importing (or reloading the mod) is idempotent rather than
# raising on the already-registered key.
register_stat_applier("partial_bonus", _partial_bonus, replace=True)

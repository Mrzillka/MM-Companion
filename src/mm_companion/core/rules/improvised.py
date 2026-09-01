"""Improvised Effects: what it takes to rig a power up on the spot (p101-102).

A character with the **Improvised Effect** advantage can build a one-off power out of
effects and modifiers they have not paid points for, and reach it with a skill check
instead. The whole thing hangs off one number — *the effect's Power Point cost* — which
sets how long the preparation takes, how hard the preparation check is, and how hard the
check to actually use it is:

- preparation takes a **time rank equal to the cost**, never below the ruleset's minimum
  (3, one minute);
- you may **shave** time ranks off that, at a stated DC penalty each;
- or **spend** extra time ranks, for a stated check bonus each;
- the preparation DC is ``defense_dc_base + cost + penalty``;
- the use DC is ``defense_dc_base + cost``, and a prepared effect lasts one scene.

None of those dials is spelled here: they come from ``system.json``
(:class:`~..data_loader.ImprovisedEffectRules`), so a ruleset that prices improvisation
differently retunes it without touching this module.

Everything is a pure function over a cost and the game data. The *cost* comes from
:func:`~.powers_cost.power_gross_cost` rather than the total, because the rules put
Removable out of bounds for an improvised effect — it is one-use by nature — and the gross
is precisely the price before any power-scope discount.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..character import Character
from ..data_loader import GameData
from ..powers import Power
from .derived import all_advantage_selections
from .powers_cost import power_gross_cost

#: The advantage that lets a character improvise at all. Its per-selection ``parameter``
#: names the skill they do it with, which is what the checks below are rolled on.
IMPROVISED_EFFECT_ADVANTAGE = "Improvised Effect"

#: The advantage that lets one be prepared in advance and kept on hand.
PREPARED_EFFECT_ADVANTAGE = "Prepared Effect"


@dataclass(frozen=True)
class ImprovisedPlan:
    """What improvising a power of a given cost would take.

    ``base_time_rank`` is the preparation time the cost alone implies (the cost as a time
    rank, floored at the minimum); ``time_rank`` is where the player's trades leave it and
    ``time_text`` is that rank as a real duration off the measurements table.

    ``ranks_saved`` and ``ranks_spent`` are the two trades, already clamped — you cannot
    shave below the minimum, and neither can go negative — so a caller can show what was
    actually applied rather than what was asked for. ``prep_dc`` and ``use_dc`` are the two
    difficulties, and ``check_bonus`` is what the extra time is worth on the preparation
    check.
    """

    cost: int
    base_time_rank: int
    time_rank: int
    time_text: str
    ranks_saved: int
    ranks_spent: int
    prep_dc: int
    use_dc: int
    check_bonus: int


def improvised_effect_cost(power: Power, game_data: GameData, char: Character | None = None) -> int:
    """The Power Point cost an improvised version of ``power`` is reckoned from.

    :func:`~.powers_cost.power_gross_cost`, not the total: "The Removable modifier does not
    apply to Improvised Effects, as they are one-use by nature" (p101), and the gross is
    the cost before any power-scope discount — which Removable is the only one of. Nothing
    else about the build is treated differently, so an improvised Blast is reckoned from
    exactly what a bought Blast would cost.
    """

    return max(0, power_gross_cost(power, game_data, char))


def improvised_plan(
    cost: int,
    game_data: GameData,
    *,
    ranks_saved: int = 0,
    ranks_spent: int = 0,
) -> ImprovisedPlan:
    """Work out the preparation time and the two DCs for an improvised effect.

    ``ranks_saved`` shortens the preparation and raises the preparation DC; ``ranks_spent``
    lengthens it and grants a bonus on the check. They are opposite trades on the same
    dial, so asking for both applies both — the net time is what it comes to, while the DC
    penalty and the check bonus are each charged for the ranks actually moved.

    The clamp matters and is the rules': the preparation "can be reduced ... down to the
    minimum of 3", so a cost already at or below the minimum has nothing to shave and is
    charged no penalty for asking.
    """

    rules = game_data.system.improvised_effect
    base = max(int(cost), rules.min_time_rank)
    saved = max(0, min(int(ranks_saved), base - rules.min_time_rank))
    spent = max(0, int(ranks_spent))
    time_rank = base - saved + spent
    dc_base = game_data.system.defense_dc_base
    return ImprovisedPlan(
        cost=int(cost),
        base_time_rank=base,
        time_rank=time_rank,
        time_text=game_data.measurements.label("time", time_rank) or f"time rank {time_rank}",
        ranks_saved=saved,
        ranks_spent=spent,
        prep_dc=dc_base + int(cost) + saved * rules.dc_per_time_rank_saved,
        use_dc=dc_base + int(cost),
        check_bonus=spent * rules.check_bonus_per_time_rank_spent,
    )


def improvised_skills(char: Character, game_data: GameData) -> tuple[str, ...]:
    """The skill row ids this character may improvise with, in the order they were taken.

    Improvised Effect is a *focused* advantage: each selection names the skill it is taken
    for (Technology for gadgets, Expertise: Magic for rituals), so a character can hold
    several and improvises with whichever fits. Empty when they lack the advantage — which
    is the honest answer, not a reason to hide the arithmetic: a player may well be working
    out whether the advantage is worth taking.

    Bought *and* power-granted (:func:`~.derived.all_advantage_selections`), and a granted
    one carries its skill on its own trait key, so ``Improvised Effect::Technology``
    arrives here as the same name-plus-parameter pair a bought one does.
    """

    return tuple(
        selection.parameter
        for selection in all_advantage_selections(char, game_data)
        if selection.name == IMPROVISED_EFFECT_ADVANTAGE and selection.parameter
    )


def prepared_effect_ranks(char: Character, game_data: GameData) -> int:
    """How many improvised effects this character can keep prepared and on hand.

    Prepared Effect is ranked and each rank is one stashed effect (p102's "have a
    previously prepared effect conveniently on-hand"). Zero without it, in which case an
    improvised effect has to be prepared during play or bought back with a Hero Point.

    Bought *and* power-granted, for the reason :func:`improvised_skills` gives.
    """

    return sum(
        max(1, selection.rank)
        for selection in all_advantage_selections(char, game_data)
        if selection.name == PREPARED_EFFECT_ADVANTAGE
    )

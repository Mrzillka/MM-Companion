"""Size: the bought size category, what alters it, and what it does to the sheet.

Its own module rather than a corner of :mod:`~.movement` because ``derived`` has to
import it: size is a *trait source* now, and ``movement`` sits **below** ``derived``
(it reaches ``powers_cost``, which imports ``derived``). Keeping the size math here —
above ``runtime``, below ``derived`` — is what breaks that cycle, so this module may
import nothing but the character, the game data, ``appliers`` and ``runtime``.
"""

from __future__ import annotations

from ..character import Character
from ..components import APPLY_BONUS
from ..data_loader import GameData
from .appliers import (
    CATEGORY_ABILITY,
    CATEGORY_RESISTANCE,
    CATEGORY_SKILL,
    GROUP_INTRINSIC,
    STACK_SUM,
    TraitContribution,
)
from .runtime import effect_is_active, live_powers


def size_shift(char: Character, game_data: GameData) -> int:
    """Net size-rank shift from active size-altering powers (Growth +, Shrinking −).

    Reads each live effect's ``size_table`` readout (``effect_readouts.json``) and, when
    the effect is currently active, applies its signed rank. Growth and Shrinking both
    on nets to the difference between them, since the signs simply sum. Zero when no
    size power is on, so the character sits at their bought size.

    The **bought** rank, not the effective one, for the reason
    :func:`~.runtime.build_contributions` uses it: an effective rank asks
    :func:`~.derived.effective_ability`, which asks what is standing on the sheet, which
    now includes what size grants. No shipped size effect folds an ability in, so this
    costs nothing and closes the loop for good.
    """

    shift = 0
    for power in live_powers(char.powers):
        for effect in power.effects:
            base = next((e for e in game_data.effects if e.id == effect.effect_id), None)
            if base is None:
                continue
            for readout in game_data.effect_readouts.get(effect.effect_id, ()):
                if readout.kind != "size_table":
                    continue
                if not effect_is_active(power, effect, base, game_data, char):
                    continue
                sign = int(readout.data.get("sign", 1))
                shift += sign * effect.rank
    return shift


def base_size_rank(char: Character, game_data: GameData) -> int:
    """The bought size category's rank (Medium → 0), defaulting to Medium."""

    category = str(char.characteristics.get("size", "Medium"))
    rank = game_data.measurements.size_rank_for_category(category)
    return rank if rank is not None else 0


def effective_size_rank(char: Character, game_data: GameData) -> int:
    """The character's current size rank: their bought size plus any :func:`size_shift`.

    A Small (−1) character growing 2 ranks is Large (+1), not Huge — the shift is
    *relative* to what they already are.
    """

    return base_size_rank(char, game_data) + size_shift(char, game_data)


def effective_size(char: Character, game_data: GameData) -> str:
    """The character's current size category, after active Growth/Shrinking.

    The bought size (clamped to the Size Table) when nothing alters it; otherwise the
    category the shifted rank lands on.
    """

    row = game_data.measurements.size_row(effective_size_rank(char, game_data))
    if row is not None:
        return row.size_category
    return str(char.characteristics.get("size", "Medium"))


def size_contributions(char: Character, game_data: GameData) -> tuple[TraitContribution, ...]:
    """Every trait modifier the character's **current** size confers.

    One contribution per :class:`~mm_companion.core.data_loader.SizeEffect` the ruleset
    declares (``measurements.json``'s ``sizeEffects``), read off the Size Table row for
    :func:`effective_size_rank` — so growing and shrinking move these numbers exactly as
    buying a different size category does. Nothing here names a trait: which column
    lands on which trait is data.

    They join the sheet in :data:`~.appliers.GROUP_INTRINSIC`, which is *added* to
    whichever bought group won rather than weighed against it — a suit of armour must
    not delete a Colossal creature's Toughness (see
    :func:`~.appliers.resolve_contributions`).

    **Zero amounts are never emitted**, and a Medium character emits nothing at all. A
    :class:`~.appliers.TraitBonus` is a dataclass instance and therefore always truthy,
    so a 0 would paint a green ``→ N`` on every Medium sheet's Defence, Toughness and
    Strength, and would switch the Skills block's "+" column on for everyone.
    """

    rank = effective_size_rank(char, game_data)
    if rank == 0:
        return ()
    row = game_data.measurements.size_row(rank)
    if row is None:
        return ()
    source = f"Size ({row.size_category})"
    return tuple(
        TraitContribution(
            amount=amount,
            stat=effect.target,
            category=effect.category,
            source=source,
            stacking=STACK_SUM,
            group=GROUP_INTRINSIC,
            kind=APPLY_BONUS,
        )
        for effect in game_data.measurements.size_effects
        if (amount := row.modifier(effect.column))
    )


def size_trait_modifier(char: Character, game_data: GameData, category: str, target: str) -> int:
    """The size modifier standing on one trait, or 0.

    The same numbers :func:`size_contributions` grants, asked one trait at a time. This
    is what the Power Level caps read: a cap has to move by exactly what size moved in
    its *inputs*, or being large is paid for twice (see
    :func:`~.validation.power_level_violations`).
    """

    return sum(
        c.amount
        for c in size_contributions(char, game_data)
        if c.category == category and c.stat == target
    )


def size_resistance_shift(char: Character, game_data: GameData, key: str) -> int:
    """How much size moved a resistance's **total**, following the same derivation.

    A mirror of :func:`~.derived.resistance_base`, and it has to be: the Size Table's
    Defense column lands on the Defence trait, while the Power Level cap is written
    against Dodge, which *derives* from it. Asking which contributions carry the trait's
    own name would find nothing on Dodge, raise its cap, and hand a large character a
    free point of defence that the Dodge + Toughness pair is meant to cost them.

    Followed properly, the paired cap needs no adjustment at all on the base ruleset:
    Dodge reports −N through Defence, Toughness +N, and the pair sums to zero.
    """

    own = size_trait_modifier(char, game_data, CATEGORY_RESISTANCE, key)
    resistance = next((r for r in game_data.resistances if r.key == key), None)
    base_key = resistance.ability if resistance else ""
    if not base_key:
        return own
    if any(a.key == base_key for a in game_data.abilities):
        return own + size_trait_modifier(char, game_data, CATEGORY_ABILITY, base_key)
    if any(r.key == base_key for r in game_data.resistances):
        return own + size_resistance_shift(char, game_data, base_key)
    return own


def size_skill_shift(char: Character, game_data: GameData, row_id: str) -> int:
    """How much size moved a skill row's **total**.

    Its own modifier plus the one on the ability it derives from, because
    :func:`~.derived.skill_total` folds the effective ability in — and the Size Table's
    Damage column lands on Strength, so every Strength-linked skill moves with it.

    A focused row (``"Expertise: Law"``) resolves against its base skill's name, which
    is what :func:`~.derived.skill_bonus` already scopes a boost by.
    """

    by_name = {s.name: s for s in game_data.skills}
    skill = by_name.get(row_id) or by_name.get(row_id.split(":", 1)[0].strip())
    if skill is None:
        return 0
    shift = size_trait_modifier(char, game_data, CATEGORY_SKILL, skill.name)
    if row_id != skill.name:
        shift += size_trait_modifier(char, game_data, CATEGORY_SKILL, row_id)
    if skill.ability:
        shift += size_trait_modifier(char, game_data, CATEGORY_ABILITY, skill.ability)
    return shift

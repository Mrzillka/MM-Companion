"""Size: the bought size category, what alters it, and what it does to the sheet.

Its own module rather than a corner of :mod:`~.movement` because ``derived`` has to
import it: size is a *trait source* now, and ``movement`` sits **below** ``derived``
(it reaches ``powers_cost``, which imports ``derived``). Keeping the size math here —
above ``runtime``, below ``derived`` — is what breaks that cycle, so this module may
import nothing but the character, the game data, ``appliers`` and ``runtime``.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from ..character import Character
from ..components import APPLY_BONUS
from ..data_loader import GameData
from ..powers import Power, PowerEffectInstance
from .appliers import (
    CATEGORY_ABILITY,
    CATEGORY_RESISTANCE,
    CATEGORY_SKILL,
    GROUP_INTRINSIC,
    STACK_SUM,
    TraitContribution,
)
from .runtime import effect_current_rank, effect_is_active, effect_stands, live_powers

#: The ``effect_readouts.json`` readout kind that marks an effect as a size shift.
#: Nothing here names Growth or Shrinking: an effect is a size effect because the
#: ruleset gave it this readout, and a mod's own one joins on the same terms.
SIZE_READOUT_KIND = "size_table"


def size_shift(char: Character, game_data: GameData) -> int:
    """Net size-rank shift from active size-altering powers (Growth +, Shrinking −).

    Reads each live effect's ``size_table`` readout (``effect_readouts.json``) and, when
    the effect is currently active, applies its signed rank. Growth and Shrinking both
    on nets to the difference between them, since the signs simply sum. Zero when no
    size power is on, so the character sits at their bought size.

    The rank read is the one the effect is *currently dialled to*
    (:func:`~.runtime.effect_current_rank`), which is what makes a Growth 3 a ladder of
    three rungs rather than one leap: the player picks a rung on the card and the whole
    sheet follows from this one number.

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
                if readout.kind != SIZE_READOUT_KIND:
                    continue
                if not effect_is_active(power, effect, base, game_data, char):
                    continue
                sign = int(readout.data.get("sign", 1))
                shift += sign * effect_current_rank(effect, game_data, char)
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


@dataclass(frozen=True)
class SizeStep:
    """One rung of a size effect's ladder — a rank the player can hold it at.

    ``rank`` is the effect rank this rung runs at and ``category`` the size the wielder
    *becomes* there, which is what the card's button is labelled with: a rung is only
    meaningful as an outcome, and "Growth 2" says nothing a Small character can act on
    while "Large" says everything.

    ``last_rank`` closes the rung's span. It is ``rank`` for every ordinary rung and
    larger only where the Size Table has **clamped** — a Colossal character's Growth 4
    reaches Awesome at rank 1 and stays there, and four buttons all reading "Awesome"
    would be four ways to do the same thing. Those ranks fold into the rung that first
    reached them, which is also what keeps :attr:`current` honest when the effect is
    dialled to one of the folded-away ranks.
    """

    rank: int
    last_rank: int
    category: str
    current: bool = False


def effect_dials_by_default(effect: PowerEffectInstance, game_data: GameData) -> bool:
    """Whether the ruleset gives this effect a rank slider without being asked.

    A size effect does, because a Growth 3 is not one leap to Gargantuan — it is Large,
    then Huge, then Gargantuan, and which rung you are standing on is a mid-fight
    decision the card has to be able to make. Nothing here names Growth or Shrinking:
    the test is the :data:`SIZE_READOUT_KIND` readout :func:`size_steps` already reads,
    so a mod's own size effect defaults to a ladder on the same terms.

    Every other effect defaults to no slider — a Blast is all-or-nothing until somebody
    says otherwise.
    """

    return any(
        readout.kind == SIZE_READOUT_KIND
        for readout in game_data.effect_readouts.get(effect.effect_id, ())
    )


def effect_has_rank_dial(effect: PowerEffectInstance, game_data: GameData) -> bool:
    """Whether this effect's card carries a rank slider at all.

    The one door both the sheet card and the Power Constructor's *Extended settings*
    checkbox go through, so the box can never say one thing and the card another.
    ``rank_dial`` is tri-state: the player's ``True``/``False`` wins, and ``None`` — the
    default, and what every save written before the tri-state says — hands the question
    to :func:`effect_dials_by_default`.

    It answers only whether the *control* exists. Whether it is worth drawing (a rank-1
    effect has nothing to choose between) is the card's own question, and how far the
    dial is turned is ``current_rank``.
    """

    if effect.rank_dial is not None:
        return bool(effect.rank_dial)
    return effect_dials_by_default(effect, game_data)


def size_steps(
    power: Power, effect: PowerEffectInstance, char: Character, game_data: GameData
) -> tuple[SizeStep, ...]:
    """The rungs a size effect can be held at, relative to *this* wielder.

    Empty for anything that is not a size effect — an effect is one because the ruleset
    gave it a :data:`SIZE_READOUT_KIND` readout, so nothing here names Growth or
    Shrinking and a mod's own size effect gets the ladder for free.

    The categories are read **against the character**, exactly as the card's size
    readout is (:func:`~.powers_terms.effect_readout_rows`): a Small character's Growth
    2 climbs Medium → Large, not Large → Huge. At most one rung is :attr:`~SizeStep
    .current`, and none is while the effect is not standing on the sheet — the ladder
    says where the power *is*, and a power switched off (or an array alternate nobody
    picked) is nowhere, not at rank 1.
    """

    readout = next(
        (
            r
            for r in game_data.effect_readouts.get(effect.effect_id, ())
            if r.kind == SIZE_READOUT_KIND
        ),
        None,
    )
    if readout is None or effect.rank <= 0:
        return ()
    base = next((e for e in game_data.effects if e.id == effect.effect_id), None)
    if base is None:
        return ()

    sign = int(readout.data.get("sign", 1))
    start = base_size_rank(char, game_data)
    steps: list[SizeStep] = []
    for rank in range(1, effect.rank + 1):
        row = game_data.measurements.size_row(start + sign * rank)
        if row is None:
            return ()
        if steps and steps[-1].category == row.size_category:
            steps[-1] = replace(steps[-1], last_rank=rank)  # the table clamped here
            continue
        steps.append(SizeStep(rank=rank, last_rank=rank, category=row.size_category))

    # A rung is lit only where the power is actually standing — see ``effect_stands``,
    # which is the same pair ``size_shift`` asks and which the card's rank dial reads to
    # position itself, so the strip and the slider can never disagree about where a
    # power is.
    if not effect_stands(power, effect, game_data, char):
        return tuple(steps)
    now = effect_current_rank(effect, game_data, char)
    return tuple(replace(s, current=s.rank <= now <= s.last_rank) for s in steps)


# -- reach -----------------------------------------------------------------------------


@dataclass(frozen=True)
class Reach:
    """How far a character makes close attacks, in feet and in whole Spaces.

    Feet are the truth and Spaces are read off them, because reach adds several sources
    that do not land on whole Spaces: a Gargantuan character reaches 3 Spaces (18 ft) and
    an Elongation 1 stretches 15 ft further, which is 33 ft — five Spaces and a half.
    Rounding each source to Spaces first would have lost that half at every step, so the
    readout keeps the feet exact and says :attr:`exact` is ``False``, which is what the
    ``~`` in ``~5 spaces / 33 ft.`` means.
    """

    feet: float
    #: How many feet one Space is (``measurements.json``'s ``reachRules``), carried so a
    #: ruleset with a different grid does not need this class to know its number.
    space_feet: float

    @property
    def spaces(self) -> int:
        """Whole Spaces the reach covers — rounded **down**, since a half-Space of reach
        does not let a character strike into the next square."""
        if self.space_feet <= 0:
            return 0
        return int(self.feet // self.space_feet)

    @property
    def exact(self) -> bool:
        """Whether the feet are a whole number of Spaces."""
        if self.space_feet <= 0:
            return True
        return abs(self.feet - self.spaces * self.space_feet) < 1e-9


def _feet_text(feet: float) -> str:
    """A distance with no trailing ``.0`` — reach lands on whole feet almost always."""
    return f"{feet:.1f}".rstrip("0").rstrip(".")


def reach_text(reach: Reach) -> str:
    """``"3 spaces / 18 ft."`` — or ``"~7 spaces / 45 ft."`` when the Spaces are rounded.

    Without the ``Reach:`` caption, so the System block can put it in a form label and
    the power's game-term line can put it inline.
    """

    spaces = reach.spaces
    noun = "space" if spaces == 1 else "spaces"
    tilde = "" if reach.exact else "~"
    return f"{tilde}{spaces} {noun} / {_feet_text(reach.feet)} ft."


def size_reach_feet(size_rank: int, game_data: GameData) -> float:
    """The reach a size category alone gives, in feet.

    The Size Table's ``reach`` column counted in Spaces, converted at
    ``reachRules.spaceDistanceRank``. A size at or below ``reachRules.baselineSizeRank``
    contributes **nothing**: that baseline Space is the character's own body — the reach
    every character already has and which the readout treats as its zero — so counting it
    again beside a stretched limb would charge for it twice. Above the baseline the row's
    whole tabulated reach counts, because those rows are stated as totals.
    """

    measurements = game_data.measurements
    row = measurements.size_row(size_rank)
    baseline = measurements.size_row(measurements.reach_rules.baseline_size_rank)
    if row is None or baseline is None or row.reach <= baseline.reach:
        return 0.0
    return row.reach * measurements.space_feet()


def reach_extension_feet(char: Character, game_data: GameData) -> float:
    """How much further the character's *active* reach-extending effects stretch, in feet.

    Elongation in the base ruleset, but nothing here names it: an effect extends reach
    because ``effects.json`` gave it a ``reach`` block (:class:`~..data_loader.EffectReach`),
    and a mod's own one is picked up on the same terms. Each contributes its rank times
    the real distance at the rank it declares — the distances are summed, never the ranks
    (``measurements.json``'s ``doNotAddRanksNote``).

    The rank read is the one the effect is currently **dialled** to, the same
    :func:`~.runtime.effect_current_rank` :func:`size_shift` reads, so an Elongation
    rationed by a Dynamic array's point pool reaches only as far as its share bought.
    """

    feet = 0.0
    for power in live_powers(char.powers):
        for effect in power.effects:
            base = next((e for e in game_data.effects if e.id == effect.effect_id), None)
            if base is None or base.reach is None:
                continue
            if not effect_is_active(power, effect, base, game_data, char):
                continue
            rank = effect_current_rank(effect, game_data, char)
            feet += rank * game_data.measurements.distance_ft(base.reach.per_rank_distance_rank)
    return feet


def character_reach(char: Character, game_data: GameData) -> Reach:
    """How far the character can make close attacks right now.

    Their *effective* size's tabulated reach — so a Growth moves it and a Shrinking cuts
    it — plus whatever their active reach-extending effects stretch on top.
    """

    return Reach(
        feet=size_reach_feet(effective_size_rank(char, game_data), game_data)
        + reach_extension_feet(char, game_data),
        space_feet=game_data.measurements.space_feet(),
    )


def base_character_reach(char: Character, game_data: GameData) -> Reach:
    """The reach the character has with nothing switched on — their bought size alone.

    What :func:`character_reach` is compared against to decide whether anything has
    *changed* it, which is the only condition the System block shows the row under: a
    Medium character reaching one Space is the baseline everything else is stated
    against, and saying so on every sheet would be noise.
    """

    return Reach(
        feet=size_reach_feet(base_size_rank(char, game_data), game_data),
        space_feet=game_data.measurements.space_feet(),
    )


def reach_is_altered(char: Character, game_data: GameData) -> bool:
    """Whether any source has moved the character's reach off what their bought size gives.

    Two questions, because reach can change without the feet moving. The feet are the
    usual one — a Growth or an Elongation lengthens them. The second catches the other
    direction: a Shrinking down to a size the table gives *no* reach at all reads as zero
    feet either way, since a baseline-size character contributes zero too
    (:func:`size_reach_feet`), and losing your reach entirely is exactly the kind of
    change the readout exists to announce.
    """

    if character_reach(char, game_data).feet != base_character_reach(char, game_data).feet:
        return True
    measurements = game_data.measurements
    now = measurements.size_row(effective_size_rank(char, game_data))
    base = measurements.size_row(base_size_rank(char, game_data))
    return now is not None and base is not None and now.reach != base.reach

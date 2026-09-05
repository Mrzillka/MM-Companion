"""Derived traits: effective ability, skill/resistance totals, defense, initiative."""

from __future__ import annotations

from dataclasses import dataclass, field

from ..character import AdvantageSelection, Character
from ..components import APPLY_BONUS
from ..data_loader import GameData, Resistance
from .advantages import advantage_by_name
from .appliers import (
    CATEGORY_ABILITY,
    CATEGORY_ADVANTAGE,
    CATEGORY_RESISTANCE,
    CATEGORY_SKILL,
    GROUP_EFFORT,
    GROUP_INTRINSIC,
    GROUP_POWERS,
    SPECIALIZED_ROW_MARKER,
    STACK_SUM,
    TraitBonus,
    TraitContribution,
    resolve_bonuses,
    resolve_contributions,
    skill_for_row,
    split_trait_key,
)
from .build_cache import build_scoped
from .conditions import ConditionEffect, condition_scope_penalty
from .runtime import equipment_contributions, power_contributions
from .size import size_contributions


@build_scoped
def granted_advantages(
    char: Character,
    game_data: GameData,
    contributions: tuple[TraitContribution, ...] | None = None,
) -> dict[str, TraitBonus]:
    """Advantages a power or an item *grants*, ``name ->`` the ranks it stands at.

    An Enhanced Trait may raise an advantage as readily as an ability (the rules' own
    Berserker Rage is *Enhanced Advantage: Fearless 2* alongside Enhanced Strength), so
    an advantage is a trait like any other here — it simply totals nothing, which is why
    :data:`~.appliers.CATEGORY_ADVANTAGE` sits outside
    :data:`~.appliers.NUMERIC_CATEGORIES`.

    A granted advantage is **paid for by the power that grants it**. It is deliberately
    absent from :func:`~.costs.advantage_points_spent` and from the shared Heroic budget
    (:func:`~.advantages.heroic_advantage_ranks`), both of which read the *bought*
    ``char.advantages`` and nothing else — charging for it twice is exactly the bug the
    trait boosts avoid on the ability side.

    Gathers the same sources :func:`trait_contributions` does, less the advantages
    themselves: an advantage cannot grant an advantage, and reading its own output back
    would be a loop rather than a rule. Pass ``contributions`` to reuse a gather already
    in hand — :func:`trait_contributions` does, since every derived total on the sheet
    goes through it and walking the powers twice per lookup would be paid for on every
    skill row.

    :func:`~.build_cache.build_scoped` for callers that have *no* gather in hand and so
    ask the bare two-argument question — :func:`all_advantage_selections` is one, and it
    sits under the initiative readout. Without the memo that readout walked the whole
    build twice per call (once for the ability, once for the bonus), inside a scope
    where every other derived number costs nothing. The three-argument call passes
    straight through, as ``build_scoped`` does for any narrower question.
    """

    if contributions is None:
        contributions = (
            size_contributions(char, game_data)
            + power_contributions(char, game_data)
            + equipment_contributions(char, game_data)
        )
    return dict(resolve_bonuses(contributions).get(CATEGORY_ADVANTAGE, {}))


def _advantage_skill_contribution(
    advantage, rank: int, parameter: str, source: str
) -> TraitContribution | None:
    """The skill bonus one advantage at one rank grants, or ``None`` when it grants none.

    Data-driven: an advantage contributes when it carries a ``skill_bonus_per_rank``, on
    the skill its ``skill_bonus_target`` names — or, lacking one, the skill ``parameter``
    chose. So a mod adds a granting advantage without touching this resolver.
    """

    if advantage is None or not advantage.skill_bonus_per_rank:
        return None
    target = advantage.skill_bonus_target or parameter
    if not target:
        return None
    return TraitContribution(
        amount=advantage.skill_bonus_per_rank * rank,
        stat=target,
        category=CATEGORY_SKILL,
        source=source,
        stacking=STACK_SUM,
        group=GROUP_POWERS,
        kind=APPLY_BONUS,
    )


def advantage_contributions(
    char: Character,
    game_data: GameData,
    granted: dict[str, TraitBonus] | None = None,
) -> tuple[TraitContribution, ...]:
    """Every trait bonus the character's advantages grant — bought and granted alike.

    Advantages are bought with Power Points like powers, so they join the same
    stacking group and add on top of a power's boost rather than competing with it.

    A **granted** advantage (:func:`granted_advantages`) grants whatever it would have
    granted if bought, so an Enhanced Advantage naming one that carries a skill bonus
    reaches the skill total rather than stopping at a name on the sheet. Its subject is
    the qualifier on its own trait key (``"Skill Mastery::Stealth"``), which is what lets
    a granted advantage whose target is the player's choice chain like a bought one.

    ``granted`` may be passed to reuse a :func:`granted_advantages` result already in
    hand; it is resolved here when it isn't.
    """

    contributions: list[TraitContribution] = []
    for selection in char.advantages:
        contribution = _advantage_skill_contribution(
            advantage_by_name(game_data, selection.name),
            selection.rank,
            selection.parameter,
            selection.name,
        )
        if contribution is not None:
            contributions.append(contribution)

    if granted is None:
        granted = granted_advantages(char, game_data)
    for key, bonus in granted.items():
        name, parameter = split_trait_key(key)
        contribution = _advantage_skill_contribution(
            advantage_by_name(game_data, name),
            bonus.amount,
            # A granted advantage *can* carry the subject its key names, so one bought
            # for a skill ("Skill Mastery::Stealth") chains onto that skill exactly as
            # the bought one would. Unqualified keys still pass "" and chain only when
            # the advantage names its own target.
            parameter,
            # Named for the pair: the advantage is what grants the bonus, the power is
            # what put the advantage there, and neither half explains the row alone.
            f"{name} ({', '.join(bonus.sources)})" if bonus.sources else name,
        )
        if contribution is not None:
            contributions.append(contribution)
    return tuple(contributions)


def granted_advantage_selections(
    char: Character,
    game_data: GameData,
    granted: dict[str, TraitBonus] | None = None,
) -> tuple[tuple[AdvantageSelection, str], ...]:
    """Granted advantages as ``(selection, granting source)`` pairs, ready to render.

    :func:`granted_advantages` keyed by the raw trait key; this splits that key into the
    advantage's name and the subject it was granted for
    (``"Improved Critical::Sword"`` → ``Improved Critical`` for ``Sword``), so the sheet
    prints a granted advantage exactly as it prints a bought one. The split lives here
    rather than in the block, because the key format is the rules layer's and a second
    reading of it in the UI is a second thing to keep in step.

    These selections are **not** part of :attr:`Character.advantages` and are never
    written back to it: the power paid for them, so they cost no advantage points and
    draw on no Heroic budget.
    """

    if granted is None:
        granted = granted_advantages(char, game_data)
    return tuple(
        (
            AdvantageSelection(name=name, rank=bonus.amount, parameter=parameter),
            ", ".join(bonus.sources),
        )
        for name, parameter, bonus in (
            (*split_trait_key(key), bonus) for key, bonus in granted.items()
        )
    )


def all_advantage_selections(
    char: Character,
    game_data: GameData,
    granted: dict[str, TraitBonus] | None = None,
) -> tuple[AdvantageSelection, ...]:
    """Every advantage standing on the sheet — the bought ones and the granted ones.

    An advantage's *mechanics* do not care which currency paid for it: an Enhanced
    Advantage: Improved Initiative 2 moves the initiative modifier exactly as two
    bought ranks would. So the resolvers that read an advantage's data fields read
    this rather than :attr:`Character.advantages`, which holds only what the player
    bought. Cost and budget math is the deliberate exception — it reads the bought
    list alone, because the power already paid (see :func:`granted_advantages`).
    """

    return tuple(char.advantages) + tuple(
        selection for selection, _source in granted_advantage_selections(char, game_data, granted)
    )


def skill_row_exists(char: Character, game_data: GameData, row_id: str) -> bool:
    """Whether ``row_id`` names a skill row this character actually has.

    A row exists when the character bought ranks in it, when the focus or specialized
    pool it names is declared on the sheet (even at zero ranks), or when it is an
    unfocused skill from the catalog — every character can try Perception at +AWE. A
    focus nobody took is not a row: a pin to it should say so rather than quietly reading
    as the bare ability, and a power granting it has to bring its own row along
    (:func:`granted_skill_rows`).
    """

    if not row_id:
        return False
    if row_id in char.skill_ranks:
        return True
    base, qualifier = split_trait_key(row_id)
    if qualifier:
        return qualifier in char.focuses.get(base, []) or qualifier.removeprefix(
            SPECIALIZED_ROW_MARKER
        ) in char.specializations.get(base, [])
    return any(s.name == row_id and not s.focused for s in game_data.skills)


def granted_skill_rows(char: Character, game_data: GameData) -> dict[str, TraitBonus]:
    """Skill *rows* a power or item grants that the character has no row of its own for.

    An Enhanced Trait may name a focus or specialized pool the sheet does not carry —
    *Expertise: Stealth* on a hero who bought no Expertise — and that row has nowhere to
    land: the Skills block builds its rows from :attr:`Character.focuses` and
    :attr:`Character.specializations`, so the boost would be paid for and invisible.
    These are the rows the block has to grow for itself.

    Only *qualified* keys can be orphans. A bonus on a bare skill name reaches every row
    of that skill, and a skill always has at least the row the catalog gives it.
    """

    bonuses = trait_bonuses(char, game_data).get(CATEGORY_SKILL, {})
    return {
        row_id: bonus
        for row_id, bonus in bonuses.items()
        if split_trait_key(row_id)[1]
        and not skill_row_exists(char, game_data, row_id)
        and skill_for_row(game_data, row_id) is not None
    }


#: How :attr:`~mm_companion.core.character.Character.extra_effort` spells one target:
#: the contribution category, then the trait key it lands on.
EFFORT_KEY_SEP = ":"

#: What the sheet names as the source of a pushed rank.
EFFORT_SOURCE = "Extra Effort"


def effort_key(category: str, stat: str) -> str:
    """The :attr:`~mm_companion.core.character.Character.extra_effort` key for one target."""

    return f"{category}{EFFORT_KEY_SEP}{stat}"


def effort_contributions(char: Character, game_data: GameData) -> tuple[TraitContribution, ...]:
    """Every rank the character has pushed into a **trait** with Extra Effort.

    The rank increase may name an effect, "your Strength rank for either Damage or
    Lifting", or "your movement Speed rank in one mode of movement you have" (p21). An
    effect's push lives on the effect; the other two are an ability and a derived
    readout, so they are stored on the character
    (:attr:`~mm_companion.core.character.Character.extra_effort`) and arrive here.

    They land in :data:`~.appliers.GROUP_EFFORT`, which is *added* on top of whatever the
    build already nets rather than weighed against it: Extra Effort's benefits "can even
    increase your ranks or bonuses beyond the normal Power Level limits", so a Strength
    pushed by 1 is one more than the character had, however they came to have it.

    ``game_data`` is unused today and taken anyway, so a ruleset that wants to filter or
    rename what may be pushed has the door open without every caller changing.
    """

    del game_data  # see the docstring
    return tuple(
        TraitContribution(
            amount=int(ranks),
            stat=key.partition(EFFORT_KEY_SEP)[2],
            category=key.partition(EFFORT_KEY_SEP)[0],
            source=EFFORT_SOURCE,
            stacking=STACK_SUM,
            group=GROUP_EFFORT,
        )
        for key, ranks in char.extra_effort.items()
        if ranks and EFFORT_KEY_SEP in key
    )


@build_scoped
def trait_contributions(char: Character, game_data: GameData) -> tuple[TraitContribution, ...]:
    """Every stat contribution standing on the sheet, in the order the sheet grants them.

    The one place the derived totals gather what is raising a trait: the character's
    size (:func:`~.size.size_contributions`), then the active powers
    (:func:`~.runtime.power_contributions`), then the advantages
    (:func:`advantage_contributions`) — the bought ones and the ones a power granted —
    then the worn gear (:func:`~.runtime.equipment_contributions`), and last whatever the
    character has pushed with Extra Effort (:func:`effort_contributions`). Conditions are
    deliberately absent — they are a display-only overlay and never part of the build.

    Order matters twice over. Size comes first because it is the one thing here nobody
    bought: it sits in :data:`~.appliers.GROUP_INTRINSIC` and is added on top of
    whatever wins, so where it appears changes no number — only which source a tooltip
    names first, and what the creature *is* reads first. Of the rest, the powers and
    advantages are the Power-Point group and *sum*, so a sheet with no equipment nets
    exactly what it always did; the gear arrives last in its own group, where the
    resolver takes the better of the two rather than adding them, and a tie goes to
    whichever group was seen first — the powers.

    Gathers the whole build every call, and sits under every derived number the sheet
    prints — which is why it is :func:`~.build_cache.build_scoped`: inside a
    :func:`~.build_cache.stable_build` the answer is worked out once and handed to the
    rest of the pass, and outside one it recomputes exactly as it always did. The
    returned tuple is shared within a scope, so treat it as read-only.
    """

    size = size_contributions(char, game_data)
    powers = power_contributions(char, game_data)
    equipment = equipment_contributions(char, game_data)
    # Gathered once and handed on: the advantages resolver needs this very same set to
    # find the advantages a power granted, and this function sits on the path of every
    # derived total the sheet prints — gathering twice would be paid for per skill row.
    granted = granted_advantages(char, game_data, size + powers + equipment)
    # Effort last, and in a group of its own that never competes: it is added on top of
    # whatever the bought groups netted, which is what "beyond the normal Power Level
    # limits" means. It is deliberately *not* offered to `granted_advantages` — straining
    # cannot grant you an advantage.
    return (
        size
        + powers
        + advantage_contributions(char, game_data, granted)
        + equipment
        + effort_contributions(char, game_data)
    )


@build_scoped
def trait_bonuses(char: Character, game_data: GameData) -> dict[str, dict[str, TraitBonus]]:
    """The net bonus on every trait, grouped ``category -> {key: TraitBonus}``.

    :func:`trait_contributions` run through the stacking resolver. Unlike
    :func:`~.runtime.power_trait_bonuses` (the powers-only view the enhancement columns
    show) this is the sheet-wide number, and it is what every derived total below
    reads.

    Scoped like the gather beneath it, and worth scoping separately: the resolver is
    not free either, and this is what every ability, resistance and skill lookup goes
    through. The returned mapping is shared within a scope — read it, don't write it.
    """

    return resolve_bonuses(trait_contributions(char, game_data))


def _trait_bonus(
    char: Character, game_data: GameData, category: str, key: str
) -> TraitBonus | None:
    """The net bonus standing on one trait, or ``None`` when there is none."""

    return trait_bonuses(char, game_data).get(category, {}).get(key)


def _resistance(game_data: GameData, key: str) -> Resistance | None:
    for res in game_data.resistances:
        if res.key == key:
            return res
    return None


def effective_ability(char: Character, game_data: GameData, key: str) -> int:
    """An ability's value for all derived math: its bought rank plus any power bonus.

    This is the value skills, resistances, initiative, and the like read, so an
    Enhanced-Trait boost to an ability flows through the whole sheet. The point cost
    (:func:`power_points_spent`) still counts only the *bought* rank — the boost is
    paid for by the power itself.
    """

    bonus = _trait_bonus(char, game_data, CATEGORY_ABILITY, key)
    return char.abilities.get(key, 0) + (bonus.amount if bonus else 0)


def _specialization_base_ranks(char: Character, row_id: str) -> tuple[TraitContribution, ...]:
    """A specialized pool's *parent* skill ranks, as a contribution standing on the pool.

    A narrow pool is bought on top of the skill, not instead of it: a hero with Stealth
    10 who buys 3 ranks of ``Stealth::spec::Hiding`` hides at 13, and the pool is the
    cheap way to be *better at one thing* than the skill already makes them. Nothing is
    charged twice — each pool is still its own rank pool at its own rate
    (:func:`~.trait_rates.skill_row_rate`); only what those ranks are *worth* stacks.

    Expressed as a contribution rather than as an addend in :func:`skill_total` so the
    Skills block's "+" column can name it: the row's columns sum to its total, and a
    total that quietly held ten ranks the row never shows is exactly what would read as
    a bug. It lands in :data:`~.appliers.GROUP_INTRINSIC`, which does not compete —
    ranks the character bought are not a bonus that a power or a suit of armour can
    supersede, nor one they should be weighed against.

    A focus is not this: a focused skill has no shared pool for a focus to add to, so
    only the ``spec::`` marker qualifies, and a skill with no ranks contributes nothing
    rather than a ``+0`` the column would have to draw.
    """

    base, qualifier = split_trait_key(row_id)
    if not qualifier.startswith(SPECIALIZED_ROW_MARKER):
        return ()
    ranks = char.skill_ranks.get(base, 0)
    if not ranks:
        return ()
    return (
        TraitContribution(
            amount=ranks,
            stat=row_id,
            category=CATEGORY_SKILL,
            source=f"{base} ranks",
            stacking=STACK_SUM,
            group=GROUP_INTRINSIC,
        ),
    )


def skill_bonus(char: Character, game_data: GameData, row_id: str) -> TraitBonus | None:
    """Every *outside* bonus standing on one skill row, or ``None`` when there is none.

    A skill row's bonus is never typed in — it is granted by something else on the
    sheet, and the sources are summed here so the view can show one number and name
    what produced it:

    * powers — an active Enhanced-Trait-style boost naming the skill
      (:func:`~.runtime.power_trait_bonuses`), which applies to the skill's every row
      (each focus and specialized pool included);
    * advantages — any advantage carrying a ``skill_bonus_per_rank``, times its bought
      rank, on the skill its ``skill_bonus_target`` names (or, lacking one, the skill
      the selection's ``parameter`` chose);
    * the parent skill's own ranks, on a *specialized* row — the one contributor the
      player did buy, since a narrow pool adds to the skill rather than replacing it
      (:func:`_specialization_base_ranks`).

    The first two are data-driven, so a mod adds a new granting advantage or effect without
    touching this resolver. Conditions are deliberately *not* folded in here: they are
    display-only, never part of the build. The sheet's "+" column shows both kinds
    netted together — see :func:`skill_modifiers`.

    A skill's contributions are gathered by *either* name a source may have used —
    the base skill (a power's boost reaches every row of it, focuses and specialized
    pools included) or this exact row (an advantage aimed at one focus) — and then
    netted by the stacking resolver, together with the parent skill's own ranks when
    the row is a specialized pool (:func:`_specialization_base_ranks`).
    """

    skill = skill_for_row(game_data, row_id)
    if skill is None:
        return None

    names = {skill.name, row_id}
    gathered = [
        c
        for c in trait_contributions(char, game_data)
        if c.category == CATEGORY_SKILL and c.stat in names
    ]
    return resolve_contributions(gathered + list(_specialization_base_ranks(char, row_id)))


def _skill_scope_keys(row_id: str) -> set[str]:
    """The keys a condition may name to reach one skill row.

    Either the row itself (a focus or specialized pool) or its base skill — an Impaired
    (Stealth) hits every Stealth row.
    """

    return {row_id, row_id.split(":", 1)[0].strip()}


@dataclass(frozen=True)
class SkillModifiers:
    """Every flat modifier standing on one skill row — what the sheet's "+" column shows.

    ``amount`` is their net signed sum: the granted bonuses (:func:`skill_bonus` — part
    of the build, so already inside :func:`skill_total`) plus the conditions' flat
    penalty (display-only, never in the build). Today those are the only two kinds;
    another source folds in here, and the column shows the net without the view
    learning where any of it came from.

    ``grants`` and ``condition`` stay whole so the view can tint and explain the cell:
    a row carrying only grants reads as a boost, any condition marks it a penalty, and
    ``condition`` also holds the *override* half of the overlay (halve/zero — not a flat
    modifier, so it lands on the total instead) and the ids that decide strikethrough.
    """

    amount: int = 0
    grants: TraitBonus | None = None
    condition: ConditionEffect = field(default_factory=ConditionEffect)

    @property
    def has_flat_modifier(self) -> bool:
        """Whether anything shifts this row by a flat amount, so the column has a
        number to show.

        ``False`` for a row modified only by an override (a Debilitated row reads 0
        however many ranks were bought): that is not a modifier, and showing it as one
        would misreport it.
        """

        return self.grants is not None or bool(self.condition.delta)


def skill_modifiers(char: Character, game_data: GameData, row_id: str) -> SkillModifiers:
    """The net of every flat modifier on one skill row (:class:`SkillModifiers`).

    The sheet's "+" column is a read-out of this: a player never types a skill modifier
    in, it is granted or imposed by something else on the sheet — a power or advantage
    (:func:`skill_bonus`) or a condition scoped to the skill
    (:func:`condition_scope_penalty`).
    """

    grants = skill_bonus(char, game_data, row_id)
    condition = condition_scope_penalty(char, game_data, _skill_scope_keys(row_id))
    return SkillModifiers(
        amount=(grants.amount if grants else 0) + condition.delta,
        grants=grants,
        condition=condition,
    )


def skill_total(char: Character, game_data: GameData, row_id: str) -> int:
    """``ability value + skill ranks + outside bonuses`` for one skill row.

    The ability value is the *effective* one (:func:`effective_ability`), and the
    bonuses are the granted ones (:func:`skill_bonus`), so an Enhanced-Trait boost to
    either the linked ability or the skill itself shows up in the total.

    On a specialized row the skill's own ranks are among those bonuses
    (:func:`_specialization_base_ranks`): a narrow pool is bought *on top of* the
    skill, so Stealth 10 with 3 ranks of ``Stealth::spec::Hiding`` hides at 13.

    This is the *build* value. A condition scoped to the skill does not move it — the
    view overlays that on top (:func:`skill_modifiers`), so a penalised roll never
    rewrites what the character bought.
    """

    skill = skill_for_row(game_data, row_id)
    ability_key = skill.ability if skill else ""
    total = effective_ability(char, game_data, ability_key) + char.skill_ranks.get(row_id, 0)
    bonus = skill_bonus(char, game_data, row_id)
    return total + (bonus.amount if bonus else 0)


def _resistance_base_trait(game_data: GameData, key: str) -> tuple[str, str]:
    """The ``(category, key)`` of the trait a resistance derives from, or ``("", "")``.

    Fortitude and Toughness derive from Stamina and Will from Awareness — *abilities* —
    while Dodge derives from the Defense combat trait, which is itself a (derived)
    resistance, and Defense derives from nothing at all. Both :func:`resistance_base`
    and :func:`resistance_base_bonus` have to draw that same distinction, and a
    resistance whose base moved while the tint explaining it did not is exactly what
    two copies of this would produce.
    """

    res = _resistance(game_data, key)
    base_key = res.ability if res else ""
    if not base_key:
        return "", ""
    if any(a.key == base_key for a in game_data.abilities):
        return CATEGORY_ABILITY, base_key
    if any(r.key == base_key for r in game_data.resistances):
        return CATEGORY_RESISTANCE, base_key
    return "", ""


def resistance_base(char: Character, game_data: GameData, key: str) -> int:
    """The trait a resistance derives from, before its bought ranks and power boosts.

    Fortitude and Toughness derive from Stamina, Will from Awareness — an *ability*,
    read at its effective value (:func:`effective_ability`). Dodge instead derives
    from the Defense combat trait, which is itself a (derived) resistance, so the base
    can be another resistance's total. A resistance with no linked trait (Defense
    itself) has base 0. This is the value a "no ranks bought" resistance equals, and
    the Ability column of the Resistances block shows it.
    """

    category, base_key = _resistance_base_trait(game_data, key)
    if category == CATEGORY_ABILITY:
        return effective_ability(char, game_data, base_key)
    if category == CATEGORY_RESISTANCE:
        return resistance_total(char, game_data, base_key)
    return 0


def resistance_base_bonus(char: Character, game_data: GameData, key: str) -> TraitBonus | None:
    """What is raising the trait a resistance derives from, or ``None`` when nothing is.

    A resistance's base is someone else's total, so an Enhanced Trait on *Stamina*
    raises Toughness without touching a rank anyone bought. The sheet says which of
    the three numbers a source moved by tinting the column it moved — this is the one
    behind the Ability column, and :func:`_trait_bonus` on the resistance itself
    (Protection) is the one behind the Total.
    """

    category, base_key = _resistance_base_trait(game_data, key)
    if not category:
        return None
    return _trait_bonus(char, game_data, category, base_key)


def resistance_total(char: Character, game_data: GameData, key: str) -> int:
    """A resistance's total: its derived base plus the bought and power ranks.

    The base is the linked trait's value (:func:`resistance_base`) — Stamina for
    Toughness/Fortitude, Awareness for Will, the Defense trait for Dodge; derived
    resistances with no linked trait (Defence itself) have base 0. On top of that sit
    the ranks bought above the base and any power boost (Protection → Toughness). A
    power that raises the linked trait therefore raises the total too.
    """

    base = resistance_base(char, game_data, key)
    bought = char.resistances.get(key, 0)
    bonus = _trait_bonus(char, game_data, CATEGORY_RESISTANCE, key)
    return base + bought + (bonus.amount if bonus else 0)


def defense_class(char: Character, game_data: GameData, key: str | None = None) -> int:
    """The DC an attacker must beat: ``base + defense rank`` (``docs/mm-core-mechanics.md`` §5).

    The base (10 in the core rules) and the default defence trait key both come from
    ``system.json`` (``defense_dc_base`` / ``trait_keys.defense``), so a mod can retune
    either without touching this resolver.
    """

    if key is None:
        key = game_data.system.trait_keys.defense
    return game_data.system.defense_dc_base + resistance_total(char, game_data, key)


def initiative_ability(char: Character, game_data: GameData) -> str:
    """The ability key initiative reads — Agility, unless Alternate Initiative swaps it.

    An advantage that offers an ``initiative_ability_choice`` (Alternate Initiative)
    and whose selection stored a valid choice in its ``parameter`` replaces the
    default with that mental ability. The first such advantage wins; without one,
    initiative uses ``system.json``'s ``default_initiative_ability`` (Agility).

    Reads :func:`all_advantage_selections`, so an Enhanced Advantage granting
    Alternate Initiative swaps the ability just as a bought one would.
    """

    for selection in all_advantage_selections(char, game_data):
        advantage = advantage_by_name(game_data, selection.name)
        if (
            advantage
            and advantage.initiative_ability_choice
            and selection.parameter in advantage.initiative_ability_choice
        ):
            return selection.parameter
    return game_data.system.default_initiative_ability


def initiative_advantage_bonus(char: Character, game_data: GameData) -> int:
    """Flat initiative bonus from advantages — Improved Initiative's +4 per rank.

    Data-driven: each advantage carrying an ``initiative_bonus_per_rank`` contributes
    that many points times its chosen rank, so the +4/rank number lives in
    ``advantages.json`` rather than here.

    Bought *and* power-granted alike (:func:`all_advantage_selections`): an Enhanced
    Advantage: Improved Initiative used to be a name on the sheet that moved no
    number, because this walked ``char.advantages`` and a granted advantage is
    deliberately never written back to it.
    """

    total = 0
    for selection in all_advantage_selections(char, game_data):
        advantage = advantage_by_name(game_data, selection.name)
        if advantage and advantage.initiative_bonus_per_rank:
            total += advantage.initiative_bonus_per_rank * selection.rank
    return total


def initiative_modifier(char: Character, game_data: GameData) -> int:
    """Initiative modifier (``docs/mm-core-mechanics.md`` §8): ability + advantage bonuses.

    The ability is the *effective* value (bought plus any Enhanced-Trait boost) of
    :func:`initiative_ability` — Agility, or the mental ability an Alternate Initiative
    advantage swaps in — plus :func:`initiative_advantage_bonus` (Improved Initiative).
    """

    ability = effective_ability(char, game_data, initiative_ability(char, game_data))
    return ability + initiative_advantage_bonus(char, game_data)

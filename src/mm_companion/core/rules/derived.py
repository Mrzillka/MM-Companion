"""Derived traits: effective ability, skill/resistance totals, defense, initiative."""

from __future__ import annotations

from dataclasses import dataclass, field

from ..character import Character
from ..data_loader import GameData, Resistance, Skill
from .advantages import advantage_by_name
from .conditions import ConditionEffect, condition_scope_penalty
from .runtime import TraitBonus, _trait_bonus


def _skill_for_row(game_data: GameData, row_id: str) -> Skill | None:
    """Resolve a skill *row id* to its :class:`Skill` record.

    A row id is either a skill name (non-focused) or ``"<Skill>: <focus>"`` for a
    focused instance; both map back to the same base skill.
    """

    by_name = {s.name: s for s in game_data.skills}
    if row_id in by_name:
        return by_name[row_id]
    base = row_id.split(":", 1)[0].strip()
    return by_name.get(base)


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

    bonus = _trait_bonus(char, game_data, "ability", key)
    return char.abilities.get(key, 0) + (bonus.amount if bonus else 0)


def skill_bonus(char: Character, game_data: GameData, row_id: str) -> TraitBonus | None:
    """Every *outside* bonus standing on one skill row, or ``None`` when there is none.

    A skill row's bonus is never bought or typed in — it is granted by something else
    on the sheet, and the sources are summed here so the view can show one number and
    name what produced it:

    * powers — an active Enhanced-Trait-style boost naming the skill
      (:func:`~.runtime.power_trait_bonuses`), which applies to the skill's every row
      (each focus and specialized pool included);
    * advantages — any advantage carrying a ``skill_bonus_per_rank``, times its bought
      rank, on the skill its ``skill_bonus_target`` names (or, lacking one, the skill
      the selection's ``parameter`` chose).

    Both are data-driven, so a mod adds a new granting advantage or effect without
    touching this resolver. Conditions are deliberately *not* folded in here: they are
    display-only, never part of the build. The sheet's "+" column shows both kinds
    netted together — see :func:`skill_modifiers`.
    """

    skill = _skill_for_row(game_data, row_id)
    if skill is None:
        return None

    amount = 0
    sources: tuple[str, ...] = ()
    power = _trait_bonus(char, game_data, "skill", skill.name)
    if power:
        amount += power.amount
        sources += power.sources
    for selection in char.advantages:
        advantage = advantage_by_name(game_data, selection.name)
        if advantage is None or not advantage.skill_bonus_per_rank:
            continue
        target = advantage.skill_bonus_target or selection.parameter
        if target not in (skill.name, row_id):
            continue
        amount += advantage.skill_bonus_per_rank * selection.rank
        sources += (advantage.name,)
    return TraitBonus(amount, sources) if sources else None


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

    This is the *build* value. A condition scoped to the skill does not move it — the
    view overlays that on top (:func:`skill_modifiers`), so a penalised roll never
    rewrites what the character bought.
    """

    skill = _skill_for_row(game_data, row_id)
    ability_key = skill.ability if skill else ""
    total = effective_ability(char, game_data, ability_key) + char.skill_ranks.get(row_id, 0)
    bonus = skill_bonus(char, game_data, row_id)
    return total + (bonus.amount if bonus else 0)


def resistance_base(char: Character, game_data: GameData, key: str) -> int:
    """The trait a resistance derives from, before its bought ranks and power boosts.

    Fortitude and Toughness derive from Stamina, Will from Awareness — an *ability*,
    read at its effective value (:func:`effective_ability`). Dodge instead derives
    from the Defense combat trait, which is itself a (derived) resistance, so the base
    can be another resistance's total. A resistance with no linked trait (Defense
    itself) has base 0. This is the value a "no ranks bought" resistance equals.
    """

    res = _resistance(game_data, key)
    base_key = res.ability if res else ""
    if not base_key:
        return 0
    if any(a.key == base_key for a in game_data.abilities):
        return effective_ability(char, game_data, base_key)
    if any(r.key == base_key for r in game_data.resistances):
        return resistance_total(char, game_data, base_key)
    return 0


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
    bonus = _trait_bonus(char, game_data, "resistance", key)
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
    """

    for selection in char.advantages:
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
    """

    total = 0
    for selection in char.advantages:
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

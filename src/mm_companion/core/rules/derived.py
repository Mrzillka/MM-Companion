"""Derived traits: effective ability, skill/resistance totals, defense, initiative."""

from __future__ import annotations

from ..character import Character
from ..data_loader import GameData, Resistance, Skill
from .advantages import advantage_by_name
from .runtime import _trait_bonus


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


def skill_total(char: Character, game_data: GameData, row_id: str) -> int:
    """``ability value + skill ranks + situational modifier`` for one skill row.

    The ability value is the *effective* one (:func:`effective_ability`), and a skill
    a power enhances also adds that power bonus, so an Enhanced-Trait boost to either
    the linked ability or the skill itself shows up in the total.
    """

    skill = _skill_for_row(game_data, row_id)
    ability_key = skill.ability if skill else ""
    ability_value = effective_ability(char, game_data, ability_key)
    total = ability_value + char.skill_ranks.get(row_id, 0) + char.skill_mods.get(row_id, 0)
    if skill:
        bonus = _trait_bonus(char, game_data, "skill", skill.name)
        if bonus:
            total += bonus.amount
    return total


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


def defense_class(char: Character, game_data: GameData, key: str = "DEF") -> int:
    """The DC an attacker must beat: ``10 + defense rank`` (``mm-core-mechanics.md`` §5)."""

    return 10 + resistance_total(char, game_data, key)


DEFAULT_INITIATIVE_ABILITY = "AGL"


def initiative_ability(char: Character, game_data: GameData) -> str:
    """The ability key initiative reads — Agility, unless Alternate Initiative swaps it.

    An advantage that offers an ``initiative_ability_choice`` (Alternate Initiative)
    and whose selection stored a valid choice in its ``parameter`` replaces the
    default Agility with that mental ability. The first such advantage wins; without
    one, initiative uses :data:`DEFAULT_INITIATIVE_ABILITY`.
    """

    for selection in char.advantages:
        advantage = advantage_by_name(game_data, selection.name)
        if (
            advantage
            and advantage.initiative_ability_choice
            and selection.parameter in advantage.initiative_ability_choice
        ):
            return selection.parameter
    return DEFAULT_INITIATIVE_ABILITY


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
    """Initiative modifier (``mm-core-mechanics.md`` §8): ability + advantage bonuses.

    The ability is the *effective* value (bought plus any Enhanced-Trait boost) of
    :func:`initiative_ability` — Agility, or the mental ability an Alternate Initiative
    advantage swaps in — plus :func:`initiative_advantage_bonus` (Improved Initiative).
    """

    ability = effective_ability(char, game_data, initiative_ability(char, game_data))
    return ability + initiative_advantage_bonus(char, game_data)

"""Size as a trait source: what the Size Table grants, and what it shifts."""

from __future__ import annotations

import pytest

from mm_companion.core.character import Character
from mm_companion.core.data_loader import load_game_data
from mm_companion.core.powers import Power, PowerEffectInstance
from mm_companion.core.rules import (
    base_size_rank,
    defense_class,
    effective_ability,
    effective_size,
    effective_size_rank,
    resistance_total,
    size_contributions,
    size_resistance_shift,
    size_skill_shift,
    size_trait_modifier,
    skill_total,
)
from mm_companion.core.rules.appliers import GROUP_INTRINSIC, STACK_SUM


@pytest.fixture
def data():
    return load_game_data()


def _char(data, size: str = "Medium") -> Character:
    char = Character.new_default(data)
    char.characteristics["size"] = size
    return char


def _growth(char: Character, rank: int, *, effect: str = "growth") -> Power:
    power = Power(name=effect.title(), effects=[PowerEffectInstance(effect, rank=rank)])
    power.activated = True
    char.powers.append(power)
    return power


# -- the rank itself -------------------------------------------------------------


def test_a_size_shift_is_relative_to_the_size_you_already_are(data) -> None:
    """A Small character growing two ranks is Large, not Huge."""
    char = _char(data, "Small")
    _growth(char, 2)

    assert base_size_rank(char, data) == -1
    assert effective_size_rank(char, data) == 1
    assert effective_size(char, data) == "Large"


def test_growing_and_shrinking_at_once_leaves_the_difference(data) -> None:
    char = _char(data, "Small")
    _growth(char, 2)
    _growth(char, 3, effect="shrinking")

    assert effective_size_rank(char, data) == -2
    assert effective_size(char, data) == "Tiny"


def test_a_shift_past_the_table_clamps_rather_than_extrapolating(data) -> None:
    """The one place the Size Table stops being linear."""
    char = _char(data, "Small")
    _growth(char, 10)

    assert effective_size_rank(char, data) == 9  # the rank is honest...
    assert effective_size(char, data) == "Awesome"  # ...the row it reads is clamped
    assert size_trait_modifier(char, data, "resistance", "TOUGHNESS") == 5


# -- what it contributes ---------------------------------------------------------


def test_a_medium_character_contributes_nothing_at_all(data) -> None:
    """Not "contributes zero" — nothing.

    A ``TraitBonus`` is a dataclass instance and therefore always truthy, so a zero
    would paint a green arrow on every Medium sheet's Defence and Toughness and switch
    the Skills block's "+" column on for everybody.
    """
    assert size_contributions(_char(data), data) == ()


def test_a_large_character_grants_one_contribution_per_non_zero_column(data) -> None:
    char = _char(data, "Large")

    by_stat = {c.stat: c for c in size_contributions(char, data)}

    assert by_stat["DEF"].amount == -1
    assert by_stat["TOUGHNESS"].amount == 1
    assert by_stat["Intimidation"].amount == 2
    assert by_stat["Stealth"].amount == -2
    # Strength is deliberately absent: the Damage column scales an effect's rank.
    assert "STR" not in by_stat


def test_every_contribution_is_intrinsic_and_names_the_size(data) -> None:
    for contribution in size_contributions(_char(data, "Huge"), data):
        assert contribution.group == GROUP_INTRINSIC
        assert contribution.stacking == STACK_SUM
        assert contribution.source == "Size (Huge)"


def test_size_reaches_the_totals_the_sheet_shows(data) -> None:
    char = _char(data, "Large")
    char.abilities["STA"] = 4
    char.abilities["AGL"] = 2
    char.skill_ranks["Stealth"] = 3

    assert resistance_total(char, data, "TOUGHNESS") == 5  # STA 4 + 1
    assert resistance_total(char, data, "DODGE") == -1  # through DEF
    assert defense_class(char, data) == 9  # 10 + DEF -1
    assert skill_total(char, data, "Stealth") == 3  # AGL 2 + 3 ranks - 2
    assert effective_ability(char, data, "STR") == 0  # untouched


def test_dodge_picks_its_size_modifier_up_through_defence(data) -> None:
    """The contribution is on DEF; Dodge derives from it, so it must not be doubled."""
    char = _char(data, "Huge")

    assert size_trait_modifier(char, data, "resistance", "DODGE") == 0
    assert size_resistance_shift(char, data, "DODGE") == -2
    assert size_resistance_shift(char, data, "TOUGHNESS") == 2


def test_the_paired_cap_shift_cancels_itself(data) -> None:
    """Which is why being large trips no Power Level warning on its own."""
    char = _char(data, "Colossal")
    pair = next(p for p in data.system.paired_caps if "DODGE" in p.traits)

    assert sum(size_resistance_shift(char, data, key) for key in pair.traits) == 0


def test_a_skill_cap_shift_follows_the_row_and_its_ability(data) -> None:
    char = _char(data, "Huge")

    assert size_skill_shift(char, data, "Intimidation") == 4
    assert size_skill_shift(char, data, "Stealth") == -4
    # Nothing maps onto an ability in the base ruleset, so an unlisted skill is flat.
    assert size_skill_shift(char, data, "Athletics") == 0
    assert size_skill_shift(char, data, "No Such Skill") == 0

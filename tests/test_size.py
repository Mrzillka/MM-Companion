"""Size as a trait source: what the Size Table grants, and what it shifts."""

from __future__ import annotations

import pytest

from mm_companion.core.character import Character
from mm_companion.core.data_loader import load_game_data
from mm_companion.core.powers import Power, PowerEffectInstance
from mm_companion.core.rules import (
    base_size_rank,
    defense_class,
    effect_current_rank,
    effective_ability,
    effective_size,
    effective_size_rank,
    power_total_cost,
    resistance_total,
    size_contributions,
    size_resistance_shift,
    size_skill_shift,
    size_steps,
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


# --- the rungs a size effect can be held at -------------------------------------------


def _steps(power, char, data):
    return size_steps(power, power.effects[0], char, data)


def test_the_ladder_names_the_size_reached_at_each_rank(data) -> None:
    """A rung is labelled by what the wielder *becomes*, not by what it cost."""
    char = _char(data)
    power = _growth(char, 3)

    assert [(s.rank, s.category) for s in _steps(power, char, data)] == [
        (1, "Large"),
        (2, "Huge"),
        (3, "Gargantuan"),
    ]
    # Full rank until something dials it down, so the top rung is the live one.
    assert [s.current for s in _steps(power, char, data)] == [False, False, True]


def test_the_ladder_is_read_against_the_wielder(data) -> None:
    """A Small character's Growth 2 climbs Medium → Large, as the sheet already said."""
    char = _char(data, "Small")
    power = _growth(char, 2)

    assert [s.category for s in _steps(power, char, data)] == ["Medium", "Large"]
    assert effective_size(char, data) == "Large"


def test_shrinking_walks_the_table_the_other_way(data) -> None:
    char = _char(data)
    power = _growth(char, 3, effect="shrinking")

    assert [s.category for s in _steps(power, char, data)] == ["Small", "Tiny", "Diminutive"]


def test_holding_a_rung_moves_the_whole_sheet(data) -> None:
    """The dialled rank is the one number everything else follows from."""
    char = _char(data)
    power = _growth(char, 3)
    char.abilities["STA"] = 2

    power.effects[0].current_rank = 1
    assert effective_size(char, data) == "Large"
    assert size_trait_modifier(char, data, "resistance", "TOUGHNESS") == 1
    assert resistance_total(char, data, "TOUGHNESS") == 3
    assert [s.current for s in _steps(power, char, data)] == [True, False, False]

    power.effects[0].current_rank = 3
    assert effective_size(char, data) == "Gargantuan"
    assert resistance_total(char, data, "TOUGHNESS") == 5


def test_a_rung_left_over_from_a_larger_bought_rank_clamps(data) -> None:
    """Editing Growth 3 down to 1 must not leave the effect running at 3."""
    char = _char(data)
    power = _growth(char, 3)
    power.effects[0].current_rank = 3

    power.effects[0].rank = 1
    assert effect_current_rank(power.effects[0]) == 1
    assert effective_size(char, data) == "Large"


def test_a_switched_off_effect_lights_no_rung(data) -> None:
    """The ladder says where the power *is*, and off is nowhere — not rank 1."""
    char = _char(data)
    power = _growth(char, 3)
    power.effects[0].current_rank = 2
    power.effects[0].toggled_on = False

    assert [s.current for s in _steps(power, char, data)] == [False, False, False]
    assert effective_size(char, data) == "Medium"
    # And the rung is remembered, so switching back on returns to where it was.
    power.effects[0].toggled_on = True
    assert effective_size(char, data) == "Huge"


def test_ranks_the_table_clamps_fold_into_one_rung(data) -> None:
    """Four buttons all reading 'Awesome' would be four ways to do the same thing."""
    char = _char(data, "Colossal")
    power = _growth(char, 3)
    power.effects[0].current_rank = 2

    steps = _steps(power, char, data)
    assert [(s.rank, s.last_rank, s.category) for s in steps] == [(1, 3, "Awesome")]
    # The dialled rank is inside that rung's span, so the one button is still lit.
    assert [s.current for s in steps] == [True]


def test_only_a_size_effect_has_rungs(data) -> None:
    char = _char(data)
    power = Power(name="Blast", effects=[PowerEffectInstance("damage", rank=5)])
    char.powers.append(power)

    assert _steps(power, char, data) == ()


def test_dialling_down_refunds_nothing(data) -> None:
    """What a power is worth is what it was bought at."""
    char = _char(data)
    power = _growth(char, 3)
    full = power_total_cost(power, data, char)

    power.effects[0].current_rank = 1
    assert power_total_cost(power, data, char) == full


def test_the_dialled_rank_is_runtime_and_survives_a_restore(data) -> None:
    """Like every other runtime flag: out of the save, carried across an undo."""
    char = _char(data)
    power = _growth(char, 3)
    power.effects[0].current_rank = 2

    assert "current_rank" not in power.effects[0].to_dict()

    snapshot = char.to_dict()
    char.restore(snapshot)
    assert char.powers[0].effects[0].current_rank == 2
    assert effective_size(char, data) == "Huge"

    # A plain load, with no runtime to carry, comes up at full rank.
    assert Character.from_dict(snapshot).powers[0].effects[0].current_rank is None

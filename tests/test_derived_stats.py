"""Derived base-info stats: speed lines, initiative, and effective size."""

from __future__ import annotations

from mm_companion.core.character import AdvantageSelection, Character
from mm_companion.core.data_loader import load_game_data
from mm_companion.core.powers import Power, PowerEffectInstance
from mm_companion.core.rules import (
    base_ground_speed_rank,
    effective_size,
    effective_size_rank,
    initiative_ability,
    initiative_advantage_bonus,
    initiative_modifier,
    size_shift,
    speed_columns,
    speed_lines,
)


def _char(data) -> Character:
    return Character.new_default(data)


# -- speed ---------------------------------------------------------------------


def test_base_speed_columns_double_along_the_distance_table() -> None:
    data = load_game_data()
    char = _char(data)
    rank = base_ground_speed_rank(char, data)
    assert rank == 1  # movement.json default ground rank

    walk, dash, run = speed_columns(rank, data)
    assert (walk, dash, run) == ("15 feet", "30 feet", "60 feet")


def test_speed_columns_convert_to_km_per_hour() -> None:
    data = load_game_data()
    # Flight rank 2 = 30 ft (8 m) per round: 8 m / 6 s = 4.8 km/h.
    walk, _dash, _run = speed_columns(2, data, metric=True)
    assert walk == "4.8 km/h"


def test_active_movement_power_adds_its_own_speed_line() -> None:
    data = load_game_data()
    char = _char(data)
    char.powers = [Power(name="Fly", effects=[PowerEffectInstance("flight", rank=2)])]

    lines = speed_lines(char, data)
    assert [(line.label, line.rank) for line in lines] == [("Base", 1), ("Flight 2", 2)]
    # The Flight line reproduces the design example 30 / 60 / 120 ft.
    assert speed_columns(2, data) == ("30 feet", "60 feet", "120 feet")


def test_switched_off_movement_power_drops_its_speed_line() -> None:
    data = load_game_data()
    char = _char(data)
    flight = Power(name="Fly", effects=[PowerEffectInstance("flight", rank=2)])
    flight.effects[0].toggled_on = False  # a Sustained toggle turned off
    char.powers = [flight]

    assert [line.label for line in speed_lines(char, data)] == ["Base"]


# -- initiative ----------------------------------------------------------------


def test_initiative_is_agility_by_default() -> None:
    data = load_game_data()
    char = _char(data)
    char.abilities["AGL"] = 3

    assert initiative_ability(char, data) == "AGL"
    assert initiative_modifier(char, data) == 3


def test_improved_initiative_adds_four_per_rank() -> None:
    data = load_game_data()
    char = _char(data)
    char.abilities["AGL"] = 2
    char.advantages.append(AdvantageSelection("Improved Initiative", rank=2))

    assert initiative_advantage_bonus(char, data) == 8
    assert initiative_modifier(char, data) == 2 + 8


def test_alternate_initiative_swaps_the_ability() -> None:
    data = load_game_data()
    char = _char(data)
    char.abilities["AGL"] = 2
    char.abilities["AWE"] = 5
    char.advantages.append(AdvantageSelection("Alternate Initiative", parameter="AWE"))

    assert initiative_ability(char, data) == "AWE"
    assert initiative_modifier(char, data) == 5


# -- effective size ------------------------------------------------------------


def test_size_unchanged_without_a_size_power() -> None:
    data = load_game_data()
    char = _char(data)
    assert size_shift(char, data) == 0
    assert effective_size(char, data) == "Medium"


def test_active_growth_increases_effective_size() -> None:
    data = load_game_data()
    char = _char(data)
    char.powers = [Power(name="Big", effects=[PowerEffectInstance("growth", rank=4)])]

    assert size_shift(char, data) == 4
    assert effective_size_rank(char, data) == 4
    assert effective_size(char, data) == "Colossal"


def test_growth_turned_off_returns_to_base_size() -> None:
    data = load_game_data()
    char = _char(data)
    growth = Power(name="Big", effects=[PowerEffectInstance("growth", rank=4)])
    growth.activated = False
    char.powers = [growth]

    assert size_shift(char, data) == 0
    assert effective_size(char, data) == "Medium"

"""What an effect's rank *means* on its card: distances, areas, masses, counts.

Headless. Each case is a number the book ties to an effect's rank and the card used to
leave out, or got wrong: Communication's Range is a band (same city, same continent),
not a distance; Environment's is the size of its area; Transmute moves mass rank −6;
Thrown starts at distance rank 2 + Strength; Teleport's Extended reaches ten further.
"""

from __future__ import annotations

import pytest

from mm_companion.core.character import Character
from mm_companion.core.data_loader import load_game_data
from mm_companion.core.powers import ModifierSelection, PowerEffectInstance
from mm_companion.core.rules import effect_stat_rows


@pytest.fixture(scope="module")
def data():
    return load_game_data()


def _rows(effect, data, char=None) -> dict[str, str]:
    return {row.label: row.value for row in effect_stat_rows(effect, data, char)}


@pytest.mark.parametrize("effect_id", ["communication", "remote_sensing"])
def test_a_band_range_replaces_the_distance_rather_than_doubling_it(data, effect_id) -> None:
    rows = effect_stat_rows(PowerEffectInstance(effect_id, rank=3), data)
    ranges = [row.value for row in rows if row.label == "Range"]
    assert ranges == ["Same continent"]


def test_communication_and_remote_sensing_say_who_can_notice_them(data) -> None:
    assert (
        "Perception DC 13" in _rows(PowerEffectInstance("communication", rank=3), data)["Overheard"]
    )
    assert "Insight DC 12" in _rows(PowerEffectInstance("remote_sensing", rank=2), data)["Noticed"]


def test_environment_reaches_an_area_its_rank_across(data) -> None:
    assert _rows(PowerEffectInstance("environment", rank=6), data)["Range"].startswith(
        "an area 500 feet across"
    )


def test_teleport_carries_more_with_increased_mass_and_extends_ten_ranks(data) -> None:
    teleport = PowerEffectInstance("teleport", rank=8)
    assert "mass rank 0" in _rows(teleport, data)["Carries"]
    assert "Extended" not in _rows(teleport, data)
    teleport.extras.append(ModifierSelection("increased_mass_teleport", rank=4))
    teleport.extras.append(ModifierSelection("extended"))
    rows = _rows(teleport, data)
    assert "mass rank 4" in rows["Carries"]
    assert rows["Extended"].startswith("250 miles")  # distance rank 18


def test_transmute_moves_six_mass_ranks_below_its_rank(data) -> None:
    assert "mass rank 4" in _rows(PowerEffectInstance("transmute", rank=10), data)["Affects"]


def test_thrown_starts_at_two_plus_strength(data) -> None:
    char = Character.new_default(data)
    char.abilities["STR"] = 3
    effect = PowerEffectInstance("damage", rank=10)
    effect.extras.append(ModifierSelection("thrown"))
    assert _rows(effect, data, char)["Distance"] == "rank 5"
    assert _rows(effect, data)["Distance"] == "rank 2"  # no wielder: the floor


@pytest.mark.parametrize(
    ("effect_id", "rank", "label", "expected"),
    [
        ("illusion", 6, "Size", "total size rank of 6"),
        ("obscure", 6, "Area", "distance rank 6"),
        ("postcognition", 3, "Uses", "3 per adventure"),
        ("precognition", 2, "Uses", "2 per adventure"),
        ("leaping", 14, "As movement", "speed rank 10"),
        ("extra_limbs", 3, "Limbs", "up to four"),
        ("create", 5, "Supports weight", "Strength 5"),
        ("move_object", 8, "Moves", "distance rank 8 minus"),
        ("healing", 6, "Disease / poison", "+6"),
        ("mind_reading", 8, "Contact", "surface thoughts"),
        ("insubstantial", 2, "In that form", "immune to direct physical damage"),
    ],
)
def test_the_rules_numbers_are_on_the_card(data, effect_id, rank, label, expected) -> None:
    assert expected in _rows(PowerEffectInstance(effect_id, rank=rank), data)[label]


def test_lifting_lifts_the_mass_at_strength_plus_its_rank(data) -> None:
    char = Character.new_default(data)
    char.abilities["STR"] = 4
    rows = _rows(PowerEffectInstance("lifting", rank=8), data, char)
    assert rows["Lifts"].startswith("50 tons")  # mass rank 12
    assert "distance rank 12 minus" in rows["Throws"]
    # No wielder, no Strength to add: the card says what to add rather than guessing 0.
    assert "your Strength rank + 8" in _rows(PowerEffectInstance("lifting", rank=8), data)["Lifts"]

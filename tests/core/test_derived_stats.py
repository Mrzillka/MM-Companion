"""Derived base-info stats: speed lines, initiative, and effective size."""

from __future__ import annotations

from mm_companion.core.character import AdvantageSelection, Character
from mm_companion.core.data_loader import load_game_data
from mm_companion.core.powers import Power, PowerEffectInstance
from mm_companion.core.rules import (
    base_ground_speed_rank,
    effective_size,
    effective_size_rank,
    ground_speed_rank,
    initiative_ability,
    initiative_advantage_bonus,
    initiative_modifier,
    movement_mode_lines,
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

    assert speed_columns(rank, data) == ("15 feet", "30 feet", "60 feet")
    # Running is a ground manoeuvre, so every other mode is a move and a dash.
    assert speed_columns(rank, data, ground=False) == ("15 feet", "30 feet")


def test_speed_columns_convert_to_km_per_hour() -> None:
    data = load_game_data()
    # Flight rank 2 = 30 ft (8 m) per round: 8 m / 6 s = 4.8 km/h.
    walk = speed_columns(2, data, metric=True)[0]
    assert walk == "4.8 km/h"


def test_active_movement_power_adds_its_own_speed_line() -> None:
    data = load_game_data()
    char = _char(data)
    char.powers = [Power(name="Fly", effects=[PowerEffectInstance("flight", rank=2)])]

    lines = speed_lines(char, data)
    assert [(line.label, line.rank) for line in lines] == [("Ground speed", 1), ("Flight", 2)]
    # A mode's line is named for the mode alone; the rank that fed it is on the hover.
    assert lines[1].sources == ("Flight 2",)
    assert speed_columns(2, data, ground=False) == ("30 feet", "60 feet")


def test_switched_off_movement_power_drops_its_speed_line() -> None:
    data = load_game_data()
    char = _char(data)
    flight = Power(name="Fly", effects=[PowerEffectInstance("flight", rank=2)])
    flight.effects[0].toggled_on = False  # a Sustained toggle turned off
    char.powers = [flight]

    assert [line.label for line in speed_lines(char, data)] == ["Ground speed"]


# -- specialised movement modes -------------------------------------------------


def _wall_crawler(tier: int) -> Power:
    return Power(
        name="Spider",
        effects=[
            PowerEffectInstance(
                "enhanced_movement",
                rank=4,
                config={"modes": [{"id": "wall_crawling", "tier": tier}]},
            )
        ],
    )


def test_movement_mode_moves_at_full_ground_speed_at_its_top_tier() -> None:
    data = load_game_data()
    char = _char(data)
    char.powers = [_wall_crawler(2)]

    lines = movement_mode_lines(char, data)
    # Tier 2 is full ground speed, so the mode's rank is the ground rank itself.
    assert [(line.label, line.rank) for line in lines] == [("Wall-Crawling 2", 1)]
    assert lines[0].note == "not vulnerable"


def test_a_relative_movement_mode_tracks_the_speed_the_character_really_moves_at() -> None:
    """ "Full ground speed" means the ground line, Speed power and all."""
    data = load_game_data()
    char = _char(data)
    char.powers = [_wall_crawler(2)]
    _movement_power(char, "speed", 3)

    assert ground_speed_rank(char, data) == base_ground_speed_rank(char, data) + 3
    assert [line.rank for line in movement_mode_lines(char, data)] == [
        ground_speed_rank(char, data)
    ]


def test_lower_tier_wall_crawling_is_a_rank_slower_and_leaves_you_vulnerable() -> None:
    data = load_game_data()
    char = _char(data)
    char.powers = [_wall_crawler(1)]

    (line,) = movement_mode_lines(char, data)
    assert line.rank == 0  # one distance rank below the ground rank of 1
    assert line.note == "vulnerable while climbing"


def test_a_mode_with_a_flat_speed_ignores_the_characters_ground_speed() -> None:
    data = load_game_data()
    char = _char(data)
    char.characteristics["size"] = "Huge"  # a size that shifts ground speed
    char.powers = [
        Power(
            name="Web-Slinging",
            effects=[
                PowerEffectInstance(
                    "enhanced_movement", rank=2, config={"modes": [{"id": "swinging", "tier": 1}]}
                )
            ],
        )
    ]

    # Swinging is a flat speed rank 2, not a multiple of how fast the character walks.
    assert [line.rank for line in movement_mode_lines(char, data)] == [2]


def test_modes_that_grant_no_speed_stay_out_of_the_movement_readout() -> None:
    data = load_game_data()
    char = _char(data)
    char.powers = [
        Power(
            name="Sure Feet",
            effects=[
                PowerEffectInstance(
                    "enhanced_movement",
                    rank=4,
                    config={
                        "modes": [
                            {"id": "safe_fall", "tier": 1},
                            {"id": "stable", "tier": 1},
                            {"id": "trackless", "tier": 1},
                            {"id": "slithering", "tier": 1},
                        ]
                    },
                )
            ],
        )
    ]

    # Only Slithering is a speed; the other three are capabilities, not rates.
    assert [line.label for line in movement_mode_lines(char, data)] == ["Slithering"]


def test_sense_qualities_are_not_movement_modes() -> None:
    data = load_game_data()
    char = _char(data)
    # Enhanced Senses lays its qualities out with the same `allocation` field type,
    # so only the effects that declare `affects: movement` may contribute a line.
    char.powers = [
        Power(
            name="Spider Sense",
            effects=[
                PowerEffectInstance(
                    "enhanced_senses",
                    rank=4,
                    config={
                        "senses": [
                            {"id": "danger_sense", "tier": 1},
                            {"id": "direction_sense", "tier": 1},
                        ]
                    },
                )
            ],
        )
    ]

    assert movement_mode_lines(char, data) == []


def test_switched_off_power_grants_no_movement_modes() -> None:
    data = load_game_data()
    char = _char(data)
    power = _wall_crawler(2)
    power.effects[0].toggled_on = False  # Enhanced Movement is a Sustained toggle
    char.powers = [power]

    assert movement_mode_lines(char, data) == []


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


def test_a_power_granted_improved_initiative_moves_the_number() -> None:
    """An Enhanced Advantage is an advantage, and advantages move initiative.

    The bonus used to read ``char.advantages``, which is the *bought* list and never
    holds a granted advantage — so Enhanced Trait: Improved Initiative 2 showed on the
    Advantages block and changed nothing.
    """
    data = load_game_data()
    char = _char(data)
    char.abilities["AGL"] = 1
    char.powers = [
        Power(
            name="Quickened",
            effects=[
                PowerEffectInstance(
                    "enhanced_trait",
                    rank=2,
                    config={"traits": [{"trait": "Improved Initiative", "ranks": 2}]},
                )
            ],
        )
    ]

    assert initiative_advantage_bonus(char, data) == 8
    assert initiative_modifier(char, data) == 1 + 8


def test_a_granted_improved_initiative_stops_when_its_power_does() -> None:
    data = load_game_data()
    char = _char(data)
    power = Power(
        name="Quickened",
        effects=[
            PowerEffectInstance(
                "enhanced_trait",
                rank=2,
                config={"traits": [{"trait": "Improved Initiative", "ranks": 2}]},
            )
        ],
    )
    power.activated = False
    char.powers = [power]

    assert initiative_advantage_bonus(char, data) == 0


def test_a_power_granted_alternate_initiative_swaps_the_ability() -> None:
    """The granted advantage carries its subject on its own trait key."""
    data = load_game_data()
    char = _char(data)
    char.abilities["AGL"] = 2
    char.abilities["INT"] = 6
    char.powers = [
        Power(
            name="Tactician",
            effects=[
                PowerEffectInstance(
                    "enhanced_trait",
                    rank=1,
                    config={"traits": [{"trait": "Alternate Initiative::INT", "ranks": 1}]},
                )
            ],
        )
    ]

    assert initiative_ability(char, data) == "INT"
    assert initiative_modifier(char, data) == 6


def test_a_granted_advantage_costs_no_advantage_points_for_initiative() -> None:
    """Reading granted advantages for initiative must not put them in the budget."""
    from mm_companion.core.rules import advantage_points_spent, heroic_advantage_ranks

    data = load_game_data()
    char = _char(data)
    char.powers = [
        Power(
            name="Quickened",
            effects=[
                PowerEffectInstance(
                    "enhanced_trait",
                    rank=2,
                    config={"traits": [{"trait": "Improved Initiative", "ranks": 2}]},
                )
            ],
        )
    ]

    assert initiative_advantage_bonus(char, data) == 8
    assert advantage_points_spent(char, data) == 0
    assert heroic_advantage_ranks(char, data) == 0


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


# --- The condition overlay on speed is resolved in core, not in the widget ------


def test_condition_speed_lines_slows_only_the_ground_line() -> None:
    from mm_companion.core.rules import apply_condition, condition_speed_lines

    data = load_game_data()
    char = Character.new_default(data)
    char.powers.append(Power(name="Flight", effects=[PowerEffectInstance("flight", rank=4)]))

    plain = condition_speed_lines(char, data)
    assert [line.rank_mod for line in plain] == [0] * len(plain)
    assert not any(line.immobilised for line in plain)

    apply_condition(char, "hindered", data)
    slowed = condition_speed_lines(char, data)
    # The ground line arrives already reduced, carrying the penalty for the UI to show.
    assert slowed[0].rank == plain[0].rank + slowed[0].rank_mod
    assert slowed[0].rank_mod < 0
    # Flight is untouched by a ground-speed condition.
    assert slowed[1].rank == plain[1].rank
    assert slowed[1].rank_mod == 0


def test_condition_speed_lines_flags_immobilised() -> None:
    from mm_companion.core.rules import apply_condition, condition_speed_lines

    data = load_game_data()
    char = Character.new_default(data)
    apply_condition(char, "immobile", data)

    lines = condition_speed_lines(char, data)
    assert lines[0].immobilised is True


def test_speed_lines_itself_carries_no_condition_overlay() -> None:
    """The plain build math stays condition-free — the overlay is a separate call."""
    from mm_companion.core.rules import apply_condition

    data = load_game_data()
    char = Character.new_default(data)
    before = speed_lines(char, data)
    apply_condition(char, "hindered", data)
    assert speed_lines(char, data) == before


# -- speed nets per mode ---------------------------------------------------------


def _movement_power(char: Character, effect: str, rank: int, *, name: str = "") -> Power:
    power = Power(
        name=name or f"{effect.title()} {rank}", effects=[PowerEffectInstance(effect, rank=rank)]
    )
    power.activated = True
    char.powers.append(power)
    return power


def test_two_flight_powers_are_one_flight_speed() -> None:
    """The readout is a line per *mode*, not a line per source."""
    data = load_game_data()
    char = _char(data)
    _movement_power(char, "flight", 4, name="Wings")
    _movement_power(char, "flight", 6, name="Jets")

    lines = speed_lines(char, data)
    assert [(line.label, line.rank) for line in lines] == [("Ground speed", 1), ("Flight", 10)]
    assert lines[1].sources == ("Flight 4", "Flight 6")


def test_the_speed_effect_feeds_the_ground_line_rather_than_a_line_of_its_own() -> None:
    data = load_game_data()
    char = _char(data)
    _movement_power(char, "speed", 4)

    lines = speed_lines(char, data)
    assert [line.label for line in lines] == ["Ground speed"]
    assert lines[0].rank == base_ground_speed_rank(char, data) + 4  # adds to walking
    assert lines[0].sources == ("Speed 4",)


def test_every_rank_of_speed_makes_the_character_faster() -> None:
    """The ground line is a sum: the first rank of Speed must not be swallowed."""
    data = load_game_data()
    char = _char(data)
    base = base_ground_speed_rank(char, data)
    _movement_power(char, "speed", 1)

    assert speed_lines(char, data)[0].rank == base + 1


def test_striding_grants_its_own_ranks_of_the_ground_mode() -> None:
    """Elongation's extra says it grants Speed, at the ranks of *Striding* bought.

    It is a ranked modifier — "+1 point per rank flat", capped at 5 — so a Striding 2
    on an Elongation 6 is two ranks of ground movement, not six.
    """
    from mm_companion.core.powers import ModifierSelection

    data = load_game_data()
    char = _char(data)
    power = _movement_power(char, "elongation", 6, name="Rubber Limbs")
    power.effects[0].extras.append(ModifierSelection("striding", rank=2))

    base = base_ground_speed_rank(char, data)
    line = speed_lines(char, data)[0]
    assert (line.rank, line.sources) == (base + 2, ("Rubber Limbs (Striding)",))

    power.activated = False  # the host's gate takes the grant with it
    assert speed_lines(char, data)[0].rank == base_ground_speed_rank(char, data)


def test_striding_is_capped_at_the_rank_the_ruleset_gives_it() -> None:
    from mm_companion.core.rules import MODIFIER_RANK_MAX, modifier_rank_cap

    data = load_game_data()
    catalog = data.modifier_catalog()

    assert modifier_rank_cap(catalog["striding"]) == 5
    # An unranked modifier is not bought in ranks at all; an uncapped one is only
    # bounded by the number a spin box has to stop at.
    assert modifier_rank_cap(catalog["accurate"]) == MODIFIER_RANK_MAX
    assert modifier_rank_cap(catalog["ranged"]) == 1


def test_normal_speed_cancels_the_shrinking_penalty() -> None:
    from mm_companion.core.powers import ModifierSelection

    data = load_game_data()
    char = _char(data)
    power = _movement_power(char, "shrinking", 2, name="Shrink")
    assert base_ground_speed_rank(char, data) == -1  # 1 - 2

    power.effects[0].extras.append(ModifierSelection("normal_speed_shrinking", rank=2))
    assert base_ground_speed_rank(char, data) == 1


def test_normal_speed_never_makes_anyone_faster_than_normal() -> None:
    """Lifting a penalty leaves you at your usual pace, not running."""
    from mm_companion.core.powers import ModifierSelection

    data = load_game_data()
    char = _char(data)
    power = _movement_power(char, "shrinking", 2, name="Shrink")
    power.effects[0].extras.append(ModifierSelection("normal_speed_shrinking", rank=6))

    assert base_ground_speed_rank(char, data) == 1


def test_a_small_character_keeps_their_own_penalty_through_normal_speed() -> None:
    """It cancels what the *power* imposed, not what the character already was."""
    from mm_companion.core.powers import ModifierSelection

    data = load_game_data()
    char = _char(data)
    char.characteristics["size"] = "Small"
    power = _movement_power(char, "shrinking", 2, name="Shrink")
    power.effects[0].extras.append(ModifierSelection("normal_speed_shrinking", rank=2))

    assert base_ground_speed_rank(char, data) == 0  # 1 - 3 + 2


def test_size_does_not_reach_strength() -> None:
    """The Damage column scales an effect's rank; it is not a Strength bonus."""
    from mm_companion.core.rules import effective_ability

    data = load_game_data()
    char = _char(data)
    char.abilities["STR"] = 3
    char.characteristics["size"] = "Colossal"

    assert effective_ability(char, data, "STR") == 3

"""Improvised Effects: the preparation time, the two DCs, and the trade between them.

Every number here is checked against the core rulebook p101-102 rather than against the
implementation, since the whole point of the feature is that the arithmetic is the book's.
"""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QApplication, QPushButton

from mm_companion.core.character import AdvantageSelection, Character
from mm_companion.core.data_loader import load_game_data
from mm_companion.core.powers import ModifierSelection, Power, PowerEffectInstance
from mm_companion.core.rules import (
    improvised_effect_cost,
    improvised_plan,
    improvised_rolls,
    improvised_skills,
    power_total_cost,
    prepared_effect_ranks,
)
from mm_companion.ui.power_constructor import PowerConstructorWindow


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


def _improviser() -> Character:
    """A character who can improvise with Technology, and is good at it."""
    data = load_game_data()
    char = Character.new_default(data)
    char.advantages.append(AdvantageSelection(name="Improvised Effect", parameter="Technology"))
    char.skill_ranks["Technology"] = 10
    return char


def test_preparation_takes_a_time_rank_equal_to_the_cost() -> None:
    data = load_game_data()
    # "This requires a time rank equal to the Power Points of the effect's cost" (p102).
    plan = improvised_plan(12, data)
    assert plan.base_time_rank == 12 and plan.time_rank == 12
    assert plan.time_text == "8 hours"  # what time rank 12 is on the measurements table


def test_preparation_never_drops_below_one_minute() -> None:
    data = load_game_data()
    # "...with a minimum of time rank 3 (one minute)" (p102).
    plan = improvised_plan(1, data)
    assert plan.base_time_rank == 3 and plan.time_text == "1 minute"
    # And there is nothing to shave off a preparation already at the floor, so asking
    # costs nothing rather than buying a penalty for no gain.
    asked = improvised_plan(1, data, ranks_saved=5)
    assert asked.ranks_saved == 0
    assert asked.time_rank == 3 and asked.prep_dc == plan.prep_dc


def test_time_can_be_traded_against_the_difficulty_both_ways() -> None:
    data = load_game_data()
    # "-1 per +5 added to the skill check DC, down to the minimum of 3" (p102).
    fast = improvised_plan(12, data, ranks_saved=5)
    assert fast.time_rank == 7 and fast.ranks_saved == 5
    assert fast.prep_dc == 10 + 12 + 5 * 5  # base DC + cost + 5 per rank shaved
    assert fast.use_dc == 10 + 12  # the use check is untouched by preparation time

    # "...extend the preparation time to gain a bonus on the skill check: a +2 check
    # bonus per +1 time rank."
    slow = improvised_plan(12, data, ranks_saved=0, ranks_spent=3)
    assert slow.time_rank == 15 and slow.check_bonus == 6
    assert slow.prep_dc == 10 + 12  # spending time buys a bonus, not a cheaper DC

    # The shave is clamped at the floor even when the ask is larger than the room.
    floored = improvised_plan(12, data, ranks_saved=20)
    assert floored.ranks_saved == 9 and floored.time_rank == 3
    assert floored.prep_dc == 10 + 12 + 9 * 5


def test_both_dcs_come_from_the_cost() -> None:
    data = load_game_data()
    # "PREPARATION CHECK = DC 10 + EFFECT'S POWER POINT COST + 5 PER -1 REDUCTION" and
    # "USE CHECK = DC 10 + EFFECT'S POWER POINT COST" (p102).
    for cost in (0, 1, 7, 30):
        plan = improvised_plan(cost, data)
        assert plan.use_dc == 10 + cost
        assert plan.prep_dc == 10 + cost


def test_a_removable_discount_does_not_apply_to_an_improvised_effect() -> None:
    data = load_game_data()
    # "The Removable modifier does not apply to Improvised Effects, as they are one-use
    # by nature" (p101) - so the cost improvisation is reckoned from is the gross.
    effects = [PowerEffectInstance("protection", rank=12, flaws=[ModifierSelection("removable")])]
    power = Power(name="Powered Armour", effects=effects)
    assert power_total_cost(power, data) < 12  # the discount is real for a bought power
    assert improvised_effect_cost(power, data) == 12  # but not for an improvised one


def test_the_advantage_names_the_skill_it_was_taken_for() -> None:
    data = load_game_data()
    char = Character.new_default(data)
    assert improvised_skills(char, data) == ()
    assert prepared_effect_ranks(char, data) == 0

    char.advantages.append(AdvantageSelection(name="Improvised Effect", parameter="Technology"))
    char.advantages.append(
        AdvantageSelection(name="Improvised Effect", parameter="Expertise: Magic")
    )
    char.advantages.append(AdvantageSelection(name="Prepared Effect", rank=2))
    # Focused, so a character can hold several and improvises with whichever fits.
    assert improvised_skills(char, data) == ("Technology", "Expertise: Magic")
    assert prepared_effect_ranks(char, data) == 2


def test_a_power_granted_improvised_effect_names_its_skill_too() -> None:
    """The same gap the initiative readout had: granted advantages are never written
    back to ``Character.advantages``, so these read the bought list and found nothing."""
    data = load_game_data()
    char = Character.new_default(data)
    char.powers.append(
        Power(
            name="Utility Belt",
            effects=[
                PowerEffectInstance(
                    "enhanced_trait",
                    rank=3,
                    config={
                        "traits": [
                            {"trait": "Improvised Effect::Technology", "ranks": 1},
                            {"trait": "Prepared Effect", "ranks": 2},
                        ]
                    },
                )
            ],
        )
    )

    assert improvised_skills(char, data) == ("Technology",)
    assert prepared_effect_ranks(char, data) == 2

    # ...and it stops with the power, like every other granted trait.
    char.powers[0].activated = False
    assert improvised_skills(char, data) == ()
    assert prepared_effect_ranks(char, data) == 0


def test_the_two_checks_carry_their_own_numbers() -> None:
    data = load_game_data()
    char = _improviser()
    plan = improvised_plan(12, data, ranks_spent=2)
    prepare, use = improvised_rolls(char, data, plan, "Technology")

    # The extra preparation time is a bonus on the preparation check and on nothing else.
    assert prepare.modifier == 10 + plan.check_bonus and prepare.dc == plan.prep_dc
    assert use.modifier == 10 and use.dc == plan.use_dc
    assert "prepare" in prepare.label and "Technology" in prepare.label
    assert "secret" in prepare.hint  # the GM rolls it, and the hint says so


# -- the constructor's panel -------------------------------------------------


def test_the_improvise_panel_appears_once_the_power_costs_something(qapp: QApplication) -> None:
    window = PowerConstructorWindow(load_game_data(), character=_improviser())
    # An empty power has nothing to improvise, so the section stays out of the way.
    assert not window._improvised_row.isVisibleTo(window)

    window.canvas.add_effect("damage")._rank.setValue(6)
    assert window._improvised_row.isVisibleTo(window)


def test_the_improvise_panel_states_the_plan_and_offers_its_rolls(qapp: QApplication) -> None:
    data = load_game_data()
    window = PowerConstructorWindow(data, character=_improviser())
    window.canvas.add_effect("damage")._rank.setValue(6)
    window._improvised_toggle.setChecked(True)

    note = window._improvised_note.text()
    assert "6 PP" in note and "DC 16" in note  # 10 + 6, both checks at the default time

    window._improvised_saved.setValue(3)
    note = window._improvised_note.text()
    assert "DC 31" in note  # 10 + 6 + 3 x 5 on the preparation
    assert "DC 16" in note  # the use check is unmoved

    buttons = [b for b in window._improvised_rolls_host.findChildren(QPushButton)]
    assert len(buttons) == 2
    assert "prepare" in buttons[0].text() and "use" in buttons[1].text()


def test_without_the_advantage_the_panel_says_so_instead_of_rolling(qapp: QApplication) -> None:
    data = load_game_data()
    char = Character.new_default(data)  # no Improvised Effect advantage
    window = PowerConstructorWindow(data, character=char)
    window.canvas.add_effect("damage")._rank.setValue(6)
    window._improvised_toggle.setChecked(True)

    # The arithmetic is still shown - a player may well be working out whether the
    # advantage is worth taking - but there is nothing to roll.
    assert "6 PP" in window._improvised_note.text()
    assert window._improvised_rolls_host.findChildren(QPushButton) == []


def test_the_panel_forwards_its_roll_rather_than_making_it(qapp: QApplication) -> None:
    window = PowerConstructorWindow(load_game_data(), character=_improviser())
    window.canvas.add_effect("damage")._rank.setValue(6)
    window._improvised_toggle.setChecked(True)

    seen: list = []
    window.rollRequested.connect(seen.append)
    window._improvised_rolls_host.findChildren(QPushButton)[1].click()
    # The constructor is a window rather than a sheet block, so it asks and the section
    # that opened it hands the request to the roller.
    assert len(seen) == 1 and seen[0].dc == 16

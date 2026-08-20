"""Sub-builds: the whole characters a Summon and a Metamorph buy inside a power.

Every number is checked against the core rulebook — Summon's ``rank x 15`` minion
(p145) and Metamorph's one-form-per-rank on the wielder's own total (p136) — rather
than against the implementation, since the point of the feature is that the budget is
the book's.
"""

from __future__ import annotations

import json

import pytest
from PySide6.QtWidgets import QApplication

from mm_companion.core.character import AdvantageSelection, Character
from mm_companion.core.data_loader import load_game_data
from mm_companion.core.powers import ModifierSelection, Power, PowerEffectInstance
from mm_companion.core.rules import (
    effect_sub_build_slots,
    new_sub_build,
    power_points_spent,
    power_sub_build_slots,
    power_sub_build_violations,
    remove_sub_build,
    store_sub_build,
    sub_build_character,
    sub_build_characters,
)
from mm_companion.ui.power_constructor import PowerConstructorWindow


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


def _summoner(rank: int = 6) -> tuple[Character, Power]:
    data = load_game_data()
    char = Character.new_default(data)
    power = Power(name="Call the Pack", effects=[PowerEffectInstance("summon", rank=rank)])
    return char, power


def _shapeshifter(metamorph_rank: int = 3) -> tuple[Character, Power]:
    data = load_game_data()
    char = Character.new_default(data)
    char.abilities["STR"] = 5  # 10 PP spent, so the wielder's own total is a real number
    power = Power(
        name="Wolfshape",
        effects=[
            PowerEffectInstance(
                "morph",
                rank=2,
                extras=[ModifierSelection("metamorph", rank=metamorph_rank)],
            )
        ],
    )
    return char, power


# -- the budgets the book prints ------------------------------------------------


def test_a_summons_minion_is_built_on_fifteen_points_per_rank() -> None:
    """ "Create the summoned character with (effect rank x 15) Power Points" (p145)."""
    data = load_game_data()
    char, power = _summoner(rank=6)
    (slot,) = power_sub_build_slots(power, data, char)
    assert slot.label == "Minion"
    assert slot.budget == 90
    assert slot.count == 1


def test_the_minion_budget_follows_the_ranks_the_summon_is_dialled_to() -> None:
    data = load_game_data()
    char, power = _summoner(rank=4)
    assert power_sub_build_slots(power, data, char)[0].budget == 60
    power.effects[0].rank = 10
    assert power_sub_build_slots(power, data, char)[0].budget == 150


def test_a_metamorph_buys_one_form_per_rank_on_the_wielders_own_total() -> None:
    """ "one set of traits per rank ... the same point total as you" (p136)."""
    data = load_game_data()
    char, power = _shapeshifter(metamorph_rank=3)
    (slot,) = power_sub_build_slots(power, data, char)
    assert slot.label == "Alternate form"
    assert slot.count == 3
    assert slot.budget == power_points_spent(char, data) == 10


def test_a_metamorph_has_no_budget_without_a_character_to_read_it_off() -> None:
    """A power built outside a sheet omits the number rather than claiming zero."""
    data = load_game_data()
    _, power = _shapeshifter()
    assert power_sub_build_slots(power, data, None)[0].budget is None


def test_an_effect_with_no_sub_build_declares_none() -> None:
    data = load_game_data()
    power = Power(name="Blast", effects=[PowerEffectInstance("damage", rank=8)])
    assert power_sub_build_slots(power, data, Character.new_default(data)) == []


def test_a_metamorph_slot_appears_only_while_the_chip_is_attached() -> None:
    data = load_game_data()
    char, power = _shapeshifter()
    effect = power.effects[0]
    assert len(effect_sub_build_slots(effect, data, char)) == 1
    effect.extras.clear()
    assert effect_sub_build_slots(effect, data, char) == []


# -- storing, reading, removing --------------------------------------------------


def test_a_stored_build_reads_back_with_the_slots_budget_stamped_on_it() -> None:
    """The budget is derived, so it is restamped on read rather than saved with the build."""
    data = load_game_data()
    char, power = _summoner(rank=4)
    slot = power_sub_build_slots(power, data, char)[0]
    store_sub_build(slot, 0, new_sub_build(slot, data))
    power.effects[0].rank = 8
    slot = power_sub_build_slots(power, data, char)[0]
    assert sub_build_character(slot, 0).power_points_total == 120


def test_a_builds_power_level_follows_the_wielders() -> None:
    """ "They are subject to the normal Power Level limits" (p145)."""
    data = load_game_data()
    char, power = _summoner()
    char.power_level = 13
    slot = power_sub_build_slots(power, data, char)[0]
    store_sub_build(slot, 0, new_sub_build(slot, data))
    assert sub_build_character(slot, 0).power_level == 13


def test_removing_the_last_build_leaves_the_config_as_it_started() -> None:
    """So a power that gained a sub-build and lost it again saves byte-for-byte."""
    data = load_game_data()
    char, power = _summoner()
    before = dict(power.effects[0].config)
    slot = power_sub_build_slots(power, data, char)[0]
    store_sub_build(slot, 0, new_sub_build(slot, data))
    assert power.effects[0].config != before
    remove_sub_build(slot, 0)
    assert power.effects[0].config == before


def test_a_sub_build_survives_a_save_and_load_round_trip() -> None:
    data = load_game_data()
    char, power = _summoner()
    slot = power_sub_build_slots(power, data, char)[0]
    minion = new_sub_build(slot, data)
    minion.profile["hero_name"] = "Dire Wolf"
    minion.abilities["STR"] = 8
    store_sub_build(slot, 0, minion)
    char.powers = [power]

    reloaded = Character.from_dict(json.loads(json.dumps(char.to_dict())))
    slot = power_sub_build_slots(reloaded.powers[0], data, reloaded)[0]
    (restored,) = sub_build_characters(slot)
    assert restored.profile["hero_name"] == "Dire Wolf"
    assert restored.abilities["STR"] == 8


def test_a_sub_build_costs_the_power_nothing() -> None:
    """The minion is what the Summon's ranks *buy*; it is not a second price tag."""
    from mm_companion.core.rules import power_total_cost

    data = load_game_data()
    char, power = _summoner(rank=6)
    before = power_total_cost(power, data, char)
    slot = power_sub_build_slots(power, data, char)[0]
    minion = new_sub_build(slot, data)
    minion.abilities["STR"] = 10
    store_sub_build(slot, 0, minion)
    assert power_total_cost(power, data, char) == before == 12


# -- what is checked -------------------------------------------------------------


def test_a_minion_over_its_budget_is_flagged() -> None:
    data = load_game_data()
    char, power = _summoner(rank=2)  # a 30-point budget
    slot = power_sub_build_slots(power, data, char)[0]
    minion = new_sub_build(slot, data)
    minion.abilities["STR"] = 20  # 40 PP
    store_sub_build(slot, 0, minion)
    (message,) = power_sub_build_violations(power, data, char)
    assert "40 PP" in message and "30 PP" in message


def test_a_minion_inside_its_budget_is_not_flagged() -> None:
    data = load_game_data()
    char, power = _summoner(rank=6)
    slot = power_sub_build_slots(power, data, char)[0]
    minion = new_sub_build(slot, data)
    minion.abilities["STR"] = 20
    store_sub_build(slot, 0, minion)
    assert power_sub_build_violations(power, data, char) == []


def test_a_minion_may_not_have_minions_of_its_own() -> None:
    """ "cannot have minions of their own, either from this effect or the Minions
    advantage" (p145) — a fact about the nested build, so no picker could prevent it."""
    data = load_game_data()
    char, power = _summoner()
    slot = power_sub_build_slots(power, data, char)[0]
    minion = new_sub_build(slot, data)
    minion.powers = [Power(name="Pets", effects=[PowerEffectInstance("summon", rank=1)])]
    minion.advantages = [AdvantageSelection(name="Minion", rank=1)]
    store_sub_build(slot, 0, minion)
    messages = power_sub_build_violations(power, data, char)
    assert any("carries Summon" in m for m in messages)
    assert any("Minion advantage" in m for m in messages)


def test_more_builds_than_the_power_buys_is_flagged_rather_than_deleted() -> None:
    """Dropping a Metamorph from rank 3 to rank 1 must not silently bin two characters."""
    data = load_game_data()
    char, power = _shapeshifter(metamorph_rank=3)
    slot = power_sub_build_slots(power, data, char)[0]
    for index in range(3):
        store_sub_build(slot, index, new_sub_build(slot, data))
    assert power_sub_build_violations(power, data, char) == []
    power.effects[0].extras[0].rank = 1
    (message,) = power_sub_build_violations(power, data, char)
    assert "3 built where this power buys 1" in message


def test_a_minion_is_held_to_the_wielders_power_level() -> None:
    """A minion is "subject to the normal Power Level limits" (p145), and the limit is
    the wielder's — which is what the slot stamps onto the build."""
    data = load_game_data()
    char, power = _summoner()
    char.power_level = 8
    slot = power_sub_build_slots(power, data, char)[0]
    minion = new_sub_build(slot, data)
    assert minion.power_level == 8
    for key in ("FGT", "AGL", "STA", "STR"):
        minion.abilities[key] = 20
    store_sub_build(slot, 0, minion)

    messages = power_sub_build_violations(power, data, char)
    # Each is prefixed with the slot: the reader is looking at the *power*, and a bare
    # "Dodge + Toughness 20 exceeds PL cap 16" would read as the wielder's own breach.
    assert any(m.startswith("Minion: Dodge + Toughness") for m in messages)
    assert any(m.startswith("Minion: Fortitude + Will") for m in messages)


def test_a_minion_inside_its_budget_and_its_power_level_warns_about_nothing() -> None:
    data = load_game_data()
    char, power = _summoner()
    char.power_level = 10
    slot = power_sub_build_slots(power, data, char)[0]
    store_sub_build(slot, 0, new_sub_build(slot, data))
    assert power_sub_build_violations(power, data, char) == []


# -- the constructor -------------------------------------------------------------


def test_the_card_shows_a_strip_for_a_summon_and_hides_it_otherwise(qapp) -> None:
    data = load_game_data()
    char = Character.new_default(data)
    window = PowerConstructorWindow(data, character=char)
    summon = window.canvas.add_effect("summon")
    damage = window.canvas.add_effect("damage")
    assert summon._sub_builds.isVisibleTo(summon)
    assert not damage._sub_builds.isVisibleTo(damage)


def test_the_strip_appears_when_a_metamorph_chip_is_attached(qapp) -> None:
    data = load_game_data()
    char = Character.new_default(data)
    window = PowerConstructorWindow(data, character=char)
    card = window.canvas.add_effect("morph")
    assert not card._sub_builds.isVisibleTo(card)
    card.attach_modifier("metamorph")
    assert card._sub_builds.isVisibleTo(card)


def test_the_constructor_warns_about_an_over_budget_minion(qapp) -> None:
    data = load_game_data()
    char = Character.new_default(data)
    window = PowerConstructorWindow(data, character=char)
    card = window.canvas.add_effect("summon")
    card._rank.setValue(1)
    slot = power_sub_build_slots(window.power, data, char)[0]
    minion = new_sub_build(slot, data)
    minion.abilities["STR"] = 20
    store_sub_build(slot, 0, minion)
    window._refresh_pl_warning()
    assert "sub-build over budget" in window._warning.text().lower()
    assert "more than the 15 PP" in window._warning.toolTip()


def test_a_sub_build_window_shows_the_budget_it_cannot_edit(qapp) -> None:
    from mm_companion.ui.sub_build_window import SubBuildWindow

    data = load_game_data()
    char, power = _summoner(rank=6)
    slot = power_sub_build_slots(power, data, char)[0]
    store_sub_build(slot, 0, new_sub_build(slot, data))
    window = SubBuildWindow(character=sub_build_character(slot, 0), label="Minion")
    system = window.sheet.system_info
    assert system._power_points.value() == 90
    assert system._power_points.isReadOnly()
    assert system._power_level.isReadOnly()
    # ...and unlocking the sheet does not hand back a field that was never the player's.
    window.sheet.set_locked(False)
    assert system._power_points.isReadOnly()

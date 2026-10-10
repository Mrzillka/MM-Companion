"""The Quick NPC builder: a Power Level and a preset in, a playable creature out.

:mod:`mm_companion.core.npc` is pure Python, and the point of it living in ``core``
is that what it builds — and the numbers a preset gives — can be proved without a
display. The wizard that collects them is covered at the bottom of this file and in
``test_gm_window.py``.
"""

from __future__ import annotations

import random

import pytest

from mm_companion.core.data_loader import load_game_data
from mm_companion.core.npc import pair_total, preset_stats, quick_npc, share_bounds
from mm_companion.core.powers import Power
from mm_companion.core.rules import (
    effect_game_terms,
    estimated_power_level,
    power_rolls,
    resistance_total,
)
from mm_companion.ui.npc_names import npc_names, random_npc_name


def _npc(**overrides):
    args = {"name": "Bandit", "attack": 6, "effect": 5, "defence": 7, "toughness": 4}
    args.update(overrides)
    return quick_npc(load_game_data(), **args)


def test_the_numbers_land_where_the_rules_read_them() -> None:
    data = load_game_data()
    npc = _npc()

    assert npc.profile["hero_name"] == "Bandit"
    # A quick NPC has no abilities, so every resistance base is 0 and what was typed
    # is what the sheet shows.
    assert resistance_total(npc, data, "DEF") == 7
    assert resistance_total(npc, data, "TOUGHNESS") == 4
    # Dodge derives from Defence, so it follows without being asked for.
    assert resistance_total(npc, data, "DODGE") == 7
    assert npc.abilities["ATK"] == 6


def test_it_comes_with_something_that_hurts_and_something_that_stops_you() -> None:
    npc = _npc(effect=8)

    names = [power.name for power in npc.powers]
    assert names == ["Damage", "Affliction"]
    assert all(isinstance(power, Power) for power in npc.powers)
    assert [power.effects[0].rank for power in npc.powers] == [8, 8]


def test_the_attack_bonus_reaches_both_powers() -> None:
    data = load_game_data()
    npc = _npc(attack=9, effect=6)

    for power in npc.powers:
        attack = next(spec for spec in power_rolls(power, npc, data) if not spec.rolled_by_target)
        assert attack.modifier == 9


def test_the_affliction_carries_a_full_degree_ladder() -> None:
    data = load_game_data()
    npc = _npc(effect=6)
    affliction = npc.powers[1].effects[0]

    assert affliction.config["degree1"] == ["dazed"]
    assert affliction.config["degree2"] == ["stunned"]
    assert affliction.config["degree3"] == ["incapacitated"]
    # And the chosen resistance replaces the base line rather than sitting beside it.
    terms = effect_game_terms(affliction, data)
    assert "Fortitude" in terms
    assert "Dazed" in terms and "Stunned" in terms


def test_the_power_level_is_estimated_from_what_it_can_do() -> None:
    data = load_game_data()

    # attack 6 + effect 6 → 6; dodge 6 + toughness 6 → 6. Nothing states a PL.
    assert estimated_power_level(_npc(attack=6, effect=6, defence=6, toughness=6), data) == 6
    # Raise the offence alone and the estimate follows it.
    assert estimated_power_level(_npc(attack=10, effect=10, defence=6, toughness=6), data) == 10


def test_a_quick_npc_round_trips_through_its_own_serialization() -> None:
    from mm_companion.core.character import Character

    npc = _npc(effect=7)
    restored = Character.from_dict(npc.to_dict())

    assert [p.name for p in restored.powers] == ["Damage", "Affliction"]
    assert restored.powers[1].effects[0].config["degree2"] == ["stunned"]
    assert restored.resistances["TOUGHNESS"] == npc.resistances["TOUGHNESS"]


def test_a_zero_rank_effect_is_allowed_and_a_negative_one_is_floored() -> None:
    # A pure brawler with no special attack is a legitimate mook; a negative rank is not.
    assert _npc(effect=0).powers[0].effects[0].rank == 0
    assert _npc(effect=-3).powers[0].effects[0].rank == 0


# -- the suggested names -----------------------------------------------------


def test_the_name_list_loads_and_re_rolling_always_changes_it() -> None:
    names = npc_names()
    assert "Bandit" in names and "Boss" in names

    for _ in range(20):
        assert random_npc_name(exclude="Bandit") != "Bandit"


# -- the presets ---------------------------------------------------------------


def test_a_pl_10_brute_hits_hard_and_shrugs_blows_off() -> None:
    """The worked example: effect 14 and attack +6 — at the cap, rounded the brute's way."""
    stats = preset_stats(load_game_data(), "brute", 10)
    assert (stats.effect, stats.attack) == (14, 6)
    assert (stats.toughness, stats.defence) == (14, 6)
    assert (stats.fortitude, stats.will) == (6, 6)


def test_speed_is_the_brute_turned_round() -> None:
    stats = preset_stats(load_game_data(), "speed", 10)
    assert (stats.attack, stats.effect) == (14, 6)
    assert (stats.defence, stats.toughness) == (14, 6)
    assert (stats.fortitude, stats.will) == (6, 6)


def test_balance_splits_every_cap_down_the_middle() -> None:
    stats = preset_stats(load_game_data(), "balance", 10)
    assert (stats.attack, stats.effect, stats.defence, stats.toughness) == (10, 10, 10, 10)
    assert (stats.fortitude, stats.will) == (6, 6)


@pytest.mark.parametrize("preset", ["brute", "speed", "balance", "random"])
def test_every_preset_sits_on_the_power_level_caps(preset: str) -> None:
    """Attack + effect and defence + toughness are always *exactly* the cap; Fortitude
    and Will are never over theirs. And the estimate reads the level straight back."""
    data = load_game_data()
    for level in range(1, 21):
        stats = preset_stats(data, preset, level, random.Random(level))
        assert stats.attack + stats.effect == pair_total(data, "attack_effect", level)
        assert stats.defence + stats.toughness == pair_total(data, "defense_toughness", level)
        assert stats.fortitude + stats.will <= pair_total(data, "fortitude_will", level)
        npc = quick_npc(data, name="X", power_level=level, **vars(stats))
        assert estimated_power_level(npc, data) == level


def test_random_stays_between_a_third_and_two_thirds_of_each_cap() -> None:
    data = load_game_data()
    total = pair_total(data, "attack_effect", 10)
    low, high = share_bounds(data, total)
    assert (low, high) == (6, 14)
    rng = random.Random(7)
    seen = set()
    for _ in range(300):
        stats = preset_stats(data, "random", 10, rng)
        for value in vars(stats).values():
            assert low <= value <= high
        seen.add(stats.attack)
    # ...and it really is random: more than a couple of splits turn up.
    assert len(seen) > 5


def test_an_unknown_preset_is_an_even_split_rather_than_an_error() -> None:
    stats = preset_stats(load_game_data(), "no-such-preset", 10)
    assert (stats.attack, stats.effect) == (10, 10)


def test_fortitude_will_and_the_level_land_on_the_character() -> None:
    npc = _npc(fortitude=5, will=3, power_level=8)
    assert npc.resistances["FORTITUDE"] == 5
    assert npc.resistances["WILL"] == 3
    assert npc.power_level == 8 and npc.characteristics["power_level"] == 8


# -- the wizard ----------------------------------------------------------------


@pytest.fixture
def dialog(qapp):
    from mm_companion.ui.npc_quick_dialog import QuickNPCDialog

    made = QuickNPCDialog()
    yield made
    made.deleteLater()


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def test_a_first_dialog_opens_on_pl_10_balanced(dialog) -> None:
    entered = dialog.value()
    assert entered.power_level == 10
    assert (entered.attack, entered.effect, entered.defence, entered.toughness) == (10,) * 4
    assert (entered.fortitude, entered.will) == (6, 6)
    assert entered.icon == "balance"


def test_a_preset_fills_every_box_and_puts_on_its_icon(dialog) -> None:
    dialog._preset_buttons["brute"].click()
    entered = dialog.value()
    assert (entered.effect, entered.attack) == (14, 6)
    assert entered.icon == "brute"


def test_changing_the_level_reapplies_the_preset_over_hand_edits(dialog) -> None:
    dialog._preset_buttons["brute"].click()
    dialog._spins["attack"].setValue(3)
    assert dialog.value().attack == 3  # a hand edit sticks...
    dialog._power_level.setValue(7)
    entered = dialog.value()
    assert (entered.effect, entered.attack) == (10, 4)  # ...until the level moves


def test_the_arrows_step_through_every_icon_and_wrap(dialog) -> None:
    from mm_companion.ui import npc_icons

    start = dialog.value().icon
    seen = []
    for _ in npc_icons.NPC_ICONS:
        dialog._set_icon(npc_icons.step(dialog.value().icon, 1))
        seen.append(dialog.value().icon)
    assert len(npc_icons.NPC_ICONS) == 8
    assert sorted(seen) == sorted(i.id for i in npc_icons.NPC_ICONS)
    assert seen[-1] == start
    assert npc_icons.step(npc_icons.NPC_ICONS[0].id, -1) == npc_icons.NPC_ICONS[-1].id


def test_the_level_and_preset_carry_to_the_next_dialog_this_run(qapp) -> None:
    from mm_companion.ui.npc_quick_dialog import QuickNPCDialog

    first = QuickNPCDialog()
    first._power_level.setValue(6)
    first._preset_buttons["speed"].click()
    first.accept()

    second = QuickNPCDialog()
    entered = second.value()
    assert entered.power_level == 6
    assert (entered.attack, entered.effect) == (8, 4)
    assert entered.icon == "speed"
    for made in (first, second):
        made.deleteLater()


def test_a_cancelled_dialog_remembers_nothing(qapp) -> None:
    from mm_companion.ui.npc_quick_dialog import QuickNPCDialog

    first = QuickNPCDialog()
    first._power_level.setValue(15)
    first.reject()
    assert QuickNPCDialog().value().power_level == 10


def test_a_nameless_npc_cannot_be_created(dialog) -> None:
    dialog._name.setText("   ")
    assert not dialog._ok_button.isEnabled()
    dialog._name.setText("Thug")
    assert dialog._ok_button.isEnabled()


def test_a_pair_over_its_cap_says_so(dialog) -> None:
    label, *_ = dialog._readouts[0]
    assert label.text() == "20 / 20"
    dialog._spins["attack"].setValue(15)
    assert label.text() == "25 / 20"
    assert label.styleSheet()

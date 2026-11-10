"""The roller after the user's 2026-11-10 play-test: ±1 steps or a slider for the
modifier, a muted unset DC whose capsule is never cut off, pills that never widen the
roller and re-arm their maneuvers, maneuver lines that leave at 0 and follow the
character's numbers, a short no-DC flag, natural 20s and 1s that stand out, and no
desktop notifications while the app is in front."""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QApplication

from mm_companion.core import storage
from mm_companion.core.character import Character
from mm_companion.core.powers import ModifierSelection, Power, PowerEffectInstance
from mm_companion.core.rules import attack_choices
from mm_companion.ui import dice_roller, theme, toasts
from mm_companion.ui.roll_history import (
    NATURAL_ONE,
    NATURAL_TWENTY,
    SessionRollCard,
    roll_headline,
    roll_natural,
    toast_accent,
)
from mm_companion.ui.roller import action_lane
from mm_companion.ui.roller.quick_rolls import _QuickRollButton
from mm_companion.ui.roller.view import DiceRollerView
from mm_companion.ui.sections.dice import DiceSection

pytestmark = pytest.mark.usefixtures("workspace")

ALL_OUT, DEFENSIVE = "all_out_attack", "defensive_attack"


def _hero(data, *, attack: int = 6, defense: int = 4) -> Character:
    hero = Character.new_default(data)
    hero.abilities["ATK"] = attack
    hero.resistances["DEF"] = defense
    damage = PowerEffectInstance("damage", rank=8)
    damage.extras.append(ModifierSelection("ranged", config={"from": "close"}))
    hero.powers = [Power(name="Blast", effects=[damage])]
    return hero


def _armed(data, **hero) -> DiceSection:
    section = DiceSection(data, _hero(data, **hero))
    (choice,) = attack_choices(section._character, section._data)
    section.panel.load_spec(choice.spec)
    return section


def _settle(qapp: QApplication, widget, width: int = 360, height: int = 700) -> None:
    widget.resize(width, height)
    for _ in range(4):
        qapp.processEvents()


# -- the modifier row ---------------------------------------------------------------


def test_the_sheet_steps_are_one_and_five(qapp, data) -> None:
    section = DiceSection(data, _hero(data))
    captions = [b.text() for b in section.panel._step_buttons]
    assert captions == ["−5", "−1", "+1", "+5"]


def test_buttons_by_default_and_a_slider_on_request(qapp, data) -> None:
    assert storage.modifier_input() == storage.MODIFIER_INPUT_BUTTONS
    view = DiceRollerView()
    view.show()
    panel = view.panel
    panel.set_modifier_steps((1, 5))
    assert panel.modifier_input() == "buttons"
    assert panel._modifier_slider.isHidden()
    assert not any(b.isHidden() for b in panel._step_buttons)

    storage.set_modifier_input(storage.MODIFIER_INPUT_SLIDER)
    view.sync_dice_layout()  # what the General page calls on Save
    assert not panel._modifier_slider.isHidden()
    assert all(b.isHidden() for b in panel._step_buttons)
    slider = panel._modifier_slider
    assert (slider.minimum(), slider.maximum()) == (-10, 20)

    slider.setValue(7)
    assert panel.adjustment() == 7
    panel._modifier_spin.setValue(25)  # past the slider's reach: it waits at its end
    assert slider.value() == 20 and panel.adjustment() == 25
    panel._modifier_spin.setValue(-3)
    assert slider.value() == -3


# -- the DC box -----------------------------------------------------------------------


def test_an_unset_dc_reads_as_a_placeholder(qapp) -> None:
    view = DiceRollerView()
    panel = view.panel
    assert panel._dc_spin.text() == dice_roller.DC_NONE != "none"
    assert theme.color("text.muted") in panel._dc_spin.styleSheet()
    panel.set_dc(15)
    assert panel._dc_spin.styleSheet() == ""


@pytest.mark.parametrize("width", [360, 900])
def test_the_dc_capsule_is_never_cut_off(qapp, width) -> None:
    # Its foot was clipped by the edge of the controls with the lane empty (pic 2).
    view = DiceRollerView()
    view.show()
    panel = view.panel
    panel.set_dc(12)
    _settle(qapp, view, width)
    capsule = panel._dc_pair
    assert capsule.parentWidget().rect().contains(capsule.geometry())


# -- quick-roll pills --------------------------------------------------------------------


def test_a_long_pill_never_widens_the_roller(qapp) -> None:
    long_name = "Rumbling: Attack +15 vs. Defense (Accurate Attack +5, All-Out Attack +5)"
    view = DiceRollerView()
    view.show()
    panel = view.panel
    _settle(qapp, view)
    before = panel.minimumSizeHint().width()
    panel._add_quick_roll({"bonus": 15, "penalty": 0}, name=long_name)
    _settle(qapp, view)
    (pill,) = panel._quick_container.findChildren(_QuickRollButton)
    assert pill.full_text().startswith(long_name)
    assert pill.sizeHint().width() <= int(theme.metric("column.quick-roll-max"))
    assert pill.text().endswith("…")
    assert long_name in pill.toolTip()
    assert panel.minimumSizeHint().width() <= before


def test_a_pill_saved_off_a_maneuvered_attack_re_arms_its_maneuvers(
    qapp, data, instant_die
) -> None:
    section = _armed(data)
    panel = section.panel
    section.start_maneuver(ALL_OUT).set_value(3)
    landed: list = []
    section.conditionsRequested.connect(landed.append)
    rolls: list[dict] = []
    panel.localRoll.connect(rolls.append)
    panel._start_roll()
    first = rolls[-1]
    assert landed == [((ALL_OUT, 3),)] and panel.action_lines() == []

    # The card's star saves it...
    panel.toggle_quick_roll({"name": first["label"], "bonus": first["bonus"], "penalty": 0})
    (entry,) = panel._quick_rolls
    assert entry["lines"] == [[ALL_OUT, 3]] and entry["spec"]["label"].startswith("Blast")
    # ...and survives a restart.
    assert panel._load_quick_rolls()[0]["lines"] == [[ALL_OUT, 3]]

    # A click re-arms the maneuver: the same roll, and the Defense penalty lands again.
    panel.load_spec(None)
    panel._apply_quick_roll(entry)
    second = rolls[-1]
    assert (second["label"], second["bonus"]) == (first["label"], first["bonus"])
    assert landed == [((ALL_OUT, 3),), ((ALL_OUT, 3),)]
    assert panel.action_lines() == [] and panel.current_spec() is None


def test_a_plain_pill_is_unchanged(qapp, instant_die) -> None:
    view = DiceRollerView()
    panel = view.panel
    panel._add_quick_roll({"bonus": 5, "penalty": 0}, name="Perception")
    (entry,) = panel._quick_rolls
    assert "spec" not in entry
    rolls: list[dict] = []
    panel.localRoll.connect(rolls.append)
    panel._apply_quick_roll(entry)
    assert (rolls[-1]["label"], rolls[-1]["bonus"]) == ("Perception", 5)


# -- the action lane ------------------------------------------------------------------------


def test_a_line_at_zero_greys_rides_on_nothing_and_goes(qapp, data, instant_die) -> None:
    section = _armed(data)
    panel = section.panel
    line = section.start_maneuver(ALL_OUT)
    line.set_value(0)
    assert line.idle and not line.applies and line.available
    assert line._zero_timer.isActive()
    assert line.trade() == action_lane.ZERO_NOTE
    assert panel.active_lines() == []

    # Raised again in time, it stays.
    line.set_value(2)
    assert not line.idle and not line._zero_timer.isActive()

    # Left at 0 and rolled, it leaves no condition behind.
    line.set_value(0)
    landed: list = []
    section.conditionsRequested.connect(landed.append)
    panel._start_roll()
    assert landed == []
    line._zero_timer.timeout.emit()
    assert panel.action_lines() == []


def test_a_line_greyed_for_want_of_room_is_not_taken_away(qapp, data) -> None:
    section = _armed(data, defense=0)  # no Defense to trade: All-Out's maximum is 0
    line = section.start_maneuver(ALL_OUT)
    assert not line.available and not line.idle
    assert not line._zero_timer.isActive()


def test_the_limits_follow_the_character(qapp, data) -> None:
    # Growth moved the user's Defense and the maximum stayed where it was.
    section = _armed(data, defense=4)
    line = section.start_maneuver(ALL_OUT)
    assert (line.maximum(), line.value()) == (4, 4)
    section._character.resistances["DEF"] = 2
    section.refresh_limits()
    assert (line.maximum(), line.value()) == (2, 2)
    section._character.resistances["DEF"] = 9
    section.refresh_limits()
    assert line.maximum() == 5 and line.value() == 2  # widened, never pushed up


def test_the_dice_block_hears_the_character_change(qapp) -> None:
    from mm_companion.ui.blocks.bus import CONDITION_CHANGED, DERIVED_CHANGED
    from mm_companion.ui.blocks.registry import block_descriptors

    (dice,) = [d for d in block_descriptors() if d.key == "dice"]
    assert dice.subscribes[DERIVED_CHANGED] == "refresh_limits"
    assert dice.subscribes[CONDITION_CHANGED] == "refresh_limits"
    assert dice.publishes == {}  # a roll is still never an edit


def test_the_slider_says_why_its_range_is_short(qapp, data) -> None:
    section = _armed(data, defense=3)
    line = section.start_maneuver(ALL_OUT)
    assert line.maximum() == 3
    assert "Defense is +3" in line._slider.toolTip()
    assert line._slider.toolTip() == line._spin.toolTip()

    section = _armed(data, attack=8, defense=8)
    line = section.start_maneuver(ALL_OUT)
    assert line.maximum() == 5 and line.cap_reason == ""
    assert line._slider.toolTip() == "0 to 5"


# -- natural 20s and 1s -----------------------------------------------------------------------


@pytest.mark.parametrize(
    "die,kind,token", [(20, NATURAL_TWENTY, "dice.critical"), (1, NATURAL_ONE, "dice.fumble")]
)
def test_a_natural_roll_bursts_and_says_so(qapp, monkeypatch, die, kind, token) -> None:
    monkeypatch.setattr(dice_roller, "ROLL_DURATION_MS", 0)
    monkeypatch.setattr(dice_roller, "roll_d20", lambda *a, **k: die)
    view = DiceRollerView()
    view.show()
    panel = view.panel
    panel._start_roll()
    assert panel.outcome == token
    assert panel.celebrating == kind
    assert ("NATURAL 20" if die == 20 else "NATURAL 1") in panel._readout.text()
    # A natural 20's numeral turns gold; the next roll puts it all back.
    gold = theme.color("dice.critical")
    assert (gold in panel._face.styleSheet()) == (die == 20)
    monkeypatch.setattr(dice_roller, "roll_d20", lambda *a, **k: 11)
    panel._start_roll()
    assert panel.celebrating is None and gold not in panel._face.styleSheet()


def test_a_lucky_reroll_or_a_take_ten_is_no_natural(qapp) -> None:
    assert roll_natural({"die": 20, "reroll": True}) is None
    assert roll_natural({"die": 10, "routine": True}) is None
    assert roll_natural({"die": 20, "degree": 2, "critical": False}) is None  # a minion's
    assert roll_natural({"die": 20, "degree": 3, "critical": True}) == NATURAL_TWENTY
    assert roll_natural({"die": 1}) == NATURAL_ONE


def test_a_natural_card_stands_out_in_the_history(qapp) -> None:
    roll = {"kind": "roll", "seq": 3, "die": 20, "bonus": 4, "penalty": 0, "label": "Blast"}
    card = SessionRollCard(roll)
    assert card.objectName() == "naturalCard"
    assert theme.color("dice.critical") in card.styleSheet()
    assert "Nat 20" in roll_headline(roll)
    assert toast_accent(roll) == theme.color("dice.critical")
    plain = SessionRollCard(dict(roll, die=12))
    assert plain.objectName() != "naturalCard"


def test_crimson_gold_tells_a_twenty_from_an_armed_die() -> None:
    from mm_companion.ui.theme import loader

    preset = loader.available_themes()["crimson-gold"]
    colours = preset.colors
    assert colours["dice.critical"].lower() != colours["accent.dice"].lower()
    assert "dice.fumble" in colours


# -- notifications -------------------------------------------------------------------------


def test_no_notification_while_the_app_is_in_front(qapp, monkeypatch) -> None:
    from mm_companion.ui.roll_history import announce_entry

    monkeypatch.setattr(toasts, "SUPPRESSED", False)
    toaster = toasts.roll_toaster()
    roll = {"kind": "roll", "seq": 991, "die": 9, "bonus": 1, "penalty": 0}

    monkeypatch.setattr(toasts, "app_focused", lambda: True)
    announce_entry(roll, None)
    assert toaster.cards() == []
    # ...and it is not announced later, once the app has gone behind something else.
    monkeypatch.setattr(toasts, "app_focused", lambda: False)
    announce_entry(roll, None)
    assert toaster.cards() == []

    announce_entry(dict(roll, seq=992), None)
    assert len(toaster.cards()) == 1
    toaster.clear()

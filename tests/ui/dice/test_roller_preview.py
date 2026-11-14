"""The roller after the 2026-11-11 UX pass: a preview of what the die will roll, a
modifier field and a DC field that no longer read as buttons, a lone pill that keeps
its size, short recipe pills, no DC said twice, no dot hanging off a wrapped line, no
Reroll under a natural 20, and the card's star level with its first line."""

from __future__ import annotations

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from mm_companion.core.character import Character
from mm_companion.core.powers import ModifierSelection, Power, PowerEffectInstance
from mm_companion.core.rules import RollSpec, attack_choices
from mm_companion.ui import dice_roller, theme
from mm_companion.ui.roll_history import (
    QuickRollStar,
    label_names_dc,
    roll_headline,
    staged_working,
)
from mm_companion.ui.roller.quick_rolls import quick_label, recipe_caption
from mm_companion.ui.roller.view import DiceRollerView
from mm_companion.ui.sections.dice import DiceSection

pytestmark = pytest.mark.usefixtures("workspace")

ALL_OUT = "all_out_attack"


def _armed(data) -> DiceSection:
    hero = Character.new_default(data)
    hero.abilities["ATK"] = 6
    hero.resistances["DEF"] = 4
    damage = PowerEffectInstance("damage", rank=8)
    damage.extras.append(ModifierSelection("ranged", config={"from": "close"}))
    hero.powers = [Power(name="Blast", effects=[damage])]
    section = DiceSection(data, hero)
    (choice,) = attack_choices(section._character, section._data)
    section.panel.load_spec(choice.spec)
    return section


def _settle(qapp: QApplication, widget, width: int = 380, height: int = 800) -> None:
    widget.resize(width, height)
    for _ in range(4):
        qapp.processEvents()


# -- the preview ------------------------------------------------------------------------


def test_the_readout_says_what_a_click_will_roll(qapp, data) -> None:
    section = _armed(data)
    panel = section.panel
    base = panel.current_spec().modifier
    panel._modifier_spin.setValue(-2)
    section.start_maneuver(ALL_OUT)
    lined = panel.with_lines(panel.current_spec()).modifier
    assert lined != base  # All-Out moved the attack
    line, hover = panel.staged_preview()
    assert line == staged_working(lined - 2)
    assert line in panel._readout.text()
    assert dice_roller.IDLE_PROMPT in panel._readout.text()
    # The hover adds it up: the attack's parts, All-Out's line, the modifier box.
    assert "All-Out Attack" in hover and "Circumstance" in hover
    assert panel._readout.toolTip() == hover


def test_a_plain_d20_with_nothing_set_has_no_preview(qapp) -> None:
    view = DiceRollerView()
    panel = view.panel
    assert panel.staged_preview() == ("", "")
    assert "<b>" not in panel._readout.text()
    panel.set_dc(15)
    assert "d20 vs DC 15" in panel._readout.text()


def test_a_roll_keeps_its_result_until_the_player_sets_up_the_next(qapp, instant_die) -> None:
    view = DiceRollerView()
    panel = view.panel
    panel._modifier_spin.setValue(3)
    panel._start_roll()
    # Landing zeroes the modifier, which must not trade the 14 for the preview.
    assert panel._modifier_spin.value() == 0
    assert "14" in panel._readout.text() and dice_roller.IDLE_PROMPT not in panel._readout.text()
    # The player's own change does: the result is in the history.
    panel.add_bonus(2)
    assert "d20 + 2" in panel._readout.text()
    assert panel._reroll_button.isHidden()


def test_no_reroll_under_a_natural_20(qapp, monkeypatch) -> None:
    monkeypatch.setattr(dice_roller, "ROLL_DURATION_MS", 0)
    view = DiceRollerView()
    panel = view.panel
    monkeypatch.setattr(dice_roller, "roll_d20", lambda *a, **k: 20)
    panel._start_roll()
    assert panel._reroll_button.isHidden()
    assert panel.own_actions()[dice_roller.ACTION_REROLL] is None  # still on the popup
    monkeypatch.setattr(dice_roller, "roll_d20", lambda *a, **k: 19)
    panel._start_roll()
    assert not panel._reroll_button.isHidden()


# -- the fields -------------------------------------------------------------------------


def test_the_modifier_and_dc_read_as_fields(qapp) -> None:
    view = DiceRollerView()
    view.show()
    panel = view.panel
    panel.set_modifier_steps((1, 5))
    _settle(qapp, view)
    assert panel._modifier_spin.text() == "+0"
    # The steps are flat; the box is the row's one field, as wide as a signed two-digit
    # number and no wider — the steps take the row's spare width (2026-11-13).
    assert all(step.autoRaise() for step in panel._step_buttons)
    boxes = (panel._modifier_spin, panel._dc_spin, panel._bonus_spin, panel._penalty_spin)
    assert panel._modifier_spin.width() == max(box.sizeHint().width() for box in boxes)
    # The DC box is the modifier box's twin, under it, between steps of its own.
    assert panel._dc_spin.alignment() & Qt.AlignmentFlag.AlignHCenter
    assert panel._dc_spin.width() == panel._modifier_spin.width()
    assert [b.text() for b in panel._dc_step_buttons] == ["−5", "−1", "+1", "+5"]


# -- pills ------------------------------------------------------------------------------


def test_a_lone_last_pill_keeps_its_size(qapp) -> None:
    view = DiceRollerView()
    view.show()
    panel = view.panel
    for bonus in (2, 3, 7, 4):
        panel._add_quick_roll({"bonus": bonus, "penalty": 0}, name="Perception")
    panel._add_quick_roll({"bonus": 11, "penalty": 0})
    _settle(qapp, view, 380)
    flow = panel._quick_flow
    chips = [flow.itemAt(i).widget() for i in range(flow.count())]
    last = chips[-1]
    assert last.y() > chips[0].y(), "the last pill should be on a line of its own"
    assert last.width() < panel._quick_container.width() // 2


def test_a_recipe_pill_is_captioned_short() -> None:
    name = "Rumbling: Attack +15 vs. Defense (Accurate Attack +5, All-Out Attack +5)"
    assert recipe_caption(name, "+15") == "Rumbling +15 · Accurate, All-Out"
    entry = {"name": name, "bonus": 15, "penalty": 0, "spec": {"label": "x"}, "lines": []}
    assert quick_label(entry) == "Rumbling +15 · Accurate, All-Out"
    # A close attack keeps the word that tells it apart; a renamed pill stays whole.
    close = "Close Combat: Unarmed +8 vs. Defense (Power Attack +2)"
    assert recipe_caption(close, "+8") == "Close Combat: Unarmed +8 · Power"
    assert recipe_caption("My combo", "+8") == "My combo"


# -- the number line --------------------------------------------------------------------


def test_the_dc_is_not_said_twice() -> None:
    assert label_names_dc("Toughness vs. 18", 18)
    assert not label_names_dc("Attack +7 vs. Defense", 12)
    assert not label_names_dc("Toughness vs. 18", 15)
    save = {"label": "Toughness vs. 18", "die": 6, "bonus": 4, "penalty": 0, "dc": 18}
    assert "vs DC" not in roll_headline({**save, "degree": -2})
    attack = {"label": "Attack +7 vs. Defense", "die": 14, "bonus": 7, "penalty": 0, "dc": 12}
    assert "vs DC 12" in roll_headline({**attack, "degree": 2})
    html = dice_roller.readout_html("Toughness vs. 18", 6, 4, 18, -2)
    assert "vs DC" not in html


def test_the_grade_takes_its_dot_with_it_when_it_wraps() -> None:
    roll = {"label": "", "die": 3, "bonus": 4, "penalty": 0, "dc": 15, "degree": -2}
    headline = roll_headline(roll)
    assert "white-space:nowrap'>· Failure" in headline
    assert " · " not in headline


def test_a_cards_star_is_one_line_of_text_tall(qapp) -> None:
    star = QuickRollStar()
    border = int(theme.metric("border.width.emphasis"))
    assert star.height() == star.fontMetrics().height() + 2 * border


def test_the_preview_follows_a_loaded_save(qapp) -> None:
    view = DiceRollerView()
    panel = view.panel
    panel.load_spec(RollSpec(label="Toughness vs. 18", modifier=5, dc=18))
    # The chip and the DC box say 18: the preview does not say it a third time.
    assert panel.staged_preview()[0] == "d20 + 5"

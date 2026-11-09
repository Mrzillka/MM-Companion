"""The roller polish pass (2026-11-10): what each change promises, one section apiece.

Pills that say what they roll and at what; a die that glows with how the roll went; a
maneuver line that says what its slider trades; results that never wrap inside a phrase;
keyboard shortcuts; a DC that stands out once set; a quiet ✕ on history cards; the time
a roll was made; and, in a wide row, the quick rolls under the controls.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from PySide6.QtCore import QPointF
from PySide6.QtGui import QEnterEvent, QKeySequence, QShortcut
from PySide6.QtWidgets import QAbstractButton, QApplication, QLabel, QPushButton

from mm_companion.core.character import Character
from mm_companion.core.powers import ModifierSelection, Power, PowerEffectInstance
from mm_companion.core.rules import maneuver_trade
from mm_companion.ui import dice_roller, theme
from mm_companion.ui.dice_roller import (
    DC_NONE,
    REROLL_SHORTCUT,
    ROLL_SHORTCUT,
    TAKE_TEN_SHORTCUT,
    outcome_token,
    readout_html,
)
from mm_companion.ui.roll_history import (
    GAP_SECONDS,
    RollHistoryPanel,
    degree_html,
    gap_text,
)
from mm_companion.ui.roller.quick_rolls import quick_label
from mm_companion.ui.roller.view import DiceRollerView
from mm_companion.ui.sections.dice import DiceSection

pytestmark = pytest.mark.usefixtures("workspace")

ALL_OUT = "all_out_attack"


def _hover(widget, inside: bool) -> None:
    point = QPointF(1, 1)
    if inside:
        widget.enterEvent(QEnterEvent(point, point, point))
    else:
        from PySide6.QtCore import QEvent

        widget.leaveEvent(QEvent(QEvent.Type.Leave))


def _remove_buttons(widget) -> list[QPushButton]:
    return [b for b in widget.findChildren(QPushButton) if b.text() == "✕"]


# -- quick-roll pills ------------------------------------------------------------------


def test_a_pill_says_what_it_rolls_and_at_what() -> None:
    assert quick_label({"name": "Perception", "bonus": 7, "penalty": 0}) == "Perception +7"
    assert quick_label({"bonus": 2, "penalty": 0}) == "d20 +2"
    assert quick_label({"bonus": 0, "penalty": 3}) == "d20 -3"


def test_a_pill_is_only_a_little_wider_than_its_caption(qapp: QApplication) -> None:
    view = DiceRollerView()
    view.panel._add_quick_roll({"bonus": 2, "penalty": 0})
    (button,) = [
        b for b in view.panel._quick_container.findChildren(QAbstractButton) if b.text() != "×"
    ]
    assert button.text() == "d20 +2"
    text = button.fontMetrics().horizontalAdvance(button.text())
    air = 2 * (int(theme.metric("space.sm")) + int(theme.metric("focus.width")))
    assert button.sizeHint().width() <= text + air


# -- the die's glow --------------------------------------------------------------------


@pytest.mark.parametrize(
    "die,degree,critical,routine,lifted,expected",
    [
        (14, 1, False, False, False, "tint.better"),
        (6, -2, False, False, False, "tint.worse"),
        (20, 3, True, False, False, "dice.critical"),
        (1, -1, True, False, False, "tint.worse"),
        (20, None, False, False, False, "dice.critical"),
        (1, None, False, False, False, "tint.worse"),
        (12, None, False, False, False, None),
        (20, None, False, False, True, None),  # a lucky re-roll's lifted die
        (10, None, False, True, False, None),  # take 10
    ],
)
def test_the_glow_says_how_the_roll_went(die, degree, critical, routine, lifted, expected) -> None:
    assert outcome_token(die, degree, critical, routine=routine, lifted=lifted) == expected


def test_the_die_glows_after_a_roll_and_settles_back(qapp: QApplication, monkeypatch) -> None:
    monkeypatch.setattr(dice_roller, "roll_d20", lambda *a, **k: 4)
    view = DiceRollerView()
    panel = view.panel
    panel.set_dc(15)
    panel._finish_roll()
    assert panel.outcome == "tint.worse"
    assert theme.color("tint.worse") in panel._die_button.styleSheet()
    assert panel._outcome_timer.isActive()
    panel._outcome_timer.timeout.emit()
    assert panel.outcome is None
    assert panel._die_button.styleSheet() == ""


def test_a_new_roll_puts_the_glow_out(qapp: QApplication, monkeypatch) -> None:
    monkeypatch.setattr(dice_roller, "roll_d20", lambda *a, **k: 20)
    view = DiceRollerView()
    panel = view.panel
    panel._finish_roll()
    assert panel.outcome == "dice.critical"
    panel._start_roll()
    assert panel.outcome is None
    panel._finish_roll()


def test_a_graded_total_wears_its_outcome() -> None:
    html = readout_html("", 6, 4, 18, -2)
    assert f"color:{theme.color('tint.worse')};'><b>10</b>" in html
    assert theme.color("tint.better") not in readout_html("", 6, 4, None, None)


# -- a result never breaks inside a phrase ---------------------------------------------


def test_degrees_and_naturals_hold_together() -> None:
    nowrap = "<span style='white-space:nowrap'>"
    assert degree_html(-2, False, 6) == f"{nowrap}Failure (2 degrees)</span>"
    assert degree_html(4, True, 20) == (
        f"{nowrap}Success (4 degrees)</span> {nowrap}— Nat 20!</span>"
    )
    assert degree_html(None, False, 6) == ""


# -- the maneuver's trade, under its slider --------------------------------------------


def test_a_maneuver_says_what_it_trades(data) -> None:
    assert maneuver_trade(data, ALL_OUT, 3) == "+3 attack, −3 Defense"
    assert maneuver_trade(data, "power_attack", 2) == "+2 effect, −2 attack"
    assert maneuver_trade(data, "aim", 2) == ""


def test_the_lane_line_follows_its_slider(qapp: QApplication, data, monkeypatch) -> None:
    hero = Character.new_default(data)
    hero.abilities["ATK"] = 6
    hero.resistances["DEF"] = 4
    damage = PowerEffectInstance("damage", rank=8)
    damage.extras.append(ModifierSelection("ranged", config={"from": "close"}))
    hero.powers = [Power(name="Blast", effects=[damage])]
    section = DiceSection(data, hero)
    monkeypatch.setattr(
        section.panel, "choose_attack", lambda: section.panel.attack_choices()[0].spec
    )
    section.panel.open_actions().tile(ALL_OUT).click()
    (line,) = section.panel.action_lines()
    assert line.trade() == f"+{line.value()} attack, −{line.value()} Defense"
    line.set_value(2)
    assert line.trade() == "+2 attack, −2 Defense"


# -- keyboard --------------------------------------------------------------------------


def _shortcut(panel, key: str) -> QShortcut:
    return next(s for s in panel.findChildren(QShortcut) if s.key() == QKeySequence(key))


def test_the_roller_rolls_rerolls_and_takes_10_from_the_keyboard(
    qapp: QApplication, instant_die
) -> None:
    view = DiceRollerView()
    panel = view.panel
    _shortcut(panel, ROLL_SHORTCUT).activated.emit()
    assert len(view._local_history.cards()) == 1
    _shortcut(panel, REROLL_SHORTCUT).activated.emit()
    assert len(view._local_history.cards()) == 2
    _shortcut(panel, TAKE_TEN_SHORTCUT).activated.emit()
    assert len(view._local_history.cards()) == 3
    assert "routine" in view._local_history.cards()[0].findChild(QLabel).text()


def test_the_roll_key_does_nothing_while_a_die_tumbles(qapp: QApplication) -> None:
    view = DiceRollerView()
    panel = view.panel
    panel._start_roll()
    assert not panel._shortcut_roll()
    panel._finish_roll()


# -- the DC box ------------------------------------------------------------------------


def test_a_set_dc_is_drawn_as_a_capsule_that_costs_no_room(qapp: QApplication) -> None:
    view = DiceRollerView()
    panel = view.panel
    assert panel._dc_spin.text() == DC_NONE
    resting = panel._dc_pair.sizeHint()
    assert "transparent" in panel._dc_pair.styleSheet()
    panel.set_dc(15)
    assert theme.color("accent.dice") in panel._dc_pair.styleSheet()
    assert panel._dc_pair.sizeHint() == resting
    panel.set_dc(None)
    assert "transparent" in panel._dc_pair.styleSheet()


# -- the quiet ✕ -----------------------------------------------------------------------


def test_a_private_cards_x_shows_only_under_the_pointer(qapp: QApplication, instant_die) -> None:
    view = DiceRollerView()
    view.resize(380, 700)
    view.show()
    view.panel._start_roll()
    (card,) = view._local_history.cards()
    (remove,) = _remove_buttons(card)
    assert remove.isHidden() and remove.sizePolicy().retainSizeWhenHidden()
    _hover(card, True)
    assert not remove.isHidden()
    _hover(card, False)
    assert remove.isHidden()
    remove.click()  # still works for whoever reaches it
    assert view._local_history.cards() == []


def test_the_gms_x_on_a_shared_card_is_quiet_too(qapp: QApplication) -> None:
    panel = RollHistoryPanel(gm=True)
    panel.add_roll(_record(1))
    (card,) = panel.cards()
    (remove,) = _remove_buttons(card)
    assert remove.isHidden()
    _hover(card, True)
    assert not remove.isHidden()


# -- when a roll was made --------------------------------------------------------------


def _record(seq: int, when: datetime | None = None) -> dict:
    record = {
        "seq": seq,
        "player_id": "p1",
        "player_name": "Aria",
        "die": 12,
        "bonus": 6,
        "penalty": 0,
        "dc": None,
        "degree": None,
        "critical": False,
        "label": "",
        "hidden": False,
    }
    if when is not None:
        record["timestamp"] = when.isoformat(timespec="seconds")
    return record


def test_the_pause_is_said_in_words() -> None:
    start = datetime(2026, 11, 10, 20, 0, tzinfo=timezone.utc)
    assert gap_text(None, start) == ""
    assert gap_text(start, start + timedelta(seconds=GAP_SECONDS - 1)) == ""
    assert gap_text(start, start + timedelta(minutes=25)) == "25 min later"
    assert gap_text(start, start + timedelta(hours=2)) == "2 h later"
    assert gap_text(start, start + timedelta(hours=1, minutes=5)) == "1 h 5 min later"
    later = start + timedelta(days=1)
    assert gap_text(start, later) == later.strftime("%a %d %b · %H:%M")


def _gap_lines(card) -> list[str]:
    return [label.text() for label in card.findChildren(QLabel, "timeGap")]


def test_a_card_after_a_pause_says_how_long_it_had_been(qapp: QApplication) -> None:
    start = datetime(2026, 11, 10, 20, 0, tzinfo=timezone.utc)
    panel = RollHistoryPanel()
    panel.set_rolls(
        [
            _record(1, start),
            _record(2, start + timedelta(minutes=2)),
            _record(3, start + timedelta(minutes=30)),
        ]
    )
    newest, middle, oldest = panel.cards()
    assert _gap_lines(newest) == ["⋯ 28 min later"]
    assert _gap_lines(middle) == [] and _gap_lines(oldest) == []
    local = (start + timedelta(minutes=30)).astimezone()
    assert newest.toolTip() == f"{local:%H:%M}"


def test_a_record_with_no_time_is_left_unstamped(qapp: QApplication) -> None:
    panel = RollHistoryPanel()
    panel.add_roll(_record(1))
    (card,) = panel.cards()
    assert card.when is None and card.toolTip() == "" and _gap_lines(card) == []


def test_a_private_roll_carries_its_time(qapp: QApplication, instant_die) -> None:
    view = DiceRollerView()
    view.panel._start_roll()
    (card,) = view._local_history.cards()
    assert card.when is not None and card.toolTip() == f"{card.when:%H:%M}"


# -- in a row, the quick rolls go under the controls -----------------------------------


def test_a_row_puts_the_quick_rolls_under_the_controls(qapp: QApplication) -> None:
    view = DiceRollerView()
    panel = view.panel
    panel._add_quick_roll({"bonus": 2, "penalty": 0})
    panel.force_reflow(True)
    assert panel._settings_grid.indexOf(panel._quick_part) >= 0
    assert panel._box.indexOf(panel._quick_part) < 0
    panel.force_reflow(False)
    assert panel._settings_grid.indexOf(panel._quick_part) < 0
    order = [
        panel._box.itemAt(i).widget()
        for i in range(panel._box.count())
        if panel._box.itemAt(i).widget() is not None
    ]
    assert order == [panel._settings_part, panel._die_part, panel._quick_part]


def test_a_row_is_two_columns_wide_not_three(qapp: QApplication) -> None:
    view = DiceRollerView()
    panel = view.panel
    panel._add_quick_roll({"bonus": 2, "penalty": 0})
    controls = max(
        panel._settings_part.minimumSizeHint().width(),
        panel._quick_part.minimumSizeHint().width(),
    )
    expected = controls + panel.REFLOW_SPACING + panel._die_part.sizeHint().width()
    assert panel.row_minimum_width() == expected
    # ...and the same whether or not they have moved in yet.
    panel.force_reflow(True)
    assert panel.row_minimum_width() == expected


def test_compact_takes_the_quick_rolls_out_from_under_the_controls(qapp: QApplication) -> None:
    view = DiceRollerView()
    panel = view.panel
    panel._add_quick_roll({"bonus": 2, "penalty": 0})
    panel.force_reflow(True)
    panel.set_compact(True)
    assert panel._pair_box.indexOf(panel._quick_part) >= 0
    assert panel._settings_grid.indexOf(panel._quick_part) < 0
    panel.set_compact(False)
    assert panel._pair_box.indexOf(panel._quick_part) < 0

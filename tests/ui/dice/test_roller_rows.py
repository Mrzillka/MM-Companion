"""The roller's rows after the actions-feedback pass (the user's list, 2026-11-13).

Every row is a ``FieldRow``: with sliders chosen the DC has one too, every box keeps
its own small width, Bonus and Penalty can stand in for the one Modifier row, an
emptied DC box means none ("—"), the steps shed to ±1 when narrow, and Aim throws its
two dice one after the other.
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QFocusEvent
from PySide6.QtWidgets import QApplication

import mm_companion.ui.dice_roller as roller_module
from mm_companion.core import storage
from mm_companion.core.rules import RollSpec
from mm_companion.ui.dice_roller import DC_NONE, DiceRollerPanel
from mm_companion.ui.roll_history import breakdown_lines, roll_working


def _panel(qapp) -> DiceRollerPanel:
    panel = DiceRollerPanel()
    panel.set_modifier_steps([1, 5])
    panel.resize(420, 500)
    panel.show()
    qapp.processEvents()
    return panel


def test_with_sliders_the_dc_has_one_too(qapp) -> None:
    panel = _panel(qapp)
    panel.set_modifier_input(storage.MODIFIER_INPUT_SLIDER)
    qapp.processEvents()

    assert panel._dc_slider.isVisible()
    assert not any(step.isVisible() for step in panel._dc_step_buttons)
    panel._dc_slider.setValue(17)
    assert panel.dc() == 17


def test_every_box_keeps_one_small_width(qapp) -> None:
    panel = _panel(qapp)
    panel.set_modifier_input(storage.MODIFIER_INPUT_SLIDER)
    panel.resize(700, 500)
    qapp.processEvents()

    assert panel._modifier_spin.width() == panel._dc_spin.width()
    # The slider, not the box, takes the row's spare width.
    assert panel._modifier_slider.width() > 2 * panel._modifier_spin.width()


def test_an_emptied_dc_box_means_none(qapp) -> None:
    panel = _panel(qapp)
    panel.set_dc(15)
    edit = panel._dc_spin.lineEdit()
    edit.setText("")
    QApplication.sendEvent(
        panel._dc_spin, QFocusEvent(QEvent.Type.FocusOut, Qt.FocusReason.OtherFocusReason)
    )

    assert panel.dc() is None
    assert edit.text() == DC_NONE == "—"


def test_bonus_and_penalty_rows_make_the_modifier(qapp) -> None:
    panel = _panel(qapp)
    panel.set_modifier_split(True)
    qapp.processEvents()
    assert panel._bonus_spin.isVisible() and not panel._modifier_spin.isVisible()

    panel._bonus_spin.setValue(5)
    panel._penalty_spin.setValue(2)
    assert panel.adjustment() == 3
    panel.add_bonus(2)  # Extra Effort's +2, a quick roll: lands in Bonus
    assert (panel._bonus_spin.value(), panel._penalty_spin.value()) == (7, 2)
    panel._modifier_spin.setValue(0)  # a roll spends it
    assert (panel._bonus_spin.value(), panel._penalty_spin.value()) == (0, 0)


def test_narrow_the_outer_steps_go(qapp) -> None:
    panel = _panel(qapp)
    controls = panel._controls
    panel.resize(160, 500)
    qapp.processEvents()

    assert controls.tight
    shown = [step.text() for step in panel._step_buttons if step.isVisible()]
    assert shown == ["−1", "+1"]
    panel.resize(420, 500)
    qapp.processEvents()
    assert not controls.tight


def test_aim_says_both_dice_and_which_was_kept() -> None:
    working = roll_working(15, 4, other_die=4)
    assert "best of 4 and 15" in working and "19" in working
    lines = dict(breakdown_lines({"die": 15, "other_die": 4, "bonus": 4}))
    assert lines["Two d20s (Aim)"] == "4 and 15"
    assert lines["Kept, the higher"] == "15"


def test_aim_rests_on_the_first_die_before_the_second(qapp, monkeypatch) -> None:
    faces = iter([4, 15])
    monkeypatch.setattr(roller_module, "roll_d20", lambda: next(faces))
    panel = _panel(qapp)
    panel.load_spec(RollSpec(label="Blast", modifier=4, roll_twice=True))

    panel._start_roll()
    assert panel._aim_stage == 1
    assert panel._land_first_die()
    assert panel._face.text() == "4" and "First die" in panel._readout.text()
    panel._throw_second_die()
    assert panel._aim_stage == 3
    panel._finish_roll()

    assert panel._face.text() == "15"
    assert "best of 4 and 15" in panel._readout.text()


# -- typing into the boxes (the user's report, 2026-11-13) ------------------------------


def _blur(box) -> None:
    QApplication.sendEvent(box, QFocusEvent(QEvent.Type.FocusOut, Qt.FocusReason.OtherFocusReason))


def test_an_emptied_modifier_bonus_or_penalty_box_clears_at_once(qapp) -> None:
    panel = _panel(qapp)
    panel._modifier_spin.setValue(4)
    panel._modifier_spin.lineEdit().setText("")
    assert panel.adjustment() == 0  # at once, not on leaving the box
    _blur(panel._modifier_spin)
    assert panel._modifier_spin.lineEdit().text() == "+0"

    panel.set_modifier_split(True)
    for box in (panel._bonus_spin, panel._penalty_spin):
        box.setValue(3)
        box.lineEdit().setText("")
        _blur(box)
        assert box.value() == 0 and box.lineEdit().text() == "0"


def test_a_focused_box_has_its_value_selected_so_typing_replaces_it(qapp) -> None:
    panel = _panel(qapp)
    panel.activateWindow()
    for box in (panel._modifier_spin, panel._dc_spin):
        QApplication.sendEvent(
            box, QFocusEvent(QEvent.Type.FocusIn, Qt.FocusReason.MouseFocusReason)
        )
        qapp.processEvents()  # the selection follows the click that focused it
        edit = box.lineEdit()
        assert edit.selectedText() == edit.text()


# -- the Actions popup's Show more ------------------------------------------------------


def test_show_more_hides_the_rest_and_is_remembered(qapp, data) -> None:
    from mm_companion.core.character import Character
    from mm_companion.ui.sections.dice import DiceSection

    section = DiceSection(data, Character.new_default(data))
    panel = section.panel
    assert storage.actions_show_more() is False
    popup = panel.open_actions()
    shown = {t.key for t in popup.shown_tiles()}
    assert {"attack", "aim", "defend", "grab", "impress", "request", "reroll"} <= shown
    assert not {"finishing_attack", "take_ten", "recover"} & shown
    closed = popup.height()

    popup.more_check.setChecked(True)
    assert {"finishing_attack", "take_ten", "recover"} <= {t.key for t in popup.shown_tiles()}
    assert popup.height() > closed
    assert storage.actions_show_more() is True
    popup.close()
    qapp.processEvents()

    again = panel.open_actions()
    assert again.more_shown and again.more_check.isChecked()
    again.close()

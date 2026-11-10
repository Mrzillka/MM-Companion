"""The roller's two rows of controls (the user's layout, 2026-11-11).

    Modifier  [−5][−1] [ +0 ] [+1][+5]   Actions ▾
    DC        [−5][−1] [ —  ] [+1][+5]   Take 10
    (the action lane, one line per maneuver)

See ``mm_companion.ui.roller.controls`` and "The two-row pass" in the dice notes.
"""

from __future__ import annotations

from PySide6.QtWidgets import QApplication

from mm_companion.core import storage
from mm_companion.core.data_loader import load_game_data
from mm_companion.ui.roller.view import DiceRollerView


def _settled(qapp: QApplication, view, width: int, height: int = 800) -> None:
    view.resize(width, height)
    for _ in range(4):
        qapp.processEvents()


def _top(widget, panel) -> int:
    return widget.mapTo(panel, widget.rect().topLeft()).y()


def _left(widget, panel) -> int:
    return widget.mapTo(panel, widget.rect().topLeft()).x()


def _view(qapp: QApplication, width: int = 380) -> DiceRollerView:
    view = DiceRollerView()
    view.show()
    view.panel.set_modifier_steps((1, 5))
    _settled(qapp, view, width)
    return view


# -- the two rows -----------------------------------------------------------------------


def test_narrow_the_buttons_drop_to_a_row_of_their_own(qapp: QApplication) -> None:
    # 480: the panel stacked over its history, the controls the full width.
    view = _view(qapp, 480)
    panel = view.panel
    controls = panel._controls
    assert controls.wide
    wide_minimum = controls.minimumSizeHint().width()

    # Narrower than the rows and the buttons beside them: the buttons go under the DC.
    _settled(qapp, view, controls.row_minimum_width() - 40)
    assert not controls.wide
    under = _top(panel._dc_spin, panel) + panel._dc_spin.height()
    assert _top(panel._routine_button, panel) >= under
    assert _top(panel._actions_button, panel) == _top(panel._routine_button, panel)
    # Take 10 on the left, Actions on the right, where it was.
    assert _left(panel._routine_button, panel) < _left(panel._actions_button, panel)
    # The minimum is the narrow shape's whichever is showing: never an adaptive one.
    assert controls.minimumSizeHint().width() == wide_minimum

    # Nothing in the narrow shape is squeezed under its own minimum.
    for widget in (panel._modifier_label, panel._dc_spin, *panel._step_buttons):
        assert widget.width() >= (widget.minimumWidth() or widget.minimumSizeHint().width())

    # And back, once there is the room again.
    _settled(qapp, view, 480)
    assert controls.wide


def test_the_controls_ask_for_the_wide_shape(qapp: QApplication) -> None:
    # A preference, so the roller is offered the width to have the buttons beside.
    view = _view(qapp, 480)
    controls = view.panel._controls
    assert controls.sizeHint().width() >= controls.row_natural_width()


def test_the_lane_grows_under_both_rows(qapp: QApplication) -> None:
    # Neither button nor the DC moves when a pick puts a line on the lane.
    view = _view(qapp)
    panel = view.panel
    before = {w: _top(w, panel) for w in (panel._routine_button, panel._dc_spin)}
    panel.start_action_line("charge", "Charge", fixed="-2 attack")
    qapp.processEvents()
    assert {w: _top(w, panel) for w in before} == before
    assert _top(panel._lane, panel) > _top(panel._routine_button, panel)


def test_in_slider_mode_the_modifier_box_still_lines_up_with_the_dc_box(
    qapp: QApplication,
) -> None:
    view = _view(qapp)
    panel = view.panel
    panel.set_modifier_input(storage.MODIFIER_INPUT_SLIDER)
    _settled(qapp, view, 380)
    assert panel._modifier_slider.isVisible()
    assert not any(step.isVisible() for step in panel._step_buttons)
    # The DC keeps its steps: a slider is no way to hit DC 17.
    assert all(step.isVisible() for step in panel._dc_step_buttons)
    assert _left(panel._modifier_spin, panel) == _left(panel._dc_spin, panel)
    assert _left(panel._modifier_slider, panel) < _left(panel._modifier_spin, panel)


# -- the DC's steps ---------------------------------------------------------------------


def _dc_step(panel, caption: str):
    return next(b for b in panel._dc_step_buttons if b.text() == caption)


def test_a_dc_step_up_from_not_set_lands_on_the_starting_dc(qapp: QApplication) -> None:
    view = _view(qapp)
    panel = view.panel
    assert panel.dc() is None
    _dc_step(panel, "−5").click()
    assert panel.dc() is None  # nothing below "not set"
    _dc_step(panel, "+1").click()
    assert panel.dc() == 10
    _dc_step(panel, "+5").click()
    assert panel.dc() == 15
    _dc_step(panel, "−1").click()
    assert panel.dc() == 14
    panel.set_dc(3)
    _dc_step(panel, "−5").click()
    assert panel.dc() is None  # down past the bottom is "not set" again

    panel.set_dc_start(15)
    _dc_step(panel, "+5").click()
    assert panel.dc() == 15


def test_the_starting_dc_is_data() -> None:
    assert load_game_data().system.dc_start == 10


# -- the action lane's lines ------------------------------------------------------------


def _all_out(panel, maximum: int = 5):
    return panel.start_action_line(
        "all_out",
        "All-Out Attack",
        limit=lambda _spec: (maximum, ""),
        describe=lambda value: f"+{value} attack, −{value} Defense",
    )


def test_a_maneuver_line_is_one_line_of_steps(qapp: QApplication) -> None:
    view = _view(qapp)
    panel = view.panel
    line = _all_out(panel, maximum=3)
    line.bind(None)
    qapp.processEvents()
    # Name, trade, − value +, ✕: all on the one line.
    tops = {
        _top(w, line) + w.height() // 2
        for w in (line._name, line._trade, line._minus, line._spin, line._plus, line._close)
    }
    assert max(tops) - min(tops) <= 2
    assert not line._slider.isVisible()
    assert _left(line._close, line) > _left(line._plus, line)

    line.set_value(3)
    assert not line._plus.isEnabled()  # at the cap
    assert line._minus.isEnabled()
    line._minus.click()
    assert line.value() == 2
    assert line.trade() == "+2 attack, −2 Defense"
    line._plus.click()
    assert line.value() == 3


def test_with_the_slider_chosen_a_maneuver_line_has_its_slider(qapp: QApplication) -> None:
    view = _view(qapp)
    panel = view.panel
    panel.set_modifier_input(storage.MODIFIER_INPUT_SLIDER)
    line = _all_out(panel)
    line.bind(None)
    qapp.processEvents()
    assert line.input_style == storage.MODIFIER_INPUT_SLIDER
    assert line._slider.isVisible()
    assert not line._minus.isVisible() and not line._plus.isVisible()
    # The trade goes under the slider.
    assert _top(line._trade, line) > _top(line._slider, line)

    # And back to steps, live, with the setting.
    panel.set_modifier_input(storage.MODIFIER_INPUT_BUTTONS)
    qapp.processEvents()
    assert line._minus.isVisible() and not line._slider.isVisible()

"""A spin box's arrows must stay clickable under every preset.

Qt's box model is all-or-nothing on a complex widget. The moment the application
stylesheet states a ``border``, a ``padding`` or a ``background`` on a
``QSpinBox``, ``QStyleSheetStyle`` — not the platform style — computes
``SC_SpinBoxEditField`` from the box's own padding rect, which knows nothing about
the arrows. It came back spanning the whole widget, so the line edit was laid over
both arrow buttons: the arrows still painted, still looked correct, and swallowed
every click aimed at them. The correction is a ``padding-right`` the width of the
arrow column, measured from the style — see
:func:`mm_companion.ui.theme.arrow_columns`.

:mod:`tests.test_theme_qss` guards the *rule* (a box on a complex widget gives its
arrow column back). This file guards the *consequence*, by asking QStyle for the
rects the mouse is actually tested against.

**Why these tests sweep the styles, and what they are worth on CI.** Whether the
trap springs is a property of the *base style*, not of the platform. Of the four
styles Qt ships it reproduces under ``windows11`` alone: Crimson & Gold (and every
other styled preset) at all times, and Classic only while a box holds keyboard
focus, which is the focus ring's border doing it — and the wheel guard means a
spin box is focused whenever anyone is about to use it. ``windowsvista``,
``Windows`` and ``Fusion`` never showed it.

CI runs offscreen on Linux, which has no ``windows11``, so **on CI these assertions
pass whether or not the bug is present** and the QSS-level rule in
``test_theme_qss.py`` is what actually protects the fix there. They are kept, and
swept across every style rather than pinned to one, because on a Windows machine —
where every user of this app is — they are the direct proof, and because a future
Qt may well spread the behaviour to another style.
"""

from __future__ import annotations

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QLineEdit,
    QSpinBox,
    QStyle,
    QStyleFactory,
    QStyleOptionComboBox,
    QStyleOptionSpinBox,
    QVBoxLayout,
    QWidget,
)

from mm_companion.ui import theme
from mm_companion.ui.lock import set_widget_locked
from mm_companion.ui.widgets import make_spin_box

PRESETS = ["classic", "slate-dark", "parchment-light", "crimson-gold"]


@pytest.fixture(params=QStyleFactory.keys())
def styled_app(request):
    """The app under one of Qt's base styles, with the previous one put back.

    Nothing in ``src/`` ever calls ``setStyle``, so this is the tests reaching
    outside the app's own choices on purpose — see the module docstring. The
    restore matters: the style is application-global and would otherwise leak into
    every test that ran afterwards.
    """
    app = QApplication.instance() or QApplication([])
    previous_style = app.style().objectName()
    previous_sheet = app.styleSheet()
    app.setStyle(QStyleFactory.create(request.param))
    # Remembered rather than read back: once a stylesheet is installed, app.style()
    # is the QStyleSheetStyle proxy wrapping the base one and its objectName is
    # empty, so a failure message would name no style at all.
    app.setProperty("test_base_style", request.param)
    yield app
    app.setStyleSheet(previous_sheet)
    restored = QStyleFactory.create(previous_style)
    if restored is not None:
        app.setStyle(restored)


def _dress(app, preset: str) -> None:
    theme.set_active_theme(preset)
    theme.reset()
    theme.apply(app)


def _spin_rects(spin: QSpinBox) -> tuple:
    option = QStyleOptionSpinBox()
    option.initFrom(spin)
    option.subControls = QStyle.SubControl.SC_All
    option.buttonSymbols = spin.buttonSymbols()
    option.frame = spin.hasFrame()
    style = spin.style()
    return tuple(
        style.subControlRect(QStyle.ComplexControl.CC_SpinBox, option, sub_control, spin)
        for sub_control in (
            QStyle.SubControl.SC_SpinBoxUp,
            QStyle.SubControl.SC_SpinBoxDown,
            QStyle.SubControl.SC_SpinBoxEditField,
        )
    )


def _combo_rects(combo: QComboBox) -> tuple:
    option = QStyleOptionComboBox()
    option.initFrom(combo)
    option.subControls = QStyle.SubControl.SC_All
    style = combo.style()
    return tuple(
        style.subControlRect(QStyle.ComplexControl.CC_ComboBox, option, sub_control, combo)
        for sub_control in (
            QStyle.SubControl.SC_ComboBoxArrow,
            QStyle.SubControl.SC_ComboBoxEditField,
        )
    )


def _under(app, preset: str) -> str:
    """Which preset on which base style — both halves are needed to reproduce."""
    return f"{preset} on {app.property('test_base_style')}: "


def _spin_box(app, width: int = 120) -> QSpinBox:
    spin = QSpinBox()
    spin.setRange(0, 999)
    spin.setValue(150)
    spin.ensurePolished()
    spin.resize(width, spin.sizeHint().height())
    app.processEvents()
    return spin


def _bare_up_arrow_centre(app, width: int, height: int):
    """Where the arrow is *painted*, per the bare platform style.

    Taken with no sheet installed, because that is the only place a person can aim
    — the drawing is the platform's, whatever the stylesheet later believes.
    """
    app.setStyleSheet("")
    probe = QSpinBox()
    probe.ensurePolished()
    probe.resize(width, height)
    option = QStyleOptionSpinBox()
    option.initFrom(probe)
    option.subControls = QStyle.SubControl.SC_All
    option.buttonSymbols = probe.buttonSymbols()
    option.frame = probe.hasFrame()
    rect = probe.style().subControlRect(
        QStyle.ComplexControl.CC_SpinBox, option, QStyle.SubControl.SC_SpinBoxUp, probe
    )
    probe.deleteLater()
    return rect.center()


@pytest.mark.parametrize("preset", PRESETS)
def test_clicking_the_up_arrow_actually_increments(styled_app, preset: str) -> None:
    """The whole bug, in the terms it was reported in: the arrow did nothing.

    The click is routed the way a real cursor is — to the topmost child under the
    point. That is the step that broke: with the edit field laid over the buttons,
    the child under the arrow was the ``QLineEdit``, which happily took the click
    and ignored it. ``QTest.mouseClick(spin, ...)`` posts straight to the spin box
    and would skip exactly the thing being tested, so it is not used here.
    """
    host = QWidget()
    layout = QVBoxLayout(host)
    spin = QSpinBox()
    spin.setRange(0, 999)
    spin.setValue(10)
    layout.addWidget(spin)
    host.resize(200, 70)
    host.show()
    styled_app.processEvents()

    point = _bare_up_arrow_centre(styled_app, spin.width(), spin.height())
    _dress(styled_app, preset)

    target = spin.childAt(point) or spin
    QTest.mouseClick(
        target,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
        target.mapFrom(spin, point),
    )
    styled_app.processEvents()

    where = _under(styled_app, preset)
    assert not isinstance(target, QLineEdit), f"{where}the text field is sitting on the up arrow"
    assert spin.value() > 10, f"{where}clicking the up arrow did nothing"


@pytest.mark.parametrize("preset", PRESETS)
def test_a_spin_boxs_arrows_do_not_sit_under_its_text(styled_app, preset: str) -> None:
    """The edit field must stop where the buttons start, or the arrows are dead."""
    _dress(styled_app, preset)

    spin = _spin_box(styled_app)
    up, down, edit = _spin_rects(spin)
    where = _under(styled_app, preset)

    assert not edit.intersects(up), f"{where}the text field covers the up arrow"
    assert not edit.intersects(down), f"{where}the text field covers the down arrow"
    assert up.isValid() and down.isValid(), f"{where}an arrow button has no rect at all"
    assert edit.width() > 0, f"{where}no room left to show the value"


@pytest.mark.parametrize("preset", PRESETS)
def test_a_focused_spin_boxs_arrows_survive_the_focus_ring(styled_app, preset: str) -> None:
    """The ring is emitted for *every* preset, and a border is a box statement.

    So Classic was correct until the moment somebody clicked into a field — and the
    wheel guard means focusing one is the normal way to start using it.
    """
    _dress(styled_app, preset)

    host = QWidget()
    layout = QVBoxLayout(host)
    spin = QSpinBox()
    spin.setRange(0, 999)
    layout.addWidget(spin)
    host.resize(200, 80)
    host.show()
    spin.setFocus()
    styled_app.processEvents()
    assert spin.hasFocus()

    up, down, edit = _spin_rects(spin)
    where = _under(styled_app, preset)
    assert not edit.intersects(up), f"{where}focused, the text field covers the up arrow"
    assert not edit.intersects(down), f"{where}focused, the text field covers the down arrow"


@pytest.mark.parametrize("preset", PRESETS)
def test_a_combo_boxs_arrow_does_not_sit_under_its_text(styled_app, preset: str) -> None:
    _dress(styled_app, preset)

    combo = QComboBox()
    combo.addItems(["Alpha", "Beta"])
    combo.ensurePolished()
    combo.resize(180, combo.sizeHint().height())
    styled_app.processEvents()

    arrow, edit = _combo_rects(combo)
    where = _under(styled_app, preset)
    assert not edit.intersects(arrow), f"{where}the text covers the dropdown arrow"
    assert arrow.isValid(), f"{where}the dropdown arrow has no rect at all"


@pytest.mark.parametrize("preset", PRESETS)
def test_a_spin_box_with_no_arrows_pays_nothing_for_them(styled_app, preset: str) -> None:
    """The sheet's rank grids are built ``buttons=False`` and are only ~56px wide.

    A 50px arrow column charged to one of those would be most of the cell, so
    ``make_spin_box`` marks them and the sheet hands the room straight back.
    """
    _dress(styled_app, preset)

    with_arrows = _spin_box(styled_app)
    without = make_spin_box(0, 999, value=150, buttons=False)
    without.ensurePolished()
    without.resize(with_arrows.width(), with_arrows.height())
    styled_app.processEvents()

    assert without.property(theme.ARROWLESS_PROPERTY) is True
    assert _spin_rects(without)[2].width() >= _spin_rects(with_arrows)[2].width(), (
        f"{_under(styled_app, preset)}an arrowless spin box is still paying for an " f"arrow column"
    )


@pytest.mark.parametrize("preset", PRESETS)
def test_a_locked_spin_box_gives_its_whole_width_to_the_value(styled_app, preset: str) -> None:
    """Locking is a *view* mode: the field should read as a label, not as a form.

    ``setFrame``/``setButtonSymbols`` alone cannot do that under a styled preset —
    the application sheet's border, radius and padding outrank both — so
    :mod:`mm_companion.ui.lock` applies a widget-level sheet as well.
    """
    _dress(styled_app, preset)

    spin = _spin_box(styled_app)
    unlocked_edit = _spin_rects(spin)[2]
    where = _under(styled_app, preset)

    set_widget_locked(spin, True)
    spin.ensurePolished()
    styled_app.processEvents()

    assert (
        _spin_rects(spin)[2].width() > unlocked_edit.width()
    ), f"{where}a locked spin box still reserves room for arrows it no longer draws"

    set_widget_locked(spin, False)
    spin.ensurePolished()
    styled_app.processEvents()

    assert (
        _spin_rects(spin)[2].width() == unlocked_edit.width()
    ), f"{where}unlocking did not put the arrow column back"

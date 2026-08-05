"""Compact mode: the window collapsed to just the dice roller.

The claim worth testing is not "a small window appears" but that **it is the same
roller**: the mini window borrows the live panel and history rather than building
a second pair, so a loaded spec, the quick-roll strip and — the one that would
really show at a table — the session history all survive the round trip, and the
sheet's bus still reaches the roller while it is away.

Most of these assert on the *resting* state, which the autouse fixture in
``conftest`` buys by zeroing the transition. The one test that is about the
animation restores a real duration and steps it by hand, since waiting on Qt's
animation timer is both slow and unreliable under the full suite.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtCore import QAbstractAnimation, QPropertyAnimation, QRect, Qt
from PySide6.QtWidgets import QApplication

from mm_companion.core import storage
from mm_companion.core.rules import RollSpec
from mm_companion.ui import theme
from mm_companion.ui.dice_roller import DiceRollerPanel
from mm_companion.ui.gm_window import GMWindow
from mm_companion.ui.main_window import MainWindow
from mm_companion.ui.session_bridge import set_active_session


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point settings at an empty temp dir so quick rolls and preferences start fresh."""
    monkeypatch.setenv(storage.HOME_ENV_VAR, str(tmp_path))
    storage.ensure_workspace()
    return tmp_path


@pytest.fixture(autouse=True)
def _clear_active_session():
    yield
    set_active_session(None)


@pytest.fixture
def window(qapp: QApplication) -> MainWindow:
    made = MainWindow(locked=False)
    yield made
    made._dirty = False  # a "save your changes?" modal would hang the teardown


def _settle(qapp: QApplication, times: int = 25) -> None:
    for _ in range(times):
        qapp.processEvents()


def _running_animation(win) -> QPropertyAnimation:
    """The geometry ease currently in flight.

    Not ``findChild``: the previous transition's animation is stopped but only
    *scheduled* for deletion, and a test never runs the event loop that would
    collect it — so the first match can easily be the last one.
    """
    running = [
        animation
        for animation in win.findChildren(QPropertyAnimation)
        if animation.state() == QAbstractAnimation.State.Running
    ]
    assert len(running) == 1
    return running[0]


def _on_screen(qapp: QApplication, win) -> None:
    """Give *win* real geometry without a native window (see test_block_canvas)."""
    win.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
    win.resize(900, 700)
    win.show()
    _settle(qapp)


# -- borrowing the roller ----------------------------------------------------


def test_entering_compact_moves_the_roller_into_the_mini_page(window: MainWindow) -> None:
    panel = window.sheet.dice.panel
    history = window.sheet.dice.view._history_part

    window._compact.enter()

    assert window._compact.is_compact
    page = window._compact.page
    assert page.isAncestorOf(panel)
    assert page.isAncestorOf(history)
    # The sheet is hidden, which is what frees the window from its minimum width.
    assert window.sheet.isHidden()
    assert not page.isHidden()
    assert window.menuBar().isHidden()


def test_leaving_puts_the_roller_back_in_its_block(window: MainWindow) -> None:
    view = window.sheet.dice.view
    panel = view.panel

    window._compact.enter()
    window._compact.leave()

    assert not window._compact.is_compact
    assert view.isAncestorOf(panel)
    assert view.isAncestorOf(view._history_part)
    assert not window.sheet.isHidden()
    assert window._compact.page.isHidden()
    assert not window.menuBar().isHidden()


def test_the_mini_roller_is_the_same_roller(window: MainWindow) -> None:
    """The whole point: state carries across because the widgets do."""
    panel = window.sheet.dice.panel
    panel.load_spec(RollSpec(label="Athletics", modifier=9))
    panel.toggle_quick_roll({"bonus": 3, "penalty": 0, "dc": 15})

    window._compact.enter()

    assert window.sheet.dice.panel is panel
    assert panel.current_spec() is not None
    assert panel.current_spec().label == "Athletics"
    assert len(panel.quick_roll_keys()) == 1

    window._compact.leave()

    assert window.sheet.dice.panel is panel
    assert panel.current_spec().label == "Athletics"
    assert len(panel.quick_roll_keys()) == 1


def test_the_sheet_still_reaches_the_roller_while_it_is_away(window: MainWindow) -> None:
    """A stat row clicked on the sheet behind loads into the mini window's chip."""
    window._compact.enter()

    window.sheet.dice.load_roll(RollSpec(label="Toughness", modifier=8))

    assert window.sheet.dice.panel.current_spec().label == "Toughness"


def test_a_sheet_with_no_roller_has_no_compact_mode(window: MainWindow) -> None:
    """A surface that lends nothing leaves the window alone rather than emptying it."""
    window.sheet.release_roller = lambda: None

    window._compact.enter()

    assert not window._compact.is_compact
    assert not window.sheet.isHidden()


# -- the compact arrangement -------------------------------------------------


def test_the_panel_puts_the_quick_rolls_beside_the_die(window: MainWindow) -> None:
    panel = window.sheet.dice.panel

    window._compact.enter()

    # The requested shape: the Roll box across the top, the other two side by side.
    assert panel._quick_part.parentWidget() is panel._pair
    assert panel._die_part.parentWidget() is panel._pair
    assert panel._settings_part.parentWidget() is panel
    assert panel._pair.isVisibleTo(panel)

    window._compact.leave()

    assert panel._quick_part.parentWidget() is panel
    assert panel._die_part.parentWidget() is panel
    assert not panel._pair.isVisibleTo(panel)


def test_the_die_shrinks_and_grows_back(window: MainWindow) -> None:
    panel = window.sheet.dice.panel
    padding = int(theme.metric("die.padding"))
    full = int(theme.metric("die.size")) + padding
    compact = int(theme.metric("die.size.compact")) + padding
    assert panel._die_button.width() == full

    window._compact.enter()
    assert panel._die_button.width() == compact

    window._compact.leave()
    assert panel._die_button.width() == full


def test_the_compact_shape_survives_a_resize(qapp: QApplication, window: MainWindow) -> None:
    """The width-driven reflow stands down while the chosen shape is in force."""
    panel = window.sheet.dice.panel
    _on_screen(qapp, window)
    window._compact.enter()
    _settle(qapp)

    window.resize(900, 400)  # wide and short: a reflow would call this a row
    _settle(qapp)

    assert panel._quick_part.parentWidget() is panel._pair


def test_the_mini_page_may_be_dragged_smaller_than_the_roller(window: MainWindow) -> None:
    """Below the floor the roller scrolls; it never holds the window open."""
    panel = window.sheet.dice.panel
    page = window._compact.page
    floor = page._scroll.minimumSize()

    assert floor.width() == int(theme.metric("compact.min-width"))
    assert floor.height() == int(theme.metric("compact.min-height"))

    window._compact.enter()

    # The roller is well wider than that, and the page still does not ask for its
    # width: a scroll area does not pass its child's minimum on, which is the whole
    # reason the roller is inside one.
    assert panel.minimumSizeHint().width() > floor.width()
    assert page.minimumSizeHint().width() <= floor.width() + page.strip.sizeHint().width()


# -- the window itself -------------------------------------------------------


def test_the_mini_window_is_frameless_and_on_top_by_default(window: MainWindow) -> None:
    window._compact.enter()

    flags = window.windowFlags()
    assert flags & Qt.WindowType.FramelessWindowHint
    assert flags & Qt.WindowType.WindowStaysOnTopHint

    window._compact.leave()

    flags = window.windowFlags()
    assert not (flags & Qt.WindowType.FramelessWindowHint)
    assert not (flags & Qt.WindowType.WindowStaysOnTopHint)


def test_the_pin_is_remembered_between_sessions(window: MainWindow) -> None:
    window._compact.enter()
    window._compact.page.strip._pin_button.setChecked(False)

    assert storage.compact_settings()["on_top"] is False
    assert not (window.windowFlags() & Qt.WindowType.WindowStaysOnTopHint)

    window._compact.leave()
    window._compact.enter()

    assert not (window.windowFlags() & Qt.WindowType.WindowStaysOnTopHint)
    assert window._compact.page.strip._pin_button.isChecked() is False


def test_the_window_goes_back_to_the_size_it_came_from(window: MainWindow) -> None:
    window.setGeometry(QRect(60, 70, 900, 700))
    before = QRect(window.geometry())

    window._compact.enter()
    assert window.geometry().width() == int(theme.metric("compact.width"))

    window._compact.leave()
    assert window.geometry() == before


def test_the_mini_size_is_remembered_and_the_full_one_is_not_overwritten(
    window: MainWindow,
) -> None:
    """The trap this guards: the layout key is shared by every sheet."""
    window.setGeometry(QRect(40, 50, 880, 660))
    expected = bytes(window.saveGeometry().toBase64()).decode("ascii")
    window._compact.enter()
    window.resize(320, 420)

    window._persist_layout()

    assert storage.compact_settings()["width"] == 320
    assert storage.compact_settings()["height"] == 420
    # And what went into the shared layout key is the full window's shape, byte for
    # byte — not the mini window's, which would open every character at 320x420.
    assert storage.load_settings()["layout"]["window_geometry"] == expected


def test_escape_leaves_compact_and_does_nothing_otherwise(window: MainWindow) -> None:
    assert not window._compact._escape.isEnabled()

    window._compact.enter()
    assert window._compact._escape.isEnabled()

    window._compact._escape.activated.emit()
    assert not window._compact.is_compact
    assert not window._compact._escape.isEnabled()


def test_the_menu_bar_glyph_follows_the_mode_however_it_changed(window: MainWindow) -> None:
    """Driven by the controller, so the roller's own button keeps the tick honest."""
    action = window._compact_action
    assert not action.isChecked()

    window.sheet.dice.panel.compactRequested.emit()

    assert window._compact.is_compact
    assert action.isChecked()
    assert action.text() == "⤢"

    window._compact.page.strip.expandRequested.emit()

    assert not window._compact.is_compact
    assert not action.isChecked()
    assert action.text() == "⤡"


# -- the animation -----------------------------------------------------------


def test_the_window_eases_between_the_two_sizes(qapp: QApplication, window: MainWindow) -> None:
    """Stepped by hand: waiting on Qt's animation timer is slow and unreliable here."""
    window._compact.ANIMATION_MS = 400  # the conftest fixture zeroes it for other tests
    _on_screen(qapp, window)
    # Not the width asked for: a shown sheet holds the window open at its own
    # minimum, which is exactly the width the animation has to start from.
    full = window.width()

    window._compact.enter()

    ease = _running_animation(window)
    assert ease.duration() == 400
    ease.setCurrentTime(0)
    assert window.geometry().width() == full

    ease.setCurrentTime(ease.duration())
    assert window.geometry().width() == int(theme.metric("compact.width"))


def test_the_roller_is_handed_back_only_once_the_window_has_grown(
    qapp: QApplication, window: MainWindow
) -> None:
    """So the transition never shows an empty window growing."""
    window._compact.ANIMATION_MS = 400
    _on_screen(qapp, window)
    window._compact.enter()
    _settle(qapp)
    page = window._compact.page
    panel = window.sheet.dice.panel

    window._compact.leave()

    ease = _running_animation(window)
    ease.setCurrentTime(ease.duration() // 2)
    assert page.isAncestorOf(panel)  # still in the mini page, mid-grow

    ease.setCurrentTime(ease.duration())
    assert window.sheet.dice.view.isAncestorOf(panel)


# -- the GM window -----------------------------------------------------------


@pytest.fixture
def gm(qapp: QApplication) -> GMWindow:
    made = GMWindow(bind="127.0.0.1")
    yield made
    made.bridge.stop()


def test_the_gm_window_lends_its_own_roller_the_same_way(gm: GMWindow) -> None:
    """Different roll surface, identical feature — which is the point of the seam."""
    roller, history = gm._roller, gm._history
    assert isinstance(roller, DiceRollerPanel)

    gm._compact.enter()

    page = gm._compact.page
    assert page.isAncestorOf(roller)
    assert page.isAncestorOf(history)
    assert gm._full.isHidden()
    assert roller._quick_part.parentWidget() is roller._pair

    gm._compact.leave()

    assert gm._rolls_layout.indexOf(roller) >= 0
    assert gm._rolls_layout.indexOf(history) >= 0
    assert not gm._full.isHidden()
    assert roller._quick_part.parentWidget() is roller


def test_the_gm_keeps_the_hidden_roll_switch_in_the_mini_window(gm: GMWindow) -> None:
    gm._compact.enter()

    assert gm._roller._hidden_check.isVisibleTo(gm._compact.page)


def test_the_gm_window_remembers_its_full_size_too(gm: GMWindow) -> None:
    gm.setGeometry(QRect(30, 40, 820, 640))
    gm._compact.enter()
    gm.resize(300, 400)

    gm._persist_layout()

    assert storage.compact_settings()["width"] == 300
    assert storage.load_settings()["gm_layout"]["window_geometry"]

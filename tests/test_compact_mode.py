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
from PySide6.QtCore import QAbstractAnimation, QPoint, QPropertyAnimation, QRect, Qt
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


def test_a_closed_dice_block_has_no_compact_mode(window: MainWindow) -> None:
    """Closing the roller closes the way in — the keyboard's way too.

    This used to hold only by accident: the shrink button is a child of the block,
    so hiding the block took the button out of sight with it. The shortcut walks
    straight past that, so ``enter`` checks the roller is on screen itself.
    """
    window.sheet.hide_block("dice")

    window._compact._shortcut.activated.emit()

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


def test_the_shortcut_gets_in_as_well_as_out(window: MainWindow) -> None:
    """Escape only ever left. Ctrl+Shift+D is the way *in* without a mouse.

    Which matters most where there is no other chrome at all: the GM's read-only
    view of a player's sheet has a roller, so it has compact mode, but its menu
    bar is deliberately bare.
    """
    assert window._compact._shortcut.isEnabled()

    window._compact._shortcut.activated.emit()
    assert window._compact.is_compact

    window._compact._shortcut.activated.emit()
    assert not window._compact.is_compact


def test_the_glyph_follows_the_mode_however_it_changed(window: MainWindow) -> None:
    """Driven by the controller, so Escape and the mini strip keep the state honest."""
    button = window._compact.button
    assert not button.isChecked()

    button.click()

    assert window._compact.is_compact
    assert button.isChecked()
    assert button.text() == "⤢"

    window._compact.page.strip.expandRequested.emit()

    assert not window._compact.is_compact
    assert not button.isChecked()
    assert button.text() == "⤡"


def test_the_button_floats_over_the_roller_and_moves_with_it(window: MainWindow) -> None:
    """One button, re-parented — the same rule the roller itself follows.

    Two buttons would be two states to keep in step, and the one showing the
    wrong glyph would be whichever happened to be off screen.
    """
    button = window._compact.button
    view = window.sheet.dice.view
    assert button.parentWidget() is view

    window._compact.enter()

    assert button is window._compact.button
    assert button.parentWidget() is window._compact.page.overlay_host

    window._compact.leave()

    assert button.parentWidget() is view


def test_the_button_sits_in_the_hosts_bottom_right_corner(
    qapp: QApplication, window: MainWindow
) -> None:
    """In no layout at all: it is placed by hand and raised over the roller."""
    _on_screen(qapp, window)
    button = window._compact.button
    host = window.sheet.dice.view

    assert host.rect().contains(button.geometry())
    # Nearer the far corner than the near one, on both axes.
    assert button.geometry().center().x() > host.rect().center().x()
    assert button.geometry().center().y() > host.rect().center().y()


def test_the_button_stays_flush_in_the_corner(qapp: QApplication, window: MainWindow) -> None:
    """Flush is a choice between two overlaps, not an oversight.

    The bottom-right of a scrolling list of cards is never empty. Here the button
    clips the tail of the history's own scroll bar, which costs nothing anyone
    reaches for. Insetting by a scroll bar's width to spare it was tried and is
    worse — it lands the button squarely on a card's ``✕``, a discrete control
    with no other way to hit it.
    """
    _on_screen(qapp, window)
    button = window._compact.button
    host = window.sheet.dice.view
    inset = int(theme.metric("space.md"))

    assert host.rect().right() - button.geometry().right() == inset
    assert host.rect().bottom() - button.geometry().bottom() == inset


def test_the_gm_windows_button_floats_over_its_rolls_block(gm: GMWindow) -> None:
    """Over the view, as on a sheet — the GM's Rolls block holds the same one now."""
    assert gm._compact.button.parentWidget() is gm._view


def test_the_roll_panel_carries_no_shrink_button_of_its_own(window: MainWindow) -> None:
    """The button belongs to the *view*, never to the Roll box.

    A button among the roll controls cost a row of the panel's height in every
    window it appeared in — including the pinned strip, where height is the
    scarce thing. Over the view it lands on the history, which has room.
    """
    panel = window.sheet.dice.panel

    assert window._compact.button.parentWidget() is not panel
    assert not hasattr(panel, "compactRequested")
    assert not hasattr(window.sheet.dice, "compactRequested")
    assert not hasattr(window.sheet, "compactRequested")


def test_the_menu_bar_carries_no_compact_toggle(window: MainWindow, gm: GMWindow) -> None:
    """It was almost invisible there, and nothing about it said "dice roller"."""
    for win in (window, gm):
        assert not hasattr(win, "_compact_button")
        # The corner is the connection indicator itself again, with no strip
        # around it — there is nothing left to share it with.
        assert win.menuBar().cornerWidget(Qt.Corner.TopRightCorner) is win.connection_indicator


# -- the roller's layout as a preference -------------------------------------


def test_the_layout_preference_shapes_the_block_from_birth(qapp: QApplication) -> None:
    storage.set_dice_layout(storage.DICE_LAYOUT_COMPACT)

    window = MainWindow(locked=False)
    panel = window.sheet.dice.panel

    assert panel._quick_part.parentWidget() is panel._pair
    window._dirty = False


def test_leaving_compact_mode_does_not_undo_the_preference(window: MainWindow) -> None:
    """The two reasons the panel might be compact are kept apart for exactly this."""
    window.sync_dice_layout()  # "auto" — the shipped default
    window._compact.enter()
    window._compact.leave()
    panel = window.sheet.dice.panel
    assert panel._quick_part.parentWidget() is panel

    storage.set_dice_layout(storage.DICE_LAYOUT_COMPACT)
    window.sync_dice_layout()
    assert panel._quick_part.parentWidget() is panel._pair

    window._compact.enter()
    window._compact.leave()

    assert panel._quick_part.parentWidget() is panel._pair


def test_compact_mode_wins_over_the_extended_preference(window: MainWindow) -> None:
    """A window shrunk to the roller alone has no room for the roomiest shape.

    And leaving it hands Extended back, which is the whole reason the window's
    reason and the user's are two flags rather than one.
    """
    storage.set_dice_layout(storage.DICE_LAYOUT_EXTENDED)
    window.sync_dice_layout()
    view = window.sheet.dice.view
    panel = view.panel
    assert panel._quick_part.parentWidget() is panel

    window._compact.enter()

    assert panel._quick_part.parentWidget() is panel._pair
    assert view._row_locked() is False  # the parts are the mini window's, not the view's

    window._compact.leave()

    assert panel._quick_part.parentWidget() is panel
    assert view._row_locked() is True


def test_the_gm_window_follows_the_layout_preference_too(gm: GMWindow) -> None:
    """The point of putting the shared view in the GM's Rolls block."""
    storage.set_dice_layout(storage.DICE_LAYOUT_EXTENDED)
    gm.sync_dice_layout()

    assert gm._view._row_locked() is True
    assert gm._roller._column_locked is True

    storage.set_dice_layout(storage.DICE_LAYOUT_COMPACT)
    gm.sync_dice_layout()

    assert gm._view._row_locked() is False
    assert gm._roller._quick_part.parentWidget() is gm._roller._pair


def test_the_general_page_applies_to_open_windows_without_a_relaunch(
    window: MainWindow,
) -> None:
    from mm_companion.ui.settings import SettingsWindow

    panel = window.sheet.dice.panel
    settings = SettingsWindow()
    page = settings._pages[0]
    assert page.title == "General"
    assert not page.is_dirty()

    page._choices[storage.DICE_LAYOUT_COMPACT].setChecked(True)
    assert page.is_dirty()
    page.save()

    assert storage.dice_layout() == storage.DICE_LAYOUT_COMPACT
    assert not page.is_dirty()
    assert page.needs_restart() is False
    assert panel._quick_part.parentWidget() is panel._pair
    settings.close()


# -- floated blocks ----------------------------------------------------------


def test_a_floated_block_comes_out_frameless_and_already_on_top(window: MainWindow) -> None:
    """On top without being asked: a block is popped out to sit *beside* something."""
    sheet = window.sheet
    sheet.float_block("abilities")
    floated = sheet.canvas.block_window("abilities")
    bar = sheet.block_frame("abilities").title_bar

    assert floated.windowFlags() & Qt.WindowType.FramelessWindowHint
    assert floated.windowFlags() & Qt.WindowType.WindowStaysOnTopHint
    assert sheet.canvas.is_block_on_top("abilities")
    # The 🖈 means "stay on top" in a window of its own, and starts lit.
    assert bar._pin_button.isCheckable()
    assert bar._pin_button.isChecked()
    assert not bar._float_button.isVisibleTo(bar)  # already popped out

    bar._pin_button.setChecked(False)
    bar._pin_clicked()

    assert not sheet.canvas.is_block_on_top("abilities")
    assert not (floated.windowFlags() & Qt.WindowType.WindowStaysOnTopHint)


def test_the_pin_goes_back_to_meaning_the_strip_once_docked(window: MainWindow) -> None:
    sheet = window.sheet
    sheet.float_block("abilities")
    bar = sheet.block_frame("abilities").title_bar
    assert bar._pin_button.isCheckable()

    sheet.dock_block("abilities", 0, 0)

    assert not bar._pin_button.isCheckable()
    assert bar._float_button.isVisibleTo(bar)


def test_letting_a_block_fall_behind_survives_docking_and_floating_again(
    window: MainWindow,
) -> None:
    """The exception is remembered by *block*, not by the window — a drag destroys that."""
    sheet = window.sheet
    sheet.float_block("abilities")
    sheet.canvas.set_block_on_top("abilities", False)

    sheet.dock_block("abilities", 0, 0)
    sheet.float_block("abilities")

    assert not sheet.canvas.is_block_on_top("abilities")
    flags = sheet.canvas.block_window("abilities").windowFlags()
    assert not (flags & Qt.WindowType.WindowStaysOnTopHint)


def test_the_arrangement_carries_the_on_top_flag_both_ways(window: MainWindow) -> None:
    """Written even when false: absence now means the default, and the default is on."""
    sheet = window.sheet
    sheet.float_block("abilities")
    assert sheet.arrangement()["floating"]["abilities"]["on_top"] is True

    sheet.canvas.set_block_on_top("abilities", False)
    saved = sheet.arrangement()
    assert saved["floating"]["abilities"]["on_top"] is False

    sheet.reset_layout()
    assert sheet.canvas.is_block_on_top("abilities")  # cleared back to the default

    assert sheet.canvas.apply_arrangement(saved)
    assert not sheet.canvas.is_block_on_top("abilities")


def test_a_layout_saved_without_the_flag_restores_on_top(window: MainWindow) -> None:
    """Read tolerantly, so it needed no schema bump — the hidden_anchors precedent."""
    sheet = window.sheet
    sheet.float_block("abilities")
    saved = sheet.arrangement()
    saved["floating"]["abilities"].pop("on_top", None)

    assert sheet.canvas.apply_arrangement(saved)
    assert sheet.canvas.is_block_on_top("abilities")


def test_a_block_dragged_while_compact_stays_floating(
    qapp: QApplication, window: MainWindow
) -> None:
    """The page is hidden but keeps its geometry, so a hit test would still "find" a slot.

    Which docked the block into a page nobody could see: it simply vanished, with
    no way back but the View menu.
    """
    _on_screen(qapp, window)
    sheet = window.sheet
    sheet.float_block("abilities")
    canvas = sheet.canvas
    floated = canvas.block_window("abilities")
    over_the_page = canvas.mapToGlobal(canvas.rect().center())

    window._compact.enter()
    _settle(qapp)
    assert not canvas.accepts_drops()

    canvas.title_bar_pressed("abilities", over_the_page)
    canvas.title_bar_moved("abilities", over_the_page + QPoint(120, 120))
    canvas.title_bar_released("abilities", over_the_page + QPoint(120, 120))

    assert canvas.block_window("abilities") is floated
    assert not canvas.is_hidden("abilities")
    assert not canvas.is_pinned("abilities")

    window._compact.leave()
    _settle(qapp)
    assert canvas.accepts_drops()


def test_shrinking_mid_drag_drops_the_gesture(qapp: QApplication, window: MainWindow) -> None:
    """A drag in flight when the window shrank is aiming at a page that has gone."""
    _on_screen(qapp, window)
    canvas = window.sheet.canvas
    origin = canvas.mapToGlobal(canvas.rect().center())
    canvas.title_bar_pressed("abilities", origin)
    canvas.title_bar_moved("abilities", origin + QPoint(80, 80))
    assert canvas._drag_active

    window._compact.enter()

    assert not canvas._drag_active


def test_compact_clears_the_loose_windows_but_keeps_the_pinned_one(window: MainWindow) -> None:
    """Compact mode is "put the app out of the way", and loose blocks are the app."""
    sheet = window.sheet
    sheet.float_block("abilities")
    sheet.float_block("skills")
    sheet.canvas.set_block_on_top("abilities", False)
    loose = sheet.canvas.block_window("abilities")
    pinned = sheet.canvas.block_window("skills")

    window._compact.enter()

    assert loose.isHidden()
    assert not pinned.isHidden()

    window._compact.leave()

    assert not loose.isHidden()
    assert not pinned.isHidden()


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


def test_toggling_mid_ease_never_records_a_half_size(
    qapp: QApplication, window: MainWindow
) -> None:
    """A transition is atomic: one still easing lands outright before the next starts.

    Both toggles read and write the window's geometry — ``saveGeometry`` going in,
    ``remember_size`` coming out — and a frame of an ease is neither of the two
    sizes anyone chose. Left unsettled, clicking the round button back and forth
    inside the 180 ms wrote a half-grown rectangle into the *shared* ``layout``
    setting, so every character sheet opened at it.
    """
    window._compact.ANIMATION_MS = 400
    _on_screen(qapp, window)
    full = QRect(window.geometry())

    window._compact.enter()
    _running_animation(window).setCurrentTime(120)  # half-shrunk, and no size at all
    assert window.width() != full.width()
    assert window.width() != int(theme.metric("compact.width"))

    window._compact.leave()

    # What the mini window is remembered at is what it was asked to be, not the
    # frame it happened to be showing.
    assert storage.compact_settings()["width"] == int(theme.metric("compact.width"))
    assert storage.compact_settings()["height"] == int(theme.metric("compact.height"))

    _running_animation(window).setCurrentTime(120)  # and now half-*grown*
    window._compact.enter()

    assert window._compact._normal_geometry == full


# -- the GM window -----------------------------------------------------------


@pytest.fixture
def gm(qapp: QApplication) -> GMWindow:
    made = GMWindow(bind="127.0.0.1")
    yield made
    made.bridge.stop()


def test_the_gm_window_lends_its_own_roller_the_same_way(gm: GMWindow) -> None:
    """The GM's window lends through its view, and lends its *own* GM history."""
    roller, history = gm._roller, gm._history
    assert isinstance(roller, DiceRollerPanel)

    gm._compact.enter()

    page = gm._compact.page
    assert page.isAncestorOf(roller)
    assert page.isAncestorOf(history)
    assert gm._full.isHidden()
    assert roller._quick_part.parentWidget() is roller._pair

    gm._compact.leave()

    assert gm._view.isAncestorOf(roller)
    assert gm._view.isAncestorOf(history)
    assert not gm._full.isHidden()
    assert roller._quick_part.parentWidget() is roller


def test_the_gm_strip_caption_follows_the_window_title(gm: GMWindow) -> None:
    """The mini window is frameless, so the strip is the *only* place a title shows.

    And the GM window retitles itself as soon as it knows what it is hosting, so a
    caption seeded once in the constructor read "GM Mode" for the whole session.
    """
    # It has already retitled itself once, for the session it is hosting — which
    # a caption seeded in the constructor missed by a hair.
    assert gm._compact.page.strip._label.text() == gm.windowTitle() != "GM Mode"

    gm.setWindowTitle("GM Mode — The Table")

    assert gm._compact.page.strip._label.text() == "GM Mode — The Table"


def test_the_sheets_strip_keeps_its_own_shorter_caption(window: MainWindow) -> None:
    """A host wanting something shorter than the window title just says so after.

    ``MainWindow._update_title`` runs its ``set_title`` *after* ``setWindowTitle``,
    so the signal that keeps the GM's honest does not overwrite this one.
    """
    assert window._compact.page.strip._label.text() == "Unnamed Character"
    assert window.windowTitle() != "Unnamed Character"


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

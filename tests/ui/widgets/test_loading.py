"""The loading screen: a page over a window while the GUI thread builds widgets.

What it promises (see ``mm_companion.ui.loading``): it covers the window it starts
from and nothing else — no new window, no change of size — its bar only ever moves
forward, it always comes down, and outside one every report is a no-op.
"""

from __future__ import annotations

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QMainWindow, QWidget

from mm_companion.core import library
from mm_companion.core.character import Character
from mm_companion.ui import loading
from mm_companion.ui.gm_window import GMWindow
from mm_companion.ui.loading import LoadingScreen, _Veil
from mm_companion.ui.main_window import MainWindow
from mm_companion.ui.start_window import StartWindow

pytestmark = pytest.mark.usefixtures("workspace")


@pytest.fixture(autouse=True)
def _screens_at_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """The suite turns loading screens off; these tests want one at the first report.

    And no fade: a switch that fades is over only once the event loop has run it, so
    the tests that want one say so (``fades``).
    """
    monkeypatch.setattr(loading, "SHOW_AFTER_S", 0.0)
    monkeypatch.setattr(loading, "FADE_S", None)


@pytest.fixture
def fades(monkeypatch: pytest.MonkeyPatch) -> None:
    """View switches fade, quickly."""
    monkeypatch.setattr(loading, "FADE_S", 0.02)


@pytest.fixture
def host(qapp: QApplication):
    window = QMainWindow()
    window.setCentralWidget(QWidget())
    window.resize(600, 400)
    window.show()
    yield window
    window.close()


@pytest.fixture
def progress(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, float]]:
    """Every (text, value) the screen shows, in order, as the work reports it."""
    seen: list[tuple[str, float]] = []
    original = loading._show

    def spy() -> None:
        original()
        screen = loading.active_screen()
        if screen is not None:
            seen.append((screen.text, screen.value))

    monkeypatch.setattr(loading, "_show", spy)
    return seen


def _screens(window: QWidget) -> list[LoadingScreen]:
    return window.findChildren(LoadingScreen)


def _assert_forward(values: list[float]) -> None:
    assert values == sorted(values), values


# -- the screen itself ------------------------------------------------------------


def test_the_screen_covers_the_window_without_a_new_window_or_a_resize(
    qapp: QApplication, host: QMainWindow
) -> None:
    size, minimum = host.size(), host.minimumSize()
    windows_before = {id(w) for w in qapp.topLevelWidgets()}

    with loading.loading(host, "Opening Ronin…", at_once=True):
        screen = loading.active_screen()
        assert screen is not None
        assert screen.parentWidget() is host
        assert screen.isVisible()
        assert screen.geometry() == host.rect()
        assert screen.autoFillBackground()  # opaque: what is under it is mid-build
        assert screen.text == "Opening Ronin…"
        assert {id(w) for w in qapp.topLevelWidgets()} == windows_before
        assert host.size() == size
        assert host.minimumSize() == minimum

    assert loading.active_screen() is None
    assert _screens(host) == []
    assert host.size() == size
    assert QApplication.overrideCursor() is None


def test_it_comes_down_when_the_work_raises(host: QMainWindow) -> None:
    with pytest.raises(RuntimeError), loading.loading(host, "Opening…", at_once=True):
        raise RuntimeError("the build failed")

    assert loading.active_screen() is None
    assert _screens(host) == []
    assert QApplication.overrideCursor() is None


def test_no_screen_over_a_window_nobody_can_see(qapp: QApplication) -> None:
    hidden = QMainWindow()
    with loading.loading(hidden, "Opening…"):
        assert loading.active_screen() is None
        # Reports with no screen up are no-ops, not errors.
        loading.expect(3)
        loading.step()
        loading.say("Connecting…")
        with loading.part(0.5):
            loading.step()
    assert _screens(hidden) == []


def test_reports_outside_any_loading_do_nothing() -> None:
    loading.expect(2)
    loading.step(2)
    loading.say("nothing listens")
    with loading.part(1.0):
        pass
    assert loading.active_screen() is None


def test_the_bar_divides_into_parts_and_counted_steps(host: QMainWindow) -> None:
    with loading.loading(host, "Opening…", at_once=True):
        screen = loading.active_screen()
        with loading.part(0.5):
            loading.expect(4)
            loading.step()
            assert screen.value == pytest.approx(0.125)
            loading.step(3)
            assert screen.value == pytest.approx(0.5)
        # A share of what is *left*: half of the remaining half.
        with loading.part(0.5):
            pass  # reported nothing, and still ends at its end
        assert screen.value == pytest.approx(0.75)


def test_steps_past_what_was_expected_stay_inside_their_part(host: QMainWindow) -> None:
    with loading.loading(host, "Opening…", at_once=True):
        screen = loading.active_screen()
        with loading.part(0.4):
            loading.expect(1)
            loading.step(5)
        assert screen.value == pytest.approx(0.4)


def test_a_loading_inside_another_shares_its_one_screen(host: QMainWindow) -> None:
    with loading.loading(host, "Opening Ronin…", at_once=True):
        outer = loading.active_screen()
        with loading.loading(host, "Opening the simple sheet…"):
            assert loading.active_screen() is outer
            assert outer.text == "Opening Ronin…"
            assert len(_screens(host)) == 1
        # Still up: only the outermost takes it down.
        assert loading.active_screen() is outer
    assert loading.active_screen() is None


def test_say_changes_what_the_screen_says(host: QMainWindow) -> None:
    with loading.loading(host, "Opening GM Mode…", at_once=True):
        loading.say("Connecting to the session…")
        assert loading.active_screen().text == "Connecting to the session…"


def test_say_puts_the_screen_up_at_once_for_the_wait_after_it(host: QMainWindow) -> None:
    """A network wait reports nothing while it waits, so it cannot wait for a step."""
    with loading.loading(host, "Opening GM Mode…"):
        assert loading.active_screen() is None
        loading.say("Connecting to the session…")
        assert loading.active_screen().text == "Connecting to the session…"


def test_quick_work_puts_no_screen_up(host: QMainWindow, monkeypatch: pytest.MonkeyPatch) -> None:
    """A spare NPC sheet handed its creature is done in a blink: no page flashes."""
    monkeypatch.setattr(loading, "SHOW_AFTER_S", 60.0)
    with loading.loading(host, "Opening…"):
        loading.expect(2)
        loading.step(2)
        assert loading.active_screen() is None
    assert _screens(host) == []


def test_the_screen_goes_up_at_the_first_report_once_due(host: QMainWindow) -> None:
    with loading.loading(host, "Opening…"):
        assert loading.active_screen() is None  # nothing reported yet
        loading.expect(2)
        loading.step()
        screen = loading.active_screen()
        assert screen is not None and screen.isVisible()
        assert screen.value == pytest.approx(0.5)


# -- the places that use it -------------------------------------------------------


def _saved(name: str, data) -> tuple[Character, object]:
    character = Character.new_default(data)
    character.profile["hero_name"] = name
    return character, library.save_character(character)


def test_the_launcher_opens_a_sheet_behind_a_loading_screen(
    qapp: QApplication, data, progress
) -> None:
    _character, path = _saved("Ronin", data)
    launcher = StartWindow()
    launcher.show()

    launcher._open_character(path)

    assert progress, "the build reported nothing to the screen"
    assert {text for text, _ in progress} == {"Opening Ronin…"}
    _assert_forward([value for _, value in progress])
    assert progress[-1][1] > 0.9
    assert _screens(launcher) == []
    sheet = launcher._child_windows[0]
    assert isinstance(sheet, MainWindow) and sheet.isVisible()
    sheet.close()


def test_switching_to_the_simple_sheet_with_no_fade_goes_behind_a_loading_screen(
    qapp: QApplication, data, progress
) -> None:
    window = MainWindow(character=Character.new_default(data), locked=False)
    window.show()
    size = window.size()

    window.sheet.set_simple(True)

    assert window.sheet.is_simple
    assert progress
    assert {text for text, _ in progress} == {"Opening the simple sheet…"}
    _assert_forward([value for _, value in progress])
    assert _screens(window) == []
    assert window.size() == size

    progress.clear()
    window.sheet.set_simple(False)

    assert not window.sheet.is_simple
    assert {text for text, _ in progress} == {"Opening the edit sheet…"}
    assert _screens(window) == []
    window.close()


def test_a_sheet_that_opens_into_the_simple_sheet_runs_one_bar(
    qapp: QApplication, data, progress, monkeypatch: pytest.MonkeyPatch
) -> None:
    from mm_companion.core import storage

    monkeypatch.setattr(storage, "simple_sheet_on_open", lambda: True)
    _character, path = _saved("Ronin", data)
    launcher = StartWindow()
    launcher.show()

    launcher._open_character(path)

    sheet = launcher._child_windows[0]
    assert sheet.sheet.is_simple
    # One screen, one text, one bar moving forward from the build through the switch.
    assert {text for text, _ in progress} == {"Opening Ronin…"}
    _assert_forward([value for _, value in progress])
    sheet.close()


def test_the_gm_window_reports_its_build(qapp: QApplication, host: QMainWindow, progress) -> None:
    with loading.loading(host, "Opening GM Mode…"):
        window = GMWindow()

    values = [value for _, value in progress]
    assert values and values[-1] == pytest.approx(1.0)
    _assert_forward(values)
    window.close()


def test_a_sheet_built_with_no_screen_up_reports_into_nothing(qapp: QApplication, data) -> None:
    """A spare NPC sheet is built in idle time, unseen; it must not raise a screen."""
    window = MainWindow(character=Character.new_default(data), locked=True)
    assert loading.active_screen() is None
    assert _screens(window) == []


# -- view switches ------------------------------------------------------------------


def _veils(window: QWidget) -> list[_Veil]:
    return window.findChildren(_Veil)


def _shown_sheet(data) -> MainWindow:
    window = MainWindow(character=Character.new_default(data), locked=False)
    window.show()
    return window


def _visible_windows(qapp: QApplication) -> set[int]:
    # Visible ones: a shed veil is an unparented widget until its deferred delete runs.
    return {id(w) for w in qapp.topLevelWidgets() if w.isVisible()}


def _settled(window: QWidget) -> bool:
    return not loading.fading(window) and not _veils(window) and not _screens(window)


@pytest.mark.usefixtures("fades")
def test_a_switch_fades_out_at_once_and_switches_after(qapp: QApplication, data, qtbot) -> None:
    """The press answers before the build blocks: the old view starts fading out."""
    window = _shown_sheet(data)
    size = window.size()
    windows_before = _visible_windows(qapp)
    said: list[bool] = []
    window.sheet.simpleChanged.connect(said.append)

    window.sheet.set_simple(True)

    # Returned before the work: the old view, as a picture, fading over the screen.
    assert not window.sheet.is_simple
    assert loading.fading(window)
    assert len(_veils(window)) == 1 and len(_screens(window)) == 1
    assert said == []

    qtbot.waitUntil(lambda: window.sheet.is_simple)
    assert said == [True]
    qtbot.waitUntil(lambda: _settled(window))
    assert window.sheet.is_simple
    assert window.size() == size
    assert _visible_windows(qapp) == windows_before
    assert QApplication.overrideCursor() is None

    window.sheet.set_simple(False)
    qtbot.waitUntil(lambda: _settled(window))
    assert not window.sheet.is_simple
    assert said == [True, False]
    assert window.size() == size
    window.close()


@pytest.mark.usefixtures("fades")
def test_the_new_view_fades_in_over_a_live_window(qapp: QApplication, data, qtbot) -> None:
    """The fade in is a picture over a sheet already working: clicks go through it."""
    window = _shown_sheet(data)
    seen: list[_Veil] = []

    window.sheet.set_simple(True)
    (fading_out,) = _veils(window)
    assert not fading_out.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

    def fading_in() -> bool:
        veils = [v for v in _veils(window) if v is not fading_out and v.isVisible()]
        seen.extend(veils)
        return bool(veils) or _settled(window)

    qtbot.waitUntil(fading_in)
    for veil in seen:
        assert veil.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        assert veil.geometry() == window.rect()
    qtbot.waitUntil(lambda: _settled(window))
    assert window.sheet.is_simple
    window.close()


@pytest.mark.usefixtures("fades")
def test_a_switch_that_was_quick_last_time_fades_to_the_bare_page(
    qapp: QApplication, data, qtbot, monkeypatch: pytest.MonkeyPatch
) -> None:
    window = _shown_sheet(data)
    loading._took[window] = {"Opening the simple sheet…": 0.0}
    monkeypatch.setattr(loading, "SHOW_AFTER_S", 60.0)  # and is quick again: no bar

    window.sheet.set_simple(True)

    (screen,) = _screens(window)
    assert not screen.content_shown
    qtbot.waitUntil(lambda: _settled(window))
    assert window.sheet.is_simple
    window.close()


@pytest.mark.usefixtures("fades")
def test_a_switch_that_was_slow_last_time_fades_to_the_bar(qapp: QApplication, data, qtbot) -> None:
    window = _shown_sheet(data)
    loading._took[window] = {"Opening the simple sheet…": 5.0}

    window.sheet.set_simple(True)

    (screen,) = _screens(window)
    assert screen.content_shown
    assert screen.text == "Opening the simple sheet…"
    qtbot.waitUntil(lambda: _settled(window))
    window.close()


@pytest.mark.usefixtures("fades")
def test_a_second_press_while_fading_is_refused_and_the_menu_kept_honest(
    qapp: QApplication, data, qtbot
) -> None:
    window = _shown_sheet(data)
    said: list[bool] = []
    window.sheet.simpleChanged.connect(said.append)

    window.sheet.set_simple(True)
    window.sheet.set_simple(False)  # F8 again, mid-fade

    assert said == [False]  # what is showing now, so the menu shows that
    qtbot.waitUntil(lambda: _settled(window))
    assert window.sheet.is_simple
    assert said == [False, True]
    window.close()


@pytest.mark.usefixtures("fades")
def test_a_switch_whose_work_raises_still_comes_back(
    host: QMainWindow, qtbot, monkeypatch: pytest.MonkeyPatch
) -> None:
    logged: list[str] = []
    monkeypatch.setattr(
        loading.diagnostics, "log_exception", lambda context, *_: logged.append(context)
    )
    finished: list[bool] = []

    def work() -> None:
        raise RuntimeError("the build failed")

    loading.fade_through(host, "Opening…", work, lambda: finished.append(True))
    qtbot.waitUntil(lambda: _settled(host))

    assert finished == [True]
    assert logged and "Opening…" in logged[0]
    assert QApplication.overrideCursor() is None


@pytest.mark.usefixtures("fades")
def test_a_switch_over_a_window_nobody_can_see_is_made_at_once(qapp: QApplication) -> None:
    hidden = QMainWindow()
    done: list[str] = []
    loading.fade_through(
        hidden, "Opening…", lambda: done.append("work"), lambda: done.append("end")
    )
    assert done == ["work", "end"]
    assert not loading.fading(hidden)
    assert _veils(hidden) == [] and _screens(hidden) == []


@pytest.mark.usefixtures("fades")
def test_a_switch_inside_other_work_joins_its_bar(host: QMainWindow, progress) -> None:
    """A sheet opening straight into the simple sheet: one bar, and no fade."""
    done: list[str] = []
    with loading.loading(host, "Opening Ronin…", at_once=True):
        loading.fade_through(host, "Opening the simple sheet…", lambda: done.append("work"))
        assert done == ["work"]
        assert not loading.fading(host)
        assert len(_screens(host)) == 1
    assert {text for text, _ in progress} <= {"Opening Ronin…"}


# -- the start of the app -------------------------------------------------------------


def test_the_splash_is_a_card_in_the_theme(qapp: QApplication) -> None:
    from mm_companion.__main__ import _make_splash
    from mm_companion.ui import theme

    splash = _make_splash()
    try:
        assert splash.isWindow() and not splash.isVisible()
        assert splash.windowFlags() & Qt.WindowType.FramelessWindowHint
        assert splash.width() == int(theme.metric("splash.width"))
        assert splash.height() == int(theme.metric("splash.height"))
        assert theme.color("splash.background") in splash.styleSheet()
    finally:
        splash.deleteLater()


def test_the_start_imports_the_interface_behind_the_bar(host: QMainWindow, progress) -> None:
    from mm_companion.__main__ import _import_interface

    with loading.loading(host, "Starting MM-Companion…", at_once=True):
        module = _import_interface()

    assert module.__name__ == "mm_companion.ui.start_window"
    assert hasattr(module, "StartWindow")
    values = [value for _, value in progress]
    assert len(values) > 1
    _assert_forward(values)
    assert values[-1] == pytest.approx(1.0)

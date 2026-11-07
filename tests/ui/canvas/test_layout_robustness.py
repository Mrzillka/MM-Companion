"""The page survives its own failures and interrupted gestures.

Every test here is about a way the sheet used to end up blank or broken rather than
merely wrong: a rebuild that raised half way left the blocks it had already taken
down hidden for good; a size read against the wrong tree raised out of the middle of
a gesture; a drag interrupted by a view switch went on resizing a row to follow a
pointer nobody was dragging with. The installed app has no console, so each of
these happened without a word — which is why the recovery paths also write to the
errors log, and why the tests check that they do.
"""

from __future__ import annotations

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from mm_companion.core import diagnostics
from mm_companion.core.data_loader import load_game_data
from mm_companion.ui import block_canvas
from mm_companion.ui import layout_tree as lt
from mm_companion.ui.character_sheet import CharacterSheet
from mm_companion.ui.grid_view import GridSplitter


@pytest.fixture
def make_sheet(qapp: QApplication):
    sheets: list[CharacterSheet] = []

    def _make() -> CharacterSheet:
        sheet = CharacterSheet(load_game_data())
        sheet.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
        sheet.resize(1200, 900)
        sheet.show()
        _settle()
        sheets.append(sheet)
        return sheet

    yield _make

    for sheet in sheets:
        sheet.hide()
        sheet.deleteLater()
    QApplication.processEvents()


@pytest.fixture
def errors_log(tmp_path, monkeypatch):
    """Point the errors log at a temporary file, and give back its path."""
    path = tmp_path / diagnostics.ERRORS_LOG
    monkeypatch.setattr(diagnostics, "_errors_path", path)
    diagnostics._reset_for_tests()
    return path


def _settle(times: int = 5) -> None:
    for _ in range(times):
        QApplication.processEvents()


def _docked(sheet: CharacterSheet) -> list[str]:
    """The blocks the page says are on it."""
    return lt.keys(sheet.canvas.page_tree())


def _shown_in_page(sheet: CharacterSheet, key: str) -> bool:
    """Whether *key*'s frame is really on the page: visible, and inside the stack."""
    frame = sheet.block_frame(key)
    stack = sheet.canvas._stack
    return frame.isVisibleTo(sheet) and stack.isAncestorOf(frame)


def _row_holding(sheet: CharacterSheet, key: str) -> int:
    for index, row in enumerate(sheet.canvas._rows):
        if key in row:
            return index
    raise AssertionError(f"{key} is not on the page")


# -- sizes read against the tree they were built for ---------------------------


def test_closing_a_whole_row_and_another_block_at_once_does_not_raise(make_sheet) -> None:
    # close_blocks detaches one block at a time and rebuilds once at the end. Each
    # detach reads the live sizes first — and once the first row had gone, the rows
    # on screen no longer lined up with the tree: the last row's index ran off the
    # end of it and IndexError came out of the middle of the gesture, leaving the
    # page half taken down.
    sheet = make_sheet()
    canvas = sheet.canvas
    first_row = list(canvas._rows[0])
    last_row = canvas._rows[-1]
    victim = last_row[-1]

    canvas.close_blocks([*first_row, victim])
    _settle()

    for key in [*first_row, victim]:
        assert canvas.is_hidden(key)
    for key in _docked(sheet):
        assert _shown_in_page(sheet, key), key


def test_sizes_are_not_read_off_rows_built_for_another_tree(make_sheet) -> None:
    sheet = make_sheet()
    canvas = sheet.canvas
    before = canvas.page_tree()
    # Change the tree without rebuilding — the state every multi-step gesture is in
    # between its first change and its rebuild.
    canvas._set_page(lt.remove(canvas.page_tree(), canvas._rows[0][0]))
    changed = canvas.page_tree()

    canvas._remember_sizes()

    assert canvas.page_tree() == changed  # nothing from the old rows written in
    assert before != changed


# -- a rebuild that fails part way ----------------------------------------------


def test_a_rebuild_that_raises_puts_every_block_back(make_sheet, errors_log, monkeypatch) -> None:
    sheet = make_sheet()
    canvas = sheet.canvas
    real_build = block_canvas.build_node
    calls = {"n": 0}

    def flaky(node, build_leaf, parent=None):
        calls["n"] += 1
        if calls["n"] == 3:  # part way through the rows: some frames already taken down
            raise RuntimeError("boom")
        return real_build(node, build_leaf, parent)

    monkeypatch.setattr(block_canvas, "build_node", flaky)
    canvas.hide_block("skills")
    _settle()

    assert canvas.is_hidden("skills")
    docked = _docked(sheet)
    assert docked and "skills" not in docked
    for key in docked:
        assert _shown_in_page(sheet, key), key
    assert "Rebuilding the page failed" in errors_log.read_text(encoding="utf-8")


def test_a_rebuild_that_keeps_failing_falls_back_to_the_default_page(
    make_sheet, errors_log, monkeypatch
) -> None:
    sheet = make_sheet()
    canvas = sheet.canvas
    # An arrangement of the user's own, so falling back to the default is a change.
    canvas.dock_block("conditions", len(canvas._rows), 0, new_row=True)
    _settle()
    real_build = block_canvas.build_node
    state = {"failing": True}
    bad_page = canvas.page_tree()

    def flaky(node, build_leaf, parent=None):
        # Fail every render of the arrangement the user had; let the default through.
        if state["failing"] and canvas.page_tree() == bad_page:
            raise RuntimeError("this arrangement cannot be drawn")
        return real_build(node, build_leaf, parent)

    monkeypatch.setattr(block_canvas, "build_node", flaky)
    canvas._relayout()
    _settle()

    for key in _docked(sheet):
        assert _shown_in_page(sheet, key), key
    assert set(_docked(sheet)) | set(canvas.pinned_keys()) == set(sheet.block_keys())
    assert "Repairing the page failed" in errors_log.read_text(encoding="utf-8")


def test_repairing_the_model_places_every_block_exactly_once(make_sheet) -> None:
    sheet = make_sheet()
    canvas = sheet.canvas
    # A tree naming a block that does not exist, and missing one that does.
    page = lt.remove(canvas.page_tree(), "skills")
    page = lt.append_row(page, "no-such-block")
    canvas._set_page(page)

    canvas._repair_model()

    keys = lt.keys(canvas.page_tree()) + lt.keys(canvas.pin_region())
    assert "no-such-block" not in keys
    assert sorted(keys) == sorted(sheet.block_keys())


# -- gestures a view switch interrupts ------------------------------------------


def _grip_under(sheet: CharacterSheet, key: str):
    stack = sheet.canvas._stack
    return stack, stack._grips[_row_holding(sheet, key)]


def test_ending_gestures_calls_off_a_grip_drag_and_keeps_its_height(make_sheet) -> None:
    sheet = make_sheet()
    stack, grip = _grip_under(sheet, "conditions")
    index = _row_holding(sheet, "conditions")
    QTest.mousePress(grip, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(5, 1))
    for step in range(1, 9):
        QTest.mouseMove(grip, QPoint(5, 1 + step * 10))
    dragged = stack.heights()[index]
    assert dragged > 0 and grip.dragging

    sheet.canvas.end_gestures()

    assert not grip.dragging
    assert stack._frozen_height == 0
    assert stack.heights()[index] == dragged
    # And the drag is in the tree, as a release would have put it.
    assert sheet.canvas.page_tree().usable_sizes()[index] == dragged


def test_a_grip_hidden_mid_drag_lets_go_of_the_page(make_sheet) -> None:
    # A hidden grip never hears its release. The page height a drag freezes has to
    # come off with it, or the page keeps a height nobody asked for.
    sheet = make_sheet()
    stack, grip = _grip_under(sheet, "conditions")
    QTest.mousePress(grip, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(5, 1))
    QTest.mouseMove(grip, QPoint(5, 60))
    assert stack._frozen_height > 0

    sheet.board.hide()

    assert not grip.dragging
    assert stack._frozen_height == 0


def test_ending_gestures_closes_nothing_a_divider_had_warned_about(make_sheet) -> None:
    sheet = make_sheet()
    canvas = sheet.canvas
    row = canvas._row_widgets[_row_holding(sheet, "abilities")]
    assert isinstance(row, GridSplitter) and row.count() >= 2
    handle = row.handle(1)
    # Drag the divider almost all the way to the left: the first block is warned.
    QTest.mousePress(
        handle, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(1, 5)
    )
    for x in range(-20, -row.sizes()[0], -20):
        QTest.mouseMove(handle, QPoint(x, 5))
    _settle()
    assert canvas._warned  # "release to close" is up

    canvas.end_gestures()
    # A release much later — the user clicking the divider — must not act on it.
    QTest.mouseRelease(
        handle, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(1, 5)
    )
    _settle()

    assert not canvas._warned
    assert not any(canvas.is_hidden(key) for key in sheet.block_keys())


# -- the pinned strip's icon -----------------------------------------------------


def test_the_strip_icon_shows_only_while_the_strip_is_empty(make_sheet) -> None:
    sheet = make_sheet()  # the Dice block starts pinned
    panel = sheet.board.panel
    assert not panel.is_empty()
    assert not panel._handle.isVisibleTo(sheet)

    sheet.canvas.unpin_all()
    _settle()
    assert panel.is_empty()
    assert panel._handle.isVisibleTo(sheet)

    sheet.pin_block("dice")
    _settle()
    assert not panel._handle.isVisibleTo(sheet)


def test_right_clicking_the_strips_divider_opens_the_strip_menu(make_sheet, monkeypatch) -> None:
    sheet = make_sheet()
    board = sheet.board
    opened: list[QPoint] = []
    monkeypatch.setattr(board.panel, "open_menu", lambda pos: opened.append(pos))
    splitter = board._splitter
    handle = splitter.handle(1)

    from PySide6.QtGui import QContextMenuEvent

    event = QContextMenuEvent(QContextMenuEvent.Reason.Mouse, QPoint(1, 1), QPoint(50, 60))
    QApplication.sendEvent(handle, event)

    assert opened == [QPoint(50, 60)]


def test_the_strip_menu_fills_a_window_menu(make_sheet) -> None:
    from PySide6.QtWidgets import QMenu

    from mm_companion.ui.pinned_panel import attach_strip_menu

    sheet = make_sheet()
    menu = QMenu()
    attach_strip_menu(menu, sheet.board.panel)
    menu.aboutToShow.emit()
    menu.aboutToShow.emit()  # refilled, not appended to

    titles = [action.text() for action in menu.actions() if action.text()]
    assert titles == ["Position", "Unpin all"]
    menu.deleteLater()

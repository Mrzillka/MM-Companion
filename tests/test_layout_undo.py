"""Taking back a layout gesture, and interleaving that with taking back an edit.

Layout was invisible to undo until the page became something you could wreck in
one careless drag. Two histories now, and one visible order over them: Ctrl+Z
takes back whatever you last did, whichever kind of thing it was.
"""

from __future__ import annotations

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from mm_companion.core.data_loader import load_game_data
from mm_companion.ui import layout_tree as lt
from mm_companion.ui.character_sheet import CharacterSheet
from mm_companion.ui.layout_undo import LayoutHistory, UndoRouter
from mm_companion.ui.main_window import MainWindow
from mm_companion.ui.undo import UndoController


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


@pytest.fixture
def sheet(qapp: QApplication) -> CharacterSheet:
    built = CharacterSheet(load_game_data())
    built.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
    built.resize(1200, 800)
    built.show()
    for _ in range(8):
        qapp.processEvents()
    yield built
    built.hide()
    built.deleteLater()


def rows_of(sheet: CharacterSheet) -> list[list[str]]:
    return [lt.keys(child) for child in sheet.canvas.page_tree().children]


class TestLayoutHistory:
    def test_a_gesture_can_be_taken_back(self, sheet) -> None:
        history = LayoutHistory(sheet.canvas)
        before = rows_of(sheet)

        sheet.canvas.merge_blocks("skills", "powers")
        history.record()

        assert rows_of(sheet) != before
        assert history.undo() is True
        assert rows_of(sheet) == before

    def test_and_put_back_again(self, sheet) -> None:
        history = LayoutHistory(sheet.canvas)
        sheet.canvas.merge_blocks("skills", "powers")
        history.record()
        merged = rows_of(sheet)
        history.undo()

        assert history.redo() is True
        assert rows_of(sheet) == merged

    def test_a_gesture_that_changed_nothing_is_not_a_step(self, sheet) -> None:
        """A divider nudged and put back, or a block dropped where it already was,
        must not become a step that appears to do nothing."""
        history = LayoutHistory(sheet.canvas)

        assert history.record() is False
        assert history.can_undo is False

    def test_an_empty_history_refuses_politely(self, sheet) -> None:
        history = LayoutHistory(sheet.canvas)

        assert history.undo() is False
        assert history.redo() is False

    def test_several_gestures_come_back_in_order(self, sheet) -> None:
        history = LayoutHistory(sheet.canvas)
        first = rows_of(sheet)
        sheet.canvas.merge_blocks("skills", "powers")
        history.record()
        second = rows_of(sheet)
        sheet.canvas.hide_block("complications")
        history.record()

        history.undo()
        assert rows_of(sheet) == second
        history.undo()
        assert rows_of(sheet) == first

    def test_a_new_gesture_drops_the_redo_stack(self, sheet) -> None:
        history = LayoutHistory(sheet.canvas)
        sheet.canvas.merge_blocks("skills", "powers")
        history.record()
        history.undo()
        assert history.can_redo is True

        sheet.canvas.hide_block("complications")
        history.record()

        assert history.can_redo is False

    def test_its_own_undo_is_not_recorded_as_a_gesture(self, sheet) -> None:
        history = LayoutHistory(sheet.canvas)
        sheet.canvas.merge_blocks("skills", "powers")
        history.record()

        history.undo()
        recorded = history.record()

        assert recorded is False, "an undo recorded itself as a new step"

    def test_rebase_makes_the_current_page_the_place_undo_stops(self, sheet) -> None:
        """What a window restoring a saved layout at startup needs: that is not
        something the user just did, and Ctrl+Z must not take the page back to the
        factory arrangement on a fresh window."""
        history = LayoutHistory(sheet.canvas)
        sheet.canvas.merge_blocks("skills", "powers")

        history.rebase()

        assert history.record() is False
        assert history.can_undo is False

    def test_the_depth_is_bounded(self, sheet) -> None:
        history = LayoutHistory(sheet.canvas, depth=3)
        for key in ("complications", "conditions", "advantages", "equipment"):
            sheet.canvas.hide_block(key)
            history.record()

        assert len(history._undo) == 3


class TestTheRouter:
    def test_it_undoes_whatever_moved_last(self, sheet) -> None:
        character = UndoController(sheet)
        history = LayoutHistory(sheet.canvas)
        router = UndoRouter(character, history)

        sheet.abilities._abilities["STR"].setValue(4)
        character.flush()
        sheet.canvas.merge_blocks("skills", "powers")
        router.note_layout_step()

        # The layout gesture was last, so it goes first.
        assert router.undo() is True
        assert sheet.canvas.group_for("powers") is None
        assert sheet.abilities._abilities["STR"].value() == 4

        # And then the edit.
        assert router.undo() is True
        assert sheet.abilities._abilities["STR"].value() != 4

    def test_it_redoes_them_the_other_way_round(self, sheet) -> None:
        character = UndoController(sheet)
        history = LayoutHistory(sheet.canvas)
        router = UndoRouter(character, history)
        sheet.abilities._abilities["STR"].setValue(4)
        character.flush()
        sheet.canvas.merge_blocks("skills", "powers")
        router.note_layout_step()
        router.undo()
        router.undo()

        router.redo()
        assert sheet.abilities._abilities["STR"].value() == 4
        router.redo()
        assert sheet.canvas.group_for("powers") is not None

    def test_an_edit_after_a_layout_gesture_is_still_the_last_thing_done(self, sheet) -> None:
        """The order has to keep counting, not just notice the first edit.

        Watching "the character can now undo" only ever fires once a session, so
        every edit after the first was filed behind whatever layout gesture came
        between them: Ctrl+Z moved a divider back instead of taking back the rank
        that had just been typed.
        """
        character = UndoController(sheet)
        router = UndoRouter(character, LayoutHistory(sheet.canvas))

        sheet.abilities._abilities["STR"].setValue(4)
        character.flush()
        sheet.canvas.merge_blocks("skills", "powers")
        router.note_layout_step()
        sheet.abilities._abilities["AGL"].setValue(6)
        character.flush()

        assert router.undo() is True
        assert sheet.abilities._abilities["AGL"].value() != 6
        assert sheet.canvas.group_for("powers") is not None, "the layout went first"

    def test_a_redo_it_drove_itself_is_not_a_new_edit(self, sheet) -> None:
        """A redo pushes onto the undo stack, which looks exactly like an edit."""
        character = UndoController(sheet)
        router = UndoRouter(character, LayoutHistory(sheet.canvas))
        sheet.abilities._abilities["STR"].setValue(4)
        character.flush()
        router.undo()
        depth = len(router._order)

        router.redo()

        assert len(router._order) == depth + 1, "the redo was counted twice"

    def test_a_layout_gesture_alone_is_still_undoable(self, sheet) -> None:
        router = UndoRouter(None, LayoutHistory(sheet.canvas))
        before = rows_of(sheet)
        sheet.canvas.merge_blocks("skills", "powers")
        router.note_layout_step()

        assert router.can_undo is True
        assert router.undo() is True
        assert rows_of(sheet) == before

    def test_nothing_to_undo_is_refused_rather_than_crashing(self, sheet) -> None:
        router = UndoRouter(None, LayoutHistory(sheet.canvas))

        assert router.can_undo is False
        assert router.undo() is False
        assert router.redo() is False

    def test_a_gesture_that_changed_nothing_adds_no_step(self, sheet) -> None:
        router = UndoRouter(None, LayoutHistory(sheet.canvas))

        router.note_layout_step()

        assert router.can_undo is False


class TestTheWindow:
    def test_a_window_can_take_back_a_layout_gesture(self, qapp, tmp_path, monkeypatch) -> None:
        from mm_companion.core import storage

        monkeypatch.setenv(storage.HOME_ENV_VAR, str(tmp_path))
        storage.ensure_workspace()
        win = MainWindow(locked=False)
        win.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
        win.resize(1100, 800)
        win.show()
        for _ in range(8):
            qapp.processEvents()
        try:
            before = rows_of(win._sheet)

            win._sheet.canvas.merge_blocks("skills", "powers")
            for _ in range(4):
                qapp.processEvents()
            assert rows_of(win._sheet) != before
            assert win._undo_action.isEnabled(), "the button never offered the step"

            win._router.undo()
            for _ in range(4):
                qapp.processEvents()

            assert rows_of(win._sheet) == before
        finally:
            win.hide()
            win.deleteLater()

    def test_undoing_a_layout_step_does_not_dirty_the_character(
        self, qapp, tmp_path, monkeypatch
    ) -> None:
        """Layout is global, not per character: a moved block is not an unsaved
        change to anybody's build."""
        from mm_companion.core import storage

        monkeypatch.setenv(storage.HOME_ENV_VAR, str(tmp_path))
        storage.ensure_workspace()
        win = MainWindow(locked=False)
        win.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
        win.show()
        for _ in range(8):
            qapp.processEvents()
        try:
            assert win._dirty is False

            win._sheet.canvas.merge_blocks("skills", "powers")
            win._router.undo()
            for _ in range(4):
                qapp.processEvents()

            assert win._dirty is False
        finally:
            win.hide()
            win.deleteLater()

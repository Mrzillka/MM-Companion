"""Dragging a block out of existence, and getting it back.

A grid the user owns has to let a divider go all the way: refusing to shrink is
the refusal the whole rework exists to remove. But a block two pixels wide is not
something anybody can read, find, or grab hold of to drag back open, so leaving
one there is not generosity — it is a block quietly lost.

So past ``grid.close-extent`` the drag says what it is really doing: the frame is
washed in the reject colour, the cursor carries a "Release to close" naming the
blocks, and letting go closes them. *Closes*, not destroys — the View menu's
checkbox clears, ``Ctrl+Z`` puts them back where they were, and the block itself
is the same widget it always was.

The other half of the rule matters as much: **only a drag may do this.** A
restored arrangement, a strip mid-rebuild or a window resize can all hand a pane a
tiny size, and none of them is somebody asking for a block to go away.
"""

from __future__ import annotations

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtWidgets import QApplication, QLabel

from mm_companion.ui import layout_tree as lt
from mm_companion.ui import theme
from mm_companion.ui.block_canvas import BlockCanvas, DropSlot
from mm_companion.ui.block_sizes import RecommendedSize
from mm_companion.ui.drop_feedback import DropFeedback
from mm_companion.ui.layout_undo import LayoutHistory


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


@pytest.fixture
def canvas(qapp: QApplication) -> BlockCanvas:
    """Four plain blocks: ``a`` and ``b`` sharing a row, then ``c`` and ``d``."""
    panels = [(key, key.upper(), QLabel(key)) for key in ("a", "b", "c", "d")]
    sizes = {key: RecommendedSize(200, 120) for key in ("a", "b", "c", "d")}
    built = BlockCanvas(panels, sizes, [["a"], ["b"], ["c"], ["d"]])
    built.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
    built.resize(800, 600)
    built.show()
    built.drop_block("b", DropSlot(False, 0, 0, target="a", side="right"))
    qapp.processEvents()
    yield built
    built.hide()
    built.deleteLater()


def _limit() -> int:
    return int(theme.metric("grid.close-extent"))


def _row(canvas: BlockCanvas):
    return canvas._row_widgets[0]


def _squash(canvas: BlockCanvas, qapp, sizes: list[int], handle: int = 1) -> None:
    """Put the row's divider where a drag would have, and mark it as one.

    *handle* is which divider the hand is on, because that is the only thing the
    warning is ever about — the panes on either side of it.
    """
    row = _row(canvas)
    row.setSizes(sizes)
    qapp.processEvents()
    row.update_collapse_marks(handle)


class TestWarningWhileTheDragIsStillHeld:
    def test_a_pane_dragged_under_the_limit_is_marked(self, canvas, qapp) -> None:
        _squash(canvas, qapp, [_limit() // 2, 780])

        frame = canvas.block_frame("a")
        assert frame._close_feedback is not None
        assert frame._close_feedback.state == DropFeedback.REJECT

    def test_dragging_back_out_takes_the_warning_down(self, canvas, qapp) -> None:
        _squash(canvas, qapp, [_limit() // 2, 780])
        _squash(canvas, qapp, [400, 400])

        assert canvas.block_frame("a")._close_feedback.state == DropFeedback.IDLE

    def test_a_pane_at_the_limit_is_not_marked(self, canvas, qapp) -> None:
        """The threshold is a floor to fall under, not one to touch."""
        _squash(canvas, qapp, [_limit(), 780 - _limit()])

        assert canvas.block_frame("a")._close_feedback is None

    def test_a_zero_grid_close_extent_turns_the_whole_thing_off(
        self, canvas, qapp, monkeypatch
    ) -> None:
        real = theme.metric
        monkeypatch.setattr(
            theme, "metric", lambda name: 0 if name == "grid.close-extent" else real(name)
        )
        _squash(canvas, qapp, [1, 799])
        _row(canvas).commit_collapse()
        qapp.processEvents()

        assert not canvas.is_hidden("a")


class TestLettingGo:
    def test_a_squashed_block_is_closed(self, canvas, qapp) -> None:
        _squash(canvas, qapp, [_limit() // 2, 780])
        _row(canvas).commit_collapse()
        qapp.processEvents()

        assert canvas.is_hidden("a")
        assert lt.keys(canvas.page_tree()) == ["b", "c", "d"]

    def test_it_is_closed_and_not_destroyed(self, canvas, qapp) -> None:
        _squash(canvas, qapp, [_limit() // 2, 780])
        _row(canvas).commit_collapse()
        qapp.processEvents()

        canvas.show_block("a")
        qapp.processEvents()
        assert not canvas.is_hidden("a")
        assert canvas.block_frame("a").isVisible()

    def test_the_warning_does_not_outlive_the_block(self, canvas, qapp) -> None:
        _squash(canvas, qapp, [_limit() // 2, 780])
        _row(canvas).commit_collapse()
        qapp.processEvents()

        assert canvas.block_frame("a")._close_feedback.state == DropFeedback.IDLE

    def test_one_ctrl_z_brings_it_back(self, canvas, qapp) -> None:
        history = LayoutHistory(canvas)
        canvas.gesture_finished.connect(history.record)

        _squash(canvas, qapp, [_limit() // 2, 780])
        _row(canvas).commit_collapse()
        qapp.processEvents()
        assert canvas.is_hidden("a")

        assert history.undo() is True
        qapp.processEvents()
        assert not canvas.is_hidden("a")

    def test_a_release_that_left_nothing_small_closes_nothing(self, canvas, qapp) -> None:
        _squash(canvas, qapp, [400, 400])
        _row(canvas).commit_collapse()
        qapp.processEvents()

        assert lt.keys(canvas.page_tree()) == ["a", "b", "c", "d"]


class TestAWholeRow:
    """A row dragged to nothing takes every block in it, and says so."""

    def _flatten(self, canvas, qapp) -> None:
        stack = canvas._stack
        stack.arm_grip(0)
        stack._on_dragged(0, _limit() // 2)
        qapp.processEvents()

    def test_every_block_in_the_row_is_warned(self, canvas, qapp) -> None:
        self._flatten(canvas, qapp)

        for key in ("a", "b"):
            feedback = canvas.block_frame(key)._close_feedback
            assert feedback is not None and feedback.state == DropFeedback.REJECT

    def test_letting_go_closes_them_all(self, canvas, qapp) -> None:
        self._flatten(canvas, qapp)
        canvas._stack._end_grip_drag()
        qapp.processEvents()

        assert canvas.is_hidden("a") and canvas.is_hidden("b")
        assert lt.keys(canvas.page_tree()) == ["c", "d"]

    def test_it_is_one_step_of_the_layout_history(self, canvas, qapp) -> None:
        """Not one per block: it was one flick of the wrist."""
        history = LayoutHistory(canvas)
        steps = []
        canvas.gesture_finished.connect(lambda: steps.append(history.record()))

        self._flatten(canvas, qapp)
        canvas._stack._end_grip_drag()
        qapp.processEvents()

        assert steps.count(True) == 1

    def test_a_rebuild_under_a_live_drag_decides_nothing(self, canvas, qapp) -> None:
        """The freeze has to come off; the blocks must not go with it."""
        self._flatten(canvas, qapp)

        canvas._relayout()  # re-renders the stack under the held grip
        qapp.processEvents()

        assert not canvas.is_hidden("a")
        assert canvas._stack.minimumHeight() == 0


def test_a_pane_this_drag_never_touched_is_left_alone(canvas, qapp) -> None:
    """The rule's other half, from inside a real drag.

    A window narrowed far enough leaves a splitter's proportional share under the
    limit all on its own, and that is not a gesture. Reading the whole run rather
    than the handle's own two panes meant the next drag of *any* divider in the
    row threw that block away — closed by a hand that never went near it.
    """
    canvas.drop_block("c", DropSlot(False, 0, 0, target="b", side="right"))
    qapp.processEvents()
    row = _row(canvas)
    assert row.count() == 3

    # `c` is a sliver because the window is narrow, not because anybody dragged it.
    row.setSizes([400, 380, _limit() // 2])
    qapp.processEvents()
    # The hand is on the *first* divider, between `a` and `b`.
    row.update_collapse_marks(1)
    row.commit_collapse()
    qapp.processEvents()

    assert not canvas.is_hidden("c")
    assert canvas.block_frame("c")._close_feedback is None


def test_a_restored_arrangement_with_a_tiny_pane_closes_nothing(canvas, qapp) -> None:
    """The rule's other half. Only a *drag* may close a block."""
    canvas.drop_block("c", DropSlot(False, 1, 0, target="b", side="right"))
    qapp.processEvents()
    model = canvas.arrangement()

    page = model["page"]
    # Reach in and give one cell a size no drag would ever leave behind.
    for node in page.get("children", []):
        if node.get("sizes"):
            node["sizes"] = [1] + list(node["sizes"])[1:]
            break

    assert canvas.apply_arrangement(model) is True
    qapp.processEvents()

    assert not canvas.is_hidden("b")
    assert not canvas.is_hidden("c")


def test_a_block_squashed_in_the_pinned_strip_closes_too(qapp, monkeypatch) -> None:
    """The strip runs the page's grid engine, so it gets the page's rule.

    A real sheet rather than the bare fixture above: the strip only exists once
    something has given the canvas a board to render into. And a raised threshold
    rather than a tiny pane, because ``setSizes`` is clamped by the frame's own
    minimum and cannot put one where a real drag can — the rule under test is the
    strip's dividers being *followed* at all, which they were not.
    """
    real = theme.metric
    monkeypatch.setattr(
        theme, "metric", lambda name: 200 if name == "grid.close-extent" else real(name)
    )

    from mm_companion.core.data_loader import load_game_data
    from mm_companion.ui.character_sheet import CharacterSheet

    sheet = CharacterSheet(load_game_data())
    sheet.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
    sheet.resize(1100, 900)
    sheet.show()
    for _ in range(6):
        qapp.processEvents()

    canvas = sheet.canvas
    canvas.unpin_all()
    canvas.pin_block("conditions")
    canvas.pin_at("complications", target="conditions", side="bottom")
    for _ in range(6):
        qapp.processEvents()

    splitters = sheet.board.panel.splitters()
    assert splitters, "the strip built no divider to drag"
    strip = splitters[0]
    strip.setSizes([20, 400])
    qapp.processEvents()
    strip.update_collapse_marks(1)
    strip.commit_collapse()
    for _ in range(4):
        qapp.processEvents()

    assert canvas.is_hidden("conditions")

    sheet.hide()
    sheet.deleteLater()


def test_hide_block_still_works_on_its_own(canvas, qapp) -> None:
    """It is the same path now; the single-block spelling has to keep its promises."""
    canvas.hide_block("c")
    qapp.processEvents()

    assert canvas.is_hidden("c")
    canvas.show_block("c")
    qapp.processEvents()
    assert not canvas.is_hidden("c")
    assert lt.keys(canvas.page_tree()) == ["a", "b", "c", "d"]


def test_hiding_a_block_twice_is_a_no_op(canvas, qapp) -> None:
    canvas.hide_block("c")
    qapp.processEvents()
    canvas.hide_block("c")
    qapp.processEvents()

    assert canvas.is_hidden("c")
    assert QPoint  # the import is used by the fixture's helpers


def test_collapsing_a_tab_group_closes_every_block_in_it(canvas, qapp) -> None:
    """A group's inactive tabs are hidden widgets, and they go with it."""
    canvas.merge_blocks("c", "b")  # b and c become one cell, sharing the row with a
    qapp.processEvents()
    _squash(canvas, qapp, [400, _limit() // 2])
    _row(canvas).commit_collapse()
    qapp.processEvents()

    assert canvas.is_hidden("b") and canvas.is_hidden("c")
    assert not canvas.is_hidden("a")

"""Moving a whole tab group, and the menus that hang off its bar.

Two gaps, both of them the same shape: a group was a cell you could put blocks
*into* and take blocks *out of*, and nothing you could do anything with as a
cell. Moving it meant dragging every tab out and merging them back together at
the far end — the same arrangement reached through four intermediate ones the
user had to watch go by — and a block inside a group had no menu at all, because
the title bar its menu lives on is the one thing a group takes away.

The strip of bar past the last tab is the group's handle now: drag it and the
cell moves, right-click it and the cell's own menu opens. A tab keeps its block's
menu, plus the one item that is about being in a group at all.
"""

from __future__ import annotations

import pytest
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QApplication, QLabel

from mm_companion.ui import layout_tree as lt
from mm_companion.ui.block_canvas import BlockCanvas, DropSlot
from mm_companion.ui.block_sizes import RecommendedSize
from mm_companion.ui.tab_group import TabGroupFrame


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


@pytest.fixture
def canvas(qapp: QApplication) -> BlockCanvas:
    """Four blocks: c and d merged into one cell, a and b in rows of their own."""
    panels = [(key, key.upper(), QLabel(key)) for key in ("a", "b", "c", "d")]
    sizes = {key: RecommendedSize(200, 120) for key in ("a", "b", "c", "d")}
    built = BlockCanvas(panels, sizes, [["a"], ["b"], ["c"], ["d"]])
    built.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
    built.resize(900, 700)
    built.show()
    built.merge_blocks("d", "c")
    for _ in range(4):
        qapp.processEvents()
    yield built
    built.hide()
    built.deleteLater()


def group_of(canvas: BlockCanvas) -> TabGroupFrame:
    return canvas.group_for("c")


def _send(widget, kind, local: QPoint, *, held: bool) -> None:
    buttons = Qt.MouseButton.LeftButton if held else Qt.MouseButton.NoButton
    QApplication.sendEvent(
        widget,
        QMouseEvent(
            kind,
            QPointF(local),
            QPointF(widget.mapToGlobal(local)),
            Qt.MouseButton.LeftButton,
            buttons,
            Qt.KeyboardModifier.NoModifier,
        ),
    )


class TestTheHandle:
    def test_the_bar_no_longer_owns_the_strip_past_the_last_tab(self, canvas, qapp) -> None:
        """Which is what makes "a tab" and "not a tab" two widgets rather than two
        readings of one coordinate."""
        group = group_of(canvas)

        assert group._handle.width() > 0
        assert group._bar.width() < group.width()

    def test_it_survives_a_cell_too_narrow_for_its_tabs(self, canvas, qapp) -> None:
        """Otherwise a group would be the one cell on the page that cannot be
        moved, and only when it was small enough to want moving."""
        from mm_companion.ui import theme

        group = group_of(canvas)
        floor = int(theme.metric("block.min-extent"))
        for width in (400, 200, 90):
            group.setFixedWidth(width)
            for _ in range(3):
                qapp.processEvents()
            assert group._handle.width() >= floor, f"the handle vanished at {width}px"

    def test_a_press_and_a_twitch_is_not_a_drag(self, canvas, qapp) -> None:
        group = group_of(canvas)
        group.groupDragStarted.emit(QPoint(500, 100))
        group.groupDragMoved.emit(QPoint(501, 100))

        assert canvas._drag_keys == ()

    def test_moving_far_enough_starts_carrying_the_whole_cell(self, canvas, qapp) -> None:
        group = group_of(canvas)
        group.groupDragStarted.emit(QPoint(500, 100))
        group.groupDragMoved.emit(QPoint(700, 400))

        assert set(canvas._drag_keys) == {"c", "d"}

    def test_the_gesture_never_offers_the_cell_a_place_inside_itself(self, canvas, qapp) -> None:
        group = group_of(canvas)
        group.groupDragStarted.emit(QPoint(500, 100))
        group.groupDragMoved.emit(QPoint(700, 400))

        assert canvas._merge_target("c") is None
        assert canvas._merge_target("d") is None
        assert canvas._merge_target("a") == "a"

    def test_releasing_without_a_drag_leaves_the_page_alone(self, canvas, qapp) -> None:
        before = canvas.arrangement()["page"]
        group = group_of(canvas)
        group.groupDragStarted.emit(QPoint(500, 100))
        group.groupDragReleased.emit(QPoint(500, 100))
        qapp.processEvents()

        assert canvas.arrangement()["page"] == before
        assert canvas._drag_keys == ()

    def test_the_handle_drives_the_gesture_from_real_mouse_events(self, canvas, qapp) -> None:
        group = group_of(canvas)
        handle = group._handle
        _send(handle, QMouseEvent.Type.MouseButtonPress, QPoint(4, 4), held=True)
        _send(handle, QMouseEvent.Type.MouseMove, QPoint(240, 300), held=True)

        assert set(canvas._drag_keys) == {"c", "d"}

        _send(handle, QMouseEvent.Type.MouseButtonRelease, QPoint(240, 300), held=False)
        qapp.processEvents()
        assert canvas._drag_keys == ()


class TestWhereItLands:
    def _carry(self, canvas) -> None:
        group = group_of(canvas)
        group.groupDragStarted.emit(QPoint(500, 100))
        group.groupDragMoved.emit(QPoint(700, 400))

    def test_dropped_beside_a_block_it_arrives_whole(self, canvas, qapp) -> None:
        self._carry(canvas)
        canvas.drop_group(("c", "d"), DropSlot(False, 0, 0, target="a", side="right"))
        qapp.processEvents()

        page = canvas.page_tree()
        assert lt.keys(page.children[0]) == ["a", "c", "d"]
        assert lt.leaf_for(page, "c") == lt.leaf_for(page, "d")

    def test_dropped_into_a_block_everything_becomes_one_group(self, canvas, qapp) -> None:
        self._carry(canvas)
        canvas.drop_group(("c", "d"), DropSlot(False, 0, 0, onto="a", target="a", side="right"))
        qapp.processEvents()

        assert lt.keys(lt.leaf_for(canvas.page_tree(), "a")) == ["a", "c", "d"]

    def test_dropped_in_a_gap_it_gets_a_row_of_its_own(self, canvas, qapp) -> None:
        self._carry(canvas)
        canvas.drop_group(("c", "d"), DropSlot(True, 0, 0))
        qapp.processEvents()

        assert lt.keys(canvas.page_tree().children[0]) == ["c", "d"]

    def test_a_cell_of_one_is_not_a_group_and_is_refused(self, canvas, qapp) -> None:
        before = canvas.arrangement()["page"]
        canvas.drop_group(("a",), DropSlot(False, 0, 0, target="b", side="right"))
        qapp.processEvents()

        assert canvas.arrangement()["page"] == before

    def test_a_drop_that_moved_nothing_is_not_a_layout_step(self, canvas, qapp) -> None:
        steps: list[int] = []
        canvas.gesture_finished.connect(lambda: steps.append(1))
        canvas.drop_group(("c", "d"), DropSlot(False, 0, 0, target="c", side="right"))
        qapp.processEvents()

        assert steps == []

    def test_the_strip_is_not_offered_to_a_group(self, canvas, qapp, monkeypatch) -> None:
        """A multi-key cell in the strip renders as one visible block and no bar,
        so a group dropped there would arrive with most of it nowhere."""
        asked: list[bool] = []

        def _refuse(_pos):
            asked.append(True)
            return None

        monkeypatch.setattr(canvas, "_pin_hit_test", _refuse)
        self._carry(canvas)

        assert asked == [], "the strip was hit-tested during a group drag"


class TestTheMenus:
    def labels(self, menu) -> list[str]:
        return [action.text() for action in menu.actions() if not action.isSeparator()]

    def test_a_tab_offers_its_own_blocks_menu(self, canvas, qapp) -> None:
        labels = self.labels(canvas.block_menu("c"))

        assert "Pop out into its own window" in labels
        assert "Close" in labels

    def test_and_the_one_item_that_is_about_being_in_a_group(self, canvas, qapp) -> None:
        assert "Move out of the group" in self.labels(canvas.block_menu("c"))
        assert "Move out of the group" not in self.labels(canvas.block_menu("a"))

    def test_the_handle_offers_the_cells_own_menu(self, canvas, qapp) -> None:
        assert self.labels(canvas.group_menu(["c", "d"])) == [
            "Fit to content",
            "Ungroup",
            "Close these blocks",
        ]

    def test_a_group_menu_for_something_that_is_not_a_group_is_empty(self, canvas) -> None:
        assert canvas.group_menu(["a"]).isEmpty()
        assert canvas.group_menu(["a", "nobody"]).isEmpty()

    def test_moving_a_block_out_leaves_it_beside_what_it_left(self, canvas, qapp) -> None:
        canvas.split_block_out("d")
        qapp.processEvents()

        page = canvas.page_tree()
        assert lt.leaf_for(page, "c") != lt.leaf_for(page, "d")
        assert lt.keys(page.children[2]) == ["c", "d"]

    def test_moving_a_block_out_that_is_not_in_a_group_does_nothing(self, canvas, qapp) -> None:
        before = canvas.arrangement()["page"]
        canvas.split_block_out("a")
        qapp.processEvents()

        assert canvas.arrangement()["page"] == before

    def test_ungroup_leaves_one_cell_per_block_in_tab_order(self, canvas, qapp) -> None:
        canvas.ungroup(["c", "d"])
        qapp.processEvents()

        page = canvas.page_tree()
        assert lt.keys(page.children[2]) == ["c", "d"]
        assert lt.leaf_for(page, "c") != lt.leaf_for(page, "d")

    def test_ungroup_is_one_entry_in_the_layout_history(self, canvas, qapp) -> None:
        """It was one menu click, however many blocks came apart."""
        steps: list[int] = []
        canvas.gesture_finished.connect(lambda: steps.append(1))

        canvas.ungroup(["c", "d"])
        qapp.processEvents()

        assert len(steps) == 1

    def test_closing_the_group_closes_every_block_in_it(self, canvas, qapp) -> None:
        canvas.close_blocks(["c", "d"])
        qapp.processEvents()

        assert canvas.is_hidden("c") and canvas.is_hidden("d")

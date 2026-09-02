"""The resizable grid, from the drop that shapes it to the drag that sizes it.

Three claims, and they are the three the whole rework rests on:

* a block can be dropped *under* another, not only beside it, and the page grows
  a nested split to hold it;
* a divider inside a row is zero-sum and a divider between rows is not — width
  tiles, height scrolls;
* nothing anywhere reports a minimum big enough to stop any of it.
"""

from __future__ import annotations

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtWidgets import QApplication, QLabel, QScrollArea, QSplitter

from mm_companion.ui import layout_tree as lt
from mm_companion.ui import theme
from mm_companion.ui.block_canvas import BlockCanvas
from mm_companion.ui.block_frame import BlockFrame
from mm_companion.ui.block_sizes import RecommendedSize
from mm_companion.ui.grid_view import GridSplitter, RowStack


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


@pytest.fixture
def canvas(qapp: QApplication) -> BlockCanvas:
    """Four plain blocks in four rows, with no character sheet involved."""
    panels = [(key, key.upper(), QLabel(key)) for key in ("a", "b", "c", "d")]
    sizes = {
        "a": RecommendedSize(200, 120),
        "b": RecommendedSize(300, 150),
        "c": RecommendedSize(),
        "d": RecommendedSize(240, 100),
    }
    built = BlockCanvas(panels, sizes, [["a"], ["b"], ["c"], ["d"]])
    # Shown, or nothing is ever laid out and every splitter answers with the
    # sizes it was born with. WA_DontShowOnScreen gives a real layout pass
    # without a window appearing, which is what the pinned-strip tests do too.
    built.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
    built.resize(800, 600)
    built.show()
    for _ in range(6):
        qapp.processEvents()
    yield built
    built.hide()
    built.deleteLater()


def rows_of(canvas: BlockCanvas) -> list[list[str]]:
    return [lt.keys(child) for child in canvas.page_tree().children]


# -- dropping: beside, and under ---------------------------------------------


class TestDropping:
    def test_a_block_dropped_beside_another_joins_its_row(self, canvas: BlockCanvas) -> None:
        canvas.drop_block("b", _slot(target="a", side="right"))

        assert rows_of(canvas) == [["a", "b"], ["c"], ["d"]]

    def test_a_block_dropped_under_another_stacks_inside_the_row(self, canvas: BlockCanvas) -> None:
        """The move the old page could not make at all: a row was the only
        container there was, so 'under' could only ever mean 'a new row'."""
        canvas.drop_block("b", _slot(target="a", side="right"))
        canvas.drop_block("c", _slot(target="a", side="bottom"))

        row = canvas.page_tree().children[0]
        assert row.orientation == lt.HORIZONTAL
        assert row.children[0] == lt.Split(lt.VERTICAL, (lt.Leaf(("a",)), lt.Leaf(("c",))))
        assert row.children[1] == lt.Leaf(("b",))

    def test_the_stack_is_rendered_as_a_real_nested_splitter(
        self, canvas: BlockCanvas, qapp: QApplication
    ) -> None:
        canvas.drop_block("b", _slot(target="a", side="right"))
        canvas.drop_block("c", _slot(target="a", side="bottom"))
        qapp.processEvents()

        row = canvas._row_widgets[0]
        assert isinstance(row, GridSplitter)
        assert isinstance(row.widget(0), GridSplitter)  # the a-over-c stack
        assert isinstance(row.widget(1), BlockFrame)

    def test_a_block_dropped_in_the_gap_still_makes_a_new_row(self, canvas: BlockCanvas) -> None:
        canvas.drop_block("d", _slot(new_row=True, row=0))

        assert rows_of(canvas) == [["d"], ["a"], ["b"], ["c"]]

    def test_dropping_a_block_where_it_already_is_changes_nothing(
        self, canvas: BlockCanvas
    ) -> None:
        before = rows_of(canvas)

        canvas.drop_block("a", _slot(target="a", side="right"))

        assert rows_of(canvas) == before


def _slot(*, target: str | None = None, side: str = "right", new_row: bool = False, row: int = 0):
    from mm_companion.ui.block_canvas import DropSlot

    return DropSlot(new_row, row, 0, None, target, side)


# -- the two kinds of divider -------------------------------------------------


class TestDividers:
    def test_a_divider_inside_a_row_is_zero_sum(
        self, canvas: BlockCanvas, qapp: QApplication
    ) -> None:
        """Give one block width and its neighbour loses exactly that much: a row
        is the viewport's width and there is nowhere else for it to come from."""
        canvas.drop_block("b", _slot(target="a", side="right"))
        qapp.processEvents()
        row = canvas._row_widgets[0]
        row.setSizes([400, 400])
        qapp.processEvents()
        before = sum(row.sizes())

        row.setSizes([550, 250])
        qapp.processEvents()

        assert sum(row.sizes()) == before

    def test_a_row_dragged_taller_makes_the_page_longer(
        self, canvas: BlockCanvas, qapp: QApplication
    ) -> None:
        """And *not* zero-sum, which is the whole reason the page is not a
        splitter: pulling row 0 down must push rows 1-3 down, not squash row 1."""
        before_height = canvas.sizeHint().height()
        before_rows = canvas._stack.heights()[1:]

        canvas._stack._on_dragged(0, canvas._stack._rows[0].sizeHint().height() + 200)
        qapp.processEvents()

        assert canvas.sizeHint().height() > before_height
        assert canvas._stack.heights()[1:] == before_rows  # nobody else moved

    def test_a_row_nobody_dragged_still_follows_its_content(
        self, canvas: BlockCanvas, qapp: QApplication
    ) -> None:
        assert canvas._stack.heights() == [0, 0, 0, 0]
        assert canvas._stack._holders[0].maximumHeight() >= 100_000  # nothing pinned it

    def test_a_dragged_height_is_kept_and_a_cleared_one_is_released(
        self, canvas: BlockCanvas, qapp: QApplication
    ) -> None:
        canvas._stack._on_dragged(1, 260)
        # The *holder*, never the row: a row holding one block is that block's
        # frame, and a height set on it would follow the block everywhere.
        assert canvas._stack._holders[1].maximumHeight() == 260
        assert canvas._stack._rows[1].maximumHeight() >= 100_000

        canvas._stack._on_dragged(1, 0)

        assert canvas._stack._holders[1].maximumHeight() >= 100_000


class TestDetents:
    def test_a_row_divider_offers_its_blocks_recommended_height(self, canvas: BlockCanvas) -> None:
        targets = canvas._stack._row_detents(canvas._stack._rows[0])

        assert targets == [120]  # block "a" recommends 120 tall

    def test_a_split_divider_offers_the_block_on_either_side_of_it(
        self, canvas: BlockCanvas, qapp: QApplication
    ) -> None:
        """Two answers, so a divider between two blocks can settle either of them
        without going round the other side."""
        canvas.drop_block("b", _slot(target="a", side="right"))
        qapp.processEvents()
        row = canvas._row_widgets[0]
        row.setSizes([400, 400])
        qapp.processEvents()

        targets = row.detent_positions(1)

        gap = row.handleWidth()
        total = sum(row.sizes()) + gap
        assert 200 in targets  # "a" at its own recommended width
        assert total - gap - 300 in targets  # and "b" at its own, from the other side

    def test_a_block_with_no_recommendation_still_offers_its_content(
        self, canvas: BlockCanvas
    ) -> None:
        # "c" states nothing, so it falls back to the honest answer: its content.
        targets = canvas._stack._row_detents(canvas._stack._rows[2])

        assert targets and targets[0] > 0


# -- and none of it is blocked by a minimum ----------------------------------


class TestNothingRefuses:
    def test_the_page_reports_no_width_worth_speaking_of(self, canvas: BlockCanvas) -> None:
        assert canvas.minimumSizeHint().width() <= int(theme.metric("block.min-extent"))

    def test_the_page_reports_its_full_height_so_it_scrolls(self, canvas: BlockCanvas) -> None:
        """The other half of the asymmetry. Without this the scroll area would
        size the page to the viewport and squash every row to fit."""
        assert canvas.minimumSizeHint().height() == canvas.sizeHint().height()

    def test_a_page_taller_than_its_window_scrolls_rather_than_squashing(
        self, canvas: BlockCanvas, qapp: QApplication
    ) -> None:
        page = QScrollArea()
        page.setWidgetResizable(True)
        page.setWidget(canvas)
        page.resize(600, 200)
        page.show()
        for _ in range(6):
            qapp.processEvents()

        assert page.verticalScrollBar().maximum() > 0
        page.deleteLater()

    def test_a_row_can_be_dragged_narrower_than_any_block_in_it(
        self, canvas: BlockCanvas, qapp: QApplication
    ) -> None:
        canvas.drop_block("b", _slot(target="a", side="right"))
        qapp.processEvents()
        row = canvas._row_widgets[0]

        row.setSizes([1, 799])
        qapp.processEvents()

        assert row.sizes()[0] < 200  # well under "a"'s recommendation, and allowed

    def test_a_splitter_lets_a_block_collapse_to_nothing(
        self, canvas: BlockCanvas, qapp: QApplication
    ) -> None:
        """The old page forbade it, because a squashed block meant a clipped one.
        A block scrolls inside itself now, so there is nothing left to protect."""
        canvas.drop_block("b", _slot(target="a", side="right"))
        qapp.processEvents()

        assert canvas._row_widgets[0].childrenCollapsible() is True


class TestTheStackItself:
    def test_it_holds_a_grip_under_every_row(self, qapp: QApplication) -> None:
        stack = RowStack()
        rows = [QLabel("one"), QLabel("two")]

        stack.set_rows(rows, [0, 0])

        assert len(stack._grips) == 2
        stack.deleteLater()

    def test_rebuilding_it_sheds_the_old_grips(self, qapp: QApplication) -> None:
        stack = RowStack()
        stack.set_rows([QLabel("one"), QLabel("two")], [0, 0])

        stack.set_rows([QLabel("only")], [0])

        assert len(stack._grips) == 1
        stack.deleteLater()

    def test_heights_shorter_than_the_rows_are_padded_out(self, qapp: QApplication) -> None:
        stack = RowStack()

        stack.set_rows([QLabel("one"), QLabel("two"), QLabel("three")], [100])

        assert stack.heights() == [100, 0, 0]
        stack.deleteLater()


def test_a_splitter_drag_is_remembered_in_the_tree(canvas: BlockCanvas, qapp) -> None:
    canvas.drop_block("b", _slot(target="a", side="right"))
    qapp.processEvents()
    canvas._row_widgets[0].setSizes([500, 300])
    qapp.processEvents()

    canvas._remember_sizes()

    row = canvas.page_tree().children[0]
    assert isinstance(row, lt.Split)
    assert row.sizes == tuple(canvas._row_widgets[0].sizes())
    assert row.sizes[0] > row.sizes[1]  # the drag really did favour the first


def test_sizes_survive_a_save_and_restore(canvas: BlockCanvas, qapp) -> None:
    canvas.drop_block("b", _slot(target="a", side="right"))
    qapp.processEvents()
    canvas._row_widgets[0].setSizes([520, 280])
    canvas._stack._on_dragged(0, 300)
    qapp.processEvents()
    model = canvas.arrangement()

    canvas.reset()
    assert canvas.page_tree().children[0] == lt.Leaf(("a",))

    assert canvas.apply_arrangement(model) is True
    qapp.processEvents()
    assert lt.keys(canvas.page_tree().children[0]) == ["a", "b"]
    assert canvas.page_tree().sizes[0] == 300
    assert isinstance(canvas._row_widgets[0], QSplitter)


def test_a_drag_gesture_lands_a_block_under_another(canvas: BlockCanvas, qapp) -> None:
    """End to end through the real controller, not the structural op underneath.

    The bottom band of a block means "and below it", which is the one reading the
    old hit test had no way to express.
    """
    canvas.drop_block("b", _slot(target="a", side="right"))
    qapp.processEvents()
    frame = canvas.block_frame("a")
    # Inside the row's core (which is inset by _GAP, where a drop means "a new
    # row") and inside the band along the block's own bottom edge.
    bottom = frame.mapToGlobal(QPoint(frame.width() // 2, frame.height() - 20))

    slot = canvas._hit_test(bottom)

    assert slot is not None
    assert slot.target == "a"
    assert slot.side == "bottom"

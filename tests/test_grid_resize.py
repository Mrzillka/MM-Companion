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
from PySide6.QtCore import QPoint, QRect, QSize, Qt
from PySide6.QtWidgets import QApplication, QLabel, QScrollArea, QSplitter, QWidget

from mm_companion.ui import layout_tree as lt
from mm_companion.ui.block_canvas import BlockCanvas, drop_side
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
    def test_a_row_divider_offers_fit_to_content_and_the_recommendation(
        self, canvas: BlockCanvas
    ) -> None:
        """Two marks, and the first is the more useful: the height at which
        nothing in the row has to scroll."""
        stack = canvas._stack
        targets = stack._row_detents(0)

        assert 120 in targets  # block "a" recommends 120 tall
        assert stack._rows[0].sizeHint().height() in targets  # and fit-to-content

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
        targets = canvas._stack._row_detents(2)

        assert targets and targets[0] > 0


# -- and none of it is blocked by a minimum ----------------------------------


class TestNothingRefuses:
    def test_the_page_reports_no_width_worth_speaking_of(self, canvas: BlockCanvas) -> None:
        """Small enough to be no constraint. The exact number is Qt's now — the
        page states no minimum of its own, so this is the rows' own minimums summed
        by an ordinary QVBoxLayout."""
        assert canvas.minimumSizeHint().width() < 200

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


class TestWhereADropLands:
    """The zone rule on its own: shares of a block, not pixel bands.

    The bands used to be 28px of merge inset and a 24px stack band, which left
    "beside this block" as a 28px strip down each edge — a target you had to aim
    at, on a gesture people use constantly. And the merge was never advertised:
    there is no discovering a gesture whose target is the part of the block you
    were already standing on.
    """

    RECT = QRect(0, 0, 300, 300)

    def side(self, x: int, y: int) -> str | None:
        return drop_side(QPoint(x, y), self.RECT, merge_share=1 / 3)

    def test_the_middle_ninth_means_merge(self) -> None:
        assert self.side(150, 150) is None
        assert self.side(110, 110) is None  # just inside the corner of the core
        assert self.side(190, 190) is None

    def test_just_outside_the_core_is_already_a_side(self) -> None:
        assert self.side(150, 95) == "top"
        assert self.side(150, 205) == "bottom"
        assert self.side(95, 150) == "left"
        assert self.side(205, 150) == "right"

    def test_a_whole_quarter_of_the_block_means_beside_it(self) -> None:
        """The complaint this is here to answer: dropping a block second in a row."""
        assert self.side(10, 150) == "left"
        assert self.side(60, 150) == "left"
        assert self.side(290, 150) == "right"

    def test_a_corner_belongs_to_the_edge_it_is_nearest(self) -> None:
        assert self.side(10, 40) == "left"
        assert self.side(40, 10) == "top"
        assert self.side(290, 260) == "right"
        assert self.side(260, 290) == "bottom"

    def test_the_zones_grow_with_the_block(self) -> None:
        """A pixel band would be a hairline on a big block and most of a small one."""
        wide = QRect(0, 0, 1200, 300)
        assert drop_side(QPoint(200, 150), wide, merge_share=1 / 3) == "left"
        narrow = QRect(0, 0, 120, 300)
        assert drop_side(QPoint(20, 150), narrow, merge_share=1 / 3) == "left"
        assert drop_side(QPoint(60, 150), narrow, merge_share=1 / 3) is None

    def test_a_degenerate_rect_does_not_divide_by_zero(self) -> None:
        assert drop_side(QPoint(0, 0), QRect(0, 0, 0, 0), merge_share=1 / 3) is not None


class TestTheDropMarks:
    """Three marks between them, and that is what makes the merge findable."""

    def _dragging(self, canvas: BlockCanvas, qapp) -> None:
        canvas.drop_block("b", _slot(target="a", side="right"))
        canvas._stack._on_dragged(0, 200)
        qapp.processEvents()
        canvas._drag_key = "b"  # as a real drag would have set it

    def test_a_side_marks_the_half_the_block_would_take(self, canvas, qapp) -> None:
        self._dragging(canvas, qapp)
        frame = canvas.block_frame("a")
        geo = canvas._row_geometry(frame)

        canvas._show_indicator(canvas._hit_test(frame.mapToGlobal(QPoint(6, frame.height() // 2))))

        assert canvas._region_mark.isVisible()
        marked = canvas._region_mark.geometry()
        assert marked.left() == geo.left()
        assert abs(marked.width() - geo.width() // 2) <= 1
        assert marked.height() == geo.height()

    def test_the_middle_washes_the_whole_block_instead(self, canvas, qapp) -> None:
        self._dragging(canvas, qapp)
        frame = canvas.block_frame("a")
        centre = frame.mapToGlobal(QPoint(frame.width() // 2, frame.height() // 2))

        slot = canvas._hit_test(centre)
        canvas._show_indicator(slot)

        assert slot.onto == "a"
        assert not canvas._region_mark.isVisible()
        assert canvas._merge_hint == "a"

    def test_a_new_row_marks_the_seam_and_nothing_else(self, canvas, qapp) -> None:
        self._dragging(canvas, qapp)
        frame = canvas.block_frame("a")
        seam = frame.mapToGlobal(QPoint(frame.width() // 2, frame.height() - 2))

        slot = canvas._hit_test(seam)
        canvas._show_indicator(slot)

        assert slot.new_row is True
        assert not canvas._region_mark.isVisible()
        assert canvas._indicator.isVisible()

    def test_ending_the_drag_takes_every_mark_down(self, canvas, qapp) -> None:
        self._dragging(canvas, qapp)
        frame = canvas.block_frame("a")
        canvas._show_indicator(canvas._hit_test(frame.mapToGlobal(QPoint(6, frame.height() // 2))))
        assert canvas._region_mark.isVisible()  # positive control

        canvas._end_drag()

        assert not canvas._region_mark.isVisible()
        assert not canvas._indicator.isVisible()


def test_a_drag_gesture_lands_a_block_under_another(canvas: BlockCanvas, qapp) -> None:
    """End to end through the real controller, not the structural op underneath.

    The bottom of a block means "and below it", which is the one reading the
    old hit test had no way to express.
    """
    canvas.drop_block("b", _slot(target="a", side="right"))
    # A row tall enough to have a middle and two ends. The fixture's blocks are
    # bare labels, and on a 40px row every point is within _GAP of a boundary.
    canvas._stack._on_dragged(0, 200)
    qapp.processEvents()
    frame = canvas.block_frame("a")
    # The zones are shares of the block, not pixel bands, so the point has to be
    # named as one: three quarters of the way down, well outside the middle ninth
    # that means "merge", and still inside the row's core (which is inset by
    # _GAP, where a drop would mean "a new row" instead).
    bottom = frame.mapToGlobal(QPoint(frame.width() // 2, frame.height() * 3 // 4))

    slot = canvas._hit_test(bottom)

    assert slot is not None
    assert slot.target == "a"
    assert slot.side == "bottom"


class _Recommending(QWidget):
    """A pane that states a recommended width, the way a block frame does.

    A *hint* as well, and separately, because the two are different answers: a
    block with no recommendation still has content, and what a fit means for it
    is the width that content takes.
    """

    def __init__(self, width: int, hint: int = 150) -> None:
        super().__init__()
        self._width = width
        self._hint = hint

    def recommended_size(self) -> RecommendedSize:
        return RecommendedSize(self._width, 0)

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt override
        return QSize(self._hint, 50)


def _fittable(qapp, widths: list[int]) -> GridSplitter:
    splitter = GridSplitter(Qt.Orientation.Horizontal)
    for width in widths:
        splitter.addWidget(_Recommending(width))
    splitter.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
    splitter.resize(600, 100)
    splitter.show()
    qapp.processEvents()
    return splitter


class TestFittingToContent:
    """The gesture that says "just make it right".

    The detent marks where a block's recommended size is and a drag can settle on
    it, but neither is a way to simply *ask* for it — the one thing the
    recommendations had no direct route to, and the gesture a splitter has
    answered with a double-click for as long as there have been splitters.
    """

    def test_it_fits_the_pane_before_the_divider(self, qapp) -> None:
        splitter = _fittable(qapp, [200, 200])
        total = sum(splitter.sizes())
        splitter.setSizes([100, total - 100])
        qapp.processEvents()

        splitter.fit_pane(0)
        qapp.processEvents()

        assert splitter.sizes()[0] == 200
        assert sum(splitter.sizes()) == total, "the row's total width moved"

    def test_a_pane_with_no_recommendation_fits_its_content(self, qapp) -> None:
        """Abilities and Resistances state no width; their tables measure one."""
        splitter = _fittable(qapp, [0, 200])
        splitter.setSizes([50, sum(splitter.sizes()) - 50])
        qapp.processEvents()

        splitter.fit_pane(0)
        qapp.processEvents()

        assert splitter.sizes()[0] == 150  # the hint, since there is no recommendation

    def test_fitting_the_last_pane_is_refused_rather_than_wrapping(self, qapp) -> None:
        """There is no neighbour on the far side to take the width from."""
        splitter = _fittable(qapp, [200, 200])
        before = splitter.sizes()

        splitter.fit_pane(1)

        assert splitter.sizes() == before

    def test_fitting_a_row_forgets_its_height_rather_than_measuring_one(self, canvas, qapp) -> None:
        """So it goes on tracking the content, exactly as an undragged row does."""
        canvas._stack.arm_grip(0)
        canvas._stack._on_dragged(0, 320)
        qapp.processEvents()
        assert canvas._stack.heights()[0] == 320

        canvas._stack.fit_row(0)
        qapp.processEvents()

        assert canvas._stack.heights()[0] == 0

    def test_fitting_a_block_does_both_at_once(self, canvas, qapp) -> None:
        canvas.drop_block("b", _slot(target="a", side="right"))
        canvas._stack.arm_grip(0)
        canvas._stack._on_dragged(0, 320)
        qapp.processEvents()
        canvas._row_widgets[0].setSizes([80, 700])
        qapp.processEvents()

        canvas.fit_block("a")
        qapp.processEvents()

        assert canvas._row_widgets[0].sizes()[0] == 200  # a's recommendation
        assert canvas._stack.heights()[0] == 0

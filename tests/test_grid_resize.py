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
from PySide6.QtWidgets import (
    QApplication,
    QFormLayout,
    QGroupBox,
    QLabel,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

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
        stacked = row.children[0]
        assert stacked.orientation == lt.VERTICAL
        assert stacked.children == (lt.Leaf(("a",)), lt.Leaf(("c",)))
        # The pair divides the height "a" had, which is the promise the drop mark
        # made: the wash filled the bottom half of "a" and nothing else moved.
        assert len(set(stacked.sizes)) == 1, "the newcomer did not take half of its target"
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


class TestABlockTallerThanItsContent:
    """What a resizable page is the first thing that can hand a block: *more* room
    than it wants down."""

    def _frame(self, qapp) -> BlockFrame:
        section = QWidget()
        layout = QVBoxLayout(section)
        for text in ("one", "two", "three"):
            layout.addWidget(QLabel(text))
        frame = BlockFrame("a", "A", section, RecommendedSize(200, 120), _NoHost())
        frame.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
        frame.resize(300, 600)  # far taller than three labels
        frame.show()
        for _ in range(6):
            qapp.processEvents()
        return frame

    def test_the_slack_goes_under_the_lines_and_not_between_them(self, qapp) -> None:
        """``setWidgetResizable`` gives the surplus to the section, and a stacked
        layout with nothing expanding in it spreads that *equally between its
        items* — so a Powers block dragged twice as tall as its cards did not grow
        a margin at the bottom, it grew a gap between every line on every card.

        The section takes the whole height (it draws the border, so anything else
        leaves that border stopping half way down a block that has not finished
        drawing) and puts the surplus in the trailing stretch under the last line.
        """
        frame = self._frame(qapp)
        try:
            section = frame.section
            assert section.height() > section.sizeHint().height(), "the section did not fill"
            assert section.y() == 0
            labels = section.findChildren(QLabel)
            gaps = [
                labels[i + 1].y() - labels[i].geometry().bottom() for i in range(len(labels) - 1)
            ]
            assert max(gaps) - min(gaps) <= 1, f"the slack was shared out between the lines: {gaps}"
            last = labels[-1].geometry().bottom()
            assert section.height() - last > 3 * max(gaps), "the slack is not under the last line"
        finally:
            frame.hide()
            frame.deleteLater()

    def test_a_section_that_asks_for_the_height_still_gets_it(self, qapp) -> None:
        """A note's editor, the roller's history, the portrait, the turn order: all
        of them grow into the room, and say so with ``fills_height``."""
        section = QWidget()
        section.fills_height = True
        QVBoxLayout(section).addWidget(QLabel("filler"))
        frame = BlockFrame("a", "A", section, RecommendedSize(200, 120), _NoHost())
        frame.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
        frame.resize(300, 600)
        frame.show()
        for _ in range(6):
            qapp.processEvents()
        try:
            assert section.height() > section.sizeHint().height()
        finally:
            frame.hide()
            frame.deleteLater()

    def test_a_form_fills_the_block_without_its_captions_drifting_apart(self, qapp) -> None:
        """A ``QFormLayout`` holds widgets rather than items, so it cannot take a
        stretch — and left to itself it shares the surplus out over its rows, which
        is the same "gap between every line" the box layouts had. One trailing row
        of nothing takes it all instead."""
        section = QGroupBox()
        form = QFormLayout(section)
        for name in ("one", "two", "three"):
            form.addRow(f"{name}:", QLabel(name))
        frame = BlockFrame("a", "A", section, RecommendedSize(200, 120), _NoHost())
        frame.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
        frame.resize(300, 600)
        frame.show()
        for _ in range(8):
            qapp.processEvents()
        try:
            assert section.height() > section.sizeHint().height(), "the form did not fill"
            fields = [form.itemAt(row, QFormLayout.ItemRole.FieldRole) for row in range(3)]
            gaps = [
                fields[i + 1].widget().y() - fields[i].widget().geometry().bottom()
                for i in range(len(fields) - 1)
            ]
            assert max(gaps) - min(gaps) <= 1, f"the slack was shared out over the rows: {gaps}"
        finally:
            frame.hide()
            frame.deleteLater()

    def test_a_form_deep_in_the_content_does_not_speak_for_the_block(self, qapp) -> None:
        """Powers and Equipment kept the gap-between-every-line fault after every
        other block was cured of it, and this is why.

        ``QFormLayout`` never overrode ``expandingDirections``, so it answers "both";
        ``QWidgetItem`` folds a widget's own layout's answer into the widget's
        wherever that widget is allowed to grow. One form on one power card therefore
        made the card expansive, which made the list of cards expansive, which made
        the section claim it already had somewhere deliberate to put a tall block's
        surplus. It had not — the height went down into the cards and came out as a
        gap between every line of every one of them.
        """
        section = QWidget()
        layout = QVBoxLayout(section)
        cards = []
        for _ in range(2):
            card = QWidget()
            form = QFormLayout(card)
            for name in ("type", "action"):
                form.addRow(f"{name}:", QLabel(name))
            layout.addWidget(card)
            cards.append(card)
        frame = BlockFrame("a", "A", section, RecommendedSize(200, 120), _NoHost())
        frame.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
        frame.resize(300, 600)
        frame.show()
        for _ in range(8):
            qapp.processEvents()
        try:
            assert section.height() > section.sizeHint().height(), "the section did not fill"
            for card in cards:
                assert card.height() <= card.sizeHint().height() + 1, "a card took the surplus"
            under = section.height() - cards[-1].geometry().bottom()
            assert under > section.sizeHint().height() // 2, "the slack is not under the cards"
        finally:
            frame.hide()
            frame.deleteLater()

    def test_a_widget_that_says_it_expands_still_takes_the_room(self, qapp) -> None:
        """The other half of that rule: a section is only judged to have a use for
        the height when something in it *says so itself* — a table that stretches its
        own rows sets ``Expanding`` on itself — and then it gets the room rather than
        a band of nothing under it."""
        section = QWidget()
        layout = QVBoxLayout(section)
        layout.addWidget(QLabel("caption"))
        table = QLabel("rows")
        table.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
        layout.addWidget(table)
        frame = BlockFrame("a", "A", section, RecommendedSize(200, 120), _NoHost())
        frame.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
        frame.resize(300, 600)
        frame.show()
        for _ in range(8):
            qapp.processEvents()
        try:
            assert table.height() > 300, "the table was left at its hint under a stretch"
        finally:
            frame.hide()
            frame.deleteLater()

    def test_a_block_whose_content_grows_tells_its_row(self, qapp) -> None:
        """The scroll area is a barrier by design — it is what stops the section's
        minimum climbing out to the window — so nothing carried a *changed* content
        height out of it either. The row went on using the height it had cached, and
        the extra content ended up scrolled out of sight with page to spare.
        """
        frame = self._frame(qapp)
        try:
            before = frame.sizeHint().height()
            frame.section.layout().addWidget(QLabel("four"))
            for _ in range(6):
                qapp.processEvents()
            assert frame.sizeHint().height() > before
            assert frame.height() >= frame.sizeHint().height() - 1
        finally:
            frame.hide()
            frame.deleteLater()


class _Wrapping(QWidget):
    """A widget as tall as its text wraps to, and no taller — a card, in miniature.

    Its layout answers ``heightForWidth`` and its ``minimumSizeHint`` does not, which
    is not a contrivance: that is every ``QBoxLayout`` holding anything that wraps,
    and it is the whole of the bug below.
    """

    def __init__(self, lines: int = 6) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        label = QLabel(" ".join(f"word{n}" for n in range(40 * lines)))
        label.setWordWrap(True)
        layout.addWidget(label)


class TestABlockNoTallerThanItsContent:
    """The other half of the same question, and the one that shipped wrong: a block
    that is *exactly* as tall as its content must not scroll."""

    def _frame(self, qapp, width: int = 600) -> BlockFrame:
        frame = BlockFrame("a", "A", _Wrapping(), RecommendedSize(200, 120), _NoHost())
        frame.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
        frame.resize(width, 400)
        frame.show()
        for _ in range(6):
            qapp.processEvents()
        # However tall this block would like to be, at the width it has.
        frame.resize(width, frame.sizeHint().height())
        for _ in range(8):
            qapp.processEvents()
        return frame

    def test_a_block_at_its_own_hint_does_not_scroll(self, qapp) -> None:
        """Qt decides whether to scroll from ``minimumSizeHint``, which a
        ``QBoxLayout`` builds by summing its items' *unwrapped* hints — no
        height-for-width anywhere in it. A block wider than its content prefers
        wraps that content into fewer lines, so the honest height came out 30px
        under the number Qt was scrolling against, and the Powers block put a
        scrollbar on 30px of nothing."""
        frame = self._frame(qapp)
        try:
            scroll = frame.findChild(QScrollArea, "blockScroll")
            assert not scroll.verticalScrollBar().isVisible(), (
                "the block scrolled over content that fits: "
                f"viewport {scroll.viewport().height()}, content {scroll.widget().height()}"
            )
        finally:
            frame.hide()
            frame.deleteLater()

    def test_and_still_scrolls_once_it_is_genuinely_too_short(self, qapp) -> None:
        """The correction only ever *lowers* an overstated minimum. A block dragged
        under what its content needs still scrolls, which is the release valve the
        whole draggable page rests on."""
        frame = self._frame(qapp)
        try:
            frame.resize(frame.width(), 80)
            for _ in range(8):
                qapp.processEvents()
            scroll = frame.findChild(QScrollArea, "blockScroll")
            assert scroll.verticalScrollBar().isVisible()
        finally:
            frame.hide()
            frame.deleteLater()

    def test_the_hint_is_measured_at_the_width_the_block_has(self, qapp) -> None:
        """A wide block wraps its text into fewer lines and is therefore shorter.
        ``sizeHint`` answers at the content's own preferred width, which is not the
        block's, so the height it reported was a taller block's."""
        narrow = self._frame(qapp, width=320)
        wide = self._frame(qapp, width=900)
        try:
            assert wide.sizeHint().height() < narrow.sizeHint().height()
        finally:
            for frame in (narrow, wide):
                frame.hide()
                frame.deleteLater()


class TestATableGivenMoreHeightThanItsRows:
    """Where a table block's spare height goes, now that there can be some."""

    def _table(self, qapp, height: int):
        from mm_companion.core.character import Character
        from mm_companion.core.data_loader import load_game_data
        from mm_companion.ui.sections.abilities import AbilitiesSection

        section = AbilitiesSection(load_game_data(), Character())
        section.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
        section.resize(400, height)
        section.show()
        for _ in range(8):
            qapp.processEvents()
        return section

    def test_the_rows_take_it_rather_than_the_space_under_them(self, qapp) -> None:
        """A table given more room than its rows want simply drew bare viewport
        under the last one — inside its own border, so dragging Abilities taller
        made the block bigger and the table no different."""
        section = self._table(qapp, 700)
        try:
            table = section.table
            rows = [table.rowHeight(row) for row in range(table.rowCount())]
            assert sum(rows) >= table.viewport().height() - 2
            assert min(rows) > 0
        finally:
            section.hide()
            section.deleteLater()

    def test_a_rule_across_the_table_is_not_a_row_and_does_not_grow(self, qapp) -> None:
        """The line between the bought traits and the derived ones would otherwise
        become a 50px band of nothing."""
        section = self._table(qapp, 700)
        try:
            table = section.table
            rules = [row for row in range(table.rowCount()) if table.is_rule_row(row)]
            assert rules, "this table is meant to have a rule in it"
            natural = table.row_heights()
            for row in rules:
                assert table.rowHeight(row) == natural[row]
        finally:
            section.hide()
            section.deleteLater()

    def test_what_the_table_reports_is_the_natural_height_throughout(self, qapp) -> None:
        """Load-bearing rather than tidy: were the hint to follow the stretched
        rows, taller rows would mean a taller hint, which means a taller block,
        which means taller rows, and the block would walk down the page on its own.
        """
        short = self._table(qapp, 300)
        tall = self._table(qapp, 900)
        try:
            assert tall.table.sizeHint().height() == short.table.sizeHint().height()
            assert tall.table.minimumSizeHint().height() == short.table.minimumSizeHint().height()
        finally:
            for section in (short, tall):
                section.hide()
                section.deleteLater()

    def test_the_rows_go_back_when_the_room_does(self, qapp) -> None:
        section = self._table(qapp, 900)
        try:
            table = section.table
            natural = table.row_heights()
            assert table.rowHeight(0) > natural[0], "it did not stretch in the first place"
            section.resize(400, table.sizeHint().height())  # exactly its rows, no surplus
            for _ in range(8):
                qapp.processEvents()
            assert [table.rowHeight(r) for r in range(table.rowCount())] == natural
        finally:
            section.hide()
            section.deleteLater()

    def test_a_widget_in_a_cell_keeps_its_own_height(self, qapp) -> None:
        """Text in a cell is centred in its row; a rank spin box was instead given
        the cell's whole rectangle and became an 85px pill."""
        short = self._table(qapp, 300)
        tall = self._table(qapp, 900)
        try:
            assert tall.table.rowHeight(0) > short.table.rowHeight(0)
            assert tall._abilities["STR"].height() == short._abilities["STR"].height()
        finally:
            for section in (short, tall):
                section.hide()
                section.deleteLater()


class TestTheDetailsDisclosure:
    """The real section behind :class:`TestASectionThatNamesItsOwnHeight`."""

    def _section(self, qapp, width: int = 420):
        from mm_companion.core.character import Character
        from mm_companion.core.data_loader import load_game_data
        from mm_companion.ui.sections.base_info import BaseInfoSection

        section = BaseInfoSection(load_game_data(), Character())
        section.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
        section.resize(width, 400)
        section.show()
        for _ in range(8):
            qapp.processEvents()
        return section

    def test_opening_details_asks_for_exactly_the_height_it_asked_for_shut(self, qapp) -> None:
        """Not approximately: the group holds the body alone, so the body's own
        height is exactly what it sheds when it shuts. Anything left over would
        creep the row open a few pixels every time the box was ticked."""
        section = self._section(qapp)
        try:
            width = section.width()
            shut = section.preferred_height(width)
            section._details_group.setChecked(True)
            for _ in range(8):
                qapp.processEvents()
            assert section.preferred_height(width) == shut
        finally:
            section.hide()
            section.deleteLater()

    def test_and_the_content_underneath_really_did_get_taller(self, qapp) -> None:
        """The positive control: only the *preference* leaves the body out, so
        there is something for the block to scroll to."""
        section = self._section(qapp)
        try:
            before = section.sizeHint().height()
            section._details_group.setChecked(True)
            for _ in range(8):
                qapp.processEvents()
            assert section.sizeHint().height() > before
        finally:
            section.hide()
            section.deleteLater()


class TestASectionThatNamesItsOwnHeight:
    """``preferred_height``: how a disclosure inside a block stops rearranging the
    page around it."""

    class _Disclosing(QWidget):
        def __init__(self) -> None:
            super().__init__()
            layout = QVBoxLayout(self)
            layout.addWidget(QLabel("always here"))
            self.body = QLabel(chr(10).join(f"detail {n}" for n in range(10)))
            layout.addWidget(self.body)
            self.body.hide()

        def preferred_height(self, width: int) -> int:  # noqa: ARG002
            height = self.sizeHint().height()
            if not self.body.isVisible():
                return height
            # A hidden item takes its spacing with it, so the body costs its own
            # height *and* the gap above it.
            return height - self.body.sizeHint().height() - self.layout().spacing()

    def _frame(self, qapp) -> BlockFrame:
        frame = BlockFrame("a", "A", self._Disclosing(), RecommendedSize(200, 120), _NoHost())
        frame.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
        frame.resize(300, 400)
        frame.show()
        for _ in range(6):
            qapp.processEvents()
        return frame

    def test_opening_the_disclosure_does_not_change_what_the_block_asks_for(self, qapp) -> None:
        frame = self._frame(qapp)
        try:
            before = frame.sizeHint().height()
            frame.section.body.show()
            for _ in range(8):
                qapp.processEvents()
            assert frame.sizeHint().height() == before
        finally:
            frame.hide()
            frame.deleteLater()

    def test_but_the_block_still_scrolls_to_the_rest_of_it(self, qapp) -> None:
        """Only the *preference* leaves the body out. What the scroll area measures
        is the whole expanded content, which is what makes the remainder reachable
        rather than clipped."""
        frame = self._frame(qapp)
        try:
            frame.resize(300, frame.sizeHint().height())
            for _ in range(8):
                qapp.processEvents()
            frame.section.body.show()
            for _ in range(8):
                qapp.processEvents()
            scroll = frame.findChild(QScrollArea, "blockScroll")
            assert scroll.verticalScrollBar().isVisible()
            assert scroll.verticalScrollBar().maximum() > 0
        finally:
            frame.hide()
            frame.deleteLater()


class _NoHost:
    """The drag host a bare frame needs and never calls."""

    def title_bar_pressed(self, key, global_pos): ...
    def title_bar_moved(self, key, global_pos): ...
    def title_bar_released(self, key, global_pos): ...
    def request_float(self, key): ...
    def request_hide(self, key): ...
    def request_pin(self, key): ...
    def block_menu(self, key):
        return None


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

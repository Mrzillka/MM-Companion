"""The shared table-block machinery: content sizing, row menus, reorder maths."""

from __future__ import annotations

import pytest
from PySide6.QtCore import QMimeData, QPoint, QPointF, Qt
from PySide6.QtGui import QDropEvent, QFont, QFontMetrics
from PySide6.QtWidgets import QApplication, QMenu, QTableWidgetItem

from mm_companion.ui.sections.row_table import (
    SORT_MANUAL,
    AutoHeightTable,
    RowIndex,
    RowReorder,
    SortControl,
    build_row_menu,
    move_within,
    remove_contributor,
    wrapping_column_width,
)


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


def _table(rows: int = 3, *, fit_width: bool = False) -> AutoHeightTable:
    table = AutoHeightTable(rows, 2, fit_width=fit_width)
    table.setHorizontalHeaderLabels(["Name", "Value"])
    for row in range(rows):
        table.setItem(row, 0, QTableWidgetItem(f"row {row}"))
        table.setItem(row, 1, QTableWidgetItem(str(row)))
    return table


# -- AutoHeightTable -----------------------------------------------------------


def test_the_table_reports_every_row_as_its_height(qapp: QApplication) -> None:
    table = _table(3)
    expected = table.horizontalHeader().height() + 2 * table.frameWidth()
    expected += sum(table.rowHeight(row) for row in range(3))

    assert table.sizeHint().height() == expected
    # The minimum is the same number: the block grows rather than the table scrolling.
    assert table.minimumSizeHint().height() == expected


def test_the_height_follows_rows_arriving_and_leaving(qapp: QApplication) -> None:
    table = _table(2)
    two_rows = table.sizeHint().height()

    table.setRowCount(4)
    assert table.sizeHint().height() > two_rows

    table.setRowCount(1)
    assert table.sizeHint().height() < two_rows


def test_fit_width_reports_the_columns_and_is_opt_in(qapp: QApplication) -> None:
    """A table that *is* the block asks for its columns; one panel of a flow does not."""
    whole_block = _table(fit_width=True)
    one_panel = _table(fit_width=False)

    columns = sum(
        max(whole_block.sizeHintForColumn(col), whole_block.horizontalHeader().sectionSizeHint(col))
        for col in range(2)
    )
    assert whole_block.minimumSizeHint().width() >= columns
    assert one_panel.minimumSizeHint().width() < columns


# -- RowIndex ------------------------------------------------------------------


def test_the_index_maps_rows_both_ways(qapp: QApplication) -> None:
    table = _table(2)
    index = RowIndex()
    first, second = object(), object()
    index.add(table, 0, first)
    index.add(table, 1, second)

    assert index.key_at(table, 1) is second
    assert index.entry_at(table, 0).key is first
    assert index.find(second).row == 1
    assert index.position(index[1]) == 1
    assert index.last_in(table).key is second
    assert index.key_at(table, 7) is None


def test_the_index_finds_an_equal_key_by_identity_first(qapp: QApplication) -> None:
    """Two equal items are two rows, and a caller means the one it was handed."""
    table = _table(2)
    index = RowIndex()
    first, second = ["same"], ["same"]
    index.add(table, 0, first)
    index.add(table, 1, second)

    assert index.find(second).row == 1


# -- install_row_menu ----------------------------------------------------------


def test_two_contributors_are_composed_and_ruled_apart(qapp: QApplication) -> None:
    table = _table(2)

    def pin(menu: QMenu, _table, _row: int) -> None:
        menu.addAction("Pin")

    contributors = (pin, remove_contributor(lambda _t, r: f"Remove {r}", lambda _t, _r: None))

    menu = build_row_menu(table, 1, contributors)
    actions = menu.actions()
    assert [action.text() for action in actions] == ["Pin", "", "Remove 1"]
    # The empty one is the rule between two contributors that both had something.
    assert actions[1].isSeparator()


def test_one_contributor_gets_no_leading_rule(qapp: QApplication) -> None:
    table = _table(2)
    contributors = (remove_contributor(lambda _t, r: f"Remove {r}", lambda _t, _r: None),)

    assert [a.text() for a in build_row_menu(table, 0, contributors).actions()] == ["Remove 0"]


def test_a_row_nobody_speaks_for_shows_nothing(qapp: QApplication) -> None:
    table = _table(2)
    contributors = (remove_contributor(lambda _t, _r: None, lambda _t, _r: None),)

    assert build_row_menu(table, 0, contributors).actions() == []


def test_the_remove_action_calls_back_with_its_row(qapp: QApplication) -> None:
    table = _table(2)
    removed: list[int] = []
    contribute = remove_contributor(lambda _t, r: f"Remove {r}", lambda _t, r: removed.append(r))
    menu = QMenu(table)
    contribute(menu, table, 1)
    menu.actions()[0].trigger()

    assert removed == [1]


# -- reorder maths -------------------------------------------------------------


def test_moving_forward_corrects_for_the_lifted_item() -> None:
    """A drop position is measured against the list *before* the move."""
    items = ["a", "b", "c", "d"]
    move_within(items, 0, 3)  # "a" dropped before "d"

    assert items == ["b", "c", "a", "d"]


def test_moving_backward_needs_no_correction() -> None:
    items = ["a", "b", "c", "d"]
    move_within(items, 3, 1)

    assert items == ["a", "d", "b", "c"]


def test_moving_past_the_end_lands_last() -> None:
    items = ["a", "b", "c"]
    move_within(items, 0, 3)

    assert items == ["b", "c", "a"]


def test_an_out_of_range_move_is_a_no_op() -> None:
    items = ["a", "b"]
    move_within(items, 5, 0)

    assert items == ["a", "b"]


def _drop_on(table: AutoHeightTable, mime_type: str, source: int, y: int) -> None:
    """Deliver a real drop of *source*'s row at *y* in the viewport."""
    mime = QMimeData()
    mime.setData(mime_type, str(source).encode("ascii"))
    event = QDropEvent(
        QPointF(10, y),
        Qt.DropAction.MoveAction,
        mime,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    table.dropEvent(event)


def test_a_drop_reports_the_row_it_landed_on_and_which_side(qapp: QApplication) -> None:
    table = _table(3)
    index = RowIndex()
    for row, key in enumerate("abc"):
        index.add(table, row, key)
    moves: list[tuple] = []
    mime_type = "application/x-mm-rows-test"
    reorder = RowReorder(mime_type, index, lambda s, t, b: moves.append((s.key, t.key, b)))
    reorder.attach(table)

    # The top half of row 2 means "before c"; the bottom half means "after".
    top = table.rowViewportPosition(2)
    _drop_on(table, mime_type, 0, top + 1)
    _drop_on(table, mime_type, 0, top + table.rowHeight(2) - 1)

    assert moves == [("a", "c", True), ("a", "c", False)]


def test_a_drop_below_the_last_row_lands_after_it(qapp: QApplication) -> None:
    table = _table(2)
    index = RowIndex()
    index.add(table, 0, "a")
    index.add(table, 1, "b")
    moves: list[tuple] = []
    mime_type = "application/x-mm-rows-test"
    reorder = RowReorder(mime_type, index, lambda s, t, b: moves.append((s.key, t.key, b)))
    reorder.attach(table)

    _drop_on(table, mime_type, 0, table.rowViewportPosition(1) + 10_000)

    assert moves == [("a", "b", False)]


def test_another_blocks_payload_is_refused(qapp: QApplication) -> None:
    table = _table(2)
    index = RowIndex()
    index.add(table, 0, "a")
    index.add(table, 1, "b")
    moves: list[tuple] = []
    reorder = RowReorder(
        "application/x-mm-rows-mine", index, lambda s, t, b: moves.append((s, t, b))
    )
    reorder.attach(table)

    _drop_on(table, "application/x-mm-rows-theirs", 0, table.rowViewportPosition(1) + 1)

    assert moves == []


def test_a_pairing_the_block_refuses_never_reaches_the_model(qapp: QApplication) -> None:
    table = _table(2)
    index = RowIndex()
    index.add(table, 0, "a")
    index.add(table, 1, "b")
    moves: list[tuple] = []
    mime_type = "application/x-mm-rows-test"
    reorder = RowReorder(
        mime_type,
        index,
        lambda s, t, b: moves.append((s.key, t.key, b)),
        accepts=lambda source, target, before: False,
    )
    reorder.attach(table)

    _drop_on(table, mime_type, 0, table.rowViewportPosition(1) + 1)

    assert moves == []


def test_a_reorder_starts_no_drag_while_it_is_disabled(qapp: QApplication) -> None:
    """A preset sort mode (or a locked sheet) turns hand ordering off entirely."""
    table = _table(2)
    index = RowIndex()
    index.add(table, 0, "a")
    index.add(table, 1, "b")
    moves: list[tuple] = []
    reorder = RowReorder(
        "application/x-mm-rows-test",
        index,
        lambda s, t, b: moves.append((s.key, t.key, b)),
        enabled=lambda: False,
    )
    reorder.attach(table)

    # Nothing raises, and nothing is lifted: the gesture simply isn't there.
    reorder.start_drag(table, QPoint(4, 4))
    assert moves == []


# -- SortControl ---------------------------------------------------------------


def test_the_sort_control_announces_a_choice_but_not_a_load(qapp: QApplication) -> None:
    control = SortControl([(SORT_MANUAL, "Manual"), ("name", "Name")])
    heard: list[str] = []
    control.sortChanged.connect(heard.append)

    control.combo.setCurrentIndex(control.combo.findData("name"))
    assert heard == ["name"]
    assert control.mode() == "name"
    # Only Manual leaves anything for a drag to do.
    assert control.reorder_enabled() is False

    # set_mode is the owner telling the control what it already knows.
    control.set_mode(SORT_MANUAL)
    assert heard == ["name"]
    assert control.reorder_enabled() is True


def test_the_sort_control_guards_the_wheel(qapp: QApplication) -> None:
    """Scrolling the page past the combo must not silently reorder the block."""
    control = SortControl([(SORT_MANUAL, "Manual"), ("name", "Name")])

    assert control.combo.focusPolicy() == Qt.FocusPolicy.StrongFocus


# -- how wide a wrapping name column should be ---------------------------------


def _metrics(qapp: QApplication) -> QFontMetrics:
    """Real font metrics, so the arithmetic is exercised against real widths."""
    return QFontMetrics(QFont())


def test_a_wrapping_column_fits_its_content_while_that_is_modest(
    qapp: QApplication,
) -> None:
    """Short labels get exactly what they need, plus the padding asked for."""
    fm = _metrics(qapp)
    labels = ["Stealth", "Deception", "Perception"]
    widest = max(fm.horizontalAdvance(label) for label in labels)

    assert wrapping_column_width(fm, labels, padding=16, cap=10_000, floor=0) == widest + 16


def test_a_wrapping_column_stops_at_its_cap(qapp: QApplication) -> None:
    """Which is the whole point: past it the text breaks instead.

    A block's minimum width must not track how much a player typed into a focus or
    an advantage's subject, so however long the label is the answer is the cap.
    """
    fm = _metrics(qapp)
    short = ["Law"]
    long = ["Interstellar Xenobiology and Comparative Anatomy, Third Edition"]

    assert wrapping_column_width(fm, long, padding=16, cap=200, floor=0) == 200
    assert wrapping_column_width(fm, long, padding=16, cap=200, floor=0) >= (
        wrapping_column_width(fm, short, padding=16, cap=200, floor=0)
    )


def test_the_floor_beats_the_content_and_the_cap(qapp: QApplication) -> None:
    """Both, deliberately.

    It is what keeps a column of short labels from collapsing to the width of the
    longest of them — a block's density metric, a header's own caption ("Skill",
    "Advantage"), which must never be the thing that clips. And it outranks the cap
    too, so a preset that sets the two inconsistently still leaves a usable column
    rather than a sliver.
    """
    fm = _metrics(qapp)

    assert wrapping_column_width(fm, ["Law"], padding=0, cap=10_000, floor=150) == 150
    assert wrapping_column_width(fm, ["Law"], padding=0, cap=20, floor=150) == 150


def test_a_wrapping_column_with_nothing_in_it_is_its_floor(qapp: QApplication) -> None:
    """An empty block still has a header to print."""
    assert wrapping_column_width(_metrics(qapp), [], padding=16, cap=200, floor=100) == 100

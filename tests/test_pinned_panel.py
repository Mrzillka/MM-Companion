"""The pinned strip: what it holds, where it sits, and what it refuses."""

from __future__ import annotations

import json

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtWidgets import QApplication, QScrollArea, QSplitter

from mm_companion.core.data_loader import load_game_data
from mm_companion.ui.block_canvas import SCHEMA_VERSION, RowWidget
from mm_companion.ui.character_sheet import CharacterSheet
from mm_companion.ui.pinned import PinSlot


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


@pytest.fixture
def make_sheet(qapp: QApplication):
    """Laid-out character sheets with no on-screen window (see test_block_canvas)."""
    sheets: list[CharacterSheet] = []

    def _make() -> CharacterSheet:
        sheet = CharacterSheet(load_game_data())
        sheet.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
        sheet.resize(1100, 900)
        sheet.show()
        _settle()
        sheets.append(sheet)
        return sheet

    yield _make

    for sheet in sheets:
        sheet.hide()
        sheet.deleteLater()
    QApplication.processEvents()


def _settle(times: int = 5) -> None:
    for _ in range(times):
        QApplication.processEvents()


def _pinned(sheet: CharacterSheet) -> dict:
    return sheet.arrangement()["pinned"]


# -- what the strip holds ---------------------------------------------------


def test_a_fresh_sheet_has_an_empty_strip_showing_its_icon(make_sheet) -> None:
    sheet = make_sheet()
    panel = sheet.board.panel

    assert panel.is_empty()
    assert _pinned(sheet)["lines"] == []
    assert panel._handle.isVisible()  # the pin icon: drop target, drag handle, menu


def test_pinning_moves_a_block_off_the_page_and_out_of_its_row(make_sheet) -> None:
    sheet = make_sheet()

    sheet.pin_block("conditions")
    _settle()

    assert sheet.is_block_pinned("conditions")
    assert _pinned(sheet)["lines"] == [["conditions"]]
    assert all("conditions" not in row for row in sheet.arrangement()["rows"])
    assert not sheet.board.panel.is_empty()
    assert sheet.board.panel._handle.isVisible()  # the handle never goes away


def test_a_pinned_block_does_not_live_inside_the_scrolling_page(make_sheet) -> None:
    # The whole point: the page scrolls, the strip does not, so a pinned block must
    # not be a descendant of the page's scroll area.
    sheet = make_sheet()
    sheet.pin_block("conditions")
    _settle()

    frame = sheet.block_frame("conditions")
    page = sheet.page_scroll_area()
    ancestors = []
    parent = frame.parentWidget()
    while parent is not None:
        ancestors.append(parent)
        parent = parent.parentWidget()

    assert page not in ancestors
    assert sheet.board.panel in ancestors


def test_unpinning_docks_the_block_back_onto_the_page(make_sheet) -> None:
    sheet = make_sheet()
    sheet.pin_block("conditions")
    _settle()

    sheet.unpin_block("conditions")
    _settle()

    assert not sheet.is_block_pinned("conditions")
    assert any("conditions" in row for row in sheet.arrangement()["rows"])
    assert sheet.board.panel.is_empty()


def test_the_title_bar_pin_button_toggles_the_block_both_ways(make_sheet) -> None:
    sheet = make_sheet()
    canvas = sheet.canvas

    canvas.request_pin("conditions")
    _settle()
    assert canvas.is_pinned("conditions")

    canvas.request_pin("conditions")
    _settle()
    assert not canvas.is_pinned("conditions")


def test_hiding_a_pinned_block_takes_it_out_of_the_strip(make_sheet) -> None:
    sheet = make_sheet()
    sheet.pin_block("conditions")
    _settle()

    sheet.hide_block("conditions")
    _settle()

    assert _pinned(sheet)["lines"] == []
    assert sheet.is_block_hidden("conditions")


def test_floating_a_pinned_block_takes_it_out_of_the_strip(make_sheet) -> None:
    sheet = make_sheet()
    sheet.pin_block("conditions")
    _settle()

    sheet.float_block("conditions")
    _settle()

    assert _pinned(sheet)["lines"] == []
    assert "conditions" in sheet.arrangement()["floating"]


def test_unpin_all_empties_the_strip(make_sheet) -> None:
    sheet = make_sheet()
    sheet.pin_block("conditions")
    sheet.pin_block("abilities")
    _settle()

    sheet.canvas.unpin_all()
    _settle()

    assert _pinned(sheet)["lines"] == []
    placed = [key for row in sheet.arrangement()["rows"] for key in row]
    assert {"conditions", "abilities"} <= set(placed)


def test_reset_layout_empties_the_strip(make_sheet) -> None:
    sheet = make_sheet()
    sheet.pin_block("conditions")
    sheet.canvas.set_pin_edge("bottom")
    _settle()

    sheet.reset_layout()
    _settle()

    assert _pinned(sheet) == {
        "edge": "right",
        "lines": [],
        "align": "fill",
        "sizes": [],
        "line_sizes": [],
        "extent": 320,
    }


def test_two_blocks_can_share_a_line_across_the_strip(make_sheet) -> None:
    # The strip is a small canvas, not a single stack: blocks go beside each other
    # as readily as under each other.
    sheet = make_sheet()
    sheet.pin_block("conditions")
    _settle()

    sheet.pin_block("abilities", line=0, slot=1, new_line=False)
    _settle()

    assert sheet.canvas.pinned_lines() == [["conditions", "abilities"]]
    line = sheet.board.panel._lines[0]
    assert line.orientation() == Qt.Orientation.Horizontal  # a right-edge strip
    first, second = line.slots
    assert first.x() < second.x()  # side by side, not stacked
    assert first.y() == second.y()


def test_a_drop_in_the_middle_of_a_line_joins_it_instead_of_starting_one(make_sheet) -> None:
    sheet = make_sheet()
    sheet.pin_block("conditions")
    _settle()
    panel = sheet.board.panel
    line = panel._lines[0]

    middle = line.mapToGlobal(line.rect().center())
    slot = panel.drop_slot(middle)

    assert slot is not None and slot.new_line is False
    assert slot.line == 0


def test_emptying_the_strip_collapses_it_at_once(make_sheet) -> None:
    # Regression: the panel pinned its own thin width but the board's splitter kept
    # the sizes it already had, so the strip stayed wide until its handle was
    # nudged.
    sheet = make_sheet()
    sheet.pin_block("conditions")
    _settle()
    assert sheet.board.panel.width() > sheet.board.panel.empty_extent()

    sheet.unpin_block("conditions")
    _settle()

    assert sheet.board.panel.width() == sheet.board.panel.empty_extent()
    assert sheet.board.extent() == 0


def test_reset_layout_puts_a_pinned_block_back_in_one_go(make_sheet) -> None:
    # Regression: the strip was re-rendered *after* the rows were rebuilt, so it
    # took the block straight back off the row it had just been placed in — and
    # only a second Reset made it reappear.
    sheet = make_sheet()
    sheet.pin_block("conditions")
    _settle()

    sheet.reset_layout()
    _settle()

    frame = sheet.block_frame("conditions")
    assert isinstance(frame.parentWidget(), RowWidget)
    assert frame.isVisible()
    assert any("conditions" in row for row in sheet.arrangement()["rows"])


def test_the_strips_thickness_survives_an_unrelated_rearrangement(make_sheet) -> None:
    # The board splitter is dragged behind the model's back, so the live sizes are
    # the truth; a page-side change must not snap the strip back to a stale width.
    sheet = make_sheet()
    sheet.pin_block("conditions")
    _settle()
    board = sheet.board
    board._splitter.setSizes([600, 380])
    _settle()
    dragged = board.extent()

    sheet.dock_block("skills", 0, 0, new_row=True)
    _settle()

    assert board.extent() == dragged


# -- where it sits ----------------------------------------------------------


def test_moving_the_strip_to_another_edge_re_lays_the_board_out(make_sheet) -> None:
    sheet = make_sheet()
    sheet.pin_block("conditions")
    _settle()
    board = sheet.board
    splitter = board.findChild(QSplitter, "pinnedBoardSplitter")

    # A right-hand strip: page first, side by side.
    assert splitter.orientation() == Qt.Orientation.Horizontal
    assert splitter.indexOf(board.panel) == 1

    sheet.canvas.set_pin_edge("top")
    _settle()

    assert splitter.orientation() == Qt.Orientation.Vertical
    assert splitter.indexOf(board.panel) == 0
    assert _pinned(sheet)["edge"] == "top"


def test_the_strip_stacks_its_blocks_along_its_own_axis(make_sheet) -> None:
    sheet = make_sheet()
    sheet.pin_block("conditions")
    sheet.pin_block("abilities")
    _settle()
    splitter = sheet.board.panel._splitter

    assert splitter.orientation() == Qt.Orientation.Vertical  # a right-edge strip

    sheet.canvas.set_pin_edge("bottom")
    _settle()

    assert splitter.orientation() == Qt.Orientation.Horizontal


def test_alignment_is_remembered_and_only_takes_known_values(make_sheet) -> None:
    sheet = make_sheet()
    sheet.pin_block("conditions")

    sheet.canvas.set_pin_align("center")
    _settle()
    assert _pinned(sheet)["align"] == "center"

    sheet.canvas.set_pin_align("sideways")  # not an alignment
    assert _pinned(sheet)["align"] == "center"


# -- the drop target --------------------------------------------------------


def test_an_empty_strip_takes_a_drop_anywhere_on_it(make_sheet) -> None:
    sheet = make_sheet()
    panel = sheet.board.panel

    center = panel.mapToGlobal(panel.rect().center())

    assert panel.drop_slot(center) == PinSlot(new_line=True, line=0, slot=0)


def test_a_drop_outside_the_strip_is_not_the_strips_business(make_sheet) -> None:
    sheet = make_sheet()

    assert sheet.board.drop_slot(QPoint(-5000, -5000)) is None
    page = sheet.page_scroll_area()
    over_the_page = page.mapToGlobal(page.rect().center())
    assert sheet.board.drop_slot(over_the_page) is None


def test_a_drop_lands_before_or_after_the_block_it_is_dropped_on(make_sheet) -> None:
    sheet = make_sheet()
    sheet.pin_block("conditions")
    sheet.pin_block("abilities")
    _settle()
    panel = sheet.board.panel
    first, second = (line.slots[0] for line in panel._lines)

    above_first = first.mapToGlobal(QPoint(first.width() // 2, 1))
    below_second = second.mapToGlobal(QPoint(second.width() // 2, second.height() - 1))

    assert panel.drop_slot(above_first) == PinSlot(new_line=True, line=0, slot=0)
    assert panel.drop_slot(below_second) == PinSlot(new_line=True, line=2, slot=0)


def test_the_strip_shows_and_clears_its_drop_feedback(make_sheet) -> None:
    sheet = make_sheet()
    sheet.pin_block("conditions")
    _settle()
    panel = sheet.board.panel

    panel.show_drop(PinSlot(new_line=True, line=1, slot=0))
    assert panel._drops.state == "accept"
    assert panel._indicator.isVisible()

    panel.hide_drop()
    assert panel._drops.state == "idle"
    assert not panel._indicator.isVisible()


def test_dragging_a_block_onto_the_strip_pins_it(make_sheet) -> None:
    # The whole gesture, through the canvas's drag controller: press the title
    # bar, drag far enough to tear the block out, release over the strip.
    sheet = make_sheet()
    canvas = sheet.canvas
    frame = sheet.block_frame("conditions")
    start = frame.title_bar.mapToGlobal(QPoint(10, 5))
    panel = sheet.board.panel
    over_the_strip = panel.mapToGlobal(panel.rect().center())

    canvas.title_bar_pressed("conditions", start)
    canvas.title_bar_moved("conditions", start + QPoint(60, 40))
    canvas.title_bar_moved("conditions", over_the_strip)
    canvas.title_bar_released("conditions", over_the_strip)
    _settle()

    assert canvas.is_pinned("conditions")
    assert "conditions" not in sheet.arrangement()["floating"]


def test_dragging_a_pinned_block_back_onto_the_page_docks_it(make_sheet) -> None:
    sheet = make_sheet()
    sheet.pin_block("conditions")
    _settle()
    canvas = sheet.canvas
    frame = sheet.block_frame("conditions")
    start = frame.title_bar.mapToGlobal(QPoint(10, 5))
    row = canvas._row_widgets[0]
    over_the_page = row.mapToGlobal(row.rect().center())

    canvas.title_bar_pressed("conditions", start)
    canvas.title_bar_moved("conditions", start + QPoint(-80, 60))
    canvas.title_bar_moved("conditions", over_the_page)
    canvas.title_bar_released("conditions", over_the_page)
    _settle()

    assert not canvas.is_pinned("conditions")
    assert any("conditions" in row_keys for row_keys in sheet.arrangement()["rows"])


def test_dragging_the_grip_into_an_edge_band_moves_the_strip(make_sheet) -> None:
    sheet = make_sheet()
    sheet.pin_block("conditions")
    _settle()
    board = sheet.board
    panel = board.panel

    panel.edge_drag_started.emit()
    left_band = board.mapToGlobal(QPoint(4, board.height() // 2))
    panel.edge_drag_moved.emit(left_band)
    assert board._overlay is not None
    assert board._overlay.hovered_edge() == "left"

    panel.edge_drag_finished.emit(left_band)
    _settle()

    assert _pinned(sheet)["edge"] == "left"
    assert board._overlay is None


def test_letting_the_grip_go_off_the_bands_leaves_the_strip_where_it_was(make_sheet) -> None:
    sheet = make_sheet()
    sheet.pin_block("conditions")
    _settle()
    board = sheet.board

    board.panel.edge_drag_started.emit()
    board.panel.edge_drag_finished.emit(board.mapToGlobal(board.rect().center()))
    _settle()

    assert _pinned(sheet)["edge"] == "right"


# -- all of a pinned block's content fits -----------------------------------


def test_a_pinned_block_holds_the_window_open_rather_than_being_clipped(make_sheet) -> None:
    sheet = make_sheet()
    panel = sheet.board.panel
    empty_hint = panel.minimumSizeHint()

    sheet.pin_block("abilities")
    _settle()

    frame = sheet.block_frame("abilities")
    hint = panel.minimumSizeHint()
    # Abilities is a fixed-width block, so the width it needs is its own bound.
    assert hint.width() >= min(frame.minimumSizeHint().width(), frame.maximumWidth())
    assert hint.height() >= frame.minimumSizeHint().height()
    assert hint.height() > empty_hint.height()


def test_the_strip_never_squashes_a_block_below_its_content(make_sheet) -> None:
    sheet = make_sheet()
    sheet.pin_block("conditions")
    sheet.pin_block("abilities")
    _settle()
    panel = sheet.board.panel

    # Ask the splitter for the impossible: everything to the first block.
    panel._splitter.setSizes([10_000, 1])
    _settle()

    for line in panel._lines:
        for slot in line.slots:
            assert slot.height() >= slot.frame.minimumSizeHint().height()


# -- persistence ------------------------------------------------------------


def test_the_strip_survives_a_save_and_restore(make_sheet) -> None:
    sheet = make_sheet()
    sheet.pin_block("conditions")
    sheet.pin_block("abilities")
    sheet.canvas.set_pin_edge("left")
    sheet.canvas.set_pin_align("start")
    _settle()
    blob = sheet.save_layout()

    sheet.reset_layout()
    _settle()
    assert _pinned(sheet)["lines"] == []

    assert sheet.restore_layout(blob) is True
    _settle()
    restored = _pinned(sheet)
    assert restored["lines"] == [["conditions"], ["abilities"]]
    assert restored["edge"] == "left"
    assert restored["align"] == "start"
    assert sheet.board.panel._splitter.count() == 2


def test_restoring_the_same_blocks_still_restores_their_proportions(make_sheet) -> None:
    # Regression: the strip skips a rebuild when the blocks look unchanged, and the
    # sizes are only read on a rebuild — so restoring a layout over an identical
    # set of pinned blocks silently kept the proportions that were on screen.
    sheet = make_sheet()
    sheet.pin_block("conditions")
    sheet.pin_block("abilities")
    _settle()
    panel = sheet.board.panel
    panel._splitter.setSizes([250, 500])
    _settle()
    blob = sheet.save_layout()
    saved = json.loads(blob)["pinned"]["sizes"]

    panel._splitter.setSizes([600, 150])
    _settle()
    assert panel.line_extents() != saved

    assert sheet.restore_layout(blob) is True
    _settle()
    assert panel.line_extents() == saved


def test_a_pinned_block_moves_between_lines_without_losing_its_place(make_sheet) -> None:
    sheet = make_sheet()
    sheet.pin_block("conditions")
    sheet.pin_block("abilities")
    sheet.pin_block("resistances")
    _settle()
    assert sheet.canvas.pinned_lines() == [["conditions"], ["abilities"], ["resistances"]]

    # Into the third line, named against the strip as it looks now — taking the
    # block out of its own line collapses that one, so the target shifts up.
    sheet.pin_block("conditions", line=2, slot=0, new_line=False)
    _settle()

    assert sheet.canvas.pinned_lines() == [["abilities"], ["conditions", "resistances"]]


def test_a_layout_that_puts_one_block_in_two_places_is_rejected(make_sheet) -> None:
    sheet = make_sheet()
    model = sheet.arrangement()
    # "conditions" is still in its row, and now also pinned.
    model["pinned"] = dict(model["pinned"], lines=[["conditions"]])

    assert sheet.restore_layout(json.dumps(model)) is False
    assert _pinned(sheet)["lines"] == []


def test_a_layout_with_an_unknown_edge_or_block_is_rejected(make_sheet) -> None:
    sheet = make_sheet()
    base = sheet.arrangement()

    sideways = dict(base, pinned=dict(base["pinned"], edge="diagonal"))
    assert sheet.restore_layout(json.dumps(sideways)) is False

    stranger = dict(base, pinned=dict(base["pinned"], lines=[["not_a_block"]]))
    assert sheet.restore_layout(json.dumps(stranger)) is False


def test_a_layout_written_before_the_strip_existed_still_restores(make_sheet) -> None:
    # `pinned` is read tolerantly for the same reason `hidden_anchors` is: its
    # absence costs nothing but an empty strip.
    sheet = make_sheet()
    model = sheet.arrangement()
    model.pop("pinned")

    assert sheet.restore_layout(json.dumps(model)) is True
    assert _pinned(sheet)["lines"] == []


def test_bad_proportions_degrade_instead_of_rejecting_the_layout(make_sheet) -> None:
    sheet = make_sheet()
    model = sheet.arrangement()
    model["pinned"] = dict(model["pinned"], sizes=["wide"], extent=-4)

    assert sheet.restore_layout(json.dumps(model)) is True
    assert _pinned(sheet)["sizes"] == []
    assert _pinned(sheet)["extent"] == 320


def test_the_schema_version_moved_with_the_strip(make_sheet) -> None:
    # A layout saved before the strip is rejected wholesale rather than migrated.
    sheet = make_sheet()

    assert sheet.arrangement()["version"] == SCHEMA_VERSION
    assert sheet.restore_layout(json.dumps({"version": SCHEMA_VERSION - 1})) is False


# -- the page keeps working -------------------------------------------------


def test_the_page_scroll_area_is_still_the_sheets_own(make_sheet) -> None:
    # The wheel guard and the canvas both reach for it; wrapping the page in the
    # board must not have moved it.
    sheet = make_sheet()

    page = sheet.page_scroll_area()
    assert isinstance(page, QScrollArea)
    assert sheet.board.page_scroll_area() is page
    assert page.widget() is sheet.canvas

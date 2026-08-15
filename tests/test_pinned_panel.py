"""The pinned strip: what it holds, where it sits, and what it refuses."""

from __future__ import annotations

import json

import pytest
from PySide6.QtCore import QPoint, QSize, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QScrollArea, QSplitter

from mm_companion.core.data_loader import load_game_data
from mm_companion.ui import pinned_panel
from mm_companion.ui.block_canvas import SCHEMA_VERSION, RowWidget
from mm_companion.ui.character_sheet import CharacterSheet
from mm_companion.ui.pinned import PinSlot


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


@pytest.fixture
def make_sheet(qapp: QApplication):
    """Laid-out character sheets with an **empty** strip (see test_block_canvas).

    A fresh sheet starts with the Dice block pinned, but almost every test below is
    about what the strip does as blocks arrive in and leave it — which is far easier
    to state from empty. So the fixture empties it, and the couple of tests that are
    about the *default* strip build their sheet themselves (see ``_raw_sheet``).
    """
    sheets: list[CharacterSheet] = []

    def _make(*, empty_strip: bool = True) -> CharacterSheet:
        sheet = CharacterSheet(load_game_data())
        sheet.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
        sheet.resize(1100, 900)
        sheet.show()
        _settle()
        if empty_strip:
            sheet.canvas.unpin_all()
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


def _wait(ms: int = 150) -> None:
    """Let real time pass, for the timers the strip uses to converge its thickness."""
    QTest.qWait(ms)


def _drag(canvas, sheet: CharacterSheet, key: str, target: QPoint) -> None:
    """Run the real drag gesture: tear *key* out and drop it at *target*."""
    start = sheet.block_frame(key).title_bar.mapToGlobal(QPoint(10, 5))
    canvas.title_bar_pressed(key, start)
    canvas.title_bar_moved(key, start + QPoint(-30, -30))
    _settle()
    canvas.title_bar_moved(key, target)
    _settle()
    canvas.title_bar_released(key, target)
    _settle()


def _pinned(sheet: CharacterSheet) -> dict:
    return sheet.arrangement()["pinned"]


# -- what the strip holds ---------------------------------------------------


def test_a_fresh_sheet_starts_with_the_dice_block_pinned(make_sheet) -> None:
    # The strip is not empty out of the box: the roller's descriptor declares
    # default_pinned, so the die is in view beside the page from the first launch.
    sheet = make_sheet(empty_strip=False)

    assert _pinned(sheet)["lines"] == [["dice"]]
    assert sheet.is_block_pinned("dice")
    assert not sheet.board.panel.is_empty()
    assert all("dice" not in row for row in sheet.arrangement()["rows"])


def test_an_emptied_strip_still_shows_its_icon(make_sheet) -> None:
    sheet = make_sheet()  # the fixture unpins everything
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


def test_reset_layout_restores_the_default_strip(make_sheet) -> None:
    sheet = make_sheet()
    sheet.pin_block("conditions")
    sheet.canvas.set_pin_edge("bottom")
    _settle()

    sheet.reset_layout()
    _settle()

    # Back to the default strip — the Dice block, on the right edge, filling it —
    # and the block this test pinned returned to the page. The live pixel sizes are
    # whatever the one rendered line measures, so they are not part of the default.
    pinned = _pinned(sheet)
    assert (pinned["edge"], pinned["lines"], pinned["align"], pinned["extent"]) == (
        "right",
        [["dice"]],
        "fill",
        320,
    )
    assert any("conditions" in row for row in sheet.arrangement()["rows"])


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


def test_moving_a_pinned_block_into_its_own_line_gives_it_a_fair_share(make_sheet) -> None:
    # Regression: the remembered proportions are live pixel sizes, only true of the
    # shape they were measured in. A line alone in the strip is recorded as the
    # strip's whole length, so slotting the newcomer's natural size in beside that
    # number handed it a sliver — a block filling a fraction of its band until the
    # strip was resized and Qt redistributed.
    sheet = make_sheet()
    sheet.pin_block("conditions")
    sheet.pin_block("advantages", line=0, slot=1, new_line=False)
    _settle()

    sheet.pin_block("advantages", line=0, slot=0, new_line=True)  # move it up
    _settle()

    assert sheet.canvas.pinned_lines() == [["advantages"], ["conditions"]]
    moved = sheet.block_frame("advantages")
    assert moved.height() >= moved.sizeHint().height()
    panel = sheet.board.panel
    for line, size in zip(panel._lines, panel._splitter.sizes(), strict=True):
        assert size >= line.minimumSizeHint().height()


def test_the_strip_shrinks_back_when_the_block_needing_the_room_moves_away(make_sheet) -> None:
    # Regression: pinning a wide block pushes the strip open, and the board recorded
    # that width as if it had been dragged there. Move the block to a line of its
    # own and the strip stayed wide — until it was resized by hand.
    sheet = make_sheet()
    sheet.pin_block("conditions")
    _settle()
    narrow = sheet.board.panel.width()

    sheet.pin_block("abilities", line=0, slot=1, new_line=False)
    _settle()
    assert sheet.board.panel.width() > narrow  # the pair really did force it open

    sheet.pin_block("abilities", line=0, slot=0, new_line=True)  # move it up
    _settle()

    assert sheet.board.panel.width() == narrow
    frame = sheet.block_frame("abilities")
    slot = sheet.board.panel._lines[0].slots[0]
    # And the block occupies that width rather than sitting in a stale, wider one.
    # Abilities states no bounds of its own any more (its table reports its real
    # content), so it fills its slot exactly instead of leaving dead space beside it.
    assert frame.width() == slot.width()
    assert frame.minimumSizeHint().width() <= narrow


def test_a_thickness_the_user_dragged_to_is_kept(make_sheet) -> None:
    # The other half of the same rule: a *chosen* thickness must survive the
    # rearrangements that a forced one must not.
    sheet = make_sheet()
    sheet.resize(1700, 900)  # slack for the page to give up, or the drag can't land
    sheet.pin_block("conditions")
    _settle()
    board = sheet.board
    page, strip = board._splitter.sizes()
    board._splitter.setSizes([page - 200, strip + 200])
    board._splitter.splitterMoved.emit(page - 200, 1)  # what dragging the handle emits
    _settle()
    dragged = board.desired_extent()
    assert dragged > strip  # the page gave up what it could spare

    sheet.pin_block("complications")
    _settle()

    assert board.desired_extent() == dragged
    assert sheet.arrangement()["pinned"]["extent"] == dragged


def test_a_scrolling_strip_makes_room_for_its_own_scrollbar(
    make_sheet, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Regression: a strip long enough to scroll lost a scrollbar's width out of its
    # viewport, leaving the blocks a dozen pixels short — so Qt put up a *second*,
    # crossways bar to cover the difference. A scrollbar caused by a scrollbar.
    monkeypatch.setattr(pinned_panel, "_usable_screen", lambda _widget: QSize(1920, 320))
    sheet = make_sheet()
    sheet.resize(1400, 320)  # room to widen, not enough to fit the blocks' length
    _settle()
    sheet.pin_block("conditions")
    sheet.pin_block("advantages")
    _settle()
    _wait()
    panel = sheet.board.panel

    assert panel._scroll.verticalScrollBar().isVisible()  # it really is scrolling
    assert not panel._scroll.horizontalScrollBar().isVisible()
    assert panel.width() > panel._splitter.width()  # the bar's width was asked for


def test_the_strip_scrolls_sideways_only_when_it_cannot_be_widened(
    make_sheet, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The other half: where the room genuinely isn't there, a crossways bar is the
    # right answer — better than clipping a block.
    monkeypatch.setattr(pinned_panel, "_usable_screen", lambda _widget: QSize(200, 1080))
    sheet = make_sheet()
    sheet.pin_block("conditions")  # needs ~340, and cannot have it
    _settle()
    _wait()
    panel = sheet.board.panel

    assert panel.width() < panel._splitter.minimumSizeHint().width()
    assert panel._scroll.horizontalScrollBar().isVisible()


def test_a_rebuilt_strip_starts_at_the_top(make_sheet, monkeypatch: pytest.MonkeyPatch) -> None:
    # The strip only scrolls when its blocks don't fit, and a stale offset would
    # show the new arrangement decapitated. Getting it to scroll at all means
    # shrinking the screen it caps its demand at — otherwise it just holds the
    # window open.
    monkeypatch.setattr(pinned_panel, "_usable_screen", lambda _widget: QSize(1920, 320))
    sheet = make_sheet()
    sheet.resize(1100, 320)  # shorter than the two blocks below need
    _settle()
    sheet.pin_block("conditions")
    sheet.pin_block("advantages")
    _settle()
    bar = sheet.board.panel._scroll.verticalScrollBar()
    assert bar.maximum() > 0  # it really is scrollable, so the assertion below bites
    bar.setValue(bar.maximum())

    sheet.pin_block("complications")
    _settle()

    assert bar.value() == 0


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
    # A page-side change must not snap the strip back to the width it had before
    # the user dragged its handle wider.
    sheet = make_sheet()
    sheet.pin_block("conditions")
    _settle()
    board = sheet.board
    # Standing in for a handle drag, which both resizes the splitter *and* emits
    # splitterMoved — the signal being the whole way the board tells a chosen
    # thickness from one the pinned blocks merely forced (see
    # PinnedBoard._remember_dragged_extent). setSizes alone is the latter.
    board._splitter.setSizes([600, 380])
    board._splitter.splitterMoved.emit(600, 1)
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


def test_moving_the_strip_to_another_edge_drops_the_old_axis_thickness(make_sheet) -> None:
    # Regression: the strip's thickness is clamped against the panel's minimum *at
    # the moment it is applied*, and right after an edge change that minimum is
    # still the old axis's — so a 645px-wide side strip became a 645px-deep floor
    # and nothing brought it back down.
    sheet = make_sheet()
    sheet.pin_block("conditions")
    sheet.pin_block("advantages", line=0, slot=1, new_line=False)
    _settle()
    panel = sheet.board.panel
    side_thickness = panel.width()

    sheet.canvas.set_pin_edge("bottom")
    _wait()  # the thickness converges over a turn or two; see PinnedBoard._ask_again

    assert panel.height() < side_thickness
    assert panel.height() == panel.minimumSizeHint().height()


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


def test_a_block_dragged_out_of_the_strip_and_left_floating_keeps_its_content(
    make_sheet,
) -> None:
    # Regression: tearing a block out re-renders the strip, and the strip's teardown
    # took every block back — including the one already moved into the floating
    # window, which was left an empty frame.
    sheet = make_sheet()
    sheet.pin_block("conditions")
    sheet.pin_block("abilities")
    _settle()
    canvas = sheet.canvas
    frame = sheet.block_frame("conditions")
    start = frame.title_bar.mapToGlobal(QPoint(10, 5))

    canvas.title_bar_pressed("conditions", start)
    canvas.title_bar_moved("conditions", start + QPoint(-200, 150))
    _settle()
    assert frame.isVisible()  # it must not vanish mid-drag either
    canvas.title_bar_released("conditions", QPoint(-5000, -5000))  # dropped nowhere
    _settle()

    window = canvas._windows["conditions"]
    assert frame.window() is window
    assert frame.isVisible() and frame.section.isVisible()
    assert canvas.pinned_lines() == [["abilities"]]
    assert sheet.block_frame("abilities").isVisible()


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


def test_a_block_dragged_into_the_strip_keeps_its_content_laid_out(make_sheet) -> None:
    # Regression: on the way into the strip a block is briefly given zero height by
    # a container that hasn't been sized yet; its inner layout cached that (a
    # *negative* geometry) and no resize event ever made Qt run the layout again.
    # The block drew as an empty framed box until the strip was resized by hand.
    #
    # Waited for **twice**, and one longer wait is not the same thing: the strip
    # converges its thickness over several turns, and a block that is free to
    # stretch (which every block now is — see test_block_sizes) is still riding
    # that convergence when the first one ends. The question here is where the
    # block *lands*, not what it looks like one frame in.
    sheet = make_sheet()
    canvas = sheet.canvas
    panel = sheet.board.panel

    _drag(canvas, sheet, "conditions", panel.mapToGlobal(panel.rect().center()))
    line = panel._lines[0]
    beside = line.mapToGlobal(QPoint(line.width() - 5, line.height() // 2))
    _drag(canvas, sheet, "abilities", beside)
    _wait()
    _wait()

    frame = sheet.block_frame("abilities")
    assert frame.layout().geometry().height() > 0
    assert frame.title_bar.height() > 0
    assert frame.section.height() > 0
    # Laid out *by its slot*, not left at the width it had on the page.
    assert frame.width() == frame.parentWidget().width()


def test_dragging_one_pinned_block_beside_another_joins_its_line(make_sheet) -> None:
    sheet = make_sheet()
    sheet.pin_block("conditions")
    sheet.pin_block("abilities")
    _settle()
    canvas = sheet.canvas
    frame = sheet.block_frame("abilities")
    start = frame.title_bar.mapToGlobal(QPoint(10, 5))
    first_line = sheet.board.panel._lines[0]
    beside = first_line.mapToGlobal(QPoint(first_line.width() - 6, first_line.height() // 2))

    canvas.title_bar_pressed("abilities", start)
    canvas.title_bar_moved("abilities", start + QPoint(-40, -40))
    _settle()
    canvas.title_bar_moved("abilities", beside)
    _settle()
    canvas.title_bar_released("abilities", beside)
    _settle()

    assert canvas.pinned_lines() == [["conditions", "abilities"]]
    assert canvas._windows == {}  # it landed; nothing left floating
    assert frame.isVisible()


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
    assert _pinned(sheet)["lines"] == [["dice"]]  # back to the default strip

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


# -- what a render costs ----------------------------------------------------


def test_a_page_side_change_does_not_re_settle_the_strip(make_sheet) -> None:
    # Regression: the canvas re-renders the strip on every structural change, and
    # the board re-asserted the thickness each time — even when the strip itself had
    # not changed. On a stock sheet that request can never be met (the default extent
    # is 320 against the Dice block's 360 floor), so every dock, drop, hide and show
    # burned the full retry budget: five setSizes over ~80ms, fighting a minimum that
    # was already satisfied. That was the jitter when rearranging blocks.
    sheet = make_sheet(empty_strip=False)  # the stock strip, whose floor beats the extent
    _settle()
    _wait()
    board = sheet.board
    board._settle_tries = 0  # from here, any retry belongs to the rearrangement below
    sizes = board._splitter.sizes()

    sheet.dock_block("skills", 0, 0, new_row=True)
    _settle()

    assert board._settle_tries == 0
    assert not board._settle.isActive()
    assert board._splitter.sizes() == sizes  # the strip was not touched at all


def test_a_change_to_the_strip_itself_still_settles(make_sheet) -> None:
    # The other half: the retries exist for a *stale* minimum, which is exactly what
    # a rebuild leaves behind — so a real change to the strip must still converge.
    sheet = make_sheet(empty_strip=False)
    _settle()
    _wait()
    board = sheet.board
    board._settle_tries = 0

    sheet.pin_block("conditions")
    _settle()

    assert board._settle_tries > 0 or board._settle.isActive()


def test_the_strip_reports_whether_it_rebuilt(make_sheet) -> None:
    """The contract the board reads to decide whether to settle."""
    sheet = make_sheet()
    sheet.pin_block("conditions")
    _settle()
    panel = sheet.board.panel
    args = (
        [[sheet.block_frame(key) for key in line] for line in sheet.canvas._pinned],
        sheet.canvas._pin_edge,
        sheet.canvas._pin_align,
        sheet.canvas._pin_sizes,
        sheet.canvas._pin_line_sizes,
    )
    assert panel.set_blocks(*args) is False  # nothing moved -> skipped
    panel.invalidate()
    assert panel.set_blocks(*args) is True  # a restore always rebuilds


def test_a_restore_rebuilds_the_strip_even_with_the_same_blocks_in_it(make_sheet) -> None:
    # keys/edge/align are all set_blocks compares, so a layout restoring the same
    # blocks at different proportions is invisible to it. invalidate() is the only
    # thing that makes it look again — which is why apply_arrangement calls it, and
    # this test is the fence on that call.
    sheet = make_sheet()
    sheet.resize(1900, 900)  # slack, or the two minimums fill the line exactly
    sheet.pin_block("conditions")
    sheet.pin_block("advantages", line=0, slot=1, new_line=False)
    _settle()
    board = sheet.board
    page, strip = board._splitter.sizes()
    board._splitter.setSizes([page - 300, strip + 300])
    board._splitter.splitterMoved.emit(page - 300, 1)  # a handle drag: widen the strip
    _settle()
    _wait()

    model = sheet.arrangement()
    within = model["pinned"]["line_sizes"][0]
    assert len(within) == 2
    total = sum(within)
    floors = [sheet.block_frame(k).minimumSizeHint().width() for k in ("conditions", "advantages")]
    assert total > sum(floors) + 40, "no slack to redistribute; widen the strip further"
    # Give the second block everything the first does not need.
    model["pinned"]["line_sizes"][0] = [floors[0], total - floors[0]]
    before = board.block_sizes()[0]

    sheet.canvas.apply_arrangement(model)
    _settle()
    _wait()

    after = board.block_sizes()[0]
    assert after != before  # the restore was not swallowed by the early return

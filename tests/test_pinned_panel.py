"""The pinned strip: what it holds, where it sits, and what it refuses."""

from __future__ import annotations

import json

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QScrollArea, QSplitter

from mm_companion.core.data_loader import load_game_data
from mm_companion.ui import layout_tree as lt
from mm_companion.ui.block_canvas import SCHEMA_VERSION
from mm_companion.ui.block_sizes import load_block_sizes
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
    """The strip, in the shape these tests have always read it in.

    The strip *is* a tree now, rendered by the same splitters the page uses, so
    its ``lines`` are derived rather than stored
    (:func:`layout_tree.region_lines`). Reading them back out here keeps every
    test below saying what it always said about the strip, and confines the model
    change to one function.
    """
    region = sheet.arrangement()["region"]
    root = lt.from_dict(region["root"], set(sheet.block_keys()))
    return {
        "edge": region["edge"],
        "extent": region["extent"],
        "lines": lt.region_lines(root, region["edge"]),
    }


def _rows(sheet: CharacterSheet) -> list[list[str]]:
    """The page's top-level rows, each flattened to a list of block keys."""
    page = lt.from_dict(sheet.arrangement()["page"], set(sheet.block_keys()))
    return [lt.keys(child) for child in lt.as_page(page).children]


# -- what the strip holds ---------------------------------------------------


def test_a_fresh_sheet_starts_with_the_dice_block_pinned(make_sheet) -> None:
    # The strip is not empty out of the box: the roller's descriptor declares
    # default_pinned, so the die is in view beside the page from the first launch.
    sheet = make_sheet(empty_strip=False)

    assert _pinned(sheet)["lines"] == [["dice"], ["scene"]]
    assert sheet.is_block_pinned("dice")
    assert not sheet.board.panel.is_empty()
    assert all("dice" not in row for row in _rows(sheet))


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
    assert all("conditions" not in row for row in _rows(sheet))
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
    assert any("conditions" in row for row in _rows(sheet))
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
    placed = [key for row in _rows(sheet) for key in row]
    assert {"conditions", "abilities"} <= set(placed)


def test_reset_layout_restores_the_default_strip(make_sheet) -> None:
    sheet = make_sheet()
    sheet.pin_block("conditions")
    sheet.canvas.set_pin_edge("bottom")
    _settle()

    sheet.reset_layout()
    _settle()

    # Back to the default strip — the Dice block and the Scene, one line each on
    # the right edge, filling it — and the block this test pinned returned to the
    # page. The live pixel sizes are whatever the rendered lines measure, so they
    # are not part of the default.
    pinned = _pinned(sheet)
    assert (pinned["edge"], pinned["lines"], pinned["extent"]) == (
        "right",
        [["dice"], ["scene"]],
        320,
    )
    assert any("conditions" in row for row in _rows(sheet))


def test_two_blocks_can_sit_across_the_strip(make_sheet) -> None:
    # The strip is a small canvas, not a single stack: blocks go beside each other
    # as readily as under each other. It renders that with the page's own
    # splitters now, so "a line across the strip" is simply a horizontal split.
    sheet = make_sheet()
    sheet.pin_block("conditions")
    _settle()

    sheet.canvas.pin_at("abilities", "conditions", "right")
    _settle()

    assert sheet.canvas.pinned_lines() == [["conditions", "abilities"]]
    (split,) = sheet.board.panel.splitters()
    assert split.orientation() == Qt.Orientation.Horizontal  # a right-edge strip
    first, second = sheet.block_frame("conditions"), sheet.block_frame("abilities")
    assert first.x() < second.x()  # side by side, not stacked
    assert first.y() == second.y()


def test_a_drop_on_a_pinned_block_names_it_and_a_side(make_sheet) -> None:
    """The same reading the page makes: a drop says which block it lands beside
    and which side of it to take, which is the only way to say "under that one"."""
    sheet = make_sheet()
    sheet.pin_block("conditions")
    _settle()
    panel = sheet.board.panel
    frame = sheet.block_frame("conditions")

    middle = frame.mapToGlobal(frame.rect().center())
    slot = panel.drop_slot(middle)

    assert slot is not None
    assert slot.target == "conditions"
    assert slot.side in ("left", "right")


def test_a_drop_at_the_end_of_a_pinned_block_stacks_under_it(make_sheet) -> None:
    sheet = make_sheet()
    sheet.pin_block("conditions")
    _settle()
    panel = sheet.board.panel
    frame = sheet.block_frame("conditions")

    low = frame.mapToGlobal(QPoint(frame.width() // 2, frame.height() - 3))
    slot = panel.drop_slot(low)

    assert slot is not None and slot.target == "conditions" and slot.side == "bottom"


def test_moving_a_pinned_block_into_its_own_cell_gives_it_a_fair_share(make_sheet) -> None:
    # Regression: the remembered proportions are live pixel sizes, only true of the
    # shape they were measured in. A cell alone in the strip is recorded as the
    # strip's whole length, so slotting the newcomer's natural size in beside that
    # number handed it a sliver — a block filling a fraction of its band until the
    # strip was resized and Qt redistributed.
    sheet = make_sheet()
    sheet.pin_block("conditions")
    sheet.canvas.pin_at("advantages", "conditions", "right")
    _settle()

    sheet.canvas.pin_at("advantages", "conditions", "top")  # move it above
    _settle()

    assert sheet.canvas.pinned_lines() == [["advantages"], ["conditions"]]
    moved = sheet.block_frame("advantages")
    assert moved.height() >= moved.minimumSizeHint().height()


def test_the_strips_thickness_does_not_follow_what_is_pinned_in_it(make_sheet) -> None:
    """It used to, and that was the bug this test was written for.

    Pinning a wide block pushed the strip open — its minimum was its content — and
    the board recorded that width as though the user had dragged it there, so
    moving the block away left the strip stale and wide. Nothing pushes it open
    now: the thickness is the one the user chose, and what is pinned inside gets
    whatever that leaves.
    """
    sheet = make_sheet()
    sheet.pin_block("conditions")
    _settle()
    thickness = sheet.board.panel.width()

    sheet.pin_block("abilities", line=0, slot=1, new_line=False)
    _settle()

    assert sheet.board.panel.width() == thickness
    # And the arriving block takes the room that is there rather than demanding more.
    frame = sheet.block_frame("abilities")
    assert frame.isVisible()
    assert frame.width() <= thickness


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
    assert _pinned(sheet)["extent"] == dragged


def test_a_strip_too_short_for_its_blocks_squashes_them_rather_than_scrolling(
    make_sheet,
) -> None:
    """The rule that replaced three tests about the strip's scrollbars.

    The strip used to report its whole content as a minimum — capped at the usable
    screen — so a window too short for the pinned blocks was answered by holding
    the *window* open, and past the cap by growing a scrollbar and asking for its
    width back. All of that existed because a block squashed was a block clipped.

    A pinned block reflows and, past that, scrolls inside its own frame, so the
    honest answer is simply to give it less room. The strip demands nothing, and
    the blocks in it get thinner.
    """
    sheet = make_sheet()
    sheet.resize(1400, 320)  # far shorter than two blocks would like
    _settle()
    sheet.pin_block("conditions")
    sheet.pin_block("advantages")
    _settle()
    _wait()
    panel = sheet.board.panel

    assert not panel._scroll.verticalScrollBar().isVisible()
    assert not panel._scroll.horizontalScrollBar().isVisible()
    for key in ("conditions", "advantages"):
        assert sheet.block_frame(key).isVisible()


def test_the_strip_demands_no_room_of_the_window(make_sheet) -> None:
    """The last place in the app where content could push the window around."""
    sheet = make_sheet()
    sheet.pin_block("conditions")
    sheet.pin_block("advantages")
    _settle()
    panel = sheet.board.panel

    content = panel._host_widget.sizeHint()
    assert panel.minimumSizeHint().width() < content.width()
    assert panel.minimumSizeHint().height() < content.height()


def test_the_strip_may_squash_a_block_below_its_content(make_sheet) -> None:
    """The exact inverse of what this file used to assert.

    ``test_the_strip_never_squashes_a_block_below_its_content`` forced the
    splitter to a sliver and checked every slot was still at least its frame's
    minimum. Squashing was the bug then, because it meant clipping; it is the
    feature now, because the block scrolls inside itself instead.
    """
    sheet = make_sheet()
    sheet.pin_block("conditions")
    sheet.pin_block("advantages")
    _settle()
    panel = sheet.board.panel

    panel.splitters()[0].setSizes([10_000, 1])
    _settle()

    thin = min(sheet.block_frame(k).height() for k in ("conditions", "advantages"))
    squashed = next(
        f
        for f in (sheet.block_frame(k) for k in ("conditions", "advantages"))
        if f.height() == thin
    )
    # Down to the block's own floor — a title bar and `block.min-extent` — which is
    # far under what its content wants, and the content scrolls inside it.
    assert thin < squashed.content_size_hint().height()
    assert thin == squashed.minimumSizeHint().height()
    assert squashed.isVisible()


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


def test_moving_the_strip_to_another_edge_keeps_its_own_thickness(make_sheet) -> None:
    """This used to be about a floor, and there is no floor left.

    The strip's thickness was clamped against the panel's minimum *at the moment
    it was applied*, and right after an edge change that minimum was still the old
    axis's — so a 645px-wide side strip became a 645px-deep bottom one. With
    nothing demanding room, the thickness is simply the number the strip carries,
    whichever edge it is on.
    """
    sheet = make_sheet()
    sheet.pin_block("conditions")
    sheet.pin_block("advantages", line=0, slot=1, new_line=False)
    _settle()
    panel = sheet.board.panel
    wanted = sheet.board.desired_extent()

    sheet.canvas.set_pin_edge("bottom")
    _wait()  # the thickness converges over a turn or two; see PinnedBoard._ask_again

    assert panel.height() == pytest.approx(wanted, abs=4)
    assert panel.height() >= panel.minimumSizeHint().height()


def test_the_strip_stacks_its_blocks_along_its_own_axis(make_sheet) -> None:
    """And takes them round with it when the axis changes.

    The splitter is re-read rather than held: the strip renders its tree with the
    page's own ``build_node`` now, so changing edge rebuilds the widgets instead of
    re-orienting one long-lived splitter.
    """
    sheet = make_sheet()
    sheet.pin_block("conditions")
    sheet.pin_block("abilities")
    _settle()

    assert sheet.board.panel.splitters()[0].orientation() == Qt.Orientation.Vertical

    sheet.canvas.set_pin_edge("bottom")
    _settle()

    assert sheet.board.panel.splitters()[0].orientation() == Qt.Orientation.Horizontal
    # And the blocks are still both there, side by side rather than stacked.
    assert sheet.canvas.pinned_keys() == ["conditions", "abilities"]


def test_alignment_is_gone_and_every_block_fills_its_cell(make_sheet) -> None:
    """``fill``/``start``/``center``/``end`` existed because a block that pinned
    its own size could not fill the cell it was given and would otherwise sit
    adrift in the middle of one. No block pins its own size now — the user does —
    so the choice had nothing left to decide."""
    sheet = make_sheet()
    sheet.pin_block("conditions")
    _settle()

    assert not hasattr(sheet.canvas, "set_pin_align")
    assert "align" not in sheet.arrangement()["region"]
    frame = sheet.block_frame("conditions")
    assert frame.width() == sheet.board.panel._host_widget.width()


# -- the drop target --------------------------------------------------------


def test_an_empty_strip_takes_a_drop_anywhere_on_it(make_sheet) -> None:
    sheet = make_sheet()
    panel = sheet.board.panel

    center = panel.mapToGlobal(panel.rect().center())

    # Nothing to land beside, so the strip names no target at all.
    assert panel.drop_slot(center) == PinSlot()


def test_a_drop_outside_the_strip_is_not_the_strips_business(make_sheet) -> None:
    sheet = make_sheet()

    assert sheet.board.drop_slot(QPoint(-5000, -5000)) is None
    page = sheet.page_scroll_area()
    over_the_page = page.mapToGlobal(page.rect().center())
    assert sheet.board.drop_slot(over_the_page) is None


def test_a_drop_lands_above_or_below_the_block_it_is_dropped_on(make_sheet) -> None:
    sheet = make_sheet()
    sheet.pin_block("conditions")
    sheet.pin_block("abilities")
    _settle()
    panel = sheet.board.panel
    first = sheet.block_frame("conditions")
    second = sheet.block_frame("abilities")

    above_first = first.mapToGlobal(QPoint(first.width() // 2, 2))
    below_second = second.mapToGlobal(QPoint(second.width() // 2, second.height() - 2))

    assert panel.drop_slot(above_first) == PinSlot("conditions", "top")
    assert panel.drop_slot(below_second) == PinSlot("abilities", "bottom")


def test_the_strip_shows_and_clears_its_drop_feedback(make_sheet) -> None:
    sheet = make_sheet()
    sheet.pin_block("conditions")
    _settle()
    panel = sheet.board.panel

    panel.show_drop(PinSlot("conditions", "bottom"))
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
    assert any("conditions" in row_keys for row_keys in _rows(sheet))


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
    landed = sheet.block_frame("conditions")
    beside = landed.mapToGlobal(QPoint(landed.width() - 5, landed.height() // 2))
    _drag(canvas, sheet, "abilities", beside)
    _wait()
    _wait()

    frame = sheet.block_frame("abilities")
    assert frame.layout().geometry().height() > 0
    assert frame.title_bar.height() > 0
    assert frame.section.height() > 0
    # Laid out by the strip's own splitter, not left at the width it had on the
    # page. It shares that width with the block it landed beside, so what is
    # asserted is that the two of them account for the strip between them.
    parent = frame.parentWidget()
    shares = sum(parent.widget(i).width() for i in range(parent.count()))
    assert shares + parent.handleWidth() * (parent.count() - 1) == parent.width()
    assert 0 < frame.width() < parent.width()


def test_dragging_one_pinned_block_beside_another_joins_its_line(make_sheet) -> None:
    sheet = make_sheet()
    sheet.pin_block("conditions")
    sheet.pin_block("abilities")
    _settle()
    canvas = sheet.canvas
    frame = sheet.block_frame("abilities")
    start = frame.title_bar.mapToGlobal(QPoint(10, 5))
    first = sheet.block_frame("conditions")
    beside = first.mapToGlobal(QPoint(first.width() - 6, first.height() // 2))

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


def test_the_strip_survives_a_save_and_restore(make_sheet) -> None:
    sheet = make_sheet()
    sheet.pin_block("conditions")
    sheet.pin_block("abilities")
    sheet.canvas.set_pin_edge("left")
    _settle()
    blob = sheet.save_layout()

    sheet.reset_layout()
    _settle()
    assert _pinned(sheet)["lines"] == [["dice"], ["scene"]]  # back to the default strip

    assert sheet.restore_layout(blob) is True
    _settle()
    restored = _pinned(sheet)
    assert restored["lines"] == [["conditions"], ["abilities"]]
    assert restored["edge"] == "left"
    assert sheet.board.panel.splitters()[0].count() == 2


def test_a_restore_puts_the_pinned_blocks_back_in_their_lines(make_sheet) -> None:
    """What survives a save is *where* the blocks are, not how wide they were.

    The strip used to persist its own pixel proportions, and this test guarded a
    bug where restoring an identical set of blocks silently kept whatever was on
    screen. Those numbers described the strip's own layout engine, which the grid
    replaced; a wrong remembered size is worse than none, so they are dropped and
    the lines lay themselves out once from their blocks' hints.
    """
    sheet = make_sheet()
    sheet.pin_block("conditions")
    sheet.pin_block("abilities")
    _settle()
    blob = sheet.save_layout()

    sheet.canvas.unpin_all()
    _settle()
    assert _pinned(sheet)["lines"] == []

    assert sheet.restore_layout(blob) is True
    _settle()
    assert _pinned(sheet)["lines"] == [["conditions"], ["abilities"]]


def test_a_pinned_block_moves_beside_another_without_losing_its_place(make_sheet) -> None:
    sheet = make_sheet()
    sheet.pin_block("conditions")
    sheet.pin_block("abilities")
    sheet.pin_block("resistances")
    _settle()
    assert sheet.canvas.pinned_lines() == [["conditions"], ["abilities"], ["resistances"]]

    # Beside the last one. The strip speaks in blocks and sides now, so there is
    # no line index to shift when taking the block out collapses the run it was in
    # — which is the whole class of bug the old vocabulary kept inviting.
    sheet.canvas.pin_at("conditions", "resistances", "right")
    _settle()

    assert sheet.canvas.pinned_lines() == [["abilities"], ["resistances", "conditions"]]


def test_a_layout_that_puts_one_block_in_two_places_is_rejected(make_sheet) -> None:
    sheet = make_sheet()
    model = sheet.arrangement()
    # "conditions" is still in its row, and now also in the strip.
    model["region"] = dict(model["region"], root=lt.to_dict(lt.Leaf(("conditions",))))

    assert sheet.restore_layout(json.dumps(model)) is False
    assert _pinned(sheet)["lines"] == []


def test_a_layout_with_an_unknown_edge_or_block_is_rejected(make_sheet) -> None:
    sheet = make_sheet()
    base = sheet.arrangement()

    sideways = dict(base, region=dict(base["region"], edge="diagonal"))
    assert sheet.restore_layout(json.dumps(sideways)) is False

    stranger = dict(base, region=dict(base["region"], root=lt.to_dict(lt.Leaf(("not_a_block",)))))
    assert sheet.restore_layout(json.dumps(stranger)) is False


def test_a_layout_written_before_the_strip_existed_still_restores(make_sheet) -> None:
    # `region` is read tolerantly for the same reason `hidden_anchors` is: its
    # absence costs nothing but an empty strip.
    sheet = make_sheet()
    model = sheet.arrangement()
    model.pop("region")

    assert sheet.restore_layout(json.dumps(model)) is True
    assert _pinned(sheet)["lines"] == []


def test_bad_proportions_degrade_instead_of_rejecting_the_layout(make_sheet) -> None:
    sheet = make_sheet()
    model = sheet.arrangement()
    model["region"] = dict(model["region"], extent=-4)  # the one number left

    assert sheet.restore_layout(json.dumps(model)) is True
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


def test_a_change_to_the_strip_settles_at_the_thickness_it_was_given(make_sheet) -> None:
    """The retries existed for a *stale minimum*, and there is no minimum now.

    A strip whose thickness was forced by its content had to converge over a turn
    or two, because the number it was clamped against only became true after the
    blocks had been laid out. Nothing clamps it any more, so a pin lands at the
    thickness the strip already had and stays there.
    """
    sheet = make_sheet(empty_strip=False)
    _settle()
    _wait()
    board = sheet.board
    wanted = board.desired_extent()

    sheet.pin_block("conditions")
    _settle()
    _wait()

    assert board.desired_extent() == wanted
    assert not board._settle.isActive()


def test_the_strip_reports_whether_it_rebuilt(make_sheet) -> None:
    """The contract the board reads to decide whether to settle."""
    sheet = make_sheet()
    sheet.pin_block("conditions")
    _settle()
    panel = sheet.board.panel
    args = (sheet.canvas.pin_region(), sheet.canvas._frames, sheet.canvas._pin_edge)
    assert panel.set_blocks(*args) is False  # nothing moved -> skipped
    panel.invalidate()
    assert panel.set_blocks(*args) is True  # a restore always rebuilds


def test_a_restore_rebuilds_the_strip_even_with_the_same_blocks_in_it(make_sheet) -> None:
    # keys and edge are all set_blocks compares, so a layout restoring the same
    # blocks is invisible to it. invalidate() is the only thing that makes it look
    # again — which is why apply_arrangement calls it, and this is the fence on
    # that call. (It used to be observed through the proportions a restore put
    # back; those are no longer part of the arrangement, so the rebuild itself is
    # what gets watched.)
    sheet = make_sheet()
    sheet.pin_block("conditions")
    sheet.pin_block("advantages", line=0, slot=1, new_line=False)
    _settle()
    model = sheet.arrangement()
    rebuilds: list[bool] = []
    panel = sheet.board.panel
    original = panel.set_blocks
    panel.set_blocks = lambda *a, **k: (rebuilds.append(True), original(*a, **k))[1]

    sheet.canvas.apply_arrangement(model)
    _settle()

    assert rebuilds, "the strip was never asked to rebuild"
    assert _pinned(sheet)["lines"] == [["conditions", "advantages"]]


def test_a_hand_written_tab_group_in_the_strip_is_opened_out(make_sheet) -> None:
    """The strip draws no tab bar, so a cell naming two blocks loses one of them.

    Nothing the app does writes one — a block is pinned one at a time and a drag
    of a whole group is refused — but a hand-edited settings file can, and the
    block that was not the active tab arrived parented to the panel with nowhere
    to be, drawn on top of the strip rather than in it. The honest reading of such
    a region is "both of these are pinned", not "throw the layout away".
    """
    sheet = make_sheet()
    sheet.pin_block("conditions")
    sheet.pin_block("advantages")
    _settle()
    model = sheet.arrangement()
    model["region"]["root"] = {
        "type": "leaf",
        "keys": ["conditions", "advantages"],
        "active": 0,
    }

    assert sheet.canvas.apply_arrangement(model) is True
    _settle()

    region = sheet.canvas.pin_region()
    assert lt.keys(region) == ["conditions", "advantages"]
    assert all(not leaf.tabbed for _path, leaf in lt.iter_leaves(region))
    for key in ("conditions", "advantages"):
        frame = sheet.canvas.block_frame(key)
        assert frame.isVisible(), f"{key} was left with nowhere to be"


class TestTheStripsOwnDetent:
    """The one divider on the sheet that had no recommended-size hint.

    The strip's *internal* dividers always had one — they are the page's own
    splitters, so they came with it. The handle that sets the strip's
    **thickness** was a bare ``QSplitter``: no detent, no mark, and no way to
    find the width at which the blocks in it actually read.
    """

    def test_the_board_offers_the_thickness_the_blocks_want(self, make_sheet) -> None:
        sheet = make_sheet()
        sheet.pin_block("dice")
        _settle()

        splitter = sheet.board._splitter
        wanted = sheet.board.panel.recommended_size().width
        assert wanted > 0, "the strip states no thickness to stick at"

        (target,) = splitter.detent_positions(1)
        sizes = splitter.sizes()
        total = sum(sizes) + splitter.handleWidth() * (len(sizes) - 1)
        assert target == total - splitter.handleWidth() - wanted

    def test_it_offers_nothing_for_the_page_side(self, make_sheet) -> None:
        """One target, not the two a divider between blocks gets.

        The other pane is the whole page, whose hint is a number about a scroll
        area rather than a width anybody wants the strip to stop at.
        """
        sheet = make_sheet()
        sheet.pin_block("dice")
        _settle()

        assert len(sheet.board._splitter.detent_positions(1)) == 1

    def test_an_empty_strip_sticks_at_nothing(self, make_sheet) -> None:
        sheet = make_sheet()  # empty by default
        _settle()

        assert sheet.board.panel.recommended_size().width == 0
        assert sheet.board._splitter.detent_positions(1) == []

    def test_blocks_down_the_strip_want_the_widest_of_them(self, make_sheet) -> None:
        """Stacked down a left/right strip they share the thickness, not divide it."""
        sheet = make_sheet()
        sheet.pin_block("dice")
        sheet.canvas.pin_at("conditions", target="dice", side="bottom")
        _settle()

        sizes = load_block_sizes()
        widest = max(sizes["dice"].width, sizes["conditions"].width)
        assert sheet.board.panel.recommended_size().width >= widest

    def test_moving_the_strip_to_the_bottom_turns_the_question(self, make_sheet) -> None:
        """Thickness is a height there, and the same two blocks now divide it."""
        sheet = make_sheet()
        sheet.pin_block("dice")
        sheet.canvas.pin_at("conditions", target="dice", side="bottom")
        _settle()
        across = sheet.board.panel.recommended_size()

        sheet.canvas.set_pin_edge("bottom")
        _settle()
        down = sheet.board.panel.recommended_size()

        assert across.width > 0 and across.height == 0
        assert down.height > 0 and down.width == 0

    def test_the_strip_still_cannot_be_collapsed_by_its_handle(self, make_sheet) -> None:
        """Its handle *is* the strip when it is empty; a collapsed one is lost."""
        sheet = make_sheet()
        sheet.pin_block("dice")
        _settle()

        assert sheet.board._splitter.childrenCollapsible() is False

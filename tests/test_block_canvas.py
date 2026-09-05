"""The block canvas: hit-test geometry and arrangement persistence."""

from __future__ import annotations

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from mm_companion.core.data_loader import load_game_data
from mm_companion.ui.block_canvas import SCHEMA_VERSION
from mm_companion.ui.character_sheet import CharacterSheet


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


@pytest.fixture
def make_sheet(qapp: QApplication):
    """Build laid-out character sheets without ever creating on-screen windows.

    These tests need real geometry (``_hit_test`` reads ``mapToGlobal`` positions),
    which requires the sheet to be shown and laid out — but on the native Windows
    platform, destroying a *real* heavy window at teardown kicks off a re-entrant
    flow-layout/scrollbar relayout that loops synchronously inside a single event
    handler and never returns, hanging the shared ``processEvents()`` teardown
    (a per-event deadline can't interrupt one non-returning handler).

    ``WA_DontShowOnScreen`` gives us the layout and geometry with no native window
    to tear down — the same headless path CI already exercises under xvfb — so the
    relayout loop is never triggered. Sheets are also disposed here (which frees
    their floated ``BlockWindow`` children, parented to the sheet) so the global
    teardown finds nothing to pump.
    """
    sheets: list[CharacterSheet] = []

    def _make() -> CharacterSheet:
        sheet = CharacterSheet(load_game_data())
        sheet.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
        sheet.resize(1000, 1000)
        sheet.show()
        for _ in range(5):
            QApplication.processEvents()
        sheets.append(sheet)
        return sheet

    yield _make

    for sheet in sheets:
        sheet.hide()
        sheet.deleteLater()  # also frees its floated BlockWindows (parented to it)
    QApplication.processEvents()


def _settle(times: int = 5) -> None:
    for _ in range(times):
        QApplication.processEvents()


def page_rows(sheet) -> list[list[str]]:
    """The page's top-level rows, each as a flat list of keys.

    The arrangement is a tree now, and most of these tests only ever cared which
    blocks share a row — the same view :attr:`BlockCanvas._rows` gives the class
    itself. Nested cells flatten into their row, in reading order.
    """
    from mm_companion.ui import layout_tree as lt

    page = lt.from_dict(sheet.arrangement()["page"], set(sheet.block_keys()))
    return [lt.keys(child) for child in lt.as_page(page).children]


def test_hit_test_targets_the_row_under_the_cursor(make_sheet) -> None:
    sheet = make_sheet()
    canvas = sheet.canvas
    row = canvas._row_widgets[1]  # the Abilities | Resistances row

    center = row.mapToGlobal(row.rect().center())
    slot = canvas._hit_test(center)

    assert slot is not None
    assert slot.new_row is False
    assert slot.row == 1


def test_hit_test_in_the_gap_between_rows_makes_a_new_row(make_sheet) -> None:
    sheet = make_sheet()
    canvas = sheet.canvas
    # In canvas coordinates: a row's own geometry() is relative to the holder
    # the stack wraps it in, which is not where the hit test is looking.
    top = canvas._row_geometry(canvas._row_widgets[0])
    below = canvas._row_geometry(canvas._row_widgets[1])

    gap_y = (top.bottom() + below.top()) // 2
    point = canvas.mapToGlobal(QPoint(canvas.width() // 2, gap_y))
    slot = canvas._hit_test(point)

    assert slot is not None
    assert slot.new_row is True
    assert slot.row == 1


def test_hit_test_off_the_page_returns_none(make_sheet) -> None:
    sheet = make_sheet()
    canvas = sheet.canvas

    # A point far outside the viewport is not a drop target.
    assert canvas._hit_test(QPoint(-5000, -5000)) is None


def test_save_restore_round_trips_a_floated_block(make_sheet) -> None:
    sheet = make_sheet()
    sheet.float_block("powers")
    sheet.dock_block("skills", 0, 0, new_row=True)

    blob = sheet.save_layout()
    sheet.reset_layout()
    assert sheet.arrangement()["floating"] == {}

    assert sheet.restore_layout(blob) is True
    restored = sheet.arrangement()
    assert "powers" in restored["floating"]
    assert page_rows(sheet)[0] == ["skills"]


def test_hidden_block_survives_relayout_and_reopens(make_sheet) -> None:
    # Regression: hiding a docked block left its frame parented to a row widget
    # that _relayout then deleted (deleteLater), destroying the frame's C++ object;
    # reopening it later crashed. The frame must survive across event-loop turns.
    sheet = make_sheet()

    sheet.hide_block("advantages")
    for _ in range(5):  # let the deleted old rows' deleteLater actually fire
        QApplication.processEvents()
    sheet.show_block("advantages")
    for _ in range(3):
        QApplication.processEvents()

    placed = [key for row in page_rows(sheet) for key in row]
    assert "advantages" in placed
    assert sheet.block_frame("advantages").isVisible()


def test_reopened_block_returns_beside_its_old_row_mate(make_sheet) -> None:
    sheet = make_sheet()
    before = page_rows(sheet)

    sheet.hide_block("resistances")
    sheet.show_block("resistances")

    # Back in the Abilities | Resistances | Conditions row, not appended at the end.
    assert page_rows(sheet) == before


def test_reopened_lone_block_returns_to_its_own_row(make_sheet) -> None:
    sheet = make_sheet()
    before = page_rows(sheet)

    sheet.hide_block("advantages")  # alone in its row
    assert all("advantages" not in row for row in page_rows(sheet))
    sheet.show_block("advantages")

    assert page_rows(sheet) == before


def test_reopen_falls_back_to_the_default_position(make_sheet) -> None:
    # A layout saved before anchors existed (or one whose anchor was dropped)
    # reopens the block at its default spot rather than at the end of the page.
    sheet = make_sheet()
    before = page_rows(sheet)

    sheet.hide_block("resistances")
    sheet.canvas._anchors.clear()
    sheet.show_block("resistances")

    assert page_rows(sheet) == before


def test_reopen_appends_when_nothing_resolves(make_sheet) -> None:
    # No remembered anchor, and the default neighbour (Abilities) is itself
    # hidden: with nothing left to hang off, the old append-at-the-end stands in.
    sheet = make_sheet()

    sheet.hide_block("abilities")
    sheet.hide_block("resistances")
    sheet.canvas._anchors.clear()
    sheet.show_block("resistances")

    assert page_rows(sheet)[-1] == ["resistances"]


def test_hidden_anchor_survives_a_layout_round_trip(make_sheet) -> None:
    sheet = make_sheet()
    # Move Powers somewhere it would never land by default, then close it there.
    sheet.dock_block("powers", 1, 0)
    sheet.hide_block("powers")

    blob = sheet.save_layout()
    sheet.reset_layout()
    assert sheet.restore_layout(blob) is True
    assert sheet.is_block_hidden("powers")

    sheet.show_block("powers")
    assert page_rows(sheet)[1][0] == "powers"


def test_layout_without_anchors_still_restores(make_sheet) -> None:
    # `hidden_anchors` is additive, so a layout written before it existed loads.
    import json

    sheet = make_sheet()
    model = sheet.arrangement()
    model.pop("hidden_anchors")

    assert sheet.restore_layout(json.dumps(model)) is True


def test_apply_arrangement_transitions_dont_destroy_frames(make_sheet) -> None:
    # A block that goes docked→floating or docked→hidden via apply_arrangement must
    # not be destroyed when its old row is freed.
    import json

    sheet = make_sheet()
    from mm_companion.ui import layout_tree as lt

    model = {
        "version": SCHEMA_VERSION,
        "page": lt.to_dict(
            lt.rows_to_page(
                [
                    ["base_info", "system_info", "character_image"],
                    ["abilities", "resistances"],
                    ["conditions"],
                    ["complications"],
                    ["powers"],
                    ["equipment"],
                    ["notes"],
                    ["dice"],
                    ["scene"],
                ]
            )
        ),
        "floating": {"skills": {"x": 50, "y": 50, "w": 400, "h": 400}},
        "hidden": ["advantages"],
    }
    assert sheet.restore_layout(json.dumps(model)) is True
    for _ in range(5):
        QApplication.processEvents()

    # Both transitioned blocks are still live and reachable.
    assert "skills" in sheet.arrangement()["floating"]
    sheet.show_block("advantages")
    sheet.dock_block("skills", 0, 0, new_row=True)
    for _ in range(3):
        QApplication.processEvents()
    assert sheet.block_frame("skills").isVisible()
    assert sheet.block_frame("advantages").isVisible()


def test_restore_layout_rejects_garbage(make_sheet) -> None:
    sheet = make_sheet()
    default_rows = page_rows(sheet)

    assert sheet.restore_layout("") is False
    assert sheet.restore_layout("not json") is False
    assert sheet.restore_layout('{"version": 1}') is False  # wrong schema version

    assert page_rows(sheet) == default_rows  # unchanged


# -- row heights: what replaced fill_last ----------------------------------


def _plain_canvas():
    """A tiny two-block canvas built directly, no character sheet involved."""
    from PySide6.QtWidgets import QLabel

    from mm_companion.ui.block_canvas import BlockCanvas
    from mm_companion.ui.block_sizes import RecommendedSize

    panels = [("a", "A", QLabel("a")), ("b", "B", QLabel("b"))]
    sizes = {"a": RecommendedSize(width=100), "b": RecommendedSize(width=100)}
    return BlockCanvas(panels, sizes, [["a"], ["b"]])


def test_a_row_nobody_has_dragged_states_no_height(qapp: QApplication) -> None:
    """Which means "as tall as your content" — so the sheet behaves exactly as it
    always has until somebody actually resizes something, and adding a skill still
    makes the Skills block taller rather than making it scroll."""
    canvas = _plain_canvas()

    assert canvas._row_heights() == [0, 0]
    assert canvas.page_tree().sizes == ()
    canvas.deleteLater()


def test_a_dragged_height_is_remembered_against_its_row(qapp: QApplication) -> None:
    canvas = _plain_canvas()

    canvas._stack._on_dragged(0, 240)
    canvas._remember_sizes()

    assert canvas.page_tree().sizes == (240, 0)
    assert canvas._row_heights() == [240, 0]
    canvas.deleteLater()


def test_a_dragged_height_survives_a_save_and_restore(qapp: QApplication) -> None:
    canvas = _plain_canvas()
    canvas._stack._on_dragged(1, 180)

    model = canvas.arrangement()
    canvas.reset()
    assert canvas.page_tree().sizes == ()

    assert canvas.apply_arrangement(model) is True
    assert canvas.page_tree().sizes == (0, 180)
    canvas.deleteLater()


class TestADropTakesHalfOfWhatItLandsBeside:
    """The mark's promise, kept.

    The wash shown under a drag fills half the block being dropped beside, so that
    block is what pays for the arrival and its neighbours must not move. The page
    used to clear the whole run's remembered sizes and lay it out afresh from every
    cell's hint, which redistributed the entire row: the block you had aimed at
    frequently came out the same size it went in while the one next to it shrank.
    """

    def _row_widths(self, canvas) -> dict[str, int]:
        row = canvas._row_widgets[0]
        return {row.widget(i).key: row.sizes()[i] for i in range(row.count())}

    def test_the_target_gives_up_half_and_its_neighbours_do_not_move(self, make_sheet) -> None:
        from mm_companion.ui.block_canvas import DropSlot

        sheet = make_sheet()
        canvas = sheet.canvas
        before = self._row_widths(canvas)
        target, *rest = list(before)

        canvas.drop_block("skills", DropSlot(False, 0, 0, target=target, side="right"))
        _settle()

        after = self._row_widths(canvas)
        assert list(after) == [target, "skills", *rest]
        # Half of the target, give or take the divider the pair now has between them.
        assert abs(after[target] - before[target] // 2) <= 4
        assert abs(after["skills"] - before[target] // 2) <= 4
        for key in rest:
            assert abs(after[key] - before[key]) <= 4, f"{key} paid for a drop beside {target}"

    def test_it_lands_on_the_side_the_mark_showed(self, make_sheet) -> None:
        from mm_companion.ui.block_canvas import DropSlot

        sheet = make_sheet()
        canvas = sheet.canvas
        target = canvas._row_widgets[0].widget(1).key

        canvas.drop_block("skills", DropSlot(False, 0, 0, target=target, side="left"))
        _settle()

        keys = list(self._row_widths(canvas))
        assert keys[keys.index("skills") + 1] == target


def test_the_page_is_as_tall_as_its_rows_rather_than_its_window(qapp: QApplication) -> None:
    """The other half of "height scrolls": dragging a row taller makes the page
    longer instead of stealing the height from the row below it."""
    canvas = _plain_canvas()
    canvas.resize(400, 400)
    qapp.processEvents()
    before = canvas.sizeHint().height()

    canvas._stack._on_dragged(0, 600)
    qapp.processEvents()

    assert canvas.sizeHint().height() > before
    canvas.deleteLater()


# -- edge auto-scroll -------------------------------------------------------


def _hot_point(sheet, x_source) -> QPoint:
    """A global point in the page's bottom auto-scroll band, at *x_source*'s x."""
    viewport = sheet.page_scroll_area().viewport()
    y = viewport.mapToGlobal(QPoint(0, viewport.height() - 5)).y()
    return QPoint(x_source.mapToGlobal(x_source.rect().center()).x(), y)


def test_a_drag_crossing_the_strip_stops_the_page_scrolling(make_sheet) -> None:
    # Regression: the velocity outlives the cursor leaving the page. update_drag
    # returns early once the cursor is over the strip, and _autoscroll_tick calls
    # straight back into update_drag — so the page scrolled forever under a gesture
    # that had gone, until the drop. The hot band is measured from the viewport's y
    # alone, so the strip beside it shares the band.
    sheet = make_sheet()  # the default strip: the Dice block, pinned right
    canvas = sheet.canvas
    viewport = sheet.page_scroll_area().viewport()
    panel = sheet.board.panel

    page_point = _hot_point(sheet, viewport)
    strip_point = _hot_point(sheet, panel)
    assert canvas._pin_hit_test(strip_point) is not None  # it really is over the strip

    start = sheet.block_frame("skills").title_bar.mapToGlobal(QPoint(10, 5))
    canvas.title_bar_pressed("skills", start)
    canvas.title_bar_moved("skills", start + QPoint(-30, -30))
    QApplication.processEvents()

    canvas.title_bar_moved("skills", page_point)
    # Asserted *before* pumping, and that is not impatience. ``_autoscroll_tick``
    # refreshes the drag from ``QCursor.pos()`` — the real mouse, which is right
    # for a real drag and is nowhere near this synthetic one — so a 16ms tick
    # landing inside ``processEvents`` reads a velocity of zero and stands the
    # timer back down. Whether one lands is a race with however long the layout
    # before it took, which makes this control a coin toss anywhere but here.
    assert canvas._autoscroll_timer.isActive()  # positive control: it did start
    QApplication.processEvents()

    canvas.title_bar_moved("skills", strip_point)
    QApplication.processEvents()
    assert not canvas._autoscroll_timer.isActive()
    assert canvas._autoscroll_velocity == 0

    bar = sheet.page_scroll_area().verticalScrollBar()
    resting = bar.value()
    QTest.qWait(80)  # real time: the timer is 16ms, so processEvents alone proves nothing
    assert bar.value() == resting

    canvas.title_bar_released("skills", strip_point)
    QApplication.processEvents()


def test_update_drag_leaves_no_autoscroll_running_when_nothing_may_land(make_sheet) -> None:
    # The same rule on update_drag's other early return. Entering compact mode
    # mid-drag already ends the gesture outright, so this is the contract of the
    # guard itself rather than of that path: asked to refresh a drag that cannot
    # land, update_drag must leave nothing running behind it.
    sheet = make_sheet()
    canvas = sheet.canvas
    viewport = sheet.page_scroll_area().viewport()

    canvas._maybe_autoscroll(_hot_point(sheet, viewport))
    assert canvas._autoscroll_timer.isActive()  # positive control

    canvas._windows_suspended = True
    canvas.update_drag(_hot_point(sheet, viewport))
    QApplication.processEvents()
    assert not canvas._autoscroll_timer.isActive()
    assert canvas._autoscroll_velocity == 0


class TestTheTitleBarMenu:
    """Right-clicking a block's title bar, which used to do nothing at all.

    The three buttons are the fast path and are unchanged. This is for the rest:
    *Fit to content* has no button and never could have one, and the arrangement
    gestures were otherwise a 10px divider you had to find and a title bar you had
    to know was draggable.
    """

    def _labels(self, sheet, key: str) -> list[str]:
        menu = sheet.canvas.block_menu(key)
        return [action.text() for action in menu.actions() if not action.isSeparator()]

    def test_a_docked_block_can_be_fitted_pinned_popped_out_or_closed(self, make_sheet) -> None:
        sheet = make_sheet()
        assert self._labels(sheet, "skills") == [
            "Fit to content",
            "Pin to the strip",
            "Pop out into its own window",
            "Close",
        ]

    def test_a_pinned_block_is_offered_the_way_back(self, make_sheet) -> None:
        sheet = make_sheet()
        sheet.canvas.pin_block("skills")
        _settle()

        labels = self._labels(sheet, "skills")
        assert "Send back to the page" in labels
        assert "Pin to the strip" not in labels

    def test_a_floated_block_is_offered_docking_and_staying_on_top(self, make_sheet) -> None:
        sheet = make_sheet()
        sheet.canvas.float_block("skills")
        _settle()

        labels = self._labels(sheet, "skills")
        assert "Dock back on the page" in labels
        assert "Keep above other windows" in labels
        assert "Pop out into its own window" not in labels

    def test_an_unknown_block_offers_nothing_rather_than_raising(self, make_sheet) -> None:
        sheet = make_sheet()
        assert sheet.canvas.block_menu("no-such-block").isEmpty()

    def test_docking_back_returns_the_block_to_the_row_it_left(self, make_sheet) -> None:
        sheet = make_sheet()
        before = page_rows(sheet)
        sheet.canvas.float_block("skills")
        _settle()
        assert "skills" not in [key for row in page_rows(sheet) for key in row]

        sheet.canvas.dock_block_back("skills")
        _settle()

        assert page_rows(sheet) == before

    def test_closing_from_the_menu_is_the_close_button(self, make_sheet) -> None:
        sheet = make_sheet()
        menu = sheet.canvas.block_menu("skills")
        close = next(action for action in menu.actions() if action.text() == "Close")

        close.trigger()
        _settle()

        assert sheet.canvas.is_hidden("skills")

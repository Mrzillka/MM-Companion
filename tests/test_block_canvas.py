"""The block canvas: hit-test geometry and arrangement persistence."""

from __future__ import annotations

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtWidgets import QApplication

from mm_companion.core.data_loader import load_game_data
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
    top = canvas._row_widgets[0].geometry()
    below = canvas._row_widgets[1].geometry()

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
    assert restored["rows"][0] == ["skills"]


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

    placed = [key for row in sheet.arrangement()["rows"] for key in row]
    assert "advantages" in placed
    assert sheet.block_frame("advantages").isVisible()


def test_apply_arrangement_transitions_dont_destroy_frames(make_sheet) -> None:
    # A block that goes docked→floating or docked→hidden via apply_arrangement must
    # not be destroyed when its old row is freed.
    import json

    sheet = make_sheet()
    model = {
        "version": 5,
        "rows": [
            ["base_info", "system_info", "character_image"],
            ["abilities", "resistances"],
            ["conditions"],
            ["powers"],
        ],
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
    default_rows = sheet.arrangement()["rows"]

    assert sheet.restore_layout("") is False
    assert sheet.restore_layout("not json") is False
    assert sheet.restore_layout('{"version": 1}') is False  # wrong schema version

    assert sheet.arrangement()["rows"] == default_rows  # unchanged

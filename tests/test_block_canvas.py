"""The block canvas: hit-test geometry and arrangement persistence."""

from __future__ import annotations

import pytest
from PySide6.QtCore import QPoint
from PySide6.QtWidgets import QApplication

from mm_companion.core.data_loader import load_game_data
from mm_companion.ui.character_sheet import CharacterSheet


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


def _shown_sheet() -> CharacterSheet:
    sheet = CharacterSheet(load_game_data())
    sheet.resize(1000, 1000)
    sheet.show()
    for _ in range(5):
        QApplication.processEvents()
    return sheet


def test_hit_test_targets_the_row_under_the_cursor(qapp: QApplication) -> None:
    sheet = _shown_sheet()
    canvas = sheet.canvas
    row = canvas._row_widgets[1]  # the Abilities | Resistances row

    center = row.mapToGlobal(row.rect().center())
    slot = canvas._hit_test(center)

    assert slot is not None
    assert slot.new_row is False
    assert slot.row == 1


def test_hit_test_in_the_gap_between_rows_makes_a_new_row(qapp: QApplication) -> None:
    sheet = _shown_sheet()
    canvas = sheet.canvas
    top = canvas._row_widgets[0].geometry()
    below = canvas._row_widgets[1].geometry()

    gap_y = (top.bottom() + below.top()) // 2
    point = canvas.mapToGlobal(QPoint(canvas.width() // 2, gap_y))
    slot = canvas._hit_test(point)

    assert slot is not None
    assert slot.new_row is True
    assert slot.row == 1


def test_hit_test_off_the_page_returns_none(qapp: QApplication) -> None:
    sheet = _shown_sheet()
    canvas = sheet.canvas

    # A point far outside the viewport is not a drop target.
    assert canvas._hit_test(QPoint(-5000, -5000)) is None


def test_save_restore_round_trips_a_floated_block(qapp: QApplication) -> None:
    sheet = _shown_sheet()
    sheet.float_block("powers")
    sheet.dock_block("skills", 0, 0, new_row=True)

    blob = sheet.save_layout()
    sheet.reset_layout()
    assert sheet.arrangement()["floating"] == {}

    assert sheet.restore_layout(blob) is True
    restored = sheet.arrangement()
    assert "powers" in restored["floating"]
    assert restored["rows"][0] == ["skills"]


def test_hidden_block_survives_relayout_and_reopens(qapp: QApplication) -> None:
    # Regression: hiding a docked block left its frame parented to a row widget
    # that _relayout then deleted (deleteLater), destroying the frame's C++ object;
    # reopening it later crashed. The frame must survive across event-loop turns.
    sheet = _shown_sheet()

    sheet.hide_block("advantages")
    for _ in range(5):  # let the deleted old rows' deleteLater actually fire
        QApplication.processEvents()
    sheet.show_block("advantages")
    for _ in range(3):
        QApplication.processEvents()

    placed = [key for row in sheet.arrangement()["rows"] for key in row]
    assert "advantages" in placed
    assert sheet.block_frame("advantages").isVisible()


def test_apply_arrangement_transitions_dont_destroy_frames(qapp: QApplication) -> None:
    # A block that goes docked→floating or docked→hidden via apply_arrangement must
    # not be destroyed when its old row is freed.
    import json

    sheet = _shown_sheet()
    model = {
        "version": 3,
        "rows": [["base_info"], ["abilities", "resistances"], ["powers"]],
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


def test_restore_layout_rejects_garbage(qapp: QApplication) -> None:
    sheet = _shown_sheet()
    default_rows = sheet.arrangement()["rows"]

    assert sheet.restore_layout("") is False
    assert sheet.restore_layout("not json") is False
    assert sheet.restore_layout('{"version": 1}') is False  # wrong schema version

    assert sheet.arrangement()["rows"] == default_rows  # unchanged

"""A gesture and its undoing leave every block the size it was.

Each test here round-trips one ordinary gesture — pin and unpin, close and reopen,
unpin and re-pin — and checks that what came back is the page that went in. They are
the regression tests for "blocks change size on their own": nothing the user did
was a resize, and yet rows came back lopsided, the top row reshaped itself when a
block several rows down was reopened, and the roller came back from an unpin a
quarter of its height.
"""

from __future__ import annotations

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QSplitter

from mm_companion.core.data_loader import load_game_data
from mm_companion.ui import layout_tree as lt
from mm_companion.ui.character_sheet import CharacterSheet
from mm_companion.ui.grid_view import GridSplitter


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


@pytest.fixture
def sheet(qapp: QApplication):
    built = CharacterSheet(load_game_data())
    built.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
    built.resize(1400, 900)
    built.show()
    _settle()
    yield built
    built.hide()
    built.deleteLater()
    QApplication.processEvents()


def _settle(times: int = 6) -> None:
    for _ in range(times):
        QApplication.processEvents()


def _row_sizes(sheet: CharacterSheet) -> list[list[int] | int]:
    """What the page shows, row by row: a splitter's sizes, or a lone block's width."""
    rows = sheet.canvas._row_widgets
    return [row.sizes() if isinstance(row, QSplitter) else row.width() for row in rows]


def _close(a: list, b: list, tolerance: int = 3) -> bool:
    """Equal to within rounding: sizes come back through a proportion."""
    if len(a) != len(b):
        return False
    for x, y in zip(a, b, strict=True):
        if isinstance(x, list) != isinstance(y, list):
            return False
        if isinstance(x, list):
            if not _close(x, y, tolerance):
                return False
        elif abs(x - y) > tolerance:
            return False
    return True


def _strip_heights(sheet: CharacterSheet) -> dict[str, int]:
    canvas = sheet.canvas
    return {key: canvas._frames[key].height() for key in canvas.pinned_keys()}


# -- the snapshot that read a placeholder ------------------------------------------


def test_a_snapshot_taken_right_after_a_rebuild_keeps_the_real_sizes(sheet) -> None:
    """A rebuilt row is born in Qt's default box, its sizes squeezed into it.

    Every layout snapshot is taken in the same breath as the gesture that rebuilt
    the page, so reading the splitters then wrote the squeezed proportions into the
    tree — and the next render scaled *those* up to the full width.
    """
    canvas = sheet.canvas
    canvas._remember_sizes()
    before = lt.to_dict(canvas.page_tree())

    canvas._relayout()  # new splitters, not laid out yet
    canvas._remember_sizes()  # what arrangement() does

    assert lt.to_dict(canvas.page_tree()) == before


def test_a_fresh_splitter_says_it_has_no_real_sizes_until_laid_out(qapp) -> None:
    splitter = GridSplitter(Qt.Orientation.Horizontal)
    splitter.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
    assert splitter.has_real_sizes is False
    splitter.resize(600, 100)
    splitter.show()
    _settle()
    assert splitter.has_real_sizes is True
    splitter.deleteLater()


# -- round trips -------------------------------------------------------------------


def test_reopening_a_block_leaves_every_other_row_alone(sheet) -> None:
    before = _row_sizes(sheet)

    sheet.canvas.hide_block("advantages")
    _settle()
    sheet.canvas.show_block("advantages")
    _settle()

    assert _close(_row_sizes(sheet), before)


def test_closing_and_reopening_a_block_in_a_row_gives_it_its_share_back(sheet) -> None:
    before = _row_sizes(sheet)

    sheet.canvas.hide_block("conditions")
    _settle()
    sheet.canvas.show_block("conditions")
    _settle()

    assert _close(_row_sizes(sheet), before)


def test_pinning_and_unpinning_a_block_puts_its_row_back(sheet) -> None:
    before = _row_sizes(sheet)

    sheet.canvas.pin_block("conditions")
    _settle()
    sheet.canvas.unpin_block("conditions")
    _settle()

    assert _close(_row_sizes(sheet), before)


def test_pinning_and_unpinning_leaves_the_rest_of_the_strip_as_it_was(sheet) -> None:
    before = _strip_heights(sheet)

    sheet.canvas.pin_block("advantages")
    _settle()
    sheet.canvas.unpin_block("advantages")
    _settle()

    after = _strip_heights(sheet)
    assert set(after) == set(before)
    assert all(abs(after[key] - before[key]) <= 4 for key in before), (before, after)


def test_a_block_pinned_by_its_button_does_not_take_half_of_the_last_one(sheet) -> None:
    """A drop promises half of its target; the pin button promises nothing of the
    kind, so the newcomer takes a share sized to it out of every block in the strip."""
    before = _strip_heights(sheet)

    sheet.canvas.pin_block("conditions")
    _settle()

    after = _strip_heights(sheet)
    last = sheet.canvas.pinned_keys()[-2]  # the block that used to end the strip
    assert after[last] > before[last] * 0.6


def test_unpinning_and_repinning_puts_a_block_back_where_it_was_in_the_strip(sheet) -> None:
    canvas = sheet.canvas
    order = canvas.pinned_keys()
    before = _strip_heights(sheet)
    first = order[0]

    canvas.unpin_block(first)
    _settle()
    canvas.pin_block(first)
    _settle()

    assert canvas.pinned_keys() == order
    after = _strip_heights(sheet)
    assert abs(after[first] - before[first]) <= 6, (before, after)


# -- the tree half -------------------------------------------------------------------


def test_share_of_reads_a_cells_fraction_of_its_run() -> None:
    row = lt.Split(lt.HORIZONTAL, (lt.Leaf(("a",)), lt.Leaf(("b",))), (300, 100))
    page = lt.Split(lt.VERTICAL, (row, lt.Leaf(("c",))), (0, 0))

    assert lt.share_of(page, "a") == pytest.approx(0.75)
    # A row of its own is not a share of anything on the page...
    assert lt.share_of(page, "c") == 0.0
    # ...but the strip's root is a run.
    assert lt.share_of(row, "b", root_is_run=True) == pytest.approx(0.25)


def test_a_returning_block_takes_its_share_from_the_whole_run() -> None:
    row = lt.Split(lt.HORIZONTAL, (lt.Leaf(("a",)), lt.Leaf(("b",))), (500, 500))
    page = lt.Split(lt.VERTICAL, (row, lt.Leaf(("d",))), (0, 0))

    grown = lt.insert_beside(page, "c", "b", "right", share=1 / 3)

    sizes = grown.children[0].sizes
    assert sizes[0] == sizes[1]  # both gave up the same proportion
    assert abs(sizes[2] - sum(sizes) / 3) <= 1


def test_a_drop_still_halves_its_target() -> None:
    row = lt.Split(lt.HORIZONTAL, (lt.Leaf(("a",)), lt.Leaf(("b",))), (600, 400))
    page = lt.Split(lt.VERTICAL, (row, lt.Leaf(("d",))), (0, 0))

    grown = lt.insert_beside(page, "c", "b", "right")

    assert grown.children[0].sizes == (600, 200, 200)

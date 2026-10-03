"""A click on one card redraws that card's block, not the sheet.

Every runtime click on a power or equipment card ends in a rebuild of that block's
whole card tree, and two faults spread that rebuild across the sheet:

* the new cards were added to an on-screen layout, which only *queues* their show,
  so the first layout pass found the block empty — the row folded to a title bar,
  the page shrank under it and every other row was laid out against the shorter page,
  then all of it came back on the next turn;
* a page whose height moved repainted every block on it, although only the rows
  under the change had moved — and a card easing on or off changes height on every
  frame of the ease.

See :func:`mm_companion.ui.widgets.rebuilding` and ``BlockCanvas.__init__``.
"""

from __future__ import annotations

import pytest
from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtWidgets import QApplication, QWidget

from mm_companion.core.character import Character
from mm_companion.core.data_loader import load_game_data
from mm_companion.core.powers import ModifierSelection, Power, PowerEffectInstance
from mm_companion.ui.character_sheet import CharacterSheet


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


def _settle(times: int = 6) -> None:
    for _ in range(times):
        QApplication.processEvents()


@pytest.fixture
def sheet_and_armor(qapp: QApplication):
    data = load_game_data()
    char = Character.new_default(data)
    for name in ("Blast", "Bolt", "Beam"):
        char.powers.append(Power(name=name, effects=[PowerEffectInstance("damage", rank=4)]))
    armor = Power(
        name="Armor",
        effects=[PowerEffectInstance("protection", rank=6, flaws=[ModifierSelection("removable")])],
    )
    char.powers.append(armor)
    sheet = CharacterSheet(data, char)
    sheet.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
    sheet.resize(1400, 900)
    sheet.show()
    _settle()
    yield sheet, armor
    sheet.hide()
    sheet.deleteLater()
    QApplication.processEvents()


class _Watch(QObject):
    """Records the heights *widget* is resized to, and paints of anything under *root*."""

    def __init__(self, widget: QWidget, root: QWidget) -> None:
        super().__init__()
        self._widget = widget
        self._root = root
        self.heights: list[int] = []
        self.paints = 0

    def eventFilter(self, watched, event) -> bool:  # noqa: ANN001, N802 - Qt override
        if watched is self._widget and event.type() == QEvent.Type.Resize:
            self.heights.append(event.size().height())
        elif event.type() == QEvent.Type.Paint and isinstance(watched, QWidget):
            if watched is self._root or self._root.isAncestorOf(watched):
                self.paints += 1
        return False


def test_a_rebuilt_card_is_shown_before_the_rebuild_returns(sheet_and_armor) -> None:
    """Not on the next turn: a layout measuring in between would find no cards."""
    sheet, armor = sheet_and_armor
    powers = sheet.powers
    powers._on_card_clicked(armor, None, "toggle")
    sheet.bus.flush()
    cards = [widget for _id, widget in powers._list_host._entries]
    assert cards and all(card.isVisible() for card in cards)


def test_a_block_measures_its_cards_the_moment_it_has_rebuilt(sheet_and_armor) -> None:
    """What the next layout pass will read, read before the event loop gets a turn.

    Asked directly rather than by watching the block resize, because whether the
    stale measurement is ever *used* depends on the order Qt happens to queue the
    layout request and the shows in: on a furnished sheet it was, and the page went
    from 3393px to 1987px and back for one click; on this small one it happens not to
    be. A block that measures right cannot fold either way.
    """
    sheet, armor = sheet_and_armor
    cards = sheet.powers._list_host  # its own hint is recomputed, not cached above it
    settled = cards.sizeHint().height()
    sheet.powers._on_card_clicked(armor, None, "toggle")
    sheet.bus.flush()
    # Switching a card off shrinks its type, so the list may lose a few pixels; it
    # used to measure as if it held no cards at all.
    assert cards.sizeHint().height() > settled // 2


def test_a_page_growing_does_not_repaint_the_blocks_above_the_change(sheet_and_armor) -> None:
    """Only what moved or newly appeared is painted: the top row did neither."""
    sheet, _armor = sheet_and_armor
    canvas = sheet.canvas
    top = sheet.block_frame("base_info")
    watch = _Watch(canvas, top)
    QApplication.instance().installEventFilter(watch)
    try:
        canvas.resize(canvas.width(), canvas.height() + 40)
        _settle()
    finally:
        QApplication.instance().removeEventFilter(watch)
    assert watch.paints == 0

"""Where a wheel over a nested widget actually goes.

A block is a scroll area now, so there are two surfaces the wheel could mean and
the guard has to pick. The rule under test is the one a user would state: **the
wheel belongs to the thing it is over, if that thing scrolls at all.**

Which is not the same as "until it runs out". A block at the bottom of its range
keeps the gesture, because the alternative is that wheeling down a squashed block
silently starts moving the whole page underneath a pointer that never left it.
The page is reached by wheeling somewhere that is not a scrolling block.
"""

from __future__ import annotations

import pytest
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import (
    QApplication,
    QLabel,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from mm_companion.ui.block_frame import _InnerScroll
from mm_companion.ui.wheel_guard import guard_wheel, has_scroll_range


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


def _wheel(delta: int) -> QWheelEvent:
    """A plain vertical wheel notch; positive is up, as Qt has it."""
    return QWheelEvent(
        QPointF(4, 4),
        QPointF(4, 4),
        QPoint(0, 0),
        QPoint(0, delta),
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.NoScrollPhase,
        False,
    )


class _Nest(QWidget):
    """A page scroll area holding a block scroll area holding a guarded widget.

    The same shape the sheet really has: ``CharacterSheet``'s page scroll around
    the canvas, a block's ``_InnerScroll`` around its section, and a spin box or
    a table inside that.

    The block is the **real** ``_InnerScroll`` and not a stand-in ``QScrollArea``,
    which is not fussiness: a plain scroll area has none of the wheel behaviour
    under test here, so a double would have gone on passing every assertion below
    while the app misbehaved. It did, once.
    """

    def __init__(self, block_content: int, page_content: int) -> None:
        super().__init__()
        self.page = QScrollArea(self)
        self.page.setWidgetResizable(True)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.page)

        host = QWidget()
        host_layout = QVBoxLayout(host)
        host_layout.setContentsMargins(0, 0, 0, 0)
        self.block = _InnerScroll(host)
        self.block.setFixedHeight(100)
        host_layout.addWidget(self.block)
        # Something under the block, so the page has a range of its own.
        filler = QLabel("filler")
        filler.setFixedHeight(page_content)
        host_layout.addWidget(filler)
        self.page.setWidget(host)

        self.inner = QLabel("inner")
        self.inner.setFixedHeight(block_content)
        self.block.setWidget(self.inner)

        self.guarded = self.inner
        guard_wheel(self.guarded)

        self.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
        self.resize(400, 300)
        self.show()
        QApplication.processEvents()


def test_a_wheel_over_a_nested_widget_scrolls_the_block_it_is_in(qapp: QApplication) -> None:
    nest = _Nest(block_content=600, page_content=800)
    assert nest.block.verticalScrollBar().maximum() > 0

    QApplication.sendEvent(nest.guarded, _wheel(-120))
    QApplication.processEvents()

    assert nest.block.verticalScrollBar().value() > 0
    assert nest.page.verticalScrollBar().value() == 0


def test_the_page_does_not_take_over_when_the_block_runs_out(qapp: QApplication) -> None:
    """The correction. A block that scrolls keeps the wheel while it is under it.

    Chaining to the page here is the behaviour that reads as a bug: nothing about
    the gesture changed, the pointer never left the block, and yet the whole sheet
    starts moving.
    """
    nest = _Nest(block_content=600, page_content=800)
    bar = nest.block.verticalScrollBar()
    bar.setValue(bar.maximum())

    QApplication.sendEvent(nest.guarded, _wheel(-120))
    QApplication.processEvents()

    assert bar.value() == bar.maximum()
    assert nest.page.verticalScrollBar().value() == 0, "the sheet scrolled under the block"


class TestTheBlockKeepsTheGesture:
    """Accepting the wheel, which is the half that actually holds it.

    Whether the page also scrolls is not decided by who *handles* the event but by
    whether the event comes back **accepted**: Qt walks an ignored wheel up the
    parent chain by itself, and the page is on that chain. ``QAbstractSlider``
    ignores a wheel that does not change its value, so a block already at its
    bottom scrolled nothing, handed the event back ignored, and the whole sheet
    moved — with every test here passing, because a *sent* event does not run
    Qt's propagation loop and the assertions were all about scrollbar values.

    So these assert the flag, which is the thing the real behaviour is read off.
    """

    def test_a_block_at_the_end_of_its_range_still_accepts_it(self, qapp) -> None:
        nest = _Nest(block_content=600, page_content=800)
        bar = nest.block.verticalScrollBar()
        bar.setValue(bar.maximum())

        event = _wheel(-120)
        QApplication.sendEvent(nest.block.viewport(), event)

        assert event.isAccepted(), "the sheet would scroll under the block"

    def test_a_block_in_the_middle_of_its_range_accepts_it(self, qapp) -> None:
        nest = _Nest(block_content=600, page_content=800)
        nest.block.verticalScrollBar().setValue(0)

        event = _wheel(-120)
        QApplication.sendEvent(nest.block.viewport(), event)

        assert event.isAccepted()

    def test_a_block_with_no_range_declines_it(self, qapp) -> None:
        """The other half: a block with nothing to scroll must not eat the wheel,
        or the page would stop scrolling everywhere a block covers it."""
        nest = _Nest(block_content=20, page_content=800)

        event = _wheel(-120)
        QApplication.sendEvent(nest.block.viewport(), event)

        assert not event.isAccepted()


def test_a_block_with_nothing_to_scroll_never_takes_the_wheel(qapp: QApplication) -> None:
    nest = _Nest(block_content=20, page_content=800)
    assert nest.block.verticalScrollBar().maximum() == 0

    QApplication.sendEvent(nest.guarded, _wheel(-120))
    QApplication.processEvents()

    assert nest.page.verticalScrollBar().value() > 0


def test_wheeling_back_up_still_moves_the_block(qapp: QApplication) -> None:
    nest = _Nest(block_content=600, page_content=800)
    bar = nest.block.verticalScrollBar()
    bar.setValue(bar.maximum())
    nest.page.verticalScrollBar().setValue(40)

    QApplication.sendEvent(nest.guarded, _wheel(120))
    QApplication.processEvents()

    assert bar.value() < bar.maximum()
    assert nest.page.verticalScrollBar().value() == 40


def test_having_a_range_is_a_question_about_the_surface_not_the_moment(
    qapp: QApplication,
) -> None:
    """Being at the end of a range is not the same as having none."""
    area = QScrollArea()
    area.setWidgetResizable(True)
    inner = QLabel("x")
    inner.setFixedHeight(600)
    area.setWidget(inner)
    area.setFixedHeight(100)
    area.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
    area.show()
    QApplication.processEvents()

    bar = area.verticalScrollBar()
    for value in (bar.minimum(), bar.maximum()):
        bar.setValue(value)
        assert has_scroll_range(area, _wheel(-120)) is True
        assert has_scroll_range(area, _wheel(120)) is True

    empty = QScrollArea()
    empty.setWidgetResizable(True)
    empty.setWidget(QLabel("x"))
    empty.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
    empty.show()
    QApplication.processEvents()
    assert has_scroll_range(empty, _wheel(-120)) is False

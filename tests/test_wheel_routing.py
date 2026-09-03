"""Where a wheel over a nested widget actually goes.

A block is a scroll area now, so there are two surfaces the wheel could mean and
the guard has to pick. The rule under test is the one a user would state: scroll
the block while it has anywhere to go, and the page once it does not.
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

from mm_companion.ui.wheel_guard import can_scroll, guard_wheel


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
        self.block = QScrollArea(host)
        self.block.setWidgetResizable(True)
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


def test_the_page_takes_over_once_the_block_has_run_out(qapp: QApplication) -> None:
    nest = _Nest(block_content=600, page_content=800)
    bar = nest.block.verticalScrollBar()
    bar.setValue(bar.maximum())

    QApplication.sendEvent(nest.guarded, _wheel(-120))
    QApplication.processEvents()

    assert bar.value() == bar.maximum()  # the block had nowhere left to go
    assert nest.page.verticalScrollBar().value() > 0


def test_a_block_with_nothing_to_scroll_never_takes_the_wheel(qapp: QApplication) -> None:
    nest = _Nest(block_content=20, page_content=800)
    assert nest.block.verticalScrollBar().maximum() == 0

    QApplication.sendEvent(nest.guarded, _wheel(-120))
    QApplication.processEvents()

    assert nest.page.verticalScrollBar().value() > 0


def test_wheeling_back_up_returns_to_the_block(qapp: QApplication) -> None:
    """The end of a range is only the end in the direction being pushed."""
    nest = _Nest(block_content=600, page_content=800)
    bar = nest.block.verticalScrollBar()
    bar.setValue(bar.maximum())
    nest.page.verticalScrollBar().setValue(40)

    QApplication.sendEvent(nest.guarded, _wheel(120))
    QApplication.processEvents()

    assert bar.value() < bar.maximum()
    assert nest.page.verticalScrollBar().value() == 40


def test_can_scroll_reads_the_end_of_a_range_as_spent(qapp: QApplication) -> None:
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
    bar.setValue(bar.minimum())
    assert can_scroll(area, _wheel(-120)) is True
    assert can_scroll(area, _wheel(120)) is False

    bar.setValue(bar.maximum())
    assert can_scroll(area, _wheel(-120)) is False
    assert can_scroll(area, _wheel(120)) is True

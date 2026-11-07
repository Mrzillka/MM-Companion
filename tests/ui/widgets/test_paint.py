"""Painting that cannot strand a widget: painters always end, freezes always thaw."""

from __future__ import annotations

import pytest
from PySide6.QtGui import QImage, QPainter

from mm_companion.ui.paint import painting


def test_a_paint_that_raises_still_ends_its_painter(qapp) -> None:
    image = QImage(20, 20, QImage.Format.Format_ARGB32)
    held = {}

    with pytest.raises(RuntimeError):
        with painting(image) as painter:
            held["painter"] = painter
            raise RuntimeError("the paint failed")

    assert not held["painter"].isActive()
    # The device is free for the next paint, which is what a stranded painter stops.
    again = QPainter(image)
    assert again.isActive()
    again.end()


def test_a_rebuild_inside_a_rebuild_paints_again_afterwards(qapp) -> None:
    # The inner rebuild used to restore "updates off" — inherited from the frozen
    # outer one — as the widget's own setting, which Qt then made sticky: the block
    # never painted again.
    from PySide6.QtWidgets import QVBoxLayout, QWidget

    from mm_companion.ui.widgets import rebuilding

    outer = QWidget()
    inner = QWidget()
    QVBoxLayout(outer).addWidget(inner)

    with rebuilding(outer):
        with rebuilding(inner):
            pass

    assert outer.updatesEnabled()
    assert inner.updatesEnabled()


def test_a_rebuild_leaves_a_widget_frozen_on_purpose_frozen(qapp) -> None:
    from PySide6.QtWidgets import QWidget

    from mm_companion.ui.widgets import rebuilding

    widget = QWidget()
    widget.setUpdatesEnabled(False)

    with rebuilding(widget):
        pass

    assert not widget.updatesEnabled()

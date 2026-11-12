"""One way to open a ``QPainter``: one that is always closed again.

``painter = QPainter(self)`` in a ``paintEvent`` is ended by Python dropping the
last reference when the method returns, and that is fine right up until the paint
raises. The exception's traceback then holds the method's frame, the frame holds
the painter, and PySide keeps the traceback (``sys.last_exc``) after reporting it —
so the painter outlives the paint event, still active on a widget Qt has finished
painting. Qt's next pass over that window finds a device already being painted,
and from there the window draws nothing, or worse.

:func:`painting` ends the painter in a ``finally`` whatever happens inside, so a
paint that fails costs one frame of one widget and no more.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from PySide6.QtGui import QPaintDevice, QPainter


@contextmanager
def painting(device: QPaintDevice) -> Iterator[QPainter]:
    """A painter on *device*, ended on the way out even if the body raises."""
    painter = QPainter(device)
    try:
        yield painter
    finally:
        if painter.isActive():
            painter.end()

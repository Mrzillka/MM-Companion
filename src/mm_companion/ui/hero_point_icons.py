"""The hero-point pip artwork, bundled as package data.

The two SVGs live under ``ui/assets/`` (UI assets, not OGL game content) and are
read via :mod:`importlib.resources` so they resolve when the app is installed as
a package — the same arrangement as :mod:`mm_companion.ui.app_icon`.

Rendering is **eager**: the file is parsed inside the ``as_file`` block rather
than handed to Qt as a path. ``QIcon`` reads an SVG lazily, at paint time, by
which point ``as_file``'s temporary extraction of a zipped install is already
gone. Rasterising here also lets a pip be rendered at the screen's device pixel
ratio instead of scaled up from one fixed bitmap.
"""

from __future__ import annotations

from functools import lru_cache
from importlib.resources import as_file, files

from PySide6.QtCore import Qt
from PySide6.QtGui import QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

RESOURCE_PACKAGE = "mm_companion.ui"
FILLED_RESOURCE = "assets/hero_points_on.svg"
EMPTY_RESOURCE = "assets/hero_points_off.svg"


@lru_cache(maxsize=16)
def hero_point_pixmap(filled: bool, size: int, ratio: float = 1.0) -> QPixmap:
    """One pip at *size* logical pixels, rasterised for a screen of *ratio*.

    *filled* picks the lit medallion over the spent grey one. Cached per
    ``(filled, size, ratio)``, so a row of five pips costs two renders.
    """
    resource = files(RESOURCE_PACKAGE).joinpath(FILLED_RESOURCE if filled else EMPTY_RESOURCE)
    pixels = max(1, round(size * ratio))
    pixmap = QPixmap(pixels, pixels)
    pixmap.fill(Qt.GlobalColor.transparent)
    with as_file(resource) as path:
        renderer = QSvgRenderer(str(path))
        painter = QPainter(pixmap)
        renderer.render(painter)
        painter.end()
    pixmap.setDevicePixelRatio(ratio)
    return pixmap

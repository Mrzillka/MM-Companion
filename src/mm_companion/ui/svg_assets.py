"""The bundled SVG artwork — the d20 and the hero-point pips — as pixmaps.

The files live under ``ui/assets/`` (UI assets, not OGL game content) and are read
via :mod:`importlib.resources` so they resolve when the app is installed as a
package — the same arrangement as :mod:`mm_companion.ui.app_icon`.

Rendering is **eager**: the file is parsed inside the ``as_file`` block rather
than handed to Qt as a path. ``QIcon`` reads an SVG lazily, at paint time, by
which point ``as_file``'s temporary extraction of a zipped install is already
gone. Rasterising here also lets a drawing be rendered at the screen's device
pixel ratio instead of scaled up from one fixed bitmap.
"""

from __future__ import annotations

from functools import lru_cache
from importlib.resources import as_file, files

from PySide6.QtCore import QRectF, QSize, QSizeF, Qt
from PySide6.QtGui import QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

RESOURCE_PACKAGE = "mm_companion.ui"

D20_RESOURCE = "assets/D20_icon.svg"
HERO_POINT_FILLED_RESOURCE = "assets/hero_points_on.svg"
HERO_POINT_EMPTY_RESOURCE = "assets/hero_points_off.svg"


@lru_cache(maxsize=16)
def svg_pixmap(resource: str, size: QSize, ratio: float = 1.0) -> QPixmap:
    """Render a bundled SVG to fit *size*, rasterised for a screen of *ratio*.

    The drawing keeps its aspect ratio and is centred in the pixmap. Not every
    asset is square — the d20 is taller than it is wide — and ``QSvgRenderer``
    stretches to whatever rectangle it is handed, so the rectangle is worked out
    here rather than left to it.

    Cached per ``(resource, size, ratio)``, so a row of five pips costs two renders.
    """
    pixels = QSize(max(1, round(size.width() * ratio)), max(1, round(size.height() * ratio)))
    pixmap = QPixmap(pixels)
    pixmap.fill(Qt.GlobalColor.transparent)
    with as_file(files(RESOURCE_PACKAGE).joinpath(resource)) as path:
        renderer = QSvgRenderer(str(path))
        painter = QPainter(pixmap)
        renderer.render(painter, _fitted(renderer.viewBoxF().size(), pixels))
        painter.end()
    pixmap.setDevicePixelRatio(ratio)
    return pixmap


def _fitted(drawing: QSizeF, bounds: QSize) -> QRectF:
    """The rectangle inside *bounds* that *drawing* fills without distorting."""
    size = drawing.scaled(QSizeF(bounds), Qt.AspectRatioMode.KeepAspectRatio)
    return QRectF(
        (bounds.width() - size.width()) / 2,
        (bounds.height() - size.height()) / 2,
        size.width(),
        size.height(),
    )


def hero_point_pixmap(filled: bool, size: int, ratio: float = 1.0) -> QPixmap:
    """One hero-point pip, *size* logical pixels square.

    *filled* picks the lit medallion over the spent grey one.
    """
    resource = HERO_POINT_FILLED_RESOURCE if filled else HERO_POINT_EMPTY_RESOURCE
    return svg_pixmap(resource, QSize(size, size), ratio)

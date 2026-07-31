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

#: The die drawings, by variant id. A theme's ``assets.die`` token names one of
#: these keys — never a path, so a preset cannot point the renderer at an
#: arbitrary file. Which one a preset asks for is the theme's business; *what
#: exists* is this module's, which is why there is no import of ``theme`` here.
DIE_VARIANTS = {
    "classic": "assets/D20_classic.svg",
    "flat": "assets/D20_no_grad.svg",
    "gradient": "assets/D20_grad.svg",
}
DEFAULT_DIE = "flat"

#: The hero-point pips, by variant id, as ``(held, spent)``.
HERO_POINT_VARIANTS = {
    "classic": ("assets/hero_point_classic_on.svg", "assets/hero_point_classic_off.svg"),
    "medallion": ("assets/hero_points_on.svg", "assets/hero_points_off.svg"),
}
DEFAULT_HERO_POINT = "medallion"


def die_resource(variant: str | None = None) -> str:
    """The die drawing for *variant*, falling back to the default on an unknown id."""
    return DIE_VARIANTS.get(variant or "", DIE_VARIANTS[DEFAULT_DIE])


def hero_point_resources(variant: str | None = None) -> tuple[str, str]:
    """The ``(held, spent)`` pips for *variant*, falling back on an unknown id."""
    return HERO_POINT_VARIANTS.get(variant or "", HERO_POINT_VARIANTS[DEFAULT_HERO_POINT])


@lru_cache(maxsize=32)
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


def hero_point_pixmap(
    filled: bool, size: int, ratio: float = 1.0, variant: str | None = None
) -> QPixmap:
    """One hero-point pip, *size* logical pixels square.

    *filled* picks the held pip over the spent one; *variant* picks which drawing
    of the pair, defaulting to the medallion.
    """
    held, spent = hero_point_resources(variant)
    return svg_pixmap(held if filled else spent, QSize(size, size), ratio)

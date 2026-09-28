"""Putting the simple sheet on paper — a printer, a preview, or a PDF.

What is printed is **the simple sheet**, in whichever preset the player has chosen,
without the parts that only mean something on a screen (the dice roller, the Scene).
It is not a screenshot of the window:

* it is a **copy**, built off screen for the purpose (:class:`PrintDocument`): the views
  are built fresh with no section behind them, and every borrowed block is a freshly
  built copy of its section (:meth:`~mm_companion.ui.character_sheet.CharacterSheet.
  build_detached_section`) wired to nothing. So printing never disturbs the sheet being
  played — no borrowed widget is moved, resized or repainted — and the copy can be laid
  out at the width of a page rather than the width of a window;
* it is **light**, whatever the theme. A dark preset printed as it looks on screen is a
  page of black ink. The copy gets a paper palette (:func:`paper_palette`) and every
  colour on the simple sheet is read off the palette or a tinted token, so it follows;
* it **breaks between things**, never through them (:func:`page_breaks`): a page ends
  under the last line, card or box that fits whole, and only a single thing taller than
  a page is ever cut.

Each page is drawn into an image at :data:`PRINT_DPI` and that image is placed on the
page. That is a decision rather than a shortcut: ``QWidget.render`` straight into a
``QPrinter`` goes through an intermediate pixmap *anyway* in this Qt (a bare ``QLabel``
reaches a PDF as a bitmap, not as text), at whatever resolution the printer reports — a
1200 dpi printer made a four-page sheet a 15 MB PDF that took eight seconds. Doing the
rasterising ourselves picks a resolution that prints sharp and stays small.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QMarginsF, QPoint, QRect, Qt
from PySide6.QtGui import (
    QColor,
    QFont,
    QImage,
    QPageLayout,
    QPageSize,
    QPainter,
    QPalette,
    QRegion,
)
from PySide6.QtWidgets import (
    QAbstractButton,
    QAbstractSlider,
    QAbstractSpinBox,
    QApplication,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from mm_companion.core import library
from mm_companion.ui import layout_tree as lt
from mm_companion.ui import theme
from mm_companion.ui.layout_tree import VERTICAL, Leaf, Split
from mm_companion.ui.simple import layout as simple_layout
from mm_companion.ui.simple.registry import SimpleContext, simple_view
from mm_companion.ui.simple.sheet import SimpleBox, SimplePage
from mm_companion.ui.simple.widgets import CaptionBox, SimpleLine, StatBox

#: The width the copy is laid out at, in logical pixels, before it is scaled onto the
#: page. About the width of the printable part of an A4 or Letter page at screen
#: density, so the point sizes on paper come out close to the ones on screen.
PRINT_WIDTH = 760

#: How finely a page is drawn: sharp on paper, and a few hundred kilobytes a page.
PRINT_DPI = 300

#: How much of a page a break may leave empty before it gives up looking for a clean
#: place and cuts: a page that ends a third of the way down because the next box is
#: tall is worse than a box that continues overleaf.
MIN_FILL = 0.35

#: The page margins, in millimetres.
MARGIN_MM = 12.0


def paper_palette() -> QPalette:
    """Black on white, whatever the theme — what a printed sheet is drawn in."""
    palette = QPalette()
    white, black = QColor("#ffffff"), QColor("#000000")
    for group in (
        QPalette.ColorGroup.Active,
        QPalette.ColorGroup.Inactive,
        QPalette.ColorGroup.Disabled,
    ):
        for role, colour in (
            (QPalette.ColorRole.Window, white),
            (QPalette.ColorRole.Base, white),
            (QPalette.ColorRole.AlternateBase, QColor("#f4f4f4")),
            (QPalette.ColorRole.Button, QColor("#f0f0f0")),
            (QPalette.ColorRole.WindowText, black),
            (QPalette.ColorRole.Text, black),
            (QPalette.ColorRole.ButtonText, black),
            (QPalette.ColorRole.BrightText, white),
            (QPalette.ColorRole.Light, white),
            (QPalette.ColorRole.Midlight, QColor("#e3e3e3")),
            (QPalette.ColorRole.Mid, QColor("#a0a0a0")),
            (QPalette.ColorRole.Dark, QColor("#707070")),
            (QPalette.ColorRole.Shadow, QColor("#404040")),
            (QPalette.ColorRole.PlaceholderText, QColor("#6e6e6e")),
            (QPalette.ColorRole.Highlight, QColor("#cfe0f5")),
            (QPalette.ColorRole.HighlightedText, black),
        ):
            palette.setColor(group, role, colour)
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.WindowText, QColor("#777"))
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, QColor("#777"))
    return palette


def print_keys(sheet) -> list[str]:
    """The blocks a print of *sheet* shows, in order: the page, then the strip's."""
    model = sheet.simple_sheet.compute_layout()
    return [key for key in model.keys() if simple_view(key).printable]


def print_tree(sheet) -> Split:
    """The page tree a print lays out: the preset's page, then the strip as rows."""
    model = sheet.simple_sheet.compute_layout()
    printable = {key for key in model.keys() if simple_view(key).printable}
    page = simple_layout.printable_page(model, printable)
    strip = [key for key in lt.keys(model.strip) if key in printable]
    rows = tuple(Leaf((key,)) for key in strip)
    return Split(VERTICAL, page.children + rows) if rows else page


class PrintDocument:
    """An off-screen copy of the simple sheet, laid out at one page's width.

    Build it, :meth:`lay_out` it, ask it for :meth:`page_breaks`, :meth:`render` each
    page, and :meth:`close` it. The root is a real top-level widget that is *shown* —
    the sections inside reflow off their own resize events, which a never-shown widget
    does not get — but with ``WA_DontShowOnScreen``, so nothing ever appears.
    """

    def __init__(self, sheet, width: int = PRINT_WIDTH) -> None:
        self.sheet = sheet
        self.width = width
        self.root = QWidget()
        self.root.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
        self.root.setObjectName("simplePrintRoot")
        self.root.setPalette(paper_palette())
        self.root.setAutoFillBackground(True)
        layout = QVBoxLayout(self.root)
        layout.setContentsMargins(0, 0, 0, 0)
        # A window's layout holds the window at least at the layout's minimum, which
        # sums every wrapped label at its narrowest (see SimplePage.minimumSizeHint);
        # the copy is sized from height-for-width instead, in lay_out.
        layout.setSizeConstraint(QVBoxLayout.SizeConstraint.SetNoConstraint)
        self.page = SimplePage()
        layout.addWidget(self.page)
        self.boxes: dict[str, SimpleBox] = {}
        tree = print_tree(sheet)
        for key in lt.keys(tree):
            box = self._box(key)
            if box is None:
                continue
            empty = getattr(box.body, "is_empty", None)
            if callable(empty) and empty():
                box.deleteLater()  # never shown, never parented: nothing to flash
                continue
            self.boxes[key] = box
        dropped = set(lt.keys(tree)) - set(self.boxes)
        tree = simple_layout.without_keys(simple_layout.SimpleLayout(tree), dropped).page
        self.page.show_tree(tree, self.boxes)
        self.dress()

    def dress(self) -> None:
        """Put every widget of the copy in the paper palette, and make it paint as vectors.

        **Every** widget, not the root alone. With an application stylesheet installed —
        every styled preset has one — Qt marks each widget ``WA_StyleSheet`` and stops
        passing a parent's palette down to its children, so a palette set on the root
        reached nothing and a dark preset printed light grey text on white.

        And no graphics effects. The edit look dims an unselected array alternate and a
        switched-off power through an opacity effect, which paints its subtree through a
        bitmap — at a printer's resolution, a very large one, and text in a PDF that can
        no longer be selected. On paper every power is there to be read anyway.
        """
        from mm_companion.ui.simple.style import TermLabel

        paper = paper_palette()
        widgets = [self.root, *self.root.findChildren(QWidget)]
        for widget in widgets:
            widget.setPalette(paper)
            if widget.graphicsEffect() is not None:
                widget.setGraphicsEffect(None)
        for widget in widgets:
            if isinstance(widget, TermLabel):
                widget.retint()

    def _box(self, key: str) -> SimpleBox | None:
        spec = simple_view(key)
        context = SimpleContext(
            self.sheet.data, self.sheet.character, None, npc=self.sheet.is_npc, key=key
        )
        title = self.sheet.block_frame(key).base_title
        factory = spec.print_factory or spec.factory
        if factory is not None:
            body = factory(context)
        else:
            body = self.sheet.build_detached_section(key)
            if body is None:
                return None
        return SimpleBox(key, title, body, heading=spec.heading)

    def lay_out(self) -> int:
        """Size the copy to its content at :attr:`width`; returns its height."""
        self.root.resize(self.width, 400)
        self.root.show()
        height = 0
        for _ in range(8):
            QApplication.processEvents()
            layout = self.root.layout()
            layout.activate()
            if layout.hasHeightForWidth():
                wanted = layout.totalHeightForWidth(self.width)
            else:
                wanted = layout.totalSizeHint().height()
            wanted = max(wanted, 1)
            if wanted == height and self.root.height() == wanted:
                break
            height = wanted
            self.root.resize(self.width, height)
        QApplication.processEvents()
        # A block that rebuilt itself while it was being laid out made new widgets,
        # and they came out in the application's palette.
        self.dress()
        return self.page.height()

    def page_breaks(self, page_height: int) -> list[tuple[int, int]]:
        """Where each page starts and ends, in the copy's own pixels."""
        return page_breaks(self.page, self.page.height(), page_height)

    def render(self, painter: QPainter, top: int, bottom: int) -> None:
        """Draw the copy's band ``top..bottom`` at the painter's origin."""
        region = QRegion(QRect(0, top, self.page.width(), bottom - top))
        self.page.render(painter, QPoint(0, 0), region, QWidget.RenderFlag.DrawChildren)

    def band_image(self, top: int, bottom: int, dpi: int = PRINT_DPI) -> QImage:
        """The band ``top..bottom`` drawn onto white paper at *dpi*."""
        factor = dpi / max(1, self.root.logicalDpiX())
        width = self.page.width()
        image = QImage(
            max(1, round(width * factor)),
            max(1, round((bottom - top) * factor)),
            QImage.Format.Format_RGB32,
        )
        image.fill(QColor("#ffffff"))
        painter = QPainter(image)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.scale(factor, factor)
        self.render(painter, top, bottom)
        painter.end()
        return image

    def close(self) -> None:
        self.root.hide()
        self.root.deleteLater()


#: The widgets a page may not end in the middle of: every line of text, every control,
#: and the small composite shapes of the simple sheet. A power card too — but only one
#: short enough to move whole to the next page.
_ATOMIC = (QLabel, QAbstractButton, QAbstractSlider, QAbstractSpinBox, StatBox, CaptionBox)
_KEEP_TOGETHER = (SimpleLine,)
#: The power and equipment cards, found by the object name they dress themselves by
#: rather than by class, which would import the card package — and through it the
#: sections — into a module the sections' own package reaches.
_CARD_NAMES = frozenset({"powerCard"})


def _intervals(page: QWidget, page_height: int) -> list[tuple[int, int]]:
    """Every vertical span a page may not end inside, in *page*'s coordinates."""
    spans: list[tuple[int, int]] = []
    for widget in page.findChildren(QWidget):
        if not widget.isVisibleTo(page):
            continue
        top = widget.mapTo(page, QPoint(0, 0)).y()
        together = isinstance(widget, _KEEP_TOGETHER) or widget.objectName() in _CARD_NAMES
        if isinstance(widget, _ATOMIC) or (together and widget.height() < page_height * 0.6):
            spans.append((top, top + widget.height()))
        if isinstance(widget, SimpleBox):
            if widget.height() < page_height * 0.6:
                # A small box moves whole rather than leaving its last lines overleaf.
                spans.append((top, top + widget.height()))
            elif widget.heading is not None:
                # A heading keeps with what it heads: never the last thing on a page.
                heading = widget.heading
                below = heading.mapTo(page, QPoint(0, 0)).y() + heading.height()
                spans.append((top, below + 3 * heading.height()))
    return spans


def page_breaks(page: QWidget, total: int, page_height: int) -> list[tuple[int, int]]:
    """Cut *page* (``total`` pixels tall) into bands no taller than *page_height*.

    Each band ends at the lowest point that falls between things rather than through
    one (see :data:`_ATOMIC`), as long as that leaves the page at least
    :data:`MIN_FILL` full; failing that it is cut at the page's own height.
    """
    if total <= 0 or page_height <= 0:
        return []
    spans = _intervals(page, page_height)
    candidates = sorted({bottom + 1 for _top, bottom in spans} | {total})

    def clear(y: int) -> bool:
        return not any(top < y < bottom for top, bottom in spans)

    bands: list[tuple[int, int]] = []
    start = 0
    while start < total:
        limit = start + page_height
        if limit >= total:
            bands.append((start, total))
            break
        floor = start + int(page_height * MIN_FILL)
        fits = [y for y in candidates if floor < y <= limit and clear(y)]
        end = fits[-1] if fits else limit
        bands.append((start, end))
        start = end
    return bands


@dataclass
class PrintResult:
    """What a print produced — how many pages, for the status bar and the tests."""

    pages: int


def paint_document(sheet, printer) -> PrintResult:
    """Lay the simple sheet out and paint it onto *printer*, page by page."""
    document = PrintDocument(sheet)
    try:
        document.lay_out()
        resolution = printer.resolution()
        paint_rect = printer.pageLayout().paintRectPixels(resolution)
        scale = paint_rect.width() / max(1, document.width)
        footer_font = QFont()
        footer_font.setPointSizeF(theme.font_size("size.simple-term"))
        footer_height = int(footer_font.pointSizeF() * resolution / 72 * 2.2)
        band_height = int((paint_rect.height() - footer_height) / scale)
        bands = document.page_breaks(band_height)
        painter = QPainter()
        if not painter.begin(printer):
            return PrintResult(0)
        try:
            name = library.display_name(sheet.character)
            for index, (top, bottom) in enumerate(bands):
                if index:
                    printer.newPage()
                image = document.band_image(top, bottom)
                target = QRect(0, 0, paint_rect.width(), round((bottom - top) * scale))
                painter.drawImage(target, image)
                painter.setFont(footer_font)
                painter.setPen(QColor("#666666"))
                footer = QRect(
                    0, paint_rect.height() - footer_height, paint_rect.width(), footer_height
                )
                painter.drawText(
                    footer, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom, name
                )
                painter.drawText(
                    footer,
                    Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignBottom,
                    f"{index + 1} / {len(bands)}",
                )
        finally:
            painter.end()
        return PrintResult(len(bands))
    finally:
        document.close()


def make_printer(*, pdf_path: Path | str | None = None):
    """A high-resolution printer on the locale's paper, with the sheet's margins.

    With *pdf_path*, a PDF writer instead of a device.
    """
    from PySide6.QtPrintSupport import QPrinter

    printer = QPrinter(QPrinter.PrinterMode.HighResolution)
    printer.setPageMargins(
        QMarginsF(MARGIN_MM, MARGIN_MM, MARGIN_MM, MARGIN_MM), QPageLayout.Unit.Millimeter
    )
    if pdf_path is not None:
        printer.setOutputFormat(QPrinter.OutputFormat.PdfFormat)
        printer.setPageSize(QPageSize(QPageSize.PageSizeId.A4))
        printer.setOutputFileName(str(pdf_path))
    return printer


def export_pdf(sheet, path: Path | str) -> PrintResult:
    """Write the simple sheet to *path* as a PDF."""
    printer = make_printer(pdf_path=path)
    printer.setDocName(library.display_name(sheet.character))
    return paint_document(sheet, printer)


def print_with_preview(sheet, parent: QWidget | None = None) -> None:
    """Open the print preview, from which the player prints (or cancels)."""
    from PySide6.QtPrintSupport import QPrintPreviewDialog

    printer = make_printer()
    printer.setDocName(library.display_name(sheet.character))
    dialog = QPrintPreviewDialog(printer, parent)
    dialog.setWindowTitle(f"Print — {library.display_name(sheet.character)}")
    dialog.paintRequested.connect(lambda target: paint_document(sheet, target))
    dialog.exec()

"""The simple sheet's typography: big values, quiet game terms, small-caps headings.

A printed character sheet reads in two registers. The numbers a player looks up in the
middle of a fight — a Dodge of 12, a Damage 10 at DC 25 — are large and plain; what
*qualifies* them (Ranged, Standard action, Instant, "Extras: Accurate") is small print
beside them, there to be read on the second glance rather than the first. The edit
sheet says everything at one size because everything on it is being edited. This
module is the second register, so every simple view says it the same way.

Every size here is a theme token, read where it is used (see
:mod:`mm_companion.ui.theme`). The one thing that is *not* a stylesheet is the game
terms' translucency: it is a colour taken off the label's own palette with an alpha
applied (:class:`TermLabel`), so it follows the preset, the OS, and the light palette a
print is made in (:mod:`mm_companion.ui.simple.printing`) without a single colour being
named here.
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import QApplication, QLabel, QSizePolicy, QWidget

from mm_companion.ui import theme


def set_font(
    widget: QWidget,
    size_token: str,
    *,
    bold: bool = False,
    italic: bool = False,
    strike: bool = False,
) -> None:
    """Put *widget*'s type at the point size *size_token* names — on the QFont.

    Never as a stylesheet ``font-size``, which would outrank the widget's own font:
    a power card animates its type through the font when it is switched off, and a
    borrowed card has to keep doing that on the simple sheet.
    """
    font = widget.font()
    font.setPointSizeF(theme.font_size(size_token))
    font.setBold(bold)
    font.setItalic(italic)
    font.setStrikeOut(strike)
    widget.setFont(font)


class TermLabel(QLabel):
    """Small, italic, translucent text: a game term that qualifies a value.

    The translucency is the label's text colour with an alpha of ``opacity.term``,
    derived from whatever text colour its parent would have given it. That is the
    reason this is a class rather than a stylesheet snippet: a colour named in a
    stylesheet is fixed, and a term must follow the palette it lands in — a dark
    preset on screen, and the light one a print is rendered in. So it re-derives on
    every palette or parent change, and never names a colour itself.

    Not a ``QGraphicsOpacityEffect``: an effect paints its subtree through an offscreen
    bitmap, and a PDF of the sheet would then carry its small print as pixels.
    """

    def __init__(self, text: str = "", parent: QWidget | None = None, *, wrap: bool = True) -> None:
        super().__init__(text, parent)
        self._tinting = False
        self.setWordWrap(wrap)
        self.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        set_font(self, "size.simple-term", italic=True)
        if wrap:
            # A wrapped label reports its whole text as a preferred width; a term line
            # has no business deciding how wide its box is.
            self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self._retint()

    def _base_colour(self):
        parent = self.parentWidget()
        source = parent.palette() if parent is not None else QApplication.palette()
        return source.color(QPalette.ColorRole.WindowText)

    def retint(self) -> None:
        """Re-derive the colour from the parent's text colour now."""
        self._retint()

    def _retint(self) -> None:
        if self._tinting:
            return
        self._tinting = True
        try:
            colour = self._base_colour()
            colour.setAlphaF(float(theme.metric("opacity.term")))
            palette = self.palette()
            palette.setColor(QPalette.ColorRole.WindowText, colour)
            palette.setColor(QPalette.ColorRole.Text, colour)
            self.setPalette(palette)
        finally:
            self._tinting = False

    def changeEvent(self, event: QEvent) -> None:  # noqa: N802 - Qt override
        super().changeEvent(event)
        if event.type() in (QEvent.Type.PaletteChange, QEvent.Type.ParentChange):
            self._retint()


def term_label(text: str = "", *, wrap: bool = True) -> TermLabel:
    """A game-term line — see :class:`TermLabel`."""
    return TermLabel(text, wrap=wrap)


def value_label(text: str = "", *, token: str = "size.simple-value") -> QLabel:
    """A key value, big and bold: a total, a bonus, a DC."""
    label = QLabel(text)
    set_font(label, token, bold=True)
    return label


def name_label(text: str = "", *, bold: bool = False, wrap: bool = False) -> QLabel:
    """What a value belongs to — a skill's name, an advantage, a power."""
    label = QLabel(text)
    set_font(label, "size.simple-label", bold=bold)
    if wrap:
        label.setWordWrap(True)
        label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
    return label


def heading_label(text: str) -> QLabel:
    """A box's heading: small, bold, spaced capitals in the accent colour.

    The one place a simple box names itself, standing in for the edit sheet's whole
    title bar. The accent is a stylesheet colour on purpose — it is a token every
    preset defines and holds to the contrast floor on both light and dark windows.
    """
    label = QLabel(text.upper())
    font = label.font()
    font.setPointSizeF(theme.font_size("size.simple-heading"))
    font.setBold(True)
    font.setLetterSpacing(font.SpacingType.PercentageSpacing, 108)
    label.setFont(font)
    label.setStyleSheet(f"color: {theme.color('accent')};")
    label.setObjectName("simpleHeading")
    return label


def tint(label: QLabel, amount: int) -> None:
    """Colour a value by which way something moved it: green up, red down, plain level."""
    if amount > 0:
        label.setStyleSheet(f"color: {theme.color('tint.better')};")
    elif amount < 0:
        label.setStyleSheet(f"color: {theme.color('tint.worse')};")
    else:
        label.setStyleSheet("")


def signed(value: int) -> str:
    """``+4`` / ``-1`` / ``+0`` — how a bonus is written on a sheet."""
    return f"{value:+d}"

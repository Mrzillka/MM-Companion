"""Markdown syntax highlighting for the notes editor.

What this buys, and what it deliberately does not: the *source* stays the
document — every character the user typed is still there, in order — and this
only paints it. Headings come out large and bold, ``**bold**`` bold, code
monospace, links accented. The markers themselves (``##``, ``**``, the backticks)
are painted in ``text.muted.rich``, which reads
as them stepping out of the way, **except on the block the caret is in**, where
they come back to full strength.

That last rule is the whole design. Obsidian *hides* a marker until the caret
enters its line, which Qt has no supported way to do — ``QSyntaxHighlighter``
paints characters, it cannot remove them, and the alternatives (rewriting the
document per paragraph on every cursor move, or a custom
``QAbstractTextDocumentLayout``) cost the native undo stack, selection across
paragraphs, and a relayout per keystroke. Dimming gets most of the same reading
for two :meth:`QSyntaxHighlighter.rehighlightBlock` calls per cursor move, and it
keeps a property real concealment cannot: **the text never reflows under the
caret**, because nothing ever changes width.

Sizes go on the :class:`QTextCharFormat`, never through a stylesheet — the
standing rule for the whole app (see :mod:`mm_companion.ui.theme`).
"""

from __future__ import annotations

import re

from PySide6.QtGui import (
    QColor,
    QFont,
    QFontDatabase,
    QSyntaxHighlighter,
    QTextCharFormat,
    QTextDocument,
)

from mm_companion.ui import theme

#: How many heading levels markdown has, and so how far the size ladder runs.
MAX_HEADING_LEVEL = 6

#: How opaque the wash behind a code span is. Code is told apart by being
#: monospace on a tinted ground rather than by a hue of its own, which is why
#: this module adds no colour token: a hue legible on both a light and a dark
#: window is a hard thing to pick, and it would be one more thing to keep right
#: in every preset.
CODE_WASH_ALPHA = 0.10

# One block-level rule per pattern. Ordered: the first whose pattern matches the
# start of the line owns the line's *base* format, and the inline rules below
# then paint over it.
_HEADING = re.compile(r"^(\s{0,3}#{1,6}\s+)(.*)$")
_QUOTE = re.compile(r"^(\s*>\s?)(.*)$")
_BULLET = re.compile(r"^(\s*(?:[-*+]|\d{1,9}[.)])\s+)")
_TASK = re.compile(r"^\s*[-*+]\s+(\[[ xX]\])\s")
_RULE = re.compile(r"^\s{0,3}(?:(?:-\s*){3,}|(?:\*\s*){3,}|(?:_\s*){3,})$")
_FENCE = re.compile(r"^\s{0,3}(?:```|~~~)")

# Inline rules, as (pattern, marker groups, content group). Each pattern's
# marker groups are dimmed and its content group carries the emphasis.
_INLINE = (
    (re.compile(r"(?<!`)(`+)([^`]+?)(\1)(?!`)"), "code"),
    (re.compile(r"(\*\*\*|___)(?=\S)(.+?)(?<=\S)(\1)"), "bold_italic"),
    (re.compile(r"(\*\*|__)(?=\S)(.+?)(?<=\S)(\1)"), "bold"),
    (re.compile(r"(?<![*\w])(\*|_)(?=\S)([^*_]+?)(?<=\S)(\1)(?![*\w])"), "italic"),
    (re.compile(r"(~~)(?=\S)(.+?)(?<=\S)(~~)"), "strike"),
)

# A link or image: the bracketed text is the accent, everything else is a marker.
_LINK = re.compile(r"(!?\[)([^\]]*)(\]\()([^)\s]*)(\))")

#: The block state marking a line inside a fenced code block, so the fence's
#: effect carries from one block to the next (a QSyntaxHighlighter is only ever
#: handed one line at a time).
_STATE_PLAIN = 0
_STATE_FENCED = 1


class MarkdownHighlighter(QSyntaxHighlighter):
    """Paint markdown source, dimming its markers away from the caret's line."""

    def __init__(self, document: QTextDocument) -> None:
        super().__init__(document)
        self._active_block = -1
        self.reload_theme()

    # -- theme ---------------------------------------------------------------

    def reload_theme(self) -> None:
        """Re-read every token and repaint. Called on a preset change."""
        # ``text.muted.rich`` and not ``text.muted``: the plain one is
        # ``palette(placeholder-text)`` under Classic, a stylesheet expression Qt
        # resolves at paint time, and a QTextCharFormat needs a real colour now.
        # That is exactly what the ``.rich`` variant exists for.
        muted = QColor(theme.color("text.muted.rich"))
        accent = QColor(theme.color("accent"))
        body = theme.font_size("size.notes")
        step = theme.font_size("scale.notes.heading")

        wash = QColor(accent)
        wash.setAlphaF(CODE_WASH_ALPHA)

        self._muted = QTextCharFormat()
        self._muted.setForeground(muted)

        self._heading: list[QTextCharFormat] = []
        for level in range(1, MAX_HEADING_LEVEL + 1):
            fmt = QTextCharFormat()
            fmt.setFontWeight(QFont.Weight.Bold)
            # Level 1 is the largest, level 6 barely above body text.
            fmt.setFontPointSize(body * step ** (MAX_HEADING_LEVEL - level + 1))
            self._heading.append(fmt)

        self._bold = QTextCharFormat()
        self._bold.setFontWeight(QFont.Weight.Bold)

        self._italic = QTextCharFormat()
        self._italic.setFontItalic(True)

        self._bold_italic = QTextCharFormat()
        self._bold_italic.setFontWeight(QFont.Weight.Bold)
        self._bold_italic.setFontItalic(True)

        self._strike = QTextCharFormat()
        self._strike.setFontStrikeOut(True)
        self._strike.setForeground(muted)

        self._code = QTextCharFormat()
        self._code.setFontFamilies([_mono_family()])
        self._code.setBackground(wash)

        self._link = QTextCharFormat()
        self._link.setForeground(accent)
        self._link.setFontUnderline(True)

        self._quote = QTextCharFormat()
        self._quote.setForeground(muted)
        self._quote.setFontItalic(True)

        self._marker_formats = {
            "bold": self._bold,
            "italic": self._italic,
            "bold_italic": self._bold_italic,
            "strike": self._strike,
            "code": self._code,
        }
        self.rehighlight()

    # -- the caret's line ----------------------------------------------------

    def set_active_block(self, number: int) -> None:
        """Take the dimming off block *number* and put it back on the last one.

        Only the two blocks that changed are repainted, so this stays cheap on a
        long note however fast the caret is moving.
        """
        if number == self._active_block:
            return
        previous, self._active_block = self._active_block, number
        document = self.document()
        if document is None:
            return
        for index in (previous, number):
            if index >= 0:
                block = document.findBlockByNumber(index)
                if block.isValid():
                    self.rehighlightBlock(block)

    def _marker(self, base: QTextCharFormat | None = None) -> QTextCharFormat:
        """How a marker is painted on the block being highlighted right now.

        On the caret's own line a marker is left alone — it reads as source,
        which is what someone editing that line wants. Everywhere else it takes
        the muted colour and steps back behind the text it is marking up.
        """
        if self.currentBlock().blockNumber() == self._active_block:
            return base if base is not None else QTextCharFormat()
        fmt = QTextCharFormat(base) if base is not None else QTextCharFormat()
        fmt.setForeground(self._muted.foreground())
        return fmt

    # -- highlighting --------------------------------------------------------

    def highlightBlock(self, text: str) -> None:  # noqa: N802 - Qt override
        fenced = self.previousBlockState() == _STATE_FENCED
        if _FENCE.match(text):
            # The fence line itself is a marker in both directions.
            self.setFormat(0, len(text), self._marker(self._code))
            self.setCurrentBlockState(_STATE_PLAIN if fenced else _STATE_FENCED)
            return
        self.setCurrentBlockState(_STATE_FENCED if fenced else _STATE_PLAIN)
        if fenced:
            self.setFormat(0, len(text), self._code)
            return

        content_start = self._highlight_block_prefix(text)
        self._highlight_inline(text, content_start)

    def _highlight_block_prefix(self, text: str) -> int:
        """Paint whatever opens the line; returns where its content starts."""
        heading = _HEADING.match(text)
        if heading:
            level = min(heading.group(1).strip().count("#"), MAX_HEADING_LEVEL)
            fmt = self._heading[level - 1]
            self.setFormat(0, len(heading.group(1)), self._marker(fmt))
            self.setFormat(heading.end(1), len(heading.group(2)), fmt)
            return heading.end(1)

        if _RULE.match(text):
            self.setFormat(0, len(text), self._marker())
            return len(text)

        quote = _QUOTE.match(text)
        if quote:
            self.setFormat(0, len(quote.group(1)), self._marker(self._quote))
            self.setFormat(quote.end(1), len(quote.group(2)), self._quote)
            return quote.end(1)

        bullet = _BULLET.match(text)
        if bullet:
            self.setFormat(0, len(bullet.group(1)), self._marker())
            task = _TASK.match(text)
            if task:
                self.setFormat(task.start(1), len(task.group(1)), self._marker(self._bold))
                return task.end(1)
            return bullet.end(1)
        return 0

    def _highlight_inline(self, text: str, start: int) -> None:
        for pattern, kind in _INLINE:
            content = self._marker_formats[kind]
            for match in pattern.finditer(text, start):
                self.setFormat(match.start(1), len(match.group(1)), self._marker(content))
                self.setFormat(match.start(2), len(match.group(2)), content)
                self.setFormat(match.start(3), len(match.group(3)), self._marker(content))

        for match in _LINK.finditer(text, start):
            self.setFormat(match.start(1), len(match.group(1)), self._marker())
            self.setFormat(match.start(2), len(match.group(2)), self._link)
            self.setFormat(match.start(3), len(match.group(3)), self._marker())
            self.setFormat(match.start(4), len(match.group(4)), self._marker(self._link))
            self.setFormat(match.start(5), len(match.group(5)), self._marker())


def _mono_family() -> str:
    """The monospace family: the token's, or the platform's own fixed font.

    ``family.mono`` is null in the shipped presets for the reason ``family`` is —
    the platform knows better than a theme file does which fixed-width font it
    actually has.
    """
    family = theme.font_family_mono()
    return family or QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont).family()

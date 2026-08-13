"""One note, in two shapes: editable markdown source, and rendered markdown.

The source side is a :class:`QPlainTextEdit` under
:class:`~mm_companion.ui.notes.highlighter.MarkdownHighlighter` — plain-text
document, native undo stack, and fast on a long file, where a ``QTextEdit``'s
rich document would be doing work nothing here uses. The rendered side is a
:class:`QTextBrowser` fed by Qt's own ``setMarkdown``, so the preview costs no
parser of ours and no dependency.

Both live in a :class:`QStackedWidget` rather than one widget swapping its
content, so flipping back and forth keeps each side's scroll position and the
caret where they were.

**This widget scrolls, and that is the exception it looks like.** Every other
sheet block shows all of its content and lets the page scroll (see
``CLAUDE.md``); a note has no bound, so the block takes a fixed preferred height
and the text scrolls inside it — the same call the roll history and the GM's
Rolls block already make. More room comes from floating or pinning the block.
"""

from __future__ import annotations

from PySide6.QtCore import QTimer, Signal
from PySide6.QtGui import QColor, QFont, QTextCharFormat, QTextCursor, QTextDocument
from PySide6.QtWidgets import (
    QPlainTextEdit,
    QSizePolicy,
    QStackedWidget,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from mm_companion.ui import theme
from mm_companion.ui.lock import set_widget_locked
from mm_companion.ui.notes.highlighter import MarkdownHighlighter
from mm_companion.ui.wheel_guard import guard_wheel

#: How long the editor waits after the last keystroke before it says it changed.
#: Matched to the undo controller's coalescing window so a typed word is one
#: event to whoever is listening, not one per letter.
EDIT_DEBOUNCE_MS = 400

#: What the widget asks for vertically. Not a floor — the block can be made
#: shorter and the text scrolls — just the height a note is worth on a page.
PREFERRED_HEIGHT = 320
MINIMUM_HEIGHT = 90

_SOURCE, _PREVIEW = 0, 1


class NoteEditor(QWidget):
    """A markdown editor with a rendered-preview mode."""

    edited = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        self.source = QPlainTextEdit()
        self.source.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        self.source.setTabChangesFocus(False)
        self.source.setPlaceholderText("Write in markdown…")
        self._highlighter = MarkdownHighlighter(self.source.document())

        self.preview = QTextBrowser()
        self.preview.setOpenExternalLinks(True)

        self._stack = QStackedWidget()
        self._stack.addWidget(self.source)
        self._stack.addWidget(self.preview)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._stack)

        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        guard_wheel(self.source, self.preview)
        self.apply_theme()

        # A note is written in bursts, and every listener downstream (autosave,
        # the block's title, the sheet's dirty flag) wants the burst and not the
        # letters, so one timer restarts on each keystroke and only the pause
        # after the last one is reported.
        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(EDIT_DEBOUNCE_MS)
        self._debounce.timeout.connect(self.edited)

        self._loading = False
        self.source.textChanged.connect(self._on_text_changed)
        self.source.cursorPositionChanged.connect(self._follow_cursor)

    # -- content -------------------------------------------------------------

    def text(self) -> str:
        return self.source.toPlainText()

    def set_text(self, text: str) -> None:
        """Put *text* in the editor without reporting it as an edit.

        Seeding is not typing: an open, a reseed after an undo, or a re-read
        because the file moved underneath must not restart the autosave timer and
        write straight back out again.
        """
        if text == self.source.toPlainText():
            return
        self._loading = True
        try:
            self.source.setPlainText(text)
        finally:
            self._loading = False
        if self._stack.currentIndex() == _PREVIEW:
            self._render()

    def flush(self) -> None:
        """Land a pending edit now, if the debounce is still counting."""
        if self._debounce.isActive():
            self._debounce.stop()
            self.edited.emit()

    def _on_text_changed(self) -> None:
        if not self._loading:
            self._debounce.start()

    def _follow_cursor(self) -> None:
        self._highlighter.set_active_block(self.source.textCursor().blockNumber())

    # -- preview -------------------------------------------------------------

    def is_preview(self) -> bool:
        return self._stack.currentIndex() == _PREVIEW

    def set_preview(self, preview: bool) -> None:
        """Show the rendered note, or go back to the source.

        Rendering happens here rather than on every keystroke: the preview is
        only ever read while it is the visible half, so parsing the note on the
        way in costs one pass instead of one per letter typed.
        """
        if preview == self.is_preview():
            return
        if preview:
            self._render()
        self._stack.setCurrentIndex(_PREVIEW if preview else _SOURCE)

    def _render(self) -> None:
        self.preview.document().setMarkdown(
            self.text(), QTextDocument.MarkdownFeature.MarkdownDialectGitHub
        )
        self._recolour_links()

    def _recolour_links(self) -> None:
        """Repaint the rendered links in the accent, over Qt's hardcoded blue.

        Neither of the two obvious routes works: ``setMarkdown`` bakes
        ``#0000ff`` into each anchor's char format as it parses, so the palette's
        ``Link`` role is never consulted and ``setDefaultStyleSheet`` (which only
        applies to ``setHtml``) never sees the document. Walking the fragments
        afterwards is what is left, and it is cheap — the preview is rendered on
        the toggle, not on every keystroke.
        """
        colour = QColor(theme.color("accent"))
        document = self.preview.document()
        cursor = QTextCursor(document)
        cursor.beginEditBlock()
        block = document.begin()
        while block.isValid():
            iterator = block.begin()
            while not iterator.atEnd():
                fragment = iterator.fragment()
                if fragment.isValid() and fragment.charFormat().isAnchor():
                    cursor.setPosition(fragment.position())
                    cursor.setPosition(
                        fragment.position() + fragment.length(),
                        QTextCursor.MoveMode.KeepAnchor,
                    )
                    tint = QTextCharFormat()
                    tint.setForeground(colour)
                    cursor.mergeCharFormat(tint)
                iterator += 1
            block = block.next()
        cursor.endEditBlock()

    # -- appearance ----------------------------------------------------------

    def apply_theme(self) -> None:
        """Re-read the tokens for both halves. Called on a preset change."""
        font = QFont(self.source.font())
        family = theme.font_family()
        if family:
            font.setFamily(family)
        font.setPointSizeF(theme.font_size("size.notes"))
        self.source.setFont(font)
        self.preview.setFont(font)
        # The document's own default font too: setMarkdown builds its HTML
        # against that, not against the widget's.
        self.preview.document().setDefaultFont(font)
        if self.is_preview():
            self._render()  # the rendered links carry a colour of their own
        self._highlighter.reload_theme()

    def set_locked(self, locked: bool) -> None:
        """Read-only view mode. The preview is already read-only either way."""
        set_widget_locked(self.source, locked)

    # -- geometry ------------------------------------------------------------

    def sizeHint(self):  # noqa: N802 - Qt override
        hint = super().sizeHint()
        hint.setHeight(PREFERRED_HEIGHT)
        return hint

    def minimumSizeHint(self):  # noqa: N802 - Qt override
        """A floor low enough that the block can still be made small.

        The text scrolls, so the honest minimum is "enough to read a line or
        two"; reporting the preferred height here would hold the block — and, in
        the pinned strip, the window — open at 320px forever.
        """
        hint = super().minimumSizeHint()
        hint.setHeight(MINIMUM_HEIGHT)
        return hint


def preview_html(text: str) -> str:
    """*text* rendered to HTML, for a test or a caller with no widget."""
    document = QTextDocument()
    document.setMarkdown(text, QTextDocument.MarkdownFeature.MarkdownDialectGitHub)
    return document.toHtml()


__all__ = ["EDIT_DEBOUNCE_MS", "NoteEditor", "preview_html"]

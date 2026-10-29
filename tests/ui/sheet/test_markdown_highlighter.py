"""The markdown editor: what gets painted, and the caret-line marker rule."""

from __future__ import annotations

from dataclasses import dataclass

import pytest
from PySide6.QtGui import QColor, QFont, QTextCursor
from PySide6.QtWidgets import QApplication

from mm_companion.ui import theme
from mm_companion.ui.notes.editor import NoteEditor, preview_html
from mm_companion.ui.notes.highlighter import MAX_HEADING_LEVEL

SAMPLE = """# Origin story

Bitten by a **radioactive** spider, with `code` and a [link](http://x).

> a quote
- [ ] a task

```python
fenced = 1
```
"""


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


@pytest.fixture
def editor(qapp: QApplication):
    widget = NoteEditor()
    widget.set_text(SAMPLE)
    # Park the caret past every line under test, so the marker-dimming rule is in
    # force everywhere except where a test deliberately puts it.
    widget.source.moveCursor(QTextCursor.MoveOperation.End)
    qapp.processEvents()
    yield widget
    widget.deleteLater()


@dataclass(frozen=True)
class Run:
    """One painted run of a line, read out eagerly.

    Every value is copied out of the ``QTextCharFormat`` here rather than the
    format being kept: ``QTextLayout.formats()`` hands back temporaries, and
    shiboken frees their C++ objects as soon as the list goes out of scope.
    """

    start: int
    length: int
    colour: str
    weight: int
    size: float
    families: tuple[str, ...]

    def covers(self, column: int) -> bool:
        return self.start <= column < self.start + self.length


def runs(editor: NoteEditor, line: int) -> list[Run]:
    """The painted runs of one line of the source editor."""
    block = editor.source.document().findBlockByNumber(line)
    return [
        Run(
            r.start,
            r.length,
            r.format.foreground().color().name(),
            int(r.format.fontWeight()),
            r.format.fontPointSize(),
            tuple(r.format.fontFamilies() or ()),
        )
        for r in block.layout().formats()
    ]


def token_colour(name: str) -> str:
    """A colour token as ``QColor.name()`` gives it back.

    A token's value can be a colour *name* (Classic's ``text.muted.rich`` is
    "gray"), and what is painted is the hex it resolves to — so the two are only
    comparable through QColor.
    """
    return QColor(theme.color(name)).name()


def run_at(editor: NoteEditor, line: int, column: int) -> Run | None:
    return next((r for r in runs(editor, line) if r.covers(column)), None)


def colour_at(editor: NoteEditor, line: int, column: int) -> str:
    run = run_at(editor, line, column)
    return run.colour if run else ""


def size_at(editor: NoteEditor, line: int, column: int) -> float:
    run = run_at(editor, line, column)
    return run.size if run else 0.0


# -- what is painted -----------------------------------------------------------


def test_a_heading_is_bold_and_larger_than_the_body(editor: NoteEditor) -> None:
    body = theme.font_size("size.notes")
    assert size_at(editor, 0, 3) > body
    assert {r.weight for r in runs(editor, 0)} == {int(QFont.Weight.Bold)}


def test_the_heading_ladder_shrinks_with_each_level(qapp: QApplication) -> None:
    widget = NoteEditor()
    widget.set_text("\n".join(f"{'#' * n} H{n}" for n in range(1, MAX_HEADING_LEVEL + 1)))
    widget.source.moveCursor(QTextCursor.MoveOperation.End)
    qapp.processEvents()

    sizes = [size_at(widget, n, n + 2) for n in range(MAX_HEADING_LEVEL)]

    assert sizes == sorted(sizes, reverse=True)
    assert sizes[-1] >= theme.font_size("size.notes")
    widget.deleteLater()


def test_emphasis_is_painted_on_the_content_not_the_markers(editor: NoteEditor) -> None:
    line = 2
    text = editor.source.document().findBlockByNumber(line).text()
    inside = text.index("radioactive") + 2

    run = run_at(editor, line, inside)
    assert run is not None and run.weight == int(QFont.Weight.Bold)


def test_a_link_is_accented_and_its_url_is_not(editor: NoteEditor) -> None:
    line = 2
    text = editor.source.document().findBlockByNumber(line).text()
    assert colour_at(editor, line, text.index("link")) == token_colour("accent")
    assert colour_at(editor, line, text.index("http://x")) == token_colour("text.muted.rich")


def test_a_fenced_block_stays_monospace_across_its_lines(editor: NoteEditor) -> None:
    # A highlighter only ever sees one line, so carrying the fence across them is
    # block state, not a pattern.
    fenced_line = next(
        n
        for n in range(editor.source.document().blockCount())
        if editor.source.document().findBlockByNumber(n).text() == "fenced = 1"
    )
    families = {r.families for r in runs(editor, fenced_line)}
    assert families and all(family for family in families)


# -- the caret-line rule -------------------------------------------------------


def test_markers_are_dimmed_away_from_the_caret(editor: NoteEditor) -> None:
    assert colour_at(editor, 0, 0) == token_colour("text.muted.rich")


def test_markers_come_back_to_full_strength_on_the_caret_line(
    editor: NoteEditor, qapp: QApplication
) -> None:
    editor.source.moveCursor(QTextCursor.MoveOperation.Start)
    qapp.processEvents()

    # One run for the whole line: nothing is dimmed, so the markers are painted
    # exactly like the text they mark up.
    assert len(runs(editor, 0)) == 1
    assert colour_at(editor, 0, 0) != token_colour("text.muted.rich")


def test_moving_the_caret_repaints_the_line_it_left(editor: NoteEditor, qapp: QApplication) -> None:
    editor.source.moveCursor(QTextCursor.MoveOperation.Start)
    qapp.processEvents()
    assert len(runs(editor, 0)) == 1

    editor.source.moveCursor(QTextCursor.MoveOperation.End)
    qapp.processEvents()

    assert len(runs(editor, 0)) > 1  # the ## is dimmed again


def test_the_text_never_reflows_as_the_caret_moves(editor: NoteEditor, qapp: QApplication) -> None:
    # The point of dimming rather than hiding: nothing changes width, so lines do
    # not shift under the caret the way real marker concealment would make them.
    before = editor.source.document().findBlockByNumber(0).text()
    editor.source.moveCursor(QTextCursor.MoveOperation.Start)
    qapp.processEvents()

    assert editor.source.document().findBlockByNumber(0).text() == before


# -- preview -------------------------------------------------------------------


def test_preview_renders_the_markdown_and_edit_keeps_the_source(editor: NoteEditor) -> None:
    editor.set_preview(True)

    assert editor.is_preview()
    assert "Origin story" in editor.preview.toPlainText()
    assert "**radioactive**" not in editor.preview.toPlainText()  # rendered, not source

    editor.set_preview(False)
    assert editor.text() == SAMPLE  # the source is untouched by a round trip


def test_a_rendered_link_takes_the_accent_not_qts_blue(editor: NoteEditor) -> None:
    # setMarkdown bakes #0000ff into each anchor as it parses — the palette's Link
    # role is never consulted — and on a dark preset that is all but unreadable.
    editor.set_preview(True)
    document = editor.preview.document()

    colours = set()
    block = document.begin()
    while block.isValid():
        iterator = block.begin()
        while not iterator.atEnd():
            fragment = iterator.fragment()
            if fragment.isValid() and fragment.charFormat().isAnchor():
                colours.add(fragment.charFormat().foreground().color().name())
            iterator += 1
        block = block.next()

    assert colours == {token_colour("accent")}


def test_preview_supports_the_github_dialect() -> None:
    html = preview_html("- [x] done\n\n| a | b |\n| - | - |\n| 1 | 2 |\n")
    assert "<table" in html


def test_seeding_text_is_not_reported_as_an_edit(qapp: QApplication) -> None:
    # An open, or a reseed after an undo, must not start the autosave timer and
    # write straight back out again.
    widget = NoteEditor()
    edits: list[int] = []
    widget.edited.connect(lambda: edits.append(1))

    widget.set_text("# Seeded\n")
    qapp.processEvents()

    assert edits == []
    assert not widget._debounce.isActive()
    widget.deleteLater()


def test_typing_reports_one_edit_for_a_burst(qapp: QApplication) -> None:
    widget = NoteEditor()
    edits: list[int] = []
    widget.edited.connect(lambda: edits.append(1))

    widget.source.setPlainText("a")
    widget.source.setPlainText("ab")
    widget.source.setPlainText("abc")
    widget.flush()

    assert edits == [1]
    widget.deleteLater()

"""The notes editor: markdown source with a rendered preview.

The widgets the Notes block is built from. The notes themselves — the files, and
what a reference to one means — are :mod:`mm_companion.core.notes`.
"""

from mm_companion.ui.notes.editor import NoteEditor, preview_html
from mm_companion.ui.notes.highlighter import MarkdownHighlighter

__all__ = ["MarkdownHighlighter", "NoteEditor", "preview_html"]

"""What the Settings window expects of a page.

A page is an ordinary ``QWidget`` that also answers four questions: what to call
it in the left-hand list, whether it holds unsaved work, how to keep that work,
and how to throw it away. The window drives those on close and never has to know
what any particular page is about — which is what makes adding a second one an
entry in :data:`~mm_companion.ui.settings.window.PAGES` rather than a rewrite.
"""

from __future__ import annotations

from PySide6.QtWidgets import QWidget


class SettingsPage(QWidget):
    """Base class for one page of the Settings window."""

    #: What the left-hand list calls this page.
    title: str = ""

    def is_dirty(self) -> bool:
        """Whether the page holds edits the user has not saved."""
        return False

    def save(self) -> None:
        """Keep the unsaved edits. Only called when :meth:`is_dirty` said so."""

    def discard(self) -> None:
        """Throw the unsaved edits away.

        Always called as the window closes, saved or not, because a page may be
        showing the user something it has to take back down — a live theme
        preview, say, which would otherwise outlive the window that set it.
        """

    def needs_restart(self) -> bool:
        """Whether what this page did only fully lands after a relaunch."""
        return False

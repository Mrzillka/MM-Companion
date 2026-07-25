"""A sheet section whose caption lives in its block's title bar, not on itself.

Every block already shows its name in the :class:`~mm_companion.ui.block_frame.BlockFrame`
title bar (the drag handle). A section that is also a ``QGroupBox`` with a caption
would print that name a second time just below the bar. To avoid the duplication,
sections drop their own caption and instead report a live title (including the
running point cost) through :attr:`TitledSection.titleChanged`; the enclosing
:class:`BlockFrame` mirrors it into the title bar.

:func:`strip_groupbox_caption` is the same treatment for the plain sections that
have no cost to report (Base Information, Conditions): it clears the caption and
collapses the top margin the caption would have reserved, so the box reads as a
plain bordered panel under the title bar.
"""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QGroupBox, QWidget

from mm_companion.ui.widgets import title_with_cost


def strip_groupbox_caption(box: QGroupBox) -> None:
    """Hide *box*'s own caption so it doesn't duplicate the block title bar.

    A ``QGroupBox`` reserves a top margin for its caption and draws the caption
    overlapping the border; with the caption gone that leaves an empty band, so
    collapse the margin and zero the title sub-control too. The stylesheet is
    scoped to this exact box by object name, so a nested captioned group box (e.g.
    Base Info's "Details") keeps its own caption.
    """
    box.setTitle("")
    name = box.objectName() or f"captionless_{id(box):x}"
    box.setObjectName(name)
    box.setStyleSheet(
        f"QGroupBox#{name} {{ margin-top: 0px; }}"
        f"QGroupBox#{name}::title {{ width: 0; height: 0; margin: 0; padding: 0; }}"
    )


class TitledSection(QGroupBox):
    """A captionless section that reports its title (with cost) to its block frame.

    Subclasses call :meth:`set_block_title` wherever they would have called
    ``setTitle``; the :class:`BlockFrame` connects :attr:`titleChanged` and reads
    :meth:`block_title` for the initial value.
    """

    titleChanged = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        strip_groupbox_caption(self)
        self._block_title = ""
        self._npc = False
        self._plain_title = ""
        self._cost_points: int | None = None

    def set_block_title(self, text: str) -> None:
        """Record and broadcast the block's title (e.g. ``"Abilities — 0 PP"``)."""
        self._block_title = text
        self.titleChanged.emit(text)

    def set_priced_title(self, title: str, points: int) -> None:
        """Set the block title with its running PP cost — omitted for NPCs.

        A GM's NPC carries no point budget, so its priced blocks show a plain caption
        (``"Abilities"``) rather than a ``"— N PP"`` subtotal. Subclasses call this in
        place of ``set_block_title(title_with_cost(...))``; NPC mode is a live toggle
        (:meth:`set_npc_mode`), so the last title/points are remembered to re-render.
        """
        self._plain_title = title
        self._cost_points = points
        self._render_priced_title()

    def set_npc_mode(self, npc: bool) -> None:
        """Show or hide the PP subtotal in this block's title (NPCs hide it)."""
        if npc == self._npc:
            return
        self._npc = npc
        if self._cost_points is not None:
            self._render_priced_title()

    def _render_priced_title(self) -> None:
        if self._npc or self._cost_points is None:
            self.set_block_title(self._plain_title)
        else:
            self.set_block_title(title_with_cost(self._plain_title, self._cost_points))

    def block_title(self) -> str:
        """The most recently set block title (``""`` until one is set)."""
        return self._block_title

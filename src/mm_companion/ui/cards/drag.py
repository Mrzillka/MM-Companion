"""The grip a card is picked up by, and the drag payload it carries."""

from __future__ import annotations

from PySide6.QtWidgets import QWidget

from mm_companion.ui.widgets import DragHandle as _Grip

#: Drag-and-drop payload: the dragged node's stable id (a ``Power.id`` or
#: ``PowerGroup.id``). A tree position needs parent context, not a bare index, so
#: drops resolve the id.
#:
#: Every widget in this package takes the format as a parameter defaulting to this
#: one, so a second block built on the same cards (equipment) names its own and the
#: two boards cannot accept each other's drags.
NODE_MIME = "application/x-mm-power-node"


class DragHandle(_Grip):
    """The shared ``⠿`` grip (:class:`~mm_companion.ui.widgets.DragHandle`), saying
    what a drag does on the sheet: a card dropped onto another power groups them."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("Drag to reorder, or drop onto another power to group them", parent)

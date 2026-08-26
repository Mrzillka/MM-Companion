"""The chip-sized controls a card is mostly made of.

Three cards draw these now — a player's, an NPC's and a scene entry's — which is
one more than the two that made it reasonable for them to live on
:mod:`~mm_companion.ui.player_card`. They are here rather than there for a second
reason as well: a scene card is reachable from ``ui/sections/`` (the Scene block
imports it), and ``player_card`` imports *out* of ``ui/sections/`` for its
condition catalogue — so a scene card that reached back into ``player_card``
closed a loop that Python will not import. This module imports nothing from
either, which is what keeps the two directions apart.
"""

from __future__ import annotations

from PySide6.QtCore import QMimeData, QPoint, Qt
from PySide6.QtGui import QDrag
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QToolButton, QWidget

from mm_companion.ui import theme
from mm_companion.ui.widgets import attach_context_removal

#: What a dragged card reference is called on the clipboard. One format for
#: every board, because the drop is the same question everywhere: put this
#: reference here. See :mod:`~mm_companion.ui.card_drop`.
SCENE_MIME = "application/x-mm-scene-ref"


def start_card_drag(widget, ref: str) -> None:
    """Begin a real :class:`QDrag` carrying *ref*, with the card as its ghost.

    Shared by the two roster cards and the scene card, because the gesture has to
    be the same object to cross between their boards: a pseudo-drag that tracks
    the pointer itself works inside one container and cannot leave it.
    """
    drag = QDrag(widget)
    mime = QMimeData()
    mime.setData(SCENE_MIME, ref.encode("utf-8"))
    drag.setMimeData(mime)
    pixmap = widget.grab()
    drag.setPixmap(pixmap)
    drag.setHotSpot(QPoint(pixmap.width() // 2, 12))
    drag.exec(Qt.DropAction.MoveAction)


#: What the Scene toggle wears. An eye, because what it controls is who can *see*
#: this creature — the players' side of the board, not the GM's.
SCENE_GLYPH = "👁"


class _SceneEye(QToolButton):
    """The 👁 that puts a creature on the shared board, and takes it back off.

    A toggle whose *state* is the readout: lit means the table can see this
    creature, unlit means only the GM can. It carries the accent rather than a
    check mark for the reason the initiative badge does — there is no room on a
    210px card for a control that also draws a box around itself.

    Being a ``QToolButton`` it swallows its own press, which is what stops a click
    here being read as the start of the card's drag.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setText(SCENE_GLYPH)
        self.setAutoRaise(True)
        self.setCheckable(True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        size = int(theme.metric("gm.damage-button"))
        self.setFixedSize(size, size)
        font = self.font()
        font.setPointSizeF(theme.font_size("size.terms"))
        self.setFont(font)
        self.set_in_scene(False)

    def set_in_scene(self, on: bool) -> None:
        """Show whether this creature is on the board, without raising ``toggled``."""
        blocked = self.blockSignals(True)
        self.setChecked(on)
        self.blockSignals(blocked)
        self._restyle(on)

    def _restyle(self, on: bool) -> None:
        colour = theme.color("accent") if on else theme.color("text.muted")
        self.setStyleSheet(f"QToolButton {{ color: {colour}; border: none; }}")
        self.setToolTip(
            "On the Scene — the players can see this. Click to take it off."
            if on
            else "Put this on the Scene, where the players can see it."
        )


class _ConditionChip(QFrame):
    """One condition, as a compact chip a right-click takes off.

    The removal used to be a "×" on the chip. It is a right-click now, everywhere a
    condition chip appears: the button was a third of the width of a short caption
    like "Hit ×3", on the one part of a collapsed card that has to hold several of
    them. See :func:`~mm_companion.ui.widgets.attach_context_removal` for the trade
    that makes, and for why the tooltip has to say so.

    *compact* is the collapsed GM card's version: the same chip in small print, so a
    creature carrying five conditions still costs one line of a card that is mostly
    pinned numbers. Only the type size and the padding change — a chip that reads
    differently in the two states would be a second thing to recognise.
    """

    def __init__(
        self,
        text: str,
        *,
        tooltip: str = "",
        compact: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setStyleSheet(
            f"border: {int(theme.metric('border.width'))}px solid"
            f" {theme.color('tint.worse')};"
            f"background: {theme.wash('tint.worse', 0.12)};"
            f"border-radius: {int(theme.metric('radius.chip'))}px;"
        )
        if tooltip:
            self.setToolTip(tooltip)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(*((3, 0, 1, 0) if compact else (5, 1, 2, 1)))
        layout.setSpacing(1 if compact else 2)
        self._label = QLabel(text)
        self._label.setStyleSheet("border: none; background: transparent;")
        if compact:
            # On the QFont, never in the sheet above: a stylesheet ``font-size``
            # outranks a widget's font everywhere in this app.
            font = self._label.font()
            font.setPointSizeF(theme.font_size("size.terms"))
            self._label.setFont(font)
        layout.addWidget(self._label)
        self._compact = compact
        self._removable = False

    def text(self) -> str:
        """The chip's caption — what :meth:`PlayerCard.condition_names` reads."""
        return self._label.text()

    @property
    def removable(self) -> bool:
        """Whether a right-click here would take this condition off.

        False on an offline player's chips: their card is a snapshot of somebody
        else's sheet, and there is nobody to send the command to.
        """
        return self._removable

    def arm_removal(self, on_remove) -> None:
        """Let a right-click ask for this condition to come off."""
        attach_context_removal(self, on_remove, what=self.text())
        self._removable = True

    def contextMenuEvent(self, event) -> None:  # noqa: N802 (Qt override)
        """Swallow a right-click that removal did not claim.

        Reached only on a chip that cannot be removed — an offline player's, whose
        app there is nobody to send the command to. Doing nothing is the point:
        left to propagate, the click would reach the *card* and offer to remove the
        player, which is not what someone aiming at a chip meant to ask for.
        """
        event.accept()

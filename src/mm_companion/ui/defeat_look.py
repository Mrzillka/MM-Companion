"""How a creature that is out of the fight looks: dimmed, with a grey portrait.

The GM's NPC cards, a group's rows and every seat's Scene cards all show it, read off
:func:`~mm_companion.core.rules.defeat_state` — down (Incapacitated, Dying) fades to the
``opacity.down`` token, dead further to ``opacity.dead``.

A fade is a :class:`QGraphicsOpacityEffect`, never ``setEnabled(False)``: a downed
creature's card is still the GM's to use (to remove the condition, to roll its
recovery), and a disabled widget greys almost nothing anyway, since stylesheet colours
outrank the disabled palette. An effect makes its whole subtree paint through an
offscreen buffer, so it is attached only while the creature is down and taken off the
moment it is up again (the ``ui/cards/card.py`` ``_dim`` rule).
"""

from __future__ import annotations

from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import QGraphicsOpacityEffect, QWidget

from mm_companion.core.rules import DEFEAT_DEAD, DEFEAT_DOWN
from mm_companion.ui import theme

#: The opacity token each defeat state fades to.
_OPACITY_TOKENS = {DEFEAT_DOWN: "opacity.down", DEFEAT_DEAD: "opacity.dead"}


def defeat_opacity(state: str) -> float | None:
    """The opacity a card in *state* is drawn at, or ``None`` when it is not faded."""

    token = _OPACITY_TOKENS.get(state)
    return None if token is None else float(theme.metric(token))


def apply_defeat_look(widget: QWidget, state: str) -> None:
    """Fade *widget* for *state*, or take the fade off when the creature is up."""

    opacity = defeat_opacity(state)
    effect = widget.graphicsEffect()
    if opacity is None:
        if isinstance(effect, QGraphicsOpacityEffect):
            widget.setGraphicsEffect(None)
        return
    if not isinstance(effect, QGraphicsOpacityEffect):
        effect = QGraphicsOpacityEffect(widget)
        widget.setGraphicsEffect(effect)
    effect.setOpacity(opacity)


def greyscale(pixmap: QPixmap) -> QPixmap:
    """*pixmap* in shades of grey, its transparency kept (the NPC icons have alpha)."""

    if pixmap.isNull():
        return pixmap
    source = pixmap.toImage().convertToFormat(QImage.Format.Format_ARGB32)
    grey = source.convertToFormat(QImage.Format.Format_Grayscale8).convertToFormat(
        QImage.Format.Format_ARGB32
    )
    grey.setAlphaChannel(source.convertToFormat(QImage.Format.Format_Alpha8))
    result = QPixmap.fromImage(grey)
    result.setDevicePixelRatio(pixmap.devicePixelRatio())
    return result


def defeated_pixmap(pixmap: QPixmap | None, state: str) -> QPixmap | None:
    """The portrait to show for *state*: grey while the creature is down or dead."""

    if pixmap is None or pixmap.isNull() or state not in _OPACITY_TOKENS:
        return pixmap
    return greyscale(pixmap)

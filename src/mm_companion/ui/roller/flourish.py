"""The flourish round the die when it lands on a natural 20 or a natural 1.

A glow in the outcome's colour was all a natural roll got, and in the Crimson & Gold
theme the natural-20 gold was all but the gold a loaded trait already lights the die
with — so a 20 looked like an armed die (the user's report, 2026-11-10). A natural roll
now gets a moment of its own, every part of it drawn over the layout so nothing moves:

- :class:`DieBurst` — a transparent overlay over the die that paints, for about a
  second, rays and sparks bursting out of a natural 20, or a ring that breaks outward
  into shards for a natural 1, in the ``dice.critical`` / ``dice.fumble`` colour;
- :func:`pulse_halo` — the die's halo swells and settles a few times (a natural 20);
- :func:`shake` — the die judders side to side (a natural 1).

The panel (``ui/dice_roller.py``) decides *when*; this module only draws. Every
animation is parented to the widget it moves, so closing the roller stops it.
"""

from __future__ import annotations

import math

from PySide6.QtCore import QEasingCurve, QPoint, QPointF, QRect, QSize, Qt, QVariantAnimation
from PySide6.QtGui import QColor, QPainterPath, QPen
from PySide6.QtWidgets import QGraphicsDropShadowEffect, QWidget

from mm_companion.ui import theme
from mm_companion.ui.paint import painting
from mm_companion.ui.roll_history import NATURAL_ONE, NATURAL_TOKENS, NATURAL_TWENTY

#: How long the burst round the die is drawn for.
BURST_MS = 1100
#: How many rays a natural 20 bursts into, and how many shards a natural 1 breaks into.
RAYS = 12
SHARDS = 9
#: How far past the die the burst reaches, as a multiple of the die's size.
BURST_REACH = 1.7
#: How many times the halo swells on a natural 20, and how long each swell takes.
PULSES = 3
PULSE_MS = 420
#: How far the halo swells, as a multiple of its resting spread.
PULSE_SWELL = 1.6
#: How long a natural 1 shakes the die, and how far either way.
SHAKE_MS = 520
SHAKE_PIXELS = 6


def natural_colour(kind: str) -> QColor:
    """The colour of a natural roll's flourish, falling back to red for a palette role."""
    colour = QColor(theme.color(NATURAL_TOKENS[kind]))
    return colour if colour.isValid() else QColor(Qt.GlobalColor.red)


class DieBurst(QWidget):
    """The burst over the die (see the module docstring).

    A child of the panel, laid over the die by :meth:`play` and as wide as
    :data:`BURST_REACH` dice, so the rays reach past the die's own frame; it lets
    every click through and hides itself when the burst is done. It is hidden in the
    constructor and only ever shown once parented, like every widget here.
    """

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        self.hide()
        self.kind: str | None = None
        self._progress = 0.0
        self._animation = QVariantAnimation(self)
        self._animation.setStartValue(0.0)
        self._animation.setEndValue(1.0)
        self._animation.setDuration(BURST_MS)
        self._animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._animation.valueChanged.connect(self._step)
        self._animation.finished.connect(self.stop)

    @property
    def running(self) -> bool:
        return self._animation.state() == QVariantAnimation.State.Running

    def play(self, kind: str, over: QWidget) -> None:
        """Burst *kind* (a natural 20 or 1) out of the widget *over*, the die."""
        parent = self.parentWidget()
        if parent is None:
            return
        centre = over.mapTo(parent, over.rect().center())
        side = int(max(over.width(), over.height()) * BURST_REACH)
        self.setGeometry(QRect(centre - QPoint(side // 2, side // 2), QSize(side, side)))
        self.kind = kind
        self._progress = 0.0
        self.raise_()
        self.show()
        self._animation.stop()
        self._animation.start()

    def stop(self) -> None:
        """End the burst now (another roll has started)."""
        self._animation.stop()
        self.kind = None
        self.hide()

    def _step(self, value: object) -> None:
        self._progress = float(value)
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt override
        if self.kind is None:
            return
        with painting(self) as painter:
            painter.setRenderHint(painter.RenderHint.Antialiasing)
            colour = natural_colour(self.kind)
            fade = max(0.0, 1.0 - self._progress)
            colour.setAlphaF(fade)
            centre = QPointF(self.width() / 2, self.height() / 2)
            radius = min(self.width(), self.height()) / 2
            # The rays start at the die's own edge, which is half the burst's reach.
            inner = radius / BURST_REACH
            if self.kind == NATURAL_TWENTY:
                self._paint_rays(painter, colour, centre, inner, radius)
            elif self.kind == NATURAL_ONE:
                self._paint_shards(painter, colour, centre, inner, radius)

    def _paint_rays(self, painter, colour: QColor, centre: QPointF, inner, radius) -> None:
        """Rays bursting out of the die, with a spark at each tip."""
        reach = inner + (radius - inner) * self._progress
        width = max(1.0, float(theme.metric("border.width.emphasis")) * (1.5 - self._progress))
        painter.setPen(QPen(colour, width, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        for index in range(RAYS):
            # Every other ray is shorter, so the burst reads as a star rather than a wheel.
            angle = 2 * math.pi * index / RAYS
            length = reach if index % 2 == 0 else inner + (reach - inner) * 0.6
            start = inner * 0.85
            painter.drawLine(
                centre + QPointF(math.cos(angle) * start, math.sin(angle) * start),
                centre + QPointF(math.cos(angle) * length, math.sin(angle) * length),
            )
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(colour)
        spark = max(1.5, width * 1.4)
        for index in range(0, RAYS, 2):
            angle = 2 * math.pi * index / RAYS + math.pi / RAYS
            at = centre + QPointF(math.cos(angle) * reach * 0.9, math.sin(angle) * reach * 0.9)
            painter.drawEllipse(at, spark, spark)

    def _paint_shards(self, painter, colour: QColor, centre: QPointF, inner, radius) -> None:
        """A ring that breaks outward from the die into falling shards."""
        width = max(1.0, float(theme.metric("border.width.emphasis")))
        ring = inner + (radius - inner) * self._progress * 0.5
        painter.setPen(QPen(colour, width * (1.0 + self._progress)))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(centre, ring, ring)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(colour)
        reach = inner + (radius - inner) * self._progress
        drop = radius * 0.35 * self._progress**2
        size = inner * 0.22
        for index in range(SHARDS):
            angle = 2 * math.pi * (index + 0.3) / SHARDS
            at = centre + QPointF(math.cos(angle) * reach, math.sin(angle) * reach + drop)
            path = QPainterPath()
            # A thin jagged triangle pointing away from the die, turning as it falls.
            spin = angle + self._progress * (1 if index % 2 else -1) * math.pi
            for corner, scale in ((0.0, 1.0), (2.5, 0.45), (3.8, 0.6)):
                turn = spin + corner
                point = at + QPointF(math.cos(turn) * size * scale, math.sin(turn) * size * scale)
                if path.elementCount() == 0:
                    path.moveTo(point)
                else:
                    path.lineTo(point)
            path.closeSubpath()
            painter.drawPath(path)


def pulse_halo(widget: QWidget) -> QVariantAnimation | None:
    """Swell *widget*'s halo (its drop-shadow effect) :data:`PULSES` times and let it
    settle. ``None`` when it has no halo to swell. The animation is the widget's
    child, so the next halo the panel sets simply takes over."""
    effect = widget.graphicsEffect()
    if not isinstance(effect, QGraphicsDropShadowEffect):
        return None
    rest = effect.blurRadius()
    animation = QVariantAnimation(widget)
    animation.setStartValue(0.0)
    animation.setEndValue(float(PULSES))
    animation.setDuration(PULSES * PULSE_MS)

    def swell(value: object) -> None:
        current = widget.graphicsEffect()
        if current is not effect:
            # The panel lit a new halo (the glow ran out, another roll began): stop.
            animation.stop()
            return
        wave = math.sin(math.pi * (float(value) % 1.0))
        effect.setBlurRadius(rest * (1.0 + (PULSE_SWELL - 1.0) * wave))

    animation.valueChanged.connect(swell)
    animation.finished.connect(
        lambda: effect.setBlurRadius(rest) if widget.graphicsEffect() is effect else None
    )
    animation.start(QVariantAnimation.DeletionPolicy.DeleteWhenStopped)
    return animation


def shake(widgets: list[QWidget]) -> QVariantAnimation | None:
    """Judder *widgets* side to side for :data:`SHAKE_MS`, then put them back.

    They are moved by hand, under their layout's nose; the layout is asked to place
    them again at the end, so a resize mid-shake costs nothing worse than a judder
    cut short.
    """
    widgets = [w for w in widgets if w is not None and w.parentWidget() is not None]
    if not widgets:
        return None
    homes = [w.pos() for w in widgets]
    owner = widgets[0]
    animation = QVariantAnimation(owner)
    animation.setStartValue(0.0)
    animation.setEndValue(1.0)
    animation.setDuration(SHAKE_MS)

    def judder(value: object) -> None:
        progress = float(value)
        offset = round(SHAKE_PIXELS * (1.0 - progress) * math.sin(progress * math.pi * 8))
        for widget, home in zip(widgets, homes, strict=True):
            widget.move(home.x() + offset, home.y())

    def settle() -> None:
        for widget, home in zip(widgets, homes, strict=True):
            widget.move(home)
            layout = widget.parentWidget().layout() if widget.parentWidget() else None
            if layout is not None:
                layout.invalidate()

    animation.valueChanged.connect(judder)
    animation.finished.connect(settle)
    animation.start(QVariantAnimation.DeletionPolicy.DeleteWhenStopped)
    return animation

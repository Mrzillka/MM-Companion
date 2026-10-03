"""A row of clickable pips for the rank a power is held at — the card's rank scale.

A power card's rank and share dials used to be a ``QSlider``. A slider is a control
for a *continuous* quantity, and a rank is a short ladder of distinct rungs the player
picks one of mid-fight; its groove and handle also looked out of place among the
card's chips and pips. So the ladder is drawn as what it is: one pip per notch, filled
up to the notch the effect is held at, and a click on a pip is the whole gesture.

**Clicking the last lit pip puts that pip out** — the effect drops a rank — so the scale
needs no separate Off control: on a power held at rank 1 the same click switches it off.
Every other pip is a jump straight to that notch. A click therefore always changes
something, which is what keeps a rebuild-per-click from ever being spent on nothing.

It is **one painted widget** rather than a button per pip, for the same reason the
slider was one: the card it sits on is rebuilt on every runtime change, and a Damage 15
would otherwise be sixteen widgets torn down and re-made per click.

What it draws, left to right:

* a **filled** pip for every notch up to the current one;
* a **hollow** pip for every notch above it the player may pick;
* a **dotted** pip for a notch that exists but cannot be reached right now — a Dynamic
  member's rungs its siblings' shares have spent (:meth:`set_ceiling`);
* a **wider gap** where the notches' names change, but only on a ladder some of whose
  names repeat: a Growth spends several ranks at one size, and the gaps are what make
  "Gargantuan" a visible stretch of the scale. A ladder whose every notch is its own
  name (a Damage's plain ranks) has nothing to group. It is a gap rather than a drawn
  line because on a size ladder most sizes are one rank wide, and a line beside every
  one of those read as noise.

Hovering a reachable pip previews what a click there would do — the pips that would
light wash in, the ones that would go out fade — and reports the notch it would land on
through :attr:`hovered`, so the owner can name it before it is chosen. Nothing is
committed until a click.

**Width adapts; height does not.** Narrower than its natural width the pips shrink
toward a floor and past it clip; nothing here reports a minimum width, since whether a
card is too narrow to read is the player's call (``CLAUDE.md``). Shrinking happens in
the paint, so it never relays anything out and needs no reflow guard.

Notch ``0`` — off — is not a pip: it is where the scale stands with every pip hollow.
"""

from __future__ import annotations

from PySide6.QtCore import QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QMouseEvent, QPainter, QPaintEvent, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

from mm_companion.ui import theme

#: The smallest a pip is drawn before the row stops shrinking and starts to clip.
_FLOOR = 4


class RankPips(QWidget):
    """Pips ``1..count``, filled up to :meth:`value`; clicking one emits :attr:`picked`.

    *names* is what each notch is called, indexed like the pips, and decides only where
    the wider gaps go (see the module docstring). ``interactive=False`` draws the same row
    with no cursor, hover or click.
    """

    picked = Signal(int)  #: the notch a click lands on, ``0..ceiling``
    #: The notch a click under the pointer would land on, or ``-1`` once it leaves.
    hovered = Signal(int)

    def __init__(
        self,
        count: int,
        value: int,
        names: dict[int, str] | None = None,
        interactive: bool = True,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._count = max(0, count)
        self._value = max(0, min(value, self._count))
        self._ceiling = self._count
        self._hover = -1
        self._pressed = -1
        self._interactive = interactive
        self._breaks = self._group_breaks(names or {})
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setMouseTracking(interactive)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, not interactive)

    # -- state ---------------------------------------------------------------

    def count(self) -> int:
        return self._count

    def value(self) -> int:
        return self._value

    def set_value(self, value: int) -> None:
        self._value = max(0, min(value, self._count))
        self.update()

    def ceiling(self) -> int:
        return self._ceiling

    def set_ceiling(self, ceiling: int) -> None:
        """The highest notch that may be picked; the pips above it are drawn dotted."""
        self._ceiling = max(0, min(ceiling, self._count))
        if self._hover > self._ceiling:
            self._set_hover(-1)
        self.update()

    def _group_breaks(self, names: dict[int, str]) -> set[int]:
        """The notches a wider gap opens *before*: where a repeating name changes."""
        run = [names.get(i, "") for i in range(1, self._count + 1)]
        if len(set(run)) == len(run):
            return set()  # every notch its own name: nothing to group
        return {i + 1 for i in range(1, len(run)) if run[i] != run[i - 1]}

    # -- geometry ------------------------------------------------------------

    def _metrics(self) -> tuple[float, float, float]:
        """``(pip, gap, group gap)`` widths: the token's, unless the row has to shrink."""
        pip = float(theme.metric("column.rank-pip"))
        gap = float(theme.metric("space.xs"))
        group_gap = float(theme.metric("space.md"))
        natural = self._natural(pip, gap, group_gap)
        if natural > self.width() > 0 and self._count:
            scale = self.width() / natural
            pip = max(_FLOOR, pip * scale)
            gap, group_gap = gap * scale, group_gap * scale
        return pip, gap, group_gap

    def _natural(self, pip: float, gap: float, group_gap: float) -> float:
        if not self._count:
            return 0.0
        return self._count * pip + (self._count - 1) * gap + len(self._breaks) * group_gap

    def _rects(self) -> list[QRectF]:
        """The square each pip is drawn in, for notches ``1..count`` in order."""
        pip, gap, group_gap = self._metrics()
        top = (self.height() - pip) / 2
        rects: list[QRectF] = []
        x = 0.0
        for notch in range(1, self._count + 1):
            if notch in self._breaks:
                x += group_gap
            rects.append(QRectF(x, top, pip, pip))
            x += pip + gap
        return rects

    def _notch_at(self, x: float) -> int:
        """The notch whose pip — or the half-gaps either side of it — holds *x*, or -1."""
        rects = self._rects()
        for index, rect in enumerate(rects):
            left = rect.left() if index == 0 else (rects[index - 1].right() + rect.left()) / 2
            right = (
                rect.right()
                if index == len(rects) - 1
                else (rect.right() + rects[index + 1].left()) / 2
            )
            if left <= x <= right:
                return index + 1
        return -1

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt override
        pip = float(theme.metric("column.rank-pip"))
        width = self._natural(pip, float(theme.metric("space.xs")), float(theme.metric("space.md")))
        return QSize(int(width + 1), self._height())

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt override
        return QSize(0, self._height())

    def _height(self) -> int:
        return int(theme.metric("column.rank-pip")) + 2 * int(theme.metric("space.xs"))

    # -- painting ------------------------------------------------------------

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802 - Qt override
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        accent = QColor(theme.color("accent"))
        muted = QColor(theme.color("text.muted.rich"))
        ring = float(theme.metric("border.width.emphasis"))
        target = self._outcome(self._hover) if self._hover > 0 else self._value
        for notch, rect in enumerate(self._rects(), start=1):
            inner = rect.adjusted(ring / 2, ring / 2, -ring / 2, -ring / 2)
            lit = notch <= self._value
            wanted = notch <= target
            if notch > self._ceiling:
                pen = QPen(muted, ring, Qt.PenStyle.DotLine)
                fill = QColor(Qt.GlobalColor.transparent)
            elif lit and wanted:
                pen, fill = QPen(accent, ring), accent
            elif wanted:  # would light up: washed in
                pen, fill = QPen(accent, ring), _alpha(accent, 0.45)
            elif lit:  # would go out: fading
                pen, fill = QPen(accent, ring), _alpha(accent, 0.2)
            else:
                pen, fill = QPen(muted, ring), QColor(Qt.GlobalColor.transparent)
            painter.setPen(pen)
            painter.setBrush(fill)
            painter.drawEllipse(inner)
        painter.end()

    # -- interaction ---------------------------------------------------------

    def _reachable(self, notch: int) -> bool:
        return 1 <= notch <= self._ceiling

    def _outcome(self, notch: int) -> int:
        """Where a click on pip *notch* lands: one lower on the last lit pip, else there."""
        return notch - 1 if notch == self._value else notch

    def _set_hover(self, notch: int) -> None:
        if notch == self._hover:
            return
        self._hover = notch
        self.setCursor(
            Qt.CursorShape.PointingHandCursor if notch > 0 else Qt.CursorShape.ArrowCursor
        )
        self.update()
        self.hovered.emit(self._outcome(notch) if notch > 0 else -1)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - Qt override
        notch = self._notch_at(event.position().x())
        self._set_hover(notch if self._reachable(notch) else -1)

    def leaveEvent(self, event) -> None:  # noqa: N802 - Qt override
        self._set_hover(-1)
        super().leaveEvent(event)

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - Qt override
        # Accepted wherever it lands, gaps included: a press that fell through to the
        # card underneath would switch the whole power on or off.
        event.accept()
        if event.button() == Qt.MouseButton.LeftButton:
            notch = self._notch_at(event.position().x())
            self._pressed = notch if self._reachable(notch) else -1

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - Qt override
        """A click is a press and a release on the same pip, as a button's is."""
        event.accept()
        pressed, self._pressed = self._pressed, -1
        if event.button() != Qt.MouseButton.LeftButton or pressed < 0:
            return
        if self._notch_at(event.position().x()) == pressed:
            self.picked.emit(self._outcome(pressed))


def _alpha(colour: QColor, alpha: float) -> QColor:
    washed = QColor(colour)
    washed.setAlphaF(alpha)
    return washed

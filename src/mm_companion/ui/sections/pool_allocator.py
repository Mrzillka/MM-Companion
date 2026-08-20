"""Direct-manipulation controls for splitting an array's point pool.

Deciding the split is "a free action, once per turn" (p101) — a thing done mid-fight,
between one attack and the next, not a form to fill in. The
:class:`~mm_companion.ui.sections.dynamic_pool_dialog.DynamicPoolDialog` meters it with
one spin box per member, which is exact and is the only way to leave part of the pool
unassigned, but two numbers typed against each other is not what "shift some power from
the shield to the jets" feels like.

So the dialog hosts an **allocator** above those spins: one control, moved once, that
always spreads the *whole* pool. The spins stay and stay authoritative — the allocator
writes through them — and they remain the way to hold points back.

There is one allocator today and the seam is built for the second:

* :class:`TwoWayPoolSlider` — **two** Dynamic members. A single handle between them;
  everything left of it is the first member's, everything right the second's.
* A polygon allocator for **three or more** is the intended next one, and
  :func:`make_allocator` is the only place that needs to learn about it. The control is
  a regular N-gon with a draggable puck whose barycentric coordinates are the members'
  shares — drag it at a vertex to give that member everything, hold it in the middle for
  an even spread — rounded by largest-remainder so the shares still sum to the pool
  exactly. ``sharesChanged`` already carries a list, so nothing downstream changes. It
  wants custom painting, which nothing in this app does except
  :class:`~mm_companion.ui.connection_indicator._StatusDot`: copy its colour handling,
  since a theme token is allowed to be a ``palette(...)`` expression that
  :class:`~PySide6.QtGui.QColor` cannot parse.

An allocator knows nothing about powers. It is handed member *names* and a pool, and a
``describe`` callback the dialog uses to say what a share currently buys — the rules
arithmetic stays in :mod:`mm_companion.core.rules`, where the rest of it lives.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QSlider, QVBoxLayout, QWidget

from mm_companion.ui import theme
from mm_companion.ui.wheel_guard import guard_wheel
from mm_companion.ui.widgets import BOLD_STYLE, muted_style

#: What one share currently buys, as ``(member index, points) -> text``. Supplied by the
#: host, because the answer is rules arithmetic and this module has none.
Describe = Callable[[int, int], str]

#: Said under the handle while the spins are holding points back. The allocator itself
#: cannot produce this state — it always spreads everything — so it is a report on where
#: the dialog was opened, and it clears the moment the handle is touched.
_SLACK_NOTE = "{points} PP of the pool is unassigned — move the handle to spread all of it."

#: Roughly how many tick marks the groove should carry. A pool of 8 gets one per point;
#: a pool of 90 gets one every five, rather than a solid grey bar.
_TICK_TARGET = 20


def _tick_interval(pool: int) -> int:
    """A tick spacing that marks the groove without filling it in."""

    return max(1, round(pool / _TICK_TARGET))


class TwoWayPoolSlider(QWidget):
    """One handle dividing a point pool between exactly two Dynamic members.

    The handle *is* the split: its value is the first member's share and the rest is the
    second's, so the control can only ever describe a legal, fully-spread division and
    there is no second number to keep consistent with the first. Each side carries its
    member's name, the points it holds and what those points currently buy, so the thing
    being chosen ("Flight 3 of 5") is on screen rather than the thing being spent.

    Unlike the sheet card's rank dial it commits on every tick rather than on release: a
    dialog is not rebuilt underneath the handle, so there is nothing to destroy mid-drag
    and live numbers are the whole point of dragging.
    """

    #: The split the handle now describes, one entry per member. Always sums to the pool.
    sharesChanged = Signal(list)

    def __init__(
        self,
        names: Sequence[str],
        pool: int,
        describe: Describe | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        if len(names) != 2:
            raise ValueError(f"a two-way slider needs exactly two members, got {len(names)}")
        self._pool = max(0, pool)
        self._describe = describe
        self._held: list[int] = [0, 0]

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(int(theme.metric("space.sm")))

        ends = QHBoxLayout()
        ends.setContentsMargins(0, 0, 0, 0)
        self._ends = []
        for index, name in enumerate(names):
            side = QVBoxLayout()
            side.setContentsMargins(0, 0, 0, 0)
            side.setSpacing(0)
            title = QLabel(name)
            title.setStyleSheet(BOLD_STYLE)
            share = QLabel()
            detail = QLabel()
            detail.setStyleSheet(muted_style())
            for widget in (title, share, detail):
                # The right-hand member reads right-aligned, so each side's numbers sit
                # against the end of the groove they belong to.
                widget.setAlignment(
                    Qt.AlignmentFlag.AlignLeft if index == 0 else Qt.AlignmentFlag.AlignRight
                )
                side.addWidget(widget)
            if index:
                ends.addStretch()
            ends.addLayout(side)
            self._ends.append((share, detail))
        outer.addLayout(ends)

        self._slider = QSlider(Qt.Orientation.Horizontal)
        self._slider.setRange(0, self._pool)
        self._slider.setSingleStep(1)
        self._slider.setPageStep(max(1, _tick_interval(self._pool)))
        self._slider.setTickPosition(QSlider.TickPosition.TicksBelow)
        self._slider.setTickInterval(_tick_interval(self._pool))
        self._slider.setCursor(Qt.CursorShape.PointingHandCursor)
        guard_wheel(self._slider)
        outer.addWidget(self._slider)

        self._note = QLabel()
        self._note.setWordWrap(True)
        self._note.setStyleSheet(muted_style())
        outer.addWidget(self._note)

        self._slider.valueChanged.connect(self._on_moved)
        self.set_shares([0, 0])

    # -- state ----------------------------------------------------------------
    def set_shares(self, shares: Sequence[int]) -> None:
        """Show *shares* without reporting them back — the host driving this control.

        The handle goes where the first member's share is, and each side reports what it
        actually holds rather than what the handle would imply. The two agree for every
        split this widget can make; they differ only when the dialog was opened on a
        split the spin boxes made that leaves part of the pool spare, and the note under
        the groove says so rather than the numbers quietly lying.
        """

        self._held = [max(0, int(share)) for share in shares[:2]]
        self._slider.blockSignals(True)
        self._slider.setValue(min(self._held[0], self._pool))
        self._slider.blockSignals(False)
        self._refresh()

    def shares(self) -> list[int]:
        """What the two sides hold right now."""

        return list(self._held)

    # -- internals ------------------------------------------------------------
    def _on_moved(self, value: int) -> None:
        self._held = [value, self._pool - value]
        self._refresh()
        self.sharesChanged.emit(list(self._held))

    def _refresh(self) -> None:
        for index, (share, detail) in enumerate(self._ends):
            points = self._held[index]
            share.setText(f"{points} PP")
            detail.setText(self._describe(index, points) if self._describe else "")
        spare = self._pool - sum(self._held)
        self._note.setText(_SLACK_NOTE.format(points=spare) if spare > 0 else "")
        self._note.setVisible(spare > 0)


def make_allocator(
    names: Sequence[str], pool: int, describe: Describe | None = None
) -> QWidget | None:
    """The allocator for this many members, or ``None`` when there isn't one yet.

    ``None`` is a working answer, not a failure: the host falls back to its spin boxes,
    which can express every split any allocator can and several none of them can. Today
    only a two-member array has a control of its own; see this module's docstring for
    the polygon that three or more want.
    """

    if pool <= 0 or len(names) != 2:
        return None
    return TwoWayPoolSlider(names, pool, describe)

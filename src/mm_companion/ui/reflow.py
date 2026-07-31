"""Laying a fixed set of parts out along whichever axis the space favours.

Some widgets hold a handful of parts that read equally well side by side or
stacked, and which of the two is right depends entirely on the shape of the space
they were handed. The Dice block is the case in point: in the narrow right-hand
pinned strip its parts belong in a column, and in a short wide *bottom* strip that
same column is a disaster — stretched sliders, a die adrift, and a content minimum
that forces the strip open to half the window. What it wants is to become a row.

This is the sibling of :mod:`mm_companion.ui.sections.column_flow`, which solves
the neighbouring problem (a *variable* number of side-by-side panels for a list),
and it borrows that module's three hard-won lessons:

* the decision is pure arithmetic, kept out of Qt so it can be tested directly
  (:func:`prefers_row`);
* it carries a **hysteresis** dead-band, because a reflow changes the widget's
  height, which can toggle a scrollbar, which changes the width back across the
  boundary — an endless relayout otherwise;
* the widget reports the **column** arrangement's width as its minimum, so it can
  always shrink by reflowing rather than pinning the window open at the row width.

:class:`ReflowBox` is the Qt half — the current axis, the ``resizeEvent`` hook, and
the two minimum-size measurements — mixed into a widget that supplies
:meth:`~ReflowBox.reflow_parts` and :meth:`~ReflowBox.apply_reflow`.
"""

from __future__ import annotations

from collections.abc import Sequence

from PySide6.QtCore import QSize
from PySide6.QtWidgets import QLayout, QWidget


def prefers_row(
    available: int,
    row_minimum: int,
    currently_row: bool = False,
    hysteresis: int = 0,
) -> bool:
    """Whether the parts should sit side by side in *available* pixels of width.

    *row_minimum* is what a row genuinely needs — the parts' own minimum widths
    plus the spacing between them. A row is chosen once there is room for it.

    *hysteresis* is a dead-band around that boundary: with it, the answer only
    changes once the width is past the boundary by that much in the direction it
    is moving, so a layout sitting near the threshold cannot flip back and forth.
    That matters because the flip changes the widget's height, which can make a
    scrollbar appear or vanish, which nudges the width by its own extent — right
    back over the line. The band should be at least a scrollbar wide. This is the
    same guard, for the same reason, as
    :func:`mm_companion.ui.sections.column_flow.column_count`'s.

    A non-positive *available* (a widget that has not been laid out yet) answers
    ``False``, so the first paint is the safe column and the real answer arrives on
    the first ``resizeEvent``.
    """
    if available <= 0 or row_minimum <= 0:
        return False
    if hysteresis <= 0:
        return available >= row_minimum
    if currently_row:
        # Already a row: only fall back to a column once the width is a full band
        # short of what the row needs.
        return available > row_minimum - hysteresis
    # Currently a column: only become a row once there is the row's width to spare.
    return available >= row_minimum + hysteresis


class ReflowBox:
    """The Qt half of the reflow pattern: an axis that follows the widget's width.

    Mix into a ``QWidget`` that lays a fixed set of parts out along one axis. The
    widget supplies:

    - :meth:`reflow_parts` — the part widgets, in order.
    - :meth:`apply_reflow` — put them on the given axis (flip a ``QBoxLayout``'s
      direction, re-orient a ``QSplitter``, …).

    Call :meth:`init_reflow` during construction *after* the parts exist, then
    :meth:`sync_reflow` from ``resizeEvent``. Because this overrides
    ``minimumSizeHint``, it must come **before** the ``QWidget`` base in the class's
    bases so it wins the MRO — the same requirement
    :class:`~mm_companion.ui.sections.column_flow.ColumnFlowPanels` has.
    """

    #: Gap assumed between parts, and the dead-band that stops the axis flipping
    #: when a scrollbar appears or disappears. The band is at least a scrollbar wide.
    REFLOW_SPACING = 6
    REFLOW_HYSTERESIS = 24

    def init_reflow(self, *, row: bool = False) -> None:
        """Start out on the given axis (a column by default) and apply it.

        Also releases the widget from its own layout's minimum. By default a layout
        *imposes* its minimum on the widget it manages, and that beats the
        :meth:`minimumSizeHint` below — so a widget currently in a row could never be
        made narrower than that row needs, and would be stuck in the arrangement it
        happened to be in. ``SetNoConstraint`` hands the decision back to us: the
        widget may be given less than the current arrangement needs, and the
        ``resizeEvent`` that follows reflows into the one that fits. The parts are
        briefly tight for that single frame, which is the price of being able to
        change shape at all.
        """
        self._reflow_row = bool(row)
        layout = self.layout()
        if layout is not None:
            layout.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)
        self.apply_reflow(self._reflow_row)

    # -- supplied by the widget ----------------------------------------------

    def reflow_parts(self) -> Sequence[QWidget]:
        raise NotImplementedError

    def apply_reflow(self, row: bool) -> None:
        raise NotImplementedError

    # -- the current axis ----------------------------------------------------

    @property
    def is_row(self) -> bool:
        """Whether the parts are currently side by side."""
        return getattr(self, "_reflow_row", False)

    def row_minimum_width(self) -> int:
        """What a side-by-side arrangement needs: the parts' minimums plus spacing."""
        parts = [part for part in self.reflow_parts() if not part.isHidden()]
        if not parts:
            return 0
        total = sum(part.minimumSizeHint().width() for part in parts)
        return total + self.REFLOW_SPACING * (len(parts) - 1)

    def row_natural_width(self) -> int:
        """What a side-by-side arrangement would *like*: the parts' preferred widths.

        The counterpart of :meth:`row_minimum_width`, and the one to lay a row out at
        when there is room. At its bare minimum a row is legible but pinched —
        captions truncate and spin boxes lose their number fields — so a host
        dividing space between a reflowing widget and something else should offer
        this and only fall back towards the minimum when it cannot.

        Deliberately independent of the axis currently in use, so it can be asked
        *before* the flip that will need it.
        """
        parts = [part for part in self.reflow_parts() if not part.isHidden()]
        if not parts:
            return 0
        total = sum(part.sizeHint().width() for part in parts)
        return total + self.REFLOW_SPACING * (len(parts) - 1)

    def column_minimum_width(self) -> int:
        """What a stacked arrangement needs: the widest part alone."""
        parts = [part for part in self.reflow_parts() if not part.isHidden()]
        if not parts:
            return 0
        return max(part.minimumSizeHint().width() for part in parts)

    def sync_reflow(self) -> bool:
        """Flip the axis if the current width now favours the other one.

        Returns whether it flipped. Call from ``resizeEvent``.
        """
        row = prefers_row(
            self.reflow_available_width(),
            self.row_minimum_width(),
            self.is_row,
            self.REFLOW_HYSTERESIS,
        )
        if row == self.is_row:
            return False
        self._reflow_row = row
        self.apply_reflow(row)
        # The arrangement's height minimum just changed, so whatever holds this
        # widget open (a block frame, and through it the pinned strip and the
        # window) has to be told to ask again.
        self.updateGeometry()
        return True

    def reflow_available_width(self) -> int:
        """The width the parts have to share, net of this widget's own margins."""
        layout = self.layout()
        if layout is None:
            return self.width()
        margins = layout.contentsMargins()
        return self.width() - margins.left() - margins.right()

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt override
        """Report the **column** width, whichever axis is in use.

        A row's minimum width would otherwise pin this widget — and the window
        holding it — at the full side-by-side width, when in truth it can always
        narrow by reflowing back into a column. The height is left as the current
        arrangement's, which is the honest answer for the shape it is actually in
        and is what lets a bottom strip be thin. Mirrors
        :meth:`ColumnFlowPanels.minimumSizeHint
        <mm_companion.ui.sections.column_flow.ColumnFlowPanels.minimumSizeHint>`.
        """
        hint = super().minimumSizeHint()
        column = self.column_minimum_width()
        if column <= 0:
            return hint
        return QSize(min(hint.width(), column), hint.height())

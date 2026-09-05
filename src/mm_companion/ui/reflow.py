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

from mm_companion.ui.widgets import no_reentry


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


#: How much narrower than a boundary something has to be before it sheds another
#: part, and how much wider before it takes one back. The same dead-band, for
#: the same reason, as :func:`~mm_companion.ui.sections.column_flow.column_count`'s
#: and :func:`~mm_companion.ui.reflow.prefers_row`'s: shedding a column changes
#: what wraps, which changes the block's height, which can toggle a scrollbar,
#: which changes the width back over the boundary — an endless relayout otherwise.
SHED_HYSTERESIS = 24


def parts_to_shed(
    available: int,
    widths: Sequence[int],
    shed_order: Sequence[int],
    *,
    current: Sequence[int] = (),
    hysteresis: int = 0,
) -> tuple[int, ...]:
    """Which parts to hide so the rest fit in *available* pixels.

    *shed_order* is the parts a widget is willing to give up, **worst first** — the
    abbreviation before the name, the modifier before the rank, an effect's game
    terms before the extras that bought them. Everything not named in it is
    load-bearing and is never hidden, so the widget can always be dragged narrower
    than it can honestly show and what remains is what was worth keeping. Past that
    it clips or scrolls; nothing is ever lost, and widening brings it all back.

    Written for a table's *columns* (:meth:`~mm_companion.ui.sections.row_table.
    AutoHeightTable.sync_shed_columns`, which still calls it ``columns_to_shed``)
    and used unchanged for a run of *widgets* (:class:`ShedBox`): "which of these,
    in this order, do I drop to fit" is one question, and a second implementation of
    it would be a second dead-band to get wrong.

    A non-positive *available* (something that has not been laid out yet) sheds
    nothing, so the first paint is the whole thing and the real answer arrives on
    the first ``resizeEvent``.

    *hysteresis* is the dead-band, and it applies from the **first** part onwards.
    It used to stand down whenever *current* was empty — which is precisely the
    state anything is in before it sheds anything, so the one transition most worth
    damping was the one transition that never was.
    """
    if available <= 0 or not shed_order:
        return ()
    total = sum(widths)
    shed: list[int] = []
    for column in shed_order:
        if total <= available:
            break
        if 0 <= column < len(widths):
            shed.append(column)
            total -= widths[column]

    if not hysteresis:
        return tuple(shed)

    def needed(hidden: Sequence[int]) -> int:
        """What the arrangement with *hidden* dropped actually takes."""
        gone = set(hidden)
        return sum(w for i, w in enumerate(widths) if i not in gone)

    # Only change the answer once the width is past the boundary by a full band in
    # the direction it is moving, or the answer flips back and forth on its own.
    # Both bands are measured against what the arrangement *in force* needs, which
    # is what makes them nest: from any state, the width at which another column
    # goes is a band below that number and the width at which one comes back is a
    # band above it, so there is no width at which both are true. Measuring the
    # shedding side against the *post*-shed width instead — which is what this did
    # — let the two overlap, and a table sitting in the overlap shed and restored
    # the same column for as long as the layout kept asking.
    if len(shed) > len(current):
        return tuple(shed) if available <= needed(current) - hysteresis else tuple(current)
    if len(shed) < len(current):
        return tuple(shed) if available >= needed(shed) + hysteresis else tuple(current)
    return tuple(shed)


class ShedBox(QWidget):
    """A run of parts along one axis, dropping the least useful as it narrows.

    The third answer to "this does not fit", beside :class:`ReflowBox` (put the
    same parts on the other axis) and
    :class:`~mm_companion.ui.sections.column_flow.ColumnFlowPanels` (use fewer
    panels): give a part up entirely. A power card's effect summary is the case —
    its game terms beside the extras and flaws that bought them, in a block a
    player may drag to a third of a page — where re-dealing the terms into one
    column is not enough and stacking them only makes a narrow card a long one.

    The decision is :func:`parts_to_shed`, unchanged from the table blocks, with
    the same dead-band and for the same reason. What is left never goes, so the
    part that matters most is simply the one left out of *shed_order*.

    The widget reports the **shed-down** arrangement as its minimum, which is the
    rule every adaptive widget on the sheet follows: a minimum is a refusal, and
    refusing the width you know how to reach is how a block ends up clipped
    instead of adapted. Its ``sizeHint`` is the whole thing, which is a preference
    and may be content-shaped.
    """

    def __init__(
        self,
        parts: Sequence[QWidget],
        shed_order: Sequence[int],
        layout: QLayout,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._parts = list(parts)
        self._shed_order = tuple(shed_order)
        self._shed: tuple[int, ...] = ()
        # What each part measured while it was showing. A hidden widget answers
        # ``0`` to every size question it is asked, so a part that had been shed
        # could never be worked out to fit again — the same trap, and the same
        # answer, as a hidden table column's remembered width.
        self._natural: dict[int, int] = {}
        self.setLayout(layout)

    @property
    def parts(self) -> list[QWidget]:
        return list(self._parts)

    @property
    def shed_order(self) -> tuple[int, ...]:
        """The parts this box is willing to give up, worst first, as indices."""
        return self._shed_order

    def shed_parts(self) -> tuple[int, ...]:
        """Which parts are hidden for want of room right now."""
        return self._shed

    def natural_widths(self) -> list[int]:
        """What each part would take if it were showing (see :attr:`_natural`)."""
        widths: list[int] = []
        for index, part in enumerate(self._parts):
            if part.isHidden():
                widths.append(self._natural.get(index, 0))
                continue
            natural = max(part.sizeHint().width(), part.minimumSizeHint().width())
            self._natural[index] = natural
            widths.append(natural)
        return widths

    def _available(self) -> int:
        layout = self.layout()
        if layout is None:
            return self.width()
        margins = layout.contentsMargins()
        spacing = layout.spacing() if layout.spacing() > 0 else 0
        showing = max(1, len(self._parts) - len(self._shed))
        return self.width() - margins.left() - margins.right() - spacing * (showing - 1)

    @no_reentry
    def sync_shed(self) -> bool:
        """Hide or restore parts to suit the width. Returns whether it changed."""
        if not self._shed_order:
            return False
        wanted = parts_to_shed(
            self._available(),
            self.natural_widths(),
            self._shed_order,
            current=self._shed,
            hysteresis=SHED_HYSTERESIS,
        )
        if wanted == self._shed:
            return False
        self._shed = wanted
        hidden = set(wanted)
        for index in self._shed_order:
            if 0 <= index < len(self._parts):
                self._parts[index].setVisible(index not in hidden)
        self.updateGeometry()
        return True

    def resizeEvent(self, event) -> None:  # noqa: ANN001, N802 - Qt override
        super().resizeEvent(event)
        if event.oldSize().width() != event.size().width():
            self.sync_shed()

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt override
        """Everything in :attr:`_shed_order` gone, whatever is hidden right now.

        Invariant of the current arrangement on purpose: the host hands this widget
        the larger of the room available and this number, so a minimum that tracked
        what was shed would be reading a width it had itself just set — the loop
        that once turned a narrowed table into a stack overflow.
        """
        hint = super().minimumSizeHint()
        sheddable = set(self._shed_order)
        widths = self.natural_widths()
        floor = sum(width for index, width in enumerate(widths) if index not in sheddable)
        layout = self.layout()
        if layout is not None:
            margins = layout.contentsMargins()
            floor += margins.left() + margins.right()
        # A run every part of which may go asks for nothing, and that is the honest
        # answer rather than an edge case to dodge: what is left when all of it has
        # gone is nothing, and a widget that asked for its content's width anyway
        # would be refusing the one arrangement it is guaranteed to be able to reach.
        return QSize(min(hint.width(), floor), hint.height())


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

    def force_reflow(self, row: bool) -> bool:
        """Put the parts on the given axis and leave them there.

        For a widget whose shape is *chosen* rather than derived from the room
        available — the roller's Compact and Extended preferences, which pin one
        arrangement whatever the width. Pair it with a :meth:`sync_reflow` that
        stands down while the choice is in force, or the next resize undoes it.

        Guarded on the current axis, so calling it every resize (which a locked
        widget does) neither re-applies the arrangement nor throws away whatever
        the host has since divided between the parts.
        """
        if self.is_row == bool(row):
            return False
        self._reflow_row = bool(row)
        self.apply_reflow(self._reflow_row)
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

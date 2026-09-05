"""Helpers for laying a list out across a variable number of side-by-side panels.

Both the Skills and Advantages blocks render their rows across several
side-by-side tables. The number of panels is not fixed: it adapts to the block's
current width so a wide block shows more columns and a narrow one shows fewer,
and it never lets a panel get narrower than the content needs (so nothing is
clipped).

Two pure functions carry the arithmetic, kept free of Qt state so they are
unit-testable:

- :func:`column_count` decides how many panels fit a given width.
- :func:`even_split` divides an ordered list of weighted blocks into that many
  near-equal-height buckets without reordering or splitting a block.

:class:`ColumnFlowPanels` is the Qt half — the panel pool, the width measurement
and the shrink-to-one-column minimum both blocks were carrying their own copy of.
"""

from __future__ import annotations

from PySide6.QtCore import QSize
from PySide6.QtWidgets import QBoxLayout, QHBoxLayout, QSizePolicy, QWidget

from mm_companion.ui.widgets import discard_widget, no_reentry


def column_count(
    available_width: int,
    min_col_width: int,
    spacing: int,
    item_count: int,
    current: int = 0,
    hysteresis: int = 0,
) -> int:
    """How many panels of at least *min_col_width* fit into *available_width*.

    ``n`` panels laid side by side with *spacing* between them occupy
    ``n * min_col_width + (n - 1) * spacing``; solving for the largest ``n`` that
    still fits gives ``(available_width + spacing) // (min_col_width + spacing)``.
    The result is clamped to at least one and never more than *item_count* (an
    empty list still yields one panel). A zero/negative width or min width falls
    back to a single panel, so the first paint — before any real geometry — is
    safe; the true value lands on the first ``resizeEvent``.

    When *current* (the count in effect now) and *hysteresis* (a px dead-band) are
    given, the count only changes once the width is past the boundary by that band.
    This breaks the feedback loop where changing the count changes a block's height,
    which toggles the page's vertical scrollbar, which changes the width back across
    the boundary — an infinite relayout. The band should be at least the scrollbar's
    width so its appearing/disappearing can't flip the count.
    """

    if item_count <= 1 or available_width <= 0 or min_col_width <= 0:
        return 1
    fit = (available_width + spacing) // (min_col_width + spacing)
    n = max(1, min(item_count, int(fit)))
    if current <= 0 or hysteresis <= 0 or n == current:
        return n

    def width_for(columns: int) -> int:
        return columns * min_col_width + (columns - 1) * spacing

    if n > current:  # only grow once there's room for the extra column *plus* the band
        return n if available_width >= width_for(n) + hysteresis else current
    # only shrink once the width is a band below what the current count needs
    return n if available_width <= width_for(current) - hysteresis else current


def even_split(weights: list[int], groups: int) -> list[list[int]]:
    """Partition indices ``0..len(weights)-1`` into *groups* ordered buckets.

    Order is preserved and a block (one weight) is never split, so this suits
    dividing a column of variable-height rows into near-equal-height panels. The
    split minimises the tallest bucket (the classic linear-partition problem)
    via a small dynamic program — a faithful N-way generalisation of the exact
    two-way split the skills block used before. Exactly *groups* buckets are
    returned; trailing ones are empty only when there are fewer blocks than
    buckets.
    """

    groups = max(1, groups)
    result: list[list[int]] = [[] for _ in range(groups)]
    n = len(weights)
    if n == 0:
        return result

    parts = min(groups, n)
    prefix = [0] * (n + 1)
    for i in range(n):
        prefix[i + 1] = prefix[i] + weights[i]

    inf = float("inf")
    # dp[p][i] = smallest achievable tallest-bucket when the first i blocks are
    # split into p contiguous parts; choice[p][i] records the last divider.
    dp = [[inf] * (n + 1) for _ in range(parts + 1)]
    choice = [[0] * (n + 1) for _ in range(parts + 1)]
    for i in range(1, n + 1):
        dp[1][i] = prefix[i]
    for p in range(2, parts + 1):
        for i in range(p, n + 1):
            best, best_j = inf, p - 1
            for j in range(p - 1, i):
                candidate = max(dp[p - 1][j], prefix[i] - prefix[j])
                if candidate < best:
                    best, best_j = candidate, j
            dp[p][i], choice[p][i] = best, best_j

    # Walk the dividers back from (parts, n) to recover each contiguous bucket.
    i = n
    for p in range(parts, 0, -1):
        j = choice[p][i]
        result[p - 1] = list(range(j, i))
        i = j
    return result


class ColumnFlowPanels:
    """The Qt half of the column-flow pattern: a pool of side-by-side table panels.

    Mix into a section that renders its rows across several tables (Skills,
    Advantages). It owns the panel pool, the available-width measurement, and the
    shrink-to-one-column minimum; the section supplies what varies:

    - :meth:`_make_table` — build one empty panel.
    - :meth:`_min_col_width` — the width a panel *reads well* at, which is what
      decides how many of them fit (driven by the widest label actually present, so
      it moves with the data).
    - :meth:`_panel_floor_width` — the narrowest a panel knows how to *reach*, which
      is a different question and is what the section reports as its minimum.
    - :meth:`_flow_item_count` — how many items are being laid out, the ceiling on
      the panel count.
    - :meth:`_rebuild` — re-render every panel; called when the count changes.

    Call :meth:`_init_flow_panels` during construction, then
    :meth:`_sync_column_count` from ``resizeEvent``. Because this overrides
    ``minimumSizeHint``, it must come *before* the ``QWidget`` base in the class's
    bases so it wins the MRO.
    """

    #: Gap between panels, and the dead-band that stops the count flipping when the
    #: page's vertical scrollbar appears/disappears (which nudges the width by its
    #: own extent). The band should be at least a scrollbar wide.
    FLOW_SPACING = 6
    FLOW_HYSTERESIS = 24

    def _init_flow_panels(self, parent_layout: QBoxLayout) -> None:
        """Build the panel container and add it to *parent_layout*."""

        self._tables: list = []
        self._column_count = 0
        self._tables_container = QWidget()
        # Expanding down, and no ``AlignTop`` on the row of panels: the block's spare
        # height has to reach the tables, which share it out over their own rows. An
        # alignment is a refusal to grow, so the panels sat at their content height
        # with the surplus as bare widget under them — and a table inside a border
        # that stops half way down a block is the shape of "it doesn't scale".
        self._tables_container.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        self._tables_layout = QHBoxLayout(self._tables_container)
        self._tables_layout.setContentsMargins(0, 0, 0, 0)
        self._tables_layout.setSpacing(self.FLOW_SPACING)
        parent_layout.addWidget(self._tables_container)
        # Release the section from its own layout's minimum, so :meth:`minimumSizeHint`
        # is actually the answer. A layout otherwise *imposes* ``totalMinimumSize`` on
        # the widget it manages and that beats any override — which pinned the block at
        # whatever the picker row happened to need and made the floor below moot. The
        # same line, for the same reason, as :meth:`~mm_companion.ui.reflow.ReflowBox.
        # init_reflow`'s.
        parent_layout.setSizeConstraint(QBoxLayout.SizeConstraint.SetNoConstraint)

    # -- supplied by the section ------------------------------------------------

    def _make_table(self):
        raise NotImplementedError

    def _min_col_width(self) -> int:
        raise NotImplementedError

    def _panel_floor_width(self) -> int:
        """The narrowest one panel knows how to reach — see :meth:`minimumSizeHint`.

        Defaults to :meth:`_min_col_width`, which is what a section that can shed
        nothing wants. A section that *can* — a table with a
        :meth:`~mm_companion.ui.sections.row_table.AutoHeightTable.set_shed_order`
        — states the arrangement with every sheddable column already gone, and it
        must be a number **no adaptive decision moves**: it is what the block hands
        the section when the viewport is smaller, so a floor that tracked what is
        currently shed would be deciding the width the shed decision then reads.
        """
        return self._min_col_width()

    def _flow_item_count(self) -> int:
        raise NotImplementedError

    def _rebuild(self) -> None:
        raise NotImplementedError

    # -- panel pool --------------------------------------------------------------

    def _ensure_tables(self, count: int) -> None:
        """Grow or shrink the pool of side-by-side panels to *count*."""

        while len(self._tables) < count:
            table = self._make_table()
            self._tables_layout.addWidget(table, stretch=1)
            self._tables.append(table)
        while len(self._tables) > count:
            table = self._tables.pop()
            self._tables_layout.removeWidget(table)
            # Hidden and unparented before the delete is scheduled, or the dropped
            # panel both ghosts and flashes — see :func:`~mm_companion.ui.widgets.
            # discard_widget`.
            discard_widget(table)

    # -- responsive count --------------------------------------------------------

    def _available_width(self) -> int:
        """The width the panels have to share, net of the section's margins."""

        margins = self.layout().contentsMargins()
        return self.width() - margins.left() - margins.right()

    def _flow_column_count(self) -> int:
        """How many panels the current width fits (hysteresis applied)."""

        return column_count(
            self._available_width(),
            self._min_col_width(),
            self.FLOW_SPACING,
            self._flow_item_count(),
            self._column_count,
            self.FLOW_HYSTERESIS,
        )

    @no_reentry
    def _sync_column_count(self) -> bool:
        """Rebuild if the width now fits a different number of panels.

        Returns whether a rebuild happened. Call from ``resizeEvent``; ``_rebuild``
        settles ``_column_count`` itself, so this only decides *whether* to run.

        Guarded against re-entry, and it is the one here that most needs it: this
        does not merely change a property, it *destroys and rebuilds* the panel
        widgets. Qt laying out again inside that would re-enter with the widgets
        whose event is on the stack already gone.
        """

        if self._flow_column_count() == self._column_count:
            return False
        self._rebuild()
        return True

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt override
        """Report **one panel at its floor** — not one panel at its comfortable width.

        Two separate questions, and reporting the same answer to both is what made a
        narrowed Skills or Advantages block *clip* rather than adapt. How wide a
        panel reads well (:meth:`_min_col_width`, the widest label present plus every
        column) is the right divisor for "how many of these fit"; it is the wrong
        minimum, because a panel is not stuck at it — its table sheds columns, its
        name column wraps — and a minimum is a refusal. The block hands the section
        the larger of the viewport and this number, so while this said "a comfortable
        panel" the section was handed a comfortable panel's width at every size below
        it, the table never got narrow enough to shed anything, and what the viewport
        could not show was simply cut off.

        A ceiling it still is: the side-by-side tables would otherwise inflate the
        minimum to the full multi-column width, pinning the whole page (and window)
        wide and forcing at least two columns. One panel lets the block narrow;
        ``resizeEvent`` then rebuilds to as many as fit.

        The floor must not move with the lock (a locked section hides its picker) —
        see ``tests/test_lock_geometry.py`` and the standing rule that a lock toggle
        may change a block's height but never its width — nor with anything the
        adaptive decisions themselves change. Both blocks answer with theme metrics
        and constants for that reason.
        """
        return QSize(self._panel_floor_width(), super().minimumSizeHint().height())

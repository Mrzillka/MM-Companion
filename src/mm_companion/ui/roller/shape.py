"""How the roller panel arranges its three parts for the room it has: the reflow between a
column and a row, the compact shape, and the die's size.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QBoxLayout,
    QWidget,
)

from mm_companion.core import storage


class RollerShapeMixin:
    """Part of :class:`~mm_companion.ui.dice_roller.DiceRollerPanel`; see the module docstring."""

    # -- shape and reflow -----------------------------------------------------

    def reflow_parts(self) -> tuple[QWidget, QWidget, QWidget]:
        """The parts the reflow arranges: roll settings, the die, the quick rolls."""
        return (self._settings_part, self._die_part, self._quick_part)

    def apply_reflow(self, row: bool) -> None:
        """Put the three parts in a row or a column.

        Only the layout's direction and its spacers change — the parts themselves
        stay where they are, so nothing is rebuilt or re-parented.

        The trailing stretch belongs to the **column** alone: there it holds the
        quick-roll strip down at the bottom so a readout growing a degree line
        doesn't nudge it about. In a row that same stretch would shove the quick
        rolls off to the right-hand edge, so it is taken out again.
        """
        self._box.setDirection(
            QBoxLayout.Direction.LeftToRight if row else QBoxLayout.Direction.TopToBottom
        )
        # Drop any stretch from the previous arrangement (the parts are widgets, so
        # every non-widget item in the layout is a spacer we put there).
        for index in reversed(range(self._box.count())):
            if self._box.itemAt(index).widget() is None:
                self._box.takeAt(index)
        if row:
            # Across a row the die keeps its fixed 180px and the other two share the
            # slack. Each part is also top-aligned, so a short one (the Roll grid is
            # a little over 100px tall) sits at the top of a tall strip instead of having its rows
            # spread down the whole height. Note this is per *item*: the one-argument
            # setAlignment places the layout inside its widget, which is not the same
            # thing and would leave the parts stretched.
            for part, stretch in zip(self.reflow_parts(), (1, 0, 1), strict=True):
                self._box.setStretchFactor(part, stretch)
                self._box.setAlignment(part, Qt.AlignmentFlag.AlignTop)
        else:
            for part in self.reflow_parts():
                self._box.setStretchFactor(part, 0)
                self._box.setAlignment(part, Qt.AlignmentFlag(0))
            self._box.insertStretch(self._box.indexOf(self._quick_part))

    def sync_reflow(self) -> bool:
        """Re-run the width-driven reflow — unless a chosen shape is in force.

        Both the compact shape and Extended's column lock are *chosen* — by the
        window shrinking, or by the user's preference — rather than derived from
        the room available, so the reflow stands down entirely while either lasts.
        Without this the panel would flip itself back out of the chosen shape the
        first time it was resized.
        """
        if self.shape_locked:
            return False
        return super().sync_reflow()

    @property
    def shape_locked(self) -> bool:
        """Whether this panel's arrangement is chosen rather than derived.

        True under Compact and under Extended alike. What it is for is telling the
        view how much width to offer: a panel that will not reflow wants what it
        actually asks for, never the width of an arrangement it is not going to
        adopt.
        """
        return self._compact or self._column_locked

    def set_compact(self, compact: bool) -> None:
        """Whether the *window* has shrunk to this roller alone."""
        self._window_compact = bool(compact)
        self._apply_shape()

    def set_layout_preference(self, layout: str) -> None:
        """The shape the user has asked for everywhere, always — a ``DICE_LAYOUT_*``.

        The other of the two reasons this panel might be compact, and they are kept
        apart on purpose: leaving compact mode must not undo the preference, which
        one shared flag would have done the first time someone expanded the window.
        """
        self._preference = layout if layout in storage.DICE_LAYOUTS else storage.DICE_LAYOUT_AUTO
        self._apply_shape()

    def _apply_shape(self) -> None:
        """Put the parts in the compact shape — the Roll box, then quick rolls | die.

        The third arrangement, beside the reflow's row and column: the settings
        across the top with the quick rolls and the die side by side under them, so
        the roller is as short as it can be while still showing every control. The
        quick rolls take the slack because their chips wrap; the die keeps its fixed
        square, at ``die.size.compact`` rather than its full size.

        Only the layout changes — like :meth:`apply_reflow`, nothing is rebuilt, so
        a roll in flight, a loaded spec and the quick-roll strip all survive the
        move. The parts *are* re-parented here (into :attr:`_pair` and back), which
        the reflow never does; that is why each direction ends by handing them back
        to a layout before the old one can be emptied.
        """
        compact = self._window_compact or self._preference == storage.DICE_LAYOUT_COMPACT
        # Extended keeps the controls stacked; the view puts the history beside
        # them. Compact wins outright — a window shrunk to the roller alone has no
        # room for anything else, whatever the preference says.
        column_locked = not compact and self._preference == storage.DICE_LAYOUT_EXTENDED
        if compact == self._compact and column_locked == self._column_locked:
            return
        self._column_locked = column_locked
        if compact == self._compact:
            # Only the lock moved, so the parts are already where they belong and
            # all that is left is to settle the axis it demands.
            if column_locked:
                self.force_reflow(False)
            else:
                self.sync_reflow()
            self.updateGeometry()
            self.contentChanged.emit()
            return
        self._compact = compact
        if compact:
            self._box.removeWidget(self._quick_part)
            self._box.removeWidget(self._die_part)
            self._pair_box.addWidget(self._quick_part, stretch=1)
            self._pair_box.addWidget(self._die_part, stretch=0)
            self._pair_box.setAlignment(self._quick_part, Qt.AlignmentFlag.AlignTop)
            self._pair_box.setAlignment(self._die_part, Qt.AlignmentFlag.AlignTop)
            self._pair.setVisible(True)
            self._box.setDirection(QBoxLayout.Direction.TopToBottom)
            for index in reversed(range(self._box.count())):
                if self._box.itemAt(index).widget() is None:
                    self._box.takeAt(index)
            self._box.addWidget(self._pair)
            self._box.setStretchFactor(self._settings_part, 0)
            self._box.setStretchFactor(self._pair, 0)
            self._set_die_size("die.size.compact", "size.dice-face.compact")
        else:
            self._pair_box.removeWidget(self._quick_part)
            self._pair_box.removeWidget(self._die_part)
            self._box.removeWidget(self._pair)
            self._pair.setVisible(False)
            self._box.addWidget(self._die_part)
            self._box.addWidget(self._quick_part)
            self._set_die_size("die.size", "size.dice-face")
            # The reflow owns the arrangement again, and the width it last decided
            # from is the mini window's — so re-derive it rather than trusting it.
            self.apply_reflow(self.is_row)
            if column_locked:
                self.force_reflow(False)
            else:
                self.sync_reflow()
        self.updateGeometry()
        self.contentChanged.emit()

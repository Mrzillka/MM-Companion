"""The roller's two rows of controls: the modifier with Actions, the DC with Take 10.

    Modifier  [−5][−1] [ +0 ] [+1][+5]   Actions ▾
    DC        [−5][−1] [ —  ] [+1][+5]   Take 10

What changes the roll is the first row; what it is rolled against, and how, the second
(the user's layout, 2026-11-11). Both rows sit on **one grid**, so the DC box is under
the modifier box, every step under its twin, and the two buttons share a right edge.

**Narrow, the buttons drop to a row of their own** under the DC row — Take 10 on the
left, Actions on the right, where it was. That is an adaptive decision, so it is a
:class:`~mm_companion.ui.reflow.ReflowBox` (hysteresis) with a ``@no_reentry`` sync,
and it reports the narrow arrangement's width as its minimum whatever it is showing.

With the slider chosen for the modifier (Settings ▸ General), the slider takes the
penalty steps' columns and the box takes its own and the bonus steps', so the box still
starts where the DC box does. The DC always has steps: DCs are exact numbers the GM
announces, and a slider is no way to hit 17.

The panel owns the widgets and their signals; this only places them.
"""

from __future__ import annotations

from collections.abc import Sequence

from PySide6.QtCore import QEvent, QPoint, QRect, QSize, Qt
from PySide6.QtWidgets import QFrame, QGridLayout, QWidget

from mm_companion.ui import theme
from mm_companion.ui.reflow import ReflowBox
from mm_companion.ui.widgets import no_reentry

#: The grid's rows: the modifier, the DC, and (narrow only) the two buttons.
MODIFIER_ROW = 0
DC_ROW = 1
BUTTON_ROW = 2


def _min_width(widget: QWidget) -> int:
    """What *widget* can be squeezed to, as a layout reckons it: its set minimum if
    it has one, else its hint's."""
    return widget.minimumWidth() or widget.minimumSizeHint().width()


class RollControls(ReflowBox, QWidget):
    """The modifier and DC rows on one grid (see the module docstring)."""

    def __init__(
        self,
        *,
        modifier_label: QWidget,
        modifier_box: QWidget,
        modifier_slider: QWidget,
        actions: QWidget,
        dc_label: QWidget,
        dc_box: QWidget,
        take_ten: QWidget,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        # The capsule a set DC is drawn as, made first so it stacks *under* the DC row
        # (:meth:`place_capsule` keeps it there); the panel paints it.
        self.capsule = QFrame(self)
        self.capsule.setObjectName("dcPair")
        self.capsule.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

        self._modifier_label = modifier_label
        self._modifier_box = modifier_box
        self._slider = modifier_slider
        self._actions = actions
        self._dc_label = dc_label
        self._dc_box = dc_box
        self._take_ten = take_ten
        self._modifier_steps: list[QWidget] = []
        self._dc_steps: list[QWidget] = []
        self._slider_on = False
        # Wide: the buttons in their own column. Set by the reflow (``apply_reflow``).
        self._wide = False
        # How many grid columns the last placement set a stretch on, to clear them.
        self._columns = 0

        self._grid = QGridLayout(self)
        # Room right of and under the DC row for its capsule, which stands a little proud
        # of the row: it is drawn by this widget, so it cannot reach past its edge.
        pad = int(theme.metric("space.xs"))
        self._grid.setContentsMargins(0, 0, pad, pad)
        self._grid.setHorizontalSpacing(int(theme.metric("space.xs")))
        self._grid.setVerticalSpacing(int(theme.metric("space.sm")))
        for widget in (dc_label, dc_box):
            widget.installEventFilter(self)
        self.init_reflow(row=False)

    # -- what goes where ------------------------------------------------------

    def set_steps(self, modifier: Sequence[QWidget], dc: Sequence[QWidget]) -> None:
        """Place the step buttons, each list left to right: its penalties, then its
        bonuses, as many of each. Steps placed before are only taken out of the grid;
        the panel discards them."""
        for widget in (*self._modifier_steps, *self._dc_steps):
            self._grid.removeWidget(widget)
        self._modifier_steps = list(modifier)
        self._dc_steps = list(dc)
        for widget in self._dc_steps:
            widget.installEventFilter(self)
        self._place()
        # What the wide shape needs has moved, and no resize is coming to say so.
        self.sync_reflow()

    def set_slider(self, on: bool) -> None:
        """Lay the modifier row out for its slider (``True``) or its steps."""
        if on == self._slider_on:
            return
        self._slider_on = on
        self._place()
        self.sync_reflow()

    @property
    def wide(self) -> bool:
        """Whether the two buttons are in their own column (else a row underneath)."""
        return self._wide

    def _side(self) -> int:
        """How many steps on each side of a box."""
        return max(len(self._modifier_steps), len(self._dc_steps)) // 2

    def _place(self) -> None:
        """Put every widget in its cell for the current mode — slider or steps, wide or
        narrow. Nothing is reparented: they all belong to this widget already."""
        grid = self._grid
        everything = (
            self._modifier_label,
            self._modifier_box,
            self._slider,
            self._actions,
            self._dc_label,
            self._dc_box,
            self._take_ten,
            *self._modifier_steps,
            *self._dc_steps,
        )
        for widget in everything:
            grid.removeWidget(widget)
        for column in range(self._columns):
            grid.setColumnStretch(column, 0)

        side = self._side()
        box = side + 1
        last = 2 * side + 1  # the last bonus step's column
        buttons = last + 1
        self._columns = buttons + 1

        grid.addWidget(self._modifier_label, MODIFIER_ROW, 0)
        grid.addWidget(self._dc_label, DC_ROW, 0)
        # The DC row: its steps either side of the box.
        self._place_steps(self._dc_steps, DC_ROW, side)
        grid.addWidget(self._dc_box, DC_ROW, box)
        # The modifier row: the same, or the slider over the penalty steps' columns and
        # the box over its own and the bonus steps'. Without steps there is no column
        # for a slider, so the box stands alone.
        slider = self._slider_on and side > 0
        if slider:
            grid.addWidget(self._slider, MODIFIER_ROW, 1, 1, side)
            grid.addWidget(self._modifier_box, MODIFIER_ROW, box, 1, side + 1)
        else:
            self._place_steps(self._modifier_steps, MODIFIER_ROW, side)
            grid.addWidget(self._modifier_box, MODIFIER_ROW, box)

        # A step takes one share of the row, a box two: the field is the row's one box.
        for column in range(1, last + 1):
            grid.setColumnStretch(column, 2 if column == box else 1)

        if self._wide:
            # Right after the last step: the buttons are boxed and the steps flat, which
            # tells them apart without a gap the right-hand strip has no room for.
            grid.addWidget(self._actions, MODIFIER_ROW, buttons)
            grid.addWidget(self._take_ten, DC_ROW, buttons)
        else:
            # Under the DC row, Take 10 under the label and penalties, Actions at the
            # right edge where it was.
            grid.addWidget(self._take_ten, BUTTON_ROW, 0, 1, box)
            grid.addWidget(self._actions, BUTTON_ROW, box, 1, last - box + 1)
        self._slider.setVisible(slider)
        self.updateGeometry()

    def _place_steps(self, steps: Sequence[QWidget], row: int, side: int) -> None:
        half = len(steps) // 2
        for index, widget in enumerate(steps[:half]):
            self._grid.addWidget(widget, row, 1 + (side - half) + index)
        for index, widget in enumerate(steps[half:]):
            self._grid.addWidget(widget, row, side + 2 + index)

    # -- the reflow -----------------------------------------------------------

    def reflow_parts(self) -> Sequence[QWidget]:
        return ()

    def apply_reflow(self, row: bool) -> None:
        self._wide = row
        self._place()

    def _fields_width(self, measure) -> int:  # noqa: ANN001 - a width getter
        """The label column and the steps-and-box columns, with the spacing between."""
        spacing = self._grid.horizontalSpacing()
        label = max(measure(self._modifier_label), measure(self._dc_label))
        side = self._side()
        steps = []
        for index in range(2 * side):
            twins = [
                group[index]
                for group in (self._modifier_steps, self._dc_steps)
                if index < len(group)
            ]
            steps.append(max(measure(widget) for widget in twins))
        box = max(measure(self._modifier_box), measure(self._dc_box))
        return label + sum(steps) + box + spacing * (2 * side + 1)

    def _buttons_width(self, measure) -> int:  # noqa: ANN001 - a width getter
        return max(measure(self._actions), measure(self._take_ten))

    def _wide_width(self, measure) -> int:  # noqa: ANN001 - a width getter
        spacing = self._grid.horizontalSpacing()
        return self._fields_width(measure) + spacing + self._buttons_width(measure)

    def row_minimum_width(self) -> int:
        """What the buttons need to sit beside the rows: every column at its minimum."""
        return self._wide_width(_min_width)

    def row_natural_width(self) -> int:
        return self._wide_width(lambda widget: widget.sizeHint().width())

    def column_minimum_width(self) -> int:
        """The narrow arrangement: the rows, or the two buttons side by side."""
        spacing = self._grid.horizontalSpacing()
        pair = _min_width(self._actions) + spacing + _min_width(self._take_ten)
        # Plus the margins: this is the widget's own minimum, not the room inside it.
        margins = self._grid.contentsMargins()
        return max(self._fields_width(_min_width), pair) + margins.left() + margins.right()

    @no_reentry
    def sync_reflow(self) -> bool:
        return super().sync_reflow()

    def resizeEvent(self, event) -> None:  # noqa: ANN001, N802 - Qt override
        super().resizeEvent(event)
        self.sync_reflow()
        self.place_capsule()

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt override
        """Ask for the room to have the buttons beside the rows — a preference, so the
        roller is offered that width even while it is showing the narrow shape."""
        hint = super().sizeHint()
        return QSize(max(hint.width(), self.row_natural_width()), hint.height())

    # -- the DC capsule -------------------------------------------------------

    def place_capsule(self) -> None:
        """Lay the DC capsule over the DC row — its label to its last step — a little
        proud of them."""
        row = [self._dc_label, self._dc_box, *self._dc_steps]
        rect = QRect()
        for widget in row:
            if widget.isHidden():
                continue
            rect = rect.united(QRect(widget.mapTo(self, QPoint(0, 0)), widget.size()))
        pad = int(theme.metric("space.xs"))
        self.capsule.setGeometry(rect.adjusted(0, -pad, pad, pad))
        self.capsule.lower()

    def eventFilter(self, watched, event) -> bool:  # noqa: ANN001, N802 - Qt override
        if event.type() in (QEvent.Type.Move, QEvent.Type.Resize):
            self.place_capsule()
        return super().eventFilter(watched, event)

"""The roller's rows of controls: the modifier, the DC, and Actions under them.

    Modifier  [−5][−1] [+0] [+1][+5]
    DC        [−5][−1] [ —] [+1][+5]
              [        Actions ▾       ]

What changes the roll is the first row, what it is rolled against the next, and the
**Actions** button has a row of its own under them, as wide as the controls (the
user's call, 2026-11-13). **Take 10** used to sit beside the DC; it was little used for
the room it took, and is a tile in the Actions popup now. All of them sit on **one
grid**, so every box is under the one above it and every step under its twin. Each
row is a :class:`~mm_companion.ui.roller.fields.FieldRow`.

**The modifier can be two rows** (Settings ▸ General, the user's call, 2026-11-13):
**Bonus** and **Penalty**, as the roller had before the combat-maneuvers rework.

**With the slider chosen** every row is ``label [════slider════] [box]``, the DC's
included; the box keeps its own size, a two-digit number and its sign, and the slider
takes the rest (it was the other way round, and the slider was a sliver).

**Narrow, the outer steps go** (``−1 [box] +1``): the roller floated in a window
thinner than its recommended size used to clip its right edge (the user's report,
2026-11-13). That is an adaptive decision, so it has a hysteresis dead-band and the
sync is ``@no_reentry``, and the minimum reported is the narrowest arrangement
whichever is showing. (Before Actions had its own row, the two buttons also moved
between a column beside the rows and a row under them; that decision is gone.)

The panel owns the widgets and their signals; this only places them.
"""

from __future__ import annotations

from collections.abc import Sequence

from PySide6.QtCore import QEvent, QPoint, QRect, QSize, Qt, QTimer
from PySide6.QtWidgets import QFrame, QGridLayout, QWidget

from mm_companion.ui import theme
from mm_companion.ui.reflow import ReflowBox, prefers_row
from mm_companion.ui.roller.fields import FieldRow
from mm_companion.ui.widgets import no_reentry


def _min_width(widget: QWidget) -> int:
    """What *widget* can be squeezed to, as a layout reckons it: its set minimum if
    it has one, else its hint's."""
    return widget.minimumWidth() or widget.minimumSizeHint().width()


def _hint_width(widget: QWidget) -> int:
    return widget.sizeHint().width()


class RollControls(QWidget):
    """The roller's field rows and the Actions button on one grid (see the module
    docstring)."""

    #: The dead-band round the tight decision, as wide as a scrollbar or more.
    HYSTERESIS = ReflowBox.REFLOW_HYSTERESIS

    def __init__(
        self,
        *,
        modifier: FieldRow,
        bonus: FieldRow,
        penalty: FieldRow,
        dc: FieldRow,
        actions: QWidget,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        # The capsule a set DC is drawn as, made first so it stacks *under* the DC row
        # (:meth:`place_capsule` keeps it there); the panel paints it.
        self.capsule = QFrame(self)
        self.capsule.setObjectName("dcPair")
        self.capsule.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

        self._modifier = modifier
        self._bonus = bonus
        self._penalty = penalty
        self._dc = dc
        self._actions = actions
        self._slider_on = False
        self._split = False
        # Tight: only the innermost step either side of each box (``sync_reflow``).
        self._tight = False
        # How many grid columns the last placement set a stretch on.
        self._columns = 0

        self._grid = QGridLayout(self)
        # Room right of and under the DC row for its capsule, which stands a little proud
        # of the row: it is drawn by this widget, so it cannot reach past its edge.
        pad = int(theme.metric("space.xs"))
        self._grid.setContentsMargins(0, 0, pad, pad)
        self._grid.setHorizontalSpacing(int(theme.metric("space.xs")))
        self._grid.setVerticalSpacing(int(theme.metric("space.sm")))
        for row in self.all_rows():
            for widget in row.widgets():
                self._adopt(widget)
        self._adopt(actions)
        for widget in (dc.label, dc.box, dc.slider):
            widget.installEventFilter(self)
        # A box's own style or font moving (a tint, a theme) moves its widest value.
        for row in self.all_rows():
            if row is not dc:
                row.box.installEventFilter(self)
        self._place()

    def _adopt(self, widget: QWidget) -> None:
        """Parent *widget* here, hidden until a placement shows it: a widget with no
        parent is a window."""
        if widget.parent() is not self:
            widget.hide()
            widget.setParent(self)

    # -- what goes where ------------------------------------------------------

    def all_rows(self) -> tuple[FieldRow, ...]:
        return (self._modifier, self._bonus, self._penalty, self._dc)

    def rows(self) -> tuple[FieldRow, ...]:
        """The rows showing, top to bottom."""
        if self._split:
            return (self._bonus, self._penalty, self._dc)
        return (self._modifier, self._dc)

    def set_steps(self, steps: dict[str, Sequence[QWidget]]) -> None:
        """Give each row (by key) its step buttons, penalties then bonuses. Steps a row
        had before are only taken out of the grid; the panel discards them."""
        for row in self.all_rows():
            if row.key not in steps:
                continue
            for widget in row.steps:
                self._grid.removeWidget(widget)
            row.steps = list(steps[row.key])
            for widget in row.steps:
                self._adopt(widget)
            if row is self._dc:
                for widget in row.steps:
                    widget.installEventFilter(self)
        self._place()
        # What the full steps need has moved, and no resize is coming to say so.
        self.sync_reflow()

    def set_slider(self, on: bool) -> None:
        """Lay every row out for its slider (``True``) or its steps."""
        if on == self._slider_on:
            return
        self._slider_on = on
        self._place()
        self.sync_reflow()

    def set_split(self, on: bool) -> None:
        """Show Bonus and Penalty rows (``True``) or the one Modifier row."""
        if on == self._split:
            return
        self._split = on
        self._place()
        self.sync_reflow()

    @property
    def tight(self) -> bool:
        """Whether only the innermost step is showing either side of each box."""
        return self._tight

    def _split_steps(self, row: FieldRow, *, tight: bool) -> tuple[list, list]:
        """The row's penalty and bonus steps that show, outermost first and last."""
        if self._slider_on:
            return [], []
        half = len(row.steps) // 2
        penalties, bonuses = row.steps[:half], row.steps[half:]
        if tight:
            penalties, bonuses = penalties[-1:], bonuses[:1]
        return list(penalties), list(bonuses)

    def _side(self, *, tight: bool) -> int:
        """How many steps show on each side of a box."""
        return max((len(self._split_steps(row, tight=tight)[0]) for row in self.rows()), default=0)

    def _fit_boxes(self) -> None:
        """Every box as wide as the widest of them needs — its widest value and sign —
        so they stand in one column, and none stretches."""
        boxes = [row.box for row in self.all_rows()]
        for box in boxes:
            box.setMinimumWidth(0)
            box.setMaximumWidth(16777215)
        width = max(box.sizeHint().width() for box in boxes)
        for box in boxes:
            box.setFixedWidth(width)

    def _place(self) -> None:
        """Put every widget in its cell for the current mode — rows, slider or steps,
        tight or not. Nothing is reparented: they all belong here."""
        grid = self._grid
        for row in self.all_rows():
            for widget in row.widgets():
                grid.removeWidget(widget)
        grid.removeWidget(self._actions)
        for column in range(self._columns):
            grid.setColumnStretch(column, 0)
        self._fit_boxes()

        rows = self.rows()
        shown: list[QWidget] = []
        if self._slider_on:
            slider_column, box = 1, 2
            last = box
            for index, row in enumerate(rows):
                grid.addWidget(row.label, index, 0)
                grid.addWidget(row.slider, index, slider_column)
                grid.addWidget(row.box, index, box)
                shown += [row.label, row.slider, row.box]
            grid.setColumnStretch(slider_column, 1)
        else:
            side = self._side(tight=self._tight)
            box = side + 1
            last = 2 * side + 1  # the last bonus step's column
            for index, row in enumerate(rows):
                penalties, bonuses = self._split_steps(row, tight=self._tight)
                grid.addWidget(row.label, index, 0)
                for offset, widget in enumerate(penalties):
                    grid.addWidget(widget, index, 1 + (side - len(penalties)) + offset)
                grid.addWidget(row.box, index, box)
                for offset, widget in enumerate(bonuses):
                    grid.addWidget(widget, index, box + 1 + offset)
                shown += [row.label, row.box, *penalties, *bonuses]
            # The steps share out the row; the box keeps its own width.
            for column in range(1, last + 1):
                if column != box:
                    grid.setColumnStretch(column, 1)
        self._columns = last + 1

        # Actions under the last row, under the controls rather than the labels.
        grid.addWidget(self._actions, len(rows), 1, 1, last)

        showing = set(map(id, shown))
        for row in self.all_rows():
            for widget in row.widgets():
                widget.setVisible(id(widget) in showing)
        self._actions.show()
        self.updateGeometry()
        self.place_capsule()

    # -- the reflow -----------------------------------------------------------

    def _controls_width(self, measure, *, tight: bool) -> int:  # noqa: ANN001 - a width getter
        """Everything right of the labels: the slider-or-steps and box columns with the
        spacing between them, or the Actions button under them if that is wider."""
        spacing = self._grid.horizontalSpacing()
        rows = self.rows()
        box = max(measure(row.box) for row in rows)
        if self._slider_on:
            slider = max(measure(row.slider) for row in rows)
            controls = slider + box + spacing
        else:
            columns: list[int] = []
            for row in rows:
                penalties, bonuses = self._split_steps(row, tight=tight)
                for index, widget in enumerate([*penalties, *bonuses]):
                    if index == len(columns):
                        columns.append(0)
                    columns[index] = max(columns[index], measure(widget))
            controls = sum(columns) + box + spacing * len(columns)
        return max(controls, measure(self._actions))

    def _fields_width(self, measure, *, tight: bool) -> int:  # noqa: ANN001 - a width getter
        """The label column and everything right of it, with the spacing."""
        label = max(measure(row.label) for row in self.rows())
        return label + self._grid.horizontalSpacing() + self._controls_width(measure, tight=tight)

    def _margins(self) -> int:
        margins = self._grid.contentsMargins()
        return margins.left() + margins.right()

    def available_width(self) -> int:
        """The width the columns have to share, net of the margins."""
        return self.width() - self._margins()

    def natural_width(self) -> int:
        """What the full steps want, margins included."""
        return self._fields_width(_hint_width, tight=False) + self._margins()

    def narrowest_width(self) -> int:
        """The narrowest arrangement, the tight rows, margins included."""
        return self._fields_width(_min_width, tight=True) + self._margins()

    @no_reentry
    def sync_reflow(self) -> bool:
        """Show only the innermost steps when the full ones do not fit; ``True`` if
        that changed."""
        full = self._fields_width(_min_width, tight=False)
        tight = not prefers_row(self.available_width(), full, not self._tight, self.HYSTERESIS)
        if tight == self._tight:
            return False
        self._tight = tight
        self._place()
        return True

    def resizeEvent(self, event) -> None:  # noqa: ANN001, N802 - Qt override
        super().resizeEvent(event)
        self.sync_reflow()
        self.place_capsule()

    def changeEvent(self, event) -> None:  # noqa: ANN001, N802 - Qt override
        super().changeEvent(event)
        # A theme or font switch moves what the widest value needs.
        if event.type() in (QEvent.Type.FontChange, QEvent.Type.StyleChange):
            self._place()

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt override
        """Ask for the room to have the full steps — a preference, so the roller is
        offered that width even while it is showing the tight shape."""
        hint = super().sizeHint()
        return QSize(max(hint.width(), self.natural_width()), hint.height())

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt override
        """The tight shape's width, whichever is showing: a minimum never moves with an
        adaptive decision."""
        return QSize(self.narrowest_width(), super().minimumSizeHint().height())

    # -- the DC capsule -------------------------------------------------------

    def place_capsule(self) -> None:
        """Lay the DC capsule over the DC row — its label to its last control — a little
        proud of them."""
        row = [self._dc.label, self._dc.box, self._dc.slider, *self._dc.steps]
        rect = QRect()
        for widget in row:
            if widget.isHidden():
                continue
            rect = rect.united(QRect(widget.mapTo(self, QPoint(0, 0)), widget.size()))
        pad = int(theme.metric("space.xs"))
        self.capsule.setGeometry(rect.adjusted(0, -pad, pad, pad))
        self.capsule.lower()

    def eventFilter(self, watched, event) -> bool:  # noqa: ANN001, N802 - Qt override
        kind = event.type()
        if kind in (QEvent.Type.Move, QEvent.Type.Resize):
            self.place_capsule()
        elif kind in (QEvent.Type.StyleChange, QEvent.Type.FontChange, QEvent.Type.Polish):
            if any(watched is row.box for row in self.all_rows()):
                # Next pass: the box's line edit is polished after the box, and its
                # margins are part of what the box needs.
                QTimer.singleShot(0, self, self._refit_boxes)
        return super().eventFilter(watched, event)

    def showEvent(self, event) -> None:  # noqa: ANN001, N802 - Qt override
        super().showEvent(event)
        QTimer.singleShot(0, self, self._refit_boxes)

    @no_reentry
    def _refit_boxes(self) -> None:
        """Fit the boxes again if what the widest of them needs has moved. Only then:
        fitting resizes them, and a resize must not loop back here. The boxes are fitted
        once as they are placed, which can be before they are polished (a tint, the
        theme's arrowless rule), and so measured a pixel or two off."""
        boxes = [row.box for row in self.all_rows()]
        wanted = max(box.sizeHint().width() for box in boxes)
        if any(box.minimumWidth() != wanted for box in boxes):
            self._fit_boxes()

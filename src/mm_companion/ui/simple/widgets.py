"""The handful of shapes every simple view is built out of.

A printed sheet has very few kinds of thing on it: a **stat box** (a name over a big
number — STR 4, Dodge 12), a **line** (a name, a value at the right, and small print
under it — Acrobatics +8, Improved Initiative 2), and a **list of lines in columns**.
They live here so the stat blocks, Skills and Advantages all read as one sheet.

A value that can be rolled is rolled the way it is everywhere else on the sheet — one
click loads it into the roller, two throw it — through
:func:`~mm_companion.ui.roll_click.attach_roll_click`, and only while the view has a
live section to spend through (a printed page has none).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QResizeEvent
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from mm_companion.core.rules import RollSpec
from mm_companion.ui import theme
from mm_companion.ui.roll_click import ROLL_TOOLTIP, attach_roll_click
from mm_companion.ui.sections.column_flow import column_count, even_split
from mm_companion.ui.simple.registry import SimpleContext
from mm_companion.ui.simple.style import set_font, term_label, tint, value_label
from mm_companion.ui.widgets import discard_widget, no_reentry

SpecFactory = Callable[[], "RollSpec | None"]


def _hover_style(name: str) -> str:
    """The quiet wash a rollable line or box wears under the pointer."""
    radius = int(theme.metric("radius.chip"))
    return (
        f"#{name} {{ border-radius: {radius}px; }}"
        f"#{name}:hover {{ background: {theme.wash('accent.dice', 0.12)}; }}"
    )


def make_rollable(widget: QWidget, context: SimpleContext, factory: SpecFactory) -> None:
    """Load *factory*'s spec on a click and throw it on a double-click — when live.

    A no-op on paper: a printed box is not a button, and a hover wash on a page that
    will never see a pointer is noise.
    """
    if not context.can_roll():
        return
    widget.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
    widget.setStyleSheet(_hover_style(widget.objectName()))
    widget.setCursor(Qt.CursorShape.PointingHandCursor)
    # Kept on the widget, so a box that rewrites its tooltip on every refresh (a stat
    # box, whose hint names the condition moving it) can say how it rolls as well.
    widget.setProperty("rollHint", ROLL_TOOLTIP)
    attach_roll_click(widget, factory, context.roll, load_sink=context.load)


class StatBox(QFrame):
    """A trait's name in small capitals over its value, big — one box of a stat block."""

    def __init__(self, caption: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("simpleStat")
        width = int(theme.metric("simple.value-box"))
        self.setFixedWidth(width)
        layout = QVBoxLayout(self)
        pad = int(theme.metric("space.xs"))
        layout.setContentsMargins(pad, pad, pad, pad)
        layout.setSpacing(0)
        self._caption = QLabel(caption)
        set_font(self._caption, "size.simple-heading", bold=True)
        self._caption.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self._value = value_label()
        self._value.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self._note = term_label(wrap=False)
        self._note.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        layout.addWidget(self._caption)
        layout.addWidget(self._value)
        layout.addWidget(self._note)
        self._note.hide()

    def set_value(self, text: str, *, moved: int = 0, note: str = "", tooltip: str = "") -> None:
        """Show *text*; *moved* tints it (+ boosted, - penalised); *note* is small print."""
        self._value.setText(text)
        tint(self._value, moved)
        self._note.setText(note)
        self._note.setVisible(bool(note))
        if tooltip:
            hint = self.property("rollHint")
            self.setToolTip("\n".join((tooltip, hint)) if hint else tooltip)

    def value_text(self) -> str:
        return self._value.text()


class CaptionBox(QFrame):
    """A small caption over any widget — a stat box whose value is a control.

    The hero-point pips are the case: they sit in the same row as Power Level and
    Initiative and read like them, but what is under the caption is five switches.
    """

    def __init__(self, caption: str, content: QWidget, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("simpleCaptionBox")
        layout = QVBoxLayout(self)
        pad = int(theme.metric("space.xs"))
        layout.setContentsMargins(pad, pad, pad, pad)
        layout.setSpacing(int(theme.metric("space.xs")))
        self.caption = QLabel(caption)
        set_font(self.caption, "size.simple-heading", bold=True)
        self.caption.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        layout.addWidget(self.caption)
        layout.addWidget(content, alignment=Qt.AlignmentFlag.AlignHCenter)
        self.content = content


class SimpleLine(QWidget):
    """One line of a list: a name, a value at the right, and small print under the name.

    The value column is optional (an advantage without ranks has nothing to put there)
    and so is the small print. The name wraps rather than holding the line open, since
    a line lives in a column whose width the block decides.
    """

    def __init__(
        self,
        name: str,
        value: str = "",
        terms: str = "",
        *,
        parent: QWidget | None = None,
        bold: bool = False,
        strike: bool = False,
        moved: int = 0,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("simpleLine")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(int(theme.metric("space.xs")), 0, int(theme.metric("space.xs")), 0)
        outer.setSpacing(0)
        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(int(theme.metric("space.sm")))
        self.name = QLabel(name)
        self.name.setWordWrap(True)
        self.name.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        set_font(self.name, "size.simple-label", bold=bold, strike=strike)
        top.addWidget(self.name, stretch=1)
        self.value: QLabel | None = None
        if value:
            self.value = value_label(value)
            self.value.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            tint(self.value, moved)
            top.addWidget(self.value)
        outer.addLayout(top)
        self.terms: QLabel | None = None
        if terms:
            self.terms = term_label(terms)
            outer.addWidget(self.terms)


class ColumnGrid(QWidget):
    """Lines dealt into as many columns as the width holds, top to bottom then across.

    Column-major, the way a printed skill list reads, and each column stands on its
    own: a long advantage in one column does not stretch the line beside it in the
    next, which a shared grid of rows did. The lines are divided so the columns come
    out as near the same height as they can without reordering anything
    (:func:`~mm_companion.ui.sections.column_flow.even_split`, the Skills block's own
    split).

    The count is :func:`~mm_companion.ui.sections.column_flow.column_count` — the same
    arithmetic and the same hysteresis band the Skills and Advantages blocks use — so
    a scrollbar appearing cannot flip it back and forth; and a re-deal only *moves* the
    existing widgets, which is cheap enough to do on every width change. Its minimum
    width is one column's, whatever it is showing: a minimum that moved with the
    column count would be reading a width it had itself just set.
    """

    SPACING = 8
    HYSTERESIS = 24

    def __init__(self, min_column: int, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._min_column = min_column
        self._items: list[QWidget] = []
        self._columns = 0
        self._row = QHBoxLayout(self)
        self._row.setContentsMargins(0, 0, 0, 0)
        self._row.setSpacing(self.SPACING)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)

    def set_items(self, items: Sequence[QWidget]) -> None:
        """Replace every line, discarding the old ones properly."""
        for widget in self._items:
            discard_widget(widget)
        self._items = list(items)
        self._columns = 0
        self._deal(self._count_for(self.width()))

    def items(self) -> list[QWidget]:
        return list(self._items)

    def column_count(self) -> int:
        return max(1, self._columns)

    def _count_for(self, width: int) -> int:
        return column_count(
            width,
            self._min_column,
            self.SPACING,
            len(self._items),
            self._columns,
            self.HYSTERESIS,
        )

    def _deal(self, columns: int) -> None:
        columns = max(1, columns)
        # Out of the old columns first (a parented widget is never left without a
        # parent here: each is re-added below before anything can show it).
        for widget in self._items:
            widget.hide()
        while self._row.count():
            item = self._row.takeAt(0)
            layout = item.layout()
            if layout is not None:
                while layout.count():
                    layout.takeAt(0)
                layout.deleteLater()
        weights = [max(1, widget.sizeHint().height()) for widget in self._items]
        spacing = int(theme.metric("simple.spacing"))
        for bucket in even_split(weights, columns):
            column = QVBoxLayout()
            column.setContentsMargins(0, 0, 0, 0)
            column.setSpacing(spacing)
            for index in bucket:
                column.addWidget(self._items[index])
            column.addStretch(1)
            self._row.addLayout(column, stretch=1)
        for widget in self._items:
            widget.show()
        self._columns = columns

    @no_reentry
    def _sync_columns(self) -> None:
        columns = self._count_for(self.width())
        if columns != self._columns:
            self._deal(columns)

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802 - Qt override
        super().resizeEvent(event)
        self._sync_columns()

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt override
        return QSize(min(self._min_column, 40), super().minimumSizeHint().height())


def clear_layout(layout) -> None:
    """Empty *layout*, discarding every widget it held and dropping nested layouts."""
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget()
        if widget is not None:
            discard_widget(widget)
            continue
        child = item.layout()
        if child is not None:
            clear_layout(child)

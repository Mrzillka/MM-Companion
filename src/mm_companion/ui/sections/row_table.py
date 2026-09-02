"""General machinery for the sheet's table blocks.

Four blocks — Abilities, Resistances, Advantages and Skills — are the same thing
seen four ways: an ordered list of rows in a :class:`QTableWidget` that shows all
of its content and never scrolls on its own. Each of them used to answer the
three questions a table block asks in its own way, and the answers disagreed: the
stat tables pinned their height once at build time while Advantages reported it
live; Advantages removed a row with a button and Skills with an inline ``✕``;
Advantages reordered with ``▲``/``▼`` and Skills not at all.

This module is the one answer to each, so a block adopts the behaviour instead of
growing its own copy of it — and so a future block constructor has something to
assemble a table block *out of*:

- :class:`AutoHeightTable` — the table itself, sized to its content.
- :class:`RowIndex` — which model object a rendered row stands for, which is not
  positional once a block fans its rows across side-by-side panels (see
  :mod:`~mm_companion.ui.sections.column_flow`).
- :func:`install_row_menu` — one right-click menu per table, composed from
  independent *contributors* so pinning and removing don't have to know about
  each other.
- :class:`RowReorder` — drag a row to a new place, across panels.
- :class:`SortControl` — the sort combo, and the standing rule that a preset sort
  mode turns hand reordering off.
- :func:`wrapping_column_width` — how wide a name column that *wraps* should be,
  which is the one answer to "the first column must not clip" both blocks used to
  give in opposite, wrong directions.

The stat-family helpers (building the ability/resistance grid, tinting a total,
the pin payload) stay in :mod:`~mm_companion.ui.sections.stat_table`, which is
the layer above this one.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator, Sequence
from typing import NamedTuple

from PySide6.QtCore import QMimeData, QPoint, QRect, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QDrag, QFontMetrics, QResizeEvent
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QMenu,
    QSizePolicy,
    QTableWidget,
    QWidget,
)

from mm_companion.ui.drop_feedback import DropFeedback, DropIndicator
from mm_companion.ui.wheel_guard import guard_wheel
from mm_companion.ui.widgets import no_reentry

#: How far the pointer must travel with the button down before a press counts as a
#: drag rather than a click. Matches the pinned strip's chips, so a row and a chip
#: feel the same to grab.
DRAG_THRESHOLD = 8

#: The insertion bar's thickness, in pixels.
INDICATOR_HEIGHT = 2

#: What every block's Manual (hand-ordered) sort mode is called. A block may name
#: its own preset modes whatever it likes; this one is shared because
#: :class:`RowReorder` is only enabled in it.
SORT_MANUAL = "manual"


#: How much narrower than a boundary the table has to be before it sheds another
#: column, and how much wider before it takes one back. The same dead-band, for
#: the same reason, as :func:`~mm_companion.ui.sections.column_flow.column_count`'s
#: and :func:`~mm_companion.ui.reflow.prefers_row`'s: shedding a column changes
#: what wraps, which changes the block's height, which can toggle a scrollbar,
#: which changes the width back over the boundary — an endless relayout otherwise.
SHED_HYSTERESIS = 24


def columns_to_shed(
    available: int,
    widths: Sequence[int],
    shed_order: Sequence[int],
    *,
    current: Sequence[int] = (),
    hysteresis: int = 0,
) -> tuple[int, ...]:
    """Which columns to hide so the rest fit in *available* pixels.

    *shed_order* is the columns a block is willing to give up, **worst first** —
    the abbreviation before the name, the modifier before the rank. Everything not
    named in it is load-bearing and is never hidden, so a table can always be
    dragged narrower than it can honestly show and the columns that remain are the
    ones worth keeping. Past that the block scrolls; nothing is ever lost.

    A non-positive *available* (a table that has not been laid out yet) sheds
    nothing, so the first paint is the full table and the real answer arrives on
    the first ``resizeEvent``.
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

    if not hysteresis or not current:
        return tuple(shed)
    # Only change the answer once the width is past the boundary by a full band in
    # the direction it is moving, or the count flips back and forth on its own.
    if len(shed) > len(current):
        deeper = tuple(shed)
        kept = sum(w for i, w in enumerate(widths) if i not in set(deeper))
        return deeper if available <= kept + hysteresis else tuple(current)
    if len(shed) < len(current):
        shallower = tuple(shed)
        kept = sum(w for i, w in enumerate(widths) if i not in set(shallower))
        return shallower if available >= kept + hysteresis else tuple(current)
    return tuple(shed)


class AutoHeightTable(QTableWidget):
    """A table that reports its full content size, so it never scrolls itself.

    The sheet's rule is that a block shows *all* of its content and the page
    scrolls when the blocks don't all fit. A table honours that by reporting the
    header plus its summed row heights as both its size hint and its minimum, with
    its own vertical scrollbar off — the enclosing block then grows as rows are
    added. Reported live, so it survives a theme switch, a font change and a row
    count that moves at runtime; the old ``fit_table_height`` measured once at
    build time, before the stylesheet had touched a row, which is why the two stat
    blocks needed a hardcoded height with "a little slack" in it.

    *word_wrap* re-measures wrapped rows on resize (their height depends on the
    stretched column's width). *fit_width* additionally reports the summed column
    widths as the minimum width — right for a table that is the whole block, and
    wrong for one panel of a column flow, whose section caps its own minimum at a
    single panel and would otherwise be pinned open by the widest of them.
    """

    def __init__(
        self,
        rows: int = 0,
        columns: int = 0,
        parent: QWidget | None = None,
        *,
        word_wrap: bool = False,
        fit_width: bool = False,
    ) -> None:
        super().__init__(rows, columns, parent)
        self._word_wrap = word_wrap
        self._fit_width = fit_width
        # Columns this table is willing to give up as it narrows, worst first.
        self._shed_order: tuple[int, ...] = ()
        self._shed: tuple[int, ...] = ()
        self._reorder: RowReorder | None = None
        self._press_pos: QPoint | None = None
        self._dragged = False
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        # How tall a wrapped row is depends on the width of the column it wraps in,
        # and that width settles *after* the rows are filled: a ResizeToContents
        # sibling measures the items it was just given, takes its share, and the
        # stretching column shrinks — with no resize of the table itself to notice.
        # Measuring on the table's own geometry alone therefore sized every row for
        # a column wider than it ended up, which is a name cut off in a row that
        # looks like it wrapped. Following the header instead catches every cause,
        # coalesced to once a turn because a layout moves several sections.
        self._remeasure: QTimer | None = None
        if word_wrap:
            self._remeasure = QTimer(self)
            self._remeasure.setSingleShot(True)
            self._remeasure.timeout.connect(self.remeasure_wrapped_rows)
            self.horizontalHeader().sectionResized.connect(lambda *_: self._remeasure.start(0))

    # -- content sizing ------------------------------------------------------

    def _content_height(self) -> int:
        height = 2 * self.frameWidth()
        header = self.horizontalHeader()
        # ``isHidden``, not ``isVisible``: a table that has not been shown yet has no
        # visible children at all, and that is precisely when its minimum is first
        # asked for — reading visibility there loses the header row's height and the
        # block opens a header short.
        if not header.isHidden():
            height += header.height()
        for row in range(self.rowCount()):
            height += self.rowHeight(row)
        return height

    def _content_width(self) -> int:
        """The width every visible column needs, header text included."""
        width = 2 * self.frameWidth()
        header = self.horizontalHeader()
        vertical = self.verticalHeader()
        if not vertical.isHidden():
            width += vertical.width()
        for column in range(self.columnCount()):
            if self.isColumnHidden(column):
                continue
            width += max(self.sizeHintForColumn(column), header.sectionSizeHint(column))
        return width

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt override
        return QSize(super().sizeHint().width(), self._content_height())

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt override
        hint = super().minimumSizeHint()
        width = max(hint.width(), self._content_width()) if self._fit_width else hint.width()
        return QSize(width, self._content_height())

    def set_shed_order(self, columns: Sequence[int]) -> None:
        """Name the columns this table may hide as it narrows, **worst first**.

        A table that names none never hides anything and simply scrolls when it
        runs out of room, which is the right answer for one whose every column is
        load-bearing. Call it once, after the columns exist.
        """
        self._shed_order = tuple(columns)
        self.sync_shed_columns()

    def natural_column_widths(self) -> list[int]:
        """What each column would take if it were showing, header text included.

        Measured whether or not the column is currently hidden — a hidden one has
        to be measurable, or the table could never work out that it now fits again
        and would stay shed forever.
        """
        header = self.horizontalHeader()
        return [
            max(self.sizeHintForColumn(column), header.sectionSizeHint(column))
            for column in range(self.columnCount())
        ]

    def _shed_available_width(self) -> int:
        width = self.viewport().width() or self.width()
        vertical = self.verticalHeader()
        if not vertical.isHidden():
            width -= vertical.width()
        return width - 2 * self.frameWidth()

    @no_reentry
    def sync_shed_columns(self) -> bool:
        """Hide or restore columns to suit the width. Returns whether it changed."""
        if not self._shed_order:
            return False
        wanted = columns_to_shed(
            self._shed_available_width(),
            self.natural_column_widths(),
            self._shed_order,
            current=self._shed,
            hysteresis=SHED_HYSTERESIS,
        )
        if wanted == self._shed:
            return False
        self._shed = wanted
        hidden = set(wanted)
        for column in self._shed_order:
            if 0 <= column < self.columnCount():
                self.setColumnHidden(column, column in hidden)
        self.updateGeometry()
        return True

    def shed_columns(self) -> tuple[int, ...]:
        """Which columns are hidden for want of room right now."""
        return self._shed

    def remeasure_wrapped_rows(self) -> None:
        """Re-fit every row to its wrapped text (a no-op unless *word_wrap*).

        Called for you whenever a column changes width; a block calls it directly
        after a rebuild, so the height is right without waiting for the event loop.
        """

        if not self._word_wrap:
            return
        for row in range(self.rowCount()):
            self.resizeRowToContents(row)
        self.updateGeometry()

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802 - Qt override
        super().resizeEvent(event)
        # Before the wrap measurement below: shedding a column changes how much
        # room the stretching one has, and therefore what wraps in it.
        if event.oldSize().width() != event.size().width():
            self.sync_shed_columns()
        # A wider/narrower table re-wraps its text, changing row heights, so
        # re-measure and let the block resize to the new content. Only on a *width*
        # change: wrapping depends on nothing else, and a panel of forty skill rows
        # is forty rows x six columns of delegate measuring, which a height-only
        # resize (the page relaying out around it) would otherwise pay for every
        # time. ``oldSize()`` is (-1, -1) on the first resize, so that one always
        # measures.
        if event.oldSize().width() != event.size().width():
            self.remeasure_wrapped_rows()
        self.updateGeometry()

    # -- drag to reorder -----------------------------------------------------

    def set_reorder(self, reorder: RowReorder | None) -> None:
        """Let rows be dragged out of and dropped into this table."""
        self._reorder = reorder
        self.setAcceptDrops(reorder is not None)
        self.viewport().setAcceptDrops(reorder is not None)

    def mousePressEvent(self, event) -> None:  # noqa: ANN001, N802 - Qt override
        if self._reorder is not None and event.button() == Qt.MouseButton.LeftButton:
            self._press_pos = event.position().toPoint()
            self._dragged = False
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: ANN001, N802 - Qt override
        """Past the threshold, hand the gesture to :class:`RowReorder`.

        ``QDrag.exec`` blocks until the drop, so everything after it has already
        happened; the release that ends the drag is swallowed below rather than
        read as a click that would load the row into the roller.
        """
        if self._reorder is not None and self._press_pos is not None and not self._dragged:
            travelled = (event.position().toPoint() - self._press_pos).manhattanLength()
            if travelled >= DRAG_THRESHOLD:
                self._dragged = True
                press_pos, self._press_pos = self._press_pos, None
                self._reorder.start_drag(self, press_pos)
                return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: ANN001, N802 - Qt override
        self._press_pos = None
        if self._dragged:
            # A drag is not a click: let it end without emitting cellClicked, which
            # would load the dragged row into the dice roller on every reorder.
            self._dragged = False
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def dragEnterEvent(self, event) -> None:  # noqa: ANN001, N802 - Qt override
        if self._reorder is None:
            super().dragEnterEvent(event)
            return
        self._reorder.drag_over(self, event)

    def dragMoveEvent(self, event) -> None:  # noqa: ANN001, N802 - Qt override
        if self._reorder is None:
            super().dragMoveEvent(event)
            return
        self._reorder.drag_over(self, event)

    def dragLeaveEvent(self, event) -> None:  # noqa: ANN001, N802 - Qt override
        if self._reorder is None:
            super().dragLeaveEvent(event)
            return
        self._reorder.drag_left(self)
        event.accept()

    def dropEvent(self, event) -> None:  # noqa: ANN001, N802 - Qt override
        if self._reorder is None:
            super().dropEvent(event)
            return
        self._reorder.drop(self, event)


def wrapping_column_width(
    metrics: QFontMetrics,
    texts: Iterable[str],
    *,
    padding: int,
    cap: int,
    floor: int = 0,
) -> int:
    """How wide a *wrapping* name column should be: the widest label, capped.

    A first column that must not clip has two ways out — grow without limit, or
    break the line. Growing is what pinned the Advantages block open (a
    ``ResizeToContents`` name column with a player's typed subject in it) and what
    squeezed a skill's name into ``"…"`` (a stretching name column paying for a
    long focus label out of room the panel never had), so both blocks break
    instead, and this is the arithmetic that decides where.

    *floor* wins over *cap*, and is what keeps the column sane when the labels are
    short: a header's own caption, a block's density metric. Past the cap the text
    wraps and the row grows taller, which is the sheet's own bargain — a block shows
    all of its content and the page scrolls.

    The cap is **hard**, and the one case it cannot answer is a single *word* wider
    than the column: Qt's word wrap breaks between words and not inside one, so such
    a label does not wrap at all, it elides. Flooring at the longest word was tried
    and is worse — it hands one typed 25-character word the power to pin the block
    (and the page behind it) open, which is the exact complaint this function exists
    to answer. So the answer to *that* case is a tooltip carrying the whole label,
    which every caller's name cell sets: elide-plus-tooltip, the same bargain
    :class:`~mm_companion.ui.widgets.ElidingLabel` strikes wherever a caption has
    to fit a box it cannot argue with.
    """

    widest = max((metrics.horizontalAdvance(text) for text in texts), default=0)
    return max(floor, min(widest + padding, cap))


class RowEntry(NamedTuple):
    """One rendered row and the model object it stands for.

    Deliberately a plain ``(table, row, key)`` triple: a block that fans its rows
    across side-by-side panels has no positional row → model mapping, and this is
    the record that replaces it.
    """

    table: QTableWidget
    row: int
    key: object


class RowIndex:
    """Every rendered row of one block, in the order the model has them.

    Filled during a rebuild and read back by everything that needs to answer
    "which item is this row" — the context menu, the drag, the selection restore.
    The order of the entries *is* the block's order, so a drag's source and target
    positions can be read straight off it.
    """

    def __init__(self) -> None:
        self._entries: list[RowEntry] = []

    def __iter__(self) -> Iterator[RowEntry]:
        return iter(self._entries)

    def __len__(self) -> int:
        return len(self._entries)

    def __getitem__(self, position: int) -> RowEntry:
        return self._entries[position]

    def clear(self) -> None:
        self._entries.clear()

    def add(self, table: QTableWidget, row: int, key: object) -> RowEntry:
        entry = RowEntry(table, row, key)
        self._entries.append(entry)
        return entry

    def entry_at(self, table: QTableWidget, row: int) -> RowEntry | None:
        for entry in self._entries:
            if entry.table is table and entry.row == row:
                return entry
        return None

    def key_at(self, table: QTableWidget, row: int) -> object | None:
        entry = self.entry_at(table, row)
        return None if entry is None else entry.key

    def find(self, key: object) -> RowEntry | None:
        """The entry standing for *key* — by identity first, then by equality.

        Identity first because a block may legitimately hold two equal items (the
        same advantage bought twice), and the caller that hands back an object it
        was given means *that* one.
        """
        for entry in self._entries:
            if entry.key is key:
                return entry
        for entry in self._entries:
            if entry.key == key:
                return entry
        return None

    def position(self, entry: RowEntry) -> int:
        """Where *entry* sits in the block's order (``-1`` when it is not here)."""
        for index, candidate in enumerate(self._entries):
            if candidate is entry:
                return index
        return -1

    def last_in(self, table: QTableWidget) -> RowEntry | None:
        """The final indexed row of *table* — where a drop below its rows lands."""
        for entry in reversed(self._entries):
            if entry.table is table:
                return entry
        return None


#: What a contributor is handed: the menu being built, and the row that was
#: right-clicked. It adds whatever actions it owns, or nothing at all.
MenuContributor = Callable[[QMenu, QTableWidget, int], None]


def build_row_menu(
    table: QTableWidget, row: int, contributors: tuple[MenuContributor, ...]
) -> QMenu:
    """The menu a right-click on *row* would show, composed from *contributors*.

    Built at click time rather than wired once, because what a row can do is
    answered *after* the block is built (the GM window says there is a card to pin
    to; a rebuild says which item this row now stands for). Contributors are
    independent — pinning knows nothing about removing — and are separated by a
    rule when more than one has something to say.

    Separate from :func:`install_row_menu` so a test can ask what a row offers
    without a modal ``exec`` it would then have to dismiss.
    """
    menu = QMenu(table)
    for contributor in contributors:
        before = len(menu.actions())
        contributor(menu, table, row)
        if before and len(menu.actions()) > before:
            menu.insertSeparator(menu.actions()[before])
    return menu


def install_row_menu(table: QTableWidget, *contributors: MenuContributor) -> None:
    """Offer a right-click menu on *table*'s rows (see :func:`build_row_menu`).

    One connection per table. A row every contributor passes on shows **no menu at
    all**, rather than an empty one.
    """

    def show(pos: QPoint) -> None:
        row = table.rowAt(pos.y())
        if row < 0:
            return
        menu = build_row_menu(table, row, contributors)
        if not menu.actions():
            return
        menu.exec(table.viewport().mapToGlobal(pos))

    table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
    table.customContextMenuRequested.connect(show)


def remove_contributor(
    label_of: Callable[[QTableWidget, int], str | None],
    on_remove: Callable[[QTableWidget, int], None],
) -> MenuContributor:
    """A "Remove …" action, worded by the row it is offered on.

    *label_of* returns the wording (``"Remove Acrobatics"``) or ``None`` for a row
    that cannot be removed — a separator, a group header, a row whose model object
    has gone. Confirmation, if a block wants one, belongs in *on_remove*: only the
    block knows what removing that row would cost.
    """

    def contribute(menu: QMenu, table: QTableWidget, row: int) -> None:
        label = label_of(table, row)
        if not label:
            return
        menu.addAction(label, lambda: on_remove(table, row))

    return contribute


class RowReorder:
    """Drag a row to a new place, across every panel of one block.

    A block's side-by-side panels are one ordered list split for display, so a row
    has to be draggable out of panel 1 and into panel 2. That is what makes this a
    controller over a *set* of tables rather than a property of one: it holds the
    block's :class:`RowIndex`, and every panel :meth:`attach`\\ es to it.

    *mime* is the block's own format, so two blocks can never accept each other's
    rows — the same discipline :mod:`mm_companion.ui.cards.drag` uses. *enabled*
    is asked at gesture time (a preset sort mode turns hand ordering off), and
    *accepts* lets a block refuse a particular pairing (a skill's focus dropped
    into another skill). A refusal is **shown**, never a bare ``ignore()``, which
    is invisible whenever an ancestor takes the drag instead.

    The block supplies *on_move* and does the actual reordering: this class knows
    where the row came from, where it is going and which side of the target it
    lands on, and nothing whatever about the model.
    """

    def __init__(
        self,
        mime: str,
        index: RowIndex,
        on_move: Callable[[RowEntry, RowEntry, bool], None],
        *,
        enabled: Callable[[], bool] | None = None,
        accepts: Callable[[RowEntry, RowEntry, bool], bool] | None = None,
    ) -> None:
        self._mime = mime
        self._index = index
        self._on_move = on_move
        self._enabled = enabled or (lambda: True)
        self._accepts = accepts or (lambda source, target, before: True)
        self._indicators: dict[QTableWidget, DropIndicator] = {}
        self._feedback: dict[QTableWidget, DropFeedback] = {}

    def attach(self, table: AutoHeightTable) -> None:
        """Make *table* both a source of dragged rows and a target for them."""
        table.set_reorder(self)
        self._indicators[table] = DropIndicator(table.viewport())
        self._feedback[table] = DropFeedback(
            table, table.metaObject().className(), radius="radius.card", border=False
        )

    # -- the drag source -----------------------------------------------------

    def start_drag(self, table: AutoHeightTable, pos: QPoint) -> None:
        """Lift the row under *pos* out of *table* (a no-op when it can't be)."""
        if not self._enabled():
            return
        entry = self._index.entry_at(table, table.rowAt(pos.y()))
        if entry is None:
            return
        position = self._index.position(entry)
        if position < 0:
            return
        drag = QDrag(table)
        mime = QMimeData()
        mime.setData(self._mime, str(position).encode("ascii"))
        drag.setMimeData(mime)
        drag.setPixmap(self._row_pixmap(table, entry.row))
        drag.exec(Qt.DropAction.MoveAction)

    @staticmethod
    def _row_pixmap(table: AutoHeightTable, row: int):  # noqa: ANN205 - a QPixmap
        viewport = table.viewport()
        rect = QRect(0, table.rowViewportPosition(row), viewport.width(), table.rowHeight(row))
        return viewport.grab(rect)

    # -- the drop target -----------------------------------------------------

    def drag_over(self, table: AutoHeightTable, event) -> None:  # noqa: ANN001 - Qt event
        source, target, before = self._resolve(table, event)
        if source is None or target is None or not self._accepts(source, target, before):
            self._indicators[table].hide_indicator()
            if source is not None and self._feedback[table].state != DropFeedback.REJECT:
                self._feedback[table].show_reject()
            event.ignore()
            return
        self._feedback[table].clear()
        event.acceptProposedAction()
        self._indicators[table].move_to(self._indicator_rect(table, target, before))

    def drag_left(self, table: AutoHeightTable) -> None:
        self._indicators[table].hide_indicator()
        self._feedback[table].clear()

    def drop(self, table: AutoHeightTable, event) -> None:  # noqa: ANN001 - Qt event
        self.drag_left(table)
        source, target, before = self._resolve(table, event)
        if source is None or target is None or not self._accepts(source, target, before):
            event.ignore()
            return
        event.acceptProposedAction()
        self._on_move(source, target, before)

    def _resolve(  # noqa: ANN001 - Qt event
        self, table: AutoHeightTable, event
    ) -> tuple[RowEntry | None, RowEntry | None, bool]:
        """The dragged row, the row it is over, and whether it lands before it."""
        if not event.mimeData().hasFormat(self._mime) or not self._enabled():
            return None, None, False
        raw = bytes(event.mimeData().data(self._mime))
        try:
            position = int(raw.decode("ascii"))
        except ValueError:
            return None, None, False
        if not 0 <= position < len(self._index):
            return None, None, False
        source = self._index[position]
        pos = event.position().toPoint()
        row = table.rowAt(pos.y())
        if row < 0:
            # Below the last row: land after whatever this panel ends with.
            return source, self._index.last_in(table), False
        target = self._index.entry_at(table, row)
        if target is None:
            return source, None, False
        top = table.rowViewportPosition(row)
        return source, target, pos.y() < top + table.rowHeight(row) / 2

    def _indicator_rect(self, table: AutoHeightTable, target: RowEntry, before: bool) -> QRect:
        y = table.rowViewportPosition(target.row) + (0 if before else table.rowHeight(target.row))
        return QRect(0, y - INDICATOR_HEIGHT // 2, table.viewport().width(), INDICATOR_HEIGHT)


def move_within(items: list, from_index: int, to_index: int) -> None:
    """Move ``items[from_index]`` so it sits at *to_index*, in place.

    *to_index* is measured against the list **as it stands**, before the move —
    which is what a drop position means — so it is corrected downward once the
    moved item has been lifted out from in front of it. The same rule
    :meth:`~mm_companion.ui.pin_panel.PinPanel.move_pin` states for the GM's
    pinned chips; it is here because every reorderable block needs it.
    """
    if not 0 <= from_index < len(items):
        return
    item = items.pop(from_index)
    if to_index > from_index:
        to_index -= 1
    items.insert(max(0, min(to_index, len(items))), item)


class SortControl(QWidget):
    """A block's "Sort:" label and mode combo.

    Presets reorder the block's stored list *permanently* — the point is that the
    new order is the one that saves — so the mode itself is view state and is not
    persisted. :data:`SORT_MANUAL` is the one mode every block has, because it is
    the only one in which hand reordering means anything: while a preset is in
    force :class:`RowReorder` stands down (see :meth:`reorder_enabled`).
    """

    #: The chosen mode's id.
    sortChanged = Signal(str)

    def __init__(self, modes: list[tuple[str, str]], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(QLabel("Sort:"))
        self.combo = QComboBox()
        for mode, label in modes:
            self.combo.addItem(label, mode)
        self.combo.currentIndexChanged.connect(lambda: self.sortChanged.emit(self.mode()))
        guard_wheel(self.combo)
        row.addWidget(self.combo)

    def mode(self) -> str:
        data = self.combo.currentData()
        return SORT_MANUAL if data is None else str(data)

    def set_mode(self, mode: str) -> None:
        """Show *mode* without announcing it — this is a load, not a choice."""
        index = self.combo.findData(mode)
        if index < 0:
            return
        blocked = self.combo.blockSignals(True)
        self.combo.setCurrentIndex(index)
        self.combo.blockSignals(blocked)

    def reorder_enabled(self) -> bool:
        """Whether hand reordering means anything right now."""
        return self.mode() == SORT_MANUAL


__all__ = [
    "DRAG_THRESHOLD",
    "SORT_MANUAL",
    "AutoHeightTable",
    "MenuContributor",
    "RowEntry",
    "RowIndex",
    "RowReorder",
    "SortControl",
    "build_row_menu",
    "install_row_menu",
    "move_within",
    "remove_contributor",
    "wrapping_column_width",
]

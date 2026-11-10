"""A popup of titled sections, each a grid of square icon tiles — the roller's Actions.

Built for the roller's **Actions** button and kept free of any game knowledge, so it can
be used again for anything else that wants "pick one of these, by picture": the caller
hands it :class:`GridSection` records of :class:`GridItem` tiles and listens for
:attr:`ActionGridPopup.picked`.

Why not a ``QMenu``: a menu is one column of text, and the point of this one is a grid
of drawings a player finds at a glance. It behaves like a menu all the same — a
frameless ``Qt.Popup`` that closes on a pick, on Esc and on any click outside it — and
it can be worked from the keyboard (the arrows move between tiles, Enter or Space picks).

**A pick may lead to a menu** (All-Out Attack → which attack). The popup stays open
while :attr:`ActionGridPopup.picked` is handled and closes after, and
:meth:`ActionGridPopup.follow_up_point` says where such a menu should open: on the
tile picked, over the popup, as a submenu would. Closing first and opening a small menu
under the button read as the popup vanishing and something else appearing.

The tiles flow (a :class:`~mm_companion.ui.flow_layout.FlowLayout` per section), so the
column count follows the popup's width: ``action-grid.columns`` tiles a row when the
screen has room, fewer when it does not. **Short sections share a row** (a *shelf*):
consecutive sections that each fit on one line and together fit the row sit side by
side, so a one-tile "Dice" section costs no row of its own. The popup is sized and
placed by :meth:`ActionGridPopup.show_under`: under the anchor, scrolling when the room
below is short, and above it only when there is more room there.

A disabled tile keeps its tooltip, which is where the caller says *why* it is disabled.
The popup is always created **with a parent** (its anchor): a parentless widget is a
top-level window of its own, and showing one is the flash the sheet tests watch for.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

from PySide6.QtCore import QPoint, QRect, QSize, Qt, Signal
from PySide6.QtGui import QFont, QFontMetrics, QIcon, QKeyEvent, QPalette
from PySide6.QtWidgets import (
    QAbstractButton,
    QFrame,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from mm_companion.ui import theme
from mm_companion.ui.flow_layout import FlowContainer, FlowLayout
from mm_companion.ui.svg_assets import action_icon_pixmap
from mm_companion.ui.widgets import BOLD_STYLE, ElidingLabel, muted_style


@dataclass(frozen=True)
class GridItem:
    """One tile: the *key* it reports when picked, its caption, an icon key (see
    :func:`~mm_companion.ui.svg_assets.action_icon_resource`) and a tooltip.

    A tile that is not :attr:`enabled` still shows, greyed out, and its tooltip says
    why — so the caller should put the reason there.
    """

    key: str
    caption: str
    icon: str = ""
    tooltip: str = ""
    enabled: bool = True


@dataclass(frozen=True)
class GridSection:
    """A titled run of tiles, and a *note* — a muted line under the title, for what the
    whole section is waiting on (every tile greyed for the one reason)."""

    title: str
    items: tuple[GridItem, ...] = ()
    note: str = ""


class GridTile(QToolButton):
    """A square tile: the icon above its caption, the caption elided to fit."""

    def __init__(self, item: GridItem, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.key = item.key
        side = int(theme.metric("action-grid.tile"))
        icon = int(theme.metric("action-grid.icon"))
        self.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
        self.setAutoRaise(True)
        self.setFixedSize(side, side)
        self.setIconSize(QSize(icon, icon))
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setIcon(self._icon(item.icon, icon))
        font = self.font()
        font.setPointSizeF(theme.font_size("size.action-tile"))
        self.setFont(font)
        pad = 2 * int(theme.metric("space.xs"))
        self.setText(
            self.fontMetrics().elidedText(item.caption, Qt.TextElideMode.ElideRight, side - pad)
        )
        self.setToolTip(item.tooltip or item.caption)
        self.setAccessibleName(item.caption)
        self.setEnabled(item.enabled)

    def _icon(self, key: str, size: int) -> QIcon:
        """The drawing in the button's own text colour, and in its disabled one.

        Read off the palette rather than a token: the palette is what the active
        preset builds (or the OS's, under a system preset), and it is the colour the
        caption under the icon is drawn in, so the two always match.
        """
        palette = self.palette()
        ratio = self.devicePixelRatioF()
        icon = QIcon()
        for mode, group in (
            (QIcon.Mode.Normal, QPalette.ColorGroup.Normal),
            (QIcon.Mode.Active, QPalette.ColorGroup.Normal),
            (QIcon.Mode.Disabled, QPalette.ColorGroup.Disabled),
        ):
            color = palette.color(group, QPalette.ColorRole.ButtonText).name()
            icon.addPixmap(action_icon_pixmap(key, size, ratio, color), mode)
        return icon


class ActionGridPopup(QFrame):
    """The popup itself — see the module docstring."""

    #: A tile was picked: its :attr:`GridItem.key`. The popup has closed by then.
    picked = Signal(str)

    def __init__(self, sections: Sequence[GridSection], parent: QWidget) -> None:
        super().__init__(parent, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
        self.setObjectName("actionGrid")
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setAutoFillBackground(True)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)

        outer = QVBoxLayout(self)
        margin = int(theme.metric("space.sm"))
        outer.setContentsMargins(margin, margin, margin, margin)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        outer.addWidget(self._scroll)

        content = QWidget()
        column = QVBoxLayout(content)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(int(theme.metric("space.xs")))
        self._titles: list[QLabel] = []
        self._flows: list[FlowContainer] = []
        #: The rows of side-by-side sections: fixed, so they are as tall as they hint.
        self._shelves: list[QWidget] = []
        self.tiles: list[GridTile] = []
        #: Set while :attr:`picked` is being handled: the centre of the tile picked.
        self._follow_up: QPoint | None = None
        #: The button it opened under (:meth:`show_under`), whose click closes it.
        self._anchor: QWidget | None = None
        live = [s for s in sections if s.items]
        self._columns = max((len(s.items) for s in live), default=1)
        #: The muted "why it is all greyed" lines under section titles.
        self._notes: list[ElidingLabel] = []
        for shelf in self._shelve(live):
            if len(shelf) == 1:
                title, flow = self._section(shelf[0], flowing=True)
                column.addWidget(title)
                if shelf[0].note:
                    note = ElidingLabel(shelf[0].note)
                    note.setStyleSheet(muted_style(italic=True))
                    self._notes.append(note)
                    column.addWidget(note)
                column.addWidget(flow)
                self._flows.append(flow)
                continue
            row = QWidget()
            across = QHBoxLayout(row)
            across.setContentsMargins(0, 0, 0, 0)
            # The tiles' own gap, and each section as wide as whole tile columns: so a
            # shelf's tiles stand in the same columns as a full row's above them, and
            # each title sits over its own tiles. A wider gap here put "Dice" over the
            # gap between columns, a column away from its Reroll tile.
            across.setSpacing(int(theme.metric("action-grid.gap")))
            for section in shelf:
                part = QWidget()
                part.setFixedWidth(self._row_width(self._section_columns(section)))
                stack = QVBoxLayout(part)
                stack.setContentsMargins(0, 0, 0, 0)
                stack.setSpacing(column.spacing())
                title, tiles = self._section(section, flowing=False)
                stack.addWidget(title)
                stack.addWidget(tiles)
                across.addWidget(part)
            across.addStretch(1)
            column.addWidget(row)
            self._shelves.append(row)
        column.addStretch(1)
        self._scroll.setWidget(content)

    # -- building ------------------------------------------------------------

    @staticmethod
    def _row_width(tiles: int) -> int:
        """How wide *tiles* tiles stand in one row."""
        side = int(theme.metric("action-grid.tile"))
        gap = int(theme.metric("action-grid.gap"))
        return tiles * side + max(0, tiles - 1) * gap

    def _section_columns(self, section: GridSection) -> int:
        """How many tile columns *section* takes on a shared row: its tiles, or as many
        as its title needs, whichever is more — "Area & Group" over one tile takes two,
        so the next section's title does not start over that tile's neighbour."""
        side = int(theme.metric("action-grid.tile"))
        gap = int(theme.metric("action-grid.gap"))
        font = QFont(self.font())
        font.setBold(True)
        title = QFontMetrics(font).horizontalAdvance(section.title)
        return max(len(section.items), math.ceil((title + gap) / (side + gap)))

    def _shelve(self, sections: Sequence[GridSection]) -> list[list[GridSection]]:
        """Group *sections* into rows: a run of short ones that fits in one row
        together shares it; anything else has its row (or rows) to itself. A section
        with a note keeps a row of its own, where the note has the width to be read."""
        wanted = max(1, min(int(theme.metric("action-grid.columns")), self._columns))
        shelves: list[list[GridSection]] = []
        used = 0  # columns the last shelf holds, or 0 when it can take no more
        for section in sections:
            own = self._section_columns(section)
            short = own <= wanted and not section.note
            if short and used and used + own <= wanted:
                shelves[-1].append(section)
                used += own
                continue
            shelves.append([section])
            used = own if short else 0
        return shelves

    def _section(self, section: GridSection, *, flowing: bool) -> tuple[QLabel, QWidget]:
        """A section's title and its tiles: flowing (wrapping with the popup's width),
        or in one fixed row on a shelf."""
        title = QLabel(section.title)
        title.setStyleSheet(BOLD_STYLE)
        self._titles.append(title)
        gap = int(theme.metric("action-grid.gap"))
        holder: QWidget
        if flowing:
            holder = FlowContainer()
            layout: FlowLayout | QHBoxLayout = FlowLayout(holder, spacing=gap)
        else:
            holder = QWidget()
            layout = QHBoxLayout(holder)
            layout.setSpacing(gap)
        layout.setContentsMargins(0, 0, 0, 0)
        for item in section.items:
            tile = GridTile(item)
            tile.clicked.connect(lambda _=False, k=item.key: self._pick(k))
            layout.addWidget(tile)
            self.tiles.append(tile)
        if isinstance(layout, QHBoxLayout):
            layout.addStretch(1)
        return title, holder

    # -- picking -------------------------------------------------------------

    def _pick(self, key: str) -> None:
        """Report, then close. A menu the pick leads to opens over this popup, on the
        tile (:meth:`follow_up_point`). A second pick while the first is handled — a
        click that closed that menu landing on another tile — is ignored."""
        if self._follow_up is not None:
            return
        tile = self.tile(key)
        self._follow_up = tile.mapToGlobal(tile.rect().center()) if tile else self.pos()
        try:
            self.picked.emit(key)
        finally:
            self.close()
            self._follow_up = None

    def follow_up_point(self) -> QPoint | None:
        """Where a menu opened by the pick being handled should appear (global): the
        middle of the tile picked. ``None`` outside a pick."""
        return self._follow_up

    def tile(self, key: str) -> GridTile | None:
        """The tile reporting *key*, if there is one."""
        return next((t for t in self.tiles if t.key == key), None)

    # -- size and place ------------------------------------------------------

    def grid_width(self, available: int) -> int:
        """How wide the tiles' area is: ``action-grid.columns`` tiles if they fit in
        *available* pixels, as many as do otherwise (one at least)."""
        side = int(theme.metric("action-grid.tile"))
        gap = int(theme.metric("action-grid.gap"))
        wanted = max(1, min(int(theme.metric("action-grid.columns")), self._columns))
        fits = max(1, (available + gap) // (side + gap))
        columns = min(wanted, fits)
        width = columns * side + (columns - 1) * gap
        # A shelf is a fixed row: never narrower than the widest that still fits.
        shelves = [s.sizeHint().width() for s in self._shelves]
        return max([width] + [w for w in shelves if w <= available])

    def _content_height(self, width: int) -> int:
        """How tall the sections stand at a tile area *width* pixels wide."""
        spacing = self._scroll.widget().layout().spacing()
        shelved = {id(t) for shelf in self._shelves for t in shelf.findChildren(QLabel)}
        parts = [t.sizeHint().height() for t in self._titles if id(t) not in shelved]
        parts += [note.sizeHint().height() for note in self._notes]
        parts += [f.heightForWidth(width) for f in self._flows]
        parts += [shelf.sizeHint().height() for shelf in self._shelves]
        return sum(parts) + spacing * max(0, len(parts) - 1)

    def fit_to(self, screen: QRect, room: int | None = None) -> None:
        """Size the popup for *screen* (an available geometry): the grid's own width
        and height, short of the screen's — or of *room* pixels, the space on the side
        of the anchor it opens on — scrolling down only when it must."""
        frame = 2 * self.frameWidth()
        margins = self.layout().contentsMargins()
        chrome_w = frame + margins.left() + margins.right()
        chrome_h = frame + margins.top() + margins.bottom()
        edge = 2 * int(theme.metric("space.lg"))
        width = self.grid_width(screen.width() - chrome_w - edge)
        height = self._content_height(width)
        room = (screen.height() - edge if room is None else room) - chrome_h
        if height > room:
            # It scrolls: the bar takes width of its own, so give it that rather than
            # let it squeeze a column off.
            width += self._scroll.verticalScrollBar().sizeHint().width()
            height = room
        self.resize(width + chrome_w, height + chrome_h)

    def show_under(self, anchor: QWidget) -> None:
        """Open centred under *anchor*, kept on the anchor's screen.

        Where the whole grid does not fit below, it scrolls there — unless there is more
        room above, where it opens instead. It never covers the anchor: it used to flip
        above when it did not fit below, run off the top, and be pulled down to the
        screen's top edge, nowhere near the button.
        """
        self._anchor = anchor
        screen = anchor.screen() or self.screen()
        available = screen.availableGeometry()
        here = QRect(anchor.mapToGlobal(QPoint(0, 0)), anchor.size())
        below = available.bottom() - here.bottom()
        above = here.top() - available.top()
        self.fit_to(available)
        under = self.height() <= below or below >= above
        self.fit_to(available, below if under else above)
        x = here.center().x() - self.width() // 2
        y = here.bottom() + 1 if under else here.top() - self.height()
        x = max(available.left(), min(x, available.right() - self.width() + 1))
        y = max(available.top(), min(y, available.bottom() - self.height() + 1))
        self.move(x, y)
        self.show()
        first = next((t for t in self.tiles if t.isEnabled()), None)
        if first is not None:
            first.setFocus(Qt.FocusReason.PopupFocusReason)

    # -- the mouse -------------------------------------------------------------

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt override
        """A press outside closes the popup (Qt's own popup handling) — and a press on the
        button it opened under is **not** passed on to that button.

        Qt replays the press that closed a popup to whatever is under it, so clicking
        Actions to put the popup away closed it and then opened it again at once (the
        user's report, 2026-11-10). Everywhere else the replay stays: a click on the die
        while the popup is up should still roll it.
        """
        anchor = self._anchor
        if anchor is not None and not self.rect().contains(event.position().toPoint()):
            here = QRect(anchor.mapToGlobal(QPoint(0, 0)), anchor.size())
            if here.contains(event.globalPosition().toPoint()):
                self.setAttribute(Qt.WidgetAttribute.WA_NoMouseReplay)
        super().mousePressEvent(event)

    # -- keyboard ------------------------------------------------------------

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802 - Qt override
        key = event.key()
        if key == Qt.Key.Key_Escape:
            self.close()
            return
        current = self.focusWidget()
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if isinstance(current, QAbstractButton) and current in self.tiles:
                current.click()
            return
        moves = {
            Qt.Key.Key_Left: (-1, 0),
            Qt.Key.Key_Right: (1, 0),
            Qt.Key.Key_Up: (0, -1),
            Qt.Key.Key_Down: (0, 1),
        }
        if key in moves and isinstance(current, GridTile):
            target = self._neighbour(current, *moves[key])
            if target is not None:
                target.setFocus(Qt.FocusReason.TabFocusReason)
                self._scroll.ensureWidgetVisible(target)
            return
        super().keyPressEvent(event)

    def _neighbour(self, tile: GridTile, dx: int, dy: int) -> GridTile | None:
        """The enabled tile one step from *tile*: the next or previous in reading order
        across, the nearest in the row above or below down."""
        live = [t for t in self.tiles if t.isEnabled()]
        if tile not in live:
            return None
        if dx:
            index = live.index(tile) + dx
            return live[index] if 0 <= index < len(live) else None
        content = self._scroll.widget()

        def centre(t: GridTile) -> QPoint:
            return t.mapTo(content, t.rect().center())

        here = centre(tile)
        rows = [t for t in live if (centre(t).y() - here.y()) * dy > 0]
        if not rows:
            return None
        nearest_row = min(abs(centre(t).y() - here.y()) for t in rows)
        row = [t for t in rows if abs(centre(t).y() - here.y()) == nearest_row]
        return min(row, key=lambda t: abs(centre(t).x() - here.x()))

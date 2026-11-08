"""A popup of titled sections, each a grid of square icon tiles — the roller's Actions.

Built for the roller's **Actions** button and kept free of any game knowledge, so it can
be used again for anything else that wants "pick one of these, by picture": the caller
hands it :class:`GridSection` records of :class:`GridItem` tiles and listens for
:attr:`ActionGridPopup.picked`.

Why not a ``QMenu``: a menu is one column of text, and the point of this one is a grid
of drawings a player finds at a glance. It behaves like a menu all the same — a
frameless ``Qt.Popup`` that closes on a pick, on Esc and on any click outside it — and
it can be worked from the keyboard (the arrows move between tiles, Enter or Space picks).

The tiles flow (a :class:`~mm_companion.ui.flow_layout.FlowLayout` per section), so the
column count follows the popup's width: ``action-grid.columns`` tiles a row when the
screen has room, fewer when it does not. The popup is sized and placed by
:meth:`ActionGridPopup.show_under`, which keeps it on the anchor's screen, scrolling
only when the whole grid is taller than that screen.

A disabled tile keeps its tooltip, which is where the caller says *why* it is disabled.
The popup is always created **with a parent** (its anchor): a parentless widget is a
top-level window of its own, and showing one is the flash the sheet tests watch for.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from PySide6.QtCore import QPoint, QRect, QSize, Qt, Signal
from PySide6.QtGui import QIcon, QKeyEvent, QPalette
from PySide6.QtWidgets import (
    QAbstractButton,
    QFrame,
    QLabel,
    QScrollArea,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from mm_companion.ui import theme
from mm_companion.ui.flow_layout import FlowContainer, FlowLayout
from mm_companion.ui.svg_assets import action_icon_pixmap
from mm_companion.ui.widgets import BOLD_STYLE


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
    """A titled run of tiles."""

    title: str
    items: tuple[GridItem, ...] = ()


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
        gap = int(theme.metric("action-grid.gap"))
        self._titles: list[QLabel] = []
        self._flows: list[FlowContainer] = []
        self.tiles: list[GridTile] = []
        for section in sections:
            if not section.items:
                continue
            title = QLabel(section.title)
            title.setStyleSheet(BOLD_STYLE)
            column.addWidget(title)
            flow = FlowContainer()
            layout = FlowLayout(flow, spacing=gap)
            layout.setContentsMargins(0, 0, 0, 0)
            for item in section.items:
                tile = GridTile(item)
                tile.clicked.connect(lambda _=False, k=item.key: self._pick(k))
                layout.addWidget(tile)
                self.tiles.append(tile)
            column.addWidget(flow)
            self._titles.append(title)
            self._flows.append(flow)
        column.addStretch(1)
        self._scroll.setWidget(content)
        self._columns = max((len(s.items) for s in sections), default=1)

    # -- picking -------------------------------------------------------------

    def _pick(self, key: str) -> None:
        """Close, then report: whatever the pick opens next (a menu, a window) should
        not have to share the screen with this popup."""
        self.close()
        self.picked.emit(key)

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
        return columns * side + (columns - 1) * gap

    def _content_height(self, width: int) -> int:
        """How tall the sections stand at a tile area *width* pixels wide."""
        spacing = self._scroll.widget().layout().spacing()
        parts = [t.sizeHint().height() for t in self._titles]
        parts += [f.heightForWidth(width) for f in self._flows]
        return sum(parts) + spacing * max(0, len(parts) - 1)

    def fit_to(self, screen: QRect) -> None:
        """Size the popup for *screen* (an available geometry): the grid's own width
        and height, short of the screen's, scrolling down only when it must."""
        frame = 2 * self.frameWidth()
        margins = self.layout().contentsMargins()
        chrome_w = frame + margins.left() + margins.right()
        chrome_h = frame + margins.top() + margins.bottom()
        edge = 2 * int(theme.metric("space.lg"))
        width = self.grid_width(screen.width() - chrome_w - edge)
        height = self._content_height(width)
        room = screen.height() - chrome_h - edge
        if height > room:
            # It scrolls: the bar takes width of its own, so give it that rather than
            # let it squeeze a column off.
            width += self._scroll.verticalScrollBar().sizeHint().width()
            height = room
        self.resize(width + chrome_w, height + chrome_h)

    def show_under(self, anchor: QWidget) -> None:
        """Open centred under *anchor* (over it when there is no room below), kept on
        the anchor's screen."""
        screen = anchor.screen() or self.screen()
        available = screen.availableGeometry()
        self.fit_to(available)
        here = QRect(anchor.mapToGlobal(QPoint(0, 0)), anchor.size())
        x = here.center().x() - self.width() // 2
        y = here.bottom() + 1
        if y + self.height() > available.bottom():
            y = here.top() - self.height()
        x = max(available.left(), min(x, available.right() - self.width() + 1))
        y = max(available.top(), min(y, available.bottom() - self.height() + 1))
        self.move(x, y)
        self.show()
        first = next((t for t in self.tiles if t.isEnabled()), None)
        if first is not None:
            first.setFocus(Qt.FocusReason.PopupFocusReason)

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

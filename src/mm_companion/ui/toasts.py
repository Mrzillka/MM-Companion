"""Messages that float: over a window, or over the whole desktop.

Two things in the app are *messages* rather than content — they say something
happened, are read once, and should then get out of the way:

* **Session notices** — "Connected to Wednesday", "Sam joined", "Lost the session —
  trying to get back in…". These used to take a strip of the window: the GM window
  laid its notice cards out under the board, and a player's sheet grew a status bar,
  so every message shoved the blocks up and handed them back a few seconds later. A
  :class:`ToastStack` floats them over the window's bottom-right corner instead, on
  top of the blocks and in no layout, so nothing moves when one comes or goes.
* **Roll notifications** — every roll, note and request that lands in a history also
  pops up in a corner of the *screen* (:class:`RollToaster`), always on top, the way a
  messenger shows a new message. A history in a pinned strip shows three or four cards
  at a time, and mid-fight a GM wants to read the last roll without hunting for it or
  giving the history half the window. Which corner, and whether at all, are settings
  (:func:`~mm_companion.core.storage.roll_notification_corner`).

Both are the same :class:`ToastCard`: a rounded card with a ``✕`` that holds still for
a dwell and then fades away. **Hovering a card holds it** — the timer stops and the
card returns to full strength until the pointer leaves — because a message that fades
out from under the cursor of the person reading it has not been delivered. Over the
desktop, hovering any card holds the whole stack, as Telegram Desktop does.

:meth:`ToastCard.hold` is the other exception, the one the GM window's "cannot reach
the table" card needs: a *condition* rather than a message, which stays until
:meth:`ToastCard.release` says it has passed.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Callable

from PySide6.QtCore import (
    QEasingCurve,
    QEvent,
    QObject,
    QPoint,
    QPropertyAnimation,
    QRect,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import QEnterEvent, QGuiApplication, QMouseEvent, QPalette
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QLayout,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from mm_companion.core import storage
from mm_companion.ui import theme

#: Set by the test suite: a test that rolls must not leave notification windows on
#: the screen of whoever runs it (see ``tests/conftest.py``).
SUPPRESSED = False


class ToastCard(QFrame):
    """A dismissible message card that fades itself out after a dwell.

    ``window=True`` makes the card its own frameless, always-on-top window that never
    takes focus — a notification over the desktop. Otherwise it is an ordinary child
    widget, faded through a :class:`QGraphicsOpacityEffect`, for a :class:`ToastStack`
    to float over a window.

    Updating its contents and calling :meth:`poke` again brings it back to full
    opacity and restarts the countdown.
    """

    #: How long a message stays fully visible before it starts to fade.
    DWELL_MS = 10_000
    #: How long the fade-out itself takes — deliberately slow, so it eases away.
    FADE_MS = 1_500

    #: The card went away — dismissed, or faded out.
    closed = Signal()
    #: The pointer came onto the card (``True``) or left it.
    hovered = Signal(bool)
    #: The body of the card (not its ``✕``) was clicked.
    clicked = Signal()

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        window: bool = False,
        dwell_ms: int | None = None,
        fade_ms: int | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("toastCard")
        self._window = window
        self._accent: str | None = None
        self._hover = False
        # Whether this card is showing a *condition* rather than a message; see the
        # module docstring and :meth:`hold`.
        self._held = False

        if window:
            self.setWindowFlags(
                Qt.WindowType.Tool
                | Qt.WindowType.FramelessWindowHint
                | Qt.WindowType.WindowStaysOnTopHint
                | Qt.WindowType.WindowDoesNotAcceptFocus
            )
            self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
            self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
            self._effect = None
            self._fade = QPropertyAnimation(self, b"windowOpacity", self)
        else:
            self._effect = QGraphicsOpacityEffect(self)
            self._effect.setOpacity(1.0)
            self.setGraphicsEffect(self._effect)
            self._fade = QPropertyAnimation(self._effect, b"opacity", self)
        self._fade.setDuration(self.FADE_MS if fade_ms is None else fade_ms)
        self._fade.setEasingCurve(QEasingCurve.Type.InCubic)
        self._fade.finished.connect(self._settle)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(self.DWELL_MS if dwell_ms is None else dwell_ms)
        self._timer.timeout.connect(self._begin_fade)

        # Over the desktop the card is a transparent window holding a styled
        # surface: a translucent top-level paints no stylesheet background of its
        # own, so the rounded card has to be a child of it. Over a window the card
        # is the surface itself.
        if window:
            self._surface = QFrame(self)
            self._surface.setObjectName("toastCard")
            outer = QVBoxLayout(self)
            outer.setContentsMargins(0, 0, 0, 0)
            outer.addWidget(self._surface)
            self.setObjectName("toastWindow")
        else:
            self._surface = self
        row = QHBoxLayout(self._surface)
        pad = int(theme.metric("space.lg"))
        edge = int(theme.metric("border.width.accent-edge"))
        row.setContentsMargins(pad + edge, pad, int(theme.metric("space.sm")), pad)
        row.setSpacing(int(theme.metric("space.sm")))
        self._body = QVBoxLayout()
        self._body.setContentsMargins(0, 0, 0, 0)
        self._body.setSpacing(int(theme.metric("space.xxs")))
        row.addLayout(self._body, stretch=1)

        self._close = QToolButton()
        self._close.setText("✕")
        self._close.setAutoRaise(True)
        self._close.setToolTip("Dismiss this message")
        self._close.setCursor(Qt.CursorShape.ArrowCursor)
        self._close.clicked.connect(self.dismiss)
        row.addWidget(self._close, alignment=Qt.AlignmentFlag.AlignTop)

        self._restyle()
        self.hide()

    # -- content -------------------------------------------------------------

    def add_widget(self, widget: QWidget) -> None:
        self._body.addWidget(widget)

    def add_layout(self, layout: QLayout) -> None:
        self._body.addLayout(layout)

    def set_accent(self, colour: str | None) -> None:
        """Colour the card's leading edge — the tone of the message, or none."""
        self._accent = colour or None
        self._restyle()

    def _restyle(self) -> None:
        width = int(theme.metric("border.width"))
        edge = int(theme.metric("border.width.accent-edge"))
        # Solid colours over the desktop: a translucent window reads a palette()
        # role as see-through, so the card would be text floating on nothing.
        colour = _solid if self._window else theme.color
        border = colour("border.card")
        accent = self._accent or border
        self._surface.setStyleSheet(
            f"#toastCard {{ background: {colour('surface.toast')};"
            f" border: {width}px solid {border};"
            f" border-left: {edge}px solid {accent};"
            f" border-radius: {int(theme.metric('radius.group'))}px; }}"
        )

    # -- showing and going ---------------------------------------------------

    def poke(self) -> None:
        """Show the card at full opacity and restart the dwell-then-fade timer."""
        self._fade.stop()
        self._set_opacity(1.0)
        self.setVisible(True)
        if not self._held and not self._hover:
            self._timer.start()

    def hold(self) -> None:
        """Show the card and keep it there until :meth:`release`."""
        self._held = True
        self._timer.stop()
        self._fade.stop()
        self._set_opacity(1.0)
        self.setVisible(True)

    def release(self) -> None:
        """The condition has passed: let the card go."""
        if not self._held:
            return
        self._held = False
        self.dismiss()

    @property
    def held(self) -> bool:
        return self._held

    def dismiss(self) -> None:
        """Retire the card at once — the ✕ button, or a caller clearing it."""
        was_visible = not self.isHidden()
        self._held = False
        self._timer.stop()
        self._fade.stop()
        self._set_opacity(1.0)
        self.setVisible(False)
        if was_visible:
            self.closed.emit()

    def pause(self) -> None:
        """Hold still — the pointer is over this card, or over one of its stack."""
        self._timer.stop()
        if self._fade.state() == QPropertyAnimation.State.Running:
            self._fade.stop()
        if not self.isHidden():
            self._set_opacity(1.0)

    def resume(self) -> None:
        """Start the countdown again from the top, unless the card is held or gone."""
        if self._held or self.isHidden() or self._hover:
            return
        self._timer.start()

    def _set_opacity(self, value: float) -> None:
        if self._effect is not None:
            self._effect.setOpacity(value)
        else:
            self.setWindowOpacity(value)

    def _opacity(self) -> float:
        return self._effect.opacity() if self._effect is not None else self.windowOpacity()

    def _begin_fade(self) -> None:
        if self._hover:
            return
        self._fade.stop()
        self._fade.setStartValue(self._opacity())
        self._fade.setEndValue(0.0)
        self._fade.start()

    def _settle(self) -> None:
        if self._opacity() <= 0.01 and not self.isHidden():
            self.setVisible(False)
            self._set_opacity(1.0)
            self.closed.emit()

    # -- the pointer ---------------------------------------------------------

    def enterEvent(self, event: QEnterEvent) -> None:  # noqa: N802 - Qt override
        super().enterEvent(event)
        self._hover = True
        self.pause()
        self.hovered.emit(True)

    def leaveEvent(self, event: QEvent) -> None:  # noqa: N802 - Qt override
        super().leaveEvent(event)
        self._hover = False
        self.resume()
        self.hovered.emit(False)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - Qt override
        super().mouseReleaseEvent(event)
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(
            event.position().toPoint()
        ):
            self.clicked.emit()


def _solid(token: str) -> str:
    """A colour token as a concrete ``#rrggbb`` — a ``palette(role)`` read off the app."""
    value = theme.color(token)
    if not value.startswith("palette("):
        return value
    name = "".join(part.capitalize() for part in value[len("palette(") : -1].split("-"))
    role = getattr(QPalette.ColorRole, name, QPalette.ColorRole.Window)
    return QApplication.palette().color(role).name()


def wrapped_label(text: str = "", *, rich: bool = False) -> QLabel:
    """A word-wrapping label for a card's body — a message is prose, and gets read."""
    label = QLabel(text)
    label.setWordWrap(True)
    label.setTextFormat(Qt.TextFormat.RichText if rich else Qt.TextFormat.PlainText)
    return label


def _fitted_height(widget: QWidget, width: int) -> int:
    """How tall *widget* wants to be at *width* — a wrapped label's honest answer."""
    layout = widget.layout()
    if layout is not None and layout.hasHeightForWidth():
        return layout.totalHeightForWidth(width)
    return widget.sizeHint().height()


# -- over a window -----------------------------------------------------------------


class ToastStack(QWidget):
    """A column of :class:`ToastCard` floating over the bottom-right corner of *host*.

    In no layout: a plain child of the host, placed by hand from an event filter on the
    host's resize and raised over its siblings — the same arrangement as the compact
    mode's round button (``ui/compact.py``), and for the same reason: the blocks under
    it are laid out as though it were not there, so a message coming or going moves
    nothing. It is exactly as big as the cards it is showing, so where there is no card
    the blocks underneath take the clicks as usual.
    """

    def __init__(self, host: QWidget) -> None:
        super().__init__(host)
        self._host = host
        self._placing = False
        self._column = QVBoxLayout(self)
        self._column.setContentsMargins(0, 0, 0, 0)
        self._column.setSpacing(int(theme.metric("space.md")))
        host.installEventFilter(self)
        self._place_later = QTimer(self)
        self._place_later.setSingleShot(True)
        self._place_later.setInterval(0)
        self._place_later.timeout.connect(self.place)
        self.place()
        # Made after its host may already be on screen, and a child added to a shown
        # parent stays hidden until told otherwise. It has a parent, so this is the
        # ordinary kind of show — and while it holds no card it is 0×0.
        self.show()

    def add_card(self, card: ToastCard) -> ToastCard:
        """Take *card* into the stack; the newest goes at the bottom, by the corner."""
        self._column.addWidget(card)
        card.installEventFilter(self)
        self.place()
        return card

    def message(self, text: str, colour: str = "") -> ToastCard:
        """Show a one-off message, which deletes itself once it has gone."""
        card = ToastCard()
        label = wrapped_label(text)
        if colour:
            label.setStyleSheet(f"color: {colour};")
        card.add_widget(label)
        card.set_accent(colour or None)
        card.closed.connect(card.deleteLater)
        self.add_card(card)
        card.poke()
        self.place()
        return card

    def cards(self) -> list[ToastCard]:
        found = []
        for index in range(self._column.count()):
            widget = self._column.itemAt(index).widget()
            if isinstance(widget, ToastCard):
                found.append(widget)
        return found

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        kind = event.type()
        if watched is self._host and kind in (QEvent.Type.Resize, QEvent.Type.Show):
            self.place()
        elif watched is not self._host and kind in (
            QEvent.Type.Show,
            QEvent.Type.Hide,
            QEvent.Type.LayoutRequest,
        ):
            # A card came, went, or re-wrapped its text: the stack's height moved.
            self._place_later.start()
        return False

    def place(self) -> None:
        """Size the stack to its visible cards and park it in the host's corner."""
        if self._placing:
            return
        self._placing = True
        try:
            margin = int(theme.metric("space.lg")) * 2
            width = min(int(theme.metric("toast.width")), max(0, self._host.width() - 2 * margin))
            visible = [card for card in self.cards() if not card.isHidden()]
            if not visible or width <= 0:
                self.setGeometry(QRect(self._host.width(), self._host.height(), 0, 0))
                return
            spacing = self._column.spacing()
            height = sum(_fitted_height(card, width) for card in visible)
            height += spacing * (len(visible) - 1)
            height = min(height, max(0, self._host.height() - 2 * margin))
            self.setGeometry(
                QRect(
                    self._host.width() - width - margin,
                    self._host.height() - height - margin,
                    width,
                    height,
                )
            )
            self.raise_()
        finally:
            self._placing = False


def window_stack(window: QWidget) -> ToastStack:
    """The :class:`ToastStack` floating over *window*'s central area, made on first use."""
    stack = getattr(window, "_toast_stack", None)
    if isinstance(stack, ToastStack):
        return stack
    central = window.centralWidget() if hasattr(window, "centralWidget") else None
    stack = ToastStack(central if central is not None else window)
    window._toast_stack = stack
    return stack


def notify_window(window: QWidget, text: str, colour: str = "") -> ToastCard:
    """Float a one-off session message over *window* — the old status-bar line."""
    return window_stack(window).message(text, colour)


# -- over the desktop --------------------------------------------------------------


class RollToaster(QObject):
    """Pops each new history entry up in a corner of the screen.

    One per application (:func:`roll_toaster`), fed by the histories themselves —
    :class:`~mm_companion.ui.roll_history.RollHistoryPanel` for the table's shared
    log and :class:`~mm_companion.ui.dice_roller.LocalRollHistory` for one's own —
    with *live* entries only: a reconnect replaying the log must not flood the
    screen with an evening's rolls.

    Deduplicated by sequence number, because one session can feed several
    histories in one app (a GM window and a sheet, or two sheets), and every one of
    them would otherwise announce the same roll.
    """

    #: How long a roll stays fully visible before it fades — a glance, not a read.
    DWELL_MS = 5_000
    FADE_MS = 800
    #: More than this many at once and the oldest goes, so a burst of rolls never
    #: climbs the whole height of the screen.
    MAX_SHOWN = 5

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._cards: list[ToastCard] = []
        self._seen: deque = deque(maxlen=400)

    def cards(self) -> list[ToastCard]:
        return list(self._cards)

    def notify(
        self,
        widgets: list[QWidget],
        *,
        key: object = None,
        accent: str | None = None,
        source: QWidget | None = None,
    ) -> ToastCard | None:
        """Show *widgets* as one notification; ``None`` if switched off or a repeat.

        *key* identifies the entry across histories (``None`` never deduplicates);
        *source* is the window whose history it is, brought forward on a click.
        """
        if SUPPRESSED or not storage.roll_notifications():
            for widget in widgets:
                widget.deleteLater()
            return None
        if key is not None:
            if key in self._seen:
                for widget in widgets:
                    widget.deleteLater()
                return None
            self._seen.append(key)

        card = ToastCard(window=True, dwell_ms=self.DWELL_MS, fade_ms=self.FADE_MS)
        for widget in widgets:
            card.add_widget(widget)
        card.set_accent(accent)
        width = int(theme.metric("toast.width"))
        card.setFixedWidth(width)
        card.setFixedHeight(_fitted_height(card, width))
        card.closed.connect(lambda c=card: self._drop(c))
        card.hovered.connect(self._on_hover)
        window = source.window() if source is not None else None
        card.clicked.connect(lambda w=window: _bring_forward(w))
        self._cards.append(card)
        while len(self._cards) > self.MAX_SHOWN:
            self._cards[0].dismiss()
        self._restack()
        card.poke()
        return card

    def clear(self) -> None:
        """Take every notification down at once (the setting was switched off)."""
        for card in list(self._cards):
            card.dismiss()

    def _drop(self, card: ToastCard) -> None:
        if card in self._cards:
            self._cards.remove(card)
        card.deleteLater()
        self._restack()

    def _on_hover(self, hovering: bool) -> None:
        """Hovering one notification holds them all, as a messenger's stack does."""
        for card in self._cards:
            if hovering:
                card.pause()
            else:
                card.resume()

    def _restack(self) -> None:
        """Lay the cards out from the chosen corner, newest nearest to it."""
        screen = QGuiApplication.primaryScreen()
        if screen is None:
            return
        area = screen.availableGeometry()
        corner = storage.roll_notification_corner()
        margin = int(theme.metric("space.lg")) * 2
        gap = int(theme.metric("space.md"))
        bottom = corner.startswith("bottom")
        right = corner.endswith("right")
        offset = 0
        for card in reversed(self._cards):
            x = area.right() - card.width() - margin + 1 if right else area.left() + margin
            if bottom:
                y = area.bottom() - margin - offset - card.height() + 1
            else:
                y = area.top() + margin + offset
            card.move(QPoint(x, y))
            offset += card.height() + gap


def _bring_forward(window: QWidget | None) -> None:
    if window is None:
        return
    if window.isMinimized():
        window.showNormal()
    window.raise_()
    window.activateWindow()


_TOASTER: RollToaster | None = None


def roll_toaster() -> RollToaster:
    """The application's one :class:`RollToaster`, made on first use."""
    global _TOASTER
    if _TOASTER is None:
        _TOASTER = RollToaster(QApplication.instance())
    return _TOASTER


#: What a history hands :func:`announce` — built by the history, which knows how its
#: own entries read; kept as a callable so nothing is built while toasts are off.
WidgetFactory = Callable[[], list[QWidget]]


def announce(
    build: WidgetFactory,
    *,
    key: object = None,
    accent: str | None = None,
    source: QWidget | None = None,
) -> None:
    """Pop an entry up, building its widgets only if it will actually be shown."""
    if SUPPRESSED or not storage.roll_notifications():
        return
    if key is not None and key in roll_toaster()._seen:
        return
    roll_toaster().notify(build(), key=key, accent=accent, source=source)

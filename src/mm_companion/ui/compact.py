"""Compact mode: the whole window collapsed to just the dice roller.

Mid-fight the only part of the app anyone touches is the roller, and a
1000x860 sheet is exactly the window you want *out of the way* while you read a
map, a VTT, or somebody else's screen. Compact mode is the sheet's own argument
for pinning the Dice block taken one step further: the window shrinks to a small
always-on-top mini roller, laid out as the Roll box across the top, the quick
rolls beside the die under it, and the history filling the rest.

Three pieces, smallest first:

* :class:`CompactStrip` — the mini window's own title bar. It has to exist
  because the window is **frameless** while compact: there is no OS title bar to
  drag it by, so this is the drag handle, and it carries the always-on-top pin,
  the way back out, and the close button.
* :class:`CompactPage` — the strip above a scroll area holding the borrowed
  roller, with a :class:`QSizeGrip` in the corner (again: no OS frame to resize
  by). The scroll area is the whole reason the mini window can be dragged down
  to almost nothing — a ``QScrollArea`` does not pass its child's minimum on, so
  the only floor is the ``compact.min-*`` tokens, and under that the die and the
  history simply scroll.
* :class:`CompactController` — the transition itself, for one window.

**The mini roller is the roller, moved.** Nothing here builds a second one. The
controller asks its *surface* (a character sheet, or the GM window) to hand its
live panel and history over, and gives them back on the way out — so the loaded
spec, the quick-roll strip, a tumble in flight and, most visibly, the session
history and its attachment to the table all carry straight across. A second
roller would have meant a second seat at the table and every roll listed twice.
That is the same borrow-and-return the block canvas performs whenever a block
moves between the page, the pinned strip and a floating window.

A **surface** is anything with ``release_roller()`` (returning the panel and the
history widget, or ``None`` when it has no roller) and ``restore_roller()``.
Both hosts implement it in three lines, which is what makes compact mode one
feature for the player's sheet and the GM window rather than two.

One ordering rule is load-bearing and easy to get wrong: **window flags change
before the animation, never during it.** Qt hides a window whose flags change
and the platform recreates it, so a ``setWindowFlag`` mid-animation loses both
the animation and the geometry.
"""

from __future__ import annotations

from PySide6.QtCore import (
    QAbstractAnimation,
    QByteArray,
    QEasingCurve,
    QObject,
    QPoint,
    QPropertyAnimation,
    QRect,
    QSize,
    Qt,
    Signal,
)
from PySide6.QtGui import QKeySequence, QMouseEvent, QShortcut
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QMainWindow,
    QScrollArea,
    QSizeGrip,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from mm_companion.core import storage
from mm_companion.ui import theme
from mm_companion.ui.widgets import ElidingLabel

#: The menu-bar toggle's two faces. It rides the bar rather than a menu, beside
#: the sheet's lock and for the same reason: shrinking to the dice roller is a
#: play-time view switch reached constantly, not a preference — and a glyph that
#: *shows* the current state is worth more there than a tick two clicks deep.
COMPACT_GLYPH_FULL = "⤡"
COMPACT_GLYPH_COMPACT = "⤢"


class CompactStrip(QFrame):
    """The mini window's title bar: drag handle, always-on-top pin, expand, close.

    Everything an OS title bar would have given us, since compact mode takes that
    frame away. Dragging anywhere on the strip moves the window; the buttons
    consume their own clicks, so pressing one never starts a drag.

    It reuses the ``blockTitleBar`` object name rather than inventing one, because
    it *is* a title bar and a styled preset already says what one looks like — a
    new name would mean a new rule in the stylesheet that every preset would then
    have to answer for.
    """

    #: The ``⤢`` button, or a double-click on the strip: give the full window back.
    expandRequested = Signal()
    #: The ``✕`` button: close the window, exactly as its OS close box would.
    closeRequested = Signal()
    #: The pin was toggled; carries whether the window should now stay on top.
    onTopToggled = Signal(bool)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("blockTitleBar")
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        # Where in the window the drag started, so the window follows the pointer
        # rather than jumping its top-left corner under it.
        self._grab: QPoint | None = None

        layout = QHBoxLayout(self)
        margin = int(theme.metric("space.sm"))
        layout.setContentsMargins(margin * 2, margin // 2, margin, margin // 2)
        layout.setSpacing(int(theme.metric("space.xs")))

        # An eliding label for the same reason a block's title bar uses one: the
        # caption is a character's name and must never become a width the mini
        # window cannot go under.
        self._label = ElidingLabel("")
        self._label.setObjectName("blockTitleLabel")
        layout.addWidget(self._label, stretch=1)

        # U+1F588 black pushpin, not U+1F4CC — the plain symbol keeps the strip
        # monochrome beside its ⤢ and ✕ neighbours, the same choice a block title
        # bar makes. Checkable, so the button's own sunken state is the read-out
        # and no stylesheet is needed to say "on".
        self._pin_button = self._make_button("🖈", "")
        self._pin_button.setCheckable(True)
        self._pin_button.toggled.connect(self._on_pin_toggled)
        layout.addWidget(self._pin_button)

        expand = self._make_button("⤢", "Give the rest of the window back")
        expand.clicked.connect(self.expandRequested)
        layout.addWidget(expand)

        close = self._make_button("✕", "Close this window")
        close.clicked.connect(self.closeRequested)
        layout.addWidget(close)

    def _make_button(self, glyph: str, tooltip: str) -> QToolButton:
        button = QToolButton()
        button.setText(glyph)
        button.setAutoRaise(True)
        button.setToolTip(tooltip)
        # An arrow over the buttons even though the strip itself offers an open
        # hand: they are things to click, not part of the handle.
        button.setCursor(Qt.CursorShape.ArrowCursor)
        return button

    def set_title(self, text: str) -> None:
        """Show *text* as the mini window's caption."""
        self._label.setText(text)

    def set_on_top(self, on_top: bool) -> None:
        """Light the pin without reporting it back as a user's toggle."""
        blocked = self._pin_button.blockSignals(True)
        self._pin_button.setChecked(bool(on_top))
        self._pin_button.blockSignals(blocked)
        self._describe_pin(bool(on_top))

    def _on_pin_toggled(self, checked: bool) -> None:
        self._describe_pin(checked)
        self.onTopToggled.emit(checked)

    def _describe_pin(self, on_top: bool) -> None:
        self._pin_button.setToolTip(
            "Staying on top of other windows — click to let it fall behind"
            if on_top
            else "Keep this window on top of the others"
        )

    # -- dragging the window -------------------------------------------------

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            window = self.window()
            self._grab = event.globalPosition().toPoint() - window.frameGeometry().topLeft()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._grab is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.window().move(event.globalPosition().toPoint() - self._grab)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._grab = None
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._grab = None
            self.expandRequested.emit()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)


class CompactPage(QWidget):
    """The mini window's contents: the strip, the borrowed roller, a size grip.

    The roller sits in a scroll area, which is what lets the window be dragged
    smaller than the roller itself: a ``QScrollArea`` reports a modest minimum of
    its own and does not pass its child's on, so the floor is the one stated here
    from ``compact.min-width`` / ``compact.min-height`` and below it the content
    scrolls rather than the window refusing to shrink.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.strip = CompactStrip()
        layout.addWidget(self.strip)

        self._body = QWidget()
        self._body_box = QVBoxLayout(self._body)
        gap = int(theme.metric("space.sm"))
        self._body_box.setContentsMargins(gap, gap, gap, gap)
        self._body_box.setSpacing(gap)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setWidget(self._body)
        self._scroll.setMinimumSize(
            int(theme.metric("compact.min-width")), int(theme.metric("compact.min-height"))
        )
        layout.addWidget(self._scroll, stretch=1)

        # A frameless window has no resize border, so this is the only way to
        # resize it with the mouse. It sits in a row of its own rather than over
        # the history, where it would cover a card's buttons.
        grip_row = QWidget()
        grip_box = QHBoxLayout(grip_row)
        grip_box.setContentsMargins(0, 0, 0, 0)
        grip_box.addStretch()
        grip_box.addWidget(QSizeGrip(grip_row))
        layout.addWidget(grip_row)

    def adopt(self, panel: QWidget, history: QWidget) -> None:
        """Take the roller in: the controls above, the history filling the rest."""
        self._body_box.addWidget(panel)
        self._body_box.addWidget(history, stretch=1)
        panel.show()
        history.show()

    def release(self) -> None:
        """Let the roller go again, without destroying it.

        Only the layout lets go; the widgets stay children of the body until the
        surface re-parents them, which is the same rescue
        :meth:`~mm_companion.ui.pinned_panel._PinnedSlot.release_frame` performs —
        a widget still parented to a container Qt is about to free goes with it.
        """
        for index in reversed(range(self._body_box.count())):
            item = self._body_box.itemAt(index)
            widget = item.widget() if item is not None else None
            if widget is not None:
                self._body_box.removeWidget(widget)


class CompactController(QObject):
    """Drives one window in and out of compact mode.

    Built by the window, which supplies three things: itself, the **surface** to
    borrow the roller from (anything with ``release_roller``/``restore_roller``),
    and the **content** widget to hide while compact. The controller builds
    :attr:`page`; the window's only other job is to put it in its layout.
    """

    #: How long the window takes to grow or shrink. A class attribute so tests can
    #: zero it and assert on the resting state without waiting on a timer — the
    #: same seam :attr:`~mm_companion.ui.sections.powers.PowersSection.TRANSITION_MS`
    #: is.
    ANIMATION_MS = 180

    #: The mode changed; carries whether the window is now compact.
    compactChanged = Signal(bool)

    def __init__(
        self,
        window: QMainWindow,
        surface: object,
        content: QWidget,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent or window)
        self._window = window
        self._surface = surface
        self._content = content
        self._compact = False
        # The geometry to give back on the way out, and whether to re-maximize.
        self._normal_geometry: QRect | None = None
        self._normal_state: QByteArray | None = None
        self._was_maximized = False
        # The borrowed pair, held only for as long as the loan lasts.
        self._roller: tuple[QWidget, QWidget] | None = None
        self._animation: QPropertyAnimation | None = None

        self.page = CompactPage()
        self.page.hide()
        self.page.strip.expandRequested.connect(self.leave)
        self.page.strip.closeRequested.connect(window.close)
        self.page.strip.onTopToggled.connect(self._set_on_top)

        # Escape is the other way out. Disabled while the window is its full self,
        # so it goes on doing whatever it did before.
        self._escape = QShortcut(QKeySequence(Qt.Key.Key_Escape), window)
        self._escape.setEnabled(False)
        self._escape.activated.connect(self.leave)

    @property
    def is_compact(self) -> bool:
        """Whether the window is currently the mini roller."""
        return self._compact

    def saved_geometry(self) -> QByteArray | None:
        """The window's ``saveGeometry`` blob from before it shrank, or ``None``.

        What a window persists on close instead of asking itself, and the reason
        it cannot just ask: closing the app from the mini window would otherwise
        write 380x560 — frameless, and never maximized — into the shared layout
        setting, and every sheet would open that way from then on. Captured before
        the window is un-maximized, so a window that was maximized is remembered
        as maximized.
        """
        return self._normal_state if self._compact else None

    def remember_size(self) -> None:
        """Store the mini window's current size as the one to reopen at (else nothing)."""
        if not self._compact:
            return
        geometry = self._window.geometry()
        storage.set_compact_settings(width=geometry.width(), height=geometry.height())

    # -- the transition ------------------------------------------------------

    def toggle(self) -> None:
        """Shrink to the roller, or give the full window back."""
        self.leave() if self._compact else self.enter()

    def enter(self) -> None:
        """Collapse the window to the mini roller.

        Does nothing when the surface has no roller to lend — a sheet whose Dice
        block a mod replaced with something that does not offer one simply has no
        compact mode, rather than a window that empties itself.
        """
        if self._compact:
            return
        roller = self._surface.release_roller()
        if roller is None:
            return
        panel, history = roller
        self._roller = (panel, history)

        # Captured before anything moves, so what the window is remembered at on
        # close is the shape the user actually left it in (see :meth:`saved_geometry`).
        self._normal_state = self._window.saveGeometry()
        # A maximized window cannot be resized, so drop out of it first — and
        # remember, so leaving puts it back the way it was found.
        self._was_maximized = self._window.isMaximized()
        if self._was_maximized:
            self._window.showNormal()
        self._normal_geometry = QRect(self._window.geometry())
        self._compact = True

        self.page.adopt(panel, history)
        panel.set_compact(True)
        # Hiding the content is what actually frees the window to shrink: a hidden
        # widget is left out of its layout's minimum, and the sheet's page and
        # pinned strip both report minimums far wider than the mini window.
        self._content.hide()
        self.page.show()
        self._window.menuBar().hide()

        preferences = storage.compact_settings()
        self.page.strip.set_on_top(preferences["on_top"])
        self._apply_flags(frameless=True, on_top=bool(preferences["on_top"]))
        self._escape.setEnabled(True)
        self._animate_to(self._compact_target(preferences))
        self.compactChanged.emit(True)

    def leave(self) -> None:
        """Give the full window back, and the roller with it."""
        if not self._compact:
            return
        self.remember_size()
        self._compact = False
        self._escape.setEnabled(False)
        # Flags first, then the animation — never the other way round, and never
        # both at once (see the module docstring).
        self._apply_flags(frameless=False, on_top=False)
        self._window.menuBar().show()
        target = self._normal_geometry or QRect(self._window.geometry())
        # The mini roller stays on screen while the window grows around it and is
        # handed back at the end, so the transition never shows an empty window.
        self._animate_to(target, on_finished=self._finish_leave)
        self.compactChanged.emit(False)

    def _finish_leave(self) -> None:
        if self._roller is None:
            return
        panel, _history = self._roller
        self._roller = None
        self.page.release()
        self._surface.restore_roller()
        panel.set_compact(False)
        self.page.hide()
        self._content.show()
        if self._was_maximized:
            self._window.showMaximized()

    def _set_on_top(self, on_top: bool) -> None:
        storage.set_compact_settings(on_top=bool(on_top))
        if self._compact:
            self._apply_flags(frameless=True, on_top=bool(on_top))

    # -- the window itself ---------------------------------------------------

    def _apply_flags(self, *, frameless: bool, on_top: bool) -> None:
        """Take the OS frame away (or give it back), keeping the window where it is.

        Qt hides a window whose flags change and the platform recreates it, so the
        geometry has to be re-applied afterwards — and this must happen while
        nothing is animating. A window that was never shown is left alone: a test
        that only constructs one must not have it thrown onto the screen.
        """
        window = self._window
        visible = window.isVisible()
        geometry = window.geometry()
        window.setWindowFlag(Qt.WindowType.FramelessWindowHint, frameless)
        window.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, on_top)
        if visible:
            window.show()
            window.setGeometry(geometry)

    def _compact_target(self, preferences: dict) -> QRect:
        """Where and how big the mini window goes.

        The size the user last left it at, or the theme's if they never have; the
        position is the full window's own top-left, so the roller appears where
        the window already was rather than flying off to a corner. Clamped onto
        the screen, since the full window's corner can be off it when maximized.
        """
        width = int(preferences.get("width") or theme.metric("compact.width"))
        height = int(preferences.get("height") or theme.metric("compact.height"))
        origin = (self._normal_geometry or self._window.geometry()).topLeft()
        rect = QRect(origin, QSize(width, height))
        screen = self._window.screen()
        if screen is not None:
            rect = _clamped(rect, screen.availableGeometry())
        return rect

    def _animate_to(self, target: QRect, on_finished=None) -> None:
        """Ease the window to *target*, or jump there when there is nothing to see.

        A zeroed duration cuts straight to the end, which is what lets a test
        assert on the resting state; so does a window that is not on screen, since
        animating one nobody can see is only a way to make the state a test reads
        depend on the event loop.
        """
        window = self._window
        if self._animation is not None:
            self._animation.stop()
            self._animation = None
        if self.ANIMATION_MS <= 0 or not window.isVisible():
            window.setGeometry(target)
            if on_finished is not None:
                on_finished()
            return
        animation = QPropertyAnimation(window, b"geometry", window)
        animation.setStartValue(QRect(window.geometry()))
        animation.setEndValue(QRect(target))
        animation.setDuration(self.ANIMATION_MS)
        animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        if on_finished is not None:
            animation.finished.connect(on_finished)
        animation.finished.connect(self._clear_animation)
        self._animation = animation
        animation.start(QAbstractAnimation.DeletionPolicy.DeleteWhenStopped)

    def _clear_animation(self) -> None:
        self._animation = None


def _clamped(rect: QRect, bounds: QRect) -> QRect:
    """*rect* moved (never resized) to sit inside *bounds* as far as it can."""
    placed = QRect(rect)
    placed.moveLeft(max(bounds.left(), min(placed.left(), bounds.right() - placed.width() + 1)))
    placed.moveTop(max(bounds.top(), min(placed.top(), bounds.bottom() - placed.height() + 1)))
    return placed

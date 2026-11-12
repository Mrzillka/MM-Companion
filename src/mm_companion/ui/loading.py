"""A loading screen over a window while the GUI thread is busy building widgets.

Opening a sheet, opening the GM window and switching to the simple sheet each build
hundreds of widgets in one go, and Qt builds widgets only on the GUI thread — so for
that long the event loop is not running and the window is a frozen picture. This does
not make the work any shorter. It covers the window it starts from with an opaque page
holding a label and a bar, and paints that page **synchronously** (``repaint``, not
``update``) each time the work reports a step, so the user sees it move instead of a
window that looks hung.

It goes up only once the work has run :data:`SHOW_AFTER_S` — a page flashing over
the window for a few milliseconds reads worse than nothing — or at once, for work
that starts by waiting on the network.

Three rules shape it:

- **No event loop runs while it is up.** ``processEvents`` would let a timer, a
  session message or a click land in a sheet that is half built — the reentrancy
  every adaptive widget here is guarded against. A synchronous repaint paints and
  delivers nothing else.
- **It is opaque.** The widgets under it are mid-build: reparented, unlaid-out, about
  to be shown. A wash would show them in that state; an opaque child hides them, and
  Qt skips painting what it covers.
- **It is a child of the window, outside every layout.** No new window, and nothing
  about the window's size or minimum changes while it is up.

How the work reports. :func:`loading` opens the screen and owns the whole bar.
Inside it, :func:`part` gives one phase a share of *what is left* of the bar — so
"the lock takes 0.3, then the build 0.75" leaves the last part for whatever follows,
whether or not the lock had anything to do — and a phase may split its share again.
:func:`expect` and :func:`step` divide a share into counted steps (one per block
built). All four do nothing when no screen is up, so a sheet built where
nobody is waiting — a spare NPC sheet in idle time, a test — reports into nothing.
And a :func:`loading` started inside another one opens no second screen: it is a
:func:`part` of the one already up, which is how a sheet opened straight into the
simple sheet runs one bar from end to end.

A **view switch** (:func:`fade_through`) runs the same work between two fades. The old
view fades out the moment the switch is asked for — before the build blocks the event
loop, so the fade is a real animation and the press answers at once — then the work
runs behind the screen, and the new view fades in once the loop is free again. Both
fades cross between *pictures* (``QWidget.grab``): the window under them is either
about to be torn up or not yet painted, and a picture costs one paint, not one per
frame. Whether the bar is on the screen it fades to depends on how long the same
switch took last time in that window; a quick one fades between the two views alone,
and its bar comes up only if this time it runs long.

The launcher uses the same fade for a window it opens: it fades to its screen at the
click, the window is built behind it, and the new window fades in over it by its own
opacity (:func:`show_window`) — the launcher, if the new window replaces it, going
only once that is in.
"""

from __future__ import annotations

import time
import weakref
from collections.abc import Callable, Iterator
from contextlib import contextmanager

import shiboken6
from PySide6.QtCore import QEasingCurve, QEvent, QObject, Qt, QTimer, QVariantAnimation
from PySide6.QtGui import QFont, QPixmap
from PySide6.QtWidgets import QApplication, QLabel, QProgressBar, QVBoxLayout, QWidget

from mm_companion.core import diagnostics
from mm_companion.ui import theme
from mm_companion.ui.paint import painting
from mm_companion.ui.widgets import discard_widget

#: The bar's resolution. Fine enough that each block of a sheet visibly moves it.
_RANGE = 1000


class _Span:
    """One share of the bar, from *start* to *end* (both fractions of the whole).

    Its cursor moves by counted steps (:meth:`step`, out of what :meth:`expect` said
    there would be) or jumps to the end of a sub-span when that one finishes. It never
    moves backwards and never leaves its own range.
    """

    def __init__(self, start: float, end: float) -> None:
        self.start = start
        self.end = end
        self.cursor = start
        self._expected = 0
        self._done = 0

    def expect(self, count: int) -> None:
        self._expected += max(0, int(count))

    def step(self, count: int) -> None:
        self._done += max(0, int(count))
        if self._expected:
            share = min(1.0, self._done / self._expected)
            self.cursor = max(self.cursor, self.start + (self.end - self.start) * share)

    def child(self, share: float) -> _Span:
        """A sub-span of *share* of what is left of this one, from the cursor on."""
        width = (self.end - self.cursor) * max(0.0, min(1.0, share))
        return _Span(self.cursor, self.cursor + width)

    def finish_child(self, child: _Span) -> None:
        self.cursor = max(self.cursor, min(self.end, child.end))


class LoadingScreen(QWidget):
    """The opaque page with a label and a bar, laid over the whole of *host*.

    With *content* off it goes up bare — the page alone — and :meth:`show_content`
    puts the label and the bar on it later: a view switch that was quick last time
    fades to the bare page, and gets its bar only if this one runs long.
    """

    def __init__(self, host: QWidget, text: str, *, content: bool = True) -> None:
        super().__init__(host)
        self.hide()  # made visible by cover(), once it is in place
        self.setObjectName("loadingScreen")
        # Opaque, in the window's own colour: what is underneath is mid-build. Not
        # ``WA_OpaquePaintEvent`` as well: that tells Qt the widget fills itself, so it
        # skips the fill, and the half-built window shows straight through.
        self.setAutoFillBackground(True)

        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.setSpacing(int(theme.metric("space.md")))

        self._label = QLabel(text)
        self._label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        font = QFont(self._label.font())
        font.setPointSizeF(theme.font_size("size.loading"))
        self._label.setFont(font)
        layout.addWidget(self._label, alignment=Qt.AlignmentFlag.AlignHCenter)

        self._bar = QProgressBar()
        self._bar.setObjectName("loadingBar")
        self._bar.setRange(0, _RANGE)
        self._bar.setValue(0)
        self._bar.setTextVisible(False)
        self._bar.setFixedHeight(int(theme.metric("space.md")))
        radius = int(theme.metric("radius.chip"))
        width = int(theme.metric("border.width"))
        # Its own sheet, from tokens every preset defines: Classic emits no
        # application rule for a progress bar (see the Equipment block's budget bar).
        self._bar.setStyleSheet(
            f"#loadingBar {{ border: {width}px solid {theme.color('border.card')};"
            f" border-radius: {radius}px; background: transparent; }}"
            f"#loadingBar::chunk {{ background: {theme.color('accent')};"
            f" border-radius: {radius}px; }}"
        )
        layout.addWidget(self._bar, alignment=Qt.AlignmentFlag.AlignHCenter)
        self._label.setVisible(content)
        self._bar.setVisible(content)

        self._host = host
        host.installEventFilter(self)
        self._fit()

    @property
    def text(self) -> str:
        return self._label.text()

    @property
    def value(self) -> float:
        """How far along the bar is, from 0 to 1."""
        return self._bar.value() / _RANGE

    @property
    def content_shown(self) -> bool:
        """Whether the label and the bar are on the page, not just its background."""
        return not self._bar.isHidden()

    def cover(self, *, paint: bool = True) -> None:
        """Lay the page over the window — and paint it now, unless *paint* is off."""
        self._fit()
        self.show()
        self.raise_()
        if paint:
            self.repaint()

    def show_content(self) -> None:
        """Put the label and the bar on a page that went up bare, and paint it now."""
        if self.content_shown:
            return
        self._label.show()
        self._bar.show()
        if self.isVisible():
            self.repaint()

    def show_progress(self, fraction: float) -> None:
        """Move the bar to *fraction* — forwards only — and paint it if it moved."""
        value = round(max(0.0, min(1.0, fraction)) * _RANGE)
        if value <= self._bar.value():
            return
        self._bar.setValue(value)
        if self.isVisible():
            self.repaint()

    def say(self, text: str) -> None:
        if text == self._label.text():
            return
        self._label.setText(text)
        if self.isVisible():
            self.repaint()

    def release(self) -> None:
        """Stop following the window. :func:`discard_widget` does the rest."""
        self._host.removeEventFilter(self)

    def _fit(self) -> None:
        rect = self._host.rect()
        self.setGeometry(rect)
        gutter = int(theme.metric("space.lg"))
        width = min(int(theme.metric("loading.width")), rect.width() - 2 * gutter)
        self._bar.setFixedWidth(max(0, width))

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802 - Qt API
        if watched is self._host and event.type() == QEvent.Type.Resize:
            self._fit()
        return False


# -- the reporting side --------------------------------------------------------

#: How long a piece of work runs before its screen goes up, in seconds. A sheet handed
#: to a ready spare, or a simple sheet with little on it, is done well inside this, and
#: a page flashing over the window for a few milliseconds reads worse than nothing.
#: ``None`` never shows one (the test suite, which builds thousands of windows).
SHOW_AFTER_S: float | None = 0.15


class _Run:
    """One piece of work in progress: where its screen goes, and its shares of the bar.

    A view switch lends it the screen its fade went to (*screen*), which may be up
    bare; the run then only puts the label and the bar on it if the work runs long,
    and leaves it up for the fade back in.
    """

    def __init__(self, host: QWidget, text: str, screen: LoadingScreen | None = None) -> None:
        self.host = host
        self.text = text
        self.started = time.perf_counter()
        self.screen = screen
        self._owned = screen is None
        self._waiting = False
        self.spans = [_Span(0.0, 1.0)]

    @property
    def shown(self) -> bool:
        """Whether the screen is up with its bar on it."""
        return self.screen is not None and self.screen.content_shown

    def cover(self) -> None:
        """Put the screen up with its bar now, if it is not up already."""
        if self.shown or SHOW_AFTER_S is None:
            return
        if self.screen is None:
            self.screen = LoadingScreen(self.host, self.text)
            self.screen.show_progress(self.spans[-1].cursor)
            self.screen.cover()
        else:
            self.screen.say(self.text)
            self.screen.show_progress(self.spans[-1].cursor)
            self.screen.show_content()
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        self._waiting = True

    def due(self) -> bool:
        return SHOW_AFTER_S is not None and time.perf_counter() - self.started >= SHOW_AFTER_S

    def close(self) -> None:
        if self._waiting:
            QApplication.restoreOverrideCursor()
            self._waiting = False
        screen, self.screen = self.screen, None
        if screen is None or not self._owned:
            return
        screen.release()
        discard_widget(screen)


#: The work in progress, if any. One at a time: a loading inside another joins it.
_run: _Run | None = None


def active_screen() -> LoadingScreen | None:
    """The loading screen that is up right now with its bar on it, or ``None``."""
    return _run.screen if _run is not None and _run.shown else None


@contextmanager
def loading(window: QWidget | None, text: str, *, at_once: bool = False) -> Iterator[None]:
    """Cover *window* with a loading screen while the block runs, if it runs long.

    *window* may be any widget; the screen goes over its top-level window, and only
    if that is on screen — a window nobody can see needs no screen. It goes up once
    the work has run :data:`SHOW_AFTER_S` and reports a step, or at once with
    *at_once* — for work that starts by waiting on the network, which reports
    nothing while it waits. Inside another :func:`loading`, this is a :func:`part`
    of that one, taking the rest of its share. It comes down however the block ends.
    """
    if _run is not None:
        with part(1.0):
            yield
        return
    host = window.window() if window is not None else None
    if host is None or not host.isVisible():
        yield
        return
    run = _Run(host, text)
    with _running(run):
        if at_once:
            run.cover()
        yield


@contextmanager
def _running(run: _Run) -> Iterator[None]:
    """Make *run* the work in progress while the block runs, and close it after."""
    global _run
    _run = run
    try:
        yield
    finally:
        _run = None
        run.close()


@contextmanager
def part(share: float) -> Iterator[None]:
    """Give *share* (0–1) of what is left of the current span to the block inside.

    Whatever the block reports divides that share; when it ends, the bar stands at
    the end of it, whether or not the block counted all its steps.
    """
    run = _run
    if run is None:
        yield
        return
    parent = run.spans[-1]
    child = parent.child(share)
    run.spans.append(child)
    try:
        yield
    finally:
        if run.spans[-1] is child:
            run.spans.pop()
        parent.finish_child(child)
        _show()


def expect(count: int) -> None:
    """Say the current share will be reported in *count* more steps."""
    if _run is not None:
        _run.spans[-1].expect(count)


def step(count: int = 1) -> None:
    """Report *count* steps of the current share done."""
    if _run is not None:
        _run.spans[-1].step(count)
        _show()


def say(text: str) -> None:
    """Change what the loading screen says, and put it up now if it is not.

    What comes next is a wait — "Connecting…" while a socket waits on a server —
    which reports nothing until it is over, so the screen cannot wait for a step.
    """
    if _run is None:
        return
    _run.text = text
    if not _run.shown:
        _run.cover()
    else:
        _run.screen.say(text)


def _show() -> None:
    run = _run
    if run is None:
        return
    if not run.shown and run.due():
        run.cover()
    if run.shown:
        run.screen.show_progress(run.spans[-1].cursor)


# -- view switches -------------------------------------------------------------

#: How long each fade of a view switch lasts, in seconds — out of the old view, and
#: into the new one. ``None`` switches at once with no fade, as does every switch
#: while :data:`SHOW_AFTER_S` is ``None`` (the test suite, which needs a switch to be
#: over when the call returns).
FADE_S: float | None = 0.15

#: How long each switch's work took last time, per window and per text: whether the
#: next one fades to the bar or to the bare page. Weak, so a closed window is forgotten.
_took: weakref.WeakKeyDictionary[QWidget, dict[str, float]] = weakref.WeakKeyDictionary()

#: The switches whose fades are running: at most one per window (see :func:`fading`).
_fades: set[_Fade] = set()


class _Veil(QWidget):
    """A picture laid over the window, fading out: what a view switch fades between.

    Over a *base* picture it is opaque — the new view, live underneath, is not painted
    once a frame — and lets the mouse through to that view. With no base it is
    translucent over the loading screen, and the view it shows is on its way out, so
    a click on it goes nowhere.
    """

    def __init__(self, host: QWidget, top: QPixmap, base: QPixmap | None = None) -> None:
        super().__init__(host)
        self.hide()
        self._top = top
        self._base = base
        self._alpha = 1.0
        if base is not None:
            # It fills itself edge to edge from the base picture, so Qt need not
            # paint what is under it.
            self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)
            self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setGeometry(host.rect())
        self.show()
        self.raise_()

    def set_alpha(self, alpha: float) -> None:
        self._alpha = float(alpha)
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802, ANN001 - Qt API
        with painting(self) as painter:
            if self._base is not None:
                painter.drawPixmap(0, 0, self._base)
            painter.setOpacity(max(0.0, min(1.0, self._alpha)))
            painter.drawPixmap(0, 0, self._top)


class _Fade(QObject):
    """One view switch over *host*: fade out, do the work behind the screen, fade in."""

    def __init__(
        self,
        host: QWidget,
        text: str,
        work: Callable[[], None],
        finished: Callable[[], None] | None,
        after: Callable[[], None] | None,
        at_once: bool,
    ) -> None:
        super().__init__(host)
        self.host = host
        self.text = text
        self._work = work
        self._finished = finished
        self._after = after
        self._at_once = at_once
        self._screen: LoadingScreen | None = None
        self._veil: _Veil | None = None
        self._animation: QVariantAnimation | None = None

    def start(self) -> None:
        _fades.add(self)
        self.destroyed.connect(lambda *_: _fades.discard(self))
        self.host.installEventFilter(self)
        old = self.host.grab()  # before anything is laid over it
        last = _took.get(self.host, {}).get(self.text)
        slow = self._at_once or last is None or last >= (SHOW_AFTER_S or 0.0)
        self._screen = LoadingScreen(self.host, self.text, content=slow)
        # Not painted now: the veil goes over it first, so the next frame still shows
        # the old view, and the fade starts from there.
        self._screen.cover(paint=False)
        self._veil = _Veil(self.host, old)
        self._animate(self._build)

    def _animate(self, then: Callable[[], None]) -> None:
        animation = QVariantAnimation(self._veil)
        animation.setStartValue(1.0)
        animation.setEndValue(0.0)
        animation.setDuration(round((FADE_S or 0.0) * 1000))
        animation.setEasingCurve(QEasingCurve.Type.OutQuad)
        animation.valueChanged.connect(self._veil.set_alpha)
        # On a turn of the loop of its own, not inside the animation's signal: what
        # comes next is the whole build, or a warning box with an event loop of its
        # own, and either would run inside Qt's animation timer while it ticks.
        animation.finished.connect(lambda: QTimer.singleShot(0, self, then))
        self._animation = animation
        animation.start()

    def _shed_veil(self) -> None:
        veil, self._veil, self._animation = self._veil, None, None
        if veil is not None and shiboken6.isValid(veil):
            discard_widget(veil)

    def _build(self) -> None:
        self._shed_veil()
        screen = self._screen
        started = time.perf_counter()
        try:
            run = _Run(self.host, self.text, screen)
            with _running(run):
                if self._at_once:
                    run.cover()
                self._work()
        except Exception:  # noqa: BLE001 - the window still has to come back
            diagnostics.log_exception(f"A view switch failed ({self.text})")
        _took.setdefault(self.host, {})[self.text] = time.perf_counter() - started
        if screen is not None and screen.content_shown:
            screen.show_progress(1.0)
        self._call("_finished")
        # A turn of the loop for the new view to lay itself out before its picture is
        # taken, with the screen still over it.
        QTimer.singleShot(0, self, self._reveal)

    def _call(self, name: str) -> None:
        """Run the *finished* or *after* callback, once, logging what it raises."""
        callback = getattr(self, name)
        setattr(self, name, None)
        if callback is None:
            return
        try:
            callback()
        except Exception:  # noqa: BLE001 - see _build
            diagnostics.log_exception(f"Finishing a view switch failed ({self.text})")

    def _reveal(self) -> None:
        waiting = _handoffs.get(self.host)
        if waiting is not None:
            # A window is fading in to replace this one: the screen stays up under it,
            # and comes down only once this one has gone (see show_window).
            waiting.append(self._done)
            return
        screen, self._screen = self._screen, None
        if screen is None or not shiboken6.isValid(screen):
            self._done()
            return
        top = screen.grab()
        screen.hide()
        base = self.host.grab() if self.host.isVisible() else None
        screen.release()
        discard_widget(screen)
        if base is None:
            self._done()
            return
        self._veil = _Veil(self.host, top, base)
        self._animate(self._done)

    def _done(self) -> None:
        self._shed_veil()
        screen, self._screen = self._screen, None
        if screen is not None and shiboken6.isValid(screen):
            screen.release()
            discard_widget(screen)
        self._call("_finished")
        if shiboken6.isValid(self.host):
            self.host.removeEventFilter(self)
        _fades.discard(self)
        self.deleteLater()
        # Last: a warning box runs an event loop of its own, and should find the
        # window with nothing laid over it.
        self._call("_after")

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802 - Qt API
        # A picture of the window at its old size is wrong at the new one: skip to the
        # end of the fade that is running rather than stretch it.
        if watched is self.host and event.type() == QEvent.Type.Resize:
            animation = self._animation
            if animation is not None and animation.state() == QVariantAnimation.State.Running:
                animation.setCurrentTime(animation.duration())
        return False


def fading(window: QWidget) -> bool:
    """Whether a switch is fading over *window*'s top-level window right now."""
    host = window.window()
    return any(fade.host is host for fade in _fades)


def fade_through(
    window: QWidget,
    text: str,
    work: Callable[[], None],
    finished: Callable[[], None] | None = None,
    *,
    after: Callable[[], None] | None = None,
    at_once: bool = False,
) -> None:
    """Switch the view of *window*: fade out, run *work* behind a loading screen, fade in.

    *finished* runs once *work* is over, before the new view fades in — where a
    window opened by the work is shown (:func:`show_window`). *after* runs once
    everything is over and the screen is down — where a warning about a failure
    goes. *at_once* puts the bar on the screen from the start, for work that begins
    by waiting on the network (see :func:`loading`).

    The fade out runs on the event loop, so this returns **before** *work* has run —
    except where a fade makes no sense, and then it is plain :func:`loading` and all
    three have run by the time it returns: a window that is not on screen, a switch
    inside other work in progress, fades or screens turned off, or a switch already
    fading over the window (a caller should refuse a second one while :func:`fading`
    says so). A fade cannot raise to the caller, so it logs whatever the work or the
    callbacks raise.
    """
    host = window.window()
    if (
        FADE_S is None
        or SHOW_AFTER_S is None
        or _run is not None
        or not host.isVisible()
        or fading(host)
    ):
        try:
            with loading(window, text, at_once=at_once):
                work()
        finally:
            if finished is not None:
                finished()
            if after is not None:
                after()
        return
    _Fade(host, text, work, finished, after, at_once).start()


# -- a new window ----------------------------------------------------------------

#: Windows being replaced by one fading in over them (see :func:`show_window`), each
#: with what waits for it to go: the switch whose screen is still up on it.
_handoffs: dict[QWidget, list[Callable[[], None]]] = {}


def show_window(window: QWidget, *, replacing: QWidget | None = None) -> None:
    """Show the new top-level *window* fading in — and then hide *replacing*, if given.

    The launcher opening a sheet: the launcher stays up under the sheet, its loading
    screen still on it, until the sheet is in, and goes only then — so there is no
    moment with neither on screen. The fade is the window's own opacity, which a
    platform without a compositor ignores; it then simply appears. With fades off it
    is a plain ``show()``, and *replacing* is hidden at once.
    """
    if FADE_S is None or SHOW_AFTER_S is None:
        window.show()
        if replacing is not None:
            replacing.hide()
        return
    if replacing is not None:
        _handoffs.setdefault(replacing, [])

    def release(hide: bool) -> None:
        if replacing is None:
            return
        if hide and shiboken6.isValid(replacing):
            replacing.hide()
        for waiting in _handoffs.pop(replacing, []):
            waiting()

    def end() -> None:
        window.setWindowOpacity(1.0)
        release(hide=True)

    window.setWindowOpacity(0.0)
    window.show()
    animation = QVariantAnimation(window)
    animation.setStartValue(0.0)
    animation.setEndValue(1.0)
    animation.setDuration(round(FADE_S * 1000))
    animation.setEasingCurve(QEasingCurve.Type.OutQuad)
    animation.valueChanged.connect(window.setWindowOpacity)
    # Off the animation's own signal, as in _Fade._animate.
    animation.finished.connect(lambda: QTimer.singleShot(0, window, end))
    # A window destroyed before it is in still lets the one it was replacing go on.
    # (Nothing of the window itself may be touched there: it is half gone.)
    window.destroyed.connect(lambda *_: release(hide=False))
    animation.start()

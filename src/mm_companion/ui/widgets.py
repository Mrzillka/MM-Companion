"""Small shared widget factories and style snippets used across the sections.

These keep widget construction consistent (and wheel-guarded) in one place,
rather than each section rolling its own spin boxes and table items. The style
helpers at the bottom do the same for the two or three inline stylesheets the
sheet repeats everywhere — a muted secondary line, a tinted emphasis — so those
resolve their theme tokens in one place instead of a dozen f-strings.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager

from PySide6.QtCore import QEvent, QObject, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QFont, QFontMetrics
from PySide6.QtWidgets import (
    QAbstractScrollArea,
    QDoubleSpinBox,
    QFrame,
    QLabel,
    QPushButton,
    QSpinBox,
    QTableWidgetItem,
    QWidget,
)

from mm_companion.ui import theme
from mm_companion.ui.wheel_guard import guard_wheel


def make_spin_box(
    minimum: int,
    maximum: int,
    *,
    value: int | None = None,
    buttons: bool = True,
    max_width: int | None = None,
    guarded: bool = True,
) -> QSpinBox:
    """Build a range-bounded spin box.

    ``buttons=False`` hides the up/down arrows, ``max_width`` caps the width for
    the compact stat/skill columns, and ``guarded`` (the default) installs the
    wheel guard so the box only reacts to the wheel once focused. Pass
    ``guarded=False`` when the caller guards the box itself in a batch.
    """
    spin = QSpinBox()
    spin.setRange(minimum, maximum)
    if value is not None:
        spin.setValue(value)
    if not buttons:
        spin.setButtonSymbols(QSpinBox.NoButtons)
        # The theme reserves a right-hand column for the arrows the platform style
        # draws (see mm_companion.ui.theme.qss). A box with none must not pay for
        # it: on the sheet's narrow rank grids that padding is most of the cell.
        spin.setProperty(theme.ARROWLESS_PROPERTY, True)
    if max_width is not None:
        spin.setMaximumWidth(max_width)
    if guarded:
        guard_wheel(spin)
    return spin


def make_double_spin_box(
    minimum: float,
    maximum: float,
    *,
    value: float | None = None,
    decimals: int = 2,
    step: float = 0.1,
    max_width: int | None = None,
    guarded: bool = True,
) -> QDoubleSpinBox:
    """Build a fractional spin box, wheel-guarded like its integer sibling.

    Point sizes, opacities and scale factors are all fractional, so the theme
    editor needs the same factory ``make_spin_box`` gives whole numbers — chiefly
    for the wheel guard, which a long scrolling form cannot do without.
    """
    spin = QDoubleSpinBox()
    spin.setDecimals(decimals)
    spin.setRange(minimum, maximum)
    spin.setSingleStep(step)
    if value is not None:
        spin.setValue(value)
    if max_width is not None:
        spin.setMaximumWidth(max_width)
    if guarded:
        guard_wheel(spin)
    return spin


def readonly_item(text: str, *, center: bool = False) -> QTableWidgetItem:
    """A table item that displays *text* but cannot be edited in place."""
    item = QTableWidgetItem(text)
    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
    if center:
        item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
    return item


def title_with_cost(title: str, points: int) -> str:
    """A group-box title annotated with its running power-point cost, e.g.
    ``"Abilities — 24 PP"`` — kept in one place so every section reads the same."""

    return f"{title} — {points} PP"


class ElidingLabel(QLabel):
    """A one-line label that gives way to its layout instead of dictating a width.

    A plain ``QLabel`` reports the full width of its text as its minimum, so a
    long caption becomes a floor the container can never go under — which is
    wrong for a *caption*: it describes the widget it sits on, it does not decide
    how wide that widget must be. This one asks for room for an ellipsis and
    shows the text elided to whatever width it is given, keeping the full string
    as its :meth:`text` and offering it as the tooltip while it doesn't fit.

    The elision is done by swapping the displayed string, not by painting the
    text ourselves, so the label keeps being drawn by Qt with whatever the
    stylesheet gives it (``#blockTitleLabel`` has its own colour and weight).
    """

    def __init__(self, text: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._full_text = ""
        self._hover_text = ""
        self.setText(text)

    def setText(self, text: str) -> None:  # noqa: N802 - Qt override
        self._full_text = text
        self._elide()

    def set_hover_text(self, text: str) -> None:
        """A tooltip of the caller's own, kept through elision.

        The label owns its tooltip — it puts the full caption there when the
        caption doesn't fit — so a caller that just called ``setToolTip`` would
        find it wiped by the next resize. A hover text set here **wins**: showing
        the whole caption is a fallback for a clipped one, and a caller who has
        said what this label's tooltip is has answered a better question. Nothing
        is lost where the two meet, since a summary written for a name label leads
        with that name.
        """
        self._hover_text = text
        self._elide()

    def text(self) -> str:
        """The whole caption, even while a narrower one is on screen."""
        return self._full_text

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt override
        """Room for the ellipsis alone — measured, not a magic pixel count."""
        return QSize(self.fontMetrics().horizontalAdvance("…"), super().minimumSizeHint().height())

    def resizeEvent(self, event) -> None:  # noqa: ANN001, N802 - Qt override
        super().resizeEvent(event)
        self._elide()

    def _elide(self) -> None:
        metrics = self.fontMetrics()
        width = self.contentsRect().width()
        fits = metrics.horizontalAdvance(self._full_text) <= width
        # Re-entrant through resizeEvent, but idempotent: the same text and width
        # yield the same string, and QLabel.setText returns early on no change.
        super().setText(
            self._full_text
            if fits
            else metrics.elidedText(self._full_text, Qt.TextElideMode.ElideRight, width)
        )
        self.setToolTip(self._hover_text or ("" if fits else self._full_text))


#: How long a :class:`ConfirmButton` stays armed with nobody touching it. Long
#: enough to read the changed caption and reach it, short enough that a button
#: left armed by a stray click is back to its safe self before it is passed again.
CONFIRM_ARM_MS = 3000


class ConfirmButton(QPushButton):
    """A button that asks a second time, in place, instead of opening a dialog.

    The app's usual answer for a destructive action is
    ``QMessageBox.question`` defaulting to No, and for a reversible one no
    question at all plus undo. This is the third case: an action that is not
    reversible and is taken **mid-round**, where a modal dialog stops the table
    to say something the button could have said itself.

    One click arms it — the caption becomes *confirm_text* and the button wears
    ``tint.worse`` — and the next click within :data:`CONFIRM_ARM_MS` does the
    thing. Nothing happens if the second click never comes: a single-shot timer
    puts the caption back, so a stray press disarms itself rather than leaving a
    live trigger on the board. The armed caption is the whole warning, which is
    why it must read as a *question* about what is about to happen.

    The look is a widget-level stylesheet built from tokens rather than a theme
    rule: ``QToolButton:checked`` is in the app's QSS and push buttons are not,
    so a checked-style push button has to define its own state or Classic (which
    emits almost no sheet) would show no change at all.
    """

    #: The second click landed while armed — do the thing.
    confirmed = Signal()

    def __init__(
        self,
        text: str,
        *,
        confirm_text: str = "Confirm?",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(text, parent)
        self._resting_text = text
        self._confirm_text = confirm_text
        self._armed = False
        self._resting_tooltip = ""
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(CONFIRM_ARM_MS)
        self._timer.timeout.connect(self.disarm)
        # Not ``clicked`` from the outside: a caller connecting to ``clicked``
        # would fire on the arming press too, which is the accident this exists
        # to prevent.
        self.clicked.connect(self._on_clicked)
        self._reserve_width()

    @property
    def armed(self) -> bool:
        """Whether the next click would go through."""
        return self._armed

    def setText(self, text: str) -> None:  # noqa: N802 - Qt override
        """Set the resting caption, even while armed.

        Recorded rather than shown when armed, so a caller restating a caption
        cannot silently cancel a confirmation the user is halfway through.
        """
        self._resting_text = text
        self._reserve_width()
        if not self._armed:
            super().setText(text)

    def _reserve_width(self) -> None:
        """Hold one width, whichever caption the button is wearing.

        A button that resized as it armed would move out from under the pointer on
        its way to the second click — which is the one click that has to land where
        the first one did. Both captions are measured **bold**, since the armed one
        is, and the chrome the style adds is taken from the button's own hint
        rather than guessed at.
        """
        bold = QFont(self.font())
        bold.setBold(True)
        metrics = QFontMetrics(bold)
        text = max(
            metrics.horizontalAdvance(self._resting_text),
            metrics.horizontalAdvance(self._confirm_text),
        )
        chrome = self.sizeHint().width() - QFontMetrics(self.font()).horizontalAdvance(
            super().text()
        )
        self.setMinimumWidth(text + max(0, chrome))

    def setToolTip(self, text: str) -> None:  # noqa: N802 - Qt override
        self._resting_tooltip = text
        if not self._armed:
            super().setToolTip(text)

    def disarm(self) -> None:
        """Back to the safe caption — the timer ran out, or the deed is done."""
        self._timer.stop()
        if not self._armed:
            return
        self._armed = False
        super().setText(self._resting_text)
        super().setToolTip(self._resting_tooltip)
        self.setStyleSheet("")

    def _arm(self) -> None:
        self._armed = True
        super().setText(self._confirm_text)
        super().setToolTip("Click again to go ahead, or wait for this to clear.")
        self.setStyleSheet(
            f"QPushButton {{ color: {theme.color('tint.worse')}; font-weight: bold;"
            f" border: {int(theme.metric('border.width.emphasis'))}px solid"
            f" {theme.color('tint.worse')};"
            f" border-radius: {int(theme.metric('radius.chip'))}px; }}"
        )
        self._timer.start()

    def _on_clicked(self) -> None:
        if self._armed:
            self.disarm()
            self.confirmed.emit()
        else:
            self._arm()


def hline_separator() -> QFrame:
    """A sunken horizontal rule used to divide primary from derived stats."""
    line = QFrame()
    line.setFrameShape(QFrame.Shape.HLine)
    line.setFrameShadow(QFrame.Shadow.Sunken)
    return line


# -- gestures on a loose widget --------------------------------------------------


class _ContextRemoval(QObject):
    """Turns a right-click on one widget into "take this off".

    Parented to the widget it watches, so it is collected with it — no module-level
    registry of live chips to keep in step. It **consumes** the event rather than
    letting it through, which matters where the removable thing sits on something
    with a context menu of its own: a condition chip on a GM card would otherwise
    also open that card's "Remove from this session / Delete".

    Filtering the chip alone is enough for its children, since a ``ContextMenu``
    event a child ignores (a plain label, the Confused chip's die button) propagates
    up to it.
    """

    def __init__(self, target: QWidget, on_remove: Callable[[], None]) -> None:
        super().__init__(target)
        self._on_remove = on_remove
        target.installEventFilter(self)

    def eventFilter(self, watched: QObject, event) -> bool:  # noqa: N802 - Qt override
        if event.type() == QEvent.Type.ContextMenu:
            self._on_remove()
            return True
        return False


def attach_context_removal(widget: QWidget, on_remove: Callable[[], None], *, what: str) -> None:
    """Make a right-click anywhere on *widget* remove the thing it stands for.

    The one way a chip sheds what it names, so the gesture is the same on a GM
    card and on the character sheet. It replaces a visible ``×`` button, which is
    the trade: a chip that is only ever a caption is a good deal narrower — the
    reason this exists — but the affordance is now invisible, so *what* it removes
    is written into the tooltip rather than left to be discovered.
    """
    _ContextRemoval(widget, on_remove)
    hint = f"Right-click to remove {what}."
    existing = widget.toolTip()
    widget.setToolTip(f"{existing}\n\n{hint}" if existing else hint)


# -- inline style snippets ------------------------------------------------------
# Built fresh on each call rather than cached in a module constant, so a theme
# switch reaches a widget the next time it is styled.


def muted_style(*, italic: bool = False) -> str:
    """A secondary line — a description, a role note — that recedes without vanishing.

    Reads on light and dark alike: the ``text.muted`` token is a ``palette()``
    role in the system preset, and a chosen shade in a styled one.
    """
    tail = " font-style: italic;" if italic else ""
    return f"color: {theme.color('text.muted')};{tail}"


def tinted_style(token: str, *, bold: bool = True) -> str:
    """Foreground in the semantic colour token *token*, bold by default.

    The sheet's one way of saying "this number is not the plain one": green for a
    boost, red for a penalty or breach, amber for a warning, blue for a homerule.
    """
    tail = " font-weight: bold;" if bold else ""
    return f"color: {theme.color(token)};{tail}"


#: "This label is the important one." A bare weight with no colour, so it works
#: on any surface — the one style snippet with nothing to theme.
BOLD_STYLE = "font-weight: bold;"


def discard_widget(widget: QWidget) -> None:
    """Take *widget* off screen and destroy it — how a rebuilt block sheds a child.

    Every block that redraws by throwing its children away goes through here, and
    the order of the three calls is the whole point.

    **Hidden before it is unparented, always.** ``setParent(None)`` makes a widget a
    *top-level window*, and a child that was visible at the time does not reliably
    stay hidden through that transition: Qt realizes it and posts it a show, so a
    real window appears — on Windows a small grey rectangle flashing on screen, gone
    again the moment the deferred delete is serviced. That is the same failure
    ``2536db9`` fixed for ``setVisible(True)`` on a parentless widget, arriving by
    the other road, and it fired on *every* spin-box step of an ability: the System
    block redraws its speed rows, and each discarded row flashed. ``hide()`` first
    settles the widget as hidden while it still has a parent, and nothing shows it
    afterwards.

    **Unparented before it is deleted**, which is why ``deleteLater`` alone will not
    do: a widget still parented to a container keeps painting until the deferred
    delete is serviced, leaving a ghost panel on screen for the rest of the event
    loop turn.
    """

    widget.hide()
    widget.setParent(None)
    widget.deleteLater()


def enclosing_scroll_area(widget: QWidget) -> QAbstractScrollArea | None:
    """The scroll area *widget* is scrolled by, or ``None``.

    The **nearest** one, unlike :meth:`~mm_companion.ui.wheel_guard.WheelGuard.
    _page_scroll_area`, which walks all the way to the outermost so a wheel over an
    inner table still reaches the page. Here the nearest is the right answer wherever
    a block ends up: the page's scroll area on the page, the strip's when it is
    pinned, its own window's when it is floated out.
    """

    parent = widget.parentWidget()
    while parent is not None:
        if isinstance(parent, QAbstractScrollArea):
            return parent
        parent = parent.parentWidget()
    return None


@contextmanager
def rebuilding(widget: QWidget) -> Iterator[None]:
    """Redraw *widget*'s contents without the page moving or the block flickering.

    Two guards, both about the same moment: a block that rebuilds by deleting every
    child and making new ones is, briefly, a fraction of its own height and empty.

    **Painting is frozen for the duration.** Qt would otherwise repaint each
    intermediate state, so the block visibly empties and refills — which on a card
    tree of any size is a flicker on every redraw, and a redraw happens on every step
    of a spin box. ``setUpdatesEnabled(False)`` collapses the whole rebuild into the
    single repaint that follows it. Restored in a ``finally`` so an exception mid-way
    cannot leave a block that never paints again.

    **The scroll position is put back.** Qt clamps the enclosing scroll bar to the
    smaller maximum *while the block is short*. The cards coming back widen the range
    again but not the value, which Qt has already thrown away — so flipping a switch
    on a card near the bottom of a long sheet jumped the page somewhere else entirely.
    Restored **twice**: once now, and once on the next turn of the event loop, because
    the range is only recomputed on the layout pass that follows and an immediate
    ``setValue`` is clamped by the stale one. The deferred call is tied to the scroll
    area's own lifetime, so a block closed mid-rebuild does not fire it at a dead
    widget.
    """

    area = enclosing_scroll_area(widget)
    bars = [] if area is None else [area.verticalScrollBar(), area.horizontalScrollBar()]
    values = [(bar, bar.value()) for bar in bars if bar is not None]
    painted = widget.updatesEnabled()
    widget.setUpdatesEnabled(False)
    try:
        yield
    finally:
        widget.setUpdatesEnabled(painted)
        for bar, value in values:
            bar.setValue(value)
        if area is not None:
            QTimer.singleShot(0, area, lambda: [bar.setValue(value) for bar, value in values])

"""Shared drag-and-drop target styling: idle, accepting, and rejecting.

Every drop target in the app used to open-code the same thing — swap the
widget's stylesheet for an accent border and a faint wash while a compatible
drag hovers, put it back on leave or drop. Five widgets did it five times, each
with its own literal radius and border width.

Worse, none of them said anything when a drag was **not** welcome. They all ended
in a bare ``event.ignore()``, which is invisible whenever an ancestor goes on to
accept the drag: Qt only shows its native "no drop" cursor when *nothing* under
the pointer will take the payload. Dragging a flaw onto an extras group looked
exactly like dragging it somewhere that simply hadn't noticed.

:class:`DropFeedback` gives a widget all three states from theme tokens, so a
refused drop is now visibly refused::

    self._drops = DropFeedback(self, "EffectCard", radius="radius.canvas")
    self._drops.set_idle(f"border: 1px solid {theme.color('border.card')};")

    def dragEnterEvent(self, event):
        if self._accepts(event):
            self._drops.show_accept()
            event.acceptProposedAction()
        else:
            self._drops.show_reject()
            event.ignore()

Drop semantics are unchanged: a rejecting widget still calls ``event.ignore()``
and an ancestor may still handle the drag. Only the appearance is added.

Clearing the reject state needs care. Qt stops sending a widget drag *move*
events once it has ignored the enter, so a rejected outline cannot rely on a
later move to take itself down. :meth:`show_reject` therefore also arms a short
timer, and every instance clears on the next enter anywhere (see
:func:`clear_all`) as well as on leave and drop — so an outline can't be left
stranded when the drag ends somewhere the widget never hears about.
"""

from __future__ import annotations

from weakref import WeakSet

from PySide6.QtCore import QEasingCurve, QPropertyAnimation, QRect, Qt, QTimer
from PySide6.QtWidgets import QFrame, QWidget

from mm_companion.ui import theme

#: How long a reject outline stays up when no leave/drop event follows. Long
#: enough to register as feedback, short enough not to linger after the drag has
#: moved on to a widget that never tells this one it left.
REJECT_TIMEOUT_MS = 700

# Every live instance, so entering one target can stand the others down. Weak so a
# destroyed widget's feedback drops out on its own.
_INSTANCES: WeakSet[DropFeedback] = WeakSet()


def clear_all(*, except_for: DropFeedback | None = None) -> None:
    """Return every drop target to its idle look, optionally sparing one.

    Called whenever a target lights up, so exactly one target is ever dressed —
    the same "only one at a time" discipline the powers cards use for hover.

    An instance whose widget has been destroyed is dropped rather than raising:
    the registry is weak on the *feedback*, not on the Qt objects behind it, so a
    closed window can leave one alive with a deleted C++ timer behind it — and one
    dead entry must not stop the live targets from standing down.
    """
    for instance in list(_INSTANCES):
        if instance is except_for:
            continue
        try:
            instance.clear()
        except RuntimeError:
            _INSTANCES.discard(instance)


class DropFeedback:
    """Idle / accept / reject styling for one drop-target widget.

    *selector* is the QSS selector the rules are scoped to — an object name
    (``"#groupHeader"``) or a class that names a whole component
    (``"PowerCanvas"``). It must never be a bare ``QFrame``/``QLabel``: an
    unscoped rule is inherited by every child, which would repaint each separator
    and label inside the target.

    *border* draws an outline around a highlighted target as well as washing it.
    A target sitting *inside* another (a chip group within an effect card) sets it
    ``False`` and reads as a wash alone, so two nested outlines never stack up.
    """

    ACCEPT = "accept"
    REJECT = "reject"
    IDLE = "idle"

    def __init__(
        self,
        widget: QWidget,
        selector: str,
        *,
        radius: str = "radius.canvas",
        wash: float = 0.10,
        border: bool = True,
    ) -> None:
        self._widget = widget
        self._selector = selector
        self._radius_token = radius
        self._wash = wash
        self._border = border
        self._idle_rules = ""
        self._state = self.IDLE
        self._timer = QTimer(widget)
        self._timer.setSingleShot(True)
        self._timer.setInterval(REJECT_TIMEOUT_MS)
        self._timer.timeout.connect(self.clear)
        _INSTANCES.add(self)

    @property
    def state(self) -> str:
        """``"idle"``, ``"accept"`` or ``"reject"`` — what the target shows now."""
        return self._state

    def set_idle(self, rules: str, *, apply_now: bool = True) -> None:
        """Set the target's resting style (a widget may have more than one).

        The canvas, for instance, is dashed while empty and solid once it holds
        cards; it calls this on each change. ``apply_now=False`` records the new
        resting style without disturbing a highlight that is currently up.
        """
        self._idle_rules = rules
        if apply_now and self._state == self.IDLE:
            self._paint(rules)

    def show_accept(self) -> None:
        """Light the target up: a compatible payload is over it."""
        clear_all(except_for=self)
        self._timer.stop()
        self._state = self.ACCEPT
        self._paint(self._highlight("accent"))

    def show_reject(self) -> None:
        """Mark the target as refusing this payload."""
        clear_all(except_for=self)
        self._state = self.REJECT
        self._paint(self._highlight("drop.reject"))
        # Qt withholds further drag events from a widget that ignored the enter,
        # so nothing else is guaranteed to take this outline down.
        self._timer.start()

    def clear(self) -> None:
        """Return the target to its resting style."""
        self._timer.stop()
        if self._state == self.IDLE:
            return
        self._state = self.IDLE
        self._paint(self._idle_rules)

    def _highlight(self, token: str) -> str:
        wash = f"background: {theme.wash(token, self._wash)};"
        if not self._border:
            return wash
        width = int(theme.metric("border.width.emphasis"))
        return f"border: {width}px solid {theme.color(token)}; {wash}"

    def _paint(self, rules: str) -> None:
        radius = int(theme.metric(self._radius_token))
        self._widget.setStyleSheet(
            f"{self._selector} {{ {rules} border-radius: {radius}px; }}" if rules else ""
        )


class _SlidingOverlay(QFrame):
    """A mark drawn over a page, which slides between places instead of jumping.

    Shared by the two marks a drag puts up — the thin line that says *where* the
    block lands and the wash that says *how much room* it takes — because sliding
    and the rule for when not to slide are the same for both, and were written
    once for the line before there was a second one.

    Transparent to the mouse: the whole point of a mark is that it appears under
    the pointer mid-gesture, and a widget there that answers a hit test is a
    widget the gesture can trip over.
    """

    SLIDE_MS = 120

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._slide = QPropertyAnimation(self, b"geometry", self)
        self._slide.setDuration(self.SLIDE_MS)
        self._slide.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.hide()  # never visible before it is parented and placed

    def restyle(self) -> None:
        """Read this mark's colours out of the active theme. Subclasses say how."""

    def move_to(self, rect: QRect) -> None:
        """Show the mark at *rect*, sliding there when it makes sense.

        Restyled as it appears rather than in the constructor, so a preset
        switched between two drags is picked up — a token read once at
        construction is exactly the cached token the theme rules forbid — and not
        on every frame of a drag, which would re-polish the widget fifty times
        crossing the page for a colour that cannot have changed in between.
        """
        first = not self.isVisible()
        if first:
            self.restyle()
        self._slide.stop()
        current = self.geometry()
        # Orientation = which dimension is the thin one; only slide within the same.
        same_axis = (current.width() < current.height()) == (rect.width() < rect.height())
        if not first and same_axis and current != rect:
            self._slide.setStartValue(current)
            self._slide.setEndValue(rect)
            self._slide.start()
        else:
            self.setGeometry(rect)
        self.show()
        self.raise_()

    def hide_indicator(self) -> None:
        self._slide.stop()
        self.hide()


class DropIndicator(_SlidingOverlay):
    """A thin accent line showing where a dragged item will drop.

    The insertion-point counterpart to :class:`DropFeedback`: that one dresses the
    *target*, this one marks the *place* inside it. Shared by the block canvas, the
    pinned strip, the GM's NPC list and the modifier chips, so an insert line looks
    and moves the same everywhere.

    It slides between drop slots instead of teleporting, and snaps instead when it
    flips between a row-boundary bar (horizontal) and an in-row bar (vertical),
    where a slide would just morph oddly.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("dropIndicator")

    def restyle(self) -> None:
        self.setStyleSheet(
            f"#dropIndicator {{ background-color: {theme.color('drop.indicator')}; }}"
        )


class DropRegion(_SlidingOverlay):
    """A translucent wash over the room a dragged block would actually take.

    The line says where the seam falls; this says how much of the target's space
    goes to the newcomer, which is the half of the answer a 2px line cannot give.
    It matters most for the gesture it was added for: a block dropped on the left
    of another takes half of it, and until you can *see* that half, "beside" and
    "instead of" look the same from under a cursor.

    Outlined as well as filled, which :class:`DropFeedback` deliberately is not.
    That one restyles the target frame itself, where a border changes the box and
    relayouts the page mid-drag; this is an overlay of its own and can afford an
    edge — and needs one, because a wash of this weight over a block full of
    content has nothing else to give it a shape.
    """

    #: How heavily the region is filled. Light: it lies over live content that
    #: still has to be readable through it, or the mark hides what it describes.
    WASH = 0.18

    #: The fill is taken from ``accent`` and the outline from ``drop.indicator``,
    #: which is not an inconsistency. A wash needs channels to dilute and
    #: ``drop.indicator`` is allowed to be a live palette role (Classic makes it
    #: ``palette(highlight)``), which has none; ``accent`` is the literal every
    #: other wash in the app is taken from, including the one a block dropped
    #: *into* another gets. So the two marks a drag puts up stay one family.
    FILL_TOKEN = "accent"
    EDGE_TOKEN = "drop.indicator"

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("dropRegion")

    def restyle(self) -> None:
        radius = int(theme.metric("radius.card"))
        width = int(theme.metric("border.width.emphasis"))
        self.setStyleSheet(
            f"#dropRegion {{ background-color: {theme.wash(self.FILL_TOKEN, self.WASH)};"
            f" border: {width}px solid {theme.color(self.EDGE_TOKEN)};"
            f" border-radius: {radius}px; }}"
        )

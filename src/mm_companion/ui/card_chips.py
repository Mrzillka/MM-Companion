"""The chip-sized controls a card is mostly made of.

Three cards draw these now — a player's, an NPC's and a scene entry's — which is
one more than the two that made it reasonable for them to live on
:mod:`~mm_companion.ui.player_card`. They are here rather than there for a second
reason as well: a scene card is reachable from ``ui/sections/`` (the Scene block
imports it), and ``player_card`` imports *out* of ``ui/sections/`` for its
condition catalogue — so a scene card that reached back into ``player_card``
closed a loop that Python will not import. This module imports nothing from
either, which is what keeps the two directions apart.
"""

from __future__ import annotations

from PySide6.QtCore import QMimeData, QObject, QPoint, Qt, QTimer, Signal
from PySide6.QtGui import QCursor, QDrag
from PySide6.QtWidgets import (
    QAbstractScrollArea,
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QToolButton,
    QWidget,
)

from mm_companion.ui import theme
from mm_companion.ui.widgets import attach_context_removal

#: What a dragged card reference is called on the clipboard. One format for
#: every board, because the drop is the same question everywhere: put this
#: reference here. See :mod:`~mm_companion.ui.card_drop`.
SCENE_MIME = "application/x-mm-scene-ref"


def start_card_drag(widget, ref: str) -> None:
    """Begin a real :class:`QDrag` carrying *ref*, with the card as its ghost.

    Shared by the two roster cards and the scene card, because the gesture has to
    be the same object to cross between their boards: a pseudo-drag that tracks
    the pointer itself works inside one container and cannot leave it.

    The page **scrolls under the drag** (:class:`DragAutoScroll`). A
    ``QScrollArea`` does nothing of the sort on its own, so a Scene block scrolled
    out of view above the cast was a target no drag could reach.
    """
    drag = QDrag(widget)
    mime = QMimeData()
    mime.setData(SCENE_MIME, ref.encode("utf-8"))
    drag.setMimeData(mime)
    pixmap = widget.grab()
    drag.setPixmap(pixmap)
    drag.setHotSpot(QPoint(pixmap.width() // 2, 12))
    scroller = DragAutoScroll(widget)
    try:
        drag.exec(Qt.DropAction.MoveAction)
    finally:
        scroller.stop()


#: How close to a scroll area's top or bottom edge, in pixels, a dragged card has
#: to come before the area starts scrolling — and how often it steps while it does.
AUTOSCROLL_MARGIN = 48
AUTOSCROLL_INTERVAL_MS = 25
#: The fastest a step goes, in pixels, at the very edge (or past it).
AUTOSCROLL_MAX_STEP = 28


class DragAutoScroll(QObject):
    """Scroll whatever the pointer is near the edge of, for as long as a drag lasts.

    Polled on a timer rather than driven by drag events, for two reasons. The drag
    events go to whichever widget is *under* the pointer — a card, a flow, a block —
    and none of those is the scroll area that has to move; and a pointer held still
    at the edge sends no events at all, where a GM expects the page to keep going.
    ``QDrag.exec`` runs its own loop, but it still services timers.

    Which area scrolls is the **innermost one that can**: under the pointer first
    (a block's own scroll before the page around it), then — when the pointer has
    gone *past* an edge, over the menu bar or off the window — the scroll areas
    around the card being dragged, so pulling a card above the top of the page
    scrolls the page up rather than doing nothing.
    """

    def __init__(self, source: QWidget) -> None:
        super().__init__(source)
        self._source = source
        self._timer = QTimer(self)
        self._timer.setInterval(AUTOSCROLL_INTERVAL_MS)
        self._timer.timeout.connect(self.tick)
        self._timer.start()

    def stop(self) -> None:
        self._timer.stop()
        self.deleteLater()

    def tick(self) -> bool:
        """One step: scroll one area if the pointer is at its edge. Returns whether it did."""
        pos = QCursor.pos()
        under = QApplication.widgetAt(pos)
        if under is not None and self._scroll_chain(_scroll_areas(under), pos, inside=True):
            return True
        return self._scroll_chain(_scroll_areas(self._source), pos, inside=False)

    @staticmethod
    def _scroll_chain(areas: list[QAbstractScrollArea], pos: QPoint, *, inside: bool) -> bool:
        for area in areas:
            bar = area.verticalScrollBar()
            if bar is None or bar.maximum() <= bar.minimum():
                continue
            viewport = area.viewport()
            local = viewport.mapFromGlobal(pos)
            if not 0 <= local.x() < viewport.width():
                continue
            if not inside and 0 <= local.y() < viewport.height():
                continue
            step = _edge_step(local.y(), viewport.height())
            if step < 0 and bar.value() > bar.minimum():
                bar.setValue(bar.value() + step)
                return True
            if step > 0 and bar.value() < bar.maximum():
                bar.setValue(bar.value() + step)
                return True
        return False


def _scroll_areas(widget: QWidget | None) -> list[QAbstractScrollArea]:
    """Every scroll area *widget* sits in, innermost first."""
    areas: list[QAbstractScrollArea] = []
    while widget is not None:
        if isinstance(widget, QAbstractScrollArea):
            areas.append(widget)
        widget = widget.parentWidget()
    return areas


def _edge_step(y: int, height: int) -> int:
    """How far to scroll for a pointer at *y* in a viewport *height* tall: negative
    up, positive down, nought away from both edges. Faster the nearer the edge."""
    margin = min(AUTOSCROLL_MARGIN, max(1, height // 3))
    if y < margin:
        depth = min(margin, margin - y)
        return -max(2, AUTOSCROLL_MAX_STEP * depth // margin)
    if y > height - margin:
        depth = min(margin, y - (height - margin))
        return max(2, AUTOSCROLL_MAX_STEP * depth // margin)
    return 0


#: What a condition chip's recovery button wears — the die that rolls it off.
RECOVERY_GLYPH = "🎲"

#: What the Scene toggle wears. An eye, because what it controls is who can *see*
#: this creature — the players' side of the board, not the GM's.
SCENE_GLYPH = "👁"


class _SceneEye(QToolButton):
    """The 👁 that puts a creature on the shared board, and takes it back off.

    A toggle whose *state* is the readout: on means the table can see this
    creature, off means only the GM can.

    It says which by **filling in**, not by changing colour. The first version
    tinted the glyph with the accent and left it plain otherwise, which is how the
    initiative badge carries its affordance — and on a colour emoji that does
    nothing at all: the font supplies its own colours and ignores the one the
    stylesheet asks for, so the two states were pixel-identical on a card a GM was
    meant to read at a glance. A washed background and a border change the *shape*
    of the control, which no font can override, and it is the same trade the
    collapse caret makes by pointing two ways rather than being two colours.

    Being a ``QToolButton`` it swallows its own press, which is what stops a click
    here being read as the start of the card's drag.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setText(SCENE_GLYPH)
        self.setAutoRaise(True)
        self.setCheckable(True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        size = int(theme.metric("gm.damage-button"))
        self.setFixedSize(size, size)
        font = self.font()
        font.setPointSizeF(theme.font_size("size.terms"))
        self.setFont(font)
        self.set_in_scene(False)

    def set_in_scene(self, on: bool) -> None:
        """Show whether this creature is on the board, without raising ``toggled``."""
        blocked = self.blockSignals(True)
        self.setChecked(on)
        self.blockSignals(blocked)
        self._restyle(on)

    def _restyle(self, on: bool) -> None:
        if on:
            rules = (
                f"color: {theme.color('accent')};"
                f"background: {theme.wash('accent', 0.22)};"
                f"border: {int(theme.metric('border.width'))}px solid {theme.color('accent')};"
                f"border-radius: {int(theme.metric('radius.chip'))}px;"
            )
        else:
            rules = f"color: {theme.color('text.muted')}; background: transparent; border: none;"
        # Scoped to this widget's own stylesheet rather than the theme's QSS: it is
        # a per-instance state, and the two GM cards each hold several tool buttons
        # that must not pick it up.
        self.setStyleSheet(f"QToolButton {{ {rules} }}")
        self.setToolTip(
            "On the Scene — the players can see this. Click to take it off."
            if on
            else "Put this on the Scene, where the players can see it."
        )


#: The initiative badge before anything has been rolled. A dash rather than a
#: zero: not rolled yet is not an initiative of nought, and the two sort
#: differently.
NO_INITIATIVE = "—"


class InitiativeBadge(QLabel):
    """A creature's initiative, and the only thing that rolls or clears it.

    It lives on the **scene card** — the one board that sorts by initiative. It
    used to be on the NPC card as well, which meant a GM read the turn order off
    two places and the cast re-arranged itself under their hands every time a mook
    rolled. The number's owner has not moved (it is still the GM window's
    ``_NpcEntry``); only the one control that touches it has.

    A ``QLabel`` for the same reason
    :class:`~mm_companion.ui.card_summary.PortraitButton` is one: a ``QToolButton``
    wraps its text in some forty pixels of its own chrome, which on a card this
    narrow leaves the name a stub. It carries the affordance instead — a pointing
    hand, a tooltip, an accent — and **swallows its press**, so clicking it can
    never be read as the start of the card's drag.

    Left-click rolls, right-click clears. The pair belongs on the one widget: the
    number *is* the thing being set, and a GM who has mis-rolled otherwise has to
    drag the card out of the rolled zone to be rid of it.
    """

    clicked = Signal()
    #: Right-clicked — take this creature back out of the initiative order.
    cleared = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(NO_INITIATIVE, parent)
        font = self.font()
        font.setBold(True)
        font.setPointSizeF(theme.font_size("size.terms"))
        self.setFont(font)
        self.setStyleSheet(f"color: {theme.color('accent')};")
        # A readout until somebody says otherwise, which is what a player's own
        # screen leaves it as.
        self.set_live(False)
        self.set_initiative(None)

    def set_live(self, live: bool) -> None:
        """Whether this badge rolls, or is only a readout.

        Off for a **player's** entry and on every player's own screen: a player
        rolls their own initiative on their own sheet, and a GM's click here would
        be rolling somebody else's die. A dead badge shows the same number in the
        same place and simply says nothing about being clicked.
        """
        self._live = live
        self.setCursor(Qt.CursorShape.PointingHandCursor if live else Qt.CursorShape.ArrowCursor)

    def set_initiative(self, total: int | None) -> None:
        """Show a rolled number, or the dash that means nobody has rolled."""
        self.setText(NO_INITIATIVE if total is None else str(total))
        if not self._live:
            self.setToolTip("Waiting for their roll" if total is None else "Initiative")
        else:
            self.setToolTip("Roll initiative for this creature. Right-click to clear it.")

    def mousePressEvent(self, event) -> None:  # noqa: N802 (Qt override)
        event.accept()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 (Qt override)
        if (
            self._live
            and event.button() == Qt.MouseButton.LeftButton
            and self.rect().contains(event.position().toPoint())
        ):
            self.clicked.emit()
        event.accept()

    def contextMenuEvent(self, event) -> None:  # noqa: N802 (Qt override)
        # Consumed either way: propagating would reach the card's own menu, which
        # is not what someone aiming at the initiative number asked for.
        if self._live:
            self.cleared.emit()
        event.accept()


class _ConditionChip(QFrame):
    """One condition, as a compact chip a right-click takes off.

    The removal used to be a "×" on the chip. It is a right-click now, everywhere a
    condition chip appears: the button was a third of the width of a short caption
    like "Hit ×3", on the one part of a collapsed card that has to hold several of
    them. See :func:`~mm_companion.ui.widgets.attach_context_removal` for the trade
    that makes, and for why the tooltip has to say so.

    *compact* is the collapsed GM card's version: the same chip in small print, so a
    creature carrying five conditions still costs one line of a card that is mostly
    pinned numbers. Only the type size and the padding change — a chip that reads
    differently in the two states would be a second thing to recognise.
    """

    def __init__(
        self,
        text: str,
        *,
        tooltip: str = "",
        compact: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setStyleSheet(
            f"border: {int(theme.metric('border.width'))}px solid"
            f" {theme.color('tint.worse')};"
            f"background: {theme.wash('tint.worse', 0.12)};"
            f"border-radius: {int(theme.metric('radius.chip'))}px;"
        )
        if tooltip:
            self.setToolTip(tooltip)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(*((3, 0, 1, 0) if compact else (5, 1, 2, 1)))
        layout.setSpacing(1 if compact else 2)
        self._label = QLabel(text)
        self._label.setStyleSheet("border: none; background: transparent;")
        if compact:
            # On the QFont, never in the sheet above: a stylesheet ``font-size``
            # outranks a widget's font everywhere in this app.
            font = self._label.font()
            font.setPointSizeF(theme.font_size("size.terms"))
            self._label.setFont(font)
        layout.addWidget(self._label)
        self._compact = compact
        self._removable = False
        self._recover_button: QToolButton | None = None

    def text(self) -> str:
        """The chip's caption — what :meth:`PlayerCard.condition_names` reads."""
        return self._label.text()

    @property
    def removable(self) -> bool:
        """Whether a right-click here would take this condition off.

        False on an offline player's chips: their card is a snapshot of somebody
        else's sheet, and there is nobody to send the command to.
        """
        return self._removable

    def arm_removal(self, on_remove) -> None:
        """Let a right-click ask for this condition to come off."""
        attach_context_removal(self, on_remove, what=self.text())
        self._removable = True

    @property
    def recoverable(self) -> bool:
        """Whether this chip carries the 🎲 that rolls its condition off."""
        return self._recover_button is not None

    def arm_recovery(self, on_roll, tooltip: str = "") -> None:
        """Give the chip a 🎲 that rolls the condition's recovery check.

        A button inside the chip rather than a gesture on it, because a left-click on
        a chip already means something on a card (it starts the card's drag) and a
        recovery is the one thing on a chip worth a visible affordance: it is how a
        condition *ends*. Being a button it swallows its own press, so rolling never
        starts a drag. What a made check does is the owner's business — the chip only
        asks.
        """
        if self._recover_button is not None:
            return
        button = QToolButton(self)
        button.setText(RECOVERY_GLYPH)
        button.setAutoRaise(True)
        button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setStyleSheet("QToolButton { border: none; background: transparent; padding: 0; }")
        font = button.font()
        font.setPointSizeF(theme.font_size("size.terms"))
        button.setFont(font)
        button.setToolTip(tooltip or "Roll the recovery check — a success takes this off.")
        button.clicked.connect(lambda: on_roll())
        self.layout().addWidget(button)
        self._recover_button = button

    def roll_recovery(self) -> None:
        """Press the chip's 🎲, as a click would."""
        if self._recover_button is not None:
            self._recover_button.click()

    def contextMenuEvent(self, event) -> None:  # noqa: N802 (Qt override)
        """Swallow a right-click that removal did not claim.

        Reached only on a chip that cannot be removed — an offline player's, whose
        app there is nobody to send the command to. Doing nothing is the point:
        left to propagate, the click would reach the *card* and offer to remove the
        player, which is not what someone aiming at a chip meant to ask for.
        """
        event.accept()

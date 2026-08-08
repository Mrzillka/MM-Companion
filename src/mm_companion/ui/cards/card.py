"""The stat-block card: a draggable frame that is also its subject's on/off switch."""

from __future__ import annotations

from PySide6.QtCore import QEvent, QMimeData, QPoint, Qt, Signal
from PySide6.QtGui import QCursor, QDrag, QEnterEvent, QMouseEvent
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QGraphicsOpacityEffect,
    QSizePolicy,
    QWidget,
)

from mm_companion.ui import theme
from mm_companion.ui.cards.drag import NODE_MIME


def lerp(start: float, end: float, progress: float) -> float:
    """*start* at ``progress`` 0, *end* at 1 — one frame of a card's on/off transition."""
    return start + (end - start) * progress


def card_ancestors_of(widget: QWidget) -> list[DraggableCard]:
    """The cards enclosing *widget*, innermost first.

    Shared by the cards themselves and by the clickable lines of a card's dice
    footer: both have to stand an enclosing card down when the pointer arrives, so
    that exactly one thing is lit and it is the thing a click would reach.
    """
    cards: list[DraggableCard] = []
    node = widget.parentWidget()
    while node is not None:
        if isinstance(node, DraggableCard):
            cards.append(node)
        node = node.parentWidget()
    return cards


class DraggableCard(QFrame):
    """A stat-block card (leaf power or group) that can be picked up by its grip.

    It carries the id of the tree node it renders; when its grip fires, it launches a
    :class:`QDrag` carrying that id (with a snapshot of the card as the drag cursor)
    so the enclosing :class:`~mm_companion.ui.cards.node_list.NodeList` — or a group
    title bar — can drop it.

    The card body is also the subject's **on/off switch**: a card marked
    :meth:`set_clickable` emits :attr:`clicked` on a left click that isn't a drag, and
    *accepts* the press so the click stops there. A card left un-clickable ignores the
    press instead, letting it bubble up to an enclosing card — which is how clicking a
    member of a Linked group toggles the whole group. Clicks that land on a real child
    control (the grip, ✎, ✕, a group's mode buttons) never reach the card at all,
    because those widgets consume the press themselves.

    It owns both halves of *showing* that switch:

    - :meth:`set_off_progress` is the switched-off look as a continuous quantity
      (``0.0`` fully on, ``1.0`` fully off), interpolating opacity, type size and
      padding together, so the section can animate a flip rather than cut to it.
    - a clickable card advertises itself before it is touched — a standing accent
      edge down its left side, and an accent border while the pointer is over it
      (a leaf card also fills faintly; a group only outlines, since a fill would
      paint behind all of its members). Exactly one card is lit at a time: the
      innermost clickable one under the pointer, enclosing cards standing down.
      An inert card stays flat and never lights up.
    """

    clicked = Signal()

    def __init__(
        self,
        node_id: str,
        group: bool = False,
        parent: QWidget | None = None,
        *,
        mime: str = NODE_MIME,
    ) -> None:
        super().__init__(parent)
        self.node_id = node_id
        self._is_group = group
        self._mime = mime
        self.setObjectName("groupCard" if group else "powerCard")
        self.setFrameShape(QFrame.Shape.StyledPanel)
        # Never shrink below the height its content needs — a card always shows all of
        # its rows (the block, not the card, grows and the page scrolls).
        policy = self.sizePolicy()
        policy.setVerticalPolicy(QSizePolicy.Policy.Minimum)
        policy.setHeightForWidth(True)
        self.setSizePolicy(policy)
        self._clickable = False
        self._hovered = False
        self._press: QPoint | None = None
        self._off = 0.0
        # Base point sizes, captured on the first set_off_progress call (i.e. once the
        # card is fully built), so scaling is always relative to the built size and
        # never compounds across frames.
        self._base_points: list[tuple[QWidget, float]] | None = None
        self._restyle()

    # -- the switched-off look --------------------------------------------
    def set_off_progress(self, progress: float) -> None:
        """Show this card *progress* of the way to its switched-off state.

        ``0.0`` is the live look and ``1.0`` the receded one; anything between is a
        frame of the transition. All three cues move together — the card dims, its
        type steps down and its padding tightens — so the whole card reads as one
        object easing out rather than as three separate changes.
        """
        progress = max(0.0, min(1.0, float(progress)))
        self._off = progress
        self._apply_opacity(progress)
        self._apply_fonts(progress)
        self._apply_metrics(progress)

    def off_progress(self) -> float:
        return self._off

    def _apply_opacity(self, progress: float) -> None:
        if progress <= 0.0:
            # A live card carries no effect at all: a graphics effect forces the card's
            # whole subtree to paint through an offscreen buffer, which is worth paying
            # for only while it is actually dimmed (same rule as BlockCanvas._fade_in).
            if self.graphicsEffect() is not None:
                self.setGraphicsEffect(None)
            return
        effect = self.graphicsEffect()
        if not isinstance(effect, QGraphicsOpacityEffect):
            effect = QGraphicsOpacityEffect(self)
            self.setGraphicsEffect(effect)
        effect.setOpacity(lerp(1.0, theme.metric("opacity.inactive"), progress))

    def _apply_fonts(self, progress: float) -> None:
        if self._base_points is None:
            self._base_points = [(w, w.font().pointSizeF()) for w in self._own_widgets()]
        scale = lerp(1.0, theme.font_size("scale.inactive"), progress)
        for widget, base in self._base_points:
            font = widget.font()
            font.setPointSizeF(base * scale)
            widget.setFont(font)

    def _own_widgets(self) -> list[QWidget]:
        """This card and the chrome it owns — never a nested card's own widgets.

        A group card contains member cards, and each of those scales itself; walking
        into them here would have the two fight over the same labels' fonts.
        """
        mine = [self]
        for widget in self.findChildren(QWidget):
            node = widget.parentWidget()
            while node is not None and not isinstance(node, DraggableCard):
                node = node.parentWidget()
            if node is self:
                mine.append(widget)
        return mine

    def _apply_metrics(self, progress: float) -> None:
        layout = self.layout()
        if layout is None:
            return
        prefix = "group" if self._is_group else "card"
        live, off = theme.box(f"{prefix}.margins"), theme.box(f"{prefix}.margins.off")
        margins = (round(lerp(a, b, progress)) for a, b in zip(live, off, strict=True))
        layout.setContentsMargins(*margins)
        spacing = lerp(theme.metric("card.spacing"), theme.metric("card.spacing.off"), progress)
        layout.setSpacing(round(spacing))

    # -- clickability -----------------------------------------------------
    def set_clickable(self, clickable: bool) -> None:
        """Arm (or disarm) the whole card as a click target."""
        self._clickable = clickable
        self.setCursor(
            Qt.CursorShape.PointingHandCursor if clickable else Qt.CursorShape.ArrowCursor
        )
        self._restyle()

    def is_clickable(self) -> bool:
        return self._clickable

    def _restyle(self) -> None:
        """Rebuild the card's own frame style from clickability and hover.

        Every rule is scoped to the card's object name so it dresses the frame alone —
        an unscoped border here would be inherited by every child QFrame (the
        separators) and every label inside the card.
        """
        width = int(theme.metric("border.width"))
        accent = theme.color("accent")
        if self._is_group:
            border, radius = theme.color("border.group"), theme.metric("radius.group")
        else:
            # Spelled out rather than left to the native StyledPanel: once a stylesheet
            # dresses this frame at all (for the accent edge), it owns every border, so
            # the plain state has to name the one it replaces.
            border, radius = theme.color("border.card"), theme.metric("radius.card")
        rules = [f"border: {width}px solid {border};", f"border-radius: {int(radius)}px;"]
        if self._clickable:
            # A standing edge says "this one is a switch" without needing a hover, and
            # the border confirms the one the pointer is actually on.
            if self._hovered:
                rules.append(f"border: {width}px solid {accent};")
                # Only a leaf card fills. A stylesheet background paints behind every
                # child, so washing a *group* would flood its whole subtree — its
                # outline lights instead, and its members stay as they were.
                if not self._is_group:
                    rules.append(f"background: {theme.wash('accent', 0.10)};")
            edge = int(theme.metric("border.width.accent-edge"))
            rules.append(f"border-left: {edge}px solid {accent};")
        self.setStyleSheet(f"#{self.objectName()} {{ {' '.join(rules)} }}")

    def set_hovered(self, hovered: bool) -> None:
        """Light (or unlight) this card. A no-op on a card that isn't a switch."""
        hovered = hovered and self._clickable
        if hovered != self._hovered:
            self._hovered = hovered
            self._restyle()

    def _card_ancestors(self) -> list[DraggableCard]:
        """The enclosing cards, innermost first."""
        return card_ancestors_of(self)

    def enterEvent(self, event: QEnterEvent) -> None:
        """Light this card — and only this one.

        Qt hands Enter to the widget the pointer moved *into* but sends Leave only as
        far up as the common ancestor, so crossing from a group card onto one of its
        members never un-hovers the group. Standing the ancestors down here is what
        keeps exactly one card lit: the innermost clickable one, which is the one a
        click would actually reach.

        An *inert* card stands nobody down — its press bubbles up to the enclosing
        card, so that card is still the switch and has to stay lit. This is the case
        for a Linked group's members, which are driven by their group.
        """
        super().enterEvent(event)
        self.set_hovered(True)
        if not self._clickable:
            return
        for ancestor in self._card_ancestors():
            ancestor.set_hovered(False)

    def leaveEvent(self, event: QEvent) -> None:
        """Unlight this card, handing the highlight back to an enclosing one.

        The ancestor this card muted on the way in gets no Enter of its own when the
        pointer steps back out onto its body — it was never left — so the hand-back has
        to happen here. Whether the pointer is still inside it is read from the cursor
        rather than from ``underMouse()``, whose flag Qt may already have cleared by
        the time this child's Leave is delivered.

        Inert ancestors are stepped over rather than stopped at: ``set_hovered``
        no-ops on a card that isn't clickable, so handing the highlight to one would
        just drop it — and the clickable card further out, which is what a click there
        would actually reach, would stay dark until the pointer left the group entirely.
        """
        super().leaveEvent(event)
        self.set_hovered(False)
        hand_back_highlight(self)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if not self._clickable or event.button() != Qt.MouseButton.LeftButton:
            event.ignore()  # let an enclosing card handle it
            return
        self._press = event.position().toPoint()
        event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        press, self._press = self._press, None
        if press is None or event.button() != Qt.MouseButton.LeftButton:
            event.ignore()
            return
        event.accept()
        released = event.position().toPoint()
        # A press that wandered (a drag or a mis-click released elsewhere) isn't a click.
        if (released - press).manhattanLength() >= QApplication.startDragDistance():
            return
        if self.rect().contains(released):
            self.clicked.emit()

    def start_drag(self) -> None:
        drag = QDrag(self)
        mime = QMimeData()
        mime.setData(self._mime, self.node_id.encode("ascii"))
        drag.setMimeData(mime)
        pixmap = self.grab()
        drag.setPixmap(pixmap)
        drag.setHotSpot(QPoint(pixmap.width() // 2, 12))
        drag.exec(Qt.DropAction.MoveAction)


def hand_back_highlight(widget: QWidget) -> None:
    """Light the innermost clickable card still under the pointer, if any.

    The other half of the one-thing-lit rule: whatever *widget* stood down on its way
    in gets the highlight back as the pointer leaves. Read from :meth:`QCursor.pos`
    rather than ``underMouse()``, whose flag Qt may already have cleared by the time
    this widget's Leave is delivered.
    """
    cursor = QCursor.pos()
    for card in card_ancestors_of(widget):
        if not card.is_clickable():
            continue
        if card.rect().contains(card.mapFromGlobal(cursor)):
            card.set_hovered(True)
            return

"""A card's dice footer: one line per roll, and the line *is* the roll button."""

from __future__ import annotations

from collections.abc import Callable, Sequence

from PySide6.QtCore import QEvent, QPoint, Qt, Signal
from PySide6.QtGui import QEnterEvent, QMouseEvent
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QVBoxLayout,
    QWidget,
)

from mm_companion.core.rules import PinRef, RollSpec
from mm_companion.ui import theme
from mm_companion.ui.cards.card import card_ancestors_of, hand_back_highlight
from mm_companion.ui.sections.stat_table import PinMenuState
from mm_companion.ui.widgets import tinted_style


class RollLine(QFrame):
    """One line of a card's dice footer — the whole line is the roll button.

    The ``🎲`` used to be a real :class:`QPushButton`, and that was doing one job
    beyond looking like a die: a button consumes its own press, so the click never
    reached :meth:`~mm_companion.ui.cards.card.DraggableCard.mousePressEvent` and
    rolling a power could not be mistaken for switching it on. Widening the target to
    the whole line means this frame has to take that job over — hence the explicit
    ``accept()`` below, and why the line cannot simply be a :class:`QLabel`, which
    would let the press through and toggle the card underneath.

    A line the *target* rolls is inert: no border, no hover, no cursor, and its press
    is deliberately left to bubble, so the card under it keeps working as the power's
    on/off switch. The wielder never makes their own target's save; that roll reaches
    the person who does as the follow-up chip on the attack's history card.
    """

    clicked = Signal()

    def __init__(self, rollable: bool, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("rollLine")
        self._rollable = rollable
        self._hovered = False
        self._press: QPoint | None = None
        if rollable:
            self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._restyle()

    def is_rollable(self) -> bool:
        return self._rollable

    def _restyle(self) -> None:
        """Dress the line from its own object name — never a bare selector.

        The same scoping rule (and the same reason) as
        :meth:`~mm_companion.ui.cards.card.DraggableCard._restyle`: an unscoped border
        here would be inherited by the line's own label, and by every other frame in
        the card.
        """
        if not self._rollable:
            self.setStyleSheet(f"#{self.objectName()} {{ border: none; }}")
            return
        width = int(theme.metric("border.width"))
        # At rest, a washed-out version of the same hue the line's text already
        # carries: enough to read as a target without competing with the card's own
        # edge, and unmistakably the *dice* affordance rather than the switch.
        border = theme.color("accent.dice") if self._hovered else theme.wash("accent.dice", 0.45)
        rules = [
            f"border: {width}px solid {border};",
            f"border-radius: {int(theme.metric('radius.chip'))}px;",
        ]
        if self._hovered:
            rules.append(f"background: {theme.wash('accent.dice', 0.10)};")
        self.setStyleSheet(f"#{self.objectName()} {{ {' '.join(rules)} }}")

    def _set_hovered(self, hovered: bool) -> None:
        hovered = hovered and self._rollable
        if hovered != self._hovered:
            self._hovered = hovered
            self._restyle()

    def enterEvent(self, event: QEnterEvent) -> None:
        """Light this line, and stand the enclosing card down.

        Otherwise the card stays lit as the power's on/off switch while the pointer
        is over a line that does something else entirely — the same one-thing-lit
        rule :meth:`~mm_companion.ui.cards.card.DraggableCard.enterEvent` keeps
        between nested cards.
        """
        super().enterEvent(event)
        self._set_hovered(True)
        if not self._rollable:
            return
        for card in card_ancestors_of(self):
            card.set_hovered(False)

    def leaveEvent(self, event: QEvent) -> None:
        """Unlight, handing the highlight back to a clickable card still under the pointer."""
        super().leaveEvent(event)
        self._set_hovered(False)
        if not self._rollable:
            return
        hand_back_highlight(self)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if not self._rollable or event.button() != Qt.MouseButton.LeftButton:
            event.ignore()  # let the enclosing card switch the power instead
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
        if (released - press).manhattanLength() >= QApplication.startDragDistance():
            return
        if self.rect().contains(released):
            self.clicked.emit()


class RollsFooter(QWidget):
    """The card's dice footer, one line per roll.

    Built from a list of :class:`~mm_companion.core.rules.RollSpec` — whatever the
    rules layer says this card's subject rolls — so a power card and an equipment
    card get the same footer from the same specs.

    A line the *wielder* rolls is a click target end to end: a bordered strip that
    lights on hover, so what can be rolled is obvious without hunting for a small
    glyph. A resistance line is not, because the wielder never makes their own
    target's save. That roll reaches the person who does make it as the follow-up
    chip on the attack's history card. The line still reads (it is what the power
    does), indented to keep the column of text straight.

    *pin_ref* is how a line offers "Pin to GM card": it maps a line's index to the
    :class:`~mm_companion.core.rules.PinRef` naming that roll. A footer built without
    one — or on a sheet with no card behind it (:attr:`PinMenuState.enabled`) — has no
    context menu.
    """

    rollRequested = Signal(object)  # RollSpec
    pinRequested = Signal(object)  # PinRef
    unpinRequested = Signal(object)  # PinRef

    def __init__(
        self,
        specs: Sequence[RollSpec],
        parent: QWidget | None = None,
        *,
        pins: PinMenuState | None = None,
        pin_ref: Callable[[int], PinRef] | None = None,
    ) -> None:
        super().__init__(parent)
        self._pins = pins or PinMenuState()
        self._pin_ref = pin_ref
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        # Enough of a gap that two neighbouring lines' borders read as two targets
        # rather than one box with a rule through it.
        layout.setSpacing(int(theme.metric("space.xs")))
        for index, spec in enumerate(specs):
            layout.addWidget(self._roll_line(spec, index))

    def _roll_line(self, spec: RollSpec, index: int) -> QWidget:
        """One footer line: its ``🎲``, if the wielder rolls it, then its text.

        *index* is carried only so the line can be pinned to a GM card — a
        :class:`~mm_companion.core.rules.pins.PinRef` names a roll by which entry of
        the card's roll list it is. The **resistance** line is pinnable too, even
        though the wielder never rolls it: "what save does this force" is the number
        a GM most wants on a mook's card.
        """
        rollable = not spec.rolled_by_target
        row = RollLine(rollable)
        if self._pin_ref is not None:
            row.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            row.customContextMenuRequested.connect(
                lambda pos, r=row, i=index: self._show_pin_menu(r, pos, i)
            )
        line = QHBoxLayout(row)
        pad = int(theme.metric("space.xs"))
        line.setContentsMargins(pad, pad, pad, pad)
        line.setSpacing(int(theme.metric("space.sm")))
        glyph_width = int(theme.metric("column.roll-button"))

        if rollable:
            row.setToolTip(spec.hint or f"Roll {spec.label}")
            row.clicked.connect(lambda s=spec: self.rollRequested.emit(s))
            glyph = QLabel("🎲")
            glyph.setFixedWidth(glyph_width)
            glyph.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
            line.addWidget(glyph)
        else:
            # No die, but the same indent, so the lines read as one column.
            line.addSpacing(glyph_width)

        label = QLabel(spec.label)
        label.setWordWrap(True)
        label.setToolTip(spec.hint)
        label.setStyleSheet(tinted_style("accent.dice", bold=False))  # calm blue for dice info
        line.addWidget(label, stretch=1)
        return row

    def _show_pin_menu(self, row: QWidget, pos, index: int) -> None:
        if not self._pins.enabled or self._pin_ref is None:
            return
        ref = self._pin_ref(index)
        sink = self.unpinRequested if self._pins.is_pinned(ref) else self.pinRequested
        menu = QMenu(row)
        menu.addAction(self._pins.action_text(ref), lambda: sink.emit(ref))
        menu.exec(row.mapToGlobal(pos))

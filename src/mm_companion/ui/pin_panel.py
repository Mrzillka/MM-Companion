"""The strip of pinned numbers down the right of a GM card.

A card shows what a creature *is*; this shows the four or five numbers the GM
keeps asking it for. Which numbers is the GM's choice, stored as
:class:`~mm_companion.core.rules.pins.PinRef`\\ s and resolved fresh on every
redraw — see that module for why a pin is a reference and never a value.

Four gestures, and the reasons they don't collide:

- **click** loads the chip's roll into the GM's roller and **double-click**
  throws it — the same bargain the character sheet's stat rows strike, so the
  sliders and the DC box can be set before anything is rolled. A click is a
  *release with no drag started*, so it cannot be confused with the drag below.
- **drag** reorders. It starts only once the pointer has travelled
  :data:`DRAG_THRESHOLD`, which is what leaves a plain click alone. A ``QDrag``
  rather than the NPC card's hand-rolled press/move maths, because the strip is a
  real drop target and Qt's enter/move/drop events are what a drop indicator
  wants.
- **right-click** removes.

The chips stack in a plain :class:`QVBoxLayout` rather than the wrapping
:class:`~mm_companion.ui.flow_layout.FlowLayout` the condition chips use. The
strip is a fixed, narrow column, so a flow would put one chip per row anyway —
and *order* is a thing the GM sets here, which a strict list states and a wrap
only implies.
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QMimeData, QPoint, QRect, QSize, Qt, Signal
from PySide6.QtGui import QDrag
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QScrollArea,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from mm_companion.core.character import Character
from mm_companion.core.data_loader import GameData
from mm_companion.core.rules import PinnedValue, PinRef, resolve_pins
from mm_companion.ui import theme
from mm_companion.ui.drop_feedback import DropIndicator
from mm_companion.ui.widgets import ElidingLabel, muted_style

#: How wide the strip is. Narrow on purpose — it sits beside a 210px card and the
#: pair has to stay a card, not become a table.
PIN_PANEL_WIDTH = 150

#: How far the pointer must travel with the button down before a press counts as
#: a drag rather than a click. Matches the NPC card's own threshold.
DRAG_THRESHOLD = 8

#: The drag's payload: the index of the chip being moved.
PIN_MIME = "application/x-mm-gm-pin"

#: The insertion bar's thickness, in pixels.
INDICATOR_HEIGHT = 2

#: Shown in place of the chips when a card has none pinned.
EMPTY_HINT = "Nothing pinned"


class _ChipScroll(QScrollArea):
    """The chips' window: exactly as tall as it is told, and scrolling past that.

    The height is **set**, not asked for, and that is the whole of the class. A
    scroll area left to negotiate is a bad neighbour here: it reports a default
    size hint that has nothing to do with its content, and it is elastic in a panel
    that ends with a stretch — so the two split the room between them, and after a
    rebuild the strip collapsed to nothing while its chips sat inside it, laid out
    and invisible. What the strip wants is never in doubt (its chips' heights,
    clipped to the cap), so :meth:`PinPanel._apply_cap` works it out and says so.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.viewport().setAutoFillBackground(False)
        self.setStyleSheet("background: transparent;")

    def set_window_height(self, height: int) -> None:
        """Show exactly *height* pixels of the chips, scrolling for any more."""
        self.setFixedHeight(max(0, height))

    def sizeHint(self) -> QSize:  # noqa: N802 (Qt override)
        return QSize(super().sizeHint().width(), self.height())


class PinChip(QFrame):
    """One pinned parameter: its caption and its current reading.

    Three looks, all from tokens. A chip that rolls carries the accent and says so
    with a pointing hand; a chip that is only *read* (a defence DC — nobody throws
    a difficulty) is plain; a chip whose pin no longer resolves is muted and shows
    a dash. The last is deliberately still here to be removed rather than gone.
    """

    #: This chip's ``RollSpec`` — the GM clicked it once, and wants it in the
    #: roller's chip without the die moving.
    loadRequested = Signal(object)
    #: The same spec, double-clicked — throw it.
    rollRequested = Signal(object)
    #: This chip's index in the strip — the GM asked to take it off.
    removeRequested = Signal(int)

    def __init__(self, value: PinnedValue, index: int, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._value = value
        self._index = index
        self._press_pos: QPoint | None = None
        self._dragging = False
        # Set by a double-click so the release that ends it is not read as a
        # second, post-roll load.
        self._rolled = False

        self.setFrameShape(QFrame.Shape.NoFrame)
        self._restyle()
        if value.hint:
            self.setToolTip(value.hint)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(5, 1, 5, 1)
        layout.setSpacing(4)

        self._label = ElidingLabel(value.label)
        self._label.setStyleSheet("border: none; background: transparent;")
        layout.addWidget(self._label, stretch=1)

        self._reading = QLabel(value.value)
        reading_font = self._reading.font()
        reading_font.setBold(True)
        self._reading.setFont(reading_font)
        self._reading.setStyleSheet(
            "border: none; background: transparent;" + (muted_style() if value.missing else "")
        )
        layout.addWidget(self._reading)

        for widget in (self, self._label, self._reading):
            font = widget.font()
            font.setPointSizeF(theme.font_size("size.terms"))
            widget.setFont(font)

    @property
    def value(self) -> PinnedValue:
        """What this chip is showing."""
        return self._value

    def text(self) -> str:
        """The chip as one readable string — what the tests assert on."""
        return f"{self._value.label} {self._value.value}"

    @property
    def rollable(self) -> bool:
        return self._value.spec is not None

    def _restyle(self) -> None:
        width = int(theme.metric("border.width"))
        radius = int(theme.metric("radius.chip"))
        if self._value.missing:
            border, background = theme.color("border.empty"), "transparent"
        elif self.rollable:
            border, background = theme.color("accent"), theme.wash("accent", 0.10)
        else:
            border, background = theme.color("border.card"), "transparent"
        self.setStyleSheet(
            f"border: {width}px solid {border};"
            f"background: {background};"
            f"border-radius: {radius}px;"
        )
        self.setCursor(
            Qt.CursorShape.PointingHandCursor if self.rollable else Qt.CursorShape.ArrowCursor
        )

    # -- the four gestures -------------------------------------------------

    def mousePressEvent(self, event) -> None:  # noqa: N802 (Qt override)
        if event.button() == Qt.MouseButton.LeftButton:
            self._press_pos = event.position().toPoint()
            self._dragging = False
        event.accept()

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802 (Qt override)
        """Throw the chip's roll.

        Qt delivers press → release → **doubleClick** → release, so the first
        release has already loaded the spec and the trailing one would load it
        again *after* the roll. ``_rolled`` swallows that last one; the leading
        load is left alone, since it is the same spec either way.
        """
        if event.button() != Qt.MouseButton.LeftButton or self._value.spec is None:
            event.accept()
            return
        self._rolled = True
        self._press_pos = None
        self.rollRequested.emit(self._value.spec)
        event.accept()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 (Qt override)
        """Past the threshold, hand the gesture to Qt's drag machinery.

        ``QDrag.exec`` blocks until the drop, so everything after it has already
        happened; ``_dragging`` is set first so the release that ends the drag is
        not also read as a click.
        """
        if self._press_pos is None or self._dragging:
            return
        if (event.position().toPoint() - self._press_pos).manhattanLength() < DRAG_THRESHOLD:
            return
        self._dragging = True
        drag = QDrag(self)
        mime = QMimeData()
        mime.setData(PIN_MIME, str(self._index).encode("ascii"))
        drag.setMimeData(mime)
        drag.setPixmap(self.grab())
        drag.setHotSpot(self._press_pos)
        drag.exec(Qt.DropAction.MoveAction)
        self._press_pos = None

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 (Qt override)
        was_click = (
            event.button() == Qt.MouseButton.LeftButton
            and self._press_pos is not None
            and not self._dragging
            and not self._rolled
            and self.rect().contains(event.position().toPoint())
        )
        self._press_pos = None
        self._rolled = False
        if was_click and self._value.spec is not None:
            self.loadRequested.emit(self._value.spec)
        event.accept()

    def contextMenuEvent(self, event) -> None:  # noqa: N802 (Qt override)
        menu = QMenu(self)
        menu.addAction(
            f"Remove {self._value.label}", lambda: self.removeRequested.emit(self._index)
        )
        menu.exec(event.globalPos())


class PinPanel(QWidget):
    """The whole strip: a header with a "+", then one :class:`PinChip` per pin."""

    #: The strip's new order/content, as a list of :class:`PinRef`.
    pinsChanged = Signal(object)
    #: A chip's ``RollSpec`` — the GM wants it in the roller, undisturbed.
    loadRequested = Signal(object)
    #: The same, double-clicked — throw it.
    rollRequested = Signal(object)
    #: The GM asked to add a pin (the "+").
    pickRequested = Signal()

    def __init__(self, data: GameData, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedWidth(PIN_PANEL_WIDTH)
        self.setAcceptDrops(True)

        self._data = data
        self._character: Character | None = None
        self._pins: list[PinRef] = []
        self._chips: list[PinChip] = []

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(int(theme.metric("space.xs")))

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(2)
        title = QLabel("📌")
        title.setToolTip("Numbers pinned to this card")
        header.addWidget(title)
        header.addStretch()
        self._add_button = QToolButton()
        self._add_button.setText("+")
        self._add_button.setToolTip("Pin a parameter from this character")
        self._add_button.setAutoRaise(True)
        self._add_button.clicked.connect(self.pickRequested)
        header.addWidget(self._add_button)
        outer.addLayout(header)

        self._empty = QLabel(EMPTY_HINT)
        self._empty.setStyleSheet(muted_style(italic=True))
        self._empty.setWordWrap(True)
        outer.addWidget(self._empty)

        # The chips live in a scroll area always, capped only when a caller asks
        # (:meth:`set_max_visible`). One code path rather than two: an uncapped
        # scroll area reports its content's height and behaves exactly as the bare
        # column it replaces.
        self._max_visible: int | None = None
        self._chip_host = QWidget()
        self._chip_layout = QVBoxLayout(self._chip_host)
        self._chip_layout.setContentsMargins(0, 0, 0, 0)
        self._chip_layout.setSpacing(int(theme.metric("space.xs")))
        self._scroll = _ChipScroll(self)
        self._scroll.setWidget(self._chip_host)
        outer.addWidget(self._scroll)
        outer.addStretch()

        self._indicator = DropIndicator(self)

    # -- what the strip is showing ------------------------------------------

    @property
    def pins(self) -> list[PinRef]:
        """The strip's pins, in order."""
        return list(self._pins)

    def set_character(self, character: Character | None) -> None:
        """Point the strip at a (possibly newly arrived) character and redraw."""
        self._character = character
        self.refresh()

    def set_pins(self, pins: list[PinRef]) -> None:
        """Replace the strip's contents without announcing it — this is a *load*.

        Distinct from the edits below, which all emit :attr:`pinsChanged`: the
        owner is telling the panel what it already knows, and echoing that back
        would have it save what it just read.
        """
        self._pins = list(pins)
        self.refresh()

    def values(self) -> list[PinnedValue]:
        """The strip's pins as currently resolved — what the chips are showing."""
        return resolve_pins(self._character, self._data, self._pins)

    def chip_texts(self) -> list[str]:
        """Every chip as one readable string, in order."""
        return [chip.text() for chip in self._chips]

    def set_max_visible(self, count: int | None) -> None:
        """Show at most *count* chips at once, scrolling for the rest (``None``: all).

        What a collapsed card asks for: the strip is most of what is left of it, and
        a creature with eight pins must not make its card twice the height of its
        neighbour's. The cap is a *maximum*, never a minimum — a strip with two chips
        on a four-chip cap is still two chips tall.
        """
        self._max_visible = count
        self._apply_cap()

    def _apply_cap(self) -> None:
        """Tell the chip window how tall to be: its chips, capped at
        :attr:`_max_visible` of them.

        Measured from a real chip rather than a token, since a chip's height is its
        font and its padding and a preset moves both. Uncapped it is still *set*
        rather than left to the layout — see :class:`_ChipScroll` for why a strip
        that merely asks ends up with nothing.
        """
        if not self._chips:
            self._scroll.set_window_height(0)
            return
        spacing = self._chip_layout.spacing()
        chip_height = self._chips[0].sizeHint().height()
        shown = len(self._chips)
        if self._max_visible is not None:
            shown = min(self._max_visible, shown)
        self._scroll.set_window_height(shown * chip_height + max(0, shown - 1) * spacing)

    def refresh(self) -> None:
        """Re-resolve every pin and rebuild the chips.

        Wholesale rather than in place: a chip is cheap, and the alternative is
        reconciling a list whose entries can change label, value *and* whether they
        resolve at all.
        """
        while self._chip_layout.count():
            item = self._chip_layout.takeAt(0)
            widget = item.widget() if item is not None else None
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
        self._chips = []

        for index, value in enumerate(self.values()):
            chip = PinChip(value, index)
            chip.loadRequested.connect(self.loadRequested)
            chip.rollRequested.connect(self.rollRequested)
            chip.removeRequested.connect(self.remove_pin)
            self._chip_layout.addWidget(chip)
            self._chips.append(chip)
        # The host is stretched to its viewport, so without a tail spacer the chips
        # would share that height between them instead of keeping their own.
        self._chip_layout.addStretch()
        self._empty.setVisible(not self._chips)
        self._apply_cap()
        self.updateGeometry()

    # -- editing the strip ---------------------------------------------------

    def add_pin(self, ref: PinRef) -> bool:
        """Pin *ref*, unless the same one is already there. Returns whether it was."""
        if ref in self._pins:
            return False
        self._pins.append(ref)
        self.refresh()
        self.pinsChanged.emit(self.pins)
        return True

    def remove_pin(self, index: int) -> None:
        if not 0 <= index < len(self._pins):
            return
        del self._pins[index]
        self.refresh()
        self.pinsChanged.emit(self.pins)

    def remove_ref(self, ref: PinRef) -> bool:
        """Take *ref* off the strip wherever it sits. Returns whether it was there.

        By identity rather than position, because the two places that unpin from
        *outside* the strip — the picker and the open sheet's row menu — know which
        parameter they mean and have no idea where its chip ended up after the GM
        dragged things around.
        """
        if ref not in self._pins:
            return False
        self._pins.remove(ref)
        self.refresh()
        self.pinsChanged.emit(self.pins)
        return True

    def move_pin(self, from_index: int, to_index: int) -> None:
        """Move the pin at *from_index* to sit at *to_index*.

        *to_index* is measured against the list **as it stands**, before the move —
        which is what a drop position means — so it is corrected downward once the
        moved pin has been lifted out from in front of it.
        """
        if not 0 <= from_index < len(self._pins):
            return
        ref = self._pins.pop(from_index)
        if to_index > from_index:
            to_index -= 1
        self._pins.insert(max(0, min(to_index, len(self._pins))), ref)
        self.refresh()
        self.pinsChanged.emit(self.pins)

    # -- the drop target -----------------------------------------------------

    def dragEnterEvent(self, event) -> None:  # noqa: N802 (Qt override)
        if event.mimeData().hasFormat(PIN_MIME):
            event.acceptProposedAction()
            self._show_indicator(event.position().toPoint())

    def dragMoveEvent(self, event) -> None:  # noqa: N802 (Qt override)
        if event.mimeData().hasFormat(PIN_MIME):
            event.acceptProposedAction()
            self._show_indicator(event.position().toPoint())

    def dragLeaveEvent(self, event) -> None:  # noqa: N802 (Qt override)
        self._indicator.hide_indicator()
        event.accept()

    def dropEvent(self, event) -> None:  # noqa: N802 (Qt override)
        self._indicator.hide_indicator()
        raw = event.mimeData().data(PIN_MIME)
        try:
            source = int(bytes(raw).decode("ascii"))
        except ValueError:
            return
        event.acceptProposedAction()
        self.move_pin(source, self._drop_index(event.position().toPoint()))

    def _drop_index(self, pos: QPoint) -> int:
        """Where in the strip a drop at *pos* lands: before the chip it is above.

        A chip's own top half means "before me", its bottom half "after me", which
        is the vertical twin of the modifier chips' left/right rule. *pos* arrives in
        the panel's coordinates (the drop events land here) while a chip's geometry
        is its scrolled host's, so it is translated on the way in — otherwise a
        strip scrolled down drops everything at the wrong index.
        """
        local = self._chip_host.mapFrom(self, pos)
        for index, chip in enumerate(self._chips):
            geometry = chip.geometry()
            if local.y() < geometry.center().y():
                return index
        return len(self._chips)

    def _show_indicator(self, pos: QPoint) -> None:
        rect = self._indicator_rect(self._drop_index(pos))
        if rect.isEmpty():
            self._indicator.hide_indicator()
        else:
            self._indicator.move_to(rect)

    def _indicator_rect(self, index: int) -> QRect:
        """The insertion bar, in the gap before *index* (or below the last chip).

        Computed among the chips and handed back in the panel's coordinates, since
        the :class:`~mm_companion.ui.drop_feedback.DropIndicator` is the panel's
        child and would otherwise be drawn a scroll offset away from the gap it
        marks.
        """
        if not self._chips:
            return QRect()
        gap = self._chip_layout.spacing() // 2
        if index < len(self._chips):
            geometry = self._chips[index].geometry()
            y = geometry.top() - gap
        else:
            geometry = self._chips[-1].geometry()
            y = geometry.bottom() + gap
        top_left = self._chip_host.mapTo(self, QPoint(geometry.left(), y))
        return QRect(top_left.x(), top_left.y(), geometry.width(), INDICATOR_HEIGHT)


def install_pin_panel(
    card: QWidget,
    column: QVBoxLayout,
    data: GameData,
    *,
    on_pins_changed: Callable[[list[PinRef]], None] | None = None,
) -> PinPanel:
    """Re-hang *card*'s single column beside a new :class:`PinPanel`.

    Both cards are built as one vertical column; this turns that into
    ``[ column ][ strip ]`` without either card having to know how. The column is
    top-aligned so a short strip does not stretch it, and the strip likewise, so
    the pair reads as two columns of a card rather than one tall box.
    """
    outer = QHBoxLayout()
    outer.setContentsMargins(0, 0, 0, 0)
    outer.setSpacing(int(theme.metric("space.sm")))
    outer.addLayout(column, stretch=1)
    panel = PinPanel(data, card)
    outer.addWidget(panel, alignment=Qt.AlignmentFlag.AlignTop)
    card.setLayout(outer)
    if on_pins_changed is not None:
        panel.pinsChanged.connect(on_pins_changed)
    return panel

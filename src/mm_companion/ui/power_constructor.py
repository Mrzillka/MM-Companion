"""The Power Constructor: a brick-builder for assembling powers.

A standalone top-level window. The left side is a palette of draggable *bricks*
grouped into three tabs — **Effects**, **Extras**, **Flaws** — built by iterating
the loaded :class:`~mm_companion.core.data_loader.GameData`. The right side is the
power being built: a name, a description, a live power-point cost, and a canvas of
effect *cards*.

Interaction (all drag-and-drop):

- Drag an **Effect** brick onto the canvas → a new :class:`EffectCard` appears
  (one :class:`~mm_companion.core.powers.PowerEffectInstance`).
- Drag an **Extra** or **Flaw** brick onto a specific card → it attaches there as a
  chip (a modifier modifies one effect, per the M&M model).

The window owns a single :class:`~mm_companion.core.powers.Power` and mutates it;
costs always come from :mod:`mm_companion.core.rules`, never computed inline.
Arrays, linking, and writing the finished power back onto a character are deferred.
"""

from __future__ import annotations

from PySide6.QtCore import QMimeData, Qt, Signal
from PySide6.QtGui import QDrag
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QPushButton,
    QScrollArea,
    QSplitter,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from mm_companion.core.data_loader import GameData, Modifier, load_game_data
from mm_companion.core.powers import ModifierSelection, Power, PowerEffectInstance
from mm_companion.core.rules import effect_cost_formula, effect_total_cost, power_total_cost
from mm_companion.ui.flow_layout import FlowLayout
from mm_companion.ui.widgets import make_spin_box

# Custom drag payload formats: the record id travels as the mime data.
EFFECT_MIME = "application/x-mm-effect"
MODIFIER_MIME = "application/x-mm-modifier"


def _mime_id(mime: QMimeData, fmt: str) -> str:
    """Decode the record id carried by a drag in the given format."""
    return bytes(mime.data(fmt)).decode("utf-8")


class BrickWidget(QFrame):
    """A draggable palette brick: a name and its cost text, carrying a record id.

    On drag it starts a :class:`QDrag` whose mime data holds the record id in the
    given format (``EFFECT_MIME`` or ``MODIFIER_MIME``), so drop targets know both
    what kind of brick it is and which record it refers to.
    """

    def __init__(
        self, title: str, subtitle: str, mime: str, payload: str, *, flat: bool = False
    ) -> None:
        super().__init__()
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self._mime = mime
        self._payload = payload
        self._press_pos = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 5, 8, 5)
        layout.setSpacing(1)

        header = QHBoxLayout()
        header.setSpacing(4)
        name = QLabel(title)
        name.setStyleSheet("font-weight: bold;")
        header.addWidget(name)
        header.addStretch()
        if flat:
            # A flat modifier costs a one-time add/subtract rather than per rank.
            badge = QLabel("flat")
            badge.setStyleSheet(
                "background: #555; color: white; border-radius: 4px; padding: 0 4px;"
            )
            header.addWidget(badge)
        layout.addLayout(header)

        if subtitle:
            cost = QLabel(subtitle)
            cost.setEnabled(False)
            layout.addWidget(cost)

    def mousePressEvent(self, event) -> None:  # noqa: N802 (Qt override)
        if event.button() == Qt.MouseButton.LeftButton:
            self._press_pos = event.position().toPoint()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 (Qt override)
        if self._press_pos is None:
            return
        moved = (event.position().toPoint() - self._press_pos).manhattanLength()
        if moved < QApplication.startDragDistance():
            return
        drag = QDrag(self)
        mime = QMimeData()
        mime.setData(self._mime, self._payload.encode("utf-8"))
        drag.setMimeData(mime)
        drag.exec(Qt.DropAction.CopyAction)
        self._press_pos = None


class ModifierChip(QFrame):
    """An attached extra/flaw shown on an effect card, with a remove button.

    A ``ranked`` modifier (bought in its own ranks, e.g. Accurate) also carries a
    rank spin box; changing it writes back to the :class:`ModifierSelection` and
    emits :attr:`changed` so the card can recompute its cost.
    """

    removeRequested = Signal(object)
    changed = Signal()

    def __init__(self, modifier: Modifier, selection: ModifierSelection) -> None:
        super().__init__()
        self.selection = selection
        self.setFrameShape(QFrame.Shape.StyledPanel)
        tint = "#2e5e33" if modifier.category == "extra" else "#5e2e2e"
        self.setStyleSheet(f"ModifierChip {{ background: {tint}; border-radius: 6px; }}")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 2, 3, 2)
        layout.setSpacing(4)
        layout.addWidget(QLabel(modifier.name))
        if modifier.ranked:
            rank = make_spin_box(1, 30, value=selection.rank, max_width=48)
            rank.setPrefix("×")
            rank.valueChanged.connect(self._on_rank_changed)
            layout.addWidget(rank)
        remove = QPushButton("✕")
        remove.setFlat(True)
        remove.setFixedWidth(18)
        remove.setCursor(Qt.CursorShape.PointingHandCursor)
        remove.clicked.connect(lambda: self.removeRequested.emit(self))
        layout.addWidget(remove)

    def _on_rank_changed(self, value: int) -> None:
        self.selection.rank = value
        self.changed.emit()


class EffectCard(QFrame):
    """One effect within the power: rank, attached modifier chips, and its cost.

    Accepts **modifier** drops (extras/flaws attach here). Writes rank/modifier
    changes straight to the shared :class:`PowerEffectInstance` and emits
    :attr:`changed` so the window can recompute the total.
    """

    changed = Signal()
    removeRequested = Signal(object)

    def __init__(self, instance: PowerEffectInstance, game_data: GameData) -> None:
        super().__init__()
        self.instance = instance
        self._data = game_data
        self._chips: list[ModifierChip] = []
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setAcceptDrops(True)

        effect = self._effect()
        layout = QVBoxLayout(self)

        header = QHBoxLayout()
        name = QLabel(effect.name if effect else instance.effect_id)
        name.setStyleSheet("font-weight: bold;")
        header.addWidget(name)
        header.addStretch()
        header.addWidget(QLabel("Rank"))
        self._rank = make_spin_box(1, 30, value=instance.rank, max_width=60)
        self._rank.valueChanged.connect(self._on_rank_changed)
        header.addWidget(self._rank)
        remove = QPushButton("✕")
        remove.setFixedWidth(24)
        remove.setToolTip("Remove this effect")
        remove.clicked.connect(lambda: self.removeRequested.emit(self))
        header.addWidget(remove)
        layout.addLayout(header)

        self._chip_area = QWidget()
        self._chip_layout = FlowLayout(self._chip_area)
        layout.addWidget(self._chip_area)

        self._hint = QLabel("Drag extras or flaws here")
        self._hint.setEnabled(False)
        layout.addWidget(self._hint)

        self._cost = QLabel()
        self._cost.setAlignment(Qt.AlignmentFlag.AlignRight)
        layout.addWidget(self._cost)

        self._refresh_cost()

    # -- data lookups -----------------------------------------------------
    def _effect(self):
        return next((e for e in self._data.effects if e.id == self.instance.effect_id), None)

    def _modifier(self, modifier_id: str) -> Modifier | None:
        return next((m for m in self._data.modifiers if m.id == modifier_id), None)

    # -- drops ------------------------------------------------------------
    def dragEnterEvent(self, event) -> None:  # noqa: N802 (Qt override)
        if event.mimeData().hasFormat(MODIFIER_MIME):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event) -> None:  # noqa: N802 (Qt override)
        self.attach_modifier(_mime_id(event.mimeData(), MODIFIER_MIME))
        event.acceptProposedAction()

    # -- mutations (also the seam headless tests drive) -------------------
    def attach_modifier(self, modifier_id: str) -> None:
        """Attach an extra/flaw to this effect (routed by the modifier's category)."""
        modifier = self._modifier(modifier_id)
        if modifier is None:
            return
        selection = ModifierSelection(modifier_id=modifier_id)
        bucket = self.instance.flaws if modifier.category == "flaw" else self.instance.extras
        bucket.append(selection)

        chip = ModifierChip(modifier, selection)
        chip.removeRequested.connect(self._remove_chip)
        chip.changed.connect(self._on_chip_changed)
        self._chips.append(chip)
        self._chip_layout.addWidget(chip)
        self._hint.setVisible(False)
        self._refresh_cost()
        self.changed.emit()

    def _remove_chip(self, chip: ModifierChip) -> None:
        for bucket in (self.instance.extras, self.instance.flaws):
            if chip.selection in bucket:
                bucket.remove(chip.selection)
        self._chips.remove(chip)
        chip.setParent(None)
        chip.deleteLater()
        self._hint.setVisible(not self._chips)
        self._refresh_cost()
        self.changed.emit()

    def _on_rank_changed(self, value: int) -> None:
        self.instance.rank = value
        self._refresh_cost()
        self.changed.emit()

    def _on_chip_changed(self) -> None:
        self._refresh_cost()
        self.changed.emit()

    def _refresh_cost(self) -> None:
        formula = effect_cost_formula(self.instance, self._data)
        total = effect_total_cost(self.instance, self._data)
        self._cost.setText(f"{formula} = {total} PP" if formula else f"{total} PP")


class PowerCanvas(QFrame):
    """The drop area that holds the power's effect cards.

    Accepts **effect** drops (each makes a new card). Owns no state itself beyond
    the shared :class:`Power`; emits :attr:`changed` on every add/remove/edit.
    """

    changed = Signal()

    def __init__(self, power: Power, game_data: GameData) -> None:
        super().__init__()
        self._power = power
        self._data = game_data
        self._cards: list[EffectCard] = []
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setAcceptDrops(True)

        self._layout = QVBoxLayout(self)
        self._hint = QLabel("Drag an effect here to start building your power")
        self._hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._hint.setEnabled(False)
        self._hint.setMinimumHeight(80)
        self._layout.addWidget(self._hint)
        self._layout.addStretch()

    def dragEnterEvent(self, event) -> None:  # noqa: N802 (Qt override)
        if event.mimeData().hasFormat(EFFECT_MIME):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event) -> None:  # noqa: N802 (Qt override)
        self.add_effect(_mime_id(event.mimeData(), EFFECT_MIME))
        event.acceptProposedAction()

    def add_effect(self, effect_id: str) -> EffectCard:
        """Append a new effect to the power and render its card."""
        instance = PowerEffectInstance(effect_id=effect_id)
        self._power.effects.append(instance)
        card = EffectCard(instance, self._data)
        card.changed.connect(self.changed)
        card.removeRequested.connect(self._remove_card)
        self._cards.append(card)
        self._layout.insertWidget(self._layout.count() - 1, card)  # before the stretch
        self._hint.setVisible(False)
        self.changed.emit()
        return card

    def _remove_card(self, card: EffectCard) -> None:
        if card.instance in self._power.effects:
            self._power.effects.remove(card.instance)
        self._cards.remove(card)
        card.setParent(None)
        card.deleteLater()
        self._hint.setVisible(not self._cards)
        self.changed.emit()

    @property
    def cards(self) -> list[EffectCard]:
        return list(self._cards)


class PowerConstructorWindow(QMainWindow):
    """Standalone brick-builder window for assembling a single power."""

    closed = Signal()
    powerSaved = Signal(object)  # reserved for the deferred character write-back

    def __init__(self, data: GameData | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._data = data or load_game_data()
        self.power = Power()
        self.setWindowTitle("Power Constructor")
        self.resize(900, 600)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self._build_palette())
        splitter.addWidget(self._build_editor())
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([260, 640])
        self.setCentralWidget(splitter)

        self._refresh_cost()

    # -- left: the palette of bricks --------------------------------------
    def _build_palette(self) -> QWidget:
        from PySide6.QtWidgets import QTabWidget  # local: only used here

        tabs = QTabWidget()
        effects = [
            BrickWidget(e.name, e.base_cost, EFFECT_MIME, e.id)
            for e in sorted(self._data.effects, key=lambda e: e.name)
        ]
        extras = [
            BrickWidget(m.name, m.cost_formula, MODIFIER_MIME, m.id, flat=m.flat)
            for m in sorted(self._data.modifiers, key=lambda m: m.name)
            if m.category == "extra"
        ]
        flaws = [
            BrickWidget(m.name, m.cost_formula, MODIFIER_MIME, m.id, flat=m.flat)
            for m in sorted(self._data.modifiers, key=lambda m: m.name)
            if m.category == "flaw"
        ]
        tabs.addTab(self._brick_list(effects), "Effects")
        tabs.addTab(self._brick_list(extras), "Extras")
        tabs.addTab(self._brick_list(flaws), "Flaws")
        return tabs

    def _brick_list(self, bricks: list[BrickWidget]) -> QScrollArea:
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setSpacing(4)
        for brick in bricks:
            layout.addWidget(brick)
        layout.addStretch()

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(container)
        return scroll

    # -- right: the power being built -------------------------------------
    def _build_editor(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)

        self._name = QLineEdit()
        self._name.setPlaceholderText("Power name (e.g. Fire Blast)")
        self._name.textChanged.connect(self._on_name_changed)
        layout.addWidget(self._name)

        self._description = QTextEdit()
        self._description.setPlaceholderText("Description / flavor text")
        self._description.setMaximumHeight(80)
        self._description.textChanged.connect(self._on_description_changed)
        layout.addWidget(self._description)

        self._cost = QLabel()
        self._cost.setStyleSheet("font-weight: bold;")
        layout.addWidget(self._cost)

        self.canvas = PowerCanvas(self.power, self._data)
        self.canvas.changed.connect(self._refresh_cost)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.canvas)
        layout.addWidget(scroll, stretch=1)
        return panel

    def _on_name_changed(self, text: str) -> None:
        self.power.name = text

    def _on_description_changed(self) -> None:
        self.power.description = self._description.toPlainText()

    def _refresh_cost(self) -> None:
        self._cost.setText(f"Total cost: {power_total_cost(self.power, self._data)} PP")

    def closeEvent(self, event) -> None:  # noqa: N802 (Qt override)
        self.closed.emit()
        super().closeEvent(event)

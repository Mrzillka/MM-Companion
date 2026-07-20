from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from mm_companion.core.character import Character
from mm_companion.core.data_loader import GameData
from mm_companion.core.powers import (
    STRUCTURE_ARRAY,
    STRUCTURE_INDEPENDENT,
    STRUCTURE_LINKED,
    Power,
    PowerEffectInstance,
)
from mm_companion.core.rules import (
    array_alternate_cost,
    array_base_index,
)
from mm_companion.ui.power_constructor.common import _ACCENT, EFFECT_MIME, _mime_id
from mm_companion.ui.power_constructor.effect_card import EffectCard
from mm_companion.ui.theme import tint_rgba

# Canvas chrome — dashed while empty (a "drop here" affordance), solid once it holds
# cards, and an accent dashed border while an effect brick hovers.
_CANVAS_STYLE_EMPTY = "PowerCanvas { border: 2px dashed palette(mid); border-radius: 8px; }"
_CANVAS_STYLE_FILLED = "PowerCanvas { border: 1px solid palette(mid); border-radius: 8px; }"
_CANVAS_STYLE_DRAG = (
    f"PowerCanvas {{ border: 2px dashed {_ACCENT}; border-radius: 8px;"
    f" background: {tint_rgba(_ACCENT, 0.08)}; }}"
)


class PowerModeBar(QWidget):
    """A three-way switch for how a multi-effect power's effects combine.

    Shown by the canvas only once a power holds two or more effects. Emits
    :attr:`changed` with the chosen structure id (``independent`` / ``linked`` /
    ``array``); the canvas writes it to the :class:`Power` and recomputes.
    """

    changed = Signal(str)

    _MODES = (
        (STRUCTURE_INDEPENDENT, "Independent", "Effects act on their own; their costs add up."),
        (STRUCTURE_LINKED, "Linked", "Effects always activate together as one; costs add up."),
        (
            STRUCTURE_ARRAY,
            "Array",
            "One effect active at a time; the costliest is paid in full and each other "
            "is a flat-cost alternate.",
        ),
    )

    def __init__(self) -> None:
        super().__init__()
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        layout.addWidget(QLabel("Multiple effects:"))
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._buttons: dict[str, QPushButton] = {}
        for structure, label, tip in self._MODES:
            button = QPushButton(label)
            button.setCheckable(True)
            button.setToolTip(tip)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            self._group.addButton(button)
            self._buttons[structure] = button
            layout.addWidget(button)
        layout.addStretch()
        self._buttons[STRUCTURE_INDEPENDENT].setChecked(True)
        self._group.buttonClicked.connect(self._on_clicked)

    def _on_clicked(self, button: QPushButton) -> None:
        for structure, candidate in self._buttons.items():
            if candidate is button:
                self.changed.emit(structure)
                return

    def set_structure(self, structure: str) -> None:
        """Reflect ``structure`` in the buttons without re-emitting :attr:`changed`."""
        button = self._buttons.get(structure)
        if button is not None:
            button.setChecked(True)


class PowerCanvas(QFrame):
    """The drop area that holds the power's effect cards and the structure switch.

    Accepts **effect** drops (each makes a new card). Owns no state itself beyond
    the shared :class:`Power`; emits :attr:`changed` on every add/remove/edit. Once
    a second card lands it reveals the :class:`PowerModeBar`, writes the chosen
    structure to the power, and keeps every card's role badge in step (the array
    base tracks the costliest effect as ranks change).
    """

    changed = Signal()

    def __init__(
        self,
        power: Power,
        game_data: GameData,
        focus_options: list[tuple[str, str]] | None = None,
        character: Character | None = None,
    ) -> None:
        super().__init__()
        self._power = power
        self._data = game_data
        # The wielder, passed to each card so an ability-folding chip can bound its
        # "amount used" spin box.
        self._character = character
        # Combat focuses each effect card can offer as an attack-skill link.
        self._focus_options = focus_options or []
        self._cards: list[EffectCard] = []
        self.setObjectName("PowerCanvas")
        self.setAcceptDrops(True)

        self._layout = QVBoxLayout(self)
        # The structure switch sits above the cards; it reveals itself only once a
        # second effect makes the choice meaningful (§4).
        self._mode_bar = PowerModeBar()
        self._mode_bar.setVisible(False)
        self._mode_bar.changed.connect(self._on_structure_changed)
        self._layout.addWidget(self._mode_bar)
        self._hint = QLabel("＋\nDrag an effect here to start building your power")
        self._hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._hint.setEnabled(False)
        self._hint.setMinimumHeight(120)
        self._layout.addWidget(self._hint)
        self._layout.addStretch()
        self._update_canvas_style()

    def _update_canvas_style(self, drag_over: bool = False) -> None:
        """Pick the frame chrome for the current state: accent while a brick hovers,
        dashed while empty (a drop affordance), solid once it holds cards."""
        if drag_over:
            self.setStyleSheet(_CANVAS_STYLE_DRAG)
        elif self._cards:
            self.setStyleSheet(_CANVAS_STYLE_FILLED)
        else:
            self.setStyleSheet(_CANVAS_STYLE_EMPTY)

    def dragEnterEvent(self, event) -> None:  # noqa: N802 (Qt override)
        if event.mimeData().hasFormat(EFFECT_MIME):
            self._update_canvas_style(drag_over=True)
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragLeaveEvent(self, event) -> None:  # noqa: N802 (Qt override)
        self._update_canvas_style()
        super().dragLeaveEvent(event)

    def dropEvent(self, event) -> None:  # noqa: N802 (Qt override)
        self.add_effect(_mime_id(event.mimeData(), EFFECT_MIME))
        self._update_canvas_style()
        event.acceptProposedAction()

    def add_effect(self, effect_id: str) -> EffectCard:
        """Append a new effect to the power and render its card."""
        instance = PowerEffectInstance(effect_id=effect_id)
        self._power.effects.append(instance)
        card = self._build_card(instance)
        self._sync_structure_ui()
        self.changed.emit()
        return card

    def _build_card(self, instance: PowerEffectInstance) -> EffectCard:
        """Render a card for an effect instance already on the power."""
        card = EffectCard(instance, self._data, self._focus_options, self._character)
        card.changed.connect(self._on_card_changed)
        card.removeRequested.connect(self._remove_card)
        self._cards.append(card)
        self._layout.insertWidget(self._layout.count() - 1, card)  # before the stretch
        self._hint.setVisible(False)
        self._update_canvas_style()
        return card

    def load_power(self) -> None:
        """Seed cards for a power that already carries effects (edit mode).

        The effects are already on ``self._power``; this renders a card for each and
        brings the structure switch in line with the loaded structure without
        emitting :attr:`changed` (the window refreshes its cost/summary itself).
        """
        for instance in self._power.effects:
            self._build_card(instance)
        self._sync_structure_ui()
        self._mode_bar.set_structure(self._power.structure)

    def _remove_card(self, card: EffectCard) -> None:
        if card.instance in self._power.effects:
            self._power.effects.remove(card.instance)
        self._cards.remove(card)
        card.setParent(None)
        card.deleteLater()
        self._hint.setVisible(not self._cards)
        self._update_canvas_style()
        self._sync_structure_ui()
        self.changed.emit()

    def _on_card_changed(self) -> None:
        # A rank/modifier edit can change which effect is the array base, so refresh
        # the badges before forwarding the change on for a cost/summary recompute.
        self._refresh_roles()
        self.changed.emit()

    def _on_structure_changed(self, structure: str) -> None:
        self._power.structure = structure
        self._refresh_roles()
        self.changed.emit()

    def _sync_structure_ui(self) -> None:
        """Reveal the switch for a multi-effect power (collapsing back to Independent
        when a removal leaves fewer than two), then refresh the card badges."""
        multi = len(self._cards) >= 2
        self._mode_bar.setVisible(multi)
        if not multi and self._power.structure != STRUCTURE_INDEPENDENT:
            self._power.structure = STRUCTURE_INDEPENDENT
            self._mode_bar.set_structure(STRUCTURE_INDEPENDENT)
        self._refresh_roles()

    def _refresh_roles(self) -> None:
        """Badge each card with its part in the current structure (§4)."""
        multi = len(self._cards) >= 2
        if multi and self._power.structure == STRUCTURE_ARRAY:
            base = array_base_index(self._power, self._data, self._character)
            note = f"{array_alternate_cost(self._data)} PP"
            for index, card in enumerate(self._cards):
                if index == base:
                    card.set_role("base")
                else:
                    card.set_role("alternate", note)
        elif multi and self._power.structure == STRUCTURE_LINKED:
            for card in self._cards:
                card.set_role("linked")
        else:
            for card in self._cards:
                card.set_role("")

    @property
    def cards(self) -> list[EffectCard]:
        return list(self._cards)

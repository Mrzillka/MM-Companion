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
    array_dynamic_primary_cost,
    configuration_by_id,
    power_from_configuration,
)
from mm_companion.ui import theme
from mm_companion.ui.drop_feedback import DropFeedback
from mm_companion.ui.power_constructor.common import (
    CONFIGURATION_MIME,
    EFFECT_MIME,
    _mime_id,
)
from mm_companion.ui.power_constructor.effect_card import EffectCard


def _idle_canvas_rules(filled: bool) -> str:
    """The canvas's resting border: dashed while empty, solid once it holds cards.

    The dash is the "drop here" affordance — an empty canvas is asking for an
    effect, a filled one is just a container.
    """
    width = int(theme.metric("border.width" if filled else "border.width.emphasis"))
    style = "solid" if filled else "dashed"
    return f"border: {width}px {style} {theme.color('border.empty')};"


#: The mode bar's fourth answer. It is a *view*, not a fourth
#: :data:`~mm_companion.core.powers.STRUCTURE_ARRAY`-style structure: a Dynamic array is
#: an array every one of whose members carries the ``dynamic`` flag. Keeping it out of
#: ``STRUCTURES`` is what leaves every cost, runtime and validation reader — which ask
#: "is this an array?" in some fifty places — correct without being touched, and it is
#: the honest model besides, since the rules price Dynamic per member.
MODE_ARRAY_DYNAMIC = "array_dynamic"


class PowerModeBar(QWidget):
    """A four-way switch for how a multi-effect power's effects combine.

    Shown by the canvas only once a power holds two or more effects. Emits
    :attr:`changed` with the chosen id (``independent`` / ``linked`` / ``array`` /
    :data:`MODE_ARRAY_DYNAMIC`); the canvas writes it to the :class:`Power` and
    recomputes.

    **Dynamic** used to be a checkbox on each effect card beside this bar, which asked
    the player the same question twice — an array and a Dynamic array are two answers to
    "how do these effects combine", not one answer and a modifier on it.
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
        (
            MODE_ARRAY_DYNAMIC,
            "Dynamic array",
            "The effects share the array's points and run at the same time at reduced "
            "effectiveness, instead of switching each other off. Each alternate costs "
            "the dearer Dynamic price, and the split is made on the card's sliders.",
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

    def set_structure(self, structure: str, dynamic: bool = False) -> None:
        """Reflect a structure in the buttons without re-emitting :attr:`changed`.

        *dynamic* lights the fourth segment instead of *Array*. It is true when **any**
        member is Dynamic rather than all of them, so a build saved while Dynamic was a
        per-member checkbox — and which may well be a mixed array — reads as what it is
        rather than as a plain array that quietly costs more.
        """

        if structure == STRUCTURE_ARRAY and dynamic:
            structure = MODE_ARRAY_DYNAMIC
        button = self._buttons.get(structure)
        if button is not None:
            button.setChecked(True)


class PowerCanvas(QFrame):
    """The drop area that holds the power's effect cards and the structure switch.

    Accepts **effect** drops (each makes a new card) and **configuration** drops (a
    named standard power like Blast or Force Field, which lands as one or more
    already-built cards). Owns no state itself beyond the shared :class:`Power`; emits
    :attr:`changed` on every add/remove/edit. Once a second card lands it reveals the
    :class:`PowerModeBar`, writes the chosen structure to the power, and keeps every
    card's role badge in step (the array base tracks the costliest effect as ranks
    change).
    """

    changed = Signal()
    #: A standard configuration was dropped; carries its name so the window can title an
    #: untitled power after it. Separate from :attr:`changed` because naming is the
    #: window's business and the canvas holds no name field.
    configurationDropped = Signal(str)

    def __init__(
        self,
        power: Power,
        game_data: GameData,
        focus_options: list[tuple[str, str]] | None = None,
        character: Character | None = None,
        unit: str = "PP",
    ) -> None:
        super().__init__()
        self._power = power
        self._data = game_data
        # The currency every card on this canvas prices itself in; see EffectCard.
        self._unit = unit
        # The wielder, passed to each card so an ability-folding chip can bound its
        # "amount used" spin box.
        self._character = character
        # Combat focuses each effect card can offer as an attack-skill link.
        self._focus_options = focus_options or []
        self._cards: list[EffectCard] = []
        self.setObjectName("PowerCanvas")
        self.setAcceptDrops(True)
        self._drops = DropFeedback(self, "PowerCanvas", radius="radius.canvas", wash=0.08)

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
        self._hint.setMinimumHeight(int(theme.metric("canvas.hint.height")))
        self._layout.addWidget(self._hint)
        self._layout.addStretch()
        self._update_canvas_style()

    def _update_canvas_style(self) -> None:
        """Refresh the canvas's resting border for whether it now holds cards."""
        self._drops.set_idle(_idle_canvas_rules(bool(self._cards)))

    def dragEnterEvent(self, event) -> None:  # noqa: N802 (Qt override)
        if event.mimeData().hasFormat(EFFECT_MIME) or event.mimeData().hasFormat(
            CONFIGURATION_MIME
        ):
            self._drops.show_accept()
            event.acceptProposedAction()
        else:
            # Only effect and configuration bricks build a power; a modifier dragged onto
            # the bare canvas (rather than onto a card) now says so instead of nothing.
            self._drops.show_reject()
            event.ignore()

    def dragLeaveEvent(self, event) -> None:  # noqa: N802 (Qt override)
        self._drops.clear()
        super().dragLeaveEvent(event)

    def dropEvent(self, event) -> None:  # noqa: N802 (Qt override)
        self._drops.clear()
        if event.mimeData().hasFormat(CONFIGURATION_MIME):
            self.add_configuration(_mime_id(event.mimeData(), CONFIGURATION_MIME))
        else:
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

    def add_configuration(self, configuration_id: str) -> list[EffectCard]:
        """Drop a named standard configuration onto the canvas as ready-built cards.

        The configuration is turned into an ordinary :class:`Power` and its effects are
        *appended* to whatever is already here — a configuration is a starting point, not
        a replacement, and a player who has built half a power and then reaches for Blast
        means to add it.

        Its ``structure`` is taken only when the canvas was **empty**. A multi-effect
        configuration (Berserker Rage is Linked) has to arrive linked to make sense, but
        stamping that over a structure the player already chose would silently rewrite
        their build.

        Returns the new cards, and emits :attr:`configurationDropped` with the name so
        the window can title an untitled power. An unknown id adds nothing.
        """

        configuration = configuration_by_id(self._data, configuration_id)
        if configuration is None:
            return []
        was_empty = not self._power.effects
        built = power_from_configuration(configuration)
        cards = []
        for instance in built.effects:
            self._power.effects.append(instance)
            cards.append(self._build_card(instance))
        if was_empty and len(built.effects) > 1:
            self._power.structure = built.structure
            self._mode_bar.set_structure(built.structure, any(e.dynamic for e in built.effects))
        self._sync_structure_ui()
        self.configurationDropped.emit(configuration.name)
        self.changed.emit()
        return cards

    def _build_card(self, instance: PowerEffectInstance) -> EffectCard:
        """Render a card for an effect instance already on the power."""
        card = EffectCard(
            instance, self._data, self._focus_options, self._character, unit=self._unit
        )
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

    def _remove_card(self, card: EffectCard) -> None:
        # Match the instance by *identity*, not equality: two cards holding the same
        # effect at the same rank are equal dataclasses, so ``list.remove`` would drop
        # the first one and leave the surviving card bound to an orphaned instance.
        index = next(
            (i for i, effect in enumerate(self._power.effects) if effect is card.instance), None
        )
        if index is not None:
            del self._power.effects[index]
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
        """Write the chosen structure down, fanning *Dynamic array* out to the members.

        The fourth segment is a view over "array, every member Dynamic", so picking it
        sets the flag on every effect and picking plain *Array* clears it. That is the
        whole of the mapping: the cost math still prices Dynamic per member and has not
        moved.
        """

        dynamic = structure == MODE_ARRAY_DYNAMIC
        self._power.structure = STRUCTURE_ARRAY if dynamic else structure
        if self._power.structure == STRUCTURE_ARRAY:
            for effect in self._power.effects:
                effect.dynamic = dynamic
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
        else:
            self._mode_bar.set_structure(
                self._power.structure, any(e.dynamic for e in self._power.effects)
            )
        self._refresh_roles()

    def _refresh_roles(self) -> None:
        """Badge each card with its part in the current structure (§4)."""
        multi = len(self._cards) >= 2
        if multi and self._power.structure == STRUCTURE_ARRAY:
            base = array_base_index(self._power, self._data, self._character)
            for index, (card, effect) in enumerate(
                zip(self._cards, self._power.effects, strict=True)
            ):
                if index == base:
                    # The base pays its own cost in full; making it Dynamic is the one
                    # thing that adds to it, so that is the only note it carries.
                    primary = array_dynamic_primary_cost(self._data)
                    card.set_role("base", f"+{primary} PP Dynamic" if effect.dynamic else "")
                else:
                    cost = array_alternate_cost(self._data, dynamic=effect.dynamic)
                    card.set_role("alternate", f"{cost} PP")
        elif multi and self._power.structure == STRUCTURE_LINKED:
            for card in self._cards:
                card.set_role("linked")
        else:
            for card in self._cards:
                card.set_role("")

    @property
    def cards(self) -> list[EffectCard]:
        return list(self._cards)

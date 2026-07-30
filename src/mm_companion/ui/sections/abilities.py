"""The abilities block: a grid of ability spin boxes backed by the shared model.

Ability ranks live on the :class:`~mm_companion.core.character.Character`, so the
spin boxes are views over it. A trait a power raises (Enhanced Trait) shows its
enhanced total in green beside the base spin box — ``→ 5`` — without replacing the
bought value; the boost is computed in
:func:`~mm_companion.core.rules.power_trait_bonuses`.
:meth:`AbilitiesSection.refresh_enhancements` recomputes those labels, and the
sheet calls it whenever a power changes.

Double-clicking a row rolls that ability: the section emits
:attr:`AbilitiesSection.rollRequested` with the spec
:func:`~mm_companion.core.rules.ability_roll` builds, and the bus hands it to the
Dice block.
"""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QLabel, QSpinBox, QVBoxLayout, QWidget

from mm_companion.core.character import Character
from mm_companion.core.data_loader import GameData
from mm_companion.core.rules import (
    RollSpec,
    ability_points_spent,
    ability_roll,
    condition_scope_penalty,
    power_trait_bonuses,
)
from mm_companion.ui.lock import set_widget_locked
from mm_companion.ui.sections.stat_grid import apply_stat_effects, build_stat_group
from mm_companion.ui.sections.titled_section import TitledSection


class AbilitiesSection(TitledSection):
    """Spin boxes for every ability, backed by the shared :class:`Character`.

    Emits :attr:`abilityChanged` (key, value) whenever an ability spin box
    changes, so dependent sections (Skills, Resistances) can recompute. Emits the
    generic :attr:`changed` whenever the point build changes, so the sheet can
    recompute spent power points.
    """

    abilityChanged = Signal(str, int)
    changed = Signal()

    #: A row was double-clicked — roll this ability. Carries a
    #: :class:`~mm_companion.core.rules.RollSpec`, and is deliberately *not* a build
    #: signal: rolling changes nothing and must never mark the sheet dirty.
    rollRequested = Signal(object)

    def __init__(self, data: GameData, character: Character, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        self._data = data
        self._character = character
        self._locked = False
        self._abilities: dict[str, QSpinBox] = {}
        # The "→ total" labels that show a power-boosted trait's enhanced value.
        self._ability_enh: dict[str, QLabel] = {}

        layout = QVBoxLayout(self)
        grid = build_stat_group(
            data.abilities,
            self._abilities,
            self._ability_enh,
            character.abilities,
            self._on_ability_changed,
            data.costs.trait_range("ability"),
            roll_spec=self._roll_spec,
            roll_sink=self.rollRequested.emit,
            is_locked=lambda: self._locked,
        )
        layout.addWidget(grid)

        self.refresh_enhancements()
        self.refresh_cost()

    def _on_ability_changed(self, key: str, value: int) -> None:
        self._character.abilities[key] = value
        # The base moved, so this ability's own "→ total" moves with it; the
        # resistances that derive from this ability are refreshed by the sheet,
        # which listens to abilityChanged.
        self.refresh_enhancements()
        self.refresh_cost()
        self.abilityChanged.emit(key, value)
        self.changed.emit()

    def refresh_cost(self) -> None:
        """Re-title the block with its current PP subtotal (also driven by a homebrew
        cost-rate change, via ``cost-rates-changed``)."""
        self.set_priced_title("Abilities", ability_points_spent(self._character, self._data))

    def refresh_enhancements(self) -> None:
        """Recompute each ability's "→ total" from power boosts and condition penalties."""
        bonuses = power_trait_bonuses(self._character, self._data)
        cond_effects = {
            a.key: condition_scope_penalty(self._character, self._data, {a.key, a.name})
            for a in self._data.abilities
        }
        apply_stat_effects(self._abilities, self._ability_enh, bonuses["ability"], cond_effects)

    def _roll_spec(self, key: str) -> RollSpec:
        """This ability's roll, built fresh at click time so it is never stale."""
        return ability_roll(self._character, self._data, key)

    def set_locked(self, locked: bool) -> None:
        """Make the ability spin boxes read-only labels (locked) or editable."""
        self._locked = locked
        for spin in self._abilities.values():
            set_widget_locked(spin, locked)

    def ability_values(self) -> dict[str, int]:
        """Current value of every ability, keyed by ability key."""
        return {key: spin.value() for key, spin in self._abilities.items()}

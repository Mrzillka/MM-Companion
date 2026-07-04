"""The resistances block: spin boxes for the derived resistance defenses.

Resistances are *derived* from a base trait (Toughness and Fortitude from Stamina,
Will from Awareness, Dodge from the Defense combat trait): a resistance's spin box
holds the **total** value, which starts equal to that base. Only the difference
from the base costs power points — raising the spin box above the base spends
points, lowering it below refunds them (:func:`~mm_companion.core.rules.resistance_base`
gives the base, and the model stores just the bought delta). So when the base trait
changes, an unmodified resistance follows it (:meth:`ResistancesSection.refresh_bases`
re-seeds the spin boxes) while a bought difference is preserved. The sheet calls
:meth:`follow_ability_change` when an ability moves.

A resistance a power raises (Protection) shows its enhanced total in green beside
the base spin box; :meth:`refresh_enhancements` recomputes those labels and the
sheet calls it whenever a power changes.
"""

from __future__ import annotations

from PySide6.QtCore import QSignalBlocker, Signal
from PySide6.QtWidgets import QGroupBox, QLabel, QSpinBox, QVBoxLayout, QWidget

from mm_companion.core.character import Character
from mm_companion.core.data_loader import GameData
from mm_companion.core.rules import (
    power_trait_bonuses,
    resistance_base,
    resistance_points_spent,
)
from mm_companion.ui.lock import set_widget_locked
from mm_companion.ui.sections.stat_grid import apply_enhancements, build_stat_group
from mm_companion.ui.widgets import title_with_cost


class ResistancesSection(QGroupBox):
    """Spin boxes for the derived resistances, backed by the shared :class:`Character`.

    Emits :attr:`changed` whenever the point build changes, so the sheet can
    recompute spent power points.
    """

    changed = Signal()

    def __init__(self, data: GameData, character: Character, parent: QWidget | None = None) -> None:
        super().__init__("Resistances", parent)

        self._data = data
        self._character = character
        self._resistances: dict[str, QSpinBox] = {}
        self._resistance_enh: dict[str, QLabel] = {}

        layout = QVBoxLayout(self)
        grid = build_stat_group(
            data.resistances,
            self._resistances,
            self._resistance_enh,
            character.resistances,
            self._on_resistance_changed,
        )
        layout.addWidget(grid)

        # The spin boxes hold the *total* (base + bought), so display the base on
        # top of the stored delta now that the grid exists.
        self.refresh_bases()
        self.refresh_enhancements()
        self._refresh_cost()

    def _on_resistance_changed(self, key: str, value: int) -> None:
        # The spin box holds the total; only the difference from the derived base is
        # bought (and costs/refunds points), so store that delta on the model.
        base = resistance_base(self._character, self._data, key)
        self._character.resistances[key] = value - base
        # Dodge derives from the Defense trait, so changing one resistance can move
        # another; re-seed them all (guarded, so this doesn't re-enter).
        self.refresh_bases()
        self.refresh_enhancements()
        self._refresh_cost()
        self.changed.emit()

    def follow_ability_change(self) -> None:
        """Re-seed the bases and enhancement labels after an ability moved.

        A resistance derived from the changed ability follows it (its bought delta
        is kept), and its "→ total" moves with the new base.
        """
        self.refresh_bases()
        self.refresh_enhancements()

    def refresh_bases(self) -> None:
        """Show each resistance's total (derived base + bought delta) in its spin box.

        The model stores only the bought delta, so the displayed total is
        :func:`~mm_companion.core.rules.resistance_base` plus that delta. Signals are
        blocked while re-seeding so following the base doesn't count as a fresh edit.
        """
        for res in self._data.resistances:
            spin = self._resistances.get(res.key)
            if spin is None:
                continue
            base = resistance_base(self._character, self._data, res.key)
            bought = self._character.resistances.get(res.key, 0)
            blocker = QSignalBlocker(spin)
            spin.setValue(base + bought)
            del blocker

    def refresh_enhancements(self) -> None:
        """Recompute each resistance's power-enhanced total and show it beside the base."""
        bonuses = power_trait_bonuses(self._character, self._data)
        apply_enhancements(self._resistances, self._resistance_enh, bonuses["resistance"])

    def _refresh_cost(self) -> None:
        self.setTitle(
            title_with_cost("Resistances", resistance_points_spent(self._character, self._data))
        )

    def set_locked(self, locked: bool) -> None:
        """Make the resistance spin boxes read-only labels (locked) or editable."""
        for spin in self._resistances.values():
            set_widget_locked(spin, locked)

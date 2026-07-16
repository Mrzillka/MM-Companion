"""The Cost config dialog: homebrew the non-power PP-cost rates for one character.

A small modal form over the point-cost rates from ``costs.json`` — ability /
resistance / advantage per-rank costs, the two skill ranks-per-point rates, and the
points-per-Power-Level budget. Edits are stored on the character as
:attr:`Character.cost_overrides` (only rates changed from the ruleset default are
kept), and drive :func:`~mm_companion.core.rules.has_cost_overrides`, which the
System / Power Level block turns into a "homebrew PP cost" notice. Powers keep their
own Dev-mode override mechanism and are out of scope here.
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from mm_companion.core.character import Character
from mm_companion.core.data_loader import GameData
from mm_companion.core.rules import effective_pp_per_level, effective_trait_costs
from mm_companion.ui.widgets import make_spin_box

#: The editable rates, in display order: ``(override key, label, unit suffix)``. Every
#: key but ``pp_per_level`` is a field of ``TraitCosts``.
_RATES: tuple[tuple[str, str, str], ...] = (
    ("ability_per_rank", "Ability", "PP / rank"),
    ("combat_per_rank", "Combat stat", "PP / rank"),
    ("resistance_per_rank", "Resistance", "PP / rank"),
    ("advantage_per_rank", "Advantage", "PP / rank"),
    ("skill_ranks_per_pp", "Skill (normal)", "ranks / PP"),
    ("skill_specialized_ranks_per_pp", "Skill (specialized)", "ranks / PP"),
    ("pp_per_level", "Points per Power Level", "PP / level"),
)

_FOCUSED_NOTE = (
    "Focused skills (Expertise, Close/Ranged Combat) use the normal-skill rate — "
    "there is no separate focused rate."
)


class CostConfigDialog(QDialog):
    """A form over the non-power PP-cost rates; Save writes homebrew overrides."""

    def __init__(
        self, character: Character, game_data: GameData, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Cost config")
        self._character = character
        self._data = game_data
        self._spins: dict[str, QSpinBox] = {}

        layout = QVBoxLayout(self)

        intro = QLabel(
            "Homebrew the power-point cost of the non-power traits for this character. "
            "A rate left at its default is not overridden."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        form = QFormLayout()
        current = self._current_values()
        for key, label, unit in _RATES:
            default = self._default_value(key)
            spin = make_spin_box(1, 9999, value=int(current[key]))
            spin.setToolTip(f"Default: {default} {unit}")
            self._spins[key] = spin

            row = QWidget()
            box = QHBoxLayout(row)
            box.setContentsMargins(0, 0, 0, 0)
            box.addWidget(spin)
            unit_label = QLabel(unit)
            unit_label.setStyleSheet("color: palette(mid);")
            box.addWidget(unit_label)
            hint = QLabel(f"(default: {default})")
            hint.setStyleSheet("color: palette(mid);")
            box.addWidget(hint)
            box.addStretch()
            form.addRow(f"{label}:", row)
        layout.addLayout(form)

        note = QLabel(_FOCUSED_NOTE)
        note.setWordWrap(True)
        note.setStyleSheet("color: palette(mid);")
        layout.addWidget(note)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        reset = QPushButton("Reset to default")
        reset.setToolTip("Clear every homebrew rate and return to the ruleset defaults.")
        reset.clicked.connect(self._reset_to_default)
        buttons.addButton(reset, QDialogButtonBox.ButtonRole.ResetRole)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    # -- values ---------------------------------------------------------------

    def _current_values(self) -> dict[str, int]:
        """The character's current *effective* rate for each editable key."""
        traits = effective_trait_costs(self._character, self._data)
        values = {key: getattr(traits, key) for key, _, _ in _RATES if key != "pp_per_level"}
        values["pp_per_level"] = effective_pp_per_level(self._character, self._data)
        return values

    def _default_value(self, key: str) -> int:
        """The ruleset default for a rate, ignoring the character's overrides."""
        if key == "pp_per_level":
            return self._data.costs.power_level.pp_per_level
        return getattr(self._data.costs.traits, key)

    def _reset_to_default(self) -> None:
        for key, spin in self._spins.items():
            spin.setValue(self._default_value(key))

    def _save(self) -> None:
        """Store only the rates that differ from default; clear the rest."""
        for key, spin in self._spins.items():
            value = spin.value()
            if value == self._default_value(key):
                self._character.cost_overrides.pop(key, None)
            else:
                self._character.cost_overrides[key] = value
        self.accept()

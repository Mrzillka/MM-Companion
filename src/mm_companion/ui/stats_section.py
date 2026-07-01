"""Section 2: abilities, resistances, and advantages."""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QListWidget,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from mm_companion.core.data_loader import GameData

ABILITY_MIN, ABILITY_MAX = -5, 30
RESISTANCE_MIN, RESISTANCE_MAX = -5, 30
RANK_MIN, RANK_MAX = 1, 20


class StatsSection(QGroupBox):
    """Simple spin boxes for abilities and resistances, plus a combo-box driven
    picker for advantages.

    Emits :attr:`abilityChanged` (key, value) whenever an ability spin box
    changes, so dependent sections (e.g. Skills) can recompute their totals.
    """

    abilityChanged = Signal(str, int)

    def __init__(self, data: GameData, parent: QWidget | None = None) -> None:
        super().__init__("Base Character Stats", parent)

        self._abilities: dict[str, QSpinBox] = {}
        self._resistances: dict[str, QSpinBox] = {}

        layout = QHBoxLayout(self)
        layout.addWidget(self._build_abilities(data), stretch=1)
        layout.addWidget(self._build_resistances(data), stretch=1)
        layout.addWidget(self._build_advantages(data), stretch=1)

    def _build_abilities(self, data: GameData) -> QGroupBox:
        box = QGroupBox("Abilities")
        form = QFormLayout(box)
        for ability in data.abilities:
            spin = QSpinBox()
            spin.setRange(ABILITY_MIN, ABILITY_MAX)
            spin.valueChanged.connect(
                lambda value, key=ability.key: self.abilityChanged.emit(key, value)
            )
            self._abilities[ability.key] = spin
            form.addRow(f"{ability.name}:", spin)
        return box

    def _build_resistances(self, data: GameData) -> QGroupBox:
        box = QGroupBox("Resistances")
        form = QFormLayout(box)
        for resistance in data.resistances:
            spin = QSpinBox()
            spin.setRange(RESISTANCE_MIN, RESISTANCE_MAX)
            self._resistances[resistance.key] = spin
            form.addRow(f"{resistance.name}:", spin)
        return box

    def _build_advantages(self, data: GameData) -> QGroupBox:
        box = QGroupBox("Advantages")
        outer = QVBoxLayout(box)

        picker = QHBoxLayout()
        self._advantage_combo = QComboBox()
        for advantage in data.advantages:
            label = f"{advantage.name} ({advantage.type})"
            self._advantage_combo.addItem(label, advantage)
        picker.addWidget(self._advantage_combo, stretch=1)

        self._advantage_rank = QSpinBox()
        self._advantage_rank.setRange(RANK_MIN, RANK_MAX)
        picker.addWidget(self._advantage_rank)

        add_button = QPushButton("Add")
        add_button.clicked.connect(self._add_advantage)
        picker.addWidget(add_button)
        outer.addLayout(picker)

        self._advantage_list = QListWidget()
        outer.addWidget(self._advantage_list)

        remove_button = QPushButton("Remove Selected")
        remove_button.clicked.connect(self._remove_advantage)
        outer.addWidget(remove_button)

        self._advantage_combo.currentIndexChanged.connect(self._sync_rank_enabled)
        self._sync_rank_enabled()
        return box

    def _sync_rank_enabled(self) -> None:
        advantage = self._advantage_combo.currentData()
        self._advantage_rank.setEnabled(bool(advantage and advantage.ranked))
        if advantage and not advantage.ranked:
            self._advantage_rank.setValue(RANK_MIN)

    def _add_advantage(self) -> None:
        advantage = self._advantage_combo.currentData()
        if advantage is None:
            return
        text = advantage.name
        if advantage.ranked:
            text = f"{advantage.name} {self._advantage_rank.value()}"
        self._advantage_list.addItem(text)

    def _remove_advantage(self) -> None:
        for item in self._advantage_list.selectedItems():
            self._advantage_list.takeItem(self._advantage_list.row(item))

    def ability_values(self) -> dict[str, int]:
        """Current value of every ability, keyed by ability key."""

        return {key: spin.value() for key, spin in self._abilities.items()}

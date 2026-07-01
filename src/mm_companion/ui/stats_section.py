"""Section 2: abilities, resistances, and advantages."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from mm_companion.core.data_loader import GameData
from mm_companion.ui.lock import set_widget_locked
from mm_companion.ui.wheel_guard import guard_wheel

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
        layout.addWidget(self._build_abilities(data))
        layout.addWidget(self._build_resistances(data))
        layout.addWidget(self._build_advantages(data), stretch=1)

    @staticmethod
    def _make_stat_spin(minimum: int, maximum: int) -> QSpinBox:
        """A spin box sized for at most two digits plus a sign."""

        spin = QSpinBox()
        spin.setRange(minimum, maximum)
        spin.setButtonSymbols(QSpinBox.NoButtons)
        spin.setMaximumWidth(80)
        guard_wheel(spin)
        return spin

    @staticmethod
    def _make_separator() -> QFrame:
        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setFrameShadow(QFrame.Shadow.Sunken)
        return line

    def _add_stat_row(
        self, grid: QGridLayout, row: int, name: str, abbr: str, spin: QSpinBox
    ) -> None:
        """Lay out one stat as three aligned columns: name, short code, spin box."""

        grid.addWidget(QLabel(f"{name}:"), row, 0)
        code = QLabel(abbr)
        code.setAlignment(Qt.AlignmentFlag.AlignCenter)
        grid.addWidget(code, row, 1)
        grid.addWidget(spin, row, 2)

    def _build_abilities(self, data: GameData) -> QGroupBox:
        box = QGroupBox("Abilities")
        grid = QGridLayout(box)
        row = 0
        separated = False
        for ability in data.abilities:
            if ability.derived and not separated:
                grid.addWidget(self._make_separator(), row, 0, 1, 3)
                row += 1
                separated = True
            spin = self._make_stat_spin(ABILITY_MIN, ABILITY_MAX)
            spin.valueChanged.connect(
                lambda value, key=ability.key: self.abilityChanged.emit(key, value)
            )
            self._abilities[ability.key] = spin
            self._add_stat_row(grid, row, ability.name, ability.abbr, spin)
            row += 1
        return box

    def _build_resistances(self, data: GameData) -> QGroupBox:
        box = QGroupBox("Resistances")
        grid = QGridLayout(box)
        row = 0
        separated = False
        for resistance in data.resistances:
            if resistance.derived and not separated:
                grid.addWidget(self._make_separator(), row, 0, 1, 3)
                row += 1
                separated = True
            spin = self._make_stat_spin(RESISTANCE_MIN, RESISTANCE_MAX)
            self._resistances[resistance.key] = spin
            self._add_stat_row(grid, row, resistance.name, resistance.abbr, spin)
            row += 1
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

        self._advantage_add_button = QPushButton("Add")
        self._advantage_add_button.clicked.connect(self._add_advantage)
        picker.addWidget(self._advantage_add_button)

        self._advantage_remove_button = QPushButton("Remove")
        self._advantage_remove_button.clicked.connect(self._remove_advantage)
        picker.addWidget(self._advantage_remove_button)
        self._advantage_picker = picker
        outer.addLayout(picker)

        self._advantage_table = QTableWidget(0, 2)
        self._advantage_table.setHorizontalHeaderLabels(["Advantage", "Description"])
        self._advantage_table.verticalHeader().setVisible(False)
        self._advantage_table.setWordWrap(True)
        self._advantage_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._advantage_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        header = self._advantage_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        outer.addWidget(self._advantage_table)

        self._advantage_combo.currentIndexChanged.connect(self._sync_rank_enabled)
        self._sync_rank_enabled()
        guard_wheel(self._advantage_combo, self._advantage_rank, self._advantage_table)
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

        row = self._advantage_table.rowCount()
        self._advantage_table.insertRow(row)
        self._advantage_table.setItem(row, 0, QTableWidgetItem(text))
        self._advantage_table.setItem(row, 1, QTableWidgetItem(advantage.description))
        self._advantage_table.resizeRowToContents(row)

    def _remove_advantage(self) -> None:
        rows = {index.row() for index in self._advantage_table.selectedIndexes()}
        for row in sorted(rows, reverse=True):
            self._advantage_table.removeRow(row)

    def set_locked(self, locked: bool) -> None:
        """Make the ability/resistance spin boxes read-only labels and hide the
        advantage picker; the advantage table is already read-only."""
        for spin in self._abilities.values():
            set_widget_locked(spin, locked)
        for spin in self._resistances.values():
            set_widget_locked(spin, locked)
        for widget in (
            self._advantage_combo,
            self._advantage_rank,
            self._advantage_add_button,
            self._advantage_remove_button,
        ):
            widget.setVisible(not locked)

    def ability_values(self) -> dict[str, int]:
        """Current value of every ability, keyed by ability key."""

        return {key: spin.value() for key, spin in self._abilities.items()}

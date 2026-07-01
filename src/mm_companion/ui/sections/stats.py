"""Section 2: abilities, resistances, and advantages."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
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
from mm_companion.ui.widgets import hline_separator, make_spin_box

STAT_MIN, STAT_MAX = -5, 30
STAT_SPIN_WIDTH = 80

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
        layout.addWidget(
            self._build_stat_group("Abilities", data.abilities, self._abilities, emit_change=True)
        )
        layout.addWidget(self._build_stat_group("Resistances", data.resistances, self._resistances))
        layout.addWidget(self._build_advantages(data), stretch=1)

    def _add_stat_row(
        self, grid: QGridLayout, row: int, name: str, abbr: str, spin: QSpinBox
    ) -> None:
        """Lay out one stat as three aligned columns: name, short code, spin box."""

        grid.addWidget(QLabel(f"{name}:"), row, 0)
        code = QLabel(abbr)
        code.setAlignment(Qt.AlignmentFlag.AlignCenter)
        grid.addWidget(code, row, 1)
        grid.addWidget(spin, row, 2)

    def _build_stat_group(
        self,
        title: str,
        entries: list,
        store: dict[str, QSpinBox],
        *,
        emit_change: bool = False,
    ) -> QGroupBox:
        """Build a titled grid of stat spin boxes (abilities or resistances).

        A separator is inserted before the first derived entry. When
        *emit_change* is set, each spin box's changes are re-emitted via
        :attr:`abilityChanged` so dependent sections can recompute.
        """

        box = QGroupBox(title)
        grid = QGridLayout(box)
        row = 0
        separated = False
        for entry in entries:
            if entry.derived and not separated:
                grid.addWidget(hline_separator(), row, 0, 1, 3)
                row += 1
                separated = True
            spin = make_spin_box(STAT_MIN, STAT_MAX, buttons=False, max_width=STAT_SPIN_WIDTH)
            if emit_change:
                spin.valueChanged.connect(
                    lambda value, key=entry.key: self.abilityChanged.emit(key, value)
                )
            store[entry.key] = spin
            self._add_stat_row(grid, row, entry.name, entry.abbr, spin)
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

        self._advantage_rank = make_spin_box(RANK_MIN, RANK_MAX, guarded=False)
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

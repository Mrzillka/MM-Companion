"""The advantages block: a combo-box picker plus a table of chosen advantages.

The chosen advantages live on the shared :class:`~mm_companion.core.character.Character`;
the table rows stay 1:1 (and in order) with the model's advantage list, so it is a
view over the model rather than the source of truth.
"""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from mm_companion.core.character import AdvantageSelection, Character
from mm_companion.core.data_loader import GameData
from mm_companion.core.rules import advantage_points_spent
from mm_companion.ui.wheel_guard import guard_wheel
from mm_companion.ui.widgets import make_spin_box, title_with_cost

RANK_MIN, RANK_MAX = 1, 20


class AdvantagesSection(QGroupBox):
    """A picker and table of advantages backed by the shared :class:`Character`.

    Emits :attr:`changed` whenever the point build changes, so the sheet can
    recompute spent power points.
    """

    changed = Signal()

    def __init__(self, data: GameData, character: Character, parent: QWidget | None = None) -> None:
        super().__init__("Advantages", parent)

        self._data = data
        self._character = character
        self._advantages_by_name = {a.name: a for a in data.advantages}

        outer = QVBoxLayout(self)

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

        # Render any advantages a loaded character already carries.
        for selection in self._character.advantages:
            advantage = self._advantages_by_name.get(selection.name)
            description = advantage.description if advantage else ""
            ranked = bool(advantage and advantage.ranked)
            self._append_advantage_row(selection.name, selection.rank, description, ranked)

        self._refresh_cost()

    def _append_advantage_row(self, name: str, rank: int, description: str, ranked: bool) -> None:
        """Add one row to the advantage table (kept 1:1 with the model list)."""
        text = f"{name} {rank}" if ranked else name
        row = self._advantage_table.rowCount()
        self._advantage_table.insertRow(row)
        self._advantage_table.setItem(row, 0, QTableWidgetItem(text))
        self._advantage_table.setItem(row, 1, QTableWidgetItem(description))
        self._advantage_table.resizeRowToContents(row)

    def _sync_rank_enabled(self) -> None:
        advantage = self._advantage_combo.currentData()
        self._advantage_rank.setEnabled(bool(advantage and advantage.ranked))
        if advantage and not advantage.ranked:
            self._advantage_rank.setValue(RANK_MIN)

    def _add_advantage(self) -> None:
        advantage = self._advantage_combo.currentData()
        if advantage is None:
            return
        rank = self._advantage_rank.value() if advantage.ranked else 1
        # The table rows stay 1:1 (and in order) with the model's advantage list.
        self._character.advantages.append(AdvantageSelection(advantage.name, rank))
        self._append_advantage_row(advantage.name, rank, advantage.description, advantage.ranked)
        self._refresh_cost()
        self.changed.emit()

    def _remove_advantage(self) -> None:
        rows = {index.row() for index in self._advantage_table.selectedIndexes()}
        for row in sorted(rows, reverse=True):
            self._advantage_table.removeRow(row)
            if 0 <= row < len(self._character.advantages):
                del self._character.advantages[row]
        if rows:
            self._refresh_cost()
            self.changed.emit()

    def _refresh_cost(self) -> None:
        self.setTitle(
            title_with_cost("Advantages", advantage_points_spent(self._character, self._data))
        )

    def set_locked(self, locked: bool) -> None:
        """Hide the advantage picker while locked; the table is already read-only."""
        for widget in (
            self._advantage_combo,
            self._advantage_rank,
            self._advantage_add_button,
            self._advantage_remove_button,
        ):
            widget.setVisible(not locked)

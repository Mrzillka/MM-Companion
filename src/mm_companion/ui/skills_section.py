"""Section 3: the skills table.

Regular skills expose a ranks spin box; their total bonus is ranks plus the
linked ability. Focused skills (Close Combat, Expertise, Ranged Combat) have no
ranks of their own — the character instead adds focused instances, each of which
becomes its own rankable row.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QGroupBox,
    QHeaderView,
    QInputDialog,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from mm_companion.core.data_loader import GameData, Skill

RANK_MIN, RANK_MAX = 0, 20
COL_NAME, COL_RANKS, COL_TOTAL = 0, 1, 2


class SkillsSection(QGroupBox):
    """A table of skills whose total bonuses track the current ability values."""

    def __init__(self, data: GameData, parent: QWidget | None = None) -> None:
        super().__init__("Skills", parent)

        self._skills = data.skills
        self._ability_values: dict[str, int] = {a.key: 0 for a in data.abilities}
        # Ranks bought per row, keyed by a stable row id.
        self._ranks: dict[str, int] = {}
        # Added focuses per focused skill name, e.g. {"Close Combat": ["Swords"]}.
        self._focuses: dict[str, list[str]] = {s.name: [] for s in data.skills if s.focused}
        # (ability_key, row_id, total_item) for every rankable row, so totals can
        # be recomputed when abilities change.
        self._rows: list[tuple[str, str, QTableWidgetItem]] = []

        layout = QVBoxLayout(self)
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Skill", "Ranks", "Total Bonus"])
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(COL_NAME, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(COL_RANKS, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(COL_TOTAL, QHeaderView.ResizeMode.ResizeToContents)
        layout.addWidget(self.table)

        self._rebuild()

    # -- data-driven rebuild -------------------------------------------------

    def _rebuild(self) -> None:
        self.table.setRowCount(0)
        self._rows.clear()
        for skill in self._skills:
            if skill.focused:
                self._add_focused_group(skill)
            else:
                self._add_skill_row(skill, skill.name, skill.name)
        self._refresh_totals()

    def _add_focused_group(self, skill: Skill) -> None:
        """Header row with an 'Add focus' button, followed by any focus rows."""

        row = self.table.rowCount()
        self.table.insertRow(row)
        self.table.setItem(row, COL_NAME, self._readonly_item(skill.name))

        add_button = QPushButton("Add focus…")
        add_button.clicked.connect(lambda _=False, s=skill: self._add_focus(s))
        self.table.setCellWidget(row, COL_RANKS, add_button)
        self.table.setItem(row, COL_TOTAL, self._readonly_item(""))

        for focus in self._focuses[skill.name]:
            display = f"{skill.name}: {focus}"
            row_id = f"{skill.name}::{focus}"
            self._add_skill_row(skill, display, row_id, indent=True)

    def _add_skill_row(self, skill: Skill, display: str, row_id: str, indent: bool = False) -> None:
        row = self.table.rowCount()
        self.table.insertRow(row)
        self.table.setItem(row, COL_NAME, self._readonly_item(("    " if indent else "") + display))

        spin = QSpinBox()
        spin.setRange(RANK_MIN, RANK_MAX)
        spin.setValue(self._ranks.get(row_id, 0))
        spin.valueChanged.connect(lambda value, rid=row_id: self._on_rank_changed(rid, value))
        self.table.setCellWidget(row, COL_RANKS, spin)

        total_item = self._readonly_item("")
        self.table.setItem(row, COL_TOTAL, total_item)
        self._rows.append((skill.ability, row_id, total_item))

    @staticmethod
    def _readonly_item(text: str) -> QTableWidgetItem:
        item = QTableWidgetItem(text)
        item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        return item

    # -- interaction ---------------------------------------------------------

    def _add_focus(self, skill: Skill) -> None:
        focus, ok = QInputDialog.getText(self, f"Add {skill.name} focus", "Focus:")
        focus = focus.strip()
        if ok and focus and focus not in self._focuses[skill.name]:
            self._focuses[skill.name].append(focus)
            self._rebuild()

    def _on_rank_changed(self, row_id: str, value: int) -> None:
        self._ranks[row_id] = value
        self._refresh_totals()

    # -- totals --------------------------------------------------------------

    def set_ability_value(self, key: str, value: int) -> None:
        """Update a single ability and refresh dependent skill totals."""

        self._ability_values[key] = value
        self._refresh_totals()

    def set_ability_values(self, values: dict[str, int]) -> None:
        """Replace all ability values (used for the initial sync)."""

        self._ability_values.update(values)
        self._refresh_totals()

    def _refresh_totals(self) -> None:
        for ability_key, row_id, total_item in self._rows:
            total = self._ranks.get(row_id, 0) + self._ability_values.get(ability_key, 0)
            total_item.setText(str(total))

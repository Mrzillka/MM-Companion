"""The advantages block: a combo-box picker plus a table of chosen advantages.

The chosen advantages live on the shared :class:`~mm_companion.core.character.Character`;
the table rows stay 1:1 (and in order) with the model's advantage list, so it is a
view over the model rather than the source of truth.

Rank limits are enforced here from the rules layer: a ranked advantage's spin box is
capped at its own maximum (:func:`~mm_companion.core.rules.advantage_rank_cap` — the
fixed numbers and Improved Initiative's ``ceil(PL/2)``), and Heroic-type advantages
also draw from a shared per-character budget
(:func:`~mm_companion.core.rules.heroic_advantage_budget`) shown beside the picker.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from mm_companion.core.character import AdvantageSelection, Character
from mm_companion.core.data_loader import Advantage, GameData
from mm_companion.core.rules import (
    HEROIC_TYPE,
    advantage_points_spent,
    advantage_rank_cap,
    debilitated_traits,
    heroic_advantage_budget,
    heroic_advantage_ranks,
)
from mm_companion.ui.sections.stat_grid import CONDITION_TINT
from mm_companion.ui.sections.titled_section import TitledSection
from mm_companion.ui.wheel_guard import guard_wheel
from mm_companion.ui.widgets import make_spin_box, title_with_cost

RANK_MIN, RANK_MAX = 1, 20


class AdvantagesSection(TitledSection):
    """A picker and table of advantages backed by the shared :class:`Character`.

    Emits :attr:`changed` whenever the point build changes, so the sheet can
    recompute spent power points.
    """

    changed = Signal()

    def __init__(self, data: GameData, character: Character, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        self._data = data
        self._character = character
        self._advantages_by_name = {a.name: a for a in data.advantages}
        self._ability_names = {a.key: a.name for a in data.abilities}

        outer = QVBoxLayout(self)

        picker = QHBoxLayout()
        self._advantage_combo = QComboBox()
        for advantage in data.advantages:
            label = f"{advantage.name} ({', '.join(advantage.types)})"
            self._advantage_combo.addItem(label, advantage)
        picker.addWidget(self._advantage_combo, stretch=1)

        # A parameter combo shown only for an advantage that needs one (Alternate
        # Initiative's mental ability). Its choices are refreshed per advantage.
        self._advantage_param = QComboBox()
        self._advantage_param.setVisible(False)
        picker.addWidget(self._advantage_param)

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

        # The shared Heroic-advantage budget, refreshed on every change and PL edit.
        self._heroic_label = QLabel()
        outer.addWidget(self._heroic_label)

        self._advantage_table = QTableWidget(0, 3)
        self._advantage_table.setHorizontalHeaderLabels(["Advantage", "Type", "Description"])
        self._advantage_table.verticalHeader().setVisible(False)
        self._advantage_table.setWordWrap(True)
        self._advantage_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._advantage_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        header = self._advantage_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        outer.addWidget(self._advantage_table)

        self._advantage_combo.currentIndexChanged.connect(self._sync_rank_enabled)
        self._sync_rank_enabled()
        guard_wheel(
            self._advantage_combo,
            self._advantage_param,
            self._advantage_rank,
            self._advantage_table,
        )

        # Render any advantages a loaded character already carries.
        for selection in self._character.advantages:
            advantage = self._advantages_by_name.get(selection.name)
            self._append_advantage_row(
                selection.name, selection.rank, advantage, selection.parameter
            )

        self._refresh_cost()
        self.refresh_limits()
        self.refresh_conditions()

    def refresh_conditions(self) -> None:
        """Strike through (and redden) any advantage a Debilitated condition has lost.

        Display-only, mirroring the abilities/skills overlay: the row's own point cost is
        untouched — the advantage is just marked non-functional while debilitated. The
        sheet calls this whenever the applied conditions change.
        """

        lost = debilitated_traits(self._character, self._data)
        for row, selection in enumerate(self._character.advantages):
            item = self._advantage_table.item(row, 0)
            if item is None:
                continue
            struck = selection.name in lost
            font = item.font()
            font.setStrikeOut(struck)
            item.setFont(font)
            if struck:
                item.setForeground(QBrush(QColor(CONDITION_TINT)))
                item.setToolTip(f"Debilitated — {selection.name} is effectively lost")
            else:
                item.setData(Qt.ItemDataRole.ForegroundRole, None)
                item.setToolTip("")

    def _append_advantage_row(
        self, name: str, rank: int, advantage: Advantage | None, parameter: str = ""
    ) -> None:
        """Add one row to the advantage table (kept 1:1 with the model list)."""
        ranked = bool(advantage and advantage.ranked)
        text = f"{name} {rank}" if ranked else name
        if parameter:
            text = f"{text} ({self._ability_names.get(parameter, parameter)})"
        types = ", ".join(advantage.types) if advantage else ""
        description = advantage.description if advantage else ""
        row = self._advantage_table.rowCount()
        self._advantage_table.insertRow(row)
        self._advantage_table.setItem(row, 0, QTableWidgetItem(text))
        self._advantage_table.setItem(row, 1, QTableWidgetItem(types))
        self._advantage_table.setItem(row, 2, QTableWidgetItem(description))
        self._advantage_table.resizeRowToContents(row)

    def _rank_ceiling(self, advantage: Advantage) -> int:
        """The highest rank the picker may offer for *advantage* right now.

        Its own cap (:func:`advantage_rank_cap`, falling back to ``RANK_MAX`` when
        uncapped), further limited for a Heroic advantage by the ranks still free in
        the shared budget — but never below ``RANK_MIN`` so the control stays usable
        (an over-budget add is refused in :meth:`_add_advantage`).
        """

        cap = advantage_rank_cap(advantage, self._character.power_level)
        ceiling = RANK_MAX if cap is None else cap
        if HEROIC_TYPE in advantage.types:
            remaining = heroic_advantage_budget(
                self._character.power_level
            ) - heroic_advantage_ranks(self._character, self._data)
            ceiling = min(ceiling, max(RANK_MIN, remaining))
        return ceiling

    def _sync_rank_enabled(self) -> None:
        advantage = self._advantage_combo.currentData()
        ranked = bool(advantage and advantage.ranked)
        self._advantage_rank.setEnabled(ranked)
        self._sync_parameter(advantage)
        if advantage is None:
            return
        if ranked:
            self._advantage_rank.setMaximum(self._rank_ceiling(advantage))
        else:
            self._advantage_rank.setMaximum(RANK_MAX)
            self._advantage_rank.setValue(RANK_MIN)

    def _sync_parameter(self, advantage: Advantage | None) -> None:
        """Show and populate the parameter combo for an advantage that needs one.

        Currently only Alternate Initiative, whose ``initiative_ability_choice`` names
        the mental abilities it can switch initiative to. Other advantages hide it.
        """
        choices = tuple(advantage.initiative_ability_choice) if advantage else ()
        self._advantage_param.clear()
        for key in choices:
            self._advantage_param.addItem(self._ability_names.get(key, key), key)
        self._advantage_param.setVisible(bool(choices))

    def _add_advantage(self) -> None:
        advantage = self._advantage_combo.currentData()
        if advantage is None:
            return
        rank = self._advantage_rank.value() if advantage.ranked else 1
        parameter = (
            self._advantage_param.currentData()
            if advantage.initiative_ability_choice and self._advantage_param.currentData()
            else ""
        )
        # Enforce the shared Heroic-advantage budget as a hard limit on the add.
        if HEROIC_TYPE in advantage.types:
            budget = heroic_advantage_budget(self._character.power_level)
            prospective = heroic_advantage_ranks(self._character, self._data) + rank
            if prospective > budget:
                self._show_heroic_budget(prospective - rank, budget, blocked=True)
                return
        # The table rows stay 1:1 (and in order) with the model's advantage list.
        self._character.advantages.append(AdvantageSelection(advantage.name, rank, parameter))
        self._append_advantage_row(advantage.name, rank, advantage, parameter)
        self._refresh_cost()
        self.refresh_limits()
        self.refresh_conditions()
        self._sync_rank_enabled()
        self.changed.emit()

    def _remove_advantage(self) -> None:
        rows = {index.row() for index in self._advantage_table.selectedIndexes()}
        for row in sorted(rows, reverse=True):
            self._advantage_table.removeRow(row)
            if 0 <= row < len(self._character.advantages):
                del self._character.advantages[row]
        if rows:
            self._refresh_cost()
            self.refresh_limits()
            self.refresh_conditions()
            self._sync_rank_enabled()
            self.changed.emit()

    def _refresh_cost(self) -> None:
        self.set_block_title(
            title_with_cost("Advantages", advantage_points_spent(self._character, self._data))
        )

    def refresh_limits(self) -> None:
        """Recompute the Heroic-advantage budget display and the rank ceiling.

        Called after any advantage change and when the Power Level changes (the
        budget is ``floor(PL/2)``), so the label and the picker's rank cap stay in
        step with the current build.
        """

        used = heroic_advantage_ranks(self._character, self._data)
        budget = heroic_advantage_budget(self._character.power_level)
        self._show_heroic_budget(used, budget, blocked=used > budget)
        self._sync_rank_enabled()

    def _show_heroic_budget(self, used: int, budget: int, *, blocked: bool) -> None:
        """Render the Heroic-advantage budget label, tinting it red when at/over cap."""

        suffix = "  — budget reached" if blocked else ""
        self._heroic_label.setText(f"Heroic advantages: {used} / {budget}{suffix}")
        self._heroic_label.setStyleSheet("color: #c0392b;" if blocked else "")

    def set_locked(self, locked: bool) -> None:
        """Hide the advantage picker while locked; the table is already read-only."""
        for widget in (
            self._advantage_combo,
            self._advantage_rank,
            self._advantage_add_button,
            self._advantage_remove_button,
        ):
            widget.setVisible(not locked)
        # Keep the parameter combo hidden unless the current advantage needs it.
        self._advantage_param.setVisible(not locked and self._advantage_param.count() > 0)

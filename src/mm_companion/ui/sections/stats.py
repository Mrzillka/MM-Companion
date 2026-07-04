"""Section 2: abilities, resistances, and advantages.

Reads and writes the shared :class:`~mm_companion.core.character.Character`:
ability/resistance ranks and the chosen advantages all live on the model, so the
spin boxes and the advantage table are views over it rather than the source of
truth.

A trait a power raises (Enhanced Trait, Protection) shows its enhanced total in
green beside the base spin box — ``→ 5`` — without replacing the bought value; the
boost itself is computed in :func:`~mm_companion.core.rules.power_trait_bonuses`.
:meth:`StatsSection.refresh_enhancements` recomputes these labels, and the sheet
calls it whenever a power changes.

Resistances are *derived* from a base trait (Toughness and Fortitude from Stamina,
Will from Awareness, Dodge from the Defense combat trait): a resistance's spin box
holds the **total** value, which starts equal to that base. Only the difference
from the base costs power points — raising the spin box above the base spends
points, lowering it below refunds them (:func:`~mm_companion.core.rules.resistance_base`
gives the base, and the model stores just the bought delta). So when the base trait
changes, an unmodified resistance follows it (:meth:`StatsSection._refresh_resistance_bases`
re-seeds the spin boxes) while a bought difference is preserved.
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QSignalBlocker, Qt, Signal
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

from mm_companion.core.character import AdvantageSelection, Character
from mm_companion.core.data_loader import GameData
from mm_companion.core.rules import (
    ability_points_spent,
    advantage_points_spent,
    power_trait_bonuses,
    resistance_base,
    resistance_points_spent,
)
from mm_companion.ui.lock import set_widget_locked
from mm_companion.ui.wheel_guard import guard_wheel
from mm_companion.ui.widgets import hline_separator, make_spin_box, title_with_cost

STAT_MIN, STAT_MAX = -5, 30
STAT_SPIN_WIDTH = 80

RANK_MIN, RANK_MAX = 1, 20

# The green a power-boosted trait's "→ total" reads in, matching the summary tints.
ENHANCED_TINT = "#2e9e4f"


class StatsSection(QGroupBox):
    """Spin boxes for abilities and resistances, plus a combo-box driven picker
    for advantages, all backed by the shared :class:`Character`.

    Emits :attr:`abilityChanged` (key, value) whenever an ability spin box
    changes, so dependent sections (e.g. Skills) can recompute. Emits the generic
    :attr:`changed` whenever anything that affects the point build changes, so the
    sheet can recompute spent power points.
    """

    abilityChanged = Signal(str, int)
    changed = Signal()

    def __init__(self, data: GameData, character: Character, parent: QWidget | None = None) -> None:
        super().__init__("Base Character Stats", parent)

        self._data = data
        self._character = character
        self._abilities: dict[str, QSpinBox] = {}
        self._resistances: dict[str, QSpinBox] = {}
        # The "→ total" labels that show a power-boosted trait's enhanced value.
        self._ability_enh: dict[str, QLabel] = {}
        self._resistance_enh: dict[str, QLabel] = {}
        self._advantages_by_name = {a.name: a for a in data.advantages}

        layout = QHBoxLayout(self)
        self._ability_box = self._build_stat_group(
            "Abilities",
            data.abilities,
            self._abilities,
            self._ability_enh,
            character.abilities,
            self._on_ability_changed,
        )
        layout.addWidget(self._ability_box)
        self._resistance_box = self._build_stat_group(
            "Resistances",
            data.resistances,
            self._resistances,
            self._resistance_enh,
            character.resistances,
            self._on_resistance_changed,
        )
        layout.addWidget(self._resistance_box)
        self._advantage_box = self._build_advantages(data)
        layout.addWidget(self._advantage_box, stretch=1)
        # The resistance spin boxes hold the *total* (base + bought), so display the
        # base on top of the stored delta now that the ability spin boxes exist.
        self._refresh_resistance_bases()
        # Seed the enhancement labels from any powers a loaded character carries.
        self.refresh_enhancements()
        # Show each group's running point cost in its title.
        self._refresh_costs()

    def _add_stat_row(
        self, grid: QGridLayout, row: int, name: str, abbr: str, spin: QSpinBox, enh: QLabel
    ) -> None:
        """Lay out one stat as four aligned columns: name, short code, spin box,
        and the (usually hidden) power-enhanced total."""

        grid.addWidget(QLabel(f"{name}:"), row, 0)
        code = QLabel(abbr)
        code.setAlignment(Qt.AlignmentFlag.AlignCenter)
        grid.addWidget(code, row, 1)
        grid.addWidget(spin, row, 2)
        grid.addWidget(enh, row, 3)

    def _build_stat_group(
        self,
        title: str,
        entries: list,
        store: dict[str, QSpinBox],
        enh_store: dict[str, QLabel],
        values: dict[str, int],
        on_change: Callable[[str, int], None],
    ) -> QGroupBox:
        """Build a titled grid of stat spin boxes (abilities or resistances).

        Spin boxes are seeded from *values* (the model dict) and write back
        through *on_change*. Each row also gets a green "→ total" label that stays
        hidden until a power enhances that trait. A separator is inserted before
        the first derived entry.
        """

        box = QGroupBox(title)
        grid = QGridLayout(box)
        row = 0
        separated = False
        for entry in entries:
            if entry.derived and not separated:
                grid.addWidget(hline_separator(), row, 0, 1, 4)
                row += 1
                separated = True
            spin = make_spin_box(
                STAT_MIN,
                STAT_MAX,
                value=values.get(entry.key, 0),
                buttons=False,
                max_width=STAT_SPIN_WIDTH,
            )
            spin.valueChanged.connect(lambda value, key=entry.key: on_change(key, value))
            store[entry.key] = spin
            enh = QLabel()
            enh.setStyleSheet(f"color: {ENHANCED_TINT}; font-weight: bold;")
            enh.setVisible(False)
            enh_store[entry.key] = enh
            self._add_stat_row(grid, row, entry.name, entry.abbr, spin, enh)
            row += 1
        return box

    def _on_ability_changed(self, key: str, value: int) -> None:
        self._character.abilities[key] = value
        self.abilityChanged.emit(key, value)
        # A resistance derived from this ability follows it (its bought delta is kept).
        self._refresh_resistance_bases()
        self.refresh_enhancements()  # the base moved, so the "→ total" does too
        self._refresh_costs()
        self.changed.emit()

    def _on_resistance_changed(self, key: str, value: int) -> None:
        # The spin box holds the total; only the difference from the derived base is
        # bought (and costs/refunds points), so store that delta on the model.
        base = resistance_base(self._character, self._data, key)
        self._character.resistances[key] = value - base
        # Dodge derives from the Defense trait, so changing one resistance can move
        # another; re-seed them all (guarded, so this doesn't re-enter).
        self._refresh_resistance_bases()
        self.refresh_enhancements()
        self._refresh_costs()
        self.changed.emit()

    def _refresh_costs(self) -> None:
        """Show each group's running point cost in its title (Abilities/Resistances/
        Advantages), recomputed from the model so it tracks every edit."""

        self._ability_box.setTitle(
            title_with_cost("Abilities", ability_points_spent(self._character, self._data))
        )
        self._resistance_box.setTitle(
            title_with_cost("Resistances", resistance_points_spent(self._character, self._data))
        )
        self._advantage_box.setTitle(
            title_with_cost("Advantages", advantage_points_spent(self._character, self._data))
        )

    def _refresh_resistance_bases(self) -> None:
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
        """Recompute each trait's power-enhanced total and show it beside the base.

        A trait with no power bonus keeps its label hidden, so the column only
        appears for traits an Enhanced-Trait/Protection power actually raises. The
        base a bonus adds to is the spin box's own value, which for a resistance is
        already its full derived total.
        """

        bonuses = power_trait_bonuses(self._character, self._data)
        self._apply_enhancements(self._abilities, self._ability_enh, bonuses["ability"])
        self._apply_enhancements(self._resistances, self._resistance_enh, bonuses["resistance"])

    @staticmethod
    def _apply_enhancements(spins: dict, labels: dict, bonuses: dict) -> None:
        for key, label in labels.items():
            bonus = bonuses.get(key)
            if bonus:
                total = spins[key].value() + bonus.amount
                label.setText(f"→ {total}")
                label.setToolTip(f"+{bonus.amount} from {', '.join(bonus.sources)}")
                label.setVisible(True)
            else:
                label.clear()
                label.setToolTip("")
                label.setVisible(False)

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

        # Render any advantages a loaded character already carries.
        for selection in self._character.advantages:
            advantage = self._advantages_by_name.get(selection.name)
            description = advantage.description if advantage else ""
            ranked = bool(advantage and advantage.ranked)
            self._append_advantage_row(selection.name, selection.rank, description, ranked)
        return box

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
        self._refresh_costs()
        self.changed.emit()

    def _remove_advantage(self) -> None:
        rows = {index.row() for index in self._advantage_table.selectedIndexes()}
        for row in sorted(rows, reverse=True):
            self._advantage_table.removeRow(row)
            if 0 <= row < len(self._character.advantages):
                del self._character.advantages[row]
        if rows:
            self._refresh_costs()
            self.changed.emit()

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

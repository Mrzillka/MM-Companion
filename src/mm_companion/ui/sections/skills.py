"""Section 3: the skills table.

Each skill row lays out its bonus as a sum of columns: the linked ability's
short code and current rank, the skill's own ranks, and a free modifier. The
total bonus is the sum of the ability rank, the skill ranks, and the modifier.
Focused skills (Close Combat, Expertise, Ranged Combat) have no ranks of their
own — the character instead adds focused instances, each of which becomes its
own rankable row.

To save vertical space the skills are laid out across two side-by-side tables:
the left flow fills the first table and the right flow fills the second. Neither
table scrolls — each is sized to show all of its rows and grows as focuses are
added, so the whole section scrolls with the page. The split is dynamic: skills
are grouped
into blocks (a plain skill is one block; a focused skill and its focus rows form
a single block), and the blocks are divided between the two tables so their
heights are as even as possible without ever splitting a focused group.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QGroupBox,
    QHBoxLayout,
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
from mm_companion.ui.lock import set_widget_locked
from mm_companion.ui.wheel_guard import guard_wheel
from mm_companion.ui.widgets import make_spin_box, readonly_item

RANK_MIN, RANK_MAX = 0, 20
MOD_MIN, MOD_MAX = -20, 20
COL_NAME, COL_ABILITY, COL_ABILITY_RANK, COL_RANKS, COL_MODS, COL_TOTAL = range(6)
HEADERS = ["Skill", "Ability", "ABL", "Rank", "+", "Total"]
# Keep the numeric spin-box columns narrow so they don't hog horizontal space.
SPIN_WIDTH = 56


class SkillsSection(QGroupBox):
    """A table of skills whose total bonuses track the current ability values."""

    def __init__(self, data: GameData, parent: QWidget | None = None) -> None:
        super().__init__("Skills", parent)

        self._skills = data.skills
        self._ability_abbrs: dict[str, str] = {a.key: a.abbr or a.key for a in data.abilities}
        self._ability_values: dict[str, int] = {a.key: 0 for a in data.abilities}
        # Ranks and modifiers bought per row, keyed by a stable row id.
        self._ranks: dict[str, int] = {}
        self._mods: dict[str, int] = {}
        # Added focuses per focused skill name, e.g. {"Close Combat": ["Swords"]}.
        self._focuses: dict[str, list[str]] = {s.name: [] for s in data.skills if s.focused}
        # (ability_key, row_id, ability_rank_item, total_item) for every rankable
        # row, so the ability-rank and total cells can be recomputed when
        # abilities change.
        self._rows: list[tuple[str, str, QTableWidgetItem, QTableWidgetItem]] = []
        # Rank/modifier spin boxes rebuilt on every layout pass, tracked so the
        # lock state can be re-applied to them.
        self._editable_spins: list[QSpinBox] = []
        self._locked = False

        layout = QVBoxLayout(self)
        tables = QHBoxLayout()
        self.table_left = self._make_table()
        self.table_right = self._make_table()
        tables.addWidget(self.table_left)
        tables.addWidget(self.table_right)
        layout.addLayout(tables)

        guard_wheel(self.table_left, self.table_right)
        # The tables fit their content and never scroll, so keep them out of the
        # focus chain; the wheel then always falls through to the page scroll.
        for table in (self.table_left, self.table_right):
            table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._rebuild()

    @staticmethod
    def _make_table() -> QTableWidget:
        table = QTableWidget(0, len(HEADERS))
        table.setHorizontalHeaderLabels(HEADERS)
        table.verticalHeader().setVisible(False)
        # The table never scrolls itself; it is resized to fit all its rows.
        table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        header = table.horizontalHeader()
        header.setSectionResizeMode(COL_NAME, QHeaderView.ResizeMode.Stretch)
        for col in (COL_ABILITY, COL_ABILITY_RANK, COL_RANKS, COL_MODS, COL_TOTAL):
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
        return table

    @staticmethod
    def _fit_table_height(table: QTableWidget) -> None:
        """Fix the table's height to exactly show every row, so it never scrolls
        internally and grows as focuses are added."""

        height = table.horizontalHeader().height() + 2 * table.frameWidth()
        for row in range(table.rowCount()):
            height += table.rowHeight(row)
        table.setFixedHeight(height)

    # -- data-driven rebuild -------------------------------------------------

    def _rebuild(self) -> None:
        self._rows.clear()
        self._editable_spins.clear()
        left, right = self._split_blocks()
        left_specs = self._expand(left)
        right_specs = self._expand(right)

        # Match row counts so the two tables stay the same height and their rows
        # line up side by side.
        row_count = max(len(left_specs), len(right_specs))
        for table, specs in ((self.table_left, left_specs), (self.table_right, right_specs)):
            table.setRowCount(0)
            table.clearSpans()
            table.setRowCount(row_count)
            self._render_side(table, specs)
            self._fit_table_height(table)

        self._apply_lock()
        self._refresh_totals()

    def _split_blocks(self) -> tuple[list[Skill], list[Skill]]:
        """Divide the skills into two ordered groups of near-equal height.

        Each skill is a block whose height is one row, plus one row per focus for
        focused skills; blocks are never split across the two groups.
        """

        sizes = [1 + len(self._focuses[s.name]) if s.focused else 1 for s in self._skills]
        total = sum(sizes)

        best_split, best_diff = 0, None
        for split in range(len(self._skills) + 1):
            left = sum(sizes[:split])
            diff = abs(left - (total - left))
            if best_diff is None or diff < best_diff:
                best_split, best_diff = split, diff

        return self._skills[:best_split], self._skills[best_split:]

    def _expand(self, skills: list[Skill]) -> list[tuple]:
        """Flatten skills into per-row specs: a focused skill yields a header row
        followed by one row per focus; a plain skill yields a single row."""

        specs: list[tuple] = []
        for skill in skills:
            if skill.focused:
                specs.append(("header", skill))
                for focus in self._focuses[skill.name]:
                    display = f"{skill.name}: {focus}"
                    row_id = f"{skill.name}::{focus}"
                    specs.append(("focus", skill, display, row_id))
            else:
                specs.append(("skill", skill, skill.name, skill.name))
        return specs

    def _render_side(self, table: QTableWidget, specs: list[tuple]) -> None:
        for row, spec in enumerate(specs):
            if spec[0] == "header":
                self._render_group_header(table, row, spec[1])
            else:
                _, skill, display, row_id = spec
                indent = spec[0] == "focus"
                self._render_skill_row(table, row, skill, display, row_id, indent=indent)

    def _render_group_header(self, table: QTableWidget, row: int, skill: Skill) -> None:
        """Header cell block with an 'Add focus' button for a focused skill."""

        table.setItem(row, COL_NAME, readonly_item(skill.name))

        # In the locked (read-only) view there's nothing to add, so the header
        # is just the skill name with no button.
        if self._locked:
            return

        # The button spans every column after the name so it reads as one wide
        # control rather than being crammed into a single narrow cell.
        add_button = QPushButton("Add focus…")
        add_button.clicked.connect(lambda _=False, s=skill: self._add_focus(s))
        table.setSpan(row, COL_ABILITY, 1, len(HEADERS) - COL_ABILITY)
        table.setCellWidget(row, COL_ABILITY, add_button)

    def _render_skill_row(
        self,
        table: QTableWidget,
        row: int,
        skill: Skill,
        display: str,
        row_id: str,
        indent: bool = False,
    ) -> None:
        name = ("    " if indent else "") + display
        table.setItem(row, COL_NAME, readonly_item(name))

        abbr = self._ability_abbrs.get(skill.ability, skill.ability)
        table.setItem(row, COL_ABILITY, readonly_item(abbr, center=True))

        ability_rank_item = readonly_item("", center=True)
        table.setItem(row, COL_ABILITY_RANK, ability_rank_item)

        ranks_spin = make_spin_box(
            RANK_MIN,
            RANK_MAX,
            value=self._ranks.get(row_id, 0),
            buttons=False,
            max_width=SPIN_WIDTH,
        )
        ranks_spin.valueChanged.connect(lambda value, rid=row_id: self._on_rank_changed(rid, value))
        table.setCellWidget(row, COL_RANKS, ranks_spin)

        mods_spin = make_spin_box(
            MOD_MIN, MOD_MAX, value=self._mods.get(row_id, 0), buttons=False, max_width=SPIN_WIDTH
        )
        mods_spin.valueChanged.connect(lambda value, rid=row_id: self._on_mod_changed(rid, value))
        table.setCellWidget(row, COL_MODS, mods_spin)

        self._editable_spins.extend((ranks_spin, mods_spin))

        total_item = readonly_item("", center=True)
        table.setItem(row, COL_TOTAL, total_item)
        self._rows.append((skill.ability, row_id, ability_rank_item, total_item))

    # -- interaction ---------------------------------------------------------

    def _add_focus(self, skill: Skill) -> None:
        focus, ok = QInputDialog.getText(self, f"Add {skill.name} focus", "Focus:")
        focus = focus.strip()
        if ok and focus and focus not in self._focuses[skill.name]:
            self._focuses[skill.name].append(focus)
            self._rebuild()

    def set_locked(self, locked: bool) -> None:
        """Make the rank/modifier spin boxes read-only labels and drop the
        'Add focus' buttons while locked.

        Rebuilds the tables so the focus buttons are omitted entirely: they live
        in table cells, where toggling visibility isn't reliable.
        """
        self._locked = locked
        self._rebuild()

    def _apply_lock(self) -> None:
        """Apply the current lock state to the spin boxes built by the last
        rebuild. (Focus buttons are omitted at build time when locked.)"""
        for spin in self._editable_spins:
            set_widget_locked(spin, self._locked)

    def _on_rank_changed(self, row_id: str, value: int) -> None:
        self._ranks[row_id] = value
        self._refresh_totals()

    def _on_mod_changed(self, row_id: str, value: int) -> None:
        self._mods[row_id] = value
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
        for ability_key, row_id, ability_rank_item, total_item in self._rows:
            ability = self._ability_values.get(ability_key, 0)
            total = ability + self._ranks.get(row_id, 0) + self._mods.get(row_id, 0)
            ability_rank_item.setText(str(ability))
            total_item.setText(str(total))

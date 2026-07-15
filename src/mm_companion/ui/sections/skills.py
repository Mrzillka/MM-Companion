"""Section 3: the skills table.

Each skill row lays out its bonus as a sum of columns: the linked ability's
short code and current rank, the skill's own ranks, and the outside bonuses
granted by powers and advantages. The total bonus is their sum. Only the ranks
are bought: the "+" column is a derived read-out (:func:`skill_bonus`), never an
input, and the whole column hides while nothing grants a bonus.
Focused skills (Close Combat, Expertise, Ranged Combat) have no ranks of their
own — the character instead adds focused instances, each of which becomes its
own rankable row. Any skill can also carry *specialized* rows: narrow, half-cost
rank pools rendered as extra indented rows under the skill.

To save vertical space the skills are laid out across several side-by-side
tables. The number of panels adapts to the block's width (see
:mod:`mm_companion.ui.sections.column_flow`): a wide block shows more columns, a
narrow one fewer, and a long skill/focus name raises the minimum panel width so
the count drops before anything clips. Neither table scrolls — each is sized to
show all of its rows and grows as focuses are added, so the whole section scrolls
with the page. The split is dynamic: skills are grouped into blocks (a plain
skill is one block; a focused skill with its focus rows, plus any skill's
specialization rows, form a single block), and the blocks are divided across the
panels so their heights are as even as possible without ever splitting a block.
"""

from __future__ import annotations

from typing import NamedTuple

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QResizeEvent
from PySide6.QtWidgets import (
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from mm_companion.core.character import Character
from mm_companion.core.data_loader import GameData, Skill
from mm_companion.core.rules import (
    condition_scope_penalty,
    effective_ability,
    skill_bonus,
    skill_points_spent,
    skill_total,
)
from mm_companion.ui.lock import set_widget_locked
from mm_companion.ui.sections.column_flow import column_count, even_split
from mm_companion.ui.sections.stat_grid import (
    CONDITION_TINT,
    ENHANCED_TINT,
    STRIKETHROUGH_CONDITIONS,
)
from mm_companion.ui.sections.titled_section import TitledSection
from mm_companion.ui.wheel_guard import guard_wheel
from mm_companion.ui.widgets import make_spin_box, readonly_item, title_with_cost

RANK_MIN, RANK_MAX = 0, 20
COL_NAME, COL_ABILITY, COL_ABILITY_RANK, COL_RANKS, COL_MODS, COL_TOTAL = range(6)
HEADERS = ["Skill", "Ability", "ABL", "Rank", "+", "Total"]
# Keep the numeric spin-box columns narrow so they don't hog horizontal space.
SPIN_WIDTH = 56
# Spacing between the side-by-side skill panels.
TABLE_SPACING = 6
# Dead-band (px) that stops the panel count from flipping when the page's vertical
# scrollbar appears/disappears (which nudges the width by its own extent).
COLUMN_HYSTERESIS = 24
# Rough widths used to decide how many panels fit without clipping a name.
# The numeric columns are near-fixed; the name column needs room for the widest
# skill/focus/specialization label. Kept lean so a second column appears before a
# lone one stretches wide and leaves a big gap between names and their numbers —
# these are UI heuristics, easy to retune.
NAME_MIN_WIDTH = 100
NAME_PADDING = 16
NUMERIC_WIDTH = 40 + SPIN_WIDTH + 24  # ABL + rank spin + Total
# The derived "+" column's share, added only while it is shown.
MOD_WIDTH = 36
FRAME_PADDING = 16


class SkillRow(NamedTuple):
    """The cells of one rendered skill row that the refresh pass writes into.

    ``name_item`` is ``None`` when the name cell is a widget rather than a plain item
    (a row carrying an inline add/remove button), so nothing there can be restyled.
    """

    ability_key: str
    row_id: str
    ability_item: QTableWidgetItem
    mod_item: QTableWidgetItem
    total_item: QTableWidgetItem
    name_item: QTableWidgetItem | None


class SkillsSection(TitledSection):
    """A table of skills whose total bonuses track the shared character model.

    Ranks and focuses are read from and written to the :class:`Character`; the "+"
    and total columns are computed by :func:`skill_bonus` / :func:`skill_total`
    rather than in the view. Emits :attr:`changed` when the build changes so the
    sheet can recompute spent power points.
    """

    changed = Signal()

    def __init__(self, data: GameData, character: Character, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        self._data = data
        self._character = character
        self._skills = data.skills
        self._ability_abbrs: dict[str, str] = {a.key: a.abbr or a.key for a in data.abilities}
        # Ranks and focuses live on the model; ensure every focused skill has a
        # (possibly empty) focus list to render from.
        self._ranks = character.skill_ranks
        self._focuses = character.focuses
        # Specializations (narrow, half-cost pools) can hang off any skill; the model
        # only carries non-empty entries, so read with .get rather than seeding all.
        self._specializations = character.specializations
        for skill in data.skills:
            if skill.focused:
                self._focuses.setdefault(skill.name, [])
        # One SkillRow per rankable row, so the derived cells can be recomputed when
        # abilities or powers change and a condition overlay can restyle the total/name.
        self._rows: list[SkillRow] = []
        # Rank spin boxes rebuilt on every layout pass, tracked so the lock state can
        # be re-applied to them.
        self._editable_spins: list[QSpinBox] = []
        self._locked = False
        # Whether any row currently carries an outside bonus; drives the "+" column's
        # visibility (and, through _min_col_width, how many panels fit).
        self._show_mods = False

        layout = QVBoxLayout(self)
        # The skills fan out across a variable number of side-by-side panels; the
        # count adapts to the block's width (see resizeEvent / _rebuild). The
        # tables live in a container whose horizontal layout is rebuilt when the
        # count changes.
        self._tables_container = QWidget()
        self._tables_layout = QHBoxLayout(self._tables_container)
        self._tables_layout.setContentsMargins(0, 0, 0, 0)
        self._tables_layout.setSpacing(TABLE_SPACING)
        self._tables_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        layout.addWidget(self._tables_container)
        self._tables: list[QTableWidget] = []
        self._column_count = 0
        self._rebuild()

    def _make_table(self) -> QTableWidget:
        table = QTableWidget(0, len(HEADERS))
        table.setHorizontalHeaderLabels(HEADERS)
        table.verticalHeader().setVisible(False)
        # The table never scrolls itself; it is resized to fit all its rows.
        table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        # Share width equally with sibling panels; keep the fitted height fixed so
        # panels of different heights top-align rather than stretch.
        table.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        header = table.horizontalHeader()
        header.setSectionResizeMode(COL_NAME, QHeaderView.ResizeMode.Stretch)
        for col in (COL_ABILITY, COL_ABILITY_RANK, COL_RANKS, COL_MODS, COL_TOTAL):
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
        # The panels fit their content and never scroll, so keep them out of the
        # focus chain; the wheel then always falls through to the page scroll.
        table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        guard_wheel(table)
        return table

    def _ensure_tables(self, count: int) -> None:
        """Grow or shrink the pool of side-by-side panels to *count*."""

        while len(self._tables) < count:
            table = self._make_table()
            self._tables_layout.addWidget(table, stretch=1)
            self._tables.append(table)
        while len(self._tables) > count:
            table = self._tables.pop()
            self._tables_layout.removeWidget(table)
            table.deleteLater()

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
        count = column_count(
            self._available_width(),
            self._min_col_width(),
            TABLE_SPACING,
            len(self._skills),
            self._column_count,
            COLUMN_HYSTERESIS,
        )
        self._column_count = count
        self._ensure_tables(count)
        for table, skills in zip(self._tables, self._split_blocks(count), strict=True):
            specs = self._expand(skills)
            table.setRowCount(0)
            table.clearSpans()
            table.setRowCount(len(specs))
            self._render_side(table, specs)
            table.setColumnHidden(COL_MODS, not self._show_mods)
            self._fit_table_height(table)

        self._apply_lock()
        self._refresh_totals()

    def _split_blocks(self, count: int) -> list[list[Skill]]:
        """Divide the skills into *count* ordered groups of near-equal height.

        Each skill is a block whose height is one row, plus one row per focus for
        focused skills and one per specialization for any skill; blocks are never
        split across groups.
        """

        sizes = []
        for skill in self._skills:
            size = 1 + len(self._focuses[skill.name]) if skill.focused else 1
            size += len(self._specializations.get(skill.name, []))
            sizes.append(size)
        return [[self._skills[i] for i in bucket] for bucket in even_split(sizes, count)]

    # -- responsive panel count ---------------------------------------------

    def _available_width(self) -> int:
        """The width the panels have to share, net of the section's margins."""

        margins = self.layout().contentsMargins()
        return self.width() - margins.left() - margins.right()

    def _min_col_width(self) -> int:
        """Narrowest a panel may get before a skill name would clip.

        Driven by the widest label actually present (a long focus or
        specialization name raises it, forcing fewer panels), plus the near-fixed
        numeric columns.
        """

        fm = self.fontMetrics()
        longest = 0
        for skill in self._skills:
            longest = max(longest, fm.horizontalAdvance(skill.name))
            for focus in self._focuses.get(skill.name, []):
                longest = max(longest, fm.horizontalAdvance(f"    {skill.name}: {focus}"))
            for spec in self._specializations.get(skill.name, []):
                label = f"    {skill.name}: {spec} (specialized)"
                longest = max(longest, fm.horizontalAdvance(label))
        name_width = max(NAME_MIN_WIDTH, longest + NAME_PADDING)
        mod_width = MOD_WIDTH if self._show_mods else 0
        return name_width + NUMERIC_WIDTH + mod_width + FRAME_PADDING

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802 - Qt override
        super().resizeEvent(event)
        count = column_count(
            self._available_width(),
            self._min_col_width(),
            TABLE_SPACING,
            len(self._skills),
            self._column_count,
            COLUMN_HYSTERESIS,
        )
        if count != self._column_count:
            self._rebuild()

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt override
        """Report a *single-column* minimum so the block can shrink to one panel.

        The side-by-side skill tables would otherwise inflate the section's
        minimum to the full multi-column width, pinning the whole page wide and
        forcing at least two columns. Capping the reported minimum at one
        column's width lets the block narrow to a single column; the resize then
        rebuilds to as many columns as fit (see :meth:`resizeEvent`).
        """
        hint = super().minimumSizeHint()
        return QSize(min(hint.width(), self._min_col_width()), hint.height())

    def _expand(self, skills: list[Skill]) -> list[tuple]:
        """Flatten skills into per-row specs.

        A focused skill yields a header row followed by one row per focus; a plain
        skill yields a single row. Either way, any specialized pools follow as extra
        indented ``"spec"`` rows.
        """

        specs: list[tuple] = []
        for skill in skills:
            if skill.focused:
                specs.append(("header", skill))
                for focus in self._focuses[skill.name]:
                    display = f"{skill.name}: {focus}"
                    row_id = f"{skill.name}::{focus}"
                    specs.append(("focus", skill, display, row_id, focus))
            else:
                specs.append(("skill", skill, skill.name, skill.name))
            for spec in self._specializations.get(skill.name, []):
                display = f"{skill.name}: {spec} (specialized)"
                row_id = f"{skill.name}::spec::{spec}"
                specs.append(("spec", skill, display, row_id, spec))
        return specs

    def _render_side(self, table: QTableWidget, specs: list[tuple]) -> None:
        for row, spec in enumerate(specs):
            kind = spec[0]
            if kind == "header":
                self._render_group_header(table, row, spec[1])
            elif kind == "spec":
                _, skill, display, row_id, spec_name = spec
                self._render_skill_row(
                    table, row, skill, display, row_id, indent=True, spec_name=spec_name
                )
            elif kind == "focus":
                _, skill, display, row_id, focus_name = spec
                self._render_skill_row(
                    table, row, skill, display, row_id, indent=True, focus_name=focus_name
                )
            else:
                _, skill, display, row_id = spec
                self._render_skill_row(table, row, skill, display, row_id, can_specialize=True)

    def _render_group_header(self, table: QTableWidget, row: int, skill: Skill) -> None:
        """Header cell block with 'Add focus' / 'Add specialization' for a focused skill."""

        table.setItem(row, COL_NAME, readonly_item(skill.name))

        # In the locked (read-only) view there's nothing to add, so the header
        # is just the skill name with no buttons.
        if self._locked:
            return

        # The buttons span every column after the name so they read as one wide
        # control rather than being crammed into a single narrow cell.
        add_focus = QPushButton("Add focus…")
        add_focus.clicked.connect(lambda _=False, s=skill: self._add_focus(s))
        add_spec = QPushButton("Add specialization…")
        add_spec.clicked.connect(lambda _=False, s=skill: self._add_specialization(s))
        host = QWidget()
        hbox = QHBoxLayout(host)
        hbox.setContentsMargins(0, 0, 0, 0)
        hbox.setSpacing(4)
        hbox.addWidget(add_focus)
        hbox.addWidget(add_spec)
        hbox.addStretch()
        table.setSpan(row, COL_ABILITY, 1, len(HEADERS) - COL_ABILITY)
        table.setCellWidget(row, COL_ABILITY, host)

    def _render_skill_row(
        self,
        table: QTableWidget,
        row: int,
        skill: Skill,
        display: str,
        row_id: str,
        indent: bool = False,
        can_specialize: bool = False,
        spec_name: str | None = None,
        focus_name: str | None = None,
    ) -> None:
        name_item = self._render_name_cell(
            table, row, skill, display, indent, can_specialize, spec_name, focus_name
        )

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
        self._editable_spins.append(ranks_spin)

        # Granted by powers/advantages, never typed in — a read-only cell filled by
        # _refresh_totals, tinted green like the stat grids' enhancement labels.
        mod_item = readonly_item("", center=True)
        mod_item.setForeground(QBrush(QColor(ENHANCED_TINT)))
        table.setItem(row, COL_MODS, mod_item)

        total_item = readonly_item("", center=True)
        table.setItem(row, COL_TOTAL, total_item)
        self._rows.append(
            SkillRow(skill.ability, row_id, ability_rank_item, mod_item, total_item, name_item)
        )

    def _render_name_cell(
        self,
        table: QTableWidget,
        row: int,
        skill: Skill,
        display: str,
        indent: bool,
        can_specialize: bool,
        spec_name: str | None,
        focus_name: str | None = None,
    ) -> QTableWidgetItem | None:
        """The skill's name cell, optionally with an inline add/remove control.

        A plain read-only label unless (and only while unlocked) the row needs a
        control: a ``＋`` to add a specialized pool on a non-focused skill's main row,
        or a ``✕`` to drop a focus or specialization row. Returns the name
        :class:`QTableWidgetItem` for a plain cell (so a condition can strike it through)
        or ``None`` for a widget cell.
        """

        name = ("    " if indent else "") + display
        if self._locked or (not can_specialize and spec_name is None and focus_name is None):
            item = readonly_item(name)
            table.setItem(row, COL_NAME, item)
            return item

        host = QWidget()
        hbox = QHBoxLayout(host)
        hbox.setContentsMargins(4, 0, 0, 0)
        hbox.setSpacing(4)
        hbox.addWidget(QLabel(name))
        hbox.addStretch()
        if spec_name is not None:
            remove = QPushButton("✕")
            remove.setFlat(True)
            remove.setFixedWidth(20)
            remove.setToolTip("Remove this specialization")
            remove.clicked.connect(
                lambda _=False, s=skill, n=spec_name: self._remove_specialization(s, n)
            )
            hbox.addWidget(remove)
        elif focus_name is not None:
            remove = QPushButton("✕")
            remove.setFlat(True)
            remove.setFixedWidth(20)
            remove.setToolTip("Remove this focus")
            remove.clicked.connect(lambda _=False, s=skill, n=focus_name: self._remove_focus(s, n))
            hbox.addWidget(remove)
        else:  # can_specialize
            add = QPushButton("＋")
            add.setFlat(True)
            add.setFixedWidth(20)
            add.setToolTip("Add a specialized (half-cost) rank pool for this skill")
            add.clicked.connect(lambda _=False, s=skill: self._add_specialization(s))
            hbox.addWidget(add)
        table.setCellWidget(row, COL_NAME, host)
        return None

    # -- interaction ---------------------------------------------------------

    def _add_focus(self, skill: Skill) -> None:
        focus, ok = QInputDialog.getText(self, f"Add {skill.name} focus", "Focus:")
        focus = focus.strip()
        if ok and focus and focus not in self._focuses[skill.name]:
            self._focuses[skill.name].append(focus)
            self._rebuild()
            self.changed.emit()

    def _remove_focus(self, skill: Skill, focus: str) -> None:
        focuses = self._focuses.get(skill.name, [])
        if focus not in focuses:
            return
        focuses.remove(focus)
        self._ranks.pop(f"{skill.name}::{focus}", None)
        self._rebuild()
        self.changed.emit()

    def _add_specialization(self, skill: Skill) -> None:
        name, ok = QInputDialog.getText(self, f"Add {skill.name} specialization", "Specialization:")
        name = name.strip()
        specs = self._specializations.setdefault(skill.name, [])
        if ok and name and name not in specs:
            specs.append(name)
            self._rebuild()
            self.changed.emit()

    def _remove_specialization(self, skill: Skill, spec_name: str) -> None:
        specs = self._specializations.get(skill.name, [])
        if spec_name not in specs:
            return
        specs.remove(spec_name)
        if not specs:  # keep the model tidy — drop the now-empty entry
            self._specializations.pop(skill.name, None)
        self._ranks.pop(f"{skill.name}::spec::{spec_name}", None)
        self._rebuild()
        self.changed.emit()

    def set_locked(self, locked: bool) -> None:
        """Make the rank spin boxes read-only labels and drop the 'Add focus'
        buttons while locked.

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
        self.changed.emit()

    # -- totals --------------------------------------------------------------

    def set_ability_value(self, key: str, value: int) -> None:
        """Refresh skill totals after an ability changed on the shared model."""

        self._refresh_totals()

    def set_ability_values(self, values: dict[str, int]) -> None:
        """Refresh skill totals (kept for the sheet's initial sync call)."""

        self._refresh_totals()

    def refresh_totals(self) -> None:
        """Recompute every skill total — the sheet calls this when powers change,
        since an Enhanced-Trait boost to a linked ability or the skill itself moves
        the total."""

        self._refresh_totals()

    def _refresh_totals(self) -> None:
        bonuses = {
            row.row_id: skill_bonus(self._character, self._data, row.row_id) for row in self._rows
        }
        if any(bonuses.values()) != self._show_mods:
            # The "+" column just appeared or emptied out. It changes how wide a panel
            # needs to be, so rebuild rather than only toggling the column; the rebuild
            # ends by calling back here, now with the two in agreement.
            self._show_mods = not self._show_mods
            self._rebuild()
            return

        for row in self._rows:
            # The ABL column shows the *effective* ability (with any power boost) so
            # the row's columns still sum to the total.
            ability = effective_ability(self._character, self._data, row.ability_key)
            total = skill_total(self._character, self._data, row.row_id)
            row.ability_item.setText(str(ability))
            self._fill_bonus_cell(row.mod_item, bonuses[row.row_id])
            # A scoped Impaired/Disabled (or a global one) overlays the total in red,
            # struck through for a lost-trait condition. This is display-only — the
            # build math above (skill_total) is untouched.
            base_name = row.row_id.split(":", 1)[0].strip()
            effect = condition_scope_penalty(self._character, self._data, {row.row_id, base_name})
            row.total_item.setText(str(effect.apply(total) if effect.active else total))
            self._style_condition(row.total_item, row.name_item, effect, total)
        # Keep the section title's running point cost current.
        self.set_block_title(
            title_with_cost("Skills", skill_points_spent(self._character, self._data))
        )

    @staticmethod
    def _fill_bonus_cell(mod_item: QTableWidgetItem, bonus) -> None:
        """Show a row's granted bonus as a signed number naming its sources on hover.

        Blank for a row nothing boosts — the column as a whole is hidden only when
        *every* row is blank, so a shown column still has empty cells.
        """

        if bonus is None:
            mod_item.setText("")
            mod_item.setToolTip("")
            return
        mod_item.setText(f"{bonus.amount:+d}")
        mod_item.setToolTip(f"{bonus.amount:+d} from {', '.join(bonus.sources)}")

    @staticmethod
    def _style_condition(total_item, name_item, effect, base_total: int) -> None:
        """Tint the total red (and strike the row) while a condition scopes to it."""

        struck = effect.active and bool(effect.condition_ids & STRIKETHROUGH_CONDITIONS)
        for item in (total_item, name_item):
            if item is None:
                continue
            font = item.font()
            font.setStrikeOut(struck)
            item.setFont(font)
            if effect.active:
                item.setForeground(QBrush(QColor(CONDITION_TINT)))
            else:
                item.setData(Qt.ItemDataRole.ForegroundRole, None)
        if effect.active:
            total_item.setToolTip(f"{base_total} {effect.tooltip}")
        else:
            total_item.setToolTip("")

"""Section 3: the skills table.

Each skill row lays out its bonus as a sum of columns: the linked ability's
short code and current rank, the skill's own ranks, and the modifiers imposed
from outside — bonuses granted by powers and advantages, penalties imposed by
conditions. The total bonus is their sum. Only the ranks are bought: the "+"
column is a derived read-out (:func:`skill_modifiers`), never an input, and the
whole column hides while nothing modifies any row.
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

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QResizeEvent
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
    PIN_SKILL,
    PinRef,
    SkillModifiers,
    effective_ability,
    skill_modifiers,
    skill_points_spent,
    skill_roll,
    skill_total,
)
from mm_companion.ui import theme
from mm_companion.ui.lock import set_widget_locked
from mm_companion.ui.sections.column_flow import ColumnFlowPanels, even_split
from mm_companion.ui.sections.stat_table import (
    CONDITION_TINT,
    ENHANCED_TINT,
    ROLL_ROLE,
    PinMenuState,
    fit_table_height,
    install_pin_menu,
    tint_item,
)
from mm_companion.ui.sections.titled_section import TitledSection
from mm_companion.ui.wheel_guard import guard_wheel
from mm_companion.ui.widgets import make_spin_box, readonly_item

RANK_MIN, RANK_MAX = 0, 20
COL_NAME, COL_ABILITY, COL_ABILITY_RANK, COL_RANKS, COL_MODS, COL_TOTAL = range(6)
HEADERS = ["Skill", "Ability", "ABL", "Rank", "+", "Total"]
# Rough widths used to decide how many panels fit without clipping a name. The
# numeric columns are near-fixed; the name column needs room for the widest
# skill/focus/specialization label. Kept lean so a second column appears before a
# lone one stretches wide and leaves a big gap between names and their numbers.
# The three that set the block's density are theme metrics — a denser preset wants
# narrower ones — and are read through spin_width()/name_min_width()/mod_width().
NAME_PADDING = 16
FRAME_PADDING = 16
# The fixed share of the ABL and Total columns, either side of the rank spin box.
NUMERIC_PADDING = 40 + 24


def spin_width() -> int:
    """Width cap for the numeric spin-box columns, so they don't hog the row."""
    return int(theme.metric("column.skill.spin"))


def name_min_width() -> int:
    """Floor for the skill-name column, before the widest label widens it."""
    return int(theme.metric("column.skill.name"))


def mod_width() -> int:
    """The derived "+" column's share, added only while that column is shown."""
    return int(theme.metric("column.skill.mod"))


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


class SkillsSection(ColumnFlowPanels, TitledSection):
    """A table of skills whose total bonuses track the shared character model.

    Ranks and focuses are read from and written to the :class:`Character`; the "+"
    and total columns are computed by :func:`skill_modifiers` / :func:`skill_total`
    rather than in the view. Emits :attr:`changed` when the build changes so the
    sheet can recompute spent power points.
    """

    changed = Signal()

    #: A row was double-clicked — roll that skill (a focus and a specialized pool
    #: each roll their own row). Carries a
    #: :class:`~mm_companion.core.rules.RollSpec`; rolling is not a build edit.
    rollRequested = Signal(object)

    #: A row was clicked once — show that skill in the roller's chip, ready to roll.
    loadRequested = Signal(object)

    #: A row was right-clicked and pinned — carries a
    #: :class:`~mm_companion.core.rules.pins.PinRef`. Only ever raised on a sheet a
    #: GM opened from a card (see :meth:`set_pin_target`).
    pinRequested = Signal(object)
    #: The same, for a row that was already on the card.
    unpinRequested = Signal(object)

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
        # Whether this sheet was opened from a GM card, and what is already on
        # that card. Both are set by the sheet after construction.
        self._pins = PinMenuState()
        # Whether any row currently carries an outside bonus; drives the "+" column's
        # visibility (and, through _min_col_width, how many panels fit).
        self._show_mods = False

        layout = QVBoxLayout(self)
        # The skills fan out across a variable number of side-by-side panels; the
        # count adapts to the block's width (see ColumnFlowPanels).
        self._init_flow_panels(layout)
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
        # Connected once per table, which outlives the frequent _rebuild: the tables
        # themselves are reused, only their items are rebuilt.
        table.cellDoubleClicked.connect(
            lambda row, _col, t=table: self._emit_row_spec(t, row, self.rollRequested)
        )
        # A single click loads the skill into the roller's chip without throwing the
        # die. It fires first on a double-click too, which is harmless: rolling loads
        # the same spec anyway. See build_stat_table for the same bargain.
        table.cellClicked.connect(
            lambda row, _col, t=table: self._emit_row_spec(t, row, self.loadRequested)
        )
        # The same "Pin to GM card" the two stat tables offer, off the same stashed
        # payload — this table builds itself rather than going through
        # build_stat_table, so it installs the menu directly.
        install_pin_menu(
            table,
            self._pin_ref,
            self.pinRequested.emit,
            self.unpinRequested.emit,
            self._pins,
        )
        guard_wheel(table)
        return table

    def _emit_row_spec(self, table: QTableWidget, row: int, signal: Signal) -> None:
        """Send this row's roll to *signal*, if the row is one that rolls.

        Every column but Rank arrives here — that one is a spin box cell widget,
        which eats the clicks itself (and unlocked would want them for editing
        anyway).
        """
        item = table.item(row, COL_TOTAL)
        payload = None if item is None else item.data(ROLL_ROLE)
        if not payload:
            return
        row_id, display = payload
        signal.emit(skill_roll(self._character, self._data, row_id, label=display))

    @staticmethod
    def _pin_ref(payload: object) -> PinRef | None:
        """The pin that names this row. Its ``(row_id, display)`` payload is the
        roll's, so a pinned skill is exactly the row that was right-clicked — a
        focus and its parent skill are different rows and stay different pins."""
        if not isinstance(payload, tuple) or not payload:
            return None
        return PinRef(PIN_SKILL, str(payload[0]))

    def set_pin_target(self, enabled: bool) -> None:
        """Whether this block's rows offer to pin at all."""
        self._pins.enabled = enabled

    def set_pinned(self, refs) -> None:
        """Which parameters are already on the card, so a row can offer Unpin."""
        self._pins.set_pinned(refs)

    #: Fix the table's height to exactly show every row, so it never scrolls
    #: internally and grows as focuses are added. Shared with the stat tables.
    _fit_table_height = staticmethod(fit_table_height)

    # -- data-driven rebuild -------------------------------------------------

    def _rebuild(self) -> None:
        self._rows.clear()
        self._editable_spins.clear()
        count = self._flow_column_count()
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

    def _flow_item_count(self) -> int:
        return len(self._skills)

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
        name_width = max(name_min_width(), longest + NAME_PADDING)
        mods = mod_width() if self._show_mods else 0
        numeric = NUMERIC_PADDING + spin_width()
        return name_width + numeric + mods + FRAME_PADDING

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802 - Qt override
        super().resizeEvent(event)
        self._sync_column_count()

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
            max_width=spin_width(),
        )
        ranks_spin.valueChanged.connect(lambda value, rid=row_id: self._on_rank_changed(rid, value))
        table.setCellWidget(row, COL_RANKS, ranks_spin)
        self._editable_spins.append(ranks_spin)

        # Imposed from outside, never typed in — a read-only cell whose text, tint and
        # tooltip are all filled by _refresh_totals.
        mod_item = readonly_item("", center=True)
        table.setItem(row, COL_MODS, mod_item)

        total_item = readonly_item("", center=True)
        # What this table row rolls, carried on the Total cell. A double-click reads
        # it back from there whichever column was hit (see _on_cell_double_clicked);
        # a group header row has no Total cell at all, which is exactly how it says
        # "nothing to roll here".
        total_item.setData(ROLL_ROLE, (row_id, display))
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
        if not ok or not name:
            return
        # Only reach for the list once the dialog was actually accepted: a bare
        # ``setdefault`` on the cancel path leaves an empty entry on the model, which
        # ``_remove_specialization`` goes out of its way to avoid and which would then
        # ride along into the saved JSON.
        specs = self._specializations.setdefault(skill.name, [])
        if name not in specs:
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

    def refresh_totals(self) -> None:
        """Recompute every skill total — the sheet calls this when powers change,
        since an Enhanced-Trait boost to a linked ability or the skill itself moves
        the total."""

        self._refresh_totals()

    def _refresh_totals(self) -> None:
        mods = {
            row.row_id: skill_modifiers(self._character, self._data, row.row_id)
            for row in self._rows
        }
        if any(m.has_flat_modifier for m in mods.values()) != self._show_mods:
            # The "+" column just appeared or emptied out. It changes how wide a panel
            # needs to be, so rebuild rather than only toggling the column; the rebuild
            # ends by calling back here, now with the two in agreement.
            self._show_mods = not self._show_mods
            self._rebuild()
            return

        for row in self._rows:
            mod = mods[row.row_id]
            # The ABL column shows the *effective* ability (with any power boost) so
            # the row's columns still sum to the total.
            ability = effective_ability(self._character, self._data, row.ability_key)
            total = skill_total(self._character, self._data, row.row_id)
            row.ability_item.setText(str(ability))
            self._fill_modifier_cell(row.mod_item, mod)
            # A scoped Impaired/Disabled (or a global one) overlays the total in red,
            # struck through for a lost-trait condition. This is display-only — the
            # build math above (skill_total) is untouched.
            effect = mod.condition
            row.total_item.setText(str(effect.apply(total) if effect.active else total))
            self._style_condition(row.total_item, row.name_item, effect, total)
        # Keep the section title's running point cost current.
        self.set_priced_title("Skills", skill_points_spent(self._character, self._data))

    @staticmethod
    def _fill_modifier_cell(mod_item: QTableWidgetItem, mod: SkillModifiers) -> None:
        """Show a row's net outside modifier as a signed number, explained on hover.

        Green while only powers/advantages grant it, red once a condition takes part
        (matching the stat grids), and struck through for a lost-trait condition. Blank
        for a row nothing modifies — the column as a whole hides only when *every* row
        is blank, so a shown column still has empty cells.
        """

        if not mod.has_flat_modifier:
            mod_item.setText("")
            mod_item.setToolTip("")
            tint_item(mod_item, None)
            return

        penalised = mod.condition.active
        mod_item.setText(f"{mod.amount:+d}")

        tips = []
        if mod.grants:
            tips.append(f"{mod.grants.amount:+d} from {', '.join(mod.grants.sources)}")
        if penalised and mod.condition.tooltip:
            tips.append(mod.condition.tooltip)
        mod_item.setToolTip("\n".join(tips))

        tint_item(
            mod_item,
            CONDITION_TINT if penalised else ENHANCED_TINT,
            struck=mod.condition.trait_lost,
        )

    @staticmethod
    def _style_condition(total_item, name_item, effect, base_total: int) -> None:
        """Tint the total red (and strike the row) while a condition scopes to it."""

        struck = effect.active and effect.trait_lost
        for item in (total_item, name_item):
            tint_item(item, CONDITION_TINT if effect.active else None, struck=struck)
        if effect.active:
            total_item.setToolTip(f"{base_total} {effect.tooltip}")
        else:
            total_item.setToolTip("")

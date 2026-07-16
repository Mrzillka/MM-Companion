"""The advantages block: a combo-box picker plus the chosen advantages.

The chosen advantages live on the shared :class:`~mm_companion.core.character.Character`
and this block is a view over that list. They render across a variable number of
side-by-side panels whose count adapts to the block's width (see
:mod:`mm_companion.ui.sections.column_flow`), so a row is no longer positionally
1:1 with the model — each rendered row keeps a reference back to its backing
``AdvantageSelection`` (``_row_refs``). A sort dropdown reorders the list (Name /
Rank / Type permanently rewrite ``Character.advantages``; Manual leaves it alone and
enables the ▲/▼ move buttons).

Rank limits are enforced here from the rules layer: a ranked advantage's spin box is
capped at its own maximum (:func:`~mm_companion.core.rules.advantage_rank_cap` — the
fixed numbers and Improved Initiative's ``ceil(PL/2)``), and Heroic-type advantages
also draw from a shared per-character budget
(:func:`~mm_companion.core.rules.heroic_advantage_budget`) shown beside the picker.
"""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QResizeEvent
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from mm_companion.core.character import AdvantageSelection, Character
from mm_companion.core.data_loader import Advantage, GameData, ParameterSpec
from mm_companion.core.powers import PowerGroup
from mm_companion.core.rules import (
    HEROIC_TYPE,
    advantage_points_spent,
    advantage_rank_cap,
    debilitated_traits,
    heroic_advantage_budget,
    heroic_advantage_ranks,
)
from mm_companion.ui.sections.column_flow import column_count, even_split
from mm_companion.ui.sections.stat_grid import CONDITION_TINT
from mm_companion.ui.sections.titled_section import TitledSection
from mm_companion.ui.wheel_guard import guard_wheel
from mm_companion.ui.widgets import make_spin_box, title_with_cost

RANK_MIN, RANK_MAX = 1, 20

# Sort modes for the chosen-advantages list (UI-only state, not persisted).
SORT_MANUAL, SORT_NAME, SORT_RANK, SORT_TYPE = "manual", "name", "rank", "type"

# Spacing between the side-by-side advantage panels.
TABLE_SPACING = 6
# Dead-band (px) that stops the panel count from flipping when the page's vertical
# scrollbar appears/disappears (which nudges the width by its own extent).
COLUMN_HYSTERESIS = 24
# Below this much room left for the combo box beside the picker controls, the
# controls wrap onto their own row so the combo keeps enough width to read.
PICKER_COMBO_MIN = 180
# Rough widths used to decide how many panels fit without clipping a row. The
# Name and Type columns size to content; the Description wraps but still wants a
# readable minimum. These are UI heuristics, easy to retune.
MIN_DESC_WIDTH = 180
NAME_PADDING = 24
TYPE_PADDING = 24
FRAME_PADDING = 24


class _AutoHeightTable(QTableWidget):
    """A table that reports its full content height so it never scrolls itself.

    The advantages block grows in height to fit every row instead of the table
    scrolling internally: the table's own vertical scrollbar is off and its size
    hint is the header plus the summed row heights, so the enclosing block (which
    is sized to its content) grows as advantages are added. Word-wrapped rows are
    re-measured on resize, since their height depends on the stretched column's
    width.
    """

    def __init__(self, rows: int, columns: int, parent: QWidget | None = None) -> None:
        super().__init__(rows, columns, parent)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def _content_height(self) -> int:
        height = 2 * self.frameWidth()
        header = self.horizontalHeader()
        if header.isVisible():
            height += header.height()
        for row in range(self.rowCount()):
            height += self.rowHeight(row)
        return height

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt override
        return QSize(super().sizeHint().width(), self._content_height())

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt override
        return QSize(super().minimumSizeHint().width(), self._content_height())

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802 - Qt override
        super().resizeEvent(event)
        # A wider/narrower table re-wraps the description column, changing row
        # heights, so re-measure and let the block resize to the new content.
        for row in range(self.rowCount()):
            self.resizeRowToContents(row)
        self.updateGeometry()


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

        self._advantage_combo = QComboBox()
        for advantage in data.advantages:
            label = f"{advantage.name} ({', '.join(advantage.types)})"
            self._advantage_combo.addItem(label, advantage)
        # Let the combo shrink and stretch so it uses whatever width its row has,
        # rather than pinning the row wide to its longest advantage name.
        self._advantage_combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._advantage_combo.setMinimumWidth(0)

        # The subject input for an advantage that needs one (a skill, an attack, a
        # foe type, ...). Its shape is data-driven per advantage (see ParameterSpec):
        # a combo for a "choice" parameter, a line edit for a free-text one. Both are
        # created once and shown/populated per advantage by _sync_parameter.
        self._advantage_param = QComboBox()
        self._advantage_param.setVisible(False)
        self._advantage_param_text = QLineEdit()
        self._advantage_param_text.setVisible(False)
        self._advantage_rank = make_spin_box(RANK_MIN, RANK_MAX, guarded=False)
        self._advantage_add_button = QPushButton("Add")
        self._advantage_add_button.clicked.connect(self._add_advantage)
        self._advantage_remove_button = QPushButton("Remove")
        self._advantage_remove_button.clicked.connect(self._remove_advantage)

        # The subject/rank/Add/Remove controls travel together as one widget so they
        # can move between the picker's two rows in one step (see _apply_picker_mode).
        self._advantage_controls = QWidget()
        controls_row = QHBoxLayout(self._advantage_controls)
        controls_row.setContentsMargins(0, 0, 0, 0)
        for widget in (
            self._advantage_param,
            self._advantage_param_text,
            self._advantage_rank,
            self._advantage_add_button,
            self._advantage_remove_button,
        ):
            controls_row.addWidget(widget)

        # The picker is one row when there's room and wraps the controls onto a
        # second row when the block is too narrow, so the combo always has space to
        # show the advantage name (see resizeEvent / _apply_picker_mode).
        self._picker_widget = QWidget()
        picker_vbox = QVBoxLayout(self._picker_widget)
        picker_vbox.setContentsMargins(0, 0, 0, 0)
        picker_vbox.setSpacing(4)
        self._picker_row1 = QHBoxLayout()
        self._picker_row1.addWidget(self._advantage_combo, stretch=1)
        self._picker_row2 = QHBoxLayout()
        picker_vbox.addLayout(self._picker_row1)
        picker_vbox.addLayout(self._picker_row2)
        self._picker_narrow: bool | None = None
        self._apply_picker_mode(False)
        outer.addWidget(self._picker_widget)

        # The shared Heroic-advantage budget, refreshed on every change and PL edit.
        self._heroic_label = QLabel()
        outer.addWidget(self._heroic_label)

        # Sort / manual-reorder controls (hidden while locked).
        controls = QHBoxLayout()
        self._sort_label = QLabel("Sort:")
        controls.addWidget(self._sort_label)
        self._sort_combo = QComboBox()
        self._sort_combo.addItem("Manual", SORT_MANUAL)
        self._sort_combo.addItem("Name (A–Z)", SORT_NAME)
        self._sort_combo.addItem("Rank (high→low)", SORT_RANK)
        self._sort_combo.addItem("Type", SORT_TYPE)
        self._sort_combo.currentIndexChanged.connect(self._on_sort_changed)
        controls.addWidget(self._sort_combo)
        self._move_up_button = QPushButton("▲")
        self._move_up_button.setToolTip("Move the selected advantage earlier")
        self._move_up_button.clicked.connect(lambda: self._move_selected(-1))
        controls.addWidget(self._move_up_button)
        self._move_down_button = QPushButton("▼")
        self._move_down_button.setToolTip("Move the selected advantage later")
        self._move_down_button.clicked.connect(lambda: self._move_selected(1))
        controls.addWidget(self._move_down_button)
        controls.addStretch()
        outer.addLayout(controls)

        # Chosen advantages fan out across a variable number of side-by-side
        # panels; the count adapts to the block's width (see resizeEvent). Row →
        # model mapping is no longer positional, so each rendered row keeps a
        # reference back to its backing AdvantageSelection.
        self._sort_mode = SORT_MANUAL
        self._locked = False
        self._selected: AdvantageSelection | None = None
        self._syncing_selection = False
        self._row_refs: list[tuple[_AutoHeightTable, int, AdvantageSelection]] = []
        self._tables: list[_AutoHeightTable] = []
        self._column_count = 0
        self._tables_container = QWidget()
        self._tables_layout = QHBoxLayout(self._tables_container)
        self._tables_layout.setContentsMargins(0, 0, 0, 0)
        self._tables_layout.setSpacing(TABLE_SPACING)
        self._tables_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        outer.addWidget(self._tables_container)
        # Keep the content packed at the top: when this block is stretched taller than
        # its content (e.g. sharing a row with the much taller Skills block) the extra
        # height goes to this stretch instead of being spread between the rows above.
        outer.addStretch(1)

        self._advantage_combo.currentIndexChanged.connect(self._sync_rank_enabled)
        self._sync_rank_enabled()
        guard_wheel(
            self._advantage_combo,
            self._advantage_param,
            self._advantage_rank,
            self._sort_combo,
        )

        self._rebuild()
        self.refresh_cost()
        self.refresh_limits()

    def refresh_conditions(self) -> None:
        """Strike through (and redden) any advantage a Debilitated condition has lost.

        Display-only, mirroring the abilities/skills overlay: the row's own point cost is
        untouched — the advantage is just marked non-functional while debilitated. The
        sheet calls this whenever the applied conditions change.
        """

        lost = debilitated_traits(self._character, self._data)
        for table, row, selection in self._row_refs:
            item = table.item(row, 0)
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

    # -- panel construction / rebuild ---------------------------------------

    def _make_table(self) -> _AutoHeightTable:
        table = _AutoHeightTable(0, 3)
        table.setHorizontalHeaderLabels(["Advantage", "Type", "Description"])
        table.verticalHeader().setVisible(False)
        table.setWordWrap(True)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        header = table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        table.itemSelectionChanged.connect(lambda t=table: self._on_selection_changed(t))
        table.cellDoubleClicked.connect(lambda row, _col, t=table: self._edit_row(t, row))
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

    def _rebuild(self) -> None:
        """Re-render every panel from the ordered advantage list.

        Called on add/remove, on a sort or manual move, and when the panel count
        changes on resize.
        """

        self._row_refs.clear()
        selections = self._character.advantages
        count = column_count(
            self._available_width(),
            self._min_col_width(),
            TABLE_SPACING,
            len(selections),
            self._column_count,
            COLUMN_HYSTERESIS,
        )
        self._column_count = count
        self._ensure_tables(count)
        buckets = even_split([1] * len(selections), count)
        for table, bucket in zip(self._tables, buckets, strict=True):
            table.setRowCount(0)
            for index in bucket:
                self._render_row(table, selections[index])
            table.updateGeometry()
        self.refresh_conditions()
        self._restore_selection()

    def _render_row(self, table: _AutoHeightTable, selection: AdvantageSelection) -> None:
        """Append one row for *selection*, recording its row → model mapping."""

        advantage = self._advantages_by_name.get(selection.name)
        ranked = bool(advantage and advantage.ranked)
        text = f"{selection.name} {selection.rank}" if ranked else selection.name
        subject = self._parameter_display(selection)
        if subject:
            text = f"{text} ({subject})"
        types = ", ".join(advantage.types) if advantage else ""
        description = advantage.description if advantage else ""
        row = table.rowCount()
        table.insertRow(row)
        table.setItem(row, 0, QTableWidgetItem(text))
        table.setItem(row, 1, QTableWidgetItem(types))
        table.setItem(row, 2, QTableWidgetItem(description))
        table.resizeRowToContents(row)
        self._row_refs.append((table, row, selection))

    # -- ordering / sorting --------------------------------------------------

    def _apply_sort(self) -> None:
        """Reorder the model list in place for the current preset sort mode.

        A preset is a *permanent* reorder — it rewrites ``Character.advantages``
        so the new order persists through ``to_dict``. Manual leaves the list
        untouched (it is the source of truth for hand ordering).
        """

        if self._sort_mode == SORT_NAME:
            self._character.advantages.sort(key=lambda s: s.name.lower())
        elif self._sort_mode == SORT_RANK:
            self._character.advantages.sort(key=lambda s: (-s.rank, s.name.lower()))
        elif self._sort_mode == SORT_TYPE:
            self._character.advantages.sort(key=lambda s: (self._type_key(s), s.name.lower()))

    def _type_key(self, selection: AdvantageSelection) -> str:
        advantage = self._advantages_by_name.get(selection.name)
        return ", ".join(advantage.types) if advantage else ""

    def _on_sort_changed(self) -> None:
        self._sort_mode = self._sort_combo.currentData()
        manual = self._sort_mode == SORT_MANUAL
        # Only Manual mode offers hand reordering.
        self._move_up_button.setEnabled(manual)
        self._move_down_button.setEnabled(manual)
        if manual:
            return  # nothing to reorder; the current order stands
        self._apply_sort()
        self._rebuild()
        self.changed.emit()  # a preset rewrites the saved order — mark it an edit

    def _move_selected(self, delta: int) -> None:
        """Swap the selected advantage with its neighbour in the model list.

        Only meaningful in Manual mode; this mutates ``Character.advantages`` so
        the hand order persists through ``to_dict``.
        """

        selected = self._selected
        if selected is None:
            return
        advantages = self._character.advantages
        index = next((i for i, a in enumerate(advantages) if a is selected), None)
        if index is None:
            return
        target = index + delta
        if not 0 <= target < len(advantages):
            return
        advantages[index], advantages[target] = advantages[target], advantages[index]
        self._rebuild()
        self.changed.emit()

    # -- selection tracking across panels ------------------------------------

    def _on_selection_changed(self, table: _AutoHeightTable) -> None:
        if self._syncing_selection:
            return
        rows = {index.row() for index in table.selectedIndexes()}
        if not rows:
            return
        self._selected = self._selection_at(table, next(iter(rows)))
        # Only one row highlights at a time, so clear the sibling panels.
        self._syncing_selection = True
        for other in self._tables:
            if other is not table:
                other.clearSelection()
        self._syncing_selection = False

    def _selection_at(self, table: _AutoHeightTable, row: int) -> AdvantageSelection | None:
        for ref_table, ref_row, selection in self._row_refs:
            if ref_table is table and ref_row == row:
                return selection
        return None

    def _restore_selection(self) -> None:
        """Re-highlight the tracked advantage after a rebuild moved its row."""

        if self._selected is None:
            return
        for table, row, selection in self._row_refs:
            if selection is self._selected:
                self._syncing_selection = True
                table.selectRow(row)
                self._syncing_selection = False
                return
        self._selected = None

    # -- responsive panel count ---------------------------------------------

    def _available_width(self) -> int:
        """The width the panels have to share, net of the section's margins."""

        margins = self.layout().contentsMargins()
        return self.width() - margins.left() - margins.right()

    def _min_col_width(self) -> int:
        """Narrowest a panel may get before a row would clip.

        Driven by the widest Name and Type text actually present (a longer
        advantage raises it, forcing fewer panels) plus a readable Description
        minimum.
        """

        fm = self.fontMetrics()
        name_width = 0
        type_width = 0
        for selection in self._character.advantages:
            advantage = self._advantages_by_name.get(selection.name)
            ranked = bool(advantage and advantage.ranked)
            text = f"{selection.name} {selection.rank}" if ranked else selection.name
            subject = self._parameter_display(selection)
            if subject:
                text = f"{text} ({subject})"
            name_width = max(name_width, fm.horizontalAdvance(text))
            types = ", ".join(advantage.types) if advantage else ""
            type_width = max(type_width, fm.horizontalAdvance(types))
        return (
            name_width + NAME_PADDING + type_width + TYPE_PADDING + MIN_DESC_WIDTH + FRAME_PADDING
        )

    def _apply_picker_mode(self, narrow: bool) -> None:
        """Lay the picker out on one or two rows.

        Wide: the subject/rank/Add/Remove controls sit to the right of the combo on a
        single row. Narrow: they drop to a second row so the combo box spans the full
        block width and can show the advantage name. A no-op when the mode is unchanged.
        """
        if narrow == self._picker_narrow:
            return
        self._picker_narrow = narrow
        self._picker_row1.removeWidget(self._advantage_controls)
        while self._picker_row2.count():
            self._picker_row2.takeAt(0)
        if narrow:
            self._picker_row2.addStretch(1)
            self._picker_row2.addWidget(self._advantage_controls)
        else:
            self._picker_row1.addWidget(self._advantage_controls)

    def _picker_prefers_narrow(self) -> bool:
        """Whether the combo would be squeezed below :data:`PICKER_COMBO_MIN` on one row."""
        return (
            self._available_width() - self._advantage_controls.sizeHint().width() < PICKER_COMBO_MIN
        )

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802 - Qt override
        super().resizeEvent(event)
        self._apply_picker_mode(self._picker_prefers_narrow())
        count = column_count(
            self._available_width(),
            self._min_col_width(),
            TABLE_SPACING,
            len(self._character.advantages),
            self._column_count,
            COLUMN_HYSTERESIS,
        )
        if count != self._column_count:
            self._rebuild()

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt override
        """Report a *single-column* minimum so the block can shrink to one panel.

        When the block is wide it fans the advantages across several side-by-side
        tables; left alone, that inflates the section's minimum to the full
        multi-column width, which then pins the whole page (and window) wide and
        clips the trailing panels. Capping the reported minimum at one column's
        width lets the block — and the window — narrow, at which point
        :meth:`resizeEvent` rebuilds to fewer columns and everything reconciles.
        """
        hint = super().minimumSizeHint()
        return QSize(min(hint.width(), self._min_col_width()), hint.height())

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
        """Show and populate the subject input for the picker's current advantage.

        Data-driven from the advantage's :class:`ParameterSpec`: a ``"choice"`` shows
        the combo (populated from the spec's options), a ``"text"`` shows the line
        edit, and an advantage that takes no subject hides both. No-op while locked —
        :meth:`set_locked` owns visibility then.
        """

        if self._locked:
            return
        spec = advantage.parameter if advantage else None
        if spec is None:
            self._advantage_param.setVisible(False)
            self._advantage_param_text.setVisible(False)
            return
        if spec.kind == "choice":
            self._populate_choice_combo(self._advantage_param, spec)
            self._advantage_param.setVisible(True)
            self._advantage_param_text.setVisible(False)
        else:
            self._advantage_param_text.setPlaceholderText(spec.label)
            self._advantage_param_text.setVisible(True)
            self._advantage_param.setVisible(False)

    def _parameter_options(self, spec: ParameterSpec) -> list[tuple[str, str]]:
        """Resolve a choice spec to ``(stored value, display label)`` pairs.

        A dynamic ``options_from`` source draws from the live build —
        ``"skills"``/``"abilities"`` from the game data (abilities store their key but
        display their name), ``"powers"`` from the character's own powers. When the
        spec *also* lists ``options``, those restrict the source to that subset in the
        given order (e.g. Alternate Initiative offers only INT/AWE/PRE, not every
        ability). Without a source, a fixed ``options`` list maps each value to itself.
        """

        if spec.options_from == "skills":
            source = [(s.name, s.name) for s in self._data.skills]
        elif spec.options_from == "abilities":
            source = [(a.key, a.name) for a in self._data.abilities]
        elif spec.options_from == "powers":
            source = [(name, name) for name in self._power_names()]
        else:
            return [(option, option) for option in spec.options]
        if spec.options:
            labels = dict(source)
            return [(value, labels.get(value, value)) for value in spec.options]
        return source

    def _power_names(self) -> list[str]:
        """Every named leaf power on the character, descending array/linked groups."""

        names: list[str] = []

        def walk(nodes) -> None:
            for node in nodes:
                if isinstance(node, PowerGroup):
                    walk(node.children)
                elif node.name:
                    names.append(node.name)

        walk(self._character.powers)
        return names

    def _populate_choice_combo(self, combo: QComboBox, spec: ParameterSpec) -> None:
        combo.clear()
        for value, label in self._parameter_options(spec):
            combo.addItem(label, value)

    def _read_parameter_value(
        self, spec: ParameterSpec, combo: QComboBox | None, line: QLineEdit | None
    ) -> str:
        """The chosen subject from whichever widget the spec uses (``""`` if none)."""

        if spec.kind == "choice" and combo is not None:
            data = combo.currentData()
            return data if data is not None else ""
        if line is not None:
            return line.text().strip()
        return ""

    def _apply_parameter_value(
        self, spec: ParameterSpec, combo: QComboBox | None, line: QLineEdit | None, value: str
    ) -> None:
        """Preselect *value* in the spec's widget (for the edit dialog's initial state)."""

        if spec.kind == "choice" and combo is not None:
            index = combo.findData(value)
            combo.setCurrentIndex(index if index >= 0 else 0)
        elif line is not None:
            line.setText(value)

    def _parameter_display(self, selection: AdvantageSelection) -> str:
        """The subject as shown in a row — an ability key resolves to its name."""

        advantage = self._advantages_by_name.get(selection.name)
        spec = advantage.parameter if advantage else None
        if spec is not None and spec.options_from == "abilities":
            return self._ability_names.get(selection.parameter, selection.parameter)
        return selection.parameter

    def refresh_power_options(self) -> None:
        """Re-populate the picker combo when it lists the character's powers.

        Subscribed to the powers block's change topic so a newly built (or removed)
        power shows up in a ``optionsFrom: "powers"`` advantage's dropdown. A no-op
        for any other advantage or while locked.
        """

        if self._locked:
            return
        advantage = self._advantage_combo.currentData()
        spec = advantage.parameter if advantage else None
        if spec is None or spec.kind != "choice" or spec.options_from != "powers":
            return
        current = self._advantage_param.currentData()
        self._populate_choice_combo(self._advantage_param, spec)
        index = self._advantage_param.findData(current)
        if index >= 0:
            self._advantage_param.setCurrentIndex(index)

    def _edit_row(self, table: _AutoHeightTable, row: int) -> None:
        """Open the edit dialog for the double-clicked row's advantage."""

        selection = self._selection_at(table, row)
        if selection is not None:
            self._edit_advantage(selection)

    def _edit_advantage(self, selection: AdvantageSelection) -> None:
        """Edit an existing advantage's rank and/or subject in place via a small dialog.

        Reuses the same parameter widgets as the picker (built fresh for the dialog).
        Disabled while locked. On accept, writes back to the ``AdvantageSelection`` and
        refreshes the panels, cost, and limits.
        """

        if self._locked:
            return
        advantage = self._advantages_by_name.get(selection.name)
        if advantage is None:
            return
        spec = advantage.parameter

        dialog = QDialog(self)
        dialog.setWindowTitle(f"Edit {selection.name}")
        form = QFormLayout(dialog)

        rank_spin = None
        if advantage.ranked:
            ceiling = max(self._rank_ceiling(advantage), selection.rank)
            rank_spin = make_spin_box(RANK_MIN, ceiling, guarded=False)
            rank_spin.setValue(selection.rank)
            form.addRow("Rank", rank_spin)

        param_combo: QComboBox | None = None
        param_line: QLineEdit | None = None
        if spec is not None:
            if spec.kind == "choice":
                param_combo = QComboBox()
                guard_wheel(param_combo)
                self._populate_choice_combo(param_combo, spec)
                self._apply_parameter_value(spec, param_combo, None, selection.parameter)
                form.addRow(spec.label or "Subject", param_combo)
            else:
                param_line = QLineEdit(selection.parameter)
                form.addRow(spec.label or "Subject", param_line)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)

        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        if rank_spin is not None:
            selection.rank = rank_spin.value()
        if spec is not None:
            selection.parameter = self._read_parameter_value(spec, param_combo, param_line)
        self._rebuild()
        self.refresh_cost()
        self.refresh_limits()
        self.changed.emit()

    def _add_advantage(self) -> None:
        advantage = self._advantage_combo.currentData()
        if advantage is None:
            return
        rank = self._advantage_rank.value() if advantage.ranked else 1
        spec = advantage.parameter
        parameter = (
            self._read_parameter_value(spec, self._advantage_param, self._advantage_param_text)
            if spec is not None
            else ""
        )
        # Enforce the shared Heroic-advantage budget as a hard limit on the add.
        if HEROIC_TYPE in advantage.types:
            budget = heroic_advantage_budget(self._character.power_level)
            prospective = heroic_advantage_ranks(self._character, self._data) + rank
            if prospective > budget:
                self._show_heroic_budget(prospective - rank, budget, blocked=True)
                return
        self._character.advantages.append(AdvantageSelection(advantage.name, rank, parameter))
        self._rebuild()
        self.refresh_cost()
        self.refresh_limits()
        self._sync_rank_enabled()
        self.changed.emit()

    def _remove_advantage(self) -> None:
        # Row → model mapping is no longer positional, so resolve the selected
        # rows back to their AdvantageSelection objects across every panel.
        selected: list[AdvantageSelection] = []
        for table in self._tables:
            for row in {index.row() for index in table.selectedIndexes()}:
                selection = self._selection_at(table, row)
                if selection is not None:
                    selected.append(selection)
        if not selected:
            return
        self._character.advantages = [
            a for a in self._character.advantages if not any(a is s for s in selected)
        ]
        if any(self._selected is s for s in selected):
            self._selected = None
        self._rebuild()
        self.refresh_cost()
        self.refresh_limits()
        self._sync_rank_enabled()
        self.changed.emit()

    def refresh_cost(self) -> None:
        """Re-title the block with its current PP subtotal (also driven by a homebrew
        cost-rate change, via ``cost-rates-changed``)."""
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
        """Hide the advantage picker and sort/move controls while locked; the
        panels are already read-only (and double-click editing is gated)."""
        self._locked = locked
        for widget in (
            self._advantage_combo,
            self._advantage_rank,
            self._advantage_add_button,
            self._advantage_remove_button,
            self._sort_label,
            self._sort_combo,
            self._move_up_button,
            self._move_down_button,
        ):
            widget.setVisible(not locked)
        if locked:
            self._advantage_param.setVisible(False)
            self._advantage_param_text.setVisible(False)
        else:
            # Restore the right subject input for the current advantage.
            self._sync_parameter(self._advantage_combo.currentData())

"""The advantages block: a combo-box picker plus the chosen advantages.

The chosen advantages live on the shared :class:`~mm_companion.core.character.Character`
and this block is a view over that list. They render across a variable number of
side-by-side panels whose count adapts to the block's width (see
:mod:`mm_companion.ui.sections.column_flow`), so a row is no longer positionally
1:1 with the model — each rendered row keeps a reference back to its backing
``AdvantageSelection`` (``_row_refs``, a
:class:`~mm_companion.ui.sections.row_table.RowIndex`). A sort dropdown reorders the
list (Name / Rank / Type permanently rewrite ``Character.advantages``; Manual leaves
it alone).

A row is **dragged** to a new place and **right-clicked** to remove — the shared
table-block gestures, out of :mod:`mm_companion.ui.sections.row_table`, in place of
the ▲/▼ and "Remove" buttons this block used to carry. Dragging works across the
panels, since they are one ordered list split for display; while a preset sort mode
is in force there is nothing to hand-order and the drag stands down.

Rank limits are enforced here from the rules layer: a ranked advantage's spin box is
capped at its own maximum (:func:`~mm_companion.core.rules.advantage_rank_cap` — the
fixed numbers and Improved Initiative's ``ceil(PL/2)``), and Heroic-type advantages
also draw from a shared per-character budget
(:func:`~mm_companion.core.rules.heroic_advantage_budget`) shown beside the picker.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
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
    heroic_advantage_ranks_free,
)
from mm_companion.ui import theme
from mm_companion.ui.sections.column_flow import ColumnFlowPanels, even_split
from mm_companion.ui.sections.row_table import (
    SORT_MANUAL,
    AutoHeightTable,
    RowIndex,
    RowReorder,
    SortControl,
    install_row_menu,
    move_within,
    remove_contributor,
)
from mm_companion.ui.sections.stat_table import CONDITION_TINT
from mm_companion.ui.sections.titled_section import TitledSection
from mm_companion.ui.wheel_guard import guard_wheel
from mm_companion.ui.widgets import make_spin_box, tinted_style

RANK_MIN, RANK_MAX = 1, 20

# Sort modes for the chosen-advantages list (UI-only state, not persisted).
# SORT_MANUAL is the shared one — see row_table, which only lets rows be dragged
# while it is in force.
SORT_NAME, SORT_RANK, SORT_TYPE = "name", "rank", "type"

#: This block's drag payload. Its own format, so no other block's rows can land here.
ROW_MIME = "application/x-mm-rows-advantages"

# Rough widths used to decide how many panels fit without clipping a row. The Name
# and Type columns size to content; the Description wraps but still wants a readable
# minimum. Both minimums are theme metrics — a denser preset narrows them — while the
# paddings below stay fixed heuristics.


def picker_combo_min() -> int:
    """Room the picker combo insists on before the controls wrap to their own row."""
    return int(theme.metric("column.advantage.combo"))


def min_desc_width() -> int:
    """Readable minimum for the wrapping Description column."""
    return int(theme.metric("column.advantage.desc"))


NAME_PADDING = 24
TYPE_PADDING = 24
FRAME_PADDING = 24


class AdvantagesSection(ColumnFlowPanels, TitledSection):
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

        # The subject/rank/Add controls travel together as one widget so they can
        # move between the picker's two rows in one step (see _apply_picker_mode).
        # There is no "Remove" beside them: removing is a thing done *to a row*, so
        # it lives on that row's right-click menu.
        self._advantage_controls = QWidget()
        controls_row = QHBoxLayout(self._advantage_controls)
        controls_row.setContentsMargins(0, 0, 0, 0)
        for widget in (
            self._advantage_param,
            self._advantage_param_text,
            self._advantage_rank,
            self._advantage_add_button,
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

        # Sort control (hidden while locked). Hand ordering is the drag on the rows
        # themselves, so there is nothing beside it.
        controls = QHBoxLayout()
        self._sort = SortControl(
            [
                (SORT_MANUAL, "Manual"),
                (SORT_NAME, "Name (A–Z)"),
                (SORT_RANK, "Rank (high→low)"),
                (SORT_TYPE, "Type"),
            ]
        )
        self._sort.sortChanged.connect(self._on_sort_changed)
        # The combo itself, for anything that drives the block by picking a mode.
        self._sort_combo = self._sort.combo
        controls.addWidget(self._sort)
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
        self._row_refs = RowIndex()
        # One controller for every panel: they are one ordered list split for
        # display, so a row has to be draggable from one into another.
        self._reorder = RowReorder(
            ROW_MIME,
            self._row_refs,
            lambda source, target, before: self.move_advantage(source.key, target.key, before),
            enabled=lambda: not self._locked and self._sort.reorder_enabled(),
        )
        self._init_flow_panels(outer)
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
                item.setForeground(QBrush(QColor(theme.color(CONDITION_TINT))))
                item.setToolTip(f"Debilitated — {selection.name} is effectively lost")
            else:
                item.setData(Qt.ItemDataRole.ForegroundRole, None)
                item.setToolTip("")

    # -- panel construction / rebuild ---------------------------------------

    def _make_table(self) -> AutoHeightTable:
        # word_wrap: the Description column wraps, so its rows have to be
        # re-measured whenever the panel's width changes.
        table = AutoHeightTable(0, 3, word_wrap=True)
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
        # A panel built later by _ensure_tables is wired here too, so every panel is
        # both a source of dragged rows and a target for them.
        self._reorder.attach(table)
        install_row_menu(table, remove_contributor(self._remove_label, self._remove_row))
        guard_wheel(table)
        return table

    def _rebuild(self) -> None:
        """Re-render every panel from the ordered advantage list.

        Called on add/remove, on a sort or manual move, and when the panel count
        changes on resize.
        """

        self._row_refs.clear()
        selections = self._character.advantages
        count = self._flow_column_count()
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

    def _render_row(self, table: AutoHeightTable, selection: AdvantageSelection) -> None:
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
        self._row_refs.add(table, row, selection)

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

    def _on_sort_changed(self, mode: str) -> None:
        self._sort_mode = mode
        # Only Manual mode offers hand reordering, which RowReorder asks about at
        # gesture time (see the `enabled` predicate it was built with).
        if mode == SORT_MANUAL:
            return  # nothing to reorder; the current order stands
        self._apply_sort()
        self._rebuild()
        self.changed.emit()  # a preset rewrites the saved order — mark it an edit

    def move_advantage(
        self, source: AdvantageSelection, target: AdvantageSelection, before: bool
    ) -> None:
        """Move *source* so it sits either side of *target* in the model list.

        The seam a dragged row lands on, and the headless-testable one: it takes
        the two model objects rather than a drop position, so a test states what it
        means without synthesising a drag. Mutates ``Character.advantages``, so the
        hand order persists through ``to_dict``.
        """

        advantages = self._character.advantages
        from_index = next((i for i, a in enumerate(advantages) if a is source), None)
        to_index = next((i for i, a in enumerate(advantages) if a is target), None)
        if from_index is None or to_index is None or source is target:
            return
        move_within(advantages, from_index, to_index if before else to_index + 1)
        self._rebuild()
        self.changed.emit()

    # -- selection tracking across panels ------------------------------------

    def _on_selection_changed(self, table: AutoHeightTable) -> None:
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

    def _selection_at(self, table: AutoHeightTable, row: int) -> AdvantageSelection | None:
        key = self._row_refs.key_at(table, row)
        return key if isinstance(key, AdvantageSelection) else None

    def _restore_selection(self) -> None:
        """Re-highlight the tracked advantage after a rebuild moved its row."""

        if self._selected is None:
            return
        entry = self._row_refs.find(self._selected)
        if entry is None:
            self._selected = None
            return
        self._syncing_selection = True
        entry.table.selectRow(entry.row)
        self._syncing_selection = False

    # -- responsive panel count ---------------------------------------------

    def _flow_item_count(self) -> int:
        return len(self._character.advantages)

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
            name_width + NAME_PADDING + type_width + TYPE_PADDING + min_desc_width() + FRAME_PADDING
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
        """Whether the combo would be squeezed below :func:`picker_combo_min` on one row."""
        return (
            self._available_width() - self._advantage_controls.sizeHint().width()
            < picker_combo_min()
        )

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802 - Qt override
        super().resizeEvent(event)
        self._apply_picker_mode(self._picker_prefers_narrow())
        self._sync_column_count()

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
            remaining = heroic_advantage_ranks_free(self._character, self._data)
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

    def _edit_row(self, table: AutoHeightTable, row: int) -> None:
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
        if HEROIC_TYPE in advantage.types and rank > heroic_advantage_ranks_free(
            self._character, self._data
        ):
            spent = heroic_advantage_ranks(self._character, self._data)
            self._show_heroic_budget(
                spent, heroic_advantage_budget(self._character.power_level), blocked=True
            )
            return
        self._character.advantages.append(AdvantageSelection(advantage.name, rank, parameter))
        self._rebuild()
        self.refresh_cost()
        self.refresh_limits()
        self._sync_rank_enabled()
        self.changed.emit()

    def _remove_label(self, table: AutoHeightTable, row: int) -> str | None:
        """How the row menu words its Remove entry — ``None`` while locked."""

        if self._locked:
            return None
        selection = self._selection_at(table, row)
        return None if selection is None else f"Remove {selection.name}"

    def _remove_row(self, table: AutoHeightTable, row: int) -> None:
        selection = self._selection_at(table, row)
        if selection is not None:
            self.remove_advantage(selection)

    def remove_advantage(self, selection: AdvantageSelection) -> None:
        """Take *selection* off the character. The seam the row menu lands on.

        By identity, not by name: the same advantage can be bought twice (two
        Benefits, two Equipment ranks), and the row that was right-clicked means
        that one.
        """

        remaining = [a for a in self._character.advantages if a is not selection]
        if len(remaining) == len(self._character.advantages):
            return
        self._character.advantages = remaining
        if self._selected is selection:
            self._selected = None
        self._rebuild()
        self.refresh_cost()
        self.refresh_limits()
        self._sync_rank_enabled()
        self.changed.emit()

    def refresh_cost(self) -> None:
        """Re-title the block with its current PP subtotal (also driven by a homebrew
        cost-rate change, via ``cost-rates-changed``)."""
        self.set_priced_title("Advantages", advantage_points_spent(self._character, self._data))

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
        self._heroic_label.setStyleSheet(
            tinted_style(CONDITION_TINT, bold=False) if blocked else ""
        )

    def set_locked(self, locked: bool) -> None:
        """Hide the advantage picker and the sort control while locked.

        The panels are already read-only (double-click editing is gated), and the
        two row gestures ask ``_locked`` themselves: the drag through the predicate
        :class:`RowReorder` was built with, the Remove entry through
        :meth:`_remove_label`, which words nothing while locked.
        """
        self._locked = locked
        for widget in (
            self._advantage_combo,
            self._advantage_rank,
            self._advantage_add_button,
            self._sort,
        ):
            widget.setVisible(not locked)
        if locked:
            self._advantage_param.setVisible(False)
            self._advantage_param_text.setVisible(False)
        else:
            # Restore the right subject input for the current advantage.
            self._sync_parameter(self._advantage_combo.currentData())

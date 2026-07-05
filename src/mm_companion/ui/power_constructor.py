"""The Power Constructor: a brick-builder for assembling powers.

A standalone top-level window. The left side is a palette of draggable *bricks*
grouped into three tabs — **Effects**, **Extras**, **Flaws** — built by iterating
the loaded :class:`~mm_companion.core.data_loader.GameData`. The right side is the
power being built: a name, a description, a live power-point cost, and a canvas of
effect *cards*.

Interaction (all drag-and-drop):

- Drag an **Effect** brick onto the canvas → a new :class:`EffectCard` appears
  (one :class:`~mm_companion.core.powers.PowerEffectInstance`).
- Drag an **Extra** or **Flaw** brick onto a specific card → it attaches there as a
  chip (a modifier modifies one effect, per the M&M model).
- Once a second effect is on the canvas a :class:`PowerModeBar` appears, switching
  the power between **Independent**, **Linked**, and **Array** structures (§4). The
  structure lives on the :class:`~mm_companion.core.powers.Power`; the cards badge
  their role and the total recomputes from it (an array pays its base in full plus
  a flat point per alternate) — the modifier chips aren't touched.

The window owns a single :class:`~mm_companion.core.powers.Power` and mutates it;
costs always come from :mod:`mm_companion.core.rules`, never computed inline. A
**Save Power** button hands the finished power to the host section via
:attr:`PowerConstructorWindow.powerSaved` and closes the window.

Given the character's Power Level, the editor flags a power that breaks a PL cap
(:func:`~mm_companion.core.rules.power_pl_violations`) with a live warning. Whether
that merely warns or actually blocks the save is a single app-wide switch —
:func:`~mm_companion.core.storage.pl_enforcement` — so it can move to a settings
toggle later without touching this window.
"""

from __future__ import annotations

from PySide6.QtCore import QMimeData, Qt, Signal
from PySide6.QtGui import QDrag
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from mm_companion.core import storage
from mm_companion.core.character import Character
from mm_companion.core.data_loader import GameData, Modifier, load_game_data
from mm_companion.core.powers import (
    STRUCTURE_ARRAY,
    STRUCTURE_INDEPENDENT,
    STRUCTURE_LINKED,
    ModifierSelection,
    Power,
    PowerEffectInstance,
)
from mm_companion.core.rules import (
    TRAIT_CATEGORIES,
    array_alternate_cost,
    array_base_index,
    effect_allocation_used,
    effect_cost_formula,
    effect_effective_rank,
    effect_stat_rows,
    effect_total_cost,
    power_allocation_violations,
    power_pl_violations,
    power_total_cost,
)
from mm_companion.ui.flow_layout import FlowLayout
from mm_companion.ui.wheel_guard import guard_wheel
from mm_companion.ui.widgets import make_spin_box

# Custom drag payload formats: the record id travels as the mime data.
EFFECT_MIME = "application/x-mm-effect"
MODIFIER_MIME = "application/x-mm-modifier"


def _mime_id(mime: QMimeData, fmt: str) -> str:
    """Decode the record id carried by a drag in the given format."""
    return bytes(mime.data(fmt)).decode("utf-8")


class BrickWidget(QFrame):
    """A draggable palette brick: a name and its cost text, carrying a record id.

    On drag it starts a :class:`QDrag` whose mime data holds the record id in the
    given format (``EFFECT_MIME`` or ``MODIFIER_MIME``), so drop targets know both
    what kind of brick it is and which record it refers to.
    """

    def __init__(
        self, title: str, subtitle: str, mime: str, payload: str, *, flat: bool = False
    ) -> None:
        super().__init__()
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self._mime = mime
        self._payload = payload
        self._press_pos = None
        # The palette search box matches on the name only — the cost subtitle
        # ("1 per rank", …) is the same across most bricks and would swamp
        # single-letter queries with matches.
        self.search_key = title.lower()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 5, 8, 5)
        layout.setSpacing(1)

        header = QHBoxLayout()
        header.setSpacing(4)
        name = QLabel(title)
        name.setStyleSheet("font-weight: bold;")
        header.addWidget(name)
        header.addStretch()
        if flat:
            # A flat modifier costs a one-time add/subtract rather than per rank.
            badge = QLabel("flat")
            badge.setStyleSheet(
                "background: #555; color: white; border-radius: 4px; padding: 0 4px;"
            )
            header.addWidget(badge)
        layout.addLayout(header)

        if subtitle:
            cost = QLabel(subtitle)
            cost.setEnabled(False)
            layout.addWidget(cost)

    def mousePressEvent(self, event) -> None:  # noqa: N802 (Qt override)
        if event.button() == Qt.MouseButton.LeftButton:
            self._press_pos = event.position().toPoint()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 (Qt override)
        if self._press_pos is None:
            return
        moved = (event.position().toPoint() - self._press_pos).manhattanLength()
        if moved < QApplication.startDragDistance():
            return
        drag = QDrag(self)
        mime = QMimeData()
        mime.setData(self._mime, self._payload.encode("utf-8"))
        drag.setMimeData(mime)
        drag.exec(Qt.DropAction.CopyAction)
        self._press_pos = None


class ModifierChip(QFrame):
    """An attached extra/flaw shown on an effect card, with a remove button.

    A ``ranked`` modifier (bought in its own ranks, e.g. Accurate) also carries a
    rank spin box; changing it writes back to the :class:`ModifierSelection` and
    emits :attr:`changed` so the card can recompute its cost.
    """

    removeRequested = Signal(object)
    changed = Signal()

    def __init__(self, modifier: Modifier, selection: ModifierSelection) -> None:
        super().__init__()
        self.selection = selection
        self.setFrameShape(QFrame.Shape.StyledPanel)
        tint = "#2e5e33" if modifier.category == "extra" else "#5e2e2e"
        self.setStyleSheet(f"ModifierChip {{ background: {tint}; border-radius: 6px; }}")

        outer = QVBoxLayout(self)
        outer.setContentsMargins(6, 2, 3, 2)
        outer.setSpacing(2)
        header = QHBoxLayout()
        header.setSpacing(4)
        header.addWidget(QLabel(modifier.name))
        if modifier.ranked:
            rank = make_spin_box(1, 30, value=selection.rank, buttons=False, max_width=44)
            rank.setPrefix("×")
            rank.valueChanged.connect(self._on_rank_changed)
            header.addWidget(rank)
        remove = QPushButton("✕")
        remove.setFlat(True)
        remove.setFixedWidth(18)
        remove.setCursor(Qt.CursorShape.PointingHandCursor)
        remove.clicked.connect(lambda: self.removeRequested.emit(self))
        header.addWidget(remove)
        outer.addLayout(header)

        # A few modifiers carry their own choices (Removable tier, Side Effect
        # backfire, a Triggered/Limited condition — see mm-powers-ui-design.md §4).
        # A choice with a cost (the tier, the always/on-failure toggle) feeds the cost
        # engine straight from the selection's config.
        if modifier.config_fields:
            outer.addLayout(self._build_config(modifier))

    def _build_config(self, modifier: Modifier) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(4)
        for cfg in modifier.config_fields:
            if cfg.type == "select":
                combo = QComboBox()
                for option in cfg.options:
                    combo.addItem(option.label, option.value)
                index = combo.findData(self.selection.config.get(cfg.key))
                combo.setCurrentIndex(index if index >= 0 else 0)
                guard_wheel(combo)
                if cfg.hint:
                    combo.setToolTip(cfg.hint)
                combo.currentIndexChanged.connect(
                    lambda _i, c=combo, k=cfg.key: self._on_config(k, c.currentData())
                )
                row.addWidget(combo)
            else:  # text
                edit = QLineEdit(self.selection.config.get(cfg.key, ""))
                edit.setPlaceholderText(cfg.label)
                if cfg.hint:
                    edit.setToolTip(cfg.hint)
                edit.textChanged.connect(lambda text, k=cfg.key: self._on_config(k, text))
                row.addWidget(edit)
        return row

    def _on_config(self, key: str, value) -> None:
        if value:
            self.selection.config[key] = value
        else:
            self.selection.config.pop(key, None)
        self.changed.emit()

    def _on_rank_changed(self, value: int) -> None:
        self.selection.rank = value
        self.changed.emit()


class ModifierGroup(QWidget):
    """A titled, vertically-stacked run of modifier chips, hidden while empty.

    An :class:`EffectCard` keeps one of these for extras and one for flaws; each
    reveals itself only once its first chip is added and hides again when its last
    chip is removed.
    """

    def __init__(self, title: str) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        header = QLabel(title)
        header.setStyleSheet("font-weight: bold;")
        layout.addWidget(header)
        chip_area = QWidget()
        self._chip_layout = FlowLayout(chip_area)
        layout.addWidget(chip_area)
        self.setVisible(False)

    def add_chip(self, chip: QWidget) -> None:
        self._chip_layout.addWidget(chip)
        self.setVisible(True)

    def remove_chip(self, chip: QWidget) -> None:
        self._chip_layout.removeWidget(chip)
        chip.setParent(None)
        chip.deleteLater()
        self.setVisible(self._chip_layout.count() > 0)


class EffectCard(QFrame):
    """One effect within the power: rank, attached modifier chips, and its cost.

    Accepts **modifier** drops (extras/flaws from the general palette attach here),
    and offers this effect's own effect-specific extras/flaws through a menu button.
    Writes rank/modifier changes straight to the shared :class:`PowerEffectInstance`
    and emits :attr:`changed` so the window can recompute the total.
    """

    changed = Signal()
    removeRequested = Signal(object)

    def __init__(self, instance: PowerEffectInstance, game_data: GameData) -> None:
        super().__init__()
        self.instance = instance
        self._data = game_data
        self._chips: list[ModifierChip] = []
        # Callables that refresh each Tier-4 allocation field's "used / rank" readout;
        # rebuilt with the config form and fired when the effect's rank changes.
        self._alloc_updaters: list = []
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setAcceptDrops(True)

        effect = self._effect()
        layout = QVBoxLayout(self)

        header = QHBoxLayout()
        name = QLabel(effect.name if effect else instance.effect_id)
        name.setStyleSheet("font-weight: bold;")
        header.addWidget(name)
        # A structure badge (Base / Alternate / Linked) the canvas drives; hidden
        # while the power is a single or independent-multi effect.
        self._role_badge = QLabel()
        self._role_badge.setVisible(False)
        header.addWidget(self._role_badge)
        header.addStretch()
        header.addWidget(QLabel("Rank"))
        self._rank = make_spin_box(1, 30, value=instance.rank, buttons=False, max_width=44)
        self._rank.valueChanged.connect(self._on_rank_changed)
        header.addWidget(self._rank)
        remove = QPushButton("✕")
        remove.setFixedWidth(24)
        remove.setToolTip("Remove this effect")
        remove.clicked.connect(lambda: self.removeRequested.emit(self))
        header.addWidget(remove)
        layout.addLayout(header)

        # A configurable trait booster (Enhanced Trait) picks which trait it raises;
        # a fixed one (Protection) has no picker. Shown just under the header so the
        # target reads before the qualities below.
        target_picker = self._build_target_picker(effect)
        if target_picker is not None:
            layout.addWidget(target_picker)

        # The config form is rebuilt on demand: attaching Extra Condition upgrades
        # Affliction's degree pickers from single-select to multiselect.
        self._config_host = QWidget()
        self._config_layout = QVBoxLayout(self._config_host)
        self._config_layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._config_host)
        self._populate_config_form()

        self._extras_group = ModifierGroup("Extras")
        self._flaws_group = ModifierGroup("Flaws")
        layout.addWidget(self._extras_group)
        layout.addWidget(self._flaws_group)

        self._hint = QLabel("Drag extras or flaws here")
        self._hint.setEnabled(False)
        layout.addWidget(self._hint)

        # Effect-specific extras/flaws (from effect_modifiers.json) can't be dragged
        # from the general palette — this effect offers its own through a menu button.
        self._specific_button = QToolButton()
        self._specific_button.setText("＋ Effect-specific extra / flaw")
        self._specific_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self._specific_menu = QMenu(self._specific_button)
        self._specific_menu.setToolTipsVisible(True)
        self._specific_menu.aboutToShow.connect(self._populate_specific_menu)
        self._specific_button.setMenu(self._specific_menu)
        if not self._data.effect_modifiers.get(instance.effect_id):
            self._specific_button.hide()  # this effect has no specific modifiers
        layout.addWidget(self._specific_button)

        self._cost = QLabel()
        self._cost.setAlignment(Qt.AlignmentFlag.AlignRight)
        layout.addWidget(self._cost)

        # When editing an existing power the instance already carries its extras and
        # flaws — render a chip for each (the config form built above already reads
        # them, e.g. an attached Extra Condition, so only the chips need seeding).
        self._seed_modifier_chips()
        self._refresh_cost()

    # -- effect-specific qualities (config) -------------------------------
    def _has_extra(self, modifier_id: str) -> bool:
        return any(sel.modifier_id == modifier_id for sel in self.instance.extras)

    def _effective_type(self, field) -> str:
        """A field's live input type — ``select`` is upgraded to ``multiselect`` only
        while its ``multiselect_with`` extra is attached (e.g. Extra Condition)."""
        if field.multiselect_with and self._has_extra(field.multiselect_with):
            return "multiselect"
        return field.type

    def _populate_config_form(self) -> None:
        """(Re)build the config form to match the effect's current modifiers.

        Called on construction and whenever a modifier is attached/removed, so a
        field can switch between single- and multi-select as its gate comes and goes.
        """
        while self._config_layout.count():  # clear any previous form
            widget = self._config_layout.takeAt(0).widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
        self._alloc_updaters = []  # rebuilt below alongside the fresh widgets

        effect = self._effect()
        if effect is None or not effect.config_fields:
            return
        form_host = QWidget()
        form = QFormLayout(form_host)
        form.setContentsMargins(0, 0, 0, 0)
        for field in effect.config_fields:
            if field.hidden_with and self._has_extra(field.hidden_with):
                # A gating extra (e.g. Variable Conditions) defers this choice to
                # use-time — show a note in place of the input instead of the widget.
                note = QLabel("chosen when used")
                note.setEnabled(False)
                form.addRow(field.label, note)
                continue
            field_type = self._effective_type(field)
            self._normalize_config(field.key, field_type)
            widget = self._config_widget(field, field_type)
            if field.hint:
                widget.setToolTip(field.hint)
            form.addRow(field.label, widget)
        self._config_layout.addWidget(form_host)

    # Config field types whose stored value is a list rather than a scalar.
    _LIST_TYPES = ("multiselect", "allocation", "repeatable")

    def _normalize_config(self, key: str, field_type: str) -> None:
        """Keep the stored value shaped like the current input type: a list for the
        list-valued types, a single value otherwise (collapsing a list to its first)."""
        value = self.instance.config.get(key)
        if value is None:
            return
        if field_type in self._LIST_TYPES and not isinstance(value, list):
            # A former single-select collapsing into a multiselect keeps its value;
            # a non-list under allocation/repeatable is malformed, so reset it.
            self.instance.config[key] = [value] if field_type == "multiselect" else []
        elif field_type not in self._LIST_TYPES and isinstance(value, list):
            if value:
                self.instance.config[key] = value[0]
            else:
                self.instance.config.pop(key, None)

    def _config_widget(self, field, field_type: str) -> QWidget:
        if field_type == "text":
            edit = QLineEdit(self.instance.config.get(field.key, ""))
            edit.textChanged.connect(lambda text, k=field.key: self._on_config_changed(k, text))
            return edit
        if field_type == "checkbox":
            return self._checkbox_widget(field)
        if field_type == "allocation":
            return self._allocation_widget(field)
        if field_type == "repeatable":
            return self._repeatable_widget(field)
        if field_type == "multiselect":
            return self._multiselect_widget(field)
        combo = QComboBox()
        combo.addItem("—", "")  # the unset choice
        for option in field.options:
            combo.addItem(option.label, option.value)
        index = combo.findData(self.instance.config.get(field.key, ""))
        combo.setCurrentIndex(index if index >= 0 else 0)
        guard_wheel(combo)
        combo.currentIndexChanged.connect(
            lambda _i, c=combo, k=field.key: self._on_config_changed(k, c.currentData())
        )
        return combo

    def _multiselect_widget(self, field) -> QWidget:
        """A wrapping row of check boxes — multiple same-category choices at once."""
        container = QWidget()
        flow = FlowLayout(container)
        chosen = self.instance.config.get(field.key, [])
        pairs: list[tuple[QCheckBox, str]] = []
        for option in field.options:
            box = QCheckBox(option.label)
            box.setChecked(option.value in chosen)
            flow.addWidget(box)
            pairs.append((box, option.value))
        for box, _ in pairs:
            box.toggled.connect(
                lambda _c, k=field.key, ps=pairs: self._on_config_changed(
                    k, [value for cb, value in ps if cb.isChecked()]
                )
            )
        return container

    def _allocation_widget(self, field) -> QWidget:
        """A Tier-4 rank-allocation checklist (Enhanced Senses/Movement, Comprehend).

        Each option is a check box; a tiered option (``2/4/6``) adds a small combo to
        pick which tier. A live "Allocated N / rank" readout under the list turns red
        when the selections spend more than the effect's rank. Selections are stored
        in ``instance.config[key]`` as ``[{"id", "tier"}, ...]``.
        """

        container = QWidget()
        outer = QVBoxLayout(container)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(3)
        grid_host = QWidget()
        flow = FlowLayout(grid_host)
        outer.addWidget(grid_host)
        total = QLabel()
        outer.addWidget(total)

        chosen = {
            e["id"]: int(e.get("tier", 1))
            for e in self.instance.config.get(field.key, [])
            if isinstance(e, dict) and "id" in e
        }
        controls: list[tuple] = []

        def update_total() -> None:
            used = effect_allocation_used(self.instance, self._data)
            rank = self._rank.value()
            total.setText(f"Allocated {used} / {rank} ranks")
            total.setStyleSheet("color: #d15b5b; font-weight: bold;" if used > rank else "")

        def commit() -> None:
            new = []
            for option, box, combo in controls:
                if box.isChecked():
                    tier = int(combo.currentData()) if combo is not None else 1
                    new.append({"id": option.id, "tier": tier})
            self.instance.config[field.key] = new
            update_total()
            self.changed.emit()

        for option in field.alloc_options:
            row = QWidget()
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.setSpacing(3)
            label = option.label + (f" ({option.per_note})" if option.per_note else "")
            box = QCheckBox(label)
            box.setChecked(option.id in chosen)
            row_layout.addWidget(box)
            combo = None
            if len(option.tiers) > 1:
                combo = QComboBox()
                for index, cost in enumerate(option.tiers, start=1):
                    combo.addItem(f"{cost} ranks", index)
                combo.setCurrentIndex(min(max(chosen.get(option.id, 1), 1), len(option.tiers)) - 1)
                combo.setEnabled(box.isChecked())
                guard_wheel(combo)
                combo.currentIndexChanged.connect(lambda _i: commit())
                row_layout.addWidget(combo)
            else:
                cost = QLabel(f"({option.tiers[0]})")
                cost.setEnabled(False)
                row_layout.addWidget(cost)
            controls.append((option, box, combo))

            def on_toggle(checked: bool, c=combo) -> None:
                if c is not None:
                    c.setEnabled(checked)
                commit()

            box.toggled.connect(on_toggle)
            flow.addWidget(row)

        self._alloc_updaters.append(update_total)
        update_total()
        return container

    def _repeatable_widget(self, field) -> QWidget:
        """A Tier-4 variable-length row list (Immunity scopes, Features).

        Each row has one widget per :class:`RepeatableColumn` (a line edit for text, a
        spin for an ``int`` rank) plus a remove button; an "Add" button appends a row.
        A "used / rank" readout meters the rows against the effect's rank (summed ranks
        for Immunity, one per row for Feature). Rows are stored in
        ``instance.config[key]`` as a list of ``{column_key: value}`` dicts.
        """

        container = QWidget()
        outer = QVBoxLayout(container)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(2)
        rows_host = QWidget()
        rows_layout = QVBoxLayout(rows_host)
        rows_layout.setContentsMargins(0, 0, 0, 0)
        rows_layout.setSpacing(2)
        outer.addWidget(rows_host)
        total = QLabel()

        existing = self.instance.config.get(field.key)
        if not isinstance(existing, list):
            existing = []
        row_widgets: list[tuple[QWidget, dict]] = []

        def update_total() -> None:
            used = effect_allocation_used(self.instance, self._data)
            rank = self._rank.value()
            total.setText(f"Allocated {used} / {rank} ranks")
            total.setStyleSheet("color: #d15b5b; font-weight: bold;" if used > rank else "")

        def commit() -> None:
            rows = []
            for _widget, cells in row_widgets:
                row = {}
                for column in field.columns:
                    cell = cells[column.key]
                    row[column.key] = cell.value() if column.type == "int" else cell.text().strip()
                if any(str(v).strip() for v in row.values()):
                    rows.append(row)
            self.instance.config[field.key] = rows
            update_total()
            self.changed.emit()

        def add_row(initial: dict | None = None) -> None:
            initial = initial or {}
            row = QWidget()
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.setSpacing(3)
            cells: dict = {}
            for column in field.columns:
                if column.type == "int":
                    cell = make_spin_box(
                        0,
                        30,
                        value=int(initial.get(column.key, 0) or 0),
                        buttons=False,
                        max_width=48,
                    )
                    cell.valueChanged.connect(lambda _v: commit())
                    row_layout.addWidget(cell)
                else:
                    cell = QLineEdit(str(initial.get(column.key, "")))
                    cell.setPlaceholderText(column.label)
                    cell.textChanged.connect(lambda _t: commit())
                    row_layout.addWidget(cell, 1)
                cells[column.key] = cell
            remove = QPushButton("✕")
            remove.setFlat(True)
            remove.setFixedWidth(20)
            row_layout.addWidget(remove)
            rows_layout.addWidget(row)
            entry = (row, cells)
            row_widgets.append(entry)

            def do_remove(_checked: bool = False) -> None:
                if entry in row_widgets:
                    row_widgets.remove(entry)
                row.setParent(None)
                row.deleteLater()
                commit()

            remove.clicked.connect(do_remove)

        for row_data in existing:
            if isinstance(row_data, dict):
                add_row(row_data)

        add_button = QPushButton("＋ Add")
        add_button.clicked.connect(lambda: add_row())
        outer.addWidget(add_button)
        outer.addWidget(total)

        self._alloc_updaters.append(update_total)
        update_total()
        return container

    def _checkbox_widget(self, field) -> QWidget:
        """A boolean toggle. When ``toggles`` names an extra it attaches/detaches that
        modifier (e.g. Damage's Strength-Based); otherwise it stores a bool in config."""
        box = QCheckBox()
        if field.toggles:
            box.setChecked(self._has_extra(field.toggles))
            box.toggled.connect(lambda on, mid=field.toggles: self._toggle_modifier(mid, on))
        else:
            box.setChecked(bool(self.instance.config.get(field.key)))
            box.toggled.connect(lambda on, k=field.key: self._on_config_changed(k, on))
        return box

    def _toggle_modifier(self, modifier_id: str, on: bool) -> None:
        """Attach or detach ``modifier_id`` to match a checkbox, if not already so."""
        attached = self._has_extra(modifier_id) or any(
            sel.modifier_id == modifier_id for sel in self.instance.flaws
        )
        if on and not attached:
            self.attach_modifier(modifier_id)
        elif not on and attached:
            chip = next((c for c in self._chips if c.selection.modifier_id == modifier_id), None)
            if chip is not None:
                self._remove_chip(chip)

    def _on_config_changed(self, key: str, value) -> None:
        if value:
            self.instance.config[key] = value
        else:  # "", empty list, or None all clear the choice
            self.instance.config.pop(key, None)
        self.changed.emit()

    # -- enhanced-trait target picker -------------------------------------
    def _build_target_picker(self, effect) -> QWidget | None:
        """A combo choosing which trait a configurable booster (Enhanced Trait) raises.

        Returns ``None`` unless the effect's :class:`TraitBoost` is ``configurable``
        and its ``affects`` names a numeric trait category (so senses/movement pickers
        don't appear). The options — abilities, resistances, and skills — are read
        from the game data, not hardcoded; the chosen key is stored in
        ``instance.config['target']``.
        """

        boost = effect.integration.trait_boost if effect and effect.integration else None
        if boost is None or not boost.configurable:
            return None
        if not (boost.affects & TRAIT_CATEGORIES):
            return None

        host = QWidget()
        form = QFormLayout(host)
        form.setContentsMargins(0, 0, 0, 0)
        combo = QComboBox()
        combo.addItem("— choose a trait —", "")
        combo.addItem("Abilities", None)  # a disabled section heading
        for ability in self._data.abilities:
            combo.addItem(f"  {ability.name}", ability.key)
        combo.addItem("Resistances", None)
        for res in self._data.resistances:
            if not res.derived:  # skip the derived Defence aggregate
                combo.addItem(f"  {res.name}", res.key)
        combo.addItem("Skills", None)
        for skill in self._data.skills:
            combo.addItem(f"  {skill.name}", skill.name)
        self._disable_section_headings(combo)

        current = combo.findData(self.instance.config.get("target", ""))
        combo.setCurrentIndex(current if current >= 0 else 0)
        guard_wheel(combo)
        combo.currentIndexChanged.connect(
            lambda _i, c=combo: self._on_config_changed("target", c.currentData())
        )
        form.addRow("Enhances", combo)
        return host

    @staticmethod
    def _disable_section_headings(combo: QComboBox) -> None:
        """Grey out the section-heading rows (those carrying ``None`` data) so they
        read as group labels rather than selectable traits."""
        model = combo.model()
        for index in range(combo.count()):
            if combo.itemData(index) is None:
                item = model.item(index)
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEnabled)

    # -- effect-specific modifier menu ------------------------------------
    def _populate_specific_menu(self) -> None:
        """(Re)build the menu from this effect's specific pool, disabling attached ones.

        Rebuilt on each open so an already-attached modifier shows as greyed-out.
        """
        self._specific_menu.clear()
        mods = self._data.effect_modifiers.get(self.instance.effect_id, [])
        attached = {sel.modifier_id for sel in (*self.instance.extras, *self.instance.flaws)}
        for category, title in (("extra", "Extras"), ("flaw", "Flaws")):
            group = [m for m in mods if m.category == category]
            if not group:
                continue
            self._specific_menu.addSection(title)
            for modifier in group:
                action = self._specific_menu.addAction(modifier.name)
                action.setToolTip(modifier.description)
                if modifier.id in attached:
                    action.setEnabled(False)  # already on this effect
                else:
                    action.triggered.connect(
                        lambda _checked=False, mid=modifier.id: self.attach_modifier(mid)
                    )

    # -- data lookups -----------------------------------------------------
    def _effect(self):
        return next((e for e in self._data.effects if e.id == self.instance.effect_id), None)

    def _modifier(self, modifier_id: str) -> Modifier | None:
        """Resolve a modifier id against the general pool, then this effect's own pool."""
        general = next((m for m in self._data.modifiers if m.id == modifier_id), None)
        if general is not None:
            return general
        specific = self._data.effect_modifiers.get(self.instance.effect_id, [])
        return next((m for m in specific if m.id == modifier_id), None)

    # -- drops ------------------------------------------------------------
    def dragEnterEvent(self, event) -> None:  # noqa: N802 (Qt override)
        if event.mimeData().hasFormat(MODIFIER_MIME):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event) -> None:  # noqa: N802 (Qt override)
        self.attach_modifier(_mime_id(event.mimeData(), MODIFIER_MIME))
        event.acceptProposedAction()

    # -- mutations (also the seam headless tests drive) -------------------
    def attach_modifier(self, modifier_id: str) -> None:
        """Attach an extra/flaw to this effect (routed by the modifier's category)."""
        modifier = self._modifier(modifier_id)
        if modifier is None:
            return
        selection = ModifierSelection(modifier_id=modifier_id)
        is_flaw = modifier.category == "flaw"
        bucket = self.instance.flaws if is_flaw else self.instance.extras
        bucket.append(selection)

        self._build_chip(modifier, selection, is_flaw)
        self._populate_config_form()  # a gating extra may change a field's type
        self._refresh_cost()
        self.changed.emit()

    def _build_chip(self, modifier: Modifier, selection: ModifierSelection, is_flaw: bool) -> None:
        """Render a chip for a selection already recorded on the instance.

        Shared by :meth:`attach_modifier` (which first appends the selection) and
        :meth:`_seed_modifier_chips` (which renders ones already present on load).
        """
        chip = ModifierChip(modifier, selection)
        chip.removeRequested.connect(self._remove_chip)
        chip.changed.connect(self._on_chip_changed)
        self._chips.append(chip)
        (self._flaws_group if is_flaw else self._extras_group).add_chip(chip)
        self._hint.setVisible(False)

    def _seed_modifier_chips(self) -> None:
        """Render chips for the instance's pre-existing extras and flaws (edit mode)."""
        for selection in self.instance.extras:
            modifier = self._modifier(selection.modifier_id)
            if modifier is not None:
                self._build_chip(modifier, selection, is_flaw=False)
        for selection in self.instance.flaws:
            modifier = self._modifier(selection.modifier_id)
            if modifier is not None:
                self._build_chip(modifier, selection, is_flaw=True)

    def _remove_chip(self, chip: ModifierChip) -> None:
        if chip.selection in self.instance.extras:
            self.instance.extras.remove(chip.selection)
            self._extras_group.remove_chip(chip)
        else:
            self.instance.flaws.remove(chip.selection)
            self._flaws_group.remove_chip(chip)
        self._chips.remove(chip)
        self._hint.setVisible(not self._chips)
        self._populate_config_form()  # removing a gating extra may downgrade a field
        self._refresh_cost()
        self.changed.emit()

    def _on_rank_changed(self, value: int) -> None:
        self.instance.rank = value
        for update_total in self._alloc_updaters:  # the rank is the allocation budget
            update_total()
        self._refresh_cost()
        self.changed.emit()

    def _on_chip_changed(self) -> None:
        self._refresh_cost()
        self.changed.emit()

    def _refresh_cost(self) -> None:
        formula = effect_cost_formula(self.instance, self._data)
        total = effect_total_cost(self.instance, self._data)
        self._cost.setText(f"{formula} = {total} PP" if formula else f"{total} PP")

    # -- structure role (driven by the canvas) ----------------------------
    def set_role(self, role: str, note: str = "") -> None:
        """Show this card's part in a composite power, or clear the badge.

        ``role`` is ``"base"``/``"alternate"`` (array), ``"linked"``, or ``""`` for
        an independent/single effect. ``note`` appends a detail (an alternate's flat
        cost) so the badge shows the number without this widget hardcoding it.
        """

        palette = {
            "base": ("Base", "#3a5f8a"),
            "alternate": ("Alternate", "#7a5c1e"),
            "linked": ("Linked", "#3a6f4a"),
        }
        if role not in palette:
            self._role_badge.clear()
            self._role_badge.setVisible(False)
            return
        text, color = palette[role]
        if note:
            text = f"{text} · {note}"
        self._role_badge.setText(text)
        self._role_badge.setStyleSheet(
            f"background: {color}; color: white; border-radius: 6px; padding: 0 6px;"
        )
        self._role_badge.setVisible(True)


class PowerModeBar(QWidget):
    """A three-way switch for how a multi-effect power's effects combine.

    Shown by the canvas only once a power holds two or more effects. Emits
    :attr:`changed` with the chosen structure id (``independent`` / ``linked`` /
    ``array``); the canvas writes it to the :class:`Power` and recomputes.
    """

    changed = Signal(str)

    _MODES = (
        (STRUCTURE_INDEPENDENT, "Independent", "Effects act on their own; their costs add up."),
        (STRUCTURE_LINKED, "Linked", "Effects always activate together as one; costs add up."),
        (
            STRUCTURE_ARRAY,
            "Array",
            "One effect active at a time; the costliest is paid in full and each other "
            "is a flat-cost alternate.",
        ),
    )

    def __init__(self) -> None:
        super().__init__()
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        layout.addWidget(QLabel("Multiple effects:"))
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._buttons: dict[str, QPushButton] = {}
        for structure, label, tip in self._MODES:
            button = QPushButton(label)
            button.setCheckable(True)
            button.setToolTip(tip)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            self._group.addButton(button)
            self._buttons[structure] = button
            layout.addWidget(button)
        layout.addStretch()
        self._buttons[STRUCTURE_INDEPENDENT].setChecked(True)
        self._group.buttonClicked.connect(self._on_clicked)

    def _on_clicked(self, button: QPushButton) -> None:
        for structure, candidate in self._buttons.items():
            if candidate is button:
                self.changed.emit(structure)
                return

    def set_structure(self, structure: str) -> None:
        """Reflect ``structure`` in the buttons without re-emitting :attr:`changed`."""
        button = self._buttons.get(structure)
        if button is not None:
            button.setChecked(True)


class PowerCanvas(QFrame):
    """The drop area that holds the power's effect cards and the structure switch.

    Accepts **effect** drops (each makes a new card). Owns no state itself beyond
    the shared :class:`Power`; emits :attr:`changed` on every add/remove/edit. Once
    a second card lands it reveals the :class:`PowerModeBar`, writes the chosen
    structure to the power, and keeps every card's role badge in step (the array
    base tracks the costliest effect as ranks change).
    """

    changed = Signal()

    def __init__(self, power: Power, game_data: GameData) -> None:
        super().__init__()
        self._power = power
        self._data = game_data
        self._cards: list[EffectCard] = []
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setAcceptDrops(True)

        self._layout = QVBoxLayout(self)
        # The structure switch sits above the cards; it reveals itself only once a
        # second effect makes the choice meaningful (§4).
        self._mode_bar = PowerModeBar()
        self._mode_bar.setVisible(False)
        self._mode_bar.changed.connect(self._on_structure_changed)
        self._layout.addWidget(self._mode_bar)
        self._hint = QLabel("Drag an effect here to start building your power")
        self._hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._hint.setEnabled(False)
        self._hint.setMinimumHeight(80)
        self._layout.addWidget(self._hint)
        self._layout.addStretch()

    def dragEnterEvent(self, event) -> None:  # noqa: N802 (Qt override)
        if event.mimeData().hasFormat(EFFECT_MIME):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event) -> None:  # noqa: N802 (Qt override)
        self.add_effect(_mime_id(event.mimeData(), EFFECT_MIME))
        event.acceptProposedAction()

    def add_effect(self, effect_id: str) -> EffectCard:
        """Append a new effect to the power and render its card."""
        instance = PowerEffectInstance(effect_id=effect_id)
        self._power.effects.append(instance)
        card = self._build_card(instance)
        self._sync_structure_ui()
        self.changed.emit()
        return card

    def _build_card(self, instance: PowerEffectInstance) -> EffectCard:
        """Render a card for an effect instance already on the power."""
        card = EffectCard(instance, self._data)
        card.changed.connect(self._on_card_changed)
        card.removeRequested.connect(self._remove_card)
        self._cards.append(card)
        self._layout.insertWidget(self._layout.count() - 1, card)  # before the stretch
        self._hint.setVisible(False)
        return card

    def load_power(self) -> None:
        """Seed cards for a power that already carries effects (edit mode).

        The effects are already on ``self._power``; this renders a card for each and
        brings the structure switch in line with the loaded structure without
        emitting :attr:`changed` (the window refreshes its cost/summary itself).
        """
        for instance in self._power.effects:
            self._build_card(instance)
        self._sync_structure_ui()
        self._mode_bar.set_structure(self._power.structure)

    def _remove_card(self, card: EffectCard) -> None:
        if card.instance in self._power.effects:
            self._power.effects.remove(card.instance)
        self._cards.remove(card)
        card.setParent(None)
        card.deleteLater()
        self._hint.setVisible(not self._cards)
        self._sync_structure_ui()
        self.changed.emit()

    def _on_card_changed(self) -> None:
        # A rank/modifier edit can change which effect is the array base, so refresh
        # the badges before forwarding the change on for a cost/summary recompute.
        self._refresh_roles()
        self.changed.emit()

    def _on_structure_changed(self, structure: str) -> None:
        self._power.structure = structure
        self._refresh_roles()
        self.changed.emit()

    def _sync_structure_ui(self) -> None:
        """Reveal the switch for a multi-effect power (collapsing back to Independent
        when a removal leaves fewer than two), then refresh the card badges."""
        multi = len(self._cards) >= 2
        self._mode_bar.setVisible(multi)
        if not multi and self._power.structure != STRUCTURE_INDEPENDENT:
            self._power.structure = STRUCTURE_INDEPENDENT
            self._mode_bar.set_structure(STRUCTURE_INDEPENDENT)
        self._refresh_roles()

    def _refresh_roles(self) -> None:
        """Badge each card with its part in the current structure (§4)."""
        multi = len(self._cards) >= 2
        if multi and self._power.structure == STRUCTURE_ARRAY:
            base = array_base_index(self._power, self._data)
            note = f"{array_alternate_cost(self._data)} PP"
            for index, card in enumerate(self._cards):
                if index == base:
                    card.set_role("base")
                else:
                    card.set_role("alternate", note)
        elif multi and self._power.structure == STRUCTURE_LINKED:
            for card in self._cards:
                card.set_role("linked")
        else:
            for card in self._cards:
                card.set_role("")

    @property
    def cards(self) -> list[EffectCard]:
        return list(self._cards)


class PowerTermsView(QWidget):
    """A read-only, tinted game-term breakdown of the power as a per-effect table.

    Rebuilt from the :class:`Power` whenever it changes (:meth:`set_power`): a titled
    block per effect listing its stats (Type, Range, …) as label/value rows. A stat
    an extra improved is shown green and one a flaw limited red — with the base value
    on the value's tooltip — reading the tint straight from
    :func:`~mm_companion.core.rules.effect_stat_rows`. Composite powers get a
    structure header and each effect its array/linked role note.

    It sizes to its content (no inner scroll bar), so the whole summary is always
    visible; the canvas below it takes the remaining space. The last-rendered rows
    are kept in :attr:`effect_rows` (one list per effect) as the seam headless tests
    read.
    """

    # Tints for a modified stat's value; readable on both light and dark themes.
    _TINTS = {"better": "#2e9e4f", "worse": "#d15b5b"}
    # How many label/value pairs sit side by side per grid row, so the short stats
    # pack across the width instead of stacking into a tall, scrolling column.
    _PAIRS_PER_ROW = 2

    def __init__(self) -> None:
        super().__init__()
        self.effect_rows: list[list] = []
        # Hug the content vertically so the summary never grows a scroll bar of its
        # own — the enclosing canvas absorbs the slack instead.
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(6)

    def set_power(self, power: Power, game_data: GameData, char: Character | None = None) -> None:
        self._clear()
        if not power.effects:
            placeholder = QLabel("Game-term summary appears here as you add effects.")
            placeholder.setStyleSheet("color: gray; font-style: italic;")
            placeholder.setWordWrap(True)
            self._layout.addWidget(placeholder)
            return

        header = self._structure_header(power)
        if header:
            label = QLabel(header)
            label.setStyleSheet("font-weight: bold;")
            self._layout.addWidget(label)
        for index, effect in enumerate(power.effects):
            self._add_effect_block(effect, index, power, game_data, char)

    def _add_effect_block(
        self,
        effect: PowerEffectInstance,
        index: int,
        power: Power,
        game_data: GameData,
        char: Character | None = None,
    ) -> None:
        base = next((e for e in game_data.effects if e.id == effect.effect_id), None)
        # The title carries the effective rank so a Strength-Based Damage reads at its
        # boosted rank (e.g. "Damage 13"), matching the DC below.
        rank = effect_effective_rank(effect, game_data, char)
        title = f"{base.name if base else effect.effect_id} {rank}"

        header = QHBoxLayout()
        header.setSpacing(6)
        name = QLabel(title)
        name.setStyleSheet("font-weight: bold;")
        header.addWidget(name)
        note = self._role_note(power, index, game_data)
        if note:
            role = QLabel(note)
            role.setStyleSheet("color: gray; font-style: italic;")
            header.addWidget(role)
        header.addStretch()
        self._layout.addLayout(header)

        rows = effect_stat_rows(effect, game_data, char)
        self.effect_rows.append(rows)
        pairs = self._PAIRS_PER_ROW
        grid = QGridLayout()
        grid.setContentsMargins(12, 0, 0, 0)
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(1)
        for index, stat in enumerate(rows):
            grid_row, pair = divmod(index, pairs)
            col = pair * 2
            label = QLabel(f"{stat.label}:")
            label.setStyleSheet("color: gray;")
            value = QLabel(stat.value)
            value.setWordWrap(True)
            tint = self._TINTS.get(stat.change)
            if tint:
                value.setStyleSheet(f"color: {tint}; font-weight: bold;")
                value.setToolTip(f"Base: {stat.base}")
            grid.addWidget(label, grid_row, col, Qt.AlignmentFlag.AlignTop)
            grid.addWidget(value, grid_row, col + 1, Qt.AlignmentFlag.AlignTop)
        # Let the value columns share the slack evenly so the pairs spread across
        # the width rather than bunching at the left.
        for pair in range(pairs):
            grid.setColumnStretch(pair * 2 + 1, 1)
        self._layout.addLayout(grid)

    @staticmethod
    def _structure_header(power: Power) -> str:
        if len(power.effects) < 2:
            return ""
        if power.structure == STRUCTURE_LINKED:
            return "Linked (all effects activate together):"
        if power.structure == STRUCTURE_ARRAY:
            return "Array (one effect active at a time):"
        return ""

    @staticmethod
    def _role_note(power: Power, index: int, game_data: GameData) -> str:
        if len(power.effects) < 2 or power.structure != STRUCTURE_ARRAY:
            return ""
        if index == array_base_index(power, game_data):
            return "base"
        return f"Alternate Effect, {array_alternate_cost(game_data)} pt"

    def _clear(self) -> None:
        self.effect_rows = []
        self._take_all(self._layout)

    def _take_all(self, layout) -> None:
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
            elif item.layout() is not None:
                self._take_all(item.layout())
                item.layout().deleteLater()


class PowerConstructorWindow(QMainWindow):
    """Standalone brick-builder window for assembling a single power."""

    closed = Signal()
    powerSaved = Signal(object)  # carries the finished Power to the host section

    def __init__(
        self,
        data: GameData | None = None,
        parent: QWidget | None = None,
        *,
        character: Character | None = None,
        power: Power | None = None,
    ) -> None:
        super().__init__(parent)
        self._data = data or load_game_data()
        # The wielding character, used to resolve stats that feed a power (Strength
        # for Strength-Based Damage, Attack for the PL cap) and to flag cap breaches.
        # None disables the check (a constructor opened without a character context).
        self._character = character
        # Editing works on a deep copy so closing the window without saving leaves
        # the character's stored power untouched; the copy is what `powerSaved` hands
        # back, and the host section swaps it in for the original on save.
        self._editing = power is not None
        self.power = Power.from_dict(power.to_dict()) if self._editing else Power()
        self.setWindowTitle("Edit Power" if self._editing else "Power Constructor")
        self.resize(900, 600)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self._build_palette())
        splitter.addWidget(self._build_editor())
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([260, 640])
        self.setCentralWidget(splitter)

        if self._editing:
            self._seed_from_power()

        self._refresh_cost()
        self._refresh_game_terms()
        self._refresh_pl_warning()

    def _seed_from_power(self) -> None:
        """Populate the editor from the (copied) power being edited."""
        self._name.setText(self.power.name)
        self._description.setPlainText(self.power.description)
        self.canvas.load_power()
        self._save_button.setText("Save Changes")
        self._save_button.setToolTip("Update this power on the character sheet")

    # -- left: the palette of bricks --------------------------------------
    def _build_palette(self) -> QWidget:
        from PySide6.QtWidgets import QTabWidget  # local: only used here

        tabs = QTabWidget()
        effects = [
            BrickWidget(e.name, e.base_cost, EFFECT_MIME, e.id)
            for e in sorted(self._data.effects, key=lambda e: e.name)
        ]
        extras = [
            BrickWidget(m.name, m.cost_formula, MODIFIER_MIME, m.id, flat=m.flat)
            for m in sorted(self._data.modifiers, key=lambda m: m.name)
            if m.category == "extra"
        ]
        flaws = [
            BrickWidget(m.name, m.cost_formula, MODIFIER_MIME, m.id, flat=m.flat)
            for m in sorted(self._data.modifiers, key=lambda m: m.name)
            if m.category == "flaw"
        ]
        # Keep each tab's search box + bricks addressable (also the test seam).
        self._search_tabs: dict[str, tuple[QLineEdit, list[BrickWidget]]] = {}
        tabs.addTab(self._build_search_tab("effects", effects, "Search effects"), "Effects")
        tabs.addTab(self._build_search_tab("extras", extras, "Search extras"), "Extras")
        tabs.addTab(self._build_search_tab("flaws", flaws, "Search flaws"), "Flaws")
        return tabs

    def _build_search_tab(self, key: str, bricks: list[BrickWidget], placeholder: str) -> QWidget:
        """A scrollable brick list with a live search box pinned above it.

        Typing filters the bricks instantly to those whose name or cost text
        contains the query (case-insensitive substring); clearing shows them all.
        """
        tab = QWidget()
        outer = QVBoxLayout(tab)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(4)

        search = QLineEdit()
        search.setPlaceholderText(placeholder)
        search.setClearButtonEnabled(True)  # a one-click reset
        outer.addWidget(search)

        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setSpacing(4)
        for brick in bricks:
            layout.addWidget(brick)
        empty = QLabel("No matches")
        empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty.setEnabled(False)
        empty.setVisible(False)
        layout.addWidget(empty)
        layout.addStretch()

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(container)
        outer.addWidget(scroll, stretch=1)

        search.textChanged.connect(
            lambda text, bs=bricks, e=empty: self._filter_bricks(text, bs, e)
        )
        self._search_tabs[key] = (search, bricks)
        return tab

    @staticmethod
    def _filter_bricks(text: str, bricks: list[BrickWidget], empty: QLabel) -> None:
        needle = text.strip().lower()
        matches = 0
        for brick in bricks:
            visible = needle in brick.search_key
            brick.setVisible(visible)
            matches += visible
        empty.setVisible(matches == 0)

    # -- right: the power being built -------------------------------------
    def _build_editor(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)

        self._name = QLineEdit()
        self._name.setPlaceholderText("Power name (e.g. Fire Blast)")
        self._name.textChanged.connect(self._on_name_changed)
        layout.addWidget(self._name)

        self._description = QTextEdit()
        self._description.setPlaceholderText("Description / flavor text")
        # A compact two-ish line box: the flavor text is short, so keep it from
        # eating vertical room the game-term table and canvas need.
        self._description.setFixedHeight(50)
        self._description.textChanged.connect(self._on_description_changed)
        layout.addWidget(self._description)

        # A read-only, auto-generated game-terms table sits under the free-text
        # description — it is derived from the effects/modifiers, not editable, and
        # tints each stat a modifier changed (green better, red worse).
        self._terms = PowerTermsView()
        layout.addWidget(self._terms)

        cost_row = QHBoxLayout()
        self._cost = QLabel()
        self._cost.setStyleSheet("font-weight: bold;")
        cost_row.addWidget(self._cost)
        cost_row.addStretch()
        # A live Power Level warning: hidden while the power is within caps, it
        # names the breach on its tooltip when it isn't.
        self._warning = QLabel()
        self._warning.setStyleSheet("color: #d1a01e; font-weight: bold;")
        self._warning.setVisible(False)
        cost_row.addWidget(self._warning)
        layout.addLayout(cost_row)

        self.canvas = PowerCanvas(self.power, self._data)
        self.canvas.changed.connect(self._refresh_cost)
        self.canvas.changed.connect(self._refresh_game_terms)
        self.canvas.changed.connect(self._refresh_pl_warning)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.canvas)
        layout.addWidget(scroll, stretch=1)

        # A save bar pinned below the canvas hands the finished power to the sheet.
        actions = QHBoxLayout()
        actions.addStretch()
        self._save_button = QPushButton("Save Power")
        self._save_button.setToolTip("Add this power to the character sheet")
        self._save_button.clicked.connect(self._save_power)
        actions.addWidget(self._save_button)
        layout.addLayout(actions)
        return panel

    def _on_name_changed(self, text: str) -> None:
        self.power.name = text

    def _on_description_changed(self) -> None:
        self.power.description = self._description.toPlainText()

    def _refresh_cost(self) -> None:
        self._cost.setText(f"Total cost: {power_total_cost(self.power, self._data)} PP")

    def _refresh_game_terms(self) -> None:
        self._terms.set_power(self.power, self._data, self._character)

    def _pl_violations(self) -> list[str]:
        """Power Level cap breaches for the current power (empty without a character)."""
        if self._character is None:
            return []
        return power_pl_violations(self.power, self._character, self._data)

    def _alloc_violations(self) -> list[str]:
        """Tier-4 over-allocation breaches (an effect spending ranks it doesn't have)."""
        return power_allocation_violations(self.power, self._data)

    def _refresh_pl_warning(self) -> None:
        """Show or hide the live warning from the current PL and allocation breaches."""
        pl = self._pl_violations()
        alloc = self._alloc_violations()
        if pl and alloc:
            headline = "⚠ Over Power Level & over-allocated"
        elif pl:
            headline = "⚠ Exceeds Power Level"
        elif alloc:
            headline = "⚠ Over-allocated"
        else:
            headline = ""
        if headline:
            self._warning.setText(headline)
            self._warning.setToolTip("\n".join((*pl, *alloc)))
        self._warning.setVisible(bool(headline))

    def _save_power(self) -> None:
        """Hand the assembled power to the host section, then close.

        A power with no effects has nothing to cost or resolve, so it is rejected
        with a prompt rather than saved empty. An over-allocated Tier-4 effect (one
        spending more ranks than it has) is always rejected — that's a build error,
        not a house-rule choice. A power that breaks a PL cap is rejected only when
        enforcement is set to *block* — otherwise the live warning has already flagged
        it and the save is allowed to proceed.
        """
        if not self.power.effects:
            QMessageBox.information(
                self,
                "Nothing to save",
                "Add at least one effect before saving this power.",
            )
            return
        alloc = self._alloc_violations()
        if alloc:
            QMessageBox.warning(
                self,
                "Over-allocated",
                "This power can't be saved because an effect allocates more ranks "
                "than it has:\n\n• " + "\n• ".join(alloc),
            )
            return
        violations = self._pl_violations()
        if violations and storage.pl_enforcement() == storage.PL_ENFORCE_BLOCK:
            QMessageBox.warning(
                self,
                "Exceeds Power Level",
                "This power can't be saved because it breaks Power Level caps:\n\n• "
                + "\n• ".join(violations),
            )
            return
        self.powerSaved.emit(self.power)
        self.close()

    def closeEvent(self, event) -> None:  # noqa: N802 (Qt override)
        self.closed.emit()
        super().closeEvent(event)

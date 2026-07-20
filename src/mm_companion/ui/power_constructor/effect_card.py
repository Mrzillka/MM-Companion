from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QPushButton,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from mm_companion.core.character import Character
from mm_companion.core.data_loader import GameData, Modifier
from mm_companion.core.powers import (
    ModifierSelection,
    PowerEffectInstance,
)
from mm_companion.core.rules import (
    TRAIT_CATEGORIES,
    effect_allocation_used,
    effect_cost_formula,
    effect_makes_attack,
    effect_total_cost,
    effective_ability,
)
from mm_companion.ui.flow_layout import FlowLayout
from mm_companion.ui.power_constructor.common import (
    _ACCENT,
    CONFIG_WIDGET_BUILDERS,
    MODIFIER_MIME,
    RANK_MAX,
    _fill_trait_combo,
    _mime_id,
    _move_item,
)
from mm_companion.ui.power_constructor.modifier_chip import ModifierChip, ModifierGroup
from mm_companion.ui.theme import TINT_WORSE, tint_rgba
from mm_companion.ui.wheel_guard import guard_wheel
from mm_companion.ui.widgets import make_spin_box

# Effect card chrome — a rounded, padded panel. The drag state swaps to an accent
# border + faint fill so a hovering modifier clearly lands "on this card".
_CARD_STYLE = "EffectCard { border: 1px solid palette(mid); border-radius: 8px; }"
_CARD_STYLE_DRAG = (
    f"EffectCard {{ border: 2px solid {_ACCENT}; border-radius: 8px;"
    f" background: {tint_rgba(_ACCENT, 0.10)}; }}"
)


class EffectCard(QFrame):
    """One effect within the power: rank, attached modifier chips, and its cost.

    Accepts **modifier** drops (extras/flaws from the general palette attach here),
    and offers this effect's own effect-specific extras/flaws through a menu button.
    Writes rank/modifier changes straight to the shared :class:`PowerEffectInstance`
    and emits :attr:`changed` so the window can recompute the total.
    """

    changed = Signal()
    removeRequested = Signal(object)

    def __init__(
        self,
        instance: PowerEffectInstance,
        game_data: GameData,
        focus_options: list[tuple[str, str]] | None = None,
        character: Character | None = None,
    ) -> None:
        super().__init__()
        self.instance = instance
        self._data = game_data
        # The wielder, so an ability-folding chip (Strength-Based) can bound its
        # "amount used" spin box by the character's effective ability.
        self._character = character
        # Close/Ranged Combat focuses the wielder can link this effect's attack to.
        self._focus_options = focus_options or []
        self._chips: list[ModifierChip] = []
        # Callables that refresh each Tier-4 allocation field's "used / rank" readout;
        # rebuilt with the config form and fired when the effect's rank changes.
        self._alloc_updaters: list = []
        self.setObjectName("EffectCard")
        self.setStyleSheet(_CARD_STYLE)
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
        self._rank = make_spin_box(1, RANK_MAX, value=instance.rank, buttons=False, max_width=44)
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

        # An optional link from *this effect's* attack to one of the wielder's
        # Close/Ranged Combat focuses: a "Use attack skill" checkbox reveals the
        # picker, whose focus total then replaces the bare Attack for this effect's
        # roll and PL cap. Only built when the character actually has combat focuses.
        attack_skill_row = self._build_attack_skill_row()
        if attack_skill_row is not None:
            layout.addWidget(attack_skill_row)

        # The config form is rebuilt on demand: attaching Extra Condition upgrades
        # Affliction's degree pickers from single-select to multiselect.
        self._config_host = QWidget()
        self._config_layout = QVBoxLayout(self._config_host)
        self._config_layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._config_host)
        self._populate_config_form()

        self._extras_group = ModifierGroup("Extras")
        self._flaws_group = ModifierGroup("Flaws")
        self._extras_group.reordered.connect(
            lambda frm, to: self._reorder_bucket(self.instance.extras, frm, to)
        )
        self._flaws_group.reordered.connect(
            lambda frm, to: self._reorder_bucket(self.instance.flaws, frm, to)
        )
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
        self._cost.setStyleSheet("font-weight: bold;")
        layout.addWidget(self._cost)

        # When editing an existing power the instance already carries its extras and
        # flaws — render a chip for each (the config form built above already reads
        # them, e.g. an attached Extra Condition, so only the chips need seeding).
        self._seed_modifier_chips()
        self._refresh_cost()

    # -- attack-skill link ------------------------------------------------
    def _build_attack_skill_row(self) -> QWidget | None:
        """A "Use attack skill" checkbox plus focus picker, or ``None`` when the
        wielder has no Close/Ranged Combat focuses to link to.

        The row is only *shown* for an effect that resolves with an attack roll; a
        Perception-Range flaw (or a base effect that never rolls to hit) hides it via
        :meth:`_refresh_attack_skill_visibility`, since there's no attack to reskill.
        """
        self._attack_skill_row = None
        if not self._focus_options:
            self._attack_skill_check = None
            self._attack_skill = None
            return None

        self._attack_skill_check = QCheckBox("Use attack skill")
        self._attack_skill_check.setToolTip(
            "Link this effect's attack to a Close/Ranged Combat focus — that focus's "
            "total replaces the character's Attack for this effect's roll and PL cap."
        )
        self._attack_skill = QComboBox()
        for display, row_id in self._focus_options:
            self._attack_skill.addItem(display, row_id)
        guard_wheel(self._attack_skill)

        # Seed from the instance: a stored link ticks the box and selects its focus.
        linked = bool(self.instance.attack_skill)
        self._attack_skill_check.setChecked(linked)
        self._attack_skill.setVisible(linked)
        if linked:
            index = self._attack_skill.findData(self.instance.attack_skill)
            self._attack_skill.setCurrentIndex(index if index >= 0 else 0)

        self._attack_skill_check.toggled.connect(self._on_use_attack_skill_toggled)
        self._attack_skill.currentIndexChanged.connect(self._on_attack_skill_changed)

        host = QWidget()
        row = QHBoxLayout(host)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        row.addWidget(self._attack_skill_check)
        row.addWidget(self._attack_skill, 1)
        self._attack_skill_row = host
        host.setVisible(effect_makes_attack(self.instance, self._data))
        return host

    def _refresh_attack_skill_visibility(self) -> None:
        """Show the attack-skill row only while the effect still makes an attack roll
        (a Perception-Range flaw drops the roll, so the link no longer applies)."""
        if self._attack_skill_row is not None:
            self._attack_skill_row.setVisible(effect_makes_attack(self.instance, self._data))

    def _on_use_attack_skill_toggled(self, checked: bool) -> None:
        self._attack_skill.setVisible(checked)
        # Checked → link the selected focus; unchecked → drop the link entirely.
        self.instance.attack_skill = self._attack_skill.currentData() or "" if checked else ""
        self.changed.emit()

    def _on_attack_skill_changed(self) -> None:
        if self._attack_skill_check is not None and self._attack_skill_check.isChecked():
            self.instance.attack_skill = self._attack_skill.currentData() or ""
            self.changed.emit()

    # -- effect-specific qualities (config) -------------------------------
    def _has_extra(self, modifier_id: str) -> bool:
        return any(sel.modifier_id == modifier_id for sel in self.instance.extras)

    def _effective_type(self, field) -> str:
        """A field's live input type — ``select`` is upgraded to ``multiselect`` only
        while its ``multiselect_with`` extra is attached (e.g. Extra Condition)."""
        if field.multiselect_with and self._has_extra(field.multiselect_with):
            return "multiselect"
        return field.type

    def _hidden_by_gate(self, field) -> bool:
        """Whether a base config field is deferred by its ``hidden_with`` gating extra.

        A plain gating extra hides the field whenever it is attached (the historical
        behaviour). A gating extra that carries a ``points`` scope — Variable Conditions
        — hides selectively: at its top tier (``points`` == the spin's max) every gated
        field is deferred to use-time, but at a lower tier only the one degree its own
        picker names is, leaving the others editable.
        """
        ext_id = field.hidden_with
        if not ext_id:
            return False
        selection = next((s for s in self.instance.extras if s.modifier_id == ext_id), None)
        if selection is None:
            return False
        modifier = self._modifier(ext_id)
        if modifier is None:
            return True
        points_field = next((c for c in modifier.config_fields if c.type == "points"), None)
        if points_field is None:
            return True  # plain gate: attached means every gated field is deferred
        points = int(selection.config.get(points_field.key, points_field.default_value))
        if points >= points_field.max_value:
            return True  # full scope: every gated field deferred
        picker = next(
            (c for c in modifier.config_fields if c.type == "select" and c.show_when_points),
            None,
        )
        chosen = selection.config.get(picker.key) if picker else None
        return field.key == chosen

    def _is_form_gate(self, modifier_id: str) -> bool:
        """Whether the modifier's presence or config decides which base config fields
        show — some base field defers to it via ``hidden_with`` or ``multiselect_with``.

        A change to such a modifier's own config (Variable Conditions' scope spin) must
        rebuild the effect's config form so the affected pickers appear or disappear.
        """
        effect = self._effect()
        if effect is None:
            return False
        return any(
            f.hidden_with == modifier_id or f.multiselect_with == modifier_id
            for f in effect.config_fields
        )

    def _hidden_config_keys(self) -> set[str]:
        """Effect config-field keys suppressed by an attached modifier whose own config
        declares ``hides_field`` — the modifier's chosen value names the effect field to
        hide (Affliction's Limited Degree picks which degree tier imposes no condition)."""
        hidden: set[str] = set()
        for selection in (*self.instance.extras, *self.instance.flaws):
            modifier = self._modifier(selection.modifier_id)
            if modifier is None:
                continue
            for cfg in modifier.config_fields:
                if cfg.hides_field:
                    value = selection.config.get(cfg.key)
                    if value:
                        hidden.add(value)
        return hidden

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
        disabled_keys = self._hidden_config_keys()
        form_host = QWidget()
        form = QFormLayout(form_host)
        form.setContentsMargins(0, 0, 0, 0)
        for field in effect.config_fields:
            if field.key in disabled_keys:
                # A flaw's config (Affliction's Limited Degree) turned this tier off:
                # drop any stored choice and show a note instead of the picker.
                self.instance.config.pop(field.key, None)
                note = QLabel("no effect (Limited Degree)")
                note.setEnabled(False)
                form.addRow(field.label, note)
                continue
            if self._hidden_by_gate(field):
                # A gating extra (e.g. Variable Conditions) defers this choice to
                # use-time — drop any stored value and show a note in its place. A
                # partial scope leaves the other degrees below still editable.
                self.instance.config.pop(field.key, None)
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
        builder = CONFIG_WIDGET_BUILDERS.get(field_type)
        if builder is not None:
            return builder(self, field, field_type)
        # ``select`` and any mod type without a builder render as the generic combo.
        return self._select_widget(field)

    def _text_widget(self, field) -> QWidget:
        edit = QLineEdit(self.instance.config.get(field.key, ""))
        edit.textChanged.connect(lambda text, k=field.key: self._on_config_changed(k, text))
        return edit

    def _select_widget(self, field) -> QWidget:
        """The single-choice option combo (the default renderer for ``select``)."""
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
            total.setStyleSheet(f"color: {TINT_WORSE}; font-weight: bold;" if used > rank else "")

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
            total.setStyleSheet(f"color: {TINT_WORSE}; font-weight: bold;" if used > rank else "")

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
                        RANK_MAX,
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
        _fill_trait_combo(combo, self._data, self.instance.config.get("target", ""))
        guard_wheel(combo)
        combo.currentIndexChanged.connect(
            lambda _i, c=combo: self._on_config_changed("target", c.currentData())
        )
        form.addRow("Enhances", combo)
        return host

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
            self.setStyleSheet(_CARD_STYLE_DRAG)  # light up as a drop target
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragLeaveEvent(self, event) -> None:  # noqa: N802 (Qt override)
        self.setStyleSheet(_CARD_STYLE)
        super().dragLeaveEvent(event)

    def dropEvent(self, event) -> None:  # noqa: N802 (Qt override)
        self.setStyleSheet(_CARD_STYLE)
        self.attach_modifier(_mime_id(event.mimeData(), MODIFIER_MIME))
        event.acceptProposedAction()

    # -- mutations (also the seam headless tests drive) -------------------
    def attach_modifier(self, modifier_id: str) -> None:
        """Attach an extra/flaw to this effect (routed by the modifier's category).

        Ignores an attach that could not change anything: one the effect already
        carries implicitly as part of its own definition (Damage's ``attack``), or a
        second copy of a modifier with no config to tell the copies apart — that would
        only double-charge the power. A modifier that *does* carry config can be taken
        more than once, since each selection means something different (Limited "only
        at night" alongside Limited "only vs. robots").
        """
        modifier = self._modifier(modifier_id)
        if modifier is None:
            return
        base = self._effect()
        if base is not None and modifier_id in base.implicit_modifiers:
            return
        attached = {sel.modifier_id for sel in (*self.instance.extras, *self.instance.flaws)}
        if modifier_id in attached and not modifier.config_fields:
            return
        selection = ModifierSelection(modifier_id=modifier_id)
        is_flaw = modifier.category == "flaw"
        bucket = self.instance.flaws if is_flaw else self.instance.extras
        bucket.append(selection)

        # An ability-folding modifier (Strength-Based) pays for a fixed amount of that
        # ability. Seed it to the wielder's current ability so a fresh chip costs what
        # it would today, then pin it there so the cost is stable as the ability moves.
        if modifier.adds_ability and self._character is not None:
            selection.config["amount"] = max(
                0, effective_ability(self._character, self._data, modifier.adds_ability)
            )

        self._build_chip(modifier, selection, is_flaw)
        self._populate_config_form()  # a gating extra may change a field's type
        self._refresh_attack_skill_visibility()  # Perception Range drops the attack roll
        self._refresh_cost()
        self.changed.emit()

    def _build_chip(self, modifier: Modifier, selection: ModifierSelection, is_flaw: bool) -> None:
        """Render a chip for a selection already recorded on the instance.

        Shared by :meth:`attach_modifier` (which first appends the selection) and
        :meth:`_seed_modifier_chips` (which renders ones already present on load).
        """
        chip = ModifierChip(modifier, selection, self._data, self._character)
        chip.removeRequested.connect(self._remove_chip)
        chip.changed.connect(lambda m=modifier: self._on_chip_changed(m))
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
        self._refresh_attack_skill_visibility()  # removing Perception Range restores it
        self._refresh_cost()
        self.changed.emit()

    def _reorder_bucket(self, bucket: list, from_index: int, to_index: int) -> None:
        """Mirror a chip reorder onto its backing selection list (extras or flaws).

        Chips are kept index-aligned with their bucket, so the group's indices apply
        directly. Order can change which of two stat-touching modifiers wins, so the
        cost/summary recompute.
        """
        if _move_item(bucket, from_index, to_index):
            self._refresh_cost()
            self.changed.emit()

    def _on_rank_changed(self, value: int) -> None:
        self.instance.rank = value
        for update_total in self._alloc_updaters:  # the rank is the allocation budget
            update_total()
        self._refresh_cost()
        self.changed.emit()

    def _on_chip_changed(self, modifier: Modifier | None = None) -> None:
        # A modifier whose config decides which of this effect's fields show must
        # rebuild the form so pickers appear/disappear — Limited Degree choosing a
        # degree (its own hides_field), or Variable Conditions' scope spin (a base
        # field defers to it via hidden_with).
        if modifier is not None and (
            any(f.hides_field for f in modifier.config_fields) or self._is_form_gate(modifier.id)
        ):
            self._populate_config_form()
        self._refresh_cost()
        self.changed.emit()

    def _refresh_cost(self) -> None:
        formula = effect_cost_formula(self.instance, self._data, self._character)
        total = effect_total_cost(self.instance, self._data, self._character)
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

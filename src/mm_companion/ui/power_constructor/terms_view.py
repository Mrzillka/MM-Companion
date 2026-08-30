from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from mm_companion.core.character import Character
from mm_companion.core.data_loader import GameData
from mm_companion.core.powers import (
    STRUCTURE_ARRAY,
    Power,
    PowerEffectInstance,
)
from mm_companion.core.rules import (
    array_base_index,
    array_member_note,
    effect_attack_skill_bonus,
    effect_effective_rank,
    effect_stat_rows,
    power_total_cost,
    resolve_stat_display,
    structure_header,
)
from mm_companion.ui.power_constructor.terms_grid import build_terms_grid
from mm_companion.ui.wheel_guard import guard_wheel
from mm_companion.ui.widgets import (
    BOLD_STYLE,
    discard_widget,
    hline_separator,
    make_spin_box,
    muted_style,
)

# The six standard game-term fields the Dev-mode override table edits directly, with
# their labels and the matching :class:`~mm_companion.core.data_loader.Effect`
# attribute (used to seed each field's editable combo with the values that field
# takes across the effect catalog, on top of its game-term ladder).
_OVERRIDE_STD_FIELDS = (
    ("effect_type", "Type", "effect_type"),
    ("range", "Range", "range_"),
    ("action", "Action", "action"),
    ("duration", "Duration", "duration"),
    ("check", "Check", "check"),
    ("resistance", "Resistance", "resistance"),
)
_OVERRIDE_STD_KEYS = frozenset(key for key, _, _ in _OVERRIDE_STD_FIELDS)


@dataclass(frozen=True)
class CostOverrideTarget:
    """Where the Dev-mode "Override total cost" row reads and writes, and in what unit.

    A power overrides its own :attr:`~mm_companion.core.powers.Power.cost_override`, in
    Power Points, and that is what this table does with no target set. An **equipment
    item** is bought in the other currency, so the constructor's gear mode hands one of
    these in pointing at
    :attr:`~mm_companion.core.equipment.EquipmentItem.ep_override` instead.

    The indirection exists precisely so the two currencies never share a field: a price
    typed as Equipment Points and stored on ``Power.cost_override`` would read back as
    Power Points to every function that asks a power what it costs, and nothing would
    raise — it would simply be wrong (``EQUIPMENT_PLAN.md``, "Two currencies").

    :attr:`derived` supplies what the spin box starts at when nothing is overridden yet,
    so the player edits *from* the engine's own answer rather than from zero.
    """

    unit: str
    read: Callable[[], int | None]
    write: Callable[[int | None], None]
    derived: Callable[[], int]


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
    # A homerule override reads in a distinct blue, apart from modifier better/worse.
    # Emitted when a Dev-mode override is edited in the table, so the window can
    # recompute cost/PL without rebuilding the table (which would drop the live widget).
    edited = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.effect_rows: list[list] = []
        # Hug the content vertically so the summary never grows a scroll bar of its
        # own — the enclosing canvas absorbs the slack instead.
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(6)
        # Retained so a Dev-mode toggle can re-render the same power in the other mode.
        self._power: Power | None = None
        self._game_data: GameData | None = None
        self._char: Character | None = None
        self._editable = False
        # Where the cost-override row reads and writes; None means the power's own
        # ``cost_override``, in Power Points.
        self._cost_target: CostOverrideTarget | None = None

    def set_power(self, power: Power, game_data: GameData, char: Character | None = None) -> None:
        self._power = power
        self._game_data = game_data
        self._char = char
        self._render()

    def set_cost_override_target(self, target: CostOverrideTarget | None) -> None:
        """Point the Dev-mode cost override at something other than the power itself.

        Set once by the host window (the constructor's gear mode); see
        :class:`CostOverrideTarget` for why the field is not simply shared.
        """
        self._cost_target = target
        self._render()

    def set_editable(self, editable: bool) -> None:
        """Switch the whole table between read-only and the Dev-mode override editor."""
        self._editable = editable
        self._render()

    def _render(self) -> None:
        self._clear()
        power, game_data = self._power, self._game_data
        if power is None or game_data is None:
            return
        if not power.effects and not self._editable:
            placeholder = QLabel("Game-term summary appears here as you add effects.")
            placeholder.setStyleSheet(muted_style(italic=True))
            placeholder.setWordWrap(True)
            self._layout.addWidget(placeholder)
            return

        header = self._structure_header(power)
        if header:
            label = QLabel(header)
            label.setStyleSheet(BOLD_STYLE)
            self._layout.addWidget(label)
        if self._editable:
            self._render_editable(power, game_data, self._char)
            return
        for index, effect in enumerate(power.effects):
            attack_bonus = effect_attack_skill_bonus(effect, self._char, game_data)
            self._add_effect_block(effect, index, power, game_data, self._char, attack_bonus)

    def _add_effect_block(
        self,
        effect: PowerEffectInstance,
        index: int,
        power: Power,
        game_data: GameData,
        char: Character | None = None,
        attack_bonus: int | None = None,
    ) -> None:
        base = next((e for e in game_data.effects if e.id == effect.effect_id), None)
        # The title carries the effective rank so a Strength-Based Damage reads at its
        # boosted rank (e.g. "Damage 13"), matching the DC below.
        rank = effect_effective_rank(effect, game_data, char)
        title = f"{base.name if base else effect.effect_id} {rank}"

        header = QHBoxLayout()
        header.setSpacing(6)
        name = QLabel(title)
        name.setStyleSheet(BOLD_STYLE)
        header.addWidget(name)
        note = self._role_note(power, index, game_data, char)
        if note:
            role = QLabel(note)
            role.setStyleSheet(muted_style(italic=True))
            header.addWidget(role)
        header.addStretch()
        self._layout.addLayout(header)

        rows = effect_stat_rows(effect, game_data, char, attack_bonus)
        self.effect_rows.append(rows)
        grid = build_terms_grid(rows)
        grid.setContentsMargins(12, 0, 0, 0)
        grid.setVerticalSpacing(1)
        self._layout.addLayout(grid)

    # -- Dev-mode editable table ------------------------------------------
    def _render_editable(self, power: Power, game_data: GameData, char: Character | None) -> None:
        """Render the whole table as the homerule override editor: a whole-power cost
        override, then one group per effect (its game-term fields, derived readout rows,
        and custom rows).

        The attack-skill bonus is threaded through exactly as the read-only path does
        it, so ticking Dev mode doesn't restate an effect's Check line without the
        combat focus it is linked to — which would make the by-the-book value look like
        an override and badge the power homerule for correcting it back.

        The cost row is rendered even with **no effects**, where the read-only table
        shows only its placeholder: a hand-set price is a fact about the whole build and
        an equipment accessory — gear that has a price and modifies its host rather than
        doing anything itself — is exactly a build with none.
        """
        self.effect_rows = []
        self._layout.addWidget(self._cost_override_row(power, game_data, char))
        multi = len(power.effects) > 1
        for index, effect in enumerate(power.effects, start=1):
            attack_bonus = effect_attack_skill_bonus(effect, char, game_data)
            rows = effect_stat_rows(effect, game_data, char, attack_bonus)
            self.effect_rows.append(rows)
            self._layout.addWidget(
                self._effect_edit_group(effect, index, multi, rows, game_data, char, attack_bonus)
            )

    def _cost_override_row(
        self, power: Power, game_data: GameData, char: Character | None
    ) -> QWidget:
        host = QWidget()
        row = QHBoxLayout(host)
        row.setContentsMargins(0, 0, 0, 0)
        self._cost_override_check = QCheckBox("Override total cost")
        target = self._cost_target
        stored = target.read() if target is not None else power.cost_override
        overridden = stored is not None
        self._cost_override_check.setChecked(overridden)
        if overridden:
            current = stored
        elif target is not None:
            current = target.derived()
        else:
            current = power_total_cost(power, game_data, char)
        self._cost_override_spin = make_spin_box(
            0, 9999, value=int(current), buttons=False, max_width=72
        )
        self._cost_override_spin.setSuffix(f" {target.unit if target else 'PP'}")
        self._cost_override_spin.setEnabled(overridden)
        self._cost_override_check.toggled.connect(self._on_cost_override_toggled)
        self._cost_override_spin.valueChanged.connect(self._on_cost_override_value)
        row.addWidget(self._cost_override_check)
        row.addWidget(self._cost_override_spin)
        row.addStretch()
        return host

    def _write_cost_override(self, value: int | None) -> None:
        """Store (or clear) the hand-set price, wherever this table's target keeps it."""
        if self._cost_target is not None:
            self._cost_target.write(value)
        elif self._power is not None:
            self._power.cost_override = value

    def _on_cost_override_toggled(self, on: bool) -> None:
        self._cost_override_spin.setEnabled(on)
        self._write_cost_override(self._cost_override_spin.value() if on else None)
        self.edited.emit()

    def _on_cost_override_value(self, value: int) -> None:
        if self._cost_override_check.isChecked():
            self._write_cost_override(value)
            self.edited.emit()

    def _effect_edit_group(
        self,
        effect: PowerEffectInstance,
        ordinal: int,
        multi: bool,
        rows: list,
        game_data: GameData,
        char: Character | None,
        attack_bonus: int | None = None,
    ) -> QWidget:
        base = next((e for e in game_data.effects if e.id == effect.effect_id), None)
        name = base.name if base else effect.effect_id
        box = QGroupBox(f"{ordinal}. {name}" if multi else name)
        outer = QVBoxLayout(box)
        outer.setContentsMargins(6, 4, 6, 4)

        # The auto values every field starts at: the resolved rows this effect would
        # show with *no* overrides at all — but still with its attack-skill link, which
        # is not an override. A field left at its auto value stores no override (so it
        # isn't flagged homerule); anything else does.
        auto_effect = replace(effect, overrides={})
        auto = {
            row.key: row.value
            for row in effect_stat_rows(auto_effect, game_data, char, attack_bonus)
        }

        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(6)
        grid.setColumnStretch(1, 1)
        gr = 0
        for key, label, _attr in _OVERRIDE_STD_FIELDS:
            grid.addWidget(QLabel(label), gr, 0)
            auto_value = auto.get(key, "")
            combo = self._make_value_combo(effect, key, auto_value, game_data, char, attack_bonus)
            order = self._make_order_combo(effect, key)
            self._wire_std_override(effect, key, combo, order, auto_value)
            grid.addWidget(combo, gr, 1)
            grid.addWidget(order, gr, 2)
            gr += 1

        # Derived readout / DC / measure rows — each a verbatim ("after") replacement,
        # pre-filled with its auto value.
        derived_rows = [
            row
            for row in rows
            if row.key not in _OVERRIDE_STD_KEYS
            and row.key != "notes"
            and not row.key.startswith("custom_")
        ]
        if derived_rows:
            grid.addWidget(hline_separator(), gr, 0, 1, 3)
            gr += 1
            for row in derived_rows:
                grid.addWidget(QLabel(row.label), gr, 0)
                auto_value = auto.get(row.key, row.value)
                entry = effect.overrides.get(row.key)
                edit = QLineEdit(str(entry.get("value", "")) if entry else auto_value)
                self._wire_derived_override(effect, row.key, row.label, edit, auto_value)
                grid.addWidget(edit, gr, 1, 1, 2)
                gr += 1
        outer.addLayout(grid)

        # Custom rows: player-added label/value pairs (always applied after modifiers).
        custom_host = QWidget()
        custom_layout = QVBoxLayout(custom_host)
        custom_layout.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(custom_host)
        for key in sorted(k for k in effect.overrides if k.startswith("custom_")):
            self._add_custom_row_widget(effect, custom_layout, key)
        add_button = QPushButton("＋ Add custom row")
        add_button.clicked.connect(
            lambda _=False, e=effect, cl=custom_layout: self._add_custom_row_widget(
                e, cl, self._next_custom_key(e)
            )
        )
        outer.addWidget(add_button, alignment=Qt.AlignmentFlag.AlignLeft)
        return box

    def _field_options(
        self,
        effect: PowerEffectInstance,
        field_key: str,
        attr: str,
        game_data: GameData,
        char: Character | None,
        attack_bonus: int | None = None,
    ) -> list[str]:
        """Resolved dropdown choices for a standard field.

        Gathers the raw candidates — the field's game-term ladder first, then every
        other value that field takes across the effect catalog (all data-driven) — and
        resolves each to this effect's concrete numbers (a resistance's save DC, a
        ``"Rank"`` range's distance), so the list offers ``"Will vs. 18"`` rather than
        the bare ``"Will vs. Effect"`` template. Order preserved, duplicates dropped.
        """
        raw = list(game_data.game_term_ladders.get(field_key, ()))
        for candidate in game_data.effects:
            value = getattr(candidate, attr, None)
            if value and value not in raw:
                raw.append(value)
        options: list[str] = []
        for value in raw:
            resolved = resolve_stat_display(effect, game_data, field_key, value, char, attack_bonus)
            if resolved and resolved not in options:
                options.append(resolved)
        return options

    def _make_value_combo(
        self,
        effect: PowerEffectInstance,
        key: str,
        auto_value: str,
        game_data: GameData,
        char: Character | None,
        attack_bonus: int | None = None,
    ) -> QComboBox:
        attr = next(a for k, _, a in _OVERRIDE_STD_FIELDS if k == key)
        combo = QComboBox()
        combo.setEditable(True)
        options = self._field_options(effect, key, attr, game_data, char, attack_bonus)
        # The auto (un-overridden) value is always selectable, so re-picking it clears
        # the override.
        if auto_value and auto_value not in options:
            options.insert(0, auto_value)
        for option in options:
            combo.addItem(option)
        entry = effect.overrides.get(key)
        # Start at the stored override, or the auto value when there's none.
        combo.setCurrentText(str(entry.get("value", "")) if entry else auto_value)
        # A long option ("Chosen resistance vs. DC 11") must not blow the column out and
        # push the order selector off-screen — let the combo shrink and stretch instead.
        combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        combo.setMinimumContentsLength(6)
        combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        combo.setMinimumWidth(80)
        guard_wheel(combo)
        return combo

    def _make_order_combo(self, effect: PowerEffectInstance, key: str) -> QComboBox:
        combo = QComboBox()
        combo.addItem("after", "after")
        combo.addItem("before", "before")
        combo.setToolTip(
            "After: your value wins over modifiers. "
            "Before: modifiers still apply on top of your value."
        )
        combo.setFixedWidth(72)  # compact so the value combo keeps the width
        entry = effect.overrides.get(key)
        order = entry.get("order", "after") if entry else "after"
        combo.setCurrentIndex(1 if order == "before" else 0)
        guard_wheel(combo)
        return combo

    def _wire_std_override(
        self,
        effect: PowerEffectInstance,
        key: str,
        combo: QComboBox,
        order: QComboBox,
        auto_value: str,
    ) -> None:
        def commit(*_args) -> None:
            value = combo.currentText().strip()
            # Leaving the field at its auto value (or blank) means "no override".
            if value and value != auto_value:
                effect.overrides[key] = {"value": value, "order": order.currentData()}
            else:
                effect.overrides.pop(key, None)
            self.edited.emit()

        combo.currentTextChanged.connect(commit)
        order.currentIndexChanged.connect(commit)

    def _wire_derived_override(
        self,
        effect: PowerEffectInstance,
        key: str,
        label: str,
        edit: QLineEdit,
        auto_value: str,
    ) -> None:
        def commit(text: str) -> None:
            value = text.strip()
            if value and value != auto_value:
                effect.overrides[key] = {"value": value, "order": "after", "label": label}
            else:
                effect.overrides.pop(key, None)
            self.edited.emit()

        edit.textChanged.connect(commit)

    def _next_custom_key(self, effect: PowerEffectInstance) -> str:
        used = [
            int(k.split("_", 1)[1])
            for k in effect.overrides
            if k.startswith("custom_") and k.split("_", 1)[1].isdigit()
        ]
        return f"custom_{max(used, default=0) + 1}"

    def _add_custom_row_widget(
        self, effect: PowerEffectInstance, layout: QVBoxLayout, key: str
    ) -> None:
        entry = effect.overrides.get(key, {})
        row = QWidget()
        line = QHBoxLayout(row)
        line.setContentsMargins(0, 0, 0, 0)
        label_edit = QLineEdit(str(entry.get("label", "")))
        label_edit.setPlaceholderText("Label")
        value_edit = QLineEdit(str(entry.get("value", "")))
        value_edit.setPlaceholderText("Value")
        remove = QPushButton("✕")
        remove.setFlat(True)
        remove.setFixedWidth(22)
        line.addWidget(label_edit, 1)
        line.addWidget(value_edit, 1)
        line.addWidget(remove)
        layout.addWidget(row)

        def commit(*_args) -> None:
            value = value_edit.text().strip()
            if value:
                effect.overrides[key] = {
                    "value": value,
                    "order": "after",
                    "label": label_edit.text().strip() or key,
                }
            else:
                effect.overrides.pop(key, None)
            self.edited.emit()

        def do_remove(*_args) -> None:
            effect.overrides.pop(key, None)
            discard_widget(row)
            self.edited.emit()

        label_edit.textChanged.connect(commit)
        value_edit.textChanged.connect(commit)
        remove.clicked.connect(do_remove)

    @staticmethod
    def _structure_header(power: Power) -> str:
        """The shared sentence, with the colon this view has always drawn.

        The constructor edits a **deep copy** of the sheet's power, shares and all, so a
        power opened while its own effects are split says so here too rather than going
        on promising the mutual exclusion the split has lifted. Same words as the card
        and the game-term summary, from :func:`~mm_companion.core.rules.structure_header`
        — this used to be a third copy of them.
        """

        header = structure_header(power)
        return f"{header}:" if header else ""

    @staticmethod
    def _role_note(
        power: Power, index: int, game_data: GameData, char: Character | None = None
    ) -> str:
        if len(power.effects) < 2 or power.structure != STRUCTURE_ARRAY:
            return ""
        effect = power.effects[index]
        if index == array_base_index(power, game_data, char):
            return "base, Dynamic" if effect.dynamic else "base"
        return array_member_note(effect.dynamic, game_data)

    def _clear(self) -> None:
        self.effect_rows = []
        self._take_all(self._layout)

    def _take_all(self, layout) -> None:
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                discard_widget(widget)
            elif item.layout() is not None:
                self._take_all(item.layout())
                item.layout().deleteLater()

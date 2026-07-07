"""The conditions block: the character's applied conditions as removable chips.

Conditions are a runtime state-tracker, not part of the point build: the "+"
menu applies a condition (prompting a :class:`ConditionParameterDialog` first
when it needs a subject) and the section renders one chip per
:class:`~mm_companion.core.character.AppliedCondition`, split into titled
category groups (General / Damage). Applying or removing a condition writes the
change through :func:`~mm_companion.core.rules.apply_condition` /
:func:`~mm_companion.core.rules.decrement_condition` on the shared
:class:`Character` and emits :attr:`conditionsChanged`, so the sheet repaints the
stat rows a condition's penalty overlays.

Conditions stay editable in both view modes — they change constantly during play,
unlike the rest of the build — so :meth:`set_locked` is a deliberate no-op.
"""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMenu,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from mm_companion.core.character import AppliedCondition, Character
from mm_companion.core.data_loader import Condition, GameData
from mm_companion.core.rules import (
    apply_condition,
    decrement_condition,
    roll_confused_action,
)
from mm_companion.ui.flow_layout import FlowContainer, FlowLayout
from mm_companion.ui.sections.titled_section import strip_groupbox_caption
from mm_companion.ui.widgets import hline_separator

CONDITIONS_ROW_HEIGHT = 44
# Reserve enough height for the "+" header plus one category section (title, rule,
# and a single row of chips) so applying the first condition fills pre-allocated
# space instead of growing the box. Only a second chip row makes it grow.
CONDITIONS_MIN_HEIGHT = 150
# The chip groups the conditions box splits into, in display order.
CONDITION_CATEGORY_SECTIONS = (("condition", "General"), ("damage_condition", "Damage"))


class ConditionsSection(QGroupBox):
    """The character's applied conditions, added via a "+" button and shown as chips.

    Edits are written to the shared :class:`Character`. Emits :attr:`edited` for
    unsaved-change tracking and :attr:`conditionsChanged` so the sheet repaints
    the stat sections a condition's penalty overlays.
    """

    # Conditions never change the point build, so :attr:`changed` is declared only
    # so the sheet can treat every block uniformly; it is never emitted.
    changed = Signal()
    edited = Signal()
    conditionsChanged = Signal()

    def __init__(self, data: GameData, character: Character, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        strip_groupbox_caption(self)

        # While seeding from a (possibly loaded) character, edits are programmatic,
        # not the user's, so they must not mark the sheet dirty.
        self._loading = True
        self._data = data
        self._character = character
        self._conditions_by_id: dict[str, Condition] = {c.id: c for c in data.conditions}
        # Conditions the "+" menu offers — statuses that apply to a character (not the
        # object-damage ladder or the "normal" bookkeeping marker).
        self._addable_conditions: list[Condition] = [
            c for c in data.conditions if c.category in ("condition", "damage_condition")
        ]
        self._condition_chips: list[QFrame] = []
        # Ephemeral last-rolled Confused action, keyed by (condition_id, parameter);
        # runtime combat state, not saved with the character.
        self._confused_rolls: dict[tuple[str, str | None], str] = {}

        self.setMinimumHeight(CONDITIONS_MIN_HEIGHT)
        outer = QVBoxLayout(self)

        header = QHBoxLayout()
        self._add_condition_button = QToolButton()
        self._add_condition_button.setText("+")
        self._add_condition_button.setToolTip("Add a condition")
        self._add_condition_button.clicked.connect(self._show_condition_menu)
        header.addWidget(self._add_condition_button)
        header.addStretch()
        outer.addLayout(header)

        # One titled sub-group of chips per category (General / Damage), each with a
        # header + rule, hidden until it holds a chip.
        self._category_flows: dict[str, FlowLayout] = {}
        self._category_sections: dict[str, tuple[QLabel, QWidget, QWidget]] = {}
        for category, title in CONDITION_CATEGORY_SECTIONS:
            head = QLabel(title)
            head.setStyleSheet("font-weight: bold; color: palette(mid); padding-top: 4px;")
            rule = hline_separator()
            container = FlowContainer()
            container.setMinimumHeight(CONDITIONS_ROW_HEIGHT)
            self._category_flows[category] = FlowLayout(container)
            outer.addWidget(head)
            outer.addWidget(rule)
            outer.addWidget(container)
            self._category_sections[category] = (head, rule, container)
        outer.addStretch()

        # Reflect any conditions a loaded character already carries.
        self._render_conditions()
        self._loading = False

    def _emit_edited(self) -> None:
        """Signal a user edit, unless we're still seeding from the model."""
        if not self._loading:
            self.edited.emit()

    def _show_condition_menu(self) -> None:
        menu = QMenu(self)
        for cond in sorted(self._addable_conditions, key=lambda c: c.name):
            menu.addAction(cond.name, lambda checked=False, c=cond: self._choose_condition(c))
        button = self._add_condition_button
        menu.exec(button.mapToGlobal(button.rect().bottomLeft()))

    def _choose_condition(self, condition: Condition) -> None:
        """Apply a picked condition, prompting for its parameter first if it needs one."""
        # Imported lazily to avoid a construction-time cycle through the dialog.
        from mm_companion.ui.sections.condition_dialog import ConditionParameterDialog

        parameter: str | None = None
        if condition.parameter is not None:
            dialog = ConditionParameterDialog(condition, self._data, self._character, self)
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return
            parameter = dialog.value()
        apply_condition(self._character, condition.id, self._data, parameter=parameter)
        self._render_conditions()
        self._emit_conditions_changed()

    def _shed_condition(self, applied: AppliedCondition) -> None:
        """Remove-button handler: peel one Hit off its stack, else drop the condition."""
        decrement_condition(self._character, applied)
        self._render_conditions()
        self._emit_conditions_changed()

    def _emit_conditions_changed(self) -> None:
        self._emit_edited()
        self.conditionsChanged.emit()

    @staticmethod
    def _clear_flow(flow: FlowLayout) -> None:
        """Empty a flow layout, reparenting each chip out immediately so no ghost
        frames linger until ``deleteLater`` is serviced."""
        while flow.count():
            item = flow.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()

    def _render_conditions(self) -> None:
        """Rebuild the chip groups from the model so a directly-applied condition, its
        bundled members, supersession, and stacking all stay 1:1 with the state, sorted
        into their category groups (empty groups hide).
        """
        for flow in self._category_flows.values():
            self._clear_flow(flow)
        self._condition_chips = []
        used: set[str] = set()
        for applied in self._character.conditions:
            record = self._conditions_by_id.get(applied.condition_id)
            category = record.category if record else "condition"
            if category not in self._category_flows:
                category = "condition"
            chip = self._build_condition_chip(applied, record)
            self._condition_chips.append(chip)
            self._category_flows[category].addWidget(chip)
            used.add(category)
        for category, (head, rule, container) in self._category_sections.items():
            for widget in (head, rule, container):
                widget.setVisible(category in used)

    def _build_condition_chip(self, applied: AppliedCondition, record: Condition | None) -> QFrame:
        name = self._condition_display_name(applied, record)

        chip = QFrame()
        chip.setFrameShape(QFrame.Shape.StyledPanel)
        chip.setToolTip(self._condition_tooltip(applied, record))
        chip_layout = QHBoxLayout(chip)
        chip_layout.setContentsMargins(6, 1, 2, 1)
        chip_layout.setSpacing(2)

        label = QLabel(name)
        # A bundled member (granted by an umbrella) reads in italic so it's clearly
        # secondary to the directly applied conditions, while staying legible in both
        # light and dark themes (a muted colour vanished on the dark theme).
        if applied.provenance is not None:
            font = label.font()
            font.setItalic(True)
            label.setFont(font)
        chip_layout.addWidget(label)

        # Confused: the turn's action is rolled, not chosen — a die button rolls it
        # and the last outcome shows inline.
        if applied.condition_id == "confused":
            rolled = self._confused_rolls.get(self._confused_key(applied))
            if rolled:
                outcome = QLabel(f"— {rolled}")
                # Italic (not a muted colour, which vanished on the dark theme) so the
                # rolled action stays legible while reading as secondary to the name.
                font = outcome.font()
                font.setItalic(True)
                outcome.setFont(font)
                chip_layout.addWidget(outcome)
            roll_button = QToolButton()
            roll_button.setText("🎲")
            roll_button.setAutoRaise(True)
            roll_button.setToolTip("Roll this turn's random action")
            roll_button.clicked.connect(lambda checked=False, a=applied: self._roll_confused(a))
            chip_layout.addWidget(roll_button)

        remove = QToolButton()
        remove.setText("×")
        remove.setAutoRaise(True)
        remove.setToolTip(f"Remove {name}")
        remove.clicked.connect(lambda checked=False, a=applied: self._shed_condition(a))
        chip_layout.addWidget(remove)
        return chip

    @staticmethod
    def _confused_key(applied: AppliedCondition) -> tuple[str, str | None]:
        return (applied.condition_id, applied.parameter)

    def _roll_confused(self, applied: AppliedCondition) -> None:
        die, row = roll_confused_action(self._character, self._data)
        outcome = row.outcome if row is not None else "no result"
        self._confused_rolls[self._confused_key(applied)] = f"{die}: {outcome}"
        self._render_conditions()

    def _condition_display_name(self, applied: AppliedCondition, record: Condition | None) -> str:
        """Fold the chosen parameter and stacking count into the shown name (§6):
        ``Impaired`` + ``Attack`` → "Attack Impaired"; ``Hit`` ×3 → "Hit ×3".
        """
        name = record.name if record else applied.condition_id
        if applied.parameter:
            ptype = record.parameter.type if record and record.parameter else ""
            if ptype in ("trait_select", "sense_select"):
                name = f"{applied.parameter} {name}"
            else:
                name = f"{name} ({applied.parameter})"
        if applied.count > 1:
            name = f"{name} ×{applied.count}"
        return name

    def _condition_tooltip(self, applied: AppliedCondition, record: Condition | None) -> str:
        if record is None:
            return ""
        parts: list[str] = []
        if applied.provenance is not None:
            umbrella = self._conditions_by_id.get(applied.provenance)
            parts.append(f"via {umbrella.name if umbrella else applied.provenance}")
        if record.effect:
            parts.append(record.effect)
        if record.recovery and record.recovery != "n/a":
            parts.append(f"Recovery: {record.recovery}")
        return "\n\n".join(parts)

    def set_locked(self, locked: bool) -> None:
        """No-op: conditions stay editable in either view mode — they change
        constantly during play, unlike the rest of the build."""

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

from collections.abc import Callable

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
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
from mm_companion.core.components import MECH_RANDOM_ACTION
from mm_companion.core.data_loader import Condition, GameData
from mm_companion.core.rules import (
    apply_condition,
    decrement_condition,
    roll_confused_action,
)
from mm_companion.ui import theme
from mm_companion.ui.flow_layout import FlowContainer, FlowLayout
from mm_companion.ui.sections.titled_section import strip_groupbox_caption
from mm_companion.ui.widgets import attach_context_removal, hline_separator

CONDITIONS_ROW_HEIGHT = 44
# Reserve enough height for the "+" header plus one category section (title, rule,
# and a single row of chips) so applying the first condition fills pre-allocated
# space instead of growing the box. Only a second chip row makes it grow.
CONDITIONS_MIN_HEIGHT = 150


def addable_categories(data: GameData) -> tuple[str, ...]:
    """The catalog categories a "+" menu offers.

    Statuses that apply to a character — not the object-damage ladder or the
    ``normal`` bookkeeping marker. Read from ``conditions.json``'s ``sheetSections``,
    so a mod's own category is offered without a code change.
    """
    return tuple(c.category for c in data.condition_categories if c.addable)


def addable_conditions(data: GameData) -> list[Condition]:
    """The conditions a "+" menu may apply, in catalog order.

    Module-level because three menus offer the same list: this block's own "+",
    and the GM's fast-apply on a player card and on an NPC card. All three reach
    it through :func:`build_condition_menu`.
    """
    addable = addable_categories(data)
    return [c for c in data.conditions if c.category in addable]


def build_condition_menu(
    parent: QWidget,
    data: GameData,
    on_pick: Callable[[Condition], None],
) -> QMenu:
    """The "+" menu, split into one submenu per ``_meta.conditionGroups`` entry.

    Module-level because three menus offer the same catalog — this block's own
    "+", and the GM's fast-apply on a player card and on an NPC card — and a
    condition should be in the same place in all three.

    The split is a finding aid: 36 conditions in one alphabetical list is slow to
    search mid-round. A condition whose ``group`` no submenu claims is appended
    flat at the end rather than dropped, so a mod that adds one without tagging it
    is still playable; a ruleset that declares no groups at all gets the old flat
    menu back untouched.
    """
    menu = QMenu(parent)
    conditions = addable_conditions(data)
    by_name = sorted(conditions, key=lambda c: c.name)

    def add_to(target: QMenu, items: list[Condition]) -> None:
        for condition in items:
            target.addAction(
                condition.name,
                lambda checked=False, c=condition: on_pick(c),
            )

    claimed: set[str] = set()
    for group in data.condition_groups:
        members = [c for c in by_name if c.group == group.group]
        if not members:
            continue
        claimed.add(group.group)
        # Constructed with *menu* as its parent, not through ``menu.addMenu(title)``:
        # that convenience overload hands ownership of the new menu back to the
        # caller, so a submenu with no Python reference is collected out from under
        # the open menu. Parenting it makes the menu keep it alive instead.
        submenu = QMenu(group.title, menu)
        menu.addMenu(submenu)
        add_to(submenu, members)
    leftovers = [c for c in by_name if c.group not in claimed]
    if leftovers:
        # Only ever a separator between real submenus and the stragglers; an
        # ungrouped ruleset produces the flat list with nothing above it.
        if claimed:
            menu.addSeparator()
        add_to(menu, leftovers)
    return menu


def matching_condition(
    character: Character, condition_id: str, parameter: str | None = None
) -> AppliedCondition | None:
    """The applied condition a by-id removal should shed, or ``None``.

    Matches on the parameter too, so removing "Attack Impaired" leaves "Dodge
    Impaired" alone, and prefers a directly applied instance over a bundled member
    — dropping the umbrella is what taking a condition off means. Module-level so
    the sheet's own chips and the GM's fast-apply on a card (player or NPC) shed
    a condition identically.
    """
    matches = [
        applied
        for applied in character.conditions
        if applied.condition_id == condition_id and applied.parameter == parameter
    ]
    if not matches:
        return None
    direct = [applied for applied in matches if applied.provenance is None]
    return direct[0] if direct else matches[0]


def condition_display_name(applied: AppliedCondition, record: Condition | None) -> str:
    """Fold the chosen parameter and stacking count into the shown name (§6):
    ``Impaired`` + ``Attack`` → "Attack Impaired"; ``Hit`` ×3 → "Hit ×3".

    Module-level because a condition reads the same wherever it is shown — this
    block's chips and the GM window's player cards both name them through here.
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


def condition_tooltip(
    applied: AppliedCondition,
    record: Condition | None,
    conditions_by_id: dict[str, Condition],
) -> str:
    """The hover hint for one applied condition: origin, effect, and recovery.

    Module-level because a condition reads the same wherever it is shown — this
    block's chips and the GM window's player cards both hint through here.
    """
    if record is None:
        return ""
    parts: list[str] = []
    if applied.provenance is not None:
        umbrella = conditions_by_id.get(applied.provenance)
        parts.append(f"via {umbrella.name if umbrella else applied.provenance}")
    if record.effect:
        parts.append(record.effect)
    if record.recovery and record.recovery != "n/a":
        parts.append(f"Recovery: {record.recovery}")
    return "\n\n".join(parts)


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

        # One titled sub-group of chips per category (General / Damage / …), each with
        # a header + rule, hidden until it holds a chip. The groups and their order come
        # from the data, so a mod's category gets one of its own.
        self._category_flows: dict[str, FlowLayout] = {}
        self._category_sections: dict[str, tuple[QLabel, QWidget, FlowContainer]] = {}
        self._fallback_category = (
            data.condition_categories[0].category if data.condition_categories else "condition"
        )
        for section in data.condition_categories:
            category, title = section.category, section.title
            head = QLabel(title)
            head.setStyleSheet(
                f"font-weight: bold; color: {theme.color('text.muted')};"
                f" padding-top: {int(theme.metric('space.sm'))}px;"
            )
            rule = hline_separator()
            container = FlowContainer()
            container.set_minimum_row_height(CONDITIONS_ROW_HEIGHT)
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
        menu = build_condition_menu(self, self._data, self._choose_condition)
        button = self._add_condition_button
        menu.exec(button.mapToGlobal(button.rect().bottomLeft()))

    def _choose_condition(self, condition: Condition) -> None:
        """Apply a picked condition, prompting for its parameter first if it needs one."""
        # Imported lazily to avoid a construction-time cycle through the dialog.
        from mm_companion.ui.sections.condition_dialog import prompt_condition_parameter

        go_ahead, parameter = prompt_condition_parameter(
            condition, self._data, self._character, self
        )
        if go_ahead:
            self.apply_condition_by_id(condition.id, parameter)

    def apply_condition_by_id(self, condition_id: str, parameter: str | None = None) -> bool:
        """Apply a condition by id, with no menu and no prompt.

        The seam a *remote* GM applies through (``ui/session_player.py``), so a
        condition the GM sends bundles, supersedes and stacks through the same
        resolver a locally applied one does — and marks the sheet dirty the same
        way. Returns whether the id was one the catalog knows.
        """
        if condition_id not in self._conditions_by_id:
            return False
        apply_condition(self._character, condition_id, self._data, parameter=parameter)
        self._render_conditions()
        self._emit_conditions_changed()
        return True

    def remove_condition_by_id(self, condition_id: str, parameter: str | None = None) -> bool:
        """Shed one instance of a condition by id; the twin of
        :meth:`apply_condition_by_id`.

        Matches on the parameter too, so removing "Attack Impaired" leaves
        "Dodge Impaired" alone, and prefers a directly applied instance over a
        bundled member — dropping the umbrella is what the GM means by taking the
        condition off. Returns whether anything was on the character to remove.
        """
        applied = matching_condition(self._character, condition_id, parameter)
        if applied is None:
            return False
        self._shed_condition(applied)
        return True

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

    def reseed(self) -> None:
        """Restate the chips from the model — the sheet put an earlier state back."""
        self._render_conditions()

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
            category = record.category if record else self._fallback_category
            if category not in self._category_flows:
                category = self._fallback_category
            chip = self._build_condition_chip(applied, record)
            self._condition_chips.append(chip)
            self._category_flows[category].addWidget(chip)
            used.add(category)
        for category, (head, rule, container) in self._category_sections.items():
            for widget in (head, rule, container):
                widget.setVisible(category in used)
        self._refit_containers()

    def _refit_containers(self) -> None:
        """Re-fit each chip container to its current content.

        ``FlowContainer`` re-pins its own height as chips come and go, but a group
        that was hidden while it changed had no width to measure at; re-pin here now
        that visibility has been settled, and let the geometry change bubble up so
        the enclosing ``BlockFrame`` re-queries its size hint.
        """
        for _head, _rule, container in self._category_sections.values():
            container.refresh_height()
        self.updateGeometry()

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

        # A condition whose turn's action is rolled rather than chosen (Confused) gets
        # a die button, with the last outcome shown inline. Driven off the record's
        # declared mechanism, not its id, so a mod's own random-action condition works.
        if record is not None and MECH_RANDOM_ACTION in record.mechanisms:
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

        # Right-click, not a "×" button — the same gesture the GM cards' chips use,
        # so shedding a condition is one thing to learn wherever a chip appears.
        # Installed last so it wins over nothing: the die button above keeps its own
        # left-click, and the two do not collide.
        attach_context_removal(chip, lambda a=applied: self._shed_condition(a), what=name)
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
        return condition_display_name(applied, record)

    def _condition_tooltip(self, applied: AppliedCondition, record: Condition | None) -> str:
        return condition_tooltip(applied, record, self._conditions_by_id)

    def set_locked(self, locked: bool) -> None:
        """No-op: conditions stay editable in either view mode — they change
        constantly during play, unlike the rest of the build."""

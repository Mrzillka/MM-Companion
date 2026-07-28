"""A small modal that asks for the subject a parameterized condition needs.

Some conditions can't be fully applied until the user names *what* they concern —
which trait (Impaired → "Attack Impaired"), sense (Unaware → "Sight Unaware"),
descriptor (Susceptible to "Fire Damage"), or controller (Controlled by whom). See
``docs/mm-conditions-design.md`` §6. This dialog renders the right control for the
condition's :class:`~mm_companion.core.data_loader.ConditionParameter` and gates its
OK button when the parameter is ``required``.

For a ``trait_select`` scope, picking a placeholder like "a specific ability" / "a
specific skill" / "a specific advantage" / "a specific power" reveals a second combo of
the concrete traits, so the stored value is one the stat sections can match against a row
(Debilitated can name any of the four; Impaired/Disabled only an ability or skill).
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QVBoxLayout,
    QWidget,
)

from mm_companion.core.character import Character
from mm_companion.core.data_loader import Condition, GameData
from mm_companion.ui.wheel_guard import guard_wheel

# Which options mean "no scope" and which open a second combo is declared per option
# in ``conditions.json`` (see :class:`ConditionParameterOption`), not matched against
# their prose here — rewording an option in the data must not silently change what a
# choice stores. The kinds below are just the ones this dialog knows how to list.
_SPECIFIC_KINDS = frozenset({"ability", "skill", "advantage", "power"})


class ConditionParameterDialog(QDialog):
    """Prompt for a condition's parameter value; :meth:`value` returns it (or ``None``)."""

    def __init__(
        self,
        condition: Condition,
        game_data: GameData,
        character: Character,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        param = condition.parameter
        assert param is not None  # only opened for parameterized conditions
        self._param = param
        self._data = game_data
        self._character = character
        # value -> its declared flags, for the two lookups below.
        self._option_specs = {spec.value: spec for spec in param.option_specs}
        self.setWindowTitle(condition.name)

        layout = QVBoxLayout(self)
        if param.help:
            help_label = QLabel(param.help)
            help_label.setWordWrap(True)
            layout.addWidget(help_label)

        form = QFormLayout()
        self._input = self._build_input()
        form.addRow(f"{param.label}:", self._input)

        # A second combo revealed only when the scope is "a specific ability/skill".
        self._specific = QComboBox()
        self._specific.setEditable(True)
        guard_wheel(self._specific)
        self._specific.editTextChanged.connect(self._sync_ok)
        self._specific_row = QLabel("Which:")
        form.addRow(self._specific_row, self._specific)
        self._set_specific_visible(False)
        layout.addLayout(form)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        self._ok_button = buttons.button(QDialogButtonBox.StandardButton.Ok)
        layout.addWidget(buttons)

        self._sync_specific()
        self._sync_ok()

    def _build_input(self) -> QWidget:
        """A free-entry combo for a select with options, else a plain line edit."""

        if self._param.options:
            combo = QComboBox()
            combo.setEditable(True)
            combo.addItems(list(self._param.options))
            combo.currentTextChanged.connect(self._on_scope_changed)
            combo.editTextChanged.connect(self._on_scope_changed)
            guard_wheel(combo)
            return combo
        edit = QLineEdit()
        edit.textChanged.connect(self._sync_ok)
        return edit

    def _set_specific_visible(self, visible: bool) -> None:
        self._specific_row.setVisible(visible)
        self._specific.setVisible(visible)

    def _on_scope_changed(self, *_: object) -> None:
        self._sync_specific()
        self._sync_ok()

    def _specific_kind(self) -> str | None:
        """The trait kind the chosen option names as its ``specificKind`` (or ``None``).

        Only the kinds this dialog can actually list concrete choices for count; a
        placeholder naming anything else stays a plain free-text scope.
        """

        spec = self._option_specs.get(self._primary_value())
        if spec is None or not spec.specific_kind:
            return None
        kind = spec.specific_kind.lower()
        return kind if kind in _SPECIFIC_KINDS else None

    def _specific_options(self, kind: str) -> list[str]:
        """Concrete choices for a "a specific <kind>" pick, drawn from data/character."""

        if kind == "ability":
            return [a.name for a in self._data.abilities]
        if kind == "skill":
            return [s.name for s in self._data.skills]
        if kind == "advantage":
            return [a.name for a in self._character.advantages]
        if kind == "power":
            return [p.name for p in self._character.powers if p.name]
        return []

    def _sync_specific(self) -> None:
        """Show/populate the second combo when a "specific …" scope is chosen."""

        kind = self._specific_kind()
        if kind is None:
            self._set_specific_visible(False)
            return
        options = self._specific_options(kind)
        if [self._specific.itemText(i) for i in range(self._specific.count())] != options:
            self._specific.clear()
            self._specific.addItems(options)
        self._set_specific_visible(True)

    def _primary_value(self) -> str:
        if isinstance(self._input, QComboBox):
            return self._input.currentText().strip()
        return self._input.text().strip()

    def value(self) -> str | None:
        """The chosen subject, or ``None`` for an unscoped/blank selection.

        A "specific …" scope resolves to the concrete trait picked in the second combo.
        """

        if self._specific_kind() is not None:
            chosen = self._specific.currentText().strip()
            return chosen or None
        primary = self._primary_value()
        spec = self._option_specs.get(primary)
        if not primary or (spec is not None and spec.unscoped):
            return None
        return primary

    def _sync_ok(self, *_: object) -> None:
        # A required parameter must resolve to a non-blank subject before OK is allowed.
        needs_specific = self._specific_kind() is not None
        blocked = (self._param.required and not self._primary_value()) or (
            needs_specific and not self._specific.currentText().strip()
        )
        self._ok_button.setEnabled(not blocked)


def prompt_condition_parameter(
    condition: Condition,
    game_data: GameData,
    character: Character,
    parent: QWidget | None = None,
) -> tuple[bool, str | None]:
    """Ask for a condition's subject when it needs one, before applying it.

    Returns ``(go_ahead, parameter)`` — ``(False, None)`` when the user cancelled.
    A condition with no parameter never opens a dialog and answers
    ``(True, None)``.

    Shared because a condition is picked in two places that must behave
    identically: the sheet's own conditions block, and the GM's fast-apply menu on
    a player card.
    """

    if condition.parameter is None:
        return True, None
    dialog = ConditionParameterDialog(condition, game_data, character, parent)
    if dialog.exec() != QDialog.DialogCode.Accepted:
        return False, None
    return True, dialog.value()

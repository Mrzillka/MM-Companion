"""A small modal that asks for the subject a parameterized condition needs.

Some conditions can't be fully applied until the user names *what* they concern —
which trait (Impaired → "Attack Impaired"), sense (Unaware → "Sight Unaware"),
descriptor (Susceptible to "Fire Damage"), or controller (Controlled by whom). See
``mm-conditions-design.md`` §6. This dialog renders the right control for the
condition's :class:`~mm_companion.core.data_loader.ConditionParameter` and gates its
OK button when the parameter is ``required``.

For a ``trait_select`` scope, picking the placeholder "a specific ability" / "a
specific skill" reveals a second combo of the concrete traits, so the stored value is
one the stat sections can match against a row.
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

# Option values that mean "no scope" rather than a real subject — normalized to None.
UNSCOPED_VALUES = {"", "All checks", "All senses"}
# Placeholder options that open a second combo of concrete traits.
SPECIFIC_ABILITY = "a specific ability"
SPECIFIC_SKILL = "a specific skill"


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

    def _sync_specific(self) -> None:
        """Show/populate the second combo when a "specific …" scope is chosen."""

        choice = self._primary_value()
        if choice == SPECIFIC_ABILITY:
            options = [a.name for a in self._data.abilities]
        elif choice == SPECIFIC_SKILL:
            options = [s.name for s in self._data.skills]
        else:
            self._set_specific_visible(False)
            return
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

        primary = self._primary_value()
        if primary in (SPECIFIC_ABILITY, SPECIFIC_SKILL):
            chosen = self._specific.currentText().strip()
            return chosen or None
        return None if primary in UNSCOPED_VALUES else primary

    def _sync_ok(self, *_: object) -> None:
        # A required parameter must resolve to a non-blank subject before OK is allowed.
        needs_specific = self._primary_value() in (SPECIFIC_ABILITY, SPECIFIC_SKILL)
        blocked = (self._param.required and not self._primary_value()) or (
            needs_specific and not self._specific.currentText().strip()
        )
        self._ok_button.setEnabled(not blocked)

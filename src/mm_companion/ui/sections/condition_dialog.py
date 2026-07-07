"""A small modal that asks for the subject a parameterized condition needs.

Some conditions can't be fully applied until the user names *what* they concern —
which trait (Impaired → "Attack Impaired"), sense (Unaware → "Sight Unaware"),
descriptor (Susceptible to "Fire Damage"), or controller (Controlled by whom). See
``mm-conditions-design.md`` §6. This dialog renders the right control for the
condition's :class:`~mm_companion.core.data_loader.ConditionParameter` and gates its
OK button when the parameter is ``required``.
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

from mm_companion.core.data_loader import Condition
from mm_companion.ui.wheel_guard import guard_wheel

# Option values that mean "no scope" rather than a real subject — normalized to None.
UNSCOPED_VALUES = {"", "All checks", "All senses"}


class ConditionParameterDialog(QDialog):
    """Prompt for a condition's parameter value; :meth:`value` returns it (or ``None``)."""

    def __init__(self, condition: Condition, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        param = condition.parameter
        assert param is not None  # only opened for parameterized conditions
        self._param = param
        self.setWindowTitle(condition.name)

        layout = QVBoxLayout(self)
        if param.help:
            help_label = QLabel(param.help)
            help_label.setWordWrap(True)
            layout.addWidget(help_label)

        form = QFormLayout()
        self._input = self._build_input()
        form.addRow(f"{param.label}:", self._input)
        layout.addLayout(form)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        self._ok_button = buttons.button(QDialogButtonBox.StandardButton.Ok)
        layout.addWidget(buttons)

        self._sync_ok()

    def _build_input(self) -> QWidget:
        """A free-entry combo for a select with options, else a plain line edit.

        Select types stay editable so a placeholder like "a specific ability" can be
        typed over with the real trait; ``descriptor_text`` / ``character_ref`` (no
        options) get a line edit — the latter a fallback until an encounter roster
        exists to pick from.
        """

        if self._param.options:
            combo = QComboBox()
            combo.setEditable(True)
            combo.addItems(list(self._param.options))
            combo.currentTextChanged.connect(self._sync_ok)
            combo.editTextChanged.connect(self._sync_ok)
            guard_wheel(combo)
            return combo
        edit = QLineEdit()
        edit.textChanged.connect(self._sync_ok)
        return edit

    def _raw_value(self) -> str:
        if isinstance(self._input, QComboBox):
            return self._input.currentText().strip()
        return self._input.text().strip()

    def value(self) -> str | None:
        """The chosen subject, or ``None`` for an unscoped/blank selection."""

        raw = self._raw_value()
        return None if raw in UNSCOPED_VALUES else raw

    def _sync_ok(self, *_: object) -> None:
        # A required parameter must have a non-blank subject before OK is allowed.
        self._ok_button.setEnabled(not (self._param.required and not self._raw_value()))

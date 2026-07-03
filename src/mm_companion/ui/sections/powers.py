"""Section 4: powers.

The most complex part of a character. An "Add Power" button opens the standalone
:class:`~mm_companion.ui.power_constructor.PowerConstructorWindow` brick-builder in
its own window; saving there hands the finished
:class:`~mm_companion.core.powers.Power` back through
:attr:`~mm_companion.ui.power_constructor.PowerConstructorWindow.powerSaved`, which
this section appends to the shared :class:`~mm_companion.core.character.Character`
and shows as a removable row (name + assembled point cost, plus a ⚠ marker when the
power breaks a Power Level cap for the character's PL). It follows the standard
section contract (``data`` + ``character`` constructor, ``changed`` signal,
``set_locked``) so it slots into the sheet like the others, and — because saved
powers live on the model — a loaded character repopulates its list at construction.
"""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from mm_companion.core.character import Character
from mm_companion.core.data_loader import GameData
from mm_companion.core.powers import Power
from mm_companion.core.rules import power_pl_violations, power_total_cost
from mm_companion.ui.power_constructor import PowerConstructorWindow


class PowersSection(QGroupBox):
    """Powers section: launches the Power Constructor and lists saved powers."""

    changed = Signal()

    def __init__(
        self,
        data: GameData,
        character: Character,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__("Powers", parent)
        self._data = data
        self._character = character
        self._locked = False
        # Keep constructor windows referenced so Qt doesn't garbage-collect them
        # the moment the click handler returns.
        self._windows: list[PowerConstructorWindow] = []

        layout = QVBoxLayout(self)
        self._empty = QLabel("No powers yet")
        self._empty.setEnabled(False)
        layout.addWidget(self._empty)

        # The saved powers stack above the Add button, one removable row each.
        self._list_host = QWidget()
        self._list_layout = QVBoxLayout(self._list_host)
        self._list_layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._list_host)

        self._add_button = QPushButton("Add Power")
        self._add_button.clicked.connect(self._open_constructor)
        layout.addWidget(self._add_button)

        # Seed from the (possibly loaded) model.
        self._rebuild_list()

    # -- constructor lifecycle --------------------------------------------
    def _open_constructor(self) -> None:
        window = PowerConstructorWindow(self._data, power_level=self._character.power_level)
        window.powerSaved.connect(self._on_power_saved)
        window.closed.connect(lambda w=window: self._on_window_closed(w))
        self._windows.append(window)
        window.show()

    def _on_power_saved(self, power: Power) -> None:
        self._character.powers.append(power)
        self._rebuild_list()
        self.changed.emit()

    def _on_window_closed(self, window: PowerConstructorWindow) -> None:
        if window in self._windows:
            self._windows.remove(window)

    # -- power list -------------------------------------------------------
    def _rebuild_list(self) -> None:
        """Rebuild the row per power from the model, toggling the empty label."""
        while self._list_layout.count():
            widget = self._list_layout.takeAt(0).widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
        for power in self._character.powers:
            self._list_layout.addWidget(self._make_row(power))
        self._empty.setVisible(not self._character.powers)

    def _make_row(self, power: Power) -> QFrame:
        row = QFrame()
        row.setFrameShape(QFrame.Shape.StyledPanel)
        layout = QHBoxLayout(row)
        layout.setContentsMargins(6, 2, 6, 2)

        name = QLabel(power.name or "Unnamed Power")
        name.setStyleSheet("font-weight: bold;")
        layout.addWidget(name)

        # A power that breaks a PL cap carries a warning marker naming the breach;
        # enforcement is a warning for now (see storage.pl_enforcement).
        violations = power_pl_violations(power, self._character.power_level, self._data)
        if violations:
            warning = QLabel("⚠")
            warning.setStyleSheet("color: #d1a01e; font-weight: bold;")
            warning.setToolTip("\n".join(violations))
            layout.addWidget(warning)
        layout.addStretch()

        cost = QLabel(f"{power_total_cost(power, self._data)} PP")
        cost.setEnabled(False)
        layout.addWidget(cost)

        remove = QPushButton("✕")
        remove.setFixedWidth(24)
        remove.setToolTip("Remove this power")
        remove.clicked.connect(lambda _checked=False, p=power: self._remove_power(p))
        remove.setVisible(not self._locked)  # editing chrome hidden in view mode
        layout.addWidget(remove)
        return row

    def _remove_power(self, power: Power) -> None:
        if power in self._character.powers:
            self._character.powers.remove(power)
            self._rebuild_list()
            self.changed.emit()

    def set_locked(self, locked: bool) -> None:
        """In read-only view mode, hide the editing entry points (Add / Remove)."""
        self._locked = locked
        self._add_button.setVisible(not locked)
        self._rebuild_list()

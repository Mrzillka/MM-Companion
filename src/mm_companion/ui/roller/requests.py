"""The roller's Ask to Roll form: pick a trait and a difficulty and ask the table to roll
it. Opened from the Actions popup's **Ask** tile.
"""

from __future__ import annotations

from dataclasses import replace

from PySide6.QtCore import QPoint, Qt
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QWidget,
)

from mm_companion.core.rules import RollSpec
from mm_companion.ui import theme
from mm_companion.ui.action_menu import NO_REQUEST_TRAITS
from mm_companion.ui.wheel_guard import guard_wheel
from mm_companion.ui.widgets import (
    BOLD_STYLE,
    make_spin_box,
)

#: The form's heading.
REQUEST_TITLE = "Ask the table to roll"


class RollerRequestsMixin:
    """Part of :class:`~mm_companion.ui.dice_roller.DiceRollerPanel`; see the module docstring."""

    def _build_request_form(self) -> QFrame:
        """The Ask to Roll form: a trait, a difficulty, and a button that asks the table.

        It used to be a row of the roller itself, always on screen — three greyed
        controls on the busiest part of the panel, for a verb used a few times a
        session and only ever at a table. So it lives in a small popup the Actions
        popup's **Ask** tile opens (:meth:`open_request`), and the roller keeps its
        height for the controls that roll something *here*.

        Built once and kept: it remembers the last trait and difficulty between
        openings, and it is a child of the panel, so it is never a window without a
        parent. ``Qt.Popup`` makes it close on a click outside, as a menu does.

        The DC box is a plain spin box where **0 means no DC**, the same as the
        roller's own DC box.
        """

        form = QFrame(self, Qt.WindowType.Popup)
        form.setObjectName("requestForm")
        form.setFrameShape(QFrame.Shape.StyledPanel)
        form.hide()
        grid = QGridLayout(form)
        pad = int(theme.metric("space.md"))
        grid.setContentsMargins(pad, pad, pad, pad)
        grid.setColumnStretch(1, 1)

        title = QLabel(REQUEST_TITLE)
        title.setStyleSheet(BOLD_STYLE)
        grid.addWidget(title, 0, 0, 1, 2)

        self._request_combo = QComboBox()
        self._request_combo.setToolTip("What to ask the table to roll")
        self._request_combo.setPlaceholderText("Trait…")
        # A combo box asks to be as wide as its longest entry, a skill name the
        # ruleset chose; the popup still shows names in full.
        self._request_combo.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        self._request_combo.setMinimumContentsLength(12)
        self._request_combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._request_combo.setMinimumWidth(int(theme.metric("column.request-trait")))
        guard_wheel(self._request_combo)
        self._request_combo.currentIndexChanged.connect(self._on_request_trait_changed)
        self._request_label = QLabel("Trait")
        grid.addWidget(self._request_label, 1, 0)
        grid.addWidget(self._request_combo, 1, 1)

        self._request_part = QWidget()
        line = QHBoxLayout(self._request_part)
        line.setContentsMargins(0, 0, 0, 0)
        line.setSpacing(int(theme.metric("space.sm")))
        # Arrowless: the theme reserves a right-hand column for the arrows the
        # platform style draws, which is more than the two digits it frames.
        self._request_dc = make_spin_box(0, 60, value=0, buttons=False, snug=True)
        self._request_dc.setToolTip("The difficulty to ask for — none, or a number")
        # And it says so: a bare "0" read as a DC of nothing, which nobody means.
        self._request_dc.setSpecialValueText("—")
        line.addWidget(self._request_dc)
        line.addStretch(1)

        self._request_button = QPushButton("Ask")
        self._request_button.setDefault(True)
        self._request_button.setToolTip(
            "Put this roll in everyone's history, for them to roll on their own sheet"
        )
        self._request_button.clicked.connect(self._emit_request)
        line.addWidget(self._request_button)
        grid.addWidget(QLabel("DC"), 2, 0)
        grid.addWidget(self._request_part, 2, 1)
        return form

    def open_request(self, at: QPoint | None = None) -> QFrame:
        """Show the Ask to Roll form at *at* (global), or under the Actions button."""

        form = self._request_form
        if at is None:
            button = self._actions_button
            at = button.mapToGlobal(button.rect().bottomLeft())
        form.adjustSize()
        screen = self.screen().availableGeometry() if self.screen() is not None else None
        if screen is not None:
            at = QPoint(
                max(screen.left(), min(at.x(), screen.right() - form.width())),
                max(screen.top(), min(at.y(), screen.bottom() - form.height())),
            )
        form.move(at)
        form.show()
        self._request_combo.setFocus()
        return form

    def request_reason(self) -> str | None:
        """Why Ask to Roll cannot run (``None`` when it can): there must be traits."""

        return None if any(spec is not None for spec in self._request_specs) else NO_REQUEST_TRAITS

    # -- asking the table to roll --------------------------------------------

    def set_roll_choices(self, groups: object) -> None:
        """Fill the Ask to Roll form's combobox from *groups*.

        *groups* is what
        :func:`~mm_companion.core.rules.pins.requested_roll_choices` returns — a
        list of titled groups of values, each carrying a template
        :class:`~mm_companion.core.rules.RollSpec`. The panel is handed the answer
        rather than the game data: it reads no ruleset. The group titles go in as
        disabled items, as in the Power Constructor's trait combo. With none, the Ask
        tile is greyed (:meth:`request_reason`).
        """

        self._request_combo.blockSignals(True)
        self._request_combo.clear()
        self._request_specs = []

        for group in groups or ():
            values = [v for v in getattr(group, "values", ()) if v.spec is not None]
            if not values:
                continue
            self._request_combo.addItem(getattr(group, "title", ""))
            self._request_specs.append(None)
            for value in values:
                self._request_combo.addItem(f"  {value.label}")
                self._request_specs.append(value.spec)

        self._request_combo.blockSignals(False)
        self._disable_request_headings()
        # No entry is selected to begin with: a combo that opens on the first trait
        # would make "Ask" send whatever happened to be first in the ruleset to a
        # table nobody had chosen anything for.
        self._request_combo.setCurrentIndex(-1)
        self._sync_request_button()

    def _disable_request_headings(self) -> None:
        """Grey the group titles so only the traits under them can be picked."""

        model = self._request_combo.model()
        for row, spec in enumerate(self._request_specs):
            if spec is None:
                item = model.item(row)
                if item is not None:
                    item.setEnabled(False)

    def _requested_spec(self) -> RollSpec | None:
        """The trait the form names, with its DC folded in, or ``None``.

        ``dc`` of 0 is *no* DC and not a difficulty of zero — see
        :meth:`_build_request_form`.
        """

        index = self._request_combo.currentIndex()
        if not 0 <= index < len(self._request_specs):
            return None
        spec = self._request_specs[index]
        if spec is None:
            return None
        dc = self._request_dc.value()
        return replace(spec, dc=dc or None)

    def _on_request_trait_changed(self) -> None:
        """Re-check the button, and say in the tooltip what the combo may be clipping.

        The combo may be narrower than a skill name — the same elide-plus-tooltip
        bargain ``ElidingLabel`` strikes, and the only one available: a combo box does
        not tooltip its own clipped text.
        """

        self._sync_request_button()
        chosen = self._requested_spec()
        self._request_combo.setToolTip(
            f"Ask the table to roll {chosen.label}" if chosen else "What to ask the table to roll"
        )

    def _sync_request_button(self) -> None:
        """The Ask button is live only once a real trait is picked."""

        if self._rolling:
            return
        self._request_button.setEnabled(self._requested_spec() is not None)

    def _emit_request(self) -> None:
        """Hand the chosen trait up to whoever knows where a request goes."""

        spec = self._requested_spec()
        if spec is not None:
            self._request_form.hide()
            self.rollRequested.emit(spec)

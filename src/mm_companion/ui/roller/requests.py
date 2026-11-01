"""The roller's Request row: the GM picks a trait and a difficulty and asks the table to
roll it.
"""

from __future__ import annotations

from dataclasses import replace

from PySide6.QtWidgets import (
    QComboBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QWidget,
)

from mm_companion.core.rules import RollSpec
from mm_companion.ui import theme
from mm_companion.ui.wheel_guard import guard_wheel
from mm_companion.ui.widgets import (
    make_spin_box,
)


class RollerRequestsMixin:
    """Part of :class:`~mm_companion.ui.dice_roller.DiceRollerPanel`; see the module docstring."""

    def _build_request_row(self, grid: QGridLayout, row: int) -> None:
        """The Request row: a trait, a difficulty, and a button that asks the table.

        Below the roll's own controls because it is a different verb — everything
        above rolls something *here*, and this asks somebody else to. It sits **on
        the same grid** as the rows above it, cell for cell: the caption in the
        caption column, the trait under the sliders, and the difficulty with its
        button under the spin boxes. It used to span the slider and spin columns
        as one free-running line, which lined up with nothing above it — the combo
        overran the sliders, the DC box floated mid-cell — and read as a second,
        broken form bolted under the first.

        The DC box is a plain spin box where **0 means no DC**, deliberately unlike
        the Difficulty Class row above it. That row's checkbox exists because the
        panel has to tell "no DC" from "DC 0" for a roll it grades; a request is
        graded on the answerer's screen against whatever they end up rolling, and
        "ask for a roll against DC 0" is not a thing anyone means. One control
        beats two in a row that is already three wide.

        The whole row is hidden until a host supplies traits
        (:meth:`set_roll_choices`), so a roller with no ruleset behind it is the
        panel it always was.
        """

        self._request_combo = QComboBox()
        self._request_combo.setToolTip("What to ask the table to roll")
        # Short on purpose: a placeholder is measured into the combo's size hint even
        # when the minimum-contents policy below has capped everything else, so a
        # roomier "— choose a trait —" would cost the block a hundred pixels of width
        # to say what the "Request" caption beside it already says.
        self._request_combo.setPlaceholderText("Trait…")
        # A combo box asks to be as wide as its longest entry, and the longest here
        # is a skill name the *ruleset* chose. Left alone that made the whole Dice
        # block — and so the pinned strip holding it — demand half again the width
        # it needs, for a row that is not even the point of the panel. So it shrinks
        # and stretches instead, exactly as the Power Constructor's term combos do:
        # the popup still shows every name in full, which is where they are read.
        self._request_combo.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        self._request_combo.setMinimumContentsLength(8)
        self._request_combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        # And an explicit floor, which *replaces* the hint-derived one rather than
        # raising it (``qSmartMinSize``) — so this, and not the longest skill name,
        # is how narrow the slider column can be squeezed. A trait name is worth
        # about this much either way.
        self._request_combo.setMinimumWidth(int(theme.metric("column.request-trait")))
        guard_wheel(self._request_combo)
        self._request_combo.currentIndexChanged.connect(self._on_request_trait_changed)

        # The difficulty and the button share the spin-box column, so the row ends
        # exactly where the Bonus and Penalty boxes above it end.
        self._request_part = QWidget()
        line = QHBoxLayout(self._request_part)
        line.setContentsMargins(0, 0, 0, 0)
        line.setSpacing(int(theme.metric("space.xs")))

        # Arrowless: the theme reserves a right-hand column for the arrows the
        # platform style draws — 50px under ``windows11`` — which is more than the
        # two digits it frames, and would leave "Ask" no room in this cell. It takes
        # the cell's slack, so the button beside it stays exactly as wide as its word.
        self._request_dc = make_spin_box(0, 60, value=0, buttons=False)
        self._request_dc.setToolTip("The difficulty to ask for — 0 for no DC")
        # And it says so: a bare "0" read as a DC of nothing, which nobody means.
        self._request_dc.setSpecialValueText("—")
        self._request_dc.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        line.addWidget(self._request_dc, stretch=1)

        self._request_button = QPushButton("Ask")
        self._request_button.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self._request_button.setToolTip(
            "Put this roll in everyone's history, for them to roll on their own sheet"
        )
        self._request_button.clicked.connect(self._emit_request)
        line.addWidget(self._request_button)

        self._request_label = QLabel("Request")
        grid.addWidget(self._request_label, row, 0)
        grid.addWidget(self._request_combo, row, 1)
        grid.addWidget(self._request_part, row, 2)
        self._show_request_row(False)

    def _show_request_row(self, visible: bool) -> None:
        """Show or hide the Request row as one thing, and say the panel changed size.

        The ``contentChanged`` is not optional: a splitter child with no stretch
        keeps the pixels it was given, so without it the view never re-divides and
        the block keeps the room a row it no longer shows was using (or grows past
        the window for one it now does).
        """

        for widget in (self._request_label, self._request_combo, self._request_part):
            widget.setVisible(visible)
        self.updateGeometry()
        self.contentChanged.emit()

    # -- asking the table to roll --------------------------------------------

    def set_roll_choices(self, groups: object) -> None:
        """Fill the Request row's combobox from *groups*, or hide the row.

        *groups* is what
        :func:`~mm_companion.core.rules.pins.requested_roll_choices` returns — a
        list of titled groups of values, each carrying a template
        :class:`~mm_companion.core.rules.RollSpec`. The panel is handed the answer
        rather than the game data, exactly as it is handed a localizer rather than
        a character: it reads no ruleset, so a host that has none (or a mod with
        nothing to offer) simply gets the panel as it was before this row existed.

        The group titles go in as disabled items, the way the Power Constructor's
        trait combo does it — a section heading in a combo box has no other home.
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
        self._show_request_row(any(spec is not None for spec in self._request_specs))

    def _disable_request_headings(self) -> None:
        """Grey the group titles so only the traits under them can be picked."""

        model = self._request_combo.model()
        for row, spec in enumerate(self._request_specs):
            if spec is None:
                item = model.item(row)
                if item is not None:
                    item.setEnabled(False)

    def _requested_spec(self) -> RollSpec | None:
        """The trait the Request row names, with its DC folded in, or ``None``.

        ``dc`` of 0 is *no* DC and not a difficulty of zero — see
        :meth:`_build_request_row`.
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

        In the pinned strip this combo is the width the block can spare, which is
        rarely the width of a skill name — the same elide-plus-tooltip bargain
        ``ElidingLabel`` strikes, and the only one available: a combo box does not
        tooltip its own clipped text.
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
            self.rollRequested.emit(spec)

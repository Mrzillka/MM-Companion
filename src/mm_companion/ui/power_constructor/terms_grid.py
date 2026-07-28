"""The label/value grid an effect's game terms are laid out in.

Two places render the same table of :class:`~mm_companion.core.rules.EffectStat`
rows: the Power Constructor's summary panel
(:class:`~mm_companion.ui.power_constructor.terms_view.PowerTermsView`) and the
finished power's card in the sheet
(:class:`~mm_companion.ui.sections.powers.PowersSection`). They differ only in
typography — the card's copy recedes into small, unbolded type so the numbers read
as reference rather than as the point of the card — so the layout itself lives here
and each caller passes its own :class:`TermsGridStyle`.

Keeping it in one place matters beyond tidiness: when the two copies drifted, the
card and the constructor disagreed about what an effect's Check line said.
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QGridLayout, QLabel

from mm_companion.core.rules import HOMERULE_TINT
from mm_companion.ui.theme import ACCENT, TINT_BETTER, TINT_WORSE

#: How a changed value is tinted: an extra improved it (green), a flaw limited it
#: (red), a Dev-mode override replaced it (a distinct blue).
TINTS = {"better": TINT_BETTER, "worse": TINT_WORSE, HOMERULE_TINT: ACCENT}

#: How many label/value pairs sit side by side per grid row, so the short stats pack
#: across the width instead of stacking into a tall column.
PAIRS_PER_ROW = 2


@dataclass(frozen=True)
class TermsGridStyle:
    """Typography for one rendering of the terms grid.

    ``point_size`` overrides the font size — set on the ``QFont``, never in a
    stylesheet, because a stylesheet ``font-size`` outranks the widget font and would
    sit out the power card's switched-off transition. ``None`` keeps the inherited
    size. ``bold_changed`` bolds a tinted (modifier-changed) value.
    """

    point_size: float | None = None
    bold_changed: bool = True


def build_terms_grid(rows: list, style: TermsGridStyle | None = None) -> QGridLayout:
    """Lay *rows* out as ``label: value`` pairs, :data:`PAIRS_PER_ROW` to a grid row.

    A value some modifier changed is tinted and carries its pre-modifier value on the
    tooltip — that is the part worth noticing at a glance. The value columns share the
    slack evenly so the pairs spread across the width rather than bunching at the left.
    """

    style = style or TermsGridStyle()
    grid = QGridLayout()
    grid.setHorizontalSpacing(8)
    for index, stat in enumerate(rows):
        grid_row, pair = divmod(index, PAIRS_PER_ROW)
        column = pair * 2
        label = QLabel(f"{stat.label}:")
        label.setStyleSheet("color: palette(placeholder-text);")
        value = QLabel(stat.value)
        value.setWordWrap(True)
        tint = TINTS.get(stat.change)
        if tint:
            weight = " font-weight: bold;" if style.bold_changed else ""
            value.setStyleSheet(f"color: {tint};{weight}")
            value.setToolTip(f"Base: {stat.base}")
        if style.point_size is not None:
            for widget in (label, value):
                font = widget.font()
                font.setPointSizeF(style.point_size)
                widget.setFont(font)
        grid.addWidget(label, grid_row, column, Qt.AlignmentFlag.AlignTop)
        grid.addWidget(value, grid_row, column + 1, Qt.AlignmentFlag.AlignTop)
    for pair in range(PAIRS_PER_ROW):
        grid.setColumnStretch(pair * 2 + 1, 1)
    return grid

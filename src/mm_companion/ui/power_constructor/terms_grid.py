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
from PySide6.QtWidgets import QGridLayout, QLabel, QWidget

from mm_companion.core.rules import HOMERULE_TINT
from mm_companion.ui import theme

#: How a changed value is tinted, as colour-token names: an extra improved it
#: (green), a flaw limited it (red), a Dev-mode override replaced it (a distinct
#: blue). Names rather than values, so the tokens are resolved at render time and
#: a theme switch reaches them.
TINT_TOKENS = {
    "better": "tint.better",
    "worse": "tint.worse",
    HOMERULE_TINT: "tint.homerule",
}

#: How many label/value pairs sit side by side per grid row, so the short stats pack
#: across the width instead of stacking into a tall column.
PAIRS_PER_ROW = 2

#: Below this, a card's terms drop to one pair per row. Two pairs in a card
#: narrower than this is four columns in a couple of hundred pixels — every value
#: wrapping over three lines, which is taller *and* harder to read than stacking
#: them honestly. Paired with :data:`PAIRS_HYSTERESIS`, because the flip changes
#: the card's height and a card tree's height moves the page's scrollbar.
PAIRS_MIN_WIDTH = 300
PAIRS_HYSTERESIS = 24


def pairs_per_row(available: int, current: int = 0) -> int:
    """How many label/value pairs fit across *available* px.

    Zero or less — a card that has not been laid out yet — answers with the full
    :data:`PAIRS_PER_ROW`, so the first paint is the ordinary card and the real
    answer arrives on the first resize.
    """
    if available <= 0:
        return PAIRS_PER_ROW
    if current == PAIRS_PER_ROW:
        return PAIRS_PER_ROW if available >= PAIRS_MIN_WIDTH - PAIRS_HYSTERESIS else 1
    if current == 1:
        return PAIRS_PER_ROW if available >= PAIRS_MIN_WIDTH + PAIRS_HYSTERESIS else 1
    return PAIRS_PER_ROW if available >= PAIRS_MIN_WIDTH else 1


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


def reflow_terms_grid(grid: QGridLayout, pairs: int) -> bool:
    """Re-deal an existing grid into *pairs* label/value pairs per row.

    The items are **moved, not remade**. A card's terms are a dozen `QLabel`s and
    rebuilding them on every resize frame would be the one thing this layer cannot
    afford: `PowersSection` and `EquipmentSection` already destroy and remake their
    whole card tree on a model change (see `docs/notes/debts.md`), and paying that
    on a divider drag would make the gesture stutter. Repositioning is a layout
    pass and nothing more.

    Returns whether anything moved.
    """
    pairs = max(1, int(pairs))
    items = []
    while grid.count():
        items.append(grid.takeAt(0))
    if not items:
        return False
    for column in range(grid.columnCount()):
        grid.setColumnStretch(column, 0)
    for index in range(0, len(items), 2):
        label, value = items[index], items[index + 1] if index + 1 < len(items) else None
        row, pair = divmod(index // 2, pairs)
        column = pair * 2
        grid.addItem(label, row, column, 1, 1, Qt.AlignmentFlag.AlignTop)
        if value is not None:
            grid.addItem(value, row, column + 1, 1, 1, Qt.AlignmentFlag.AlignTop)
    for pair in range(pairs):
        grid.setColumnStretch(pair * 2 + 1, 1)
    return True


class TermsGridBox(QWidget):
    """A card's terms, re-dealt into fewer columns as the card narrows.

    Two label/value pairs abreast read well in a card of ordinary width and badly
    in a narrow one, where four columns in two hundred pixels means every value
    wrapping over three lines — taller *and* harder to read than stacking them
    honestly. This watches its own width and re-deals.
    """

    def __init__(self, grid: QGridLayout, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._grid = grid
        self._pairs = PAIRS_PER_ROW
        self.setLayout(grid)

    @property
    def grid(self) -> QGridLayout:
        return self._grid

    @property
    def pairs(self) -> int:
        return self._pairs

    def sync_pairs(self, available: int | None = None) -> bool:
        """Re-deal if this width now favours a different number of pairs."""
        margins = self._grid.contentsMargins()
        width = self.width() if available is None else available
        wanted = pairs_per_row(width - margins.left() - margins.right(), self._pairs)
        if wanted == self._pairs:
            return False
        self._pairs = wanted
        reflow_terms_grid(self._grid, wanted)
        self.updateGeometry()
        return True

    def resizeEvent(self, event) -> None:  # noqa: ANN001, N802 - Qt override
        super().resizeEvent(event)
        if event.oldSize().width() != event.size().width():
            self.sync_pairs()


def build_terms_grid(
    rows: list, style: TermsGridStyle | None = None, *, pairs: int = PAIRS_PER_ROW
) -> QGridLayout:
    """Lay *rows* out as ``label: value`` pairs, *pairs* to a grid row.

    A value some modifier changed is tinted and carries its pre-modifier value on the
    tooltip — that is the part worth noticing at a glance. The value columns share the
    slack evenly so the pairs spread across the width rather than bunching at the left.
    """

    style = style or TermsGridStyle()
    pairs = max(1, int(pairs))
    grid = QGridLayout()
    grid.setHorizontalSpacing(int(theme.metric("space.lg")))
    for index, stat in enumerate(rows):
        grid_row, pair = divmod(index, pairs)
        column = pair * 2
        label = QLabel(f"{stat.label}:")
        label.setStyleSheet(f"color: {theme.color('text.muted')};")
        value = QLabel(stat.value)
        value.setWordWrap(True)
        token = TINT_TOKENS.get(stat.change)
        if token:
            weight = " font-weight: bold;" if style.bold_changed else ""
            value.setStyleSheet(f"color: {theme.color(token)};{weight}")
            value.setToolTip(f"Base: {stat.base}")
        if style.point_size is not None:
            for widget in (label, value):
                font = widget.font()
                font.setPointSizeF(style.point_size)
                widget.setFont(font)
        grid.addWidget(label, grid_row, column, Qt.AlignmentFlag.AlignTop)
        grid.addWidget(value, grid_row, column + 1, Qt.AlignmentFlag.AlignTop)
    for pair in range(pairs):
        grid.setColumnStretch(pair * 2 + 1, 1)
    return grid

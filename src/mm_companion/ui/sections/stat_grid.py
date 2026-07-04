"""Shared helpers for the ability and resistance stat grids.

Abilities and resistances are laid out identically: a titled grid of spin boxes,
one row per trait, with a green "→ total" label that appears only when a power
enhances that trait. Both :class:`~mm_companion.ui.sections.abilities.AbilitiesSection`
and :class:`~mm_companion.ui.sections.resistances.ResistancesSection` build their
grids through :func:`build_stat_group` and refresh their enhancement labels through
:func:`apply_enhancements`.
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QGridLayout, QLabel, QSpinBox, QWidget

from mm_companion.ui.widgets import hline_separator, make_spin_box

STAT_MIN, STAT_MAX = -5, 30
STAT_SPIN_WIDTH = 80

# The green a power-boosted trait's "→ total" reads in, matching the summary tints.
ENHANCED_TINT = "#2e9e4f"


def add_stat_row(
    grid: QGridLayout, row: int, name: str, abbr: str, spin: QSpinBox, enh: QLabel
) -> None:
    """Lay out one stat as four aligned columns: name, short code, spin box, and
    the (usually hidden) power-enhanced total."""

    grid.addWidget(QLabel(f"{name}:"), row, 0)
    code = QLabel(abbr)
    code.setAlignment(Qt.AlignmentFlag.AlignCenter)
    grid.addWidget(code, row, 1)
    grid.addWidget(spin, row, 2)
    grid.addWidget(enh, row, 3)


def build_stat_group(
    entries: list,
    store: dict[str, QSpinBox],
    enh_store: dict[str, QLabel],
    values: dict[str, int],
    on_change: Callable[[str, int], None],
) -> QWidget:
    """Build a frameless grid of stat spin boxes (abilities or resistances).

    Spin boxes are seeded from *values* (the model dict) and write back through
    *on_change*. Each row also gets a green "→ total" label that stays hidden
    until a power enhances that trait. A separator is inserted before the first
    derived entry. *store* and *enh_store* are filled in place, keyed by each
    entry's ``key``. The container is frameless — the hosting section's group box
    carries the title and running cost.
    """

    container = QWidget()
    grid = QGridLayout(container)
    grid.setContentsMargins(0, 0, 0, 0)
    row = 0
    separated = False
    for entry in entries:
        if entry.derived and not separated:
            grid.addWidget(hline_separator(), row, 0, 1, 4)
            row += 1
            separated = True
        spin = make_spin_box(
            STAT_MIN,
            STAT_MAX,
            value=values.get(entry.key, 0),
            buttons=False,
            max_width=STAT_SPIN_WIDTH,
        )
        spin.valueChanged.connect(lambda value, key=entry.key: on_change(key, value))
        store[entry.key] = spin
        enh = QLabel()
        enh.setStyleSheet(f"color: {ENHANCED_TINT}; font-weight: bold;")
        enh.setVisible(False)
        enh_store[entry.key] = enh
        add_stat_row(grid, row, entry.name, entry.abbr, spin, enh)
        row += 1
    return container


def apply_enhancements(spins: dict, labels: dict, bonuses: dict) -> None:
    """Show or hide each trait's "→ total" label from the power bonuses.

    A trait with no power bonus keeps its label hidden, so the column only
    appears for traits a power actually raises. The base a bonus adds to is the
    spin box's own value (for a resistance, already its full derived total).
    """

    for key, label in labels.items():
        bonus = bonuses.get(key)
        if bonus:
            total = spins[key].value() + bonus.amount
            label.setText(f"→ {total}")
            label.setToolTip(f"+{bonus.amount} from {', '.join(bonus.sources)}")
            label.setVisible(True)
        else:
            label.clear()
            label.setToolTip("")
            label.setVisible(False)

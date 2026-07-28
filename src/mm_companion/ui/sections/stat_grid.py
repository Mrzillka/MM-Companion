"""Shared helpers for the ability and resistance stat grids.

Abilities and resistances are laid out identically: a titled grid of spin boxes,
one row per trait, with a green "→ total" label that appears only when a power
enhances that trait. Both :class:`~mm_companion.ui.sections.abilities.AbilitiesSection`
and :class:`~mm_companion.ui.sections.resistances.ResistancesSection` build their
grids through :func:`build_stat_group` and refresh their enhancement labels through
:func:`apply_stat_effects`.
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QGridLayout, QLabel, QSpinBox, QWidget

from mm_companion.core.data_loader import TraitRange
from mm_companion.core.rules import ConditionEffect
from mm_companion.ui.theme import TINT_BETTER, TINT_WORSE
from mm_companion.ui.widgets import hline_separator, make_spin_box

STAT_SPIN_WIDTH = 80

# The green a power-boosted trait's "→ total" reads in, matching the summary tints.
ENHANCED_TINT = TINT_BETTER
# The red a condition penalty's "→ total" reads in, matching the constructor's flaw tint.
CONDITION_TINT = TINT_WORSE


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
    trait_range: TraitRange,
) -> QWidget:
    """Build a frameless grid of stat spin boxes (abilities or resistances).

    Spin boxes are seeded from *values* (the model dict) and write back through
    *on_change*, and accept the data-driven *trait_range* for their trait family
    (``costs.json``'s ``trait_ranges``). Each row also gets a green "→ total" label
    that stays hidden until a power enhances that trait. A separator is inserted
    before the first derived entry. *store* and *enh_store* are filled in place, keyed
    by each entry's ``key``. The container is frameless — the hosting section's group
    box carries the title and running cost.
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
            trait_range.min,
            trait_range.max,
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


def set_stat_value(spin: QSpinBox, value: int) -> None:
    """Seed a stat spin box, widening its range first if *value* falls outside it.

    A spin box silently clamps a value it can't represent, which is destructive here:
    the resistance spins hold a *total* and the model stores only the bought delta, so a
    clamped display would make the very next edit rewrite that delta from the wrong
    number. Rather than lose ranks to a range that is too tight (a stingy mod's, or a
    character built before a range was narrowed), stretch the range to fit.
    """

    if value < spin.minimum():
        spin.setMinimum(value)
    elif value > spin.maximum():
        spin.setMaximum(value)
    spin.setValue(value)


def _set_label_strike(label: QLabel, struck: bool) -> None:
    font = label.font()
    font.setStrikeOut(struck)
    label.setFont(font)


def apply_stat_effects(
    spins: dict,
    labels: dict,
    bonuses: dict,
    cond_effects: dict[str, ConditionEffect] | None = None,
) -> None:
    """Show or hide each trait's "→ total" label from power bonuses and conditions.

    The label reads ``→ N``, where ``N`` is the spin box's value plus any power boost,
    then a condition overlay (a Hit penalty on Toughness, a halved/zeroed active defense,
    a scoped check penalty). A pure power boost tints green; any condition tints it red,
    struck through when the overlay reports the trait lost (``ConditionEffect.trait_lost``
    — Disabled/Debilitated in the base data). A trait with neither keeps its label hidden.
    """

    cond_effects = cond_effects or {}
    for key, label in labels.items():
        bonus = bonuses.get(key)
        effect = cond_effects.get(key)
        has_cond = effect is not None and effect.active
        if not bonus and not has_cond:
            label.clear()
            label.setToolTip("")
            _set_label_strike(label, False)
            label.setVisible(False)
            continue

        total = spins[key].value() + (bonus.amount if bonus else 0)
        if has_cond:
            total = effect.apply(total)
        label.setText(f"→ {total}")

        tips = []
        if bonus:
            tips.append(f"+{bonus.amount} from {', '.join(bonus.sources)}")
        if has_cond and effect.tooltip:
            tips.append(effect.tooltip)
        label.setToolTip("\n".join(tips))

        tint = CONDITION_TINT if has_cond else ENHANCED_TINT
        label.setStyleSheet(f"color: {tint}; font-weight: bold;")
        _set_label_strike(label, has_cond and effect.trait_lost)
        label.setVisible(True)

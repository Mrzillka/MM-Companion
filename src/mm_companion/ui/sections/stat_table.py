"""Shared helpers for the ability and resistance stat tables.

Abilities and resistances are laid out identically — one row per trait, with its
short code, its rank, and a "→ total" that appears only when a power or a
condition moves it — so both
:class:`~mm_companion.ui.sections.abilities.AbilitiesSection` and
:class:`~mm_companion.ui.sections.resistances.ResistancesSection` build their table
through :func:`build_stat_table` and refresh their totals through
:func:`apply_stat_effects`.

It is a real :class:`QTableWidget` rather than a hand-laid grid, which is what the
Skills block has always been. Three things come with that and none of them are
cosmetic: the columns line up under headers that say what they are, a row is a row
(so rolling hangs off ``cellDoubleClicked`` rather than an event filter on each of
four widgets), and the whole block reads as one family with Skills instead of two
blocks that merely happen to be near each other.

The pieces both this and Skills need — the roll payload's item role, fitting a
table to its rows, tinting an item — live here, since this is the lower module.
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QHeaderView,
    QSizePolicy,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
)

from mm_companion.core.data_loader import TraitRange
from mm_companion.core.rules import ConditionEffect, RollSpec
from mm_companion.ui import theme
from mm_companion.ui.wheel_guard import guard_wheel
from mm_companion.ui.widgets import hline_separator, make_spin_box, readonly_item

#: Where a rollable row stashes what to roll, on its Total cell. Shared with the
#: Skills table so both stat blocks answer a double-click the same way.
ROLL_ROLE = Qt.ItemDataRole.UserRole

# Colour-token names, not values: resolved where they are used so a theme switch
# reaches them (see :mod:`mm_companion.ui.theme`).
#: The green a power-boosted trait's "→ total" reads in, matching the summary tints.
ENHANCED_TINT = "tint.better"
#: The red a condition penalty's "→ total" reads in, matching the constructor's flaw tint.
CONDITION_TINT = "tint.worse"

COL_NAME, COL_ABBR, COL_RANK, COL_TOTAL = range(4)
HEADERS = ["Trait", "ABL", "Rank", "Total"]

#: What a table needs to make its rows rollable: a factory turning a trait key into
#: that trait's :class:`RollSpec`, and the sink the spec goes to (the section's
#: ``rollRequested.emit``).
RollHookFactory = Callable[[str], "RollSpec | None"]


def build_stat_table(
    entries: list,
    store: dict[str, QSpinBox],
    total_store: dict[str, QTableWidgetItem],
    values: dict[str, int],
    on_change: Callable[[str, int], None],
    trait_range: TraitRange,
    *,
    roll_spec: RollHookFactory | None = None,
    roll_sink: Callable[[RollSpec], None] | None = None,
    load_sink: Callable[[RollSpec], None] | None = None,
) -> QTableWidget:
    """Build the stat table for one trait family (abilities or resistances).

    Rank cells are spin boxes seeded from *values* (the model dict) writing back
    through *on_change*, bounded by the data-driven *trait_range* for their family
    (``costs.json``'s ``trait_ranges``). Each row also carries a Total cell that
    stays blank until a power or a condition moves that trait. A separator row is
    spanned across before the first derived entry. *store* and *total_store* are
    filled in place, keyed by each entry's ``key``.

    Given *roll_spec* and *roll_sink*, double-clicking a row rolls that trait, and
    with *load_sink* a single click loads it into the roller's chip without throwing
    the die. The spec is built at click time from the trait key, so it is never
    stale. The Rank column is the one that never arrives — its spin box eats both
    clicks, which unlocked is what selects the number for retyping.
    """
    table = QTableWidget(0, len(HEADERS))
    table.setHorizontalHeaderLabels(HEADERS)
    table.verticalHeader().setVisible(False)
    # The table never scrolls itself; it is sized to show every row (see
    # :func:`fit_table_height`), and the page scrolls when the blocks don't all fit.
    table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    table.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
    header = table.horizontalHeader()
    header.setSectionResizeMode(COL_NAME, QHeaderView.ResizeMode.Stretch)
    for col in (COL_ABBR, COL_RANK, COL_TOTAL):
        header.setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
    # Fits its content and never scrolls, so keep it out of the focus chain; the
    # wheel then always falls through to the page.
    table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    guard_wheel(table)

    rows = len(entries) + (1 if any(entry.derived for entry in entries) else 0)
    table.setRowCount(rows)

    row = 0
    separated = False
    for entry in entries:
        if entry.derived and not separated:
            # The derived traits (Attack, Defence) are not bought like the rest, and
            # the rule between them says so — spanned, since a table has no other way
            # to draw across its columns.
            table.setSpan(row, 0, 1, len(HEADERS))
            table.setCellWidget(row, 0, hline_separator())
            row += 1
            separated = True

        table.setItem(row, COL_NAME, readonly_item(entry.name))
        table.setItem(row, COL_ABBR, readonly_item(entry.abbr, center=True))

        spin = make_spin_box(
            trait_range.min,
            trait_range.max,
            value=values.get(entry.key, 0),
            buttons=False,
            max_width=int(theme.metric("column.stat.spin")),
        )
        spin.valueChanged.connect(lambda value, key=entry.key: on_change(key, value))
        store[entry.key] = spin
        table.setCellWidget(row, COL_RANK, spin)

        total = readonly_item("", center=True)
        if roll_spec is not None and roll_sink is not None:
            total.setData(ROLL_ROLE, entry.key)
        total_store[entry.key] = total
        table.setItem(row, COL_TOTAL, total)
        row += 1

    if roll_spec is not None and roll_sink is not None:
        table.cellDoubleClicked.connect(lambda r, _c: _row_spec_to(table, r, roll_spec, roll_sink))
        if load_sink is not None:
            # A single click always fires before the double-click that follows it, so
            # this runs first and the double then rolls. That is harmless and in fact
            # what is wanted: rolling loads the same spec anyway, so the pair is
            # load → (load + roll) on one spec. Deferring it by the double-click
            # interval would only make a plain click feel a beat late.
            table.cellClicked.connect(lambda r, _c: _row_spec_to(table, r, roll_spec, load_sink))
    fit_table_height(table)
    return table


def _row_spec_to(
    table: QTableWidget,
    row: int,
    roll_spec: RollHookFactory,
    sink: Callable[[RollSpec], None],
) -> None:
    """Send this row's spec to *sink*, if the row is one that rolls (a separator is not)."""
    item = table.item(row, COL_TOTAL)
    key = None if item is None else item.data(ROLL_ROLE)
    if not key:
        return
    spec = roll_spec(key)
    if spec is not None:
        sink(spec)


def fit_table_height(table: QTableWidget) -> None:
    """Fix *table*'s height to exactly show every row, so it never scrolls itself."""
    height = table.horizontalHeader().height() + 2 * table.frameWidth()
    for row in range(table.rowCount()):
        height += table.rowHeight(row)
    table.setFixedHeight(height)


def tint_item(item: QTableWidgetItem | None, token: str | None, struck: bool = False) -> None:
    """Colour *item* with the theme colour *token* (or clear it), and strike it or not.

    Clearing has to go through ``setData(ForegroundRole, None)`` rather than a
    "normal" brush: a table item has no inherited default to reinstate, so anything
    else leaves it painted in whatever it was last tinted.
    """
    if item is None:
        return
    font = item.font()
    font.setStrikeOut(struck)
    item.setFont(font)
    if token is None:
        item.setData(Qt.ItemDataRole.ForegroundRole, None)
    else:
        item.setForeground(QBrush(QColor(theme.color(token))))


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


def apply_stat_effects(
    spins: dict,
    totals: dict,
    bonuses: dict,
    cond_effects: dict[str, ConditionEffect] | None = None,
) -> None:
    """Fill or clear each trait's Total cell from power bonuses and conditions.

    The cell reads ``→ N``, where ``N`` is the rank spin box's value plus any power
    boost, then a condition overlay (a Hit penalty on Toughness, a halved/zeroed
    active defense, a scoped check penalty). A pure power boost tints green; any
    condition tints it red, struck through when the overlay reports the trait lost
    (``ConditionEffect.trait_lost`` — Disabled/Debilitated in the base data). A trait
    with neither keeps an empty cell, exactly as the Skills table's "+" column does.
    """

    cond_effects = cond_effects or {}
    for key, item in totals.items():
        bonus = bonuses.get(key)
        effect = cond_effects.get(key)
        has_cond = effect is not None and effect.active
        if not bonus and not has_cond:
            item.setText("")
            item.setToolTip("")
            tint_item(item, None)
            continue

        total = spins[key].value() + (bonus.amount if bonus else 0)
        if has_cond:
            total = effect.apply(total)
        item.setText(f"→ {total}")

        tips = []
        if bonus:
            tips.append(f"+{bonus.amount} from {', '.join(bonus.sources)}")
        if has_cond and effect.tooltip:
            tips.append(effect.tooltip)
        item.setToolTip("\n".join(tips))

        tint_item(
            item,
            CONDITION_TINT if has_cond else ENHANCED_TINT,
            struck=has_cond and effect.trait_lost,
        )


__all__ = [
    "CONDITION_TINT",
    "COL_ABBR",
    "COL_NAME",
    "COL_RANK",
    "COL_TOTAL",
    "ENHANCED_TINT",
    "ROLL_ROLE",
    "apply_stat_effects",
    "build_stat_table",
    "fit_table_height",
    "set_stat_value",
    "tint_item",
]

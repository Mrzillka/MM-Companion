from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from html import escape
from typing import TYPE_CHECKING

from PySide6.QtCore import QMimeData
from PySide6.QtWidgets import QLineEdit, QWidget

from mm_companion.core.character import Character
from mm_companion.core.data_loader import GameData
from mm_companion.core.registry import Registry
from mm_companion.core.rules import TRAIT_COLUMN_TYPE, config_source_options, trait_rank_cap
from mm_companion.ui.power_constructor.trait_picker import (
    TRAIT_SOURCE_ALL,
    TRAIT_SOURCE_BOOSTABLE,
    TRAIT_SOURCE_BUYABLE,
    TRAIT_SOURCES,
    CellContext,
    TraitPicker,
    build_trait_cell,
    fill_trait_combo,
)
from mm_companion.ui.widgets import make_spin_box

__all__ = [
    "CONFIG_WIDGET_BUILDERS",
    "REPEATABLE_CELL_KINDS",
    "RANK_MAX",
    "STRENGTH_AMOUNT_MAX",
    "TRAIT_SOURCES",
    "TRAIT_SOURCE_ALL",
    "TRAIT_SOURCE_BOOSTABLE",
    "TRAIT_SOURCE_BUYABLE",
    "CellContext",
    "ConfigWidgetBuilder",
    "RepeatableCellKind",
    "TraitPicker",
    "brick_tooltip",
    "config_source_options",
    "combat_focus_options",
    "fill_trait_combo",
    "is_trait_allocation",
    "link_trait_row",
    "register_base_config_widgets",
    "register_base_repeatable_cells",
    "repeatable_cell_kind",
]

if TYPE_CHECKING:  # forward ref only — importing EffectCard here would cycle
    from mm_companion.ui.power_constructor.effect_card import EffectCard

# -- config-field widget registry ----------------------------------------------
# Each effect config field declares a ``type``; the constructor builds the matching
# input widget for it. A builder receives the :class:`EffectCard`, the config field,
# and its effective type (``select`` upgrades to ``multiselect`` while gated), and
# returns the widget. The base types register at import; a mod's Python module can
# ``CONFIG_WIDGET_BUILDERS.register(type, builder)`` to add an input for a config-field
# ``type`` it also registered core-side (``CONFIG_DISPLAY_KINDS``) without editing this
# module. An unregistered type falls back to the generic option combo (unchanged).
ConfigWidgetBuilder = Callable[["EffectCard", object, str], "QWidget"]

CONFIG_WIDGET_BUILDERS: Registry[ConfigWidgetBuilder] = Registry("config-widget")


def register_base_config_widgets() -> None:
    """Register the built-in config-field input builders (idempotent-safe via replace)."""
    CONFIG_WIDGET_BUILDERS.register(
        "text", lambda card, field, ft: card._text_widget(field), replace=True
    )
    CONFIG_WIDGET_BUILDERS.register(
        "checkbox", lambda card, field, ft: card._checkbox_widget(field), replace=True
    )
    CONFIG_WIDGET_BUILDERS.register(
        "allocation", lambda card, field, ft: card._allocation_widget(field), replace=True
    )
    CONFIG_WIDGET_BUILDERS.register(
        "repeatable", lambda card, field, ft: card._repeatable_widget(field), replace=True
    )
    CONFIG_WIDGET_BUILDERS.register(
        "multiselect", lambda card, field, ft: card._multiselect_widget(field), replace=True
    )
    CONFIG_WIDGET_BUILDERS.register(
        "points", lambda card, field, ft: card._points_widget(field), replace=True
    )


register_base_config_widgets()


# -- repeatable column-cell registry -------------------------------------------
# A ``repeatable`` config field's rows are shaped by its columns, and each column's
# ``type`` decides what one cell looks like, how its value is read back, and whether it
# stretches. Same seam as ``CONFIG_WIDGET_BUILDERS`` one level down: a mod's Python
# module can register a column kind of its own. An unregistered type falls back to the
# plain line edit, which is what every column was before there were kinds.
@dataclass(frozen=True)
class RepeatableCellKind:
    """How one ``repeatable`` column's cell is built, read back, and laid out.

    ``build`` receives a :class:`~mm_companion.ui.power_constructor.trait_picker.CellContext`
    (the game data, and the character if there is one), the column, the row's stored dict
    and a ``commit`` callback, and returns the input widget — wiring the widget's own
    change signal to ``commit`` is the builder's job, since only it knows which signal
    that is. ``read`` turns the widget back into the value stored in the row. ``stretch``
    is the layout stretch the cell takes: a free-text or trait cell earns the leftover
    width, a rank spin does not.

    The builders take that context rather than the widget that hosts them so the same
    row of cells serves an effect's config field and a modifier selection's — Enhanced
    Trait's traits and its Reduced Trait flaw's are the same rows, and would drift if
    each side built its own.
    """

    build: Callable[[CellContext, object, dict, Callable[[], None]], QWidget]
    read: Callable[[QWidget], object]
    stretch: int = 0


REPEATABLE_CELL_KINDS: Registry[RepeatableCellKind] = Registry("repeatable-column")


def _repeatable_text_cell(context, column, initial: dict, commit) -> QWidget:
    """A free-text cell (an Immunity scope's name, a Feature's description)."""
    cell = QLineEdit(str(initial.get(column.key, "")))
    cell.setPlaceholderText(column.label)
    cell.textChanged.connect(lambda _t: commit())
    return cell


def _repeatable_int_cell(context, column, initial: dict, commit) -> QWidget:
    """A rank spin — the ranks this row spends out of the effect's rank pool."""
    cell = make_spin_box(
        0, RANK_MAX, value=int(initial.get(column.key, 0) or 0), buttons=False, max_width=48
    )
    cell.valueChanged.connect(lambda _v: commit())
    return cell


def register_base_repeatable_cells() -> None:
    """Register the built-in ``repeatable`` column kinds (idempotent-safe via replace)."""
    REPEATABLE_CELL_KINDS.register(
        "text",
        RepeatableCellKind(build=_repeatable_text_cell, read=lambda c: c.text().strip(), stretch=1),
        replace=True,
    )
    REPEATABLE_CELL_KINDS.register(
        "int",
        RepeatableCellKind(build=_repeatable_int_cell, read=lambda c: c.value()),
        replace=True,
    )
    REPEATABLE_CELL_KINDS.register(
        "trait",
        RepeatableCellKind(build=build_trait_cell, read=lambda c: c.value(), stretch=2),
        replace=True,
    )


register_base_repeatable_cells()


def link_trait_row(context: CellContext, columns, cells: dict) -> None:
    """Let a trait-allocation row's rank spin follow the cap of the trait it names.

    Most traits have no ceiling of their own — an ability or a skill is bounded by the
    Power Level, checked against the whole build elsewhere — but an advantage does: most
    are not ranked at all, and a few cap at a fixed number
    (:func:`~mm_companion.core.rules.trait_rank_cap`). A spin that stops at 1 for Fearless
    says so before the points are spent, which a warning after the fact does not.

    The two cells are built independently, so this is where they are introduced. Shared
    by the effect card's rows and a modifier chip's, since Enhanced Trait's rows and its
    Reduced Trait flaw's are the same rows and a cap enforced on one alone would be a cap
    the player could walk around.
    """

    picker = next(
        (cells.get(c.key) for c in columns if c.type == TRAIT_COLUMN_TYPE),
        None,
    )
    spin = next((cells.get(c.key) for c in columns if c.type == "int"), None)
    if not isinstance(picker, TraitPicker) or spin is None:
        return

    def apply_cap() -> None:
        cap = trait_rank_cap(context.character, context.game_data, picker.value())
        spin.setMaximum(cap if cap else RANK_MAX)

    picker.changed.connect(apply_cap)
    apply_cap()


def is_trait_allocation(field) -> bool:
    """Whether a ``repeatable`` field's rows name traits (Enhanced Trait, Reduced Trait).

    Such a field starts with one blank row rather than an "Add" button alone: the row is
    the whole question the field asks ("which trait?"), and an Enhanced Trait showing no
    trait picker at all reads as a missing control rather than an empty list. A scope or
    feature list, which really can be empty, still starts empty.
    """

    return any(c.type == TRAIT_COLUMN_TYPE for c in getattr(field, "columns", ()))


def repeatable_cell_kind(column) -> RepeatableCellKind:
    """The cell kind for one ``repeatable`` column.

    An unregistered column type reads as free text — what every column was before the
    kinds existed, so a mod's unknown type degrades rather than raising in the middle
    of building a card.
    """

    return REPEATABLE_CELL_KINDS.get(column.type) or REPEATABLE_CELL_KINDS.get("text")


def combat_focus_options(character: Character | None, game_data: GameData) -> list[tuple[str, str]]:
    """``(display, row_id)`` for each Close/Ranged Combat focus the wielder has.

    Combat skills are the focused ones linked to the Attack ability, so they're
    found data-driven (no hardcoded names); a focus row id matches the skills
    section's ``"<Skill>::<focus>"`` scheme. Empty without a character.
    """
    if character is None:
        return []
    options: list[tuple[str, str]] = []
    for skill in game_data.skills:
        if skill.ability != "ATK" or not skill.focused:
            continue
        for focus in character.focuses.get(skill.name, []):
            options.append((f"{skill.name}: {focus}", f"{skill.name}::{focus}"))
    return options


# Custom drag payload formats: the record id travels as the mime data.
EFFECT_MIME = "application/x-mm-effect"
MODIFIER_MIME = "application/x-mm-modifier"
CONFIGURATION_MIME = "application/x-mm-configuration"
# A chip carries its own index when dragged to reorder within its group.
CHIP_MIME = "application/x-mm-chip"

# Object name marking a palette section header, so headers can be addressed as such
# rather than by their text (a brick may share a group's name).
_GROUP_HEADER = "palette_group_header"

# The rank ceiling for effect and modifier spin boxes. Kept well above the usual
# PL-bound ranks so allocation-heavy effects — an Immunity whose named scopes sum
# past 30 (e.g. all Fortitude + all Will effects), a stacked Enhanced Trait — aren't
# clipped by the input. It's a UI guard rail, not a rules cap.
RANK_MAX = 250

# The ceiling for a Strength-Based (ability-folding) modifier's "amount used" spin
# box. Fixed and independent of the wielder's current ability so the power's point
# cost stays stable when that ability is enhanced or suppressed; a bought amount above
# the wielder's actual ability is flagged as a warning, not repriced.
STRENGTH_AMOUNT_MAX = 50


def _mime_id(mime: QMimeData, fmt: str) -> str:
    """Decode the record id carried by a drag in the given format."""
    return bytes(mime.data(fmt)).decode("utf-8")


def brick_tooltip(name: str, cost: str, description: str) -> str:
    """The hover text for an effect or modifier — its name, cost, and description.

    Module-level because a record reads the same wherever it is shown: the palette
    brick you drag in and the chip it becomes once attached both hint through here,
    so a modifier never explains itself one way in the list and another on the card.
    Rich text, so Qt wraps the description instead of running it off the screen.
    Every effect and modifier record carries a ``description``; the parts that are
    empty are simply left out.
    """

    parts = [f"<b>{escape(name)}</b>"]
    if cost:
        parts.append(escape(cost))
    if description:
        parts.append(f"<br>{escape(description)}")
    return "<br>".join(parts)


def _move_item(seq: list, from_index: int, to_index: int) -> bool:
    """Move ``seq[from_index]`` so it lands at insertion point ``to_index``.

    ``to_index`` is an insertion index in the *original* list (0..len), so a drop
    just before or just after the item is a no-op. Returns whether the order changed,
    so callers can skip firing change signals for a drag that settled in place.
    """
    target = to_index - 1 if to_index > from_index else to_index
    if target == from_index:
        return False
    seq.insert(target, seq.pop(from_index))
    return True

from __future__ import annotations

from collections.abc import Callable
from html import escape
from typing import TYPE_CHECKING

from PySide6.QtCore import QMimeData, Qt
from PySide6.QtWidgets import (
    QComboBox,
    QWidget,
)

from mm_companion.core.character import Character
from mm_companion.core.data_loader import GameData
from mm_companion.core.registry import Registry

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


register_base_config_widgets()


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


def _disable_section_headings(combo: QComboBox) -> None:
    """Grey out a trait combo's section-heading rows (those carrying ``None`` data)
    so they read as group labels rather than selectable traits."""
    model = combo.model()
    for index in range(combo.count()):
        if combo.itemData(index) is None:
            item = model.item(index)
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEnabled)


#: Config-field ``source`` values the trait picker answers to. ``"traits"`` lists only
#: the traits a power can *buy* up or down; ``"all_traits"`` adds the derived stats that
#: can still be rolled, for a field asking what the player checks rather than what the
#: power changes.
TRAIT_SOURCES = ("traits", "all_traits")


def _fill_trait_combo(combo: QComboBox, game_data, current: str, *, derived: bool = False) -> None:
    """Populate ``combo`` with the character's traits (abilities, resistances, skills)
    grouped under disabled headings, and select ``current``. Data-driven — the trait
    names come from the game data, never hardcoded. Shared by Enhanced Trait's "which
    trait goes up" picker and any modifier config field with ``source="traits"`` (the
    Reduced Trait flaw's "which trait goes down").

    With ``derived`` the list also covers the numeric stats that are computed rather
    than bought — the derived Defence aggregate and the ``derived_traits`` named in
    ``system.json`` (Initiative). Those can be *checked* but not raised, so they belong
    in Check Required's "which check" picker and not in a trait-boosting one."""
    combo.addItem("— choose a trait —", "")
    combo.addItem("Abilities", None)  # a disabled section heading
    for ability in game_data.abilities:
        combo.addItem(f"  {ability.name}", ability.key)
    combo.addItem("Resistances", None)
    for res in game_data.resistances:
        if derived or not res.derived:  # the derived Defence aggregate can't be bought
            combo.addItem(f"  {res.name}", res.key)
    combo.addItem("Skills", None)
    for skill in game_data.skills:
        combo.addItem(f"  {skill.name}", skill.name)
    if derived:
        extra = [d for d in game_data.system.derived_traits if combo.findData(d.key) < 0]
        if extra:
            combo.addItem("Other", None)
            for trait in extra:
                combo.addItem(f"  {trait.label}", trait.key)
    _disable_section_headings(combo)
    index = combo.findData(current)
    combo.setCurrentIndex(index if index >= 0 else 0)

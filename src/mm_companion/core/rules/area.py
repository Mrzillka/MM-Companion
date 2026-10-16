"""Area effects: what shape an area fills, how big it is, and the Dodge check against it.

An effect becomes an area effect by carrying one of the modifiers ``actions.json``'s
``area`` block names (the Area Effect extra in the base ruleset). That modifier's config
says the rest: the **shape** it fills, how many **applications** were bought (past the
first, each widens the shape by one distance rank, or doubles a Shapeable area's spaces),
and how it **spreads**. Everything a number depends on is data; this module only reads it.

The resolution half — who is in the area, who was hit directly, what each Dodge check
did — is :mod:`.area_attack`'s, which sits on :mod:`.rolls`. This half sits *below*
:mod:`.powers_terms` so the game-terms table can state an area's size.
"""

from __future__ import annotations

import math

from ..character import Character
from ..data_loader import GameData, Modifier
from ..powers import ModifierSelection, PowerEffectInstance
from .derived import all_advantage_selections

__all__ = [
    "AREA_SHAPE_KEY",
    "AREA_APPLICATIONS_KEY",
    "AREA_SPREAD_KEY",
    "area_modifier",
    "area_selection",
    "area_band_ranks",
    "effect_is_area",
    "area_shape_options",
    "area_shape",
    "area_shape_label",
    "area_applications",
    "area_spread_label",
    "area_distance_rank",
    "area_size_text",
    "area_dodge_dc",
    "area_resisted_rank",
    "evasion_rank",
    "area_dodge_bonus",
    "evasion_negates",
]

#: The area modifier's config keys (see ``modifiers.json``'s ``area_effect``).
AREA_SHAPE_KEY = "shape"
AREA_APPLICATIONS_KEY = "applications"
AREA_SPREAD_KEY = "spread"


def area_modifier(game_data: GameData) -> Modifier | None:
    """The ruleset's area modifier record — the first one the ``area`` block names."""

    catalog = game_data.modifier_catalog()
    return next(
        (catalog[mid] for mid in game_data.area.modifiers if mid in catalog),
        None,
    )


def area_selection(effect: PowerEffectInstance, game_data: GameData) -> ModifierSelection | None:
    """The selection that makes *effect* an area effect, or ``None`` if nothing does."""

    wanted = set(game_data.area.modifiers)
    return next((s for s in effect.extras if s.modifier_id in wanted), None)


def area_band_ranks(selection: ModifierSelection | None) -> int | None:
    """How many ranks a banded area covers — ``None`` when it covers them all.

    An area bought on part of an effect (a Rocket Launcher's Area on 7 of its 10 Damage
    ranks) is an area of that many ranks: the Dodge DC and the save everyone in the
    blast makes are figured from them, while the creature the attack hits directly
    takes the full effect.
    """

    if selection is None or not selection.applies_to:
        return None
    return max(1, selection.applies_to - max(1, selection.applies_from) + 1)


def effect_is_area(effect: PowerEffectInstance, game_data: GameData) -> bool:
    """Whether *effect* carries an area modifier."""

    return area_selection(effect, game_data) is not None


def _field_options(game_data: GameData, key: str) -> tuple[tuple[str, str], ...]:
    modifier = area_modifier(game_data)
    if modifier is None:
        return ()
    for cfg in modifier.config_fields:
        if cfg.key == key:
            return tuple((o.value, o.label) for o in cfg.options)
    return ()


def area_shape_options(game_data: GameData) -> tuple[tuple[str, str], ...]:
    """Every shape an area may fill, as ``(value, label)`` — for a picker."""

    return _field_options(game_data, AREA_SHAPE_KEY)


def area_shape(selection: ModifierSelection | None, game_data: GameData) -> str:
    """The shape *selection* fills — the ruleset's default when none was picked yet."""

    chosen = str((selection.config if selection else {}).get(AREA_SHAPE_KEY, "") or "")
    known = {value for value, _label in area_shape_options(game_data)}
    if chosen and (chosen in known or not known):
        return chosen
    if game_data.area.default_shape:
        return game_data.area.default_shape
    options = area_shape_options(game_data)
    return options[0][0] if options else ""


def area_shape_label(shape: str, game_data: GameData) -> str:
    """A shape id as it reads — ``"Sphere"``."""

    return dict(area_shape_options(game_data)).get(shape, shape.capitalize())


def area_applications(selection: ModifierSelection | None) -> int:
    """How many times the area modifier was applied — never fewer than one."""

    raw = (selection.config if selection else {}).get(AREA_APPLICATIONS_KEY, 1)
    try:
        return max(1, int(raw))
    except (TypeError, ValueError):
        return 1


def area_spread_label(selection: ModifierSelection | None, game_data: GameData) -> str:
    """The spread a selection picked, as it reads — ``""`` for the default (from a point)."""

    chosen = str((selection.config if selection else {}).get(AREA_SPREAD_KEY, "") or "")
    options = _field_options(game_data, AREA_SPREAD_KEY)
    if not chosen or (options and chosen == options[0][0]):
        return ""
    return dict(options).get(chosen, chosen)


def area_distance_rank(applications: int, game_data: GameData) -> int:
    """The distance rank every dimension of the shape spans."""

    return game_data.area.base_distance_rank + max(0, applications - 1)


def area_size_text(shape: str, applications: int, rank: int, game_data: GameData) -> str:
    """An area's size in words — ``"Sphere, 15 feet across"`` or ``"Shapeable, 8 spaces"``.

    The phrasing per shape is data (``area.shapes``): ``{d}`` is the measurement of the
    shape's distance rank, ``{n}`` a Shapeable area's spaces — its effect *rank*, doubled
    per application past the first.
    """

    applications = max(1, applications)
    template = game_data.area.shapes.get(shape, "{d}")
    distance = game_data.measurements.label("distance", area_distance_rank(applications, game_data))
    spaces = max(1, rank) * 2 ** (applications - 1)
    size = template.replace("{d}", distance or "").replace("{n}", str(spaces)).strip()
    label = area_shape_label(shape, game_data)
    return f"{label}, {size}" if size else label


def area_dodge_dc(rank: int, game_data: GameData) -> int:
    """The DC a creature in the area makes its Dodge check against: base + area rank."""

    return game_data.area.dc_base + rank


def area_resisted_rank(rank: int, game_data: GameData) -> int:
    """The rank a creature that made its Dodge check resists — half, rounded per data."""

    if game_data.area.halve == "down":
        return max(0, rank // 2)
    return max(0, math.ceil(rank / 2))


def evasion_rank(char: Character | None, game_data: GameData) -> int:
    """How many ranks of the area-dodging advantage (Evasion) *char* has — bought or granted."""

    evasion = game_data.area.evasion
    if char is None or evasion is None:
        return 0
    record = next((a for a in game_data.advantages if a.id == evasion.advantage), None)
    if record is None:
        return 0
    return max(
        (sel.rank for sel in all_advantage_selections(char, game_data) if sel.name == record.name),
        default=0,
    )


def area_dodge_bonus(char: Character | None, game_data: GameData) -> int:
    """What *char* adds to a Dodge check against an area (Evasion's +5), or 0."""

    evasion = game_data.area.evasion
    if evasion is None or evasion_rank(char, game_data) <= 0:
        return 0
    return evasion.dodge_bonus


def evasion_negates(char: Character | None, game_data: GameData) -> bool:
    """Whether a made Dodge check means *no* effect at all for *char* (Evasion 2)."""

    evasion = game_data.area.evasion
    if evasion is None or evasion.negate_at_rank <= 0:
        return False
    return evasion_rank(char, game_data) >= evasion.negate_at_rank

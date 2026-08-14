"""Movement speeds and size — the derived readouts the System block shows."""

from __future__ import annotations

from dataclasses import dataclass, replace

from ..character import Character
from ..data_loader import GameData
from .conditions import condition_speed_rank_mod
from .powers_cost import effect_effective_rank
from .runtime import effect_is_active, live_powers, worn_items
from .size import effective_size_rank

# -- movement / speed ------------------------------------------------------------


@dataclass(frozen=True)
class SpeedLine:
    """One movement mode's speed, as a rank the UI expands into walk/dash/run columns.

    ``label`` names the mode (``"Base"`` for ground movement, else the power's effect
    and rank, e.g. ``"Flight 2"``); ``rank`` is the speed rank the three distance
    columns derive from.

    ``rank_mod`` and ``immobilised`` carry a condition overlay
    (:func:`condition_speed_lines`): ``rank_mod`` is the penalty *already folded into*
    ``rank``, kept alongside so the UI can say how much was lost and tint the line, and
    ``immobilised`` marks a line a condition has zeroed outright. Both are inert on the
    plain build lines :func:`speed_lines` returns.
    """

    label: str
    rank: int
    rank_mod: int = 0
    immobilised: bool = False


def _size_speed_mod(char: Character, game_data: GameData) -> int:
    """The ground-speed rank modifier the character's *effective* size confers.

    Zero at Medium; an active Growth pushes it up, Shrinking down, via the Size Table's
    ``speed_mod`` (see :func:`effective_size_rank`).
    """

    row = game_data.measurements.size_row(effective_size_rank(char, game_data))
    return row.speed_mod if row else 0


def base_ground_speed_rank(char: Character, game_data: GameData) -> int:
    """The character's walking (ground) speed rank before per-mode columns.

    The data-driven base (``movement.json``) plus any size-derived speed modifier, so
    growing or shrinking shifts ground movement.
    """

    return game_data.movement.base_ground_speed_rank + _size_speed_mod(char, game_data)


def _build_speed_lines(
    build, char: Character, game_data: GameData, *, name: str = ""
) -> list[SpeedLine]:
    """The speed lines **one** assembled build currently grants.

    Every active effect carrying a per-round distance measure (Flight, Speed, Swimming,
    Burrowing, …) at its *effective* rank. Shared by :func:`speed_lines` and
    :func:`equipment_speed_lines`, since an item's
    :attr:`~mm_companion.core.equipment.EquipmentItem.build` is a real
    :class:`~mm_companion.core.powers.Power` — the same seam
    :func:`~.runtime.build_contributions` uses, so gear and powers cannot drift.

    *name* is what to call the line. Empty (the powers case) labels it by the base
    effect, because a power's own title is arbitrary while *Flight* is the mechanic; a
    piece of gear passes its own name instead, since that is what the item **is** and
    two worn items granting the same effect would otherwise read identically.
    """

    lines: list[SpeedLine] = []
    for effect in build.effects:
        base = next((e for e in game_data.effects if e.id == effect.effect_id), None)
        if base is None or base.measure is None:
            continue
        if base.measure.column != "distance" or not base.measure.per_round:
            continue
        if not effect_is_active(build, effect, base, game_data, char):
            continue
        rank = effect_effective_rank(effect, game_data, char)
        lines.append(SpeedLine(f"{name or base.name} {rank}", rank))
    return lines


def equipment_speed_lines(char: Character, game_data: GameData) -> list[SpeedLine]:
    """The movement speeds the character's **worn** gear currently grants.

    A glider flies and a bicycle is faster than walking, so gear reaches the Speed
    readout exactly as a movement power does. Only *worn* items count
    (:func:`~.runtime.worn_items`) — a bicycle in the garage moves nobody — which is the
    same runtime gate a power's on/off switch provides.

    No base ground line here: that belongs to the character, not to any one source, and
    :func:`speed_lines` supplies it once.
    """

    lines: list[SpeedLine] = []
    for item in worn_items(char):
        lines.extend(_build_speed_lines(item.build, char, game_data, name=item.name))
    return lines


def speed_lines(char: Character, game_data: GameData) -> list[SpeedLine]:
    """The character's movement speeds — a base ground line plus one per active mode.

    The first line is always ground movement (:func:`base_ground_speed_rank`). Then
    every currently-active power effect that carries a per-round distance measure
    (Flight, Speed, Swimming, Burrowing, …) adds its own line at its *effective* rank,
    labelled by the effect name and rank, and finally the worn gear that does the same
    (:func:`equipment_speed_lines`). A switched-off or suppressed movement power — and a
    stowed item — contributes nothing (:func:`effect_is_active`).

    The base line stays first, which is what lets :func:`condition_speed_lines` overlay
    the ground penalty on ``lines[0]`` however many modes are appended after it.
    """

    lines = [SpeedLine("Base", base_ground_speed_rank(char, game_data))]
    for power in live_powers(char.powers):
        lines.extend(_build_speed_lines(power, char, game_data))
    lines.extend(equipment_speed_lines(char, game_data))
    return lines


def condition_speed_lines(char: Character, game_data: GameData) -> list[SpeedLine]:
    """:func:`speed_lines` with the character's condition overlay resolved into them.

    Conditions are a display overlay, not build math, and they only reach the *base*
    (ground) line — a Hindered character's Flight is unaffected. Resolving it here
    rather than in the widget keeps the arithmetic in one place, the way
    :func:`~mm_companion.core.rules.resistance_condition_effect` and
    :func:`~mm_companion.core.rules.condition_scope_penalty` already do for the grids.
    """

    lines = speed_lines(char, game_data)
    mod = condition_speed_rank_mod(char, game_data)
    if not lines or mod == 0:
        return lines
    base = lines[0]
    if mod is None:  # Immobile / Prone zeroes ground movement outright
        lines[0] = replace(base, immobilised=True)
    else:
        lines[0] = replace(base, rank=base.rank + mod, rank_mod=mod)
    return lines


@dataclass(frozen=True)
class MovementModeLine:
    """One specialised way of moving an active power grants (Wall-Crawling, Permeate…).

    Unlike a :class:`SpeedLine`, a mode is not a movement power of its own — it is one
    allocation option chosen inside an effect such as Enhanced Movement, so its rate
    comes from the option's tier (often relative to the character's ground speed)
    rather than from the effect's rank. ``rank`` is that rate's distance rank,
    ``note`` an optional per-tier caveat ("vulnerable while climbing"), and
    ``description`` the option's hover text.
    """

    label: str
    rank: int
    description: str = ""
    note: str = ""


#: The ``statIntegration.affects`` category marking an effect as one that changes how
#: the character *moves*. Enhanced Senses lays its sense qualities out with the same
#: ``allocation`` field type, so the field shape alone can't tell the two apart.
MOVEMENT_AFFECTS = "movement"


def _affects_movement(base) -> bool:
    """Whether a base effect's ``statIntegration`` declares it a movement effect."""
    boost = base.integration.trait_boost
    return boost is not None and MOVEMENT_AFFECTS in boost.affects


def _build_mode_lines(
    build, char: Character, game_data: GameData, ground: int
) -> list[MovementModeLine]:
    """The specialised movement modes **one** assembled build currently grants."""

    lines: list[MovementModeLine] = []
    for effect in build.effects:
        base = next((e for e in game_data.effects if e.id == effect.effect_id), None)
        if base is None or not _affects_movement(base):
            continue
        if not effect_is_active(build, effect, base, game_data, char):
            continue
        for field in base.config_fields:
            if field.type != "allocation":
                continue
            chosen = {
                entry["id"]: int(entry.get("tier", 1))
                for entry in effect.config.get(field.key, [])
                if isinstance(entry, dict) and "id" in entry
            }
            for option in field.alloc_options:
                tier = chosen.get(option.id)
                if tier is None:
                    continue
                speed = option.speed(tier)
                if speed is None:
                    continue  # a capability, not a speed — nothing to show here
                label = option.label + (f" {tier}" if len(option.tiers) > 1 else "")
                lines.append(
                    MovementModeLine(
                        label,
                        speed.rank(ground),
                        option.description,
                        option.tier_note(tier),
                    )
                )
    return lines


def movement_mode_lines(char: Character, game_data: GameData) -> list[MovementModeLine]:
    """The specialised movement *speeds* the character's active builds currently grant.

    Walks the ``allocation`` config fields of every live effect that declares itself a
    *movement* effect (:func:`_affects_movement`) and yields one line per chosen option
    at the rate its tier grants — a flat rank for a mode with a speed of its own
    (Swinging), or one derived from the character's ground speed for a mode expressed
    against it (Wall-Crawling).

    Worn gear is walked alongside the live powers, for the reason
    :func:`equipment_speed_lines` gives: a swing line grants Swinging exactly as a power
    would, and showing gear-granted Flight while hiding gear-granted Swinging would be a
    distinction the sheet cannot justify. A mode is named by the *option* either way —
    the capability is what the row is about, and the item's own card is where it says
    which thing grants it.

    Only options that actually confer a **rate** appear. Safe Fall, Stable, Trackless
    and the like are real capabilities, but they are not speeds, so listing them under a
    speed readout would say nothing; they stay on the power's own card instead. Empty
    when nothing is switched on.
    """

    ground = base_ground_speed_rank(char, game_data)
    lines: list[MovementModeLine] = []
    for power in live_powers(char.powers):
        lines.extend(_build_mode_lines(power, char, game_data, ground))
    for item in worn_items(char):
        lines.extend(_build_mode_lines(item.build, char, game_data, ground))
    return lines


def speed_columns(rank: int, game_data: GameData, *, metric: bool = False) -> tuple[str, str, str]:
    """The walk / dash / run distances for a speed ``rank``.

    Each column is the measurements-table distance at ``rank`` plus the movement
    steps (``walk``/``dash``/``run`` rank steps). With ``metric=False`` the columns are
    the table's imperial per-round labels; with ``metric=True`` they are the sustained
    speed in km/h, computed from the metric distance per round and ``round_seconds``.
    """

    move = game_data.movement
    steps = (move.walk_rank_step, move.dash_rank_step, move.run_rank_step)
    if not metric:
        return tuple(  # type: ignore[return-value]
            (game_data.measurements.label("distance", rank + step) or "—") for step in steps
        )
    return tuple(_speed_kmh(rank + step, game_data) for step in steps)  # type: ignore[return-value]


def _speed_kmh(rank: int, game_data: GameData) -> str:
    """A per-round distance rank rendered as a km/h speed label (e.g. ``"18 km/h"``)."""

    metres = game_data.measurements.distance_m(rank)
    seconds = game_data.movement.round_seconds or 6
    kmh = metres / seconds * 3.6
    if kmh <= 0:
        return "—"
    text = f"{kmh:.0f}" if kmh >= 10 else f"{kmh:.1f}"
    return f"{text} km/h"

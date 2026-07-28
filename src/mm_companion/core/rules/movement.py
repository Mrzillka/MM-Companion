"""Movement speeds and size — the derived readouts the System block shows."""

from __future__ import annotations

from dataclasses import dataclass

from ..character import Character
from ..data_loader import GameData
from .powers_cost import effect_effective_rank
from .runtime import effect_is_active, live_powers

# -- movement / speed ------------------------------------------------------------


@dataclass(frozen=True)
class SpeedLine:
    """One movement mode's speed, as a rank the UI expands into walk/dash/run columns.

    ``label`` names the mode (``"Base"`` for ground movement, else the power's effect
    and rank, e.g. ``"Flight 2"``); ``rank`` is the speed rank the three distance
    columns derive from.
    """

    label: str
    rank: int


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


def speed_lines(char: Character, game_data: GameData) -> list[SpeedLine]:
    """The character's movement speeds — a base ground line plus one per active mode.

    The first line is always ground movement (:func:`base_ground_speed_rank`). Then
    every currently-active power effect that carries a per-round distance measure
    (Flight, Speed, Swimming, Burrowing, …) adds its own line at its *effective* rank,
    labelled by the effect name and rank. A switched-off or suppressed movement power
    contributes nothing (:func:`effect_is_active`).
    """

    lines = [SpeedLine("Base", base_ground_speed_rank(char, game_data))]
    for power in live_powers(char.powers):
        for effect in power.effects:
            base = next((e for e in game_data.effects if e.id == effect.effect_id), None)
            if base is None or base.measure is None:
                continue
            if base.measure.column != "distance" or not base.measure.per_round:
                continue
            if not effect_is_active(power, effect, base, game_data, char):
                continue
            rank = effect_effective_rank(effect, game_data, char)
            lines.append(SpeedLine(f"{base.name} {rank}", rank))
    return lines


@dataclass(frozen=True)
class MovementModeLine:
    """One specialised way of moving an active power grants (Wall-Crawling, Permeate…).

    Unlike a :class:`SpeedLine`, a mode is not a movement power of its own — it is one
    allocation option chosen inside an effect such as Enhanced Movement, so it moves at
    a rate derived from the character's ground speed rather than from the effect's rank.
    ``rank`` is that rate's distance rank, or ``None`` for a mode that confers no rate
    of its own (Safe Fall, Trackless). ``description`` is the option's hover text.
    """

    label: str
    rank: int | None
    description: str = ""


#: The ``statIntegration.affects`` category marking an effect as one that changes how
#: the character *moves*. Enhanced Senses lays its sense qualities out with the same
#: ``allocation`` field type, so the field shape alone can't tell the two apart.
MOVEMENT_AFFECTS = "movement"


def _affects_movement(base) -> bool:
    """Whether a base effect's ``statIntegration`` declares it a movement effect."""
    boost = base.integration.trait_boost
    return boost is not None and MOVEMENT_AFFECTS in boost.affects


def movement_mode_lines(char: Character, game_data: GameData) -> list[MovementModeLine]:
    """The specialised movement modes the character's active powers currently grant.

    Walks the ``allocation`` config fields of every live effect that declares itself a
    *movement* effect (:func:`_affects_movement`) and yields one line per chosen option,
    at the ground speed rank shifted by that option's tier (``speed_rank_offsets`` —
    ``0`` full speed, ``-1`` half). Options that grant no rate still list, so the block
    shows *every* way the character can move, not only the ones with a number. Empty
    when nothing is switched on.
    """

    ground = base_ground_speed_rank(char, game_data)
    lines: list[MovementModeLine] = []
    for power in live_powers(char.powers):
        for effect in power.effects:
            base = next((e for e in game_data.effects if e.id == effect.effect_id), None)
            if base is None or not _affects_movement(base):
                continue
            if not effect_is_active(power, effect, base, game_data, char):
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
                    offset = option.speed_rank_offset(tier)
                    label = option.label + (f" {tier}" if len(option.tiers) > 1 else "")
                    lines.append(
                        MovementModeLine(
                            label,
                            None if offset is None else ground + offset,
                            option.description,
                        )
                    )
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


# -- size ------------------------------------------------------------------------


def size_shift(char: Character, game_data: GameData) -> int:
    """Net size-rank shift from active size-altering powers (Growth +, Shrinking −).

    Reads each live effect's ``size_table`` readout (``effect_readouts.json``) and, when
    the effect is currently active, applies its signed rank. Zero when no size power is
    on, so the character sits at their bought size.
    """

    shift = 0
    for power in live_powers(char.powers):
        for effect in power.effects:
            base = next((e for e in game_data.effects if e.id == effect.effect_id), None)
            if base is None:
                continue
            for readout in game_data.effect_readouts.get(effect.effect_id, ()):
                if readout.kind != "size_table":
                    continue
                if not effect_is_active(power, effect, base, game_data, char):
                    continue
                sign = int(readout.data.get("sign", 1))
                shift += sign * effect_effective_rank(effect, game_data, char)
    return shift


def base_size_rank(char: Character, game_data: GameData) -> int:
    """The bought size category's rank (Medium → 0), defaulting to Medium."""

    category = str(char.characteristics.get("size", "Medium"))
    rank = game_data.measurements.size_rank_for_category(category)
    return rank if rank is not None else 0


def effective_size_rank(char: Character, game_data: GameData) -> int:
    """The character's current size rank: their bought size plus any :func:`size_shift`."""

    return base_size_rank(char, game_data) + size_shift(char, game_data)


def effective_size(char: Character, game_data: GameData) -> str:
    """The character's current size category, after active Growth/Shrinking.

    The bought size (clamped to the Size Table) when nothing alters it; otherwise the
    category the shifted rank lands on.
    """

    row = game_data.measurements.size_row(effective_size_rank(char, game_data))
    if row is not None:
        return row.size_category
    return str(char.characteristics.get("size", "Medium"))

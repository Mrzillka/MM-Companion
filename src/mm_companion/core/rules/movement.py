"""Movement speeds and size — the derived readouts the System block shows."""

from __future__ import annotations

from dataclasses import dataclass, replace

from ..character import Character
from ..components import APPLY_PENALTY_REMOVED
from ..data_loader import GameData
from .appliers import (
    CATEGORY_MOVEMENT,
    CATEGORY_PENALTY,
    GROUP_EQUIPMENT,
    GROUP_POWERS,
    STACK_MAX,
    STACK_SUM,
    TraitContribution,
    resolve_contributions,
)
from .conditions import condition_speed_rank_mod
from .derived import effort_contributions
from .powers_cost import effect_effective_rank
from .runtime import build_contributions, effect_is_active, live_powers, worn_items
from .size import base_size_rank, effective_size_rank

# -- movement / speed ------------------------------------------------------------


@dataclass(frozen=True)
class SpeedLine:
    """One movement mode's speed, as a rank the UI expands into walk/dash/run columns.

    ``label`` names the mode and nothing else — ``"Ground speed"`` for the line
    everybody has (:attr:`~mm_companion.core.data_loader.Movement.ground_label`), else
    the name of the effect that declares the mode (``"Flight"``, ``"Burrowing"``).
    ``rank`` is the speed rank the distance columns derive from, and it is deliberately
    *not* in the caption: a line is the net of everything granting that mode, so the one
    rank a label could name is nobody's in particular — what fed it is on ``sources``.

    ``sources`` names everything that fed the line, because a line is now the *mode*
    rather than any one power: two Flight powers make one flight speed, and the label
    can only name the mode, so what granted it goes on the hover instead.

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
    sources: tuple[str, ...] = ()
    #: The mode key the line was netted from (``"ground"``, ``"flight"``) — what the
    #: contributions are keyed by, as opposed to what the line is *called*. A caller that
    #: needs to write back to a mode (Extra Effort pushing one) needs the key, and a
    #: label is a display string that a ruleset may translate.
    mode: str = ""


def _size_speed_mod(char: Character, game_data: GameData) -> int:
    """The ground-speed rank modifier the character's *effective* size confers.

    Zero at Medium; an active Growth pushes it up, Shrinking down, via the Size Table's
    ``speed_mod`` (see :func:`effective_size_rank`).
    """

    row = game_data.measurements.size_row(effective_size_rank(char, game_data))
    return row.speed_mod if row else 0


def _size_speed_shift(char: Character, game_data: GameData) -> int:
    """How much of the speed modifier an active size power is responsible for.

    The difference between the effective row's ``speed_mod`` and the *bought* row's —
    which is the only part a "your speed isn't reduced while shrunk" extra has any
    claim on. The whole row's modifier is not: a character who is simply Small owns
    that penalty, and cancelling it would have their Shrinking make them faster than
    they are with the power switched off.

    It has to be a difference rather than the effect's rank, too. The rank is what the
    effect was *bought* at, while the row follows what it is currently dialled to
    (:func:`~.runtime.effect_current_rank`), and the Size Table clamps at its ends — so
    the two agree only on a linear stretch of the table with nothing dialled, and part
    company exactly when someone uses the ladder.
    """

    effective = _size_speed_mod(char, game_data)
    row = game_data.measurements.size_row(base_size_rank(char, game_data))
    return effective - (row.speed_mod if row else 0)


def _ground_penalty_removed(char: Character, game_data: GameData) -> int:
    """How much of a ground-speed *penalty* something active cancels.

    The consumer for :data:`~..components.APPLY_PENALTY_REMOVED` on the ground mode:
    Shrinking's *Normal Speed* extra says "your speed isn't reduced while shrunk", and
    that is a penalty being lifted rather than a bonus being granted — which is the whole
    reason the two appliers are separate. Only powers count; the character has to be the
    one shrinking.
    """

    removed = 0
    for power in live_powers(char.powers):
        for grant in build_contributions(power, char, game_data):
            if (
                grant.category == CATEGORY_PENALTY
                and grant.kind == APPLY_PENALTY_REMOVED
                and grant.stat == game_data.movement.ground_mode
            ):
                removed += grant.amount
    return removed


def base_ground_speed_rank(char: Character, game_data: GameData) -> int:
    """The character's walking (ground) speed rank before per-mode columns.

    The data-driven base (``movement.json``) plus any size-derived speed modifier, so
    growing or shrinking shifts ground movement — less whatever cancels that penalty
    (:func:`_ground_penalty_removed`).

    The cancellation is **clamped at zero**: lifting a penalty can leave you at your
    normal speed and no faster, so a Shrinking 2 with Normal Speed 4 walks at its base
    rank rather than running. It also only ever reaches a *negative* modifier — a Growth
    is not a penalty to be cancelled — and only the part of it a size power *added*
    (:func:`_size_speed_shift`), so a character who is simply Small keeps their own −1
    however much of their Shrinking is cancelled.
    """

    size_mod = _size_speed_mod(char, game_data)
    shift = _size_speed_shift(char, game_data)
    if shift < 0:
        cancelled = min(-shift, _ground_penalty_removed(char, game_data))
        size_mod += cancelled
    return game_data.movement.base_ground_speed_rank + size_mod


def _measure_grants(
    build,
    char: Character,
    game_data: GameData,
    *,
    name: str = "",
    stacking: str = STACK_SUM,
    group: str = GROUP_POWERS,
) -> list[TraitContribution]:
    """The movement one assembled build grants, as contributions keyed by **mode**.

    Every active effect carrying a per-round distance measure (Flight, Speed, Swimming,
    Burrowing, …) at its *effective* rank, lifted into the same
    :class:`~.appliers.TraitContribution` shape the trait bonuses use so one resolver can
    net them (:func:`speed_lines`). Shared by the powers and the gear walks, since an
    item's :attr:`~mm_companion.core.equipment.EquipmentItem.build` is a real
    :class:`~mm_companion.core.powers.Power` — the same seam
    :func:`~.runtime.build_contributions` uses, so gear and powers cannot drift.

    Deliberately *not* routed through ``STAT_APPLIERS`` like a trait boost: this walk
    uses the **effective** rank where an applier gets the bought one, and giving these
    effects a ``statIntegration.target`` would make :func:`_affects_movement` true and
    drag them into :func:`movement_mode_lines`, which is a different readout entirely.

    *name* is what to call the source. Empty (the powers case) names the base effect,
    because a power's own title is arbitrary while *Flight* is the mechanic; a piece of
    gear passes its own name instead, since that is what the item **is** and two worn
    items granting the same effect would otherwise read identically.
    """

    grants: list[TraitContribution] = []
    for effect in build.effects:
        base = next((e for e in game_data.effects if e.id == effect.effect_id), None)
        if base is None or base.measure is None:
            continue
        if base.measure.column != "distance" or not base.measure.per_round:
            continue
        if not effect_is_active(build, effect, base, game_data, char):
            continue
        rank = effect_effective_rank(effect, game_data, char)
        grants.append(
            TraitContribution(
                amount=rank,
                stat=base.measure.mode or base.id,
                category=CATEGORY_MOVEMENT,
                source=f"{name or base.name} {rank}",
                stacking=stacking,
                group=group,
            )
        )
    return grants


def _movement_grants(char: Character, game_data: GameData) -> list[TraitContribution]:
    """Everything on the sheet that grants a movement rank, in one list.

    Two producers, and they stay two: the ``measure.perRound`` walk above, and the
    ``movement``-category contributions a *modifier* can now grant (Elongation's
    Striding, which the appliers turn into ranks of the ground mode). One reducer nets
    them, in :func:`speed_lines`.

    Gear travels under its own group and exclusivity, exactly as its trait bonuses do,
    so a worn glider is weighed against a Flight power rather than piled on top of it
    (``docs/mm-equipment-design.md`` §3). A mode pushed with Extra Effort joins them from
    :func:`~.derived.effort_contributions`, in the group that never competes.
    """

    grants: list[TraitContribution] = [
        # A mode's rank pushed with Extra Effort, in its own always-added group so it
        # sits on top of the modes the build grants rather than competing with them.
        grant
        for grant in effort_contributions(char, game_data)
        if grant.category == CATEGORY_MOVEMENT
    ]
    for power in live_powers(char.powers):
        grants.extend(_measure_grants(power, char, game_data))
        grants.extend(
            c
            for c in build_contributions(power, char, game_data)
            if c.category == CATEGORY_MOVEMENT
        )
    for item in worn_items(char):
        stacking = STACK_SUM if item.stacks else STACK_MAX
        grants.extend(
            _measure_grants(
                item.build,
                char,
                game_data,
                name=item.name,
                stacking=stacking,
                group=GROUP_EQUIPMENT,
            )
        )
        grants.extend(
            c
            for c in build_contributions(
                item.build, char, game_data, stacking=stacking, group=GROUP_EQUIPMENT
            )
            if c.category == CATEGORY_MOVEMENT
        )
    return grants


def equipment_speed_lines(char: Character, game_data: GameData) -> list[SpeedLine]:
    """The movement speeds the character's **worn** gear currently grants, on its own.

    Only *worn* items count (:func:`~.runtime.worn_items`) — a bicycle in the garage
    moves nobody — which is the same runtime gate a power's on/off switch provides.

    No base ground line here: that belongs to the character, not to any one source, and
    :func:`speed_lines` supplies it once. Note these are the gear's *unreconciled*
    grants: the sheet's readout nets them against the powers' in :func:`speed_lines`,
    where a glider can legitimately lose to a Flight power.
    """

    lines: list[SpeedLine] = []
    for item in worn_items(char):
        for grant in _measure_grants(item.build, char, game_data, name=item.name):
            lines.append(SpeedLine(grant.source, grant.amount, sources=(grant.source,)))
    return lines


def _mode_label(mode: str, game_data: GameData) -> str:
    """What to call a movement mode on the readout.

    The name of the effect that declares it, so nothing here spells "Flight": the four
    movement effects name their own mode in ``effects.json``, and a mod's fifth is
    labelled by the same rule. An unclaimed mode falls back to its own id.
    """

    for effect in game_data.effects:
        if effect.measure is not None and effect.measure.mode == mode:
            return effect.name
    return mode.replace("_", " ").title()


def speed_lines(char: Character, game_data: GameData) -> list[SpeedLine]:
    """The character's movement speeds — one line per **mode**, not per source.

    The first line is always ground movement. Then every other mode anything active
    grants, netted by :func:`~.appliers.resolve_contributions`: two Flight powers sum
    into one flight speed, a worn glider is weighed against them rather than added, and
    each line names its contributors on :attr:`SpeedLine.sources`.

    A mode's rank is the **sum** of everything granting it, and the ground mode is that
    same sum started from what the character has for free: their own ground rank
    (:func:`base_ground_speed_rank`, which is where size lands) plus whatever the
    ground-mode grants net to. So a Speed 1 makes a Medium character *faster* than
    walking rather than merely matching it, and Elongation's Striding adds on top of
    both. (It used to be a ``max`` against the base — which meant the first rank or two
    of Speed a character bought did nothing at all.) A switched-off movement power — and
    a stowed item — contributes nothing (:func:`effect_is_active`).

    The ground line stays first, which is what lets :func:`condition_speed_lines`
    overlay the ground penalty on ``lines[0]`` however many modes are appended after it.
    """

    ground_mode = game_data.movement.ground_mode
    by_mode: dict[str, list[TraitContribution]] = {}
    for grant in _movement_grants(char, game_data):
        by_mode.setdefault(grant.stat, []).append(grant)

    base = base_ground_speed_rank(char, game_data)
    ground = resolve_contributions(by_mode.pop(ground_mode, []))
    lines = [
        SpeedLine(
            game_data.movement.ground_label,
            base + (ground.amount if ground else 0),
            sources=ground.sources if ground else (),
            mode=ground_mode,
        )
    ]
    for mode, grants in by_mode.items():
        netted = resolve_contributions(grants)
        if netted is None:
            continue
        lines.append(
            SpeedLine(
                _mode_label(mode, game_data),
                netted.amount,
                sources=netted.sources,
                mode=mode,
            )
        )
    return lines


def ground_speed_rank(char: Character, game_data: GameData) -> int:
    """How fast the character actually moves on the ground, everything counted.

    The rank of :func:`speed_lines`' first line — the base rank plus every active grant
    on the ground mode — as opposed to :func:`base_ground_speed_rank`, which is only the
    part nobody paid for. This is the one a mode expressed *relative* to walking is
    measured against (:func:`movement_mode_lines`): a character with Speed 4 crawls up a
    wall at their real pace, not at the pace they would have had without the power.
    """

    return speed_lines(char, game_data)[0].rank


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

    A mode expressed against walking is measured against :func:`ground_speed_rank` — how
    fast the character *actually* moves, Speed powers and Striding included — rather than
    the bare :func:`base_ground_speed_rank`, or a speedster would crawl up a wall at the
    pace they walk without their power.
    """

    ground = ground_speed_rank(char, game_data)
    lines: list[MovementModeLine] = []
    for power in live_powers(char.powers):
        lines.extend(_build_mode_lines(power, char, game_data, ground))
    for item in worn_items(char):
        lines.extend(_build_mode_lines(item.build, char, game_data, ground))
    return lines


def speed_columns(
    rank: int, game_data: GameData, *, metric: bool = False, ground: bool = True
) -> tuple[str, ...]:
    """The walk / dash / run distances for a speed ``rank``.

    Each column is the measurements-table distance at ``rank`` plus the movement
    steps (``walk``/``dash``/``run`` rank steps). With ``metric=False`` the columns are
    the table's imperial per-round labels; with ``metric=True`` they are the sustained
    speed in km/h, computed from the metric distance per round and ``round_seconds``.

    With ``ground=False`` — any mode but the one the character walks in — the run column
    is dropped, since running is a ground manoeuvre and a flier's line has only a move
    and a dash to print. Whether that holds is the ruleset's to say
    (:attr:`~mm_companion.core.data_loader.Movement.run_is_ground_only`), which is why
    the result is a variable-length tuple rather than always three columns.
    """

    move = game_data.movement
    steps = (move.walk_rank_step, move.dash_rank_step, move.run_rank_step)
    if not ground and move.run_is_ground_only:
        steps = steps[:-1]
    if not metric:
        return tuple(
            (game_data.measurements.label("distance", rank + step) or "—") for step in steps
        )
    return tuple(_speed_kmh(rank + step, game_data) for step in steps)


def _speed_kmh(rank: int, game_data: GameData) -> str:
    """A per-round distance rank rendered as a km/h speed label (e.g. ``"18 km/h"``)."""

    metres = game_data.measurements.distance_m(rank)
    seconds = game_data.movement.round_seconds or 6
    kmh = metres / seconds * 3.6
    if kmh <= 0:
        return "—"
    text = f"{kmh:.0f}" if kmh >= 10 else f"{kmh:.1f}"
    return f"{text} km/h"

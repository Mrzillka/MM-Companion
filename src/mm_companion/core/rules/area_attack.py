"""Area attacks: one power against everyone in an area, resolved the way Impress is.

An area use of a power has three moving parts, and the rules give each a clear owner:

1. **A primary target**, optionally. The attacker may aim the area at one creature
   with an ordinary attack check against its Defense; a hit gives that creature no
   Dodge check — it resists the effect at full rank. Without one the area is placed
   on a point in space, a routine check nobody needs to roll.
2. **Everyone else in the area** makes a Dodge check against ``10 + area rank``.
   Making it gives Resistance: the rank they then resist the effect at is halved
   (:func:`~.area.area_resisted_rank`). Evasion adds +5 to that Dodge check, and at
   rank 2 a made one means no effect at all.
3. **The effect's own resistance** — Toughness against a Damage, whatever an
   Affliction names — at the full or halved rank, landing on a rung of the effect's
   own ladder exactly as a single-target hit does (:mod:`.targeting`).

This module turns a power into an :class:`AreaUse` (what it offers as an area), and
defines the record a player's app sends the GM (:class:`AreaAttempt`) and the specs
the history offers caught players. The GM's window decides who is in the area; the
session server rolls; :mod:`.targeting` applies. Nothing here names a shape, a
resistance or a condition: those are data.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from ..character import Character
from ..data_loader import GameData, ResistanceOutcome
from ..powers import Power, PowerEffectInstance
from .accessories import item_effective_build
from .area import (
    area_applications,
    area_dodge_bonus,
    area_dodge_dc,
    area_resisted_rank,
    area_selection,
    area_shape,
    area_shape_options,
    area_size_text,
    area_spread_label,
    effect_is_area,
    evasion_negates,
)
from .powers_terms import (
    effect_area_rank,
    effect_attack_skill_bonus,
    effect_makes_attack,
    effect_roll_numbers,
    effect_stat_rows,
)
from .rolls import (
    KIND_AREA,
    KIND_AREA_DODGE,
    KIND_POWER_CHECK,
    KIND_POWER_SAVE,
    RollSpec,
    _resistance_key,
    power_rolls,
    resistance_roll,
)
from .targeting import ladder_captions, outcome_rung, rung_caption
from .validation import leaf_powers

__all__ = [
    "AreaEffect",
    "AreaUse",
    "AreaAttempt",
    "power_offers_area",
    "power_area_use",
    "area_line_spec",
    "area_attack_spec",
    "area_dodge_spec",
    "area_resist_spec",
    "area_request_specs",
    "area_dodge_modifier",
    "area_resist_modifier",
    "area_stage_rank",
    "area_effect_rung",
    "area_effect_ladder",
    "area_effect_rung_caption",
    "power_rolls_with_area",
    "find_area_power",
]


def _text(raw: Mapping[str, object], key: str) -> str:
    value = raw.get(key, "")
    return value if isinstance(value, str) else ""


def _int(raw: Mapping[str, object], key: str, default: int = 0) -> int:
    value = raw.get(key, default)
    return value if isinstance(value, int) and not isinstance(value, bool) else default


@dataclass(frozen=True)
class AreaEffect:
    """One resisted effect an area carries, as the GM needs it to resolve it.

    ``rank`` is the **area** rank (capped for a free use), ``resistance`` the key of
    what it is resisted with (``""`` when the target picks between two — an Alternate
    Resistance best-of), and ``config`` the effect instance's choices, which is where
    an Affliction's rungs read their conditions from.
    """

    effect_id: str
    name: str
    rank: int
    resistance: str = ""
    resistance_label: str = ""
    config: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "effect_id": self.effect_id,
            "name": self.name,
            "rank": self.rank,
            "resistance": self.resistance,
            "resistance_label": self.resistance_label,
            "config": dict(self.config),
        }

    @classmethod
    def from_dict(cls, raw: object) -> AreaEffect | None:
        if not isinstance(raw, Mapping) or not _text(raw, "effect_id"):
            return None
        config = raw.get("config", {})
        return cls(
            effect_id=_text(raw, "effect_id"),
            name=_text(raw, "name") or _text(raw, "effect_id"),
            rank=max(0, _int(raw, "rank")),
            resistance=_text(raw, "resistance"),
            resistance_label=_text(raw, "resistance_label"),
            config=dict(config) if isinstance(config, Mapping) else {},
        )


@dataclass(frozen=True)
class AreaUse:
    """What one power offers as an area, worked out on its wielder's sheet.

    ``free`` marks the table's free area use of an attack that bought no extra;
    ``attack`` is the attack check bonus against a primary target, or ``None`` for an
    area that makes none (a Perception-range one names its primary without a roll).
    The Dodge DC and the halving are figured from the **lead** effect's rank, the one
    every creature in the area dodges once.
    """

    power_id: str
    power_name: str
    shape: str
    applications: int = 1
    spread: str = ""
    free: bool = False
    rank: int = 0
    attack: int | None = None
    effects: tuple[AreaEffect, ...] = ()

    def dodge_dc(self, game_data: GameData) -> int:
        return area_dodge_dc(self.rank, game_data)

    def size_text(self, game_data: GameData) -> str:
        text = area_size_text(self.shape, self.applications, self.rank, game_data)
        return f"{text}, {self.spread}" if self.spread else text

    def to_dict(self) -> dict:
        return {
            "power_id": self.power_id,
            "power_name": self.power_name,
            "shape": self.shape,
            "applications": self.applications,
            "spread": self.spread,
            "free": self.free,
            "rank": self.rank,
            "attack": self.attack,
            "effects": [e.to_dict() for e in self.effects],
        }

    @classmethod
    def from_dict(cls, raw: object) -> AreaUse | None:
        if not isinstance(raw, Mapping):
            return None
        effects = tuple(
            e
            for e in (AreaEffect.from_dict(r) for r in raw.get("effects", ()) or ())
            if e is not None
        )
        if not effects:
            return None
        attack = raw.get("attack")
        return cls(
            power_id=_text(raw, "power_id"),
            power_name=_text(raw, "power_name"),
            shape=_text(raw, "shape"),
            applications=max(1, _int(raw, "applications", 1)),
            spread=_text(raw, "spread"),
            free=bool(raw.get("free", False)),
            rank=max(0, _int(raw, "rank")),
            attack=attack if isinstance(attack, int) and not isinstance(attack, bool) else None,
            effects=effects,
        )


def _base(game_data: GameData, effect_id: str):
    return next((e for e in game_data.effects if e.id == effect_id), None)


def _resisted(effect: PowerEffectInstance, game_data: GameData) -> bool:
    base = _base(game_data, effect.effect_id)
    return base is not None and base.resistance_dc_base is not None


def _area_effects(power: Power, game_data: GameData) -> list[PowerEffectInstance]:
    """The effects of *power* that act as an area: bought, or the table's free use."""

    if not game_data.area.available:
        return []
    chosen = [e for e in power.effects if _resisted(e, game_data) and effect_is_area(e, game_data)]
    if chosen:
        return chosen
    if power.area_use:
        return [
            e
            for e in power.effects
            if _resisted(e, game_data) and effect_makes_attack(e, game_data)
        ]
    return []


def find_area_power(char: Character, game_data: GameData, power_id: str) -> Power | None:
    """The build an area line names on *char*'s sheet — a power, or a piece of gear.

    A piece of gear is returned as it is *used*, accessories folded in
    (:func:`~.accessories.item_effective_build`), since that is the build its roll
    lines were drawn from.
    """

    if not power_id:
        return None
    for power in leaf_powers(char.powers):
        if power.id == power_id:
            return power
    for item in char.equipment:
        if item.build is not None and item.build.id == power_id:
            return item_effective_build(item, game_data)
    return None


def power_offers_area(power: Power, game_data: GameData) -> bool:
    """Whether *power* could be switched to the free area use — it is an attack.

    The question a card's context menu asks before offering the switch: a power that
    already *is* an area needs none, and one that attacks nobody cannot be one.
    """

    if not game_data.area.available:
        return False
    if any(effect_is_area(e, game_data) for e in power.effects):
        return False
    return any(_resisted(e, game_data) and effect_makes_attack(e, game_data) for e in power.effects)


def _resistance_of(
    effect: PowerEffectInstance, game_data: GameData, char: Character | None
) -> tuple[str, str]:
    """``(key, label)`` of what *effect* is resisted by — the label as the card reads it."""

    attack_bonus = effect_attack_skill_bonus(effect, char, game_data)
    rows = {r.key: r for r in effect_stat_rows(effect, game_data, char, attack_bonus)}
    row = rows.get("resistance") or rows.get("effect_dc")
    if row is None:
        return "", ""
    key = _resistance_key(row.value, game_data)
    name = next((r.name for r in game_data.resistances if r.key == key), "")
    return key, name or row.value.partition(" vs")[0].strip()


def power_area_use(
    power: Power,
    char: Character | None,
    game_data: GameData,
    *,
    shape: str = "",
) -> AreaUse | None:
    """What *power* offers as an area on *char*'s sheet, or ``None`` if nothing.

    *shape* picks the shape of a **free** use (one with no extra to have chosen it);
    an area bought with the extra keeps its own. A free use's rank is capped at the
    Power Level when the ruleset says so — an area's rank is, and a free one is not a
    way around that.
    """

    effects = _area_effects(power, game_data)
    if not effects:
        return None
    lead = effects[0]
    selection = area_selection(lead, game_data)
    free = selection is None
    cap = char.power_level if free and char is not None and game_data.area.free_use_pl_cap else None

    def capped(rank: int) -> int:
        return min(rank, cap) if cap is not None else rank

    carried = []
    for effect in effects:
        base = _base(game_data, effect.effect_id)
        key, label = _resistance_of(effect, game_data, char)
        carried.append(
            AreaEffect(
                effect_id=effect.effect_id,
                name=effect.label or (base.name if base else effect.effect_id),
                rank=capped(effect_area_rank(effect, game_data, char)),
                resistance=key,
                resistance_label=label,
                config=dict(effect.config),
            )
        )
    if free:
        known = {value for value, _label in area_shape_options(game_data)}
        chosen = shape if shape in known else area_shape(None, game_data)
    else:
        chosen = area_shape(selection, game_data)
    attack = None
    if effect_makes_attack(lead, game_data):
        attack_bonus = effect_attack_skill_bonus(lead, char, game_data)
        attack = effect_roll_numbers(lead, game_data, char, attack_bonus).check_actor
    return AreaUse(
        power_id=power.id,
        power_name=power.name or carried[0].name,
        shape=chosen,
        applications=area_applications(selection),
        spread=area_spread_label(selection, game_data),
        free=free,
        rank=carried[0].rank,
        attack=attack,
        effects=tuple(carried),
    )


# -- the specs the footer and the history offer ------------------------------------


def area_line_spec(use: AreaUse, game_data: GameData) -> RollSpec:
    """The power card's footer line that opens the area window.

    Not a die: :data:`~.rolls.KIND_AREA`, with the power's id in the trait key, so the
    sheet's roller knows to open the window rather than throw anything.
    """

    free = " (free use)" if use.free else ""
    return RollSpec(
        label=f"Area{free}: {use.size_text(game_data)}; Dodge DC {use.dodge_dc(game_data)}",
        kind=KIND_AREA,
        trait_key=use.power_id,
        hint=(
            "Use this power on an area: pick a primary target, if any, and who else is "
            "caught, then send it to the GM."
        ),
    )


def power_rolls_with_area(
    power: Power, char: Character | None, game_data: GameData
) -> list[RollSpec]:
    """:func:`~.rolls.power_rolls`, with the area line last when the power offers one.

    Last, so a pin — which names a roll by its index in this list — keeps pointing at
    the roll it was made on when an area switch is flipped.
    """

    specs = power_rolls(power, char, game_data)
    use = power_area_use(power, char, game_data)
    if use is not None:
        specs.append(area_line_spec(use, game_data))
    return specs


def area_attack_spec(
    use: AreaUse, *, action_id: str, source: str = "", target: str = ""
) -> RollSpec:
    """The attack check against the area's primary target.

    It provokes nothing on the card: the primary's save is the GM's to resolve along
    with everyone else in the area, so the history offers no chip for it. It carries
    the attempt's id so the GM's copy of the card can offer to resolve it.
    """

    against = f" at {target}" if target else ""
    return RollSpec(
        label=f"{use.power_name}: area attack{against} vs. Defense",
        modifier=use.attack or 0,
        kind=KIND_POWER_CHECK,
        hint=(
            "The primary target's Defense goes in the DC box. A hit means no Dodge "
            "check for them; the GM resolves everyone in the area."
        ),
        action_id=action_id,
        targets=(target,) if target else (),
        title=f"{source}: {use.power_name} (area)" if source else f"{use.power_name} (area)",
    )


def area_resist_spec(
    use: AreaUse,
    effect: AreaEffect,
    game_data: GameData,
    *,
    full: bool = False,
    source: str = "",
    targets: tuple[str, ...] = (),
) -> RollSpec | None:
    """The save one of the area's effects calls for — at full rank, or after a Dodge.

    With *full* it is a plain save at the full rank (a primary target that was hit).
    Otherwise it is a follow-up of the Dodge check: full rank on a failed Dodge,
    :attr:`~.rolls.RollSpec.dc_if_resisted` on a made one.
    """

    base = _base(game_data, effect.effect_id)
    if base is None or base.resistance_dc_base is None:
        return None
    by = f" {source}'s" if source else ""
    label = effect.resistance_label or "Resist"
    dc = base.resistance_dc_base + effect.rank
    outcomes = area_effect_ladder(effect, game_data)
    success = (
        rung_caption(base.resistance_success, game_data, effect.config)
        if base.resistance_success is not None
        else ""
    )
    spec = RollSpec(
        label=f"{label} vs.{by} {effect.name}",
        dc=dc,
        kind=KIND_POWER_SAVE,
        hint="Apply what it does to you on your own sheet.",
        outcomes=outcomes,
        success_outcome=success,
        rolled_by_target=True,
        trait_key=effect.resistance,
        targets=targets,
    )
    if full:
        return spec
    halved = base.resistance_dc_base + area_resisted_rank(effect.rank, game_data)
    note = ""
    evasion = game_data.area.evasion
    if evasion is not None and evasion.negate_at_rank:
        record = next((a for a in game_data.advantages if a.id == evasion.advantage), None)
        if record is not None:
            note = f"no effect at all with {record.name} {evasion.negate_at_rank}"
    return RollSpec(
        label=spec.label,
        dc=spec.dc,
        kind=spec.kind,
        hint=spec.hint,
        outcomes=spec.outcomes,
        success_outcome=spec.success_outcome,
        rolled_by_target=True,
        trait_key=spec.trait_key,
        targets=targets,
        dc_if_resisted=halved,
        note_if_resisted=note,
    )


def area_dodge_spec(
    use: AreaUse,
    game_data: GameData,
    *,
    source: str = "",
    targets: tuple[str, ...] = (),
) -> RollSpec:
    """The Dodge check against the area, with the lead effect's save as its follow-up.

    Offered whatever the degree (:attr:`~.rolls.RollSpec.follow_up_always`): a failed
    Dodge is when the save is due at full rank, and a made one when it is due halved.
    """

    by = f"{source}'s " if source else ""
    follow_up = area_resist_spec(use, use.effects[0], game_data, source=source, targets=targets)
    return RollSpec(
        label=f"Dodge {by}{use.power_name} (area)",
        dc=use.dodge_dc(game_data),
        kind=KIND_AREA_DODGE,
        hint="Making it halves the rank you resist the effect at.",
        rolled_by_target=True,
        trait_key=game_data.area.dodge,
        follow_up=follow_up,
        follow_up_always=follow_up is not None,
        targets=targets,
        title=f"{source}: {use.power_name} (area)" if source else f"{use.power_name} (area)",
    )


def area_request_specs(
    use: AreaUse,
    game_data: GameData,
    *,
    source: str = "",
    caught: tuple[str, ...] = (),
    primary: str = "",
) -> list[RollSpec]:
    """What the history asks the caught **players** to roll, one request each.

    The Dodge check (chained to the lead effect's save) for everyone caught in the
    area, and a plain full-rank save for a player who was the primary target and was
    hit. Any further effect of a linked area gets a request of its own, whose label
    gives both DCs — one card can only chain one save, and both are due on one Dodge.
    """

    specs: list[RollSpec] = []
    if caught:
        specs.append(area_dodge_spec(use, game_data, source=source, targets=caught))
        for effect in use.effects[1:]:
            spec = area_resist_spec(use, effect, game_data, full=True, source=source)
            if spec is None:
                continue
            halved = spec.dc - effect.rank + area_resisted_rank(effect.rank, game_data)
            specs.append(
                RollSpec(
                    label=f"{spec.label} (DC {halved} if you made the Dodge)",
                    dc=spec.dc,
                    kind=spec.kind,
                    hint=spec.hint,
                    outcomes=spec.outcomes,
                    success_outcome=spec.success_outcome,
                    trait_key=spec.trait_key,
                    targets=caught,
                )
            )
    if primary:
        for effect in use.effects:
            spec = area_resist_spec(
                use, effect, game_data, full=True, source=source, targets=(primary,)
            )
            if spec is not None:
                specs.append(spec)
    return specs


# -- the numbers the GM's table rolls -------------------------------------------------


def area_dodge_modifier(char: Character | None, game_data: GameData) -> int:
    """*char*'s Dodge against an area — the resistance total plus Evasion, conditions in."""

    if char is None or not game_data.area.dodge:
        return 0
    return resistance_roll(char, game_data, game_data.area.dodge).modifier + area_dodge_bonus(
        char, game_data
    )


def area_resist_modifier(char: Character | None, game_data: GameData, key: str) -> int:
    """*char*'s number for the resistance *key* names — 0 when either is unknown."""

    if char is None or not any(r.key == key for r in game_data.resistances):
        return 0
    return resistance_roll(char, game_data, key).modifier


def area_stage_rank(
    rank: int, dodged: bool, char: Character | None, game_data: GameData
) -> int | None:
    """The rank one creature resists after its Dodge check — or ``None`` for no effect.

    Full on a failed Dodge, halved on a made one, and nothing at all on a made one for
    a creature whose Evasion is high enough to shrug the area off.
    """

    if not dodged:
        return rank
    if evasion_negates(char, game_data):
        return None
    return area_resisted_rank(rank, game_data)


def area_effect_rung(
    effect: AreaEffect, degree: int | None, game_data: GameData
) -> ResistanceOutcome | None:
    """The rung of *effect*'s ladder a save of *degree* lands on (a made one: its Hit)."""

    base = _base(game_data, effect.effect_id)
    if base is None:
        return None
    return outcome_rung(base.resistance_outcomes, degree, base.resistance_success)


def area_effect_ladder(effect: AreaEffect, game_data: GameData) -> tuple[str, ...]:
    """*effect*'s ladder in words, one entry per degree of failure."""

    base = _base(game_data, effect.effect_id)
    if base is None:
        return ()
    return ladder_captions(base.resistance_outcomes, game_data, effect.config)


def area_effect_rung_caption(effect: AreaEffect, degree: int | None, game_data: GameData) -> str:
    """What a save of *degree* did, in words — ``""`` for nothing."""

    rung = area_effect_rung(effect, degree, game_data)
    return "" if rung is None else rung_caption(rung, game_data, effect.config)


# -- what travels between seats -------------------------------------------------


@dataclass(frozen=True)
class AreaAttempt:
    """A declared area attack, as a player's app sends it to the GM.

    ``primary`` and ``targets`` are scene refs, like an impression's. The attack check
    against the primary (if any) is rolled into the history as an ordinary roll
    carrying :attr:`action_id`; the GM's controller pairs the two up, which is how the
    attack's total reaches the window. ``use`` is the area as the attacker's own sheet
    worked it out — the one copy guaranteed current.
    """

    action_id: str
    use: AreaUse
    primary: str = ""
    targets: tuple[str, ...] = ()
    source_name: str = ""
    source_ref: str = ""

    def to_dict(self) -> dict:
        return {
            "action_id": self.action_id,
            "use": self.use.to_dict(),
            "primary": self.primary,
            "targets": list(self.targets),
            "source_name": self.source_name,
            "source_ref": self.source_ref,
        }

    @classmethod
    def from_dict(cls, raw: object) -> AreaAttempt | None:
        """Parse a payload from another seat, or ``None`` when it is unusable."""

        if not isinstance(raw, Mapping):
            return None
        action_id = _text(raw, "action_id")
        use = AreaUse.from_dict(raw.get("use"))
        if not action_id or use is None:
            return None
        targets = raw.get("targets", [])
        return cls(
            action_id=action_id,
            use=use,
            primary=_text(raw, "primary"),
            targets=(
                tuple(t for t in targets if isinstance(t, str)) if isinstance(targets, list) else ()
            ),
            source_name=_text(raw, "source_name"),
            source_ref=_text(raw, "source_ref"),
        )

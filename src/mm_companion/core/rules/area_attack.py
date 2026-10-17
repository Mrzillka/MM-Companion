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
from dataclasses import dataclass, field, replace

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
from .defenses import damage_negated, penetrating_ranks, toughness_defense
from .powers_cost import effect_resistance_dc_base, effect_size_rank_shift
from .powers_terms import (
    effect_area_rank,
    effect_attack_skill_bonus,
    effect_full_area_rank,
    effect_makes_attack,
    effect_roll_numbers,
    effect_stat_rows,
)
from .rolls import (
    KIND_ABILITY,
    KIND_AREA,
    KIND_AREA_DODGE,
    KIND_BEST_OF,
    KIND_POWER_CHECK,
    KIND_POWER_SAVE,
    KIND_RESISTANCE,
    KIND_SKILL,
    KIND_WORST_OF,
    LADDER_AREA,
    RollSpec,
    SpecLadder,
    _resistance_key,
    best_of_key,
    localize_spec,
    power_rolls,
    resistance_roll,
)
from .targeting import (
    concrete_rung,
    ladder_captions,
    outcome_rung,
    rung_caption,
    success_removals,
)
from .validation import estimated_power_level, leaf_powers

__all__ = [
    "AreaEffect",
    "AreaUse",
    "AreaAttempt",
    "AREA_MODE_RESOLVE",
    "AREA_MODE_REQUEST",
    "power_offers_area",
    "power_area_use",
    "power_area_uses",
    "AREA_TITLE_SUFFIX",
    "area_title",
    "area_key",
    "area_key_index",
    "area_key_power_id",
    "area_use_adjusted",
    "area_use_heals",
    "area_primary_rank",
    "area_heal_spec",
    "area_heal_removals",
    "area_capable_builds",
    "area_line_spec",
    "area_attack_spec",
    "area_dodge_spec",
    "area_resist_spec",
    "area_request_specs",
    "area_ask_dodge_spec",
    "area_ask_save_spec",
    "area_dodge_modifier",
    "area_resist_modifier",
    "area_save_dc",
    "area_stage_rank",
    "area_npc_dodge_spec",
    "area_npc_save_specs",
    "area_effect_rung",
    "area_effect_ladder",
    "area_effect_rung_caption",
    "power_rolls_with_area",
    "find_area_power",
]

#: How an area is run: the GM's table rolls it and applies what it did, or the history
#: is left a request card for each roll, which everyone caught answers themselves.
AREA_MODE_RESOLVE = "resolve"
AREA_MODE_REQUEST = "request"

#: What ends the title of every table and card an area attack leaves.
AREA_TITLE_SUFFIX = " (area)"

#: What separates a build's id from the index of one of its area effects in an area
#: line's key — ``"<build id>#1"`` is the build's second area effect.
_KEY_SEPARATOR = "#"


def area_title(source: str, power_name: str) -> str:
    """What an area attack's table is headed by — "Thug: Fire Blast (area)"."""

    head = f"{source}: {power_name}" if source else power_name
    return f"{head}{AREA_TITLE_SUFFIX}"


def area_key(power_id: str, effect_index: int = 0) -> str:
    """The key an area line carries for one of a build's area effects.

    A build whose effects are several areas (a Linked Blast and Affliction, both Area)
    offers one line per effect, each run as an area of its own. The first is the bare
    build id, which is what every line was before there could be more than one.
    """

    return power_id if effect_index <= 0 else f"{power_id}{_KEY_SEPARATOR}{effect_index}"


def area_key_index(key: str) -> int:
    """Which of a build's area effects an area key names (0 for a bare build id)."""

    _power, separator, index = key.rpartition(_KEY_SEPARATOR)
    if not separator or not index.isdigit():
        return 0
    return int(index)


def area_key_power_id(key: str) -> str:
    """The build id an area key names, without the index of its area effect."""

    power, separator, index = key.rpartition(_KEY_SEPARATOR)
    return power if separator and index.isdigit() else key


def _text(raw: Mapping[str, object], key: str) -> str:
    value = raw.get(key, "")
    return value if isinstance(value, str) else ""


def _int(raw: Mapping[str, object], key: str, default: int = 0) -> int:
    value = raw.get(key, default)
    return value if isinstance(value, int) and not isinstance(value, bool) else default


def _scalar_config(config: object) -> dict:
    """The plain values of an effect's config — what its ladder reads, and all that travels.

    An area's attempt crosses the wire as a session payload, whose sanitizer keeps only
    four levels of nesting (:data:`~..session.protocol.MAX_MOD_DEPTH`). An effect's
    config sits at the third, so a value of its own may not be a list or a dict. No
    resistance ladder reads one (an Affliction's rungs name one condition each), so the
    rest — an Illusion's senses — stays home rather than going missing on the way.
    """

    if not isinstance(config, Mapping):
        return {}
    return {
        k: v
        for k, v in config.items()
        if isinstance(k, str) and (v is None or isinstance(v, (str, int, float, bool)))
    }


@dataclass(frozen=True)
class AreaEffect:
    """One resisted effect an area carries, as the GM needs it to resolve it.

    ``rank`` is the **area** rank (capped for a free use).

    ``resistance`` is the trait key it is resisted with, read the way
    ``resistance_kind`` says. It is a resistance key for an ordinary save, or a
    :func:`~.rolls.best_of_key` when the target resists with the better
    (:data:`~.rolls.KIND_BEST_OF`) or the worse (:data:`~.rolls.KIND_WORST_OF`) of two
    — a skill or an ability in a resistance's place is a choice of one.

    ``dc_base`` is what the rank is added to for the save's DC, as the wielder's sheet
    worked it out (``None``: the base effect's own). ``config`` is the effect
    instance's choices, which is where an Affliction's rungs read their conditions from.
    """

    effect_id: str
    name: str
    rank: int
    resistance: str = ""
    resistance_label: str = ""
    resistance_kind: str = KIND_POWER_SAVE
    dc_base: int | None = None
    config: dict = field(default_factory=dict)
    #: The rank the creature the attack hits directly resists, when it is not the area's
    #: (a heavy weapon's Area on part of its Damage). ``None``: the same rank.
    primary_rank: int | None = None
    #: How many of its ranks are Penetrating — what a target's Impervious Toughness is
    #: measured against (:mod:`.defenses`).
    penetrating: int = 0

    def to_dict(self) -> dict:
        raw = {
            "effect_id": self.effect_id,
            "name": self.name,
            "rank": self.rank,
            "resistance": self.resistance,
            "resistance_label": self.resistance_label,
            "resistance_kind": self.resistance_kind,
            "config": _scalar_config(self.config),
        }
        if self.dc_base is not None:
            raw["dc_base"] = self.dc_base
        if self.primary_rank is not None:
            raw["primary_rank"] = self.primary_rank
        if self.penetrating:
            raw["penetrating"] = self.penetrating
        return raw

    @classmethod
    def from_dict(cls, raw: object) -> AreaEffect | None:
        if not isinstance(raw, Mapping) or not _text(raw, "effect_id"):
            return None
        dc_base = raw.get("dc_base")
        primary_rank = raw.get("primary_rank")
        return cls(
            effect_id=_text(raw, "effect_id"),
            name=_text(raw, "name") or _text(raw, "effect_id"),
            rank=max(0, _int(raw, "rank")),
            resistance=_text(raw, "resistance"),
            resistance_label=_text(raw, "resistance_label"),
            resistance_kind=_text(raw, "resistance_kind") or KIND_POWER_SAVE,
            dc_base=(
                dc_base if isinstance(dc_base, int) and not isinstance(dc_base, bool) else None
            ),
            config=_scalar_config(raw.get("config", {})),
            primary_rank=(
                max(0, primary_rank)
                if isinstance(primary_rank, int) and not isinstance(primary_rank, bool)
                else None
            ),
            penetrating=max(0, _int(raw, "penetrating")),
        )


def area_primary_rank(effect: AreaEffect) -> int:
    """The rank the primary target resists *effect* at when the attack hits it."""

    return effect.rank if effect.primary_rank is None else effect.primary_rank


@dataclass(frozen=True)
class AreaUse:
    """One area a power makes, worked out on its wielder's sheet.

    ``free`` marks the table's free area use of an attack that bought no extra;
    ``attack`` is the attack check bonus against a primary target, or ``None`` for an
    area that makes none (a Perception-range one names its primary without a roll).
    ``effects`` is the one area effect it is (a build of several areas makes one use
    per effect, see :func:`power_area_uses`); the Dodge DC and the halving are figured
    from its rank.
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
    #: Which of the build's area effects this is (see :func:`area_key`).
    effect_index: int = 0

    @property
    def key(self) -> str:
        """The area line's key: the build, and which of its area effects."""
        return area_key(self.power_id, self.effect_index)

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
            "effect_index": self.effect_index,
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
            effect_index=max(0, _int(raw, "effect_index")),
        )


def _base(game_data: GameData, effect_id: str):
    return next((e for e in game_data.effects if e.id == effect_id), None)


def _resisted(effect: PowerEffectInstance, game_data: GameData) -> bool:
    return effect_resistance_dc_base(effect, game_data) is not None


def _heals(effect_id: str, game_data: GameData) -> bool:
    """Whether an effect is *checked* to take conditions off its subjects (Healing)."""
    base = _base(game_data, effect_id)
    return (
        base is not None
        and base.resistance_dc_base is None
        and base.check_dc is not None
        and bool(base.success_removes)
    )


def _area_effects(
    power: Power, game_data: GameData, *, free_use: bool = False
) -> list[PowerEffectInstance]:
    """The effects of *power* that act as an area: bought, or the table's free use.

    *free_use* asks as though the power's free-use switch were on, which is how the GM
    reaches an NPC's attack from its card without flipping anything on its sheet.
    """

    if not game_data.area.available:
        return []
    chosen = [
        e
        for e in power.effects
        if effect_is_area(e, game_data)
        and (_resisted(e, game_data) or _heals(e.effect_id, game_data))
    ]
    if chosen:
        return chosen
    if power.area_use or free_use:
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

    power_id = area_key_power_id(power_id)
    if not power_id:
        return None
    for power in leaf_powers(char.powers):
        if power.id == power_id:
            return power
    for item in char.equipment:
        if item.build is not None and item.build.id == power_id:
            return item_effective_build(item, game_data)
    return None


def area_capable_builds(char: Character, game_data: GameData) -> list[Power]:
    """Every build on *char*'s sheet that could be used on an area, in sheet order.

    An area bought with the extra, or any attack the table's free use could spread —
    whether or not its switch is on. Powers first, then gear (as it is used, accessories
    folded in). What an NPC card's Area button offers the GM.
    """

    builds: list[Power] = [
        power
        for power in leaf_powers(char.powers)
        if _area_effects(power, game_data, free_use=True)
    ]
    for item in char.equipment:
        if item.build is None:
            continue
        build = item_effective_build(item, game_data)
        if build is not None and _area_effects(build, game_data, free_use=True):
            builds.append(build)
    return builds


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


def _trait_named(name: str, game_data: GameData) -> tuple[str, str] | None:
    """``(kind, trait key)`` of the trait a card's resistance phrase names, or ``None``.

    A resistance first, then an ability, then a skill — Alternate Resistance may swap in
    any of them.
    """

    wanted = name.strip().lower()
    for resistance in game_data.resistances:
        if resistance.name.lower() == wanted:
            return KIND_RESISTANCE, resistance.key
    for ability in game_data.abilities:
        if ability.name.lower() == wanted:
            return KIND_ABILITY, ability.key
    for skill in game_data.skills:
        if skill.name.lower() == wanted:
            return KIND_SKILL, skill.name
    return None


def _takes_the_worse(effect: PowerEffectInstance, game_data: GameData) -> bool:
    """Whether a modifier choice on *effect* makes a choice of two the worse one."""

    for selection in (*effect.extras, *effect.flaws):
        for modifier_id, key, value in game_data.area.worst_of:
            if selection.modifier_id == modifier_id and selection.config.get(key) == value:
                return True
    return False


def _resistance_of(
    effect: PowerEffectInstance, game_data: GameData, char: Character | None
) -> tuple[str, str, str]:
    """``(kind, key, label)`` of what *effect* is resisted by — the label as the card reads it.

    One resistance is an ordinary save (:data:`~.rolls.KIND_POWER_SAVE`). A phrase
    naming two (``"Fortitude or Will"``), or a skill or an ability in a resistance's
    place, is a choice among candidates. It is rolled as the better of them, or as the
    worse when a modifier says so (``area.worstOf``). The key is ``""`` when the card
    names nothing this ruleset knows, and the number is then typed by hand.
    """

    attack_bonus = effect_attack_skill_bonus(effect, char, game_data)
    rows = {r.key: r for r in effect_stat_rows(effect, game_data, char, attack_bonus)}
    row = rows.get("resistance") or rows.get("effect_dc")
    if row is None:
        return KIND_POWER_SAVE, "", ""
    phrase = row.value.partition(" vs")[0].strip()
    key = _resistance_key(row.value, game_data)
    if key:
        name = next((r.name for r in game_data.resistances if r.key == key), "")
        return KIND_POWER_SAVE, key, name or phrase
    candidates = [
        found
        for found in (_trait_named(part, game_data) for part in phrase.split(" or "))
        if found is not None
    ]
    if not candidates:
        return KIND_POWER_SAVE, "", phrase
    kind = KIND_WORST_OF if _takes_the_worse(effect, game_data) else KIND_BEST_OF
    return kind, best_of_key(candidates), phrase


def power_area_uses(
    power: Power,
    char: Character | None,
    game_data: GameData,
    *,
    shape: str = "",
    npc: bool = False,
    free_use: bool = False,
) -> list[AreaUse]:
    """What *power* offers as areas on *char*'s sheet: one use per area effect.

    A build whose effects are several areas (Linked) is used as one area per effect,
    each with its own Dodge and its own save, rather than one Dodge against the first
    effect's rank. Each use's :attr:`AreaUse.effect_index` says which it is.

    *shape* picks the shape of a **free** use (one with no extra to have chosen it);
    an area bought with the extra keeps its own.

    A free use's rank is capped at the Power Level when the ruleset says so — an
    area's rank is, and a free one is not a way around that. The cap rises by whatever
    the wielder's **size** adds to the effect
    (:func:`~.powers_cost.effect_size_rank_shift`), as the validator's rank cap does:
    being large is the book's own reason for the free use, so it must not be what the
    use is cut back for.

    *npc* reads the Power Level the way a GM's NPC has one: estimated from what it
    can do (:func:`~.validation.estimated_power_level`), not the number on file, which
    nothing treats as a rule for an NPC.

    *free_use* reads an attack as though its free-use switch were on (see
    :func:`_area_effects`).
    """

    effects = _area_effects(power, game_data, free_use=free_use)
    return [
        _effect_use(power, effect, index, char, game_data, shape=shape, npc=npc)
        for index, effect in enumerate(effects)
    ]


def power_area_use(
    power: Power,
    char: Character | None,
    game_data: GameData,
    *,
    shape: str = "",
    npc: bool = False,
    free_use: bool = False,
    effect_index: int = 0,
) -> AreaUse | None:
    """One of :func:`power_area_uses` — the *effect_index*-th — or ``None`` if none."""

    uses = power_area_uses(power, char, game_data, shape=shape, npc=npc, free_use=free_use)
    return next((u for u in uses if u.effect_index == effect_index), None)


def _effect_use(
    power: Power,
    effect: PowerEffectInstance,
    index: int,
    char: Character | None,
    game_data: GameData,
    *,
    shape: str,
    npc: bool,
) -> AreaUse:
    """One area effect of *power* as an area of its own (see :func:`power_area_uses`)."""

    selection = area_selection(effect, game_data)
    free = selection is None
    level: int | None = None
    if free and char is not None and game_data.area.free_use_pl_cap:
        level = estimated_power_level(char, game_data) if npc else char.power_level

    base = _base(game_data, effect.effect_id)
    kind, key, label = _resistance_of(effect, game_data, char)
    rank = effect_area_rank(effect, game_data, char)
    full = effect_full_area_rank(effect, game_data, char)
    attack_bonus = effect_attack_skill_bonus(effect, char, game_data)
    dc = effect_roll_numbers(effect, game_data, char, attack_bonus).dc
    # What the rank is added to: the base effect's own, or what a DC written over
    # the card's makes of it. The card's DC is the whole effect's, so the base comes
    # off the full rank — an area on part of the effect is a smaller rank on that base.
    dc_base = dc - full if dc is not None else None
    if level is not None:
        cap = level + effect_size_rank_shift(effect, game_data, char)
        rank, full = min(rank, cap), min(full, cap)
    carried = AreaEffect(
        effect_id=effect.effect_id,
        name=effect.label or (base.name if base else effect.effect_id),
        rank=rank,
        resistance=key,
        resistance_label=label,
        resistance_kind=kind,
        dc_base=dc_base,
        config=dict(effect.config),
        primary_rank=full if full != rank else None,
        penetrating=min(penetrating_ranks(effect, game_data), rank),
    )
    if free:
        known = {value for value, _label in area_shape_options(game_data)}
        chosen = shape if shape in known else area_shape(None, game_data)
    else:
        chosen = area_shape(selection, game_data)
    attack = None
    if effect_makes_attack(effect, game_data):
        attack = effect_roll_numbers(effect, game_data, char, attack_bonus).check_actor
    return AreaUse(
        power_id=power.id,
        power_name=power.name or carried.name,
        shape=chosen,
        applications=area_applications(selection),
        spread=area_spread_label(selection, game_data),
        free=free,
        rank=carried.rank,
        attack=attack,
        effects=(carried,),
        effect_index=index,
    )


def area_use_adjusted(use: AreaUse, *, rank: int = 0, attack: int = 0) -> AreaUse:
    """*use* with the GM's adjustments: *rank* on every effect's rank, *attack* on the check.

    The area's rank moves its Dodge DC and every save DC with it; the attack check's
    adjustment counts only against the primary target's Defense.
    """

    if not rank and not attack:
        return use
    effects = tuple(
        replace(
            effect,
            rank=max(0, effect.rank + rank),
            primary_rank=(
                None if effect.primary_rank is None else max(0, effect.primary_rank + rank)
            ),
        )
        for effect in use.effects
    )
    return replace(
        use,
        effects=effects,
        rank=effects[0].rank if effects else use.rank,
        attack=None if use.attack is None else use.attack + attack,
    )


def area_use_heals(use: AreaUse, game_data: GameData) -> bool:
    """Whether *use* is checked to help everyone in it (an area Healing), not resisted.

    No Dodge, no primary target and no save: the wielder makes one check, and every
    creature in the area loses a condition per degree of success.
    """

    return bool(use.effects) and _heals(use.effects[0].effect_id, game_data)


def area_heal_spec(
    use: AreaUse, game_data: GameData, *, source: str = "", action_id: str = ""
) -> RollSpec | None:
    """The one check an area Healing makes: the effect's rank against its DC.

    ``None`` for an area that is not one. Each degree of success takes a condition off
    every creature in the area (:func:`area_heal_removals`).
    """

    if not area_use_heals(use, game_data):
        return None
    effect = use.effects[0]
    base = _base(game_data, effect.effect_id)
    dc = base.check_dc if base is not None else None
    return RollSpec(
        label=f"{use.power_name}: {effect.name} check (area)",
        modifier=effect.rank,
        dc=dc,
        kind=KIND_POWER_CHECK,
        hint="Each degree of success takes a condition off everyone in the area.",
        targets=(source,) if source else (),
        action_id=action_id,
        title=area_title(source, use.power_name),
    )


def area_heal_removals(
    use: AreaUse, char: Character | None, degrees: int, game_data: GameData
) -> tuple[tuple[str, str | None], ...]:
    """What *degrees* of an area Healing's success take off *char* — least severe first."""

    if char is None or not use.effects:
        return ()
    base = _base(game_data, use.effects[0].effect_id)
    if base is None:
        return ()
    return success_removals(char, base.success_removes, degrees)


# -- the specs the footer and the history offer ------------------------------------


def area_line_spec(use: AreaUse, game_data: GameData, *, named: bool = False) -> RollSpec:
    """The power card's footer line that opens the area window.

    Not a die: :data:`~.rolls.KIND_AREA`, with the use's key (:func:`area_key`) in the
    trait key, so the sheet's roller knows to open the window rather than throw
    anything. *named* puts the effect's name in the label, for a build that offers more
    than one area.
    """

    free = " (free use)" if use.free else ""
    which = f" ({use.effects[0].name})" if named and use.effects else ""
    if area_use_heals(use, game_data):
        return RollSpec(
            label=f"Area{which}{free}: {use.size_text(game_data)}",
            kind=KIND_AREA,
            trait_key=use.key,
            hint="Use this power on an area: pick who is in it, then send it to the GM.",
        )
    return RollSpec(
        label=(
            f"Area{which}{free}: {use.size_text(game_data)}; " f"Dodge DC {use.dodge_dc(game_data)}"
        ),
        kind=KIND_AREA,
        trait_key=use.key,
        hint=(
            "Use this power on an area: pick a primary target, if any, and who else is "
            "caught, then send it to the GM."
        ),
    )


def power_rolls_with_area(
    power: Power, char: Character | None, game_data: GameData, *, npc: bool = False
) -> list[RollSpec]:
    """:func:`~.rolls.power_rolls`, with the area line last when the power offers one.

    Last, so a pin — which names a roll by its index in this list — keeps pointing at
    the roll it was made on when an area switch is flipped. A build of several areas
    gets a line for each. *npc* is as for :func:`power_area_use`.
    """

    specs = power_rolls(power, char, game_data)
    uses = power_area_uses(power, char, game_data, npc=npc)
    for use in uses:
        specs.append(area_line_spec(use, game_data, named=len(uses) > 1))
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
        title=area_title(source, use.power_name),
    )


def area_save_dc(effect: AreaEffect, rank: int, game_data: GameData) -> int | None:
    """The DC of *effect*'s save at *rank* — full, or what a made Dodge left.

    ``None`` for an effect nobody resists. The one place an area's save DC is worked
    out, so the request a player answers and the line the GM's table rolls agree.
    """

    base = effect.dc_base
    if base is None:
        record = _base(game_data, effect.effect_id)
        base = record.resistance_dc_base if record is not None else None
    return None if base is None else base + rank


def _adjusted(label: str, *, cover: int = 0, adjust: int = 0) -> str:
    """*label*, naming what the GM set for these creatures alone."""

    said = []
    if cover:
        said.append(f"{cover:+d} cover")
    if adjust:
        said.append(f"{adjust:+d} from the GM")
    return f"{label} ({', '.join(said)})" if said else label


def area_resist_spec(
    use: AreaUse,
    effect: AreaEffect,
    game_data: GameData,
    *,
    full: bool = False,
    source: str = "",
    targets: tuple[str, ...] = (),
    adjust: int = 0,
    rank: int | None = None,
) -> RollSpec | None:
    """The save one of the area's effects calls for — at full rank, or after a Dodge.

    With *full* it is a plain save at the full rank (a primary target that was hit),
    or at *rank* when that is given. Otherwise it is a follow-up of the Dodge check:
    full rank on a failed Dodge, :attr:`~.rolls.RollSpec.dc_if_resisted` on a made one.
    *adjust* is the GM's bonus or penalty on these creatures' checks; it rides as the
    modifier, which the clicker's own resistance is added to
    (:func:`~.rolls.localize_spec`).
    """

    dc = area_save_dc(effect, effect.rank if rank is None else rank, game_data)
    base = _base(game_data, effect.effect_id)
    if dc is None or base is None:
        return None
    by = f" {source}'s" if source else ""
    label = effect.resistance_label or "Resist"
    success = (
        rung_caption(base.resistance_success, game_data, effect.config)
        if base.resistance_success is not None
        else ""
    )
    spec = RollSpec(
        label=_adjusted(f"{label} vs.{by} {effect.name}", adjust=adjust),
        modifier=adjust,
        dc=dc,
        kind=effect.resistance_kind,
        hint="Apply what it does to you on your own sheet.",
        outcomes=area_effect_ladder(effect, game_data),
        success_outcome=success,
        rolled_by_target=True,
        trait_key=effect.resistance,
        targets=targets,
        ladder=SpecLadder(
            LADDER_AREA,
            tuple(concrete_rung(r, game_data, effect.config) for r in base.resistance_outcomes),
            (
                None
                if base.resistance_success is None
                else concrete_rung(base.resistance_success, game_data, effect.config)
            ),
        ),
    )
    if full:
        return spec
    note = ""
    evasion = game_data.area.evasion
    if evasion is not None and evasion.negate_at_rank:
        record = next((a for a in game_data.advantages if a.id == evasion.advantage), None)
        if record is not None:
            note = f"no effect at all with {record.name} {evasion.negate_at_rank}"
    return replace(
        spec,
        dc_if_resisted=area_save_dc(effect, area_resisted_rank(effect.rank, game_data), game_data),
        note_if_resisted=note,
    )


def area_dodge_spec(
    use: AreaUse,
    game_data: GameData,
    *,
    source: str = "",
    targets: tuple[str, ...] = (),
    cover: int = 0,
    adjust: int = 0,
) -> RollSpec:
    """The Dodge check against the area, with the effect's save as its follow-up.

    Offered whatever the degree (:attr:`~.rolls.RollSpec.follow_up_always`): a failed
    Dodge is when the save is due at full rank, and a made one when it is due halved.
    *cover* counts on the Dodge alone; *adjust* on the Dodge and on the save.
    """

    by = f"{source}'s " if source else ""
    follow_up = area_resist_spec(
        use, use.effects[0], game_data, source=source, targets=targets, adjust=adjust
    )
    return RollSpec(
        label=_adjusted(f"Dodge {by}{use.power_name} (area)", cover=cover, adjust=adjust),
        modifier=cover + adjust,
        dc=use.dodge_dc(game_data),
        kind=KIND_AREA_DODGE,
        hint="Making it halves the rank you resist the effect at.",
        rolled_by_target=True,
        trait_key=game_data.area.dodge,
        follow_up=follow_up,
        follow_up_always=follow_up is not None,
        targets=targets,
        title=area_title(source, use.power_name),
    )


def area_request_specs(
    use: AreaUse,
    game_data: GameData,
    *,
    source: str = "",
    caught: tuple[str, ...] = (),
    primary: str = "",
    action_id: str = "",
    cover: int = 0,
    adjust: int = 0,
) -> list[RollSpec]:
    """What the history asks everyone the area caught to roll, when nobody resolves it.

    Two request cards per effect, answered by whoever they name on their own sheet:

    * the **Dodge** check, for everyone *caught* (the primary target the attack hit
      makes none);
    * the **save**, one card offering it at full rank (a failed Dodge, or a direct hit)
      and at half (a made Dodge) — the history draws the two as two buttons
      (:attr:`~.rolls.RollSpec.dc_if_resisted`). A *primary* target the attack hit is
      named on it too, its button the full one — unless nobody else is caught, or it
      resists a different rank (a heavy weapon's): then it gets a full-rank card alone.

    *cover* and *adjust* are what the GM set for these creatures (see
    :func:`area_dodge_spec`). An area Healing asks nobody for anything.
    """

    if area_use_heals(use, game_data):
        return []
    title = area_title(source, use.power_name)
    specs: list[RollSpec] = []
    if caught:
        dodge = area_dodge_spec(
            use, game_data, source=source, targets=caught, cover=cover, adjust=adjust
        )
        specs.append(
            replace(
                dodge,
                follow_up=None,
                follow_up_always=False,
                hint="Making it halves the rank you resist the area at: save at half.",
            )
        )
    for effect in use.effects:
        # The primary shares the caught's card (its button the full one) unless nobody
        # else is caught, or it resists a different rank: then it has a full card of its own.
        joins = bool(primary and caught and effect.primary_rank is None)
        shared = caught + ((primary,) if joins else ())
        if shared:
            spec = area_resist_spec(
                use, effect, game_data, source=source, targets=shared, adjust=adjust
            )
            if spec is not None:
                specs.append(
                    replace(
                        spec,
                        hint=(
                            "Full rank if you failed the Dodge or were hit directly; half "
                            "if you made the Dodge. Apply what it does on your own sheet."
                        ),
                    )
                )
        if primary and not joins:
            spec = area_resist_spec(
                use,
                effect,
                game_data,
                full=True,
                source=source,
                targets=(primary,),
                adjust=adjust,
                rank=area_primary_rank(effect),
            )
            if spec is not None:
                specs.append(spec)
    return [replace(spec, action_id=action_id, title=title) for spec in specs]


def area_ask_dodge_spec(
    use: AreaUse,
    game_data: GameData,
    *,
    target: str,
    action_id: str,
    source: str = "",
    cover: int = 0,
    adjust: int = 0,
) -> RollSpec:
    """The Dodge check the GM asks one **player** for, on its own.

    :func:`area_dodge_spec` with no save chained to it: the result goes back to the
    GM's table, which works out the rank the player then resists and asks for that save
    next (:func:`area_ask_save_spec`). It names the player in ``targets`` and carries
    the action's id, so the roll finds its way into the action's table and back to the
    GM. Its trait key is still Dodge, so the player's own number goes in.
    """

    spec = area_dodge_spec(
        use, game_data, source=source, targets=(target,), cover=cover, adjust=adjust
    )
    return replace(
        spec,
        follow_up=None,
        follow_up_always=False,
        action_id=action_id,
        hint="Your Dodge against the area. The GM's table works out the save that follows.",
    )


def area_ask_save_spec(
    use: AreaUse,
    effect: AreaEffect,
    rank: int,
    game_data: GameData,
    *,
    target: str,
    action_id: str,
    source: str = "",
    adjust: int = 0,
) -> RollSpec | None:
    """The save the GM asks one **player** for, at the rank their Dodge left.

    ``None`` for an effect nobody resists. Its DC is :func:`area_save_dc` at *rank* —
    the one place the table and the request agree on it.
    """

    spec = area_resist_spec(
        use, effect, game_data, full=True, source=source, targets=(target,), adjust=adjust
    )
    dc = area_save_dc(effect, rank, game_data)
    if spec is None or dc is None:
        return None
    halved = " (halved)" if rank < effect.rank else ""
    return replace(
        spec,
        dc=dc,
        label=f"{spec.label}{halved}",
        action_id=action_id,
        title=area_title(source, use.power_name),
        hint="The GM's table applies what it does to you.",
    )


# -- the numbers the GM's table rolls -------------------------------------------------


def area_dodge_modifier(
    char: Character | None, game_data: GameData, *, cover: int = 0, adjust: int = 0
) -> int:
    """*char*'s Dodge against an area — the resistance total plus Evasion, conditions in.

    *cover* and *adjust* are the GM's for this creature, added in.
    """

    if char is None or not game_data.area.dodge:
        return cover + adjust
    own = resistance_roll(char, game_data, game_data.area.dodge).modifier
    return own + area_dodge_bonus(char, game_data) + cover + adjust


def area_resist_modifier(char: Character | None, game_data: GameData, effect: AreaEffect) -> int:
    """*char*'s number against *effect* — its resistance, or the better or worse of two.

    0 when either is unknown: the save is then rolled at the GM's adjustment alone.
    """

    if char is None or not effect.resistance:
        return 0
    probe = RollSpec(label="", kind=effect.resistance_kind, trait_key=effect.resistance)
    return localize_spec(probe, char, game_data).modifier


def area_npc_dodge_spec(
    use: AreaUse,
    char: Character | None,
    game_data: GameData,
    *,
    cover: int = 0,
    adjust: int = 0,
) -> RollSpec:
    """One creature's Dodge against the area as the GM's table rolls it: its number in."""

    return RollSpec(
        label="Dodge",
        modifier=area_dodge_modifier(char, game_data, cover=cover, adjust=adjust),
        dc=use.dodge_dc(game_data),
        kind=KIND_AREA_DODGE,
    )


def area_npc_save_specs(
    use: AreaUse,
    char: Character | None,
    game_data: GameData,
    *,
    dodged: bool | None,
    adjust: int = 0,
) -> list[tuple[AreaEffect, RollSpec]] | None:
    """Each save one creature makes once its Dodge is in, as the GM's table rolls it.

    *dodged* is whether it made its Dodge check, or ``None`` for the primary target the
    attack hit — no Dodge, full rank. ``None`` back means a made Dodge left it no effect
    at all (Evasion's second rank). Each spec carries the creature's own number and the
    DC its rank leaves; an effect nobody resists is left out.
    """

    many = len(use.effects) > 1
    saves: list[tuple[AreaEffect, RollSpec]] = []
    for effect in use.effects:
        rank = (
            area_primary_rank(effect)
            if dodged is None
            else area_stage_rank(effect.rank, dodged, char, game_data)
        )
        if rank is None:
            return None
        dc = area_save_dc(effect, rank, game_data)
        base = _base(game_data, effect.effect_id)
        if dc is None or base is None:
            continue
        what = effect.resistance_label or "Resist"
        if many:
            what = f"{what} ({effect.name})"
        saves.append(
            (
                effect,
                RollSpec(
                    label=what,
                    modifier=area_resist_modifier(char, game_data, effect) + adjust,
                    dc=dc,
                    kind=KIND_POWER_SAVE,
                    outcomes=area_effect_ladder(effect, game_data),
                    success_outcome=(
                        rung_caption(base.resistance_success, game_data, effect.config)
                        if base.resistance_success is not None
                        else ""
                    ),
                ),
            )
        )
    return saves


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
    effect: AreaEffect,
    degree: int | None,
    game_data: GameData,
    char: Character | None = None,
    rank: int | None = None,
) -> ResistanceOutcome | None:
    """The rung of *effect*'s ladder a save of *degree* lands on (a made one: its Hit).

    Given the creature that saved, a Damage area is first measured against its
    Toughness defences (:mod:`.defenses`) at *rank* — the rank it resisted, halved by a
    made Dodge (the area's own rank when not given).
    """

    base = _base(game_data, effect.effect_id)
    if base is None:
        return None
    if char is not None and base.id == game_data.system.damage_effect:
        if damage_negated(
            toughness_defense(char, game_data),
            rank=effect.rank if rank is None else rank,
            penetrating=effect.penetrating,
            degree=degree,
        ):
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

    **Flat on the wire**: the use's keys sit beside the attempt's own rather than under
    a ``"use"`` key. The session's payload sanitizer keeps four levels of nesting
    (:data:`~..session.protocol.MAX_MOD_DEPTH`), and one more level put every value of
    an effect's config past it. An Affliction then arrived with no conditions on its
    ladder, and every creature that failed its save was reported to have resisted.
    """

    action_id: str
    use: AreaUse
    primary: str = ""
    targets: tuple[str, ...] = ()
    source_name: str = ""
    source_ref: str = ""
    #: How the declarer would have it run (:data:`AREA_MODE_RESOLVE` or
    #: :data:`AREA_MODE_REQUEST`); the GM's window opens on it and may change it.
    mode: str = AREA_MODE_RESOLVE

    def to_dict(self) -> dict:
        return {
            **self.use.to_dict(),
            "action_id": self.action_id,
            "primary": self.primary,
            "targets": list(self.targets),
            "source_name": self.source_name,
            "source_ref": self.source_ref,
            "mode": self.mode,
        }

    @classmethod
    def from_dict(cls, raw: object) -> AreaAttempt | None:
        """Parse a payload from another seat, or ``None`` when it is unusable."""

        if not isinstance(raw, Mapping):
            return None
        action_id = _text(raw, "action_id")
        use = AreaUse.from_dict(raw)
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
            mode=(
                AREA_MODE_REQUEST if _text(raw, "mode") == AREA_MODE_REQUEST else AREA_MODE_RESOLVE
            ),
        )

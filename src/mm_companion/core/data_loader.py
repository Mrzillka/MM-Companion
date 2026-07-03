"""Load MM-Companion game data from the bundled data files.

This module is the single entry point the UI uses to obtain rules *content*
(abilities, resistances, skills, advantages, conditions, point costs, ...).
Nothing here implements game rules; it only parses the JSON in
:mod:`mm_companion.data` into typed records so the UI never hardcodes that
content.

Content is aggregated from several files: the core traits (profile fields,
characteristics, abilities, resistances) still live in ``placeholder.json``,
while the richer 4e catalogs come from their own files (``skills.json``,
``advantages.json``, ``conditions.json``) and the point-cost constants from
``costs.json``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from functools import lru_cache
from importlib import resources

DATA_PACKAGE = "mm_companion"
PLACEHOLDER_FILE = "placeholder.json"


@dataclass(frozen=True)
class Field:
    """A free-text descriptive field (character/hero/player name, hair, ...).

    ``primary`` fields are the few identifying ones the UI always shows (name,
    hero name, player); the rest are secondary details the UI may keep in a
    collapsible group.
    """

    key: str
    label: str
    primary: bool = False


@dataclass(frozen=True)
class Characteristic:
    """A trait that is not bought with power points (size, speed, ...).

    ``kind`` selects how the UI renders the value:

    - ``"text"`` — free-text field (the default).
    - ``"choice"`` — one of ``options``, rendered as a combo box.
    - ``"number"`` — an integer spin box bounded by ``minimum``/``maximum``.
    - ``"pool"`` — a calculated *current* value shown beside an editable
      *total* spin box (e.g. power points current / total).

    ``default`` seeds the initial value (an option string, or a number).
    """

    key: str
    label: str
    kind: str = "text"
    options: list[str] = field(default_factory=list)
    default: str | int | None = None
    minimum: int = 0
    maximum: int = 999


@dataclass(frozen=True)
class Ability:
    """A core ability, bought directly with power points.

    ``abbr`` is the short display code (STR, STA, ...). ``derived`` marks combat
    stats (e.g. Attack) the UI shows below a separator, apart from the core
    abilities.
    """

    key: str
    name: str
    abbr: str = ""
    derived: bool = False


@dataclass(frozen=True)
class Resistance:
    """A defense/resistance, linked to the ability it derives from.

    ``abbr`` is the short display code. ``derived`` marks combat stats (e.g.
    Defence) the UI shows below a separator, apart from the core resistances.
    """

    key: str
    name: str
    ability: str = ""
    abbr: str = ""
    derived: bool = False


@dataclass(frozen=True)
class Skill:
    """A skill and the ability it adds to.

    ``focused`` skills have no ranks of their own; the character instead buys
    focused instances (e.g. Close Combat: Swords), one rank pool per focus.
    ``focuses`` lists the suggested focuses for a focused skill;
    ``specializations`` lists illustrative common uses of a non-focused skill.
    ``trained_only`` marks skills that can't be used untrained.
    """

    name: str
    ability: str
    focused: bool
    id: str = ""
    trained_only: bool = False
    action: str = ""
    specializations: tuple[str, ...] = ()
    focuses: tuple[str, ...] = ()
    description: str = ""


@dataclass(frozen=True)
class Advantage:
    """An advantage. ``ranked`` advantages can be taken at more than one rank.

    ``types`` is one or more category tags (Combat, Skill, Fortune, ...);
    ``max_rank`` is a hard cap when the rules specify one (``None`` otherwise);
    ``focused`` advantages apply to one chosen focus and are bought again per
    focus. ``description`` is short summary text the UI shows.
    """

    name: str
    ranked: bool
    description: str = ""
    id: str = ""
    types: tuple[str, ...] = ()
    max_rank: int | None = None
    focused: bool = False

    @property
    def type(self) -> str:
        """The primary category tag (kept for widgets that group by a single type)."""

        return self.types[0] if self.types else ""


@dataclass(frozen=True)
class Condition:
    """A status condition that can affect a character (dazed, stunned, ...).

    ``category`` distinguishes general conditions from the damage/object-damage
    ladders. ``includes`` lists ids of sub-conditions this one bundles in, and
    ``supersedes`` lists ids a more severe condition replaces — together these
    form the condition graph the combat state machine walks. ``effect`` and
    ``recovery`` are short summary text the UI may show.
    """

    name: str
    description: str = ""
    id: str = ""
    category: str = ""
    includes: tuple[str, ...] = ()
    supersedes: tuple[str, ...] = ()
    effect: str = ""
    recovery: str = ""


@dataclass(frozen=True)
class ConfigOption:
    """One selectable value for an :class:`EffectConfigField`.

    ``value`` is what gets stored in ``PowerEffectInstance.config``; ``label`` is
    what the UI shows (e.g. value ``"dazed"`` shown as ``"Dazed"``).
    """

    value: str
    label: str


@dataclass(frozen=True)
class EffectConfigField:
    """One configurable *quality* of an effect (see ``mm-powers-architecture.md`` §9).

    Effects like Affliction require player choices — which resistance it targets,
    which condition each degree inflicts. Each field is stored under ``key`` in the
    :class:`~mm_companion.core.powers.PowerEffectInstance` ``config`` dict. ``type``
    is ``"select"`` (one of ``options``), ``"multiselect"`` (a list of them), or
    ``"text"`` (free text). ``overrides``, if set, names a base game-term field (e.g.
    ``"resistance"``) that the chosen value replaces in the generated summary;
    otherwise the choice is appended to it. ``multiselect_with`` names an extra whose
    presence upgrades a ``select`` field to ``multiselect`` — e.g. Affliction's
    ``extra_condition`` lets each degree hold two same-degree conditions.
    """

    key: str
    label: str
    type: str = "select"
    overrides: str | None = None
    multiselect_with: str | None = None
    options: tuple[ConfigOption, ...] = ()


@dataclass(frozen=True)
class Measure:
    """A rank-derived real-world measurement an effect exposes (see ``measurements.json``).

    ``column`` picks the measurements-table column (``"distance"``/``"mass"``/
    ``"time"``/``"volume"``); ``label`` is the table row this measure is shown under
    (e.g. ``"Speed"``); ``per_round`` marks a speed — a distance covered each round —
    so the value reads e.g. ``"30 feet/round"`` rather than a bare distance.
    """

    column: str
    label: str
    per_round: bool = False


@dataclass(frozen=True)
class Effect:
    """A base power effect from ``effects.json`` (see ``mm-powers-architecture.md``).

    A power is assembled from one or more of these, each carrying its own extras
    and flaws. ``base_cost`` is the human-readable prose (e.g. ``"1 per rank"``);
    ``base_cost_value`` is the canonical machine-readable points-per-rank used for
    automatic cost calculation. ``configurable_target`` marks Enhanced-Trait-style
    effects that target a chosen trait. ``stat_pattern``/``stat_affects``/``stat_target``
    are the flattened ``statIntegration`` object describing how the effect patches
    stats: ``stat_affects`` is the trait *category* it can boost
    (``ability``/``resistance``/``defense``/``skill``/…), and ``stat_target`` is the
    specific trait key for a fixed-target booster like Protection (``"TOUGHNESS"``),
    left blank when the player chooses the target (``configurable_target``).
    ``config_fields`` are the effect's configurable qualities (Affliction's
    conditions, etc.), the player's choices for which live in the instance's config.
    ``measure`` is a rank-derived real-world quantity the effect exposes (a movement
    speed, a leap distance). ``resistance_dc_base`` is the fixed part of the save DC
    an attack imposes — the resistance DC is ``resistance_dc_base + rank`` (10 for
    most resistible effects, 15 for Damage, 0 for the opposed ones like Move Object)
    — left ``None`` for effects that impose no save DC.
    """

    id: str
    name: str
    effect_type: str
    action: str = ""
    range_: str = ""
    duration: str = ""
    check: str | None = None
    resistance: str | None = None
    base_cost: str = ""
    base_cost_value: int = 1
    configurable_target: bool = False
    stat_pattern: str = ""
    stat_affects: str = ""
    description: str = ""
    config_fields: tuple[EffectConfigField, ...] = ()
    measure: Measure | None = None
    resistance_dc_base: int | None = None
    stat_target: str = ""


@dataclass(frozen=True)
class Modifier:
    """An extra or flaw from ``modifiers.json`` (see ``mm-powers-architecture.md``).

    ``category`` is ``"extra"`` (adds cost/benefit) or ``"flaw"`` (subtracts
    cost/adds a restriction). ``cost_formula`` is the prose; ``cost_value`` is the
    canonical numeric magnitude (always non-negative — the sign comes from
    ``category``). ``flat`` is ``True`` when the cost is a one-time add/subtract to
    the effect total rather than per rank. ``ranked`` is ``True`` when the modifier
    itself is bought in ranks (chosen independently of the effect's rank), so its
    contribution is ``cost_value × rank`` — e.g. Accurate, Extended Range.

    ``overrides`` maps a base-effect game-term field (``range``, ``action``,
    ``duration``, ``resistance``, ``check``, ``effect_type``) to the value this
    modifier forces it to — e.g. Ranged sets ``range`` to ``"Ranged"``, replacing
    a Close or Perception base. It drives the generated game-terms summary only,
    not the point cost.

    The remaining fields describe a modifier's other game-term impacts (see
    :func:`mm_companion.core.rules.effect_stat_rows`), again for the summary, not
    the cost: ``check_bonus`` is a signed adjustment to the effect's attack-roll
    number, per the modifier's rank (Accurate ``+2``, Inaccurate ``-2``);
    ``drops_check`` removes the attack roll entirely (Perception Range);
    ``check_note`` is a parenthetical appended to the check row (Area's
    Dodge-for-half); and ``step_field``/``step_by`` shift a field one or more steps
    along its :attr:`GameData.game_term_ladders` ordering (Increased Duration steps
    ``duration`` up, Increased Action steps ``action`` to a slower one).
    """

    id: str
    name: str
    category: str
    cost_formula: str = ""
    cost_value: int = 0
    flat: bool = False
    ranked: bool = False
    description: str = ""
    overrides: dict[str, str] = field(default_factory=dict)
    check_bonus: int = 0
    drops_check: bool = False
    check_note: str = ""
    step_field: str = ""
    step_by: int = 0


@dataclass(frozen=True)
class TraitCosts:
    """Power-point cost constants for the point-bought traits (``mm-core-mechanics.md`` §6)."""

    ability_per_rank: int
    combat_per_rank: int
    resistance_per_rank: int
    advantage_per_rank: int
    skill_ranks_per_pp: int
    skill_focus_ranks_per_pp: int


@dataclass(frozen=True)
class PowerLevelCap:
    """A Power Level cap expressed as ``power_level * mult + add`` (``mm-core-mechanics.md`` §7)."""

    mult: int
    add: int

    def limit(self, power_level: int) -> int:
        return power_level * self.mult + self.add


@dataclass(frozen=True)
class PowerLevelRules:
    """Power-Level-derived budget and caps."""

    pp_per_level: int
    caps: dict[str, PowerLevelCap]


@dataclass(frozen=True)
class Costs:
    """The parsed contents of ``costs.json``."""

    traits: TraitCosts
    power_level: PowerLevelRules


@dataclass(frozen=True)
class Measurements:
    """The rank → real-world measurement conversion tables (from ``measurements.json``).

    Both the imperial and metric labels are parsed so a later settings toggle need
    only pass a different ``system``; the UI shows imperial for now. ``label`` returns
    the book's own display string for a rank/column (e.g. distance rank 3 →
    ``"60 feet"``), or ``""`` when the rank is outside the tabulated −5…30 range.
    """

    by_rank: dict[int, dict[str, dict[str, str]]]  # rank -> system -> column -> label

    def label(self, column: str, rank: int, system: str = "imperial") -> str:
        return self.by_rank.get(rank, {}).get(system, {}).get(column, "")


@dataclass(frozen=True)
class GameData:
    """The full parsed game-data content, aggregated across the data files.

    ``modifiers`` is the general-purpose extra/flaw pool that applies broadly;
    ``effect_modifiers`` maps an effect id to the extras/flaws specific to that one
    effect (from ``effect_modifiers.json``). A power builder offers both pools for a
    given effect; :meth:`modifier_catalog` merges them into a single id lookup for
    cost math and the game-terms summary.

    ``game_term_ladders`` maps a game-term field (``"duration"``, ``"action"``) to
    its ordered values from least to most, so a stepping modifier (Increased
    Duration, Increased Action) can move a value along it without hardcoding the
    order in code.
    """

    profile_fields: list[Field]
    characteristics: list[Characteristic]
    abilities: list[Ability]
    resistances: list[Resistance]
    skills: list[Skill]
    advantages: list[Advantage]
    conditions: list[Condition]
    effects: list[Effect]
    modifiers: list[Modifier]
    effect_modifiers: dict[str, list[Modifier]]
    costs: Costs
    measurements: Measurements
    game_term_ladders: dict[str, tuple[str, ...]]

    def modifier_catalog(self) -> dict[str, Modifier]:
        """A single ``id -> Modifier`` lookup over the general and effect-specific pools.

        Effect-specific ids are globally unique and never collide with the general
        pool, so a flat merge is unambiguous.
        """

        catalog: dict[str, Modifier] = {m.id: m for m in self.modifiers}
        for mods in self.effect_modifiers.values():
            for modifier in mods:
                catalog.setdefault(modifier.id, modifier)
        return catalog


def _parse_characteristic(c: dict) -> Characteristic:
    options = list(c.get("options", []))
    # Infer a widget kind when not stated: enumerated -> choice, else text.
    kind = c.get("kind") or ("choice" if options else "text")
    return Characteristic(
        key=c["key"],
        label=c["label"],
        kind=kind,
        options=options,
        default=c.get("default"),
        minimum=int(c.get("min", 0)),
        maximum=int(c.get("max", 999)),
    )


def _parse_skill(s: dict) -> Skill:
    return Skill(
        name=s["name"],
        ability=s["ability"],
        focused=bool(s["focused"]),
        id=s.get("id", ""),
        trained_only=bool(s.get("trainedOnly", False)),
        action=s.get("action", ""),
        specializations=tuple(s.get("specializations", ())),
        focuses=tuple(s.get("focuses", ())),
        description=s.get("description", ""),
    )


def _parse_advantage(a: dict) -> Advantage:
    # Accept the rich ``types`` list, falling back to a legacy singular ``type``.
    types = tuple(a["types"]) if "types" in a else tuple(t for t in (a.get("type"),) if t)
    return Advantage(
        name=a["name"],
        ranked=bool(a["ranked"]),
        description=a.get("description", ""),
        id=a.get("id", ""),
        types=types,
        max_rank=a.get("maxRank"),
        focused=bool(a.get("focused", False)),
    )


def _parse_condition(c: dict) -> Condition:
    return Condition(
        name=c["name"],
        description=c.get("description", ""),
        id=c.get("id", ""),
        category=c.get("category", ""),
        includes=tuple(c.get("includes", ())),
        supersedes=tuple(c.get("supersedes", ())),
        effect=c.get("effect", ""),
        recovery=c.get("recovery", ""),
    )


def _parse_config_field(c: dict) -> EffectConfigField:
    return EffectConfigField(
        key=c["key"],
        label=c.get("label", c["key"]),
        type=c.get("type", "select"),
        overrides=c.get("overrides"),
        multiselect_with=c.get("multiselectWith"),
        options=tuple(
            ConfigOption(value=o["value"], label=o.get("label", o["value"]))
            for o in c.get("options", [])
        ),
    )


def _parse_measure(raw: dict | None) -> Measure | None:
    if not raw:
        return None
    return Measure(
        column=raw.get("column", "distance"),
        label=raw["label"],
        per_round=bool(raw.get("perRound", False)),
    )


def _parse_effect(e: dict) -> Effect:
    integration = e.get("statIntegration", {})
    return Effect(
        id=e["id"],
        name=e["name"],
        effect_type=e["effectType"],
        action=e.get("action", ""),
        range_=e.get("range", ""),
        duration=e.get("duration", ""),
        check=e.get("check"),
        resistance=e.get("resistance"),
        base_cost=e.get("baseCost", ""),
        base_cost_value=int(e.get("baseCostValue", 1)),
        configurable_target=bool(e.get("configurableTarget", False)),
        stat_pattern=integration.get("pattern", ""),
        stat_affects=integration.get("affects", ""),
        stat_target=integration.get("target", ""),
        description=e.get("description", ""),
        config_fields=tuple(_parse_config_field(c) for c in e.get("config", [])),
        measure=_parse_measure(e.get("measure")),
        resistance_dc_base=e.get("resistanceDcBase"),
    )


def _parse_modifier(m: dict, category: str | None = None) -> Modifier:
    # Effect-specific modifiers carry no ``category`` of their own — it comes from
    # whether they sit in an ``extras`` or ``flaws`` array (passed in as ``category``).
    return Modifier(
        id=m["id"],
        name=m["name"],
        category=category or m["category"],
        cost_formula=m.get("costFormula", ""),
        cost_value=int(m.get("costValue", 0)),
        flat=bool(m.get("flat", False)),
        ranked=bool(m.get("ranked", False)),
        overrides=dict(m.get("overrides", {})),
        check_bonus=int(m.get("checkBonus", 0)),
        drops_check=bool(m.get("dropsCheck", False)),
        check_note=m.get("checkNote", ""),
        step_field=m.get("stepField", ""),
        step_by=int(m.get("stepBy", 0)),
        description=m.get("description", ""),
    )


def _parse_ladders(raw: dict) -> dict[str, tuple[str, ...]]:
    """Read ``gameTermLadders`` (field -> ordered values) from ``modifiers.json``."""

    return {field: tuple(values) for field, values in raw.get("gameTermLadders", {}).items()}


def _parse_effect_modifiers(raw: dict) -> dict[str, list[Modifier]]:
    """Parse ``effect_modifiers.json`` into ``effect id -> [Modifier, ...]``.

    Each effect's ``extras`` and ``flaws`` arrays are flattened into one list, with
    the category tagged onto each modifier from the array it came from.
    """

    result: dict[str, list[Modifier]] = {}
    for effect_id, groups in raw.get("effectModifiers", {}).items():
        mods = [_parse_modifier(m, "extra") for m in groups.get("extras", [])]
        mods += [_parse_modifier(m, "flaw") for m in groups.get("flaws", [])]
        result[effect_id] = mods
    return result


def _parse_measurements(raw: dict) -> Measurements:
    """Flatten ``rankMeasures`` into ``rank -> system -> column -> label``.

    Time is a single column shared by both systems, so it is copied into each.
    """

    by_rank: dict[int, dict[str, dict[str, str]]] = {}
    for row in raw.get("rankMeasures", []):
        rank = int(row["rank"])
        time_label = row.get("time", {}).get("label", "")
        systems: dict[str, dict[str, str]] = {}
        for system in ("imperial", "metric"):
            block = row.get(system, {})
            labels = {
                col: block.get(col, {}).get("label", "") for col in ("mass", "distance", "volume")
            }
            labels["time"] = time_label
            systems[system] = labels
        by_rank[rank] = systems
    return Measurements(by_rank=by_rank)


def _parse_costs(raw: dict) -> Costs:
    traits = TraitCosts(**{k: int(v) for k, v in raw["trait_costs"].items()})
    pl = raw["power_level"]
    caps = {
        name: PowerLevelCap(mult=int(cap["mult"]), add=int(cap["add"]))
        for name, cap in pl["caps"].items()
    }
    return Costs(
        traits=traits,
        power_level=PowerLevelRules(pp_per_level=int(pl["pp_per_level"]), caps=caps),
    )


def _read_json(filename: str) -> dict:
    source = resources.files(DATA_PACKAGE).joinpath("data", filename)
    return json.loads(source.read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def load_game_data() -> GameData:
    """Parse and return the bundled game data (cached after first call)."""

    base = _read_json(PLACEHOLDER_FILE)
    skills_raw = _read_json("skills.json")
    advantages_raw = _read_json("advantages.json")
    conditions_raw = _read_json("conditions.json")
    effects_raw = _read_json("effects.json")
    modifiers_raw = _read_json("modifiers.json")
    effect_modifiers_raw = _read_json("effect_modifiers.json")
    costs_raw = _read_json("costs.json")
    measurements_raw = _read_json("measurements.json")

    return GameData(
        profile_fields=[Field(**f) for f in base["profile_fields"]],
        characteristics=[_parse_characteristic(c) for c in base["characteristics"]],
        abilities=[Ability(**a) for a in base["abilities"]],
        resistances=[Resistance(**r) for r in base["resistances"]],
        skills=[_parse_skill(s) for s in skills_raw["skills"]],
        advantages=[_parse_advantage(a) for a in advantages_raw["advantages"]],
        conditions=[_parse_condition(c) for c in conditions_raw["conditions"]],
        effects=[_parse_effect(e) for e in effects_raw["effects"]],
        modifiers=[_parse_modifier(m) for m in modifiers_raw["modifiers"]],
        effect_modifiers=_parse_effect_modifiers(effect_modifiers_raw),
        costs=_parse_costs(costs_raw),
        measurements=_parse_measurements(measurements_raw),
        game_term_ladders=_parse_ladders(modifiers_raw),
    )

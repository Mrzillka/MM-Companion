"""Load MM-Companion game data from the bundled data files.

This module is the single entry point the UI uses to obtain rules *content*
(abilities, resistances, skills, advantages, conditions, point costs, ...).
Nothing here implements game rules; it only parses the JSON in
:mod:`mm_companion.data` into typed records so the UI never hardcodes that
content.

Content is aggregated from several files: the core traits live in their own
files (``profile.json``, ``characteristics.json``, ``abilities.json``,
``resistances.json``), the richer 4e catalogs come from theirs (``skills.json``,
``advantages.json``, ``conditions.json``), the gear catalog from
``equipment.json``, and the point-cost constants from ``costs.json``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, fields, replace

from . import mods as mods_module
from .components import APPLY_BONUS, Integration, TraitBoost

DATA_PACKAGE = "mm_companion"


# ===========================================================================
# Records — the typed dataclasses each data file parses into. Grouped below by
# the content they model; the parsing functions that build them follow further
# down, then the mod merge loader and cached public entry point at the end.
# ===========================================================================


# --- Core character traits: profile, characteristics, abilities, resistances,
#     skills, advantages (the point-bought sheet content). --------------------
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
    #: Unrecognised JSON keys (e.g. from a mod), retained rather than dropped.
    extra: dict = field(default_factory=dict, compare=False)


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
    #: Unrecognised JSON keys (e.g. from a mod), retained rather than dropped.
    extra: dict = field(default_factory=dict, compare=False)


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
    #: Unrecognised JSON keys (e.g. from a mod), retained rather than dropped.
    extra: dict = field(default_factory=dict, compare=False)


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
    #: Unrecognised JSON keys (e.g. from a mod), retained rather than dropped.
    extra: dict = field(default_factory=dict, compare=False)


@dataclass(frozen=True)
class Skill:
    """A skill and the ability it adds to.

    ``focused`` skills have no ranks of their own; the character instead buys
    focused instances (e.g. Close Combat: Swords), one rank pool per focus.
    ``focuses`` lists the suggested focuses for a focused skill;
    ``specializations`` lists illustrative common uses of a non-focused skill.
    ``trained_only`` marks skills that can't be used untrained.
    ``specialized_cost`` prices this skill's ordinary ranks at the cheaper
    specialized rate (Expertise, whose mandatory focus makes it 4 ranks/PP).
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
    specialized_cost: bool = False
    #: Unrecognised JSON keys (e.g. from a mod), retained rather than dropped.
    extra: dict = field(default_factory=dict, compare=False)


@dataclass(frozen=True)
class ParameterSpec:
    """Describes the per-selection subject an advantage asks the player to choose.

    Advantages like Skill Mastery (a skill), Improved Critical (an attack), or
    Benefit (a free-form description) attach to a chosen subject, stored on the
    character as ``AdvantageSelection.parameter``. This record — parsed from an
    advantage's ``parameter`` JSON object — tells the UI *what* to ask for:

    - ``label`` — the prompt shown beside the picker (e.g. ``"Skill"``).
    - ``kind`` — ``"text"`` for a free-text field, ``"choice"`` for a dropdown.
    - ``options`` — a fixed choice list (e.g. the interaction skills), when the
      choice is a small enumerated set baked into the data.
    - ``options_from`` — a *dynamic* choice source resolved by the UI against the
      live build instead of a fixed list: ``"skills"``/``"abilities"`` (from
      :class:`GameData`) or ``"powers"`` (the character's own powers). Empty when
      ``options`` supplies the list (or ``kind == "text"``).
    """

    label: str
    kind: str = "text"
    options: tuple[str, ...] = ()
    options_from: str = ""


@dataclass(frozen=True)
class Advantage:
    """An advantage. ``ranked`` advantages can be taken at more than one rank.

    ``types`` is one or more category tags (Combat, Skill, Fortune, ...);
    ``max_rank`` is a hard cap when the rules specify one (``None`` otherwise);
    ``max_rank_kind`` says how that cap is derived (``"fixed"`` uses ``max_rank``,
    ``"power_level_half"`` is Improved Initiative's ``ceil(PL/2)``, ``"heroic_budget"``
    draws from the shared Heroic pool, ``"power_level"``/``"none"`` impose no
    standalone number — see ``advantages.json``'s ``maxRankKindKey``);
    ``focused`` advantages apply to one chosen focus and are bought again per
    focus. ``parameter`` (when set) is the subject the UI prompts for — see
    :class:`ParameterSpec`. ``description`` is short summary text the UI shows.

    ``skill_bonus_per_rank`` makes the advantage a standing source of skill bonus:
    that many points per bought rank land on the skill named by ``skill_bonus_target``,
    or — when that is empty — on the skill the selection's ``parameter`` chose.
    """

    name: str
    ranked: bool
    description: str = ""
    id: str = ""
    types: tuple[str, ...] = ()
    max_rank: int | None = None
    max_rank_kind: str = "none"
    focused: bool = False
    initiative_bonus_per_rank: int = 0
    initiative_ability_choice: tuple[str, ...] = ()
    skill_bonus_per_rank: int = 0
    skill_bonus_target: str = ""
    parameter: ParameterSpec | None = None
    #: Unrecognised JSON keys (e.g. from a mod), retained rather than dropped.
    extra: dict = field(default_factory=dict, compare=False)

    @property
    def type(self) -> str:
        """The primary category tag (kept for widgets that group by a single type)."""

        return self.types[0] if self.types else ""


# --- Conditions: the status catalog + its mechanical sub-records (parameters,
#     debilitation, defense/attack/resistance mods, stacking, recovery). -------
@dataclass(frozen=True)
class ConditionParameterOption:
    """One choice in a condition parameter's dropdown.

    ``value`` is both the label shown and the text stored. The two flags mark the
    choices that are not ordinary subjects, so the dialog recognises them from the
    data rather than by matching their prose (which a reword or a translation would
    silently break):

    - ``unscoped`` — the choice means *no particular subject* (Disabled's
      "All checks", Unaware's "All senses"), and is stored as no scope at all.
    - ``specific_kind`` — the choice is a placeholder that opens a second dropdown of
      concrete traits of that kind: ``"a specific skill"`` carries ``"skill"``.
    """

    value: str
    unscoped: bool = False
    specific_kind: str = ""


@dataclass(frozen=True)
class ConditionParameter:
    """The subject a condition must be qualified with when applied (§6).

    ``type`` is one of ``trait_select`` / ``sense_select`` / ``descriptor_text`` /
    ``character_ref`` and drives the UI control; ``options`` populates a combobox
    (empty ⇒ free text), with ``option_specs`` carrying the same choices plus the
    per-choice flags described on :class:`ConditionParameterOption`. ``required``
    gates whether the condition can be applied before the subject is named — see
    ``docs/mm-conditions-design.md`` §6.
    """

    type: str
    required: bool = False
    label: str = ""
    help: str = ""
    options: tuple[str, ...] = ()
    option_specs: tuple[ConditionParameterOption, ...] = ()


@dataclass(frozen=True)
class Debilitation:
    """Trait-loss cascade for a ``debilitate_trait`` condition (§7).

    ``cascade`` maps a chosen trait name to the hard conditions its loss triggers
    (Strength → Incapacitated); an empty tuple means the trait is lost with no
    cascade. ``notes`` carries the extra per-trait rules as prose.
    """

    cascade: dict[str, tuple[str, ...]] = field(default_factory=dict)
    notes: str = ""


@dataclass(frozen=True)
class DefenseMod:
    """How a condition alters Defense/Dodge — each ``"halve"`` / ``"zero"`` or empty."""

    defense: str = ""
    dodge: str = ""


@dataclass(frozen=True)
class AttackMods:
    """Prone-style attack modifiers (own close, incoming close, incoming ranged)."""

    own_close: int = 0
    incoming_close: int = 0
    incoming_ranged: int = 0


@dataclass(frozen=True)
class ResistanceMod:
    """Scoped resistance penalty (Susceptible / Weakness).

    ``penalty_formula`` and ``best_outcome`` are read as data by the resistance
    subsystem; the actual per-check math (which needs the incoming effect's rank)
    is the roll layer's job.
    """

    scope: str = ""
    penalty_formula: str = ""
    best_outcome: str = ""


@dataclass(frozen=True)
class StackingRule:
    """Per-instance accumulation rule (Hit): each instance adds ``per_instance_penalty``."""

    per_instance_penalty: int = 0
    applies_to: str = ""
    removed_per_recovery: int = 0


@dataclass(frozen=True)
class RecoveryCheck:
    """Structured recovery check (§8). Loaded now; consumed by the future roll layer."""

    trait: str | None = None
    dc: int | None = None
    cadence: str = ""
    condition: str = ""
    outcome: str = ""


@dataclass(frozen=True)
class RandomActionRow:
    """One row of a ``random_action`` table (Confused). Loaded now, roll layer later."""

    range: str = ""
    outcome: str = ""


@dataclass(frozen=True)
class Condition:
    """A status condition that can affect a character (dazed, stunned, ...).

    ``category`` distinguishes general conditions from the damage/object-damage
    ladders, and is what the sheet sorts *applied* chips by. ``group`` is the
    finer, orthogonal split a "+" menu offers the catalog under (Senses,
    Movement, …) — a finding aid, not a rule, since a flat list of 36 is slow to
    search mid-round. ``includes`` lists ids of sub-conditions this one bundles in, and
    ``supersedes`` lists ids a more severe condition replaces — together these
    form the condition graph the combat state machine walks (see
    ``docs/mm-conditions-design.md`` §3). ``mechanisms`` names which engine subsystems
    the condition feeds (§4); the typed effect fields (``penalty``,
    ``speed_rank_mod``, ``defense_mod``, …) carry the data those subsystems read so
    the engine never parses ``effect`` prose. ``tooltip`` is a short always-visible
    line; ``effect``/``recovery`` are the fuller summaries.
    """

    name: str
    description: str = ""
    id: str = ""
    category: str = ""
    group: str = ""
    tooltip: str = ""
    includes: tuple[str, ...] = ()
    supersedes: tuple[str, ...] = ()
    mechanisms: tuple[str, ...] = ()
    stacking: bool = False
    parameter: ConditionParameter | None = None
    debilitates: Debilitation | None = None
    effect: str = ""
    recovery: str = ""
    penalty: int | None = None
    speed_rank_mod: int | None = None
    defense_mod: DefenseMod | None = None
    attack_mods: AttackMods | None = None
    resistance_mod: ResistanceMod | None = None
    stacking_rule: StackingRule | None = None
    recovery_check: RecoveryCheck | None = None
    random_table: tuple[RandomActionRow, ...] = ()
    #: Whether the trait this condition scopes to should read as lost — the sheet
    #: strikes it through (Disabled, Debilitated). Not derivable from ``mechanisms``:
    #: Disabled is a check penalty severe enough to count, Debilitated a real trait
    #: removal, so the data says so outright.
    trait_lost: bool = False


@dataclass(frozen=True)
class ConditionCategory:
    """One group the Conditions block sorts its chips into, from ``conditions.json``.

    ``category`` matches a :class:`Condition`'s ``category``; ``title`` is the heading
    shown above that group; ``addable`` marks a group the "+" menu may apply from —
    false for the object-damage ladder and the ``normal`` bookkeeping marker, which
    are not statuses a player puts on a character.

    In data (``_meta.sheetSections``) rather than in the widget so a mod that adds a
    category gets a group of its own instead of having its conditions silently folded
    into the general one.
    """

    category: str
    title: str
    addable: bool = True


@dataclass(frozen=True)
class ConditionGroup:
    """One submenu a "+" menu splits the addable catalog into, from ``conditions.json``.

    ``group`` matches a :class:`Condition`'s ``group``; ``title`` is the submenu's
    caption. Read from ``_meta.conditionGroups`` in declared order.

    Deliberately separate from :class:`ConditionCategory`, which the two axes are
    easy to confuse: a *category* is a rules fact (this is part of the damage
    ladder) and groups the chips already on a character; a *group* is an
    ergonomic one (Blind and Deaf are both things you do to someone's senses) and
    only ever shapes a menu. A ruleset that declares no groups gets the flat
    alphabetical menu back, so this is purely additive.
    """

    group: str
    title: str


# --- Powers layer: base effects, modifiers (extras/flaws), and the config
#     fields / measures that qualify an effect. --------------------------------
@dataclass(frozen=True)
class ConfigOption:
    """One selectable value for an :class:`EffectConfigField`.

    ``value`` is what gets stored in ``PowerEffectInstance.config``; ``label`` is
    what the UI shows (e.g. value ``"dazed"`` shown as ``"Dazed"``). ``cost_value``,
    when set on a *modifier's* config option, overrides that modifier's cost
    magnitude while the option is chosen — so a Side Effect's always/on-failure
    toggle or a Removable tier changes the discount (see ``docs/mm-powers-ui-design.md``
    §4). ``None`` leaves the modifier's own ``cost_value`` in force.

    ``flat``, likewise on a *modifier's* config option, overrides whether the
    modifier is charged once (``True``) or per rank (``False``) while the option is
    chosen — Affliction's Onset is a flat ``-1`` when its conditions land after a
    round but a per-rank ``-1`` when they land after a scene. ``None`` leaves the
    modifier's own ``flat`` in force.

    ``ranked`` mirrors ``flat``: on a *modifier's* config option it overrides whether
    the modifier's cost is multiplied by its own rank while the option is chosen — a
    Custom modifier's *flat* mode is charged ``cost × rank`` while its *per-rank* mode
    ignores the rank (it already scales with the effect's rank). ``None`` leaves the
    modifier's own ``ranked`` in force.
    """

    value: str
    label: str
    cost_value: int | None = None
    flat: bool | None = None
    ranked: bool | None = None


@dataclass(frozen=True)
class SpeedRank:
    """The rate one tier of a movement mode grants, as a distance rank.

    Some modes move at a rate of their own regardless of how fast the character walks
    (Swinging's flat rank 2, Permeate's slow lower tiers); others are expressed against
    the character's ground speed (Wall-Crawling's "ground speed, minus one rank at the
    lower tier"). ``from_ground`` picks which, and ``value`` is the flat rank or the
    signed offset accordingly.

    In JSON a tier is written as a bare number for a flat rank (``2``) or as a
    ``"ground"`` expression for a relative one (``"ground"``, ``"ground-1"``).
    """

    value: int = 0
    from_ground: bool = False

    def rank(self, ground_rank: int) -> int:
        return ground_rank + self.value if self.from_ground else self.value


@dataclass(frozen=True)
class AllocationOption:
    """One named sub-ability on a Tier-4 ``allocation`` field (Enhanced Senses etc.).

    ``tiers`` lists the rank cost of each successive tier of the option — a single
    entry for a fixed-cost option (``(2,)`` = 2 ranks), several for a tiered one
    (``(2, 4, 6)`` = increasing scope). Picking the option consumes the chosen
    tier's cost from the effect's rank pool. ``per_note`` is an optional qualifier
    shown after the label (e.g. ``"per environment"``, ``"per sense"``).

    ``description`` is a one-line summary of what the option does, shown on hover in
    the constructor's checklist — a list of two dozen bare names (Permeate, Trackless,
    Ultravision) is otherwise unreadable without the rulebook open beside it.

    ``speeds`` gives the movement *rate* each tier grants as a :class:`SpeedRank`, one
    entry per tier. It is **empty for an option that is not a way of moving at all**
    (Safe Fall, Trackless, Stable) — those confer no rate, so nothing lists them among
    the character's movement speeds. ``tier_notes`` is an optional per-tier caveat shown
    beside that rate (Wall-Crawling's "vulnerable while climbing"). Both are read by
    :func:`mm_companion.core.rules.movement_mode_lines`.
    """

    id: str
    label: str
    tiers: tuple[int, ...] = (1,)
    per_note: str = ""
    description: str = ""
    speeds: tuple[SpeedRank | None, ...] = ()
    tier_notes: tuple[str, ...] = ()

    def speed(self, tier: int) -> SpeedRank | None:
        """The rate a 1-based ``tier`` grants, or ``None`` when it grants none."""
        if 1 <= tier <= len(self.speeds):
            return self.speeds[tier - 1]
        return None

    def tier_note(self, tier: int) -> str:
        """The caveat attached to a 1-based ``tier`` (``""`` when it has none)."""
        if 1 <= tier <= len(self.tier_notes):
            return self.tier_notes[tier - 1]
        return ""


@dataclass(frozen=True)
class RepeatableColumn:
    """One column of a Tier-4 ``repeatable`` field's rows (Immunity, Feature).

    ``type`` is ``"text"`` (free text) or ``"int"`` (a rank spin). ``key`` names
    where the value lives inside each stored row dict.
    """

    key: str
    label: str
    type: str = "text"


@dataclass(frozen=True)
class EffectConfigField:
    """One configurable *quality* of an effect (see ``docs/mm-powers-architecture.md`` §9).

    Effects like Affliction require player choices — which resistance it targets,
    which condition each degree inflicts. Each field is stored under ``key`` in the
    :class:`~mm_companion.core.powers.PowerEffectInstance` ``config`` dict. ``type``
    is one of:

    - ``"select"`` — one of ``options``;
    - ``"multiselect"`` — a list of ``options``;
    - ``"text"`` — free text;
    - ``"checkbox"`` — a boolean that, if ``toggles`` is set, attaches/detaches that
      named extra rather than storing a value (e.g. Damage's Strength-Based);
    - ``"allocation"`` — a checklist of ``alloc_options`` whose chosen tier costs sum
      against the effect's rank (Enhanced Senses/Movement, Comprehend); stored as a
      list of ``{"id", "tier"}`` dicts;
    - ``"repeatable"`` — a variable-length list of rows shaped by ``columns``
      (Immunity scopes, Features); stored as a list of row dicts.
    - ``"points"`` — an integer spin box bounded by ``min_value``/``max_value`` whose
      stored value *is* the modifier's flat cost magnitude (a Subtle extra worth 1 or
      2 points); ``default_value`` is the value a fresh selection starts at.

    ``overrides``, if set, names a base game-term field (e.g. ``"resistance"``) that
    the chosen value replaces in the generated summary; otherwise the choice is
    appended to it. ``multiselect_with`` names an extra whose presence upgrades a
    ``select`` field to ``multiselect`` — e.g. Affliction's ``extra_condition`` lets
    each degree hold two same-degree conditions. ``hidden_with`` names an extra whose
    presence hides the field entirely (Affliction's ``variable_conditions`` defers
    the degree choices to use-time). ``toggles`` is the extra a ``checkbox`` field
    attaches. ``source``, on a ``select`` field, names a data-driven option source
    to populate instead of a static ``options`` list — currently ``"traits"``
    (abilities, resistances, and skills), used by Enhanced Trait's Reduced Trait
    flaw to pick which trait is lowered. ``hides_field``, on a *modifier's* config
    field, marks that the chosen value is the ``key`` of one of the *parent effect's*
    config fields to hide — Affliction's Limited Degree flaw picks a degree tier
    (``degree1``/``degree2``/``degree3``) whose condition picker then disappears.
    ``hint`` is helper text shown under an ``allocation``/``repeatable`` field
    (e.g. Immunity's suggested-rank tiers). ``show_when_points``, on a *modifier's*
    config field, gates its visibility on a sibling ``points`` field's value — the
    field appears only when that spin box reads exactly this number (Affliction's
    Variable Conditions reveals its "which degree" picker only at the 1-point tier).
    Zero (the default) means always shown.
    """

    key: str
    label: str
    type: str = "select"
    overrides: str | None = None
    multiselect_with: str | None = None
    hidden_with: str | None = None
    toggles: str | None = None
    source: str | None = None
    hides_field: bool = False
    hint: str = ""
    min_value: int = 0
    max_value: int = 0
    default_value: int = 0
    show_when_points: int = 0
    options: tuple[ConfigOption, ...] = ()
    alloc_options: tuple[AllocationOption, ...] = ()
    columns: tuple[RepeatableColumn, ...] = ()


@dataclass(frozen=True)
class Measure:
    """A rank-derived real-world measurement an effect exposes (see ``measurements.json``).

    ``column`` picks the measurements-table column (``"distance"``/``"mass"``/
    ``"time"``/``"volume"``); ``label`` is the table row this measure is shown under
    (e.g. ``"Speed"``); ``per_round`` marks a speed — a distance covered each round —
    so the value reads e.g. ``"30 feet/round"`` rather than a bare distance.

    ``mode`` names the *way of moving* a per-round distance is a speed **in**, which is
    what lets the Speed readout net several sources into one line: two Flight powers are
    one flight speed, and the Speed effect feeds the same ground line the character walks
    on. Effects sharing a mode are reconciled; effects in different modes are simply
    different ways to get about and each keep a line. Defaults to the effect's own id, so
    an effect that names none is its own mode and behaves exactly as it always did.
    """

    column: str
    label: str
    per_round: bool = False
    mode: str = ""


@dataclass(frozen=True)
class ResistanceOutcome:
    """One rung of an effect's degree-of-failure ladder (``resistanceOutcomes``).

    Failing a resistance check does something specific to the target, and *how*
    specific depends on the effect. Damage's rungs are fixed by the rules
    (``conditions`` naming ids from ``conditions.json``); Affliction's are whatever
    the player chose when building the power, so its rungs carry a ``config_key``
    naming the instance config field to read the ids out of instead. ``text`` is the
    escape hatch for a rung the condition catalog can't express, and ``note`` is a
    short qualifier shown after the conditions ("Stunned instead if already Dazed").

    The ladder is indexed by degree of failure — index 0 is one degree — and its last
    rung covers every deeper failure, so a three-rung ladder answers a five-degree
    rout without inventing rungs. A ladder may also carry a ``success`` rung
    (:attr:`Effect.resistance_success`): making a Toughness save is not "nothing
    happened" — the target still takes a Hit unless their Toughness is Hardened,
    Impervious or Impenetrable, which is a caveat only the ``note`` can carry since
    this app cannot see the target's sheet.
    """

    conditions: tuple[str, ...] = ()
    config_key: str = ""
    text: str = ""
    note: str = ""
    #: What this rung applies *instead*, as ``(already-has, apply-instead)`` pairs —
    #: the data form of "Stunned instead of Dazed if already Dazed". Chained by
    #: :func:`mm_companion.core.rules.resolve_damage_step`, so a rung that names both
    #: ``incapacitated -> dying`` and ``dying -> dead`` walks a target down the ladder
    #: one failure at a time. Pairs rather than a mapping so the record stays hashable.
    escalates: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class Effect:
    """A base power effect from ``effects.json`` (see ``docs/mm-powers-architecture.md``).

    A power is assembled from one or more of these, each carrying its own extras
    and flaws. ``base_cost`` is the human-readable prose (e.g. ``"1 per rank"``);
    ``base_cost_value`` is the canonical machine-readable points-per-rank used for
    automatic cost calculation. ``integration`` is the parsed ``statIntegration``
    component (see :class:`mm_companion.core.components.Integration`) describing how
    the effect patches stats — its activation ``pattern`` and, for the passive
    trait-boosting effects (Enhanced Trait, Protection), a ``trait_boost`` naming the
    trait categories it can raise and any fixed target.
    ``config_fields`` are the effect's configurable qualities (Affliction's
    conditions, etc.), the player's choices for which live in the instance's config.
    ``measure`` is a rank-derived real-world quantity the effect exposes (a movement
    speed, a leap distance). ``resistance_dc_base`` is the fixed part of the save DC
    an attack imposes — the resistance DC is ``resistance_dc_base + rank`` (10 for
    most resistible effects, 0 for the opposed ones like Move Object) — left
    ``None`` for effects that impose no save DC.

    ``implicit_modifiers`` names modifier ids the effect carries as part of its own
    definition rather than as a player's choice — an attacking effect (Damage,
    Affliction, …) implicitly has the ``attack`` extra, which is what supplies its
    "Attack vs. Defense" check. :func:`mm_companion.core.rules.effect_stat_rows`
    folds them into the effect's *base* stats untinted, so they read as part of the
    record; only their ``overrides`` and ``grants_attack`` apply. They never sit on
    the instance, so they cost nothing and render no chip.

    ``effect_type`` is the **catalog taxonomy** — it groups the constructor's effect
    palette and seeds the Type game-term row. A modifier may override the *effective*
    Type of an instance (the ``attack`` extra sets it to ``"Attack"``), so a Control
    effect that implicitly attacks reads as Type "Attack" on its card while still
    filing under Control in the palette.
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
    integration: Integration = field(default_factory=Integration)
    description: str = ""
    config_fields: tuple[EffectConfigField, ...] = ()
    measure: Measure | None = None
    resistance_dc_base: int | None = None
    #: What failing this effect's resistance check does to the target, one rung per
    #: degree of failure (see :class:`ResistanceOutcome`). Empty for an effect whose
    #: outcome is a GM call rather than a table.
    resistance_outcomes: tuple[ResistanceOutcome, ...] = ()
    #: What *making* the check still costs the target, for the effects where that is
    #: not nothing (Damage's Hit). ``None`` where a made save really is a clean escape.
    resistance_success: ResistanceOutcome | None = None
    implicit_modifiers: tuple[str, ...] = ()
    #: How far this effect reaches once its range resolves to Ranged. Seeded from the
    #: system-wide default and overridden by the effect's own ``rangeDistance`` block.
    range_distance: RangeDistance | None = None


@dataclass(frozen=True)
class Modifier:
    """An extra or flaw from ``modifiers.json`` (see ``docs/mm-powers-architecture.md``).

    ``category`` is ``"extra"`` (adds cost/benefit) or ``"flaw"`` (subtracts
    cost/adds a restriction). ``cost_formula`` is the prose; ``cost_value`` is the
    canonical numeric magnitude (always non-negative — the sign comes from
    ``category``). ``flat`` is ``True`` when the cost is a one-time add/subtract to
    the effect total rather than per rank. ``ranked`` is ``True`` when the modifier
    itself is bought in ranks (chosen independently of the effect's rank), so its
    contribution is ``cost_value × rank`` — e.g. Accurate, Extended Range.
    ``max_rank`` is the ceiling the rules put on those ranks (Striding's 5), and
    ``None`` when they give none — read it through
    :func:`mm_companion.core.rules.modifier_rank_cap` rather than off the record, so a
    caller need not carry the two cases. It says nothing about an *unranked* modifier,
    which always stands at the one rank its host effect has.

    ``overrides`` maps a base-effect game-term field (``range``, ``action``,
    ``duration``, ``resistance``, ``check``, ``effect_type``) to the value this
    modifier forces it to — e.g. Ranged sets ``range`` to ``"Ranged"``, replacing
    a Close or Perception base. It drives the generated game-terms summary only,
    not the point cost. The JSON spells its keys in camelCase like every other key
    in these files, so ``_parse_modifier`` normalizes ``effectType`` to the
    ``effect_type`` the stat dicts use (see :data:`_OVERRIDE_KEYS`).

    The remaining fields describe a modifier's other game-term impacts (see
    :func:`mm_companion.core.rules.effect_stat_rows`), again for the summary, not
    the cost: ``check_bonus`` is a signed adjustment to the effect's attack-roll
    number, per the modifier's rank (Accurate ``+2``, Inaccurate ``-2``);
    ``grants_attack`` marks the modifier that *gives* the effect its attack roll —
    it is what :func:`mm_companion.core.rules.effect_makes_attack` reads, rather
    than sniffing the check prose, and ``drops_check`` cancels it;
    ``drops_check`` removes the attack roll entirely (Perception Range);
    ``check_note`` is a parenthetical appended to the check row (Area's
    Dodge-for-half); and ``step_field``/``step_by`` shift a field one or more steps
    along its :attr:`GameData.game_term_ladders` ordering (Increased Duration steps
    ``duration`` up, Increased Action steps ``action`` to a slower one).

    ``adds_ability`` names a character ability whose rank is added to the effect's
    *effective* rank — Strength-Based Damage (``"STR"``) folds the wielder's Strength
    into the Damage rank for its resistance DC and Power Level cap. It is the one
    modifier field that reaches back into character stats, so cost/PL math must be
    given the character to resolve it.

    ``gate`` marks a flaw that can switch an effect's standing bonus off at runtime
    (one of :mod:`mm_companion.core.components`'s ``GATE_*`` kinds): Activation
    (``"activation"``), Removable (``"removable"``), Limited (``"limited"``),
    Requires-Effect (``"requires_effect"``). Empty for modifiers with no runtime gate.
    Consulted by :func:`mm_companion.core.rules.effect_is_active`.
    ``requires_effect_id`` pairs with a ``"requires_effect"`` gate: it names the base
    effect id that must be currently active on the wielder for this effect's bonus to
    apply (e.g. ``"insubstantial"`` for the *Limited to While Insubstantial* flaw).

    ``hidden`` keeps a record out of the constructor's palette while leaving it in the
    modifier catalog for cost/lookup use — the structural ``linked`` / ``alternate_effect``
    modifiers are applied automatically from a power's structure (and the array flat cost
    is read from the record), so they should not be draggable as ordinary extras.

    ``note_template`` is a descriptive line the modifier contributes to its effect's
    generated Notes row instead of its bare name — a ``{n}`` placeholder is replaced
    by the effect's rank times ``note_per_rank`` (or the bare rank when that is zero),
    so Affliction's Empowering reads "transformed form gains 60 power points" at rank 4.
    Empty leaves the modifier listed by name.

    ``requires_any`` lists modifier ids of which at least one must also be attached to
    the same effect for this modifier to be valid — Affliction's Increasing Difficulty
    needs Cumulative or Progressive to have repeated checks to escalate. Empty imposes
    no requirement; a breach is a warning (see
    :func:`mm_companion.core.rules.power_modifier_requirement_violations`).
    """

    id: str
    name: str
    category: str
    cost_formula: str = ""
    cost_value: int = 0
    flat: bool = False
    ranked: bool = False
    max_rank: int | None = None
    description: str = ""
    overrides: dict[str, str] = field(default_factory=dict)
    check_bonus: int = 0
    grants_attack: bool = False
    drops_check: bool = False
    check_note: str = ""
    step_field: str = ""
    step_by: int = 0
    #: How many distance ranks each rank of this modifier adds to a Ranged effect's
    #: reach (Extended Range's ``1``). Zero for every modifier that doesn't reach further.
    distance_rank_bonus: int = 0
    adds_ability: str = ""
    gate: str = ""
    requires_effect_id: str = ""
    hidden: bool = False
    note_template: str = ""
    note_per_rank: int = 0
    #: Using the effect first calls for an extra roll that can fail (Check Required).
    #: Such a modifier gets its own game-term row and a line in the card's dice footer
    #: rather than being buried in Notes — it is something someone has to roll.
    requires_check: bool = False
    requires_any: tuple[str, ...] = ()
    config_fields: tuple[EffectConfigField, ...] = ()
    custom: bool = False
    """A blank, player-defined homebrew modifier (Custom Extra / Custom Flaw): its
    name and point cost come from the selection's ``config``, not this record, and it
    has no game-term impact. Marks a power as homerule (see
    :func:`mm_companion.core.rules.power_has_custom_modifier`)."""

    integration: Integration | None = None
    """A ``statIntegration`` of the modifier's own — what taking it *grants*.

    The same record a base effect carries, read by the same appliers, so an extra whose
    text says "longer strides grant ranks of Speed" grants them instead of only costing
    points. ``None`` for the overwhelming majority, which change a price, a game term or
    a gate and nothing on the sheet.

    The rank it is worth is the modifier's own when it is ``ranked``, and otherwise the
    **host effect's** — which is what a per-rank price already says it is charging for.
    """


# --- Point costs & Power Level: per-rank trait costs and the PL-derived budget
#     and caps (from ``costs.json``). ------------------------------------------
@dataclass(frozen=True)
class TraitCosts:
    """Power-point cost constants for the point-bought traits (``docs/mm-core-mechanics.md`` §7)."""

    ability_per_rank: int
    combat_per_rank: int
    resistance_per_rank: int
    advantage_per_rank: int
    skill_ranks_per_pp: int
    skill_specialized_ranks_per_pp: int


@dataclass(frozen=True)
class PowerLevelCap:
    """A Power Level cap as ``power_level * mult + add`` (``docs/mm-core-mechanics.md`` §7)."""

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
class TraitRange:
    """The range a trait's sheet control accepts, from ``costs.json``'s ``trait_ranges``.

    Kept in data so a high-power campaign can widen it without a code change. Note the
    ranges are *not* the same for every trait family: an ability spin box holds the rank
    the player bought, while a resistance spin box holds the **total** (derived base plus
    bought delta), which runs much higher — a high-Stamina character's Toughness total
    easily passes an ability's ceiling.
    """

    min: int = -5
    max: int = 30


#: Fallbacks for a ``costs.json`` (a mod's, or an older one) that omits ``trait_ranges``.
DEFAULT_TRAIT_RANGES: dict[str, TraitRange] = {
    "ability": TraitRange(min=-5, max=30),
    "resistance": TraitRange(min=-5, max=60),
}


@dataclass(frozen=True)
class Costs:
    """The parsed contents of ``costs.json``."""

    traits: TraitCosts
    power_level: PowerLevelRules
    trait_ranges: dict[str, TraitRange] = field(default_factory=dict)

    def trait_range(self, family: str) -> TraitRange:
        """The spin-box range for a trait *family* (``"ability"``, ``"resistance"``, …),
        falling back to :data:`DEFAULT_TRAIT_RANGES` and then to a plain default."""

        if family in self.trait_ranges:
            return self.trait_ranges[family]
        return DEFAULT_TRAIT_RANGES.get(family, TraitRange())


# --- System rules: the trait-key strings and paired caps the resolvers
#     reference (from ``system.json``). ----------------------------------------
@dataclass(frozen=True)
class TraitKeys:
    """The trait-key strings the resolvers reference, from ``system.json``.

    Keeping them in data means a mod can rename or re-point the combat traits
    (e.g. an Attack ability, the active-defence resistances) without a code change.
    """

    attack: str = "ATK"
    defense: str = "DEF"
    dodge: str = "DODGE"
    toughness: str = "TOUGHNESS"


@dataclass(frozen=True)
class PairedCap:
    """A Power-Level cap that sums two resistance traits (``docs/mm-core-mechanics.md`` §7).

    ``cap`` names the :class:`PowerLevelCap` in ``costs.json``; ``traits`` are the two
    resistance keys whose totals are summed against it; ``label`` is the message prefix.
    """

    cap: str
    traits: tuple[str, ...]
    label: str


@dataclass(frozen=True)
class RangeDistance:
    """How far a ranged effect reaches, as a distance rank plus its range increments.

    An effect whose range resolves to ``range_value`` reaches distance rank
    ``rank_source`` (the effect's own effective rank, or a fixed ``rank`` when the
    effect doesn't scale that way) shifted by ``offset``. ``steps`` are the further
    rank shifts of each range increment and ``step_labels`` names them — the default
    ``(0, 1, 2)`` / short-medium-long is the ×1/×2/×4 progression, the same idiom
    :func:`mm_companion.core.rules.speed_columns` uses for walk/dash/run.

    The default lives in ``system.json``; an effect in ``effects.json`` may carry its
    own ``rangeDistance`` block overriding whichever keys it names — the effects whose
    reach isn't a function of their rank say so there rather than in code.
    """

    rank_source: str = "effect_rank"
    rank: int | None = None
    offset: int = 0
    steps: tuple[int, ...] = (0, 1, 2)
    step_labels: tuple[str, ...] = ("short", "medium", "long")
    range_value: str = "Ranged"


@dataclass(frozen=True)
class DerivedTrait:
    """A numeric stat a player can be asked to check that isn't a bought trait.

    The abilities, resistances and skills are enumerated by their own data files;
    these are the leftovers a check can still name — the derived Defence aggregate,
    Initiative — listed in ``system.json`` rather than hardcoded in the picker.
    """

    key: str
    label: str


@dataclass(frozen=True)
class SystemRules:
    """System-level rule references (from ``system.json``).

    The trait keys, formulas, sentinel scope strings, and structural modifier ids the
    ``core.rules`` resolvers read instead of hardcoding — so a mod can retune them.
    """

    default_initiative_ability: str = "AGL"
    defense_dc_base: int = 10
    heroic_budget_divisor: int = 2
    #: What a natural 20 adds to the resistance DC of the effect it lands, and what a
    #: natural 1 that still hits gives the *target* on their resistance check.
    critical_effect_bonus: int = 5
    critical_miss_resistance_bonus: int = 5
    trait_keys: TraitKeys = field(default_factory=TraitKeys)
    paired_caps: tuple[PairedCap, ...] = ()
    unscoped_scope_values: tuple[str, ...] = ("All checks",)
    alternate_effect_modifier: str = "alternate_effect"
    linked_modifier: str = "linked"
    ranged_distance: RangeDistance = field(default_factory=RangeDistance)
    derived_traits: tuple[DerivedTrait, ...] = ()
    #: Which effect's resistance ladder is *the* damage ladder — the rungs the GM's
    #: quick-damage buttons walk. An id, not a rule, so a ruleset that calls its
    #: damage effect something else retargets the whole control from data.
    damage_effect: str = "damage"


# --- Measurements & movement: the rank ↔ real-world conversion tables, the
#     Size Table, and movement constants. --------------------------------------

#: Splits a camelCase JSON key so it can be matched to its snake_case dataclass field.
_CAMEL_TO_SNAKE = re.compile(r"(?<!^)(?=[A-Z])")


@dataclass(frozen=True)
class SizeRow:
    """One row of the Size Table (from ``measurements.json``'s ``sizeTable``).

    Maps a size rank (a Growth rank is a positive shift, Shrinking a negative one)
    to its size category and the combat/skill modifiers that size confers.
    """

    size_category: str
    size_rank: int
    spaces: float
    reach: int
    defense_mod: int
    damage_mod: int
    toughness_mod: int
    speed_mod: int
    intimidation_mod: int
    stealth_mod: int

    def modifier(self, column: str) -> int:
        """This row's value for a Size Table column *named as the JSON names it*.

        ``"defenseMod"`` → :attr:`defense_mod`. The translation lives here rather than
        at the call site because :class:`SizeEffect` quotes the author's own column
        name, and an unknown one reads as 0 rather than raising — a mod naming a column
        this ruleset does not have grants nothing, exactly as an unregistered applier
        kind does.
        """

        attribute = _CAMEL_TO_SNAKE.sub("_", column).lower()
        return int(getattr(self, attribute, 0) or 0)


@dataclass(frozen=True)
class SizeEffect:
    """Which trait one Size Table column modifies (``measurements.json``'s ``sizeEffects``).

    The seam that keeps size out of Python: ``column`` names a field of :class:`SizeRow`,
    ``category`` one of the applier categories (``ability``/``resistance``/``skill``) and
    ``target`` the trait key or skill name it lands on. A ruleset that calls its defence
    something else, or maps a column somewhere else entirely, edits the data.

    Two Size Table columns deliberately have no entry, because neither modifies a
    trait: ``speedMod`` applies to the base ground movement rank
    (:func:`~mm_companion.core.rules.base_ground_speed_rank`), and ``damageMod`` applies
    to an effect's rank (:attr:`Measurements.size_rank_column`).
    """

    column: str
    category: str
    target: str


@dataclass(frozen=True)
class Measurements:
    """The rank → real-world measurement conversion tables (from ``measurements.json``).

    Both the imperial and metric labels are parsed so a later settings toggle need
    only pass a different ``system``; the UI shows imperial for now. ``label`` returns
    the book's own display string for a rank/column (e.g. distance rank 3 →
    ``"60 feet"``), or ``""`` when the rank is outside the tabulated −5…30 range.
    ``size_row`` returns the :class:`SizeRow` for a size rank (clamped to the table's
    range), driving Growth/Shrinking's derived combat modifiers, and ``size_effects``
    says which trait each of those columns actually modifies (:class:`SizeEffect`).

    ``size_rank_column`` names the one column that modifies no trait at all: how much
    size raises the *rank* of an effect the character's own body drives, which is a
    different kind of thing and is read by
    :func:`~mm_companion.core.rules.effect_size_rank_shift`.

    ``distance_m`` returns the normalized numeric metric distance (metres) for a rank —
    the numeric sibling of the ``distance`` label — so a per-round distance can be
    converted to km/h; ``0.0`` when the rank is off-table.
    """

    by_rank: dict[int, dict[str, dict[str, str]]]  # rank -> system -> column -> label
    size_by_rank: dict[int, SizeRow] = field(default_factory=dict)
    distance_m_by_rank: dict[int, float] = field(default_factory=dict)
    size_effects: tuple[SizeEffect, ...] = ()
    size_rank_column: str = ""

    def label(self, column: str, rank: int, system: str = "imperial") -> str:
        return self.by_rank.get(rank, {}).get(system, {}).get(column, "")

    def distance_m(self, rank: int) -> float:
        """The numeric metric distance (metres) for a rank, or ``0.0`` if off-table."""
        return self.distance_m_by_rank.get(rank, 0.0)

    def size_row(self, size_rank: int) -> SizeRow | None:
        """The size-table row for ``size_rank``, clamped to the tabulated range."""
        if not self.size_by_rank:
            return None
        lo = min(self.size_by_rank)
        hi = max(self.size_by_rank)
        return self.size_by_rank.get(max(lo, min(hi, size_rank)))

    def size_rank_for_category(self, category: str) -> int | None:
        """The size rank of a named size category (``"Medium"`` → 0), or ``None``."""
        for rank, row in self.size_by_rank.items():
            if row.size_category == category:
                return rank
        return None


@dataclass(frozen=True)
class Movement:
    """Ground-movement and turn-timing constants (from ``movement.json``).

    ``base_ground_speed_rank`` is a character's default walking speed rank; the
    walking / dashing / running columns are the measurements-table distance at that
    rank plus ``walk_rank_step`` / ``dash_rank_step`` / ``run_rank_step``.
    ``round_seconds`` (6) converts a per-round distance into km/h.

    ``run_is_ground_only`` drops that third column from every other mode: running is a
    ground manoeuvre, so a flier moves at its speed or dashes at double it and has no
    run distance to print.

    ``ground_mode`` names the movement mode everybody has — the one
    ``base_ground_speed_rank`` seeds and the Speed effect feeds, and ``ground_label``
    is what that line is called on the sheet. Every other mode's line is built from its
    grants alone (:func:`~mm_companion.core.rules.speed_lines`).
    """

    base_ground_speed_rank: int = 1
    walk_rank_step: int = 0
    dash_rank_step: int = 1
    run_rank_step: int = 2
    run_is_ground_only: bool = True
    round_seconds: int = 6
    ground_mode: str = "ground"
    ground_label: str = "Ground speed"


# --- Equipment: the gear catalog, its grouping axis, and the rules constants
#     that govern the second currency (from ``equipment.json``). ---------------
@dataclass(frozen=True)
class EquipmentModifierRef:
    """One extra or flaw a catalog entry applies to its effect.

    ``modifier`` is a bare :class:`Modifier` id — the JSON writes it namespaced
    (``"modifiers:accurate"``) and the loader unqualifies it; ``rank`` is its rank
    where it takes one; ``note`` is the printed qualification the table gives
    ("Ballistic damage only") and is display copy, not a rule.
    """

    modifier: str
    rank: int | None = None
    note: str = ""
    #: Unrecognised JSON keys (e.g. from a mod), retained rather than dropped.
    extra: dict = field(default_factory=dict, compare=False)


@dataclass(frozen=True)
class EquipmentEffectRef:
    """One base effect an item is built from, as named by a catalog entry.

    ``effect`` is a bare :class:`Effect` id — the JSON writes it namespaced
    (``"effects:damage"``) and the loader unqualifies it, so the catalog reads as
    the design reference does while the record carries something that indexes
    straight into :attr:`GameData.effects`.

    The remaining fields are the qualities an item's printed line fixes: the
    ``rank`` it comes at, whether its Damage is ``strength_based``, its
    ``descriptors``, and the effect's own configuration — ``config`` for the
    keyed form an effect's config fields take, ``configuration`` for a named
    preset ("stun", "toxin"), plus the ``resistance`` it is checked against and
    the condition ``degrees`` an Affliction inflicts. Nothing is *built* here;
    turning these into a real :class:`~mm_companion.core.powers.Power` is the
    equipment rules layer's job.
    """

    effect: str
    rank: int | None = None
    strength_based: bool = False
    descriptors: tuple[str, ...] = ()
    config: dict = field(default_factory=dict)
    configuration: str = ""
    resistance: str = ""
    degrees: tuple[tuple[str, ...], ...] = ()
    #: What to call *this* effect on a multi-effect item — a tank's "Cannon". Empty
    #: for the ordinary case, where the base effect's own name says everything there
    #: is to say; see :attr:`~mm_companion.core.powers.PowerEffectInstance.label`.
    label: str = ""
    #: Extras and flaws carried by this effect alone, on top of the entry-wide
    #: :attr:`EquipmentEntry.modifiers` every effect gets. An item whose effects each
    #: want their own (a vehicle's two weapons: one Area, one Multiattack) has no other
    #: way to say so.
    modifiers: tuple[EquipmentModifierRef, ...] = ()
    #: Unrecognised JSON keys (e.g. from a mod), retained rather than dropped.
    extra: dict = field(default_factory=dict, compare=False)


@dataclass(frozen=True)
class CriticalProfile:
    """An item's critical-hit profile: the natural rolls that threaten, and the
    ranks of Improved Critical that ``threat_range`` already reflects."""

    threat_range: tuple[int, int] = (20, 20)
    improved_critical_ranks: int = 0


@dataclass(frozen=True)
class EquipmentEntry:
    """One item of the equipment catalog (from ``equipment.json``).

    ``cost`` is the item's printed Equipment Point price — ``None`` for a
    ``cost_kind`` of ``"built"``, which has no single printed number because the
    item is assembled from a trait table. Equipment Points are a *second
    currency*: they are bought by ranks of the Equipment advantage and never mix
    with Power Points (see :class:`EquipmentRules`).

    ``effects``/``modifiers`` are the build the item's printed line describes,
    ``grants`` the advantages it hands its wielder (``{"advantages": (...)}``,
    ids unqualified), ``critical`` its threat range, and ``patterns`` the
    behavioural tags (``"attack_item"``, ``"passive_trait_item"``, …) named in
    ``docs/mm-equipment-design.md`` §2.

    ``implementation`` is deliberately an open bag rather than typed fields: it
    is the per-item mechanical detail the engine grows into over time (ranges,
    charges, attachment hosts, ammo modes), and its keys are as varied as the
    catalog. Reading one is a matter for the phase that needs it; retaining it
    whole is what lets that happen without another data migration.
    """

    id: str
    name: str
    category: str = ""
    subcategory: str = ""
    cost: int | None = None
    cost_kind: str = "fixed"
    cost_note: str = ""
    description: str = ""
    effects: tuple[EquipmentEffectRef, ...] = ()
    modifiers: tuple[EquipmentModifierRef, ...] = ()
    grants: dict = field(default_factory=dict)
    critical: CriticalProfile | None = None
    patterns: tuple[str, ...] = ()
    implementation: dict = field(default_factory=dict)
    #: Unrecognised JSON keys (e.g. from a mod), retained rather than dropped.
    extra: dict = field(default_factory=dict, compare=False)


@dataclass(frozen=True)
class EquipmentCategory:
    """One group the Equipment block sorts its cards into, from
    ``_meta.equipmentCategories`` — in display order.

    ``id`` matches an :class:`EquipmentEntry`'s ``category``; ``title`` is the
    heading shown above that group; ``description`` is the category's own
    one-liner from ``_meta.categoryKey``.

    The same split the conditions layer makes between a rules fact and a display
    axis: the category *is* a rules fact here (a weapon is not armour, and a
    weapon may not be dragged into the armour group), but which order the groups
    come in and what they are called is presentation, so it lives in ``_meta``
    where a mod can add a row rather than having its items folded into someone
    else's group.
    """

    id: str
    title: str
    description: str = ""


@dataclass(frozen=True)
class EquipmentRules:
    """The constants governing equipment as a whole, from ``equipment.json``'s ``_meta``.

    ``points_per_advantage_rank`` × the character's rank of the ``advantage``
    is the Equipment Point budget. ``currency_name``/``currency_abbreviation``
    name that second currency for the UI.

    ``stacking_targets`` are the stats the no-stacking rule governs: equipment
    contributions to one of them resolve as ``max()`` among themselves and
    ``max()`` again against non-equipment sources — never a sum of the two
    maxima. ``cost_kinds`` maps each ``cost_kind`` to its explanation.

    ``material_toughness`` is the breakage table from
    ``_meta.strengthBasedDamage.breakage``: how much Toughness an item made of a
    given material has, which is the most Strength it can carry before it breaks
    in use (``docs/mm-equipment-design.md`` §4). Which material an item is made of
    is its own ``implementation.material``. Empty for a ruleset that declares
    none, which simply means nothing is ever warned about — the warning is a
    courtesy, not a rule the engine enforces. ``breakage_rule`` is that section's
    own prose, shown as the warning's tooltip.
    """

    currency_name: str = "Equipment Point"
    currency_abbreviation: str = "EP"
    advantage: str = "equipment"
    points_per_advantage_rank: int = 5
    stacking_rule: str = ""
    stacking_targets: tuple[str, ...] = ()
    cost_kinds: dict = field(default_factory=dict)
    material_toughness: dict = field(default_factory=dict)
    breakage_rule: str = ""
    #: Unrecognised ``_meta`` keys, retained rather than dropped.
    extra: dict = field(default_factory=dict, compare=False)


# --- Vehicles: the stock platforms, the trait table their sizes come from, and
#     the two catalogs a custom one is assembled out of (from ``equipment.json``). ---
@dataclass(frozen=True)
class VehicleClass:
    """One medium a vehicle travels through, from ``_meta.vehicleClasses``.

    ``effect`` is the :class:`Effect` id a vehicle of this class measures its printed
    Speed rank in — ``flight`` for an aircraft, ``swimming`` for a boat — so a
    vehicle's speed reaches the sheet as the movement effect it actually is rather
    than as a bare number. Empty for a class that names no default (``exotic``): such
    a vehicle carries its own :attr:`StockVehicle.movement` instead.
    """

    id: str
    title: str
    effect: str = ""


@dataclass(frozen=True)
class VehicleSizeRow:
    """One row of the vehicle Size table: the baselines a size rank confers.

    Size is chosen first when a vehicle is built, because it sets ``strength``,
    ``toughness`` and ``defense``; everything above those baselines is what the
    build pays for (``docs/mm-equipment-design.md`` §5).
    """

    size_rank: int
    strength: int
    toughness: int
    defense: int
    examples: tuple[str, ...] = ()


@dataclass(frozen=True)
class VehicleSizeExtension:
    """What each size rank *past* the printed table adds (``_meta.vehicleSizeExtension``).

    The table stops at rank 5 and the book extends it arithmetically rather than
    printing more rows, so the deltas are data and :func:`~mm_companion.core.rules.vehicle_size_row`
    applies them.
    """

    strength: int = 2
    toughness: int = 1
    defense: int = -1
    note: str = ""


@dataclass(frozen=True)
class VehicleWeapon:
    """One weapon a stock vehicle mounts.

    ``name`` is what the table calls it ("Cannon"), which becomes the built effect's
    :attr:`~mm_companion.core.powers.PowerEffectInstance.label` so a tank's two rolls
    are told apart. ``modifiers`` are that weapon's own — the Area rank of a cannon is
    a fact about the cannon, not about the tank.
    """

    name: str
    effect: str
    rank: int = 0
    modifiers: tuple[EquipmentModifierRef, ...] = ()
    note: str = ""


@dataclass(frozen=True)
class StockVehicle:
    """One vehicle off the printed table (``stockVehicles``).

    A vehicle is a *platform*: five traits (Size, Strength, Speed, Defense, Toughness)
    rather than a bundle of effects, which is why its card shows a trait grid where an
    item's shows a game-term table. What it *does* still becomes a real
    :class:`~mm_companion.core.powers.Power` — its movement and its weapons — so its
    speed reaches the Speed readout and its cannon rolls, exactly as a power's would.
    That translation is :func:`vehicle_entry`.

    ``cost`` is the printed Equipment Point price, ``None`` for the two whose
    ``cost_kind`` is ``"built"``; ``cost_formula`` is the printed recipe those give
    instead. ``defenses`` are the modifiers on its Toughness (a tank's Impervious 4),
    which are trait annotations rather than effects and so are drawn on the grid.
    """

    id: str
    name: str
    vehicle_class: str = ""
    size: int = 0
    strength: int = 0
    speed: int | None = None
    defense_modifier: int = 0
    toughness: int = 0
    cost: int | None = None
    cost_kind: str = "fixed"
    cost_formula: str = ""
    defenses: tuple[EquipmentModifierRef, ...] = ()
    weapons: tuple[VehicleWeapon, ...] = ()
    movement: EquipmentEffectRef | None = None
    patterns: tuple[str, ...] = ()
    variants: str = ""
    notes: str = ""
    #: Unrecognised JSON keys (e.g. from a mod), retained rather than dropped.
    extra: dict = field(default_factory=dict, compare=False)


@dataclass(frozen=True)
class PlatformFeature:
    """A named 1-point Feature a platform can carry (``vehicleFeatures``).

    ``repeatable`` marks the ones bought more than once, and a repeatable Feature with
    a defined escalation carries it: ``dc_increase_per_extra_rank`` and
    ``max_dc_increase`` (``docs/mm-equipment-design.md`` §2 pattern K), stored rather
    than branched on per feature name.
    """

    id: str
    name: str
    cost: int = 1
    repeatable: bool = False
    description: str = ""
    dc_increase_per_extra_rank: int = 0
    max_dc_increase: int = 0
    #: Unrecognised JSON keys (e.g. from a mod), retained rather than dropped.
    extra: dict = field(default_factory=dict, compare=False)


@dataclass(frozen=True)
class VehicleModifier:
    """Durable / Minion / Summonable — the rare modifiers that price the *advantage*.

    Structurally unlike every other modifier in the app: they change what a rank of
    the Equipment advantage costs in **Power Points** for the ranks allocated to this
    vehicle, not what the vehicle costs in Equipment Points
    (``docs/mm-equipment-design.md`` §5). That is why they are their own catalog and
    never join :meth:`GameData.modifier_catalog` — folding them in would let a cost
    engine spend one currency as the other.
    """

    id: str
    name: str
    cost: int = 0
    cost_kind: str = "per_rank_flat"
    description: str = ""
    #: Unrecognised JSON keys (e.g. from a mod), retained rather than dropped.
    extra: dict = field(default_factory=dict, compare=False)


@dataclass(frozen=True)
class VehicleRules:
    """The constants a vehicle is read and built against (``equipment.json``'s ``_meta``).

    ``category`` is the equipment category vehicles are filed under, named in the data
    rather than in Python so a ruleset can move them. ``combat`` keeps the §5 Combat
    prose (the moving/stationary Defense Class rules) for the card's tooltips.
    """

    category: str = "vehicle"
    classes: tuple[VehicleClass, ...] = ()
    size_table: tuple[VehicleSizeRow, ...] = ()
    size_extension: VehicleSizeExtension = field(default_factory=VehicleSizeExtension)
    combat: dict = field(default_factory=dict)

    def vehicle_class(self, class_id: str) -> VehicleClass | None:
        """The :class:`VehicleClass` with this id, or ``None``."""

        return next((c for c in self.classes if c.id == class_id), None)


# --- Installations: the second kind of platform, and a simpler one. Size and
#     Toughness off their own table, Features, and effects (from ``equipment.json``). ---
@dataclass(frozen=True)
class InstallationSizeRow:
    """One row of the installation Size table: what a size rank costs.

    Unlike a vehicle's, an installation's size confers no trait baselines — it is
    priced and nothing more (``docs/mm-equipment-design.md`` §6). The pivot is the
    free rank (a house), whose ``cost`` is ``0``; below it the cost is negative and
    the points come *back*.
    """

    size_rank: int
    cost: int = 0
    examples: tuple[str, ...] = ()


@dataclass(frozen=True)
class InstallationRules:
    """What an installation starts with for free, and what more of it costs.

    ``free_size_rank`` and ``free_toughness`` are the starting points every
    installation gets without paying (rank 5, Toughness 6); ``toughness_per_point``
    is how much Toughness one Equipment Point buys above that. ``category`` is the
    equipment category installations file under, named in the data rather than in
    Python for the reason :attr:`VehicleRules.category` is.

    ``toughness_pl_multiple`` and ``impervious_pl_multiple`` are the installation's
    own Power Level cap pair, and it is genuinely a different pair from a character's:
    an installation has no defences other than its Toughness, so that Toughness may
    reach **twice** the series Power Level while Impervious stays capped at the Power
    Level itself.
    """

    category: str = "installation"
    size_table: tuple[InstallationSizeRow, ...] = ()
    free_size_rank: int = 5
    free_toughness: int = 6
    toughness_per_point: int = 2
    toughness_pl_multiple: int = 2
    impervious_pl_multiple: int = 1
    #: Which modifier the Impervious cap governs, named in the data rather than in
    #: Python — the cap is a rule, but *Impervious* is content.
    impervious_modifier: str = "impervious"
    note: str = ""

    def size_row(self, size_rank: int) -> InstallationSizeRow | None:
        """The row for this size rank, or ``None`` when the table does not print one."""

        return next((row for row in self.size_table if row.size_rank == size_rank), None)


@dataclass(frozen=True)
class StockInstallation:
    """One installation off the printed table (``stockInstallations``).

    A platform like a vehicle, with far less to it: Size, Toughness, and a list of
    Features. It carries no movement and no weapons, so the
    :class:`~mm_companion.core.powers.Power` :func:`installation_entry` gives it is
    usually empty — which is exactly right, since what an installation *does* is its
    Features rather than its effects.
    """

    id: str
    name: str
    size: int = 0
    toughness: int = 0
    cost: int | None = None
    cost_kind: str = "fixed"
    cost_formula: str = ""
    features: tuple[str, ...] = ()
    patterns: tuple[str, ...] = ()
    notes: str = ""
    #: Unrecognised JSON keys (e.g. from a mod), retained rather than dropped.
    extra: dict = field(default_factory=dict, compare=False)


# --- Derived readouts & declarative blocks: per-effect Tier-5 readouts and the
#     data-described sheet blocks a data-only mod can add. ---------------------
@dataclass(frozen=True)
class Readout:
    """A derived, display-only Tier-5 readout for an effect (from ``effect_readouts.json``).

    ``kind`` selects how :func:`mm_companion.core.rules.effect_readout_rows` renders it
    (``"size_table"``, ``"state"``, ``"measure_offsets"``, ``"thresholds"``,
    ``"config_flag"``, ``"points_per_rank"``); ``label`` is the row label; ``data``
    holds the kind-specific parameters (the byRank map, the offset rows, ...). These
    are computed information, never editable — see ``docs/mm-powers-ui-design.md`` §2 Tier 5.
    """

    kind: str
    label: str = ""
    data: dict = field(default_factory=dict)


@dataclass(frozen=True)
class BlockFieldSpec:
    """One row of a data-described (declarative) sheet block.

    ``kind`` selects how the generic declarative block renders the row:

    - ``"text"`` — an editable line backed by ``Character.profile[key]``.
    - ``"label"`` — a static, read-only line showing ``text``.

    Unknown kinds fall back to a static label so a mod can add new kinds (with a
    matching UI handler) without the loader rejecting the row.
    """

    key: str = ""
    label: str = ""
    kind: str = "text"
    text: str = ""
    #: Unrecognised JSON keys (e.g. from a mod), retained rather than dropped.
    extra: dict = field(default_factory=dict, compare=False)


@dataclass(frozen=True)
class BlockSpec:
    """A data-described sheet block a mod can add without shipping any Python.

    The UI turns each spec into a generic declarative block (a titled group of
    field/label rows) and registers it through the same block registry as the
    built-in blocks, so it appears on the sheet and can be floated / hidden /
    rearranged like any other. ``row``/``col`` place it in the default
    arrangement; the ``*_width``/``*_height`` bounds feed its size constraints
    (``0``/omitted means unconstrained).
    """

    id: str
    title: str = ""
    row: int = 0
    col: int = 0
    fields: tuple[BlockFieldSpec, ...] = ()
    min_width: int = 0
    min_height: int = 0
    max_width: int = 0
    max_height: int = 0
    #: Unrecognised JSON keys (e.g. from a mod), retained rather than dropped.
    extra: dict = field(default_factory=dict, compare=False)


# --- Aggregate: the single record bundling every parsed list above, with the
#     merged-catalog lookups the resolvers walk. -------------------------------
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

    ``duration_action_floor`` maps a resulting duration to the minimum action its
    effect must take (a Sustained effect needs at least a free action to toggle on
    and maintain), so :mod:`mm_companion.core.rules` can raise a below-floor action
    in the game-terms summary without hardcoding the rule.
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
    duration_action_floor: dict[str, str] = field(default_factory=dict)
    effect_readouts: dict[str, tuple[Readout, ...]] = field(default_factory=dict)
    movement: Movement = field(default_factory=Movement)
    system: SystemRules = field(default_factory=SystemRules)
    #: Data-described blocks a mod contributes via ``blocks.json`` (empty for the
    #: base ruleset, whose blocks are built-in Python widgets).
    blocks: tuple[BlockSpec, ...] = ()
    #: How the Conditions block groups its chips, from ``conditions.json``'s
    #: ``_meta.sheetSections`` — in display order.
    condition_categories: tuple[ConditionCategory, ...] = ()
    #: How a "+" menu splits the addable catalog into submenus, from
    #: ``conditions.json``'s ``_meta.conditionGroups`` — in display order. Empty
    #: when a ruleset declares none, which means "offer the flat list".
    condition_groups: tuple[ConditionGroup, ...] = ()
    #: The gear catalog, from ``equipment.json``.
    equipment: tuple[EquipmentEntry, ...] = ()
    #: How the Equipment block groups its cards, from ``equipment.json``'s
    #: ``_meta.equipmentCategories`` — in display order.
    equipment_categories: tuple[EquipmentCategory, ...] = ()
    #: The Equipment Point currency and the no-stacking rule's targets.
    equipment_rules: EquipmentRules = field(default_factory=EquipmentRules)
    #: The printed stock vehicles, from ``equipment.json``'s ``stockVehicles``.
    vehicles: tuple[StockVehicle, ...] = ()
    #: The same vehicles as ordinary catalog entries, so every seam that already
    #: knows how to price, build and draw a piece of gear handles one unchanged.
    #: Derived at parse time by :func:`vehicle_entry` — see :meth:`equipment_catalog`.
    vehicle_entries: tuple[EquipmentEntry, ...] = ()
    #: The Features a platform can carry, from ``vehicleFeatures``.
    vehicle_features: tuple[PlatformFeature, ...] = ()
    #: Durable / Minion / Summonable — the modifiers that price the *advantage*.
    vehicle_modifiers: tuple[VehicleModifier, ...] = ()
    #: The vehicle size table, the class → movement-effect axis, and the §5 constants.
    vehicle_rules: VehicleRules = field(default_factory=VehicleRules)
    #: The printed stock installations, from ``equipment.json``'s ``stockInstallations``.
    installations: tuple[StockInstallation, ...] = ()
    #: The same installations as ordinary catalog entries — see :meth:`equipment_catalog`.
    installation_entries: tuple[EquipmentEntry, ...] = ()
    #: The Features an installation can carry, from ``installationFeatures``.
    installation_features: tuple[PlatformFeature, ...] = ()
    #: The installation size table, its free starting points, and its own PL cap pair.
    installation_rules: InstallationRules = field(default_factory=InstallationRules)

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

    def condition_catalog(self) -> dict[str, Condition]:
        """A single ``id -> Condition`` lookup, for the condition resolver in ``rules``."""

        return {c.id: c for c in self.conditions}

    def equipment_catalog(self) -> dict[str, EquipmentEntry]:
        """A single ``id -> EquipmentEntry`` lookup, for the equipment rules layer.

        Both kinds of **platform** are in it. A stock vehicle or installation is a
        different *shape* of record — bought traits rather than a bundle of effects —
        but it is bought, priced, worn and drawn as one more thing off the catalog, so
        it enters the rules layer through the same door (:attr:`vehicle_entries`,
        :attr:`installation_entries`). Everything that already works
        on an entry then works on a vehicle: the picker lists it under its category,
        :func:`~mm_companion.core.rules.build_item_from_entry` gives it a build, and
        :func:`~mm_companion.core.rules.item_is_stock` keeps it at its printed price.
        Its platform traits are read from :meth:`vehicle_catalog` by the one layer that
        needs them.

        Items win an id collision: a ruleset that ships a platform and an item under one
        id has a bug, and the item is the older, larger catalog.
        """

        catalog: dict[str, EquipmentEntry] = {e.id: e for e in self.vehicle_entries}
        catalog.update({e.id: e for e in self.installation_entries})
        catalog.update({e.id: e for e in self.equipment})
        return catalog

    def vehicle_catalog(self) -> dict[str, StockVehicle]:
        """A single ``id -> StockVehicle`` lookup, for the platform traits a card shows."""

        return {v.id: v for v in self.vehicles}

    def vehicle_feature_catalog(self) -> dict[str, PlatformFeature]:
        """A single ``id -> PlatformFeature`` lookup over the vehicle Features."""

        return {f.id: f for f in self.vehicle_features}

    def installation_catalog(self) -> dict[str, StockInstallation]:
        """A single ``id -> StockInstallation`` lookup, for the traits a card shows."""

        return {i.id: i for i in self.installations}

    def installation_feature_catalog(self) -> dict[str, PlatformFeature]:
        """A single ``id -> PlatformFeature`` lookup over the installation Features."""

        return {f.id: f for f in self.installation_features}


# ===========================================================================
# Parsing — turn each raw JSON dict into the typed records above. One ``_parse_*``
# per record group; all mechanical (read keys, coerce types), so kept terse.
# ===========================================================================


def _extras(raw: dict, *known: str) -> dict:
    """Any keys of *raw* the engine doesn't recognise, so a mod's extra JSON
    fields are retained on the record rather than silently dropped."""

    return {k: v for k, v in raw.items() if k not in known}


def _parse_field(f: dict) -> Field:
    return Field(
        key=f["key"],
        label=f["label"],
        primary=bool(f.get("primary", False)),
        extra=_extras(f, "key", "label", "primary"),
    )


def _parse_ability(a: dict) -> Ability:
    return Ability(
        key=a["key"],
        name=a["name"],
        abbr=a.get("abbr", ""),
        derived=bool(a.get("derived", False)),
        extra=_extras(a, "key", "name", "abbr", "derived"),
    )


def _parse_resistance(r: dict) -> Resistance:
    return Resistance(
        key=r["key"],
        name=r["name"],
        ability=r.get("ability", ""),
        abbr=r.get("abbr", ""),
        derived=bool(r.get("derived", False)),
        extra=_extras(r, "key", "name", "ability", "abbr", "derived"),
    )


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
        extra=_extras(c, "key", "label", "kind", "options", "default", "min", "max"),
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
        specialized_cost=bool(s.get("specializedCost", False)),
        extra=_extras(
            s,
            "name",
            "ability",
            "focused",
            "id",
            "trainedOnly",
            "action",
            "specializations",
            "focuses",
            "description",
            "specializedCost",
        ),
    )


def _parse_parameter(raw: dict | None, initiative_choice: tuple[str, ...]) -> ParameterSpec | None:
    """Build the advantage's :class:`ParameterSpec`, or ``None`` if it takes no subject.

    An explicit ``parameter`` object wins; otherwise Alternate Initiative's
    ``initiativeAbilityChoice`` is synthesised into an ability-choice spec so the
    one legacy field keeps driving both the picker and the initiative math.
    """

    if raw is not None:
        return ParameterSpec(
            label=raw.get("label", ""),
            kind=raw.get("kind", "text"),
            options=tuple(raw.get("options", ())),
            options_from=raw.get("optionsFrom", ""),
        )
    if initiative_choice:
        return ParameterSpec(
            label="Initiative",
            kind="choice",
            options=initiative_choice,
            options_from="abilities",
        )
    return None


def _parse_advantage(a: dict) -> Advantage:
    # Accept the rich ``types`` list, falling back to a legacy singular ``type``.
    types = tuple(a["types"]) if "types" in a else tuple(t for t in (a.get("type"),) if t)
    initiative_choice = tuple(a.get("initiativeAbilityChoice", ()))
    return Advantage(
        name=a["name"],
        ranked=bool(a["ranked"]),
        description=a.get("description", ""),
        id=a.get("id", ""),
        types=types,
        max_rank=a.get("maxRank"),
        max_rank_kind=a.get("maxRankKind", "none"),
        focused=bool(a.get("focused", False)),
        initiative_bonus_per_rank=int(a.get("initiativeBonusPerRank", 0)),
        initiative_ability_choice=initiative_choice,
        skill_bonus_per_rank=int(a.get("skillBonusPerRank", 0)),
        skill_bonus_target=a.get("skillBonusTarget", ""),
        parameter=_parse_parameter(a.get("parameter"), initiative_choice),
        extra=_extras(
            a,
            "name",
            "ranked",
            "description",
            "id",
            "types",
            "type",
            "maxRank",
            "maxRankKind",
            "focused",
            "initiativeBonusPerRank",
            "initiativeAbilityChoice",
            "skillBonusPerRank",
            "skillBonusTarget",
            "parameter",
        ),
    )


def _parse_condition_parameter_option(raw) -> ConditionParameterOption:
    """One ``options`` entry: a bare string, or an object carrying the per-choice flags."""

    if isinstance(raw, dict):
        return ConditionParameterOption(
            value=str(raw.get("value", "")),
            unscoped=bool(raw.get("unscoped", False)),
            specific_kind=str(raw.get("specificKind", "")),
        )
    return ConditionParameterOption(value=str(raw))


def _parse_condition_parameter(raw: dict | None) -> ConditionParameter | None:
    if not raw:
        return None
    specs = tuple(_parse_condition_parameter_option(o) for o in raw.get("options", ()))
    return ConditionParameter(
        type=raw.get("type", ""),
        required=bool(raw.get("required", False)),
        label=raw.get("label", ""),
        help=raw.get("help", ""),
        options=tuple(spec.value for spec in specs),
        option_specs=specs,
    )


def _parse_debilitation(raw: dict | None) -> Debilitation | None:
    if not raw:
        return None
    cascade = {trait: tuple(conds) for trait, conds in raw.get("cascade", {}).items()}
    return Debilitation(cascade=cascade, notes=raw.get("notes", ""))


def _parse_speed_rank_mod(raw: int | str | None) -> int | None:
    """``"zero"`` → 0, an int passes through, absent → ``None`` (no movement mod)."""

    if raw is None:
        return None
    if raw == "zero":
        return 0
    return int(raw)


def _parse_defense_mod(raw: dict | None) -> DefenseMod | None:
    if not raw:
        return None
    return DefenseMod(defense=raw.get("defense", ""), dodge=raw.get("dodge", ""))


def _parse_attack_mods(raw: dict | None) -> AttackMods | None:
    if not raw:
        return None
    return AttackMods(
        own_close=int(raw.get("ownCloseAttack", 0)),
        incoming_close=int(raw.get("incomingCloseAttack", 0)),
        incoming_ranged=int(raw.get("incomingRangedAttack", 0)),
    )


def _parse_resistance_mod(raw: dict | None) -> ResistanceMod | None:
    if not raw:
        return None
    return ResistanceMod(
        scope=raw.get("scope", ""),
        penalty_formula=raw.get("penaltyFormula", ""),
        best_outcome=raw.get("bestOutcome", ""),
    )


def _parse_stacking_rule(raw: dict | None) -> StackingRule | None:
    if not raw:
        return None
    return StackingRule(
        per_instance_penalty=int(raw.get("perInstancePenalty", 0)),
        applies_to=raw.get("appliesTo", ""),
        removed_per_recovery=int(raw.get("removedPerRecovery", 0)),
    )


def _parse_recovery_check(raw: dict | None) -> RecoveryCheck | None:
    if not raw:
        return None
    return RecoveryCheck(
        trait=raw.get("trait"),
        dc=raw.get("dc"),
        cadence=raw.get("cadence", ""),
        condition=raw.get("condition", ""),
        outcome=raw.get("outcome", ""),
    )


def _parse_condition(c: dict) -> Condition:
    return Condition(
        name=c["name"],
        description=c.get("description", ""),
        id=c.get("id", ""),
        category=c.get("category", ""),
        group=c.get("group", ""),
        tooltip=c.get("tooltip", ""),
        includes=tuple(c.get("includes", ())),
        supersedes=tuple(c.get("supersedes", ())),
        mechanisms=tuple(c.get("mechanisms", ())),
        stacking=bool(c.get("stacking", False)),
        parameter=_parse_condition_parameter(c.get("parameter")),
        debilitates=_parse_debilitation(c.get("debilitates")),
        effect=c.get("effect", ""),
        recovery=c.get("recovery", ""),
        penalty=c.get("penalty"),
        speed_rank_mod=_parse_speed_rank_mod(c.get("speedRankMod")),
        defense_mod=_parse_defense_mod(c.get("defenseMod")),
        attack_mods=_parse_attack_mods(c.get("attackMods")),
        resistance_mod=_parse_resistance_mod(c.get("resistanceMod")),
        stacking_rule=_parse_stacking_rule(c.get("stackingRule")),
        recovery_check=_parse_recovery_check(c.get("recoveryCheck")),
        random_table=tuple(
            RandomActionRow(range=r.get("range", ""), outcome=r.get("outcome", ""))
            for r in c.get("randomTable", ())
        ),
        trait_lost=bool(c.get("traitLost", False)),
    )


def _parse_speed_rank(raw) -> SpeedRank | None:
    """One ``speeds`` entry: a flat rank (``2``) or a ground expression (``"ground-1"``).

    ``None`` (or anything unparseable) means the tier grants no rate of its own.
    """

    if raw is None:
        return None
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        return SpeedRank(value=int(raw))
    text = str(raw).strip().replace(" ", "")
    if not text.startswith("ground"):
        return SpeedRank(value=int(text)) if text.lstrip("+-").isdigit() else None
    offset = text[len("ground") :]
    return SpeedRank(value=int(offset) if offset else 0, from_ground=True)


def _parse_config_field(c: dict) -> EffectConfigField:
    return EffectConfigField(
        key=c["key"],
        label=c.get("label", c["key"]),
        type=c.get("type", "select"),
        overrides=c.get("overrides"),
        multiselect_with=c.get("multiselectWith"),
        hidden_with=c.get("hiddenWith"),
        toggles=c.get("toggles"),
        source=c.get("source"),
        hides_field=bool(c.get("hidesField", False)),
        hint=c.get("hint", ""),
        min_value=int(c.get("min", 0)),
        max_value=int(c.get("max", 0)),
        default_value=int(c.get("default", 0)),
        show_when_points=int(c.get("showWhenPoints", 0)),
        options=tuple(
            ConfigOption(
                value=o["value"],
                label=o.get("label", o["value"]),
                cost_value=o.get("costValue"),
                flat=o.get("flat"),
                ranked=o.get("ranked"),
            )
            for o in c.get("options", [])
        ),
        alloc_options=tuple(
            AllocationOption(
                id=o["id"],
                label=o.get("label", o["id"]),
                tiers=tuple(int(t) for t in o.get("tiers", (1,))),
                per_note=o.get("perNote", ""),
                description=o.get("description", ""),
                speeds=tuple(_parse_speed_rank(s) for s in o.get("speeds", ())),
                tier_notes=tuple(o.get("tierNotes", ())),
            )
            for o in c.get("allocOptions", [])
        ),
        columns=tuple(
            RepeatableColumn(
                key=col["key"], label=col.get("label", col["key"]), type=col.get("type", "text")
            )
            for col in c.get("columns", [])
        ),
    )


def _parse_measure(raw: dict | None, effect_id: str = "") -> Measure | None:
    if not raw:
        return None
    return Measure(
        column=raw.get("column", "distance"),
        label=raw["label"],
        per_round=bool(raw.get("perRound", False)),
        mode=raw.get("mode", "") or effect_id,
    )


def _parse_integration(raw: dict, configurable: bool) -> Integration:
    """Build the typed :class:`Integration` from a ``statIntegration`` block.

    A base effect's, or a **modifier's**: an extra like Elongation's Striding ("longer
    strides grant ranks of Speed") is a stat effect in every sense except that it hangs
    off another effect, and it is read by the same appliers.

    A :class:`TraitBoost` is attached only for the trait-boosting effects — those
    the player targets (``configurable``, e.g. Enhanced Trait) or that carry a fixed
    ``target`` (e.g. Protection). ``affects`` is the ``"a|b"`` category string split
    into a set.

    ``apply``/``amountPerRank``/``amountFlat`` say which applier reads the record and
    what it is worth (see :mod:`mm_companion.core.rules.appliers`). All three default
    to the historical rule — a ``bonus`` worth the effect's rank — so an effect that
    states none of them, in this ruleset or a mod's, behaves exactly as before.
    """

    affects = frozenset(a for a in raw.get("affects", "").split("|") if a)
    target = raw.get("target", "")
    boost = None
    if configurable or target:
        boost = TraitBoost(
            affects=affects,
            target=target,
            configurable=configurable,
            apply=raw.get("apply", APPLY_BONUS),
            per_rank=int(raw.get("amountPerRank", 1)),
            flat=int(raw.get("amountFlat", 0)),
        )
    return Integration(pattern=raw.get("pattern", ""), trait_boost=boost)


def _parse_outcome_rung(entry: object) -> ResistanceOutcome | None:
    """One rung of a ``resistanceOutcomes`` ladder; a plain string is shorthand for ``text``."""

    if isinstance(entry, str):
        return ResistanceOutcome(text=entry)
    if not isinstance(entry, dict):
        return None
    escalates = entry.get("escalates", {})
    return ResistanceOutcome(
        conditions=tuple(entry.get("conditions", ())),
        config_key=entry.get("configKey", ""),
        text=entry.get("text", ""),
        note=entry.get("note", ""),
        escalates=tuple(escalates.items()) if isinstance(escalates, dict) else (),
    )


def _parse_resistance_outcomes(raw: object) -> tuple[ResistanceOutcome, ...]:
    """Parse an effect's ``resistanceOutcomes`` failure ladder (see :class:`ResistanceOutcome`).

    Accepts the ladder either as a bare list of rungs or wrapped in an object with a
    ``degrees`` list, so a mod can hang its own documentation — and its ``success``
    rung — off the same block.
    """

    if isinstance(raw, dict):
        raw = raw.get("degrees", ())
    if not isinstance(raw, list):
        return ()
    rungs = (_parse_outcome_rung(entry) for entry in raw)
    return tuple(rung for rung in rungs if rung is not None)


def _parse_resistance_success(raw: object) -> ResistanceOutcome | None:
    """The ``success`` rung of a ``resistanceOutcomes`` block, if it has one."""

    if not isinstance(raw, dict):
        return None
    return _parse_outcome_rung(raw.get("success"))


def _parse_effect(e: dict, ranged_distance: RangeDistance | None = None) -> Effect:
    default_distance = ranged_distance or RangeDistance()
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
        integration=_parse_integration(
            e.get("statIntegration", {}), bool(e.get("configurableTarget", False))
        ),
        description=e.get("description", ""),
        config_fields=tuple(_parse_config_field(c) for c in e.get("config", [])),
        measure=_parse_measure(e.get("measure"), e["id"]),
        resistance_dc_base=e.get("resistanceDcBase"),
        resistance_outcomes=_parse_resistance_outcomes(e.get("resistanceOutcomes")),
        resistance_success=_parse_resistance_success(e.get("resistanceOutcomes")),
        implicit_modifiers=tuple(e.get("implicitModifiers", ())),
        range_distance=_parse_range_distance(e.get("rangeDistance"), default_distance)
        or default_distance,
    )


# ``overrides`` keys are camelCase in the JSON like every other key there, but the
# stat dicts :func:`mm_companion.core.rules.effect_stat_rows` builds are snake_case.
_OVERRIDE_KEYS = {"effectType": "effect_type"}


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
        max_rank=m.get("maxRank"),
        overrides={_OVERRIDE_KEYS.get(k, k): v for k, v in m.get("overrides", {}).items()},
        check_bonus=int(m.get("checkBonus", 0)),
        grants_attack=bool(m.get("grantsAttack", False)),
        drops_check=bool(m.get("dropsCheck", False)),
        check_note=m.get("checkNote", ""),
        step_field=m.get("stepField", ""),
        step_by=int(m.get("stepBy", 0)),
        distance_rank_bonus=int(m.get("distanceRankBonus", 0)),
        adds_ability=m.get("addsAbility", ""),
        gate=m.get("gate", ""),
        requires_effect_id=m.get("requiresEffect", ""),
        hidden=bool(m.get("hidden", False)),
        note_template=m.get("noteTemplate", ""),
        note_per_rank=int(m.get("notePerRank", 0)),
        requires_check=bool(m.get("requiresCheck", False)),
        requires_any=tuple(m.get("requiresAny", ())),
        config_fields=tuple(_parse_config_field(c) for c in m.get("config", [])),
        custom=bool(m.get("custom", False)),
        description=m.get("description", ""),
        integration=(
            _parse_integration(m["statIntegration"], bool(m.get("configurableTarget", False)))
            if m.get("statIntegration")
            else None
        ),
    )


def _parse_ladders(raw: dict) -> dict[str, tuple[str, ...]]:
    """Read ``gameTermLadders`` (field -> ordered values) from ``modifiers.json``."""

    return {field: tuple(values) for field, values in raw.get("gameTermLadders", {}).items()}


def _parse_duration_action_floor(raw: dict) -> dict[str, str]:
    """Read ``durationActionFloor`` (duration -> minimum action) from ``modifiers.json``."""

    return {str(k): str(v) for k, v in raw.get("durationActionFloor", {}).items()}


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
    distance_m_by_rank: dict[int, float] = {}
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
        metric_distance = row.get("metric", {}).get("distance", {}).get("m")
        if metric_distance is not None:
            distance_m_by_rank[rank] = float(metric_distance)

    size_by_rank: dict[int, SizeRow] = {}
    for row in raw.get("sizeTable", []):
        size_rank = int(row["sizeRank"])
        size_by_rank[size_rank] = SizeRow(
            size_category=row["sizeCategory"],
            size_rank=size_rank,
            spaces=float(row["spaces"]),
            reach=int(row["reach"]),
            defense_mod=int(row["defenseMod"]),
            damage_mod=int(row["damageMod"]),
            toughness_mod=int(row["toughnessMod"]),
            speed_mod=int(row["speedMod"]),
            intimidation_mod=int(row["intimidationMod"]),
            stealth_mod=int(row["stealthMod"]),
        )
    size_effects = tuple(
        SizeEffect(
            column=entry["column"],
            category=entry["category"],
            target=entry["target"],
        )
        for entry in raw.get("sizeEffects", [])
    )
    return Measurements(
        by_rank=by_rank,
        size_by_rank=size_by_rank,
        distance_m_by_rank=distance_m_by_rank,
        size_effects=size_effects,
        size_rank_column=raw.get("sizeRankColumn", ""),
    )


def _parse_movement(raw: dict) -> Movement:
    return Movement(
        base_ground_speed_rank=int(raw.get("baseGroundSpeedRank", 1)),
        walk_rank_step=int(raw.get("walkRankStep", 0)),
        dash_rank_step=int(raw.get("dashRankStep", 1)),
        run_rank_step=int(raw.get("runRankStep", 2)),
        run_is_ground_only=bool(raw.get("runIsGroundOnly", True)),
        round_seconds=int(raw.get("roundSeconds", 6)),
        ground_mode=raw.get("groundMode", "ground"),
        ground_label=raw.get("groundLabel", "Ground speed"),
    )


def _parse_readouts(raw: dict) -> dict[str, tuple[Readout, ...]]:
    """Parse ``effect_readouts.json`` into ``effect id -> (Readout, ...)``.

    Each readout keeps its ``kind`` and ``label``; everything else on the entry is
    carried in ``data`` for the renderer to interpret per kind.
    """

    result: dict[str, tuple[Readout, ...]] = {}
    for effect_id, items in raw.get("effectReadouts", {}).items():
        result[effect_id] = tuple(
            Readout(
                kind=item["kind"],
                label=item.get("label", ""),
                data={k: v for k, v in item.items() if k not in ("kind", "label")},
            )
            for item in items
        )
    return result


def _unqualify(ref: object) -> str:
    """``"effects:damage"`` -> ``"damage"``; a bare id passes through.

    The equipment catalog names its effects, modifiers and advantages with the
    file they live in, which reads well in the data and is how the design
    reference writes them, but is not how those records are keyed. Splitting on
    the last colon is mechanical, so a mod inventing a namespace of its own gets
    the same treatment without the loader knowing anything about it.
    """

    return str(ref or "").rsplit(":", 1)[-1]


def _parse_equipment_effect_ref(raw: dict) -> EquipmentEffectRef:
    rank = raw.get("rank")
    return EquipmentEffectRef(
        effect=_unqualify(raw.get("effect")),
        rank=None if rank is None else int(rank),
        strength_based=bool(raw.get("strengthBased", False)),
        descriptors=tuple(str(d) for d in raw.get("descriptors", ())),
        config=dict(raw.get("config", {})),
        configuration=str(raw.get("configuration", "")),
        resistance=str(raw.get("resistance", "")),
        degrees=tuple(tuple(str(c) for c in degree) for degree in raw.get("degrees", ())),
        label=str(raw.get("label", raw.get("name", ""))),
        modifiers=tuple(_parse_equipment_modifier_ref(m) for m in raw.get("modifiers", ())),
        extra=_extras(
            raw,
            "effect",
            "rank",
            "strengthBased",
            "descriptors",
            "config",
            "configuration",
            "resistance",
            "degrees",
            "label",
            "name",
            "modifiers",
        ),
    )


def _parse_equipment_modifier_ref(raw: dict) -> EquipmentModifierRef:
    rank = raw.get("rank")
    return EquipmentModifierRef(
        modifier=_unqualify(raw.get("modifier")),
        rank=None if rank is None else int(rank),
        note=str(raw.get("note", "")),
        extra=_extras(raw, "modifier", "rank", "note"),
    )


def _parse_critical(raw: dict | None) -> CriticalProfile | None:
    if not isinstance(raw, dict):
        return None
    threat = tuple(int(v) for v in raw.get("threatRange", ()))[:2]
    return CriticalProfile(
        threat_range=threat if len(threat) == 2 else (20, 20),
        improved_critical_ranks=int(raw.get("improvedCriticalRanks", 0)),
    )


def _parse_equipment_entry(raw: dict) -> EquipmentEntry:
    cost = raw.get("cost")
    return EquipmentEntry(
        id=str(raw["id"]),
        name=str(raw.get("name", raw["id"])),
        category=str(raw.get("category", "")),
        subcategory=str(raw.get("subcategory") or ""),
        cost=None if cost is None else int(cost),
        cost_kind=str(raw.get("costKind", "fixed")),
        cost_note=str(raw.get("costNote", "")),
        description=str(raw.get("description", "")),
        effects=tuple(_parse_equipment_effect_ref(e) for e in raw.get("effects", ())),
        modifiers=tuple(_parse_equipment_modifier_ref(m) for m in raw.get("modifiers", ())),
        # Values are lists of ids in the same namespaced form as the refs above.
        grants={
            str(key): tuple(_unqualify(v) for v in values)
            for key, values in raw.get("grants", {}).items()
        },
        critical=_parse_critical(raw.get("critical")),
        patterns=tuple(str(p) for p in raw.get("patterns", ())),
        implementation=dict(raw.get("implementation", {})),
        extra=_extras(
            raw,
            "id",
            "name",
            "category",
            "subcategory",
            "cost",
            "costKind",
            "costNote",
            "description",
            "effects",
            "modifiers",
            "grants",
            "critical",
            "patterns",
            "implementation",
        ),
    )


def _parse_equipment_categories(raw: dict) -> tuple[EquipmentCategory, ...]:
    """The Equipment block's groups, from ``_meta.equipmentCategories``.

    Empty when a ruleset declares none — like the condition groups, and unlike
    the condition *categories*, there is nothing sensible to invent, and a block
    handed nothing falls back to grouping by the raw category id.
    """

    meta = raw.get("_meta", {})
    descriptions = meta.get("categoryKey", {})
    entries = meta.get("equipmentCategories")
    if not isinstance(entries, list):
        return ()
    return tuple(
        EquipmentCategory(
            id=str(entry["id"]),
            title=str(entry.get("title", entry["id"])),
            description=str(descriptions.get(entry["id"], "")),
        )
        for entry in entries
        if isinstance(entry, dict) and entry.get("id")
    )


def _parse_equipment_rules(raw: dict) -> EquipmentRules:
    meta = raw.get("_meta", {})
    currency = meta.get("currency", {})
    stacking = meta.get("stackingRule", {})
    breakage = meta.get("strengthBasedDamage", {}).get("breakage", {})
    defaults = EquipmentRules()
    return EquipmentRules(
        currency_name=str(currency.get("name", defaults.currency_name)),
        currency_abbreviation=str(currency.get("abbreviation", defaults.currency_abbreviation)),
        advantage=_unqualify(currency.get("source")) or defaults.advantage,
        points_per_advantage_rank=int(
            currency.get("pointsPerAdvantageRank", defaults.points_per_advantage_rank)
        ),
        stacking_rule=str(stacking.get("rule", "")),
        stacking_targets=tuple(str(t) for t in stacking.get("appliesTo", ())),
        cost_kinds=dict(meta.get("costKindKey", {})),
        material_toughness={
            str(material): int(toughness)
            for material, toughness in breakage.get("materialToughness", {}).items()
        },
        breakage_rule=str(breakage.get("rule", "")),
        extra=_extras(
            meta,
            "currency",
            "categoryKey",
            "equipmentCategories",
            "equipmentCategoriesNote",
            "costKindKey",
            "stackingRule",
            "strengthBasedDamage",
            "description",
            "vehicleCategory",
            "vehicleClasses",
            "vehicleClassesNote",
            "vehicleSizeExtension",
            "vehicleCombat",
            "installationCategory",
            "installationTraits",
            "installationPowerLevel",
        ),
    )


def _parse_vehicle_weapon(raw: dict) -> VehicleWeapon:
    return VehicleWeapon(
        name=str(raw.get("name", "")),
        effect=_unqualify(raw.get("effect")),
        rank=int(raw.get("rank", 0) or 0),
        modifiers=tuple(_parse_equipment_modifier_ref(m) for m in raw.get("modifiers", ())),
        note=str(raw.get("note", "")),
    )


def _parse_stock_vehicle(raw: dict) -> StockVehicle:
    cost = raw.get("cost")
    speed = raw.get("speed")
    movement = raw.get("movement")
    return StockVehicle(
        id=str(raw["id"]),
        name=str(raw.get("name", raw["id"])),
        vehicle_class=str(raw.get("class", "")),
        size=int(raw.get("size", 0) or 0),
        strength=int(raw.get("strength", 0) or 0),
        speed=None if speed is None else int(speed),
        defense_modifier=int(raw.get("defenseModifier", 0) or 0),
        toughness=int(raw.get("toughness", 0) or 0),
        cost=None if cost is None else int(cost),
        cost_kind=str(raw.get("costKind", "fixed")),
        cost_formula=str(raw.get("costFormula", "")),
        defenses=tuple(_parse_equipment_modifier_ref(d) for d in raw.get("defenses", ())),
        weapons=tuple(_parse_vehicle_weapon(w) for w in raw.get("weapons", ())),
        movement=_parse_equipment_effect_ref(movement) if isinstance(movement, dict) else None,
        patterns=tuple(str(p) for p in raw.get("patterns", ())),
        variants=str(raw.get("variants", "")),
        notes=str(raw.get("notes", "")),
        extra=_extras(
            raw,
            "id",
            "name",
            "class",
            "size",
            "strength",
            "speed",
            "defenseModifier",
            "toughness",
            "cost",
            "costKind",
            "costFormula",
            "defenses",
            "weapons",
            "movement",
            "patterns",
            "variants",
            "notes",
        ),
    )


def _parse_platform_feature(raw: dict) -> PlatformFeature:
    return PlatformFeature(
        id=str(raw["id"]),
        name=str(raw.get("name", raw["id"])),
        cost=int(raw.get("cost", 1) or 0),
        repeatable=bool(raw.get("repeatable", False)),
        description=str(raw.get("description", "")),
        dc_increase_per_extra_rank=int(raw.get("dcIncreasePerExtraRank", 0) or 0),
        max_dc_increase=int(raw.get("maxDcIncrease", 0) or 0),
        extra=_extras(
            raw,
            "id",
            "name",
            "cost",
            "repeatable",
            "description",
            "dcIncreasePerExtraRank",
            "maxDcIncrease",
        ),
    )


def _parse_vehicle_modifier(raw: dict) -> VehicleModifier:
    return VehicleModifier(
        id=str(raw["id"]),
        name=str(raw.get("name", raw["id"])),
        cost=int(raw.get("cost", 0) or 0),
        cost_kind=str(raw.get("costKind", "per_rank_flat")),
        description=str(raw.get("description", "")),
        extra=_extras(raw, "id", "name", "cost", "costKind", "description"),
    )


def _parse_vehicle_rules(raw: dict) -> VehicleRules:
    meta = raw.get("_meta", {})
    defaults = VehicleRules()
    extension = meta.get("vehicleSizeExtension", {})
    if not isinstance(extension, dict):  # a ruleset may state it as prose
        extension = {}
    return VehicleRules(
        category=str(meta.get("vehicleCategory", defaults.category)),
        classes=tuple(
            VehicleClass(
                id=str(entry["id"]),
                title=str(entry.get("title", entry["id"])),
                effect=_unqualify(entry.get("effect")),
            )
            for entry in meta.get("vehicleClasses", ())
            if isinstance(entry, dict) and entry.get("id")
        ),
        size_table=tuple(
            VehicleSizeRow(
                size_rank=int(row.get("sizeRank", 0) or 0),
                strength=int(row.get("strength", 0) or 0),
                toughness=int(row.get("toughness", 0) or 0),
                defense=int(row.get("defense", 0) or 0),
                examples=tuple(str(e) for e in row.get("examples", ())),
            )
            for row in raw.get("vehicleSizeTable", ())
            if isinstance(row, dict)
        ),
        size_extension=VehicleSizeExtension(
            strength=int(extension.get("strength", 2)),
            toughness=int(extension.get("toughness", 1)),
            defense=int(extension.get("defense", -1)),
            note=str(extension.get("note", "")),
        ),
        combat=dict(meta.get("vehicleCombat", {})),
    )


def vehicle_entry(vehicle: StockVehicle, rules: VehicleRules) -> EquipmentEntry:
    """A stock vehicle as an ordinary :class:`EquipmentEntry`.

    The one translation the vehicle layer needs, and it is a translation of *shape*
    rather than of rules: a platform's printed line names its movement and its weapons
    in a vehicle-shaped spelling, and this writes the same information in the catalog's.
    What comes out is priced, built, worn, rolled and grouped by the code that already
    exists — see :meth:`GameData.equipment_catalog`.

    Its movement effect is its own ``movement`` block when it has one, and otherwise
    the effect its :class:`VehicleClass` measures Speed in, at the printed speed rank.
    A vehicle whose class names no effect and that carries no block of its own (the
    time machine) simply has no movement effect, which is the honest answer rather than
    an invented one. The five platform traits are deliberately **not** folded in: they
    are not effects, they cost points off their own table, and they are read straight
    off the :class:`StockVehicle` by the card that shows them.
    """

    effects: list[EquipmentEffectRef] = []
    movement = vehicle.movement
    if movement is None and vehicle.speed is not None:
        vehicle_class = rules.vehicle_class(vehicle.vehicle_class)
        if vehicle_class is not None and vehicle_class.effect:
            movement = EquipmentEffectRef(effect=vehicle_class.effect, rank=vehicle.speed)
    if movement is not None:
        effects.append(movement)
    effects.extend(
        EquipmentEffectRef(
            effect=weapon.effect,
            rank=weapon.rank,
            label=weapon.name,
            modifiers=weapon.modifiers,
        )
        for weapon in vehicle.weapons
    )
    description = " ".join(part for part in (vehicle.notes, vehicle.variants) if part)
    return EquipmentEntry(
        id=vehicle.id,
        name=vehicle.name,
        category=rules.category,
        cost=vehicle.cost,
        cost_kind=vehicle.cost_kind,
        cost_note=vehicle.cost_formula,
        description=description,
        effects=tuple(effects),
        patterns=vehicle.patterns,
    )


def _parse_stock_installation(raw: dict) -> StockInstallation:
    cost = raw.get("cost")
    return StockInstallation(
        id=str(raw["id"]),
        name=str(raw.get("name", raw["id"])),
        size=int(raw.get("size", 0) or 0),
        toughness=int(raw.get("toughness", 0) or 0),
        cost=None if cost is None else int(cost),
        cost_kind=str(raw.get("costKind", "fixed")),
        cost_formula=str(raw.get("costFormula", "")),
        features=tuple(_unqualify(f) for f in raw.get("features", ())),
        patterns=tuple(str(p) for p in raw.get("patterns", ())),
        notes=str(raw.get("notes", "")),
        extra=_extras(
            raw,
            "id",
            "name",
            "size",
            "toughness",
            "cost",
            "costKind",
            "costFormula",
            "features",
            "patterns",
            "notes",
        ),
    )


def _parse_installation_rules(raw: dict) -> InstallationRules:
    meta = raw.get("_meta", {})
    defaults = InstallationRules()
    traits = meta.get("installationTraits", {})
    if not isinstance(traits, dict):  # a ruleset may state it as prose
        traits = {}
    power_level = meta.get("installationPowerLevel", {})
    if not isinstance(power_level, dict):
        power_level = {}
    return InstallationRules(
        category=str(meta.get("installationCategory", defaults.category)),
        size_table=tuple(
            InstallationSizeRow(
                size_rank=int(row.get("sizeRank", 0) or 0),
                cost=int(row.get("cost", 0) or 0),
                examples=tuple(str(e) for e in row.get("examples", ())),
            )
            for row in raw.get("installationSizeTable", ())
            if isinstance(row, dict)
        ),
        free_size_rank=int(traits.get("freeSizeRank", defaults.free_size_rank)),
        free_toughness=int(traits.get("freeToughness", defaults.free_toughness)),
        toughness_per_point=max(
            1, int(traits.get("toughnessPerPoint", defaults.toughness_per_point))
        ),
        toughness_pl_multiple=int(
            power_level.get("toughnessMultiplier", defaults.toughness_pl_multiple)
        ),
        impervious_pl_multiple=int(
            power_level.get("imperviousMultiplier", defaults.impervious_pl_multiple)
        ),
        impervious_modifier=_unqualify(
            power_level.get("imperviousModifier", defaults.impervious_modifier)
        ),
        note=str(traits.get("note", "")),
    )


def installation_entry(installation: StockInstallation, rules: InstallationRules) -> EquipmentEntry:
    """A stock installation as an ordinary :class:`EquipmentEntry`.

    The installation twin of :func:`vehicle_entry`, and simpler for the same reason an
    installation is simpler than a vehicle: it has no movement and no weapons, so the
    entry it yields usually carries no effects at all. What it *is* — Size, Toughness
    and its Features — is read straight off the :class:`StockInstallation` by the one
    layer that needs it, exactly as a vehicle's five traits are. What this buys is
    everything around that: the picker lists it, the budget prices it, a card draws it
    and a save round-trips it, with no installation branch anywhere.
    """

    return EquipmentEntry(
        id=installation.id,
        name=installation.name,
        category=rules.category,
        cost=installation.cost,
        cost_kind=installation.cost_kind,
        cost_note=installation.cost_formula,
        description=installation.notes,
        patterns=installation.patterns,
    )


#: Used when ``conditions.json`` carries no ``_meta.sheetSections`` (an older file, or
#: a mod's), so the Conditions block still groups the base categories sensibly.
_DEFAULT_CONDITION_CATEGORIES = (
    ConditionCategory("condition", "General", addable=True),
    ConditionCategory("damage_condition", "Damage", addable=True),
)


def _parse_condition_categories(raw: dict) -> tuple[ConditionCategory, ...]:
    sections = raw.get("_meta", {}).get("sheetSections")
    if not isinstance(sections, list) or not sections:
        return _DEFAULT_CONDITION_CATEGORIES
    parsed = [
        ConditionCategory(
            category=str(entry["category"]),
            title=str(entry.get("title", entry["category"])),
            addable=bool(entry.get("addable", True)),
        )
        for entry in sections
        if isinstance(entry, dict) and entry.get("category")
    ]
    return tuple(parsed) or _DEFAULT_CONDITION_CATEGORIES


def _parse_condition_groups(raw: dict) -> tuple[ConditionGroup, ...]:
    """The "+" menu's submenus, from ``_meta.conditionGroups``.

    Empty when the file declares none — unlike the categories, there is no
    sensible default to invent here, and a menu builder handed nothing simply
    falls back to the flat alphabetical list it used to show.
    """
    groups = raw.get("_meta", {}).get("conditionGroups")
    if not isinstance(groups, list):
        return ()
    return tuple(
        ConditionGroup(
            group=str(entry["group"]),
            title=str(entry.get("title", entry["group"])),
        )
        for entry in groups
        if isinstance(entry, dict) and entry.get("group")
    )


def _parse_costs(raw: dict) -> Costs:
    # Tolerate unknown keys (e.g. from a mod) so they can't crash the loader.
    trait_fields = {f.name for f in fields(TraitCosts)}
    traits = TraitCosts(**{k: int(v) for k, v in raw["trait_costs"].items() if k in trait_fields})
    pl = raw["power_level"]
    caps = {
        name: PowerLevelCap(mult=int(cap["mult"]), add=int(cap["add"]))
        for name, cap in pl["caps"].items()
    }
    ranges = {
        family: TraitRange(
            min=int(entry.get("min", DEFAULT_TRAIT_RANGES.get(family, TraitRange()).min)),
            max=int(entry.get("max", DEFAULT_TRAIT_RANGES.get(family, TraitRange()).max)),
        )
        for family, entry in raw.get("trait_ranges", {}).items()
        if isinstance(entry, dict)
    }
    return Costs(
        traits=traits,
        power_level=PowerLevelRules(pp_per_level=int(pl["pp_per_level"]), caps=caps),
        trait_ranges=ranges,
    )


def _parse_range_distance(raw: dict | None, base: RangeDistance) -> RangeDistance | None:
    """A ``rangeDistance`` block laid over ``base``, or ``None`` when there is none.

    Only the keys the block actually names are overridden, so an effect that merely
    reaches further than its rank suggests writes ``{"offset": 2}`` and inherits the
    rest of the system-wide derivation.
    """

    if not raw:
        return None
    return replace(
        base,
        rank_source=raw.get("rankSource", base.rank_source),
        rank=None if raw.get("rank") is None else int(raw["rank"]),
        offset=int(raw.get("offset", base.offset)),
        steps=tuple(int(s) for s in raw["steps"]) if "steps" in raw else base.steps,
        step_labels=(tuple(raw["stepLabels"]) if "stepLabels" in raw else base.step_labels),
        range_value=raw.get("rangeValue", base.range_value),
    )


def _parse_system(raw: dict) -> SystemRules:
    """Parse ``system.json`` into :class:`SystemRules`, tolerating unknown keys.

    Every field falls back to its dataclass default, so a mod (or a stripped file)
    can override only the keys it cares about.
    """

    sys = raw.get("system", raw)
    defaults = SystemRules()
    tk_raw = sys.get("trait_keys", {})
    trait_keys = TraitKeys(
        attack=tk_raw.get("attack", defaults.trait_keys.attack),
        defense=tk_raw.get("defense", defaults.trait_keys.defense),
        dodge=tk_raw.get("dodge", defaults.trait_keys.dodge),
        toughness=tk_raw.get("toughness", defaults.trait_keys.toughness),
    )
    paired_caps = tuple(
        PairedCap(cap=p["cap"], traits=tuple(p["traits"]), label=p["label"])
        for p in sys.get("paired_caps", [])
    )
    return SystemRules(
        default_initiative_ability=sys.get(
            "default_initiative_ability", defaults.default_initiative_ability
        ),
        defense_dc_base=int(sys.get("defense_dc_base", defaults.defense_dc_base)),
        heroic_budget_divisor=int(sys.get("heroic_budget_divisor", defaults.heroic_budget_divisor)),
        critical_effect_bonus=int(sys.get("criticalEffectBonus", defaults.critical_effect_bonus)),
        critical_miss_resistance_bonus=int(
            sys.get("criticalMissResistanceBonus", defaults.critical_miss_resistance_bonus)
        ),
        trait_keys=trait_keys,
        paired_caps=paired_caps,
        unscoped_scope_values=tuple(
            sys.get("unscoped_scope_values", defaults.unscoped_scope_values)
        ),
        alternate_effect_modifier=sys.get(
            "alternate_effect_modifier", defaults.alternate_effect_modifier
        ),
        linked_modifier=sys.get("linked_modifier", defaults.linked_modifier),
        damage_effect=sys.get("damage_effect", defaults.damage_effect),
        ranged_distance=_parse_range_distance(sys.get("ranged_distance"), defaults.ranged_distance)
        or defaults.ranged_distance,
        derived_traits=tuple(
            DerivedTrait(key=d["key"], label=d.get("label", d["key"]))
            for d in sys.get("derived_traits", [])
        ),
    )


def _parse_block_field(f: dict) -> BlockFieldSpec:
    return BlockFieldSpec(
        key=f.get("key", ""),
        label=f.get("label", ""),
        kind=f.get("kind", "text"),
        text=f.get("text", ""),
        extra=_extras(f, "key", "label", "kind", "text"),
    )


def _parse_block_spec(b: dict) -> BlockSpec:
    return BlockSpec(
        id=b["id"],
        title=b.get("title", ""),
        row=int(b.get("row", 0)),
        col=int(b.get("col", 0)),
        fields=tuple(_parse_block_field(f) for f in b.get("fields", [])),
        min_width=int(b.get("min_width", 0)),
        min_height=int(b.get("min_height", 0)),
        max_width=int(b.get("max_width", 0)),
        max_height=int(b.get("max_height", 0)),
        extra=_extras(
            b,
            "id",
            "title",
            "row",
            "col",
            "fields",
            "min_width",
            "min_height",
            "max_width",
            "max_height",
        ),
    )


# ===========================================================================
# Mod merge loader — gather the active mods' content in load order, deep-merge
# by record id, parse once, and cache. The public entry point lives at the end.
# ===========================================================================


# Candidate id fields for record lists, tried in order. Whichever a list's dict
# elements all carry identifies records for the by-id merge; a list whose elements
# share none (e.g. an ``options`` list of strings) is replaced wholesale by a mod.
_MERGE_ID_KEYS = ("id", "key", "name", "effect_id", "rank", "sizeRank", "column")


def _list_id_key(items: list) -> str | None:
    """The id field shared by every dict element of *items*, or ``None``."""
    if not items or not all(isinstance(x, dict) for x in items):
        return None
    for key in _MERGE_ID_KEYS:
        if all(key in x for x in items):
            return key
    return None


def _merge_lists(base: list, override: list) -> list:
    base_key = _list_id_key(base)
    if base_key is None or base_key != _list_id_key(override):
        # Not record lists (or keyed differently): the mod replaces it wholesale.
        return list(override)
    result: list = []
    index: dict = {}
    for item in base:
        index[item[base_key]] = len(result)
        result.append(item)
    for item in override:
        ident = item[base_key]
        if ident in index:
            result[index[ident]] = _deep_merge(result[index[ident]], item)
        else:
            index[ident] = len(result)
            result.append(item)
    return result


def _deep_merge(base, override):
    """Recursively merge *override* onto *base* (later source wins).

    Dicts merge key-by-key; record lists (whose elements share an id field —
    see :data:`_MERGE_ID_KEYS`) merge by id, later records overriding earlier ones
    of the same id and new ids appended; anything else is replaced by *override*.
    This is how a mod overrides one record (supply just its id + changed fields) or
    adds new ones without restating the base content.
    """
    if isinstance(base, dict) and isinstance(override, dict):
        merged = dict(base)
        for key, value in override.items():
            merged[key] = _deep_merge(merged[key], value) if key in merged else value
        return merged
    if isinstance(base, list) and isinstance(override, list):
        return _merge_lists(base, override)
    return override


def _merge_content(mods: list[mods_module.Mod]) -> dict[str, dict]:
    """Deep-merge every content file across *mods* (base first) into ``name -> raw``."""
    filenames: list[str] = []
    for mod in mods:
        for filename in mod.files:
            if filename not in filenames:
                filenames.append(filename)
    merged: dict[str, dict] = {}
    for filename in filenames:
        acc: dict | None = None
        for mod in mods:
            raw = mod.read(filename)
            if raw is None:
                continue
            acc = raw if acc is None else _deep_merge(acc, raw)
        merged[filename] = acc if acc is not None else {}
    return merged


def _build_game_data(content: dict[str, dict]) -> GameData:
    """Parse already-merged raw content into the typed :class:`GameData` record."""
    profile_raw = content.get("profile.json", {})
    characteristics_raw = content.get("characteristics.json", {})
    abilities_raw = content.get("abilities.json", {})
    resistances_raw = content.get("resistances.json", {})
    skills_raw = content.get("skills.json", {})
    advantages_raw = content.get("advantages.json", {})
    conditions_raw = content.get("conditions.json", {})
    effects_raw = content.get("effects.json", {})
    modifiers_raw = content.get("modifiers.json", {})
    effect_modifiers_raw = content.get("effect_modifiers.json", {})
    effect_readouts_raw = content.get("effect_readouts.json", {})
    costs_raw = content.get("costs.json", {})
    system_raw = content.get("system.json", {})
    measurements_raw = content.get("measurements.json", {})
    movement_raw = content.get("movement.json", {})
    equipment_raw = content.get("equipment.json", {})
    blocks_raw = content.get("blocks.json", {})

    # Parsed first: an effect's own ``rangeDistance`` block overrides only the keys it
    # names, so it needs the system-wide default to lay itself over.
    system = _parse_system(system_raw)
    # Likewise: a stock vehicle becomes a catalog entry against its own rules record
    # (which class means which movement effect, which category they file under).
    vehicle_rules = _parse_vehicle_rules(equipment_raw)
    vehicles = tuple(_parse_stock_vehicle(v) for v in equipment_raw.get("stockVehicles", []))
    installation_rules = _parse_installation_rules(equipment_raw)
    installations = tuple(
        _parse_stock_installation(i) for i in equipment_raw.get("stockInstallations", [])
    )

    return GameData(
        profile_fields=[_parse_field(f) for f in profile_raw.get("profile_fields", [])],
        characteristics=[
            _parse_characteristic(c) for c in characteristics_raw.get("characteristics", [])
        ],
        abilities=[_parse_ability(a) for a in abilities_raw.get("abilities", [])],
        resistances=[_parse_resistance(r) for r in resistances_raw.get("resistances", [])],
        skills=[_parse_skill(s) for s in skills_raw.get("skills", [])],
        advantages=[_parse_advantage(a) for a in advantages_raw.get("advantages", [])],
        conditions=[_parse_condition(c) for c in conditions_raw.get("conditions", [])],
        condition_categories=_parse_condition_categories(conditions_raw),
        condition_groups=_parse_condition_groups(conditions_raw),
        effects=[_parse_effect(e, system.ranged_distance) for e in effects_raw.get("effects", [])],
        modifiers=[_parse_modifier(m) for m in modifiers_raw.get("modifiers", [])],
        effect_modifiers=_parse_effect_modifiers(effect_modifiers_raw),
        costs=_parse_costs(costs_raw),
        measurements=_parse_measurements(measurements_raw),
        game_term_ladders=_parse_ladders(modifiers_raw),
        duration_action_floor=_parse_duration_action_floor(modifiers_raw),
        effect_readouts=_parse_readouts(effect_readouts_raw),
        movement=_parse_movement(movement_raw),
        equipment=tuple(_parse_equipment_entry(e) for e in equipment_raw.get("equipment", [])),
        equipment_categories=_parse_equipment_categories(equipment_raw),
        equipment_rules=_parse_equipment_rules(equipment_raw),
        vehicles=vehicles,
        vehicle_entries=tuple(vehicle_entry(v, vehicle_rules) for v in vehicles),
        vehicle_features=tuple(
            _parse_platform_feature(f) for f in equipment_raw.get("vehicleFeatures", [])
        ),
        vehicle_modifiers=tuple(
            _parse_vehicle_modifier(m) for m in equipment_raw.get("vehicleModifiers", [])
        ),
        vehicle_rules=vehicle_rules,
        installations=installations,
        installation_entries=tuple(
            installation_entry(i, installation_rules) for i in installations
        ),
        installation_features=tuple(
            _parse_platform_feature(f) for f in equipment_raw.get("installationFeatures", [])
        ),
        installation_rules=installation_rules,
        system=system,
        blocks=tuple(_parse_block_spec(b) for b in blocks_raw.get("blocks", [])),
    )


# Cache keyed on the active mod stack's fingerprint, so the base ruleset (and each
# distinct set of enabled mods) is parsed once per process. Replaces the former
# ``@lru_cache`` — clear it via :func:`clear_game_data_cache` after changing mods.
_game_data_cache: dict[tuple[str, ...], GameData] = {}


def load_game_data() -> GameData:
    """Parse and return the merged game data for the active mod stack (cached).

    With no mods enabled this is the bundled base ruleset, identical to before the
    mod pipeline existed. Enabled workspace mods (``enabled_mods`` setting) are
    deep-merged on top, later/higher-priority mods overriding earlier records.
    """
    mods = mods_module.active_mods()
    key = tuple(mod.fingerprint() for mod in mods)
    cached = _game_data_cache.get(key)
    if cached is None:
        cached = _build_game_data(_merge_content(mods))
        _game_data_cache[key] = cached
    return cached


def clear_game_data_cache() -> None:
    """Drop the cached game data; the next :func:`load_game_data` re-parses.

    Call after enabling/disabling a mod or editing a mod's files so the change is
    picked up (the cache key does not track file contents).
    """
    _game_data_cache.clear()

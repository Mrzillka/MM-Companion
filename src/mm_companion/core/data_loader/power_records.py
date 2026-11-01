"""Records for the powers layer: base effects, modifiers (extras and flaws), and the
configuration fields an effect is set up with.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from ..components import Integration

if TYPE_CHECKING:
    from .system_records import (
        RangeDistance,
    )


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

    ``cost_delta`` *adjusts* the magnitude the option arrives at rather than replacing
    it, and unlike the three above it is summed across **every** config field rather
    than first-wins — so a second choice can shade a price the first one set (Area's
    Emanation, +1 on its shape's rate). The sum is floored at 0, not at 1.

    ``flat_delta`` adjusts a **power-scope** modifier's *total* instead — added once,
    after the per-points rate has been charged, rather than to the rate itself. It is how
    Removable's Short-Term Only is priced: "a 1-point flat reduction in the value of the
    flaw" (p159–160), so a 20-point Removable power is discounted 4 − 1 = 3, to 17. Summed
    across every field like ``cost_delta``, and the total is floored at 0.

    ``supersedes``, on a **multiselect** option, names the other options this one already
    covers, so ticking both is paying twice for one thing. An entry is a bare ``value``
    in the same field (Environment's *Extreme cold* covers its *Intense cold*) or
    ``"field:value"`` when the option it covers lives in a sibling field (Obscure's whole
    *Sight* sense type covers the single *sight* sense). Nothing is blocked — the pair is
    a legal, wasteful build, and the player may have meant it — but
    :func:`mm_companion.core.rules.power_redundant_option_violations` says so.
    """

    value: str
    label: str
    cost_value: int | None = None
    flat: bool | None = None
    ranked: bool | None = None
    cost_delta: int = 0
    flat_delta: int = 0
    supersedes: tuple[str, ...] = ()


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

    ``tier_labels`` names what each tier *buys*, one entry per tier ("all sight senses").
    It is what a readout says instead of the tier's number: a Concealment at Sight tier 2
    used to print "Sight 2" beside a combo reading "4 ranks", two numbers that mean
    different things sitting next to each other. Optional — an option without one falls
    back to naming the ranks the tier costs, which is still not its index.

    ``prompt`` is what the row's note asks for — "which sense", "what it detects", "which
    environment". Most of these options apply *to* something the rules leave the player
    to name, and an option may be taken more than once for different things (Accurate
    hearing beside Accurate smell), so every chosen row carries a free-text note. Empty
    means the generic prompt.
    """

    id: str
    label: str
    tiers: tuple[int, ...] = (1,)
    per_note: str = ""
    description: str = ""
    speeds: tuple[SpeedRank | None, ...] = ()
    tier_notes: tuple[str, ...] = ()
    tier_labels: tuple[str, ...] = ()
    prompt: str = ""

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

    def tier_label(self, tier: int) -> str:
        """What a 1-based ``tier`` buys (``""`` when the option does not say)."""
        if 1 <= tier <= len(self.tier_labels):
            return self.tier_labels[tier - 1]
        return ""

    def tier_cost(self, tier: int) -> int:
        """The ranks a 1-based ``tier`` costs, clamped to the tiers the option has."""
        if not self.tiers:
            return 0
        return self.tiers[max(0, min(tier, len(self.tiers)) - 1)]


@dataclass(frozen=True)
class RepeatableColumn:
    """One column of a Tier-4 ``repeatable`` field's rows (Immunity, Feature).

    ``type`` is ``"text"`` (free text), ``"int"`` (a rank spin), or ``"trait"`` (a
    trait picker, populated from the game data the same way a ``select`` field with
    ``source="traits"`` is). ``key`` names where the value lives inside each stored
    row dict.

    A field carrying a ``"trait"`` column is a *trait allocation* — each row names a
    trait and, through the field's ``int`` column, the ranks put into it. That is what
    lets one Enhanced Trait raise several traits at once out of a single rank pool; see
    :func:`mm_companion.core.rules.boost_allocations`.
    """

    key: str
    label: str
    type: str = "text"
    #: For a ``"trait"`` column, which trait list the picker offers — the same
    #: vocabulary a ``select`` field's ``source`` uses (see
    #: :data:`mm_companion.ui.power_constructor.TRAIT_SOURCES`). Empty elsewhere.
    source: str = ""
    #: For a ``"suggest"`` column: the standard answers offered in its editable combo,
    #: as ``(label, ranks)`` pairs — picking one also sets the row's ``int`` column to
    #: its ranks (Immunity's tiers: Aging is 1, Critical Hits 2, all Fortitude effects
    #: 30). Free text is still accepted; the list is a shortcut, not a limit.
    suggestions: tuple[tuple[str, int], ...] = ()
    #: What a row with no stored value in this ``int`` column counts as — 1 for a
    #: Feature, which "typically requires 1 rank", so a row saved before the column
    #: existed still costs what it did.
    default: int = 0


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
    the degree choices to use-time), and ``shown_with`` is its mirror: the field is
    there **only** while that extra is attached (Affliction's second resistance, which
    means nothing until Alternate Resistance is on). ``widen_with`` names an extra whose
    presence *adds* options to a ``select`` — the ones named by ``widen_source`` — so a
    field can offer the short, ordinary list until a modifier is bought that widens what
    is legal (Affliction is resisted by one of four defenses; Alternate Resistance
    opens the field to every resistance, ability, skill and Damage). ``toggles`` is the
    extra a ``checkbox`` field attaches. ``source``, on a ``select`` field, names a
    data-driven option source to populate instead of a static ``options`` list —
    currently ``"traits"`` (abilities, resistances, and skills), used by Enhanced
    Trait's Reduced Trait flaw to pick which trait is lowered. ``hides_field``, on a
    *modifier's* config field, marks that the chosen value is the ``key`` of one of the
    *parent effect's* config fields to hide — Affliction's Limited Degree picks a tier
    (``degree1``/``degree2``/``degree3``) whose condition picker then disappears.
    ``hint`` is helper text shown under an ``allocation``/``repeatable`` field
    (e.g. Immunity's suggested-rank tiers). ``show_when_points``, on a *modifier's*
    config field, gates its visibility on a sibling ``points`` field's value — the
    field appears only when that spin box reads exactly this number (Affliction's
    Variable Conditions reveals its "which degree" picker only at the 1-point tier).
    Zero (the default) means always shown. ``show_when_field`` / ``show_when_value``
    are the same idea one level out, on an *effect's* own config: the field appears
    only while the sibling field named by ``show_when_field`` holds
    ``show_when_value`` — Affliction reveals its imposed-effect picker only once a
    degree is set to Transformed. The sibling may be single- or multi-select, so
    "holds" means equals *or* contains. Both empty (the default) means always shown.

    ``names_owner`` marks a ``select`` whose options are names for the *modifier itself*
    rather than qualifiers of it, so the chosen label replaces the modifier's name
    instead of being appended in parentheses. Removable's tiers are the case it exists
    for: their labels read "Removable (only while Stunned and Defenseless)" / "Easily
    Removable (…)", which qualified a modifier already called Removable produced
    "Removable (removable (only while stunned and defenseless))".

    ``grants``, on a *modifier's* field that ``overrides`` a stat, lets the choice
    **supply** that stat on an effect that has none, rather than only replacing one it
    already has: the Resistible flaw and the Attack extra pick what a Teleport or a
    Concealment is resisted by. On an effect that already has the stat the field is
    *claimed* (:func:`mm_companion.core.rules.modifier_field_claimed`) and the chip
    does not show it. ``template`` wraps the chosen value before it replaces the stat —
    Alternate Defense writes ``"Attack vs. {value} + 10"`` into the check — and
    ``required`` marks a field whose being left empty leaves the build unfinished, which
    the constructor warns about (Affliction's resistance, Resistible's).
    """

    key: str
    label: str
    type: str = "select"
    overrides: str | None = None
    grants: bool = False
    template: str = ""
    required: bool = False
    multiselect_with: str | None = None
    hidden_with: str | None = None
    shown_with: str | None = None
    widen_with: str | None = None
    widen_source: str | None = None
    toggles: str | None = None
    source: str | None = None
    hides_field: bool = False
    names_owner: bool = False
    hint: str = ""
    min_value: int = 0
    max_value: int = 0
    default_value: int = 0
    show_when_points: int = 0
    show_when_field: str = ""
    show_when_value: str = ""
    options: tuple[ConfigOption, ...] = ()
    alloc_options: tuple[AllocationOption, ...] = ()
    columns: tuple[RepeatableColumn, ...] = ()


@dataclass(frozen=True)
class EffectReach:
    """How an effect extends the reach its wielder makes close attacks at.

    ``per_rank_distance_rank`` is the distance rank **one rank buys**: the effect adds
    its rank times the real distance at that rank, which for Elongation is rank 1 (15
    feet a rank). A real distance, not a rank, because reach sums several sources and
    ranks are never added to each other (``measurements.json``'s ``doNotAddRanksNote``).

    Nothing in code names Elongation — an effect extends reach because the ruleset gave
    it this block, and a mod's own reach effect joins on exactly the same terms.
    """

    per_rank_distance_rank: int = 0


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
    #: The parameter each named condition is applied with, as ``(condition id,
    #: template)`` pairs — Frightened needs an object of fear. ``{source}`` in a
    #: template stands for whoever imposed the rung (see
    #: :func:`mm_companion.core.rules.targeting.resolve_rung`). Pairs for the same
    #: reason :attr:`escalates` is.
    parameters: tuple[tuple[str, str], ...] = ()
    #: The instance config field naming the trait this rung's conditions are scoped
    #: to — Weaken's chosen trait, so its Impaired lands as "Strength Impaired". Read
    #: only for a condition whose parameter is a trait; blank in the config, unscoped.
    scope_key: str = ""


@dataclass(frozen=True)
class ConfiguredModifier:
    """One extra or flaw a standard power configuration already has attached.

    ``id`` names a record in the merged modifier catalog (general or effect-specific);
    ``rank`` is how many ranks of it a ranked modifier starts at (``0`` leaves the
    selection's own default), and ``config`` seeds the modifier's config choices — the
    Ranged extra's "from Close", a Limited Degree's chosen degree.
    """

    id: str
    rank: int = 0
    config: dict = field(default_factory=dict)


@dataclass(frozen=True)
class ConfiguredEffect:
    """One effect of a standard power configuration, already set up.

    ``rank`` is a rank the configuration *nails down* — Invisibility is Concealment 2,
    X-Ray Vision is Enhanced Senses 4 — and ``0`` leaves the player to choose, which is
    the usual case for the per-rank configurations. ``config`` seeds the effect's own
    configurable qualities (an Affliction's conditions by degree, Remote Sensing's sense
    types).
    """

    effect_id: str
    rank: int = 0
    config: dict = field(default_factory=dict)
    extras: tuple[ConfiguredModifier, ...] = ()
    flaws: tuple[ConfiguredModifier, ...] = ()


@dataclass(frozen=True)
class PowerConfiguration:
    """A named, pre-built power from the rulebook's standard configurations.

    These are the game's own shorthand — *Blast* rather than "Damage, Ranged" — and
    they are **not** new rules: each is an ordinary assembly of effects, extras and flaws
    that already exist in the other data files, which is why building one produces a
    :class:`~mm_companion.core.powers.Power` the player can then edit like any other.

    ``base_effect`` is the effect id the configuration files under in the palette.
    ``cost_note`` is the cost the *book* prints for it and ``cite`` the page — both are
    reference text and neither is ever used in the arithmetic: what a built power costs
    is derived from its effects and modifiers like anything else. Where the two disagree
    the reason is a rule the app does not model yet (see ``configurations.json``'s
    ``_meta.costNote``). ``structure`` matters only for a multi-effect configuration.
    """

    id: str
    name: str
    base_effect: str = ""
    cost_note: str = ""
    cite: str = ""
    description: str = ""
    structure: str = "independent"
    effects: tuple[ConfiguredEffect, ...] = ()


@dataclass(frozen=True)
class BaseCostBy:
    """How an effect whose base cost depends on its *configuration* works that cost out.

    A handful of effects are not a fixed number of points per rank: Illusion costs 1 per
    sense type it fools and Obscure 1 per sense it blocks (sight counting double for
    both), Remote Sensing 5 for the first sense type and 1 for each after, Transmute 2 to
    5 by how broad its source and result are, and Environment adds up the sub-effects it
    imposes. All of them are still charged *flat, per rank* — only which number differs,
    which is why this is a field on the effect rather than another
    :data:`mm_companion.core.rules.BASE_COST_KINDS` mode.

    The cost is ``base + Σ (cost_value of every option currently chosen across
    ``fields``)``, clamped to ``[minimum, maximum]``. ``minimum`` is therefore also what
    an unconfigured effect costs, which is what keeps a freshly dropped card priced at
    its floor rather than at zero. ``maximum`` of ``None`` means the rules name no
    ceiling (Environment, where sub-effects simply keep adding).

    The per-option numbers live on the config options themselves
    (:attr:`ConfigOption.cost_value`), the same place a modifier's config already keeps
    them — so adding a sense type or a new Environment condition is a data edit with no
    Python behind it.
    """

    fields: tuple[str, ...] = ()
    base: int = 0
    minimum: int = 1
    maximum: int | None = None


@dataclass(frozen=True)
class SubBuild:
    """A whole nested *character* an effect or a modifier buys, and its budget.

    Four places in the rules spend a sub-character's worth of points inside a power:
    Summon's minion (``rank x 15`` PP, p145), Morph's Metamorph forms (one per rank of
    the extra, each on the wielder's own point total, p136), Variable's pool and
    Affliction's Empowering form. The first two genuinely buy a *character* — the other
    two are a menu and a GM's build — so only those two declare one of these.

    ``key`` is the config key the builds are stored under, on the effect's ``config``
    for an effect-owned slot and on the chip's for a modifier-owned one. The value is a
    list of :meth:`Character.to_dict` dicts, so it is ordinary JSON and rides along in
    every save, every undo snapshot and every session push with no special handling.

    ``budget`` and ``count`` are :data:`mm_companion.core.rules.NOTE_VALUE_KINDS` specs
    — the same ``{"kind": ...}`` shape a modifier's ``noteValues`` uses, resolved by the
    same registry, so a mod can price a sub-build off anything a note can say. ``count``
    absent means one build.

    ``forbids_effects`` / ``forbids_advantages`` are what the sub-character may not
    itself have: a summoned minion "cannot have minions of their own, either from this
    effect or the Minions advantage" (p145). Checked as a warning, not prevented.

    ``menu_with`` names modifiers that turn a counted slot into an **open menu** — as
    many builds as the player cares to make, one of them summoned at a time. "You always
    summon the same minion unless you apply the Variable Type modifier" (p145) is the one
    case: a Variable Type Summon is not entitled to *more* minions at once, it is entitled
    to have built several to choose between. It is a list of modifier ids rather than a
    flag on the modifier because it is the *slot* that changes shape, and the slot is
    where a reader looks to find out how many builds it holds.
    """

    key: str
    label: str
    budget: dict = field(default_factory=dict)
    count: dict = field(default_factory=dict)
    hint: str = ""
    forbids_effects: tuple[str, ...] = ()
    forbids_advantages: tuple[str, ...] = ()
    menu_with: tuple[str, ...] = ()


@dataclass(frozen=True)
class Effect:
    """A base power effect from ``effects.json`` (see ``docs/mm-powers-architecture.md``).

    A power is assembled from one or more of these, each carrying its own extras
    and flaws. ``base_cost`` is the human-readable prose (e.g. ``"1 per rank"``);
    ``base_cost_value`` is the canonical machine-readable points-per-rank used for
    automatic cost calculation, and ``base_cost_mode`` names *how* that is charged —
    one of :data:`mm_companion.core.rules.BASE_COST_KINDS`. Effects are priced flat
    (points per rank) unless they say otherwise; Enhanced Trait is priced ``as_trait``,
    where the cost comes from the traits it raises and ``base_cost_value`` is only the
    nominal rate its per-rank modifiers are read against. ``base_cost_by``, when set,
    makes the points-per-rank a function of the effect's own configuration instead of a
    constant (see :class:`BaseCostBy`); ``base_cost_value`` is then the unconfigured
    floor, and :func:`mm_companion.core.rules.effect_base_cost_value` is what resolves
    the two cases for every caller. ``integration`` is the parsed
    ``statIntegration`` component (see :class:`mm_companion.core.components.Integration`)
    describing how the effect patches stats — its activation ``pattern`` and, for the passive
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

    ``opposed_check`` names what an effect's own **opposed effect check** is rolled
    against — Nullify's ``"targeted rank or Will"``. An effect check is ``d20 + effect
    rank`` (p107) and the *wielder* makes it, which is the whole reason this is its own
    field: Nullify's second roll reads like a resistance in the book's stat block, but the
    procedure is "make an opposed check of your Nullify rank and the targeted effect rank
    or the target's Will" — put in the ``resistance`` slot it becomes a roll attributed to
    the target and carrying no bonus at all. Empty for every effect that makes no such
    check of its own.

    ``personal`` is whether the effect works on **its user alone** — which is what the
    rules mean by "a Personal Range effect" (p110). It defaults to ``range_ ==
    "Personal"`` and only the exceptions state it in the data, because a handful of
    self-only effects spend their Range parameter on a *distance* instead: Teleport's
    Range is "Rank" (the rank is how far you go), and so is Communication's and Remote
    Sensing's. The book settles it by naming Teleport as an example of an effect an
    Affliction may impose, alongside Morph and Shrinking. Read it through
    :func:`mm_companion.core.rules.effect_is_personal`, not off the record, so the
    default lives in one place.
    """

    id: str
    name: str
    effect_type: str
    action: str = ""
    range_: str = ""
    #: ``None`` means "not stated" and defers to ``range_``; see the class docstring
    #: and :func:`mm_companion.core.rules.effect_is_personal`.
    personal: bool | None = None
    duration: str = ""
    check: str | None = None
    resistance: str | None = None
    #: What this effect's own opposed effect check is rolled against; see the class
    #: docstring. Empty for effects that make none.
    opposed_check: str = ""
    base_cost: str = ""
    base_cost_value: int = 1
    base_cost_mode: str = "flat"
    #: Set when the effect's points-per-rank is worked out from its configuration
    #: rather than being a constant (Illusion, Obscure, Remote Sensing, Transmute,
    #: Environment). ``None`` for every effect priced at a flat rate.
    base_cost_by: BaseCostBy | None = None
    #: A whole nested character this effect buys, with its budget (Summon's minion).
    #: ``None`` for every effect that buys no such thing; see :class:`SubBuild`.
    sub_build: SubBuild | None = None
    #: Whether the effect's rank *is* the ranks its config allocates, rather than a
    #: budget the player sets by hand. True for Enhanced Trait, whose cost comes from
    #: the traits it raises and whose rank has no other meaning — so the constructor
    #: shows the rank read-only and keeps it in step with the rows.
    rank_follows_allocation: bool = False
    integration: Integration = field(default_factory=Integration)
    description: str = ""
    config_fields: tuple[EffectConfigField, ...] = ()
    measure: Measure | None = None
    resistance_dc_base: int | None = None
    #: Whether the *first* resistance check is made against the wielder's **check
    #: result** rather than the effect DC — Mind Reading's "Will vs. Effect Check
    #: Result" (p133). The save its check provokes is then an opposed one; the later,
    #: recurring saves are still against the effect DC.
    resistance_vs_check: bool = False
    #: What failing this effect's resistance check does to the target, one rung per
    #: degree of failure (see :class:`ResistanceOutcome`). Empty for an effect whose
    #: outcome is a GM call rather than a table.
    resistance_outcomes: tuple[ResistanceOutcome, ...] = ()
    #: What *making* the check still costs the target, for the effects where that is
    #: not nothing (Damage's Hit). ``None`` where a made save really is a clean escape.
    resistance_success: ResistanceOutcome | None = None
    #: The DC of the effect's own check, for an effect that is *checked* rather than
    #: resisted (Healing's DC 10). ``None`` for every other effect.
    check_dc: int | None = None
    #: The conditions each degree of success on that check takes off its subject, one
    #: per degree, in this order (Healing's least severe first). Empty for most effects.
    success_removes: tuple[str, ...] = ()
    implicit_modifiers: tuple[str, ...] = ()
    #: How far this effect reaches once its range resolves to Ranged. Seeded from the
    #: system-wide default and overridden by the effect's own ``rangeDistance`` block.
    range_distance: RangeDistance | None = None
    #: How much *close*-attack reach the effect grants its wielder while it is active
    #: (Elongation). ``None`` for every effect that grants none; see :class:`EffectReach`.
    reach: EffectReach | None = None


@dataclass(frozen=True)
class ResistanceGrant:
    """A resistance check a modifier gives an effect that has none of its own.

    The Resistible flaw (p162) "gives it one. Choose the resistance when the flaw is
    configured", and the Attack extra turns a Teleport into something its target can
    resist. The chosen resistance comes from the modifier's ``grants`` config field;
    this record says what the DC is. ``dc_base`` is its fixed part, added to the
    effect's rank — or, when ``dc_trait`` names a resistance key, to the **wielder's**
    total in that trait instead (Fortune Control's Resistible is DC 10 + your Will).
    ``placeholder`` is the resistance phrase shown before anything is chosen; empty
    means the grant waits for a choice (the Attack extra adds no save until one is
    picked, since an attack need not be resisted by anything).
    """

    dc_base: int = 10
    dc_trait: str = ""
    placeholder: str = ""


@dataclass(frozen=True)
class Modifier:
    """An extra or flaw from ``modifiers.json`` (see ``docs/mm-powers-architecture.md``).

    ``category`` is ``"extra"`` (adds cost/benefit) or ``"flaw"`` (subtracts
    cost/adds a restriction). ``cost_formula`` is the prose; ``cost_value`` is the
    canonical numeric magnitude (always non-negative — the sign comes from
    ``category``). ``cost_mode`` names how that magnitude is arrived at, from the same
    vocabulary as :attr:`Effect.base_cost_mode`: the default reads ``cost_value``, while
    ``"as_trait"`` computes it from the modifier's own trait allocation (Reduced Trait is
    worth whatever the trait it lowers was worth). ``flat`` is ``True`` when the cost is a
    one-time add/subtract to the effect total rather than per rank. ``ranked`` is ``True``
    when the modifier itself is bought in ranks (chosen independently of the effect's rank), so its
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
    Dodge-for-half); and ``step_field``/``step_by`` shift a field along its
    :attr:`GameData.game_term_ladders` ordering (Increased Duration steps ``duration``
    up, Increased Action steps ``action`` to a slower one). A *ranked* stepping modifier
    moves ``step_by`` positions **per rank bought**, so two ranks of Increased Action pay
    twice and move twice; an unranked one moves ``step_by`` once.

    ``adds_ability`` names a character ability whose rank is added to the effect's
    *effective* rank — Strength-Based Damage (``"STR"``) folds the wielder's Strength
    into the Damage rank for its resistance DC and Power Level cap. It is the one
    modifier field that reaches back into character stats, so cost/PL math must be
    given the character to resolve it.

    ``cost_scope`` says *what* the modifier is priced against. The default (empty) is
    the effect it is attached to, which is every modifier but one. ``"power"`` prices it
    against the assembled power's total instead — Removable "applies to the power as a
    whole and not to individual effects" (p161) — so the effect-level buckets skip it
    and :func:`mm_companion.core.rules.power_total_cost` applies it once, after the
    effects are summed. ``cost_per_points``, alongside it, makes the magnitude a *rate*:
    Removable's 5 means "its value per 5 points of the power's final cost, rounded up".
    Zero charges the magnitude once.

    ``dynamic_cost_value`` belongs to the array's ``alternate_effect`` record alone: an
    Alternate Effect is "1 or 2 points flat" (p151), 1 for an ordinary alternate and 2
    for a **Dynamic** one that shares the array's point pool. Making the array's *base*
    Dynamic instead "requires 1 Alternate Effect rank" (p101) — that is ``cost_value``,
    not a third number. Zero everywhere else.

    ``gate`` marks a flaw that can switch an effect's standing bonus off at runtime
    (one of :mod:`mm_companion.core.components`'s ``GATE_*`` kinds): Activation
    (``"activation"``), Removable (``"removable"``), Limited (``"limited"``),
    Requires-Effect (``"requires_effect"``). Empty for modifiers with no runtime gate.
    Consulted by :func:`mm_companion.core.rules.effect_is_active`.
    ``requires_effect_id`` pairs with a ``"requires_effect"`` gate: it names the base
    effect id that must be currently active on the wielder for this effect's bonus to
    apply (e.g. ``"insubstantial"`` for the *Limited to While Insubstantial* flaw).

    ``repeatable`` allows a *second* copy of the modifier on the same effect. The default
    is one only: a duplicate almost always double-charges the power while overriding
    nothing, since the later copy's game-term and cost effects are the same as the first's.
    The exceptions are the modifiers whose meaning lives in their config text — two Limited
    flaws ("only at night", "only vs. robots") are two different restrictions, and a Custom
    modifier is whatever the player named it. Read by the constructor
    (``EffectCard.attach_modifier``), not by the cost math, which prices whatever is
    attached.

    ``hidden`` keeps a record out of the constructor's palette while leaving it in the
    modifier catalog for cost/lookup use — the structural ``linked`` / ``alternate_effect``
    modifiers are applied automatically from a power's structure (and the array flat cost
    is read from the record), so they should not be draggable as ordinary extras.

    ``note_template`` is a descriptive line the modifier contributes to its effect's
    generated Notes row instead of its bare name — a ``{n}`` placeholder is replaced
    by the effect's rank times ``note_per_rank`` (or the bare rank when that is zero),
    so Affliction's Empowering reads "transformed form gains 60 power points" at rank 4.
    Empty leaves the modifier listed by name.

    ``note_values`` supplies the placeholders a rank alone cannot answer, as
    ``{name: {"kind": ..., ...}}``. ``kind`` picks a handler out of
    :data:`mm_companion.core.rules.NOTE_VALUE_KINDS`, so the arithmetic is named in data
    rather than spelled per modifier: Multiple Minions' count *doubles* with its rank
    (p145) and Metamorph's alternate forms are each worth the **wielder's own** point
    total (p136), and neither is a multiple of anything the note already had. A
    placeholder whose value cannot be worked out — ``character_points`` with no
    character in hand — is dropped from the sentence rather than shown as a zero.

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
    cost_mode: str = ""
    flat: bool = False
    ranked: bool = False
    max_rank: int | None = None
    description: str = ""
    overrides: dict[str, str] = field(default_factory=dict)
    check_bonus: int = 0
    grants_attack: bool = False
    drops_check: bool = False
    #: The resistance check this modifier gives an effect that has none; see
    #: :class:`ResistanceGrant`. ``None`` for every modifier that grants none.
    grants_resistance: ResistanceGrant | None = None
    check_note: str = ""
    step_field: str = ""
    step_by: int = 0
    #: How many distance ranks each rank of this modifier adds to a Ranged effect's
    #: reach (Extended Range's ``1``). Zero for every modifier that doesn't reach further.
    distance_rank_bonus: int = 0
    #: How many whole **Spaces** of *close*-attack reach each rank of this modifier adds
    #: (the Reach extra's ``1``). Spaces rather than a distance rank because that is how
    #: the rules state it, and because it lands on the same total the Size Table's reach
    #: column does. Zero for every modifier that extends no reach.
    reach_spaces: int = 0
    #: Where a range this modifier gives starts, when it is not the effect's rank:
    #: ``{"base": 2, "ability": "STR"}`` is Thrown's "short range is distance rank 2 +
    #: the character's Strength rank" (p155). Empty for every other modifier.
    range_rank: dict = field(default_factory=dict)
    adds_ability: str = ""
    cost_scope: str = ""
    cost_per_points: int = 0
    #: What one *Dynamic* alternate costs, for the array modifier only (p151's "1 or 2
    #: points flat"). Zero on every other record, and read through
    #: :func:`mm_companion.core.rules.array_alternate_cost`.
    dynamic_cost_value: int = 0
    gate: str = ""
    requires_effect_id: str = ""
    repeatable: bool = False
    #: A whole nested character this *modifier* buys (Morph's Metamorph forms).
    #: ``None`` for every modifier that buys none; see :class:`SubBuild`.
    sub_build: SubBuild | None = None
    hidden: bool = False
    note_template: str = ""
    note_per_rank: int = 0
    note_values: dict[str, dict] = field(default_factory=dict)
    #: Using the effect first calls for an extra roll that can fail (Check Required).
    #: Such a modifier gets its own game-term row and a line in the card's dice footer
    #: rather than being buried in Notes — it is something someone has to roll.
    requires_check: bool = False
    #: The trait such a check is made with when the rules fix it rather than the player
    #: (Leaping's Acrobatics Check Required). ``""``: a config field picks it, or the
    #: roll is a bare d20 (Unreliable's reliability die).
    check_trait: str = ""
    #: The DC of such a check when the rules fix it (Unreliable fails on 10 or less, so
    #: 11; Leaping's Acrobatics is DC 10). ``None``: the system's base DC plus the
    #: modifier's own rank, Check Required's "10 + ranks in Check Required".
    check_dc: int | None = None
    #: What using the effect puts on its wielder, as condition ids — or the fatigue
    #: ladder's sentinel (:data:`~mm_companion.core.rules.extra_effort.USE_COST_FATIGUE`)
    #: for Tiring. Distracting's is ``("vulnerable",)``. Empty for everything else.
    use_costs: tuple[str, ...] = ()
    #: How many uses the effect gets before it stops until recharged (Short-Term's
    #: six). 0: the modifier counts no uses.
    uses_per_charge: int = 0
    #: How many ranks each use costs the effect until recharged (Fades' one). 0: none.
    fades_per_use: int = 0
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

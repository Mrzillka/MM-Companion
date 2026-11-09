"""Records for the point-bought sheet content (profile, characteristics, abilities,
resistances, skills, advantages) and for conditions.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..components import TONE_HINDRANCE

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
    Defense) the UI shows below a separator, apart from the core resistances.
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
    ``focuses`` lists the suggested focus *names* for a focused skill — ready choices
    wherever a focus is picked, never a closed list; ``focus_note`` carries the guidance
    for a skill whose focuses cannot be enumerated (Expertise, Languages), shown as a
    hint rather than offered as something selectable.
    ``specializations`` lists illustrative common uses of a skill — and, since any of
    them may be bought as a narrow half-cost rank pool, the suggested *names* wherever a
    specialization is named; ``specialization_note`` is their ``focus_note``, carrying
    the guidance for a skill whose pools are drawn from something that cannot be listed
    ("By specific sense"), shown as a prompt rather than offered as a name.
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
    specialization_note: str = ""
    focuses: tuple[str, ...] = ()
    focus_note: str = ""
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
    #: Uses per adventure for each bought rank — ``0`` for an advantage that is not a
    #: limited resource. Luck, Determination and Edit Scene are ``1``: once per rank per
    #: adventure, all reset at the next one. See :func:`~.rules.advantage_uses`.
    uses_per_rank: int = 0
    #: Whether owning this lets trained-only skills be tried without ranks
    #: (Jack-of-All-Trades), and the skills it still leaves closed.
    allows_untrained: bool = False
    allows_untrained_except: tuple[str, ...] = ()
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
    """Structured recovery check (§8), rolled by :mod:`mm_companion.core.rules.recovery`.

    ``trait`` and ``dc`` are the check; a record with either missing (Fatigued's rest)
    is not a check at all and offers no die. ``success_degrees`` is how many degrees of
    success clear it (Dying needs two to stabilise), ``becomes`` what the condition
    turns into when it does (Dying stabilises to Incapacitated), and ``blocked_by`` the
    conditions whose presence means no check may be made yet (Staggered recovers only
    once nothing worse is on the creature). ``cadence``/``condition``/``outcome`` are
    the prose the tooltip carries.
    """

    trait: str | None = None
    dc: int | None = None
    cadence: str = ""
    condition: str = ""
    outcome: str = ""
    success_degrees: int = 1
    becomes: str = ""
    blocked_by: tuple[str, ...] = ()


@dataclass(frozen=True)
class AmountMod:
    """One trait a condition moves by its applied *amount* (All-Out Attack's −X Defense).

    ``trait`` is a trait key — a resistance's (``DEF``) or a roll's
    (``system.trait_keys.attack_check`` for attack checks); ``sign`` is ``+1`` or ``−1``,
    multiplied by the applied instance's amount. ``parameter``, when set, limits the mod to
    an instance applied with that parameter.
    """

    trait: str
    sign: int = 1
    parameter: str = ""


#: What spends an ``opposing`` condition (:attr:`OpposingRule.spent_by`): the bearer's
#: next attack on the creature its parameter names, or that creature's next attack on
#: the bearer.
OPPOSING_ATTACKING = "attacking"
OPPOSING_ATTACKED = "attacked"


@dataclass(frozen=True)
class OpposingRule:
    """What a condition does only between its bearer and the creature its parameter
    names — Aid's bonus, a Feint's Vulnerable, a Trick (decision 17 of the combat
    maneuvers plan). Counted in one attack the GM's app judges between the two, and
    spent by it (:attr:`spent_by`: :data:`OPPOSING_ATTACKING` / :data:`OPPOSING_ATTACKED`).

    :attr:`attack` and :attr:`defense` are multiplied by the applied amount: the
    bearer's attack check against that creature, the bearer's Defense against its
    attack. :attr:`defense_as` judges the bearer's Defense against it as if under that
    condition (a feint's ``vulnerable``).
    """

    attack: int = 0
    defense: int = 0
    defense_as: str = ""
    spent_by: str = ""


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
    #: What the condition does to its bearer on balance — ``"benefit"`` or
    #: ``"hindrance"`` (:data:`~mm_companion.core.components.TONES`) — and so the colour
    #: its chip is drawn in. Hindrance unless the record says otherwise.
    tone: str = TONE_HINDRANCE
    #: The traits an applied instance moves by its ``amount`` (the maneuvers' ±X).
    amount_mods: tuple[AmountMod, ...] = ()
    #: The amount an instance gets when applied without one (Defending's +2).
    default_amount: int | None = None
    #: The largest amount the rules allow (a maneuver's 5) — what the "+" menu offers
    #: up to when it asks for one. ``None`` leaves it open.
    max_amount: int | None = None
    #: Always applied as temporary — gone at the start of the bearer's next turn — from
    #: wherever it is applied, the "+" menu included.
    temporary: bool = False
    #: What it does only between the bearer and the creature its parameter names, and
    #: which judged attack spends it (Aid, Feint, Trick). ``None`` for every other.
    opposing: OpposingRule | None = None


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

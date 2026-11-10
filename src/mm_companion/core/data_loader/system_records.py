"""Records for point costs and Power Level, the system rules the resolvers read, and the
measurement and movement tables.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


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
    fortitude: str = "FORTITUDE"
    will: str = "WILL"
    #: Not a trait on the sheet but the attack check an attack rolls — what a
    #: condition's ``amountMods`` names to move it (Defensive Attack's −X).
    attack_check: str = "attack_check"


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
    """How far a ranged effect reaches, and its range increments.

    The book's rule (p104, p198) is **rank × distance value**: short range is the
    effect's rank times the distance at distance rank 1, medium rank × distance rank 2,
    long rank × distance rank 3. So with ``multiply_by_rank`` (the default) the
    *multiplier* is ``rank_source`` — the effect's own effective rank, or a fixed
    ``rank`` — and the short range is read at distance rank ``distance_value`` +
    ``offset``. Rank 10 is 10 × 15 feet, not distance rank 10 (a mile).

    Without ``multiply_by_rank`` the source rank *is* the distance rank (plus
    ``offset``), for a reach the rules state as a plain distance rank.

    ``steps`` are the distance-rank shifts of each increment from the short one and
    ``step_labels`` names them — the default ``(0, 1, 2)`` / short-medium-long. Extended
    and Diminished Range move every increment by their ``distanceRankBonus``.

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
    multiply_by_rank: bool = True
    distance_value: int = 1


@dataclass(frozen=True)
class DerivedTrait:
    """A numeric stat a player can be asked to check that isn't a bought trait.

    The abilities, resistances and skills are enumerated by their own data files;
    these are the leftovers a check can still name — the derived Defense aggregate,
    Initiative — listed in ``system.json`` rather than hardcoded in the picker.
    """

    key: str
    label: str


@dataclass(frozen=True)
class ImprovisedEffectRules:
    """The dials behind an Improvised Effect's preparation (``system.json``, p101–102).

    Preparing one takes a **time rank equal to its Power Point cost**, floored at
    ``min_time_rank`` (3 — one minute). From there the two trades: each time rank shaved
    off costs ``dc_per_time_rank_saved`` on the preparation DC, and each one spent beyond
    the base grants ``check_bonus_per_time_rank_spent`` on the check. Both DCs start from
    the system's ``defense_dc_base``, so a ruleset with a different base DC moves them
    together.
    """

    min_time_rank: int = 3
    dc_per_time_rank_saved: int = 5
    check_bonus_per_time_rank_spent: int = 2


@dataclass(frozen=True)
class ExtraEffortUse:
    """One of the benefits Extra Effort can buy (``system.json``, p20-21).

    ``target`` is what the use has to be pointed at: ``""`` for the ones that need
    nothing from the build (an extra action, a bonus on a check), ``"effect"`` for the
    two that name one of the character's own effects — the rank increase and the power
    stunt. It is what lets the menus be built from the list rather than from a hardcoded
    split, so a ruleset adding a seventh use gets it offered in the right place.
    """

    id: str
    label: str
    description: str = ""
    target: str = ""


@dataclass(frozen=True)
class PushableTrait:
    """One thing other than an effect that Extra Effort's rank increase may name (p21).

    The rules give three targets, and the app already had the first: an effect's rank,
    "your Strength rank for either Damage or Lifting", or "your movement Speed rank in
    one mode of movement you have". The other two are not
    :class:`~mm_companion.core.powers.PowerEffectInstance` records — they are an ability and a
    derived readout — so they are named here instead of being found on a power.

    ``category`` is the contribution category the push lands in (``"ability"``,
    ``"movement"``). A ``movement`` entry leaves ``stat`` empty, meaning **one of the
    modes this character actually has**: which modes those are is a fact about the sheet,
    not about the ruleset, so it is expanded per character rather than listed here.
    ``note`` is the rules' own qualification, shown beside the label.
    """

    category: str
    stat: str = ""
    label: str = ""
    note: str = ""


@dataclass(frozen=True)
class ExtraEffortRules:
    """What Extra Effort grants and what it costs (``system.json``, p20-21).

    ``fatigue_ladder`` is the price, in order: the first rung a fresh character takes,
    then where each further use pushes them. ``rank_increase`` is what the Rank Increase
    use is worth before ``untapped_potential_advantage`` adds its own ranks, and
    ``check_bonus`` what the Bonus use is worth. ``permanent_durations`` are the
    durations that cannot be improved or stunted with at all (p104).

    The three advantages are named rather than spelled in Python for the usual reason:
    which advantage shrugs the fatigue off is a rules fact, and a ruleset that renames
    or replaces one retunes it from data.
    """

    fatigue_ladder: tuple[str, ...] = ("fatigued", "exhausted", "incapacitated")
    rank_increase: int = 1
    check_bonus: int = 2
    permanent_durations: tuple[str, ...] = ("Permanent",)
    determination_advantage: str = "Determination"
    untapped_potential_advantage: str = "Untapped Potential"
    extraordinary_effort_advantage: str = "Extraordinary Effort"
    uses: tuple[ExtraEffortUse, ...] = ()
    pushable_traits: tuple[PushableTrait, ...] = ()


#: The six numbers a quick NPC is written from, in the order the GM's dialog shows
#: them. The vocabulary of ``system.json``'s ``quick_npc`` block: a pair names two of
#: these and a preset's ``shares`` is keyed by them.
QUICK_NPC_STATS = ("attack", "effect", "defence", "toughness", "fortitude", "will")


@dataclass(frozen=True)
class QuickNPCPair:
    """Two quick-NPC stats that share one Power Level cap (``system.json`` ``quick_npc``).

    ``cap`` names the :class:`PowerLevelCap` in ``costs.json`` whose total the pair is
    measured against. A ``fill`` pair always sums to that total exactly: the stat the
    preset names takes its share and the other whatever is left (the first, if a
    preset names both). A pair that does not fill
    sets each stat on its own share, and never past the total between them.
    """

    cap: str
    stats: tuple[str, str]
    fill: bool = True


@dataclass(frozen=True)
class QuickNPCPreset:
    """One of the GM's quick-NPC presets: a share of each pair's cap, per stat.

    A share is ``"high"`` (the larger part of the cap, rounded up), ``"low"`` (what
    the high part leaves), ``"even"`` (half) or ``"random"`` (anywhere between low and
    high). A stat a preset does not name takes ``"even"``.
    """

    id: str
    name: str
    description: str = ""
    shares: tuple[tuple[str, str], ...] = ()

    def share(self, stat: str) -> str:
        return dict(self.shares).get(stat, "even")


@dataclass(frozen=True)
class QuickNPCRules:
    """What the GM's Quick NPC dialog fills its numbers from (``system.json``)."""

    default_power_level: int = 10
    #: The larger share as a fraction ``(numerator, denominator)`` of a pair's cap.
    high_share: tuple[int, int] = (2, 3)
    pairs: tuple[QuickNPCPair, ...] = ()
    presets: tuple[QuickNPCPreset, ...] = ()


@dataclass(frozen=True)
class DamageDefenses:
    """Which modifiers on a Toughness change what Damage does, and how (``system.json``).

    Each entry is a list of modifier ids, so the rules are read off the data and the
    names stay content (p152, p154, p116):

    - ``roll_twice`` — the target rolls twice and keeps the higher against a Damage
      rank no greater than the ranks the modifier covers (Hardened);
    - ``two_degrees`` — two or more degrees of success on the save leave *no* damage
      conditions, not even the Hit (Hardened, Impervious, Impenetrable);
    - ``immune`` — the target is simply unaffected by a Damage rank no greater than the
      ranks covered (Impervious, Impenetrable);
    - ``ignores_penetrating`` — the defense holds against a Penetrating attack too
      (Impenetrable);
    - ``penetrating`` — the attacker's side: those ranks of an attack cut through
      ``immune`` (the target resists the Penetrating rank instead) and deny
      ``two_degrees`` a defense that does not ignore them.
    """

    roll_twice: tuple[str, ...] = ()
    two_degrees: tuple[str, ...] = ()
    immune: tuple[str, ...] = ()
    ignores_penetrating: tuple[str, ...] = ()
    penetrating: tuple[str, ...] = ()


@dataclass(frozen=True)
class MinionSystem:
    """The two numbers the minion rules need (``system.json``'s ``minions``, PDF p25).

    ``impress_bonus`` is what a hero gains on a routine Impress check against minions
    (PDF p201). ``ceiling`` is the condition a minion's failed saves stop at: escalation
    never carries one past it, so a thug who fails again stays Incapacitated rather
    than walking the Dying ladder (the GM marks it dead by hand). Which of the rules
    are *on* is not here — that is the session's, :class:`~.rules.minions.MinionRules`.
    """

    impress_bonus: int = 5
    ceiling: str = "incapacitated"


@dataclass(frozen=True)
class TeamAttackRules:
    """The Team Attack's numbers (``system.json``'s ``teamAttack``, PDF p201).

    ``steps`` are ``(degrees, bonus)`` pairs, lowest first: the other hits' total
    degrees of success reach a step, and the primary attack's rank gains its bonus.
    ``rank_spread`` is how far apart the combined attacks' ranks may be;
    ``critical_bonus`` what any critical among them adds, once.
    """

    rank_spread: int = 5
    steps: tuple[tuple[int, int], ...] = ((1, 2), (3, 5))
    critical_bonus: int = 5


@dataclass(frozen=True)
class GroupCheckRules:
    """Team and group checks' numbers (``system.json``'s ``teamCheck`` and
    ``groupCheck``, PDF p18)."""

    #: The DC a team check's helpers roll against.
    helper_dc: int = 10
    #: What each of their degrees of success adds to the leader's check.
    bonus_per_degree: int = 1
    #: The share of a group check's members who must succeed for all to.
    success_share: float = 0.5


@dataclass(frozen=True)
class DefeatSystem:
    """Which conditions mean a creature is out of the fight (``system.json``'s ``defeat``).

    ``down`` lists the conditions that put a creature down — Incapacitated, which Dying
    includes — and ``dead`` the ones it does not get up from. The GM's cards and every
    seat's Scene dim a creature by them (:func:`~.rules.conditions.defeat_state`); dead
    outranks down.
    """

    down: tuple[str, ...] = ("incapacitated",)
    dead: tuple[str, ...] = ("dead",)


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
    #: The common circumstance modifiers (PDF p17), smallest first: the roller's step
    #: buttons offer each one as a bonus and as a penalty.
    circumstance_modifiers: tuple[int, ...] = (1, 5)
    #: Where the roller's first DC step lands from "not set".
    dc_start: int = 10
    trait_keys: TraitKeys = field(default_factory=TraitKeys)
    paired_caps: tuple[PairedCap, ...] = ()
    unscoped_scope_values: tuple[str, ...] = ("All checks",)
    alternate_effect_modifier: str = "alternate_effect"
    linked_modifier: str = "linked"
    #: Which bonus the **one** attack check of a Linked power rolls at when its effects'
    #: bonuses differ (p155 gives a single check and no rule for that): ``"first"`` (the
    #: first attacking effect on the card), ``"highest"`` or ``"lowest"``.
    linked_attack_bonus: str = "first"
    improvised_effect: ImprovisedEffectRules = field(default_factory=ImprovisedEffectRules)
    extra_effort: ExtraEffortRules = field(default_factory=ExtraEffortRules)
    ranged_distance: RangeDistance = field(default_factory=RangeDistance)
    derived_traits: tuple[DerivedTrait, ...] = ()
    #: Which effect's resistance ladder is *the* damage ladder — the rungs the GM's
    #: quick-damage buttons walk. An id, not a rule, so a ruleset that calls its
    #: damage effect something else retargets the whole control from data.
    damage_effect: str = "damage"
    #: The Toughness modifiers that change what that damage does (Impervious and kin).
    damage_defenses: DamageDefenses = field(default_factory=DamageDefenses)
    #: The presets and cap pairs the GM's Quick NPC dialog builds a creature from.
    quick_npc: QuickNPCRules = field(default_factory=QuickNPCRules)
    #: The minion rules' numbers (see :class:`MinionSystem`).
    minions: MinionSystem = field(default_factory=MinionSystem)
    #: The conditions that take a creature out of the fight (see :class:`DefeatSystem`).
    defeat: DefeatSystem = field(default_factory=DefeatSystem)
    #: How several attacks combine into one (see :class:`TeamAttackRules`).
    team_attack: TeamAttackRules = field(default_factory=TeamAttackRules)
    #: Team and group checks (see :class:`GroupCheckRules`).
    group_checks: GroupCheckRules = field(default_factory=GroupCheckRules)


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
class ReachRules:
    """How far a character can make close attacks (``measurements.json``'s ``reachRules``).

    Two numbers, both of them so the rules stay out of Python. ``space_distance_rank``
    is the distance rank one Space *is*, which is the only thing that converts the Size
    Table's ``reach`` column (counted in Spaces) into a real distance an effect's own
    extension can be added to. ``baseline_size_rank`` names the size a reach readout
    treats as its **zero**: a character that size has no reach worth naming, so their
    size contributes nothing, while a larger one contributes its whole tabulated reach.

    That asymmetry is deliberate and is the house rule this readout was asked for: the
    baseline Space is the character's own body, already counted in every larger row of
    the table, so adding it again to a stretched limb would charge for it twice.
    """

    space_distance_rank: int = 0
    baseline_size_rank: int = 0


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
    distance_ft_by_rank: dict[int, float] = field(default_factory=dict)
    size_effects: tuple[SizeEffect, ...] = ()
    size_rank_column: str = ""
    reach_rules: ReachRules = field(default_factory=ReachRules)

    def label(self, column: str, rank: int, system: str = "imperial") -> str:
        return self.by_rank.get(rank, {}).get(system, {}).get(column, "")

    def distance_m(self, rank: int) -> float:
        """The numeric metric distance (metres) for a rank, or ``0.0`` if off-table."""
        return self.distance_m_by_rank.get(rank, 0.0)

    def distance_ft(self, rank: int) -> float:
        """The numeric imperial distance (feet) for a rank, or ``0.0`` if off-table.

        The imperial twin of :meth:`distance_m`, and parsed for the same reason: a
        readout that has to *add* two distances (reach) cannot work from the labels,
        and must not add the ranks (``doNotAddRanksNote``).
        """
        return self.distance_ft_by_rank.get(rank, 0.0)

    def space_feet(self) -> float:
        """How many feet one Space is — the distance value at ``space_distance_rank``."""
        return self.distance_ft(self.reach_rules.space_distance_rank)

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

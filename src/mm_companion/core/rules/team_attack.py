"""Team Attack (PDF p201): several attackers combine their attacks on one target.

The attacks must have the same effect, be resisted by the same check, and be within
``rank_spread`` ranks of each other. Each attacker rolls against the target's Defense
(an effect with no attack check counts as one degree of success). The largest effect
rank among the hits is the **primary**; the other hits' total degrees of success raise
its rank by a step's bonus (1 degree +2, 3 degrees +5 by the book), and any critical
among them adds a one-time bonus on top. Misses do nothing — degrees of failure do not
reduce the total. The target then makes one resistance check against the primary.

The numbers are data (``system.json``'s ``teamAttack``,
:class:`~..data_loader.TeamAttackRules`); this module is the arithmetic. The GM's
window that runs one for a group of NPCs is ``ui/team_attack.py``.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from ..character import Character
from ..data_loader import GameData
from .equipment import item_effective_build
from .rolls import KIND_POWER_SAVE, RollSpec, power_rolls

__all__ = [
    "TeamEntry",
    "TeamOption",
    "TeamResult",
    "combine_team_attack",
    "team_attack_options",
    "team_incompatibility",
    "team_save_spec",
]


@dataclass(frozen=True)
class TeamOption:
    """One attack a creature could bring to a team attack.

    ``key`` names it across a group — the power's (or item's) name and which of its
    attacks — so a squad of copies, or a mixed group with the same gun, line up.
    ``attack`` is the check the attacker rolls, ``None`` for an effect with no attack
    check (it hits, counting one degree); ``save`` is what the target resists.
    """

    key: str
    label: str
    save: RollSpec
    attack: RollSpec | None = None

    @property
    def rank(self) -> int:
        """The effect's rank (0 when the save does not say)."""
        return self.save.ladder.rank if self.save.ladder is not None else 0


def _options_from(prefix: str, name: str, specs: list[RollSpec]) -> list[TeamOption]:
    options: list[TeamOption] = []
    attacks = [s for s in specs if s.follow_up is not None and not s.rolled_by_target]
    for index, spec in enumerate(attacks):
        options.append(
            TeamOption(f"{prefix}:{name}:{index}", f"{name}: {spec.label}", spec.follow_up, spec)
        )
    if attacks:
        return options
    # No attack check at all (a Perception-ranged effect): its save is the whole attack.
    saves = [s for s in specs if s.kind == KIND_POWER_SAVE and s.ladder is not None]
    for index, save in enumerate(saves):
        options.append(TeamOption(f"{prefix}:{name}:save{index}", f"{name}: {save.label}", save))
    return options


def team_attack_options(char: Character, game_data: GameData) -> list[TeamOption]:
    """Every attack *char* could bring to a team attack: its powers', then its gear's."""

    options: list[TeamOption] = []
    for power in char.powers:
        options += _options_from("power", power.name, power_rolls(power, char, game_data))
    for item in char.equipment:
        build = item_effective_build(item, game_data)
        options += _options_from("item", item.name, power_rolls(build, char, game_data))
    return options


def team_incompatibility(options: list[TeamOption], game_data: GameData) -> str:
    """Why these attacks cannot combine, in words — ``""`` when they can.

    The same effect (the save's ladder kind) resisted by the same trait, and every
    rank within ``rank_spread`` of every other.
    """

    if len(options) < 2:
        return "A team attack takes two attackers or more."
    first = options[0].save
    kind = first.ladder.kind if first.ladder is not None else ""
    for option in options[1:]:
        save = option.save
        other = save.ladder.kind if save.ladder is not None else ""
        if save.trait_key != first.trait_key or other != kind:
            return "The attacks must have the same effect and the same resistance."
    ranks = [o.rank for o in options if o.rank]
    spread = game_data.system.team_attack.rank_spread
    if ranks and max(ranks) - min(ranks) > spread:
        return f"The attacks' ranks must be within {spread} of each other."
    return ""


@dataclass(frozen=True)
class TeamEntry:
    """One attacker's part: what they brought and how their attack went."""

    name: str
    rank: int
    hit: bool
    #: The attack's degree of success (a hit by an effect with no check counts one).
    degree: int = 1
    critical: bool = False


@dataclass(frozen=True)
class TeamResult:
    """What a team attack came to."""

    #: Index of the primary attack among the entries, ``None`` when nothing hit.
    primary: int | None
    #: The other hits' total degrees of success.
    other_degrees: int = 0
    #: The rank the step those degrees reached adds.
    step_bonus: int = 0
    #: The one-time bonus for a critical among the hits.
    critical_bonus: int = 0

    @property
    def bonus(self) -> int:
        """Everything the primary attack's rank gains."""
        return self.step_bonus + self.critical_bonus

    @property
    def any_hit(self) -> bool:
        return self.primary is not None


def combine_team_attack(entries: list[TeamEntry], game_data: GameData) -> TeamResult:
    """Combine *entries* into one attack (PDF p201).

    The primary is the hit with the largest rank. On a tie it is the one with the
    *fewest* degrees (then the first), the book being silent: the primary's own degrees
    count for nothing, so that is the reading that counts the most of the team's. The
    other hits' degrees — at least one each — reach a step; a critical
    anywhere among the hits adds its bonus once. Misses count for nothing.
    """

    rules = game_data.system.team_attack
    hits = [i for i, e in enumerate(entries) if e.hit]
    if not hits:
        return TeamResult(primary=None)
    primary = max(hits, key=lambda i: (entries[i].rank, -entries[i].degree, -i))
    degrees = sum(max(1, entries[i].degree) for i in hits if i != primary)
    step = 0
    for needed, bonus in rules.steps:
        if degrees >= needed:
            step = bonus
    critical = rules.critical_bonus if any(entries[i].critical for i in hits) else 0
    return TeamResult(primary, degrees, step, critical)


def team_save_spec(save: RollSpec, bonus: int, *, title: str = "") -> RollSpec:
    """The primary attack's save, its rank (and so its DC) raised by *bonus*.

    The ladder's rank rises with it, so a target's Impervious Toughness is measured
    against what the team actually hit with.
    """

    if not bonus:
        return replace(save, title=title or save.title)
    ladder = save.ladder
    if ladder is not None and ladder.rank:
        ladder = replace(ladder, rank=ladder.rank + bonus)
    dc = None if save.dc is None else save.dc + bonus
    label = save.label
    if dc is not None and save.dc is not None:
        label = label.replace(str(save.dc), str(dc), 1)
    return replace(
        save,
        dc=dc,
        ladder=ladder,
        label=f"{label} (team attack +{bonus})",
        title=title or save.title,
    )

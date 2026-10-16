"""The Impress action: an interaction skill check that imposes a condition.

The impresser picks an **impression** (what success does — Demoralized, Distracted,
…) and an **interaction skill** (what is rolled), freely paired. Everyone targeted
resists with the same skill, Insight or Will — by default whichever is highest on
their own sheet. The impress check's total is the DC each of them resists, adjusted
per target; the degree a target fails by picks a rung of the impression's ladder,
and :mod:`.targeting` applies it.

The GM sets the situational modifiers (``actions.json``'s ``modifiers``), and a check
aimed at more than one target takes the group penalty. Everything about the action —
the impressions and their ladders, the skills, the resist options, the modifiers — is
data; this module only interprets it.

It also defines the two small records that travel between the seats while an
attempt is being set up (:class:`ImpressAttempt`, :class:`ResistAnswer`). The session
layer carries them as opaque payloads and never reads them, so their shape lives here
with the rules that give it meaning.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace

from ..character import Character
from ..data_loader import ActionModifier, GameData, Impression, ResistanceOutcome
from .derived import all_advantage_selections, own_skill_rows
from .rolls import (
    KIND_BEST_OF,
    KIND_IMPRESS,
    KIND_RESISTANCE,
    KIND_SKILL,
    LADDER_IMPRESS,
    RollSpec,
    SpecLadder,
    best_of_key,
    best_own_rolls,
    skill_roll,
)
from .targeting import ladder_captions, outcome_rung

__all__ = [
    "MODE_RESOLVE",
    "MODE_REQUEST",
    "TraitChoice",
    "ImpressAttempt",
    "ResistAnswer",
    "impression_allowed",
    "impress_skill_choices",
    "resist_candidates",
    "resist_choices",
    "best_choice",
    "modifier_value",
    "modifier_total",
    "modifier_lines",
    "group_penalty",
    "impress_label",
    "impression_outcomes",
    "impress_resist_spec",
    "impress_ask_resist_spec",
    "impress_spec",
    "impress_rung",
]

#: The GM rolls every resistance and applies what failing did.
MODE_RESOLVE = "resolve"
#: Only the impress check is rolled; each target is left a button to resist with.
MODE_REQUEST = "request"


@dataclass(frozen=True)
class TraitChoice:
    """One trait a character could roll for an action, with their number for it.

    ``kind`` is a :class:`~.rolls.RollSpec` kind (a skill or a resistance) and ``key``
    the trait as that kind names it — a skill *row id*, a resistance key.
    """

    kind: str
    key: str
    label: str
    modifier: int

    @property
    def ref(self) -> str:
        """A stable name for this choice, for a combo box or the wire."""
        return f"{self.kind}:{self.key}"


def impression_allowed(char: Character | None, game_data: GameData, impression: Impression) -> bool:
    """Whether *char* may make *impression* — only a gated one can be refused.

    An impression an advantage grants (Dazing Interaction's Dazed) needs that
    advantage on the sheet, bought or granted by a power. With no character to ask
    the answer is yes: the GM is the judge then.
    """

    if not impression.advantage:
        return True
    if char is None:
        return True
    record = next((a for a in game_data.advantages if a.id == impression.advantage), None)
    if record is None:
        return False
    return any(sel.name == record.name for sel in all_advantage_selections(char, game_data))


def impress_skill_choices(char: Character, game_data: GameData) -> list[TraitChoice]:
    """Every interaction skill row on *char*'s sheet, with their number for it.

    A skill's own row and each specialized pool it has (a Deception specialized in
    Bluffing is its own, higher row), in the ruleset's skill order.
    """

    choices: list[TraitChoice] = []
    for skill_id in game_data.impress.skills:
        skill = next((s for s in game_data.skills if s.id == skill_id), None)
        if skill is None:
            continue
        for row in own_skill_rows(char, skill):
            spec = skill_roll(char, game_data, row.row_id)
            choices.append(TraitChoice(KIND_SKILL, row.row_id, spec.label, spec.modifier))
    return choices


def _base_skill(row_id: str) -> str:
    """The skill a row id belongs to — a specialized pool resists as its skill."""
    return row_id.partition("::spec::")[0].partition("::")[0]


def resist_candidates(game_data: GameData, skill_row: str) -> tuple[tuple[str, str], ...]:
    """What resisting an impress check made with *skill_row* may be rolled with.

    ``(kind, trait key)`` pairs in the ruleset's order: the impresser's own skill
    (as the skill, not their specialization), then the further skills and
    resistances ``actions.json`` names. Duplicates are dropped, so impressing with
    Insight would not offer it twice.
    """

    options = game_data.impress.resist
    candidates: list[tuple[str, str]] = []
    if options.same_skill and skill_row:
        candidates.append((KIND_SKILL, _base_skill(skill_row)))
    for skill_id in options.skills:
        skill = next((s for s in game_data.skills if s.id == skill_id), None)
        if skill is not None:
            candidates.append((KIND_SKILL, skill.name))
    for key in options.resistances:
        if any(r.key == key for r in game_data.resistances):
            candidates.append((KIND_RESISTANCE, key))
    seen: set[tuple[str, str]] = set()
    unique = []
    for candidate in candidates:
        if candidate not in seen:
            seen.add(candidate)
            unique.append(candidate)
    return tuple(unique)


def resist_choices(char: Character, game_data: GameData, skill_row: str) -> list[TraitChoice]:
    """*char*'s number for each way of resisting an impress check made with *skill_row*."""

    key = best_of_key(resist_candidates(game_data, skill_row))
    return [
        TraitChoice(spec.kind, spec.trait_key, spec.label, spec.modifier)
        for spec in best_own_rolls(key, char, game_data)
    ]


def best_choice(choices: list[TraitChoice]) -> TraitChoice | None:
    """The highest of *choices* — "Use highest". Ties go to the first listed."""

    if not choices:
        return None
    return max(choices, key=lambda c: c.modifier)


def modifier_value(modifier: ActionModifier, raw: object) -> int:
    """What one modifier is worth at the control's position *raw*.

    A toggle is worth its value when on. A slider is worth its position, clamped to
    its range and snapped to its step — a value off the wire is not trusted to be on
    the scale.
    """

    if modifier.kind == "toggle":
        return modifier.value if raw else 0
    try:
        value = int(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0
    low, high = sorted((modifier.minimum, modifier.maximum))
    value = max(low, min(high, value))
    return low + (value - low) // modifier.step * modifier.step


def _active_values(
    game_data: GameData, values: Mapping[str, object]
) -> list[tuple[ActionModifier, int]]:
    """Each modifier that contributes, with its worth; one toggle per exclusive group."""

    taken: set[str] = set()
    active = []
    for modifier in game_data.impress.modifiers:
        worth = modifier_value(modifier, values.get(modifier.id))
        if not worth:
            continue
        if modifier.kind == "toggle" and modifier.exclusive:
            if modifier.exclusive in taken:
                continue
            taken.add(modifier.exclusive)
        active.append((modifier, worth))
    return active


def modifier_total(game_data: GameData, values: Mapping[str, object]) -> int:
    """The sum of the GM's situational modifiers at *values* (``id -> position``)."""

    return sum(worth for _modifier, worth in _active_values(game_data, values))


def modifier_lines(game_data: GameData, values: Mapping[str, object]) -> list[str]:
    """Each contributing modifier as "Impress as a full action +5", for a tooltip."""

    return [f"{modifier.label} {worth:+d}" for modifier, worth in _active_values(game_data, values)]


def group_penalty(game_data: GameData, target_count: int) -> int:
    """The group-impression penalty for *target_count* targets — 0 for one or none."""

    return game_data.impress.group_penalty if target_count > 1 else 0


def impress_label(impression: Impression, skill_label: str) -> str:
    """What an impress check is called in the history: "Impress: Demoralized (Deception)"."""

    return (
        f"Impress: {impression.name} ({skill_label})"
        if skill_label
        else f"Impress: {impression.name}"
    )


def impression_outcomes(game_data: GameData, impression: Impression) -> tuple[str, ...]:
    """*impression*'s ladder in words, one entry per degree of failure."""

    return ladder_captions(impression.outcomes, game_data)


def impress_resist_spec(
    game_data: GameData,
    impression: Impression,
    skill_row: str,
    *,
    source: str = "",
) -> RollSpec:
    """The roll resisting *impression* — whichever resist option is highest.

    A :data:`~.rolls.KIND_BEST_OF` spec naming the candidates, so whoever clicks it
    gets their own best number filled in (:func:`~.rolls.localize_spec`), and the
    ladder in words so the card says what failing did. Its DC is the impress check's
    total (``dc_from_total``), which only exists once that check is rolled.
    """

    against = f" vs. {source}" if source else ""
    return RollSpec(
        label=f"Resist {impression.name}{against}",
        kind=KIND_BEST_OF,
        trait_key=best_of_key(resist_candidates(game_data, skill_row)),
        outcomes=impression_outcomes(game_data, impression),
        rolled_by_target=True,
        dc_from_total=True,
        ladder=SpecLadder(LADDER_IMPRESS, impression.outcomes),
    )


def impress_ask_resist_spec(
    game_data: GameData,
    impression: Impression,
    skill_row: str,
    dc: int,
    *,
    target: str,
    action_id: str,
    source: str = "",
    choice: TraitChoice | None = None,
    title: str = "",
) -> RollSpec:
    """The resistance the GM asks one **player** for, once the impress check is in.

    :func:`impress_resist_spec` with the check's total already in as its *dc* (the
    per-target adjustment included), naming the player in ``targets`` and carrying the
    action's id, so the roll goes back to the GM's table. With *choice* — the GM picked
    how this target resists — it asks for that trait alone; otherwise the player's best
    is filled in on their own sheet, and they may pick another.
    """

    spec = replace(
        impress_resist_spec(game_data, impression, skill_row, source=source),
        dc=dc,
        dc_from_total=False,
        targets=(target,),
        action_id=action_id,
        title=title,
        hint="The GM's table applies what failing does.",
    )
    if choice is not None:
        spec = replace(
            spec,
            kind=choice.kind,
            trait_key=choice.key,
            label=f"{spec.label} ({choice.label})",
        )
    return spec


def impress_spec(
    game_data: GameData,
    impression: Impression,
    skill: TraitChoice,
    modifier: int,
    *,
    source: str = "",
    request: bool = False,
    hint: str = "",
) -> RollSpec:
    """The impress check itself, at its final *modifier* (skill plus every adjustment).

    With *request* the spec carries the resistance as its follow-up, so every card in
    the history offers it as a button whose DC is this check's total — the "request
    only" way of running the action, and the only one off the air.
    """

    spec = RollSpec(
        label=impress_label(impression, skill.label),
        modifier=modifier,
        kind=KIND_IMPRESS,
        hint=hint,
    )
    if request:
        spec = replace(
            spec, follow_up=impress_resist_spec(game_data, impression, skill.key, source=source)
        )
    return spec


def impress_rung(impression: Impression, degree: int | None) -> ResistanceOutcome | None:
    """The rung of *impression* a resistance check of *degree* lands on."""

    return outcome_rung(impression.outcomes, degree)


# -- what travels between seats -------------------------------------------------


def _text(raw: Mapping[str, object], key: str) -> str:
    value = raw.get(key, "")
    return value if isinstance(value, str) else ""


def _int(raw: Mapping[str, object], key: str) -> int:
    value = raw.get(key, 0)
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


@dataclass(frozen=True)
class ImpressAttempt:
    """A declared Impress, as a player's app sends it to the GM.

    ``targets`` are scene refs — the opaque ids every seat already sees on the board —
    so a player can name a creature without learning anything the board does not
    show. ``skill_modifier`` is the impresser's own number off their own sheet, which
    is the one copy guaranteed current.

    An attempt with no ``impression`` is a **bare** declaration: the player only said
    they are impressing (they chose to leave the choices to the GM, or the GM keeps
    them), so the GM picks the impression, the skill and the targets.
    """

    action_id: str
    impression: str
    skill_row: str
    skill_label: str
    skill_modifier: int
    targets: tuple[str, ...] = ()
    mode: str = MODE_RESOLVE
    source_name: str = ""
    source_ref: str = ""

    @property
    def bare(self) -> bool:
        """Whether the GM is to choose the impression and the skill."""
        return not self.impression

    def to_dict(self) -> dict:
        return {
            "action_id": self.action_id,
            "impression": self.impression,
            "skill_row": self.skill_row,
            "skill_label": self.skill_label,
            "skill_modifier": self.skill_modifier,
            "targets": list(self.targets),
            "mode": self.mode,
            "source_name": self.source_name,
            "source_ref": self.source_ref,
        }

    @classmethod
    def from_dict(cls, raw: object) -> ImpressAttempt | None:
        """Parse a payload from another seat, or ``None`` when it is unusable."""

        if not isinstance(raw, Mapping):
            return None
        action_id = _text(raw, "action_id")
        impression = _text(raw, "impression")
        if not action_id:
            return None
        targets = raw.get("targets", [])
        mode = _text(raw, "mode")
        return cls(
            action_id=action_id,
            impression=impression,
            skill_row=_text(raw, "skill_row"),
            skill_label=_text(raw, "skill_label"),
            skill_modifier=_int(raw, "skill_modifier"),
            targets=(
                tuple(t for t in targets if isinstance(t, str)) if isinstance(targets, list) else ()
            ),
            mode=mode if mode in (MODE_RESOLVE, MODE_REQUEST) else MODE_RESOLVE,
            source_name=_text(raw, "source_name"),
            source_ref=_text(raw, "source_ref"),
        )


@dataclass(frozen=True)
class ResistAnswer:
    """A targeted player's choice of how to resist, sent back to the GM.

    ``use_highest`` is the default answer; otherwise ``choice`` is a
    :attr:`TraitChoice.ref`. ``label``/``modifier`` are the chosen trait as the
    player's own sheet reads it.
    """

    action_id: str
    use_highest: bool = True
    choice: str = ""
    label: str = ""
    modifier: int = 0
    options: tuple[TraitChoice, ...] = field(default=())

    def to_dict(self) -> dict:
        return {
            "action_id": self.action_id,
            "use_highest": self.use_highest,
            "choice": self.choice,
            "label": self.label,
            "modifier": self.modifier,
            "options": [
                {"kind": o.kind, "key": o.key, "label": o.label, "modifier": o.modifier}
                for o in self.options
            ],
        }

    @classmethod
    def from_dict(cls, raw: object) -> ResistAnswer | None:
        if not isinstance(raw, Mapping):
            return None
        action_id = _text(raw, "action_id")
        if not action_id:
            return None
        options = []
        for entry in raw.get("options", []) if isinstance(raw.get("options"), list) else []:
            if isinstance(entry, Mapping) and _text(entry, "key"):
                options.append(
                    TraitChoice(
                        _text(entry, "kind"),
                        _text(entry, "key"),
                        _text(entry, "label") or _text(entry, "key"),
                        _int(entry, "modifier"),
                    )
                )
        use_highest = raw.get("use_highest", True)
        return cls(
            action_id=action_id,
            use_highest=use_highest if isinstance(use_highest, bool) else True,
            choice=_text(raw, "choice"),
            label=_text(raw, "label"),
            modifier=_int(raw, "modifier"),
            options=tuple(options),
        )

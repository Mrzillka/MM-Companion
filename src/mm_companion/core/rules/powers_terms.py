"""Effect game-term summaries: effective stats, stat rows, readouts, one-liners."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, replace

from ..character import Character
from ..data_loader import GameData, Modifier
from ..powers import STRUCTURE_ARRAY, STRUCTURE_LINKED, Power, PowerEffectInstance
from ..registry import Registry
from .appliers import trait_category
from .derived import effective_ability
from .powers_cost import array_alternate_cost, array_base_index, effect_effective_rank
from .runtime import _resolved_trait_target, _trait_name


def _effect_name(effect: PowerEffectInstance, game_data: GameData) -> str:
    """The display name of an effect's base, falling back to its raw id."""

    base = next((e for e in game_data.effects if e.id == effect.effect_id), None)
    return base.name if base else effect.effect_id


# The base game-term fields, in display order, with their table labels.
_STAT_FIELDS = (
    ("effect_type", "Type"),
    ("range", "Range"),
    ("action", "Action"),
    ("duration", "Duration"),
    ("check", "Check"),
    ("resistance", "Resistance"),
)

# The six standard game-term keys a Dev-mode override targets directly on the stats
# dict (all others are row-level overrides applied to the assembled table rows).
_STANDARD_STAT_KEYS = tuple(key for key, _ in _STAT_FIELDS)

# The ``change`` tag a homerule override carries, so the UI can tint an overridden
# cell distinctly from a modifier's "better"/"worse".
HOMERULE_TINT = "homerule"


def _override_value(effect: PowerEffectInstance, key: str, order: str) -> str | None:
    """The Dev-mode override string for ``key`` when it is set with ``order``.

    ``order`` is ``"before"`` (folded into the base the modifiers build on) or
    ``"after"`` (applied last, winning over every modifier); a stored override with
    no explicit order is treated as ``"after"``. Returns ``None`` when there is no
    matching override.
    """

    entry = (effect.overrides or {}).get(key)
    if entry and entry.get("order", "after") == order:
        return str(entry.get("value", ""))
    return None


@dataclass(frozen=True)
class EffectStat:
    """One row of an effect's game-term table (see :func:`effect_stat_rows`).

    ``base`` is the unmodified value and ``value`` the current one; ``change`` is
    ``"better"`` when an extra improved the field (the UI tints it green),
    ``"worse"`` when a flaw limited it (red), or ``""`` when it is unchanged or set
    by a neutral player choice.
    """

    key: str
    label: str
    base: str
    value: str
    change: str = ""


@dataclass(frozen=True)
class EffectImpact:
    """Modifier-derived game-term adjustments that aren't a plain field override.

    Gathered alongside the stat dicts by :func:`_effective_stats`. ``grants_attack``
    is set when a modifier gives the effect its attack roll — an attacking effect's
    implicit ``attack`` extra, or one taken explicitly on any other effect — and is
    what :func:`~mm_companion.core.rules.effect_makes_attack` reads rather than
    sniffing the check prose; ``check_bonus`` is the net signed number modifiers add
    to the effect's attack roll (Accurate ``+2``/rank, Inaccurate ``-2``/rank);
    ``drops_check`` is set when a modifier removes the attack roll entirely
    (Perception Range), and cancels ``grants_attack``; ``check_notes`` are
    parentheticals a modifier appends to the check row (Area's Dodge-for-half); and
    ``notes`` names every attached modifier with no other visible game-term impact,
    so the table can list them and nothing an effect carries goes unseen.
    """

    check_bonus: int = 0
    grants_attack: bool = False
    drops_check: bool = False
    check_notes: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()


def _step_along(ladder: tuple[str, ...], value: str, step: int) -> str:
    """Move ``value`` ``step`` positions along ``ladder`` (clamped to its ends).

    Returns ``value`` unchanged when it isn't on the ladder or ``step`` is zero, so
    a stepping modifier on a value the ladder doesn't cover is simply a no-op.
    """

    if not ladder or not step or value not in ladder:
        return value
    index = ladder.index(value) + step
    return ladder[max(0, min(len(ladder) - 1, index))]


def modifier_detail(modifier: Modifier, selection) -> str:
    """The free-text detail a player typed into a modifier's text config field.

    A modifier like Limited or Quirk carries a single ``"text"`` config field for
    the player to describe *how* it applies. Returns the first non-empty such value
    (e.g. ``"only at night"``), or ``""`` if none was entered. Used to qualify a
    modifier's displayed name as ``"Limited (only at night)"`` wherever it is
    listed, so a bare ``"Limited"`` never hides the circumstance the player chose.
    """

    for cfg in modifier.config_fields:
        if cfg.type == "text":
            value = str(selection.config.get(cfg.key, "")).strip()
            if value:
                return value
    return ""


def modifier_label(modifier: Modifier, selection, *, rank_sep: str = " ") -> str:
    """A modifier's display name, qualified with its rank and free-text detail.

    A ranked modifier above rank 1 gains its rank (``"Penetrating 3"``); a modifier
    with a typed text detail gains it in parentheses (``"Limited (only at night)"``).
    ``rank_sep`` separates the name from the rank (the card uses ``" ×"``).

    A blank Custom modifier leads with the player's typed name instead of the generic
    record name (``"Custom Extra"``), so the homebrew shows up under the name the player
    gave it; it falls back to the record name until one is typed.
    """

    detail = modifier_detail(modifier, selection)
    base = detail if modifier.custom and detail else modifier.name
    label = base
    if modifier.ranked and selection.rank > 1:
        label = f"{base}{rank_sep}{selection.rank}"
    if detail and not (modifier.custom and detail):
        label = f"{label} ({detail})"
    return label


def _config_trait_name(game_data: GameData | None, value: str) -> str:
    """A stored config value shown as a trait name — ``"AGL"`` → ``"Agility"``.

    Falls through :func:`_trait_name` (abilities/resistances/skills) to the derived
    stats ``system.json`` names, so a checkable-but-unbuyable stat like Initiative
    still reads by its label. Anything else (a free-text value) passes through.
    """

    if game_data is None:
        return value
    for trait in game_data.system.derived_traits:
        if trait.key == value:
            return trait.label
    return _trait_name(game_data, value)


def _render_note(
    modifier: Modifier,
    rank: int,
    selection=None,
    game_data: GameData | None = None,
) -> str:
    """A modifier's :attr:`note_template` with its placeholders resolved.

    ``{n}`` becomes the effect's ``rank`` times the modifier's ``note_per_rank`` (or the
    bare rank when that is zero) — Empowering's ``notePerRank`` of 15 turns a rank-4
    Affliction's note into "transformed form gains 60 power points".

    With a ``selection``, three more placeholders resolve: ``{rank}`` is the modifier's
    own rank, ``{dc}`` the difficulty it sets (the system's base DC plus that rank —
    Check Required's "DC 10 + the flaw's rank"), and ``{<config key>}`` any value the
    player chose on the chip, with a trait key rendered as its display name so a stored
    ``"AGL"`` reads as "Agility".
    """

    value = rank * modifier.note_per_rank if modifier.note_per_rank else rank
    text = modifier.note_template.replace("{n}", str(value))
    if selection is None:
        return text
    dc_base = game_data.system.defense_dc_base if game_data else 10
    text = text.replace("{rank}", str(selection.rank))
    text = text.replace("{dc}", str(dc_base + selection.rank))
    for key, chosen in selection.config.items():
        text = text.replace(f"{{{key}}}", _config_trait_name(game_data, str(chosen)))
    # A placeholder the player hasn't filled in yet drops out rather than showing its
    # braces, so an unconfigured Check Required still reads as a sentence.
    text = re.sub(r"\s*\{[^{}]*\}", "", text).strip()
    return text[:1].upper() + text[1:] if text else text


def required_checks(
    effect: PowerEffectInstance, game_data: GameData
) -> tuple[tuple[str, int | None], ...]:
    """The extra checks the effect's modifiers demand before it works, with their DCs.

    A flaw flagged ``requiresCheck`` (Check Required) makes using the effect an extra
    roll that can simply fail, so it is a *roll*, not a footnote — each one renders
    from the modifier's ``note_template`` (``"{trait} check, DC {dc}"``) and gets its
    own game-term row, which is what puts it in the power card's dice footer beside the
    attack and the save.

    Each entry is ``(rendered note, DC)``. The DC is the same number
    :func:`_render_note` writes into the sentence (the system's base DC plus the
    flaw's rank), returned alongside it so the dice roller does not have to read it
    back out of the prose.
    """

    catalog = game_data.modifier_catalog()
    checks: list[tuple[str, int | None]] = []
    for selection in (*effect.extras, *effect.flaws):
        modifier = catalog.get(selection.modifier_id)
        if modifier is None or not modifier.requires_check:
            continue
        rendered = _render_note(modifier, effect.rank, selection, game_data)
        dc = game_data.system.defense_dc_base + selection.rank
        checks.append((rendered or modifier_label(modifier, selection), dc))
    return tuple(checks)


def required_check_notes(effect: PowerEffectInstance, game_data: GameData) -> tuple[str, ...]:
    """Just the note text of every :func:`required_checks` entry."""

    return tuple(note for note, _dc in required_checks(effect, game_data))


def _modifier_notes(
    effect: PowerEffectInstance, catalog: dict, impactful: set[str]
) -> tuple[str, ...]:
    """Notes for the effect's attached modifiers that produced no visible stat change.

    Skips the ids in ``impactful`` (those already reflected in a stat cell) so the
    Notes row lists only what would otherwise be invisible. A modifier with a
    ``note_template`` contributes that rendered line (Empowering's bonus-point tally);
    otherwise it is listed by name — a ranked modifier taken above rank 1 carries its
    rank (e.g. ``"Penetrating 3"``), and one with a typed detail carries it
    (``"Limited (only at night)"``).
    """

    notes: list[str] = []
    for selection in (*effect.extras, *effect.flaws):
        modifier = catalog.get(selection.modifier_id)
        if modifier is None or selection.modifier_id in impactful:
            continue
        if modifier.note_template:
            notes.append(_render_note(modifier, effect.rank))
        else:
            notes.append(modifier_label(modifier, selection))
    return tuple(notes)


def _effective_stats(
    effect: PowerEffectInstance, game_data: GameData
) -> tuple[dict[str, str], dict[str, str], dict[str, str], EffectImpact]:
    """``(base, effective, change, impact)`` for an effect's game-term fields.

    ``base`` is the unmodified stat, ``effective`` has each modifier and config
    override applied (extras-then-flaws, so a later one wins, then config choices),
    and ``change`` records how the final value differs from the base: ``"better"``
    (an extra changed it), ``"worse"`` (a flaw), or ``""`` (unchanged or a neutral
    config choice). ``impact`` collects the modifier effects that aren't a field
    replacement (attack-roll bonus, dropped/noted check, plus the Notes list). Empty
    dicts and a blank :class:`EffectImpact` for an unknown effect id.

    The base effect's own
    :attr:`~mm_companion.core.data_loader.Effect.implicit_modifiers` are folded into
    ``base`` itself — they are part of the effect's definition, not a player's choice,
    so a Damage reads exactly as if its "Attack vs. Defense" check were still written
    on the record: no tint, no note, no chip, no cost. Only their ``overrides`` and
    ``grants_attack`` apply; stepping, check bonuses and check notes are for attached
    modifiers, whose ordering and action-floor interplay the loop below handles.
    """

    base_effect = next((e for e in game_data.effects if e.id == effect.effect_id), None)
    if base_effect is None:
        return {}, {}, {}, EffectImpact()
    base = {
        "effect_type": base_effect.effect_type,
        "range": base_effect.range_,
        "action": base_effect.action,
        "duration": base_effect.duration,
        "check": base_effect.check or "",
        "resistance": base_effect.resistance or "",
    }
    ladders = game_data.game_term_ladders
    catalog = game_data.modifier_catalog()

    grants_attack = False
    for modifier_id in base_effect.implicit_modifiers:
        modifier = catalog.get(modifier_id)
        if modifier is None:
            continue
        for key, value in modifier.overrides.items():
            if key in base:
                base[key] = value
        grants_attack = grants_attack or modifier.grants_attack

    # Dev-mode "before" overrides replace the base value the modifiers then build on,
    # so an extra/flaw can still step or re-override it (standard game-term fields
    # only — other override keys are row-level, handled in ``effect_stat_rows``).
    for key in _STANDARD_STAT_KEYS:
        before = _override_value(effect, key, "before")
        if before is not None:
            base[key] = before

    stats = dict(base)
    change = dict.fromkeys(base, "")

    check_bonus = 0
    drops_check = False
    check_notes: list[str] = []
    impactful: set[str] = set()  # ids reflected in a stat cell — kept out of Notes
    # An action step (Increased/Reduced Action) is deferred and applied below, after
    # the free-action floor a Sustained duration imposes, so it steps from that floor
    # rather than from a Permanent effect's bare "None".
    action_step = 0
    action_step_tint = ""

    for selection in (*effect.extras, *effect.flaws):
        modifier = catalog.get(selection.modifier_id)
        if modifier is None:
            continue
        tint = "better" if modifier.category == "extra" else "worse"
        touched = False
        for key, value in modifier.overrides.items():
            if key in stats:
                stats[key] = value
                change[key] = tint
                touched = True
        if modifier.step_field == "action":
            action_step += modifier.step_by
            if modifier.step_by:
                action_step_tint = tint
            touched = True
        elif modifier.step_field in stats:
            stepped = _step_along(
                ladders.get(modifier.step_field, ()), stats[modifier.step_field], modifier.step_by
            )
            if stepped != stats[modifier.step_field]:
                stats[modifier.step_field] = stepped
                change[modifier.step_field] = tint
            touched = True
        if modifier.check_bonus:
            check_bonus += modifier.check_bonus * (
                selection.rank if modifier.ranked else effect.rank
            )
            touched = True
        if modifier.grants_attack:
            grants_attack = True
            touched = True
        if modifier.drops_check:
            drops_check = True
            touched = True
        if modifier.check_note:
            check_notes.append(modifier.check_note)
            touched = True
        if modifier.distance_rank_bonus:
            # Extended Range reaches further — visible in the Distance row, so it is
            # spoken for and doesn't also need listing as a bare Note.
            touched = True
        if modifier.requires_check:
            touched = True  # gets its own roll row (see :func:`required_check_notes`)
        if touched:
            impactful.add(selection.modifier_id)

    # Config choices that name a stat replace it (e.g. Affliction's chosen
    # resistance). These are neutral player choices, not modifiers, so they carry
    # no better/worse tint.
    for field in base_effect.config_fields:
        if field.overrides and field.overrides in stats:
            value = effect.config.get(field.key)
            if value:
                stats[field.overrides] = _config_display(field, value)
                change[field.overrides] = ""

    # A Sustained effect must be toggled on and maintained with at least a free
    # action, so its action is floored by the one its (possibly modified) duration
    # implies — a Permanent effect made toggleable by the Sustained extra comes with
    # action "None". The floor is the baseline an Increased/Reduced Action step then
    # moves from, and a hard minimum afterwards (a step can't push below it). The
    # floor itself is a rule consequence, not a modifier win, so it carries no tint.
    action_ladder = ladders.get("action", ())
    floor = game_data.duration_action_floor.get(stats["duration"])

    def _floor_action() -> None:
        if floor in action_ladder and stats["action"] in action_ladder:
            if action_ladder.index(stats["action"]) < action_ladder.index(floor):
                stats["action"] = floor

    _floor_action()  # baseline the action step moves from
    if action_step:
        stepped = _step_along(action_ladder, stats["action"], action_step)
        if stepped != stats["action"]:
            stats["action"] = stepped
            change["action"] = action_step_tint
        _floor_action()  # hard minimum: a step can't drop below the free-action floor

    # A modifier that lands the value back on its base isn't really a change.
    for key in change:
        if stats[key] == base[key]:
            change[key] = ""

    # Dev-mode overrides. An "after" override wins over every modifier; a "before"
    # override that no modifier ended up moving still reads as a homerule edit, so
    # both carry the homerule tint unless a modifier already tinted the field.
    for key in _STANDARD_STAT_KEYS:
        after = _override_value(effect, key, "after")
        if after is not None:
            stats[key] = after
            change[key] = HOMERULE_TINT
        elif _override_value(effect, key, "before") is not None and not change[key]:
            change[key] = HOMERULE_TINT

    impact = EffectImpact(
        check_bonus=check_bonus,
        grants_attack=grants_attack,
        drops_check=drops_check,
        check_notes=tuple(check_notes),
        notes=_modifier_notes(effect, catalog, impactful),
    )
    return base, stats, change, impact


def effective_effect_stats(effect: PowerEffectInstance, game_data: GameData) -> dict[str, str]:
    """The base effect's game-term stats with its modifiers' overrides applied.

    Starts from the effect's own ``effect_type``/``range``/``action``/``duration``/
    ``check``/``resistance`` and lets each attached modifier's
    :attr:`~mm_companion.core.data_loader.Modifier.overrides` replace fields — e.g.
    Ranged forces ``range`` to ``"Ranged"``. Modifiers apply extras-then-flaws, so a
    later one wins. Returns ``{}`` for an unknown effect id.
    """

    return _effective_stats(effect, game_data)[1]


# The actor's own roll in a check/resistance phrase ("Attack vs. …", "Effect vs. …")
# — the leading word before "vs." — is the effect's own d20 bonus, its rank.
_ACTOR_ROLL = re.compile(r"^(?:Attack|Deflect|Effect) vs\.")


def _numeric_roll(text: str, actor_bonus: int, dc: int | None, *, resistance: bool) -> str:
    """Fill an effect's attack bonus / save DC into a check or resistance phrase.

    The actor's own roll (the ``Attack``/``Deflect``/``Effect`` before ``vs.``)
    becomes ``actor_bonus`` — the effect rank, plus any Accurate/Inaccurate
    adjustment — so ``"Attack vs. Defense"`` reads ``"8 vs. Defense"``. A resisted
    threshold (``"Effect"`` / ``"Effect DC"`` after ``vs.``) becomes the save
    ``dc``, so ``"Toughness vs. Effect"`` reads ``"Toughness vs. 18"``. A bare
    resistance name a config override left behind (e.g. Affliction's ``"Will"``)
    gets the DC appended. ``dc`` is ``None`` for effects that impose no save DC (the
    phrase's threshold is then left as prose).
    """

    if not text:
        return text
    if dc is not None:
        text = text.replace("Effect DC", f"DC {dc}")
        text = re.sub(r"vs\. Effect\b", f"vs. {dc}", text)
    text = _ACTOR_ROLL.sub(f"{actor_bonus} vs.", text)
    if resistance and dc is not None and " vs. " not in text:
        text = f"{text} vs. DC {dc}"
    return text


def _measure_value(measure, rank: int, game_data: GameData) -> str:
    """The imperial measurement label for a rank, with a ``/round`` suffix for a speed.

    Metric is deferred to a settings toggle — this reads the ``imperial`` column for
    now. Returns ``""`` when the rank is outside the tabulated range.
    """

    label = game_data.measurements.label(measure.column, rank)
    if not label:
        return ""
    return f"{label}/round" if measure.per_round else label


def resolve_stat_display(
    effect: PowerEffectInstance,
    game_data: GameData,
    field_key: str,
    raw_value: str,
    char: Character | None = None,
    attack_bonus: int | None = None,
) -> str:
    """Resolve one raw game-term value to the concrete form the table would show.

    The same numeric substitution :func:`effect_stat_rows` applies, for a single
    field and a candidate value: a ``"Rank"`` range becomes its measured distance, a
    check/resistance phrase gets the effect's save DC (``resistance_dc_base`` plus the
    effective rank) and — for an attack check — the wielder's Attack filled in. Fields
    without a numeric form (Type/Action/Duration) come back unchanged, as does a value
    the effect can't resolve. This lets the Dev-mode dropdowns offer real numbers
    (``"Will vs. 18"``) rather than bare templates (``"Will vs. Effect"``).
    """

    if not raw_value:
        return raw_value
    base_effect = next((e for e in game_data.effects if e.id == effect.effect_id), None)
    if base_effect is None:
        return raw_value
    if field_key == "range" and raw_value == "Rank":
        return game_data.measurements.label("distance", effect.rank) or "Rank"
    effective_rank = effect_effective_rank(effect, game_data, char)
    dc = (
        None
        if base_effect.resistance_dc_base is None
        else base_effect.resistance_dc_base + effective_rank
    )
    if field_key == "check":
        if attack_bonus is not None:
            attack = attack_bonus
        elif char is not None:
            attack = effective_ability(char, game_data, game_data.system.trait_keys.attack)
        else:
            attack = effect.rank
        actor = attack if raw_value.startswith("Attack") else effect.rank
        return _numeric_roll(raw_value, actor, dc, resistance=False)
    if field_key == "resistance":
        return _numeric_roll(raw_value, effect.rank, dc, resistance=True)
    return raw_value


def _distance_rank_bonus(effect: PowerEffectInstance, game_data: GameData) -> int:
    """Distance ranks the effect's modifiers add to its reach (Extended Range's ranks)."""
    catalog = game_data.modifier_catalog()
    bonus = 0
    for selection in (*effect.extras, *effect.flaws):
        modifier = catalog.get(selection.modifier_id)
        if modifier is not None and modifier.distance_rank_bonus:
            bonus += modifier.distance_rank_bonus * (selection.rank if modifier.ranked else 1)
    return bonus


def ranged_distance_ranks(
    effect: PowerEffectInstance, game_data: GameData, char: Character | None = None
) -> tuple[int, int] | None:
    """``(base rank, effective rank)`` an effect reaches, or ``None`` if it isn't ranged.

    A ranged effect's reach is a distance rank — by default the effect's own effective
    rank, though an effect whose reach doesn't scale that way overrides the derivation
    in its ``rangeDistance`` block. The effective rank adds whatever the attached
    modifiers extend it by (Extended Range), so the two returned ranks differ exactly
    when the reach was bought up and the Distance row should read as improved.

    ``None`` for a Close, Personal or Perception effect — those have no distance to
    state — which also covers a Ranged effect a flaw has since brought back to Close.
    """

    base_effect = next((e for e in game_data.effects if e.id == effect.effect_id), None)
    spec = base_effect.range_distance if base_effect else None
    if base_effect is None or spec is None:
        return None
    if effective_effect_stats(effect, game_data).get("range") != spec.range_value:
        return None
    source = effect_effective_rank(effect, game_data, char) if spec.rank is None else spec.rank
    base_rank = source + spec.offset
    return base_rank, base_rank + _distance_rank_bonus(effect, game_data)


def ranged_distance_increments(rank: int, game_data: GameData, spec) -> str:
    """The reach's range increments — ``"short 200 feet / medium 400 feet / long 800 feet"``.

    One entry per ``spec.steps`` offset from ``rank``, each named by the matching
    ``spec.step_labels`` entry. The names sit in the value rather than the row's label
    because the summary panel lays labels out in a fixed column, and a label long
    enough to spell them pushes the value column off the panel. Steps that fall off the
    ends of the measurement table are dropped rather than rendered blank.
    """

    parts = []
    for index, step in enumerate(spec.steps):
        measure = game_data.measurements.label("distance", rank + step)
        if not measure:
            continue
        name = spec.step_labels[index] if index < len(spec.step_labels) else ""
        parts.append(f"{name} {measure}".strip())
    return " / ".join(parts)


def _ranged_distance_rows(
    effect: PowerEffectInstance,
    game_data: GameData,
    char: Character | None,
    base_effect,
) -> list[EffectStat]:
    """The Distance and Measurements rows that follow a Ranged effect's Range row.

    "Ranged" on its own says nothing about how far, so the reach is spelled out: the
    distance rank, then that rank's range increments as real measurements. The rank row
    reads as improved when a modifier bought the reach up past its base.
    """

    ranks = ranged_distance_ranks(effect, game_data, char)
    if ranks is None:
        return []
    base_rank, rank = ranks
    spec = base_effect.range_distance
    rows = [
        EffectStat(
            "distance_rank",
            "Distance",
            f"rank {base_rank}",
            f"rank {rank}",
            "better" if rank > base_rank else "",
        )
    ]
    increments = ranged_distance_increments(rank, game_data, spec)
    if increments:
        rows.append(EffectStat("distance", "Measurements", "", increments, ""))
    return rows


@dataclass(frozen=True)
class EffectRollNumbers:
    """The raw d20 numbers behind an effect's check and resistance phrases.

    :func:`effect_stat_rows` renders those numbers *into prose* (``"8 vs. Defense"``,
    ``"Toughness vs. 18"``), which is right for a summary table and useless for
    actually rolling one. Both read this record, so the dice roller gets the same
    numbers the card shows without regexing them back out of the sentence.

    ``attack`` is the wielder's attack-trait bonus (or a linked combat focus's
    total), and ``check_actor`` is what the check row actually rolls: ``attack`` for
    an "Attack vs. …" phrase, the effect's rank for an "Effect"/"Deflect vs. …" one,
    plus any Accurate/Inaccurate adjustment. ``dc`` is the save DC the effect imposes
    (``resistance_dc_base`` plus the *effective* rank, so a Strength-Based Damage
    folds in Strength), or ``None`` for an effect that imposes none. ``drops_check``
    is set when a modifier removed the attack roll entirely (Perception Range).
    """

    attack: int = 0
    check_actor: int = 0
    dc: int | None = None
    drops_check: bool = False


def effect_roll_numbers(
    effect: PowerEffectInstance,
    game_data: GameData,
    char: Character | None = None,
    attack_bonus: int | None = None,
) -> EffectRollNumbers:
    """The d20 numbers for one effect's rolls — see :class:`EffectRollNumbers`.

    ``char`` and ``attack_bonus`` mean exactly what they do for
    :func:`effect_stat_rows`, which shares this computation.
    """

    base_effect = next((e for e in game_data.effects if e.id == effect.effect_id), None)
    if base_effect is None:
        return EffectRollNumbers()
    base, stats, _change, impact = _effective_stats(effect, game_data)
    return _roll_numbers(effect, game_data, char, attack_bonus, base_effect, base, stats, impact)


def _roll_numbers(
    effect: PowerEffectInstance,
    game_data: GameData,
    char: Character | None,
    attack_bonus: int | None,
    base_effect,
    base: dict[str, str],
    stats: dict[str, str],
    impact: EffectImpact,
) -> EffectRollNumbers:
    """:func:`effect_roll_numbers` once the stat dicts are already in hand."""

    effective_rank = effect_effective_rank(effect, game_data, char)
    dc = (
        None
        if base_effect.resistance_dc_base is None
        else base_effect.resistance_dc_base + effective_rank
    )
    # The attacker's own d20 bonus. An "Attack vs. Defense" roll uses the character's
    # Attack (plus this power's Accurate/Inaccurate); an "Effect vs. …" / "Deflect
    # vs. …" phrase instead uses the effect's own rank. A linked combat focus
    # overrides the Attack via ``attack_bonus``. Without either we fall back to the
    # effect rank so a context-free summary still reads.
    if attack_bonus is not None:
        attack = attack_bonus
    elif char is not None:
        attack = effective_ability(char, game_data, game_data.system.trait_keys.attack)
    else:
        attack = effect.rank
    # Which of the two the check row uses is decided by the phrase, and an "after"
    # override replaces the phrase wholesale — read the base in that case, since the
    # override is verbatim text with no actor to substitute.
    phrase = stats.get("check") or base.get("check") or ""
    actor = attack if phrase.startswith("Attack") else effect.rank
    return EffectRollNumbers(
        attack=attack,
        check_actor=actor + impact.check_bonus,
        dc=dc,
        drops_check=impact.drops_check,
    )


def effect_stat_rows(
    effect: PowerEffectInstance,
    game_data: GameData,
    char: Character | None = None,
    attack_bonus: int | None = None,
) -> list[EffectStat]:
    """The effect's non-empty game-term fields as tintable table rows.

    Each :class:`EffectStat` carries its base and current value plus a ``change``
    tag, so the UI can render a small table and highlight the fields an extra
    improved (green) or a flaw limited (red). Numeric measures derived from the rank
    are filled in from ``measurements.json``: a ``"Rank"`` range becomes the actual
    distance, and an effect with a ``measure`` (movement speeds, leap distance) gets
    its own row. Modifier impacts beyond a field override are folded in too — an
    Accurate/Inaccurate bonus shifts (and tints) the attack roll, Perception Range
    drops the check row, Area annotates it — and every attached modifier with no
    other visible impact is gathered into a trailing ``Notes`` row. The configured
    qualities that don't override a stat (e.g. Affliction's condition degrees) are
    appended as untinted rows so the table stays a complete summary. Empty for an
    unknown effect id.

    When ``char`` is given, the numbers reflect the wielder: an attack roll shows the
    character's Attack (plus Accurate/Inaccurate) rather than the effect rank, and the
    resistance save DC uses the effective effect rank (a Strength-Based Damage folds in
    the wielder's Strength). Without a character both fall back to the effect rank, so a
    context-free summary still reads.

    ``attack_bonus`` overrides the attacker's base d20 bonus for an "Attack vs. …"
    phrase — an effect linked to a Close/Ranged Combat focus passes that focus's total
    (:func:`effect_attack_skill_bonus`) so the shown roll matches the PL check. ``None``
    keeps the default (the character's Attack ability, or the effect rank without one).
    """

    base_effect = next((e for e in game_data.effects if e.id == effect.effect_id), None)
    if base_effect is None:
        return []
    base, stats, change, impact = _effective_stats(effect, game_data)

    # A "Rank" range means "a distance equal to the effect's rank" — show the number
    # (in both base and current, so it isn't mistaken for a modifier change).
    for scope in (base, stats):
        if scope.get("range") == "Rank":
            scope["range"] = game_data.measurements.label("distance", effect.rank) or "Rank"

    # Resolve the check/resistance phrases to concrete numbers: the save DC is
    # ``base + effective rank`` (effective rank folds in a Strength-Based bonus), and
    # the attack roll uses the character's Attack (see :func:`_roll_numbers`).
    numbers = _roll_numbers(effect, game_data, char, attack_bonus, base_effect, base, stats, impact)
    dc = numbers.dc
    attack = numbers.attack

    def _actor(phrase: str, *, with_mods: bool) -> int:
        roll = attack if phrase.startswith("Attack") else effect.rank
        return roll + (impact.check_bonus if with_mods else 0)

    # An "after" override of the check/resistance is a verbatim manual value — keep
    # it out of the numeric substitution (and the Accurate/Inaccurate re-tint) below.
    check_overridden = _override_value(effect, "check", "after") is not None
    resistance_overridden = _override_value(effect, "resistance", "after") is not None
    base["check"] = _numeric_roll(
        base["check"], _actor(base["check"], with_mods=False), dc, resistance=False
    )
    base["resistance"] = _numeric_roll(base["resistance"], effect.rank, dc, resistance=True)
    if not check_overridden:
        stats["check"] = _numeric_roll(
            stats["check"], _actor(stats["check"], with_mods=True), dc, resistance=False
        )
    if not resistance_overridden:
        stats["resistance"] = _numeric_roll(stats["resistance"], effect.rank, dc, resistance=True)

    # Accurate/Inaccurate move the attack number — tint the check by the net sign.
    if not check_overridden and stats["check"] and impact.check_bonus:
        change["check"] = "better" if impact.check_bonus > 0 else "worse"
    # Area-style notes ride along on the current check value only (not the base).
    if not check_overridden and stats["check"] and impact.check_notes:
        stats["check"] = f"{stats['check']} ({'; '.join(impact.check_notes)})"

    rows = []
    for key, label in _STAT_FIELDS:
        if key == "check" and impact.drops_check:  # e.g. Perception Range — no attack roll
            continue
        if stats[key]:
            rows.append(EffectStat(key, label, base[key], stats[key], change[key]))
        if key == "range":
            rows.extend(_ranged_distance_rows(effect, game_data, char, base_effect))
    # An effect can impose a save DC without either a (shown) check or resistance
    # phrase to carry it — surface it in its own row so the number is never lost.
    check_shown = "" if impact.drops_check else stats["check"]
    if dc is not None and not check_shown and not stats["resistance"]:
        rows.append(EffectStat("effect_dc", "Effect DC", "", f"DC {dc}", ""))
    if base_effect.measure:
        value = _measure_value(base_effect.measure, effect.rank, game_data)
        if value:
            rows.append(EffectStat("measure", base_effect.measure.label, "", value, ""))
    for field in base_effect.config_fields:
        if field.overrides or field.type == "checkbox":
            continue  # a checkbox is a toggle or surfaced via a readout, not a value row
        value = effect.config.get(field.key)
        if value:
            rows.append(EffectStat(field.key, field.label, "", _config_display(field, value), ""))
    # A trait booster (Enhanced Trait, Protection) shows which trait it raises and by
    # how much — green, since it's an improvement — so the summary isn't blank.
    target = _resolved_trait_target(effect, base_effect)
    if target and trait_category(game_data, target):
        raised = f"{_trait_name(game_data, target)} +{effect.rank}"
        rows.append(EffectStat("enhances", "Enhances", "", raised, "better"))
    # Tier-5 derived readouts (Growth's size mods, Insubstantial's state, ...) — purely
    # computed information, appended as untinted (or sign-tinted) rows.
    rows.extend(effect_readout_rows(effect, game_data))
    # A Check Required-style flaw is a roll, not a footnote — its own row, which is
    # what carries it into the power card's dice footer.
    for note in required_check_notes(effect, game_data):
        rows.append(EffectStat("required_check", "Required check", "", note, "worse"))
    if impact.notes:
        rows.append(EffectStat("notes", "Notes", "", ", ".join(impact.notes), ""))
    return _apply_row_overrides(rows, effect)


def _apply_row_overrides(rows: list[EffectStat], effect: PowerEffectInstance) -> list[EffectStat]:
    """Apply the Dev-mode overrides that aren't one of the six standard stat fields.

    Every other override key is *row-level*: it replaces the value of the assembled
    row with that key (an ``effect_dc``, a ``measure``, a size/state/pool readout, a
    config quality) or, when no row carries the key, appends a fresh custom row using
    the override's stored ``label``. Overridden and added rows are tinted homerule.
    The six standard fields are handled in :func:`_effective_stats`, so they're skipped
    here.
    """

    extra = {
        key: entry
        for key, entry in (effect.overrides or {}).items()
        if key not in _STANDARD_STAT_KEYS
    }
    if not extra:
        return rows
    seen: set[str] = set()
    out: list[EffectStat] = []
    for row in rows:
        entry = extra.get(row.key)
        if entry is not None:
            out.append(replace(row, value=str(entry.get("value", "")), change=HOMERULE_TINT))
            seen.add(row.key)
        else:
            out.append(row)
    for key, entry in extra.items():
        if key in seen:
            continue
        label = entry.get("label") or key.replace("_", " ").title()
        out.append(EffectStat(key, label, "", str(entry.get("value", "")), HOMERULE_TINT))
    return out


def effect_readout_rows(effect: PowerEffectInstance, game_data: GameData) -> list[EffectStat]:
    """The effect's Tier-5 derived readout rows (``docs/mm-powers-ui-design.md`` §2 Tier 5).

    Reads the effect's entries in ``effect_readouts.json`` and renders each by its
    ``kind`` — a Growth/Shrinking size shift into its Size Table modifiers, an
    Insubstantial rank into its state name, a Communication rank into its range band,
    a Burrowing rank into per-terrain speeds, and so on. These are never editable, so
    the UI shows them as read-only rows. Empty when the effect has no readouts.
    """

    rows: list[EffectStat] = []
    for readout in game_data.effect_readouts.get(effect.effect_id, ()):
        rows.extend(_readout_rows(readout, effect, game_data))
    return rows


# One handler per readout ``kind`` (``docs/mm-powers-ui-design.md`` §2 Tier 5). Each takes
# ``(readout, effect, game_data)`` and returns the rendered rows. The base kinds are
# registered below; a mod's Python module can add a kind by registering another handler.
# An unregistered kind resolves to no rows (see :func:`_readout_rows`).
ReadoutHandler = Callable[[object, PowerEffectInstance, GameData], "list[EffectStat]"]
READOUT_KINDS: Registry[ReadoutHandler] = Registry("readout.kind")


@READOUT_KINDS.handler("size_table")
def _readout_size_table(readout, effect: PowerEffectInstance, game_data: GameData):
    rank = effect.rank
    data = readout.data
    sign = int(data.get("sign", 1))
    size = game_data.measurements.size_row(sign * rank)
    if size is None or rank <= 0:
        return []
    out = [EffectStat("size", readout.label or "Size", "", size.size_category, "")]
    for label, mod in (
        ("Defense", size.defense_mod),
        ("Damage", size.damage_mod),
        ("Toughness", size.toughness_mod),
        ("Speed", size.speed_mod),
        ("Intimidation", size.intimidation_mod),
        ("Stealth", size.stealth_mod),
    ):
        if mod:
            change = "better" if mod > 0 else "worse"
            out.append(EffectStat(f"size_{label.lower()}", label, "", f"{mod:+d}", change))
    return out


@READOUT_KINDS.handler("state")
def _readout_state(readout, effect: PowerEffectInstance, game_data: GameData):
    rank = effect.rank
    by_rank = {int(k): v for k, v in readout.data.get("byRank", {}).items()}
    if not by_rank:
        return []
    eligible = [k for k in sorted(by_rank) if k <= rank]
    chosen = by_rank[eligible[-1]] if eligible else by_rank[min(by_rank)]
    return [EffectStat(readout.label.lower() or "state", readout.label, "", chosen, "")]


@READOUT_KINDS.handler("measure_offsets")
def _readout_measure_offsets(readout, effect: PowerEffectInstance, game_data: GameData):
    rank = effect.rank
    data = readout.data
    column = data.get("column", "distance")
    out = []
    for row in data.get("rows", []):
        value = game_data.measurements.label(column, rank + int(row.get("offset", 0)))
        if not value:
            continue
        if row.get("perRound"):
            value = f"{value}/round"
        out.append(EffectStat("readout", row.get("label", ""), "", value, ""))
    return out


@READOUT_KINDS.handler("thresholds")
def _readout_thresholds(readout, effect: PowerEffectInstance, game_data: GameData):
    rank = effect.rank
    return [
        EffectStat("readout", row.get("label", ""), "", row.get("text", ""), "")
        for row in readout.data.get("rows", [])
        if rank >= int(row.get("minRank", 0))
    ]


@READOUT_KINDS.handler("config_flag")
def _readout_config_flag(readout, effect: PowerEffectInstance, game_data: GameData):
    data = readout.data
    on = bool(effect.config.get(data.get("key", "")))
    text = data.get("trueText", "") if on else data.get("falseText", "")
    return [EffectStat(readout.label.lower() or "readout", readout.label, "", text, "")]


@READOUT_KINDS.handler("points_per_rank")
def _readout_points_per_rank(readout, effect: PowerEffectInstance, game_data: GameData):
    per = int(readout.data.get("perRank", 1))
    return [EffectStat("pool", readout.label, "", f"{effect.rank * per} points", "")]


@READOUT_KINDS.handler("capped_rank_bonus")
def _readout_capped_rank_bonus(readout, effect: PowerEffectInstance, game_data: GameData):
    """A per-rank bonus that stops climbing at a cap, applied to a chosen subject.

    Extra Limbs is the case: ``+1`` per rank to *either* Grab or Stability, no higher
    than ``+5``. ``fromConfig`` names the config field holding the subject; when that
    field is deferred to use-time (Extra Limbs' Variable extra reassigns the limbs each
    turn, so no subject is stored) the bonus still shows, just without a subject.
    """

    data = readout.data
    per_rank = int(data.get("perRank", 1))
    cap = int(data.get("cap", 0))
    bonus = effect.rank * per_rank
    capped = cap and bonus > cap
    if capped:
        bonus = cap
    if bonus <= 0:
        return []

    field_key = data.get("fromConfig", "")
    chosen = effect.config.get(field_key) if field_key else None
    subject = ""
    if chosen:
        base = next((e for e in game_data.effects if e.id == effect.effect_id), None)
        field = next((f for f in base.config_fields if f.key == field_key), None) if base else None
        options = field.options if field else ()
        subject = next((o.label for o in options if o.value == chosen), str(chosen))

    text = f"+{bonus} {subject}".strip()
    text += " (capped)" if capped else ""
    if not subject:
        text += f" {data.get('unchosenNote', '(chosen when used)')}"
    return [EffectStat("bonus", readout.label or "Bonus", "", text.strip(), "better")]


def _readout_rows(readout, effect: PowerEffectInstance, game_data: GameData) -> list[EffectStat]:
    """Render one :class:`~mm_companion.core.data_loader.Readout` to table rows.

    Dispatches on the readout's ``kind`` through :data:`READOUT_KINDS`; an unregistered
    kind renders nothing.
    """

    handler = READOUT_KINDS.get(readout.kind)
    if handler is None:
        return []
    return handler(readout, effect, game_data)


# One handler per config-field ``type`` that renders its stored value specially
# (``docs/mm-powers-architecture.md`` §9). Each takes ``(field, value)`` and returns the
# display text. Only the types whose stored shape isn't a plain option value need a
# handler; the base ``allocation``/``repeatable`` renderers are registered below. A
# mod's Python module can add a type by registering another handler. An unregistered
# type (``select``/``multiselect``/``text`` and any mod type without a handler) falls
# through to the generic option-label renderer in :func:`_config_display`.
ConfigDisplay = Callable[[object, object], str]
CONFIG_DISPLAY_KINDS: Registry[ConfigDisplay] = Registry("config_field.type")


@CONFIG_DISPLAY_KINDS.handler("allocation")
def _config_display_allocation(field, value) -> str:
    by_id = {o.id: o for o in field.alloc_options}
    parts = []
    for entry in value:
        option = by_id.get(entry.get("id"))
        if option is None:
            continue
        label = option.label
        if len(option.tiers) > 1:
            label += f" {entry.get('tier', 1)}"
        parts.append(label)
    return ", ".join(parts)


@CONFIG_DISPLAY_KINDS.handler("repeatable")
def _config_display_repeatable(field, value) -> str:
    name_key = field.columns[0].key if field.columns else "name"
    int_key = next((c.key for c in field.columns if c.type == "int"), None)
    parts = []
    for row in value:
        name = str(row.get(name_key, "")).strip()
        if not name:
            continue
        if int_key and row.get(int_key):
            name += f" ({row[int_key]})"
        parts.append(name)
    return ", ".join(parts)


def _config_display(field, value) -> str:
    """Display text for a stored config ``value``: an option's label, or, for a
    multiselect list, its labels joined with ``+`` (falls back to the raw value).

    ``allocation`` values (a list of ``{"id", "tier"}``) render as their option
    labels (tiered ones carry the chosen tier number); ``repeatable`` values (a list
    of row dicts) render as their named rows, an Immunity scope carrying its rank.
    Dispatches on the field's ``type`` through :data:`CONFIG_DISPLAY_KINDS`; an
    unregistered type falls back to the generic option-label rendering."""

    handler = CONFIG_DISPLAY_KINDS.get(field.type)
    if handler is not None:
        return handler(field, value)

    values = value if isinstance(value, list) else [value]
    labels = (next((o.label for o in field.options if o.value == v), v) for v in values)
    return " + ".join(labels)


def effect_game_terms(effect: PowerEffectInstance, game_data: GameData) -> str:
    """One-line game-term summary of an effect, e.g.
    ``Affliction 4: Attack, Ranged range, Standard action, Instant duration``.

    Reads the effective stats (base plus modifier and config overrides) and renders
    the non-empty ones; a resistance is appended in parentheses, then any remaining
    configured qualities (Affliction's condition degrees, etc.). Returns ``""`` for
    an unknown effect id.
    """

    base = next((e for e in game_data.effects if e.id == effect.effect_id), None)
    if base is None:
        return ""
    stats = effective_effect_stats(effect, game_data)

    segments = [stats["effect_type"]]
    if stats["range"]:
        segments.append(f"{stats['range']} range")
    if stats["action"] and stats["action"] != "None":
        segments.append(f"{stats['action']} action")
    if stats["duration"]:
        segments.append(f"{stats['duration']} duration")

    line = f"{base.name} {effect.rank}: " + ", ".join(s for s in segments if s)
    if stats["resistance"]:
        line += f" (resisted by {stats['resistance']})"

    # Configured qualities that don't override a stat are appended (e.g. conditions).
    chosen = []
    for field in base.config_fields:
        if field.overrides or field.type == "checkbox":
            continue
        value = effect.config.get(field.key)
        if value:
            chosen.append(f"{field.label}: {_config_display(field, value)}")
    if chosen:
        line += "; " + ", ".join(chosen)
    return line


def power_game_terms(power: Power, game_data: GameData, char: Character | None = None) -> str:
    """The power's game-term summary: one :func:`effect_game_terms` line per effect.

    A ``linked`` or ``array`` power (with two or more effects) prefixes a header and
    tags each line with its role — the array marks its base and notes the flat cost
    of each alternate — so the composite structure reads at a glance. ``char`` is
    threaded to :func:`array_base_index` so the base badge tracks the same
    Strength-adjusted costs the cards show.
    """

    lines = [effect_game_terms(e, game_data) for e in power.effects]
    if len(power.effects) > 1 and power.structure == STRUCTURE_LINKED:
        body = "\n".join(f"• {line}" for line in lines)
        return "Linked (all effects activate together):\n" + body
    if len(power.effects) > 1 and power.structure == STRUCTURE_ARRAY:
        base = array_base_index(power, game_data, char)
        alt = array_alternate_cost(game_data)
        tagged = [
            f"• {line}" + (" [base]" if i == base else f" (Alternate Effect, {alt} pt)")
            for i, line in enumerate(lines)
        ]
        return "Array (one effect active at a time):\n" + "\n".join(tagged)
    return "\n".join(lines)

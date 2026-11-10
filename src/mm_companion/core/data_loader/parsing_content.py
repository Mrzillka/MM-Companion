"""Parsing: turn the traits', conditions', powers' and actions' raw JSON dicts into their
typed records.
"""

from __future__ import annotations

import dataclasses

from ..components import APPLY_BONUS, TONE_HINDRANCE, Integration, TraitBoost
from .game_data import (
    ActionModifier,
    AreaCover,
    AreaEvasion,
    AreaRules,
    CombatAction,
    CombatCheck,
    CombatEffect,
    CombatMenuSection,
    CombatOption,
    CombatRules,
    ConditionChange,
    DegreeOutcome,
    Impression,
    ImpressRules,
    KillRule,
    ManeuverLimits,
    OpposedStage,
    ReactionRule,
    RecoveryRule,
    ResistOptions,
    SlamRules,
    TargetedRules,
)
from .parsing_rules import (
    _extras,
    _parse_range_distance,
)
from .power_records import (
    AllocationOption,
    BaseCostBy,
    ConfigOption,
    ConfiguredEffect,
    ConfiguredModifier,
    Effect,
    EffectConfigField,
    EffectReach,
    Measure,
    Modifier,
    PowerConfiguration,
    RepeatableColumn,
    ResistanceGrant,
    ResistanceOutcome,
    SpeedRank,
    SubBuild,
)
from .system_records import (
    RangeDistance,
)
from .traits import (
    Ability,
    Advantage,
    AmountMod,
    AttackMods,
    Characteristic,
    Condition,
    ConditionParameter,
    ConditionParameterOption,
    Debilitation,
    DefenseMod,
    Field,
    OpposingRule,
    ParameterSpec,
    RandomActionRow,
    RecoveryCheck,
    Resistance,
    ResistanceMod,
    Skill,
    StackingRule,
)

# ===========================================================================
# Parsing — turn each raw JSON dict into the typed records above. One ``_parse_*``
# per record group; all mechanical (read keys, coerce types), so kept terse.
# ===========================================================================


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
        specialization_note=s.get("specializationNote", ""),
        focuses=tuple(s.get("focuses", ())),
        focus_note=s.get("focusNote", ""),
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
            "specializationNote",
            "focuses",
            "focusNote",
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
        uses_per_rank=max(0, int(a.get("usesPerAdventurePerRank", 0) or 0)),
        allows_untrained=bool(a.get("allowsUntrained", False)),
        allows_untrained_except=tuple(str(n) for n in a.get("allowsUntrainedExcept", ())),
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
            "usesPerAdventurePerRank",
            "allowsUntrained",
            "allowsUntrainedExcept",
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
        success_degrees=max(1, int(raw.get("successDegrees", 1) or 1)),
        becomes=str(raw.get("becomes", "") or ""),
        blocked_by=tuple(str(c) for c in raw.get("blockedBy", ()) or ()),
    )


def _parse_amount_mods(raw) -> tuple[AmountMod, ...]:
    """A condition's ``amountMods``: ``{trait, sign, parameter?}`` entries, sign ±1."""

    mods = []
    for entry in raw or ():
        if not isinstance(entry, dict) or not entry.get("trait"):
            continue
        sign = -1 if int(entry.get("sign", 1) or 1) < 0 else 1
        mods.append(
            AmountMod(
                trait=str(entry["trait"]),
                sign=sign,
                parameter=str(entry.get("parameter", "") or ""),
            )
        )
    return tuple(mods)


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
        tone=str(c.get("tone") or TONE_HINDRANCE),
        amount_mods=_parse_amount_mods(c.get("amountMods")),
        default_amount=(int(c["defaultAmount"]) if c.get("defaultAmount") is not None else None),
        max_amount=(int(c["maxAmount"]) if c.get("maxAmount") is not None else None),
        temporary=bool(c.get("temporary", False)),
        opposing=_parse_opposing(c.get("opposing")),
    )


def _parse_opposing(raw) -> OpposingRule | None:
    """A condition's ``opposing`` block (see :class:`OpposingRule`)."""

    if not isinstance(raw, dict):
        return None
    return OpposingRule(
        attack=_optional_int(raw.get("attack")) or 0,
        defense=_optional_int(raw.get("defense")) or 0,
        defense_as=str(raw.get("defenseAs", "") or ""),
        spent_by=str(raw.get("spentBy", "") or ""),
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
        grants=bool(c.get("grants", False)),
        template=c.get("template", ""),
        required=bool(c.get("required", False)),
        multiselect_with=c.get("multiselectWith"),
        hidden_with=c.get("hiddenWith"),
        shown_with=c.get("shownWith"),
        widen_with=c.get("widenWith"),
        widen_source=c.get("widenSource"),
        toggles=c.get("toggles"),
        source=c.get("source"),
        hides_field=bool(c.get("hidesField", False)),
        names_owner=bool(c.get("namesOwner", False)),
        hint=c.get("hint", ""),
        min_value=int(c.get("min", 0)),
        max_value=int(c.get("max", 0)),
        default_value=int(c.get("default", 0)),
        show_when_points=int(c.get("showWhenPoints", 0)),
        show_when_field=c.get("showWhenField", ""),
        show_when_value=c.get("showWhenValue", ""),
        options=tuple(
            ConfigOption(
                value=o["value"],
                label=o.get("label", o["value"]),
                cost_value=o.get("costValue"),
                flat=o.get("flat"),
                ranked=o.get("ranked"),
                cost_delta=int(o.get("costDelta", 0)),
                flat_delta=int(o.get("flatDelta", 0)),
                supersedes=tuple(o.get("supersedes", ())),
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
                tier_labels=tuple(o.get("tierLabels", ())),
                prompt=o.get("prompt", ""),
            )
            for o in c.get("allocOptions", [])
        ),
        columns=tuple(
            RepeatableColumn(
                key=col["key"],
                label=col.get("label", col["key"]),
                type=col.get("type", "text"),
                source=col.get("source", ""),
                suggestions=tuple(
                    (str(item["label"]), int(item.get("ranks", 0)))
                    for item in col.get("suggestions", ())
                ),
                default=int(col.get("default", 0)),
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
    to a ``bonus`` worth the effect's rank.
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
    parameters = entry.get("parameters", {})
    return ResistanceOutcome(
        conditions=tuple(entry.get("conditions", ())),
        config_key=entry.get("configKey", ""),
        scope_key=str(entry.get("scopeKey", "")),
        text=entry.get("text", ""),
        note=entry.get("note", ""),
        escalates=tuple(escalates.items()) if isinstance(escalates, dict) else (),
        parameters=(
            tuple((str(k), str(v)) for k, v in parameters.items())
            if isinstance(parameters, dict)
            else ()
        ),
    )


def _parse_resistance_outcomes(raw: object) -> tuple[ResistanceOutcome, ...]:
    """Parse an effect's ``resistanceOutcomes`` failure ladder (see :class:`ResistanceOutcome`).

    Accepts the ladder either as a bare list of rungs or wrapped in an object with a
    ``degrees`` list, so a mod can hang its own documentation — and its ``success``
    rung — off the same block.

    The wrapped form may also carry ``failureEscalates``: escalations every *failure*
    rung inherits, for a rule that holds whatever the degree ("a Dying character who
    fails by any degree is Dead"). They are folded into each rung's own
    :attr:`~.ResistanceOutcome.escalates` here — after the rung's own pairs, which win
    on a shared source — so every reader of a rung (the resolver, a spec on the wire,
    the minion cap) sees one list. The ``success`` rung never inherits them.
    """

    inherited: tuple[tuple[str, str], ...] = ()
    if isinstance(raw, dict):
        shared = raw.get("failureEscalates", {})
        if isinstance(shared, dict):
            inherited = tuple((str(k), str(v)) for k, v in shared.items())
        raw = raw.get("degrees", ())
    if not isinstance(raw, list):
        return ()
    rungs = (_parse_outcome_rung(entry) for entry in raw)
    return tuple(_inherit(rung, inherited) for rung in rungs if rung is not None)


def _inherit(rung: ResistanceOutcome, inherited: tuple[tuple[str, str], ...]) -> ResistanceOutcome:
    """*rung* with the ladder's ``failureEscalates`` appended to its own escalations."""

    own = {before for before, _ in rung.escalates}
    extra = tuple(pair for pair in inherited if pair[0] not in own)
    if not extra:
        return rung
    return dataclasses.replace(rung, escalates=rung.escalates + extra)


def _parse_resistance_success(raw: object) -> ResistanceOutcome | None:
    """The ``success`` rung of a ``resistanceOutcomes`` block, if it has one."""

    if not isinstance(raw, dict):
        return None
    return _parse_outcome_rung(raw.get("success"))


def _parse_action_modifier(raw: dict) -> ActionModifier:
    kind = str(raw.get("kind", "toggle"))
    return ActionModifier(
        id=str(raw.get("id", "")),
        label=str(raw.get("label", raw.get("id", ""))),
        kind=kind if kind in ("toggle", "slider") else "toggle",
        value=int(raw.get("value", 0)),
        minimum=int(raw.get("min", 0)),
        maximum=int(raw.get("max", 0)),
        step=max(1, int(raw.get("step", 1))),
        exclusive=str(raw.get("exclusive", "")),
        hint=str(raw.get("hint", "")),
    )


def _parse_impression(raw: dict) -> Impression:
    return Impression(
        id=str(raw.get("id", "")),
        name=str(raw.get("name", raw.get("id", ""))),
        skill=str(raw.get("skill", "")),
        advantage=str(raw.get("advantage", "")),
        outcomes=_parse_resistance_outcomes(raw.get("outcomes")),
    )


def _parse_impress(raw: dict) -> ImpressRules:
    """Parse ``actions.json``'s ``impress`` block (see :class:`ImpressRules`)."""

    block = raw.get("impress")
    if not isinstance(block, dict):
        return ImpressRules()
    resist = block.get("resist", {})
    if not isinstance(resist, dict):
        resist = {}
    return ImpressRules(
        skills=tuple(str(s) for s in block.get("skills", ())),
        resist=ResistOptions(
            same_skill=bool(resist.get("sameSkill", True)),
            skills=tuple(str(s) for s in resist.get("skills", ())),
            resistances=tuple(str(r) for r in resist.get("resistances", ())),
        ),
        group_penalty=int(block.get("groupPenalty", 0)),
        impressions=tuple(
            _parse_impression(i) for i in block.get("impressions", ()) if isinstance(i, dict)
        ),
        modifiers=tuple(
            _parse_action_modifier(m) for m in block.get("modifiers", ()) if isinstance(m, dict)
        ),
    )


def _parse_area(raw: dict) -> AreaRules:
    """Parse ``actions.json``'s ``area`` block (see :class:`AreaRules`)."""

    block = raw.get("area")
    if not isinstance(block, dict):
        return AreaRules()
    evasion = block.get("evasion")
    shapes = block.get("shapes", {})
    return AreaRules(
        modifiers=tuple(str(m) for m in block.get("modifiers", ())),
        dodge=str(block.get("dodge", "")),
        dc_base=int(block.get("dcBase", 10)),
        halve=str(block.get("halve", "up")),
        base_distance_rank=int(block.get("baseDistanceRank", 1)),
        shapes={str(k): str(v) for k, v in shapes.items()} if isinstance(shapes, dict) else {},
        default_shape=str(block.get("defaultShape", "")),
        evasion=(
            AreaEvasion(
                advantage=str(evasion.get("advantage", "")),
                dodge_bonus=int(evasion.get("dodgeBonus", 0)),
                negate_at_rank=int(evasion.get("negateAtRank", 0)),
            )
            if isinstance(evasion, dict) and evasion.get("advantage")
            else None
        ),
        free_use_pl_cap=bool(block.get("freeUsePlCap", True)),
        cover=tuple(
            AreaCover(str(c["id"]), str(c.get("label", c["id"])), int(c.get("value", 0)))
            for c in block.get("cover", ())
            if isinstance(c, dict) and c.get("id")
        ),
        worst_of=tuple(
            (str(w["modifier"]), str(w.get("key", "")), str(w.get("value", "")))
            for w in block.get("worstOf", ())
            if isinstance(w, dict) and w.get("modifier")
        ),
    )


def _optional_int(value: object) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return None


def _parse_condition_changes(raw: object, *, remove: bool = False) -> tuple:
    """``applies`` (``[{condition, amount?, temporary?}]``) or ``removes`` (``[id]``)."""

    changes = []
    for entry in raw if isinstance(raw, list) else ():
        if isinstance(entry, str) and entry:
            changes.append(ConditionChange(entry, remove=remove))
        elif isinstance(entry, dict) and entry.get("condition"):
            temporary = entry.get("temporary")
            changes.append(
                ConditionChange(
                    str(entry["condition"]),
                    amount=_optional_int(entry.get("amount")),
                    temporary=temporary if isinstance(temporary, bool) else None,
                    remove=remove,
                    on=str(entry.get("on", "") or ""),
                    against=str(entry.get("against", "") or ""),
                )
            )
    return tuple(changes)


def _parse_degree_outcomes(raw: object) -> tuple[DegreeOutcome, ...]:
    """A targeted action's ``wins`` / ``losses`` ladder, shallowest rung first."""

    rungs = [
        DegreeOutcome(
            max(1, _optional_int(r.get("degrees")) or 1),
            _parse_condition_changes(r.get("applies"))
            + _parse_condition_changes(r.get("removes"), remove=True),
            str(r.get("text", "") or ""),
        )
        for r in (raw if isinstance(raw, list) else ())
        if isinstance(r, dict)
    ]
    return tuple(sorted(rungs, key=lambda r: r.degrees))


def _parse_check(raw: object) -> CombatCheck | None:
    if isinstance(raw, dict) and raw.get("kind"):
        return CombatCheck(
            str(raw["kind"]), str(raw.get("trait", "")), _optional_int(raw.get("dc"))
        )
    return None


def _parse_checks(raw: object) -> tuple[CombatCheck, ...]:
    """A targeted action's ``check``: ``"attack"`` (the attack picked), one
    ``{kind, trait}``, or a list of them (the actor rolls the best)."""

    if isinstance(raw, str) and raw:
        return (CombatCheck(raw),)
    entries = raw if isinstance(raw, list) else [raw]
    found = []
    for entry in entries:
        check = CombatCheck(entry) if isinstance(entry, str) and entry else _parse_check(entry)
        if check is not None:
            found.append(check)
    return tuple(found)


def _parse_resist(raw: object) -> tuple[tuple[str, str], ...]:
    return tuple(
        (str(r["kind"]), str(r.get("trait", "")))
        for r in (raw if isinstance(raw, list) else ())
        if isinstance(r, dict) and r.get("kind")
    )


def _parse_targeted(raw: object) -> TargetedRules | None:
    """A targeted action's ``targeted`` block (see :class:`TargetedRules`). Its ``dc`` is
    a number, ``"defense"`` (the target's Defense), or ``{base, kind, trait}`` (a base
    plus one of the target's traits)."""

    if not isinstance(raw, dict):
        return None
    dc = raw.get("dc")
    opposed = raw.get("opposed")
    dc_from = None
    if isinstance(dc, dict) and dc.get("kind"):
        dc_from = CombatCheck(
            str(dc["kind"]), str(dc.get("trait", "")), _optional_int(dc.get("base")) or 0
        )
    return TargetedRules(
        checks=_parse_checks(raw.get("check")),
        dc=_optional_int(dc),
        vs_defense=dc == "defense",
        dc_from=dc_from,
        resist=_parse_resist(raw.get("resist")),
        opposed=(
            OpposedStage(_parse_checks(opposed.get("check")), _parse_resist(opposed.get("resist")))
            if isinstance(opposed, dict)
            else None
        ),
        ally=bool(raw.get("ally", False)),
    )


def _parse_combat_effect(raw: dict) -> CombatEffect:
    """The effect fields a combat action or one of its options carries."""

    check = raw.get("check")
    kill = raw.get("kill")
    return CombatEffect(
        line=str(raw.get("line", "")),
        tone=str(raw.get("tone", "")),
        attack_modifier=_optional_int(raw.get("attackModifier")) or 0,
        roll_twice=bool(raw.get("rollTwice", False)),
        dc=_optional_int(raw.get("dc")),
        critical_hit=bool(raw.get("criticalHit", False)),
        slam=bool(raw.get("slam", False)),
        full_speed=bool(raw.get("fullSpeed", False)),
        kill=(
            KillRule(int(kill.get("degrees", 3)), str(kill.get("condition", "")))
            if isinstance(kill, dict) and kill.get("condition")
            else None
        ),
        applies=_parse_condition_changes(raw.get("applies"))
        + _parse_condition_changes(raw.get("removes"), remove=True),
        check=_parse_check(check),
        clears=str(raw.get("clears", "")),
        wins=_parse_degree_outcomes(raw.get("wins")),
        losses=_parse_degree_outcomes(raw.get("losses")),
    )


def _parse_combat_action(raw: dict) -> CombatAction:
    slam = raw.get("slamRules")
    recovery = raw.get("recovery")
    reaction = raw.get("reaction")
    return CombatAction(
        id=str(raw.get("id", "")),
        name=str(raw.get("name", raw.get("id", ""))),
        caption=str(raw.get("caption", "")),
        icon=str(raw.get("icon", "")),
        tooltip=str(raw.get("tooltip", "")),
        kind=str(raw.get("kind", "self")),
        needs_attack=bool(raw.get("needsAttack", False)),
        gm_only=bool(raw.get("gmOnly", False)),
        bonus=str(raw.get("bonus", "")),
        penalty=str(raw.get("penalty", "")),
        lasting=str(raw.get("lasting", "")),
        effect=_parse_combat_effect(raw),
        options=tuple(
            CombatOption(
                id=str(o["id"]),
                name=str(o.get("name", o["id"])),
                tooltip=str(o.get("tooltip", "")),
                effect=_parse_combat_effect(o),
            )
            for o in raw.get("options", ())
            if isinstance(o, dict) and o.get("id")
        ),
        requires=_parse_requires(raw.get("requires")),
        refused_by=str(raw.get("refusedBy", "")),
        gm_runs=str(raw.get("gmRuns", "")),
        slam=(
            SlamRules(
                rank_bonus=int(slam.get("rankBonus", 1)),
                full_speed_bonus=int(slam.get("fullSpeedBonus", 1)),
                self_resistance=str(slam.get("selfResistance", "")),
                halve_self=bool(slam.get("halveSelf", True)),
            )
            if isinstance(slam, dict)
            else None
        ),
        recovery=(
            RecoveryRule(
                conditions=tuple(str(c) for c in recovery.get("from", ()) if isinstance(c, str)),
                fatigue=bool(recovery.get("fatigue", False)),
            )
            if isinstance(recovery, dict)
            else None
        ),
        targeted=_parse_targeted(raw.get("targeted")),
        reaction=(
            ReactionRule(
                condition=str(reaction["condition"]),
                step=_optional_int(reaction.get("step")) or 0,
                line=str(reaction.get("line", "")),
            )
            if isinstance(reaction, dict) and reaction.get("condition")
            else None
        ),
    )


def _parse_requires(raw: object) -> tuple[str, ...]:
    """``requires``: one condition id, or a list of them (any one will do)."""

    if isinstance(raw, str):
        return (raw,) if raw else ()
    return tuple(str(r) for r in raw if isinstance(r, str) and r) if isinstance(raw, list) else ()


def _parse_combat(raw: dict) -> CombatRules:
    """Parse ``actions.json``'s ``combat`` block (see :class:`CombatRules`)."""

    block = raw.get("combat")
    if not isinstance(block, dict):
        return CombatRules()
    limits = block.get("limits")
    if not isinstance(limits, dict):
        limits = {}
    return CombatRules(
        menu=tuple(
            CombatMenuSection(
                id=str(s["id"]),
                title=str(s.get("title", s["id"])),
                items=tuple(str(i) for i in s.get("items", ()) if isinstance(i, str)),
            )
            for s in block.get("menu", ())
            if isinstance(s, dict) and s.get("id")
        ),
        actions=tuple(
            _parse_combat_action(a)
            for a in block.get("actions", ())
            if isinstance(a, dict) and a.get("id")
        ),
        limits=ManeuverLimits(
            max_shift=int(limits.get("maxShift", 5)),
            floor=int(limits.get("floor", 0)),
            double_cap=bool(limits.get("doubleCap", True)),
        ),
    )


def _parse_base_cost_by(raw: dict | None) -> BaseCostBy | None:
    """Parse an effect's ``baseCostBy`` block, or ``None`` when it has none.

    ``max`` is optional and absent means uncapped, so it cannot be read with a numeric
    default the way ``base``/``min`` can.
    """

    if not raw:
        return None
    maximum = raw.get("max")
    return BaseCostBy(
        fields=tuple(raw.get("fields", ())),
        base=int(raw.get("base", 0)),
        minimum=int(raw.get("min", 1)),
        maximum=None if maximum is None else int(maximum),
    )


def _parse_sub_build(raw: dict | None) -> SubBuild | None:
    """Parse a ``subBuild`` block, or ``None`` for the records that declare none."""

    if not raw:
        return None
    return SubBuild(
        key=str(raw["key"]),
        label=str(raw.get("label", "Build")),
        budget=dict(raw.get("budget", {})),
        count=dict(raw.get("count", {})),
        hint=str(raw.get("hint", "")),
        forbids_effects=tuple(raw.get("forbidsEffects", ())),
        forbids_advantages=tuple(raw.get("forbidsAdvantages", ())),
        menu_with=tuple(raw.get("menuWith", ())),
    )


def _parse_effect(e: dict, ranged_distance: RangeDistance | None = None) -> Effect:
    default_distance = ranged_distance or RangeDistance()
    return Effect(
        id=e["id"],
        name=e["name"],
        effect_type=e["effectType"],
        action=e.get("action", ""),
        range_=e.get("range", ""),
        personal=None if e.get("personal") is None else bool(e["personal"]),
        duration=e.get("duration", ""),
        check=e.get("check"),
        resistance=e.get("resistance"),
        opposed_check=e.get("opposedCheck", ""),
        base_cost=e.get("baseCost", ""),
        base_cost_value=int(e.get("baseCostValue", 1)),
        base_cost_mode=e.get("baseCostMode", "flat"),
        base_cost_by=_parse_base_cost_by(e.get("baseCostBy")),
        sub_build=_parse_sub_build(e.get("subBuild")),
        rank_follows_allocation=bool(e.get("rankFollowsAllocation", False)),
        integration=_parse_integration(
            e.get("statIntegration", {}), bool(e.get("configurableTarget", False))
        ),
        description=e.get("description", ""),
        config_fields=tuple(_parse_config_field(c) for c in e.get("config", [])),
        measure=_parse_measure(e.get("measure"), e["id"]),
        resistance_dc_base=e.get("resistanceDcBase"),
        resistance_vs_check=bool(e.get("resistanceVsCheck", False)),
        resistance_outcomes=_parse_resistance_outcomes(e.get("resistanceOutcomes")),
        resistance_success=_parse_resistance_success(e.get("resistanceOutcomes")),
        check_dc=_check_dc(e.get("checkOutcomes")),
        success_removes=_success_removes(e.get("checkOutcomes")),
        implicit_modifiers=tuple(e.get("implicitModifiers", ())),
        range_distance=_parse_range_distance(e.get("rangeDistance"), default_distance)
        or default_distance,
        reach=_parse_effect_reach(e.get("reach")),
    )


def _check_dc(raw: object) -> int | None:
    """An effect's ``checkOutcomes.dc`` — the DC its own check is made against."""
    if not isinstance(raw, dict):
        return None
    dc = raw.get("dc")
    return dc if isinstance(dc, int) and not isinstance(dc, bool) else None


def _success_removes(raw: object) -> tuple[str, ...]:
    """An effect's ``checkOutcomes.removes`` — condition ids, one per degree of success."""
    if not isinstance(raw, dict):
        return ()
    return tuple(str(c) for c in raw.get("removes", ()) or () if str(c).strip())


def _parse_effect_reach(raw: dict | None) -> EffectReach | None:
    if not raw:
        return None
    return EffectReach(per_rank_distance_rank=int(raw.get("perRankDistanceRank", 0)))


# ``overrides`` keys are camelCase in the JSON like every other key there, but the
# stat dicts :func:`mm_companion.core.rules.effect_stat_rows` builds are snake_case.
_OVERRIDE_KEYS = {"effectType": "effect_type"}


def _parse_resistance_grant(raw: object) -> ResistanceGrant | None:
    """A modifier's ``grantsResistance`` block, or ``None`` when it has none."""
    if not isinstance(raw, dict):
        return None
    return ResistanceGrant(
        dc_base=int(raw.get("dcBase", 10)),
        dc_trait=str(raw.get("dcTrait", "")),
        placeholder=str(raw.get("placeholder", "")),
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
        cost_mode=m.get("costMode", ""),
        flat=bool(m.get("flat", False)),
        ranked=bool(m.get("ranked", False)),
        max_rank=m.get("maxRank"),
        overrides={_OVERRIDE_KEYS.get(k, k): v for k, v in m.get("overrides", {}).items()},
        check_bonus=int(m.get("checkBonus", 0)),
        grants_attack=bool(m.get("grantsAttack", False)),
        drops_check=bool(m.get("dropsCheck", False)),
        grants_resistance=_parse_resistance_grant(m.get("grantsResistance")),
        check_note=m.get("checkNote", ""),
        step_field=m.get("stepField", ""),
        step_by=int(m.get("stepBy", 0)),
        distance_rank_bonus=int(m.get("distanceRankBonus", 0)),
        range_rank=dict(m.get("rangeRank", {})),
        reach_spaces=int(m.get("reachSpaces", 0)),
        adds_ability=m.get("addsAbility", ""),
        cost_scope=m.get("costScope", ""),
        cost_per_points=int(m.get("costPerPoints", 0)),
        dynamic_cost_value=int(m.get("dynamicCostValue", 0)),
        gate=m.get("gate", ""),
        requires_effect_id=m.get("requiresEffect", ""),
        repeatable=bool(m.get("repeatable", False)),
        sub_build=_parse_sub_build(m.get("subBuild")),
        hidden=bool(m.get("hidden", False)),
        note_template=m.get("noteTemplate", ""),
        note_per_rank=int(m.get("notePerRank", 0)),
        note_values={k: dict(v) for k, v in m.get("noteValues", {}).items()},
        requires_check=bool(m.get("requiresCheck", False)),
        check_trait=str(m.get("checkTrait", "")),
        check_dc=m.get("checkDc") if isinstance(m.get("checkDc"), int) else None,
        use_costs=tuple(str(c) for c in m.get("useCosts", ())),
        uses_per_charge=int(m.get("usesPerCharge", 0)),
        fades_per_use=int(m.get("fadesPerUse", 0)),
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


def _parse_configured_modifier(raw: dict) -> ConfiguredModifier:
    return ConfiguredModifier(
        id=raw["id"], rank=int(raw.get("rank", 0)), config=dict(raw.get("config", {}))
    )


def _parse_configured_effect(raw: dict) -> ConfiguredEffect:
    return ConfiguredEffect(
        effect_id=raw["effectId"],
        rank=int(raw.get("rank", 0)),
        config=dict(raw.get("config", {})),
        extras=tuple(_parse_configured_modifier(m) for m in raw.get("extras", [])),
        flaws=tuple(_parse_configured_modifier(m) for m in raw.get("flaws", [])),
    )


def _parse_configuration(raw: dict) -> PowerConfiguration:
    return PowerConfiguration(
        id=raw["id"],
        name=raw["name"],
        base_effect=raw.get("baseEffect", ""),
        cost_note=raw.get("costNote", ""),
        cite=raw.get("cite", ""),
        description=raw.get("description", ""),
        structure=raw.get("structure", "independent"),
        effects=tuple(_parse_configured_effect(e) for e in raw.get("effects", [])),
    )


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

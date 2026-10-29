"""The data-driven stat-effect layer: appliers, contributions, and the stacking resolver.

The registry side of this is the mod seam (``register_stat_applier``); the resolver
side is the no-stacking rule equipment needs. Both are pure ``core`` — no Qt, no
display.
"""

from __future__ import annotations

import dataclasses

import pytest

from mm_companion.core.character import AdvantageSelection, Character
from mm_companion.core.components import (
    APPLY_BONUS,
    APPLY_PENALTY_REMOVED,
    APPLY_SENSE,
    APPLY_SPEED,
    TraitBoost,
)
from mm_companion.core.data_loader import Advantage, load_game_data
from mm_companion.core.powers import Power, PowerEffectInstance
from mm_companion.core.rules import (
    CATEGORY_ABILITY,
    CATEGORY_MOVEMENT,
    CATEGORY_PENALTY,
    CATEGORY_SKILL,
    GROUP_EQUIPMENT,
    GROUP_INTRINSIC,
    GROUP_POWERS,
    STACK_MAX,
    STACK_SUM,
    STAT_APPLIERS,
    ApplyContext,
    TraitContribution,
    apply_stat_effect,
    power_contributions,
    power_trait_bonuses,
    register_stat_applier,
    resolve_bonuses,
    resolve_contributions,
    skill_bonus,
    trait_bonuses,
    trait_category,
    trait_contributions,
)


def _char_with(power: Power) -> Character:
    data = load_game_data()
    char = Character.new_default(data)
    char.powers.append(power)
    return char


def _context(record: TraitBoost, rank: int, target: str, **kwargs) -> ApplyContext:
    return ApplyContext(
        record=record, rank=rank, target=target, source="Test", game_data=load_game_data(), **kwargs
    )


def _bonus(amount: int, stat: str = "TOUGHNESS", **kwargs) -> TraitContribution:
    kwargs.setdefault("category", "resistance")
    kwargs.setdefault("source", f"+{amount}")
    return TraitContribution(amount=amount, stat=stat, **kwargs)


# -- the applier registry --------------------------------------------------------------


def test_every_shipped_apply_kind_has_an_applier() -> None:
    from mm_companion.core.components import APPLY_KINDS

    for kind in APPLY_KINDS:
        assert STAT_APPLIERS.get(kind) is not None


def test_an_unregistered_apply_kind_contributes_nothing() -> None:
    # An effect naming a kind whose mod is disabled grants no bonus rather than raising.
    assert apply_stat_effect("no_such_kind", _context(TraitBoost(target="STR"), 3, "STR")) == ()


def test_bonus_applier_uses_the_records_rank_scaling() -> None:
    record = TraitBoost(target="STR", per_rank=2, flat=1)
    (contribution,) = apply_stat_effect(APPLY_BONUS, _context(record, 3, "STR"))
    assert contribution.amount == 7  # flat 1 + rank 3 x 2
    assert (contribution.stat, contribution.category) == ("STR", CATEGORY_ABILITY)
    assert contribution.kind == APPLY_BONUS


def test_bonus_applier_declines_a_target_the_sheet_does_not_total() -> None:
    # An Immunity naming a descriptor ("Fire") is not a row anything sums.
    assert apply_stat_effect(APPLY_BONUS, _context(TraitBoost(configurable=True), 4, "Fire")) == ()
    assert apply_stat_effect(APPLY_BONUS, _context(TraitBoost(configurable=True), 4, "")) == ()


def test_a_record_that_says_it_boosts_senses_never_raises_a_trait() -> None:
    # A hand-edited save could name a skill as an Enhanced Senses target; the record's
    # own ``affects`` says what it raises, and it does not say "skill".
    record = TraitBoost(affects=frozenset({"senses"}), configurable=True)
    assert apply_stat_effect(APPLY_BONUS, _context(record, 4, "Acrobatics")) == ()
    # A record declaring no categories at all is taken at its target.
    (contribution,) = apply_stat_effect(APPLY_BONUS, _context(TraitBoost(), 4, "Acrobatics"))
    assert contribution.category == CATEGORY_SKILL


def test_speed_and_sense_appliers_land_outside_the_numeric_trait_lists() -> None:
    (speed,) = apply_stat_effect(APPLY_SPEED, _context(TraitBoost(target="flight"), 6, "flight"))
    assert (speed.category, speed.amount) == (CATEGORY_MOVEMENT, 6)
    (sense,) = apply_stat_effect(APPLY_SENSE, _context(TraitBoost(target="acute"), 2, "acute"))
    assert sense.category == "sense"
    (lifted,) = apply_stat_effect(
        APPLY_PENALTY_REMOVED, _context(TraitBoost(target="Stealth"), 2, "Stealth")
    )
    assert lifted.category == CATEGORY_PENALTY  # not a bonus on the Stealth row


def test_a_mod_can_register_its_own_apply_kind() -> None:
    def double(context: ApplyContext) -> tuple[TraitContribution, ...]:
        return (
            TraitContribution(
                amount=context.amount * 2,
                stat=context.target,
                category=CATEGORY_ABILITY,
                source=context.source,
            ),
        )

    register_stat_applier("test_double", double)
    try:
        (contribution,) = apply_stat_effect("test_double", _context(TraitBoost(), 3, "STR"))
        assert contribution.amount == 6
    finally:
        STAT_APPLIERS.unregister("test_double")
    assert apply_stat_effect("test_double", _context(TraitBoost(), 3, "STR")) == ()


def test_trait_category_names_the_list_a_target_belongs_to() -> None:
    data = load_game_data()
    assert trait_category(data, "STR") == CATEGORY_ABILITY
    assert trait_category(data, "TOUGHNESS") == "resistance"
    assert trait_category(data, "Acrobatics") == CATEGORY_SKILL
    assert trait_category(data, "Fire") == ""


# -- gathering contributions off the sheet ---------------------------------------------


def test_a_live_power_contributes_through_the_registry() -> None:
    data = load_game_data()
    char = _char_with(
        Power(
            name="Mighty",
            effects=[PowerEffectInstance("enhanced_trait", rank=3, config={"target": "STR"})],
        )
    )
    (contribution,) = power_contributions(char, data)
    assert contribution == TraitContribution(
        amount=3,
        stat="STR",
        category=CATEGORY_ABILITY,
        source="Mighty",
        stacking=STACK_SUM,
        group=GROUP_POWERS,
        kind=APPLY_BONUS,
    )


def test_a_switched_off_power_contributes_nothing() -> None:
    data = load_game_data()
    power = Power(
        name="Mighty",
        effects=[PowerEffectInstance("enhanced_trait", rank=3, config={"target": "STR"})],
    )
    char = _char_with(power)
    power.activated = False
    assert power_contributions(char, data) == ()


def test_effects_data_carries_the_apply_kind() -> None:
    data = load_game_data()
    boosters = {e.id: e for e in data.effects}
    protection = boosters["protection"].integration.trait_boost
    assert (protection.apply, protection.per_rank, protection.flat) == (APPLY_BONUS, 1, 0)
    assert boosters["enhanced_trait"].integration.trait_boost.apply == APPLY_BONUS
    # An effect that carries no stat effect at all has no record to apply.
    assert boosters["damage"].integration.trait_boost is None


# -- the stacking resolver -------------------------------------------------------------


def test_nothing_standing_on_a_trait_resolves_to_none() -> None:
    assert resolve_contributions(()) is None


def test_bonuses_bought_with_power_points_stack() -> None:
    resolved = resolve_contributions([_bonus(2, source="A"), _bonus(3, source="B")])
    assert (resolved.amount, resolved.sources) == (5, ("A", "B"))
    assert resolved.superseded == ()


def test_equipment_does_not_stack_with_itself() -> None:
    vest = _bonus(2, source="Vest", stacking=STACK_MAX, group=GROUP_EQUIPMENT)
    plate = _bonus(4, source="Plate", stacking=STACK_MAX, group=GROUP_EQUIPMENT)
    resolved = resolve_contributions([vest, plate])
    assert (resolved.amount, resolved.sources) == (4, ("Plate",))
    assert [(s.source, s.beaten_by) for s in resolved.superseded] == [("Vest", "Plate")]


def test_equipment_and_powers_take_the_better_of_the_two_never_the_sum() -> None:
    field = _bonus(4, source="Force Field")
    plate = _bonus(3, source="Plate", stacking=STACK_MAX, group=GROUP_EQUIPMENT)
    resolved = resolve_contributions([field, plate])
    assert (resolved.amount, resolved.sources) == (4, ("Force Field",))
    assert [(s.source, s.amount, s.beaten_by) for s in resolved.superseded] == [
        ("Plate", 3, "Force Field")
    ]


def test_the_better_gear_can_beat_the_power() -> None:
    field = _bonus(2, source="Force Field")
    plate = _bonus(6, source="Plate", stacking=STACK_MAX, group=GROUP_EQUIPMENT)
    resolved = resolve_contributions([field, plate])
    assert (resolved.amount, resolved.sources) == (6, ("Plate",))
    assert [s.source for s in resolved.superseded] == ["Force Field"]


def test_an_item_flagged_stacks_adds_on_top_of_its_group() -> None:
    plate = _bonus(4, source="Plate", stacking=STACK_MAX, group=GROUP_EQUIPMENT)
    vest = _bonus(2, source="Vest", stacking=STACK_MAX, group=GROUP_EQUIPMENT)
    charm = _bonus(1, source="Charm", stacking=STACK_SUM, group=GROUP_EQUIPMENT)
    resolved = resolve_contributions([plate, vest, charm])
    assert (resolved.amount, resolved.sources) == (5, ("Plate", "Charm"))
    assert [s.source for s in resolved.superseded] == ["Vest"]


def test_a_tie_between_the_groups_goes_to_the_one_seen_first() -> None:
    field = _bonus(3, source="Force Field")
    plate = _bonus(3, source="Plate", stacking=STACK_MAX, group=GROUP_EQUIPMENT)
    resolved = resolve_contributions([field, plate])
    assert resolved.sources == ("Force Field",)


# -- the intrinsic group: what the creature *is* ----------------------------------------


def _intrinsic(amount: int, **kwargs):
    kwargs.setdefault("source", "Size (Large)")
    return _bonus(amount, group=GROUP_INTRINSIC, **kwargs)


def test_size_alone_on_a_trait_resolves_without_a_group_to_join() -> None:
    """The common case: a large character with no powers and no gear."""
    resolved = resolve_contributions([_intrinsic(1)])
    assert (resolved.amount, resolved.sources) == (1, ("Size (Large)",))
    assert resolved.superseded == ()


def test_size_is_added_on_top_of_whichever_group_won() -> None:
    """Gear beats powers outright — but neither of them beats being Colossal."""
    field = _bonus(2, source="Force Field")
    plate = _bonus(6, source="Plate", stacking=STACK_MAX, group=GROUP_EQUIPMENT)
    resolved = resolve_contributions([_intrinsic(4), field, plate])

    assert resolved.amount == 10  # 6 from the winning group, plus 4 for being enormous
    assert resolved.sources == ("Plate", "Size (Large)")


def test_size_neither_supersedes_nor_is_superseded() -> None:
    field = _bonus(2, source="Force Field")
    plate = _bonus(6, source="Plate", stacking=STACK_MAX, group=GROUP_EQUIPMENT)
    resolved = resolve_contributions([_intrinsic(4), field, plate])

    beaten = {(s.source, s.beaten_by) for s in resolved.superseded}
    assert beaten == {("Force Field", "Plate")}


def test_a_negative_intrinsic_nets_against_a_bought_bonus() -> None:
    """A small creature's Defence penalty is not a bonus to be picked over its rivals."""
    resolved = resolve_contributions([_intrinsic(-2, source="Size (Tiny)"), _bonus(5)])
    assert resolved.amount == 3


def test_two_intrinsic_contributions_sum() -> None:
    resolved = resolve_contributions([_intrinsic(1, source="A"), _intrinsic(2, source="B")])
    assert resolved.amount == 3


def test_resolve_bonuses_keys_by_category_then_stat() -> None:
    resolved = resolve_bonuses(
        [
            _bonus(2, "STR", category=CATEGORY_ABILITY, source="A"),
            _bonus(3, "STR", category=CATEGORY_ABILITY, source="B"),
            _bonus(1, "TOUGHNESS", source="C"),
        ]
    )
    assert resolved[CATEGORY_ABILITY]["STR"].amount == 5
    assert resolved["resistance"]["TOUGHNESS"].amount == 1
    assert CATEGORY_SKILL not in resolved


# -- what the derived totals read ------------------------------------------------------


def _with_skill_advantage(char: Character, name: str, per_rank: int, target: str) -> object:
    """A ``GameData`` carrying one synthetic skill-granting advantage, plus the selection.

    No shipped advantage grants a flat skill bonus today — the seam is data-driven and
    unused — so the test supplies its own record rather than asserting on content that
    may never exist.
    """

    data = load_game_data()
    advantage = Advantage(
        name=name, ranked=True, skill_bonus_per_rank=per_rank, skill_bonus_target=target
    )
    char.advantages.append(AdvantageSelection(name=name, rank=2))
    return dataclasses.replace(data, advantages=[*data.advantages, advantage])


def test_advantages_contribute_alongside_powers_and_the_two_add_up() -> None:
    char = _char_with(
        Power(
            name="Cat's Grace",
            effects=[
                PowerEffectInstance("enhanced_trait", rank=6, config={"target": "Acrobatics"})
            ],
        )
    )
    data = _with_skill_advantage(char, "Tumbler", per_rank=1, target="Acrobatics")

    bonus = skill_bonus(char, data, "Acrobatics")
    assert (bonus.amount, bonus.sources) == (8, ("Cat's Grace", "Tumbler"))  # 6 + 1 x 2 ranks
    # The sheet-wide view agrees; the powers-only view (what the enhancement columns
    # show) still reports the power alone.
    assert trait_bonuses(char, data)[CATEGORY_SKILL]["Acrobatics"].amount == 8
    assert power_trait_bonuses(char, data)[CATEGORY_SKILL]["Acrobatics"].amount == 6


def test_an_advantage_aimed_at_one_focus_stays_on_that_row() -> None:
    char = Character.new_default(load_game_data())
    data = _with_skill_advantage(char, "Linguist", per_rank=2, target="Expertise: Law")

    assert skill_bonus(char, data, "Expertise: Law").amount == 4
    assert skill_bonus(char, data, "Expertise: Medicine") is None


def test_trait_contributions_gather_powers_before_advantages() -> None:
    char = _char_with(
        Power(
            name="Grace",
            effects=[
                PowerEffectInstance("enhanced_trait", rank=1, config={"target": "Acrobatics"})
            ],
        )
    )
    data = _with_skill_advantage(char, "Tumbler", per_rank=1, target="Acrobatics")
    assert [c.source for c in trait_contributions(char, data)] == ["Grace", "Tumbler"]


@pytest.mark.parametrize("rank", [0, 4])
def test_a_boost_of_any_rank_reaches_the_effective_ability(rank: int) -> None:
    from mm_companion.core.rules import effective_ability

    data = load_game_data()
    char = _char_with(
        Power(effects=[PowerEffectInstance("enhanced_trait", rank=rank, config={"target": "STR"})])
    )
    char.abilities["STR"] = 2
    assert effective_ability(char, data, "STR") == 2 + rank

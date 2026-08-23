"""Size as a trait source: what the Size Table grants, and what it shifts."""

from __future__ import annotations

import pytest

from mm_companion.core.character import Character
from mm_companion.core.data_loader import load_game_data
from mm_companion.core.powers import ModifierSelection, Power, PowerEffectInstance
from mm_companion.core.rules import (
    base_character_reach,
    base_ground_speed_rank,
    base_size_rank,
    character_reach,
    defense_class,
    effect_current_rank,
    effect_stat_rows,
    effective_ability,
    effective_size,
    effective_size_rank,
    estimated_power_level,
    power_pl_violations,
    power_total_cost,
    reach_is_altered,
    reach_text,
    resistance_total,
    size_contributions,
    size_resistance_shift,
    size_skill_shift,
    size_steps,
    size_trait_modifier,
    skill_total,
)
from mm_companion.core.rules.appliers import GROUP_INTRINSIC, STACK_SUM


@pytest.fixture
def data():
    return load_game_data()


def _char(data, size: str = "Medium") -> Character:
    char = Character.new_default(data)
    char.characteristics["size"] = size
    return char


def _growth(char: Character, rank: int, *, effect: str = "growth") -> Power:
    power = Power(name=effect.title(), effects=[PowerEffectInstance(effect, rank=rank)])
    power.activated = True
    char.powers.append(power)
    return power


# -- the rank itself -------------------------------------------------------------


def test_a_size_shift_is_relative_to_the_size_you_already_are(data) -> None:
    """A Small character growing two ranks is Large, not Huge."""
    char = _char(data, "Small")
    _growth(char, 2)

    assert base_size_rank(char, data) == -1
    assert effective_size_rank(char, data) == 1
    assert effective_size(char, data) == "Large"


def test_growing_and_shrinking_at_once_leaves_the_difference(data) -> None:
    char = _char(data, "Small")
    _growth(char, 2)
    _growth(char, 3, effect="shrinking")

    assert effective_size_rank(char, data) == -2
    assert effective_size(char, data) == "Tiny"


def test_a_shift_past_the_table_clamps_rather_than_extrapolating(data) -> None:
    """The one place the Size Table stops being linear."""
    char = _char(data, "Small")
    _growth(char, 10)

    assert effective_size_rank(char, data) == 9  # the rank is honest...
    assert effective_size(char, data) == "Awesome"  # ...the row it reads is clamped
    assert size_trait_modifier(char, data, "resistance", "TOUGHNESS") == 5


# -- what it contributes ---------------------------------------------------------


def test_a_medium_character_contributes_nothing_at_all(data) -> None:
    """Not "contributes zero" — nothing.

    A ``TraitBonus`` is a dataclass instance and therefore always truthy, so a zero
    would paint a green arrow on every Medium sheet's Defence and Toughness and switch
    the Skills block's "+" column on for everybody.
    """
    assert size_contributions(_char(data), data) == ()


def test_a_large_character_grants_one_contribution_per_non_zero_column(data) -> None:
    char = _char(data, "Large")

    by_stat = {c.stat: c for c in size_contributions(char, data)}

    assert by_stat["DEF"].amount == -1
    assert by_stat["TOUGHNESS"].amount == 1
    assert by_stat["Intimidation"].amount == 2
    assert by_stat["Stealth"].amount == -2
    # Strength is deliberately absent: the Damage column scales an effect's rank.
    assert "STR" not in by_stat


def test_every_contribution_is_intrinsic_and_names_the_size(data) -> None:
    for contribution in size_contributions(_char(data, "Huge"), data):
        assert contribution.group == GROUP_INTRINSIC
        assert contribution.stacking == STACK_SUM
        assert contribution.source == "Size (Huge)"


def test_size_reaches_the_totals_the_sheet_shows(data) -> None:
    char = _char(data, "Large")
    char.abilities["STA"] = 4
    char.abilities["AGL"] = 2
    char.skill_ranks["Stealth"] = 3

    assert resistance_total(char, data, "TOUGHNESS") == 5  # STA 4 + 1
    assert resistance_total(char, data, "DODGE") == -1  # through DEF
    assert defense_class(char, data) == 9  # 10 + DEF -1
    assert skill_total(char, data, "Stealth") == 3  # AGL 2 + 3 ranks - 2
    assert effective_ability(char, data, "STR") == 0  # untouched


def test_dodge_picks_its_size_modifier_up_through_defence(data) -> None:
    """The contribution is on DEF; Dodge derives from it, so it must not be doubled."""
    char = _char(data, "Huge")

    assert size_trait_modifier(char, data, "resistance", "DODGE") == 0
    assert size_resistance_shift(char, data, "DODGE") == -2
    assert size_resistance_shift(char, data, "TOUGHNESS") == 2


def test_the_paired_cap_shift_cancels_itself(data) -> None:
    """Which is why being large trips no Power Level warning on its own."""
    char = _char(data, "Colossal")
    pair = next(p for p in data.system.paired_caps if "DODGE" in p.traits)

    assert sum(size_resistance_shift(char, data, key) for key in pair.traits) == 0


def test_a_skill_cap_shift_follows_the_row_and_its_ability(data) -> None:
    char = _char(data, "Huge")

    assert size_skill_shift(char, data, "Intimidation") == 4
    assert size_skill_shift(char, data, "Stealth") == -4
    # Nothing maps onto an ability in the base ruleset, so an unlisted skill is flat.
    assert size_skill_shift(char, data, "Athletics") == 0
    assert size_skill_shift(char, data, "No Such Skill") == 0


# --- the rungs a size effect can be held at -------------------------------------------


def _steps(power, char, data):
    return size_steps(power, power.effects[0], char, data)


def test_the_ladder_names_the_size_reached_at_each_rank(data) -> None:
    """A rung is labelled by what the wielder *becomes*, not by what it cost."""
    char = _char(data)
    power = _growth(char, 3)

    assert [(s.rank, s.category) for s in _steps(power, char, data)] == [
        (1, "Large"),
        (2, "Huge"),
        (3, "Gargantuan"),
    ]
    # Full rank until something dials it down, so the top rung is the live one.
    assert [s.current for s in _steps(power, char, data)] == [False, False, True]


def test_the_ladder_is_read_against_the_wielder(data) -> None:
    """A Small character's Growth 2 climbs Medium → Large, as the sheet already said."""
    char = _char(data, "Small")
    power = _growth(char, 2)

    assert [s.category for s in _steps(power, char, data)] == ["Medium", "Large"]
    assert effective_size(char, data) == "Large"


def test_shrinking_walks_the_table_the_other_way(data) -> None:
    char = _char(data)
    power = _growth(char, 3, effect="shrinking")

    assert [s.category for s in _steps(power, char, data)] == ["Small", "Tiny", "Diminutive"]


def test_holding_a_rung_moves_the_whole_sheet(data) -> None:
    """The dialled rank is the one number everything else follows from."""
    char = _char(data)
    power = _growth(char, 3)
    char.abilities["STA"] = 2

    power.effects[0].current_rank = 1
    assert effective_size(char, data) == "Large"
    assert size_trait_modifier(char, data, "resistance", "TOUGHNESS") == 1
    assert resistance_total(char, data, "TOUGHNESS") == 3
    assert [s.current for s in _steps(power, char, data)] == [True, False, False]

    power.effects[0].current_rank = 3
    assert effective_size(char, data) == "Gargantuan"
    assert resistance_total(char, data, "TOUGHNESS") == 5


def test_a_rung_left_over_from_a_larger_bought_rank_clamps(data) -> None:
    """Editing Growth 3 down to 1 must not leave the effect running at 3."""
    char = _char(data)
    power = _growth(char, 3)
    power.effects[0].current_rank = 3

    power.effects[0].rank = 1
    assert effect_current_rank(power.effects[0]) == 1
    assert effective_size(char, data) == "Large"


def test_a_switched_off_effect_lights_no_rung(data) -> None:
    """The ladder says where the power *is*, and off is nowhere — not rank 1."""
    char = _char(data)
    power = _growth(char, 3)
    power.effects[0].current_rank = 2
    power.effects[0].toggled_on = False

    assert [s.current for s in _steps(power, char, data)] == [False, False, False]
    assert effective_size(char, data) == "Medium"
    # And the rung is remembered, so switching back on returns to where it was.
    power.effects[0].toggled_on = True
    assert effective_size(char, data) == "Huge"


def test_ranks_the_table_clamps_fold_into_one_rung(data) -> None:
    """Four buttons all reading 'Awesome' would be four ways to do the same thing."""
    char = _char(data, "Colossal")
    power = _growth(char, 3)
    power.effects[0].current_rank = 2

    steps = _steps(power, char, data)
    assert [(s.rank, s.last_rank, s.category) for s in steps] == [(1, 3, "Awesome")]
    # The dialled rank is inside that rung's span, so the one button is still lit.
    assert [s.current for s in steps] == [True]


def test_only_a_size_effect_has_rungs(data) -> None:
    char = _char(data)
    power = Power(name="Blast", effects=[PowerEffectInstance("damage", rank=5)])
    char.powers.append(power)

    assert _steps(power, char, data) == ()


def test_dialling_down_refunds_nothing(data) -> None:
    """What a power is worth is what it was bought at."""
    char = _char(data)
    power = _growth(char, 3)
    full = power_total_cost(power, data, char)

    power.effects[0].current_rank = 1
    assert power_total_cost(power, data, char) == full


def test_the_dialled_rank_is_saved_and_reloads_at_the_same_size(data) -> None:
    """The rung a size power is held at is part of the save, not lost on reopening.

    A Growth held at Large is a decision about the character — four of the sheet's
    numbers hang off it — so a plain load has to come back at the same size rather
    than at full rank.
    """
    char = _char(data)
    power = _growth(char, 3)
    power.effects[0].current_rank = 2

    assert power.effects[0].to_dict()["current_rank"] == 2

    snapshot = char.to_dict()
    char.restore(snapshot)
    assert char.powers[0].effects[0].current_rank == 2
    assert effective_size(char, data) == "Huge"

    # And a plain load — opening the saved file — lands on the same rung.
    reloaded = Character.from_dict(snapshot)
    assert reloaded.powers[0].effects[0].current_rank == 2
    assert effective_size(reloaded, data) == "Huge"

    # An effect nobody has dialled still writes nothing and comes up all the way up.
    assert "current_rank" not in _growth(_char(data), 3).effects[0].to_dict()


# -- what a size power does to walking pace ---------------------------------------


def _shrink_with_normal_speed(char: Character, rank: int, *, dialled: int | None = None) -> Power:
    """Shrinking with the extra that says your speed isn't reduced while shrunk."""
    effect = PowerEffectInstance(
        "shrinking",
        rank=rank,
        extras=[ModifierSelection(modifier_id="normal_speed_shrinking", rank=1)],
    )
    effect.current_rank = dialled
    power = Power(name="Shrink", effects=[effect])
    power.activated = True
    char.powers.append(power)
    return power


@pytest.mark.parametrize("dialled", [None, 1, 2])
def test_normal_speed_leaves_you_at_your_own_pace_however_far_you_are_dialled(
    data, dialled
) -> None:
    """Cancelling the shrink's penalty must not cancel the one you were born with.

    The removal is the effect's *bought* rank while the penalty follows the rung it
    is dialled to, so the two agreed only on a linear stretch of the Size Table with
    nothing dialled. Anywhere else the cancellation overshot and a Small character
    out-walked their own unshrunk self.
    """
    plain = base_ground_speed_rank(_char(data, "Small"), data)

    char = _char(data, "Small")
    _shrink_with_normal_speed(char, 4, dialled=dialled)

    assert base_ground_speed_rank(char, data) == plain


def test_normal_speed_does_not_overshoot_past_the_tables_floor(data) -> None:
    """Past the clamp the rank keeps climbing while the speed modifier stops."""
    char = _char(data, "Small")
    _shrink_with_normal_speed(char, 8)

    assert base_ground_speed_rank(char, data) == base_ground_speed_rank(_char(data, "Small"), data)


def test_shrinking_without_normal_speed_still_slows_you_down(data) -> None:
    """The guard on the three above: the penalty is real when nothing lifts it."""
    char = _char(data, "Medium")
    _growth(char, 4, effect="shrinking")

    assert base_ground_speed_rank(char, data) < base_ground_speed_rank(_char(data), data)


def test_being_large_does_not_inflate_the_estimated_power_level(data) -> None:
    """The estimator and the validator have to agree about what size is worth.

    Size raises an attack effect's effective rank *and* the cap it is measured
    against, which power_pl_violations does by shifting its limits. The estimator
    used the raised rank against an unshifted cap, so switching a Growth on
    advertised a mook at a Power Level its own validator called perfectly legal —
    and on a GM card that estimate *is* the creature's PL.
    """
    plain = _char(data)
    damage = Power(name="Smash", effects=[PowerEffectInstance("damage", rank=10)])
    damage.activated = True
    plain.powers.append(damage)
    baseline = estimated_power_level(plain, data)

    for rank in (2, 4):
        char = _char(data)
        smash = Power(name="Smash", effects=[PowerEffectInstance("damage", rank=10)])
        smash.activated = True
        char.powers.append(smash)
        _growth(char, rank)

        assert estimated_power_level(char, data) == baseline
        assert power_pl_violations(char.powers[0], char, data) == []


# -- reach -----------------------------------------------------------------------------


def test_size_gives_reach_and_elongation_stretches_it_further(data) -> None:
    """The two sources add as real distances, because that is the only way they can.

    A Gargantuan character reaches three Spaces (18 ft) and an Elongation 1 stretches 15
    ft further, which is 33 ft — five Spaces and a half. Rounding each source to Spaces
    before adding would quietly lose that half at every step, so the feet are the truth
    and the Spaces are read back off them.
    """

    char = _char(data)
    _growth(char, 3)
    assert reach_text(character_reach(char, data)) == "3 spaces / 18 ft."

    _growth(char, 1, effect="elongation")
    assert reach_text(character_reach(char, data)) == "~5 spaces / 33 ft."


def test_elongation_reaches_its_own_distance_rank_per_rank(data) -> None:
    """Each rank is worth the distance value at the rank ``effects.json`` names — 15 ft.

    Not a rank added to a rank: reach sums several sources, and ranks are never added to
    each other. The Spaces are rounded down and flagged, since half a Space of reach does
    not let anyone strike into the next square.
    """

    char = _char(data)
    _growth(char, 3, effect="elongation")
    assert reach_text(character_reach(char, data)) == "~7 spaces / 45 ft."


def test_an_unchanged_reach_is_not_a_reading(data) -> None:
    """The baseline Space is what a close attack already means, so nothing states it.

    A character nothing has stretched reaches exactly as far as their bought size says,
    and that is the number every other reach is stated against — so the System block's
    row asks :func:`reach_is_altered` rather than printing it on every sheet.
    """

    char = _char(data)
    assert not reach_is_altered(char, data)
    assert character_reach(char, data).feet == base_character_reach(char, data).feet

    big = _char(data, "Huge")  # ...and a bought size is still that character's baseline
    assert not reach_is_altered(big, data)


def test_shrinking_out_of_reach_is_a_change_the_feet_cannot_show(data) -> None:
    """Shrinking to a size the table gives no reach reads as zero feet either way.

    A baseline-size character contributes zero feet too, so the subtraction alone would
    call this unchanged — and losing your reach entirely is exactly what the row is for.
    """

    char = _char(data)
    _growth(char, 2, effect="shrinking")
    assert effective_size(char, data) == "Tiny"
    assert reach_is_altered(char, data)
    assert reach_text(character_reach(char, data)) == "0 spaces / 0 ft."


def test_the_reach_extra_is_stated_from_the_arm_that_swings_it(data) -> None:
    """A Reach 2 weapon on a Gargantuan character strikes at five Spaces, not two.

    Reach belongs to the arm doing the reaching, so the extra is only meaningful added to
    the reach its wielder already has — and five is the number the player needs, rather
    than a ``Reach 2`` chip to add by hand to a row on another block.
    """

    char = _char(data)
    _growth(char, 3)
    effect = PowerEffectInstance("damage", rank=3, extras=[ModifierSelection("reach", rank=2)])
    rows = {row.label: row.value for row in effect_stat_rows(effect, data, char)}
    assert rows["Reach"] == "5 spaces / 30 ft."
    assert "Reach 2" not in rows.get("Notes", "")  # spoken for by the row above

    # Without a wielder (the Power Constructor) the line states what the extra alone buys.
    bare = {row.label: row.value for row in effect_stat_rows(effect, data)}
    assert bare["Reach"] == "2 spaces / 12 ft."


def test_a_rationed_elongation_reaches_only_as_far_as_its_share_bought(data) -> None:
    """The reach follows the dialled rank, like every other number a Dynamic pool holds."""

    char = _char(data)
    power = _growth(char, 4, effect="elongation")
    assert reach_text(character_reach(char, data)) == "10 spaces / 60 ft."
    power.effects[0].current_rank = 1
    assert effect_current_rank(power.effects[0], data, char) == 1
    assert reach_text(character_reach(char, data)) == "~2 spaces / 15 ft."

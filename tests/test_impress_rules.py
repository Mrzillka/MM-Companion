"""The Impress action's rules, and the targeting layer it is the first user of.

Headless — ``core`` only. What a check against several targets resolves to is
provable without a window, and the GM window and the server only ever carry these
numbers around.
"""

from __future__ import annotations

import random

import pytest

from mm_companion.core.character import AdvantageSelection, Character
from mm_companion.core.data_loader import ResistanceOutcome, load_game_data
from mm_companion.core.dice import resolve_group
from mm_companion.core.rules import (
    KIND_BEST_OF,
    KIND_IMPRESS,
    MODE_REQUEST,
    ImpressAttempt,
    ResistAnswer,
    TraitChoice,
    apply_rung,
    best_choice,
    follow_up_for_result,
    group_penalty,
    impress_rung,
    impress_skill_choices,
    impress_spec,
    impression_allowed,
    impression_outcomes,
    localize_spec,
    modifier_lines,
    modifier_total,
    opposed_dc,
    outcome_rung,
    resist_candidates,
    resist_choices,
    resolve_rung,
)


@pytest.fixture(scope="module")
def data():
    return load_game_data()


@pytest.fixture
def talker(data) -> Character:
    char = Character.new_default(data)
    char.abilities.update({"PRE": 3, "AWE": 1})
    char.skill_ranks["Deception"] = 6
    char.skill_ranks["Insight"] = 4
    char.specializations["Deception"] = ["Bluffing"]
    char.skill_ranks["Deception::spec::Bluffing"] = 4
    return char


# -- the data -----------------------------------------------------------------


def test_the_base_ruleset_ships_the_impress_action(data) -> None:
    rules = data.impress
    assert rules.available
    assert {"deception", "intimidation", "persuasion"} <= set(rules.skills)
    assert rules.group_penalty == -5
    demoralized = rules.impression("demoralized")
    assert demoralized is not None
    assert impression_outcomes(data, demoralized) == ("Impaired", "Frightened")


def test_every_impression_names_conditions_the_catalog_has(data) -> None:
    catalog = data.condition_catalog()
    for impression in data.impress.impressions:
        for rung in impression.outcomes:
            for condition_id in rung.conditions:
                assert condition_id in catalog, (impression.id, condition_id)
        if impression.advantage:
            assert any(a.id == impression.advantage for a in data.advantages), impression.id


# -- who may impress with what --------------------------------------------------


def test_an_advantage_gated_impression_needs_the_advantage(talker, data) -> None:
    dazed = data.impress.impression("dazed")
    assert not impression_allowed(talker, data, dazed)
    talker.advantages.append(AdvantageSelection("Dazing Interaction"))
    assert impression_allowed(talker, data, dazed)
    # No character to ask means the GM decides.
    assert impression_allowed(None, data, dazed)
    assert impression_allowed(talker, data, data.impress.impression("demoralized"))


def test_every_interaction_skill_row_is_offered_with_its_total(talker, data) -> None:
    choices = {c.key: c for c in impress_skill_choices(talker, data)}
    assert choices["Deception"].modifier == 9
    # A specialized pool is a row of its own, and a higher one.
    assert choices["Deception::spec::Bluffing"].modifier > choices["Deception"].modifier
    assert "Intimidation" in choices and "Persuasion" in choices


def test_resisting_offers_the_same_skill_insight_and_will(data) -> None:
    candidates = resist_candidates(data, "Deception::spec::Bluffing")
    # A specialized pool is resisted as its skill.
    assert candidates == (("skill", "Deception"), ("skill", "Insight"), ("resistance", "WILL"))
    # Impressing with Insight would not offer it twice.
    assert len(resist_candidates(data, "Insight")) == 2


def test_use_highest_picks_the_best_of_the_targets_own_numbers(talker, data) -> None:
    choices = resist_choices(talker, data, "Deception")
    labels = [c.label for c in choices]
    assert labels == ["Deception", "Insight", "Will"]
    assert best_choice(choices).label == "Deception"
    assert best_choice([]) is None
    tie = [TraitChoice("skill", "A", "A", 3), TraitChoice("skill", "B", "B", 3)]
    assert best_choice(tie).key == "A"


# -- the GM's modifiers -----------------------------------------------------------


def test_modifiers_add_up_and_an_exclusive_pair_counts_once(data) -> None:
    values = {"simple_action": True, "full_action": True, "mood": 3, "surprised": True}
    # Simple action wins the "action" group by coming first; full action is ignored.
    assert modifier_total(data, values) == -5 + 3 + 2
    assert modifier_lines(data, values)[0].endswith("-5")


def test_a_slider_value_off_the_scale_is_clamped_and_snapped(data) -> None:
    assert modifier_total(data, {"mood": 40}) == 5
    assert modifier_total(data, {"previous_attempts": -7}) == -10
    assert modifier_total(data, {"mood": "garbage"}) == 0


def test_the_group_penalty_applies_only_past_one_target(data) -> None:
    assert group_penalty(data, 0) == 0
    assert group_penalty(data, 1) == 0
    assert group_penalty(data, 3) == -5


# -- the rolls ------------------------------------------------------------------


def test_a_request_mode_check_carries_the_resistance_as_an_opposed_follow_up(talker, data) -> None:
    impression = data.impress.impression("demoralized")
    skill = TraitChoice("skill", "Intimidation", "Intimidation", 4)
    spec = impress_spec(data, impression, skill, 6, source="Nova", request=True)
    assert spec.kind == KIND_IMPRESS
    assert spec.label == "Impress: Demoralized (Intimidation)"
    follow = spec.follow_up
    assert follow.kind == KIND_BEST_OF and follow.dc_from_total
    assert follow.outcomes == ("Impaired", "Frightened")

    # Its DC is the check's total, raised on a natural 20 — and a natural 1 is just
    # a low total, not an attack's graze.
    assert follow_up_for_result(spec, 12, None, data, total=18).dc == 18
    crit = follow_up_for_result(spec, 20, None, data, total=26)
    assert crit.dc == 26 + data.system.critical_effect_bonus
    assert follow_up_for_result(spec, 1, None, data, total=7).modifier == 0

    # Clicked on a sheet, it rolls that sheet's best option and says which.
    resolved = localize_spec(follow_up_for_result(spec, 12, None, data, total=18), talker, data)
    assert resolved.label.endswith("(Insight)")
    assert resolved.modifier == 5


def test_the_spec_survives_the_wire(data) -> None:
    from mm_companion.core.rules import RollSpec
    from mm_companion.core.session.protocol import sanitize_spec

    impression = data.impress.impression("distracted")
    skill = TraitChoice("skill", "Deception", "Deception", 4)
    spec = impress_spec(data, impression, skill, 4, request=True)
    back = RollSpec.from_dict(sanitize_spec(spec.to_dict()))
    assert back == spec


# -- the targeting layer -----------------------------------------------------------


def test_a_degree_picks_a_rung_and_the_last_covers_deeper_failures(data) -> None:
    ladder = data.impress.impression("distracted").outcomes
    assert outcome_rung(ladder, 1) is None
    assert outcome_rung(ladder, None) is None
    assert outcome_rung(ladder, -1).conditions == ("vulnerable",)
    assert outcome_rung(ladder, -4).conditions == ("defenseless",)
    success = ResistanceOutcome(conditions=("hit",))
    assert outcome_rung(ladder, 2, success) is success


def test_an_opposed_dc_is_the_total_plus_a_crit_and_an_offset(data) -> None:
    assert opposed_dc(17, 9, data) == 17
    assert opposed_dc(25, 20, data, offset=-2) == 25 + data.system.critical_effect_bonus - 2


def test_a_rung_resolves_its_parameter_from_whoever_imposed_it(talker, data) -> None:
    demoralized = data.impress.impression("demoralized")
    rung = impress_rung(demoralized, -2)
    assert resolve_rung(talker, rung, data, source="Doctor Doom") == (
        ("frightened", "Doctor Doom"),
    )
    applied = apply_rung(talker, rung, data, source="Doctor Doom")
    assert applied == (("frightened", "Doctor Doom"),)
    assert [(c.condition_id, c.parameter) for c in talker.conditions] == [
        ("frightened", "Doctor Doom")
    ]
    # A rung that is only words (a Coerced attitude) applies nothing.
    coerced = impress_rung(data.impress.impression("coerced"), -1)
    assert resolve_rung(talker, coerced, data) == ()


def test_a_group_rolls_the_lead_first_and_each_line_against_its_total() -> None:
    rng = random.Random(7)
    result = resolve_group([(3, 0), (0, -4)], lead_modifier=5, critical_bonus=5, rng=rng)
    dc = result.lead_total + (5 if result.lead_die == 20 else 0)
    assert [line.dc for line in result.lines] == [dc, dc - 4]
    assert all(line.total == line.die_roll + line.modifier for line in result.lines)

    fixed = resolve_group([(2, 1)], dc=15, rng=random.Random(1))
    assert fixed.lead_die is None and fixed.lines[0].dc == 16


def test_a_natural_20_on_the_lead_raises_every_dc() -> None:
    class Twenty(random.Random):
        def randint(self, a: int, b: int) -> int:
            return 20

    result = resolve_group([(0, 0)], lead_modifier=1, critical_bonus=5, rng=Twenty())
    assert result.lead_total == 21
    assert result.lines[0].dc == 26


# -- what travels between seats -------------------------------------------------------


def test_an_attempt_round_trips_and_rejects_junk() -> None:
    attempt = ImpressAttempt(
        action_id="a1",
        impression="demoralized",
        skill_row="Intimidation",
        skill_label="Intimidation",
        skill_modifier=8,
        targets=("s1", "s2"),
        mode=MODE_REQUEST,
        source_name="Nova",
        source_ref="s0",
    )
    assert ImpressAttempt.from_dict(attempt.to_dict()) == attempt
    assert ImpressAttempt.from_dict({"action_id": "x"}) is None
    assert ImpressAttempt.from_dict("nope") is None
    odd = ImpressAttempt.from_dict({"action_id": "x", "impression": "y", "mode": "?", "targets": 3})
    assert odd.mode == "resolve" and odd.targets == ()


def test_an_answer_round_trips() -> None:
    answer = ResistAnswer(
        action_id="a1",
        use_highest=False,
        choice="resistance:WILL",
        label="Will",
        modifier=7,
        options=(TraitChoice("resistance", "WILL", "Will", 7),),
    )
    assert ResistAnswer.from_dict(answer.to_dict()) == answer
    assert ResistAnswer.from_dict({}) is None

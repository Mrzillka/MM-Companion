"""Minions (PDF p25): the flag, the rules it switches on, and how they travel.

Headless — the rules layer, the dice, the server and the wire. The GM window's side is
proven in ``test_minions_ui.py``.
"""

from __future__ import annotations

from mm_companion.core.character import Character
from mm_companion.core.data_loader import ResistanceOutcome
from mm_companion.core.dice import ROUTINE_DIE, resolve_check, resolve_group
from mm_companion.core.powers import ModifierSelection, Power, PowerEffectInstance
from mm_companion.core.rules import (
    BOOK_MINION_RULES,
    LADDER_DAMAGE,
    MINION_RULE_KEYS,
    MinionRules,
    RollSpec,
    SpecLadder,
    answer_rung,
    capped_escalation,
    critical_answer,
    critical_rung,
    damage_steps,
    follow_up_for_result,
    impress_rung,
    minion_added_success,
    minion_rung,
    minion_save_spec,
    minion_step,
    outcome_rung,
    routine_impress_dc,
)
from mm_companion.core.session.model import RollRecord, new_session
from mm_companion.core.session.protocol import (
    RollRequest,
    sanitize_group_line,
    sanitize_scene,
)
from mm_companion.core.session.server import SessionServer


def damage_ladder(data) -> tuple[tuple[ResistanceOutcome, ...], ResistanceOutcome | None]:
    effect = next(e for e in data.effects if e.id == data.system.damage_effect)
    return effect.resistance_outcomes, effect.resistance_success


def ids(rung: ResistanceOutcome | None) -> tuple[str, ...]:
    return () if rung is None else rung.conditions


# -- the flag ----------------------------------------------------------------------


def test_the_flag_is_omitted_until_set_so_old_saves_round_trip(data) -> None:
    character = Character.new_default(data)
    assert "minion" not in character.to_dict()
    character.minion = True
    raw = character.to_dict()
    assert raw["minion"] is True
    assert Character.from_dict(raw).minion is True
    # Anything but a real true reads as a full NPC.
    assert Character.from_dict({**raw, "minion": "yes"}).minion is False


def test_restore_carries_the_flag_both_ways(data) -> None:
    character = Character.new_default(data)
    before = character.to_dict()
    character.minion = True
    after = character.to_dict()
    character.restore(before)
    assert character.minion is False
    character.restore(after)
    assert character.minion is True


def test_the_numbers_are_data(data) -> None:
    assert data.system.minions.impress_bonus == 5
    assert data.system.minions.ceiling == "incapacitated"


# -- the session's switches --------------------------------------------------------


def test_the_book_is_every_switch_on() -> None:
    assert all(BOOK_MINION_RULES.to_dict().values())
    assert list(BOOK_MINION_RULES.to_dict()) == list(MINION_RULE_KEYS)


def test_rules_round_trip_and_read_tolerantly() -> None:
    rules = MinionRules(worst_degree=False, show_players=False)
    assert MinionRules.from_dict(rules.to_dict()) == rules
    # A newer app's extra key is ignored; a missing one is the book's; a non-bool too.
    assert MinionRules.from_dict({"worst_degree": False, "later": True}) == MinionRules(
        worst_degree=False
    )
    assert MinionRules.from_dict({"worst_degree": "no"}) == BOOK_MINION_RULES
    assert MinionRules.from_dict(None) == BOOK_MINION_RULES


# -- the worst degree, and no further ------------------------------------------------


def test_outcome_rung_worst_lands_any_failure_on_the_last_rung(data) -> None:
    ladder, success = damage_ladder(data)
    assert outcome_rung(ladder, -1, success, worst=True) is ladder[-1]
    assert outcome_rung(ladder, 1, success, worst=True) is success
    assert outcome_rung(ladder, -1, success) is ladder[0]


def test_escalation_stops_at_the_ceiling() -> None:
    pairs = (("dazed", "stunned"), ("incapacitated", "dying"), ("dying", "dead"))
    assert capped_escalation(pairs, "incapacitated") == (("dazed", "stunned"),)


def test_a_minion_failing_by_one_is_incapacitated_and_goes_no_further(data) -> None:
    ladder, success = damage_ladder(data)
    rung = minion_rung(ladder, -1, success, data, minion=True)
    assert "incapacitated" in ids(rung)
    assert rung.escalates == () and rung.note == ""
    # A made save is the same Hit anyone takes.
    assert minion_rung(ladder, 2, success, data, minion=True) is success
    # Not a minion: the rolled rung.
    assert minion_rung(ladder, -1, success, data, minion=False) is ladder[0]


def test_each_switch_turns_its_rule_off(data) -> None:
    ladder, success = damage_ladder(data)
    rolled = minion_rung(
        ladder, -1, success, data, minion=True, rules=MinionRules(worst_degree=False)
    )
    assert ids(rolled) == ladder[0].conditions
    walking = minion_rung(
        ladder, -3, success, data, minion=True, rules=MinionRules(stop_at_incapacitated=False)
    )
    assert ("incapacitated", "dying") in walking.escalates


def test_the_damage_buttons_stop_a_minion_at_the_ceiling(data) -> None:
    worst = damage_steps(data)[-1]
    assert minion_step(worst, data).escalates == ()
    assert minion_step(worst, data, MinionRules(stop_at_incapacitated=False)) is worst


def test_answering_a_save_as_a_minion_lands_the_worst(data) -> None:
    ladder, success = damage_ladder(data)
    spec_ladder = SpecLadder(kind=LADDER_DAMAGE, rungs=ladder, success=success)
    mook = Character.new_default(data)
    mook.minion = True
    assert "incapacitated" in ids(answer_rung(spec_ladder, -1, mook, data))
    assert (
        ids(answer_rung(spec_ladder, -1, Character.new_default(data), data)) == ladder[0].conditions
    )


def test_an_impressed_minion_takes_the_most_severe(data) -> None:
    demoralized = next(i for i in data.impress.impressions if i.id == "demoralized")
    rung = impress_rung(demoralized, -1, data, minion=True)
    assert rung is demoralized.outcomes[-1]
    assert impress_rung(demoralized, -1) is demoralized.outcomes[0]


def test_a_critical_hit_lands_the_worst_unless_the_session_says_not(data) -> None:
    ladder, _success = damage_ladder(data)
    assert "incapacitated" in ids(critical_rung(ladder, data))
    assert critical_rung(ladder, data, MinionRules(crit_auto_fails=False)) is None


def test_a_minions_save_card_reads_the_worst_rung() -> None:
    spec = RollSpec(label="Toughness", outcomes=("Dazed", "Stunned", "Incapacitated"))
    assert minion_save_spec(spec).outcomes == ("Incapacitated",)
    assert minion_save_spec(spec, MinionRules(worst_degree=False)) is spec


# -- no added success --------------------------------------------------------------


def test_a_minions_natural_twenty_adds_no_degree_and_is_no_critical() -> None:
    normal = resolve_check(0, 25, roll=20)
    minion = resolve_check(0, 25, roll=20, added_success=False)
    assert normal.degree == 1 and normal.critical
    assert minion.degree == -1 and not minion.critical
    # Its natural 1 still costs a degree.
    assert resolve_check(10, 11, roll=1, added_success=False).degree == -1


def test_a_group_line_may_be_a_minions() -> None:
    class Twenty:
        def randint(self, _a: int, _b: int) -> int:
            return 20

    result = resolve_group([(0, 25), (0, 25, True)], dc=0, rng=Twenty())
    assert [r.degree for r in result.lines] == [1, -1]


def test_only_a_minion_at_a_table_playing_the_rule_loses_its_crits() -> None:
    assert minion_added_success(False)
    assert not minion_added_success(True)
    assert minion_added_success(True, MinionRules(no_added_success=False))


def test_a_minions_twenty_raises_no_save(data) -> None:
    save = RollSpec(label="Toughness", dc=20)
    attack = RollSpec(label="Punch", follow_up=save)
    assert follow_up_for_result(attack, 20, 1, data).dc == 20 + data.system.critical_effect_bonus
    assert follow_up_for_result(attack, 20, 1, data, added_success=False).dc == 20
    # The graze still applies: the rule takes the luck, not the bad luck.
    grazed = follow_up_for_result(attack, 1, 1, data, added_success=False)
    assert grazed.modifier == data.system.critical_miss_resistance_bonus


# -- routine checks ----------------------------------------------------------------


def test_the_impress_routine_dc_is_ten_the_check_and_the_bonus(data) -> None:
    assert routine_impress_dc(8, data) == 10 + 8 + 5
    assert routine_impress_dc(8, data, offset=2) == 25


def _server() -> SessionServer:
    return SessionServer(new_session("Table"), host="127.0.0.1", port=0, persist=False)


def test_the_server_takes_ten_for_a_routine_check() -> None:
    record = _server().roll(label="Attack", bonus=6, dc=15, routine=True, reroll=True)
    assert record.die == ROUTINE_DIE and record.routine
    assert not record.critical and not record.reroll
    assert record.degree == 1


def test_the_server_stamps_a_minions_roll() -> None:
    srv = _server()

    class Twenty:
        def randint(self, _a: int, _b: int) -> int:
            return 20

    srv._rng = Twenty()  # noqa: SLF001
    record = srv.roll(label="Punch", dc=25, minion=True)
    assert record.die == 20 and record.minion and not record.critical
    assert record.degree == -1  # 20 vs 25 and no added success: still a miss
    (line,) = srv.group_roll("g", [{"label": "Mook: Dodge", "bonus": 0, "minion": True}], dc=25)
    assert line.minion and line.degree == -1


# -- the wire ----------------------------------------------------------------------


def test_the_flags_cross_the_wire() -> None:
    request = RollRequest.from_payload({"routine": True, "minion": True})
    assert request.routine and request.minion
    assert sanitize_group_line({"minion": True})["minion"] is True
    assert sanitize_group_line({"minion": 1})["minion"] is False
    record = RollRecord(seq=1, player_id="gm", player_name="GM", die=10, routine=True, minion=True)
    again = RollRecord.from_dict(record.to_dict())
    assert again.routine and again.minion


def test_the_scene_badge_is_whitelisted_only_when_true() -> None:
    badged, plain = sanitize_scene(
        [{"ref": "a", "name": "Thug", "minion": True}, {"ref": "b", "minion": "yes"}]
    )
    assert badged["minion"] is True
    assert "minion" not in plain


# -- a critical hit against an Impervious minion ---------------------------------------


def _armoured_minion(data, rank: int) -> Character:
    char = Character.new_default(data)
    char.minion = True
    effect = PowerEffectInstance("protection", rank=rank)
    effect.extras.append(ModifierSelection("impervious"))
    char.powers.append(Power(name="Plating", effects=[effect]))
    return char


def _damage_ladder(data, rank: int) -> SpecLadder:
    rungs = next(e for e in data.effects if e.id == data.system.damage_effect).resistance_outcomes
    return SpecLadder(LADDER_DAMAGE, rungs=rungs, rank=rank)


def test_a_critical_an_impervious_minion_shrugs_off_lands_nothing(data) -> None:
    # Damage 4 critical is rank 9 against Impervious 10: no effect, so no auto-fail.
    assert critical_answer(_damage_ladder(data, 4), _armoured_minion(data, 10), data) is None


def test_a_critical_raised_past_impervious_lands_the_worst(data) -> None:
    # Damage 6 critical is rank 11, past Impervious 10: the worst, unrolled.
    rung = critical_answer(_damage_ladder(data, 6), _armoured_minion(data, 10), data)
    assert rung is not None and "incapacitated" in rung.conditions


def test_a_critical_on_a_minion_with_no_sheet_is_the_plain_rung(data) -> None:
    ladder = _damage_ladder(data, 4)
    assert critical_answer(ladder, None, data) == critical_rung(ladder.rungs, data)

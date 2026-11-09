"""The self actions (PDF p200–205): Aim, Charge and Slam, Defend, Recover, Drop Prone,
Stand, Finishing Attack and Catch — read off ``actions.json`` (combat maneuvers, phase 6),
and the two dice an Aim asks the server for."""

from __future__ import annotations

from mm_companion.core.character import Character
from mm_companion.core.data_loader import ConditionChange
from mm_companion.core.powers import Power, PowerEffectInstance
from mm_companion.core.rules import (
    LADDER_DAMAGE,
    RollSpec,
    action_reason,
    apply_actions,
    apply_condition,
    apply_condition_changes,
    attack_choices,
    check_spec,
    follow_up_for_result,
    judge_attack,
    line_fit,
    line_key,
    recover_choices,
    self_action,
    self_action_changes,
    self_action_note,
    speed_rank,
    split_line_key,
)
from mm_companion.core.session.model import RollRecord, new_session
from mm_companion.core.session.protocol import RollRequest, sanitize_spec
from mm_companion.core.session.server import SessionServer


def _hero(data, *, attack: int = 6, rank: int = 8, stamina: int = 4) -> Character:
    hero = Character.new_default(data)
    hero.abilities["ATK"] = attack
    hero.abilities["STA"] = stamina
    hero.powers = [Power(name="Punch", effects=[PowerEffectInstance("damage", rank=rank)])]
    return hero


def _attack(hero, data) -> RollSpec:
    (choice,) = attack_choices(hero, data)
    return choice.spec


def _has(hero, condition_id: str) -> bool:
    return any(c.condition_id == condition_id for c in hero.conditions)


# -- the data -------------------------------------------------------------------------


def test_the_self_actions_load_with_their_effects(data) -> None:
    charge = self_action(data, "charge")
    assert charge.effect.attack_modifier == -2 and charge.effect.line
    assert [o.id for o in charge.options] == ["charge", "slam", "slam_full"]
    assert charge.slam.rank_bonus == 1 and charge.slam.self_resistance == "TOUGHNESS"
    assert self_action(data, "aim").effect.roll_twice
    assert self_action(data, "catch").effect.check.dc == 10
    assert self_action(data, "stand").requires == ("prone",)
    assert self_action(data, "drop_prone").refused_by == "prone"
    assert self_action(data, "recover").recovery.fatigue
    assert self_action(data, "accurate_attack") is None  # a maneuver, not a self action
    assert self_action(data, "block").reaction.step == -2
    # A record that does nothing (a mod's action this version cannot run) is none.
    from dataclasses import replace

    from mm_companion.core.data_loader import CombatAction, CombatRules

    blank = CombatAction("mod_blank", "Blank", kind="self")
    modded = replace(data, combat=CombatRules(actions=(*data.combat.actions, blank)))
    assert self_action(modded, "mod_blank") is None


def test_an_option_overrides_its_action_field_by_field(data) -> None:
    charge = self_action(data, "charge")
    slam = charge.effect_for("slam_full")
    assert slam.slam and slam.full_speed
    assert slam.attack_modifier == -2  # kept from the action
    assert slam.line != charge.effect.line  # its own caption
    kill = self_action(data, "finishing_attack").effect_for("kill")
    assert kill.dc == 10 and kill.critical_hit and kill.kill.condition == "dead"


def test_a_line_key_carries_the_option() -> None:
    assert line_key("charge") == "charge"
    assert line_key("charge", "slam") == "charge/slam"
    assert split_line_key("charge/slam") == ("charge", "slam")
    assert split_line_key("aim") == ("aim", "")


# -- the lines on an attack -----------------------------------------------------------


def test_charge_takes_two_off_the_attack_and_says_so(data) -> None:
    hero = _hero(data)
    spec = apply_actions(_attack(hero, data), [("charge", None)], data, hero)
    assert spec.modifier == 4
    assert spec.label == "Attack +4 vs. Defense (Charge)"


def test_lines_and_maneuvers_are_named_together(data) -> None:
    hero = _hero(data)
    spec = apply_actions(_attack(hero, data), [("all_out_attack", 3), ("charge", None)], data, hero)
    assert spec.modifier == 7
    assert spec.label.endswith("(All-Out Attack +3, Charge)")


def test_aim_rolls_twice(data) -> None:
    hero = _hero(data)
    spec = apply_actions(_attack(hero, data), [("aim", None)], data, hero)
    assert spec.roll_twice and spec.modifier == 6
    # Local only: it does not travel, and a spec rebuilt off the wire still equals it.
    assert "roll_twice" not in spec.to_dict()
    assert RollSpec.from_dict(spec.to_dict()) == spec


def test_a_finishing_attack_is_against_ten_and_every_hit_is_critical(data) -> None:
    hero = _hero(data)
    spec = apply_actions(_attack(hero, data), [("finishing_attack/finishing", None)], data, hero)
    assert spec.dc == 10 and spec.critical_hit
    assert spec.to_dict()["critical_hit"] is True
    assert sanitize_spec(spec.to_dict())["critical_hit"] is True
    assert RollSpec.from_dict(spec.to_dict()).critical_hit
    # A 12 that hits raises the save as a natural 20 would.
    save = follow_up_for_result(spec, 12, 1, data)
    assert save.dc == spec.follow_up.dc + data.system.critical_effect_bonus
    assert "critical hit" in save.label


def test_the_gm_judges_a_finishing_hit_critical() -> None:
    assert judge_attack(6, 12, 10, critical_hit=True).critical
    assert not judge_attack(6, 12, 10).critical
    assert not judge_attack(0, 2, 10, critical_hit=True).critical  # a miss is no critical


def test_intent_to_kill_makes_the_third_failure_dead(data) -> None:
    hero = _hero(data)
    spec = apply_actions(_attack(hero, data), [("finishing_attack/kill", None)], data, hero)
    save = spec.follow_up
    assert save.ladder.kind == LADDER_DAMAGE
    assert save.ladder.rungs[2].conditions == ("dead",)
    assert len(save.ladder.rungs) == 3
    assert save.outcomes[2] == "Dead"
    plain = _attack(hero, data).follow_up
    assert save.ladder.rungs[:2] == plain.ladder.rungs[:2]
    assert "Dead" in save.label


def test_a_slam_raises_the_damage_and_loads_the_slammers_own_save(data) -> None:
    hero = _hero(data, rank=8, stamina=4)
    plain = _attack(hero, data)
    spec = apply_actions(plain, [("charge/slam", None)], data, hero)
    # max(8 + 1, speed) = 9
    assert speed_rank(hero, data) < 9
    assert spec.follow_up.dc == plain.follow_up.dc + 1
    assert spec.follow_up.ladder.rank == 9
    assert spec.modifier == 4  # still a Charge
    own = spec.then
    # Resistance halves the rank before the save: 9 // 2 = 4.
    assert own.dc == plain.follow_up.dc - 8 + 4
    assert own.modifier == 4  # this sheet's Toughness, filled in
    assert not own.rolled_by_target and own.trait_key == "TOUGHNESS"
    assert "then" not in spec.to_dict()


def test_a_full_speed_slam_adds_one_more(data) -> None:
    hero = _hero(data, rank=8)
    plain = _attack(hero, data)
    spec = apply_actions(plain, [("charge/slam_full", None)], data, hero)
    assert spec.follow_up.ladder.rank == 10


def test_a_line_does_not_fit_an_attack_with_no_attack_check(data) -> None:
    save = RollSpec(label="Will vs. 15", dc=15, rolled_by_target=True)
    perception = RollSpec(label="Mind Blast vs. Will", follow_up=save)  # no attack check
    assert line_fit(data, "aim", perception)
    assert apply_actions(perception, [("aim", None)], data) == perception
    assert line_fit(data, "aim", None) == ""


def test_a_slam_needs_a_damage_attack(data) -> None:
    save = RollSpec(label="Will vs. 18", dc=18, effect_rank=8, rolled_by_target=True)
    attack = RollSpec(
        label="Attack +6 vs. Defense", modifier=6, follow_up=save, marks=("attack_check",)
    )
    assert line_fit(data, "charge/slam", attack)
    assert line_fit(data, "charge", attack) == ""


# -- the actions taken at once ----------------------------------------------------------


def test_stand_needs_prone_and_drop_prone_refuses_it(data) -> None:
    hero = _hero(data)
    stand, drop = self_action(data, "stand"), self_action(data, "drop_prone")
    assert action_reason(data, stand, hero)
    assert action_reason(data, drop, hero) is None
    apply_condition(hero, "prone", data)
    assert action_reason(data, stand, hero) is None
    assert action_reason(data, drop, hero)


def test_a_player_cannot_take_an_action_the_gm_runs(data) -> None:
    surprise = data.combat.action("surprise_attack")
    assert action_reason(data, surprise, None)
    assert action_reason(data, surprise, None, gm=True) is None


def test_defend_puts_on_a_temporary_two(data) -> None:
    hero = _hero(data)
    changes = self_action_changes(self_action(data, "defend").effect, data)
    assert changes == (ConditionChange("defending", amount=2),)
    assert apply_condition_changes(hero, changes, data)
    (applied,) = hero.conditions
    assert applied.condition_id == "defending" and applied.temporary and applied.amount == 2
    assert self_action_note(data, "Ana", "Defend", changes) == "Ana: Defend — Defending +2."


def test_drop_prone_is_lasting_and_standing_slowly_hinders(data) -> None:
    hero = _hero(data)
    apply_condition_changes(
        hero, self_action_changes(self_action(data, "drop_prone").effect, data), data
    )
    (prone,) = hero.conditions
    assert prone.condition_id == "prone" and not prone.temporary
    slow = self_action_changes(self_action(data, "stand").effect_for("slow"), data)
    apply_condition_changes(hero, slow, data)
    assert not _has(hero, "prone")
    (hindered,) = hero.conditions
    assert hindered.condition_id == "hindered" and hindered.temporary
    note = self_action_note(data, "Ana", "Stand slowly", slow)
    assert note == "Ana: Stand slowly — Hindered, no longer Prone."


def test_the_checks_load_at_their_dc(data) -> None:
    hero = _hero(data)
    hero.abilities["AGL"] = 3
    catch = self_action(data, "catch")
    spec = check_spec(data, hero, catch, catch.effect)
    assert spec.dc == 10 and spec.modifier == 3 and spec.label == "Catch: Agility"
    stand = self_action(data, "stand")
    acro = check_spec(data, hero, stand, stand.effect_for("acrobatics"))
    assert acro.dc == 20 and acro.recovers == "prone" and "Acrobatics" in acro.label
    speed = check_spec(data, hero, stand, stand.effect_for("speed"))
    assert speed.modifier == speed_rank(hero, data) and speed.recovers == "prone"
    assert check_spec(data, hero, stand, stand.effect_for("slow")) is None


def test_recover_offers_the_worst_damage_check_and_a_fatigue_step(data) -> None:
    hero = _hero(data)
    recover = self_action(data, "recover")
    assert recover_choices(hero, data, recover) == []
    apply_condition(hero, "hit", data)
    apply_condition(hero, "staggered", data)
    apply_condition(hero, "exhausted", data)
    choices = recover_choices(hero, data, recover)
    check, fatigue = choices
    assert check.spec.recovers == "staggered"  # the worst one, not the Hit
    assert fatigue.changes == (
        ConditionChange("exhausted", remove=True),
        ConditionChange("fatigued"),
    )
    apply_condition_changes(hero, fatigue.changes, data)
    assert _has(hero, "fatigued") and not _has(hero, "exhausted")


# -- Aim's two dice -------------------------------------------------------------------


class _Dice:
    def __init__(self, *faces: int) -> None:
        self._faces = list(faces)

    def randint(self, _a: int, _b: int) -> int:
        return self._faces.pop(0)


def _server(*faces: int) -> SessionServer:
    srv = SessionServer(new_session("Table"), host="127.0.0.1", port=0, persist=False)
    srv._rng = _Dice(*faces)  # noqa: SLF001
    return srv


def test_the_server_rolls_twice_and_keeps_the_higher() -> None:
    record = _server(7, 15).roll(label="Attack", bonus=6, dc=20, twice=True)
    assert record.die == 15 and record.other_die == 7
    assert record.degree == 1
    again = RollRecord.from_dict(record.to_dict())
    assert again.other_die == 7


def test_a_lucky_aim_rerolls_both_dice() -> None:
    # Two lucky dice: a 3 lifted to 13, and a natural 16.
    record = _server(3, 16).roll(label="Attack", dc=30, twice=True, reroll=True)
    assert (record.die, record.other_die) == (16, 13) and record.reroll


def test_one_die_leaves_no_other_and_routine_rolls_none() -> None:
    assert _server(9).roll(label="Attack", dc=10).other_die == 0
    assert _server().roll(label="Attack", dc=10, routine=True, twice=True).other_die == 0


def test_twice_crosses_the_wire() -> None:
    assert RollRequest.from_payload({"twice": True}).twice
    assert RollRecord.from_dict({"die": 12, "other_die": "junk"}).other_die == 0

"""A team attack as a call to roll (combat maneuvers phase 7b): the call, an attack
stamped as its entry, and which entries combine."""

from __future__ import annotations

from dataclasses import replace

from mm_companion.core.rules import (
    KIND_TEAM_CALL,
    TEAM_NOT_SAME,
    RollSpec,
    SpecLadder,
    TeamOption,
    aimed_attack,
    is_team_call,
    team_call_spec,
    team_call_target,
    team_fits,
    team_join_spec,
)


def _attack(data, *, modifier: int = 8, rank: int = 8, kind: str = "damage", key="TOU"):
    save = RollSpec(
        label=f"Toughness vs. {15 + rank}",
        dc=15 + rank,
        kind="power-save",
        trait_key=key,
        ladder=SpecLadder(kind=kind, rungs=(), rank=rank),
    )
    return RollSpec(
        label=f"Blast: Attack {modifier:+d} vs. Defense",
        modifier=modifier,
        kind="power-check",
        follow_up=save,
        marks=(data.system.trait_keys.attack_check,),
    )


def test_the_call_names_its_target_and_carries_the_adjustment() -> None:
    call = team_call_spec("act1", "Thug", 2)
    assert call.kind == KIND_TEAM_CALL and is_team_call(call)
    assert call.label == "Team attack → Thug (+2)" and call.title == "Team attack → Thug"
    assert call.modifier == 2 and team_call_target(call) == "Thug"
    # It travels as a request record, so it must survive the wire's round trip.
    assert is_team_call(RollSpec.from_dict(call.to_dict()))
    assert team_call_spec("act1", "Thug").label == "Team attack → Thug"


def test_an_ordinary_spec_is_no_call() -> None:
    assert not is_team_call(RollSpec(label="Perception"))
    assert not is_team_call(None)
    assert not is_team_call(RollSpec(label="x", kind=KIND_TEAM_CALL))  # no action id


def test_a_joined_attack_is_stamped_as_the_calls_entry(data) -> None:
    call = team_call_spec("act1", "Thug", 2)
    entry = team_join_spec(_attack(data), call, "Ana", data)
    assert entry.action_id == "act1" and entry.title == "Team attack → Thug"
    assert entry.targets == ("Ana",)  # the attacker is the entry's row
    assert entry.modifier == 10
    assert entry.label == "Blast: Attack +10 vs. Defense (team attack +2)"
    # Aimed by its shape: no card offers its save — the GM's app resolves it once.
    assert aimed_attack(entry)
    assert entry.follow_up is not None


def test_a_call_with_no_adjustment_changes_no_number(data) -> None:
    entry = team_join_spec(_attack(data), team_call_spec("a", "Thug"), "Ana", data)
    assert entry.modifier == 8 and entry.label.endswith("vs. Defense (team attack)")


def test_an_effect_check_takes_no_attack_bonus(data) -> None:
    check = replace(_attack(data), marks=())
    entry = team_join_spec(check, team_call_spec("a", "Thug", 3), "Ana", data)
    assert entry.modifier == 8


def _option(data, **kw) -> TeamOption:
    spec = _attack(data, **kw)
    return TeamOption("k", spec.label, spec.follow_up, spec)


def test_entries_of_another_effect_or_resistance_are_left_out(data) -> None:
    options = [
        _option(data),
        _option(data, kind="affliction"),
        _option(data, key="WILL"),
        _option(data, rank=9),
    ]
    assert team_fits(options, data) == {1: TEAM_NOT_SAME, 2: TEAM_NOT_SAME}


def test_an_entry_far_below_the_strongest_is_left_out(data) -> None:
    spread = data.system.team_attack.rank_spread
    options = [_option(data, rank=4), _option(data, rank=4 + spread + 1), _option(data, rank=5)]
    out = team_fits(options, data)
    assert set(out) == {0}
    assert "below the strongest" in out[0]
    assert team_fits([], data) == {}

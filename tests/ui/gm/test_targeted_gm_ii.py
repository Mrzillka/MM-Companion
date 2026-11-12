"""Grab, Trip, Disarm, Smash and Escape on the GM's side (combat maneuvers phase 9): the
attack is judged against the target's Defense, a hit goes on to the opposed stage (the
actor's check rolled for an NPC or asked of a player, then the target's resistance), and
what lands goes on the target and the actor; Escape is judged against 10 + the grabber's
Strength and ends the hold on both."""

from __future__ import annotations

from dataclasses import replace

import pytest

from mm_companion.core.dice import CheckResult, GroupResult
from mm_companion.core.rules import (
    RollSpec,
    apply_condition,
    attack_choices,
    defense_class_in_play,
    targeted_action,
    targeted_check_spec,
    targeted_opposed_spec,
)
from mm_companion.ui import action_run
from mm_companion.ui.attack_targets import (
    TargetedDeclaration,
    targeted_roll_spec,
    targeted_table_title,
)
from mm_companion.ui.gm.model import SCENE_NPC
from mm_companion.ui.gm_window import GMWindow
from mm_companion.ui.session_bridge import set_active_session
from tests.ui.gm.test_attack_targets import _roll
from tests.ui.gm.test_combat_gm import _Menu, _npc

pytestmark = pytest.mark.usefixtures("workspace")


@pytest.fixture(autouse=True)
def _clear_active_session():
    yield
    set_active_session(None)


@pytest.fixture
def window(qapp) -> GMWindow:
    made = GMWindow(bind="127.0.0.1")
    yield made
    made.bridge.stop()


@pytest.fixture
def menu(monkeypatch):
    _Menu.picks, _Menu.shown, _Menu.greyed = [], [], []
    monkeypatch.setattr("mm_companion.ui.action_menu.QMenu", _Menu)
    return _Menu


def _staged(lead_die: int, degrees: list[int], calls: list):
    """``resolve_group`` that rolls a lead at *lead_die* and lands each line on a degree."""

    def fake(lines, *, lead_modifier=None, dc=None, critical_bonus=0, rng=None):
        calls.append((lead_modifier, list(lines)))
        results = tuple(
            CheckResult(10, line[0], 10 + line[0], (dc or 0) + line[1], degree, False)
            for line, degree in zip(lines, degrees, strict=False)
        )
        if lead_modifier is None:
            return GroupResult(None, None, results)
        return GroupResult(lead_die, lead_die + lead_modifier, results)

    return fake


def _ref(window: GMWindow, name: str) -> str:
    return window._scene_entry_for(SCENE_NPC, name).ref


def _direct(window: GMWindow, name: str) -> list[tuple[str, str | None]]:
    return [
        (c.condition_id, c.parameter)
        for c in window._npc_state[name].character.conditions
        if c.provenance is None
    ]


def _declare(window: GMWindow, action_id: str, target: str, *, option: str = ""):
    """A player called Ana declares *action_id* against the NPC *target* (her numbers
    borrowed off the target's sheet: only the arithmetic matters)."""
    data = window._data
    action = targeted_action(data, action_id)
    hero = window._npc_state[target].character
    attack = attack_choices(hero, data)[0].spec
    check = targeted_check_spec(data, action, option, hero, attack)
    opposed = targeted_opposed_spec(data, action, option, hero, attack)
    name = window._npc_state[target].summary.name
    spec = targeted_roll_spec(check, "t1", targeted_table_title(action.name, "Ana", name), "Ana")
    declaration = TargetedDeclaration(
        "t1", action_id, _ref(window, target), name, spec, "Ana", option, opposed=opposed
    )
    return window._targeted.declare("p1", declaration.to_dict()), spec


def _hits(window: GMWindow, target: str, spec: RollSpec) -> int:
    """A die that hits *target*'s Defense with *spec* — without a natural 20."""
    dc = defense_class_in_play(window._npc_state[target].character, window._data)
    return max(2, min(19, dc - spec.modifier + 1))


@pytest.fixture
def player_seat(window: GMWindow, monkeypatch):
    """The GM's bridge as if at a table: what is asked of the player and sent to them."""
    seat = {"asked": [], "applied": []}
    monkeypatch.setattr(
        window._bridge,
        "prompt_action",
        lambda pid, kind, aid, payload: (kind == "roll" and seat["asked"].append(payload)) or True,
    )
    monkeypatch.setattr(window._bridge, "prompt_roll", lambda _spec: True)
    monkeypatch.setattr(
        window._bridge,
        "apply_condition",
        lambda pid, cid, parameter=None, **_kw: seat["applied"].append((pid, cid, parameter))
        or True,
    )
    return seat


# -- a player's two-stage action -------------------------------------------------------------


def test_a_players_grab_hits_asks_for_strength_and_holds(
    window: GMWindow, player_seat, monkeypatch
) -> None:
    thug = _npc(window, "Thug", scene=True)
    calls: list = []
    monkeypatch.setattr(action_run, "resolve_group", _staged(0, [-1], calls))
    act, spec = _declare(window, "grab", thug)
    window._targeted.take_roll(_roll(spec, _hits(window, thug, spec), spec.modifier))
    assert "Thug's Defense" in act.card.status()
    window._targeted.accept("t1")
    # The hit: Ana is asked for her Strength check.
    (prompt,) = player_seat["asked"]
    ask = RollSpec.from_dict(prompt["spec"])
    assert ask.targets == ("Ana",) and "Strength" in ask.label
    assert calls == [] and not act.done
    window._targeted.take_roll(_roll(ask, 15, ask.modifier))
    # Then the Thug resists her total with Strength or Dodge, and fails by one.
    ((_lead, (line,)),) = calls
    assert line[1] == 15 + ask.modifier
    assert _direct(window, thug) == [("restrained", None)]
    assert player_seat["applied"] == [("p1", "holding", "Thug")]
    assert act.done and "Restrained" in act.card.status()


def test_a_grab_that_misses_asks_nothing(window: GMWindow, player_seat, monkeypatch) -> None:
    thug = _npc(window, "Thug", scene=True)
    calls: list = []
    monkeypatch.setattr(action_run, "resolve_group", _staged(0, [-1], calls))
    act, spec = _declare(window, "grab", thug)
    window._targeted.take_roll(_roll(spec, 2, spec.modifier))
    window._targeted.accept("t1")
    assert player_seat["asked"] == [] and calls == []
    assert act.done and "missed" in act.card.status()
    assert _direct(window, thug) == []


def test_a_smash_that_hits_is_narrated(window: GMWindow, player_seat) -> None:
    thug = _npc(window, "Thug", scene=True)
    act, spec = _declare(window, "smash", thug, option="held")
    window._targeted.take_roll(_roll(spec, _hits(window, thug, spec), spec.modifier))
    window._targeted.accept("t1")
    assert act.done and "lands on what Thug holds" in act.card.status()
    assert _direct(window, thug) == [] and player_seat["applied"] == []


def test_a_feinted_target_is_easier_to_grab_and_the_feint_is_spent(
    window: GMWindow, player_seat, monkeypatch
) -> None:
    thug = _npc(window, "Thug", scene=True)
    data = window._data
    thug_char = window._npc_state[thug].character
    full = defense_class_in_play(thug_char, data)
    apply_condition(thug_char, "feinted", data, parameter="Ana")
    monkeypatch.setattr(action_run, "resolve_group", _staged(0, [2], []))
    act, spec = _declare(window, "grab", thug)
    die = full - spec.modifier - 1  # misses the full Defense, hits the halved one
    window._targeted.take_roll(_roll(spec, die, spec.modifier))
    window._targeted.accept("t1")
    assert player_seat["asked"]  # it hit: on to the opposed check
    assert ("feinted", "Ana") not in _direct(window, thug)


# -- the GM's own ----------------------------------------------------------------------------


def _act_as(window: GMWindow, monkeypatch, name: str, die: int) -> None:
    sources, _targets = window.impress_parties()
    party = next(p for p in sources if p.name == name)
    monkeypatch.setattr(window, "_choose_actor", lambda *_a, **_k: party)

    def roll_spec(spec, **_kw):
        window._targeted.take_roll(_roll(spec, die, spec.modifier))
        return True

    monkeypatch.setattr(window._roller, "roll_spec", roll_spec)


def test_the_gms_npc_trips_another(window: GMWindow, menu, monkeypatch) -> None:
    thug = _npc(window, "Thug", scene=True)
    brute = _npc(window, "Brute", scene=True)
    calls: list = []
    monkeypatch.setattr(action_run, "resolve_group", _staged(14, [-2], calls))
    brute_char = window._npc_state[brute].character
    attack = attack_choices(window._npc_state[thug].character, window._data)[0].spec
    _act_as(window, monkeypatch, "Thug", defense_class_in_play(brute_char, window._data))
    menu.picks = [0, 0]  # the Brute; the Thug's first attack
    window._gm_combat_action("trip")
    (lead, no_lines), (no_lead, (line,)) = calls
    assert no_lines == [] and lead == max(attack.modifier, 0)  # its close attack bonus
    assert no_lead is None and line[1] == 14 + lead
    assert _direct(window, brute) == [("prone", None)]
    assert _direct(window, thug) == []


def test_the_gms_npc_grabs_and_holds(window: GMWindow, menu, monkeypatch) -> None:
    thug = _npc(window, "Thug", scene=True)
    brute = _npc(window, "Brute", scene=True)
    monkeypatch.setattr(action_run, "resolve_group", _staged(15, [-3], []))
    brute_char = window._npc_state[brute].character
    _act_as(window, monkeypatch, "Thug", defense_class_in_play(brute_char, window._data))
    menu.picks = [0, 0]
    window._gm_combat_action("grab")
    assert _direct(window, brute) == [("bound", None)]
    assert _direct(window, thug) == [("holding", "Brute")]


def test_escape_is_judged_against_the_grabbers_strength(
    window: GMWindow, menu, monkeypatch
) -> None:
    thug = _npc(window, "Thug", scene=True)
    brute = _npc(window, "Brute", scene=True)
    data = window._data
    thug_char = window._npc_state[thug].character
    brute_char = window._npc_state[brute].character
    brute_char.abilities["STR"] = 3  # the grab's DC: 13
    apply_condition(thug_char, "restrained", data)
    apply_condition(brute_char, "holding", data, parameter="Thug")
    _act_as(window, monkeypatch, "Thug", 12)  # +0: one short
    menu.picks = [0]  # the Brute
    window._gm_combat_action("escape")
    assert _direct(window, thug) == [("restrained", None)]
    _act_as(window, monkeypatch, "Thug", 14)
    menu.picks = [0]
    window._gm_combat_action("escape")
    assert _direct(window, thug) == []
    assert _direct(window, brute) == []


def test_escape_is_greyed_for_an_npc_that_is_not_held(window: GMWindow, menu) -> None:
    _npc(window, "Thug", scene=True)
    _npc(window, "Brute", scene=True)
    action = targeted_action(window._data, "escape")
    sources, _targets = window.impress_parties()
    reasons = [window._actor_reason(p, action) for p in sources if p.name in ("Thug", "Brute")]
    assert reasons and all("Restrained or Bound" in r for r in reasons)


def test_a_hosted_grab_runs_both_stages_through_the_server(qapp, window: GMWindow) -> None:
    from tests.ui.gm.test_attack_targets import _host
    from tests.ui.gm.test_team_call import _wait

    _host(qapp, window)
    thug = _npc(window, "Thug", scene=True)
    brute = _npc(window, "Brute", scene=True)
    data = window._data
    sources, targets = window.impress_parties()
    party = next(p for p in sources if p.name == "Thug")
    target = next(t for t in targets if t.ref == _ref(window, brute))
    action = targeted_action(data, "grab")
    thug_char = window._npc_state[thug].character
    attack = attack_choices(thug_char, data)[0].spec
    # A sure hit, so the second stage always runs.
    check = targeted_check_spec(data, action, "", thug_char, attack)
    check = replace(check, modifier=check.modifier + 40)
    opposed = targeted_opposed_spec(data, action, "", thug_char, attack)
    act = window._targeted.act_own(party, "grab", "", target, check, opposed=opposed)
    _wait(qapp, lambda: act.done)
    said = "; ".join(act.said)
    # The hit, the Thug's opposed check off the server, then the Brute's resistance.
    assert said.startswith("hit") and "Thug " in said and "Brute " in said, act.said

"""Aid, Feint and Trick on the GM's side (combat maneuvers phase 8): a player's
declaration waits for Accept, the target resists (rolled for an NPC, asked of a player),
what the actor's degree lands goes on the target or the ally, and the aimed-attack judge
counts and spends it (decision 17)."""

from __future__ import annotations

from dataclasses import replace

import pytest

from mm_companion.core.rules import (
    apply_condition,
    attack_choices,
    defense_class_in_play,
    targeted_action,
    targeted_check_spec,
)
from mm_companion.ui import action_run
from mm_companion.ui.action_menu import NO_ALLY_AND_OPPONENT, NO_ONE_TO_TARGET
from mm_companion.ui.attack_targets import (
    TargetedDeclaration,
    targeted_roll_spec,
    targeted_table_title,
)
from mm_companion.ui.gm.combat import NO_NPC_TO_ACT
from mm_companion.ui.gm.model import SCENE_NPC
from mm_companion.ui.gm_window import GMWindow
from mm_companion.ui.impress import PARTY_PLAYER, ImpressParty
from mm_companion.ui.session_bridge import set_active_session
from tests.ui.gm.test_attack_targets import _roll, _staged
from tests.ui.gm.test_combat_gm import _conditions, _Menu, _npc

pytestmark = pytest.mark.usefixtures("workspace")

TILES = ("feint", "trick", "aid")


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


def _ref(window: GMWindow, name: str) -> str:
    return window._scene_entry_for(SCENE_NPC, name).ref


def _opposing(window: GMWindow, name: str) -> list[tuple[str, str | None, int | None]]:
    return [
        (c.condition_id, c.parameter, c.amount)
        for c in window._npc_state[name].character.conditions
        if c.provenance is None
    ]


def _declare(
    window: GMWindow,
    action_id: str,
    target: str,
    *,
    option: str = "",
    ally: str = "",
    source: str = "Ana",
):
    """A player called *source* declares *action_id* against the NPC *target*."""
    data = window._data
    action = targeted_action(data, action_id)
    hero = window._npc_state[target].character  # any sheet: only the numbers matter
    attack = attack_choices(hero, data)[0].spec
    check = targeted_check_spec(data, action, option, hero, attack)
    target_name = window._npc_state[target].summary.name
    ally_name = window._npc_state[ally].summary.name if ally else ""
    title = targeted_table_title(action.name, source, target_name, ally_name)
    spec = targeted_roll_spec(check, "t1", title, source)
    declaration = TargetedDeclaration(
        "t1",
        action_id,
        _ref(window, target),
        target_name,
        spec,
        source,
        option,
        _ref(window, ally) if ally else "",
        ally_name,
    )
    # Through the wire's shape, as a player's arrives.
    return window._targeted.declare("p1", declaration.to_dict()), spec


# -- the popup ---------------------------------------------------------------------------


def test_the_tiles_wait_for_an_npc_and_someone_on_the_scene(window: GMWindow) -> None:
    popup = window._roller.open_actions()
    for key in TILES:
        assert NO_NPC_TO_ACT in popup.tile(key).toolTip()
    popup.close()
    thug = _npc(window, "Thug")
    popup = window._roller.open_actions()
    for key in TILES:
        tile = popup.tile(key)
        assert not tile.isEnabled() and NO_ONE_TO_TARGET in tile.toolTip()
    popup.close()
    window._set_in_scene(SCENE_NPC, thug, True)
    popup = window._roller.open_actions()
    assert all(popup.tile(key).isEnabled() for key in TILES)


def test_aid_needs_an_ally_and_an_opponent(window: GMWindow, menu, monkeypatch) -> None:
    _npc(window, "Thug", scene=True)
    notices: list[str] = []
    monkeypatch.setattr(window, "_show_notice", lambda text, _colour: notices.append(text))
    window._gm_combat_action("aid")  # the Thug is alone: nobody to aid
    assert notices == [NO_ALLY_AND_OPPONENT]


# -- a player's declaration ------------------------------------------------------------------


def test_a_players_feint_waits_for_accept_and_leaves_the_target_feinted(
    window: GMWindow, monkeypatch
) -> None:
    thug = _npc(window, "Thug", scene=True)
    calls: list = []
    monkeypatch.setattr(action_run, "resolve_group", _staged([-1], calls))
    act, spec = _declare(window, "feint", thug, option="standard")
    assert act.card is not None and act.card.second_button is not None
    window._targeted.take_roll(_roll(spec, 14, spec.modifier))
    assert calls == []  # nothing until the GM says so
    window._targeted.accept("t1")
    ((line,),) = calls  # the Thug's resistance, against the feint's total
    assert line[1] == 14 + spec.modifier
    assert _opposing(window, thug) == [("feinted", "Ana", None)]
    assert "Feinted (Ana)" in act.card.status()


def test_a_resisted_feint_does_nothing(window: GMWindow, monkeypatch) -> None:
    thug = _npc(window, "Thug", scene=True)
    monkeypatch.setattr(action_run, "resolve_group", _staged([2], []))
    _act, spec = _declare(window, "feint", thug, option="simple")
    assert (
        spec.modifier
        == attack_choices(window._npc_state[thug].character, window._data)[0].spec.modifier - 5
    )
    window._targeted.accept("t1")  # accepted first: waits for the roll
    window._targeted.take_roll(_roll(spec, 3, spec.modifier))
    assert _opposing(window, thug) == []


def test_a_trick_lost_badly_gives_the_target_the_advantage(window: GMWindow, monkeypatch) -> None:
    thug = _npc(window, "Thug", scene=True)
    monkeypatch.setattr(action_run, "resolve_group", _staged([2], []))
    _act, spec = _declare(window, "trick", thug)
    window._targeted.take_roll(_roll(spec, 2, spec.modifier))
    window._targeted.accept("t1")
    assert _opposing(window, thug) == [("trick_advantage", "Ana", 5)]


def test_aid_lands_on_the_ally_against_the_opponent(window: GMWindow) -> None:
    thug = _npc(window, "Thug", scene=True)
    bo = _npc(window, "Bo", scene=True)
    _act, spec = _declare(window, "aid", thug, option="defense", ally=bo)
    assert spec.dc == 10
    window._targeted.take_roll(_roll(spec, 20 - spec.modifier, spec.modifier))  # 20: 3 degrees
    window._targeted.accept("t1")
    assert _opposing(window, bo) == [("aided_defense", "Thug", 5)]
    assert _opposing(window, thug) == []


def test_a_failed_aid_and_a_declined_one_do_nothing(window: GMWindow) -> None:
    thug = _npc(window, "Thug", scene=True)
    bo = _npc(window, "Bo", scene=True)
    act, spec = _declare(window, "aid", thug, option="attack", ally=bo)
    window._targeted.take_roll(_roll(spec, 2, 0))
    window._targeted.accept("t1")
    assert _opposing(window, bo) == [] and "no effect" in act.card.status()
    act, spec = _declare(window, "aid", thug, option="attack", ally=bo, source="Cy")
    # Same action id: the settled one is kept, not run again.
    assert act.done


def test_a_declined_feint_does_nothing(window: GMWindow, monkeypatch) -> None:
    thug = _npc(window, "Thug", scene=True)
    calls: list = []
    monkeypatch.setattr(action_run, "resolve_group", _staged([-3], calls))
    act, spec = _declare(window, "feint", thug, option="standard")
    window._targeted.take_roll(_roll(spec, 18, spec.modifier))
    window._targeted.decline("t1")
    assert calls == [] and _opposing(window, thug) == []
    assert "Declined" in act.card.status()


def test_a_player_target_is_asked_and_told(window: GMWindow, monkeypatch) -> None:
    thug = _npc(window, "Thug", scene=True)
    player = ImpressParty("player:p2", "Bo", PARTY_PLAYER, "p2", None)
    sources, targets = window.impress_parties()
    monkeypatch.setattr(window, "impress_parties", lambda: (sources, [*targets, player]))
    asked: list = []
    told: list = []
    sent: list = []
    monkeypatch.setattr(
        window._bridge,
        "prompt_action",
        lambda pid, kind, aid, payload: (asked if kind == "roll" else told).append(payload) or True,
    )
    monkeypatch.setattr(window._bridge, "prompt_roll", lambda _spec: True)
    monkeypatch.setattr(
        window._bridge,
        "apply_condition",
        lambda pid, cid, parameter=None, **kw: sent.append((pid, cid, parameter)) or True,
    )
    action = targeted_action(window._data, "feint")
    check = targeted_check_spec(
        window._data,
        action,
        "standard",
        window._npc_state[thug].character,
        attack_choices(window._npc_state[thug].character, window._data)[0].spec,
    )
    party = next(p for p in sources if p.name == "Thug")
    rolled: list = []
    monkeypatch.setattr(
        window._roller, "roll_spec", lambda spec, **_kw: rolled.append(spec) or True
    )
    act = window._targeted.act_own(party, "feint", "standard", player, check)
    (thrown,) = rolled
    window._targeted.take_roll(_roll(thrown, 15, thrown.modifier))
    (prompt,) = asked
    assert prompt["spec"]["targets"] == ["Bo"] and prompt["spec"]["dc"] == 15 + thrown.modifier
    answer = replace(thrown, label="Resist Feint", targets=("Bo",), follow_up=None, kind="best-of")
    window._targeted.take_roll({**_roll(answer, 3, 2), "degree": -2})
    assert sent == [("p2", "feinted", "Thug")]
    (outcome,) = [p for p in told if "text" in p]  # beside the "roll closed" one
    assert "Feinted (Thug)" in outcome["text"]
    assert act.done


# -- the GM's own ------------------------------------------------------------------------------


def test_the_gms_npc_feints_off_the_roller(window: GMWindow, menu, monkeypatch) -> None:
    thug = _npc(window, "Thug", scene=True)
    brute = _npc(window, "Brute", scene=True)
    monkeypatch.setattr(action_run, "resolve_group", _staged([-1], []))
    sources, _targets = window.impress_parties()
    party = next(p for p in sources if p.name == "Thug")
    monkeypatch.setattr(window, "_choose_actor", lambda *_a, **_k: party)

    def roll_spec(spec, **_kw):
        window._targeted.take_roll(_roll(spec, 15, spec.modifier))
        return True

    monkeypatch.setattr(window._roller, "roll_spec", roll_spec)
    menu.picks = [0, 0, 0]  # the Brute; Feint (standard); the Thug's first attack
    window._gm_combat_action("feint")
    assert menu.shown[0] == ["Brute"]
    assert _opposing(window, brute) == [("feinted", "Thug", None)]
    assert _conditions(window, thug) == []


# -- the judge counts and spends them -----------------------------------------------------------


def test_an_aimed_attack_counts_and_spends_what_aid_and_feint_left(
    window: GMWindow, monkeypatch
) -> None:
    thug = _npc(window, "Thug", scene=True)
    brute = _npc(window, "Brute", scene=True)
    data = window._data
    brute_char = window._npc_state[brute].character
    thug_char = window._npc_state[thug].character
    full = defense_class_in_play(brute_char, data)
    apply_condition(brute_char, "feinted", data, parameter="Thug")
    apply_condition(thug_char, "aided_attack", data, parameter="Brute", amount=5)
    apply_condition(thug_char, "aided_attack", data, parameter="Someone else", amount=2)
    monkeypatch.setattr(action_run, "resolve_group", _staged([1], []))
    sources, targets = window.impress_parties()
    party = next(p for p in sources if p.name == "Thug")
    target = next(t for t in targets if t.ref == _ref(window, brute))
    attack = attack_choices(thug_char, data)[0].spec
    # The die alone would miss the full Defense; Vulnerable and +5 make it a hit.
    die = full - attack.modifier - 3

    def roll_spec(spec, **_kw):
        window._attack.take_roll(_roll(spec, die, spec.modifier))
        return True

    monkeypatch.setattr(window._roller, "roll_spec", roll_spec)
    aimed = window._attack.aim_own(party, attack, target.ref, target.name)
    assert aimed.done
    said = "; ".join(aimed.said)
    assert "Aided Attack (Brute) +5" in said and "Feinted (Thug)" in said
    assert "Missed" not in said
    # Spent, hit or miss — and only the instances this attack used.
    assert ("feinted", "Thug", None) not in _opposing(window, brute)
    assert _opposing(window, thug) == [("aided_attack", "Someone else", 2)]


def test_a_hosted_feint_resists_through_the_server(qapp, window: GMWindow) -> None:
    from tests.ui.gm.test_attack_targets import _host
    from tests.ui.gm.test_team_call import _wait

    _host(qapp, window)
    thug = _npc(window, "Thug", scene=True)
    brute = _npc(window, "Brute", scene=True)
    sources, targets = window.impress_parties()
    party = next(p for p in sources if p.name == "Thug")
    target = next(t for t in targets if t.ref == _ref(window, brute))
    data = window._data
    action = targeted_action(data, "feint")
    thug_char = window._npc_state[thug].character
    check = targeted_check_spec(
        data, action, "standard", thug_char, attack_choices(thug_char, data)[0].spec
    )
    act = window._targeted.act_own(party, "feint", "standard", target, check)
    _wait(qapp, lambda: act.done)
    # The check, then the Brute's resistance off the server: both settled the action.
    assert len(act.said) >= 2 and act.said[1].startswith("Brute "), act.said

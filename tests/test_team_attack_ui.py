"""Team attack from an NPC group's menu: the window, the rolls, the save, what lands."""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication, QDialog

from mm_companion.core import library, storage
from mm_companion.core.data_loader import load_game_data
from mm_companion.core.dice import CheckResult, GroupResult
from mm_companion.core.npc import quick_npc
from mm_companion.core.rules import defense_class, team_attack_options
from mm_companion.ui import action_run
from mm_companion.ui.gm_window import SCENE_NPC, GMWindow
from mm_companion.ui.npc_quick_dialog import QuickNPC, QuickNPCDialog
from mm_companion.ui.session_bridge import set_active_session


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv(storage.HOME_ENV_VAR, str(tmp_path))
    storage.ensure_workspace()
    return tmp_path


@pytest.fixture(autouse=True)
def _clear_active_session():
    yield
    set_active_session(None)


@pytest.fixture
def window(qapp: QApplication) -> GMWindow:
    made = GMWindow(bind="127.0.0.1")
    yield made
    team = getattr(made, "_team_window", None)
    if team is not None:
        team.close()
    made.bridge.stop()


def _squad(window: GMWindow, monkeypatch, count: int = 3, *, minion: bool = True) -> str:
    entered = QuickNPC(
        name="Thug",
        power_level=6,
        attack=6,
        effect=5,
        defence=4,
        toughness=3,
        fortitude=3,
        will=1,
        icon="brute",
        minion=minion,
        count=count,
    )
    monkeypatch.setattr(QuickNPCDialog, "exec", lambda self: QDialog.DialogCode.Accepted)
    monkeypatch.setattr(QuickNPCDialog, "value", lambda self: entered)
    window._quick_npc()
    return next(iter(window._group_cards))


def _target(window: GMWindow, *, minion: bool = False) -> str:
    brute = quick_npc(load_game_data(), name="Brute", attack=6, effect=6, defence=6, toughness=8)
    brute.minion = minion
    path = library.save_character(brute, directory=window._npc_dir())
    window._register_npc(path)
    window._set_in_scene(SCENE_NPC, path.name, True)
    return path.name


class _Dice:
    """Stand-in for the group roll: attack dice first, then the save's degree."""

    def __init__(self, attack_dice: list[int], save_degree: int) -> None:
        self.attack_dice = attack_dice
        self.save_degree = save_degree
        self.calls: list[list] = []

    def __call__(self, lines, *, lead_modifier=None, dc=None, critical_bonus=0, rng=None):
        self.calls.append(list(lines))
        base = dc or 0
        if len(self.calls) == 1:
            results = tuple(
                CheckResult(die, line[0], die + line[0], base + line[1], 1, die == 20)
                for line, die in zip(lines, self.attack_dice, strict=False)
            )
        else:
            results = tuple(
                CheckResult(10, line[0], 10 + line[0], base + line[1], self.save_degree, False)
                for line in lines
            )
        return GroupResult(None, None, results)


def _open(window: GMWindow, group_id: str, target: str):
    team = window._team_attack(group_id)
    team.set_target(window._scene_entry_for(SCENE_NPC, target).ref)
    return team


def test_the_group_menu_offers_a_team_attack(window: GMWindow, monkeypatch) -> None:
    group_id = _squad(window, monkeypatch)
    menu = window._group_cards[group_id].build_menu()
    assert any(a.text() == "Team attack…" for a in menu.actions())


def test_the_window_lists_the_squad_and_leaves_out_the_downed(
    window: GMWindow, monkeypatch
) -> None:
    group_id = _squad(window, monkeypatch)
    members = window._group_cards[group_id].members
    window._apply_npc_condition(members[2], "incapacitated", None)
    brute = _target(window)
    team = _open(window, group_id, brute)
    assert [c.isChecked() for c in team._checks] == [True, True, False]
    # Nobody in the squad is offered as its own target.
    assert [team._target.itemText(i) for i in range(team._target.count())] == ["Brute"]
    assert team._roll.isEnabled()


def _attack_option(window: GMWindow, group_id: str):
    data = load_game_data()
    member = window._npc_state[window._group_cards[group_id].members[0]].character
    return next(o for o in team_attack_options(member, data) if o.attack is not None)


def _exactly_one_degree(window: GMWindow, group_id: str, target: str, adjust: int = 0) -> int:
    """The die that makes an attack hit the target by exactly one degree."""
    dc = defense_class(window._npc_state[target].character, load_game_data())
    return dc - (_attack_option(window, group_id).attack.modifier + adjust)


def test_hits_combine_and_the_target_resists_the_raised_rank(window: GMWindow, monkeypatch) -> None:
    group_id = _squad(window, monkeypatch)
    brute = _target(window)
    die = _exactly_one_degree(window, group_id, brute)
    dice = _Dice([die, die, die], save_degree=-2)
    monkeypatch.setattr(action_run, "resolve_group", dice)
    team = _open(window, group_id, brute)
    team.roll()
    # One group for the three attack checks, one for the Brute's save.
    assert len(dice.calls) == 2 and len(dice.calls[0]) == 3 and len(dice.calls[1]) == 1
    dc = defense_class(window._npc_state[brute].character, load_game_data())
    assert all(line[1] == dc for line in dice.calls[0])
    # The two other hits of one degree each: +2 on the primary's rank, and its DC.
    assert dice.calls[1][0][1] == _attack_option(window, group_id).save.dc + 2
    assert window._npc_state[brute].character.conditions
    assert team._results[0].text()  # each attacker's roll is shown


def test_three_degrees_raise_it_by_five(window: GMWindow, monkeypatch) -> None:
    group_id = _squad(window, monkeypatch)
    brute = _target(window)
    die = _exactly_one_degree(window, group_id, brute)
    # The primary (fewest degrees) by one; the others by two degrees each: 4 in all.
    dice = _Dice([die, die + 5, die + 5], save_degree=1)
    monkeypatch.setattr(action_run, "resolve_group", dice)
    _open(window, group_id, brute).roll()
    assert dice.calls[1][0][1] == _attack_option(window, group_id).save.dc + 5


def test_a_team_that_misses_rolls_no_save(window: GMWindow, monkeypatch) -> None:
    group_id = _squad(window, monkeypatch)
    brute = _target(window)
    dice = _Dice([1, 2, 1], save_degree=-3)
    monkeypatch.setattr(action_run, "resolve_group", dice)
    team = _open(window, group_id, brute)
    team.roll()
    assert len(dice.calls) == 1
    assert not window._npc_state[brute].character.conditions
    assert "missed" in team._note.text()


def test_a_minion_squads_twenty_adds_no_critical(window: GMWindow, monkeypatch) -> None:
    group_id = _squad(window, monkeypatch, 2, minion=True)
    brute = _target(window)
    # A penalty that brings a 20 down to exactly the Defense: one degree, no more.
    adjust = defense_class(window._npc_state[brute].character, load_game_data()) - (
        20 + _attack_option(window, group_id).attack.modifier
    )
    die = _exactly_one_degree(window, group_id, brute, adjust)
    dice = _Dice([20, die], save_degree=1)
    monkeypatch.setattr(action_run, "resolve_group", dice)
    team = _open(window, group_id, brute)
    team._adjust.setValue(adjust)
    team.roll()
    # One other hit of one degree: +2. A non-minion's 20 would add a critical's +5.
    assert dice.calls[1][0][1] == _attack_option(window, group_id).save.dc + 2


def test_a_critical_on_a_minion_target_lands_the_worst_unrolled(
    window: GMWindow, monkeypatch
) -> None:
    group_id = _squad(window, monkeypatch, 2, minion=False)
    mook = _target(window, minion=True)
    dice = _Dice([20, 15], save_degree=1)
    monkeypatch.setattr(action_run, "resolve_group", dice)
    team = _open(window, group_id, mook)
    team.roll()
    assert len(dice.calls) == 1
    conditions = {c.condition_id for c in window._npc_state[mook].character.conditions}
    assert "incapacitated" in conditions


def test_one_attacker_is_not_a_team(window: GMWindow, monkeypatch) -> None:
    group_id = _squad(window, monkeypatch, 2)
    brute = _target(window)
    team = _open(window, group_id, brute)
    team.set_member(1, False)
    assert not team._roll.isEnabled()
    assert "two attackers" in team._note.text()


# -- review fixes (2026-10-28) -------------------------------------------------------------


def test_two_attackers_with_one_name_keep_their_own_rolls(window: GMWindow, monkeypatch) -> None:
    """Rolls are matched to attackers by place, not by name, which two creatures can share."""
    from dataclasses import replace

    group_id = _squad(window, monkeypatch, 2)
    brute = _target(window)
    option = _attack_option(window, group_id)
    members = [
        (replace(window._npc_party(name), name="Thug"), option)
        for name in window._group_cards[group_id].members
    ]
    die = _exactly_one_degree(window, group_id, brute)
    monkeypatch.setattr(action_run, "resolve_group", _Dice([die, 1], save_degree=1))
    shown: list = []
    ref = window._scene_entry_for(SCENE_NPC, brute).ref
    window._attack.team_attack(members, ref, on_rolled=lambda lines, _s: shown.extend(lines))
    assert [text.rsplit(": ", 1)[1] for _p, text in shown] == ["hit", "missed"]


def test_the_attack_lines_offer_no_save_of_their_own(window: GMWindow, monkeypatch) -> None:
    from mm_companion.core.rules import RollSpec

    group_id = _squad(window, monkeypatch)
    brute = _target(window)
    held: dict = {}
    monkeypatch.setattr(
        action_run.ActionRun,
        "roll_group",
        lambda self, lines, about, finish, **_kw: held.update(lines=lines) or True,
    )
    _open(window, group_id, brute).roll()
    assert all(RollSpec.from_dict(line["spec"]).follow_up is None for line in held["lines"])


def test_closing_the_window_mid_attack_is_harmless(window: GMWindow, monkeypatch, qapp) -> None:
    from PySide6.QtCore import QCoreApplication, QEvent

    group_id = _squad(window, monkeypatch)
    brute = _target(window)
    held: dict = {}

    def hold(self, lines, about, finish, **_kw):
        held.update(about=about, lines=lines, finish=finish)
        return True

    monkeypatch.setattr(action_run.ActionRun, "roll_group", hold)
    team = _open(window, group_id, brute)
    team.roll()
    team.close()  # deleted on close
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    records = [{"die": 2, "bonus": 0, "penalty": 0, "critical": False} for _ in held["lines"]]
    held["finish"](
        list(zip(held["about"], records, strict=True))
    )  # every attack missed: must not raise


def test_rolls_that_never_left_leave_the_window_usable(window: GMWindow, monkeypatch) -> None:
    group_id = _squad(window, monkeypatch)
    brute = _target(window)
    monkeypatch.setattr(action_run.ActionRun, "roll_group", lambda *a, **k: False)
    team = _open(window, group_id, brute)
    team.roll()
    assert team._roll.isEnabled() and "did not reach" in team._note.text()

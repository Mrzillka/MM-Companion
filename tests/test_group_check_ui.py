"""Team checks and group checks from an NPC group's menu (PDF p18)."""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication, QDialog

from mm_companion.core import storage
from mm_companion.core.data_loader import load_game_data
from mm_companion.core.dice import CheckResult, GroupResult, degrees
from mm_companion.core.rules import localize_spec, requested_roll_choices
from mm_companion.ui import action_run
from mm_companion.ui.gm_window import GMWindow
from mm_companion.ui.group_check import MODE_GROUP, MODE_TEAM
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
    check = getattr(made, "_check_window", None)
    if check is not None:
        check.close()
    made.bridge.stop()


def _squad(window: GMWindow, monkeypatch, count: int = 3) -> str:
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
        minion=False,
        count=count,
    )
    monkeypatch.setattr(QuickNPCDialog, "exec", lambda self: QDialog.DialogCode.Accepted)
    monkeypatch.setattr(QuickNPCDialog, "value", lambda self: entered)
    window._quick_npc()
    return next(iter(window._group_cards))


class _Dice:
    """Stand-in for the group roll: hands out the dice it is given, in order."""

    def __init__(self, dice: list[int]) -> None:
        self.dice = list(dice)
        self.calls: list[dict] = []

    def __call__(self, lines, *, lead_modifier=None, dc=None, critical_bonus=0, rng=None):
        self.calls.append({"lines": list(lines), "lead": lead_modifier})
        if lead_modifier is not None:
            die = self.dice.pop(0)
            return GroupResult(die, die + lead_modifier, ())
        results = []
        for line in lines:
            die = self.dice.pop(0)
            target = (dc or 0) + line[1]
            degree = degrees(die + line[0] - target)
            results.append(CheckResult(die, line[0], die + line[0], target, degree, die in (1, 20)))
        return GroupResult(None, None, tuple(results))


def _will(window: GMWindow, group_id: str) -> int:
    data = load_game_data()
    spec = next(
        v.spec
        for g in requested_roll_choices(data)
        for v in g.values
        if v.spec is not None and v.spec.trait_key == "WILL"
    )
    member = window._npc_state[window._group_cards[group_id].members[0]].character
    return localize_spec(spec, member, data).modifier


def _open(window, group_id, mode, notes: list, monkeypatch):
    monkeypatch.setattr(window, "_post_note", notes.append)
    check = window._group_check(group_id, mode)
    check.set_trait("WILL")
    return check


def test_the_group_menu_offers_both_checks(window: GMWindow, monkeypatch) -> None:
    group_id = _squad(window, monkeypatch)
    texts = [a.text() for a in window._group_cards[group_id].build_menu().actions()]
    assert "Team check…" in texts and "Group check…" in texts


def test_half_succeeding_carries_the_group(window: GMWindow, monkeypatch) -> None:
    group_id = _squad(window, monkeypatch)
    will = _will(window, group_id)
    notes: list[str] = []
    check = _open(window, group_id, MODE_GROUP, notes, monkeypatch)
    check.set_dc(15)
    # Two of three make DC 15 by under five: the group succeeds at one degree.
    dice = _Dice([15 - will, 16 - will, 2])
    monkeypatch.setattr(action_run, "resolve_group", dice)
    check.roll()
    assert len(dice.calls) == 1 and len(dice.calls[0]["lines"]) == 3
    assert "2 of 3 made DC 15" in notes[0] and "success, 1 degree" in notes[0]


def test_a_natural_twenty_can_carry_a_failing_group(window: GMWindow, monkeypatch) -> None:
    group_id = _squad(window, monkeypatch)
    will = _will(window, group_id)
    notes: list[str] = []
    check = _open(window, group_id, MODE_GROUP, notes, monkeypatch)
    check.set_dc(20 + will + 1)  # even the 20 misses on its own total
    dice = _Dice([20, 19, 18])
    monkeypatch.setattr(action_run, "resolve_group", dice)
    check.roll()
    assert "0 of 3" in notes[0]
    assert "move it +1, to success, 1 degree" in notes[0]


def test_helpers_degrees_become_the_leaders_bonus(window: GMWindow, monkeypatch) -> None:
    group_id = _squad(window, monkeypatch)
    will = _will(window, group_id)
    notes: list[str] = []
    check = _open(window, group_id, MODE_TEAM, notes, monkeypatch)
    check.set_dc(15)
    # Helper 1 makes DC 10 by 5 (two degrees), helper 2 fails: the leader gets +2.
    dice = _Dice([15 - will, 3, 12])
    monkeypatch.setattr(action_run, "resolve_group", dice)
    check.roll()
    helpers, leader = dice.calls
    assert len(helpers["lines"]) == 2 and all(line[1] == 10 for line in helpers["lines"])
    assert leader["lines"][0][0] == will + 2
    assert "helpers +2" in notes[0] and "vs DC 15" in notes[0]


def test_a_leader_with_no_dc_just_rolls(window: GMWindow, monkeypatch) -> None:
    group_id = _squad(window, monkeypatch)
    will = _will(window, group_id)
    notes: list[str] = []
    check = _open(window, group_id, MODE_TEAM, notes, monkeypatch)
    check.set_dc(None)
    dice = _Dice([10 - will, 10 - will, 14])
    monkeypatch.setattr(action_run, "resolve_group", dice)
    check.roll()
    assert dice.calls[1]["lead"] == will + 2
    assert f"rolls {14 + will + 2}" in notes[0]


def test_one_member_is_no_group(window: GMWindow, monkeypatch) -> None:
    group_id = _squad(window, monkeypatch, 2)
    notes: list[str] = []
    check = _open(window, group_id, MODE_GROUP, notes, monkeypatch)
    check.set_member(1, False)
    assert not check._roll.isEnabled()
    team = _open(window, group_id, MODE_TEAM, notes, monkeypatch)
    team.set_member(1, False)
    assert not team._roll.isEnabled() and "helper" in team._note.text()


def test_a_downed_member_starts_left_out(window: GMWindow, monkeypatch) -> None:
    group_id = _squad(window, monkeypatch)
    members = window._group_cards[group_id].members
    window._apply_npc_condition(members[0], "incapacitated", None)
    check = window._group_check(group_id, MODE_GROUP)
    assert [c.isChecked() for c in check._checks] == [False, True, True]


# -- review fixes (2026-10-28) -------------------------------------------------------------


def test_each_mark_agrees_with_the_count(window: GMWindow, monkeypatch) -> None:
    """A member's ✓ / ✗ is their own total against the DC, before the natural 20s and 1s
    the group counts afterwards — so it reads the same as "0 of 3 made"."""
    group_id = _squad(window, monkeypatch)
    will = _will(window, group_id)
    notes: list[str] = []
    check = _open(window, group_id, MODE_GROUP, notes, monkeypatch)
    check.set_dc(20 + will + 1)
    monkeypatch.setattr(action_run, "resolve_group", _Dice([20, 19, 18]))
    check.roll()
    first = check._results[0].text()
    assert "✗" in first and "nat 20" in first
    assert "0 of 3" in notes[0]


def test_closed_mid_roll_still_posts_the_outcome(window: GMWindow, monkeypatch, qapp) -> None:
    from PySide6.QtCore import QCoreApplication, QEvent

    group_id = _squad(window, monkeypatch)
    notes: list[str] = []
    check = _open(window, group_id, MODE_GROUP, notes, monkeypatch)
    check.set_dc(15)
    held: dict = {}

    def hold(self, lines, about, finish, **_kw):
        held.update(about=about, lines=lines, finish=finish)
        return True

    monkeypatch.setattr(action_run.ActionRun, "roll_group", hold)
    check.roll()
    check.close()  # deleted on close
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    records = [{"die": 12, "bonus": 5, "penalty": 0, "critical": False} for _ in held["lines"]]
    held["finish"](list(zip(held["about"], records, strict=True)))  # must not raise
    assert notes and "group check" in notes[0]


def test_rolls_that_never_left_hand_the_window_back(window: GMWindow, monkeypatch) -> None:
    group_id = _squad(window, monkeypatch)
    notes: list[str] = []
    check = _open(window, group_id, MODE_GROUP, notes, monkeypatch)
    monkeypatch.setattr(action_run.ActionRun, "roll_group", lambda *a, **k: False)
    check.roll()
    assert check._roll.isEnabled() and "did not reach" in check._note.text()
    assert notes == []

"""Joining a team attack's call to roll (combat maneuvers phase 7b), a player's side: the
call's table in the history with its Join button, and the sheet's next attack stamped as
the entry, maneuvers and all."""

from __future__ import annotations

import pytest

from mm_companion.core.character import Character
from mm_companion.core.powers import ModifierSelection, Power, PowerEffectInstance
from mm_companion.core.rules import attack_choices, team_call_spec, team_join_spec
from mm_companion.core.session.model import KIND_REQUEST
from mm_companion.ui.roll_history import JOIN_CAPTION, GroupTableCard, RollHistoryPanel
from mm_companion.ui.roller.combat_runner import TEAM_LINE
from mm_companion.ui.sections.dice import DiceSection

pytestmark = pytest.mark.usefixtures("workspace")

ALL_OUT = "all_out_attack"


class _Menu:
    """Stands in for ``action_menu``'s ``QMenu``: picks the first entry, or dismisses."""

    pick: int | None = 0
    shown: list[list[str]] = []

    def __init__(self, _parent=None) -> None:
        self.entries: list[_Entry] = []
        _Menu.shown.append([])

    def addAction(self, text: str) -> _Entry:  # noqa: N802 - mirrors Qt
        entry = _Entry(text)
        self.entries.append(entry)
        _Menu.shown[-1].append(text)
        return entry

    def exec(self, *_a):
        return None if _Menu.pick is None else self.entries[_Menu.pick]


class _Entry:
    def __init__(self, text: str) -> None:
        self.text = text
        self._data = None

    def setData(self, value) -> None:  # noqa: N802 - mirrors Qt
        self._data = value

    def data(self):
        return self._data

    def setEnabled(self, _on: bool) -> None:  # noqa: N802 - mirrors Qt
        pass


@pytest.fixture
def menu(monkeypatch):
    _Menu.pick, _Menu.shown = 0, []
    monkeypatch.setattr("mm_companion.ui.action_menu.QMenu", _Menu)
    return _Menu


def _hero(data) -> Character:
    hero = Character.new_default(data)
    hero.profile["character_name"] = "Ana"
    hero.abilities["ATK"] = 6
    hero.resistances["DEF"] = 4
    damage = PowerEffectInstance("damage", rank=8)
    damage.extras.append(ModifierSelection("ranged", config={"from": "close"}))
    hero.powers = [Power(name="Blast", effects=[damage])]
    return hero


def _rolls(panel) -> list[dict]:
    rolls: list[dict] = []
    panel.localRoll.connect(rolls.append)
    return rolls


CALL = team_call_spec("act1", "Thug", 2)


# -- the sheet's roller ---------------------------------------------------------------


def test_joining_asks_for_an_attack_and_puts_a_line_on_the_lane(qapp, data, menu) -> None:
    section = DiceSection(data, _hero(data))
    section.view.teamJoinRequested.emit(CALL)
    assert len(menu.shown) == 1  # the attack picker, nothing being loaded
    (line,) = section.panel.action_lines()
    assert line.key == TEAM_LINE and line.available
    assert section.combat.team_call == CALL


def test_dismissing_the_picker_joins_nothing(qapp, data, menu) -> None:
    section = DiceSection(data, _hero(data))
    menu.pick = None
    assert section.combat.join_team(CALL) is None
    assert section.panel.action_lines() == [] and section.combat.team_call is None


def test_the_next_attack_is_the_entry_and_spends_the_line(qapp, data, instant_die) -> None:
    hero = _hero(data)
    section = DiceSection(data, hero)
    panel = section.panel
    (choice,) = attack_choices(hero, data)
    panel.load_spec(choice.spec)
    section.combat.join_team(CALL)
    rolls = _rolls(panel)
    panel._start_roll()
    (roll,) = rolls
    spec = roll["spec"]
    assert roll["bonus"] == 8  # +6, and the GM's +2
    assert spec.action_id == "act1" and spec.title == CALL.title
    assert spec.targets == ("Ana",)
    assert spec.label.endswith("(team attack +2)")
    assert panel.action_lines() == [] and section.combat.team_call is None
    # The next attack is an ordinary one again.
    panel._start_roll()
    assert not rolls[1]["spec"].action_id


def test_a_maneuver_rides_on_the_entry_too(qapp, data, instant_die) -> None:
    hero = _hero(data)
    section = DiceSection(data, hero)
    panel = section.panel
    (choice,) = attack_choices(hero, data)
    panel.load_spec(choice.spec)
    section.combat.join_team(CALL)
    section.start_maneuver(ALL_OUT).set_value(2)
    landed: list = []
    section.conditionsRequested.connect(landed.append)
    rolls = _rolls(panel)
    panel._start_roll()
    (roll,) = rolls
    assert roll["bonus"] == 10
    assert roll["spec"].action_id == "act1"
    assert "All-Out Attack +2" in roll["spec"].label
    assert "Attack +10 vs." in roll["spec"].label
    assert landed == [(("all_out_attack", 2),)]


def test_a_reroll_of_the_entry_is_still_the_entry(qapp, data, instant_die) -> None:
    hero = _hero(data)
    section = DiceSection(data, hero)
    panel = section.panel
    (choice,) = attack_choices(hero, data)
    panel.load_spec(choice.spec)
    section.combat.join_team(CALL)
    rolls = _rolls(panel)
    panel._start_roll()
    assert panel.reroll()
    assert rolls[1]["reroll"] and rolls[1]["spec"].action_id == "act1"


def test_joining_again_replaces_the_line(qapp, data, menu) -> None:
    section = DiceSection(data, _hero(data))
    section.combat.join_team(CALL)
    other = team_call_spec("act2", "Ogre", -1)
    section.combat.join_team(other)
    (line,) = section.panel.action_lines()
    assert line.key == TEAM_LINE and section.combat.team_call == other


# -- the history ----------------------------------------------------------------------


def _request(seq: int) -> dict:
    return {
        "seq": seq,
        "kind": KIND_REQUEST,
        "player_id": "gm",
        "player_name": "GM",
        "die": 0,
        "label": CALL.label,
        "spec": CALL.to_dict(),
    }


def _entry(data, seq: int, name: str) -> dict:
    hero = _hero(data)
    (choice,) = attack_choices(hero, data)
    spec = team_join_spec(choice.spec, CALL, name, data)
    return {
        "seq": seq,
        "kind": "roll",
        "player_id": name.lower(),
        "player_name": name,
        "die": 12,
        "bonus": 8,
        "penalty": 0,
        "dc": None,
        "label": spec.label,
        "spec": spec.to_dict(),
    }


def _tables(panel: RollHistoryPanel) -> list[GroupTableCard]:
    return [c for c in panel.cards() if isinstance(c, GroupTableCard)]


def test_the_call_is_a_table_with_a_join_button(qapp, data) -> None:
    panel = RollHistoryPanel()
    joined: list = []
    panel.teamJoinRequested.connect(joined.append)
    panel.add_roll(_request(1))
    (table,) = _tables(panel)
    assert table.join_button is not None and table.join_button.text() == JOIN_CAPTION
    assert table.rows() == []
    table.join_button.click()
    assert joined == [CALL]


def test_each_joined_attack_is_a_row_of_the_calls_table(qapp, data) -> None:
    panel = RollHistoryPanel()
    panel.add_roll(_request(1))
    panel.add_roll(_entry(data, 2, "Ana"))
    panel.add_roll(_entry(data, 3, "Bo"))
    (table,) = _tables(panel)
    assert [name for name, _rolls in table.rows()] == ["Ana", "Bo"]
    assert "Team attack → Thug" in table.lines()[0]


def test_striking_the_call_takes_the_button_and_leaves_the_rows(qapp, data) -> None:
    panel = RollHistoryPanel()
    panel.add_roll(_request(1))
    panel.add_roll(_entry(data, 2, "Ana"))
    panel.remove_roll(1)
    (table,) = _tables(panel)
    assert table.join_button is None
    assert [name for name, _rolls in table.rows()] == ["Ana"]

"""Grab, Trip, Disarm, Smash, Escape and Block on a player's sheet (combat maneuvers phase
9): a two-stage action is declared with the actor's opposed check worked out on their own
sheet, Escape waits for a hold and needs no attack, and Block readies, then loads a block
check that is harder for each one already made."""

from __future__ import annotations

import pytest

from mm_companion.core.rules import apply_condition, attack_choices
from mm_companion.ui.attack_targets import TargetedDeclaration
from mm_companion.ui.character_sheet import CharacterSheet
from mm_companion.ui.sections import dice as dice_section
from tests.ui.dice.test_self_actions_ui import _hero, _Menu
from tests.ui.dice.test_targeted_ui import ME, THUG, _Bridge, _rolled, _section

pytestmark = pytest.mark.usefixtures("workspace")


@pytest.fixture
def menu(monkeypatch):
    _Menu.pick, _Menu.shown = 0, []
    monkeypatch.setattr("mm_companion.ui.action_menu.QMenu", _Menu)
    return _Menu


@pytest.fixture
def table(monkeypatch):
    def live(scene: list[dict]) -> _Bridge:
        bridge = _Bridge(scene)
        monkeypatch.setattr(dice_section, "live_session", lambda: bridge)
        return bridge

    return live


@pytest.mark.parametrize("action", ["grab", "trip", "disarm", "smash"])
def test_the_attack_actions_wait_for_a_table_and_an_attack(qapp, data, table, action) -> None:
    section = _section(data)
    table([ME, THUG])
    assert section.panel.open_actions().tile(action).isEnabled()


def test_a_grab_is_declared_with_its_opposed_check(qapp, data, table, menu, monkeypatch) -> None:
    section = _section(data)
    section._character.abilities["STR"] = 4
    bridge = table([ME, THUG])
    thrown = _rolled(section, monkeypatch)
    assert section.start_targeted("grab")  # the Thug, the Punch
    declaration = TargetedDeclaration.from_dict(bridge.sent[0][2])
    assert declaration.action == "grab" and declaration.target_name == "Thug"
    assert declaration.opposed is not None and declaration.opposed.modifier == 4
    (spec,) = thrown
    assert spec.label.endswith("vs. Defense") and spec.dc is None
    assert spec.title == "Grab: Ana → Thug" and spec.follow_up is None


def test_a_disarm_opposes_with_the_attacks_damage(qapp, data, table, menu, monkeypatch) -> None:
    section = _section(data)
    bridge = table([ME, THUG])
    thrown = _rolled(section, monkeypatch)
    assert section.start_targeted("disarm")
    declaration = TargetedDeclaration.from_dict(bridge.sent[0][2])
    assert declaration.opposed.modifier == 8  # the Punch's Damage rank
    punch = attack_choices(section._character, data)[0].spec
    assert thrown[0].modifier == declaration.spec.modifier == punch.modifier - 2


def test_escape_waits_for_a_hold_and_picks_no_attack(qapp, data, table, menu, monkeypatch) -> None:
    section = _section(data)
    bridge = table([ME, THUG])
    thrown = _rolled(section, monkeypatch)
    assert not section.panel.open_actions().tile("escape").isEnabled()
    assert not section.start_targeted("escape")
    apply_condition(section._character, "restrained", data)
    assert section.panel.open_actions().tile("escape").isEnabled()
    assert section.start_targeted("escape")
    assert _Menu.shown == [["Thug"]]  # whom only: no option, no attack picker
    declaration = TargetedDeclaration.from_dict(bridge.sent[0][2])
    assert declaration.action == "escape" and declaration.opposed is None
    assert thrown[0].dc is None  # the GM reads the DC off the grabber


def test_block_readies_then_loads_a_harder_block_each_time(qapp, data, menu) -> None:
    hero = _hero(data)
    sheet = CharacterSheet(data, hero)
    dice = sheet.dice
    dice.start_self_action("block")
    assert [c.condition_id for c in hero.conditions] == ["blocking"]
    assert _Menu.shown == []  # readying asks nothing
    dice.start_self_action("block")  # a close attack comes: block it with the Punch
    first = dice.panel.current_spec()
    assert first.dc is None and "attacker's result" in first.label
    (blocking,) = hero.conditions
    assert blocking.count == 2
    dice.start_self_action("block")
    second = dice.panel.current_spec()
    assert second.modifier == first.modifier - 2

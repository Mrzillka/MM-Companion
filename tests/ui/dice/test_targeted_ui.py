"""Aid, Feint and Trick on a player's sheet (combat maneuvers phase 8): the tiles wait
for a table and someone on its Scene; picking one asks whom (Aid: the ally, then the
opponent), which way, and which attack, then declares it to the GM and rolls the check."""

from __future__ import annotations

import pytest

from mm_companion.core.character import Character
from mm_companion.core.powers import Power, PowerEffectInstance
from mm_companion.ui.action_menu import NEEDS_A_TABLE, NO_ALLY_AND_OPPONENT, NO_ONE_TO_TARGET
from mm_companion.ui.attack_targets import KIND_TARGETED, TargetedDeclaration
from mm_companion.ui.sections import dice as dice_section
from mm_companion.ui.sections.dice import DiceSection
from tests.ui.dice.test_self_actions_ui import _Menu

pytestmark = pytest.mark.usefixtures("workspace")

TILES = ("feint", "trick", "aid")


@pytest.fixture
def menu(monkeypatch):
    _Menu.pick, _Menu.shown = 0, []
    monkeypatch.setattr("mm_companion.ui.action_menu.QMenu", _Menu)
    return _Menu


class _Bridge:
    """A live table seen from the player's seat: the Scene, and what is declared."""

    in_session = True
    is_gm = False

    def __init__(self, scene: list[dict]) -> None:
        self._scene = scene
        self.sent: list[tuple[str, str, dict]] = []

    def own_player_id(self) -> str:
        return "p1"

    def scene(self) -> list[dict]:
        return self._scene

    def send_action_request(self, kind, action_id, payload) -> bool:
        self.sent.append((kind, action_id, payload))
        return True


ME = {"ref": "r0", "name": "Ana", "player_id": "p1"}
THUG = {"ref": "r1", "name": "Thug"}
BO = {"ref": "r2", "name": "Bo", "player_id": "p2"}


def _section(data) -> DiceSection:
    hero = Character.new_default(data)
    hero.profile["hero_name"] = "Ana"
    hero.abilities["ATK"] = 6
    hero.powers = [Power(name="Punch", effects=[PowerEffectInstance("damage", rank=8)])]
    return DiceSection(data, hero)


@pytest.fixture
def table(monkeypatch):
    def live(scene: list[dict]) -> _Bridge:
        bridge = _Bridge(scene)
        monkeypatch.setattr(dice_section, "live_session", lambda: bridge)
        return bridge

    return live


def _rolled(section: DiceSection, monkeypatch) -> list:
    thrown: list = []

    def roll_spec(spec, *, before=None, **_kw):
        if before is not None and not before(spec):
            return False
        thrown.append(spec)
        return True

    monkeypatch.setattr(section.view.panel, "roll_spec", roll_spec)
    return thrown


def test_the_tiles_wait_for_a_table_and_its_scene(qapp, data, table) -> None:
    section = _section(data)
    popup = section.panel.open_actions()
    assert all(NEEDS_A_TABLE in popup.tile(k).toolTip() for k in TILES)
    popup.close()
    table([ME])
    popup = section.panel.open_actions()
    assert all(NO_ONE_TO_TARGET in popup.tile(k).toolTip() for k in ("feint", "trick"))
    assert NO_ALLY_AND_OPPONENT in popup.tile("aid").toolTip()
    popup.close()
    table([ME, THUG, BO])
    popup = section.panel.open_actions()
    assert all(popup.tile(k).isEnabled() for k in TILES)


def test_a_feint_is_declared_and_rolled(qapp, data, table, menu, monkeypatch) -> None:
    section = _section(data)
    bridge = table([ME, THUG])
    thrown = _rolled(section, monkeypatch)
    _Menu.pick = 0  # the Thug, a standard Feint, the Punch
    assert section.start_targeted("feint")
    assert _Menu.shown[0] == ["Thug"]
    ((kind, action_id, payload),) = bridge.sent
    declaration = TargetedDeclaration.from_dict(payload)
    assert kind == KIND_TARGETED and declaration.action == "feint"
    assert declaration.target_ref == "r1" and declaration.option == "standard"
    (spec,) = thrown
    assert spec.action_id == action_id and spec.targets == ("Ana",)
    assert spec.title == "Feint: Ana → Thug" and spec.follow_up is None


def test_aid_asks_for_the_ally_then_the_opponent(qapp, data, table, menu, monkeypatch) -> None:
    section = _section(data)
    bridge = table([ME, THUG, BO])
    thrown = _rolled(section, monkeypatch)
    _Menu.pick = 0
    assert section.start_targeted("aid")
    assert _Menu.shown[0] == ["Aid Thug", "Aid Bo"]
    assert _Menu.shown[1] == ["… against Bo"]  # the ally is left out
    declaration = TargetedDeclaration.from_dict(bridge.sent[0][2])
    assert (declaration.ally_name, declaration.target_name) == ("Thug", "Bo")
    assert thrown[0].dc == 10


def test_dismissing_a_menu_calls_it_off(qapp, data, table, menu, monkeypatch) -> None:
    section = _section(data)
    bridge = table([ME, THUG])
    thrown = _rolled(section, monkeypatch)
    _Menu.pick = None
    assert not section.start_targeted("trick")
    assert bridge.sent == [] and thrown == []

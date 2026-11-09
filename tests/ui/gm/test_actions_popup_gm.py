"""The GM's Actions popup on the Rolls block: Impress, Area and the group actions.

The Team attack tile is a call to roll now (``tests/ui/gm/test_team_call.py``).
"""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QApplication

from mm_companion.ui.gm.rolls import GROUP_ACTIONS, NO_NPC_GROUPS
from mm_companion.ui.gm_window import GMWindow
from mm_companion.ui.group_check import MODE_GROUP, MODE_TEAM
from mm_companion.ui.session_bridge import set_active_session
from tests.ui.gm.test_group_check_ui import _squad

pytestmark = pytest.mark.usefixtures("workspace")


@pytest.fixture(autouse=True)
def _clear_active_session():
    yield
    set_active_session(None)


@pytest.fixture
def window(qapp: QApplication) -> GMWindow:
    made = GMWindow(bind="127.0.0.1")
    yield made
    made.bridge.stop()


def test_the_gm_gets_the_group_tiles_greyed_until_there_is_a_group(window, monkeypatch) -> None:
    popup = window._roller.open_actions()
    for key in GROUP_ACTIONS:
        tile = popup.tile(key)
        assert tile is not None and not tile.isEnabled()
        assert NO_NPC_GROUPS in tile.toolTip()
    assert popup.tile("impress").isEnabled() and popup.tile("area").isEnabled()
    popup.close()

    _squad(window, monkeypatch)
    popup = window._roller.open_actions()
    assert all(popup.tile(key).isEnabled() for key in GROUP_ACTIONS)


@pytest.mark.parametrize(
    "key,expected",
    [
        ("team_check", ("check", MODE_TEAM)),
        ("group_check", ("check", MODE_GROUP)),
    ],
)
def test_a_group_tile_with_one_group_opens_its_window(window, monkeypatch, key, expected) -> None:
    group_id = _squad(window, monkeypatch)
    opened: list[tuple] = []
    monkeypatch.setattr(
        window, "_group_check", lambda gid, mode: opened.append(("check", mode, gid))
    )
    window._roller.open_actions().tile(key).click()
    assert opened == [(*expected, group_id)]


class _Menu:
    """Stands in for the group chooser's ``QMenu``: records its entries and picks the
    first. (Patching ``QMenu.exec`` on the class does not reach a PySide instance.)"""

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
        return self.entries[0]


class _Entry:
    def __init__(self, text: str) -> None:
        self.text = text
        self._data = None

    def setData(self, value) -> None:  # noqa: N802 - mirrors Qt
        self._data = value

    def data(self):
        return self._data


def test_with_several_groups_the_gm_picks_one_off_a_menu(window, monkeypatch) -> None:
    _squad(window, monkeypatch, count=2)
    _squad(window, monkeypatch, count=2)
    assert len(window._npc_groups) == 2
    _Menu.shown = []
    monkeypatch.setattr("mm_companion.ui.gm.rolls.QMenu", _Menu)
    opened: list[tuple] = []
    monkeypatch.setattr(window, "_group_check", lambda gid, mode: opened.append((gid, mode)))
    window._roller.open_actions().tile("team_check").click()
    assert len(_Menu.shown[0]) == 2 and all(text.endswith("(2)") for text in _Menu.shown[0])
    assert opened == [(window._npc_groups[0].id, MODE_TEAM)]


def test_every_tile_the_gm_sees_has_a_road(window) -> None:
    """Phase 10's audit: the GM's menu holds every built-in action, and none of them is
    greyed as "not built" (a rule may still grey one — no group, nobody on the Scene)."""
    from mm_companion.ui.action_menu import NOT_BUILT

    data = window._data
    popup = window._roller.open_actions()
    wanted = {action_id for menu in data.combat.menu for action_id in menu.items}
    assert {tile.key for tile in popup.tiles} == wanted
    assert [tile.key for tile in popup.tiles if NOT_BUILT in tile.toolTip()] == []

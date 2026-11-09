"""The GM's combat actions (combat maneuvers, phase 7): the maneuvers, the self actions
and the Attack tile taken as an NPC picked off a menu, and Surprise Attack granted to a
creature on the Scene."""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QApplication

from mm_companion.core import library
from mm_companion.core.data_loader import load_game_data
from mm_companion.core.npc import quick_npc
from mm_companion.core.rules import apply_condition, maneuver_limit
from mm_companion.ui.attack_targets import KIND_ATTACK
from mm_companion.ui.gm.combat import NO_ATTACKS_MARK, NO_NPC_TO_ACT, NO_ONE_ON_SCENE
from mm_companion.ui.gm.model import SCENE_NPC
from mm_companion.ui.gm_window import GMWindow
from mm_companion.ui.impress import PARTY_PLAYER, ImpressParty
from mm_companion.ui.session_bridge import set_active_session
from tests.ui.gm.test_group_check_ui import _squad

pytestmark = pytest.mark.usefixtures("workspace")

ALL_OUT = "all_out_attack"


@pytest.fixture(autouse=True)
def _clear_active_session():
    yield
    set_active_session(None)


@pytest.fixture
def window(qapp: QApplication) -> GMWindow:
    made = GMWindow(bind="127.0.0.1")
    yield made
    made.bridge.stop()


class _Menu:
    """Stands in for ``action_menu``'s ``QMenu``: picks :attr:`picks` in turn (``None``
    dismisses; past the end, the first entry) and remembers what each offered."""

    picks: list[int | None] = []
    shown: list[list[str]] = []
    greyed: list[list[str]] = []

    def __init__(self, _parent=None) -> None:
        self.entries: list[_Entry] = []
        _Menu.shown.append([])
        _Menu.greyed.append([])

    def addAction(self, text: str) -> _Entry:  # noqa: N802 - mirrors Qt
        entry = _Entry(text)
        self.entries.append(entry)
        _Menu.shown[-1].append(text)
        return entry

    def exec(self, *_a):
        pick = _Menu.picks.pop(0) if _Menu.picks else 0
        return None if pick is None else self.entries[pick]


class _Entry:
    def __init__(self, text: str) -> None:
        self.text = text
        self._data = None

    def setData(self, value) -> None:  # noqa: N802 - mirrors Qt
        self._data = value

    def data(self):
        return self._data

    def setEnabled(self, on: bool) -> None:  # noqa: N802 - mirrors Qt
        if not on:
            _Menu.greyed[-1].append(self.text)

    def setIcon(self, _icon) -> None:  # noqa: N802 - mirrors Qt
        pass


@pytest.fixture
def menu(monkeypatch):
    _Menu.picks, _Menu.shown, _Menu.greyed = [], [], []
    monkeypatch.setattr("mm_companion.ui.action_menu.QMenu", _Menu)
    return _Menu


def _npc(window: GMWindow, name: str, *, minion: bool = False, scene: bool = False) -> str:
    character = quick_npc(load_game_data(), name=name, attack=6, effect=6, defence=6, toughness=6)
    character.minion = minion
    path = library.save_character(character, directory=window._npc_dir())
    window._register_npc(path)
    if scene:
        window._set_in_scene(SCENE_NPC, path.name, True)
    return path.name


def _rolls(window: GMWindow) -> list[dict]:
    rolls: list[dict] = []
    window._roller.localRoll.connect(rolls.append)
    return rolls


def _conditions(window: GMWindow, name: str) -> list[tuple[str, int | None, bool]]:
    return [
        (c.condition_id, c.amount, c.temporary)
        for c in window._npc_state[name].character.conditions
        if c.provenance is None
    ]


# -- the popup --------------------------------------------------------------------------


def test_the_npc_actions_wait_for_an_npc(window: GMWindow) -> None:
    popup = window._roller.open_actions()
    for key in (ALL_OUT, "defend", "aim", KIND_ATTACK):
        tile = popup.tile(key)
        assert not tile.isEnabled() and NO_NPC_TO_ACT in tile.toolTip()
    popup.close()
    _npc(window, "Thug")
    popup = window._roller.open_actions()
    assert all(popup.tile(k).isEnabled() for k in (ALL_OUT, "defend", "aim", KIND_ATTACK))


def test_surprise_attack_waits_for_someone_on_the_scene(window: GMWindow) -> None:
    name = _npc(window, "Thug")
    tile = window._roller.open_actions().tile("surprise_attack")
    assert not tile.isEnabled() and NO_ONE_ON_SCENE in tile.toolTip()
    window._set_in_scene(SCENE_NPC, name, True)
    assert window._roller.open_actions().tile("surprise_attack").isEnabled()


# -- who acts -----------------------------------------------------------------------------


def test_defend_asks_which_npc_and_lands_on_that_one(window: GMWindow, menu) -> None:
    thug = _npc(window, "Thug")
    ogre = _npc(window, "Ogre")
    notes: list[str] = []
    window._post_note = notes.append
    menu.picks = [1]
    window._roller.open_actions().tile("defend").click()
    assert menu.shown[0] == ["Thug", "Ogre"]
    assert _conditions(window, ogre) == [("defending", 2, True)]
    assert _conditions(window, thug) == []
    assert notes == ["Ogre: Defend — Defending +2."]
    # And it is on disk, since no sheet for it is open.
    saved = library.load_character(window._npc_state[ogre].path)
    assert [c.condition_id for c in saved.conditions] == ["defending"]


def test_with_one_npc_there_is_no_chooser(window: GMWindow, menu) -> None:
    thug = _npc(window, "Thug")
    window._gm_combat_action("defend")
    assert menu.shown == []
    assert _conditions(window, thug) == [("defending", 2, True)]


def test_the_chooser_greys_an_npc_that_cannot_and_says_why(window: GMWindow, menu) -> None:
    thug = _npc(window, "Thug")
    _npc(window, "Ogre")
    apply_condition(window._npc_state[thug].character, "prone", window._data)
    menu.picks = [None]
    window._gm_combat_action("stand")
    (captions,) = menu.shown
    assert captions[0] == "Thug"
    assert captions[1].startswith("Ogre — ") and "Prone" in captions[1]
    assert menu.greyed == [[captions[1]]]


def test_an_npc_with_no_attack_is_greyed_for_a_maneuver(window: GMWindow, menu) -> None:
    _npc(window, "Thug")
    ogre = _npc(window, "Ogre")
    window._npc_state[ogre].character.powers = []
    menu.picks = [None]
    window._gm_combat_action(ALL_OUT)
    assert menu.greyed == [[f"Ogre — {NO_ATTACKS_MARK}"]]


def test_a_minion_is_named_in_the_chooser(window: GMWindow, menu) -> None:
    _npc(window, "Boss")
    _npc(window, "Mook", minion=True)
    menu.picks = [None]
    window._gm_combat_action("defend")
    assert menu.shown[0] == ["Boss", "Mook (minion)"]


def test_a_groups_members_are_listed_one_by_one(window: GMWindow, menu, monkeypatch) -> None:
    _squad(window, monkeypatch, count=2)
    group = window._npc_groups[0]
    menu.picks = [None]
    window._gm_combat_action("defend")
    names = [window._npc_state[m].summary.name for m in group.members]
    assert menu.shown[0] == [f"{n} · {group.name}" for n in names]


# -- maneuvers as an NPC ---------------------------------------------------------------------


def test_all_out_as_an_npc_rolls_its_attack_and_lands_on_it(
    window: GMWindow, menu, instant_die
) -> None:
    thug = _npc(window, "Thug")
    character = window._npc_state[thug].character
    window._gm_combat_action(ALL_OUT)  # one NPC: no chooser; then the attack picker
    (line,) = window._roller.action_lines()
    spec = window._roller.loaded_attack()
    assert spec.actor == "Thug" and spec.label.startswith("Thug: ")
    expected = maneuver_limit(window._data, ALL_OUT, spec, character, {}).maximum
    assert expected > 0 and line.value() == expected
    line.set_value(2)
    rolls = _rolls(window)
    window._roller._start_roll()
    assert rolls and rolls[-1]["label"].startswith("Thug: ")
    assert "All-Out Attack +2" in rolls[-1]["label"]
    assert window._roller.action_lines() == []
    assert _conditions(window, thug) == [("all_out_attack", 2, True)]


def test_another_npc_acting_drops_the_last_ones_lines(window: GMWindow, menu) -> None:
    _npc(window, "Thug")
    _npc(window, "Ogre")
    menu.picks = [0]  # Thug, then the first attack
    window._gm_combat_action(ALL_OUT)
    assert len(window._roller.action_lines()) == 1
    menu.picks = [1]  # Ogre defends
    window._gm_combat_action("defend")
    assert window._roller.action_lines() == []


def test_a_card_roll_for_another_npc_drops_the_lines(window: GMWindow, menu) -> None:
    _npc(window, "Thug")
    ogre = _npc(window, "Ogre")
    menu.picks = [0]
    window._gm_combat_action(ALL_OUT)
    spec = window._roller.loaded_attack()
    window._npc_roll(ogre, spec, load=True)
    assert window._roller.action_lines() == []


def test_a_new_actor_asks_for_its_own_attack(window: GMWindow, menu) -> None:
    _npc(window, "Thug")
    _npc(window, "Ogre")
    menu.picks = [0]
    window._gm_combat_action(ALL_OUT)
    assert window._roller.loaded_attack().actor == "Thug"
    menu.picks = [1]  # Ogre, then its attack
    window._gm_combat_action("power_attack")
    assert window._roller.loaded_attack().actor == "Ogre"


def test_a_minions_attack_is_loaded_as_a_minions(window: GMWindow, menu, instant_die) -> None:
    _npc(window, "Mook", minion=True)
    window._gm_combat_action("aim")
    rolls = _rolls(window)
    window._roller._start_roll()
    assert rolls and rolls[-1].get("minion") is True


# -- the Attack tile ---------------------------------------------------------------------------


def test_the_attack_tile_aims_the_npcs_shifted_attack(
    window: GMWindow, menu, monkeypatch, instant_die
) -> None:
    brute = _npc(window, "Brute", scene=True)
    _npc(window, "Thug", scene=True)
    menu.picks = [0]  # Brute, its attack
    window._gm_combat_action(ALL_OUT)
    window._roller.action_lines()[0].set_value(2)
    before = window._roller.loaded_attack().modifier
    aimed: list = []
    real = window._attack.aim_own

    def aim_own(source, spec, ref, name):
        made = real(source, spec, ref, name)
        aimed.append(made)
        return made

    monkeypatch.setattr(window._attack, "aim_own", aim_own)
    menu.picks = [0, 0, 1]  # Brute, its attack, then Thug (Skip is first)
    window._gm_combat_action(KIND_ATTACK)
    (made,) = aimed
    assert made.source.source == brute
    assert made.declaration.target_name == "Thug"
    # What is resolved is what the die was made of: All-Out's +2 is on it.
    assert made.declaration.spec.modifier == before + 2
    assert _conditions(window, brute) == [("all_out_attack", 2, True)]


def test_skipping_the_target_loads_the_attack(window: GMWindow, menu) -> None:
    _npc(window, "Brute", scene=True)
    _npc(window, "Thug", scene=True)
    menu.picks = [0, 0, 0]  # Brute, its attack, Skip
    window._gm_combat_action(KIND_ATTACK)
    assert window._roller.loaded_attack().actor == "Brute"


# -- Surprise Attack -------------------------------------------------------------------------------


@pytest.mark.parametrize("option,condition", [(0, "vulnerable"), (1, "defenseless")])
def test_surprise_attack_makes_an_npc_vulnerable_or_defenseless(
    window: GMWindow, menu, option, condition
) -> None:
    thug = _npc(window, "Thug", scene=True)
    notes: list[str] = []
    window._post_note = notes.append
    menu.picks = [0, option]
    window._gm_combat_action("surprise_attack")
    assert (condition, None, True) in _conditions(window, thug)
    assert notes and notes[0].startswith("Thug: Surprise Attack — ")


def test_surprise_attack_on_a_player_goes_over_the_session(
    window: GMWindow, menu, monkeypatch
) -> None:
    player = ImpressParty("player:p1", "Ana", PARTY_PLAYER, "p1", None)
    monkeypatch.setattr(window, "impress_parties", lambda: ([], [player]))
    sent: list = []
    monkeypatch.setattr(
        window._bridge,
        "apply_condition",
        lambda pid, cid, parameter=None, **kw: sent.append((pid, cid, kw)) or True,
    )
    window._post_note = lambda _text: None
    menu.picks = [0, 0]
    window._grant(window._data.combat.action("surprise_attack"))
    assert sent == [("p1", "vulnerable", {"amount": None, "temporary": True})]


# -- self actions that change conditions or roll a check -----------------------------------------


def test_standing_slowly_hinders_the_npc_and_takes_prone_off(window: GMWindow, menu) -> None:
    thug = _npc(window, "Thug")
    apply_condition(window._npc_state[thug].character, "prone", window._data)
    window._post_note = lambda _text: None
    menu.picks = [2]  # one NPC: no chooser; the third option, slowly
    window._gm_combat_action("stand")
    assert _conditions(window, thug) == [("hindered", None, True)]


def test_a_stand_check_is_held_so_making_it_sheds_prone(
    window: GMWindow, menu, monkeypatch
) -> None:
    thug = _npc(window, "Thug")
    apply_condition(window._npc_state[thug].character, "prone", window._data)
    menu.picks = [0]  # Acrobatics
    window._gm_combat_action("stand")
    loaded = window._roller._spec
    assert loaded is not None and loaded.recovers == "prone" and loaded.actor == "Thug"
    assert window._recovering is not None and window._recovering.source == thug
    window._on_recovery_back({"label": loaded.label, "degree": 1})
    assert _conditions(window, thug) == []


def test_an_npcs_action_reaches_its_open_sheet_as_temporary(window: GMWindow, menu) -> None:
    thug = _npc(window, "Thug")
    entry = window._npc_state[thug]
    window._open_npc(thug)
    sheet_window = window._window_for(entry.path)
    window._gm_combat_action("defend")
    (applied,) = sheet_window.sheet.character.conditions
    assert applied.condition_id == "defending" and applied.temporary and applied.amount == 2


def test_a_blocking_npc_with_no_attack_is_greyed_for_block(window) -> None:
    """The GM's chooser greys a Blocking NPC with no attack for Block, as the sheet
    greys the tile: its block check picks an attack."""
    from mm_companion.core.character import Character
    from mm_companion.core.rules import apply_condition
    from mm_companion.ui.gm.combat import NO_ATTACKS_MARK
    from mm_companion.ui.impress import PARTY_NPC, ImpressParty

    data = window._data
    plain = Character.new_default(data)
    block = data.combat.action("block")
    party = ImpressParty("npc:x", "Thug", PARTY_NPC, "thug.json", plain)
    assert window._actor_reason(party, block) == ""
    apply_condition(plain, "blocking", data)
    assert window._actor_reason(party, block) == NO_ATTACKS_MARK

"""Temporary conditions on the GM's cards (combat maneuvers, phase 2).

GM cards show the chip only (no dots): tinted by its tone, a ✕ on a temporary one, and a
"Clear temporary" on the NPC card, the player card and the Scene card, shown only while
something temporary is on the creature.
"""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QApplication

from mm_companion.core import library
from mm_companion.core.character import Character
from mm_companion.core.data_loader import load_game_data
from mm_companion.core.npc import quick_npc
from mm_companion.core.rules import apply_condition
from mm_companion.ui.gm_window import GMWindow
from mm_companion.ui.player_card import PlayerCard
from mm_companion.ui.scene_card import SceneCard
from mm_companion.ui.session_bridge import set_active_session

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


def _npc(window: GMWindow, name: str = "Thug") -> str:
    character = quick_npc(load_game_data(), name=name, attack=6, effect=6, defence=6, toughness=6)
    path = library.save_character(character, directory=window._npc_dir())
    window._register_npc(path)
    return path.name


def test_an_npc_takes_a_sized_maneuver_and_its_chip_says_so(window: GMWindow) -> None:
    name = _npc(window)
    window._apply_npc_condition(name, "all_out_attack", None, 3)

    card = window._npc_state[name].card
    assert card.condition_names() == ["All-Out Attack -3"]
    chip = card.condition_chips()[0]
    assert chip.tone == "tint.worse" and chip.dismissable
    assert card._clear_temporary.isVisibleTo(card)


def test_an_npcs_benefit_is_green_and_a_lasting_one_has_no_cross(window: GMWindow) -> None:
    name = _npc(window)
    window._apply_npc_condition(name, "defending", None)
    window._apply_npc_condition(name, "prone", None)

    chips = window._npc_state[name].card.condition_chips()
    assert [c.tone for c in chips] == ["tint.better", "tint.worse"]
    assert [c.dismissable for c in chips] == [True, False]


def test_clear_temporary_on_an_npc_keeps_the_lasting_ones(window: GMWindow) -> None:
    name = _npc(window)
    window._apply_npc_condition(name, "prone", None)
    window._apply_npc_condition(name, "all_out_attack", None, 2)

    window._clear_npc_temporary(name)

    entry = window._npc_state[name]
    assert [c.condition_id for c in entry.character.conditions] == ["prone"]
    assert not entry.card._clear_temporary.isVisibleTo(entry.card)
    # And it is on disk, since no sheet for it is open.
    saved = library.load_character(entry.path)
    assert [c.condition_id for c in saved.conditions] == ["prone"]


def test_clear_temporary_reaches_an_open_npc_sheet(window: GMWindow, qapp) -> None:
    name = _npc(window)
    window._apply_npc_condition(name, "defending", None)
    entry = window._npc_state[name]
    window._open_npc(name)
    sheet_window = window._window_for(entry.path)
    assert sheet_window is not None
    assert [c.condition_id for c in sheet_window.sheet.character.conditions] == ["defending"]

    window._clear_npc_temporary(name)

    assert sheet_window.sheet.character.conditions == []
    assert entry.character.conditions == []


def _player_card(data, conditions: list[dict], *, connected: bool = True) -> PlayerCard:
    card = PlayerCard(data)
    card.set_roster({"player_id": "p1", "display_name": "Nova", "connected": connected})
    character = Character.new_default(data)
    raw = character.to_dict()
    raw["conditions"] = conditions
    card.set_character(raw)
    return card


def test_a_player_card_offers_clear_temporary_and_asks_for_it(qapp, data) -> None:
    card = _player_card(data, [{"id": "all_out_attack", "amount": 2, "temporary": True}])
    asked: list[str] = []
    card.clearTemporaryRequested.connect(asked.append)

    assert card.condition_names() == ["All-Out Attack -2"]
    assert card._clear_temporary.isVisibleTo(card)
    card._clear_temporary.click()
    assert asked == ["p1"]


def test_an_offline_player_card_offers_no_clear(qapp, data) -> None:
    card = _player_card(data, [{"id": "defending", "temporary": True}], connected=False)
    assert not card._clear_temporary.isVisibleTo(card)


def test_a_scene_card_draws_tone_and_offers_clear_to_the_gm_only(qapp, data) -> None:
    entry = {
        "ref": "s1",
        "name": "Thug",
        "conditions": [{"id": "defending", "amount": 2, "temporary": True}],
    }
    gm_card = SceneCard(entry, data, gm=True)
    asked: list[str] = []
    gm_card.clearTemporaryRequested.connect(asked.append)

    assert gm_card.condition_names() == ["Defending +2"]
    assert gm_card._clear_temporary.isVisibleTo(gm_card)
    gm_card._clear_temporary.click()
    assert asked == ["s1"]

    player_view = SceneCard(entry, data, gm=False)
    assert not player_view._clear_temporary.isVisibleTo(player_view)


def test_the_scene_payload_carries_amount_and_temporary(window: GMWindow) -> None:
    from mm_companion.ui.gm.model import SCENE_NPC

    name = _npc(window)
    window._set_in_scene(SCENE_NPC, name, True)
    apply_condition(window._npc_state[name].character, "all_out_attack", window._data, amount=4)
    payload = window._scene_payload()
    assert payload[0]["conditions"] == [{"id": "all_out_attack", "amount": 4, "temporary": True}]


def test_the_scene_cards_clear_routes_to_the_npc(window: GMWindow) -> None:
    from mm_companion.ui.gm.model import SCENE_NPC

    name = _npc(window)
    window._set_in_scene(SCENE_NPC, name, True)
    window._apply_npc_condition(name, "defending", None)
    ref = window._scene_entry_for(SCENE_NPC, name).ref

    window._clear_scene_temporary(ref)

    assert window._npc_state[name].character.conditions == []

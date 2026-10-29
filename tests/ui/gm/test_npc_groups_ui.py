"""NPC groups on the GM's board: squads from Quick NPC, combining by drop, the frame."""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QApplication, QDialog, QInputDialog

from mm_companion.core import library, storage
from mm_companion.core.data_loader import load_game_data
from mm_companion.core.npc import quick_npc
from mm_companion.ui.gm_window import SCENE_NPC, GMWindow
from mm_companion.ui.npc_card import NPCCard
from mm_companion.ui.npc_group import NPCGroupCard, group_ref
from mm_companion.ui.npc_quick_dialog import QuickNPC, QuickNPCDialog
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


def _npc(window: GMWindow, name: str) -> str:
    character = quick_npc(load_game_data(), name=name, attack=6, effect=6, defence=6, toughness=6)
    path = library.save_character(character, directory=window._npc_dir())
    window._register_npc(path)
    return path.name


def _board(window: GMWindow) -> list:
    flow = window._npc_flow
    return [flow.itemAt(i).widget() for i in range(flow.count())]


def _squad(window: GMWindow, monkeypatch, count: int = 3, name: str = "Thug") -> None:
    entered = QuickNPC(
        name=name,
        power_level=6,
        attack=6,
        effect=5,
        defence=6,
        toughness=4,
        fortitude=4,
        will=2,
        icon="brute",
        minion=True,
        count=count,
    )
    monkeypatch.setattr(QuickNPCDialog, "exec", lambda self: QDialog.DialogCode.Accepted)
    monkeypatch.setattr(QuickNPCDialog, "value", lambda self: entered)
    window._quick_npc()


# -- Quick NPC × N -------------------------------------------------------------------


def test_quick_npc_makes_a_numbered_grouped_squad(window: GMWindow, monkeypatch) -> None:
    _squad(window, monkeypatch, 3)
    names = [e.summary.name for e in window._npc_state.values()]
    assert sorted(names) == ["Thug 1", "Thug 2", "Thug 3"]
    assert all(e.character.minion for e in window._npc_state.values())
    (group,) = _board(window)
    assert isinstance(group, NPCGroupCard)
    assert group.title_text() == "Thug ×3"
    assert [row.display_name() for row in group.rows] == ["Thug 1", "Thug 2", "Thug 3"]
    # Not put on the Scene.
    assert window._scene_payload() == []
    # Remembered per GM.
    assert storage.gm_npc_groups()[0]["members"] == group.members


def test_a_second_squad_numbers_on(window: GMWindow, monkeypatch) -> None:
    _squad(window, monkeypatch, 2)
    _squad(window, monkeypatch, 2)
    names = sorted(e.summary.name for e in window._npc_state.values())
    assert names == ["Thug 1", "Thug 2", "Thug 3", "Thug 4"]
    assert len(window._group_cards) == 2


def test_a_row_is_a_working_card(window: GMWindow, monkeypatch) -> None:
    _squad(window, monkeypatch, 2)
    (group,) = _board(window)
    row = group.rows[0]
    assert isinstance(row, NPCCard) and row.row and row.collapsed
    # The window's own entry points reach a row exactly as a loose card.
    assert window._npc_state[row.name_key].card is row
    window._apply_npc_condition(row.name_key, "incapacitated", None)
    assert group.status_text() == "1 up · 1 down"


# -- combining by drop ---------------------------------------------------------------


def test_dropping_a_card_onto_another_makes_a_group(window: GMWindow) -> None:
    a = _npc(window, "Goon")
    b = _npc(window, "Goon-2")
    target = window._npc_state[a].card
    assert window._can_merge_npcs(f"{SCENE_NPC}:{b}", target)
    window._merge_npcs(f"{SCENE_NPC}:{b}", target)
    (group,) = _board(window)
    assert isinstance(group, NPCGroupCard) and group.members == [a, b]
    assert group.title_text() == "Goon ×2"


def test_dropping_onto_a_group_joins_it_and_a_card_onto_itself_does_nothing(
    window: GMWindow, monkeypatch
) -> None:
    _squad(window, monkeypatch, 2)
    loner = _npc(window, "Brute")
    group = next(w for w in _board(window) if isinstance(w, NPCGroupCard))
    window._merge_npcs(f"{SCENE_NPC}:{loner}", group)
    (group,) = _board(window)
    assert group.members[-1] == loner
    window._merge_npcs(f"{SCENE_NPC}:{loner}", group)
    assert len(_board(window)) == 1


def test_a_row_dropped_between_cards_leaves_its_group(window: GMWindow, monkeypatch) -> None:
    _squad(window, monkeypatch, 3)
    (group,) = _board(window)
    leaving = group.members[0]
    window._drop_on_npcs(f"{SCENE_NPC}:{leaving}", 0)
    board = _board(window)
    assert isinstance(board[0], NPCCard) and board[0].name_key == leaving
    assert isinstance(board[1], NPCGroupCard) and leaving not in board[1].members


def test_a_pair_that_loses_one_dissolves(window: GMWindow, monkeypatch) -> None:
    _squad(window, monkeypatch, 2)
    (group,) = _board(window)
    window._leave_group(group.members[1])
    assert all(isinstance(w, NPCCard) and not w.row for w in _board(window))
    assert storage.gm_npc_groups() == []


def test_a_whole_group_moves_by_its_header(window: GMWindow, monkeypatch) -> None:
    first = _npc(window, "Boss")
    _squad(window, monkeypatch, 2)
    group = next(w for w in _board(window) if isinstance(w, NPCGroupCard))
    window._drop_on_npcs(group_ref(group.group_id), 0)
    board = _board(window)
    assert isinstance(board[0], NPCGroupCard)
    assert board[1].name_key == first


def test_ungroup_rename_and_scene(window: GMWindow, monkeypatch) -> None:
    _squad(window, monkeypatch, 2)
    (group,) = _board(window)
    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: ("Goons", True))
    window._rename_group(group.group_id)
    (group,) = _board(window)
    assert group.title_text() == "Goons ×2"
    window._group_to_scene(group.group_id, True)
    assert len(window._scene_payload()) == 2
    window._ungroup(group.group_id)
    assert len(_board(window)) == 2 and not window._group_cards


def test_a_group_takes_the_boards_width_and_its_rows_fit_inside(
    qapp, window: GMWindow, monkeypatch
) -> None:
    _squad(window, monkeypatch, 2)
    window.resize(1200, 900)
    window.show()
    for _ in range(6):
        qapp.processEvents()
    (group,) = _board(window)
    for row in group.rows:
        assert row.width() < group.width()
    window.hide()


def test_a_copy_of_a_squad_member_joins_the_squad(window: GMWindow, monkeypatch) -> None:
    _squad(window, monkeypatch, 2)
    (group,) = _board(window)
    window._copy_npc(group.members[0])
    (group,) = _board(window)
    assert len(group.members) == 3


def test_a_deleted_member_leaves_its_squad(window: GMWindow, monkeypatch) -> None:
    from PySide6.QtWidgets import QMessageBox

    _squad(window, monkeypatch, 3)
    (group,) = _board(window)
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    window._delete_npc(group.members[0])
    assert len(storage.gm_npc_groups()[0]["members"]) == 2

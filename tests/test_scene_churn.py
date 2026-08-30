"""What a scene push costs when nothing on the scene has changed.

The GM re-publishes the whole board whenever anything on it *could* have moved,
and one of those triggers is a player's snapshot — which arrives every time they
touch a spin box. Sent whole and redrawn whole, that made one player's stat edit a
full board re-render on every screen at the table, plus a re-encode of every
portrait on it, for a number the board never showed.

Nothing here is about correctness of the board's contents (``test_scene.py`` and
``test_scene_block.py`` own that). These are about the work *not* done: a push that
would say the same thing again says nothing, and a redraw keeps the cards it has.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication

from mm_companion.core import library, storage
from mm_companion.core.data_loader import load_game_data
from mm_companion.core.npc import quick_npc
from mm_companion.ui import gm_window
from mm_companion.ui.gm_window import SCENE_NPC, GMWindow
from mm_companion.ui.scene_board import SceneBoard
from mm_companion.ui.session_bridge import set_active_session

GOON = {"ref": "n1", "name": "Goon", "initiative": 14}
THUG = {"ref": "n2", "name": "Thug"}
BOSS = {"ref": "n3", "name": "Boss", "initiative": 20}


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
    made.bridge.stop()


def _npc_file(window: GMWindow, name: str) -> str:
    path = library.save_character(
        quick_npc(window._data, name=name, attack=4, effect=6, defence=6, toughness=6),
        directory=storage.get_workspace().gm_characters_dir,
    ).name
    window._set_npc_paths([path])
    window._refresh_npcs()
    return path


# -- the GM's side: a push that would say nothing ------------------------------


def test_a_repeated_push_does_not_redraw_the_board(window: GMWindow) -> None:
    goon = _npc_file(window, "Goon")
    window._set_in_scene(SCENE_NPC, goon, True)
    ref = window._scene[0].ref
    card = window._scene_board.card(ref)

    window._push_scene()
    window._push_scene()

    assert window._scene_board.card(ref) is card


def test_a_portrait_is_encoded_once_however_often_the_scene_is_pushed(
    window: GMWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The encode ran per entry per push; only the *send* was ever guarded.

    It is a file read (or a base64 decode), a scale and a JPEG re-encode, so a board
    of a dozen creatures paid for a dozen of them every time a player moved a slider.
    """
    goon = _npc_file(window, "Goon")
    encodes: list[str] = []
    monkeypatch.setattr(
        gm_window,
        "encode_scene_portrait",
        lambda path: encodes.append(path or "") or "",
    )

    window._set_in_scene(SCENE_NPC, goon, True)
    for _ in range(5):
        window._push_scene()

    assert len(encodes) <= 1


def test_a_new_picture_is_still_encoded(window: GMWindow, monkeypatch: pytest.MonkeyPatch) -> None:
    """The cache holds the input it was made from, so a changed picture invalidates it."""
    goon = _npc_file(window, "Goon")
    window._set_in_scene(SCENE_NPC, goon, True)
    window._push_scene()

    encodes: list[str] = []
    monkeypatch.setattr(
        gm_window,
        "encode_scene_portrait",
        lambda path: encodes.append(path or "") or "",
    )
    entry = window._scene[0]
    npc = window._npc_state[entry.source]
    npc.summary = replace(npc.summary, image_path="somewhere/else.png")
    window._push_scene()

    assert encodes == ["somewhere/else.png"]


def test_a_real_change_still_reaches_the_board(window: GMWindow) -> None:
    """The guard must not swallow the pushes that matter."""
    goon = _npc_file(window, "Goon")
    window._set_in_scene(SCENE_NPC, goon, True)
    ref = window._scene[0].ref

    window._apply_npc_condition(goon, "dazed", None)

    assert window._scene_payload()[0]["conditions"] == [{"id": "dazed"}]
    assert window._scene_board.card(ref).condition_names() == ["Dazed"]


# -- the player's side: what a board does with an update -----------------------


def test_an_identical_scene_keeps_every_card(qapp: QApplication) -> None:
    board = SceneBoard(load_game_data())
    board.set_scene([GOON, THUG])
    cards = {ref: board.card(ref) for ref in ("n1", "n2")}

    board.set_scene([GOON, THUG])

    assert {ref: board.card(ref) for ref in ("n1", "n2")} == cards


def test_changing_one_entry_keeps_every_other_card(qapp: QApplication) -> None:
    """One mook taking a condition must not rebuild the whole turn order."""
    board = SceneBoard(load_game_data())
    board.set_scene([GOON, THUG, BOSS])
    untouched = {ref: board.card(ref) for ref in ("n2", "n3")}

    board.set_scene([{**GOON, "conditions": [{"id": "dazed"}]}, THUG, BOSS])

    assert {ref: board.card(ref) for ref in ("n2", "n3")} == untouched


def test_a_changed_entry_is_restated_in_the_card_it_already_had(qapp: QApplication) -> None:
    board = SceneBoard(load_game_data())
    board.set_scene([GOON])
    card = board.card("n1")

    board.set_scene([{**GOON, "name": "Goon Captain"}])

    assert board.card("n1") is card
    assert "Goon Captain" in card._name.text()


def test_an_entry_that_leaves_takes_its_card_with_it(qapp: QApplication) -> None:
    board = SceneBoard(load_game_data())
    board.set_scene([GOON, THUG])

    board.set_scene([THUG])

    assert board.card("n1") is None
    assert board.ordered_refs() == ["n2"]


def test_reordering_keeps_the_cards_and_moves_them(qapp: QApplication) -> None:
    board = SceneBoard(load_game_data())
    board.set_scene([GOON, THUG])
    cards = {ref: board.card(ref) for ref in ("n1", "n2")}

    # Thug rolls higher than Goon, so it sorts above and the order flips.
    board.set_scene([GOON, {**THUG, "initiative": 25}])

    assert board.ordered_refs() == ["n2", "n1"]
    assert {ref: board.card(ref) for ref in ("n1", "n2")} == cards

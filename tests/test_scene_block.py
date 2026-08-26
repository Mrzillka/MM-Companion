"""The Scene block: the GM's board as a player sees it.

The board's own ordering and drop arithmetic are covered here too, since the same
:class:`~mm_companion.ui.scene_board.SceneBoard` is what the GM window shows —
this is the file that owns it. What ``test_gm_window.py`` covers is the GM's half:
which creatures end up on the board and what the eye does.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication

from mm_companion.core import storage
from mm_companion.core.character import Character
from mm_companion.core.data_loader import load_game_data
from mm_companion.core.session.model import new_session
from mm_companion.ui.character_sheet import CharacterSheet
from mm_companion.ui.scene_board import NO_SCENE_PLAYER, NOT_IN_SESSION, SceneBoard
from mm_companion.ui.scene_card import order_scene
from mm_companion.ui.sections import SceneSection
from mm_companion.ui.session_bridge import SessionBridge, set_active_session

NOVA = {"ref": "p1", "name": "Nova", "player_id": "p1", "initiative": 22}
ROOK = {"ref": "p2", "name": "Rook", "player_id": "p2"}
GOON = {"ref": "n1", "name": "Goon", "initiative": 14, "conditions": [{"id": "dazed"}]}


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


def _sheet(qapp: QApplication) -> CharacterSheet:
    return CharacterSheet(load_game_data())


def _board(gm: bool = False) -> SceneBoard:
    return SceneBoard(load_game_data(), gm=gm)


# -- the ordering ------------------------------------------------------------


def test_a_rolled_entry_sorts_above_every_unrolled_one() -> None:
    ordered = order_scene([dict(ROOK), dict(GOON), dict(NOVA)], ["p2", "n1", "p1"])

    assert [e["ref"] for e in ordered] == ["p1", "n1", "p2"]


def test_unrolled_entries_keep_the_order_they_were_given() -> None:
    """ "The order the GM dragged them into" is the whole of the un-rolled rule."""
    entries = [dict(ROOK), {"ref": "n1", "name": "Goon"}]

    assert [e["ref"] for e in order_scene(entries, ["n1", "p2"])] == ["n1", "p2"]
    assert [e["ref"] for e in order_scene(entries, ["p2", "n1"])] == ["p2", "n1"]


def test_an_entry_the_manual_order_has_not_heard_of_goes_on_the_end() -> None:
    """A creature the GM just added has no place in an arrangement made before it."""
    entries = [dict(ROOK), {"ref": "n9", "name": "New"}]

    assert [e["ref"] for e in order_scene(entries, ["p2"])] == ["p2", "n9"]


def test_an_initiative_of_zero_is_a_roll_and_not_an_absence() -> None:
    """A bad roll still has a place in the order; not having rolled does not."""
    ordered = order_scene([{"ref": "a"}, {"ref": "b", "initiative": 0}], ["a", "b"])

    assert [e["ref"] for e in ordered] == ["b", "a"]


# -- the board ---------------------------------------------------------------


def test_the_board_renders_a_card_per_entry_in_order(qapp: QApplication) -> None:
    board = _board()

    board.set_scene([dict(ROOK), dict(GOON), dict(NOVA)])

    assert board.ordered_refs() == ["p1", "n1", "p2"]
    assert board.card("n1") is not None


def test_a_card_shows_the_conditions_the_wire_carried(qapp: QApplication) -> None:
    board = _board()

    board.set_scene([dict(GOON)])

    assert board.card("n1").condition_names() == ["Dazed"]


def test_a_card_carries_nothing_the_wire_did_not(qapp: QApplication) -> None:
    """The real guarantee is upstream — a card cannot show what never left the GM's
    machine — but a defence smuggled into an entry must not find its way onto one."""
    board = _board()

    board.set_scene([{"ref": "n1", "name": "Goon", "toughness": 9, "abilities": {"STR": 4}}])

    card = board.card("n1")
    assert card is not None
    assert "9" not in card._name.text()
    assert card.condition_names() == []


def test_a_portrait_survives_a_scene_update(qapp: QApplication) -> None:
    """A scene is re-sent whenever anything on it changes and its pictures are not,
    so a rebuild that dropped them would blank every card on every condition."""
    board = _board()
    board.set_scene([dict(GOON)])
    board.set_portrait("n1", _one_pixel_jpeg())
    assert board.card("n1")._thumb.pixmap().isNull() is False

    board.set_scene([dict(GOON), dict(NOVA)])

    assert board.card("n1")._thumb.pixmap().isNull() is False


def test_a_portrait_leaves_with_its_entry(qapp: QApplication) -> None:
    board = _board()
    board.set_scene([dict(GOON)])
    board.set_portrait("n1", _one_pixel_jpeg())

    board.set_scene([dict(NOVA)])
    board.set_scene([dict(GOON)])

    assert board.card("n1")._thumb.pixmap().isNull() is True


def test_an_empty_board_says_so(qapp: QApplication) -> None:
    board = _board()
    board.set_placeholder(NO_SCENE_PLAYER)

    assert board._empty.isVisibleTo(board)
    assert board._empty.text() == NO_SCENE_PLAYER

    board.set_scene([dict(GOON)])
    assert not board._empty.isVisibleTo(board)


# -- the block on the sheet --------------------------------------------------


def test_the_sheet_builds_a_scene_block_pinned_in_the_strip(qapp: QApplication) -> None:
    """It starts in the strip for the roller's reason: a turn order that has
    scrolled away under the sheet is no use in the round it matters."""
    sheet = _sheet(qapp)

    assert isinstance(sheet.scene, SceneSection)
    assert sheet.is_block_pinned("scene")
    assert sheet.block_frame("scene").base_title == "Scene"


def test_the_block_says_there_is_no_session_when_there_is_none(qapp: QApplication) -> None:
    sheet = _sheet(qapp)

    assert sheet.scene.board._empty.text() == NOT_IN_SESSION


def test_the_block_follows_the_session_it_is_told_about(qapp: QApplication) -> None:
    """Built with the sheet, long before a session exists — so the sheet fans
    ``sync_session`` out to it when one begins."""
    sheet = _sheet(qapp)
    bridge = SessionBridge()
    bridge.host(new_session("Table"), port=0, bind="127.0.0.1")
    set_active_session(bridge)
    try:
        sheet.sync_session()
        assert sheet.scene.board._empty.text() == NO_SCENE_PLAYER

        bridge.set_scene([dict(NOVA), dict(GOON)])

        assert sheet.scene.board.ordered_refs() == ["p1", "n1"]
    finally:
        bridge.stop()


def test_a_scene_that_arrived_before_the_block_is_seeded_not_waited_for(
    qapp: QApplication,
) -> None:
    """A block built mid-fight would otherwise sit empty until the GM next touched
    the board, which could be the rest of the fight."""
    bridge = SessionBridge()
    bridge.host(new_session("Table"), port=0, bind="127.0.0.1")
    set_active_session(bridge)
    try:
        bridge.set_scene([dict(GOON)])

        section = SceneSection(load_game_data(), Character.new_default(load_game_data()))

        assert section.board.ordered_refs() == ["n1"]
    finally:
        bridge.stop()


def test_the_block_empties_when_the_session_ends(qapp: QApplication) -> None:
    sheet = _sheet(qapp)
    bridge = SessionBridge()
    bridge.host(new_session("Table"), port=0, bind="127.0.0.1")
    set_active_session(bridge)
    sheet.sync_session()
    bridge.set_scene([dict(GOON)])
    assert sheet.scene.board.ordered_refs() == ["n1"]

    bridge.stop()
    set_active_session(None)
    sheet.sync_session()

    assert sheet.scene.board.ordered_refs() == []
    assert sheet.scene.board._empty.text() == NOT_IN_SESSION


def test_a_scene_update_is_not_a_character_edit(qapp: QApplication) -> None:
    """The Dice block's bargain: what the table does must never dirty this sheet."""
    sheet = _sheet(qapp)
    seen: list[str] = []
    sheet.bus.subscribe("edited", lambda: seen.append("edited"))
    sheet.bus.subscribe("build-changed", lambda: seen.append("build-changed"))
    bridge = SessionBridge()
    bridge.host(new_session("Table"), port=0, bind="127.0.0.1")
    set_active_session(bridge)
    try:
        sheet.sync_session()
        bridge.set_scene([dict(GOON)])

        assert seen == []
    finally:
        bridge.stop()


def test_locking_the_sheet_leaves_the_scene_alone(qapp: QApplication) -> None:
    """There is nothing here to edit, so the lock has nothing to take away."""
    sheet = _sheet(qapp)
    bridge = SessionBridge()
    bridge.host(new_session("Table"), port=0, bind="127.0.0.1")
    set_active_session(bridge)
    try:
        sheet.sync_session()
        bridge.set_scene([dict(GOON)])

        sheet.set_locked(True)

        assert sheet.scene.board.ordered_refs() == ["n1"]
    finally:
        bridge.stop()


def _one_pixel_jpeg() -> str:
    """A real, tiny JPEG as base64 — ``decode_portrait`` refuses anything else."""
    import base64

    from PySide6.QtCore import QBuffer, QIODevice
    from PySide6.QtGui import QImage

    image = QImage(1, 1, QImage.Format.Format_RGB32)
    image.fill(0x336699)
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, "JPEG", 80)
    return base64.b64encode(bytes(buffer.data())).decode("ascii")

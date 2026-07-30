"""The Dice block: a roller on the sheet, in the strip, and live while locked.

The roller's own behaviour is covered by ``test_dice_roller.py``; this is about it
being a *block* — where it starts, that a roll is not a character edit, and that
locking the sheet does not take the die away.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication

from mm_companion.core import storage
from mm_companion.core.data_loader import load_game_data
from mm_companion.core.session.model import new_session
from mm_companion.ui import dice_roller
from mm_companion.ui.character_sheet import CharacterSheet
from mm_companion.ui.sections import DiceSection
from mm_companion.ui.session_bridge import SessionBridge, set_active_session


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point settings at an empty temp dir so quick rolls start empty."""
    monkeypatch.setenv(storage.HOME_ENV_VAR, str(tmp_path))
    storage.ensure_workspace()
    return tmp_path


@pytest.fixture(autouse=True)
def _clear_active_session():
    yield
    set_active_session(None)


def _sheet(qapp: QApplication) -> CharacterSheet:
    return CharacterSheet(load_game_data())


# -- the block on the sheet --------------------------------------------------


def test_the_sheet_builds_a_dice_block_pinned_in_the_strip(qapp: QApplication) -> None:
    sheet = _sheet(qapp)

    assert isinstance(sheet.dice, DiceSection)
    assert sheet.is_block_pinned("dice")
    assert sheet.block_frame("dice").base_title == "Dice Roller"


def test_the_dice_block_can_be_unpinned_onto_the_page_and_back(qapp: QApplication) -> None:
    # Nothing about it is special once it is on the sheet: it moves like any block.
    sheet = _sheet(qapp)

    sheet.unpin_block("dice")
    assert not sheet.is_block_pinned("dice")
    assert any("dice" in row for row in sheet.arrangement()["rows"])

    sheet.pin_block("dice")
    assert sheet.is_block_pinned("dice")


def test_hiding_and_reopening_the_dice_block(qapp: QApplication) -> None:
    sheet = _sheet(qapp)

    sheet.hide_block("dice")
    assert sheet.is_block_hidden("dice")

    sheet.show_block("dice")
    assert not sheet.is_block_hidden("dice")


# -- a roll is not an edit ---------------------------------------------------


def test_rolling_does_not_mark_the_sheet_edited(
    qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The roller reads nothing off the character and writes nothing to it, so a roll
    # must never put a `*` in the window title.
    monkeypatch.setattr(dice_roller, "roll_d20", lambda *a, **k: 11)
    sheet = _sheet(qapp)
    edits: list[None] = []
    sheet.edited.connect(lambda: edits.append(None))

    sheet.dice.panel._finish_roll()

    assert "11" in sheet.dice.panel._readout.text()
    assert edits == []


def test_rolling_still_works_in_the_locked_read_only_view(
    qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Rolling is a mid-play action, not a build edit — the same reasoning that keeps
    # a power's on/off switch live in a locked sheet.
    monkeypatch.setattr(dice_roller, "roll_d20", lambda *a, **k: 17)
    sheet = _sheet(qapp)
    sheet.set_locked(True)

    assert sheet.dice.panel._die_button.isEnabled()
    sheet.dice.panel._finish_roll()

    assert "17" in sheet.dice.panel._readout.text()
    assert len(sheet.dice.view._local_history.cards()) == 1


# -- the session --------------------------------------------------------------


def test_the_block_swaps_to_the_shared_history_when_told_of_a_session(
    qapp: QApplication,
) -> None:
    # The block is built with the sheet, long before a session exists, so the sheet
    # fans `sync_session` out to it when one is joined (and when one ends).
    sheet = _sheet(qapp)
    view = sheet.dice.view
    assert view._local_box.isVisibleTo(view)
    assert not view._session_box.isVisibleTo(view)

    bridge = SessionBridge()
    bridge.host(new_session("Table"), port=0, bind="127.0.0.1")
    set_active_session(bridge)
    try:
        sheet.sync_session()

        assert view._session_box.isVisibleTo(view)
        assert not view._local_box.isVisibleTo(view)
    finally:
        bridge.stop()

    set_active_session(None)
    sheet.sync_session()

    assert view._local_box.isVisibleTo(view)
    assert not view._session_box.isVisibleTo(view)

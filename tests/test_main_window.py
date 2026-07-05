"""The main window should track unsaved edits and guard them on close."""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QApplication, QMessageBox

from mm_companion.core import library, storage
from mm_companion.core.character import Character
from mm_companion.core.data_loader import load_game_data
from mm_companion.ui.main_window import MainWindow


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def _home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv(storage.HOME_ENV_VAR, str(tmp_path))
    return tmp_path


def test_new_window_starts_clean(qapp: QApplication) -> None:
    win = MainWindow(locked=False)
    assert win._dirty is False
    assert "*" not in win.windowTitle()


def test_editing_marks_dirty_and_flags_the_title(qapp: QApplication) -> None:
    win = MainWindow(locked=False)
    win._sheet.base_info._profile_fields["hero_name"].setText("Ghost")

    assert win._dirty is True
    assert win.windowTitle().startswith("MM-Companion — *")


def test_loading_a_character_does_not_start_dirty(qapp: QApplication) -> None:
    char = Character.new_default(load_game_data())
    char.profile["hero_name"] = "Seeded"
    char.conditions.add("dazed")
    path = library.save_character(char)

    win = MainWindow(character=library.load_character(path), path=path, locked=True)

    assert win._dirty is False
    assert "*" not in win.windowTitle()


def test_saving_clears_dirty(qapp: QApplication) -> None:
    win = MainWindow(locked=False)
    win._sheet.abilities._abilities["STR"].setValue(3)
    assert win._dirty is True

    win._write(storage.get_workspace().characters_dir / "hero.json")

    assert win._dirty is False
    assert "*" not in win.windowTitle()


def test_view_menu_hides_and_shows_a_block(qapp: QApplication) -> None:
    win = MainWindow(locked=False)
    action = win._block_actions["advantages"]
    assert action.isChecked()  # visible by default

    action.setChecked(False)
    assert win._sheet.is_block_hidden("advantages")
    assert "advantages" not in [k for row in win._sheet.arrangement()["rows"] for k in row]

    action.setChecked(True)
    assert not win._sheet.is_block_hidden("advantages")


def test_hiding_a_block_updates_its_view_menu_toggle(qapp: QApplication) -> None:
    win = MainWindow(locked=False)

    # Hiding elsewhere (a block's × button) keeps the View toggle in sync.
    win._sheet.hide_block("powers")

    assert not win._block_actions["powers"].isChecked()


def test_clean_window_closes_without_prompting(qapp: QApplication) -> None:
    win = MainWindow(locked=False)
    event = QCloseEvent()
    win.closeEvent(event)
    assert event.isAccepted()


def test_close_can_be_cancelled_when_dirty(
    qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    win = MainWindow(locked=False)
    win._sheet.abilities._abilities["STR"].setValue(3)
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Cancel)

    event = QCloseEvent()
    win.closeEvent(event)

    assert not event.isAccepted()  # the window stays open


def test_close_can_discard_unsaved_changes(
    qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    win = MainWindow(locked=False)
    win._sheet.abilities._abilities["STR"].setValue(3)
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Discard)

    event = QCloseEvent()
    win.closeEvent(event)

    assert event.isAccepted()


def test_close_save_persists_then_accepts(
    qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    win = MainWindow(locked=False)
    # Give it a path so the Save branch doesn't open a dialog.
    win._path = storage.get_workspace().characters_dir / "onclose.json"
    win._sheet.abilities._abilities["STR"].setValue(4)
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Save)

    event = QCloseEvent()
    win.closeEvent(event)

    assert event.isAccepted()
    assert win._path.is_file()
    assert library.load_character(win._path).abilities["STR"] == 4

"""The main window should track unsaved edits and guard them on close."""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QApplication, QMessageBox

from mm_companion.core import library, storage
from mm_companion.core.character import AppliedCondition, Character
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
    char.conditions.append(AppliedCondition("dazed"))
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


def _menu_labels(win: MainWindow, title: str) -> list[str]:
    """The entries of one menu bar menu.

    Read inside this scope rather than handing the ``QMenu`` back: PySide ties the
    returned wrapper's lifetime to the ``QAction`` it came from, so a menu that
    outlives the loop reports its C++ object as already deleted.
    """
    for action in win.menuBar().actions():
        if action.text() == title:
            return [entry.text() for entry in action.menu().actions()]
    raise AssertionError(f"no {title} menu")


def _trigger(win: MainWindow, title: str, entry_text: str) -> None:
    for action in win.menuBar().actions():
        if action.text() == title:
            for entry in action.menu().actions():
                if entry.text() == entry_text:
                    entry.trigger()
                    return
    raise AssertionError(f"no {entry_text!r} in {title}")


def test_settings_menu_opens_the_settings_window(qapp: QApplication) -> None:
    """The Theme submenu is gone; the whole look lives in the Settings window now."""
    win = MainWindow(locked=False)
    assert "Theme" not in _menu_labels(win, "&Settings")

    _trigger(win, "&Settings", "Preferences...")

    assert win._settings_window is not None
    win._settings_window.close()


def test_the_lock_toggle_sits_on_the_bar_and_not_in_a_menu(qapp: QApplication) -> None:
    """It is a play-time view switch, reached constantly — one click, not three.

    An action added straight to a ``QMenuBar`` with no submenu behaves as a
    button, so the bar's last entry *is* the toggle.
    """
    win = MainWindow(locked=True)

    assert "Lock" not in _menu_labels(win, "&Settings")
    bar_entry = win.menuBar().actions()[-1]
    assert bar_entry is win._lock_action
    assert bar_entry.menu() is None
    assert bar_entry.isCheckable()


def test_the_lock_glyph_reads_out_the_current_state(qapp: QApplication) -> None:
    """The glyph is the state read-out — a tick two clicks deep was not."""
    from mm_companion.ui.main_window import LOCK_GLYPH_LOCKED, LOCK_GLYPH_UNLOCKED

    win = MainWindow(locked=True)
    assert win._lock_action.text() == LOCK_GLYPH_LOCKED
    assert win._sheet.locked is True

    win._lock_action.trigger()

    assert win._lock_action.text() == LOCK_GLYPH_UNLOCKED
    assert win._sheet.locked is False

    win._lock_action.trigger()

    assert win._lock_action.text() == LOCK_GLYPH_LOCKED
    assert win._sheet.locked is True


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


def test_restored_hidden_block_leaves_the_view_toggle_unchecked(qapp: QApplication) -> None:
    """The View menu is built from the default arrangement, then the saved layout is
    restored over it — so the restore has to announce what it hid, or the menu keeps
    describing the arrangement it replaced and the first click on it does nothing."""
    first = MainWindow(locked=False)
    first._sheet.hide_block("powers")
    first._persist_layout()

    second = MainWindow(locked=False)
    assert second._sheet.is_block_hidden("powers")
    action = second._block_actions["powers"]
    assert action.isChecked() is False

    # One click brings it back (previously the first click was swallowed).
    action.trigger()
    assert second._sheet.is_block_hidden("powers") is False
    assert action.isChecked() is True


def test_view_menu_labels_are_plain_block_names(qapp: QApplication) -> None:
    """The entries must not snapshot a section's live priced caption, which they never
    re-read and which would sit at its build-time subtotal forever."""
    win = MainWindow(locked=False)
    win._sheet.abilities._abilities["STR"].setValue(6)

    label = win._block_actions["abilities"].text()
    assert "PP" not in label
    assert "Abilities" in label
    # The frame's own title does carry the running cost.
    assert "PP" in win._sheet.block_frame("abilities").title


def test_renaming_after_the_first_edit_updates_the_title(qapp: QApplication) -> None:
    win = MainWindow(locked=False)
    fields = win._sheet.base_info._profile_fields
    fields["hero_name"].setText("Superman")
    assert win.windowTitle() == "MM-Companion — *Superman"

    fields["hero_name"].setText("Batman")
    assert win.windowTitle() == "MM-Companion — *Batman"

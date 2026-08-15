"""The main window should track unsaved edits and guard them on close."""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtGui import QCloseEvent, QKeySequence, QPixmap
from PySide6.QtWidgets import QApplication, QMessageBox

from mm_companion.core import library, storage
from mm_companion.core.character import AppliedCondition, Character
from mm_companion.core.data_loader import load_game_data
from mm_companion.ui.main_window import MainWindow
from mm_companion.ui.npc_window import NPCWindow


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


def test_the_undo_buttons_sit_on_the_bar_just_before_the_lock(qapp: QApplication) -> None:
    """Same bargain the lock strikes — reached constantly, so one click, not three.

    Order matters: the lock stays the bar's last entry (see the test below), so the
    two glyphs go immediately before it.
    """
    win = MainWindow(locked=False)

    entries = win.menuBar().actions()
    assert entries[-3:-1] == [win._undo_action, win._redo_action]
    assert win._undo_action.menu() is None
    assert win._redo_action.menu() is None


def test_the_undo_buttons_carry_the_expected_shortcuts(qapp: QApplication) -> None:
    """Ctrl+Z back; both spellings of redo, since either is somebody's muscle memory."""
    win = MainWindow(locked=False)

    assert win._undo_action.shortcut() == QKeySequence("Ctrl+Z")
    assert set(win._redo_action.shortcuts()) == {
        QKeySequence("Ctrl+Shift+Z"),
        QKeySequence("Ctrl+Y"),
    }


def test_the_shortcuts_also_belong_to_the_window(qapp: QApplication) -> None:
    """Compact mode hides the menu bar, and a hidden widget's shortcuts go with it."""
    win = MainWindow(locked=False)

    assert win._undo_action in win.actions()
    assert win._redo_action in win.actions()


def test_the_undo_buttons_start_disabled_and_light_on_the_first_edit(
    qapp: QApplication,
) -> None:
    win = MainWindow(locked=False)
    assert not win._undo_action.isEnabled()
    assert not win._redo_action.isEnabled()

    win._sheet.abilities._abilities["STR"].setValue(3)

    assert win._undo_action.isEnabled()
    win._undo_action.trigger()
    assert win._sheet.character.abilities["STR"] == 0
    assert win._redo_action.isEnabled()

    win._dirty = False  # a "save your changes?" modal would hang the teardown


def test_a_gm_view_window_has_no_undo_at_all(qapp: QApplication) -> None:
    """Nothing there can be edited, and the GM opens one per click on a card."""
    win = MainWindow(gm_view=True)

    assert win._undo is None
    assert win.sheet.undo is None
    assert not hasattr(win, "_undo_action")


def test_an_npc_window_gets_undo_too(qapp: QApplication) -> None:
    """NPCs open unlocked and are the sheets edited most destructively mid-session."""
    win = NPCWindow()

    assert win._undo is not None
    assert win.menuBar().actions()[-3:-1] == [win._undo_action, win._redo_action]

    win._dirty = False


def test_undoing_back_to_the_saved_state_clears_the_title_marker(
    qapp: QApplication, tmp_path: Path
) -> None:
    """The ``*`` is a claim about the file, so walking back to it has to retract it."""
    win = MainWindow(locked=False)
    assert win._write(tmp_path / "hero.json")
    assert "*" not in win.windowTitle()

    win._sheet.abilities._abilities["STR"].setValue(3)
    assert "*" in win.windowTitle()

    win._undo.undo()

    assert win._dirty is False
    assert "*" not in win.windowTitle()


def test_stepping_off_the_saved_state_puts_the_marker_back(
    qapp: QApplication, tmp_path: Path
) -> None:
    """The twin of the test above, and the direction that loses work.

    A restore runs under ``_applying``, which suppresses ``edited`` — so nothing
    sets the flag when an undo walks *away* from what is on disk. Left one-way,
    save-then-undo closed without prompting and the change was gone.
    """
    win = MainWindow(locked=False)
    assert win._write(tmp_path / "hero.json")

    win._sheet.abilities._abilities["STR"].setValue(3)
    assert win._write(tmp_path / "hero.json")  # STR 3 is now the saved state
    assert "*" not in win.windowTitle()

    win._undo.undo()  # back to STR 0, which is *not* what the file holds

    assert win._dirty is True
    assert "*" in win.windowTitle()


def test_redoing_away_from_the_saved_state_puts_the_marker_back(
    qapp: QApplication, tmp_path: Path
) -> None:
    """The same hole reached the other way round: undo cleared it, redo left it."""
    win = MainWindow(locked=False)
    assert win._write(tmp_path / "hero.json")

    win._sheet.abilities._abilities["STR"].setValue(3)
    win._undo.undo()
    assert win._dirty is False

    win._undo.redo()

    assert win._dirty is True
    assert "*" in win.windowTitle()


def test_a_never_saved_sheet_is_not_declared_dirty_by_the_history(
    qapp: QApplication,
) -> None:
    """The guard on the two above: there is nothing to be clean *against* yet.

    ``at_saved_state()`` is False for a sheet with no file, so re-deriving from it
    alone would star a brand-new window before the user had touched it.
    """
    win = MainWindow(locked=False)

    win._on_undo_state()

    assert win._dirty is False
    assert "*" not in win.windowTitle()


def test_a_save_is_not_an_undo_step(qapp: QApplication, tmp_path: Path) -> None:
    """Saving rewrites an external image path — the app tidying up, not a user edit."""
    win = MainWindow(locked=False)
    source = tmp_path / "face.png"
    QPixmap(4, 4).save(str(source))
    win._sheet.character.image_path = str(source)
    win._sheet.edited.emit()

    assert win._write(tmp_path / "hero.json")
    depth = len(win._undo._undo)
    win._undo.flush()

    assert len(win._undo._undo) == depth
    assert win._dirty is False


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

"""The start window should list saved characters and lay out its buttons."""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication, QLabel, QPushButton

from mm_companion.core import storage
from mm_companion.core.library import CharacterSummary, list_saved_characters
from mm_companion.ui.main_window import MainWindow
from mm_companion.ui.start_window import CharacterCard, StartWindow


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the workspace at an empty temp dir so the library starts empty."""
    monkeypatch.setenv(storage.HOME_ENV_VAR, str(tmp_path))
    return tmp_path


def test_list_saved_characters_is_empty_when_none_saved() -> None:
    assert list_saved_characters() == []


def test_start_window_shows_the_four_actions(qapp: QApplication) -> None:
    window = StartWindow()
    labels = {b.text() for b in window.findChildren(QPushButton)}
    assert labels == {"Create New Character", "Open Existing", "Open GM Mode", "Exit"}


def test_empty_store_shows_the_empty_state(qapp: QApplication) -> None:
    window = StartWindow()
    assert window._library.widget() is window._empty_label
    assert window._cards_flow.count() == 0


def test_create_new_character_opens_an_unlocked_sheet(qapp: QApplication) -> None:
    window = StartWindow()
    window._create_new_character()

    assert len(window._child_windows) == 1
    sheet_window = window._child_windows[0]
    assert isinstance(sheet_window, MainWindow)
    assert sheet_window._lock_action.isChecked() is False


def test_launcher_hides_while_the_sheet_is_open_and_returns_on_close(
    qapp: QApplication,
) -> None:
    window = StartWindow()
    window.show()
    assert not window.isHidden()

    window._create_new_character()
    assert window.isHidden()  # hidden behind the sheet

    window._child_windows[0].close()
    assert not window.isHidden()  # the launcher is back
    assert window._child_windows == []


def test_character_card_renders_name_and_power_level(qapp: QApplication) -> None:
    card = CharacterCard(CharacterSummary(name="Ronin", power_level=8))
    texts = {label.text() for label in card.findChildren(QLabel)}
    assert "Ronin" in texts
    assert "PL 8" in texts

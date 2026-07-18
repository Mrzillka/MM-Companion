"""The dice roller presents results, records history, and persists quick rolls."""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication, QPushButton

from mm_companion.core import storage
from mm_companion.core.dice import resolve_check
from mm_companion.ui import dice_roller
from mm_companion.ui.dice_roller import DiceRollerWindow, RollCard, degree_text


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point settings at an empty temp dir so quick rolls start empty."""
    monkeypatch.setenv(storage.HOME_ENV_VAR, str(tmp_path))
    return tmp_path


# -- degree_text (pure) ------------------------------------------------------


def test_degree_text_reports_a_single_success() -> None:
    result = resolve_check(5, 15, roll=10)  # total 15 at the DC → +1 degree
    assert degree_text(result) == "Success"


def test_degree_text_counts_multiple_degrees() -> None:
    result = resolve_check(5, 10, roll=10)  # total 15 vs DC 10 → +2 degrees
    assert degree_text(result) == "Success (2 degrees)"


def test_degree_text_reports_failure() -> None:
    result = resolve_check(0, 15, roll=10)  # total 10 vs DC 15 → -1 degree
    assert degree_text(result) == "Failure"


def test_degree_text_notes_a_natural_twenty() -> None:
    result = resolve_check(5, 15, roll=20)  # nat 20 adds a degree
    assert degree_text(result) == "Success (4 degrees) — Nat 20!"


def test_degree_text_notes_a_natural_one() -> None:
    result = resolve_check(20, 20, roll=1)  # nat 1 drags a hit to a miss
    assert degree_text(result) == "Failure — Nat 1!"


def test_degree_text_is_empty_without_a_dc() -> None:
    assert degree_text(None) == ""


# -- rolling (GUI) -----------------------------------------------------------


def test_roll_without_dc_shows_total_and_records_history(
    qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(dice_roller, "roll_d20", lambda *a, **k: 12)
    window = DiceRollerWindow()
    window._bonus_spin.setValue(4)
    window._penalty_spin.setValue(1)  # net modifier +3
    window._dc_check.setChecked(False)

    window._finish_roll()

    cards = window._history_container.findChildren(RollCard)
    assert len(cards) == 1
    assert cards[0]._params == {"bonus": 4, "penalty": 1, "dc": None}
    text = window._readout.text()
    assert "15" in text  # die 12 + net modifier 3
    assert "Success" not in text and "Failure" not in text  # no DC → no degree


def test_roll_with_dc_shows_degree_of_success(
    qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(dice_roller, "roll_d20", lambda *a, **k: 20)
    window = DiceRollerWindow()
    window._bonus_spin.setValue(5)
    window._penalty_spin.setValue(0)
    window._dc_check.setChecked(True)
    window._dc_spin.setValue(15)

    window._finish_roll()

    assert "Success" in window._readout.text()
    assert "Nat 20" in window._readout.text()


def test_saving_a_roll_adds_a_persisted_quick_roll(qapp: QApplication) -> None:
    window = DiceRollerWindow()

    window._add_quick_roll({"bonus": 4, "penalty": 1, "dc": None})

    assert window._quick_flow.count() == 1
    assert storage.load_settings()["quick_rolls"] == [{"bonus": 4, "penalty": 1, "dc": None}]

    # De-duplicated: the same params don't stack a second chip.
    window._add_quick_roll({"bonus": 4, "penalty": 1, "dc": None})
    assert window._quick_flow.count() == 1


def test_quick_rolls_persist_across_windows(qapp: QApplication) -> None:
    first = DiceRollerWindow()
    first._add_quick_roll({"bonus": 2, "penalty": 0, "dc": 15})

    second = DiceRollerWindow()
    assert second._quick_flow.count() == 1
    assert second._quick_rolls == [{"bonus": 2, "penalty": 0, "dc": 15}]


def test_removing_a_quick_roll_persists(qapp: QApplication) -> None:
    window = DiceRollerWindow()
    entry = {"bonus": 3, "penalty": 0, "dc": None}
    window._add_quick_roll(entry)

    window._remove_quick_roll(entry)

    assert window._quick_flow.count() == 0
    assert storage.load_settings()["quick_rolls"] == []


def test_named_quick_roll_shows_its_name(qapp: QApplication) -> None:
    window = DiceRollerWindow()

    window._add_quick_roll({"bonus": 1, "penalty": 0, "dc": None}, name="Perception")

    assert window._quick_rolls == [{"bonus": 1, "penalty": 0, "dc": None, "name": "Perception"}]
    labels = {b.text() for b in window._quick_container.findChildren(QPushButton)}
    assert "Perception" in labels


def test_reordering_moves_and_persists(qapp: QApplication) -> None:
    window = DiceRollerWindow()
    first = {"bonus": 1, "penalty": 0, "dc": None}
    second = {"bonus": 2, "penalty": 0, "dc": None}
    window._add_quick_roll(first)
    window._add_quick_roll(second)

    # Drop the second chip (index 1) before the first (insertion index 0).
    window._reorder_quick_roll(1, 0)

    assert window._quick_rolls == [second, first]
    assert storage.load_settings()["quick_rolls"] == [second, first]


def test_removing_a_history_card(qapp: QApplication, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(dice_roller, "roll_d20", lambda *a, **k: 7)
    window = DiceRollerWindow()
    window._finish_roll()
    card = window._history_container.findChildren(RollCard)[0]

    card.removeRequested.emit()
    qapp.processEvents()

    assert window._history_container.findChildren(RollCard) == []

"""The dice roller presents results, records history, and persists quick rolls.

Two modes, and the difference is the point. On its own the panel rolls locally
and its cards land in the window's own history. In a session it rolls *nothing*
— it asks the server and waits for the broadcast — so the tests below drive a
real loopback session and check that the number on screen is the server's, that
the shared history replaces the private one, and that a session which never
answers releases the inputs instead of locking the die forever.

The tumble is zeroed (``ROLL_DURATION_MS``) wherever a test would otherwise wait
1.4 s for an animation.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication, QPushButton

from mm_companion.core import storage
from mm_companion.core.dice import resolve_check
from mm_companion.core.session.model import new_session
from mm_companion.ui import dice_roller
from mm_companion.ui.dice_roller import DiceRollerWindow, RollCard, degree_text
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


@pytest.fixture
def hosting(qapp: QApplication) -> SessionBridge:
    """A real session on loopback, published as the process-wide one."""
    bridge = SessionBridge()
    bridge.host(new_session("Table"), port=0, bind="127.0.0.1")
    set_active_session(bridge)
    yield bridge
    bridge.stop()


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
    window.panel._bonus_spin.setValue(4)
    window.panel._penalty_spin.setValue(1)  # net modifier +3
    window.panel._dc_check.setChecked(False)

    window.panel._finish_roll()

    cards = window._history_container.findChildren(RollCard)
    assert len(cards) == 1
    assert cards[0]._params == {"bonus": 4, "penalty": 1, "dc": None}
    text = window.panel._readout.text()
    assert "15" in text  # die 12 + net modifier 3
    assert "Success" not in text and "Failure" not in text  # no DC → no degree


def test_roll_with_dc_shows_degree_of_success(
    qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(dice_roller, "roll_d20", lambda *a, **k: 20)
    window = DiceRollerWindow()
    window.panel._bonus_spin.setValue(5)
    window.panel._penalty_spin.setValue(0)
    window.panel._dc_check.setChecked(True)
    window.panel._dc_spin.setValue(15)

    window.panel._finish_roll()

    assert "Success" in window.panel._readout.text()
    assert "Nat 20" in window.panel._readout.text()


def test_saving_a_roll_adds_a_persisted_quick_roll(qapp: QApplication) -> None:
    window = DiceRollerWindow()

    window.panel._add_quick_roll({"bonus": 4, "penalty": 1, "dc": None})

    assert window.panel._quick_flow.count() == 1
    assert storage.load_settings()["quick_rolls"] == [{"bonus": 4, "penalty": 1, "dc": None}]

    # De-duplicated: the same params don't stack a second chip.
    window.panel._add_quick_roll({"bonus": 4, "penalty": 1, "dc": None})
    assert window.panel._quick_flow.count() == 1


def test_quick_rolls_persist_across_windows(qapp: QApplication) -> None:
    first = DiceRollerWindow()
    first.panel._add_quick_roll({"bonus": 2, "penalty": 0, "dc": 15})

    second = DiceRollerWindow()
    assert second.panel._quick_flow.count() == 1
    assert second.panel._quick_rolls == [{"bonus": 2, "penalty": 0, "dc": 15}]


def test_removing_a_quick_roll_persists(qapp: QApplication) -> None:
    window = DiceRollerWindow()
    entry = {"bonus": 3, "penalty": 0, "dc": None}
    window.panel._add_quick_roll(entry)

    window.panel._remove_quick_roll(entry)

    assert window.panel._quick_flow.count() == 0
    assert storage.load_settings()["quick_rolls"] == []


def test_named_quick_roll_shows_its_name(qapp: QApplication) -> None:
    window = DiceRollerWindow()

    window.panel._add_quick_roll({"bonus": 1, "penalty": 0, "dc": None}, name="Perception")

    assert window.panel._quick_rolls == [
        {"bonus": 1, "penalty": 0, "dc": None, "name": "Perception"}
    ]
    labels = {b.text() for b in window.panel._quick_container.findChildren(QPushButton)}
    assert "Perception" in labels


def test_reordering_moves_and_persists(qapp: QApplication) -> None:
    window = DiceRollerWindow()
    first = {"bonus": 1, "penalty": 0, "dc": None}
    second = {"bonus": 2, "penalty": 0, "dc": None}
    window.panel._add_quick_roll(first)
    window.panel._add_quick_roll(second)

    # Drop the second chip (index 1) before the first (insertion index 0).
    window.panel._reorder_quick_roll(1, 0)

    assert window.panel._quick_rolls == [second, first]
    assert storage.load_settings()["quick_rolls"] == [second, first]


def test_removing_a_history_card(qapp: QApplication, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(dice_roller, "roll_d20", lambda *a, **k: 7)
    window = DiceRollerWindow()
    window.panel._finish_roll()
    card = window._history_container.findChildren(RollCard)[0]

    card.removeRequested.emit()
    qapp.processEvents()

    assert window._history_container.findChildren(RollCard) == []


# -- rolling in a session ----------------------------------------------------


def test_a_session_replaces_the_private_history(qapp: QApplication, hosting: SessionBridge) -> None:
    window = DiceRollerWindow()

    assert window._session_box.isVisibleTo(window)
    assert not window._local_box.isVisibleTo(window)


def test_the_roll_comes_from_the_server_not_the_panel(
    qapp: QApplication, monkeypatch: pytest.MonkeyPatch, hosting: SessionBridge
) -> None:
    monkeypatch.setattr(dice_roller, "ROLL_DURATION_MS", 0)
    # Any local roll would use this; the server has its own rng.
    monkeypatch.setattr(dice_roller, "roll_d20", lambda *a, **k: 1)
    window = DiceRollerWindow()
    window.panel._bonus_spin.setValue(4)

    window.panel._start_roll()
    qapp.processEvents()

    recorded = hosting.server.state.rolls
    assert len(recorded) == 1
    assert recorded[0].bonus == 4
    assert str(recorded[0].total) in window.panel._readout.text()
    # The card is the shared one; nothing landed in the private list.
    assert len(window._session_history.cards()) == 1
    assert window._history_container.findChildren(RollCard) == []


def test_the_session_roll_is_graded_against_the_dc(
    qapp: QApplication, monkeypatch: pytest.MonkeyPatch, hosting: SessionBridge
) -> None:
    monkeypatch.setattr(dice_roller, "ROLL_DURATION_MS", 0)
    window = DiceRollerWindow()
    window.panel._dc_check.setChecked(True)
    window.panel._dc_spin.setValue(10)
    window.panel._bonus_spin.setValue(20)  # cannot fail

    window.panel._start_roll()
    qapp.processEvents()

    assert "Success" in window.panel._readout.text()
    assert "DC 10" in window.panel._readout.text()


def test_someone_elses_roll_does_not_end_ours(
    qapp: QApplication, monkeypatch: pytest.MonkeyPatch, hosting: SessionBridge
) -> None:
    monkeypatch.setattr(dice_roller, "ROLL_DURATION_MS", 0)
    window = DiceRollerWindow()
    # Nothing answers our request, so the panel stays waiting.
    monkeypatch.setattr(hosting, "request_roll", lambda **kw: True)
    window.panel._start_roll()

    other = hosting.server.state.add_player("Bo")
    hosting.server.roll(player_id=other.player_id, label="not ours")
    qapp.processEvents()

    assert window.panel._awaiting is True
    assert window.panel._rolling is True
    window.panel._abandon_roll("done")  # release the timer chain


def test_a_session_that_never_answers_releases_the_die(
    qapp: QApplication, monkeypatch: pytest.MonkeyPatch, hosting: SessionBridge
) -> None:
    monkeypatch.setattr(dice_roller, "ROLL_DURATION_MS", 0)
    monkeypatch.setattr(dice_roller, "SESSION_ROLL_TIMEOUT_MS", 0)
    monkeypatch.setattr(hosting, "request_roll", lambda **kw: True)
    window = DiceRollerWindow()

    window.panel._start_roll()

    assert dice_roller.NO_ANSWER in window.panel._readout.text()
    assert window.panel._die_button.isEnabled()
    assert window.panel._bonus_spin.isEnabled()


def test_a_roll_that_cannot_be_sent_says_so(
    qapp: QApplication, monkeypatch: pytest.MonkeyPatch, hosting: SessionBridge
) -> None:
    monkeypatch.setattr(dice_roller, "ROLL_DURATION_MS", 0)
    monkeypatch.setattr(hosting, "request_roll", lambda **kw: False)
    window = DiceRollerWindow()

    window.panel._start_roll()

    assert dice_roller.NOT_SENT in window.panel._readout.text()
    assert window.panel._die_button.isEnabled()


def test_leaving_the_session_brings_the_private_history_back(
    qapp: QApplication, hosting: SessionBridge
) -> None:
    window = DiceRollerWindow()

    hosting.stop()
    set_active_session(None)
    window._sync_session()

    assert window._local_box.isVisibleTo(window)
    assert not window._session_box.isVisibleTo(window)


# -- the hidden-roll option --------------------------------------------------


def test_the_hidden_option_is_off_by_default(qapp: QApplication) -> None:
    window = DiceRollerWindow()
    assert window.panel._hidden_check.isVisibleTo(window.panel) is False


def test_a_hidden_roll_never_reaches_the_wire(
    qapp: QApplication, monkeypatch: pytest.MonkeyPatch, hosting: SessionBridge
) -> None:
    monkeypatch.setattr(dice_roller, "ROLL_DURATION_MS", 0)
    panel = dice_roller.DiceRollerPanel(hidden_option=True)
    panel._hidden_check.setChecked(True)

    panel._start_roll()
    qapp.processEvents()

    recorded = hosting.server.state.rolls
    assert [roll.hidden for roll in recorded] == [True]
    assert hosting.server.state.visible_rolls() == []
    assert "only you" in panel._readout.text()

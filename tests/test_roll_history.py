"""The shared roll history: one log, rendered the same on every screen.

The panel is a *view* over the session's log, so the tests here drive it the two
ways it is really fed — a whole history when it attaches, and one roll at a time
after that — and check the two things that are easy to get wrong: a roll that
arrives twice must not double, and a hidden roll must be marked as hidden on the
one screen that gets it at all.

The GM's end runs against a real loopback session, because "the GM sees hidden
rolls and players do not" is a property of the server, not of this widget.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication, QLabel, QPushButton

from mm_companion.core import storage
from mm_companion.core.session.model import new_session
from mm_companion.ui.roll_history import (
    HIDDEN_MARK,
    MAX_CARDS,
    RollHistoryPanel,
    degree_label,
    roll_parameters,
)
from mm_companion.ui.session_bridge import SessionBridge, set_active_session


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
def panel(qapp: QApplication) -> RollHistoryPanel:
    return RollHistoryPanel()


@pytest.fixture
def hosting(qapp: QApplication) -> SessionBridge:
    bridge = SessionBridge()
    bridge.host(new_session("Table"), port=0, bind="127.0.0.1")
    yield bridge
    bridge.stop()


def roll(seq: int = 1, **kwargs) -> dict:
    """A roll record as the server broadcasts one."""
    defaults = {
        "seq": seq,
        "player_id": "p1",
        "player_name": "Aria",
        "die": 12,
        "bonus": 6,
        "penalty": 0,
        "dc": 15,
        "degree": 1,
        "critical": False,
        "label": "",
        "hidden": False,
    }
    return {**defaults, **kwargs}


def card_text(panel: RollHistoryPanel) -> str:
    """Everything the cards say, as one blob — enough for "does it mention X"."""
    return "\n".join(text_of(card) for card in panel.cards())


def text_of(card) -> str:
    """One card's labels, joined."""
    return "\n".join(label.text() for label in card.findChildren(QLabel))


# -- grading (pure) ---------------------------------------------------------


def test_degree_label_reads_a_single_success() -> None:
    assert degree_label(1, False, 10) == "Success"


def test_degree_label_counts_degrees() -> None:
    assert degree_label(-2, False, 10) == "Failure (2 degrees)"


def test_degree_label_notes_a_natural_twenty() -> None:
    assert degree_label(4, True, 20) == "Success (4 degrees) — Nat 20!"


def test_degree_label_notes_a_natural_one() -> None:
    assert degree_label(-1, True, 1) == "Failure — Nat 1!"


def test_degree_label_is_empty_without_a_grade() -> None:
    assert degree_label(None, False, 10) == ""


def test_roll_parameters_are_what_a_quick_roll_needs() -> None:
    assert roll_parameters(roll()) == {"bonus": 6, "penalty": 0, "dc": 15}


# -- rendering --------------------------------------------------------------


def test_an_empty_panel_says_so(panel: RollHistoryPanel) -> None:
    assert panel.cards() == []
    assert panel._empty.isVisibleTo(panel)


def test_a_roll_renders_who_what_and_how_it_went(panel: RollHistoryPanel) -> None:
    panel.add_roll(roll(label="Perception"))

    text = card_text(panel)
    assert "Aria" in text
    assert "Perception" in text
    assert "18" in text  # die 12 + net modifier 6
    assert "DC 15" in text
    assert "Success" in text
    assert not panel._empty.isVisibleTo(panel)


def test_an_ungraded_roll_shows_no_degree(panel: RollHistoryPanel) -> None:
    panel.add_roll(roll(dc=None, degree=None))

    text = card_text(panel)
    assert "Success" not in text and "Failure" not in text


def test_the_newest_roll_is_on_top(panel: RollHistoryPanel) -> None:
    panel.add_roll(roll(seq=1, player_name="Aria"))
    panel.add_roll(roll(seq=2, player_name="Bo"))

    assert "Bo" in text_of(panel.cards()[0])
    assert len(panel.cards()) == 2


def test_a_repeated_roll_is_not_shown_twice(panel: RollHistoryPanel) -> None:
    panel.add_roll(roll(seq=7))
    panel.add_roll(roll(seq=7))

    assert len(panel.cards()) == 1


def test_a_replacement_history_starts_over(panel: RollHistoryPanel) -> None:
    panel.add_roll(roll(seq=1, player_name="Aria"))
    panel.set_rolls([roll(seq=1, player_name="Aria"), roll(seq=2, player_name="Bo")])

    assert len(panel.cards()) == 2
    assert "Bo" in card_text(panel)


def test_the_list_is_capped(panel: RollHistoryPanel) -> None:
    for seq in range(MAX_CARDS + 20):
        panel.add_roll(roll(seq=seq))

    assert len(panel.cards()) == MAX_CARDS


def test_a_hidden_roll_is_marked(panel: RollHistoryPanel) -> None:
    panel.add_roll(roll(hidden=True))

    assert HIDDEN_MARK in card_text(panel)


def test_a_name_cannot_smuggle_markup(panel: RollHistoryPanel) -> None:
    panel.add_roll(roll(player_name="<b>huge</b>"))

    assert "&lt;b&gt;huge&lt;/b&gt;" in card_text(panel)


# -- the save button --------------------------------------------------------


def test_only_your_own_roll_can_be_saved(panel: RollHistoryPanel) -> None:
    panel._own_id = "p1"
    panel.add_roll(roll(seq=1, player_id="p1"))
    panel.add_roll(roll(seq=2, player_id="p2"))

    buttons = panel.findChildren(QPushButton)
    assert len(buttons) == 1


def test_saving_reports_the_parameters(panel: RollHistoryPanel) -> None:
    seen: list[dict] = []
    panel.saveRequested.connect(seen.append)
    panel._own_id = "p1"
    panel.add_roll(roll())

    panel.findChildren(QPushButton)[0].click()

    assert seen == [{"bonus": 6, "penalty": 0, "dc": 15}]


# -- the remove button (GM only) --------------------------------------------


def test_only_a_gm_panel_shows_a_remove_button(qapp: QApplication) -> None:
    gm_panel = RollHistoryPanel(gm=True)
    gm_panel.add_roll(roll(seq=1, player_id="p2"))
    assert any(b.text() == "✕" for b in gm_panel.findChildren(QPushButton))

    player_panel = RollHistoryPanel()
    player_panel.add_roll(roll(seq=1, player_id="p2"))
    assert not any(b.text() == "✕" for b in player_panel.findChildren(QPushButton))


def test_remove_roll_drops_the_matching_card(qapp: QApplication) -> None:
    panel = RollHistoryPanel(gm=True)
    panel.add_roll(roll(seq=1))
    panel.add_roll(roll(seq=2))

    panel.remove_roll(1)

    assert [card.seq for card in panel.cards()] == [2]
    # The seq is forgotten, so the same roll could legitimately be re-added later.
    assert 1 not in panel._seen


def test_removing_the_last_card_shows_the_empty_state(qapp: QApplication) -> None:
    panel = RollHistoryPanel(gm=True)
    panel.add_roll(roll(seq=1))
    panel.remove_roll(1)
    assert panel.cards() == []
    assert panel._empty.isVisibleTo(panel)


def test_an_offline_roll_can_be_struck_with_no_session(qapp: QApplication) -> None:
    """A roll made before hosting (no bridge) still removes locally off its ✕."""
    panel = RollHistoryPanel(gm=True)
    seen: list[int] = []
    panel.rollRemovedLocally.connect(seen.append)
    panel.add_roll(roll(seq=-1))  # a pre-hosting roll's negative id

    button = next(b for b in panel.findChildren(QPushButton) if b.text() == "✕")
    button.click()

    assert panel.cards() == []
    assert seen == [-1]


def test_a_roll_without_any_id_shows_no_remove_button(qapp: QApplication) -> None:
    panel = RollHistoryPanel(gm=True)
    no_seq = {k: v for k, v in roll().items() if k != "seq"}
    panel.add_roll(no_seq)
    assert not any(b.text() == "✕" for b in panel.findChildren(QPushButton))


def test_the_gm_removing_a_roll_clears_it_for_a_player(
    qapp: QApplication, hosting: SessionBridge
) -> None:
    gm_panel = RollHistoryPanel(gm=True)
    gm_panel.attach(hosting)
    player = SessionBridge()
    player_panel = RollHistoryPanel()
    try:
        player.join(_code_for(hosting), "Aria")
        player_panel.attach(player)

        record = hosting.server.roll(label="oops", bonus=1)
        _pump(qapp, lambda: len(player_panel.cards()) == 1)
        assert len(gm_panel.cards()) == 1

        # The GM strikes it; the removal fans out over the session to both panels.
        hosting.remove_roll(record.seq)
        _pump(qapp, lambda: player_panel.cards() == [] and gm_panel.cards() == [])

        assert gm_panel.cards() == []
        assert player_panel.cards() == []
    finally:
        player_panel.detach()
        player.stop()


# -- following a session ----------------------------------------------------


def test_attaching_seeds_from_the_history_already_there(
    qapp: QApplication, panel: RollHistoryPanel, hosting: SessionBridge
) -> None:
    hosting.server.roll(label="before the panel existed", bonus=3)

    panel.attach(hosting)

    assert len(panel.cards()) == 1
    assert "before the panel existed" in card_text(panel)


def test_an_attached_panel_follows_new_rolls(
    qapp: QApplication, panel: RollHistoryPanel, hosting: SessionBridge
) -> None:
    panel.attach(hosting)

    hosting.server.roll(label="live", bonus=2)
    qapp.processEvents()

    assert len(panel.cards()) == 1
    assert "live" in card_text(panel)


def test_attaching_twice_does_not_double_the_rolls(
    qapp: QApplication, panel: RollHistoryPanel, hosting: SessionBridge
) -> None:
    panel.attach(hosting)
    panel.attach(hosting)

    hosting.server.roll(label="once", bonus=0)
    qapp.processEvents()

    assert len(panel.cards()) == 1


def test_detaching_stops_the_feed(
    qapp: QApplication, panel: RollHistoryPanel, hosting: SessionBridge
) -> None:
    panel.attach(hosting)
    panel.detach()

    hosting.server.roll(label="unseen", bonus=0)
    qapp.processEvents()

    assert panel.cards() == []


def test_the_gm_sees_its_own_hidden_roll(
    qapp: QApplication, panel: RollHistoryPanel, hosting: SessionBridge
) -> None:
    panel.attach(hosting)

    hosting.server.roll(label="behind the screen", hidden=True)
    qapp.processEvents()

    assert len(panel.cards()) == 1
    assert HIDDEN_MARK in card_text(panel)


def test_a_players_panel_never_receives_a_hidden_roll(
    qapp: QApplication, hosting: SessionBridge
) -> None:
    """The exclusion is the server's, not the widget's — a player is never sent one."""
    player = SessionBridge()
    panel = RollHistoryPanel()
    try:
        player.join(
            _code_for(hosting),
            "Aria",
        )
        panel.attach(player)
        hosting.server.roll(label="behind the screen", hidden=True)
        hosting.server.roll(label="in the open", bonus=1)
        _pump(qapp, lambda: len(panel.cards()) >= 1)
    finally:
        player.stop()

    assert len(panel.cards()) == 1
    assert "in the open" in card_text(panel)
    assert "behind the screen" not in card_text(panel)


def _code_for(hosting: SessionBridge):
    from mm_companion.core.session.discovery import JoinCode

    host, port = hosting.server.address
    return JoinCode(host=host, port=port, token=hosting.server.state.host_token)


def _pump(qapp: QApplication, done, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while not done() and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.01)
    qapp.processEvents()

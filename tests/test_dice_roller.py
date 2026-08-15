"""The dice roller presents results, records history, and persists quick rolls.

The subject is :class:`DiceRollerView` — the roll panel plus whichever history is
the right one — which is what the sheet's Dice block puts on screen. There is no
standalone roller window any more.

Two modes, and the difference is the point. On its own the panel rolls locally
and its cards land in the view's own private history. In a session it rolls
*nothing* — it asks the server and waits for the broadcast — so the tests below
drive a real loopback session and check that the number on screen is the
server's, that the shared history replaces the private one, and that a session
which never answers releases the inputs instead of locking the die forever.

The tumble is zeroed (``ROLL_DURATION_MS``) wherever a test would otherwise wait
1.4 s for an animation.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QBoxLayout, QLabel, QPushButton

from mm_companion.core import storage
from mm_companion.core.dice import resolve_check
from mm_companion.core.rules import RollSpec
from mm_companion.core.session.model import new_session
from mm_companion.ui import dice_roller
from mm_companion.ui.dice_roller import DiceRollerPanel, DiceRollerView, degree_text
from mm_companion.ui.roll_history import (
    HISTORY_FLOOR_HEIGHT,
    MAX_QUICK_ROLLS,
    MIN_HISTORY_HEIGHT,
    NoteCard,
)
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
    view = DiceRollerView()
    view.panel._bonus_spin.setValue(4)
    view.panel._penalty_spin.setValue(1)  # net modifier +3
    view.panel._dc_check.setChecked(False)

    view.panel._finish_roll()

    cards = view._local_history.cards()
    assert len(cards) == 1
    assert cards[0]._params == {"bonus": 4, "penalty": 1, "dc": None}
    text = view.panel._readout.text()
    assert "15" in text  # die 12 + net modifier 3
    assert "Success" not in text and "Failure" not in text  # no DC → no degree


def test_roll_with_dc_shows_degree_of_success(
    qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(dice_roller, "roll_d20", lambda *a, **k: 20)
    view = DiceRollerView()
    view.panel._bonus_spin.setValue(5)
    view.panel._penalty_spin.setValue(0)
    view.panel._dc_check.setChecked(True)
    view.panel._dc_spin.setValue(15)

    view.panel._finish_roll()

    assert "Success" in view.panel._readout.text()
    assert "Nat 20" in view.panel._readout.text()


def test_saving_a_roll_adds_a_persisted_quick_roll(qapp: QApplication) -> None:
    view = DiceRollerView()

    view.panel._add_quick_roll({"bonus": 4, "penalty": 1, "dc": None})

    assert view.panel._quick_flow.count() == 1
    assert storage.load_settings()["quick_rolls"] == [{"bonus": 4, "penalty": 1, "dc": None}]

    # De-duplicated by the numbers alone, so a *name* doesn't make a second chip of
    # the same roll — which is what lets one star answer for both.
    view.panel._add_quick_roll({"bonus": 4, "penalty": 1, "dc": None})
    view.panel._add_quick_roll({"bonus": 4, "penalty": 1, "dc": None}, name="Attack")
    assert view.panel._quick_flow.count() == 1


def test_quick_rolls_persist_across_windows(qapp: QApplication) -> None:
    first = DiceRollerView()
    first.panel._add_quick_roll({"bonus": 2, "penalty": 0, "dc": 15})

    second = DiceRollerView()
    assert second.panel._quick_flow.count() == 1
    assert second.panel._quick_rolls == [{"bonus": 2, "penalty": 0, "dc": 15}]


def test_removing_a_quick_roll_persists(qapp: QApplication) -> None:
    view = DiceRollerView()
    entry = {"bonus": 3, "penalty": 0, "dc": None}
    view.panel._add_quick_roll(entry)

    view.panel._remove_quick_roll(entry)

    assert view.panel._quick_flow.count() == 0
    assert storage.load_settings()["quick_rolls"] == []


def test_named_quick_roll_shows_its_name(qapp: QApplication) -> None:
    view = DiceRollerView()

    view.panel._add_quick_roll({"bonus": 1, "penalty": 0, "dc": None}, name="Perception")

    assert view.panel._quick_rolls == [{"bonus": 1, "penalty": 0, "dc": None, "name": "Perception"}]
    labels = {b.text() for b in view.panel._quick_container.findChildren(QPushButton)}
    assert "Perception" in labels


def test_reordering_moves_and_persists(qapp: QApplication) -> None:
    view = DiceRollerView()
    first = {"bonus": 1, "penalty": 0, "dc": None}
    second = {"bonus": 2, "penalty": 0, "dc": None}
    view.panel._add_quick_roll(first)
    view.panel._add_quick_roll(second)

    # Drop the second chip (index 1) before the first (insertion index 0).
    view.panel._reorder_quick_roll(1, 0)

    assert view.panel._quick_rolls == [second, first]
    assert storage.load_settings()["quick_rolls"] == [second, first]


def test_the_strip_holds_no_more_than_the_cap(qapp: QApplication) -> None:
    # An unbounded strip ratchets the whole Dice block (and the pinned strip holding
    # it) taller and taller, which is the point of the cap.
    view = DiceRollerView()

    for bonus in range(MAX_QUICK_ROLLS + 3):
        view.panel._add_quick_roll({"bonus": bonus, "penalty": 0, "dc": None})

    assert view.panel._quick_flow.count() == MAX_QUICK_ROLLS
    assert view.panel.quick_rolls_full() is True
    assert len(storage.load_settings()["quick_rolls"]) == MAX_QUICK_ROLLS


def test_a_settings_file_over_the_cap_is_truncated_on_load(qapp: QApplication) -> None:
    # Written before there was a cap, or by hand — either way it must not hold the
    # strip open past it.
    stored = [{"bonus": b, "penalty": 0, "dc": None} for b in range(MAX_QUICK_ROLLS + 4)]
    storage.update_settings(quick_rolls=stored)

    view = DiceRollerView()

    assert len(view.panel._quick_rolls) == MAX_QUICK_ROLLS
    assert view.panel._quick_flow.count() == MAX_QUICK_ROLLS


def test_a_star_saves_then_unsaves_the_same_roll(qapp: QApplication) -> None:
    view = DiceRollerView()
    params = {"bonus": 3, "penalty": 0, "dc": 15}

    view.panel.toggle_quick_roll(params)
    assert view.panel._quick_flow.count() == 1

    view.panel.toggle_quick_roll(params)
    assert view.panel._quick_flow.count() == 0
    assert storage.load_settings()["quick_rolls"] == []


def test_a_stars_click_takes_out_the_chip_however_it_was_renamed(qapp: QApplication) -> None:
    view = DiceRollerView()
    entry = {"bonus": 3, "penalty": 0, "dc": None}
    view.panel._add_quick_roll(entry, name="Perception")

    # The card knows only its numbers; the name came later, on the chip.
    view.panel.toggle_quick_roll({"bonus": 3, "penalty": 0, "dc": None})

    assert view.panel._quick_rolls == []


def test_a_history_cards_star_follows_the_strip(
    qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(dice_roller, "roll_d20", lambda *a, **k: 9)
    view = DiceRollerView()
    view.panel._bonus_spin.setValue(2)
    view.panel._finish_roll()
    star = view._local_history.cards()[0].star

    assert star.is_saved() is False

    # Saving that roll lights its card, with no rebuild of the card in between.
    view.panel.toggle_quick_roll({"bonus": 2, "penalty": 0, "dc": None})
    assert star.is_saved() is True

    view.panel.toggle_quick_roll({"bonus": 2, "penalty": 0, "dc": None})
    assert star.is_saved() is False


def test_a_full_strip_disables_an_unsaved_cards_star(
    qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(dice_roller, "roll_d20", lambda *a, **k: 9)
    view = DiceRollerView()
    view.panel._bonus_spin.setValue(20)  # not a roll any of the chips below match
    view.panel._finish_roll()

    for bonus in range(MAX_QUICK_ROLLS):
        view.panel._add_quick_roll({"bonus": bonus, "penalty": 0, "dc": None})

    star = view._local_history.cards()[0].star
    assert star.is_saved() is False
    assert star.isEnabled() is False


def test_renaming_a_chip_persists_and_recaptions_it(
    qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    view = DiceRollerView()
    entry = {"bonus": 1, "penalty": 0, "dc": None}
    view.panel._add_quick_roll(entry)
    monkeypatch.setattr(
        dice_roller.QInputDialog, "getText", staticmethod(lambda *a, **k: ("Perception", True))
    )

    view.panel._rename_quick_roll(entry)

    assert storage.load_settings()["quick_rolls"] == [dict(entry, name="Perception")]
    labels = {b.text() for b in view.panel._quick_container.findChildren(QPushButton)}
    assert "Perception" in labels

    # And an empty answer puts the numbers back.
    monkeypatch.setattr(
        dice_roller.QInputDialog, "getText", staticmethod(lambda *a, **k: ("  ", True))
    )
    view.panel._rename_quick_roll(view.panel._quick_rolls[0])
    assert "name" not in view.panel._quick_rolls[0]


def test_removing_a_history_card(qapp: QApplication, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(dice_roller, "roll_d20", lambda *a, **k: 7)
    view = DiceRollerView()
    view.panel._finish_roll()
    card = view._local_history.cards()[0]

    card.removeRequested.emit()
    qapp.processEvents()

    assert view._local_history.cards() == []


# -- reflowing to the space available ----------------------------------------


def _settled(qapp: QApplication, view: DiceRollerView, width: int, height: int) -> None:
    """Resize *view* and let the deferred re-division settle (see `_divide_row`)."""
    view.resize(width, height)
    for _ in range(8):
        qapp.processEvents()


def test_a_narrow_tall_roller_stacks_its_parts(qapp: QApplication) -> None:
    view = DiceRollerView()
    view.show()

    _settled(qapp, view, 360, 800)  # the right-hand pinned strip's shape

    assert view.is_row is False
    assert view.panel.is_row is False
    assert view._splitter.orientation() is Qt.Orientation.Vertical
    assert view.panel._box.direction() is QBoxLayout.Direction.TopToBottom


def test_a_short_wide_roller_puts_every_part_in_one_row(qapp: QApplication) -> None:
    # The case that prompted the whole thing: a bottom strip is short and wide, and
    # a column there forces the strip open to half the window.
    view = DiceRollerView()
    view.show()

    _settled(qapp, view, 1500, 300)

    assert view.is_row is True
    assert view.panel.is_row is True  # both levels, so it is one row of four
    assert view._splitter.orientation() is Qt.Orientation.Horizontal
    assert view.panel._box.direction() is QBoxLayout.Direction.LeftToRight


def test_the_arrangement_follows_the_shape_back_and_forth(qapp: QApplication) -> None:
    view = DiceRollerView()
    view.show()

    _settled(qapp, view, 1500, 300)
    assert (view.is_row, view.panel.is_row) == (True, True)

    _settled(qapp, view, 360, 800)
    assert (view.is_row, view.panel.is_row) == (False, False)

    _settled(qapp, view, 1500, 300)
    assert (view.is_row, view.panel.is_row) == (True, True)


def test_a_row_reports_a_shorter_minimum_than_a_column(qapp: QApplication) -> None:
    # This is *why* the reflow fixes the bottom strip: the strip's thickness is
    # driven by the block's minimum height, and the row's is far smaller.
    view = DiceRollerView()
    view.show()

    _settled(qapp, view, 360, 800)
    tall = view.minimumSizeHint().height()
    _settled(qapp, view, 1500, 300)
    short = view.minimumSizeHint().height()

    assert short < tall


def test_going_wide_never_raises_the_minimum_width(qapp: QApplication) -> None:
    # A row's real width minimum would pin the window open at it; the widget can
    # always narrow again by reflowing, so it must keep reporting the column width.
    view = DiceRollerView()
    view.show()

    _settled(qapp, view, 360, 800)
    narrow = view.minimumSizeHint().width()
    _settled(qapp, view, 1500, 300)

    assert view.minimumSizeHint().width() <= narrow
    assert view.minimumSizeHint().width() < view.row_minimum_width()


# -- a chosen shape, rather than a derived one -------------------------------


def _extended(qapp: QApplication) -> DiceRollerView:
    storage.set_dice_layout(storage.DICE_LAYOUT_EXTENDED)
    view = DiceRollerView()
    view.show()
    return view


def test_extended_puts_the_controls_beside_the_history_however_narrow(
    qapp: QApplication,
) -> None:
    """The shape GM Mode was built as, now had by asking for it.

    At this width the auto layout stacks the two (see
    ``test_a_narrow_tall_roller_stacks_its_parts``) — which is the point: Extended
    is chosen, not derived from the room, so the room does not get a vote.
    """
    view = _extended(qapp)

    _settled(qapp, view, 360, 800)

    assert view.is_row is True
    assert view._splitter.orientation() is Qt.Orientation.Horizontal
    # ...and the panel stays a column inside it, or this would be a row of four.
    assert view.panel.is_row is False
    assert view.panel._box.direction() is QBoxLayout.Direction.TopToBottom


def test_extended_survives_the_widths_that_would_reshape_an_auto_roller(
    qapp: QApplication,
) -> None:
    view = _extended(qapp)

    for width, height in ((1500, 300), (360, 800), (900, 500)):
        _settled(qapp, view, width, height)
        assert (view.is_row, view.panel.is_row) == (True, False)


def test_extended_reports_the_row_width_it_cannot_narrow_out_of(
    qapp: QApplication,
) -> None:
    """The one rule the reflow's own minimum has to be turned around for.

    An auto roller reports the *column* width whatever axis it is on, because it
    can always narrow by flipping. A locked row cannot, so it has to hold the block
    — and through it the strip and the window — open at what it really needs.
    """
    view = _extended(qapp)
    _settled(qapp, view, 900, 500)
    locked = view.minimumSizeHint().width()

    view.set_layout(storage.DICE_LAYOUT_AUTO)
    _settled(qapp, view, 900, 500)

    assert locked > view.minimumSizeHint().width()
    assert locked >= view.panel.column_minimum_width()


def test_leaving_extended_hands_the_arrangement_back_to_the_room(
    qapp: QApplication,
) -> None:
    view = _extended(qapp)
    _settled(qapp, view, 360, 800)
    assert view.is_row is True

    view.set_layout(storage.DICE_LAYOUT_AUTO)
    _settled(qapp, view, 360, 800)

    assert (view.is_row, view.panel.is_row) == (False, False)


def test_a_column_gives_the_space_back_when_a_chip_goes(qapp: QApplication) -> None:
    # The panel carries no splitter stretch, so it keeps whatever height the chips
    # pushed it to unless the space is divided again — which used to leave a growing
    # gap above the strip as chips were removed.
    view = DiceRollerView()
    view.show()
    for bonus in range(MAX_QUICK_ROLLS):
        view.panel._add_quick_roll({"bonus": bonus, "penalty": 0, "dc": None})
    _settled(qapp, view, 360, 800)

    full_panel, full_history = view._splitter.sizes()

    for entry in list(view.panel._quick_rolls):
        view.panel._remove_quick_roll(entry)
    for _ in range(8):
        qapp.processEvents()

    empty_panel, empty_history = view._splitter.sizes()
    assert empty_panel < full_panel
    assert empty_history > full_history


def test_a_history_asks_for_two_cards_and_settles_for_one(qapp: QApplication) -> None:
    """The two heights are deliberately different numbers, and the gap is the point.

    A history *asks* for two cards' worth so it reads as a list. It is not a
    *minimum*, because the history is the elastic part of the Dice block: when the
    roll panel above it grows, this is what gives the room back rather than the
    block growing past the window.
    """
    view = DiceRollerView()

    for history in (view._local_history, view._session_history):
        assert history._scroll.minimumHeight() == HISTORY_FLOOR_HEIGHT
        assert history.sizeHint().height() == MIN_HISTORY_HEIGHT
        assert HISTORY_FLOOR_HEIGHT < MIN_HISTORY_HEIGHT


def test_a_history_does_not_grow_taller_as_the_log_gets_longer(qapp: QApplication) -> None:
    """What it asks for is a readable window onto the log, not the whole log.

    Left to itself a ``QScrollArea`` reports its inner widget's hint, and a block's
    minimum is its content's *preferred* height — so the Dice block's minimum used
    to climb with every roll until the pinned strip could no longer fit it.
    """
    view = DiceRollerView()
    before = view._local_history.sizeHint()

    for _ in range(20):
        view._local_history.add_roll(
            {"die": 11, "bonus": 0, "penalty": 0, "dc": None, "result": None}
        )

    assert len(view._local_history.cards()) == 20
    assert view._local_history.sizeHint() == before


def test_a_dragged_split_is_left_alone_until_the_axis_flips(qapp: QApplication) -> None:
    view = DiceRollerView()
    view.show()
    _settled(qapp, view, 1500, 300)

    # What dragging the handle emits (setSizes never does — the same distinction
    # PinnedBoard._remember_dragged_extent relies on).
    view._splitter.setSizes([900, 580])
    view._splitter.splitterMoved.emit(900, 1)
    _settled(qapp, view, 1400, 300)

    assert view._user_sized is True
    assert view._splitter.sizes()[0] > view._row_sizes()[0]  # our division did not win

    _settled(qapp, view, 360, 800)  # a flip is a fresh axis, so the choice is dropped
    assert view._user_sized is False


# -- rolling in a session ----------------------------------------------------


def test_a_session_replaces_the_private_history(qapp: QApplication, hosting: SessionBridge) -> None:
    view = DiceRollerView()

    assert view._session_box.isVisibleTo(view)
    assert not view._local_box.isVisibleTo(view)


def test_the_roll_comes_from_the_server_not_the_panel(
    qapp: QApplication, monkeypatch: pytest.MonkeyPatch, hosting: SessionBridge
) -> None:
    monkeypatch.setattr(dice_roller, "ROLL_DURATION_MS", 0)
    # Any local roll would use this; the server has its own rng.
    monkeypatch.setattr(dice_roller, "roll_d20", lambda *a, **k: 1)
    view = DiceRollerView()
    view.panel._bonus_spin.setValue(4)

    view.panel._start_roll()
    qapp.processEvents()

    recorded = hosting.server.state.rolls
    assert len(recorded) == 1
    assert recorded[0].bonus == 4
    assert str(recorded[0].total) in view.panel._readout.text()
    # The card is the shared one; nothing landed in the private list.
    assert len(view._session_history.cards()) == 1
    assert view._local_history.cards() == []


def test_the_session_roll_is_graded_against_the_dc(
    qapp: QApplication, monkeypatch: pytest.MonkeyPatch, hosting: SessionBridge
) -> None:
    monkeypatch.setattr(dice_roller, "ROLL_DURATION_MS", 0)
    view = DiceRollerView()
    view.panel._dc_check.setChecked(True)
    view.panel._dc_spin.setValue(10)
    view.panel._bonus_spin.setValue(20)  # cannot fail

    view.panel._start_roll()
    qapp.processEvents()

    assert "Success" in view.panel._readout.text()
    assert "DC 10" in view.panel._readout.text()


def test_someone_elses_roll_does_not_end_ours(
    qapp: QApplication, monkeypatch: pytest.MonkeyPatch, hosting: SessionBridge
) -> None:
    monkeypatch.setattr(dice_roller, "ROLL_DURATION_MS", 0)
    view = DiceRollerView()
    # Nothing answers our request, so the panel stays waiting.
    monkeypatch.setattr(hosting, "request_roll", lambda **kw: True)
    view.panel._start_roll()

    other = hosting.server.state.add_player("Bo")
    hosting.server.roll(player_id=other.player_id, label="not ours")
    qapp.processEvents()

    assert view.panel._awaiting is True
    assert view.panel._rolling is True
    view.panel._abandon_roll("done")  # release the timer chain


def test_a_session_that_never_answers_releases_the_die(
    qapp: QApplication, monkeypatch: pytest.MonkeyPatch, hosting: SessionBridge
) -> None:
    monkeypatch.setattr(dice_roller, "ROLL_DURATION_MS", 0)
    monkeypatch.setattr(dice_roller, "SESSION_ROLL_TIMEOUT_MS", 0)
    monkeypatch.setattr(hosting, "request_roll", lambda **kw: True)
    view = DiceRollerView()

    view.panel._start_roll()

    assert dice_roller.NO_ANSWER in view.panel._readout.text()
    assert view.panel._die_button.isEnabled()
    assert view.panel._bonus_spin.isEnabled()


def test_a_roll_that_cannot_be_sent_says_so(
    qapp: QApplication, monkeypatch: pytest.MonkeyPatch, hosting: SessionBridge
) -> None:
    monkeypatch.setattr(dice_roller, "ROLL_DURATION_MS", 0)
    monkeypatch.setattr(hosting, "request_roll", lambda **kw: False)
    view = DiceRollerView()

    view.panel._start_roll()

    assert dice_roller.NOT_SENT in view.panel._readout.text()
    assert view.panel._die_button.isEnabled()


def test_leaving_the_session_brings_the_private_history_back(
    qapp: QApplication, hosting: SessionBridge
) -> None:
    view = DiceRollerView()

    hosting.stop()
    set_active_session(None)
    view._sync_session()

    assert view._local_box.isVisibleTo(view)
    assert not view._session_box.isVisibleTo(view)


# -- notes in the history ----------------------------------------------------


def test_a_note_lands_in_the_private_history(qapp: QApplication) -> None:
    view = DiceRollerView()

    view.add_local_note("spent a hero point — 2 left")

    notes = view._local_history.findChildren(NoteCard)
    assert [note.findChild(QLabel).text() for note in notes] == ["spent a hero point — 2 left"]
    # A note is not a roll: it has no parameters to save to the quick-roll strip.
    assert view._local_history.cards() == []


def test_a_note_reaches_the_shared_history(qapp: QApplication, hosting: SessionBridge) -> None:
    view = DiceRollerView()

    assert hosting.post_note("gained a hero point — 3 left") is True
    qapp.processEvents()

    cards = view._session_history.findChildren(NoteCard)
    assert len(cards) == 1
    assert "gained a hero point" in cards[0].findChildren(QLabel)[-1].text()


def test_a_note_arriving_mid_roll_is_not_mistaken_for_the_answer(
    qapp: QApplication, monkeypatch: pytest.MonkeyPatch, hosting: SessionBridge
) -> None:
    """A note has no die; taking one for the roll would settle the d20 on zero."""
    monkeypatch.setattr(dice_roller, "ROLL_DURATION_MS", 0)
    monkeypatch.setattr(hosting, "request_roll", lambda **kw: True)
    view = DiceRollerView()
    view.panel._start_roll()

    hosting.post_note("spent a hero point — 1 left")  # from our own seat, mid-tumble
    qapp.processEvents()

    assert view.panel._awaiting is True
    assert view.panel._pending is None
    view.panel._abandon_roll("done")  # release the timer chain


# -- the hidden-roll option --------------------------------------------------


def test_the_hidden_option_is_off_by_default(qapp: QApplication) -> None:
    view = DiceRollerView()
    assert view.panel._hidden_check.isVisibleTo(view.panel) is False


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


@pytest.mark.parametrize("preference", [storage.DICE_LAYOUT_COMPACT, storage.DICE_LAYOUT_EXTENDED])
def test_a_chosen_shape_is_offered_the_width_it_asks_for(qapp, preference: str) -> None:
    """A panel that will not reflow must not be measured as one that might.

    Only Extended took the locked branch, so a Compact preference fell through to
    the auto one and was sized at its row-of-three minimum — an arrangement it is
    pinned out of adopting. On a wide block that handed the controls two thirds of
    the width and squeezed the history, the opposite of what Compact is for.
    """
    view = DiceRollerView()
    view.set_layout(preference)
    view.resize(1000, 500)
    view.show()
    qapp.processEvents()

    panel_width, history_width = view._splitter.sizes()

    assert panel_width == view.panel.sizeHint().width()
    assert history_width > panel_width


def test_clearing_the_chip_takes_back_the_dc_it_brought(qapp) -> None:
    """The ✕ on the chip means "forget what I loaded", DC included.

    A spec that names its own difficulty ticks the box; unloading it left the box
    ticked, so every later manual roll was graded against a trait that was no longer
    there. Requests made this the common path rather than a rare one.
    """
    panel = DiceRollerPanel()
    panel.load_spec(RollSpec(label="Toughness", modifier=0, dc=18))
    assert panel._dc_check.isChecked()
    assert panel._dc_spin.value() == 18

    panel.load_spec(None)

    assert not panel._dc_check.isChecked()


def test_a_dc_the_player_typed_is_left_alone(qapp) -> None:
    """The guard on the one above: only a DC the chip supplied is withdrawn."""
    panel = DiceRollerPanel()
    panel._dc_check.setChecked(True)
    panel._dc_spin.setValue(15)

    panel.load_spec(RollSpec(label="Athletics", modifier=4))  # brings no DC of its own
    assert panel._dc_check.isChecked()
    assert panel._dc_spin.value() == 15

    panel.load_spec(None)
    assert panel._dc_check.isChecked()
    assert panel._dc_spin.value() == 15

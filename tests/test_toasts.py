"""Notifications: session notices floating over a window, and rolls over the desktop."""

from __future__ import annotations

import pytest
from PySide6.QtCore import QEvent
from PySide6.QtWidgets import (
    QApplication,
    QLabel,
    QMainWindow,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

from mm_companion.core import storage
from mm_companion.ui import toasts
from mm_companion.ui.roll_history import RollHistoryPanel
from mm_companion.ui.toasts import ToastCard, ToastStack, notify_window, roll_toaster


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


@pytest.fixture
def live_toasts():
    """Let notifications through for this test (the suite suppresses them)."""
    toasts.SUPPRESSED = False
    yield roll_toaster()
    roll_toaster().clear()
    QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    toasts.SUPPRESSED = True


def _roll(seq: int, **extra) -> dict:
    roll = {
        "kind": "roll",
        "seq": seq,
        "player_name": "Sam",
        "label": "Athletics",
        "die": 12,
        "bonus": 9,
        "penalty": 0,
        "dc": 15,
        "degree": 2,
        "critical": False,
    }
    roll.update(extra)
    return roll


# -- the card ------------------------------------------------------------------------


def test_hovering_a_card_holds_it_until_the_pointer_leaves(qapp) -> None:
    card = ToastCard()
    card.add_widget(QLabel("Sam joined."))
    card.poke()
    assert card._timer.isActive()

    card.enterEvent(None)  # the pointer arrives
    assert not card._timer.isActive()
    # Even the dwell running out while it is hovered does not start the fade.
    card._begin_fade()
    assert card._fade.state() != card._fade.State.Running

    card.leaveEvent(QEvent(QEvent.Type.Leave))
    assert card._timer.isActive()


def test_a_held_card_ignores_the_pointer_leaving(qapp) -> None:
    card = ToastCard()
    card.hold()
    card.enterEvent(None)
    card.leaveEvent(QEvent(QEvent.Type.Leave))
    assert card.held and not card._timer.isActive()


def test_a_card_says_when_it_has_gone(qapp) -> None:
    card = ToastCard()
    gone: list[bool] = []
    card.closed.connect(lambda: gone.append(True))
    card.poke()
    card.dismiss()
    assert gone == [True]
    card.dismiss()  # already gone: nothing more to say
    assert gone == [True]


# -- over a window -------------------------------------------------------------------


def test_a_notice_floats_over_the_window_rather_than_taking_a_row(qapp) -> None:
    host = QWidget()
    column = QVBoxLayout(host)
    block = QLabel("a block")
    column.addWidget(block)
    host.resize(600, 400)
    host.show()
    qapp.processEvents()
    before = block.geometry()

    stack = ToastStack(host)
    card = stack.message("Connected to “Wednesday”.")
    qapp.processEvents()

    # The block underneath did not move or shrink: the stack is in no layout.
    assert block.geometry() == before
    assert column.indexOf(stack) == -1
    # And it sits in the bottom-right corner, over the content.
    assert stack.geometry().right() < host.width()
    assert stack.geometry().bottom() < host.height()
    assert stack.geometry().height() > 0
    assert not card.isHidden()


def test_a_one_off_message_goes_away_for_good(qapp) -> None:
    host = QWidget()
    host.resize(500, 300)
    stack = ToastStack(host)
    card = stack.message("Saved.")
    card.dismiss()
    QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert stack.cards() == []


def test_a_player_window_gets_its_session_messages_as_floating_notices(qapp) -> None:
    window = QMainWindow()
    window.setCentralWidget(QWidget())
    card = notify_window(window, "Joined “Wednesday”.")
    assert isinstance(card.parentWidget(), ToastStack)
    # No status bar was conjured up to hold it.
    assert window.findChild(QStatusBar) is None
    assert notify_window(window, "Again").parentWidget() is card.parentWidget()


# -- over the desktop ------------------------------------------------------------------


def test_a_live_roll_pops_up_once_however_many_histories_show_it(qapp, live_toasts) -> None:
    first, second = RollHistoryPanel(), RollHistoryPanel()
    first.add_roll(_roll(7))
    second.add_roll(_roll(7))

    shown = live_toasts.cards()
    assert len(shown) == 1
    labels = " ".join(label.text() for label in shown[0].findChildren(QLabel))
    assert "Sam" in labels and "Athletics" in labels and "Success" in labels


def test_a_replayed_log_does_not_flood_the_screen(qapp, live_toasts) -> None:
    panel = RollHistoryPanel()
    panel.set_rolls([_roll(seq) for seq in range(1, 30)])
    assert live_toasts.cards() == []

    panel.add_roll(_roll(30))
    assert len(live_toasts.cards()) == 1


def test_switching_notifications_off_stops_them(qapp, live_toasts) -> None:
    storage.set_roll_notifications(False)
    RollHistoryPanel().add_roll(_roll(41))
    assert live_toasts.cards() == []


def test_a_burst_keeps_only_the_newest_few(qapp, live_toasts) -> None:
    panel = RollHistoryPanel()
    for seq in range(100, 100 + live_toasts.MAX_SHOWN + 3):
        panel.add_roll(_roll(seq))
    QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert len(live_toasts.cards()) == live_toasts.MAX_SHOWN


def test_the_stack_grows_away_from_the_chosen_corner(qapp, live_toasts) -> None:
    storage.set_roll_notification_corner(storage.TOAST_CORNER_TOP_LEFT)
    panel = RollHistoryPanel()
    panel.add_roll(_roll(201))
    panel.add_roll(_roll(202))
    older, newer = live_toasts.cards()
    # Top corner: the newest sits nearest the top, the older one below it.
    assert newer.y() < older.y()
    assert newer.x() == older.x()


def test_a_private_roll_pops_up_too(qapp, live_toasts) -> None:
    from mm_companion.ui.dice_roller import LocalRollHistory

    history = LocalRollHistory()
    history.add_roll({"die": 5, "bonus": 2, "penalty": 0, "dc": None, "result": None})
    history.add_note("spent a hero point — 1 left")
    assert len(live_toasts.cards()) == 2

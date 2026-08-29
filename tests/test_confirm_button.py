"""The button that asks a second time in place of opening a dialog.

The app has two answers for a destructive action: ``QMessageBox.question``
defaulting to No, and — for anything reversible — no question at all plus undo.
This is the third case. "New scene" is neither reversible nor something a GM does
at leisure: it happens mid-round, where a modal stops the whole table to ask
something the button could have said in its own caption.

Driven through ``click()`` rather than synthetic mouse events: what is being
tested is the arm/confirm state machine, not Qt's press handling.
"""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QApplication

from mm_companion.ui import theme
from mm_companion.ui.widgets import CONFIRM_ARM_MS, ConfirmButton


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


@pytest.fixture
def button(qapp) -> ConfirmButton:
    return ConfirmButton("New scene")


def test_it_starts_safe(button: ConfirmButton) -> None:
    assert button.armed is False
    assert button.text() == "New scene"
    assert button.styleSheet() == ""


def test_one_click_only_arms_it(button: ConfirmButton) -> None:
    fired: list[int] = []
    button.confirmed.connect(lambda: fired.append(1))

    button.click()

    assert button.armed is True
    assert fired == []


def test_the_armed_caption_is_the_whole_warning(button: ConfirmButton) -> None:
    """There is no dialog to read, so the button has to say what is about to
    happen — and look unlike its resting self while it does."""
    button.click()

    assert button.text() == "Confirm?"
    assert theme.color("tint.worse") in button.styleSheet()


def test_the_second_click_goes_through_and_disarms(button: ConfirmButton) -> None:
    fired: list[int] = []
    button.confirmed.connect(lambda: fired.append(1))

    button.click()
    button.click()

    assert fired == [1]
    assert button.armed is False
    assert button.text() == "New scene"
    assert button.styleSheet() == ""


def test_a_stray_click_disarms_itself(button: ConfirmButton) -> None:
    """Nothing happens if the second click never comes: a button left armed by an
    accident must be back to its safe self before it is passed again."""
    fired: list[int] = []
    button.confirmed.connect(lambda: fired.append(1))
    button.click()

    button.disarm()  # what the single-shot timer does on its own

    assert button.armed is False
    assert fired == []
    button.click()
    assert button.armed is True  # and the next click re-arms rather than firing
    assert fired == []


def test_it_arms_for_a_stated_dwell(button: ConfirmButton) -> None:
    """Long enough to read the caption and reach it, short enough not to linger."""
    button.click()

    assert button._timer.isSingleShot() is True
    assert button._timer.interval() == CONFIRM_ARM_MS


def test_restating_the_caption_does_not_cancel_a_confirmation(
    button: ConfirmButton,
) -> None:
    """A caller keeping a caption up to date must not silently disarm a question
    the user is halfway through answering."""
    button.click()

    button.setText("Clear scene")

    assert button.armed is True
    assert button.text() == "Confirm?"

    button.disarm()
    assert button.text() == "Clear scene"

"""The Settings window's GM Mode page: the default pinned-parameter strips.

The interesting cases are all about the gap between a *default* and a card. A
default is written before the card it seeds exists, so it can only name
character-free things (hence the picker this page opens); and a card's strip stops
following the defaults the moment the card exists, so overruling that is a button
rather than a side effect of Save.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication, QMessageBox

from mm_companion.core import storage
from mm_companion.core.rules import (
    PIN_ABILITY,
    PIN_INITIATIVE,
    PIN_POWER,
    PIN_RESISTANCE,
    SELECT_FIRST_DAMAGE,
    PinRef,
)
from mm_companion.core.storage import DEFAULT_SETTINGS
from mm_companion.ui.pin_picker import PIN_ROLE
from mm_companion.ui.settings.gm_page import GMPage


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, qapp) -> Path:
    monkeypatch.setenv(storage.HOME_ENV_VAR, str(tmp_path))
    storage.ensure_workspace()
    return tmp_path


@pytest.fixture
def page(qapp) -> GMPage:
    return GMPage()


def say(monkeypatch: pytest.MonkeyPatch, answer: QMessageBox.StandardButton) -> None:
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: answer)


def captions(page: GMPage, kind: str) -> list[str]:
    widget = page._lists[kind]
    return [widget.item(row).text() for row in range(widget.count())]


# -- the lists ------------------------------------------------------------------


def test_both_lists_open_on_what_is_stored(page: GMPage) -> None:
    assert captions(page, "player") == ["DEF", "Toughness", "Initiative", "Perception"]
    assert captions(page, "npc") == ["DEF", "Toughness", "ATK", "First Damage"]
    assert not page.is_dirty()


def test_adding_a_pin_marks_the_page_dirty_and_saving_writes_it(page: GMPage) -> None:
    page._add_pin("player", PinRef(PIN_RESISTANCE, "WILL"))
    assert page.is_dirty()

    page.save()

    assert not page.is_dirty()
    assert storage.gm_default_pins()["player"][-1] == {"kind": "resistance", "key": "WILL"}


def test_a_pin_already_in_the_list_is_not_added_twice(page: GMPage) -> None:
    page._add_pin("npc", PinRef(PIN_ABILITY, "ATK"))

    assert captions(page, "npc").count("ATK") == 1
    assert not page.is_dirty()


def test_removing_leaves_the_other_list_alone(page: GMPage) -> None:
    page._remove_pin("npc", PinRef(PIN_POWER, select=SELECT_FIRST_DAMAGE))
    page.save()

    assert storage.gm_default_pins()["npc"] == [
        {"kind": "resistance", "key": "DEF"},
        {"kind": "resistance", "key": "TOUGHNESS"},
        {"kind": "ability", "key": "ATK"},
    ]
    assert storage.gm_default_pins()["player"] == DEFAULT_SETTINGS["gm_default_pins"]["player"]


def test_a_reordered_list_is_saved_in_its_new_order(page: GMPage) -> None:
    reversed_pins = list(reversed(page.pins("player")))
    page.set_pins("player", reversed_pins)

    page.save()

    assert storage.gm_default_pins()["player"][0] == {"kind": "skill", "key": "Perception"}


def test_a_list_emptied_on_purpose_survives_the_round_trip(page: GMPage, qapp) -> None:
    """The same rule a card's own strip follows: empty is an answer."""
    page.set_pins("npc", [])
    page.save()

    assert GMPage().pins("npc") == []


def test_restore_shipped_puts_one_list_back_without_touching_the_other(page: GMPage) -> None:
    page.set_pins("npc", [])
    page.set_pins("player", [PinRef(PIN_INITIATIVE)])

    page._restore_shipped("npc")

    assert captions(page, "npc") == ["DEF", "Toughness", "ATK", "First Damage"]
    assert captions(page, "player") == ["Initiative"]


# -- the picker -------------------------------------------------------------------


def test_the_picker_offers_only_what_a_default_can_name(page: GMPage) -> None:
    """No concrete power: a power id belongs to one character, a default to none."""
    page._open_picker("npc")
    picker = page._pickers["npc"]
    try:
        tree = picker._tree
        groups = {tree.topLevelItem(i).text(0) for i in range(tree.topLevelItemCount())}
        powers = next(
            tree.topLevelItem(i)
            for i in range(tree.topLevelItemCount())
            if tree.topLevelItem(i).text(0) == "Powers"
        )

        assert "Skills" in groups
        assert powers.childCount() == 1
        assert powers.child(0).data(0, PIN_ROLE) == PinRef(PIN_POWER, select=SELECT_FIRST_DAMAGE)
        # No reading to show without a character, so no column claiming there is.
        assert tree.isColumnHidden(1)
    finally:
        picker.close()


def test_the_picker_toggles_rows_onto_the_list_behind_it(page: GMPage) -> None:
    page._open_picker("player")
    picker = page._pickers["player"]
    try:
        picker.pinRequested.emit(PinRef(PIN_RESISTANCE, "WILL"))
        assert captions(page, "player")[-1] == "Will"

        picker.unpinRequested.emit(PinRef(PIN_RESISTANCE, "WILL"))
        assert "Will" not in captions(page, "player")
    finally:
        picker.close()


def test_a_removal_made_here_reaches_an_open_picker(page: GMPage) -> None:
    """Or its menu offers to unpin something the list no longer has."""
    page._open_picker("npc")
    picker = page._pickers["npc"]
    try:
        assert picker.action_text(PinRef(PIN_ABILITY, "ATK")) == "Unpin"

        page.set_pins("npc", [])
        page._push_to_picker("npc")

        assert picker.action_text(PinRef(PIN_ABILITY, "ATK")) == "Pin"
    finally:
        picker.close()


def test_closing_the_page_takes_its_pickers_with_it(page: GMPage, qapp) -> None:
    """A modeless dialog outliving the window that opened it is nobody's idea."""
    page._open_picker("player")

    page.discard()
    qapp.processEvents()

    assert page._pickers == {}


# -- applying to the board ----------------------------------------------------------


def test_applying_to_the_board_saves_first_and_clears_the_card_strips(
    page: GMPage, monkeypatch: pytest.MonkeyPatch
) -> None:
    say(monkeypatch, QMessageBox.StandardButton.Yes)
    storage.update_settings(gm_pins={"npc:goon.json": [{"kind": "initiative"}]})
    page._add_pin("player", PinRef(PIN_RESISTANCE, "WILL"))

    page._apply_to_board()

    assert not page.is_dirty()
    assert storage.gm_default_pins()["player"][-1] == {"kind": "resistance", "key": "WILL"}
    assert storage.load_settings()["gm_pins"] == {}


def test_a_declined_apply_changes_nothing(page: GMPage, monkeypatch: pytest.MonkeyPatch) -> None:
    say(monkeypatch, QMessageBox.StandardButton.No)
    storage.update_settings(gm_pins={"npc:goon.json": [{"kind": "initiative"}]})
    page._add_pin("player", PinRef(PIN_RESISTANCE, "WILL"))

    page._apply_to_board()

    assert page.is_dirty()
    assert storage.load_settings()["gm_pins"] != {}

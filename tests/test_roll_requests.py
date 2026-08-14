"""Asking the table to roll something: the Request row, the card, and the wire.

The other half of the chain ``test_roll_routing.py`` covers. There, a roll that
landed put a button on everyone's card; here nothing is rolled at all — one
player names a trait and a difficulty, and the button appears on every screen for
whoever's sheet is being asked about.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication, QPushButton

from mm_companion.core import storage
from mm_companion.core.character import Character
from mm_companion.core.data_loader import load_game_data
from mm_companion.core.rules import (
    KIND_ABILITY,
    KIND_INITIATIVE,
    KIND_RESISTANCE,
    KIND_SKILL,
    RollSpec,
    localize_spec,
    requested_roll_choices,
)
from mm_companion.core.session.model import KIND_REQUEST, new_session
from mm_companion.ui import dice_roller
from mm_companion.ui.character_sheet import CharacterSheet
from mm_companion.ui.roll_history import RequestCard
from mm_companion.ui.session_bridge import SessionBridge, set_active_session


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


@pytest.fixture(scope="module")
def data():
    return load_game_data()


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
def hosting(qapp: QApplication) -> SessionBridge:
    """A real session on loopback, published as the process-wide one."""
    bridge = SessionBridge()
    bridge.host(new_session("Table"), port=0, bind="127.0.0.1")
    set_active_session(bridge)
    yield bridge
    bridge.stop()


def _hero(data) -> Character:
    char = Character.new_default(data)
    char.abilities.update({"STR": 4, "STA": 5, "AGL": 3, "AWE": 2, "ATK": 7})
    char.skill_ranks["Athletics"] = 5
    return char


def _sheet() -> CharacterSheet:
    data = load_game_data()
    return CharacterSheet(data, _hero(data))


def _roll_buttons(card) -> list[QPushButton]:
    return [b for b in card.findChildren(QPushButton) if b.text().startswith("🎲")]


def _pick(panel, label: str) -> None:
    """Select the combobox entry captioned *label* (a trait is indented under its group)."""
    index = panel._request_combo.findText(f"  {label}")
    assert index >= 0, label
    panel._request_combo.setCurrentIndex(index)


# -- the choices -------------------------------------------------------------


def test_the_choices_are_the_four_kinds_a_sheet_can_answer(data) -> None:
    groups = {group.title: group for group in requested_roll_choices(data)}

    assert set(groups) == {"Abilities", "Resistances", "Derived", "Skills"}
    kinds = {v.spec.kind for group in groups.values() for v in group.values}
    assert kinds == {KIND_ABILITY, KIND_RESISTANCE, KIND_SKILL, KIND_INITIATIVE}


def test_no_choice_names_something_only_one_character_has(data) -> None:
    """No powers, no equipment, no Defence DC.

    A power pin is an id belonging to one character, and a Defence DC resolves to
    no spec at all — neither is a thing to put in somebody else's roller.
    """
    groups = requested_roll_choices(data)
    titles = {group.title for group in groups}
    assert "Powers" not in titles and "Equipment" not in titles

    labels = {v.label for group in groups for v in group.values}
    assert not any(label.endswith(" DC") for label in labels)


def test_every_choice_travels_at_zero_with_a_key_that_localizes(data) -> None:
    """The mechanism itself: the asker sends no number and the reader's sheet fills one in."""
    hero = _hero(data)
    for group in requested_roll_choices(data):
        for value in group.values:
            assert value.spec.modifier == 0, value.label
            assert value.spec.trait_key, value.label
            # Resolvable on a real sheet — a key nothing answers is a dead chip.
            assert localize_spec(value.spec, hero, data) is not value.spec, value.label


# -- the control row ---------------------------------------------------------


def test_the_row_is_hidden_until_a_host_supplies_traits(qapp: QApplication) -> None:
    """A roller with no ruleset behind it is the panel it always was."""
    panel = dice_roller.DiceRollerPanel()
    assert not panel._request_button.isVisibleTo(panel)

    panel.set_roll_choices(requested_roll_choices(load_game_data()))
    assert panel._request_button.isVisibleTo(panel)


def test_the_ask_button_waits_for_a_real_trait(qapp: QApplication) -> None:
    """A group title is a heading, not a choice, and nothing is picked to begin with."""
    panel = _sheet().dice.panel
    assert panel._request_combo.currentIndex() == -1
    assert not panel._request_button.isEnabled()

    heading = panel._request_combo.findText("Abilities")
    assert heading >= 0
    panel._request_combo.setCurrentIndex(heading)
    assert not panel._request_button.isEnabled()

    _pick(panel, "Strength")
    assert panel._request_button.isEnabled()


def test_a_difficulty_of_zero_means_no_difficulty(qapp: QApplication) -> None:
    """ "Everyone roll Initiative" asks for no DC, and DC 0 is not a thing anyone means."""
    panel = _sheet().dice.panel
    _pick(panel, "Initiative")

    assert panel._request_dc.value() == 0
    assert panel._requested_spec().dc is None

    panel._request_dc.setValue(15)
    assert panel._requested_spec().dc == 15


def test_asking_rolls_nothing_here(qapp: QApplication) -> None:
    """The asker's die stays still: this is a request, not a roll."""
    sheet = _sheet()
    panel = sheet.dice.panel
    _pick(panel, "Perception")
    panel._request_button.click()

    assert panel.current_spec() is None
    # ``cards()`` is the roll cards; a request is not one of them.
    assert sheet.dice.view._local_history.cards() == []


# -- the card ----------------------------------------------------------------


def test_off_the_air_the_request_lands_in_the_private_history(qapp: QApplication) -> None:
    """Asking with no table still writes a card — the button rolls it here."""
    sheet = _sheet()
    panel = sheet.dice.panel
    _pick(panel, "Athletics")
    panel._request_dc.setValue(15)
    panel._request_button.click()

    card = sheet.dice.view._local_history.findChild(RequestCard)
    assert card is not None
    assert _roll_buttons(card)[0].text() == "🎲 Athletics vs. DC 15"


def test_the_button_rolls_it_on_the_sheet_that_clicks_it(qapp: QApplication) -> None:
    """The point of the feature: one click, with your own number already in."""
    sheet = _sheet()  # Strength 4 + 5 ranks of Athletics
    spec = RollSpec(label="Athletics", kind=KIND_SKILL, trait_key="Athletics", dc=15)
    history = sheet.dice.view._session_history
    history.add_roll(
        {
            "seq": 1,
            "player_id": "someone-else",
            "player_name": "Ada",
            "die": 0,
            "kind": KIND_REQUEST,
            "label": "Athletics",
            "dc": 15,
            "spec": spec.to_dict(),
        }
    )

    card = history.cards()[0]
    assert isinstance(card, RequestCard)
    _roll_buttons(card)[0].click()

    assert sheet.dice.panel.current_spec().modifier == 9
    assert sheet.dice.panel._dc_spin.value() == 15


def test_the_asker_gets_the_button_too(qapp: QApplication, hosting: SessionBridge) -> None:
    """You are asking yourself as well — unlike a save, where you are not the target."""
    sheet = _sheet()
    sheet.sync_session()
    _pick(sheet.dice.panel, "Athletics")
    sheet.dice.panel._request_button.click()
    qapp.processEvents()

    card = sheet.dice.view._session_history.cards()[0]
    assert isinstance(card, RequestCard)
    assert _roll_buttons(card)[0].text() == "🎲 Athletics"


def test_a_request_landing_mid_tumble_does_not_settle_the_die(qapp: QApplication) -> None:
    """A request has no die, so taking one for the answer would show a zero."""
    panel = _sheet().dice.panel
    panel._awaiting = True
    panel._own_id = "me"

    panel._on_session_roll(
        {"seq": 1, "player_id": "me", "die": 0, "kind": KIND_REQUEST, "spec": {"label": "x"}}
    )
    assert panel._pending is None


def test_a_request_of_ones_own_is_not_deferred(qapp: QApplication) -> None:
    """Deferral waits on a die's tumble, and a request has none to wait for."""
    sheet = _sheet()
    history = sheet.dice.view._session_history
    history.set_defer_own(True)
    history._own_id = "me"

    history.add_roll(
        {
            "seq": 1,
            "player_id": "me",
            "die": 0,
            "kind": KIND_REQUEST,
            "spec": RollSpec(label="Perception").to_dict(),
        }
    )
    assert history.findChild(RequestCard) is not None


# -- the wire ----------------------------------------------------------------


def test_a_request_reaches_the_table_as_its_own_kind_of_entry(
    qapp: QApplication, hosting: SessionBridge
) -> None:
    sheet = _sheet()
    sheet.sync_session()
    sheet.dice.request_roll(RollSpec(label="Awareness", kind=KIND_ABILITY, trait_key="AWE", dc=15))
    qapp.processEvents()

    recorded = hosting.server.state.rolls[0]
    assert recorded.kind == KIND_REQUEST
    assert recorded.die == 0 and recorded.degree is None
    assert recorded.label == "Awareness"
    assert recorded.dc == 15
    assert recorded.spec["trait_key"] == "AWE"


def test_anyone_may_ask_not_only_the_gm(qapp: QApplication, hosting: SessionBridge) -> None:
    """Asking the table to roll is not a GM privilege, unlike striking a card."""
    server = hosting.server
    slot = server.state.add_player(display_name="Player", token="t")
    assert slot.is_gm is False

    record = server.prompt_roll({"label": "Perception"}, player_id=slot.player_id)
    assert record is not None
    assert record.kind == KIND_REQUEST
    assert record.player_name == "Player"


def test_a_request_with_nothing_to_roll_is_dropped(
    qapp: QApplication, hosting: SessionBridge
) -> None:
    """A card with a dead button on it is worse than no card at all."""
    assert hosting.server.prompt_roll(None) is None
    assert hosting.server.prompt_roll({"no": "label"}) is None
    assert hosting.server.state.rolls == []


def test_the_gm_can_strike_a_request(qapp: QApplication, hosting: SessionBridge) -> None:
    """It took a seq from the same counter, which is why it is a record and not a message."""
    record = hosting.server.prompt_roll({"label": "Perception"})
    assert hosting.server.remove_roll(record.seq) is True
    assert hosting.server.state.rolls == []

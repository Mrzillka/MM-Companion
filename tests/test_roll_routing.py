"""Rolling a stat from the sheet: the double-click, the routing, and the chain.

``test_roll_specs.py`` proves the numbers headlessly; this is about the wiring
that carries them — a stat block asking, the bus routing, the Dice block rolling,
and the roll reaching the table under a name.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QApplication, QLabel, QPushButton

from mm_companion.core import storage
from mm_companion.core.character import Character
from mm_companion.core.data_loader import load_game_data
from mm_companion.core.powers import Power, PowerEffectInstance
from mm_companion.core.rules import (
    PIN_ABILITY,
    PIN_INITIATIVE,
    PIN_POWER,
    PIN_RESISTANCE,
    PIN_SKILL,
    PinRef,
    RollSpec,
    ability_roll,
)
from mm_companion.core.session.model import new_session
from mm_companion.ui import dice_roller
from mm_companion.ui.character_sheet import CharacterSheet
from mm_companion.ui.sections.powers import _RollLine
from mm_companion.ui.sections.stat_table import COL_NAME, COL_TOTAL, ROLL_ROLE
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
def _instant_die(monkeypatch: pytest.MonkeyPatch) -> None:
    """No tumble and a known die, so a roll resolves inside the call that starts it."""
    monkeypatch.setattr(dice_roller, "ROLL_DURATION_MS", 0)
    monkeypatch.setattr(dice_roller, "roll_d20", lambda *a, **k: 11)


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
    char.abilities.update({"STR": 4, "STA": 5, "AGL": 3, "ATK": 7})
    char.skill_ranks["Athletics"] = 5
    return char


def _sheet() -> CharacterSheet:
    data = load_game_data()
    return CharacterSheet(data, _hero(data))


def _mouse(widget, kind: QMouseEvent.Type) -> None:
    """Send *kind* as a real left-button event at the widget's centre."""
    point = QPointF(widget.width() / 2, widget.height() / 2)
    event = QMouseEvent(
        kind,
        point,
        widget.mapToGlobal(point),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    QApplication.sendEvent(widget, event)


def _double_click(widget) -> None:
    """A real left double-click at the widget's centre."""
    _mouse(widget, QMouseEvent.Type.MouseButtonDblClick)


def _click(widget) -> None:
    """A real left press-and-release at the widget's centre."""
    _mouse(widget, QMouseEvent.Type.MouseButtonPress)
    _mouse(widget, QMouseEvent.Type.MouseButtonRelease)


def _release(widget) -> None:
    """The release that ends a plain left click — what the load path watches for."""
    _mouse(widget, QMouseEvent.Type.MouseButtonRelease)


# -- routing -----------------------------------------------------------------


def test_a_stat_blocks_request_reaches_the_dice_block(qapp: QApplication) -> None:
    sheet = _sheet()
    sheet.abilities.rollRequested.emit(ability_roll(sheet.character, sheet.abilities._data, "STR"))

    panel = sheet.dice.panel
    assert panel.current_spec().label == "Strength"
    assert "Strength" in panel._readout.text()


def test_the_request_travels_without_either_block_naming_the_other(qapp: QApplication) -> None:
    """The point of the payload channel: the wiring is the descriptors, not the sheet.

    Publishing the topic directly must roll, exactly as a section's own signal does.
    """
    from mm_companion.ui.blocks.bus import ROLL_REQUESTED

    sheet = _sheet()
    sheet.bus.publish_request(ROLL_REQUESTED, RollSpec(label="Improvised", modifier=3))

    assert sheet.dice.panel.current_spec().label == "Improvised"


def test_a_bad_payload_costs_a_roll_not_the_sheet(qapp: QApplication) -> None:
    sheet = _sheet()
    sheet.abilities.rollRequested.emit("not a spec")  # must not raise
    assert sheet.dice.panel.current_spec() is None


def test_a_closed_dice_block_is_reopened_rather_than_rolling_unseen(qapp: QApplication) -> None:
    sheet = _sheet()
    sheet.hide_block("dice")
    assert sheet.is_block_hidden("dice")

    sheet.abilities.rollRequested.emit(RollSpec(label="Strength", modifier=4))

    assert not sheet.is_block_hidden("dice")
    assert sheet.dice.panel.current_spec().label == "Strength"


# -- one click loads, two roll -----------------------------------------------


def test_a_load_request_shows_the_spec_without_throwing_the_die(qapp: QApplication) -> None:
    sheet = _sheet()
    panel = sheet.dice.panel

    sheet.abilities.loadRequested.emit(RollSpec(label="Strength", modifier=4))

    assert panel.current_spec().label == "Strength"
    # The chip is there, but nothing was rolled — the readout still says nothing.
    assert "Strength" not in panel._readout.text()


def test_a_closed_dice_block_is_reopened_for_a_load_too(qapp: QApplication) -> None:
    """Loading into a hidden block would read as the app ignoring the click.

    Which is exactly what revealing the server exists to prevent, so `load-requested`
    is deliberately not one of the quiet topics.
    """
    sheet = _sheet()
    sheet.hide_block("dice")

    sheet.abilities.loadRequested.emit(RollSpec(label="Strength", modifier=4))

    assert not sheet.is_block_hidden("dice")


def test_clicking_a_stat_row_loads_it_and_double_clicking_rolls_it(qapp: QApplication) -> None:
    sheet = _sheet()
    panel = sheet.dice.panel
    table = sheet.abilities.table
    row = _stat_row(table, "STR")

    table.cellClicked.emit(row, COL_NAME)
    assert panel.current_spec().label == "Strength"
    assert "Strength" not in panel._readout.text()  # loaded, not rolled

    # A real double-click fires the single first; that is harmless, since rolling
    # loads the same spec anyway.
    table.cellClicked.emit(row, COL_NAME)
    table.cellDoubleClicked.emit(row, COL_NAME)
    assert "Strength" in panel._readout.text()


def test_clicking_a_skill_row_loads_it(qapp: QApplication) -> None:
    sheet = _sheet()
    seen: list[RollSpec] = []
    sheet.skills.loadRequested.connect(seen.append)

    table = sheet.skills._tables[0]
    row = next(
        r
        for r in range(table.rowCount())
        if (table.item(r, 5) or None) and table.item(r, 5).data(ROLL_ROLE)
    )
    table.cellClicked.emit(row, 5)

    assert len(seen) == 1
    assert seen[0].kind == "skill"


def test_clicking_the_separator_row_loads_nothing(qapp: QApplication) -> None:
    sheet = _sheet()
    seen: list[RollSpec] = []
    sheet.abilities.loadRequested.connect(seen.append)

    table = sheet.abilities.table
    separator = next(row for row in range(table.rowCount()) if table.item(row, COL_TOTAL) is None)
    table.cellClicked.emit(separator, COL_NAME)

    assert seen == []


def test_clicking_initiative_loads_it_and_double_clicking_rolls_it(qapp: QApplication) -> None:
    """The one rollable readout that is not in a table, so it goes through roll_click."""
    sheet = _sheet()
    loaded: list[RollSpec] = []
    rolled: list[RollSpec] = []
    sheet.system_info.loadRequested.connect(loaded.append)
    sheet.system_info.rollRequested.connect(rolled.append)

    _release(sheet.system_info._initiative)
    assert [s.label for s in loaded] == ["Initiative"]
    assert rolled == []

    _double_click(sheet.system_info._initiative)
    assert [s.label for s in rolled] == ["Initiative"]


def test_loading_a_trait_is_not_an_edit(qapp: QApplication) -> None:
    """Same promise rolling makes: reading a number off the sheet changes nothing."""
    sheet = _sheet()
    dirty: list[int] = []
    sheet.edited.connect(lambda: dirty.append(1))

    table = sheet.abilities.table
    table.cellClicked.emit(_stat_row(table, "STR"), COL_NAME)

    assert dirty == []


# -- the double-click itself --------------------------------------------------


def _stat_row(table, key: str) -> int:
    """The row of *table* whose Total cell carries *key* as its roll payload."""
    return next(
        row
        for row in range(table.rowCount())
        if (table.item(row, COL_TOTAL) or None)
        and table.item(row, COL_TOTAL).data(ROLL_ROLE) == key
    )


def test_double_clicking_an_ability_row_rolls_it(qapp: QApplication) -> None:
    sheet = _sheet()
    seen: list[RollSpec] = []
    sheet.abilities.rollRequested.connect(seen.append)

    table = sheet.abilities.table
    table.cellDoubleClicked.emit(_stat_row(table, "STR"), COL_NAME)

    assert [s.label for s in seen] == ["Strength"]


def test_double_clicking_a_resistance_row_rolls_it(qapp: QApplication) -> None:
    sheet = _sheet()
    seen: list[RollSpec] = []
    sheet.resistances.rollRequested.connect(seen.append)

    table = sheet.resistances.table
    table.cellDoubleClicked.emit(_stat_row(table, "TOUGHNESS"), COL_NAME)

    assert [s.kind for s in seen] == ["resistance"]


def test_the_derived_separator_row_is_not_rollable(qapp: QApplication) -> None:
    """It is a rule drawn across the table, not a trait — a double-click there is nothing."""
    sheet = _sheet()
    seen: list[RollSpec] = []
    sheet.abilities.rollRequested.connect(seen.append)

    table = sheet.abilities.table
    separator = next(row for row in range(table.rowCount()) if table.item(row, COL_TOTAL) is None)
    table.cellDoubleClicked.emit(separator, COL_NAME)

    assert seen == []


def test_the_rank_spin_box_keeps_its_own_double_click(qapp: QApplication) -> None:
    """Unlocked, a double-click in a spin box selects the number for retyping.

    Stealing that would make editing hostile. In a table the spin box is a cell
    widget, so it consumes the double-click itself and the table never hears it —
    which is the same bargain the Skills block has always struck.
    """
    sheet = _sheet()
    seen: list[RollSpec] = []
    sheet.abilities.rollRequested.connect(seen.append)
    spin = sheet.abilities._abilities["STR"]

    sheet.set_locked(False)
    _double_click(spin.lineEdit())

    assert seen == []


def test_double_clicking_a_skill_row_rolls_that_row(qapp: QApplication) -> None:
    sheet = _sheet()
    seen: list[RollSpec] = []
    sheet.skills.rollRequested.connect(seen.append)

    table = sheet.skills._tables[0]
    row = next(
        r
        for r in range(table.rowCount())
        if (table.item(r, 5) or None) and table.item(r, 5).data(Qt.ItemDataRole.UserRole)
    )
    table.cellDoubleClicked.emit(row, 5)

    assert len(seen) == 1
    assert seen[0].kind == "skill"


def test_double_clicking_initiative_rolls_it(qapp: QApplication) -> None:
    sheet = _sheet()
    seen: list[RollSpec] = []
    sheet.system_info.rollRequested.connect(seen.append)

    _double_click(sheet.system_info._initiative)

    assert [s.label for s in seen] == ["Initiative"]


def _roll_lines(sheet: CharacterSheet) -> list[_RollLine]:
    return sheet.powers.findChildren(_RollLine)


def test_a_power_card_rolls_from_the_whole_line_and_never_toggles_it(
    qapp: QApplication,
) -> None:
    """The card body is the power's on/off switch, so a roll must not reach it.

    The line consumes its own press — the same mechanism the ✎/✕/grip rely on, and
    the reason it is a frame that accepts the event rather than a clickable label,
    which would let the press through to the card.
    """
    data = load_game_data()
    char = _hero(data)
    char.powers.append(Power(name="Blast", effects=[PowerEffectInstance("damage", rank=8)]))
    sheet = CharacterSheet(data, char)
    power = char.powers[0]
    power.activated = True

    seen: list[RollSpec] = []
    sheet.powers.rollRequested.connect(seen.append)
    dirty: list[int] = []
    sheet.edited.connect(lambda: dirty.append(1))

    # Only the attack is rollable here: the wielder never rolls their own target's
    # save, so that line is written down inert (it arrives as the follow-up chip).
    rollable = [line for line in _roll_lines(sheet) if line.is_rollable()]
    assert len(rollable) == 1
    _click(rollable[0])

    assert seen[0].modifier == 7
    assert dirty == []  # a roll is not an edit
    assert power.activated is True  # and it did not flip the switch underneath


def test_the_resistance_line_is_written_down_but_stays_inert(qapp: QApplication) -> None:
    data = load_game_data()
    char = _hero(data)
    char.powers.append(Power(name="Blast", effects=[PowerEffectInstance("damage", rank=8)]))
    sheet = CharacterSheet(data, char)

    texts = [lb.text() for lb in sheet.powers.findChildren(QLabel)]
    assert any(t.startswith("Toughness vs. ") for t in texts)

    inert = [line for line in _roll_lines(sheet) if not line.is_rollable()]
    assert len(inert) == 1
    # Its press bubbles, so the card under it keeps working as the power's switch.
    assert inert[0].cursor().shape() is Qt.CursorShape.ArrowCursor


# -- the sliders, the DC, and the wire ---------------------------------------


def test_the_sliders_add_on_top_of_the_loaded_trait(qapp: QApplication) -> None:
    sheet = _sheet()
    panel = sheet.dice.panel
    panel._bonus_spin.setValue(2)
    panel._penalty_spin.setValue(5)

    panel.load_spec(RollSpec(label="Athletics", modifier=9))

    # 9 + 2 - 5 = 6, split back into a non-negative bonus/penalty pair for the wire.
    assert panel._roll_parameters()[:2] == (6, 0)


def test_a_net_negative_modifier_becomes_a_penalty(qapp: QApplication) -> None:
    sheet = _sheet()
    panel = sheet.dice.panel
    panel._penalty_spin.setValue(8)
    panel.load_spec(RollSpec(label="Stealth", modifier=3))

    assert panel._roll_parameters()[:2] == (0, 5)


def test_a_specs_own_dc_wins_and_shows_itself_in_the_box(qapp: QApplication) -> None:
    sheet = _sheet()
    panel = sheet.dice.panel
    panel._dc_check.setChecked(True)
    panel._dc_spin.setValue(15)

    panel.load_spec(RollSpec(label="Toughness vs. 18", dc=18))

    assert panel._dc_spin.value() == 18
    assert panel._roll_parameters()[2] == 18


def test_a_trait_check_uses_the_dc_box_because_it_has_none_of_its_own(
    qapp: QApplication,
) -> None:
    sheet = _sheet()
    panel = sheet.dice.panel
    panel.load_spec(RollSpec(label="Athletics", modifier=9))
    assert panel._roll_parameters()[2] is None

    panel._dc_check.setChecked(True)
    panel._dc_spin.setValue(15)
    assert panel._roll_parameters()[2] == 15


def test_the_chip_stays_so_the_same_trait_can_be_rolled_again(qapp: QApplication) -> None:
    sheet = _sheet()
    panel = sheet.dice.panel
    panel.roll_spec(RollSpec(label="Athletics", modifier=9))
    panel._finish_roll()

    assert panel.current_spec().label == "Athletics"
    assert panel._spec_label.text() == "Athletics +9"

    # And clearing it puts the panel back to a plain manual roll.
    panel.load_spec(None)
    assert panel.current_spec() is None
    assert panel._roll_parameters()[:2] == (0, 0)


def test_the_table_is_told_what_was_rolled(qapp: QApplication, hosting: SessionBridge) -> None:
    sheet = _sheet()
    sheet.sync_session()
    sheet.abilities.rollRequested.emit(RollSpec(label="Strength", modifier=4))
    qapp.processEvents()

    recorded = hosting.server.state.rolls
    assert len(recorded) == 1
    assert recorded[0].label == "Strength"
    assert recorded[0].bonus == 4


# -- the chain ----------------------------------------------------------------


def _chain_buttons(card) -> list[QPushButton]:
    return [b for b in card.findChildren(QPushButton) if b.text().startswith("🎲")]


def test_a_hit_offers_the_save_it_forced(qapp: QApplication) -> None:
    data = load_game_data()
    char = _hero(data)
    char.powers.append(Power(name="Blast", effects=[PowerEffectInstance("damage", rank=8)]))
    sheet = CharacterSheet(data, char)
    panel = sheet.dice.panel
    history = sheet.dice.view._local_history

    attack = sheet.powers._rolls(char.powers[0])[0]
    panel.load_spec(attack)
    panel._dc_check.setChecked(True)
    panel._dc_spin.setValue(12)  # the target's Defense
    panel._start_roll()
    panel._finish_roll()

    chain = _chain_buttons(history.cards()[0])
    assert len(chain) == 1
    assert chain[0].text() == "🎲 Toughness vs. 18"

    # Pressing it primes the save with its own DC already filled in.
    chain[0].click()
    assert panel.current_spec().label == "Toughness vs. 18"
    assert panel._dc_spin.value() == 18


def test_a_failed_save_says_what_it_did_to_the_target(qapp: QApplication) -> None:
    sheet = _sheet()
    panel = sheet.dice.panel
    history = sheet.dice.view._local_history

    panel.load_spec(
        RollSpec(label="Toughness vs. 18", dc=18, outcomes=("Dazed", "Staggered", "Incapacitated"))
    )
    panel._start_roll()
    panel._finish_roll()  # d20 11 vs DC 18 — two degrees of failure

    lines = [lb.text() for lb in history.cards()[0].findChildren(QLabel)]
    assert "Staggered!" in lines


def test_a_save_that_held_still_reports_the_hit_it_cost(qapp: QApplication) -> None:
    """A made Toughness save is not "nothing happened" — the target still takes a Hit.

    The commonest result of an attack is the one the app used to go silent on.
    """
    sheet = _sheet()
    panel = sheet.dice.panel
    history = sheet.dice.view._local_history

    panel._bonus_spin.setValue(20)  # cannot fail
    panel.load_spec(
        RollSpec(
            label="Toughness vs. 18",
            dc=18,
            outcomes=("Dazed",),
            success_outcome="Hit (unless Impervious)",
        )
    )
    panel._start_roll()
    panel._finish_roll()

    lines = [lb.text() for lb in history.cards()[0].findChildren(QLabel)]
    assert "Hit (unless Impervious)!" in lines


def test_a_save_with_nothing_to_say_on_a_success_says_nothing(qapp: QApplication) -> None:
    sheet = _sheet()
    panel = sheet.dice.panel
    history = sheet.dice.view._local_history

    panel._bonus_spin.setValue(20)  # cannot fail
    panel.load_spec(RollSpec(label="Will vs. 16", dc=16, outcomes=("Dazed",)))
    panel._start_roll()
    panel._finish_roll()

    lines = [lb.text() for lb in history.cards()[0].findChildren(QLabel)]
    assert not any(line.endswith("!") for line in lines)


# -- criticals ----------------------------------------------------------------


def _attack_with_die(sheet: CharacterSheet, die: int, *, dc: int = 12):
    """Roll the sheet's first power's attack on a given die, and return its card."""
    panel = sheet.dice.panel
    panel.load_spec(sheet.powers._rolls(sheet.character.powers[0])[0])
    panel._dc_check.setChecked(True)
    panel._dc_spin.setValue(dc)
    dice_roller.roll_d20 = lambda *a, **k: die
    panel._start_roll()
    panel._finish_roll()
    return sheet.dice.view._local_history.cards()[0]


def _blasting_sheet() -> CharacterSheet:
    data = load_game_data()
    char = _hero(data)
    char.powers.append(Power(name="Blast", effects=[PowerEffectInstance("damage", rank=8)]))
    return CharacterSheet(data, char)


def test_a_natural_20_raises_the_dc_of_the_save_it_forces(qapp: QApplication) -> None:
    sheet = _blasting_sheet()
    chain = _chain_buttons(_attack_with_die(sheet, 20))

    # 18 + the system's critical_effect_bonus, and the chip says why.
    assert chain[0].text() == "🎲 Toughness vs. 18 — critical hit, DC 23"
    chain[0].click()
    assert sheet.dice.panel._dc_spin.value() == 23


def test_a_natural_1_that_still_hits_helps_the_target_resist(qapp: QApplication) -> None:
    # A natural 1 costs a degree of its own, so the attack has to beat the Defense by
    # two to survive it: 1 + 7 = 8 against a Defense of 3 is two degrees, then one.
    sheet = _blasting_sheet()
    chain = _chain_buttons(_attack_with_die(sheet, 1, dc=3))

    assert chain[0].text() == "🎲 Toughness vs. 18 — natural 1, +5 to resist"
    chain[0].click()
    assert sheet.dice.panel.current_spec().modifier == 5
    assert sheet.dice.panel._dc_spin.value() == 18  # the DC is untouched


def test_a_natural_1_that_misses_forces_no_save_at_all(qapp: QApplication) -> None:
    sheet = _blasting_sheet()
    assert _chain_buttons(_attack_with_die(sheet, 1, dc=40)) == []


# -- the chain across the table ----------------------------------------------


def test_the_spec_travels_with_the_roll(qapp: QApplication, hosting: SessionBridge) -> None:
    """The chain is worthless if only the roller can see it — so it goes on the wire."""
    sheet = _blasting_sheet()
    sheet.sync_session()
    attack = sheet.powers._rolls(sheet.character.powers[0])[0]
    sheet.powers.rollRequested.emit(attack)
    qapp.processEvents()

    recorded = hosting.server.state.rolls[0]
    assert recorded.spec is not None
    assert recorded.spec["follow_up"]["dc"] == 18
    assert recorded.spec["follow_up"]["trait_key"] == "TOUGHNESS"
    assert recorded.spec["follow_up"]["outcomes"][0].startswith("Hit + Dazed")


def test_another_players_card_offers_the_save_with_their_own_toughness(
    qapp: QApplication,
) -> None:
    """The whole point: the attack lands on *my* screen and I click to resist it.

    A card built from somebody else's roll carries the chip, and rolling it fills in
    this sheet's Toughness — so the target's player answers with one click.
    """
    sheet = _sheet()  # Stamina 5, so Toughness 5
    save = RollSpec(label="Toughness vs. 18", dc=18, trait_key="TOUGHNESS", rolled_by_target=True)
    theirs = {
        "seq": 1,
        "player_id": "someone-else",
        "player_name": "Ada",
        "die": 14,
        "bonus": 7,
        "dc": 12,
        "degree": 2,
        "label": "7 vs. Defense",
        "spec": RollSpec(label="7 vs. Defense", modifier=7, follow_up=save).to_dict(),
    }
    history = sheet.dice.view._session_history
    history.add_roll(theirs)

    chain = _chain_buttons(history.cards()[0])
    assert chain[0].text() == "🎲 Toughness vs. 18"

    chain[0].click()
    assert sheet.dice.panel.current_spec().modifier == 5
    assert sheet.dice.panel._dc_spin.value() == 18


def test_your_own_card_does_not_fill_in_your_own_toughness(qapp: QApplication) -> None:
    """You are not the target of your own attack.

    The chip is still there — a GM running both sides needs it — but quietly using
    the attacker's Toughness for the defender would be a confident wrong number.
    """
    sheet = _sheet()
    save = RollSpec(label="Toughness vs. 18", dc=18, trait_key="TOUGHNESS", rolled_by_target=True)
    mine = {
        "seq": 1,
        "player_id": "me",
        "player_name": "Me",
        "die": 14,
        "bonus": 7,
        "dc": 12,
        "degree": 2,
        "spec": RollSpec(label="7 vs. Defense", modifier=7, follow_up=save).to_dict(),
    }
    history = sheet.dice.view._session_history
    history._own_id = "me"
    # One's own roll is held until the die settles, then released — the real path.
    history.release_roll(mine)

    _chain_buttons(history.cards()[0])[0].click()
    assert sheet.dice.panel.current_spec().modifier == 0


# -- pinning a row to a GM card ----------------------------------------------


def test_a_sheet_offers_no_pinning_until_it_is_told_it_has_a_card(qapp: QApplication) -> None:
    """A player's own sheet is unchanged: the action would have nowhere to go."""
    sheet = _sheet()

    assert sheet.abilities._pins.enabled is False
    assert sheet.skills._pins.enabled is False
    assert sheet.system_info._pins.enabled is False
    assert sheet.powers._pins.enabled is False

    sheet.set_pin_target(True)

    assert sheet.abilities._pins.enabled is True
    assert sheet.powers._pins.enabled is True


def test_a_pin_request_leaves_the_sheet_rather_than_finding_a_block(qapp: QApplication) -> None:
    """The one request no block answers — a card is outside the sheet entirely."""
    sheet = _sheet()
    seen: list[object] = []
    sheet.pinRequested.connect(seen.append)

    sheet.abilities.pinRequested.emit(PinRef(PIN_ABILITY, "STR"))

    assert seen == [PinRef(PIN_ABILITY, "STR")]


def test_every_pinnable_block_reaches_the_sheet_the_same_way(qapp: QApplication) -> None:
    sheet = _sheet()
    seen: list[object] = []
    sheet.pinRequested.connect(seen.append)

    sheet.abilities.pinRequested.emit(PinRef(PIN_ABILITY, "STR"))
    sheet.resistances.pinRequested.emit(PinRef(PIN_RESISTANCE, "DEF"))
    sheet.skills.pinRequested.emit(PinRef(PIN_SKILL, "Perception"))
    sheet.system_info.pinRequested.emit(PinRef(PIN_INITIATIVE))
    sheet.powers.pinRequested.emit(PinRef(PIN_POWER, "abc", 1))

    assert [ref.kind for ref in seen] == [
        PIN_ABILITY,
        PIN_RESISTANCE,
        PIN_SKILL,
        PIN_INITIATIVE,
        PIN_POWER,
    ]


def test_pinning_does_not_open_a_block_the_way_a_roll_does(qapp: QApplication) -> None:
    """A roll reveals whatever serves it; a pin has no server to reveal.

    Listed in QUIET_REQUESTS for that reason — hunting for a block would be the
    sheet answering a question about itself.
    """
    from mm_companion.ui.blocks.bus import PIN_REQUESTED, QUIET_REQUESTS

    assert PIN_REQUESTED in QUIET_REQUESTS

    sheet = _sheet()
    sheet.hide_block("dice")
    sheet.set_pin_target(True)
    sheet.abilities.pinRequested.emit(PinRef(PIN_ABILITY, "STR"))

    assert sheet.is_block_hidden("dice") is True


def test_a_pinned_row_names_the_same_trait_the_roll_does(qapp: QApplication) -> None:
    """Both read the one payload the row stashed, so they cannot disagree."""
    sheet = _sheet()
    sheet.set_pin_target(True)
    table = sheet.abilities.table
    row = _stat_row(table, "STR")

    key = table.item(row, COL_TOTAL).data(ROLL_ROLE)

    assert sheet.abilities._pin_ref(key) == PinRef(PIN_ABILITY, "STR")
    assert sheet.abilities._roll_spec(key).label == "Strength"


def test_a_pinned_skill_row_keeps_its_focus_apart_from_its_parent(qapp: QApplication) -> None:
    sheet = _sheet()

    assert sheet.skills._pin_ref(("Expertise::Law", "Expertise: Law")) == PinRef(
        PIN_SKILL, "Expertise::Law"
    )
    # A group header stashes nothing, so there is nothing to pin.
    assert sheet.skills._pin_ref(None) is None

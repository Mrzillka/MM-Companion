"""The four attack maneuvers on the roller, end to end on a player's sheet (combat
maneuvers, phase 5): the tile starts a line, the roll is made of the shifted attack, and
what the maneuver leaves behind lands on the sheet once the die has settled."""

from __future__ import annotations

import pytest

from mm_companion.core.character import Character
from mm_companion.core.powers import ModifierSelection, Power, PowerEffectInstance
from mm_companion.core.rules import attack_choices
from mm_companion.ui.action_menu import NO_ATTACKS
from mm_companion.ui.attack_targets import AttackDeclaration, SceneTarget
from mm_companion.ui.character_sheet import CharacterSheet
from mm_companion.ui.roller.action_lane import NEEDS_ATTACK
from mm_companion.ui.sections import dice as dice_section
from mm_companion.ui.sections.dice import DiceSection

pytestmark = pytest.mark.usefixtures("workspace")

ACCURATE, ALL_OUT, DEFENSIVE, POWER = (
    "accurate_attack",
    "all_out_attack",
    "defensive_attack",
    "power_attack",
)


class _Menu:
    """Stands in for ``action_menu``'s ``QMenu`` (see ``test_action_lane.py``): picks the
    first entry, or dismisses with :attr:`pick` ``None``."""

    pick: int | None = 0
    shown: list[list[str]] = []

    def __init__(self, _parent=None) -> None:
        self.entries: list[_Entry] = []
        _Menu.shown.append([])

    def addAction(self, text: str) -> _Entry:  # noqa: N802 - mirrors Qt
        entry = _Entry(text)
        self.entries.append(entry)
        _Menu.shown[-1].append(text)
        return entry

    def exec(self, *_a):
        return None if _Menu.pick is None else self.entries[_Menu.pick]


class _Entry:
    def __init__(self, text: str) -> None:
        self.text = text
        self._data = None

    def setData(self, value) -> None:  # noqa: N802 - mirrors Qt
        self._data = value

    def data(self):
        return self._data

    def setEnabled(self, _on: bool) -> None:  # noqa: N802 - mirrors Qt
        pass


@pytest.fixture
def menu(monkeypatch):
    _Menu.pick, _Menu.shown = 0, []
    monkeypatch.setattr("mm_companion.ui.action_menu.QMenu", _Menu)
    return _Menu


def _hero(data, *, attack: int = 6, defense: int = 4) -> Character:
    hero = Character.new_default(data)
    hero.abilities["ATK"] = attack
    hero.resistances["DEF"] = defense
    damage = PowerEffectInstance("damage", rank=8)
    damage.extras.append(ModifierSelection("ranged", config={"from": "close"}))
    hero.powers = [Power(name="Blast", effects=[damage])]
    return hero


def _attack(section: DiceSection):
    (choice,) = attack_choices(section._character, section._data)
    return choice.spec


def _rolls(panel) -> list[dict]:
    rolls: list[dict] = []
    panel.localRoll.connect(rolls.append)
    return rolls


# -- the tiles -------------------------------------------------------------------------


@pytest.mark.parametrize("action", [ACCURATE, ALL_OUT, DEFENSIVE, POWER])
def test_the_maneuver_tiles_are_live_with_an_attack_to_make(qapp, data, action) -> None:
    section = DiceSection(data, _hero(data))
    assert section.panel.open_actions().tile(action).isEnabled()


def test_the_maneuver_tiles_are_greyed_with_no_attack(qapp, data) -> None:
    section = DiceSection(data, Character.new_default(data))
    tile = section.panel.open_actions().tile(ALL_OUT)
    assert not tile.isEnabled() and NO_ATTACKS in tile.toolTip()


def test_a_tile_with_no_attack_loaded_asks_for_one_and_starts_at_the_maximum(
    qapp, data, menu
) -> None:
    section = DiceSection(data, _hero(data, attack=6, defense=4))
    section.panel.open_actions().tile(ALL_OUT).click()
    assert len(_Menu.shown) == 1  # the attack picker
    (line,) = section.panel.action_lines()
    assert section.panel.loaded_attack() is not None
    # min(5, Defense 4, attack +6 doubled) = 4, and the slider starts there.
    assert (line.key, line.maximum(), line.value()) == (ALL_OUT, 4, 4)
    assert line.available


def test_dismissing_the_picker_calls_the_maneuver_off(qapp, data, menu) -> None:
    section = DiceSection(data, _hero(data))
    _Menu.pick = None
    assert section.start_maneuver(ALL_OUT) is None
    assert section.panel.action_lines() == []


def test_unloading_the_attack_greys_the_maneuver(qapp, data) -> None:
    section = DiceSection(data, _hero(data))
    section.panel.load_spec(_attack(section))
    line = section.start_maneuver(POWER)
    section.panel.load_spec(None)
    assert not line.available and line.reason == NEEDS_ATTACK


def test_two_maneuvers_share_the_room_to_double_the_attack(qapp, data) -> None:
    section = DiceSection(data, _hero(data, attack=4, defense=9))
    section.panel.load_spec(_attack(section))
    accurate = section.start_maneuver(ACCURATE)
    assert accurate.value() == 4  # min(5, +4 doubled, rank 8)
    all_out = section.start_maneuver(ALL_OUT)
    # Accurate already doubled the +4: nothing left for All-Out.
    assert all_out.maximum() == 0 and not all_out.available
    accurate.set_value(1)
    # Dropping Accurate to 1 frees three points, and All-Out's range follows.
    assert all_out.maximum() == 3 and all_out.available
    all_out.set_value(3)
    # ...and Accurate is now held to the 1 it has.
    assert accurate.maximum() == 1


# -- rolling ---------------------------------------------------------------------------


def test_a_roll_is_made_of_the_shifted_attack_and_spends_the_line(qapp, data, instant_die) -> None:
    section = DiceSection(data, _hero(data))
    panel = section.panel
    panel.load_spec(_attack(section))
    section.start_maneuver(ALL_OUT).set_value(3)
    landed: list = []
    section.conditionsRequested.connect(landed.append)
    rolls = _rolls(panel)
    panel._start_roll()
    (roll,) = rolls
    assert roll["bonus"] == 9  # +6, and +3 for the maneuver
    assert roll["spec"].label.endswith("(All-Out Attack +3)")
    assert panel.action_lines() == []  # spent by the roll
    assert landed == [(("all_out_attack", 3),)]
    # The chip keeps the attack as loaded, so the next roll starts from it again.
    assert panel.current_spec().modifier == 6


def test_power_attack_moves_the_save_the_roll_carries(qapp, data, instant_die) -> None:
    section = DiceSection(data, _hero(data))
    panel = section.panel
    panel.load_spec(_attack(section))
    section.start_maneuver(POWER).set_value(2)
    rolls = _rolls(panel)
    landed: list = []
    section.conditionsRequested.connect(landed.append)
    panel._start_roll()
    (roll,) = rolls
    assert roll["bonus"] == 4
    assert roll["spec"].follow_up.dc == 20
    assert roll["spec"].follow_up.ladder.rank == 10
    assert landed == []  # Power Attack leaves nothing behind


def test_a_cards_die_takes_the_maneuver_on_the_lane(qapp, data, instant_die) -> None:
    section = DiceSection(data, _hero(data))
    panel = section.panel
    attack = _attack(section)
    panel.load_spec(attack)
    section.start_maneuver(DEFENSIVE).set_value(2)
    rolls = _rolls(panel)
    section.perform_roll(attack)  # a card's 🎲 for the same attack
    assert rolls[0]["bonus"] == 4


def test_an_abandoned_roll_lands_nothing_and_keeps_the_line(qapp, data) -> None:
    section = DiceSection(data, _hero(data))
    panel = section.panel
    panel.load_spec(_attack(section))
    section.start_maneuver(ALL_OUT)
    landed: list = []
    section.conditionsRequested.connect(landed.append)
    panel._rolled_lines = tuple(panel.active_lines())
    panel._rolling = True
    panel._abandon_roll("No answer")
    assert landed == []
    assert [line.key for line in panel.action_lines()] == [ALL_OUT]


def test_a_reroll_throws_the_maneuver_again_but_lands_nothing(qapp, data, instant_die) -> None:
    section = DiceSection(data, _hero(data))
    panel = section.panel
    panel.load_spec(_attack(section))
    section.start_maneuver(ALL_OUT).set_value(2)
    landed: list = []
    section.conditionsRequested.connect(landed.append)
    rolls = _rolls(panel)
    panel._start_roll()
    assert panel.reroll()
    assert [r["bonus"] for r in rolls] == [8, 8]
    assert rolls[1]["reroll"]
    assert landed == [(("all_out_attack", 2),)]  # once, for the first roll


def test_on_the_sheet_the_condition_lands_temporary(qapp, data, instant_die) -> None:
    hero = _hero(data)
    sheet = CharacterSheet(data, hero)
    panel = sheet.dice.panel
    panel.load_spec(_attack(sheet.dice))
    sheet.dice.start_maneuver(ALL_OUT).set_value(3)
    panel._start_roll()
    (applied,) = [c for c in hero.conditions if c.condition_id == "all_out_attack"]
    assert applied.amount == 3 and applied.temporary
    # Re-rolling with All-Out again replaces the amount: one All-Out a turn.
    panel.load_spec(_attack(sheet.dice))
    sheet.dice.start_maneuver(ALL_OUT).set_value(1)
    panel._start_roll()
    (applied,) = [c for c in hero.conditions if c.condition_id == "all_out_attack"]
    assert applied.amount == 1


def test_defensive_attack_lands_and_lowers_the_next_attack(qapp, data, instant_die) -> None:
    hero = _hero(data)
    sheet = CharacterSheet(data, hero)
    panel = sheet.dice.panel
    panel.load_spec(_attack(sheet.dice))
    sheet.dice.start_maneuver(DEFENSIVE).set_value(2)
    panel._start_roll()
    assert any(c.condition_id == "defensive_attack" for c in hero.conditions)
    # Its attack-check penalty lasts: the next attack line is built two lower.
    assert _attack(sheet.dice).modifier == 4


# -- aimed attacks ---------------------------------------------------------------------


def test_an_aimed_attack_declares_the_shifted_spec(qapp, data, instant_die, monkeypatch) -> None:
    sent: list = []

    class _Bridge:
        def send_action_request(self, kind, action_id, payload):
            sent.append(payload)
            return True

    monkeypatch.setattr(dice_section, "live_session", lambda: _Bridge())
    section = DiceSection(data, _hero(data))
    panel = section.panel
    attack = _attack(section)
    panel.load_spec(attack)
    section.start_maneuver(POWER).set_value(2)
    rolls = _rolls(panel)
    section.aim_attack((attack, SceneTarget("r2", "Thug")))
    (payload,) = sent
    declared = AttackDeclaration.from_dict(payload).spec
    assert declared.modifier == 4 and declared.follow_up.dc == 20
    assert declared.targets == ("Thug",)
    # The die rolled is the one declared, and the line was spent on it.
    assert rolls[0]["bonus"] == 4
    assert panel.action_lines() == []


def test_a_declaration_that_fails_rolls_nothing(qapp, data, monkeypatch) -> None:
    class _Bridge:
        def send_action_request(self, kind, action_id, payload):
            return False

    monkeypatch.setattr(dice_section, "live_session", lambda: _Bridge())
    monkeypatch.setattr(dice_section, "notify_window", lambda *a, **k: None)
    section = DiceSection(data, _hero(data))
    panel = section.panel
    attack = _attack(section)
    panel.load_spec(attack)
    section.start_maneuver(POWER)
    section.aim_attack((attack, SceneTarget("r2", "Thug")))
    assert not panel.rolling
    assert [line.key for line in panel.action_lines()] == [POWER]
    assert panel.current_spec() == attack  # the chip as it was

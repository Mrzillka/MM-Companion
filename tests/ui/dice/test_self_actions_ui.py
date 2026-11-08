"""The self actions on the roller, on a player's sheet (combat maneuvers, phase 6): the
tiles, the lines that ride on the next roll (Aim, Charge, Slam, Finishing Attack), and
the actions that change conditions or load a check at once (Defend, Drop Prone, Stand,
Recover, Catch)."""

from __future__ import annotations

import pytest

from mm_companion.core.character import Character
from mm_companion.core.powers import Power, PowerEffectInstance
from mm_companion.core.rules import apply_condition, attack_choices
from mm_companion.ui.action_menu import NO_ATTACKS
from mm_companion.ui.character_sheet import CharacterSheet
from mm_companion.ui.sections.dice import RECOVER_DEFENSE_ONLY, DiceSection

pytestmark = pytest.mark.usefixtures("workspace")


class _Menu:
    """Stands in for ``action_menu``'s ``QMenu``: picks entry :attr:`pick` (``None``
    dismisses) and remembers what each menu offered."""

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


def _hero(data, *, attack: int = 6, stamina: int = 4) -> Character:
    hero = Character.new_default(data)
    hero.abilities["ATK"] = attack
    hero.abilities["STA"] = stamina
    hero.powers = [Power(name="Punch", effects=[PowerEffectInstance("damage", rank=8)])]
    return hero


def _loaded(data, hero=None) -> DiceSection:
    section = DiceSection(data, hero or _hero(data))
    (choice,) = attack_choices(section._character, data)
    section.panel.load_spec(choice.spec)
    return section


def _rolls(panel) -> list[dict]:
    rolls: list[dict] = []
    panel.localRoll.connect(rolls.append)
    return rolls


# -- the tiles -------------------------------------------------------------------------


@pytest.mark.parametrize("action", ["aim", "charge", "finishing_attack"])
def test_the_attack_actions_need_an_attack(qapp, data, action) -> None:
    popup = DiceSection(data, Character.new_default(data)).panel.open_actions()
    tile = popup.tile(action)
    assert not tile.isEnabled() and NO_ATTACKS in tile.toolTip()
    assert DiceSection(data, _hero(data)).panel.open_actions().tile(action).isEnabled()


@pytest.mark.parametrize("action", ["defend", "recover", "catch", "drop_prone"])
def test_the_actions_anyone_can_take_are_live(qapp, data, action) -> None:
    popup = DiceSection(data, Character.new_default(data)).panel.open_actions()
    assert popup.tile(action).isEnabled()


def test_stand_is_live_only_while_prone(qapp, data) -> None:
    hero = _hero(data)
    section = DiceSection(data, hero)
    assert not section.panel.open_actions().tile("stand").isEnabled()
    apply_condition(hero, "prone", data)
    assert section.panel.open_actions().tile("stand").isEnabled()
    assert not section.panel.open_actions().tile("drop_prone").isEnabled()


def test_the_self_actions_not_built_yet_stay_greyed(qapp, data) -> None:
    popup = DiceSection(data, _hero(data)).panel.open_actions()
    assert not popup.tile("block").isEnabled() and not popup.tile("escape").isEnabled()


def test_surprise_attack_is_the_gms_to_grant(qapp, data) -> None:
    tile = DiceSection(data, _hero(data)).panel.open_actions().tile("surprise_attack")
    assert not tile.isEnabled() and "GM" in tile.toolTip()


# -- lines on the next roll ---------------------------------------------------------------


def test_charge_is_a_fixed_line_that_takes_two_off(qapp, data, menu, instant_die) -> None:
    section = _loaded(data)
    panel = section.panel
    line = section.start_self_action("charge")  # the first option: a plain Charge
    assert line.key == "charge/charge" and not line.configurable and line.available
    rolls = _rolls(panel)
    panel._start_roll()
    (roll,) = rolls
    assert roll["bonus"] == 4 and roll["spec"].label.endswith("(Charge)")
    assert panel.action_lines() == []


def test_a_slam_replaces_a_charge_and_loads_the_own_save(qapp, data, menu, instant_die) -> None:
    section = _loaded(data)
    panel = section.panel
    section.start_self_action("charge")
    _Menu.pick = 1
    section.start_self_action("charge")  # Slam
    assert [line.key for line in panel.action_lines()] == ["charge/slam"]
    rolls = _rolls(panel)
    panel._start_roll()
    (roll,) = rolls
    assert roll["spec"].follow_up.ladder.rank == 9
    # Once the die settles, the slammer's own save is on the chip, filled in.
    own = panel.current_spec()
    assert own.label.startswith("Your own impact") and own.modifier == 4
    assert own.dc == roll["spec"].follow_up.dc - 9 + 4


def test_aim_rolls_two_dice_and_keeps_the_higher(qapp, data, menu, monkeypatch) -> None:
    faces = iter([5, 17])
    monkeypatch.setattr("mm_companion.ui.dice_roller.roll_d20", lambda: next(faces))
    section = _loaded(data)
    panel = section.panel
    section.start_self_action("aim")
    rolls = _rolls(panel)
    panel._start_roll()
    panel._finish_roll()
    (roll,) = rolls
    assert (roll["die"], roll["other_die"]) == (17, 5)
    assert "<s>5</s>" in panel._readout.text()


def test_a_rerolled_aim_throws_two_lucky_dice(qapp, data, menu, monkeypatch) -> None:
    faces = iter([5, 17, 2, 8])
    monkeypatch.setattr("mm_companion.ui.dice_roller.roll_d20", lambda: next(faces))
    monkeypatch.setattr(
        "mm_companion.ui.dice_roller.reroll_d20", lambda: (lambda d: (d + 10, False))(next(faces))
    )
    section = _loaded(data)
    panel = section.panel
    section.start_self_action("aim")
    rolls = _rolls(panel)
    panel._start_roll()
    panel._finish_roll()
    assert panel.reroll()
    panel._finish_roll()
    assert (rolls[1]["die"], rolls[1]["other_die"]) == (18, 12)


def test_a_finishing_attack_is_rolled_against_ten(qapp, data, menu, instant_die) -> None:
    section = _loaded(data)
    panel = section.panel
    _Menu.pick = 1
    section.start_self_action("finishing_attack")  # with intent to kill
    rolls = _rolls(panel)
    panel._start_roll()
    (roll,) = rolls
    assert roll["dc"] == 10
    assert roll["spec"].critical_hit
    assert roll["spec"].follow_up.ladder.rungs[2].conditions == ("dead",)


def test_dismissing_the_options_takes_nothing(qapp, data, menu) -> None:
    section = _loaded(data)
    _Menu.pick = None
    assert section.start_self_action("charge") is None
    assert section.panel.action_lines() == []


def test_unloading_the_attack_greys_the_line(qapp, data, menu) -> None:
    section = _loaded(data)
    line = section.start_self_action("aim")
    section.panel.load_spec(None)
    assert not line.available


# -- at once ---------------------------------------------------------------------------


def test_defend_lands_on_the_sheet_with_a_note(qapp, data, menu) -> None:
    hero = _hero(data)
    sheet = CharacterSheet(data, hero)
    notes: list = []
    sheet.dice.post_note = notes.append
    assert sheet.dice.start_self_action("defend") is None
    (applied,) = hero.conditions
    assert applied.condition_id == "defending" and applied.temporary and applied.amount == 2
    assert notes and "Defending +2" in notes[0]
    assert sheet.dice.panel.action_lines() == []


def test_drop_prone_then_stand_slowly(qapp, data, menu) -> None:
    hero = _hero(data)
    sheet = CharacterSheet(data, hero)
    sheet.dice.start_self_action("drop_prone")
    assert [c.condition_id for c in hero.conditions] == ["prone"]
    _Menu.pick = 2  # Stand slowly
    sheet.dice.start_self_action("stand")
    (hindered,) = hero.conditions
    assert hindered.condition_id == "hindered" and hindered.temporary


def test_a_made_stand_check_takes_prone_off(qapp, data, menu, instant_die, monkeypatch) -> None:
    monkeypatch.setattr("mm_companion.ui.dice_roller.roll_d20", lambda: 20)
    hero = _hero(data)
    apply_condition(hero, "prone", data)
    sheet = CharacterSheet(data, hero)
    _Menu.pick = 0  # the Acrobatics check
    sheet.dice.start_self_action("stand")
    panel = sheet.dice.panel
    assert panel.current_spec().dc == 20 and panel.current_spec().recovers == "prone"
    panel._start_roll()  # lands at once (instant_die)
    assert hero.conditions == []


def test_catch_loads_the_agility_check(qapp, data, menu) -> None:
    section = DiceSection(data, _hero(data))
    section.start_self_action("catch")
    spec = section.panel.current_spec()
    assert spec.label == "Catch: Agility" and spec.dc == 10


def test_recover_offers_the_check_and_rolls_it(qapp, data, menu, instant_die) -> None:
    hero = _hero(data)
    apply_condition(hero, "staggered", data)
    sheet = CharacterSheet(data, hero)
    rolls = _rolls(sheet.dice.panel)
    sheet.dice.start_self_action("recover")
    assert _Menu.shown[-1][-1] == RECOVER_DEFENSE_ONLY
    assert any(c.condition_id == "recovering" for c in hero.conditions)
    (roll,) = rolls
    assert roll["spec"].recovers == "staggered"


def test_recover_with_nothing_to_shed_just_takes_the_bonus(qapp, data, menu) -> None:
    hero = _hero(data)
    sheet = CharacterSheet(data, hero)
    sheet.dice.start_self_action("recover")
    assert _Menu.shown == []  # nothing to choose between
    assert [c.condition_id for c in hero.conditions] == ["recovering"]

"""The roller's one Bonus/Penalty slider, its action lane, the attack picker, and the
Actions popup's Attack and Area tiles (combat maneuvers, phase 4)."""

from __future__ import annotations

import pytest

from mm_companion.core.character import Character
from mm_companion.core.powers import ModifierSelection, Power, PowerEffectInstance
from mm_companion.core.rules import RollSpec, attack_choices
from mm_companion.ui.action_menu import NO_AREAS, NO_ATTACKS, SKIP_TARGET
from mm_companion.ui.attack_targets import SceneTarget
from mm_companion.ui.dice_roller import MODIFIER_LIMIT, NO_DC_WARNING
from mm_companion.ui.roller.action_lane import NEEDS_ATTACK
from mm_companion.ui.roller.view import DiceRollerView
from mm_companion.ui.sections.dice import DiceSection

pytestmark = pytest.mark.usefixtures("workspace")


class _Menu:
    """Stands in for ``action_menu``'s ``QMenu``: records what it offered and picks
    :attr:`pick` (an index, or ``None`` to dismiss). Patching ``QMenu.exec`` on the class
    does not reach a PySide instance."""

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


def _blast(name: str = "Blast", rank: int = 8) -> Power:
    damage = PowerEffectInstance("damage", rank=rank)
    damage.extras.append(ModifierSelection("ranged", config={"from": "close"}))
    return Power(name=name, effects=[damage])


def _burst() -> Power:
    effect = PowerEffectInstance(
        "damage", rank=6, extras=[ModifierSelection("area_effect", config={})]
    )
    return Power(name="Fire Burst", effects=[effect])


def _section(data, *powers: Power) -> DiceSection:
    hero = Character.new_default(data)
    hero.powers = list(powers)
    return DiceSection(data, hero)


def _attack(modifier: int = 6, label: str = "Blast") -> RollSpec:
    save = RollSpec(label="Toughness vs. 23", rolled_by_target=True)
    return RollSpec(label=label, modifier=modifier, follow_up=save)


def _by_modifier(spec: RollSpec | None) -> tuple[int, str]:
    """A stand-in limit: as far as the loaded attack's own bonus, capped at 5."""
    if spec is None:
        return 0, "no attack"
    return min(5, spec.modifier), "" if spec.modifier > 0 else "No attack bonus to trade."


# -- the one slider --------------------------------------------------------------------


def test_one_signed_slider_runs_both_ways_from_zero(qapp) -> None:
    view = DiceRollerView()  # held: the panel's widgets die with it
    panel = view.panel
    assert panel._modifier_slider.minimum() == -MODIFIER_LIMIT
    assert panel._modifier_slider.maximum() == MODIFIER_LIMIT
    assert panel._modifier_slider.value() == 0
    panel._modifier_spin.setValue(3)
    assert panel._modifier_spin.text() == "+3"
    assert panel._modifier_slider.value() == 3
    panel._modifier_slider.setValue(-4)
    assert panel._modifier_spin.text() == "-4"
    assert panel.adjustment() == -4
    # A penalty travels as the pair the wire and the history cards speak.
    assert panel._roll_parameters()[:2] == (0, 4)


def test_extra_effort_adds_on_top_and_clamps(qapp) -> None:
    view = DiceRollerView()
    panel = view.panel
    panel._modifier_spin.setValue(-1)
    panel.add_bonus(2)
    assert panel.adjustment() == 1
    panel.add_bonus(50)
    assert panel.adjustment() == MODIFIER_LIMIT


def test_an_old_quick_roll_with_both_numbers_loads_as_its_net(qapp) -> None:
    from mm_companion.core import storage
    from mm_companion.ui.roll_history import quick_roll_key

    storage.update_settings(quick_rolls=[{"bonus": 4, "penalty": 1}, {"bonus": 3, "penalty": 0}])
    view = DiceRollerView()
    # The same roll twice once read by the net: one chip.
    assert view.panel._quick_rolls == [{"bonus": 3, "penalty": 0}]
    # And a card from before still lights its star.
    assert quick_roll_key({"bonus": 4, "penalty": 1}) == quick_roll_key({"bonus": 3})


# -- the lane --------------------------------------------------------------------------


def test_the_lane_holds_one_lines_height_even_when_empty(qapp) -> None:
    view = DiceRollerView()
    panel = view.panel
    empty = panel._lane.sizeHint().height()
    assert empty > 0
    panel.start_action_line("charge", "Charge", fixed="-2 attack")
    assert panel._lane.sizeHint().height() == empty
    panel.action_lines()[0].removed.emit("charge")
    assert panel.action_lines() == []
    assert panel._lane.sizeHint().height() == empty


def test_a_configurable_line_starts_at_its_maximum(qapp) -> None:
    view = DiceRollerView()
    panel = view.panel
    panel.load_spec(_attack(modifier=3))
    line = panel.start_action_line("all_out", "All-Out Attack", limit=_by_modifier)
    assert line.configurable and line.available
    assert (line.maximum(), line.value()) == (3, 3)
    assert panel.active_lines() == [("all_out", 3)]


def test_picking_the_same_action_again_replaces_its_line(qapp) -> None:
    view = DiceRollerView()
    panel = view.panel
    panel.start_action_line("charge", "Charge", fixed="-2 attack")
    panel.start_action_line("charge", "Charge", fixed="-2 attack")
    assert [line.key for line in panel.action_lines()] == ["charge"]


def test_needing_an_attack_with_none_loaded_opens_the_picker(qapp, data, menu) -> None:
    section = _section(data, _blast("Blast"), _blast("Bolt", rank=4))
    panel = section.panel
    _Menu.pick = 1
    line = panel.start_action_line("aim", "Aim", fixed="roll twice", needs_attack=True)
    assert _Menu.shown[0] == [c.caption for c in attack_choices(section._character, data)]
    assert panel.current_spec() == panel.loaded_attack()
    assert panel.current_spec().label == attack_choices(section._character, data)[1].spec.label
    assert line is not None and line.available


def test_dismissing_the_picker_calls_the_action_off(qapp, data, menu) -> None:
    section = _section(data, _blast())
    _Menu.pick = None
    assert section.panel.start_action_line("aim", "Aim", needs_attack=True) is None
    assert section.panel.action_lines() == []
    assert section.panel.current_spec() is None


def test_unloading_the_attack_greys_the_line_and_another_resets_it(qapp) -> None:
    view = DiceRollerView()
    panel = view.panel
    panel.load_spec(_attack(modifier=4))
    line = panel.start_action_line("all_out", "All-Out", limit=_by_modifier, needs_attack=True)
    line.set_value(1)

    panel.load_spec(None)
    assert not line.available and line.reason == NEEDS_ATTACK
    assert panel.action_lines() == [line]  # greyed, not gone
    assert panel.active_lines() == []

    panel.load_spec(_attack(modifier=2, label="Punch"))
    assert line.available
    assert (line.maximum(), line.value()) == (2, 2)  # the new attack's maximum


def test_a_line_with_nothing_to_trade_is_greyed_with_the_reason(qapp) -> None:
    view = DiceRollerView()
    panel = view.panel
    panel.load_spec(_attack(modifier=0))
    line = panel.start_action_line("all_out", "All-Out", limit=_by_modifier, needs_attack=True)
    assert not line.available
    assert "No attack bonus" in line.toolTip()


def test_a_roll_spends_the_lines_it_used_and_keeps_the_greyed_ones(qapp, instant_die) -> None:
    view = DiceRollerView()
    panel = view.panel
    panel.start_action_line("defend", "Defend", fixed="+2 Defense")
    greyed = panel.start_action_line("aim", "Aim", fixed="roll twice", needs_attack=False)
    panel._lane.line("aim")._set_available(False, "for the test")
    panel._start_roll()
    assert [line.key for line in panel.action_lines()] == ["aim"]
    assert panel.action_lines() == [greyed]


def test_an_abandoned_roll_keeps_its_lines(qapp) -> None:
    view = DiceRollerView()
    panel = view.panel
    panel.start_action_line("defend", "Defend", fixed="+2 Defense")
    panel._rolled_lines = (("defend", None),)
    panel._rolling = True
    panel._abandon_roll("No answer")
    panel._spend_extras()
    assert [line.key for line in panel.action_lines()] == ["defend"]


def test_a_reroll_leaves_the_current_lines_alone(qapp, instant_die) -> None:
    view = DiceRollerView()
    panel = view.panel
    panel._start_roll()
    panel.start_action_line("defend", "Defend", fixed="+2 Defense")
    assert panel.reroll()
    assert [line.key for line in panel.action_lines()] == ["defend"]


# -- the no-DC warning -----------------------------------------------------------------


def test_an_attack_rolled_with_no_dc_and_no_target_is_warned_about(qapp, instant_die) -> None:
    view = DiceRollerView()
    panel = view.panel
    panel._dc_check.setChecked(False)
    panel.roll_spec(_attack())
    assert "No DC and no target" in panel._readout.text()
    assert NO_DC_WARNING.startswith("⚠")


def test_no_warning_with_a_dc_or_for_a_plain_check(qapp, instant_die) -> None:
    view = DiceRollerView()
    panel = view.panel
    panel._dc_check.setChecked(True)
    panel.roll_spec(_attack())
    assert "No DC" not in panel._readout.text()
    panel._dc_check.setChecked(False)
    panel.roll_spec(RollSpec(label="Athletics", modifier=4))
    assert "No DC" not in panel._readout.text()


def test_no_warning_for_an_aimed_attack(qapp, instant_die) -> None:
    from dataclasses import replace

    view = DiceRollerView()
    view.panel._dc_check.setChecked(False)
    view.panel.roll_spec(replace(_attack(), targets=("Thug",), action_id="a1"))
    assert "No DC" not in view.panel._readout.text()


# -- the Attack tile -------------------------------------------------------------------


def test_the_attack_tile_is_greyed_with_no_attack_to_make(qapp, data) -> None:
    section = _section(data)
    tile = section.panel.open_actions().tile("attack")
    assert not tile.isEnabled() and NO_ATTACKS in tile.toolTip()


def test_off_the_air_the_attack_tile_loads_the_attack(qapp, data, menu) -> None:
    section = _section(data, _blast())
    tile = section.panel.open_actions().tile("attack")
    assert tile.isEnabled()
    tile.click()
    assert len(_Menu.shown) == 1  # the attacks; no target menu with no session
    assert section.panel.loaded_attack() is not None
    assert not section.panel.rolling


def test_at_a_table_skip_comes_first_and_loads_the_attack(qapp, data, menu, monkeypatch) -> None:
    section = _section(data, _blast())
    monkeypatch.setattr(
        "mm_companion.ui.sections.dice.session_targets",
        lambda _bridge: [SceneTarget("s1", "Thug"), SceneTarget("s2", "Goon", minion=True)],
    )
    aimed: list = []
    monkeypatch.setattr(section, "aim_attack", aimed.append)
    _Menu.pick = 0
    section.start_attack()
    assert _Menu.shown[1] == [SKIP_TARGET, "Thug", "Goon (minion)"]
    assert aimed == [] and section.panel.loaded_attack() is not None


def test_at_a_table_a_target_aims_the_attack(qapp, data, menu, monkeypatch) -> None:
    section = _section(data, _blast())
    monkeypatch.setattr(
        "mm_companion.ui.sections.dice.session_targets",
        lambda _bridge: [SceneTarget("s1", "Thug")],
    )
    aimed: list = []
    monkeypatch.setattr(section, "aim_attack", aimed.append)

    class _Pick(_Menu):
        def exec(self, *_a):
            # The attack first, then the target (index 1, past Skip).
            return self.entries[0 if len(_Menu.shown) == 1 else 1]

    monkeypatch.setattr("mm_companion.ui.action_menu.QMenu", _Pick)
    section.start_attack()
    ((spec, target),) = aimed
    assert target.name == "Thug" and spec.label.startswith("Blast: Attack")
    assert section.panel.current_spec() is None  # aimed rolls; nothing left loaded


# -- the Area tile ---------------------------------------------------------------------


def test_the_area_tile_is_greyed_without_an_area(qapp, data) -> None:
    section = _section(data, _blast())
    tile = section.panel.open_actions().tile("area")
    assert not tile.isEnabled() and NO_AREAS in tile.toolTip()


def test_the_area_tile_opens_the_only_area_straight_away(qapp, data, monkeypatch) -> None:
    section = _section(data, _burst())
    opened: list = []
    monkeypatch.setattr(section, "start_area", opened.append)
    tile = section.panel.open_actions().tile("area")
    assert tile.isEnabled()
    tile.click()
    (key,) = opened
    assert isinstance(key, str) and key

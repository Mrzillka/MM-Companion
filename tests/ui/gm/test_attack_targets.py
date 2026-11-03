"""Aimed attacks: right-click an attack, pick a creature off the Scene, the GM resolves it."""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QApplication

from mm_companion.core import library
from mm_companion.core.character import Character
from mm_companion.core.data_loader import load_game_data
from mm_companion.core.dice import CheckResult, GroupResult
from mm_companion.core.npc import quick_npc
from mm_companion.core.powers import ModifierSelection, Power, PowerEffectInstance
from mm_companion.core.rules import (
    MinionRules,
    defense_class,
    judge_attack,
    power_rolls,
    targetable_attack,
)
from mm_companion.ui import action_run
from mm_companion.ui.attack_targets import (
    KIND_ATTACK,
    AttackDeclaration,
    SceneTarget,
    aimed_spec,
    scene_targets,
)
from mm_companion.ui.cards.rolls import RollsFooter
from mm_companion.ui.gm_window import SCENE_NPC, GMWindow
from mm_companion.ui.session_bridge import set_active_session

pytestmark = pytest.mark.usefixtures("workspace")


@pytest.fixture(autouse=True)
def _clear_active_session():
    yield
    set_active_session(None)


@pytest.fixture
def window(qapp: QApplication) -> GMWindow:
    made = GMWindow(bind="127.0.0.1")
    yield made
    for aimed in made._attack.aimed.values():
        if aimed.window is not None:
            aimed.window.close()
    made.bridge.stop()


def _blast(data, *, linked: bool = False) -> tuple[Character, list]:
    hero = Character.new_default(data)
    hero.profile["hero_name"] = "Ana"
    damage = PowerEffectInstance("damage", rank=8)
    damage.extras.append(ModifierSelection("ranged", config={"from": "close"}))
    effects = [damage]
    if linked:
        # A Linked power: one attack, a save per effect (see test_power_roll_rules).
        affliction = PowerEffectInstance(
            "affliction",
            rank=6,
            config={
                "resistance": "Fortitude",
                "degree1": "dazed",
                "degree2": "stunned",
                "degree3": "incapacitated",
            },
        )
        affliction.extras.append(ModifierSelection("ranged", config={"from": "close"}))
        effects.append(affliction)
    power = Power(name="Blast", effects=effects)
    if linked:
        power.structure = "linked"
    specs = power_rolls(power, hero, data)
    return hero, specs


def _attack_spec(data, *, linked: bool = False):
    _hero, specs = _blast(data, linked=linked)
    return next(s for s in specs if targetable_attack(s))


def _npc(window: GMWindow, name: str, *, minion: bool = False) -> str:
    character = quick_npc(load_game_data(), name=name, attack=6, effect=6, defence=6, toughness=4)
    character.minion = minion
    path = library.save_character(character, directory=window._npc_dir())
    window._register_npc(path)
    window._set_in_scene(SCENE_NPC, path.name, True)
    return path.name


def _ref(window: GMWindow, name: str) -> str:
    return window._scene_entry_for(SCENE_NPC, name).ref


def _staged(degrees: list[int], calls: list):
    def fake(lines, *, lead_modifier=None, dc=None, critical_bonus=0, rng=None):
        calls.append(list(lines))
        results = tuple(
            CheckResult(10, line[0], 10 + line[0], (dc or 0) + line[1], degree, False)
            for line, degree in zip(lines, degrees, strict=False)
        )
        return GroupResult(None, None, results)

    return fake


def _declare(window: GMWindow, data, target: str, *, linked: bool = False):
    spec = _attack_spec(data, linked=linked)
    aimed = aimed_spec(spec, "a1", window._npc_state[target].summary.name, "Ana")
    payload = AttackDeclaration(
        "a1", _ref(window, target), window._npc_state[target].summary.name, aimed, "Ana"
    ).to_dict()
    return window._attack.declare("p1", payload), aimed


def _roll(spec, die: int, modifier: int, **extra) -> dict:
    return {
        "kind": "roll",
        "die": die,
        "bonus": max(modifier, 0),
        "penalty": max(-modifier, 0),
        "critical": die in (1, 20),
        "spec": spec.to_dict(),
        **extra,
    }


# -- core ------------------------------------------------------------------------------


def test_only_an_attack_the_wielder_rolls_can_be_aimed(data) -> None:
    _hero, specs = _blast(data)
    assert [targetable_attack(s) for s in specs] == [True, False]


def test_judging_an_attack(data) -> None:
    assert judge_attack(5, 12, 16).hit
    assert not judge_attack(5, 8, 16).hit
    # A natural 20 adds a degree: a near miss becomes a critical hit.
    crit = judge_attack(0, 20, 22)
    assert crit.hit and crit.critical
    # A minion's 20 is no added success.
    assert not judge_attack(0, 20, 22, added_success=False).hit
    # Against a minion the routine result counts.
    routine = judge_attack(6, 3, 16, routine_total=16)
    assert routine.hit and routine.by_routine and not routine.critical


def test_the_scene_lists_everyone_but_the_attacker() -> None:
    entries = [
        {"ref": "r1", "name": "Ana", "player_id": "p1"},
        {"ref": "r2", "name": "Thug", "minion": True},
        {"ref": "r3", "name": "Boss"},
    ]
    targets = scene_targets(entries, own_player_id="p1", skip_ref="r3")
    assert targets == [SceneTarget("r2", "Thug", True)]


# -- the roll line's menu ----------------------------------------------------------------


class _FakeBridge:
    in_session = True
    is_gm = False

    def own_player_id(self) -> str:
        return "p1"

    def scene(self) -> list[dict]:
        return [{"ref": "r1", "name": "Ana", "player_id": "p1"}, {"ref": "r2", "name": "Thug"}]


def test_an_attack_lines_right_click_offers_the_scene(qapp, data) -> None:
    _hero, specs = _blast(data)
    footer = RollsFooter(specs)
    picked: list = []
    footer.targetRequested.connect(picked.append)
    set_active_session(_FakeBridge())
    menu = footer.build_menu(footer, 0, specs[0])
    names = [a.text() for a in menu.actions()]
    assert "Thug" in names and "Ana" not in names
    next(a for a in menu.actions() if a.text() == "Thug").trigger()
    assert picked[0][1].ref == "r2"
    # The save line has nothing to aim.
    assert footer.build_menu(footer, 1, specs[1]) is None


def test_with_no_session_the_menu_says_so(qapp, data) -> None:
    _hero, specs = _blast(data)
    footer = RollsFooter(specs)
    menu = footer.build_menu(footer, 0, specs[0])
    assert any("needs a session" in a.text() for a in menu.actions())


# -- the GM's side -----------------------------------------------------------------------


def test_a_players_aimed_attack_waits_for_accept(window: GMWindow, data, monkeypatch) -> None:
    thug = _npc(window, "Thug")
    calls: list = []
    monkeypatch.setattr(action_run, "resolve_group", _staged([-2], calls))
    aimed, spec = _declare(window, data, thug)
    assert aimed.card is not None and aimed.card.second_button is not None
    dc = defense_class(window._npc_state[thug].character, data)
    window._attack.take_roll(_roll(spec, 15, dc))
    assert calls == []  # nothing until the GM says so
    window._attack.accept("a1")
    assert len(calls) == 1
    conditions = {c.condition_id for c in window._npc_state[thug].character.conditions}
    assert conditions  # two degrees of failure on a Damage save leave a mark
    assert not aimed.card.pending


def test_a_declined_attack_does_nothing(window: GMWindow, data, monkeypatch) -> None:
    thug = _npc(window, "Thug")
    calls: list = []
    monkeypatch.setattr(action_run, "resolve_group", _staged([-3], calls))
    aimed, spec = _declare(window, data, thug)
    window._attack.take_roll(_roll(spec, 18, 20))
    window._attack.decline("a1")
    assert calls == [] and not window._npc_state[thug].character.conditions
    assert "Declined" in aimed.card.status()


def test_a_miss_rolls_no_save(window: GMWindow, data, monkeypatch) -> None:
    thug = _npc(window, "Thug")
    calls: list = []
    monkeypatch.setattr(action_run, "resolve_group", _staged([-3], calls))
    aimed, spec = _declare(window, data, thug)
    window._attack.accept("a1")  # accepted before the roll: waits for it
    window._attack.take_roll(_roll(spec, 2, -10))
    assert calls == [] and "Missed" in aimed.card.status()


def test_the_lone_save_takes_the_gm_rollers_adjustment(window: GMWindow, data, monkeypatch) -> None:
    thug = _npc(window, "Thug")
    calls: list = []
    monkeypatch.setattr(action_run, "resolve_group", _staged([1], calls))
    monkeypatch.setattr(window, "roller_adjustment", lambda: 3)
    _aimed, spec = _declare(window, data, thug)
    # Read before the save lands: a made Damage save still costs a Hit, -1 Toughness.
    plain, _ = _declare_line_modifier(window, data, thug)
    window._attack.take_roll(_roll(spec, 15, 20))
    window._attack.accept("a1")
    assert calls[0][0][0] == plain + 3


def _declare_line_modifier(window, data, thug):
    from mm_companion.core.rules import localize_spec

    save = _attack_spec(data).follow_up
    own = localize_spec(save, window._npc_state[thug].character, data)
    return own.modifier, own


def test_a_critical_on_a_minion_lands_the_worst_unrolled(
    window: GMWindow, data, monkeypatch
) -> None:
    mook = _npc(window, "Mook", minion=True)
    calls: list = []
    monkeypatch.setattr(action_run, "resolve_group", _staged([1], calls))
    _aimed, spec = _declare(window, data, mook)
    window._attack.take_roll(_roll(spec, 20, 30))
    window._attack.accept("a1")
    assert calls == []
    conditions = {c.condition_id for c in window._npc_state[mook].character.conditions}
    assert "incapacitated" in conditions


def test_a_routine_hit_on_a_minion(window: GMWindow, data, monkeypatch) -> None:
    mook = _npc(window, "Mook", minion=True)
    calls: list = []
    monkeypatch.setattr(action_run, "resolve_group", _staged([1], calls))
    aimed, spec = _declare(window, data, mook)
    dc = defense_class(window._npc_state[mook].character, data)
    # A rolled 2 at +dc-10 misses, but 10 + the same modifier reaches the Defense.
    window._attack.take_roll(_roll(spec, 2, dc - 10))
    window._attack.accept("a1")
    assert len(calls) == 1
    monkeypatch.setattr(window, "minion_rules", lambda: MinionRules(routine_attacks=False))


def test_several_saves_open_the_window(window: GMWindow, data, monkeypatch) -> None:
    thug = _npc(window, "Thug")
    calls: list = []
    monkeypatch.setattr(action_run, "resolve_group", _staged([-1, -1], calls))
    aimed, spec = _declare(window, data, thug, linked=True)
    if len(spec.follow_ups()) < 2:
        pytest.skip("this ruleset's Linked power forces one save")
    window._attack.take_roll(_roll(spec, 18, 20))
    window._attack.accept("a1")
    assert aimed.window is not None and calls == []
    aimed.window.set_choice(0, "FORTITUDE", -2)
    aimed.window._roll()
    assert len(calls) == 1 and len(calls[0]) == 2


def test_the_gm_aims_an_npcs_tile_straight_through(window: GMWindow, data, monkeypatch) -> None:
    brute = _npc(window, "Brute")
    thug = _npc(window, "Thug")
    calls: list = []
    monkeypatch.setattr(action_run, "resolve_group", _staged([-1], calls))
    rolled: list = []

    def roll_spec(spec, **_kw):
        rolled.append(spec)
        window._attack.take_roll(_roll(spec, 19, 20))
        return True

    monkeypatch.setattr(window._roller, "roll_spec", roll_spec)
    target = next(t for t in window._aim_targets_for(brute) if t.ref == _ref(window, thug))
    assert all(t.ref != _ref(window, brute) for t in window._aim_targets_for(brute))
    window._aim_from_card(brute, (_attack_spec(data), target))
    assert rolled and rolled[0].action_id and rolled[0].targets == ("Thug",)
    assert len(calls) == 1  # no card to accept: straight to the save


def test_the_declaration_survives_the_wire(data) -> None:
    from mm_companion.core.session.protocol import sanitize_mod_payload

    spec = aimed_spec(_attack_spec(data), "a1", "Thug", "Ana")
    payload = AttackDeclaration("a1", "r2", "Thug", spec, "Ana").to_dict()
    back = AttackDeclaration.from_dict(sanitize_mod_payload(payload))
    assert back is not None and back.spec.follow_up is not None
    assert back.spec.targets == ("Thug",) and back.spec.action_id == "a1"
    assert KIND_ATTACK == "attack"


# -- review fixes (2026-10-28) -------------------------------------------------------------


def test_a_lucky_reroll_of_20_is_no_critical(window: GMWindow, data, monkeypatch) -> None:
    """A re-roll's lifted 20 is not natural: a hit, but its save's DC is not raised."""
    thug = _npc(window, "Thug")
    calls: list = []
    monkeypatch.setattr(action_run, "resolve_group", _staged([1], calls))
    aimed, spec = _declare(window, data, thug)
    window._attack.take_roll(_roll(spec, 20, 20, critical=False, reroll=True))
    window._attack.accept("a1")
    assert calls[0][0][1] == spec.follow_up.dc
    assert "critical" not in aimed.card.status()


def test_a_natural_20_still_raises_the_save(window: GMWindow, data, monkeypatch) -> None:
    thug = _npc(window, "Thug")
    calls: list = []
    monkeypatch.setattr(action_run, "resolve_group", _staged([1], calls))
    _aimed, spec = _declare(window, data, thug)
    window._attack.take_roll(_roll(spec, 20, 20))
    window._attack.accept("a1")
    bonus = data.system.critical_effect_bonus
    assert calls[0][0][1] == spec.follow_up.dc + bonus


def test_a_routine_hit_after_a_natural_1_is_no_graze(window: GMWindow, data, monkeypatch) -> None:
    """The attacker took 10: the natural 1 they rolled gives the minion no +5 to resist."""
    mook = _npc(window, "Mook", minion=True)
    calls: list = []
    monkeypatch.setattr(action_run, "resolve_group", _staged([1], calls))
    plain, _ = _declare_line_modifier(window, data, mook)
    _aimed, spec = _declare(window, data, mook)
    dc = defense_class(window._npc_state[mook].character, data)
    window._attack.take_roll(_roll(spec, 1, dc - 10))
    window._attack.accept("a1")
    assert calls[0][0][0] == plain


def test_the_card_names_the_die_and_the_save(window: GMWindow, data, monkeypatch) -> None:
    thug = _npc(window, "Thug")
    monkeypatch.setattr(action_run, "resolve_group", _staged([-1], []))
    aimed, spec = _declare(window, data, thug)
    window._attack.take_roll(_roll(spec, 14, 6))
    assert "d20 14 +6" in aimed.card.text()  # a total of 20, not a natural 20
    window._attack.accept("a1")
    assert "vs DC" in aimed.card.status()  # the save that was rolled, not only its rung


def test_accept_waits_for_a_players_sheet(window: GMWindow, data, monkeypatch) -> None:
    from mm_companion.ui.impress import PARTY_PLAYER, ImpressParty

    thug = _npc(window, "Thug")
    aimed, spec = _declare(window, data, thug)
    sheetless = ImpressParty(_ref(window, thug), "Bea", PARTY_PLAYER, "p2", None)
    monkeypatch.setattr(window._attack, "_target", lambda _a: sheetless)
    window._attack.take_roll(_roll(spec, 15, 20))
    window._attack.accept("a1")
    assert not aimed.done and "has not reached" in aimed.card.status()
    assert aimed.card.button.isEnabled()  # the GM may accept again once it has


def test_saves_that_never_left_settle_the_card(window: GMWindow, data, monkeypatch) -> None:
    thug = _npc(window, "Thug")
    monkeypatch.setattr(action_run.ActionRun, "roll_group", lambda *a, **k: False)
    aimed, spec = _declare(window, data, thug)
    window._attack.take_roll(_roll(spec, 15, 20))
    window._attack.accept("a1")
    assert aimed.done and "did not reach" in aimed.card.status()


def test_an_aimed_roll_offers_no_second_save(data) -> None:
    """Its save is the GM's to resolve: the history puts the roll in the attack's table,
    with no button to answer the save a second time."""
    from mm_companion.ui.roll_history import _provokes, action_table_key

    spec = _attack_spec(data)
    plain = _roll(spec, 15, 6)
    aimed = _roll(aimed_spec(spec, "a1", "Thug", "Ana"), 15, 6)
    assert _provokes(plain)
    assert not _provokes(aimed) and action_table_key(aimed) == "a1"


def test_an_npc_sheet_aims_off_the_gms_scene_without_itself(window: GMWindow, data) -> None:
    from PySide6.QtWidgets import QWidget

    from mm_companion.ui.attack_targets import targets_for

    brute = _npc(window, "Brute")
    _npc(window, "Thug")

    class _Sheet(QWidget):
        path = window._npc_state[brute].path

    sheet = _Sheet()
    sheet.aim_targets = lambda: window._aim_targets_for_window(sheet)
    line = QWidget(sheet)
    names = [t.name for t in targets_for(line)]  # offline, and never the Brute itself
    assert names == ["Thug"]


def test_a_tumbling_die_declares_nothing(qapp, data, monkeypatch) -> None:
    from mm_companion.ui.character_sheet import CharacterSheet
    from mm_companion.ui.sections import dice as dice_section

    sent: list = []

    class _Bridge:
        def send_action_request(self, kind, action_id, payload):
            sent.append(kind)
            return True

    monkeypatch.setattr(dice_section, "live_session", lambda: _Bridge())
    sheet = CharacterSheet(data)
    panel = sheet.dice.view.panel
    monkeypatch.setattr(type(panel), "rolling", property(lambda self: True))
    sheet.dice.aim_attack((_attack_spec(data), SceneTarget("r2", "Thug")))
    assert sent == []  # no die, so no card waiting on one at the GM's

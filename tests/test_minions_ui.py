"""Minions in the GM's hands: the toggle, the card, the board, the actions, the roller,
and the Session settings page that decides which rules apply.

Offscreen Qt. The rules themselves are proven headless in ``test_minions.py``.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication

from mm_companion.core import library, storage
from mm_companion.core.character import Character
from mm_companion.core.data_loader import load_game_data
from mm_companion.core.dice import CheckResult, GroupResult
from mm_companion.core.npc import quick_npc
from mm_companion.core.powers import ModifierSelection, Power, PowerEffectInstance
from mm_companion.core.rules import (
    AreaAttempt,
    MinionRules,
    RollSpec,
    defense_class,
    effect_spec_ladder,
    power_area_use,
)
from mm_companion.ui import action_run
from mm_companion.ui.action_ui import (
    TABLE_MOD_ID,
    TABLE_SESSION_KEY,
    publish_minion_rules,
    session_minion_rules,
)
from mm_companion.ui.area import AreaWindow
from mm_companion.ui.dice_roller import DiceRollerPanel
from mm_companion.ui.gm_window import SCENE_NPC, GMWindow
from mm_companion.ui.impress import PARTY_NPC, PARTY_PLAYER, ImpressParty
from mm_companion.ui.npc_window import NPCWindow
from mm_companion.ui.roll_history import AnswerContext, answer_context, roll_headline
from mm_companion.ui.scene_card import SceneCard
from mm_companion.ui.session_bridge import set_active_session
from mm_companion.ui.settings import SessionPage, SettingsWindow


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


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
def data():
    return load_game_data()


@pytest.fixture
def window(qapp: QApplication) -> GMWindow:
    made = GMWindow(bind="127.0.0.1")
    yield made
    for npc in list(made._npc_windows.values()):
        npc._dirty = False
        npc.close()
    for win in list(made._impress.windows.values()) + list(made._area.windows.values()):
        win.close()
    made.bridge.stop()


def mook_file(window: GMWindow, name: str = "Mook", *, minion: bool = False) -> str:
    character = quick_npc(load_game_data(), name=name, attack=6, effect=6, defence=6, toughness=6)
    character.abilities["PRE"] = 2
    character.skill_ranks["Intimidation"] = 6
    character.minion = minion
    path = library.save_character(character, directory=window._npc_dir())
    window._register_npc(path)
    return path.name


def card_of(window: GMWindow, name: str):
    return window._npc_state[name].card


def use_rules(window: GMWindow, monkeypatch, **switches) -> None:
    """Play this test's session by *switches* — as if the GM had saved them."""
    monkeypatch.setattr(window, "minion_rules", lambda: MinionRules(**switches))
    window._apply_minion_rules()


# -- the toggle --------------------------------------------------------------------


def test_the_card_makes_a_minion_and_back(window: GMWindow) -> None:
    name = mook_file(window)
    card = card_of(window, name)
    assert not card.character.minion and "minion" not in card._pl_label.text()

    card._minion_button.click()

    assert window._npc_state[name].character.minion
    assert library.load_character(window._npc_state[name].path).minion  # saved
    card = card_of(window, name)
    assert "minion" in card._pl_label.text() and card._minion_button.isChecked()
    assert card.damage_row.visible_labels() == ["✓", "✗"]

    card._minion_button.click()
    assert not window._npc_state[name].character.minion
    assert card.damage_row.visible_labels() == ["✓", "1", "2", "3"]


def test_the_card_menu_says_which_way_it_goes(window: GMWindow) -> None:
    name = mook_file(window)
    card = card_of(window, name)
    texts = [a.text() for a in card.build_context_menu().actions()]
    assert "Make a minion" in texts
    window._set_npc_minion(name, True)
    texts = [a.text() for a in card_of(window, name).build_context_menu().actions()]
    assert "Make a full NPC" in texts


def test_an_open_sheet_follows_the_card_without_an_undo_step(window: GMWindow) -> None:
    name = mook_file(window)
    window._open_npc(name)
    sheet = next(iter(window._npc_windows.values())).sheet
    undo_before = sheet.undo.can_undo

    window._set_npc_minion(name, True)

    assert sheet.character.minion
    assert sheet.system_info.minion_check.isChecked()
    assert sheet.undo.can_undo == undo_before


def test_a_copy_is_a_minion_too(window: GMWindow) -> None:
    name = mook_file(window, minion=True)
    window._copy_npc(name)
    copies = [e for n, e in window._npc_state.items() if n != name]
    assert copies and all(e.character.minion for e in copies)


def test_the_npc_sheet_has_an_undoable_minion_box(qapp) -> None:
    window = NPCWindow()
    try:
        box = window.sheet.system_info.minion_check
        assert box.isVisibleTo(window)
        box.click()
        assert window.sheet.character.minion
        window.sheet.undo.undo()
        assert not window.sheet.character.minion and not box.isChecked()
    finally:
        window._dirty = False
        window.close()


# -- damage ------------------------------------------------------------------------


def test_a_failed_save_puts_a_minion_down_and_no_further(window: GMWindow) -> None:
    name = mook_file(window, minion=True)
    row = card_of(window, name).damage_row
    worst = max(s.index for s in row.steps)
    assert "worst" in row.button_tooltips()[-1]

    window._apply_npc_damage(name, worst)
    window._apply_npc_damage(name, worst)

    ids = {c.condition_id for c in window._npc_state[name].character.conditions}
    assert "incapacitated" in ids
    assert "dying" not in ids and "dead" not in ids


def test_with_the_ladder_rule_off_a_minion_walks_on(window: GMWindow, monkeypatch) -> None:
    name = mook_file(window, minion=True)
    use_rules(window, monkeypatch, worst_degree=False, stop_at_incapacitated=False)
    row = card_of(window, name).damage_row
    assert row.visible_labels() == ["✓", "1", "2", "3"]
    worst = max(s.index for s in row.steps)
    window._apply_npc_damage(name, worst)
    window._apply_npc_damage(name, worst)
    ids = {c.condition_id for c in window._npc_state[name].character.conditions}
    assert "dying" in ids


# -- the board ---------------------------------------------------------------------


def test_the_board_shows_a_minion_and_the_table_sees_it_when_allowed(
    window: GMWindow, monkeypatch
) -> None:
    name = mook_file(window, minion=True)
    window._set_in_scene(SCENE_NPC, name, True)
    (entry,) = window._scene_payload()
    assert entry["minion"] is True

    sent: list[list[dict]] = []
    monkeypatch.setattr(type(window.bridge), "in_session", property(lambda _self: True))
    monkeypatch.setattr(window.bridge, "set_scene", lambda entries, sources: sent.append(entries))
    window._pushed_scene = None
    window._push_scene()
    assert sent[-1][0].get("minion") is True

    use_rules(window, monkeypatch, show_players=False)
    assert "minion" not in sent[-1][0]
    # The GM's own board still says so.
    ref = window._scene[0].ref
    assert window._scene_board.card(ref).minion


def test_a_scene_card_says_minion(qapp, data) -> None:
    card = SceneCard({"ref": "s1", "name": "Mook", "minion": True}, data, gm=False)
    assert card.minion and not card._minion_label.isHidden()
    assert "Minion" in card._name.toolTip()
    card.set_entry({"ref": "s1", "name": "Mook"})
    assert not card.minion and card._minion_label.isHidden()


# -- Impress -----------------------------------------------------------------------


def _staged(degrees: list[int], calls: list[dict]):
    def fake(lines, *, lead_modifier=None, dc=None, critical_bonus=0, rng=None):
        calls.append({"lines": list(lines), "lead": lead_modifier})
        if lead_modifier is not None:
            return GroupResult(12, 12 + lead_modifier, ())
        results = tuple(
            CheckResult(10, line[0], 10 + line[0], (dc or 0) + line[1], degree, False)
            for line, degree in zip(lines, degrees, strict=False)
        )
        return GroupResult(None, None, results)

    return fake


def _impress(window: GMWindow, target: str):
    impress = window._impress.open_new()
    assert impress is not None
    impress._impression.setCurrentIndex(impress._impression.findData("demoralized"))
    for row in impress._rows:
        row.include.setChecked(row.party.source == target)
    return impress


def test_impressing_minions_is_routine_and_lands_the_worst(window: GMWindow, monkeypatch) -> None:
    mook_file(window, "Thug")
    mook = mook_file(window, "Mook", minion=True)
    window._set_in_scene(SCENE_NPC, mook, True)
    calls: list[dict] = []
    monkeypatch.setattr(action_run, "resolve_group", _staged([-1], calls))

    impress = _impress(window, mook)
    modifier = impress.check_modifier()
    impress._roll.click()

    # Nothing was rolled for the check: one group, the Mook's resistance, no lead.
    (call,) = calls
    assert call["lead"] is None
    assert call["lines"][0][1] == 10 + modifier + 5
    # A failure by one is the most severe rung: Frightened, not Impaired.
    conditions = {c.condition_id for c in window._npc_state[mook].character.conditions}
    assert "frightened" in conditions and "impaired" not in conditions
    assert "routine" in impress.check_text()


def test_with_routine_impress_off_the_check_is_rolled(window: GMWindow, monkeypatch) -> None:
    mook_file(window, "Thug")
    mook = mook_file(window, "Mook", minion=True)
    window._set_in_scene(SCENE_NPC, mook, True)
    use_rules(window, monkeypatch, routine_impress=False)
    calls: list[dict] = []
    monkeypatch.setattr(action_run, "resolve_group", _staged([1], calls))

    _impress(window, mook)._roll.click()

    assert calls[0]["lead"] is not None


def _mixed_impress(window: GMWindow, monkeypatch, lead_die: int) -> dict[str, int]:
    """Impress a full NPC and a minion together; the DC each resisted, by name."""
    mook_file(window, "Thug")
    brute = mook_file(window, "Brute")
    mook = mook_file(window, "Mook", minion=True)
    for name in (brute, mook):
        window._set_in_scene(SCENE_NPC, name, True)
    calls: list[dict] = []

    def fake(lines, *, lead_modifier=None, dc=None, critical_bonus=0, rng=None):
        calls.append({"lines": list(lines), "lead": lead_modifier})
        if lead_modifier is not None:
            return GroupResult(lead_die, lead_die + lead_modifier, ())
        results = tuple(
            CheckResult(10, line[0], 10 + line[0], (dc or 0) + line[1], 1, False) for line in lines
        )
        return GroupResult(None, None, results)

    monkeypatch.setattr(action_run, "resolve_group", fake)
    impress = window._impress.open_new()
    impress._impression.setCurrentIndex(impress._impression.findData("demoralized"))
    for row in impress._rows:
        row.include.setChecked(row.party.source in (brute, mook))
    modifier = impress.check_modifier()
    impress._roll.click()
    lead, lines = calls[0], calls[1]["lines"]
    assert lead["lead"] is not None
    dcs = {"Brute": lines[0][1], "Mook": lines[1][1]}
    dcs["modifier"] = modifier
    return dcs


def test_a_high_roll_counts_against_minions_in_a_mixed_group(window: GMWindow, monkeypatch) -> None:
    dcs = _mixed_impress(window, monkeypatch, 18)
    # The roll beat a routine check: both resist it, the minion no easier.
    assert dcs["Mook"] == dcs["Brute"] == 18 + dcs["modifier"]


def test_a_low_roll_leaves_minions_the_routine_dc(window: GMWindow, monkeypatch) -> None:
    dcs = _mixed_impress(window, monkeypatch, 3)
    assert dcs["Brute"] == 3 + dcs["modifier"]
    assert dcs["Mook"] == 10 + dcs["modifier"] + 5


# -- area attacks ------------------------------------------------------------------


def burst() -> Power:
    return Power(
        name="Fire Burst",
        effects=[
            PowerEffectInstance(
                "damage", rank=8, extras=[ModifierSelection("area_effect", config={})]
            )
        ],
    )


def _parties(data, *, minion: bool):
    hero = Character.new_default(data)
    hero.profile["hero_name"] = "Ana"
    mook = Character.new_default(data)
    mook.profile["hero_name"] = "Mook"
    mook.minion = minion
    source = ImpressParty("s0", "Ana", PARTY_PLAYER, "p1", hero)
    target = ImpressParty("s1", "Mook", PARTY_NPC, "mook.json", mook)
    return source, [source, target]


def test_a_routine_attack_hits_a_minion_without_a_roll(qapp, data) -> None:
    source, targets = _parties(data, minion=True)
    use = power_area_use(burst(), source.character, data)
    dc = defense_class(targets[1].character, data)
    area = AreaWindow(
        data, attempt=AreaAttempt("a", use, primary="s1"), source=source, targets=targets
    )
    try:
        routine = area.routine_attack()
        assert routine == 10 + use.attack
        assert area.hit() == (routine >= dc)
        # A rolled 1 still hits when the routine result would have.
        area.set_attack(1 + use.attack, 1)
        assert area.hit() == (routine >= dc)
        area.set_minion_rules(MinionRules(routine_attacks=False))
        assert area.routine_attack() is None and not area.hit()
    finally:
        area.close()


def _routine_hits(data) -> tuple:
    """An area window whose routine attack on its minion primary hits, nothing rolled."""
    source, targets = _parties(data, minion=True)
    use = power_area_use(burst(), source.character, data)
    area = AreaWindow(
        data, attempt=AreaAttempt("a", use, primary="s1"), source=source, targets=targets
    )
    dc = defense_class(targets[1].character, data)
    # Lift the attack until 10 + attack reaches the Defense, whatever the default sheet is.
    area.set_attack_adjust(max(0, dc - (10 + use.attack)))
    assert area.hit()
    return area, targets


def test_a_session_without_routine_attacks_unticks_the_routine_hit(qapp, data) -> None:
    area, _targets = _routine_hits(data)
    try:
        area.set_minion_rules(MinionRules(routine_attacks=False))
        assert not area.hit()
    finally:
        area.close()


def test_a_primary_that_is_no_minion_unticks_the_routine_hit(qapp, data) -> None:
    area, targets = _routine_hits(data)
    try:
        targets[1].character.minion = False
        area._on_primary()
        assert not area.hit()
    finally:
        area.close()


def test_a_lowered_adjustment_unticks_the_routine_hit(qapp, data) -> None:
    area, _targets = _routine_hits(data)
    try:
        area.set_attack_adjust(-20)
        assert not area.hit()
    finally:
        area.close()


def test_a_hit_the_gm_ticked_stays_whatever_the_rules(qapp, data) -> None:
    area, _targets = _routine_hits(data)
    try:
        area.set_hit(True)
        area.set_minion_rules(MinionRules(routine_attacks=False))
        assert area.hit()
    finally:
        area.close()


def test_a_critical_hit_on_a_minion_primary_needs_no_save(
    window: GMWindow, data, monkeypatch
) -> None:
    source, targets = _parties(data, minion=True)
    mook_name = mook_file(window, "Mook", minion=True)
    targets[1].source = mook_name
    targets[1].character = window._npc_state[mook_name].character
    monkeypatch.setattr(window, "impress_parties", lambda: ([source], targets))
    calls: list[dict] = []
    monkeypatch.setattr(action_run, "resolve_group", _staged([], calls))
    use = power_area_use(burst(), source.character, data)
    window._area.declare("p1", AreaAttempt("act", use, primary="s1").to_dict())
    area = window._area.open("act")
    spec = RollSpec(label="Fire Burst: area attack", action_id="act", kind="power-check")
    window._area.take_roll(
        {"seq": 9, "die": 20, "bonus": 6, "penalty": 0, "critical": True, "spec": spec.to_dict()}
    )
    assert area.hit()
    area._resolve.click()

    assert calls == []  # nothing rolled: not the Dodge, not the save
    conditions = {c.condition_id for c in window._npc_state[mook_name].character.conditions}
    assert "incapacitated" in conditions and "dying" not in conditions


# -- answering a save off the history ------------------------------------------------


def _damage_save(data) -> RollSpec:
    ladder = effect_spec_ladder(PowerEffectInstance("damage", rank=10), data)
    return RollSpec(
        label="Toughness",
        dc=20,
        kind="power-save",
        trait_key="TOUGHNESS",
        ladder=ladder,
        outcomes=("Dazed", "Stunned", "Incapacitated"),
    )


def test_a_critical_attack_is_one_a_minion_takes_unrolled(data) -> None:
    attack = RollSpec(label="Punch", follow_up=_damage_save(data))
    roll = {"seq": 4, "die": 20, "critical": True, "spec": attack.to_dict()}
    assert answer_context(roll).critical
    assert not answer_context({**roll, "minion": True}).critical
    assert not answer_context({**roll, "die": 19}).critical


def test_a_minion_answering_a_save_lands_the_worst_and_its_card_says_so(
    window: GMWindow, data, monkeypatch
) -> None:
    mook = mook_file(window, "Mook", minion=True)
    window._set_in_scene(SCENE_NPC, mook, True)
    party = next(p for p in window.impress_parties()[1] if p.source == mook)
    sent: list[tuple[RollSpec, bool]] = []
    monkeypatch.setattr(
        window._roller,
        "roll_spec",
        lambda s, minion=False: sent.append((s, minion)) or True,
    )
    window._roll_as(_damage_save(data), party, AnswerContext(seq=3))
    ((spec, minion),) = sent
    assert minion and spec.outcomes == ("Incapacitated",)
    record = {
        "seq": -7,
        "kind": "roll",
        "player_name": "GM",
        "label": spec.label,
        "die": 5,
        "bonus": 0,
        "penalty": 0,
        "dc": 20,
        "degree": -1,
        "spec": spec.to_dict(),
    }
    window._on_answer_back(record)
    conditions = {c.condition_id for c in window._npc_state[mook].character.conditions}
    assert "incapacitated" in conditions


def test_a_critical_on_a_minion_from_the_history_needs_no_roll(
    window: GMWindow, data, monkeypatch
) -> None:
    mook = mook_file(window, "Mook", minion=True)
    window._set_in_scene(SCENE_NPC, mook, True)
    party = next(p for p in window.impress_parties()[1] if p.source == mook)
    rolled: list = []
    monkeypatch.setattr(window._roller, "roll_spec", lambda *a, **k: rolled.append(a) or True)
    from mm_companion.core.rules import critical_rung

    rung = critical_rung(_damage_save(data).ladder.rungs, data)
    window._crit_minion(party, rung, AnswerContext(seq=None, source="Ana", critical=True))
    assert rolled == []
    conditions = {c.condition_id for c in window._npc_state[mook].character.conditions}
    assert "incapacitated" in conditions


# -- the roller --------------------------------------------------------------------


def test_take_ten_rolls_a_ten_once(qapp) -> None:
    panel = DiceRollerPanel()
    seen: list[dict] = []
    panel.localRoll.connect(seen.append)
    panel._routine_check.setChecked(True)
    panel._start_roll()
    panel._finish_roll()
    (roll,) = seen
    assert roll["die"] == 10 and roll["routine"]
    assert not panel._routine_check.isChecked()  # one roll's worth
    assert "routine 10" in roll_headline({"die": 10, "routine": True})
    assert not panel.reroll()  # nothing was rolled, so there is no luck to have


def test_a_minions_twenty_is_no_critical_on_the_roller(qapp, monkeypatch) -> None:
    from mm_companion.ui import dice_roller

    monkeypatch.setattr(dice_roller, "roll_d20", lambda *_a: 20)
    panel = DiceRollerPanel()
    seen: list[dict] = []
    panel.localRoll.connect(seen.append)
    panel.roll_spec(RollSpec(label="Punch", modifier=0, dc=25), minion=True)
    panel._finish_roll()
    result = seen[-1]["result"]
    assert result.degree == -1 and not result.critical and seen[-1]["minion"]


# -- the Session settings page --------------------------------------------------------


class FakeBridge(QObject):
    """Just enough of a session bridge: its shared state, and whether it is live."""

    modStateChanged = Signal(str, str, object)
    connected = Signal(object)
    disconnected = Signal(str)
    started = Signal(str, int)
    stopped = Signal()

    def __init__(self, *, live: bool = True) -> None:
        super().__init__()
        self.live = live
        self.state: dict[str, dict[str, dict]] = {}

    @property
    def in_session(self) -> bool:
        return self.live

    def mod_state(self, mod_id: str = "") -> dict:
        return self.state.get(mod_id, {}) if mod_id else self.state

    def set_mod_state(self, mod_id: str, key: str, payload: dict | None) -> bool:
        self.state.setdefault(mod_id, {})[key] = dict(payload or {})
        self.modStateChanged.emit(mod_id, key, payload)
        return True


def test_with_no_session_the_page_is_the_book_and_greyed_out(qapp) -> None:
    page = SessionPage(bridge=FakeBridge(live=False))
    assert not page._minions_box.isEnabled() and not page._save_button.isEnabled()
    assert all(page.check(key).isChecked() for key in MinionRules().to_dict())
    assert not page.is_dirty()


def test_save_publishes_to_the_session_and_writes_nothing_locally(qapp) -> None:
    bridge = FakeBridge()
    page = SessionPage(bridge=bridge)
    before = storage.load_settings()
    page.check("worst_degree").setChecked(False)
    assert page.is_dirty()
    page.save()
    assert not page.is_dirty()
    assert session_minion_rules(bridge) == MinionRules(worst_degree=False)
    assert storage.load_settings() == before
    # Another session — a fresh bridge — starts from the book.
    assert session_minion_rules(FakeBridge()) == MinionRules()


def test_saving_keeps_the_rest_of_the_sessions_settings(qapp) -> None:
    bridge = FakeBridge()
    bridge.set_mod_state(TABLE_MOD_ID, TABLE_SESSION_KEY, {"later": {"x": True}})
    publish_minion_rules(bridge, MinionRules(show_players=False))
    entry = bridge.mod_state(TABLE_MOD_ID)[TABLE_SESSION_KEY]
    assert entry["later"] == {"x": True} and entry["minions"]["show_players"] is False


def test_the_page_follows_the_session_it_is_looking_at(qapp) -> None:
    bridge = FakeBridge()
    page = SessionPage(bridge=bridge)
    publish_minion_rules(bridge, MinionRules(crit_auto_fails=False))
    assert not page.check("crit_auto_fails").isChecked()


def test_only_the_gm_window_offers_the_session_page(window: GMWindow) -> None:
    window.open_session_settings()
    titles = [p.title for p in window._settings_window._pages]
    assert titles[-1] == "Session"
    assert window._settings_window._stack.currentWidget().title == "Session"
    window._settings_window.close()
    assert "Session" not in [p.title for p in SettingsWindow()._pages]


def test_the_gm_window_reads_the_rules_off_its_session(window: GMWindow, monkeypatch) -> None:
    bridge = FakeBridge()
    publish_minion_rules(bridge, MinionRules(no_added_success=False))
    real = window._bridge
    monkeypatch.setattr(window, "_bridge", bridge)
    assert window.minion_rules() == MinionRules(no_added_success=False)
    monkeypatch.setattr(window, "_bridge", real)  # the fixture stops the real one
    monkeypatch.setattr(window, "_bridge", bridge)
    assert not window.rolls_as_minion(Character(minion=True))
    monkeypatch.setattr(window, "_bridge", real)


# -- the summoned tag ---------------------------------------------------------------


def test_the_summoned_tag_round_trips_only_when_set(data) -> None:
    char = Character.new_default(data)
    assert "summoned" not in char.to_dict()
    char.summoned = True
    assert Character.from_dict(char.to_dict()).summoned


def test_the_card_tags_a_creature_summoned_and_the_scene_says_so(window: GMWindow) -> None:
    name = mook_file(window, "Imp")
    window._set_in_scene(SCENE_NPC, name, True)
    window._set_npc_summoned(name, True)
    entry = window._npc_state[name]
    assert entry.character.summoned
    assert library.load_character(entry.path).summoned
    assert "summoned" in card_of(window, name)._pl_label.text()
    (item,) = [e for e in window._scene_payload() if e.get("name") == "Imp"]
    assert item.get("summoned") is True
    window._set_npc_summoned(name, False)
    assert not window._npc_state[name].character.summoned


def test_a_scene_card_says_summoned_and_minion_on_one_line(qapp, data) -> None:
    card = SceneCard({"ref": "s1", "name": "Imp", "summoned": True}, data, gm=False)
    assert card.summoned and card._minion_label.text() == "Summoned"
    card.set_entry({"ref": "s1", "name": "Imp", "summoned": True, "minion": True})
    assert card._minion_label.text() == "Minion · Summoned"


def test_the_wire_keeps_the_summoned_tag() -> None:
    from mm_companion.core.session.protocol import sanitize_scene

    (entry,) = sanitize_scene([{"ref": "abc", "name": "Imp", "summoned": True}])
    assert entry.get("summoned") is True
    (plain,) = sanitize_scene([{"ref": "abc", "name": "Imp", "summoned": "yes"}])
    assert "summoned" not in plain


def test_the_npc_sheet_has_an_undoable_summoned_box(qapp) -> None:
    window = NPCWindow()
    try:
        box = window.sheet.system_info.summoned_check
        assert box.isVisibleTo(window)
        box.click()
        assert window.sheet.character.summoned
        window.sheet.undo.undo()
        assert not window.sheet.character.summoned and not box.isChecked()
    finally:
        window._dirty = False
        window.close()

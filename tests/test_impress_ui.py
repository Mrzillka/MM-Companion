"""The Impress action's windows, and the GM's side of running one.

Offscreen Qt. The rules are proven headless in ``test_impress_rules.py``; what is
proven here is that the windows hand those rules the right inputs, that a GM's roll
lands conditions on the right creature, that a player's declaration waits in the GM's
history, and that a whole attempt survives the trip through a real session — a player
declaring, the GM asking them for their check, the server rolling the rest.
"""

from __future__ import annotations

import random
import time
from dataclasses import replace
from pathlib import Path

import pytest
from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import QApplication, QComboBox, QWidget

from mm_companion.core import library, storage
from mm_companion.core.character import Character
from mm_companion.core.data_loader import ActionModifier, load_game_data
from mm_companion.core.dice import CheckResult, GroupResult
from mm_companion.core.rules import (
    KIND_BEST_OF,
    KIND_IMPRESS,
    MODE_REQUEST,
    MODE_RESOLVE,
    ImpressAttempt,
    RollSpec,
    best_of_key,
)
from mm_companion.core.session import client as client_mod
from mm_companion.core.session import discovery
from mm_companion.core.session.client import SessionClient
from mm_companion.ui import action_run, impress_gm
from mm_companion.ui.action_ui import (
    KIND_OUTCOME,
    KIND_ROLL_ASK,
    KIND_ROLL_CLOSED,
    PHASE_RUNNING,
    RollPromptDialog,
)
from mm_companion.ui.gm_window import SCENE_NPC, GMWindow
from mm_companion.ui.impress import (
    IMPRESS_ACTION,
    PARTY_NPC,
    PARTY_PLAYER,
    ImpressDialog,
    ImpressParty,
    ImpressWindow,
    ModifiersPanel,
    _fill_impressions,
)
from mm_companion.ui.roll_history import ActionCard, GroupTableCard, SessionRollCard
from mm_companion.ui.sections.dice import DiceSection
from mm_companion.ui.session_bridge import set_active_session
from mm_companion.ui.session_dialogs import HostOptions


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
    for win in list(made._impress.windows.values()):
        win.close()
    made.bridge.stop()


def talker(data, name: str = "Thug") -> Character:
    character = Character.new_default(data)
    character.profile["hero_name"] = name
    character.abilities["PRE"] = 2
    character.skill_ranks["Intimidation"] = 6
    return character


def write_npc(data, name: str) -> Path:
    return library.save_character(
        talker(data, name), directory=storage.get_workspace().gm_characters_dir
    )


def wait_until(qapp: QApplication, predicate, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        qapp.processEvents()
        time.sleep(0.01)
    raise AssertionError("condition never became true")


def staged(lead_die: int, lead_total: int, degrees: list[int]):
    """A stand-in for resolve_group: the check rolls *lead_total*, then each target
    fails or makes it by the given degrees — two calls, as the GM's resolve makes them."""

    def fake(lines, *, lead_modifier=None, dc=None, critical_bonus=0, rng=None):
        if lead_modifier is not None:
            return GroupResult(lead_die, lead_total, ())
        results = tuple(
            CheckResult(10, modifier, 10 + modifier, (dc or 0) + offset, degree, False)
            for (modifier, offset), degree in zip(lines, degrees, strict=False)
        )
        return GroupResult(None, None, results)

    return fake


# -- the modifiers ----------------------------------------------------------------


def test_the_modifiers_panel_is_built_from_data_and_sums_like_the_rules(qapp, data) -> None:
    panel = ModifiersPanel(data)
    panel.set_values({"simple_action": True, "mood": 3})
    assert panel.total() == -5 + 3
    # An exclusive pair: checking full action turns simple action off.
    panel._toggles["full_action"].setChecked(True)
    assert panel.values()["simple_action"] is False
    assert panel.total() == 5 + 3
    # A range only stops on its own steps, counted up from its minimum (-20).
    spin = panel._ranges["previous_attempts"]
    assert panel.values()["previous_attempts"] == 0
    spin.setValue(2)
    assert panel.values()["previous_attempts"] == -10
    assert spin.text() == "-10"
    panel.set_values({"previous_attempts": -15})
    assert spin.value() == 1
    # A typed value snaps onto the scale.
    spin.lineEdit().setText("-12")
    spin.interpretText()
    assert panel.values()["previous_attempts"] == -15


def test_the_modifiers_fold_away_and_say_what_they_add_up_to(qapp, data) -> None:
    panel = ModifiersPanel(data)
    assert not panel.is_expanded() and panel._sum.text() == "none"
    panel.set_values({"mood": 3})
    # Setting one that counts unfolds the panel, so the GM sees what is in the check.
    assert panel.is_expanded() and panel._sum.text() == "+3"


def test_a_slider_whose_range_is_off_its_step_still_lands_on_values_it_may_take(qapp, data) -> None:
    """-3..3 in twos: positions are -3, -1, 1, 3, and 0 snaps down to -1."""
    odd = ActionModifier(
        id="odd", label="Odd", kind="slider", value=0, minimum=-3, maximum=3, step=2, exclusive=""
    )
    data = replace(data, impress=replace(data.impress, modifiers=(odd,)))
    panel = ModifiersPanel(data)
    spin = panel._ranges["odd"]
    assert (spin.minimum(), spin.maximum()) == (0, 3)
    assert panel.values()["odd"] == -1
    spin.setValue(spin.maximum())
    assert panel.values()["odd"] == 3
    assert panel.total() == 3


# -- the impresser's dialog ----------------------------------------------------------


def test_offline_the_dialog_rolls_a_check_that_carries_the_resistance(qapp, data) -> None:
    dialog = ImpressDialog(data, talker(data))
    dialog._impression.setCurrentIndex(dialog._impression.findData("demoralized"))
    # Demoralized suggests Intimidation, so it is preselected.
    assert dialog.skill().label == "Intimidation"
    dialog.modifiers.set_values({"full_action": True})
    spec = dialog.offline_spec(source_name="Thug")
    assert spec.kind == KIND_IMPRESS
    assert spec.modifier == 8 + 5
    assert spec.follow_up is not None and spec.follow_up.dc_from_total
    assert "Full action" in spec.hint


def test_an_advantage_gated_impression_is_shown_but_cannot_be_picked(qapp, data) -> None:
    dialog = ImpressDialog(data, talker(data))
    index = dialog._impression.findData("dazed")
    item = dialog._impression.model().item(index)
    assert not item.isEnabled()
    assert "Dazing Interaction" in dialog._impression.itemData(index, Qt.ItemDataRole.ToolTipRole)


def test_online_the_dialog_takes_targets_the_way_an_area_does(qapp, data) -> None:
    scene = [
        {"ref": "s1", "name": "Me", "player_id": "p-me"},
        {"ref": "s2", "name": "Goon"},
        {"ref": "s3", "name": "Other Goon"},
    ]
    dialog = ImpressDialog(data, talker(data), scene=scene, own_player_id="p-me")
    # You cannot impress yourself.
    assert not dialog.targets.field.add_ref("s1")
    assert not dialog._ok.isEnabled()  # resolve mode needs a target
    assert dialog.targets.field.add_ref("s2")
    assert dialog.targets.field.add_ref("s3")
    assert dialog._ok.isEnabled()
    assert "group -5" in dialog._total.text()
    attempt = dialog.attempt("a1", source_name="Me")
    assert attempt.targets == ("s2", "s3")
    assert attempt.skill_modifier == dialog.skill().modifier
    assert ImpressAttempt.from_dict(attempt.to_dict()) == attempt


def test_the_dice_block_offers_impress_on_its_action_row(qapp, data) -> None:
    section = DiceSection(data, talker(data))
    buttons = section.panel._action_buttons
    assert [b.text() for b in buttons] == [IMPRESS_ACTION[1]]
    assert section.panel._action_label.isVisibleTo(section.panel)


def test_offline_the_dice_block_rolls_the_check_itself(qapp, data, monkeypatch) -> None:
    section = DiceSection(data, talker(data))
    rolled: list[RollSpec] = []
    monkeypatch.setattr(section.view.panel, "roll_spec", rolled.append)
    dialog = section.start_action(KIND_IMPRESS)
    # Modeless: the board behind it is where targets are dragged from.
    assert dialog is not None and not dialog.isModal()
    dialog.accept()
    assert len(rolled) == 1 and rolled[0].kind == KIND_IMPRESS


class _PlayerBridge:
    """A player's live session, as the sheet's Dice block sees it."""

    in_session = True
    is_gm = False

    def __init__(self, rules: dict | None = None) -> None:
        self.sent: list = []
        self.rules = rules or {}

    def mod_state(self, mod_id=""):
        return self.rules

    def scene(self):
        return [{"ref": "s2", "name": "Goon"}]

    def own_player_id(self):
        return "me"

    def send_action_request(self, kind, action_id, payload=None):
        self.sent.append((kind, payload))
        return True


def test_at_a_table_impress_goes_to_the_gm_bare_by_default(qapp, data, monkeypatch) -> None:
    from mm_companion.ui.sections import dice as dice_module

    bridge = _PlayerBridge()
    monkeypatch.setattr(dice_module, "live_session", lambda: bridge)
    section = DiceSection(data, talker(data, "Nova"))
    assert section.start_action(KIND_IMPRESS) is None
    ((kind, payload),) = bridge.sent
    attempt = ImpressAttempt.from_dict(payload)
    assert kind == KIND_IMPRESS and attempt.bare and attempt.source_name == "Nova"

    storage.set_resolve_actions_myself(True)
    dialog = section.start_action(KIND_IMPRESS)
    assert dialog is not None and dialog.targets is not None
    dialog.close()


# -- the GM, off the air ------------------------------------------------------------------


def _cast(window: GMWindow, data) -> tuple[str, str]:
    """A Thug in the cast and a Mook on the board; their file names."""
    thug = write_npc(data, "Thug")
    mook = write_npc(data, "Mook")
    window._register_npc(thug)
    window._register_npc(mook)
    window._set_in_scene(SCENE_NPC, mook.name, True)
    return thug.name, mook.name


def test_the_gm_rolls_an_npc_impression_and_the_target_takes_the_rung(
    qapp, window: GMWindow, data, monkeypatch
) -> None:
    thug, mook = _cast(window, data)
    monkeypatch.setattr(action_run, "resolve_group", staged(12, 20, [-2]))

    impress = window._impress.open_new()
    assert impress is not None
    assert impress.source().name == "Thug"
    impress._impression.setCurrentIndex(impress._impression.findData("demoralized"))
    (row,) = impress._rows
    row.include.setChecked(True)
    impress.modifiers.set_values({"mood": 2})
    assert impress.check_modifier() == 8 + 2
    impress._roll.click()

    # Two degrees of failure on Demoralized: Frightened of the Thug.
    model = window._npc_state[mook].character
    assert [(c.condition_id, c.parameter) for c in model.conditions] == [("frightened", "Thug")]
    # One table for the whole action: the check, then the Mook's row and what it did.
    table = next(c for c in window._history.cards() if isinstance(c, GroupTableCard))
    lines = table.lines()
    assert len(lines) == 3 and "Thug: Demoralized" in lines[0]
    assert "Mook" in lines[2] and "Frightened" in lines[2]
    assert "Frightened" in window._notice_label.text()
    # The window stays, and says what happened.
    # The check's total is the die and the modifier: 12 + 10.
    assert "DC <b>22</b>" in impress.check_text()
    assert "Frightened" in impress.result_text(row.party.ref)
    action = next(c for c in window._history.cards() if isinstance(c, ActionCard))
    assert action.button.isHidden()


def test_a_resisted_impression_changes_nothing(qapp, window: GMWindow, data, monkeypatch) -> None:
    _thug, mook = _cast(window, data)
    monkeypatch.setattr(action_run, "resolve_group", staged(5, 12, [1]))
    impress = window._impress.open_new()
    impress._rows[0].include.setChecked(True)
    impress._roll.click()
    assert window._npc_state[mook].character.conditions == []
    assert "resisted" in window._notice_label.text()


def test_a_natural_twenty_raises_the_dc_by_the_critical_bonus(
    qapp, window: GMWindow, data, monkeypatch
) -> None:
    _cast(window, data)
    seen: list = []
    fake = staged(20, 18, [1])

    def spy(lines, **kwargs):
        seen.append(lines)
        return fake(lines, **kwargs)

    monkeypatch.setattr(action_run, "resolve_group", spy)
    impress = window._impress.open_new()
    impress._rows[0].include.setChecked(True)
    impress._rows[0].modifier.setValue(2)
    modifier = impress.check_modifier()
    impress._roll.click()
    _check, resist = seen
    assert resist[0][1] == 20 + modifier + data.system.critical_effect_bonus + 2


def test_the_gm_sets_a_bonus_or_penalty_on_the_impressers_check(qapp, window, data) -> None:
    _cast(window, data)
    impress = window._impress.open_new()
    base = impress.check_modifier()
    impress.set_check_adjust(3)
    assert impress.check_modifier() == base + 3
    impress.set_check_adjust(-4)
    assert impress.check_modifier() == base - 4
    assert impress.plan().check_modifier == base - 4
    impress.close()


def test_request_mode_rolls_only_the_check_with_a_button_to_resist(
    qapp, window: GMWindow, data
) -> None:
    _cast(window, data)
    impress = window._impress.open_new()
    impress.set_mode(MODE_REQUEST)
    # Request only needs nobody ticked: the card is for whoever is on the receiving end.
    assert impress._roll.isEnabled()
    impress._roll.click()
    (card,) = [c for c in window._history.cards() if isinstance(c, SessionRollCard)]
    spec = RollSpec.from_dict(card.roll["spec"])
    assert spec.follow_up is not None and spec.follow_up.dc_from_total


def test_the_gm_needs_a_cast_to_impress_with(qapp, window: GMWindow) -> None:
    assert window._impress.open_new() is None
    assert "NPC" in window._notice_label.text()


def test_an_npc_cards_impress_button_picks_that_npc(qapp, window: GMWindow, data) -> None:
    thug, mook = _cast(window, data)
    window._npc_state[mook].card._impress_button.click()
    (impress,) = window._impress.windows.values()
    assert impress.source().source == mook
    impress.close()


# -- the GM window on its own ---------------------------------------------------------------


def _gm_window(data, *, mode: str = "") -> tuple[ImpressWindow, list[str]]:
    """An NPC Thug impressing a player Nova and an NPC Mook; what it signals."""
    thug = talker(data, "Thug")
    thug.skill_ranks["Deception"] = 4
    nova = talker(data, "Nova")
    window = ImpressWindow(
        data,
        action_id="w1",
        sources=[ImpressParty("s-thug", "Thug", PARTY_NPC, "thug.json", thug)],
        targets=[
            ImpressParty("s-nova", "Nova", PARTY_PLAYER, "p-nova", nova),
            ImpressParty("s-mook", "Mook", PARTY_NPC, "mook.json", talker(data, "Mook")),
        ],
    )
    if mode:
        window.set_mode(mode)
    said: list[str] = []
    window.cancelled.connect(lambda: said.append("cancelled"))
    return window, said


def test_escape_settles_the_window_like_cancel_does(qapp, data) -> None:
    """Esc rejects a dialog without a close event — the action must still be let go."""
    window, said = _gm_window(data)
    window.show()
    window.reject()
    assert said == ["cancelled"]


def test_closing_a_rolling_window_only_hides_it(qapp, data) -> None:
    window, said = _gm_window(data)
    window._row("s-mook").include.setChecked(True)
    window.show()
    window._roll.click()
    assert window.phase == PHASE_RUNNING
    assert not window._row("s-mook").include.isEnabled()  # locked once rolled
    window.close()
    assert said == []  # nothing called off: the rolls are still on their way


def test_a_target_resists_with_its_highest_unless_the_gm_picks(qapp, data) -> None:
    window, _said = _gm_window(data)
    nova = window._row("s-nova")
    nova.include.setChecked(True)
    assert nova.uses_highest() and nova.choice.currentText().startswith("Highest")
    # A player's "Highest" is theirs to pick when asked.
    tip = nova.choice.itemData(0, Qt.ItemDataRole.ToolTipRole)
    assert "when asked" in tip
    nova.choice.setCurrentIndex(nova.choice.findData("resistance:WILL"))
    (target,) = window.plan().targets
    assert not target.highest and target.resist.key == "WILL"


def test_request_mode_locks_the_per_target_controls(qapp, data) -> None:
    window, _said = _gm_window(data, mode=MODE_REQUEST)
    nova = window._row("s-nova")
    nova.include.setChecked(True)
    assert not nova.modifier.isEnabled() and not nova.choice.isEnabled()
    window.set_mode(MODE_RESOLVE)
    assert nova.modifier.isEnabled() and nova.choice.isEnabled()


def test_an_impression_the_impresser_cannot_make_is_not_kept_selected(qapp, data) -> None:
    combo = QComboBox()
    _fill_impressions(combo, data, talker(data), current="dazed")
    assert combo.currentData() != "dazed"
    # With nobody to ask (a player's declared attempt) the GM is the judge.
    _fill_impressions(combo, data, None, current="dazed")
    assert combo.currentData() == "dazed"


def test_a_roll_during_a_blip_fails_visibly_rather_than_rolling_here(qapp, data) -> None:
    class Bridge(QObject):
        actionRequested = Signal(str, str, str, object)
        rollAdded = Signal(object)
        in_session = True  # in a session, but the socket is down for a moment
        joined = False
        hosting = False
        is_gm = True

        def group_roll(self, *args, **kwargs) -> bool:
            return False

        def prompt_action(self, *args, **kwargs) -> bool:
            return False

        def prompt_roll(self, *args, **kwargs) -> bool:
            return False

        def own_player_id(self) -> str:
            return "gm"

    class Host:
        def __init__(self) -> None:
            self.offline: list[dict] = []
            self.notices: list[str] = []

        def impress_parties(self):
            return [], []

        def impress_apply(self, party, changes) -> bool:
            return True

        def impress_show_offline(self, record: dict) -> None:
            self.offline.append(record)

        def impress_notice(self, text: str, colour: str) -> None:
            self.notices.append(text)

        def action_add_card(self, card) -> None:
            pass

    parent = QWidget()
    host = Host()
    controller = impress_gm.ImpressController(data, Bridge(), host, parent)
    window, _said = _gm_window(data)
    window._row("s-mook").include.setChecked(True)
    action = controller._make("w1", [window.source()], None, from_card=False)
    controller._roll(action, window.plan())
    assert host.offline == []
    assert host.notices and "did not reach" in host.notices[0]


# -- the player's prompt ------------------------------------------------------------------


def test_a_roll_prompt_offers_the_highest_and_lets_the_player_pick(qapp, data) -> None:
    hero = talker(data, "Hero")
    hero.abilities["AWE"] = 4
    hero.skill_ranks["Insight"] = 6
    rolled: list[RollSpec] = []
    spec = RollSpec(
        label="Resist Demoralized",
        dc=21,
        kind=KIND_BEST_OF,
        trait_key=best_of_key(
            [("skill", "Intimidation"), ("skill", "Insight"), ("resistance", "WILL")]
        ),
        targets=("Hero",),
        action_id="a1",
        title="Thug: Demoralized",
    )
    dialog = RollPromptDialog(
        data,
        hero,
        action_id="a1",
        spec=spec,
        asker="The GM",
        roll=lambda s: rolled.append(s) or True,
    )
    assert "Insight" in dialog._choice.itemText(0) and "+10" in dialog._caption.text()
    dialog._choice.setCurrentIndex(dialog._choice.findData("WILL"))
    assert "Will" in dialog._caption.text()
    dialog._roll.click()
    (made,) = rolled
    assert made.trait_key == "WILL" and made.dc == 21 and made.action_id == "a1"


def test_the_player_end_opens_closes_and_tells(qapp, data, monkeypatch) -> None:
    from mm_companion.ui import session_player
    from mm_companion.ui.character_sheet import CharacterSheet

    class Bridge(QObject):
        actionPrompted = Signal(str, str, object)

    class Win(QWidget):
        def __init__(self) -> None:
            super().__init__()
            self.sheet = CharacterSheet(data, talker(data, "Nova"))

    told: list[str] = []
    monkeypatch.setattr(
        session_player, "notify_window", lambda w, text, colour="": told.append(text)
    )
    win = Win()
    bridge = Bridge()
    receiver = session_player.ActionPromptReceiver(win, bridge, parent=win)
    spec = RollSpec(label="Dodge", dc=18, kind="area-dodge", targets=("Nova",), action_id="a1")
    bridge.actionPrompted.emit(KIND_ROLL_ASK, "a1", {"spec": spec.to_dict(), "asker": "The GM"})
    dialog = receiver.dialogs["a1"]
    assert dialog.isVisible() and "Dodge" in dialog._caption.text()
    # The next roll of the same action replaces it in place.
    save = replace(spec, label="Toughness", dc=23)
    bridge.actionPrompted.emit(KIND_ROLL_ASK, "a1", {"spec": save.to_dict()})
    assert receiver.dialogs["a1"] is dialog and "Toughness" in dialog._caption.text()
    bridge.actionPrompted.emit(KIND_ROLL_CLOSED, "a1", {"name": "Nova"})
    assert "a1" not in receiver.dialogs
    bridge.actionPrompted.emit(KIND_OUTCOME, "a1", {"text": "Fire Burst: Dazed"})
    assert told == ["Fire Burst: Dazed"]


# -- through a real session ------------------------------------------------------------------


class Events:
    def __init__(self) -> None:
        self.seen: list[tuple[str, dict]] = []

    def __call__(self, kind: str, payload: dict) -> None:
        self.seen.append((kind, payload))

    def of(self, kind: str) -> list[dict]:
        return [payload for k, payload in self.seen if k == kind]


def _host(qapp, window: GMWindow) -> None:
    window.bridge._publish_session = lambda port, **kw: discovery.Reachability(
        host="127.0.0.1", port=port, method=discovery.METHOD_LAN
    )
    window.start_hosting(HostOptions(name="Table", port=0, tunnel="", relay="", use_relay=False))
    wait_until(qapp, lambda: window.bridge.hosting)


def _join(qapp, window: GMWindow, data, name: str = "Nova") -> tuple[SessionClient, Events]:
    server = window.bridge.server
    host, port = server.address
    events = Events()
    player = SessionClient(
        "127.0.0.1" if host == "0.0.0.0" else host,
        port,
        token=server.state.host_token,
        display_name=name,
        on_event=events,
    )
    player.connect(timeout=5.0)
    player.send_snapshot(talker(data, name).to_dict())
    wait_until(qapp, lambda: player.player_id in window._snapshots)
    wait_until(qapp, lambda: any(e.source == player.player_id for e in window._scene))
    return player, events


def _prompts(events: Events, kind: str) -> list[dict]:
    return [p for p in events.of(client_mod.EVENT_ACTION_PROMPT) if p["kind"] == kind]


def test_the_table_hears_whether_players_choose(qapp, window: GMWindow, data) -> None:
    from mm_companion.ui.action_ui import players_may_choose

    _host(qapp, window)
    assert players_may_choose(window.bridge)
    storage.set_gm_players_choose_actions(False)
    window.publish_table_rules()
    assert not players_may_choose(window.bridge)


def test_a_players_impression_waits_in_the_history_and_they_roll_the_check(
    qapp, window: GMWindow, data, monkeypatch
) -> None:
    _host(qapp, window)
    thug_path = write_npc(data, "Thug")
    window._register_npc(thug_path)
    window._set_in_scene(SCENE_NPC, thug_path.name, True)
    player, events = _join(qapp, window, data)
    try:
        thug_ref = next(e.ref for e in window._scene if e.kind == SCENE_NPC)
        nova_ref = next(e.ref for e in window._scene if e.source == player.player_id)

        attempt = ImpressAttempt(
            action_id="act1",
            impression="distracted",
            skill_row="Deception",
            skill_label="Deception",
            skill_modifier=4,
            targets=(thug_ref,),
            source_name="Nova",
        )
        player.send_action_request(KIND_IMPRESS, attempt.action_id, attempt.to_dict())
        # It waits in the GM's history, the way an area attack does.
        wait_until(qapp, lambda: "act1" in window._impress.actions)
        assert "act1" not in window._impress.windows
        card = next(c for c in window._history.cards() if isinstance(c, ActionCard))
        assert card.button.text() == impress_gm.RESOLVE_CAPTION and "Distracted" in card.text()
        card.button.click()
        impress: ImpressWindow = window._impress.windows["act1"]
        assert impress.source().source == player.player_id
        assert [r.party.ref for r in impress.included()] == [thug_ref]
        assert not impress._source.isEnabled()  # the player's choices are theirs
        assert not impress._impression.isEnabled()
        # An impresser is never offered as their own target.
        assert all(r.party.ref != nova_ref for r in impress._rows)

        impress.set_check_adjust(2)
        impress._roll.click()
        # The check is the player's to roll: they are asked, and nothing is rolled yet.
        wait_until(qapp, lambda: _prompts(events, KIND_ROLL_ASK))
        ask = RollSpec.from_dict(_prompts(events, KIND_ROLL_ASK)[0]["payload"]["spec"])
        assert ask.modifier == 4 + 2 and ask.action_id == "act1" and ask.targets == ("Nova",)
        assert not [r for r in events.of(client_mod.EVENT_ROLL) if r.get("group")]

        monkeypatch.setattr(window.bridge.server, "_rng", random.Random(1))
        player.request_roll(ask.label, bonus=ask.modifier, spec=ask.to_dict())
        wait_until(
            qapp,
            lambda: len([r for r in events.of(client_mod.EVENT_ROLL) if r.get("group")]) == 1,
        )
        check = next(
            r
            for r in events.of(client_mod.EVENT_ROLL)
            if not r.get("group") and r.get("kind", "roll") == "roll"
        )
        (line,) = [r for r in events.of(client_mod.EVENT_ROLL) if r.get("group")]
        total = check["die"] + check["bonus"] - check["penalty"]
        # The NPC's resistance is the GM's to roll, against the player's total.
        assert line["player_id"] == window.bridge.server.gm_slot().player_id
        assert line["dc"] == total + (5 if check["die"] == 20 else 0)
        # Their prompt is closed, and the GM's window has the result.
        wait_until(qapp, lambda: _prompts(events, KIND_ROLL_CLOSED))
        assert impress.result_text(thug_ref)
    finally:
        player.close()


def test_a_targeted_player_is_asked_to_roll_once_the_check_is_in(
    qapp, window: GMWindow, data
) -> None:
    _host(qapp, window)
    thug_path = write_npc(data, "Thug")
    window._register_npc(thug_path)
    player, events = _join(qapp, window, data)
    try:
        impress = window._impress.open_new()
        (row,) = [r for r in impress._rows if r.party.source == player.player_id]
        row.include.setChecked(True)
        # Nobody is asked anything before the roll.
        qapp.processEvents()
        assert _prompts(events, KIND_ROLL_ASK) == []
        impress._roll.click()
        wait_until(qapp, lambda: _prompts(events, KIND_ROLL_ASK))
        ask = RollSpec.from_dict(_prompts(events, KIND_ROLL_ASK)[0]["payload"]["spec"])
        assert ask.kind == KIND_BEST_OF and ask.dc is not None and ask.targets == ("Nova",)
        # The same request waits in everyone's history.
        wait_until(
            qapp, lambda: any(r.get("kind") == "request" for r in events.of(client_mod.EVENT_ROLL))
        )
        assert window._impress.actions[impress.action_id].run.waiting() == ["Nova"]

        player.request_roll(ask.label, bonus=8, dc=ask.dc, spec=ask.to_dict())
        wait_until(qapp, lambda: _prompts(events, KIND_ROLL_CLOSED))
        wait_until(qapp, lambda: bool(impress.result_text(row.party.ref)))
        assert "Will" in impress.result_text(row.party.ref) or "Resist" in impress.result_text(
            row.party.ref
        )
    finally:
        player.close()


def test_impress_from_a_sheet_the_gm_has_open_reaches_the_gm_window(
    qapp, window: GMWindow, data
) -> None:
    """The server forwards nothing from the GM to the GM, so the bridge answers it here."""
    _host(qapp, window)
    thug, mook = _cast(window, data)
    attempt = ImpressAttempt(
        action_id="own1",
        impression="demoralized",
        skill_row="Intimidation",
        skill_label="Intimidation",
        skill_modifier=8,
        targets=(next(e.ref for e in window._scene if e.source == mook),),
        source_name="Thug",
    )
    assert window.bridge.send_action_request(KIND_IMPRESS, "own1", attempt.to_dict())
    impress = window._impress.windows["own1"]
    # The sheet's creature is the cast NPC of that name: a creature nobody plays.
    assert impress.source().kind == PARTY_NPC and impress.source().source == thug
    assert [r.party.source for r in impress.included()] == [mook]


def test_a_bare_declaration_lets_the_gm_choose_off_the_players_sheet(
    qapp, window: GMWindow, data
) -> None:
    _host(qapp, window)
    write_npc(data, "Thug")
    player, _events = _join(qapp, window, data)
    try:
        bare = ImpressAttempt("bare1", "", "", "", 0, source_name="Nova")
        player.send_action_request(KIND_IMPRESS, "bare1", bare.to_dict())
        wait_until(qapp, lambda: "bare1" in window._impress.actions)
        impress = window._impress.open("bare1")
        assert impress._impression.isEnabled() and impress._skill.isEnabled()
        # The skills are the player's own, off their snapshot.
        assert {c.label: c.modifier for c in impress._skills}["Intimidation"] == 8
        impress._on_dismiss()
        card = next(c for c in window._history.cards() if isinstance(c, ActionCard))
        assert card.button.isHidden() and "Dismissed" in card.status()
    finally:
        player.close()

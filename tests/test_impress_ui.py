"""The Impress action's windows, and the GM's side of running one.

Offscreen Qt. The rules are proven headless in ``test_impress_rules.py``; what is
proven here is that the windows hand those rules the right inputs, that a GM's roll
lands conditions on the right creature, and that a whole attempt survives the trip
through a real session — a player declaring, a target answering, the server rolling.
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
    KIND_IMPRESS,
    MODE_REQUEST,
    MODE_RESOLVE,
    ImpressAttempt,
    ResistAnswer,
    RollSpec,
    TraitChoice,
)
from mm_companion.core.session import client as client_mod
from mm_companion.core.session import discovery
from mm_companion.core.session.client import SessionClient
from mm_companion.ui import impress_gm
from mm_companion.ui.gm_window import SCENE_NPC, GMWindow
from mm_companion.ui.impress import (
    IMPRESS_ACTION,
    PARTY_NPC,
    PARTY_PLAYER,
    ImpressDialog,
    ImpressParty,
    ImpressWindow,
    ModifiersPanel,
    ResistPromptDialog,
    _fill_impressions,
)
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


def fixed_group(lead_die: int, lead_total: int, degrees: list[int]):
    """A stand-in for resolve_group that fails each target by the given degrees."""

    def fake(lines, *, lead_modifier=None, dc=None, critical_bonus=0, rng=None):
        results = tuple(
            CheckResult(10, modifier, 10 + modifier, lead_total + offset, degree, False)
            for (modifier, offset), degree in zip(lines, degrees, strict=False)
        )
        return GroupResult(lead_die, lead_total, results)

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
    # A slider only stops on its own steps, counted up from its minimum (-20).
    slider, readout, _ = panel._sliders["previous_attempts"]
    assert panel.values()["previous_attempts"] == 0
    slider.setValue(2)
    assert panel.values()["previous_attempts"] == -10
    assert readout.text() == "-10"
    panel.set_values({"previous_attempts": -15})
    assert slider.value() == 1


def test_a_slider_whose_range_is_off_its_step_still_lands_on_values_it_may_take(qapp, data) -> None:
    """-3..3 in twos: positions are -3, -1, 1, 3, and 0 snaps down to -1."""
    odd = ActionModifier(
        id="odd", label="Odd", kind="slider", value=0, minimum=-3, maximum=3, step=2, exclusive=""
    )
    data = replace(data, impress=replace(data.impress, modifiers=(odd,)))
    panel = ModifiersPanel(data)
    slider, _readout, _ = panel._sliders["odd"]
    assert (slider.minimum(), slider.maximum()) == (0, 3)
    assert panel.values()["odd"] == -1
    slider.setValue(slider.maximum())
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
    assert "full action" in spec.hint


def test_an_advantage_gated_impression_is_shown_but_cannot_be_picked(qapp, data) -> None:
    dialog = ImpressDialog(data, talker(data))
    index = dialog._impression.findData("dazed")
    item = dialog._impression.model().item(index)
    assert not item.isEnabled()
    assert "Dazing Interaction" in dialog._impression.itemData(index, Qt.ItemDataRole.ToolTipRole)


def test_online_the_dialog_declares_targets_and_takes_the_group_penalty(qapp, data) -> None:
    scene = [
        {"ref": "s1", "name": "Me", "player_id": "p-me"},
        {"ref": "s2", "name": "Goon"},
        {"ref": "s3", "name": "Other Goon"},
    ]
    dialog = ImpressDialog(data, talker(data), scene=scene, own_player_id="p-me")
    # You cannot impress yourself.
    assert dialog._targets.count() == 2
    assert not dialog._ok.isEnabled()  # resolve mode needs a target
    dialog.set_targets(["s2", "s3"])
    assert dialog._ok.isEnabled()
    assert "-5" in dialog._group_note.text()
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
    monkeypatch.setattr(ImpressDialog, "exec", lambda self: ImpressDialog.DialogCode.Accepted)
    rolled: list[RollSpec] = []
    monkeypatch.setattr(section.view.panel, "roll_spec", rolled.append)
    section.start_action(KIND_IMPRESS)
    assert len(rolled) == 1 and rolled[0].kind == KIND_IMPRESS


# -- the target's prompt ---------------------------------------------------------------


def test_a_target_answers_with_their_highest_by_default(qapp, data) -> None:
    character = talker(data, "Hero")
    character.abilities["AWE"] = 4
    character.skill_ranks["Insight"] = 6
    dialog = ResistPromptDialog(
        data,
        character,
        action_id="a1",
        payload={"impression_name": "Demoralized", "skill_row": "Intimidation"},
    )
    answer = dialog.answer()
    assert answer.use_highest and answer.label == "Insight" and answer.modifier == 10
    dialog._highest.setChecked(False)
    dialog._choice.setCurrentIndex(dialog._choice.findData("resistance:WILL"))
    picked = dialog.answer()
    assert not picked.use_highest and picked.choice == "resistance:WILL"
    assert {o.label for o in picked.options} == {"Intimidation", "Insight", "Will"}


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
    monkeypatch.setattr(impress_gm, "resolve_group", fixed_group(12, 20, [-2]))

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
    cards = window._history.cards()
    assert len(cards) == 2
    assert "Frightened" in window._notice_label.text()


def test_a_resisted_impression_changes_nothing(qapp, window: GMWindow, data, monkeypatch) -> None:
    _thug, mook = _cast(window, data)
    monkeypatch.setattr(impress_gm, "resolve_group", fixed_group(5, 12, [1]))
    impress = window._impress.open_new()
    impress._rows[0].include.setChecked(True)
    impress._roll.click()
    assert window._npc_state[mook].character.conditions == []
    assert "resisted" in window._notice_label.text()


def test_request_mode_rolls_only_the_check_with_a_button_to_resist(
    qapp, window: GMWindow, data
) -> None:
    _cast(window, data)
    impress = window._impress.open_new()
    impress.set_mode(MODE_REQUEST)
    # Request only needs nobody ticked: the card is for whoever is on the receiving end.
    assert impress._roll.isEnabled()
    impress._roll.click()
    (card,) = window._history.cards()
    spec = RollSpec.from_dict(card._roll["spec"])
    assert spec.follow_up is not None and spec.follow_up.dc_from_total


def test_the_gm_needs_a_cast_to_impress_with(qapp, window: GMWindow) -> None:
    assert window._impress.open_new() is None
    assert "NPC" in window._notice_label.text()


# -- the GM window's prompts ---------------------------------------------------------------


def _gm_window(data, *, mode: str = "") -> tuple[ImpressWindow, list[tuple[str, object]]]:
    """An NPC Thug impressing a player Nova and an NPC Mook; every signal it sends."""
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
    said: list[tuple[str, object]] = []
    window.promptNeeded.connect(lambda party: said.append(("ask", party.ref)))
    window.promptsChanged.connect(lambda: said.append(("re-ask", None)))
    window.promptWithdrawn.connect(lambda party: said.append(("withdraw", party.ref)))
    window.cancelled.connect(lambda: said.append(("cancelled", None)))
    return window, said


def _answer(window: ImpressWindow, modifier: int = 9) -> ResistAnswer:
    return ResistAnswer(
        action_id=window.action_id,
        use_highest=False,
        choice="resistance:WILL",
        label="Will",
        modifier=modifier,
        options=(TraitChoice("resistance", "WILL", "Will", modifier),),
    )


def test_escape_settles_the_window_like_cancel_does(qapp, data) -> None:
    """Esc rejects a dialog without a close event — the prompts must still be closed."""
    window, said = _gm_window(data)
    window.show()
    window.reject()
    assert said == [("cancelled", None)]


def test_changing_the_skill_voids_the_answers_given_against_the_old_one(qapp, data) -> None:
    window, said = _gm_window(data)
    nova = window._row("s-nova")
    nova.include.setChecked(True)
    window.mark_prompted("s-nova", True)
    window.set_answer("p-nova", _answer(window))
    assert nova.answered and nova.status.text() == "Answered"

    other = next(i for i in range(window._skill.count()) if i != window._skill.currentIndex())
    window._skill.setCurrentIndex(other)
    # Same-skill is one way to resist, so the old answer no longer applies: the window
    # reads the sheet again and asks once more.
    assert not nova.answered
    assert nova.status.text() == "Waiting…"
    assert ("re-ask", None) in said
    assert {c.label for c in nova.choices} >= {"Insight", "Will"}


def test_untargeting_a_player_withdraws_their_prompt(qapp, data) -> None:
    window, said = _gm_window(data)
    nova = window._row("s-nova")
    nova.include.setChecked(True)
    window.mark_prompted("s-nova", True)
    nova.include.setChecked(False)
    assert ("withdraw", "s-nova") in said
    assert window.prompted_parties() == []
    # An answer arriving after that is to nothing.
    window.set_answer("p-nova", _answer(window))
    assert not nova.answered


def test_request_mode_asks_nobody_and_locks_the_per_target_controls(qapp, data) -> None:
    window, said = _gm_window(data, mode=MODE_REQUEST)
    nova = window._row("s-nova")
    nova.include.setChecked(True)
    assert said == []
    assert not nova.bonus.isEnabled() and not nova.highest.isEnabled()
    window.set_mode(MODE_RESOLVE)
    assert said == [("ask", "s-nova")]
    assert nova.bonus.isEnabled() and nova.highest.isEnabled()


def test_an_unreachable_player_stays_marked_as_such(qapp, data) -> None:
    window, _said = _gm_window(data)
    nova = window._row("s-nova")
    nova.include.setChecked(True)
    window.mark_prompted("s-nova", False)
    nova.bonus.setValue(2)  # any later redraw of the row
    window._sync_row(nova)
    assert nova.status.text().startswith("Not connected")


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

    parent = QWidget()
    host = Host()
    controller = impress_gm.ImpressController(data, Bridge(), host, parent)
    window, _said = _gm_window(data)
    window._row("s-mook").include.setChecked(True)
    controller._roll(window.plan())
    assert host.offline == []
    assert host.notices and "did not reach" in host.notices[0]


# -- a whole attempt, through a real session ------------------------------------------------


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


def test_a_players_impression_runs_through_the_gm_and_back(
    qapp, window: GMWindow, data, monkeypatch
) -> None:
    _host(qapp, window)
    thug_path = write_npc(data, "Thug")
    window._register_npc(thug_path)
    window._set_in_scene(SCENE_NPC, thug_path.name, True)

    server = window.bridge.server
    host, port = server.address
    events = Events()
    player = SessionClient(
        "127.0.0.1" if host == "0.0.0.0" else host,
        port,
        token=server.state.host_token,
        display_name="Nova",
        on_event=events,
    )
    player.connect(timeout=5.0)
    try:
        hero = talker(data, "Nova")
        player.send_snapshot(hero.to_dict())
        wait_until(qapp, lambda: player.player_id in window._snapshots)
        wait_until(qapp, lambda: any(e.source == player.player_id for e in window._scene))
        thug_ref = next(e.ref for e in window._scene if e.kind == SCENE_NPC)
        nova_ref = next(e.ref for e in window._scene if e.source == player.player_id)

        # The player declares; the GM's window opens on it.
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
        wait_until(qapp, lambda: "act1" in window._impress.windows)
        impress: ImpressWindow = window._impress.windows["act1"]
        assert impress.source().source == player.player_id
        assert [r.party.ref for r in impress.included()] == [thug_ref]
        assert not impress._source.isEnabled()  # the player's choices are theirs

        # An impresser is never offered as their own target.
        assert all(r.party.ref != nova_ref for r in impress._rows)

        monkeypatch.setattr(server, "_rng", random.Random(1))
        impress._roll.click()
        wait_until(
            qapp,
            lambda: len([r for r in events.of(client_mod.EVENT_ROLL) if r.get("group")]) == 2,
        )
        lead, line = [r for r in events.of(client_mod.EVENT_ROLL) if r.get("group")]
        # The check is the impresser's, attributed to their seat; the NPC's resistance
        # is the GM's to roll.
        assert lead["player_id"] == player.player_id
        assert line["player_id"] == server.gm_slot().player_id
        assert line["dc"] == lead["die"] + lead["bonus"] - lead["penalty"] + (
            5 if lead["die"] == 20 else 0
        )
    finally:
        player.close()


def test_a_targeted_player_is_asked_and_their_answer_reaches_the_gm(
    qapp, window: GMWindow, data
) -> None:
    _host(qapp, window)
    thug_path = write_npc(data, "Thug")
    window._register_npc(thug_path)
    server = window.bridge.server
    host, port = server.address
    events = Events()
    player = SessionClient(
        "127.0.0.1" if host == "0.0.0.0" else host,
        port,
        token=server.state.host_token,
        display_name="Nova",
        on_event=events,
    )
    player.connect(timeout=5.0)
    try:
        player.send_snapshot(talker(data, "Nova").to_dict())
        wait_until(qapp, lambda: any(e.source == player.player_id for e in window._scene))

        impress = window._impress.open_new()
        (row,) = [r for r in impress._rows if r.party.source == player.player_id]
        row.include.setChecked(True)
        wait_until(qapp, lambda: events.of(client_mod.EVENT_ACTION_PROMPT))
        prompt = events.of(client_mod.EVENT_ACTION_PROMPT)[0]
        assert prompt["kind"] == "resist"
        assert prompt["payload"]["source_name"] == "Thug"
        assert row.status.text() == "Waiting…"

        answer = ResistAnswer(
            action_id=impress.action_id,
            use_highest=False,
            choice="resistance:WILL",
            label="Will",
            modifier=9,
        )
        player.send_action_request("resist", impress.action_id, answer.to_dict())
        wait_until(qapp, lambda: row.answered)
        assert not row.highest.isChecked()
        assert row.status.text() == "Answered"

        # Settling it tells the player their prompt is over.
        impress.close()
        wait_until(
            qapp,
            lambda: any(
                p["kind"] == "resist-closed" for p in events.of(client_mod.EVENT_ACTION_PROMPT)
            ),
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

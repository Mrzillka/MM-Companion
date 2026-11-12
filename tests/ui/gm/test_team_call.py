"""The GM's team attack as a call to roll (combat maneuvers phase 7b, decision 15): the
tile, the call in the history, the entries gathered and judged, Resolve, Call off, and
an NPC joining off the GM's roller."""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QApplication

from mm_companion.core import library
from mm_companion.core.data_loader import load_game_data
from mm_companion.core.npc import quick_npc
from mm_companion.core.rules import (
    KIND_TEAM_CALL,
    RollSpec,
    attack_choices,
    defense_class_in_play,
    team_join_spec,
)
from mm_companion.core.session.model import KIND_REQUEST
from mm_companion.ui import action_run
from mm_companion.ui.gm.combat import TEAM_CALL_OVER
from mm_companion.ui.gm.model import SCENE_NPC
from mm_companion.ui.gm.rolls import TEAM_ATTACK
from mm_companion.ui.gm_window import GMWindow
from mm_companion.ui.roller.combat_runner import TEAM_LINE
from mm_companion.ui.session_bridge import SessionBridge, set_active_session
from mm_companion.ui.team_call import (
    NO_SESSION_FOR_CALL,
    NO_TARGET_FOR_CALL,
    NOBODY_YET,
    TeamCallWindow,
)

pytestmark = pytest.mark.usefixtures("workspace")


@pytest.fixture(autouse=True)
def _clear_active_session():
    yield
    set_active_session(None)


@pytest.fixture
def window(qapp: QApplication) -> GMWindow:
    made = GMWindow(bind="127.0.0.1")
    yield made
    if made._team_calls.window is not None:
        made._team_calls.window.close()
    made.bridge.stop()


class _Wire:
    """The session, faked: what the GM window sends, and a seq counter."""

    def __init__(self) -> None:
        self.prompts: list[dict] = []
        self.removed: list[int] = []


@pytest.fixture
def wire(window: GMWindow, monkeypatch) -> _Wire:
    fake = _Wire()
    monkeypatch.setattr(SessionBridge, "in_session", property(lambda self: True))
    monkeypatch.setattr(
        window.bridge, "prompt_roll", lambda spec: fake.prompts.append(spec) or True
    )
    monkeypatch.setattr(window.bridge, "remove_roll", lambda seq: fake.removed.append(seq) or True)
    return fake


class _Menu:
    """Stands in for ``action_menu``'s ``QMenu``: picks :attr:`picks` in turn."""

    picks: list[int | None] = []
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
        pick = _Menu.picks.pop(0) if _Menu.picks else 0
        return None if pick is None else self.entries[pick]


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

    def setIcon(self, _icon) -> None:  # noqa: N802 - mirrors Qt
        pass


@pytest.fixture
def menu(monkeypatch):
    _Menu.picks, _Menu.shown = [], []
    monkeypatch.setattr("mm_companion.ui.action_menu.QMenu", _Menu)
    return _Menu


def _npc(window: GMWindow, name: str, *, scene: bool = True, defence: int = 6) -> str:
    character = quick_npc(
        load_game_data(), name=name, attack=6, effect=6, defence=defence, toughness=6
    )
    path = library.save_character(character, directory=window._npc_dir())
    window._register_npc(path)
    if scene:
        window._set_in_scene(SCENE_NPC, path.name, True)
    return path.name


def _ref(window: GMWindow, name: str) -> str:
    return window._scene_entry_for(SCENE_NPC, name).ref


def _send(window: GMWindow, target: str, adjust: int = 0):
    call = window._team_calls.send(_ref(window, target), adjust)
    assert call is not None
    # The request comes back from the session with its seq.
    window.bridge.rollAdded.emit(
        {
            "seq": 5,
            "kind": KIND_REQUEST,
            "player_id": "gm",
            "player_name": "GM",
            "die": 0,
            "label": call.spec.label,
            "spec": call.spec.to_dict(),
        }
    )
    return call


def _entry(window: GMWindow, call, attacker: str, die: int, *, seq: int, actor: bool = True):
    """An attack *attacker* (an NPC of the cast, rolled as by the GM; or a player seat
    with ``actor=False``) made as the call's entry, as the session reports it."""
    data = window._data
    npc = next(e for e in window._npc_state.values() if e.summary.name == attacker)
    choice = attack_choices(npc.character, data)[0]
    spec = team_join_spec(choice.spec, call.spec, attacker, data)
    if actor:
        spec = RollSpec.from_dict({**spec.to_dict(), "actor": attacker})
    record = {
        "seq": seq,
        "kind": "roll",
        "player_id": "gm" if actor else attacker.lower(),
        "player_name": "GM" if actor else attacker,
        "die": die,
        "bonus": max(0, spec.modifier),
        "penalty": max(0, -spec.modifier),
        "dc": None,
        "critical": die == 20,
        "label": spec.label,
        "spec": spec.to_dict(),
    }
    window.bridge.rollAdded.emit(record)
    return record


def _dc(window: GMWindow, name: str) -> int:
    return defense_class_in_play(window._npc_state[name].character, window._data)


class _Saves:
    """Stands in for the server's group roll of the target's save."""

    def __init__(self, degree: int) -> None:
        self.degree = degree
        self.lines: list[list[dict]] = []

    def __call__(self, lines, about, finish, **_kw) -> bool:
        self.lines.append(lines)
        records = [
            {"die": 10, "bonus": 0, "penalty": 0, "dc": line["dc_offset"], "degree": self.degree}
            for line in lines
        ]
        finish(list(zip(about, records, strict=True)))
        return True

    def install(self, monkeypatch) -> _Saves:
        monkeypatch.setattr(
            action_run.ActionRun, "roll_group", lambda _run, *a, **kw: self(*a, **kw)
        )
        return self


# -- the tile -------------------------------------------------------------------------


def test_the_tile_needs_a_session_and_someone_on_the_scene(window, monkeypatch) -> None:
    tile = window._roller.open_actions().tile(TEAM_ATTACK)
    assert not tile.isEnabled() and NO_SESSION_FOR_CALL in tile.toolTip()
    monkeypatch.setattr(SessionBridge, "in_session", property(lambda self: True))
    tile = window._roller.open_actions().tile(TEAM_ATTACK)
    assert not tile.isEnabled() and NO_TARGET_FOR_CALL in tile.toolTip()
    _npc(window, "Brute")
    assert window._roller.open_actions().tile(TEAM_ATTACK).isEnabled()


def test_the_tile_opens_the_setup_window_and_send_posts_the_call(window, wire) -> None:
    brute = _npc(window, "Brute")
    window._roller.open_actions().tile(TEAM_ATTACK).click()
    setup = window._team_calls.window
    assert isinstance(setup, TeamCallWindow)
    setup.set_choice(_ref(window, brute), 2)
    setup._send()
    (prompt,) = wire.prompts
    spec = RollSpec.from_dict(prompt)
    assert spec.kind == KIND_TEAM_CALL and spec.modifier == 2
    assert spec.label == "Team attack → Brute (+2)"
    (call,) = window._team_calls.calls.values()
    assert call.card is not None and call.card.status() == NOBODY_YET
    assert not call.card.button.isEnabled()  # nothing to resolve yet


def test_a_call_the_session_would_not_take_leaves_no_card(window, monkeypatch) -> None:
    monkeypatch.setattr(SessionBridge, "in_session", property(lambda self: True))
    monkeypatch.setattr(window.bridge, "prompt_roll", lambda spec: False)
    brute = _npc(window, "Brute")
    assert window._team_calls.send(_ref(window, brute), 0) is None
    assert window._team_calls.calls == {}


# -- gathering the entries ------------------------------------------------------------


def test_each_entry_is_judged_on_the_card_as_it_comes(window, wire) -> None:
    brute = _npc(window, "Brute")
    _npc(window, "Thug")
    _npc(window, "Ogre")
    call = _send(window, brute)
    assert call.seq == 5
    dc = _dc(window, brute)
    _entry(window, call, "Thug", 18, seq=6)
    _entry(window, call, "Ogre", 2, seq=7)
    assert len(call.entries) == 2
    text = call.card.text()
    assert f"vs DC {dc}: hit" in text and f"vs DC {dc}: missed" in text
    assert call.card.status().startswith("Now: Primary: Thug")
    assert call.card.button.isEnabled()


def test_a_later_roll_replaces_the_seats_entry(window, wire) -> None:
    brute = _npc(window, "Brute")
    _npc(window, "Thug")
    call = _send(window, brute)
    _entry(window, call, "Thug", 2, seq=6)
    _entry(window, call, "Thug", 19, seq=7)  # a reroll, say
    (entry,) = call.entries.values()
    assert entry.record["seq"] == 7


def test_the_target_cannot_join_the_attack_on_itself(window, wire) -> None:
    brute = _npc(window, "Brute")
    call = _send(window, brute)
    _entry(window, call, "Brute", 15, seq=6)
    assert call.entries == {}


def test_an_aimed_attack_controller_leaves_the_entries_alone(window, wire) -> None:
    brute = _npc(window, "Brute")
    _npc(window, "Thug")
    call = _send(window, brute)
    _entry(window, call, "Thug", 15, seq=6)
    assert call.action_id not in window._attack._early


def test_a_player_seat_joins_under_their_own_name(window, wire) -> None:
    brute = _npc(window, "Brute")
    _npc(window, "Thug", scene=False)
    call = _send(window, brute)
    _entry(window, call, "Thug", 15, seq=6, actor=False)
    (entry,) = call.entries.values()
    assert entry.party.kind == "player" and entry.party.name == "Thug"


# -- settling -------------------------------------------------------------------------


def test_resolve_strikes_the_call_combines_and_rolls_the_save(window, wire, monkeypatch) -> None:
    brute = _npc(window, "Brute")
    _npc(window, "Thug")
    _npc(window, "Ogre")
    call = _send(window, brute)
    dc = _dc(window, brute)
    thug = _entry(window, call, "Thug", 18, seq=6)
    modifier = thug["bonus"] - thug["penalty"]
    _entry(window, call, "Ogre", dc - modifier, seq=7)  # a hit by exactly one degree
    saves = _Saves(degree=-2).install(monkeypatch)
    call.card.button.click()
    assert wire.removed == [5]  # the call's Join buttons go
    (lines,) = saves.lines
    save_dc = RollSpec.from_dict(thug["spec"]).follow_up.dc
    assert lines[0]["dc_offset"] == save_dc + 2  # one other degree: +2
    assert call.done and not call.card.pending
    assert window._npc_state[brute].character.conditions  # the failed save landed


def test_every_miss_settles_with_no_save(window, wire, monkeypatch) -> None:
    brute = _npc(window, "Brute")
    _npc(window, "Thug")
    call = _send(window, brute)
    _entry(window, call, "Thug", 1, seq=6)
    saves = _Saves(degree=-2).install(monkeypatch)
    window._team_calls.resolve(call.action_id)
    assert saves.lines == [] and call.done
    assert "missed" in call.card.status()


def test_entries_landing_after_resolve_change_nothing(window, wire, monkeypatch) -> None:
    brute = _npc(window, "Brute")
    _npc(window, "Thug")
    _npc(window, "Ogre")
    call = _send(window, brute)
    _entry(window, call, "Thug", 1, seq=6)
    _Saves(degree=-2).install(monkeypatch)
    window._team_calls.resolve(call.action_id)
    _entry(window, call, "Ogre", 18, seq=8)
    assert len(call.entries) == 1


def test_call_off_strikes_the_call_and_does_nothing_else(window, wire) -> None:
    brute = _npc(window, "Brute")
    call = _send(window, brute)
    call.card.second_button.click()
    assert wire.removed == [5] and call.done
    assert "Called off" in call.card.status()
    assert window._npc_state[brute].character.conditions == []


def test_the_gms_cross_on_the_table_calls_it_off(window, wire) -> None:
    brute = _npc(window, "Brute")
    call = _send(window, brute)
    window.bridge.rollRemoved.emit(5)
    assert call.done and "Called off" in call.card.status()
    assert wire.removed == []  # already gone: nothing to strike again


def test_a_struck_entry_leaves_the_team(window, wire) -> None:
    brute = _npc(window, "Brute")
    _npc(window, "Thug")
    call = _send(window, brute)
    _entry(window, call, "Thug", 15, seq=6)
    window.bridge.rollRemoved.emit(6)
    assert call.entries == {} and not call.done


# -- an NPC joins -----------------------------------------------------------------------


def test_join_in_the_gms_history_asks_which_npc_leaving_out_the_target(window, wire, menu) -> None:
    brute = _npc(window, "Brute")
    _npc(window, "Thug")
    _npc(window, "Ogre")
    call = _send(window, brute)
    menu.picks = [1, 0]  # Ogre, then its one attack
    window._history.teamJoinRequested.emit(call.spec)
    assert menu.shown[0] == ["Thug", "Ogre"]
    assert window._combat.actor is not None and window._combat.actor.name == "Ogre"
    assert [line.key for line in window._roller.action_lines()] == [TEAM_LINE]
    assert window._combat.team_call == call.spec


def test_joining_a_settled_call_says_so(window, wire, menu, monkeypatch) -> None:
    brute = _npc(window, "Brute")
    _npc(window, "Thug")
    call = _send(window, brute)
    window._team_calls.call_off(call.action_id)
    notices: list[str] = []
    monkeypatch.setattr(window, "_show_notice", lambda text, _colour: notices.append(text))
    window._gm_team_join(call.spec)
    assert notices == [TEAM_CALL_OVER] and menu.shown == []


# -- through a hosted session -------------------------------------------------------------


def _wait(qapp, condition, timeout: float = 5.0) -> None:
    import time

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return
        qapp.processEvents()
        time.sleep(0.01)
    raise AssertionError("condition never became true")


def test_a_hosted_call_is_joined_rolled_and_resolved(qapp, window: GMWindow, menu) -> None:
    import random

    from mm_companion.core.session import discovery
    from mm_companion.ui.roll_history import GroupTableCard
    from mm_companion.ui.session_dialogs import HostOptions

    window.bridge._publish_session = lambda port, **kw: discovery.Reachability(
        host="127.0.0.1", port=port, method=discovery.METHOD_LAN
    )
    window.start_hosting(HostOptions(name="Table", port=0, tunnel="", relay="", use_relay=False))
    _wait(qapp, lambda: window.bridge.hosting)
    window.bridge.server._rng = random.Random(4)
    brute = _npc(window, "Brute")
    _npc(window, "Thug")
    call = window._team_calls.send(_ref(window, brute), 1)
    _wait(qapp, lambda: call.seq is not None)
    # The call came back through the server's sanitizer as a call, and drew its table.
    (table,) = [c for c in window._history.cards() if isinstance(c, GroupTableCard)]
    assert table.join_button is not None
    table.join_button.click()  # the GM joins for Thug (the only one who may)
    assert window._combat.team_call == call.spec
    window._roller._start_roll()
    _wait(qapp, lambda: bool(call.entries))
    (entry,) = call.entries.values()
    assert entry.party.name == "Thug"
    assert RollSpec.from_dict(entry.record["spec"]).follow_up is not None  # the save travelled
    window._team_calls.resolve(call.action_id)
    _wait(qapp, lambda: call.done)
    assert table.join_button is None  # struck from everyone's history

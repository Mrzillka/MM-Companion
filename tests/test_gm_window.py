"""GUI tests for the GM Mode window (headless / offscreen).

Two jobs. The **connectivity story**: a join code that matches the session, the
advice from ``discovery`` shown **verbatim**, and a visible difference between
"players anywhere can join" and "only this network". Reachability is canned here
— the network probe itself is covered in ``test_session_discovery.py`` — but the
server really binds on loopback.

And the **player cards**: they are fed by two independent signals (a roster
entry carries no character, a snapshot carries no name), so the tests drive
those separately and in both orders, and check that a repeated roster updates a
card in place rather than rebuilding the grid. The card's fast-apply is tested
as far as the command going out — the card must not move its own chips, since
what it shows is the player's state, not the GM's intent. Applying it at the far
end is covered in ``test_session_player.py``.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication, QDialog, QLabel, QMessageBox

from mm_companion.core import library, storage
from mm_companion.core.character import Character
from mm_companion.core.data_loader import load_game_data
from mm_companion.core.rules import apply_condition
from mm_companion.core.session import discovery, store
from mm_companion.core.session.model import new_session
from mm_companion.core.session.protocol import sanitize_snapshot
from mm_companion.ui import dice_roller, player_card
from mm_companion.ui import gm_window as gm_window_module
from mm_companion.ui.gm_window import GMWindow
from mm_companion.ui.npc_card import NPCCard
from mm_companion.ui.npc_quick_dialog import QuickNPC, QuickNPCDialog
from mm_companion.ui.npc_window import NPCWindow
from mm_companion.ui.roll_history import HIDDEN_MARK
from mm_companion.ui.sections.conditions import addable_conditions
from mm_companion.ui.session_bridge import active_session, set_active_session
from mm_companion.ui.session_dialogs import (
    GMSessionLaunchDialog,
    HostOptions,
    HostSessionDialog,
)
from mm_companion.ui.start_window import StartWindow


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
def window(qapp: QApplication) -> GMWindow:
    made = GMWindow(bind="127.0.0.1")
    yield made
    for npc in list(made._npc_windows.values()):
        npc._dirty = False  # a "save your changes?" modal would hang the run
        npc.close()
    made.bridge.stop()


class FakeMapping:
    """Stands in for a UPnP mapping: the bridge releases it on stop."""

    def release(self) -> bool:
        return True


def canned(**kwargs) -> discovery.Reachability:
    defaults = {"host": "192.168.0.5", "port": 47331, "method": discovery.METHOD_LAN}
    return discovery.Reachability(**{**defaults, **kwargs})


def host_options(**overrides) -> HostOptions:
    """Host options with a free port by default, so tests never collide."""
    defaults = {"name": "Session", "port": 0, "tunnel": "", "relay": "", "use_relay": True}
    return HostOptions(**{**defaults, **overrides})


def start_hosting(
    qapp: QApplication, window: GMWindow, reachability, *, timeout: float = 5.0, **options
) -> None:
    """Host, with the network probe replaced by *reachability*, and wait for the code.

    The connection choices the GM would pick in the dialog are passed as keyword
    options (``relay=``, ``tunnel=``, ``use_relay=``); hosting is driven through
    :meth:`GMWindow.start_hosting` directly rather than through the modal dialog.
    """
    window.bridge._publish_session = lambda port, **kw: reachability
    options.setdefault("name", window._state.name)
    window.start_hosting(host_options(**options))
    deadline = time.monotonic() + timeout
    while not window._join_code and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.01)
    qapp.processEvents()


def settle(qapp: QApplication, rounds: int = 12) -> None:
    """Let pending layout work finish, the way a live event loop would.

    A geometry change ripples out one level per pass — a chip resizes its card,
    which re-wraps the grid, which re-measures the block — so a single
    ``processEvents`` catches only the first step.
    """
    for _ in range(rounds):
        qapp.processEvents()


def advice_texts(window: GMWindow) -> list[str]:
    return [
        window._advice_layout.itemAt(i).widget().text()
        for i in range(window._advice_layout.count())
    ]


# -- the draggable blocks --------------------------------------------------


def test_the_gm_window_has_the_three_board_blocks(window: GMWindow) -> None:
    # The session block moved to the launch dialog; the board is players/npcs/rolls.
    assert set(window._canvas.block_keys()) == {"players", "npcs", "rolls"}


def test_the_view_menu_can_hide_and_show_a_block(window: GMWindow) -> None:
    action = window._block_actions["npcs"]
    assert action.isChecked() is True

    action.setChecked(False)  # toggled -> hide
    assert window._canvas.is_hidden("npcs") is True

    action.setChecked(True)  # toggled -> show
    assert window._canvas.is_hidden("npcs") is False


def test_hiding_a_block_by_its_x_syncs_the_view_menu(window: GMWindow) -> None:
    window._canvas.hide_block("rolls")
    assert window._block_actions["rolls"].isChecked() is False
    window._canvas.show_block("rolls")
    assert window._block_actions["rolls"].isChecked() is True


def test_the_player_and_npc_blocks_grow_to_fit_more_cards(window: GMWindow) -> None:
    # A growable (unpinned) width lets the FlowLayout add columns as the block widens.
    assert window._canvas._is_growable("players") is True
    assert window._canvas._is_growable("npcs") is True


def test_the_block_layout_persists_across_a_reopen(qapp: QApplication, window: GMWindow) -> None:
    window._canvas.hide_block("npcs")
    window._persist_layout()

    reopened = GMWindow(bind="127.0.0.1")
    try:
        assert reopened._canvas.is_hidden("npcs") is True
    finally:
        reopened.bridge.stop()


def test_an_offline_roll_can_be_removed_before_any_session(
    qapp: QApplication, window: GMWindow
) -> None:
    from PySide6.QtWidgets import QPushButton

    # A roll made in GM mode without hosting (the roller's localRoll) shows with a ✕.
    window._show_offline_roll({"die": 14, "bonus": 2, "penalty": 0, "dc": 10, "result": None})
    assert len(window._history.cards()) == 1
    card = window._history.cards()[0]
    assert card.seq is not None and card.seq < 0

    button = next(b for b in card.findChildren(QPushButton) if b.text() == "✕")
    button.click()
    assert window._history.cards() == []


def test_a_gm_block_can_be_pinned_beside_the_scrolling_board(
    qapp: QApplication, window: GMWindow
) -> None:
    # The GM board hosts the same canvas as a character sheet, so it gets the same
    # pinned strip: the roll history stays put while the rest of the board scrolls.
    assert window._board.panel.is_empty()

    window._canvas.pin_block("rolls")
    QApplication.processEvents()

    assert window._canvas.is_pinned("rolls") is True
    assert window._board.panel.frames() == [[window._canvas.block_frame("rolls")]]
    assert all("rolls" not in row for row in window._canvas.arrangement()["rows"])

    window._persist_layout()
    reopened = GMWindow(bind="127.0.0.1")
    try:
        assert reopened._canvas.pinned_keys() == ["rolls"]
    finally:
        reopened.bridge.stop()


def test_reset_layout_brings_every_block_back(window: GMWindow) -> None:
    window._canvas.hide_block("npcs")
    window._reset_layout()
    assert window._canvas.is_hidden("npcs") is False
    assert window._block_actions["npcs"].isChecked() is True


# -- the idle window -------------------------------------------------------


def test_the_window_starts_not_hosting(window: GMWindow) -> None:
    # The fixture builds the window without autohosting; the launcher passes
    # autohost=True so a real GM window comes up already hosting.
    assert window.bridge.hosting is False
    assert window._join_code == ""
    assert window._copy_code_action.isEnabled() is False
    assert "Not hosting" in window._status_label.text()


def test_the_window_publishes_itself_as_the_active_session(window: GMWindow) -> None:
    assert active_session() is window.bridge


def test_renaming_the_session_retitles_the_window(window: GMWindow) -> None:
    window._state.name = "Wednesday Night"
    window._rename_session()
    assert window._state.name == "Wednesday Night"
    assert "Wednesday Night" in window.windowTitle()


# -- hosting ---------------------------------------------------------------


def test_hosting_shows_a_code_that_matches_the_session(
    qapp: QApplication, window: GMWindow
) -> None:
    start_hosting(qapp, window, canned())

    assert window.bridge.hosting is True
    code = discovery.decode_join_code(window._join_code)
    assert code.host == "192.168.0.5"
    assert code.token == window._state.host_token
    assert window._copy_code_action.isEnabled() is True


def test_stopping_clears_the_code(qapp: QApplication, window: GMWindow) -> None:
    start_hosting(qapp, window, canned())
    window.stop_hosting()

    assert window.bridge.hosting is False
    assert window._join_code == ""
    assert window._copy_code_action.isEnabled() is False
    assert "Not hosting" in window._status_label.text()


def test_a_port_already_in_use_is_reported_not_raised(
    qapp: QApplication, window: GMWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    warnings: list[str] = []
    monkeypatch.setattr(
        QMessageBox,
        "warning",
        lambda *args, **kw: warnings.append(args[2]) or QMessageBox.StandardButton.Ok,
    )
    monkeypatch.setattr(
        window.bridge, "host", lambda *a, **k: (_ for _ in ()).throw(OSError("address in use"))
    )
    window.start_hosting(host_options())

    assert window.bridge.hosting is False
    assert "address in use" in warnings[0]


# -- what the GM is told about reachability --------------------------------


def test_advice_is_rendered_verbatim(qapp: QApplication, window: GMWindow) -> None:
    start_hosting(qapp, window, canned(advice=(discovery.ADVICE_CGNAT, discovery.ADVICE_FIREWALL)))
    shown = advice_texts(window)
    assert len(shown) == 2
    assert discovery.ADVICE_CGNAT in shown[0]
    assert discovery.ADVICE_FIREWALL in shown[1]


def test_an_unreachable_session_says_so_and_reads_as_a_warning(
    qapp: QApplication, window: GMWindow
) -> None:
    start_hosting(qapp, window, canned(advice=(discovery.ADVICE_CGNAT,)))
    assert "Only players on this network" in window._status_label.text()
    assert gm_window_module.theme.color("tint.worse") in window._status_label.styleSheet()


def test_a_reachable_session_reads_as_success(qapp: QApplication, window: GMWindow) -> None:
    start_hosting(
        qapp,
        window,
        canned(
            host="203.0.113.7",
            method=discovery.METHOD_UPNP,
            external_ip="203.0.113.7",
            mapping=FakeMapping(),
        ),
    )
    assert "Players anywhere can join" in window._status_label.text()
    assert gm_window_module.theme.color("tint.better") in window._status_label.styleSheet()


def test_the_two_outcomes_do_not_look_alike(qapp: QApplication, window: GMWindow) -> None:
    start_hosting(qapp, window, canned())
    lan_status = (window._status_label.text(), window._status_label.styleSheet())
    window.stop_hosting()
    start_hosting(
        qapp,
        window,
        canned(host="203.0.113.7", external_ip="203.0.113.7", mapping=FakeMapping()),
    )
    assert (window._status_label.text(), window._status_label.styleSheet()) != lan_status


def test_advice_from_an_earlier_host_does_not_linger(qapp: QApplication, window: GMWindow) -> None:
    start_hosting(qapp, window, canned(advice=(discovery.ADVICE_CGNAT,)))
    window.stop_hosting()
    assert advice_texts(window) == []


# -- the tunnel path -------------------------------------------------------


def test_a_tunnel_address_becomes_the_join_code(qapp: QApplication, window: GMWindow) -> None:
    calls: list[dict] = []

    def fake(port: int, **kwargs):
        calls.append(kwargs)
        return discovery.publish_session(port, **kwargs)

    window.bridge._publish_session = fake
    window.start_hosting(host_options(tunnel="tunnel.example.net:12345", use_relay=False))
    deadline = time.monotonic() + 5.0
    while not window._join_code and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.01)
    qapp.processEvents()

    assert calls[0]["manual_host"] == "tunnel.example.net"
    assert calls[0]["external_port"] == 12345
    code = discovery.decode_join_code(window._join_code)
    assert (code.host, code.port) == ("tunnel.example.net", 12345)
    assert "tunnel.example.net:12345" in window._status_label.text()


def test_an_unreadable_tunnel_address_refuses_to_host(
    qapp: QApplication, window: GMWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    warnings: list[str] = []
    monkeypatch.setattr(
        QMessageBox,
        "warning",
        lambda *args, **kw: warnings.append(args[2]) or QMessageBox.StandardButton.Ok,
    )
    window.start_hosting(host_options(tunnel="nonsense:port"))

    assert window.bridge.hosting is False
    assert warnings  # the GM is told why, rather than silently not hosting


# -- roster and clipboard --------------------------------------------------


def roster(*entries: dict) -> list[dict]:
    """A roster payload as the server broadcasts it (no tokens, no characters)."""
    return [
        {"player_id": f"p{i}", "is_gm": False, "connected": True, **entry}
        for i, entry in enumerate(entries)
    ]


def a_character(**kwargs) -> dict:
    """A snapshot as a player would push it."""
    data = load_game_data()
    character = Character.new_default(data)
    character.profile["hero_name"] = kwargs.pop("hero_name", "Nightingale")
    character.power_level = kwargs.pop("power_level", 10)
    character.characteristics["hero_points"] = kwargs.pop("hero_points", 3)
    for condition_id in kwargs.pop("conditions", ()):
        apply_condition(character, condition_id, data)
    return sanitize_snapshot(character.to_dict())


def test_the_roster_becomes_one_card_per_player(qapp: QApplication, window: GMWindow) -> None:
    start_hosting(qapp, window, canned())
    window._show_roster(
        roster(
            {"display_name": "GM", "is_gm": True},
            {"display_name": "Aria"},
            {"display_name": "Bex", "connected": False},
        )
    )
    # The GM is not a player on their own board — no card for the GM's own seat.
    names = [card._name_label.text() for card in window._cards.values()]
    assert names == ["Aria", "Bex — offline"]
    assert window._no_players.isHidden() is True


def test_a_card_shows_the_player_character_from_their_snapshot(
    qapp: QApplication, window: GMWindow
) -> None:
    start_hosting(qapp, window, canned())
    window._show_roster(roster({"display_name": "Aria"}))
    window._on_snapshot("p0", a_character(power_level=12, hero_points=4, conditions=["dazed"]))

    card = window._cards["p0"]
    assert card._character_label.text() == "Nightingale"
    assert card._pl_label.text() == "PL 12"
    assert card._hero_points.value() == 4
    assert card.condition_names() == ["Dazed"]
    assert card._open_button.isEnabled() is True


def test_a_card_without_a_snapshot_says_so_and_cannot_be_opened(
    qapp: QApplication, window: GMWindow
) -> None:
    start_hosting(qapp, window, canned())
    window._show_roster(roster({"display_name": "Aria"}))

    card = window._cards["p0"]
    assert card._character_label.text() == player_card.NO_CHARACTER
    assert card._open_button.isEnabled() is False
    assert card.character is None


def test_a_snapshot_that_arrives_before_the_roster_still_lands(
    qapp: QApplication, window: GMWindow
) -> None:
    start_hosting(qapp, window, canned())
    window._on_snapshot("p0", a_character(hero_points=2))
    window._show_roster(roster({"display_name": "Aria"}))
    assert window._cards["p0"]._hero_points.value() == 2


def test_a_second_roster_updates_the_same_card_rather_than_rebuilding_it(
    qapp: QApplication, window: GMWindow
) -> None:
    start_hosting(qapp, window, canned())
    window._show_roster(roster({"display_name": "Aria"}))
    card = window._cards["p0"]

    window._show_roster(roster({"display_name": "Aria", "connected": False}))

    assert window._cards["p0"] is card
    assert "offline" in card._name_label.text()


def test_a_seat_that_leaves_the_roster_loses_its_card(qapp: QApplication, window: GMWindow) -> None:
    start_hosting(qapp, window, canned())
    window._show_roster(roster({"display_name": "Aria"}, {"display_name": "Bex"}))
    window._show_roster([{"player_id": "p0", "display_name": "Aria", "connected": True}])

    assert list(window._cards) == ["p0"]
    assert window._cards_flow.count() == 1


def test_a_connected_player_card_offers_removal(qapp: QApplication, window: GMWindow) -> None:
    card = player_card.PlayerCard(window._data)
    card.set_roster({"player_id": "p0", "display_name": "Aria", "connected": True})
    assert card._is_gm is False and card.player_id == "p0"
    # The GM's own card never offers it, however the roster describes the seat.
    gm = player_card.PlayerCard(window._data)
    gm.set_roster({"player_id": "g0", "display_name": "GM", "is_gm": True, "connected": True})
    assert gm._is_gm is True


def test_removing_a_player_confirms_then_kicks(
    qapp: QApplication, window: GMWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    start_hosting(qapp, window, canned())
    window._show_roster(roster({"display_name": "Aria"}))
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    kicked: list[str] = []
    monkeypatch.setattr(window._bridge, "kick", lambda pid, *a, **k: kicked.append(pid) or True)

    window._remove_player("p0")
    assert kicked == ["p0"]


def test_removing_a_player_can_be_cancelled(
    qapp: QApplication, window: GMWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    start_hosting(qapp, window, canned())
    window._show_roster(roster({"display_name": "Aria"}))
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.No)
    monkeypatch.setattr(
        window._bridge, "kick", lambda *a, **k: pytest.fail("kick should not run on cancel")
    )

    window._remove_player("p0")


def test_stopping_clears_the_cards(qapp: QApplication, window: GMWindow) -> None:
    start_hosting(qapp, window, canned())
    window._show_roster(roster({"display_name": "Aria"}))
    window.stop_hosting()

    assert window._cards == {}
    assert window._no_players.isHidden() is False


def test_a_resumed_session_seeds_its_cards_from_the_stored_snapshots(
    qapp: QApplication, window: GMWindow
) -> None:
    slot = window._state.add_player("Aria")
    slot.character = a_character(power_level=11)
    start_hosting(qapp, window, canned())

    card = window._cards[slot.player_id]
    assert card._pl_label.text() == "PL 11"


def test_open_sheet_shows_the_character_read_only(qapp: QApplication, window: GMWindow) -> None:
    start_hosting(qapp, window, canned())
    window._show_roster(roster({"display_name": "Aria"}))
    window._on_snapshot("p0", a_character(hero_name="Nightingale"))

    window._cards["p0"]._open_button.click()

    sheet_window = window._player_windows["p0"]
    assert sheet_window.sheet.character.profile["hero_name"] == "Nightingale"
    # A GM view is a locked read-only window: no Lock toggle to undo it, and only
    # a View menu (no File/Settings/Tools/Session).
    assert not hasattr(sheet_window, "_lock_action")
    assert sheet_window.sheet.locked is True
    menus = [action.text() for action in sheet_window.menuBar().actions()]
    assert menus == ["&View"]
    window.close()  # closes the player sheets it opened


def test_copy_puts_the_code_on_the_clipboard(qapp: QApplication, window: GMWindow) -> None:
    start_hosting(qapp, window, canned())
    window._copy_code_action.trigger()
    assert QApplication.clipboard().text() == window._join_code
    assert window._notice.isVisible() or window._notice_label.text()


def test_closing_stops_hosting_and_the_window_reopens_onto_the_same_session(
    qapp: QApplication, window: GMWindow
) -> None:
    window._state.name = "Wednesday"
    window._rename_session()
    start_hosting(qapp, window, canned())
    session_id = window._state.id

    window.close()
    assert window.bridge.hosting is False
    assert active_session() is None
    assert window._join_code == ""

    window.show()
    assert active_session() is window.bridge
    assert window._state.id == session_id
    assert window._state.name == "Wednesday"


def test_a_refused_connection_is_shown_to_the_gm(qapp: QApplication, window: GMWindow) -> None:
    window._on_refused({"code": "bad_token", "message": "that join code is not for this session"})
    assert "not for this session" in window._notice_label.text()


def test_a_notice_can_be_dismissed_with_its_close_button(
    qapp: QApplication, window: GMWindow
) -> None:
    window._show_notice("Join code copied — send it to your players.", "")
    assert not window._notice.isHidden()
    window._notice._close.click()
    assert window._notice.isHidden()


def test_a_notice_fades_itself_out_after_its_dwell(qapp: QApplication, window: GMWindow) -> None:
    from PySide6.QtCore import QAbstractAnimation

    notice = window._notice
    window._show_notice("A player joined.", "")
    assert not notice.isHidden()

    # Driven directly rather than on the wall clock: the dwell elapsing kicks off
    # the fade, and the fade reaching zero opacity retires the card.
    notice._timer.timeout.emit()
    assert notice._fade.state() == QAbstractAnimation.State.Running
    notice._effect.setOpacity(0.0)
    notice._fade.finished.emit()
    assert notice.isHidden()


def test_a_status_update_brings_the_faded_card_back(qapp: QApplication, window: GMWindow) -> None:
    window._status_notice.dismiss()
    assert window._status_notice.isHidden()
    window._set_status("Players anywhere can join with this code.", "")
    assert not window._status_notice.isHidden()
    assert "Players anywhere" in window._status_label.text()


# -- the launcher ----------------------------------------------------------


def _accept_launch(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make the GM launch dialog accept at once and the window skip real hosting.

    The pre-stage is modal, and autohost would bind a real socket — neither is
    wanted in a launcher test that only checks the window opened.
    """
    from mm_companion.ui import session_dialogs

    monkeypatch.setattr(
        session_dialogs.GMSessionLaunchDialog, "exec", lambda self: QDialog.DialogCode.Accepted
    )
    monkeypatch.setattr(gm_window_module.GMWindow, "start_hosting", lambda self, options: None)


def test_the_launcher_opens_gm_mode(qapp: QApplication, monkeypatch: pytest.MonkeyPatch) -> None:
    _accept_launch(monkeypatch)
    launcher = StartWindow()
    launcher.show()
    launcher._open_gm_mode()
    try:
        assert isinstance(launcher._gm_window, GMWindow)
        # The launcher stays up behind it — a GM still opens character sheets.
        assert launcher.isHidden() is False
    finally:
        launcher._gm_window.bridge.stop()


def test_reopening_gm_mode_reuses_the_same_window(
    qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    _accept_launch(monkeypatch)
    launcher = StartWindow()
    launcher._open_gm_mode()
    first = launcher._gm_window
    launcher._open_gm_mode()
    try:
        assert launcher._gm_window is first
    finally:
        first.bridge.stop()


def test_a_label_exists_for_every_piece_of_advice_the_window_is_given(
    qapp: QApplication, window: GMWindow
) -> None:
    everything = (
        discovery.ADVICE_NO_IGD,
        discovery.ADVICE_UPNP_REFUSED,
        discovery.ADVICE_PORT_TAKEN,
        discovery.ADVICE_CGNAT,
        discovery.ADVICE_DOUBLE_NAT,
        discovery.ADVICE_NO_EXTERNAL_IP,
        discovery.ADVICE_MANUAL_ADDRESS,
        discovery.ADVICE_LAN_ONLY,
        discovery.ADVICE_FIREWALL,
    )
    window._show_advice(everything)
    labels = [w for w in window.findChildren(QLabel) if w.text().startswith("• ")]
    assert len(labels) == len(everything)


# -- the connection ladder: direct first, relay last -----------------------


def test_an_unreachable_machine_falls_back_to_the_relay(
    qapp: QApplication, window: GMWindow, relay_box
) -> None:
    start_hosting(qapp, window, canned(advice=(discovery.ADVICE_CGNAT,)), relay=relay_box.base)

    code = discovery.decode_join_code(window._join_code)
    assert code.host.startswith("mmrelay")
    assert code.host.endswith(window._state.id)
    assert window.bridge.relaying is True
    assert discovery.ADVICE_RELAY in advice_texts(window)[0]
    assert "relay" in window._status_label.text()


def test_a_reachable_machine_never_touches_the_relay(
    qapp: QApplication, window: GMWindow, relay_box
) -> None:
    """Direct costs the relay nothing, so a working direct connection keeps it."""
    start_hosting(
        qapp,
        window,
        canned(host="203.0.113.7", mapping=FakeMapping(), external_ip="203.0.113.7"),
        relay=relay_box.base,
    )

    assert window.bridge.relaying is False
    assert discovery.decode_join_code(window._join_code).host == "203.0.113.7"
    assert relay_box.server.session_count() == 0


def test_a_tunnel_address_is_taken_at_its_word(
    qapp: QApplication, window: GMWindow, relay_box
) -> None:
    start_hosting(
        qapp,
        window,
        canned(host="147.185.221.23", port=12345, method=discovery.METHOD_MANUAL),
        relay=relay_box.base,
        tunnel="147.185.221.23:12345",
        use_relay=False,
    )

    assert window.bridge.relaying is False
    assert relay_box.server.session_count() == 0


def test_the_relay_is_only_used_when_it_is_asked_for(
    qapp: QApplication, window: GMWindow, relay_box
) -> None:
    start_hosting(
        qapp,
        window,
        canned(advice=(discovery.ADVICE_CGNAT,)),
        relay=relay_box.base,
        use_relay=False,
    )

    assert window.bridge.relaying is False
    assert discovery.decode_join_code(window._join_code).host == "192.168.0.5"


def test_a_relay_that_cannot_be_reached_leaves_the_session_hosted(
    qapp: QApplication, window: GMWindow
) -> None:
    start_hosting(
        qapp,
        window,
        canned(advice=(discovery.ADVICE_CGNAT,)),
        relay="mmrelay+tcp://127.0.0.1:1",
    )

    assert window.bridge.hosting is True
    assert window.bridge.relaying is False
    assert discovery.ADVICE_RELAY_UNREACHABLE in window._notice_label.text()


def test_the_host_dialog_remembers_the_relay_between_launches(qapp: QApplication) -> None:
    dialog = HostSessionDialog()
    dialog._form._relay_edit.setText("relay.example.net")
    dialog._on_accept()

    assert storage.relay_url() == "relay.example.net"
    # A fresh dialog pre-fills the field from the saved value.
    assert HostSessionDialog()._form._relay_edit.text() == "relay.example.net"


def test_the_host_dialog_defaults_to_automatic(qapp: QApplication) -> None:
    dialog = HostSessionDialog(session_name="Tuesday")
    opts = dialog.options()
    assert opts.name == "Tuesday"
    assert opts.tunnel == "" and opts.use_relay is True


def test_the_host_dialog_reads_back_a_tunnel_and_arms_no_relay(qapp: QApplication) -> None:
    dialog = HostSessionDialog()
    dialog._form._via_tunnel.setChecked(True)
    dialog._form._tunnel_edit.setText("1.2.3.4:5678")
    opts = dialog.options()
    assert opts.tunnel == "1.2.3.4:5678"
    assert opts.use_relay is False  # a typed address is taken at its word


def test_the_host_dialog_only_shows_the_tunnel_field_for_the_tunnel_method(
    qapp: QApplication,
) -> None:
    dialog = HostSessionDialog()
    dialog.show()
    assert dialog._form._tunnel_edit.isVisibleTo(dialog) is False
    dialog._form._via_tunnel.setChecked(True)
    assert dialog._form._tunnel_edit.isVisibleTo(dialog) is True


# -- fast-apply conditions -------------------------------------------------


def a_condition(condition_id: str):
    return load_game_data().condition_catalog()[condition_id]


def test_only_a_connected_player_can_be_given_a_condition(
    qapp: QApplication, window: GMWindow
) -> None:
    start_hosting(qapp, window, canned())
    window._show_roster(
        roster(
            {"display_name": "GM", "is_gm": True},
            {"display_name": "Aria"},
            {"display_name": "Bex", "connected": False},
        )
    )

    # The GM applies conditions to itself on its own sheet, so gets no card at all;
    # an offline player has no connection for the command to travel down.
    assert "p0" not in window._cards
    assert window._cards["p1"]._condition_button.isEnabled() is True
    assert window._cards["p2"]._condition_button.isEnabled() is False


def test_the_menu_offers_the_same_conditions_the_sheet_does(
    qapp: QApplication, window: GMWindow
) -> None:
    start_hosting(qapp, window, canned())
    window._show_roster(roster({"display_name": "Aria"}))
    card = window._cards["p0"]

    offered = {c.name for c in card._addable_conditions}

    assert {c.name for c in addable_conditions(load_game_data())} == offered
    assert "Dazed" in offered
    # Not the object-damage ladder or the bookkeeping marker.
    assert "Normal" not in offered


def test_picking_a_condition_sends_it_to_that_player(
    qapp: QApplication, window: GMWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    start_hosting(qapp, window, canned())
    window._show_roster(roster({"display_name": "Aria"}))
    sent: list[tuple] = []
    monkeypatch.setattr(
        window.bridge.server,
        "apply_condition",
        lambda *args: sent.append(args) or True,
    )

    window._cards["p0"]._choose_condition(a_condition("dazed"))

    assert sent == [("p0", "dazed", None)]
    assert "Dazed" in window._notice_label.text()


def test_clicking_a_players_hero_points_sends_the_new_total(
    qapp: QApplication, window: GMWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    start_hosting(qapp, window, canned())
    window._show_roster(roster({"display_name": "Aria"}))
    sent: list[tuple] = []
    monkeypatch.setattr(
        window.bridge.server, "set_hero_points", lambda *args: sent.append(args) or True
    )

    window._cards["p0"]._hero_points._on_click(2)  # light the 3rd pip

    assert sent == [("p0", 1)]


def test_a_connected_players_hero_points_take_clicks(qapp: QApplication, window: GMWindow) -> None:
    start_hosting(qapp, window, canned())
    window._show_roster(
        roster(
            {"display_name": "Aria"},
            {"display_name": "Bex", "connected": False},
        )
    )
    # A connected seat's pips are clickable; an offline one's are inert.
    assert (
        window._cards["p0"]._hero_points.testAttribute(
            gm_window_module.Qt.WidgetAttribute.WA_TransparentForMouseEvents
        )
        is False
    )
    assert (
        window._cards["p1"]._hero_points.testAttribute(
            gm_window_module.Qt.WidgetAttribute.WA_TransparentForMouseEvents
        )
        is True
    )


def test_a_command_to_a_player_who_is_gone_says_so(qapp: QApplication, window: GMWindow) -> None:
    """Nothing is applied optimistically — the card only moves on the snapshot back."""
    start_hosting(qapp, window, canned())
    window._show_roster(roster({"display_name": "Aria"}))

    window._cards["p0"]._choose_condition(a_condition("dazed"))

    assert "not connected" in window._notice_label.text()
    assert window._cards["p0"].condition_names() == []


def test_a_chip_can_be_taken_off_again(
    qapp: QApplication, window: GMWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    start_hosting(qapp, window, canned())
    window._show_roster(roster({"display_name": "Aria"}))
    window._on_snapshot("p0", a_character(conditions=["dazed"]))
    removed: list[tuple] = []
    monkeypatch.setattr(
        window.bridge.server,
        "remove_condition",
        lambda *args: removed.append(args) or True,
    )
    card = window._cards["p0"]
    assert card.condition_names() == ["Dazed"]

    card._chip_flow.itemAt(0).widget()._remove.click()

    assert removed == [("p0", "dazed", None)]


def test_an_offline_players_chips_lose_their_remove_button(
    qapp: QApplication, window: GMWindow
) -> None:
    start_hosting(qapp, window, canned())
    window._show_roster(roster({"display_name": "Aria"}))
    window._on_snapshot("p0", a_character(conditions=["dazed"]))
    card = window._cards["p0"]
    assert card._chip_flow.itemAt(0).widget()._remove is not None

    window._show_roster(roster({"display_name": "Aria", "connected": False}))

    assert card.condition_names() == ["Dazed"]  # still shown, just not commandable
    assert card._chip_flow.itemAt(0).widget()._remove is None


# -- the GM's roller and the shared history --------------------------------


def test_the_gm_roller_offers_hidden_rolls(window: GMWindow) -> None:
    assert window._roller._hidden_check.isVisibleTo(window._roller) is True


def test_a_gm_roll_lands_in_the_session_and_the_history(
    qapp: QApplication, window: GMWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(dice_roller, "ROLL_DURATION_MS", 0)
    start_hosting(qapp, window, canned())
    window._roller._bonus_spin.setValue(5)

    window._roller._start_roll()
    qapp.processEvents()

    recorded = window.bridge.server.state.rolls
    assert [roll.bonus for roll in recorded] == [5]
    assert len(window._history.cards()) == 1


def test_a_hidden_gm_roll_is_marked_and_not_broadcast(
    qapp: QApplication, window: GMWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(dice_roller, "ROLL_DURATION_MS", 0)
    start_hosting(qapp, window, canned())
    window._roller._hidden_check.setChecked(True)

    window._roller._start_roll()
    qapp.processEvents()

    assert window.bridge.server.state.visible_rolls() == []
    assert len(window._history.cards()) == 1
    labels = window._history.cards()[0].findChildren(QLabel)
    assert any(HIDDEN_MARK in label.text() for label in labels)


def test_the_history_shows_a_resumed_sessions_rolls_before_hosting(
    qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = new_session("Last night")
    state.record_roll(player_id="p0", player_name="Aria", die=11, bonus=2)
    store.save_session(state, write_rolls=True)
    monkeypatch.setattr(gm_window_module, "last_session", lambda: state)

    resumed = GMWindow(bind="127.0.0.1")
    try:
        assert len(resumed._history.cards()) == 1
    finally:
        resumed.bridge.stop()


def test_a_roll_made_before_hosting_is_shown_but_not_recorded(
    qapp: QApplication, window: GMWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(dice_roller, "ROLL_DURATION_MS", 0)
    monkeypatch.setattr(dice_roller, "roll_d20", lambda *a, **k: 9)

    window._roller._start_roll()
    qapp.processEvents()

    assert len(window._history.cards()) == 1
    assert window._state.rolls == []


# --------------------------------------------------------------------------
# NPCs
#
# The GM's bestiary lives in the workspace ``gm_characters/`` dir and outlives any
# one session; what belongs to a session is which of those are *in* it
# (``SessionState.npc_paths``). So the two verbs are deliberately different —
# removing takes an NPC out of tonight's cast, deleting takes the file away — and
# both have to survive the app being closed.
# --------------------------------------------------------------------------


def write_npc(name: str = "Thug", power_level: int = 8) -> Path:
    """Save an NPC into the GM folder, the way the NPC window would."""
    character = Character.new_default(load_game_data())
    character.profile["hero_name"] = name
    character.power_level = power_level
    return library.save_character(character, directory=storage.get_workspace().gm_characters_dir)


def npc_names(window: GMWindow) -> list[str]:
    """The captions on the NPC cards, in the order they are laid out."""
    return [
        window._npc_flow.itemAt(i).widget()._summary.name  # type: ignore[union-attr]
        for i in range(window._npc_flow.count())
    ]


def npc_cards(window: GMWindow) -> list[NPCCard]:
    return [
        window._npc_flow.itemAt(i).widget()  # type: ignore[return-value]
        for i in range(window._npc_flow.count())
    ]


def test_a_session_starts_with_no_npcs(window: GMWindow) -> None:
    assert npc_names(window) == []
    assert window._no_npcs.isVisibleTo(window)


def test_an_npc_renders_as_a_live_card_over_its_model(window: GMWindow) -> None:
    window._register_npc(write_npc("Ogre", power_level=9))

    (card,) = npc_cards(window)
    assert isinstance(card, NPCCard)
    assert card.display_name() == "Ogre"
    # The card is backed by the loaded model, not just a summary.
    assert card.character.profile["hero_name"] == "Ogre"
    # And the GM window holds an entry keyed by the file name.
    assert "ogre.json" in window._npc_state


def test_saving_a_new_npc_puts_it_in_the_cast(qapp: QApplication, window: GMWindow) -> None:
    window._create_npc()
    (sheet,) = window._npc_windows.values()
    sheet.sheet.character.profile["hero_name"] = "Bank Robber"
    sheet._write(window._npc_dir() / "bank-robber.json")
    qapp.processEvents()

    assert window._state.npc_paths == ["bank-robber.json"]
    assert npc_names(window) == ["Bank Robber"]


def test_the_quick_wizard_saves_a_playable_npc_and_opens_it(
    qapp: QApplication, window: GMWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Five numbers, and the creature is in the cast before anything else is filled in."""
    entered = QuickNPC(name="Bandit", attack=6, effect=5, defence=7, toughness=4, image_path=None)
    monkeypatch.setattr(QuickNPCDialog, "exec", lambda self: QDialog.DialogCode.Accepted)
    monkeypatch.setattr(QuickNPCDialog, "value", lambda self: entered)

    window._quick_npc()
    qapp.processEvents()

    # Saved and in the cast straight away — no trip through an unsaved sheet.
    assert window._state.npc_paths == ["bandit.json"]
    assert npc_names(window) == ["Bandit"]

    # And it opened in the ordinary NPC sheet, carrying its two powers.
    (sheet,) = window._npc_windows.values()
    assert isinstance(sheet, NPCWindow)
    assert [power.name for power in sheet.sheet.character.powers] == ["Damage", "Affliction"]


def test_cancelling_the_quick_wizard_creates_nothing(
    window: GMWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(QuickNPCDialog, "exec", lambda self: QDialog.DialogCode.Rejected)

    window._quick_npc()

    assert window._state.npc_paths == []
    assert window._npc_windows == {}


def test_an_npc_sheet_is_the_simplified_one(window: GMWindow) -> None:
    window._create_npc()
    (sheet,) = window._npc_windows.values()

    assert isinstance(sheet, NPCWindow)
    assert sheet.storage_dir() == window._npc_dir()
    assert sheet.sheet.system_info._estimated_pl.isVisibleTo(sheet)


def test_an_existing_npc_can_be_brought_into_the_session(
    window: GMWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = write_npc("Ogre")
    monkeypatch.setattr(
        gm_window_module.QFileDialog, "getOpenFileName", lambda *a, **k: (str(path), "")
    )

    window._add_existing_npc()

    assert window._state.npc_paths == ["ogre.json"]
    assert npc_names(window) == ["Ogre"]


def test_the_same_npc_is_not_added_twice(window: GMWindow) -> None:
    path = write_npc("Ogre")
    window._register_npc(path)
    window._register_npc(path)

    assert window._state.npc_paths == ["ogre.json"]
    assert npc_names(window) == ["Ogre"]


def test_the_cast_is_still_there_next_time_gm_mode_opens(
    qapp: QApplication, window: GMWindow
) -> None:
    window._register_npc(write_npc("Ogre"))

    reopened = GMWindow(bind="127.0.0.1")
    try:
        assert reopened._state.id == window._state.id
        assert npc_names(reopened) == ["Ogre"]
    finally:
        reopened.bridge.stop()


def test_removing_an_npc_leaves_its_file_where_it_is(window: GMWindow) -> None:
    path = write_npc("Ogre")
    window._register_npc(path)

    window._remove_npc(path.name)

    assert window._state.npc_paths == []
    assert npc_names(window) == []
    assert path.is_file()  # still in the bestiary, just not in this session


def test_deleting_an_npc_takes_the_file_with_it(
    window: GMWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = write_npc("Ogre")
    window._register_npc(path)
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)

    window._delete_npc(path.name)

    assert window._state.npc_paths == []
    assert npc_names(window) == []
    assert not path.exists()


def test_a_refused_deletion_changes_nothing(
    window: GMWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = write_npc("Ogre")
    window._register_npc(path)
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.No)

    window._delete_npc(path.name)

    assert window._state.npc_paths == ["ogre.json"]
    assert path.is_file()


def test_an_npc_whose_file_vanished_drops_out_of_the_session(window: GMWindow) -> None:
    path = write_npc("Ogre")
    window._register_npc(path)
    path.unlink()  # deleted behind the app's back

    window._refresh_npcs()

    assert window._state.npc_paths == []
    assert npc_names(window) == []


def test_opening_the_same_npc_twice_raises_the_one_window(window: GMWindow) -> None:
    path = write_npc("Ogre")
    window._register_npc(path)

    window._open_npc(path.name)
    first = next(iter(window._npc_windows.values()))
    window._open_npc(path.name)

    # Reopening must not replace the sheet the way a player's read-only one is:
    # this one is editable, and a replacement would take unsaved work with it.
    assert list(window._npc_windows.values()) == [first]


def test_an_npc_added_while_hosting_goes_through_the_server(
    qapp: QApplication, window: GMWindow
) -> None:
    start_hosting(qapp, window, canned(mapping=FakeMapping()))
    window._register_npc(write_npc("Ogre"))

    server = window.bridge.server
    assert server is not None
    # Through the server, not around it: the state is shared with its worker
    # threads, so the write has to take the same lock every other mutation does.
    assert server.state.npc_paths == ["ogre.json"]
    assert store.load_session(window._state.id).npc_paths == ["ogre.json"]


def test_applying_a_condition_to_an_npc_persists_and_shows_a_chip(window: GMWindow) -> None:
    path = write_npc("Ogre")
    window._register_npc(path)

    window._apply_npc_condition(path.name, "dazed", None)

    # On the model, on disk, and on the card.
    entry = window._npc_state[path.name]
    assert [c.condition_id for c in entry.character.conditions] == ["dazed"]
    assert [c.condition_id for c in library.load_character(path).conditions] == ["dazed"]
    assert entry.card.condition_names() == ["Dazed"]


def test_removing_a_condition_from_an_npc_takes_it_off(window: GMWindow) -> None:
    path = write_npc("Ogre")
    window._register_npc(path)
    window._apply_npc_condition(path.name, "dazed", None)

    window._remove_npc_condition(path.name, "dazed", None)

    entry = window._npc_state[path.name]
    assert entry.character.conditions == []
    assert library.load_character(path).conditions == []
    assert entry.card.condition_names() == []


def test_an_npc_condition_leaves_the_other_blocks_where_they_were(
    qapp: QApplication, window: GMWindow
) -> None:
    """A chip on an NPC card must cost the NPCs block room, and nothing else.

    Two bugs used to make the *Players* block jump instead. The NPC card grid pinned
    a minimum height it never lowered, so a chip that came and went left the block
    permanently taller; and the grid advertised height-for-width, which Qt evaluates
    at a single card's width — so two cards side by side claimed the height of two
    rows, and the surplus went to whatever else shared the page.
    """
    window.show()
    ogre = write_npc("Ogre")
    window._register_npc(ogre)
    window._register_npc(write_npc("Thug"))
    settle(qapp)

    players = window._canvas.block_frame("players")
    players_height = players.minimumSizeHint().height()
    npcs_height = window._npc_container.minimumHeight()

    window._apply_npc_condition(ogre.name, "dazed", None)
    settle(qapp)
    assert window._npc_container.minimumHeight() > npcs_height  # room for the chip
    assert players.minimumSizeHint().height() == players_height

    window._remove_npc_condition(ogre.name, "dazed", None)
    settle(qapp)
    assert window._npc_container.minimumHeight() == npcs_height  # and given back
    assert players.minimumSizeHint().height() == players_height


def test_removing_an_npc_condition_matches_on_the_parameter(window: GMWindow) -> None:
    path = write_npc("Ogre")
    window._register_npc(path)
    window._apply_npc_condition(path.name, "impaired", "Fortitude")
    window._apply_npc_condition(path.name, "impaired", "Dodge")

    window._remove_npc_condition(path.name, "impaired", "Fortitude")

    entry = window._npc_state[path.name]
    remaining = [(c.condition_id, c.parameter) for c in entry.character.conditions]
    assert remaining == [("impaired", "Dodge")]


def test_copying_an_npc_names_it_goon_2(window: GMWindow) -> None:
    path = write_npc("Goon")
    window._register_npc(path)

    window._copy_npc(path.name)

    assert sorted(npc_names(window)) == ["Goon", "Goon-2"]
    # The copy is its own file in the session's cast.
    assert "goon-2.json" in window._state.npc_paths


def test_copying_twice_walks_up_the_numbers(window: GMWindow) -> None:
    path = write_npc("Goon")
    window._register_npc(path)

    window._copy_npc("goon.json")
    window._copy_npc("goon.json")

    assert sorted(npc_names(window)) == ["Goon", "Goon-2", "Goon-3"]


def test_copying_a_numbered_npc_keeps_the_base(window: GMWindow) -> None:
    window._register_npc(write_npc("Goon"))
    window._copy_npc("goon.json")  # -> Goon-2

    window._copy_npc("goon-2.json")  # base is "Goon", not "Goon-2"

    assert sorted(npc_names(window)) == ["Goon", "Goon-2", "Goon-3"]


def test_next_copy_name_is_base_aware() -> None:
    assert gm_window_module._next_copy_name("Goon", set()) == "Goon-2"
    assert gm_window_module._next_copy_name("Goon", {"Goon-2"}) == "Goon-3"
    assert gm_window_module._next_copy_name("Goon-2", {"Goon-2"}) == "Goon-3"
    assert gm_window_module._next_copy_name("Goon-7", {"Goon-2", "Goon-3"}) == "Goon-4"


def register_npcs(window: GMWindow, *names: str) -> None:
    for name in names:
        window._register_npc(write_npc(name))


def test_rolled_npcs_sort_highest_initiative_first(window: GMWindow) -> None:
    register_npcs(window, "Alpha", "Bravo", "Charlie")

    window._on_npc_initiative("alpha.json", 12)
    window._on_npc_initiative("bravo.json", 24)
    window._on_npc_initiative("charlie.json", 18)

    assert npc_names(window) == ["Bravo", "Charlie", "Alpha"]


def test_unrolled_npcs_sit_below_rolled(window: GMWindow) -> None:
    register_npcs(window, "Alpha", "Bravo")

    window._on_npc_initiative("bravo.json", 15)

    assert npc_names(window) == ["Bravo", "Alpha"]


def test_manual_order_among_unrolled_is_honored(window: GMWindow) -> None:
    register_npcs(window, "Alpha", "Bravo", "Charlie")

    window._reorder_npc("charlie.json", 0)  # drag Charlie to the front

    assert npc_names(window) == ["Charlie", "Alpha", "Bravo"]


def test_dragging_a_rolled_card_clears_its_initiative(window: GMWindow) -> None:
    register_npcs(window, "Alpha", "Bravo")
    window._on_npc_initiative("alpha.json", 25)
    assert npc_names(window) == ["Alpha", "Bravo"]

    window._reorder_npc("alpha.json", 99)  # drag Alpha down into the manual zone

    assert window._npc_state["alpha.json"].initiative is None
    assert npc_names(window) == ["Bravo", "Alpha"]


def test_the_hover_summary_shows_abilities_resistances_and_powers(window: GMWindow) -> None:
    from mm_companion.core.powers import Power, PowerEffectInstance

    character = Character.new_default(load_game_data())
    character.profile["hero_name"] = "Ogre"
    character.abilities["STR"] = 6
    character.powers.append(Power(effects=[PowerEffectInstance("damage", rank=8)], name="Smash"))
    path = library.save_character(character, directory=storage.get_workspace().gm_characters_dir)
    window._register_npc(path)

    (card,) = npc_cards(window)
    html = card.summary_html()

    assert "Abilities" in html and "Strength +6" in html
    assert "Resistances" in html
    assert "Powers" in html and "Smash" in html


def write_agile_npc(name: str, agility: int) -> Path:
    """An NPC with a known Agility, so its initiative modifier is predictable."""
    character = Character.new_default(load_game_data())
    character.profile["hero_name"] = name
    character.abilities["AGL"] = agility
    return library.save_character(character, directory=storage.get_workspace().gm_characters_dir)


def test_rolling_npc_initiative_uses_its_modifier_and_shows_a_badge(
    window: GMWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = write_agile_npc("Ogre", agility=3)
    window._register_npc(path)
    from mm_companion.ui import npc_card as npc_card_module

    monkeypatch.setattr(npc_card_module, "roll_d20", lambda *a, **k: 15)

    (card,) = npc_cards(window)
    total = card.roll_initiative()

    assert total == 18  # d20 15 + Agility 3
    assert card.initiative == 18
    assert "18" in card._initiative_badge.text()
    # The GM window remembered it for the ordering to come.
    assert window._npc_state[path.name].initiative == 18


def test_a_rolled_initiative_survives_a_refresh(
    window: GMWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = write_agile_npc("Ogre", agility=0)
    window._register_npc(path)
    from mm_companion.ui import npc_card as npc_card_module

    monkeypatch.setattr(npc_card_module, "roll_d20", lambda *a, **k: 12)
    npc_cards(window)[0].roll_initiative()

    window._refresh_npcs()

    (card,) = npc_cards(window)
    assert card.initiative == 12
    assert "12" in card._initiative_badge.text()


# --------------------------------------------------------------------------
# Previous sessions (GM side)
#
# A GM can re-run any session they have ever hosted, not just the last one. The
# choice is made in the launch dialog before the window opens: store.list_sessions
# enumerates the workspace, and the chosen session is handed to the window as its
# state. The old in-window session switcher is gone.
# --------------------------------------------------------------------------


def test_opening_a_session_as_the_window_state_resumes_its_cast(
    qapp: QApplication,
) -> None:
    write_npc("Ghoul")  # the cast file the stored session names
    other = new_session("Old Table")
    other.npc_paths = ["ghoul.json"]
    store.save_session(other, write_rolls=True)

    resumed = GMWindow(bind="127.0.0.1", state=store.load_session(other.id))
    try:
        assert resumed._state.id == other.id
        assert resumed._state.name == "Old Table"
        assert npc_names(resumed) == ["Ghoul"]
    finally:
        resumed.bridge.stop()


def test_the_launch_dialog_lists_previous_sessions_and_returns_the_chosen_id(
    qapp: QApplication,
) -> None:
    a = new_session("A")
    store.save_session(a)
    store.save_session(new_session("B"))

    dialog = GMSessionLaunchDialog()
    assert dialog._table.rowCount() == 2

    dialog._table.selectRow(next(i for i, s in enumerate(dialog._summaries) if s.id == a.id))
    dialog._on_accept()
    assert dialog.chosen_session_id() == a.id


def test_the_launch_dialog_starts_a_new_session_when_none_is_chosen(qapp: QApplication) -> None:
    store.save_session(new_session("A"))

    dialog = GMSessionLaunchDialog()
    dialog._start_new()  # clears any selection
    dialog._on_accept()
    assert dialog.chosen_session_id() is None


def test_the_launch_dialog_deletes_a_session(
    qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    store.save_session(new_session("A"))
    store.save_session(new_session("B"))

    dialog = GMSessionLaunchDialog()
    dialog._table.selectRow(0)
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    dialog._delete_selected()

    assert dialog._table.rowCount() == 1
    assert len(store.list_sessions()) == 1

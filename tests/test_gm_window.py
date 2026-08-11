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
from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QApplication, QDialog, QLabel, QMenu, QMessageBox

from mm_companion.core import library, storage
from mm_companion.core.character import Character
from mm_companion.core.data_loader import load_game_data
from mm_companion.core.npc import quick_npc
from mm_companion.core.rules import PinRef, apply_condition, attach_accessory, build_item_from_entry
from mm_companion.core.session import discovery, store
from mm_companion.core.session.model import new_session
from mm_companion.core.session.protocol import sanitize_snapshot
from mm_companion.ui import dice_roller, player_card
from mm_companion.ui import gm_window as gm_window_module
from mm_companion.ui.gm_window import GMWindow
from mm_companion.ui.npc_card import NPCCard
from mm_companion.ui.npc_quick_dialog import QuickNPC, QuickNPCDialog
from mm_companion.ui.npc_window import NPCWindow
from mm_companion.ui.pin_picker import LABEL_ROLE, PIN_ROLE
from mm_companion.ui.roll_history import HIDDEN_MARK
from mm_companion.ui.sections.conditions import addable_conditions, build_condition_menu
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


def test_the_rolls_block_starts_in_the_pinned_strip(qapp: QApplication, window: GMWindow) -> None:
    """Where the sheet's Dice block starts, and for the same reason.

    A roller that scrolls away with the board is no use mid-fight, so the strip is
    the Rolls block's home rather than somewhere a GM has to drag it — and the page
    holds only what is read between rolls.
    """
    assert window._canvas.pinned_keys() == ["rolls"]
    assert window._board.panel.frames() == [[window._canvas.block_frame("rolls")]]
    assert all("rolls" not in row for row in window._canvas.arrangement()["rows"])


def test_a_gm_block_can_be_pinned_beside_the_scrolling_board(
    qapp: QApplication, window: GMWindow
) -> None:
    # The GM board hosts the same canvas as a character sheet, so it gets the same
    # pinned strip, and a block moves either way across it.
    window._canvas.unpin_block("rolls")
    QApplication.processEvents()
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
    assert card._portrait.isEnabled() is True
    # The hover summary is the NPC card's, now on both.
    assert "Resistances" in card.toolTip()


def test_a_card_without_a_snapshot_says_so_and_cannot_be_opened(
    qapp: QApplication, window: GMWindow
) -> None:
    start_hosting(qapp, window, canned())
    window._show_roster(roster({"display_name": "Aria"}))

    card = window._cards["p0"]
    assert card._character_label.text() == player_card.NO_CHARACTER
    # Nothing to open, so the portrait does not pretend otherwise.
    assert card._portrait.toolTip() == ""
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

    window._cards["p0"]._portrait.clicked.emit()

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

    menu = build_condition_menu(card, load_game_data(), lambda _c: None)
    offered = _menu_condition_names(menu)

    assert {c.name for c in addable_conditions(load_game_data())} == set(offered)
    assert "Dazed" in offered
    # Not the object-damage ladder or the bookkeeping marker.
    assert "Normal" not in offered
    # Every condition sits in exactly one place — a duplicate would mean a group
    # claimed a condition another had already taken.
    assert len(offered) == len(set(offered))


def test_the_menu_splits_the_catalog_into_groups(qapp: QApplication, window: GMWindow) -> None:
    """The whole point of the split: nothing is a flat 36-item list any more."""
    data = load_game_data()
    menu = build_condition_menu(window, data, lambda _c: None)

    submenus = {
        a.menu().title(): _menu_condition_names(a.menu()) for a in menu.actions() if a.menu()
    }

    assert set(submenus) == {g.title for g in data.condition_groups}
    assert "Blind" in submenus["Senses"]
    assert "Staggered" in submenus["Damage"]
    # Nothing left over: every addable condition is grouped, so the menu has no
    # flat tail below the submenus.
    assert [a for a in menu.actions() if a.menu() is None and not a.isSeparator()] == []


def test_the_submenus_outlive_the_call_that_built_them(
    qapp: QApplication, window: GMWindow
) -> None:
    """QMenu.addMenu(title) hands ownership *back* to the caller.

    So a submenu with no Python reference is collected out from under the open
    menu — the whole grouped menu falls apart the moment the builder returns.
    Parenting each submenu to the menu is what stops it; this proves it by
    collecting aggressively before reading the menu back.
    """
    import gc

    menu = build_condition_menu(window, load_game_data(), lambda _c: None)
    gc.collect()
    qapp.processEvents()
    gc.collect()

    submenus = [a.menu() for a in menu.actions() if a.menu() is not None]
    assert [m.title() for m in submenus] == [g.title for g in load_game_data().condition_groups]
    assert all(m.actions() for m in submenus)


def _menu_condition_names(menu: QMenu) -> list[str]:
    """Every condition a menu offers, submenus and flat tail alike."""
    names: list[str] = []
    for action in menu.actions():
        if action.isSeparator():
            continue
        names.extend(_menu_condition_names(action.menu()) if action.menu() else [action.text()])
    return names


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


def test_only_the_portrait_opens_an_npc_sheet(qapp: QApplication, window: GMWindow) -> None:
    """The card body is the drag handle; a short drag must not open a window.

    This is the bug the split fixes: the reorder gesture and "open the sheet" were
    the same press, told apart only by how far the pointer had travelled.
    """
    window._register_npc(write_npc("Ogre"))
    (card,) = npc_cards(window)
    opened: list[str] = []
    card.openRequested.connect(opened.append)

    at = QPointF(20, 60)
    press = QMouseEvent(
        QEvent.Type.MouseButtonPress,
        at,
        card.mapToGlobal(at),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    release = QMouseEvent(
        QEvent.Type.MouseButtonRelease,
        at,
        card.mapToGlobal(at),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
    )
    card.mousePressEvent(press)
    card.mouseReleaseEvent(release)
    assert opened == []

    card._portrait.clicked.emit()
    assert opened == ["ogre.json"]


def test_a_card_restates_its_hover_summary_from_the_model(window: GMWindow) -> None:
    """Whenever a card redraws itself, the summary is re-derived rather than kept."""
    window._register_npc(write_npc("Ogre"))
    (card,) = npc_cards(window)

    window._apply_npc_condition("ogre.json", "dazed", None)

    assert card.toolTip() == card.summary_html()


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


def test_the_hover_summary_shows_the_gear_a_powerless_mook_fights_with(
    window: GMWindow,
) -> None:
    """A thug whose whole threat is its rifle used to hover blank below Resistances.

    Every item, worn or not — the same rule the pinned strip beside this follows, since
    wearing is a runtime flag a GM flips constantly.
    """
    data = load_game_data()
    character = Character.new_default(data)
    character.profile["hero_name"] = "Thug"
    character.abilities["ATK"] = 6
    catalog = data.equipment_catalog()
    gun = build_item_from_entry(catalog["assault_rifle"], data)
    sight = build_item_from_entry(catalog["laser_sight"], data)
    character.equipment = [gun, sight]
    attach_accessory(character, gun, sight, data)
    path = library.save_character(character, directory=storage.get_workspace().gm_characters_dir)
    window._register_npc(path)

    (card,) = npc_cards(window)
    html = card.summary_html()

    assert "Powers" not in html, "it has none, and should not claim a heading"
    assert "Equipment" in html and "Assault Rifle" in html
    # The *effective* build: the fitted sight raises the number the GM is reading.
    assert "8 vs. Defense" in html


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


# -- pinned parameters -------------------------------------------------------


def quick_npc_file(window: GMWindow, name: str = "Goon", effect: int = 6) -> str:
    """A quick NPC saved into the cast — it has the Damage power the defaults want."""
    character = quick_npc(
        load_game_data(), name=name, attack=6, effect=effect, defence=6, toughness=6
    )
    path = library.save_character(character, directory=window._npc_dir())
    window._register_npc(path)
    return path.name


def chip_index(card, caption: str) -> int:
    """Where *caption* sits in the strip. By name, so adding a default pin to the
    shipped list does not renumber every test that touches a chip."""
    return next(i for i, text in enumerate(card.pins.chip_texts()) if text.startswith(caption))


def test_a_new_npc_card_starts_with_the_default_strip(window: GMWindow) -> None:
    """DEF, Toughness, ATK and the first Damage power's attack roll."""
    quick_npc_file(window)
    (card,) = npc_cards(window)

    assert card.pins.chip_texts() == ["DEF 6", "Toughness 6", "ATK +6", "Damage +6"]


def test_a_new_player_card_starts_with_the_default_strip(
    qapp: QApplication, window: GMWindow
) -> None:
    start_hosting(qapp, window, canned())
    window._show_roster(roster({"display_name": "Aria"}))
    window._on_snapshot("p0", a_character())

    card = window._cards["p0"]
    assert [v.label for v in card.pins.values()] == [
        "DEF",
        "Toughness",
        "Initiative",
        "Perception",
    ]


def test_a_players_chips_follow_their_snapshot(qapp: QApplication, window: GMWindow) -> None:
    """A card is a view of a live sheet, so a pin is re-read, not remembered."""
    start_hosting(qapp, window, canned())
    window._show_roster(roster({"display_name": "Aria"}))
    window._on_snapshot("p0", a_character())
    card = window._cards["p0"]
    assert card.pins.chip_texts()[0] == "DEF 0"

    stronger = Character.new_default(load_game_data())
    stronger.resistances["DEF"] = 9
    window._on_snapshot("p0", sanitize_snapshot(stronger.to_dict()))

    assert card.pins.chip_texts()[0] == "DEF 9"


def test_clicking_a_chip_loads_it_and_double_clicking_rolls_it(window: GMWindow) -> None:
    """The end of the chain, not the signal: the roller really holds the spec.

    The same bargain a stat row on the sheet strikes — one click to load, so the
    sliders and the DC box can be set, and two to throw.
    """
    quick_npc_file(window)
    (card,) = npc_cards(window)
    assert window._roller._spec is None

    attack = card.pins._chips[chip_index(card, "ATK")]
    attack.loadRequested.emit(attack.value.spec)

    assert window._roller._spec is not None
    assert window._roller._spec.label == "Attack"
    assert window._roller._rolling is False  # loaded, not thrown

    attack.rollRequested.emit(attack.value.spec)

    assert window._roller._rolling is True


def test_a_forced_save_chip_is_read_rather_than_rolled(window: GMWindow) -> None:
    """The wielder never makes their own target's save.

    It already reaches the person who does as the follow-up chip on the attack's
    history card, so a rollable chip here would throw a second, meaningless die.
    The *attack* on the same power stays rollable — that one is the wielder's, and
    is what the NPC default pins.
    """
    name = quick_npc_file(window)
    (card,) = npc_cards(window)
    power_id = card.character.powers[0].id
    window._store_pins("npc:" + name, [PinRef("power", power_id, 0), PinRef("power", power_id, 1)])
    window._refresh_npcs()
    (card,) = npc_cards(window)

    attack, save = card.pins._chips

    assert (attack.text(), attack.rollable) == ("Damage +6", True)
    assert (save.text(), save.rollable) == ("Damage DC 16", False)
    assert save.value.missing is False  # read-only, not broken


def test_a_chip_with_nothing_to_roll_ignores_a_click(window: GMWindow) -> None:
    """A defence DC is a difficulty; nobody throws one."""
    name = quick_npc_file(window)
    window._store_pins("npc:" + name, [PinRef("defense_class")])
    window._refresh_npcs()
    (card,) = npc_cards(window)

    (chip,) = card.pins._chips
    assert chip.text() == "DEF DC 16"
    assert chip.rollable is False


def test_a_strip_can_be_reordered_and_the_order_persists(window: GMWindow) -> None:
    name = quick_npc_file(window)
    (card,) = npc_cards(window)

    card.pins.move_pin(chip_index(card, "Damage"), 0)

    assert card.pins.chip_texts()[0] == "Damage +6"
    stored = storage.load_settings()["gm_pins"]["npc:" + name]
    assert [entry["kind"] for entry in stored] == [
        "power",
        "resistance",
        "resistance",
        "ability",
    ]
    # And it survives the wholesale card rebuild every cast change does.
    window._refresh_npcs()
    assert npc_cards(window)[0].pins.chip_texts()[0] == "Damage +6"


def test_a_chip_can_be_removed(window: GMWindow) -> None:
    name = quick_npc_file(window)
    (card,) = npc_cards(window)

    card.pins.remove_pin(chip_index(card, "ATK"))

    assert card.pins.chip_texts() == ["DEF 6", "Toughness 6", "Damage +6"]
    assert len(storage.load_settings()["gm_pins"]["npc:" + name]) == 3


def test_a_strip_the_gm_emptied_stays_empty(window: GMWindow) -> None:
    """An empty strip is an answer, not a missing one.

    A GM who takes every chip off a card means it — so it has to be written, or
    the next launch sees no entry, seeds the defaults and hands back the four
    chips they just removed.
    """
    name = quick_npc_file(window)
    (card,) = npc_cards(window)

    for ref in list(card.pins.pins):
        card.pins.remove_ref(ref)

    assert storage.load_settings()["gm_pins"]["npc:" + name] == []

    relaunched = GMWindow(bind="127.0.0.1")
    try:
        assert npc_cards(relaunched)[0].pins.chip_texts() == []
    finally:
        relaunched.bridge.stop()


def test_the_gm_reaches_the_settings_window_from_its_own_menu(window: GMWindow) -> None:
    """And lands on the GM page, not the one the character sheet cares about."""
    menus = [a.text() for a in window.menuBar().actions()]
    assert "&Settings" in menus

    window._open_settings()
    try:
        assert window._settings_window._stack.currentWidget().title == "GM Mode"
    finally:
        window._settings_window.close()


def test_applying_the_defaults_reseeds_a_card_the_gm_had_tailored(window: GMWindow) -> None:
    """The deliberate exception to "a card's strip is its own once it exists"."""
    name = quick_npc_file(window)
    (card,) = npc_cards(window)
    for ref in list(card.pins.pins):
        card.pins.remove_ref(ref)
    assert card.pins.chip_texts() == []

    window.reseed_pins_from_defaults()

    assert card.pins.chip_texts() == ["DEF 6", "Toughness 6", "ATK +6", "Damage +6"]
    assert len(storage.load_settings()["gm_pins"]["npc:" + name]) == 4


def test_reseeding_restates_an_open_picker(window: GMWindow) -> None:
    """Or its menu offers to pin something the card already has back."""
    name = quick_npc_file(window)
    (card,) = npc_cards(window)
    card.pins.remove_pin(chip_index(card, "ATK"))
    window._open_npc_pin_picker(name)
    picker = window._pin_pickers["npc:" + name]
    assert picker.action_text(PinRef("ability", "ATK")) == "Pin"

    window.reseed_pins_from_defaults()

    assert picker.action_text(PinRef("ability", "ATK")) == "Unpin"
    picker.close()


def test_reseeding_forgets_the_cards_that_are_not_on_the_board(window: GMWindow) -> None:
    """So they seed from the *new* defaults when they are next seen."""
    name = quick_npc_file(window)
    npc_cards(window)[0].pins.remove_pin(0)
    window._remove_npc(name)
    assert "npc:" + name in window._pins

    window.reseed_pins_from_defaults()

    assert "npc:" + name not in window._pins
    assert "npc:" + name not in storage.load_settings()["gm_pins"]


def test_the_picker_pins_onto_the_card_behind_it(window: GMWindow) -> None:
    name = quick_npc_file(window)
    (card,) = npc_cards(window)

    window._open_npc_pin_picker(name)
    picker = window._pin_pickers["npc:" + name]
    picker.pinRequested.emit(PinRef("resistance", "WILL"))

    assert card.pins.chip_texts()[-1] == "Will 0"
    picker.close()


def picker_rows(picker) -> dict[str, list[str]]:
    """Every visible row of the picker, grouped by heading."""
    tree = picker._tree
    return {
        tree.topLevelItem(i).text(0): [
            tree.topLevelItem(i).child(c).data(0, LABEL_ROLE)
            for c in range(tree.topLevelItem(i).childCount())
            if not tree.topLevelItem(i).child(c).isHidden()
        ]
        for i in range(tree.topLevelItemCount())
    }


def test_the_picker_offers_this_npcs_own_powers(window: GMWindow) -> None:
    name = quick_npc_file(window)
    window._open_npc_pin_picker(name)
    picker = window._pin_pickers["npc:" + name]

    groups = picker_rows(picker)

    assert "Damage" in groups["Powers"]
    assert "Affliction" in groups["Powers"]
    picker.close()


def test_the_pickers_filter_matches_every_word(window: GMWindow) -> None:
    name = quick_npc_file(window)
    window._open_npc_pin_picker(name)
    picker = window._pin_pickers["npc:" + name]

    picker._filter.setText("tough")

    shown = [row for rows in picker_rows(picker).values() for row in rows]
    assert shown == ["Toughness"]
    picker.close()


def test_a_copied_npc_inherits_the_strip_it_was_copied_from(window: GMWindow) -> None:
    """A duplicate is the same creature under a new name."""
    name = quick_npc_file(window)
    card = npc_cards(window)[0]
    card.pins.move_pin(chip_index(card, "Damage"), 0)

    window._copy_npc(name)

    copy = next(c for c in npc_cards(window) if c.display_name() == "Goon-2")
    assert copy.pins.chip_texts()[0] == "Damage +6"


def test_deleting_an_npc_forgets_its_pins_but_removing_it_does_not(
    window: GMWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A file that is gone can never resolve again; one merely set aside can."""
    name = quick_npc_file(window)
    npc_cards(window)[0].pins.remove_pin(0)
    assert "npc:" + name in window._pins

    window._remove_npc(name)
    assert "npc:" + name in window._pins

    window._register_npc(window._npc_dir() / name)
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    window._delete_npc(name)

    assert "npc:" + name not in window._pins
    assert "npc:" + name not in storage.load_settings()["gm_pins"]


def test_a_departing_seat_takes_its_pins_with_it(qapp: QApplication, window: GMWindow) -> None:
    start_hosting(qapp, window, canned())
    window._show_roster(roster({"display_name": "Aria"}))
    window._on_snapshot("p0", a_character())
    window._cards["p0"].pins.remove_pin(0)
    assert "player:p0" in window._pins

    window._show_roster([])

    assert "player:p0" not in window._pins


def test_a_pin_to_a_power_the_npc_lost_still_shows_and_can_be_taken_off(
    window: GMWindow,
) -> None:
    """Rendered as a dash rather than dropped — otherwise there is no way to clear it."""
    name = quick_npc_file(window)
    entry = window._npc_state[name]
    entry.character.powers = []
    library.save_character(entry.character, path=entry.path, directory=window._npc_dir())
    window._refresh_npcs()

    (card,) = npc_cards(window)
    # Captioned by what the pin *says* rather than by the name it had — there is
    # nothing left to read the name off.
    index = chip_index(card, "First Damage")
    assert card.pins.chip_texts()[index].endswith("—")
    card.pins.remove_pin(index)
    assert len(card.pins.chip_texts()) == 3


def test_pinning_from_an_npcs_own_sheet_lands_on_its_card(window: GMWindow) -> None:
    """The other route in: open the sheet, right-click a row, and it is on the card."""
    name = quick_npc_file(window)
    (card,) = npc_cards(window)

    window._open_npc(name)
    sheet_window = next(iter(window._npc_windows.values()))
    assert sheet_window.sheet.abilities._pins.enabled is True

    sheet_window.sheet.resistances.pinRequested.emit(PinRef("resistance", "WILL"))

    assert card.pins.chip_texts()[-1] == "Will 0"


def test_pinning_from_a_players_read_only_sheet_lands_on_their_card(
    qapp: QApplication, window: GMWindow
) -> None:
    start_hosting(qapp, window, canned())
    window._show_roster(roster({"display_name": "Aria"}))
    window._on_snapshot("p0", a_character())
    card = window._cards["p0"]

    window._open_player_sheet("p0")
    sheet_window = window._player_windows["p0"]
    sheet_window.sheet.system_info.pinRequested.emit(PinRef("defense_class"))

    assert card.pins.chip_texts()[-1] == "DEF DC 10"
    window.close()


def test_a_players_own_sheet_never_offers_to_pin(qapp: QApplication) -> None:
    """Nothing about a normal sheet changes: there is no card behind it."""
    from mm_companion.ui.main_window import MainWindow

    plain = MainWindow(character=Character.new_default(load_game_data()))
    try:
        assert plain.sheet.abilities._pins.enabled is False
    finally:
        plain._dirty = False
        plain.close()


def test_a_card_on_an_older_workspace_still_gets_its_default_strip(
    qapp: QApplication, window: GMWindow
) -> None:
    """The bug that made the whole feature look unimplemented.

    settings.json is only written with the shipped defaults when the workspace is
    *created*, so every existing user's file has no gm_default_pins key at all.
    Read straight off load_settings that came back None and every card came up
    empty.
    """
    settings = storage.load_settings()
    settings.pop("gm_default_pins", None)
    storage.save_settings(settings)

    quick_npc_file(window)
    (card,) = npc_cards(window)

    assert card.pins.chip_texts() == ["DEF 6", "Toughness 6", "ATK +6", "Damage +6"]


def test_the_picker_unpins_what_is_already_on_the_card(window: GMWindow) -> None:
    """The card's own ✕ used to be the only way off, and the GM is already here."""
    name = quick_npc_file(window)
    (card,) = npc_cards(window)
    window._open_npc_pin_picker(name)
    picker = window._pin_pickers["npc:" + name]

    picker.unpinRequested.emit(PinRef("resistance", "DEF"))

    assert "DEF 6" not in card.pins.chip_texts()
    picker.close()


def test_a_pickers_row_menu_says_which_way_it_will_go(window: GMWindow) -> None:
    name = quick_npc_file(window)
    window._open_npc_pin_picker(name)
    picker = window._pin_pickers["npc:" + name]

    assert picker_row_action(picker, "Resistances", "DEF") == "Unpin"  # a default
    assert picker_row_action(picker, "Resistances", "Will") == "Pin"

    picker.close()


def test_double_clicking_a_picker_row_toggles_it(window: GMWindow) -> None:
    name = quick_npc_file(window)
    (card,) = npc_cards(window)
    window._open_npc_pin_picker(name)
    picker = window._pin_pickers["npc:" + name]
    row = picker_row(picker, "Resistances", "Will")

    picker._toggle(row)
    assert card.pins.chip_texts()[-1] == "Will 0"
    assert picker_row_action(picker, "Resistances", "Will") == "Unpin"

    picker._toggle(row)
    assert "Will 0" not in card.pins.chip_texts()
    assert picker_row_action(picker, "Resistances", "Will") == "Pin"
    picker.close()


def test_the_picker_shows_the_characters_own_numbers(window: GMWindow) -> None:
    """A catalogue, not a combat readout.

    Deciding what is worth pinning is not the moment to be shown a halved Dodge —
    while the chip on the card, which is what gets read mid-fight, keeps it.
    """
    name = quick_npc_file(window)
    window._apply_npc_condition(name, "vulnerable", None)
    (card,) = npc_cards(window)

    window._open_npc_pin_picker(name)
    picker = window._pin_pickers["npc:" + name]

    assert picker_row(picker, "Resistances", "Dodge").text(1) == "6"
    assert card.pins.chip_texts()[0] == "DEF 3"  # halved, as the GM must actually use it
    picker.close()


def picker_row(picker, group_title: str, caption: str):
    """One row of the picker's tree, by its group heading and caption."""
    tree = picker._tree
    group = next(
        tree.topLevelItem(i)
        for i in range(tree.topLevelItemCount())
        if tree.topLevelItem(i).text(0) == group_title
    )
    return next(
        group.child(c)
        for c in range(group.childCount())
        if group.child(c).data(0, LABEL_ROLE) == caption
    )


def picker_row_action(picker, group_title: str, caption: str) -> str:
    """What that row's right-click menu offers — asked of the dialog itself."""
    row = picker_row(picker, group_title, caption)
    return picker.action_text(row.data(0, PIN_ROLE))


def test_a_sheet_row_offers_unpin_once_the_card_has_it(window: GMWindow) -> None:
    """The complaint that started this: the sheet kept offering to pin what was
    already pinned. It knows now, because the card tells it."""
    name = quick_npc_file(window)
    (card,) = npc_cards(window)
    window._open_npc(name)
    sheet = next(iter(window._npc_windows.values())).sheet

    # DEF is a default, so it is on the card before the sheet ever opened.
    assert sheet.resistances._pins.action_text(PinRef("resistance", "DEF")) == "Unpin from GM card"
    assert sheet.resistances._pins.action_text(PinRef("resistance", "WILL")) == "Pin to GM card"

    sheet.resistances.pinRequested.emit(PinRef("resistance", "WILL"))

    assert card.pins.chip_texts()[-1] == "Will 0"
    assert sheet.resistances._pins.action_text(PinRef("resistance", "WILL")) == "Unpin from GM card"


def test_unpinning_from_the_sheet_takes_it_off_the_card(window: GMWindow) -> None:
    name = quick_npc_file(window)
    (card,) = npc_cards(window)
    window._open_npc(name)
    sheet = next(iter(window._npc_windows.values())).sheet

    sheet.resistances.unpinRequested.emit(PinRef("resistance", "DEF"))

    assert "DEF 6" not in card.pins.chip_texts()
    assert sheet.resistances._pins.action_text(PinRef("resistance", "DEF")) == "Pin to GM card"


def test_the_card_corrects_a_sheet_that_was_left_open(window: GMWindow) -> None:
    """A strip changed from the card (or the picker) must reach the sheet's menus.

    Otherwise the sheet goes on offering Unpin for something the GM already
    removed with the chip's own ✕.
    """
    name = quick_npc_file(window)
    (card,) = npc_cards(window)
    window._open_npc(name)
    sheet = next(iter(window._npc_windows.values())).sheet
    ref = PinRef("resistance", "DEF")
    assert sheet.resistances._pins.is_pinned(ref) is True

    card.pins.remove_ref(ref)

    assert sheet.resistances._pins.is_pinned(ref) is False


def test_every_pinnable_block_learns_what_is_on_the_card(window: GMWindow) -> None:
    name = quick_npc_file(window)
    window._open_npc(name)
    sheet = next(iter(window._npc_windows.values())).sheet

    sheet.set_pinned([PinRef("initiative"), PinRef("skill", "Perception"), PinRef("power", "x", 1)])

    assert sheet.system_info._pins.is_pinned(PinRef("initiative")) is True
    assert sheet.skills._pins.is_pinned(PinRef("skill", "Perception")) is True
    assert sheet.powers._pins.is_pinned(PinRef("power", "x", 1)) is True
    assert sheet.abilities._pins.is_pinned(PinRef("ability", "STR")) is False

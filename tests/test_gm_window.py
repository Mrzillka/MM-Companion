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
from PySide6.QtCore import QEvent, QMimeData, QPointF, Qt
from PySide6.QtGui import QContextMenuEvent, QDragEnterEvent, QDropEvent, QMouseEvent
from PySide6.QtWidgets import QApplication, QDialog, QLabel, QMenu, QMessageBox, QPushButton

from mm_companion.core import library, storage
from mm_companion.core.character import Character
from mm_companion.core.data_loader import load_game_data
from mm_companion.core.npc import quick_npc
from mm_companion.core.rules import (
    KIND_SKILL,
    PinnedValue,
    PinRef,
    RollSpec,
    apply_condition,
    attach_accessory,
    build_item_from_entry,
)
from mm_companion.core.session import discovery, store
from mm_companion.core.session.model import new_session
from mm_companion.core.session.protocol import sanitize_snapshot
from mm_companion.ui import card_chips, dice_roller, player_card, theme
from mm_companion.ui import gm_window as gm_window_module
from mm_companion.ui.drop_feedback import DropFeedback
from mm_companion.ui.gm_window import SCENE_NPC, SCENE_PLAYER, GMWindow
from mm_companion.ui.npc_card import NPCCard
from mm_companion.ui.npc_quick_dialog import QuickNPC, QuickNPCDialog
from mm_companion.ui.npc_window import NPCWindow
from mm_companion.ui.pin_picker import LABEL_ROLE, PIN_ROLE
from mm_companion.ui.roll_history import HIDDEN_MARK, RequestCard
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


def test_the_gm_window_has_the_four_board_blocks(window: GMWindow) -> None:
    # The session block moved to the launch dialog; the board is the shared Scene
    # plus the two rosters that feed it, and the roll log.
    assert set(window._canvas.block_keys()) == {"scene", "players", "npcs", "rolls"}


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


def right_click(widget) -> None:
    """Right-click *widget*, the way a GM sheds a condition chip.

    A real ``QContextMenuEvent`` rather than calling the handler: what is under
    test is partly that the event is *consumed*, so it never reaches the context
    menu of the card the chip sits on.
    """
    centre = widget.rect().center()
    QApplication.sendEvent(
        widget,
        QContextMenuEvent(QContextMenuEvent.Reason.Mouse, centre, widget.mapToGlobal(centre)),
    )


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

    right_click(card._chip_flow.itemAt(0).widget())

    assert removed == [("p0", "dazed", None)]


def test_an_offline_players_chips_cannot_be_removed(
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
    assert card._chip_flow.itemAt(0).widget().removable is True

    window._show_roster(roster({"display_name": "Aria", "connected": False}))

    assert card.condition_names() == ["Dazed"]  # still shown, just not commandable
    chip = card._chip_flow.itemAt(0).widget()
    assert chip.removable is False
    right_click(chip)
    assert removed == []


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

    assert card._name_label.toolTip() == card.summary_html()


def test_the_hover_summary_is_on_the_name_and_not_the_whole_card(window: GMWindow) -> None:
    """A tooltip on the card fires wherever the pointer rests, so it landed over
    the pinned chip or the damage button a GM was lining up."""
    window._register_npc(write_npc("Ogre"))
    (card,) = npc_cards(window)

    assert card.toolTip() == ""
    assert "Abilities" in card._name_label.toolTip()
    # And on the collapsed card's name, which is a different label — through the
    # eliding label's own seam, or its next resize would wipe it.
    card.set_collapsed(True)
    assert "Abilities" in card._header_name.toolTip()


def test_saving_a_new_npc_puts_it_in_the_cast(qapp: QApplication, window: GMWindow) -> None:
    window._create_npc()
    (sheet,) = window._npc_windows.values()
    sheet.sheet.character.profile["hero_name"] = "Bank Robber"
    sheet._write(window._npc_dir() / "bank-robber.json")
    qapp.processEvents()

    assert window._state.npc_paths == ["bank-robber.json"]
    assert npc_names(window) == ["Bank Robber"]


def test_the_quick_wizard_saves_a_playable_npc_and_opens_nothing(
    qapp: QApplication, window: GMWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Five numbers, and the creature is in the cast before anything else is filled in.

    And **no window**: a GM making five mooks wanted five cards, not five sheets to
    close. The card lands collapsed for the same reason — a batch of goons is a
    batch, and a shrunk card is what a board wants a dozen of.
    """
    entered = QuickNPC(name="Bandit", attack=6, effect=5, defence=7, toughness=4, image_path=None)
    monkeypatch.setattr(QuickNPCDialog, "exec", lambda self: QDialog.DialogCode.Accepted)
    monkeypatch.setattr(QuickNPCDialog, "value", lambda self: entered)

    window._quick_npc()
    qapp.processEvents()

    # Saved and in the cast straight away — no trip through an unsaved sheet.
    assert window._state.npc_paths == ["bandit.json"]
    assert npc_names(window) == ["Bandit"]
    assert window._npc_windows == {}
    (card,) = npc_cards(window)
    assert card.collapsed is True
    # It is still a playable creature, carrying its two powers.
    assert [p.name for p in window._npc_state["bandit.json"].character.powers] == [
        "Damage",
        "Affliction",
    ]


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


def test_a_condition_replayed_into_an_open_npc_sheet_is_not_undoable(
    window: GMWindow,
) -> None:
    """The card's entry and the open sheet are two different Character objects.

    They are kept in step by replaying the settled ids; an undo on the sheet would
    roll one back and not the other, which is exactly the disagreement the replay
    exists to prevent. So the replay is absorbed rather than recorded.
    """
    path = write_npc("Ogre")
    window._register_npc(path)
    window._open_npc(path.name)
    sheet_window = next(iter(window._npc_windows.values()))
    sheet_window._sheet.abilities._abilities["STR"].setValue(4)

    window._apply_npc_condition(path.name, "dazed", None)

    assert [c.condition_id for c in sheet_window.sheet.character.conditions] == ["dazed"]

    sheet_window._undo.undo()

    assert sheet_window.sheet.character.abilities["STR"] == 0  # the GM's own edit
    assert [c.condition_id for c in sheet_window.sheet.character.conditions] == ["dazed"]
    sheet_window._dirty = False


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


def test_the_cast_never_sorts_itself_by_initiative(window: GMWindow) -> None:
    """It used to, exactly as the Scene does — one ordering on two boards.

    The cost was that the grid re-arranged itself under the GM's hands in the
    middle of a round, on the very cards they were reaching into. The turn order
    lives on the Scene; this is a cast list and it stays where it is put.
    """
    register_npcs(window, "Alpha", "Bravo", "Charlie")

    window._npc_state["bravo.json"].initiative = 24
    window._npc_state["charlie.json"].initiative = 18
    window._refresh_npcs()

    assert npc_names(window) == ["Alpha", "Bravo", "Charlie"]


def test_manual_order_is_honored(window: GMWindow) -> None:
    register_npcs(window, "Alpha", "Bravo", "Charlie")

    window._reorder_npc("charlie.json", 0)  # drag Charlie to the front

    assert npc_names(window) == ["Charlie", "Alpha", "Bravo"]


def test_dragging_a_card_in_the_cast_leaves_its_initiative_alone(window: GMWindow) -> None:
    """A drag here is an arrangement and nothing more.

    It used to clear the dragged NPC's roll, and a rolled neighbour's too, which
    was the right rule while this grid *was* the turn order. That rule lives on
    the Scene now, where the ordering it protects is.
    """
    register_npcs(window, "Alpha", "Bravo")
    window._npc_state["alpha.json"].initiative = 25

    window._reorder_npc("alpha.json", 99)

    assert window._npc_state["alpha.json"].initiative == 25
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


def test_rolling_an_entrys_initiative_uses_its_modifier_and_shows_a_badge(
    window: GMWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Rolled from the scene card, which is the one board that sorts by the number."""
    path = write_agile_npc("Ogre", agility=3)
    window._register_npc(path)
    window._set_in_scene(SCENE_NPC, path.name, True)
    monkeypatch.setattr(gm_window_module, "roll_d20", lambda *a, **k: 15)
    (ref,) = window._scene_board.ordered_refs()

    window._scene_board.card(ref)._badge.clicked.emit()

    # d20 15 + Agility 3, on the window's own state and on the board that reads it.
    assert window._npc_state[path.name].initiative == 18
    assert window._scene_board.card(ref).initiative == 18
    assert window._scene_board.card(ref)._badge.text() == "18"


def test_a_rolled_initiative_survives_a_refresh_of_the_cast(
    window: GMWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = write_agile_npc("Ogre", agility=0)
    window._register_npc(path)
    window._set_in_scene(SCENE_NPC, path.name, True)
    monkeypatch.setattr(gm_window_module, "roll_d20", lambda *a, **k: 12)
    window._roll_entry_initiative(window._scene_board.ordered_refs()[0])

    window._refresh_npcs()

    assert window._npc_state[path.name].initiative == 12
    (ref,) = window._scene_board.ordered_refs()
    assert window._scene_board.card(ref)._badge.text() == "12"


def test_only_the_gm_side_of_a_scene_card_rolls(window: GMWindow) -> None:
    """A player rolls their own initiative on their own sheet, so a GM's click on
    their badge would be rolling somebody else's die."""
    (goon,) = _npc_files(window, "Goon")
    window._show_roster([{"player_id": "p1", "display_name": "Alex", "connected": True}])
    window._set_in_scene(SCENE_NPC, goon, True)

    by_name = {c._name.text(): c for c in window._scene_board._cards.values()}

    assert by_name["Goon"]._badge._live is True
    assert by_name["Alex"]._badge._live is False


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


# --- collapsing a card, and the damage ladder on it -----------------------
#
# A collapsed card is the combat readout: name, initiative, the pinned numbers,
# the conditions, and the damage row. What it sheds is everything that describes
# the creature rather than tracks it through a fight.


def test_a_card_starts_expanded_and_shrinks_from_the_caret(window: GMWindow) -> None:
    quick_npc_file(window)
    (card,) = npc_cards(window)
    assert card.collapsed is False

    card._collapse_button.click()

    assert card.collapsed is True
    # The full portrait, the PL and the roster buttons are what a collapse sheds.
    assert card._portrait.isVisibleTo(card) is False
    assert card._pl_label.isVisibleTo(card) is False
    assert card._copy_button.isVisibleTo(card) is False
    # What it keeps: a thumbnail that still opens the sheet, the "+", the damage
    # row, and the pinned strip.
    assert card._thumb.isVisibleTo(card) is True
    assert card._condition_button.isVisibleTo(card) is True
    assert card._damage.isVisibleTo(card) is True
    assert card.pins.isVisibleTo(card) is True


def test_a_collapsed_card_is_much_shorter(qapp: QApplication, window: GMWindow) -> None:
    quick_npc_file(window)
    window.show()
    qapp.processEvents()
    (card,) = npc_cards(window)
    expanded = card.sizeHint().height()

    card.set_collapsed(True)
    qapp.processEvents()

    assert card.sizeHint().height() < expanded / 1.5
    window.hide()


# --------------------------------------------------------------------------
# How wide a card is
#
# A card knows what it needs; only the block knows what its columns need. The
# wrapping flow lays items out at their own size hint, so cards of differing
# widths stop lining up — which is why the width used to be a flat 210 + 150
# constant that no board ever actually wanted.
# --------------------------------------------------------------------------


def test_the_pinned_strip_fits_what_is_on_it(window: GMWindow) -> None:
    """It was a flat 150px whatever was pinned, which on a card reading "Dodge 12"
    is most of the card's width spent on white space."""
    quick_npc_file(window)
    (card,) = npc_cards(window)
    seeded = card.pins.natural_width()
    assert seeded < 150  # what the strip used to cost regardless

    card.pins.set_pins([])

    # Nothing pinned, nothing to fit — down to the floor that keeps the "+" reachable.
    assert card.pins.natural_width() < seeded
    assert card.pins.natural_width() == int(theme.metric("gm.pin-strip.min"))


def test_a_chip_measures_its_own_caption_and_reading(qapp: QApplication) -> None:
    from mm_companion.ui.pin_panel import PinChip

    ref = PinRef(kind="ability", key="AGL")
    short = PinChip(PinnedValue(ref=ref, label="Dodge", value="12"), 0)
    long = PinChip(PinnedValue(ref=ref, label="Fortitude save", value="12"), 0)

    assert short.natural_width() < long.natural_width()


def test_the_strip_never_grows_past_what_it_used_to_cost(window: GMWindow) -> None:
    """The cap is the old fixed width, so a long caption elides exactly as it
    always did and no strip is ever *wider* than before."""
    quick_npc_file(window)
    (card,) = npc_cards(window)
    card.pins.values = lambda: [  # type: ignore[method-assign]
        PinnedValue(
            ref=PinRef(kind="ability", key="AGL"),
            label="A caption nobody would ever really pin",
            value="12",
        )
    ]
    card.pins.refresh()

    assert card.pins.natural_width() == int(theme.metric("gm.pin-strip.max"))


def test_every_card_in_a_block_is_one_width(window: GMWindow) -> None:
    """Otherwise the flow stops forming columns and re-flows on every pin change."""
    quick_npc_file(window, name="Goon")
    quick_npc_file(window, name="Brute")
    quick_npc_file(window, name="Ogre")
    cards = npc_cards(window)
    window._npc_state["goon.json"].card.pins.set_pins([])

    window._sync_card_widths()

    assert len({card.width() for card in cards}) == 1
    assert len({card.pins.width() for card in cards}) == 1


def test_collapsing_a_card_never_reflows_its_neighbours(
    qapp: QApplication, window: GMWindow
) -> None:
    """One card shrinking must not shuffle the columns the others sit in.

    That is what the shared width buys, and it is why the width is the *block's*
    and not the card's. The 96px portrait is still in `body_width_hint` and a
    collapsed card sheds it — a ruleset whose damage ladder is narrower than a
    portrait would see a wholly shut board narrow — but with the bundled one the
    ladder wins, so the collapse takes its room out of the height as it always has.
    """
    quick_npc_file(window, name="Goon")
    quick_npc_file(window, name="Brute")
    window.show()
    qapp.processEvents()
    cards = npc_cards(window)
    before = cards[0].width()
    tall = cards[0].sizeHint().height()

    cards[0]._collapse_button.click()
    qapp.processEvents()

    assert {card.width() for card in npc_cards(window)} == {before}

    window._collapse_all_button.click()
    qapp.processEvents()

    assert {card.width() for card in npc_cards(window)} == {before}
    assert all(card.sizeHint().height() < tall for card in npc_cards(window))
    window.hide()


def test_the_board_is_far_narrower_than_the_constant_it_replaced(
    window: GMWindow,
) -> None:
    """Every card used to be 210 + 150 + spacing = 372px whatever was on it."""
    quick_npc_file(window)
    (card,) = npc_cards(window)

    assert card.width() < 300
    assert card.width() == card.body_width_hint() + card.pin_width_hint() + (
        int(theme.metric("space.sm")) * 3
    )


def test_collapsing_is_remembered_per_card(window: GMWindow) -> None:
    """It survives the rebuild every initiative roll and condition change causes."""
    name = quick_npc_file(window)
    (card,) = npc_cards(window)

    card._collapse_button.click()
    window._refresh_npcs()

    (rebuilt,) = npc_cards(window)
    assert rebuilt is not card
    assert rebuilt.collapsed is True
    assert storage.gm_collapsed_cards() == {f"npc:{name}": True}


def test_reopening_a_card_is_remembered_too(window: GMWindow) -> None:
    quick_npc_file(window)
    (card,) = npc_cards(window)

    card._collapse_button.click()
    card._collapse_button.click()

    assert card.collapsed is False
    # Only the shrunk ones are stored, so the file stays down to the exceptions.
    assert storage.gm_collapsed_cards() == {}


def test_set_collapsed_is_silent(window: GMWindow) -> None:
    """The owner telling the card what it already decided must not echo back."""
    quick_npc_file(window)
    (card,) = npc_cards(window)
    heard: list[bool] = []
    card.collapsedChanged.connect(lambda _name, state: heard.append(state))

    card.set_collapsed(True)

    assert heard == []


def test_a_copied_npc_inherits_the_shrunk_card(window: GMWindow) -> None:
    """Copying a mook is how a GM makes the fourth guard, who wants guard three's
    card rather than a fresh one."""
    name = quick_npc_file(window)
    (card,) = npc_cards(window)
    card._collapse_button.click()

    window._copy_npc(name)

    assert storage.gm_collapsed_cards().get("npc:goon-2.json") is True
    assert all(one.collapsed for one in npc_cards(window))


def test_deleting_an_npc_forgets_that_it_was_shrunk(
    window: GMWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    name = quick_npc_file(window)
    (card,) = npc_cards(window)
    card._collapse_button.click()
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)

    window._delete_npc(name)

    assert storage.gm_collapsed_cards() == {}


def test_the_collapsed_strip_shows_four_pins_and_scrolls_for_the_rest(
    window: GMWindow,
) -> None:
    quick_npc_file(window)
    (card,) = npc_cards(window)
    card.pins.set_pins([PinRef("ability", ability.key) for ability in load_game_data().abilities])
    uncapped = card.pins.sizeHint().height()

    card.set_collapsed(True)

    assert card.pins.sizeHint().height() < uncapped
    assert card.pins._scroll.maximumHeight() > 0
    # Every pin is still *there* — the strip is a window onto them, not a cap.
    assert len(card.pins.chip_texts()) == len(load_game_data().abilities)


def test_the_damage_row_is_on_both_states(window: GMWindow) -> None:
    """A GM who never collapses a card still wants one-click damage."""
    quick_npc_file(window)
    (card,) = npc_cards(window)

    assert card._damage.isVisibleTo(card) is True
    card.set_collapsed(True)
    assert card._damage.isVisibleTo(card) is True


def test_a_degree_button_puts_the_whole_rung_on_the_npc(window: GMWindow) -> None:
    quick_npc_file(window)
    (card,) = npc_cards(window)

    card._damage.stepChosen.emit(2)

    ids = [applied.condition_id for applied in card.character.conditions]
    assert {"hit", "staggered", "stunned"} <= set(ids)
    assert "dazed" not in ids
    assert "Stunned" in card.condition_names()


def test_a_degree_button_escalates_against_what_the_npc_already_has(
    window: GMWindow,
) -> None:
    quick_npc_file(window)
    (card,) = npc_cards(window)

    card._damage.stepChosen.emit(1)
    card._damage.stepChosen.emit(1)

    ids = [applied.condition_id for applied in card.character.conditions]
    assert "stunned" in ids
    assert "dazed" not in ids
    # And the GM is told which of the two rungs actually landed.
    assert "Stunned" in window._notice_label.text()


def test_damage_reaches_the_file(window: GMWindow) -> None:
    name = quick_npc_file(window)
    (card,) = npc_cards(window)

    card._damage.stepChosen.emit(1)

    saved = library.load_character(window._npc_state[name].path)
    assert {"hit", "dazed"} <= {applied.condition_id for applied in saved.conditions}


def test_damage_reaches_an_open_sheet_rather_than_the_file(window: GMWindow) -> None:
    """An open sheet owns its own save, and holds its own copy of the character —
    so it is handed the ids the escalation settled on, not the rung."""
    name = quick_npc_file(window)
    (card,) = npc_cards(window)
    window._open_npc(name)
    sheet = next(iter(window._npc_windows.values())).sheet

    card._damage.stepChosen.emit(2)

    ids = {applied.condition_id for applied in sheet.character.conditions}
    assert {"hit", "staggered", "stunned"} <= ids
    assert "dazed" not in ids


def test_the_damage_buttons_say_what_they_will_do(window: GMWindow) -> None:
    """Resolved against this creature, so an escalation is visible before the
    click rather than a surprise after it."""
    quick_npc_file(window)
    (card,) = npc_cards(window)
    assert "Dazed" in card._damage.button_tooltips()[1]

    apply_condition(card.character, "dazed", load_game_data())
    card.refresh_conditions()

    assert "Stunned" in card._damage.button_tooltips()[1]


def test_collapse_all_shrinks_every_card_and_says_what_it_will_do(
    window: GMWindow,
) -> None:
    quick_npc_file(window, name="Goon")
    quick_npc_file(window, name="Brute")
    assert window._collapse_all_button.text() == gm_window_module.COLLAPSE_ALL

    window._collapse_all_button.click()

    assert all(card.collapsed for card in npc_cards(window))
    # The caption is a readout of the board as well as the next action.
    assert window._collapse_all_button.text() == gm_window_module.EXPAND_ALL
    assert len(storage.gm_collapsed_cards()) == 2

    window._collapse_all_button.click()

    assert not any(card.collapsed for card in npc_cards(window))
    assert window._collapse_all_button.text() == gm_window_module.COLLAPSE_ALL
    assert storage.gm_collapsed_cards() == {}


def test_one_card_still_open_means_collapse_all(window: GMWindow) -> None:
    """Anything open means "collapse"; only a wholly shut board offers to expand."""
    quick_npc_file(window, name="Goon")
    quick_npc_file(window, name="Brute")
    first, _second = npc_cards(window)

    first._collapse_button.click()

    assert window._collapse_all_button.text() == gm_window_module.COLLAPSE_ALL
    window._collapse_all_button.click()
    assert all(card.collapsed for card in npc_cards(window))


def test_collapse_all_is_dead_with_no_cast(window: GMWindow) -> None:
    assert window._collapse_all_button.isEnabled() is False

    quick_npc_file(window)

    assert window._collapse_all_button.isEnabled() is True


def test_an_npc_card_carries_no_initiative_and_no_eye(window: GMWindow) -> None:
    """Both moved to the Scene, which is the board that is about the fight.

    Two eyes for one fact and two boards sorting by one number were each the GM
    holding the same thing in two places.
    """
    quick_npc_file(window)
    (card,) = npc_cards(window)

    assert not hasattr(card, "_initiative_badge")
    assert not hasattr(card, "_initiative_button")
    assert not hasattr(card, "_scene_eye")
    assert not hasattr(card, "roll_initiative")


def test_right_clicking_a_scene_badge_clears_the_initiative(window: GMWindow) -> None:
    goon, brute = _npc_files(window, "Goon", "Brute")
    for name in (goon, brute):
        window._set_in_scene(SCENE_NPC, name, True)
    window._roll_scene_initiative()
    assert window._npc_state[goon].initiative is not None
    ref = next(e.ref for e in window._scene if e.source == goon)

    right_click(window._scene_board.card(ref)._badge)

    # Cleared in the window's state, and back in the un-rolled zone on the board.
    assert window._npc_state[goon].initiative is None
    assert window._scene_board.card(ref)._badge.text() == card_chips.NO_INITIATIVE
    assert window._scene_board.ordered_refs()[-1] == ref


def test_right_clicking_an_npc_chip_sheds_the_condition(window: GMWindow) -> None:
    name = quick_npc_file(window)
    window._apply_npc_condition(name, "dazed", None)
    (card,) = npc_cards(window)
    assert card.condition_names() == ["Dazed"]

    right_click(card._chip_flow.itemAt(0).widget())

    assert window._npc_state[name].card.condition_names() == []
    assert library.load_character(window._npc_state[name].path).conditions == []


def test_a_chip_right_click_never_reaches_the_cards_own_menu(
    window: GMWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The card offers "Remove from this session / Delete" on a right-click, and
    that must not be what someone aiming at a chip gets."""
    name = quick_npc_file(window)
    window._apply_npc_condition(name, "dazed", None)
    (card,) = npc_cards(window)
    opened: list[QMenu] = []
    monkeypatch.setattr(QMenu, "exec", lambda self, *a, **k: opened.append(self))

    right_click(card._chip_flow.itemAt(0).widget())

    assert opened == []


# -- requesting a roll --------------------------------------------------------


def test_the_gm_can_ask_the_table_for_a_roll(qapp: QApplication, window: GMWindow) -> None:
    """The GM's roller gets the Request row on the same terms a player's does."""
    index = window._roller._request_combo.findText("  Perception")
    assert index >= 0
    window._roller._request_combo.setCurrentIndex(index)
    window._roller._request_dc.setValue(15)
    window._roller._request_button.click()
    qapp.processEvents()

    card = window._history.findChild(RequestCard)
    assert card is not None
    button = next(b for b in card.findChildren(QPushButton) if b.text().startswith("🎲"))
    assert button.text() == "🎲 Perception vs. DC 15"


def test_an_offline_request_is_strikeable_like_an_offline_roll(
    qapp: QApplication, window: GMWindow
) -> None:
    """Before hosting there is no session to record it in, so it gets a negative seq.

    Without one the button would silently do nothing off the air, which reads as a
    bug — the same reason ``_show_offline_roll`` exists.
    """
    window._request_roll(RollSpec(label="Perception", kind=KIND_SKILL, trait_key="Perception"))
    qapp.processEvents()

    card = window._history.findChild(RequestCard)
    assert card is not None and card.seq is not None and card.seq < 0


# --------------------------------------------------------------------------
# The Scene
#
# The shared board: which creatures are on it, in what order, and what reaches
# the table. The drop tests matter more than most, because converting the NPC
# grid's pseudo-drag into a real QDrag is what made a cross-block drag possible
# at all — and nothing covered the old gesture.
# --------------------------------------------------------------------------


def _npc_files(window: GMWindow, *names: str) -> list[str]:
    """Save a quick NPC per name into the session's cast, and return the file names."""
    paths = [
        library.save_character(
            quick_npc(window._data, name=name, attack=4, effect=6, defence=6, toughness=6),
            directory=storage.get_workspace().gm_characters_dir,
        ).name
        for name in names
    ]
    window._set_npc_paths(paths)
    window._refresh_npcs()
    return paths


def test_a_real_drag_lands_on_an_empty_scene(qapp: QApplication, window: GMWindow) -> None:
    """The regression the whole rework started from: the *first* drop always failed.

    Driven with real drag events at a real point, because that is the half that
    was broken. The handler was always fine — the empty board simply had nothing
    under the pointer that would take a payload, since the flow was hidden while it
    held no cards and the sentence saying "drag one here" was a plain label beside
    it. Asserting the widget under the middle of the board is what pins that down.
    """
    (goon,) = _npc_files(window, "Goon")
    window.show()
    qapp.processEvents()
    board = window._scene_board
    assert board.is_empty()

    assert board.childAt(board.rect().center()) is board._flow_host

    host = board._flow_host
    mime = QMimeData()
    mime.setData(card_chips.SCENE_MIME, f"{SCENE_NPC}:{goon}".encode())
    point = host.rect().center()
    enter = QDragEnterEvent(
        point,
        Qt.DropAction.MoveAction,
        mime,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    qapp.sendEvent(host, enter)
    assert enter.isAccepted()

    qapp.sendEvent(
        host,
        QDropEvent(
            QPointF(point),
            Qt.DropAction.MoveAction,
            mime,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        ),
    )
    qapp.processEvents()

    assert [e.source for e in window._scene] == [goon]
    window.hide()


def test_a_dragged_npc_joins_the_scene_and_its_card_takes_it_off(window: GMWindow) -> None:
    """The two gestures the eye used to be, split between the two boards that own
    them: the cast says "put this on the board" and the board says "take it off"."""
    goon, _boss = _npc_files(window, "Goon", "Boss")

    window._drop_on_scene(f"{SCENE_NPC}:{goon}", 0)
    assert [e.source for e in window._scene] == [goon]

    (ref,) = window._scene_board.ordered_refs()
    window._scene_board.card(ref).removeRequested.emit(ref)
    assert window._scene == []


def test_the_scene_carries_a_name_and_the_conditions_and_nothing_else(
    window: GMWindow,
) -> None:
    """A player reads the visible battlefield state off a card, not a statblock —
    and the guarantee is that the other fields are never put on the wire."""
    (goon,) = _npc_files(window, "Goon")
    window._set_in_scene(SCENE_NPC, goon, True)
    window._apply_npc_condition(goon, "dazed", None)

    entry = window._scene_payload()[0]

    assert entry["name"] == "Goon"
    assert entry["conditions"] == [{"id": "dazed"}]
    # Disposition is public on purpose and is the only field here that is the GM's
    # *judgement* rather than a reading: telling friend from foe is most of what a
    # player needs the board for, and it is a thing only the GM knows.
    assert entry["disposition"] == "enemy"
    assert set(entry) <= {
        "ref",
        "name",
        "player_id",
        "initiative",
        "disposition",
        "conditions",
    }


def test_a_scene_ref_says_nothing_about_the_file_it_stands_for(window: GMWindow) -> None:
    """An NPC's file name can be a spoiler outright, and the Scene is exactly where
    a GM would find that out too late."""
    (goon,) = _npc_files(window, "Goon")
    window._set_in_scene(SCENE_NPC, goon, True)

    entry = window._scene_payload()[0]

    assert "goon" not in entry["ref"].lower()
    assert entry["ref"] != goon


def test_rolling_for_the_scene_rolls_only_what_is_on_it(window: GMWindow) -> None:
    goon, boss = _npc_files(window, "Goon", "Boss")
    window._set_in_scene(SCENE_NPC, goon, True)

    window._roll_scene_initiative()

    assert window._npc_state[goon].initiative is not None
    assert window._npc_state[boss].initiative is None


def test_a_scene_roll_shows_up_on_the_board_that_owns_it(window: GMWindow) -> None:
    """One number with one owner: the window holds it and the board reads the same
    field, so the two cannot come to disagree."""
    (goon,) = _npc_files(window, "Goon")
    window._set_in_scene(SCENE_NPC, goon, True)

    window._roll_scene_initiative()

    rolled = window._npc_state[goon].initiative
    (ref,) = window._scene_board.ordered_refs()
    assert window._scene_board.card(ref).initiative == rolled
    assert window._scene_payload()[0]["initiative"] == rolled


def test_rolling_for_the_board_leaves_the_cast_cards_where_they_are(
    window: GMWindow,
) -> None:
    """It used to rebuild the whole grid to re-sort it, taking every hover and
    scroll position with it. Nothing about the cast depends on initiative now."""
    goon, boss = _npc_files(window, "Goon", "Boss")
    for name in (goon, boss):
        window._set_in_scene(SCENE_NPC, name, True)
    before = [id(entry.card) for entry in window._npc_state.values()]

    window._roll_scene_initiative()

    assert [id(entry.card) for entry in window._npc_state.values()] == before


def test_the_board_puts_the_rolled_above_the_unrolled(window: GMWindow) -> None:
    goon, boss = _npc_files(window, "Goon", "Boss")
    window._set_in_scene(SCENE_NPC, goon, True)
    window._set_in_scene(SCENE_NPC, boss, True)
    window._npc_state[boss].initiative = 20
    window._push_scene()

    assert [e["name"] for e in window._scene_payload()] == ["Goon", "Boss"]
    ordered = window._scene_board.ordered_refs()
    assert window._scene_entry(ordered[0]).source == boss


def test_dropping_a_card_into_the_manual_zone_costs_it_its_initiative(
    window: GMWindow,
) -> None:
    """The rule the NPC grid already spells out, and for its reason: a drop is the
    GM arranging by hand, which no rolled number can be sorted around."""
    goon, boss = _npc_files(window, "Goon", "Boss")
    window._set_in_scene(SCENE_NPC, goon, True)
    window._set_in_scene(SCENE_NPC, boss, True)
    window._npc_state[goon].initiative = 18
    window._push_scene()
    ref = window._scene_entry_for(SCENE_NPC, goon).ref

    # Slot 2: past the Boss, so it is a real move rather than its own place back.
    window._drop_on_scene(ref, 2)

    assert window._npc_state[goon].initiative is None


def test_a_card_dropped_back_on_its_own_slot_keeps_its_roll(window: GMWindow) -> None:
    """Every path through a drop clears an initiative, so a GM who picked a rolled
    card up and put it down again would silently lose it."""
    goon, boss = _npc_files(window, "Goon", "Boss")
    for name in (goon, boss):
        window._set_in_scene(SCENE_NPC, name, True)
    window._npc_state[goon].initiative = 18
    window._push_scene()
    ref = window._scene_entry_for(SCENE_NPC, goon).ref
    order = window._scene_board.ordered_refs()
    assert order[0] == ref

    # Both gaps either side of a card name its own place.
    window._drop_on_scene(ref, 0)
    window._drop_on_scene(ref, 1)

    assert window._npc_state[goon].initiative == 18
    assert window._scene_board.ordered_refs() == order


def test_dropping_an_unrolled_card_in_front_of_a_rolled_one_clears_both(
    window: GMWindow,
) -> None:
    """Putting an un-rolled creature first is the GM saying it acts first, which is
    impossible while the other keeps a number to sort by. One of the two has to go."""
    goon, boss = _npc_files(window, "Goon", "Boss")
    window._set_in_scene(SCENE_NPC, goon, True)
    window._set_in_scene(SCENE_NPC, boss, True)
    window._npc_state[goon].initiative = 18
    window._push_scene()
    boss_ref = window._scene_entry_for(SCENE_NPC, boss).ref

    window._drop_on_scene(boss_ref, 0)

    assert window._npc_state[goon].initiative is None
    assert window._scene_board.ordered_refs()[0] == boss_ref


def test_dragging_a_roster_card_onto_the_scene_adds_it(window: GMWindow) -> None:
    """The other half of the eye, and the one that needed the gesture to be a real
    drag: this ref came from a card in a different block."""
    (goon,) = _npc_files(window, "Goon")

    window._scene_board.dropped.emit(f"{SCENE_NPC}:{goon}", 0)

    assert [e.source for e in window._scene] == [goon]


def test_a_ref_from_no_roster_at_all_is_refused(window: GMWindow) -> None:
    _npc_files(window, "Goon")

    window._scene_board.dropped.emit(f"{SCENE_NPC}:nobody.json", 0)

    assert window._scene == []


def test_dragging_an_npc_within_its_own_grid_still_reorders_it(window: GMWindow) -> None:
    """The regression the QDrag conversion could have caused: the grid's own
    reorder is the gesture that changed, and nothing covered it before."""
    alpha, bravo, charlie = _npc_files(window, "Alpha", "Bravo", "Charlie")
    assert window._ordered_npcs() == [alpha, bravo, charlie]

    window._npc_container.dropped.emit(f"{SCENE_NPC}:{charlie}", 0)

    assert window._ordered_npcs() == [charlie, alpha, bravo]


def test_a_player_dropped_on_the_npc_grid_is_refused(qapp: QApplication, window: GMWindow) -> None:
    """Not a thing that can be done — and refused *visibly*, which is the half that
    was missing: the grid used to light up green for a player and then drop it on
    the floor, which reads as a broken gesture rather than a refused one."""
    alpha, bravo = _npc_files(window, "Alpha", "Bravo")

    window._npc_container.dropped.emit(f"{SCENE_PLAYER}:p1", 0)
    assert window._ordered_npcs() == [alpha, bravo]

    _drag_enter(qapp, window._npc_container, f"{SCENE_PLAYER}:p1")
    assert window._npc_container._feedback.state == DropFeedback.REJECT

    _drag_enter(qapp, window._npc_container, f"{SCENE_NPC}:{alpha}")
    assert window._npc_container._feedback.state == DropFeedback.ACCEPT


def test_an_npc_removed_from_the_session_leaves_the_scene_with_it(window: GMWindow) -> None:
    (goon,) = _npc_files(window, "Goon")
    window._set_in_scene(SCENE_NPC, goon, True)

    window._remove_npc(goon)

    assert window._scene == []


def test_a_players_initiative_arrives_on_the_shared_roll_log(window: GMWindow) -> None:
    """No message of its own: a roll already carries the spec that says what it was,
    which catches the request card and a player's own sheet at once."""
    window._show_roster([{"player_id": "p1", "display_name": "Alex", "connected": True}])
    assert window._scene_entry_for(SCENE_PLAYER, "p1") is not None

    window._on_roll_added(
        {
            "kind": "roll",
            "player_id": "p1",
            "die": 15,
            "bonus": 4,
            "spec": {"kind": "initiative", "label": "Initiative"},
        }
    )

    assert window._player_initiative == {"p1": 19}
    assert window._scene_payload()[0]["initiative"] == 19


def test_a_roll_that_is_not_an_initiative_is_left_alone(window: GMWindow) -> None:
    window._show_roster([{"player_id": "p1", "display_name": "Alex", "connected": True}])

    window._on_roll_added({"kind": "roll", "player_id": "p1", "die": 20, "spec": {"kind": "skill"}})

    assert window._player_initiative == {}


def test_a_players_initiative_is_ignored_while_they_are_off_the_board(
    window: GMWindow,
) -> None:
    storage.set_gm_scene_auto_players(False)
    window._show_roster([{"player_id": "p1", "display_name": "Alex", "connected": True}])
    assert window._scene == []

    window._on_roll_added(
        {"kind": "roll", "player_id": "p1", "die": 15, "spec": {"kind": "initiative"}}
    )

    assert window._player_initiative == {}


def test_players_join_the_board_by_themselves_by_default(window: GMWindow) -> None:
    window._show_roster([{"player_id": "p1", "display_name": "Alex", "connected": True}])

    assert [(e.kind, e.source) for e in window._scene] == [(SCENE_PLAYER, "p1")]
    assert not window._cards["p1"]._scene_eye.isVisibleTo(window._cards["p1"])


def test_with_the_preference_off_a_player_waits_for_the_eye(window: GMWindow) -> None:
    storage.set_gm_scene_auto_players(False)

    window._show_roster([{"player_id": "p1", "display_name": "Alex", "connected": True}])

    assert window._scene == []
    card = window._cards["p1"]
    card.sceneToggled.emit("p1", True)
    assert [(e.kind, e.source) for e in window._scene] == [(SCENE_PLAYER, "p1")]


def test_a_seat_that_leaves_takes_its_place_on_the_board_with_it(window: GMWindow) -> None:
    window._show_roster([{"player_id": "p1", "display_name": "Alex", "connected": True}])
    window._on_roll_added(
        {"kind": "roll", "player_id": "p1", "die": 15, "spec": {"kind": "initiative"}}
    )
    assert window._player_initiative

    window._show_roster([])

    assert window._scene == []
    assert window._player_initiative == {}


def test_a_new_scene_clears_the_board_and_every_initiative(
    window: GMWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    (goon,) = _npc_files(window, "Goon")
    window._set_in_scene(SCENE_NPC, goon, True)
    window._roll_scene_initiative()
    assert window._npc_state[goon].initiative is not None

    window._new_scene()

    assert window._scene == []
    assert window._npc_state[goon].initiative is None


def test_a_new_scene_keeps_the_players_when_they_join_by_themselves(
    window: GMWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Clearing them would be undone by the next roster anyway, and a button that
    visibly does not do what it says is worse than one that does less."""
    (goon,) = _npc_files(window, "Goon")
    window._set_in_scene(SCENE_NPC, goon, True)
    window._show_roster([{"player_id": "p1", "display_name": "Alex", "connected": True}])

    window._new_scene()

    assert [(e.kind, e.source) for e in window._scene] == [(SCENE_PLAYER, "p1")]


def test_a_new_scene_takes_two_clicks_and_opens_no_dialog(
    window: GMWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The button asks in its own caption instead of stopping the table with a
    modal, which is the wrong thing to do in the middle of a round."""
    monkeypatch.setattr(
        gm_window_module.QMessageBox,
        "question",
        lambda *a, **k: pytest.fail("New scene must not open a dialog"),
    )
    (goon,) = _npc_files(window, "Goon")
    window._set_in_scene(SCENE_NPC, goon, True)
    button = window._new_scene_button

    button.click()

    assert button.armed is True
    assert button.text() != "New scene"
    assert [e.source for e in window._scene] == [goon]

    button.click()

    assert button.armed is False
    assert button.text() == "New scene"
    assert window._scene == []


def test_an_armed_new_scene_disarms_itself(window: GMWindow) -> None:
    """A stray click must not leave a live trigger sitting on the board."""
    (goon,) = _npc_files(window, "Goon")
    window._set_in_scene(SCENE_NPC, goon, True)
    button = window._new_scene_button

    button.click()
    button.disarm()  # what the timer does when nobody comes back
    button.click()

    assert button.armed is True
    assert [e.source for e in window._scene] == [goon]


# --------------------------------------------------------------------------
# Coming back to a fight in progress
#
# `scene_sources` is written on every push, persisted with the session and handed
# back in the GM's own Welcome — for this, exactly as `npc_paths` is. It had no
# reader for a while, and the cost was that a GM who closed the app mid-fight came
# back to a full cast and an empty board, and the first push after that wrote the
# empty board over the stored one.
# --------------------------------------------------------------------------


def _reopened(qapp: QApplication, window: GMWindow) -> GMWindow:
    """Close *window* and open a second one onto the same stored session."""
    session_id = window._state.id
    window.close()
    qapp.processEvents()
    return GMWindow(bind="127.0.0.1", state=store.load_session(session_id))


def test_the_board_survives_closing_the_app_mid_fight(qapp: QApplication, window: GMWindow) -> None:
    goon, boss = _npc_files(window, "Goon", "Boss")
    start_hosting(qapp, window, canned())
    for name in (goon, boss):
        window._set_in_scene(SCENE_NPC, name, True)
    window._set_disposition(window._scene_entry_for(SCENE_NPC, boss).ref, "friendly")
    window._npc_state[goon].initiative = 18
    window._push_scene()  # what every real mutation ends with

    reopened = _reopened(qapp, window)
    try:
        assert [(e.source, e.disposition) for e in reopened._scene] == [
            (goon, "enemy"),
            (boss, "friendly"),
        ]
        # The numbers come back with it: a turn order without them is most of the
        # way to no turn order at all.
        assert reopened._npc_state[goon].initiative == 18
        assert reopened._scene_board.ordered_refs()[0] == reopened._scene[0].ref
    finally:
        reopened.close()


def test_a_creature_deleted_between_sessions_is_not_restored(
    qapp: QApplication, window: GMWindow
) -> None:
    """Its place is dropped rather than restored as a card standing for nothing."""
    goon, boss = _npc_files(window, "Goon", "Boss")
    start_hosting(qapp, window, canned())
    for name in (goon, boss):
        window._set_in_scene(SCENE_NPC, name, True)
    session_id = window._state.id
    window.close()
    qapp.processEvents()
    (storage.get_workspace().gm_characters_dir / boss).unlink()

    reopened = GMWindow(bind="127.0.0.1", state=store.load_session(session_id))
    try:
        assert [e.source for e in reopened._scene] == [goon]
    finally:
        reopened.close()


def test_the_seats_are_left_for_the_players_to_take_back(
    qapp: QApplication, window: GMWindow
) -> None:
    """A restored seat would be dropped by the very next roster anyway — there is
    nothing to show for a player who is not there, which is the rule
    ``_sync_scene_players`` already keeps."""
    start_hosting(qapp, window, canned())
    window._show_roster(roster({"display_name": "Aria"}))
    assert any(e.kind == SCENE_PLAYER for e in window._scene)

    reopened = _reopened(qapp, window)
    try:
        assert reopened._scene == []
    finally:
        reopened.close()


def test_a_board_already_built_is_never_overwritten_by_a_restore(
    qapp: QApplication, window: GMWindow
) -> None:
    """The restore runs once at startup. Called again it must be a no-op, or a
    later one would drag a fight that has moved on back to where it began."""
    (goon,) = _npc_files(window, "Goon")
    start_hosting(qapp, window, canned())
    window._set_in_scene(SCENE_NPC, goon, True)
    before = list(window._scene)

    window._restore_scene()

    assert window._scene == before


# --------------------------------------------------------------------------
# More on the board than the wire will carry
# --------------------------------------------------------------------------


def test_a_board_past_the_wires_limit_says_so(qapp: QApplication, window: GMWindow) -> None:
    """``sanitize_scene`` keeps the first `MAX_SCENE_ENTRIES` and drops the rest,
    silently and by *insertion* order — which is not the order the board reads in,
    so what falls off can be a creature at the top of the initiative. The GM's own
    board shows all of it either way."""
    limit = gm_window_module.MAX_SCENE_ENTRIES
    names = _npc_files(window, *[f"Mook {n}" for n in range(limit + 2)])
    start_hosting(qapp, window, canned())
    for name in names[:limit]:
        window._set_in_scene(SCENE_NPC, name, True)

    assert window._notice_label.text() == ""

    window._set_in_scene(SCENE_NPC, names[limit], True)

    assert "1 more creature is" in window._notice_label.text()
    assert str(limit) in window._notice_label.text()

    window._set_in_scene(SCENE_NPC, names[limit + 1], True)

    assert "2 more creatures are" in window._notice_label.text()


# --------------------------------------------------------------------------
# What a creature is to the table
#
# The board's one piece of colour, and the only field on an entry that is the
# GM's judgement rather than a reading off a model. Public on purpose: telling
# friend from foe at a glance is most of what a player needs the board for, and
# it is a thing only the GM knows.
# --------------------------------------------------------------------------


def test_an_npc_starts_an_enemy_and_a_seat_starts_a_player(window: GMWindow) -> None:
    """The safe way round rather than the tidy one: a board is mostly things to
    fight, so the mistake the default can make is an ally drawn as a threat."""
    (goon,) = _npc_files(window, "Goon")
    window._show_roster([{"player_id": "p1", "display_name": "Alex", "connected": True}])
    window._set_in_scene(SCENE_NPC, goon, True)

    by_name = {e["name"]: e["disposition"] for e in window._scene_payload()}

    assert by_name == {"Goon": "enemy", "Alex": "player"}


def test_the_gm_can_say_what_a_creature_is(window: GMWindow) -> None:
    (vale,) = _npc_files(window, "Vale")
    window._set_in_scene(SCENE_NPC, vale, True)
    (ref,) = window._scene_board.ordered_refs()

    window._scene_board.card(ref).dispositionChanged.emit(ref, "friendly")

    assert window._scene_entry(ref).disposition == "friendly"
    assert window._scene_payload()[0]["disposition"] == "friendly"
    assert window._scene_board.card(ref).disposition == "friendly"


def test_a_seat_can_never_be_called_anything_but_a_player(window: GMWindow) -> None:
    """A seat is a player. The card offers no way to say otherwise and the window
    refuses it anyway — the signal is public, and the lie would reach the table."""
    window._show_roster([{"player_id": "p1", "display_name": "Alex", "connected": True}])
    (ref,) = window._scene_board.ordered_refs()

    window._set_disposition(ref, "enemy")

    assert window._scene_entry(ref).disposition == "player"


def test_a_player_entrys_card_offers_no_disposition_menu(window: GMWindow) -> None:
    (goon,) = _npc_files(window, "Goon")
    window._show_roster([{"player_id": "p1", "display_name": "Alex", "connected": True}])
    window._set_in_scene(SCENE_NPC, goon, True)
    cards = {c._name.text(): c for c in window._scene_board._cards.values()}

    offered = {
        name: [a.text() for a in card.build_context_menu().actions()]
        for name, card in cards.items()
    }

    assert "Mark as" not in offered["Alex"]
    assert "Mark as" in offered["Goon"]


def test_the_mark_as_menu_ticks_what_the_creature_already_is(window: GMWindow) -> None:
    """It is a state with one answer at a time, so the tick is half of what the
    menu is for."""
    (vale,) = _npc_files(window, "Vale")
    window._set_in_scene(SCENE_NPC, vale, True)
    (ref,) = window._scene_board.ordered_refs()
    window._set_disposition(ref, "neutral")

    import gc

    menu = window._scene_board.card(ref).build_context_menu()
    # Collected aggressively, for the reason
    # ``test_the_submenus_outlive_the_call_that_built_them`` gives: an unparented
    # submenu vanishes out from under the menu that is showing it.
    gc.collect()
    (marks,) = [a.menu() for a in menu.actions() if a.menu() is not None]

    assert [a.text() for a in marks.actions()] == ["Enemy", "Friendly", "Neutral"]
    assert [a.text() for a in marks.actions() if a.isChecked()] == ["Neutral"]


def test_each_disposition_draws_its_own_edge(window: GMWindow) -> None:
    """Four colours a glance can tell apart, and four tokens a preset can retune."""
    names = _npc_files(window, "A", "B", "C")
    for name in names:
        window._set_in_scene(SCENE_NPC, name, True)
    refs = window._scene_board.ordered_refs()
    for ref, value in zip(refs, ("enemy", "friendly", "neutral"), strict=True):
        window._set_disposition(ref, value)

    sheets = [window._scene_board.card(r).styleSheet() for r in refs]

    for sheet, value in zip(sheets, ("enemy", "friendly", "neutral"), strict=True):
        assert theme.color(f"scene.{value}") in sheet
    assert len(set(sheets)) == 3


def test_the_card_says_what_it_is_in_words_as_well(window: GMWindow) -> None:
    """A fact told only in colour is told to fewer people than one told twice."""
    (vale,) = _npc_files(window, "Vale")
    window._set_in_scene(SCENE_NPC, vale, True)
    (ref,) = window._scene_board.ordered_refs()
    window._set_disposition(ref, "friendly")

    assert "Friendly" in window._scene_board.card(ref)._name.toolTip()


# --------------------------------------------------------------------------
# Putting a creature on the board without a mouse
# --------------------------------------------------------------------------


def test_an_npc_card_can_seat_itself_from_its_own_menu(window: GMWindow) -> None:
    """The drag is the quick answer; this is the one that always works — from a
    keyboard, without a steady hand, and when the Scene block is hidden outright."""
    (goon,) = _npc_files(window, "Goon")
    (card,) = npc_cards(window)
    assert card.in_scene is False

    card.sceneToggled.emit(goon, True)

    assert [e.source for e in window._scene] == [goon]
    assert npc_cards(window)[0].in_scene is True

    npc_cards(window)[0].sceneToggled.emit(goon, False)

    assert window._scene == []
    assert npc_cards(window)[0].in_scene is False


def test_the_menu_item_says_which_way_it_will_go(window: GMWindow) -> None:
    (goon,) = _npc_files(window, "Goon")

    before = [a.text() for a in npc_cards(window)[0].build_context_menu().actions()]
    window._set_in_scene(SCENE_NPC, goon, True)
    after = [a.text() for a in npc_cards(window)[0].build_context_menu().actions()]

    assert "Put on the Scene" in before
    assert "Take off the Scene" in after


def test_the_players_block_visibly_refuses_an_npc(qapp: QApplication, window: GMWindow) -> None:
    """It takes a drop only so a player's own card dragged out and back reads as a
    cancelled drag. It used to take every card and silently drop the rest."""
    flow = window._cards_container

    _drag_enter(qapp, flow, f"{SCENE_NPC}:goon.json")
    assert flow._feedback.state == DropFeedback.REJECT

    _drag_enter(qapp, flow, f"{SCENE_PLAYER}:p1")
    assert flow._feedback.state == DropFeedback.ACCEPT


def _drag_enter(qapp: QApplication, widget, ref: str) -> None:
    """Drive a real drag over *widget*. The mime is held for the send: the event
    does not own it, and a collected one reads back as a bare ``QObject``."""
    mime = QMimeData()
    mime.setData(card_chips.SCENE_MIME, ref.encode())
    qapp.sendEvent(
        widget,
        QDragEnterEvent(
            widget.rect().center(),
            Qt.DropAction.MoveAction,
            mime,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        ),
    )


def test_a_condition_on_a_scene_npc_reaches_the_table(window: GMWindow) -> None:
    """The bug the two-window run caught: the GM's own card updated and the
    players went on looking at an undazed creature.

    ``_after_npc_condition_change`` is the *only* path that moves an NPC's
    conditions without going through ``_refresh_npcs`` — the "+", the right-click
    that sheds one, and every rung of the damage ladder all land there.
    """
    (goon,) = _npc_files(window, "Goon")
    window._set_in_scene(SCENE_NPC, goon, True)

    window._apply_npc_condition(goon, "dazed", None)

    assert window._scene_payload()[0]["conditions"] == [{"id": "dazed"}]
    ref = window._scene_entry_for(SCENE_NPC, goon).ref
    assert window._scene_board.card(ref).condition_names() == ["Dazed"]


def test_shedding_a_condition_reaches_the_table_too(window: GMWindow) -> None:
    (goon,) = _npc_files(window, "Goon")
    window._set_in_scene(SCENE_NPC, goon, True)
    window._apply_npc_condition(goon, "dazed", None)

    window._remove_npc_condition(goon, "dazed", None)

    assert "conditions" not in window._scene_payload()[0]


def test_a_damage_rung_reaches_the_table(window: GMWindow) -> None:
    """A failed Toughness save is the condition change a table most wants to see."""
    (goon,) = _npc_files(window, "Goon")
    window._set_in_scene(SCENE_NPC, goon, True)

    window._apply_npc_damage(goon, 1)

    assert window._scene_payload()[0].get("conditions")


def test_coming_back_from_settings_lands_the_scene_preference(window: GMWindow) -> None:
    """Otherwise it waits for a roster, which is frequent in a live session and
    never at a quiet table — so a GM who unticked it and came straight back would
    find the player cards still without their eyes.

    Unticking stops the *automatic* part and does not clear the board: it says
    "from now on I decide", not "throw out the fight in progress". What it changes
    at once is that the eyes appear, so the GM can decide.
    """
    window._show_roster([{"player_id": "p1", "display_name": "Alex", "connected": True}])
    assert [(e.kind, e.source) for e in window._scene] == [(SCENE_PLAYER, "p1")]

    storage.set_gm_scene_auto_players(False)
    window._reload_scene_preference()

    card = window._cards["p1"]
    assert card._scene_eye.isVisibleTo(card)
    assert card.in_scene  # still on the board, and the eye says so
    assert [(e.kind, e.source) for e in window._scene] == [(SCENE_PLAYER, "p1")]

    card.sceneToggled.emit("p1", False)
    assert window._scene == []


def test_turning_the_preference_back_on_seats_the_players_again(window: GMWindow) -> None:
    storage.set_gm_scene_auto_players(False)
    window._show_roster([{"player_id": "p1", "display_name": "Alex", "connected": True}])
    assert window._scene == []

    storage.set_gm_scene_auto_players(True)
    window._reload_scene_preference()

    assert [(e.kind, e.source) for e in window._scene] == [(SCENE_PLAYER, "p1")]


def test_an_unchanged_preference_leaves_the_board_alone(window: GMWindow) -> None:
    """Guarded on the value actually moving, so an ordinary alt-tab costs a
    settings read and nothing else."""
    (goon,) = _npc_files(window, "Goon")
    window._set_in_scene(SCENE_NPC, goon, True)
    window._roll_scene_initiative()
    before = window._npc_state[goon].initiative

    window._reload_scene_preference()

    assert window._npc_state[goon].initiative == before

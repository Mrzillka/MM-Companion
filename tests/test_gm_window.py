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
from PySide6.QtWidgets import QApplication, QLabel, QMessageBox

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
from mm_companion.ui.npc_window import NPCWindow
from mm_companion.ui.roll_history import HIDDEN_MARK
from mm_companion.ui.sections.conditions import addable_conditions
from mm_companion.ui.session_bridge import active_session, set_active_session
from mm_companion.ui.session_dialogs import HostOptions, HostSessionDialog
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
    while not window._code_edit.text() and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.01)
    qapp.processEvents()


def advice_texts(window: GMWindow) -> list[str]:
    return [
        window._advice_layout.itemAt(i).widget().text()
        for i in range(window._advice_layout.count())
    ]


# -- the draggable blocks --------------------------------------------------


def test_the_gm_window_has_the_four_session_blocks(window: GMWindow) -> None:
    assert set(window._canvas.block_keys()) == {"session", "players", "npcs", "rolls"}


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


def test_reset_layout_brings_every_block_back(window: GMWindow) -> None:
    window._canvas.hide_block("npcs")
    window._reset_layout()
    assert window._canvas.is_hidden("npcs") is False
    assert window._block_actions["npcs"].isChecked() is True


# -- the idle window -------------------------------------------------------


def test_the_window_starts_not_hosting(window: GMWindow) -> None:
    assert window.bridge.hosting is False
    assert window._host_button.text() == "Start a session…"
    assert window._code_edit.text() == ""
    assert window._copy_button.isEnabled() is False
    assert "Not hosting" in window._status_label.text()


def test_the_window_publishes_itself_as_the_active_session(window: GMWindow) -> None:
    assert active_session() is window.bridge


def test_renaming_the_session_retitles_the_window(window: GMWindow) -> None:
    window._state.name = "Wednesday Night"
    window._rename_session()
    assert window._state.name == "Wednesday Night"
    assert "Wednesday Night" in window.windowTitle()


def test_a_new_session_replaces_the_state(window: GMWindow) -> None:
    first = window._state.id
    window._new_session()
    assert window._state.id != first


# -- hosting ---------------------------------------------------------------


def test_hosting_shows_a_code_that_matches_the_session(
    qapp: QApplication, window: GMWindow
) -> None:
    start_hosting(qapp, window, canned())

    assert window.bridge.hosting is True
    assert window._host_button.text() == "Stop hosting"
    code = discovery.decode_join_code(window._code_edit.text())
    assert code.host == "192.168.0.5"
    assert code.token == window._state.host_token
    assert window._copy_button.isEnabled() is True


def test_hosting_locks_out_starting_a_new_session(qapp: QApplication, window: GMWindow) -> None:
    start_hosting(qapp, window, canned())
    assert window._host_button.text() == "Stop hosting"
    assert window._new_button.isEnabled() is False


def test_stopping_clears_the_code_and_reopens_the_start_button(
    qapp: QApplication, window: GMWindow
) -> None:
    start_hosting(qapp, window, canned())
    window._host_button.click()

    assert window.bridge.hosting is False
    assert window._code_edit.text() == ""
    assert window._copy_button.isEnabled() is False
    assert window._host_button.text() == "Start a session…"
    assert window._new_button.isEnabled() is True
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
    assert window._host_button.text() == "Start a session…"


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
    assert gm_window_module.theme.TINT_WORSE in window._status_label.styleSheet()


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
    assert gm_window_module.theme.TINT_BETTER in window._status_label.styleSheet()


def test_the_two_outcomes_do_not_look_alike(qapp: QApplication, window: GMWindow) -> None:
    start_hosting(qapp, window, canned())
    lan_status = (window._status_label.text(), window._status_label.styleSheet())
    window._host_button.click()  # stop
    start_hosting(
        qapp,
        window,
        canned(host="203.0.113.7", external_ip="203.0.113.7", mapping=FakeMapping()),
    )
    assert (window._status_label.text(), window._status_label.styleSheet()) != lan_status


def test_advice_from_an_earlier_host_does_not_linger(qapp: QApplication, window: GMWindow) -> None:
    start_hosting(qapp, window, canned(advice=(discovery.ADVICE_CGNAT,)))
    window._host_button.click()
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
    while not window._code_edit.text() and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.01)
    qapp.processEvents()

    assert calls[0]["manual_host"] == "tunnel.example.net"
    assert calls[0]["external_port"] == 12345
    code = discovery.decode_join_code(window._code_edit.text())
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
    names = [card._name_label.text() for card in window._cards.values()]
    assert names == ["GM (you)", "Aria", "Bex — offline"]
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


def test_stopping_clears_the_cards(qapp: QApplication, window: GMWindow) -> None:
    start_hosting(qapp, window, canned())
    window._show_roster(roster({"display_name": "Aria"}))
    window._host_button.click()

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
    assert sheet_window._lock_action.isChecked() is True
    window.close()  # closes the player sheets it opened


def test_copy_puts_the_code_on_the_clipboard(qapp: QApplication, window: GMWindow) -> None:
    start_hosting(qapp, window, canned())
    window._copy_button.click()
    assert QApplication.clipboard().text() == window._code_edit.text()
    assert window._notice.isVisible() or window._notice.text()


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
    assert window._code_edit.text() == ""

    window.show()
    assert active_session() is window.bridge
    assert window._state.id == session_id
    assert window._state.name == "Wednesday"


def test_a_refused_connection_is_shown_to_the_gm(qapp: QApplication, window: GMWindow) -> None:
    window._on_refused({"code": "bad_token", "message": "that join code is not for this session"})
    assert "not for this session" in window._notice.text()


# -- the launcher ----------------------------------------------------------


def test_the_launcher_opens_gm_mode(qapp: QApplication) -> None:
    launcher = StartWindow()
    launcher.show()
    launcher._open_gm_mode()
    try:
        assert isinstance(launcher._gm_window, GMWindow)
        # The launcher stays up behind it — a GM still opens character sheets.
        assert launcher.isHidden() is False
    finally:
        launcher._gm_window.bridge.stop()


def test_reopening_gm_mode_reuses_the_same_window(qapp: QApplication) -> None:
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

    code = discovery.decode_join_code(window._code_edit.text())
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
    assert discovery.decode_join_code(window._code_edit.text()).host == "203.0.113.7"
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
    assert discovery.decode_join_code(window._code_edit.text()).host == "192.168.0.5"


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
    assert discovery.ADVICE_RELAY_UNREACHABLE in window._notice.text()


def test_the_host_dialog_remembers_the_relay_between_launches(qapp: QApplication) -> None:
    dialog = HostSessionDialog()
    dialog._relay_edit.setText("relay.example.net")
    dialog._on_accept()

    assert storage.relay_url() == "relay.example.net"
    # A fresh dialog pre-fills the field from the saved value.
    assert HostSessionDialog()._relay_edit.text() == "relay.example.net"


def test_the_host_dialog_defaults_to_automatic(qapp: QApplication) -> None:
    dialog = HostSessionDialog(session_name="Tuesday")
    opts = dialog.options()
    assert opts.name == "Tuesday"
    assert opts.tunnel == "" and opts.use_relay is True


def test_the_host_dialog_reads_back_a_tunnel_and_arms_no_relay(qapp: QApplication) -> None:
    dialog = HostSessionDialog()
    dialog._via_tunnel.setChecked(True)
    dialog._tunnel_edit.setText("1.2.3.4:5678")
    opts = dialog.options()
    assert opts.tunnel == "1.2.3.4:5678"
    assert opts.use_relay is False  # a typed address is taken at its word


def test_the_host_dialog_only_shows_the_tunnel_field_for_the_tunnel_method(
    qapp: QApplication,
) -> None:
    dialog = HostSessionDialog()
    dialog.show()
    assert dialog._tunnel_edit.isVisibleTo(dialog) is False
    dialog._via_tunnel.setChecked(True)
    assert dialog._tunnel_edit.isVisibleTo(dialog) is True


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

    # The GM applies conditions to itself on its own sheet, and an offline player
    # has no connection for the command to travel down.
    assert window._cards["p0"]._condition_button.isEnabled() is False
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
    assert "Dazed" in window._notice.text()


def test_a_command_to_a_player_who_is_gone_says_so(qapp: QApplication, window: GMWindow) -> None:
    """Nothing is applied optimistically — the card only moves on the snapshot back."""
    start_hosting(qapp, window, canned())
    window._show_roster(roster({"display_name": "Aria"}))

    window._cards["p0"]._choose_condition(a_condition("dazed"))

    assert "not connected" in window._notice.text()
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


def test_a_session_starts_with_no_npcs(window: GMWindow) -> None:
    assert npc_names(window) == []
    assert window._no_npcs.isVisibleTo(window)


def test_saving_a_new_npc_puts_it_in_the_cast(qapp: QApplication, window: GMWindow) -> None:
    window._create_npc()
    (sheet,) = window._npc_windows.values()
    sheet.sheet.character.profile["hero_name"] = "Bank Robber"
    sheet._write(window._npc_dir() / "bank-robber.json")
    qapp.processEvents()

    assert window._state.npc_paths == ["bank-robber.json"]
    assert npc_names(window) == ["Bank Robber"]


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

    window._remove_npc(library.list_saved_characters(window._npc_dir())[0])

    assert window._state.npc_paths == []
    assert npc_names(window) == []
    assert path.is_file()  # still in the bestiary, just not in this session


def test_deleting_an_npc_takes_the_file_with_it(
    window: GMWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = write_npc("Ogre")
    window._register_npc(path)
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)

    window._delete_npc(library.list_saved_characters(window._npc_dir())[0])

    assert window._state.npc_paths == []
    assert npc_names(window) == []
    assert not path.exists()


def test_a_refused_deletion_changes_nothing(
    window: GMWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = write_npc("Ogre")
    window._register_npc(path)
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.No)

    window._delete_npc(library.list_saved_characters(window._npc_dir())[0])

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
    window._register_npc(write_npc("Ogre"))
    summary = library.list_saved_characters(window._npc_dir())[0]

    window._open_npc(summary)
    first = next(iter(window._npc_windows.values()))
    window._open_npc(summary)

    # Reopening must not replace the sheet the way a player's read-only one is:
    # this one is editable, and a replacement would take unsaved work with it.
    assert list(window._npc_windows.values()) == [first]


def test_a_new_session_starts_with_an_empty_cast(window: GMWindow) -> None:
    window._register_npc(write_npc("Ogre"))

    window._new_session()

    assert window._state.npc_paths == []
    assert npc_names(window) == []


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

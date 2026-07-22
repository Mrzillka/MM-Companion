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

from mm_companion.core import storage
from mm_companion.core.character import Character
from mm_companion.core.data_loader import load_game_data
from mm_companion.core.rules import apply_condition
from mm_companion.core.session import discovery
from mm_companion.core.session.protocol import sanitize_snapshot
from mm_companion.ui import gm_window as gm_window_module
from mm_companion.ui import player_card
from mm_companion.ui.gm_window import GMWindow
from mm_companion.ui.sections.conditions import addable_conditions
from mm_companion.ui.session_bridge import active_session, set_active_session
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
    made._port_spin.setValue(0)  # any free port, so tests never collide
    yield made
    made.bridge.stop()


class FakeMapping:
    """Stands in for a UPnP mapping: the bridge releases it on stop."""

    def release(self) -> bool:
        return True


def canned(**kwargs) -> discovery.Reachability:
    defaults = {"host": "192.168.0.5", "port": 47331, "method": discovery.METHOD_LAN}
    return discovery.Reachability(**{**defaults, **kwargs})


def start_hosting(qapp: QApplication, window: GMWindow, reachability, timeout: float = 5.0) -> None:
    """Host, with the network probe replaced by *reachability*, and wait for the code."""
    window.bridge._publish_session = lambda port, **kw: reachability
    window._host_button.click()
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


# -- the idle window -------------------------------------------------------


def test_the_window_starts_not_hosting(window: GMWindow) -> None:
    assert window.bridge.hosting is False
    assert window._host_button.text() == "Start hosting"
    assert window._code_edit.text() == ""
    assert window._copy_button.isEnabled() is False
    assert "Not hosting" in window._status_label.text()


def test_the_window_publishes_itself_as_the_active_session(window: GMWindow) -> None:
    assert active_session() is window.bridge


def test_renaming_the_session_retitles_the_window(window: GMWindow) -> None:
    window._name_edit.setText("Wednesday Night")
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


def test_hosting_locks_the_fields_that_the_code_depends_on(
    qapp: QApplication, window: GMWindow
) -> None:
    start_hosting(qapp, window, canned())
    assert window._port_spin.isEnabled() is False
    assert window._tunnel_edit.isEnabled() is False
    assert window._new_button.isEnabled() is False


def test_stopping_clears_the_code_and_unlocks_the_fields(
    qapp: QApplication, window: GMWindow
) -> None:
    start_hosting(qapp, window, canned())
    window._host_button.click()

    assert window.bridge.hosting is False
    assert window._code_edit.text() == ""
    assert window._copy_button.isEnabled() is False
    assert window._port_spin.isEnabled() is True
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
    window._host_button.click()

    assert window.bridge.hosting is False
    assert "address in use" in warnings[0]
    assert window._host_button.text() == "Start hosting"


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

    window._tunnel_edit.setText("tunnel.example.net:12345")
    window.bridge._publish_session = fake
    window._host_button.click()
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
    window._tunnel_edit.setText("nonsense:port")
    window._host_button.click()

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
    window._name_edit.setText("Wednesday")
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
    assert window._name_edit.text() == "Wednesday"


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
    window._relay_edit.setText(relay_box.base)
    start_hosting(qapp, window, canned(advice=(discovery.ADVICE_CGNAT,)))

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
    window._relay_edit.setText(relay_box.base)
    start_hosting(
        qapp, window, canned(host="203.0.113.7", mapping=FakeMapping(), external_ip="203.0.113.7")
    )

    assert window.bridge.relaying is False
    assert discovery.decode_join_code(window._code_edit.text()).host == "203.0.113.7"
    assert relay_box.server.session_count() == 0


def test_a_tunnel_address_is_taken_at_its_word(
    qapp: QApplication, window: GMWindow, relay_box
) -> None:
    window._relay_edit.setText(relay_box.base)
    window._tunnel_edit.setText("147.185.221.23:12345")
    start_hosting(
        qapp, window, canned(host="147.185.221.23", port=12345, method=discovery.METHOD_MANUAL)
    )

    assert window.bridge.relaying is False
    assert relay_box.server.session_count() == 0


def test_the_relay_is_only_used_when_it_is_asked_for(
    qapp: QApplication, window: GMWindow, relay_box
) -> None:
    window._relay_edit.setText(relay_box.base)
    window._relay_check.setChecked(False)
    start_hosting(qapp, window, canned(advice=(discovery.ADVICE_CGNAT,)))

    assert window.bridge.relaying is False
    assert discovery.decode_join_code(window._code_edit.text()).host == "192.168.0.5"


def test_a_relay_that_cannot_be_reached_leaves_the_session_hosted(
    qapp: QApplication, window: GMWindow
) -> None:
    window._relay_edit.setText("mmrelay+tcp://127.0.0.1:1")
    start_hosting(qapp, window, canned(advice=(discovery.ADVICE_CGNAT,)))

    assert window.bridge.hosting is True
    assert window.bridge.relaying is False
    assert discovery.ADVICE_RELAY_UNREACHABLE in window._notice.text()


def test_the_relay_address_is_remembered_between_launches(
    qapp: QApplication, window: GMWindow
) -> None:
    window._relay_edit.setText("relay.example.net")
    window._save_relay_url()
    assert storage.relay_url() == "relay.example.net"
    assert GMWindow(bind="127.0.0.1")._relay_edit.text() == "relay.example.net"


def test_hosting_locks_the_relay_controls_too(qapp: QApplication, window: GMWindow) -> None:
    start_hosting(qapp, window, canned())
    assert window._relay_edit.isEnabled() is False
    assert window._relay_check.isEnabled() is False


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

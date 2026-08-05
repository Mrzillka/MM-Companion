"""The menu-bar connection read-out.

Driven through a stand-in bridge rather than a real socket: what is under test is
the mapping from a session's state to what a player sees, and a loopback server
would only make that slower to provoke. The one thing that *is* real is the
install — a corner widget nobody put in the menu bar helps no one.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QApplication, QWidget

from mm_companion.core import storage
from mm_companion.core.session import client as session_client
from mm_companion.core.session.discovery import METHOD_RELAY, Reachability
from mm_companion.ui.connection_indicator import (
    ALL_TEXTS,
    MUTED_TOKEN,
    TEXT_CONNECTING,
    TEXT_DROPPED,
    TEXT_HOSTING,
    TEXT_LAN,
    TEXT_OFFLINE,
    TEXT_ONLINE,
    TEXT_PUBLISHING,
    TEXT_RECONNECTING,
    TEXT_REMOVED,
    TEXT_UNREACHABLE,
    ConnectionIndicator,
)


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv(storage.HOME_ENV_VAR, str(tmp_path))
    storage.ensure_workspace()
    return tmp_path


class FakeBridge(QWidget):
    """Every signal and property the indicator reads, and nothing else.

    A ``QWidget`` only so it can carry real signals; the indicator never asks it
    for anything Qt-shaped.
    """

    started = Signal(str, int)
    stopped = Signal()
    published = Signal(object)
    listenerLost = Signal(object)
    connected = Signal(object)
    disconnected = Signal(str)
    kicked = Signal(str)
    connectionStateChanged = Signal(str, object)

    def __init__(self) -> None:
        super().__init__()
        self.hosting = False
        self.connection_state = session_client.STATE_OFFLINE
        self.reachability = None
        self.client = None


class FakeClient:
    session_name = "Wednesday"
    display_name = "Aria"
    latency_ms = None


@pytest.fixture
def pair(qapp: QApplication):
    bridge = FakeBridge()
    indicator = ConnectionIndicator()
    indicator.set_bridge(bridge)
    return indicator, bridge


# --------------------------------------------------------------------------
# What each state looks like
# --------------------------------------------------------------------------


def test_with_no_session_it_says_offline_and_recedes(qapp: QApplication) -> None:
    indicator = ConnectionIndicator()

    assert indicator.text == TEXT_OFFLINE
    assert indicator.token == MUTED_TOKEN


def test_being_connected_reads_green(pair) -> None:
    indicator, bridge = pair
    bridge.client = FakeClient()
    bridge.connection_state = session_client.STATE_ONLINE
    bridge.connectionStateChanged.emit(session_client.STATE_ONLINE, {})

    assert indicator.text == TEXT_ONLINE
    assert indicator.token == "tint.better"
    assert "Wednesday" in indicator.toolTip()


def test_the_tooltip_carries_the_round_trip(pair) -> None:
    indicator, bridge = pair
    client = FakeClient()
    client.latency_ms = 42.4
    bridge.client = client
    bridge.connection_state = session_client.STATE_ONLINE
    bridge.connectionStateChanged.emit(session_client.STATE_ONLINE, {})

    assert "42 ms" in indicator.toolTip()


def test_connecting_reads_amber(pair) -> None:
    indicator, bridge = pair
    bridge.connection_state = session_client.STATE_CONNECTING
    bridge.connectionStateChanged.emit(session_client.STATE_CONNECTING, {})

    assert indicator.text == TEXT_CONNECTING
    assert indicator.token == "tint.warning"


def test_reconnecting_reads_amber_and_says_how_it_is_going(pair) -> None:
    """A blip is not a red state — nothing has gone wrong yet that will stay wrong."""
    indicator, bridge = pair
    bridge.connection_state = session_client.STATE_RECONNECTING
    bridge.connectionStateChanged.emit(
        session_client.STATE_RECONNECTING,
        {"reason": "closed", "attempt": 2, "retry_in": 5.0},
    )

    assert indicator.text == TEXT_RECONNECTING
    assert indicator.token == "tint.warning"
    assert "Attempt 2" in indicator.toolTip()
    assert "5s" in indicator.toolTip()


def test_a_session_that_ended_reads_red_and_says_why(pair) -> None:
    indicator, bridge = pair
    bridge.disconnected.emit("timeout")

    assert indicator.text == TEXT_DROPPED
    assert indicator.token == "tint.worse"
    assert "timeout" in indicator.toolTip()


def test_being_removed_says_so_rather_than_disconnected(pair) -> None:
    indicator, bridge = pair
    bridge.kicked.emit("told to go")

    assert indicator.text == TEXT_REMOVED
    assert indicator.token == "tint.worse"


def test_reconnecting_clears_a_previous_drop(pair) -> None:
    """A card reading "Disconnected" must not linger behind a live connection."""
    indicator, bridge = pair
    bridge.disconnected.emit("closed")
    assert indicator.text == TEXT_DROPPED

    bridge.client = FakeClient()
    bridge.connection_state = session_client.STATE_ONLINE
    bridge.connected.emit({})

    assert indicator.text == TEXT_ONLINE
    assert indicator.token == "tint.better"


# --------------------------------------------------------------------------
# Hosting
# --------------------------------------------------------------------------


def test_hosting_before_publishing_is_not_yet_green(pair) -> None:
    indicator, bridge = pair
    bridge.hosting = True
    bridge.started.emit("127.0.0.1", 47331)

    assert indicator.text == TEXT_PUBLISHING
    assert indicator.token == "tint.warning"


def test_a_published_session_reads_green_with_its_own_advice(pair) -> None:
    indicator, bridge = pair
    bridge.hosting = True
    bridge.reachability = Reachability(
        host="relay.example",
        port=47332,
        method=METHOD_RELAY,
        advice=("Players anywhere can join with this code.",),
    )
    bridge.published.emit(bridge.reachability)

    assert indicator.text == TEXT_HOSTING
    assert indicator.token == "tint.better"
    assert "Players anywhere can join" in indicator.toolTip()


def test_a_lan_only_session_says_so(pair) -> None:
    indicator, bridge = pair
    bridge.hosting = True
    bridge.reachability = Reachability(host="192.168.1.5", port=47331, advice=("LAN only.",))
    bridge.published.emit(bridge.reachability)

    assert indicator.text == TEXT_LAN
    assert indicator.token == "accent"


def test_every_state_fits_the_width_reserved_for_it(qapp: QApplication) -> None:
    """The clip that made the states worth reading the ones you could not read.

    A menu bar lays a corner widget out when the *bar* resizes, so a label growing
    from "Offline" to "Reconnecting…" gets no more room and is simply cut off.
    The widget reserves the widest state up front; this is the guard on that, and
    on nobody later adding a state that does not fit.
    """
    indicator = ConnectionIndicator()
    metrics = indicator._label.fontMetrics()
    reserved = indicator._label.width()

    for text in ALL_TEXTS:
        assert metrics.horizontalAdvance(text) <= reserved, f"{text!r} would be clipped"


def test_hosting_nobody_can_reach_is_flagged(pair) -> None:
    """The failure that used to be silent: hosting fine, and unjoinable."""
    indicator, bridge = pair
    bridge.hosting = True
    bridge.reachability = Reachability(host="relay.example", port=47332, method=METHOD_RELAY)
    bridge.published.emit(bridge.reachability)
    assert indicator.text == TEXT_HOSTING

    bridge.listenerLost.emit({"session_id": "s1"})

    assert indicator.text == TEXT_UNREACHABLE
    assert indicator.token == "tint.warning"


def test_hosting_again_clears_a_lost_listener(pair) -> None:
    indicator, bridge = pair
    bridge.hosting = True
    bridge.listenerLost.emit({"session_id": "s1"})
    assert indicator.text == TEXT_UNREACHABLE

    bridge.reachability = Reachability(host="relay.example", port=47332, method=METHOD_RELAY)
    bridge.started.emit("127.0.0.1", 47331)

    assert indicator.text == TEXT_HOSTING


# --------------------------------------------------------------------------
# Where it lives
# --------------------------------------------------------------------------


def test_dropping_the_bridge_goes_quiet(pair) -> None:
    """A stale bridge must not keep repainting a window that has left its session."""
    indicator, bridge = pair
    bridge.client = FakeClient()
    bridge.connection_state = session_client.STATE_ONLINE
    bridge.connected.emit({})

    indicator.set_bridge(None)

    assert indicator.text == TEXT_OFFLINE
    bridge.disconnected.emit("closed")  # no longer ours to hear
    assert indicator.text == TEXT_OFFLINE


def _corner_indicator(window) -> ConnectionIndicator | None:
    """The indicator in *window*'s menu-bar corner, if it has one."""
    corner = window.menuBar().cornerWidget(Qt.Corner.TopRightCorner)
    return corner if isinstance(corner, ConnectionIndicator) else None


def test_the_sheet_installs_one_in_its_menu_bar_corner(qapp: QApplication) -> None:
    from mm_companion.ui.main_window import MainWindow

    window = MainWindow()

    indicator = _corner_indicator(window)
    assert isinstance(indicator, ConnectionIndicator)
    # Also stashed on the window, which is where a later set_bridge looks.
    assert indicator is window.connection_indicator
    window.close()


def test_a_sheet_that_can_never_join_gets_no_indicator(qapp: QApplication) -> None:
    """An NPC sheet and a GM's read-only view of a player are the GM's own windows.

    Neither ever joins anything, so an indicator on them would read "Offline" for
    the whole of a perfectly healthy session the GM is hosting — the exact false
    reading this widget exists to prevent.
    """
    from mm_companion.ui.main_window import MainWindow
    from mm_companion.ui.npc_window import NPCWindow

    npc = NPCWindow()
    gm_view = MainWindow(gm_view=True)

    assert npc.menuBar().cornerWidget(Qt.Corner.TopRightCorner) is None
    assert gm_view.menuBar().cornerWidget(Qt.Corner.TopRightCorner) is None
    npc.close()
    gm_view.close()


def test_the_gm_window_installs_one_following_its_own_bridge(qapp: QApplication) -> None:
    from mm_companion.ui.gm_window import GMWindow

    window = GMWindow()

    indicator = _corner_indicator(window)
    assert isinstance(indicator, ConnectionIndicator)
    # Already following the window's bridge — a GM window owns one from birth,
    # unlike a sheet, which only learns its session at the join dialog.
    assert indicator._bridge is window.bridge
    window.close()

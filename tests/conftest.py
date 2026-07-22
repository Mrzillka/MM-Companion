"""Shared pytest fixtures.

GUI tests build heavyweight top-level widgets (``CharacterSheet``,
``MainWindow``, …) and, on a real display, many ``.show()`` them. Left
undestroyed, every one of those windows survives for the whole pytest process;
the growing pile makes each later test's event processing and window creation
progressively slower, turning a ~90s suite into a 20-minute crawl (and masking
as fast only under the cheap ``offscreen`` platform). The autouse teardown below
closes and deletes any leftover top-level widgets after every test so windows
never accumulate across the session.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass

import pytest
from PySide6.QtWidgets import QApplication

from mm_companion.core.session.relay import RELAY_SCHEME_PLAIN
from mm_companion.relay import RelayServer
from mm_companion.ui.sections.powers import PowersSection


@dataclass
class RelayBox:
    """A real relay on loopback, for tests about hosting through one."""

    server: RelayServer
    base: str


@pytest.fixture
def relay_box():
    """A plaintext relay on an ephemeral loopback port, running in a thread.

    Plaintext because a TLS relay needs a certificate; the encrypted path is
    covered end to end in ``test_session_relay.py``.
    """
    server = RelayServer("127.0.0.1", 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.address
    yield RelayBox(server, f"{RELAY_SCHEME_PLAIN}://{host}:{port}")
    server.stop()
    thread.join(timeout=5.0)


@pytest.fixture(autouse=True)
def _instant_power_card_transitions():
    """Switch a power card between its live and off looks instantly, not over a timer.

    A card eases into its switched-off look, which means the state a test asserts on
    right after a toggle is only the *first frame* of that transition — and no frame
    ever runs, because a test has no event loop turning. Zeroing the duration makes
    every card land on its resting look synchronously. A test that is specifically
    about the animation restores a real duration itself.
    """
    original = PowersSection.TRANSITION_MS
    PowersSection.TRANSITION_MS = 0
    yield
    PowersSection.TRANSITION_MS = original


@pytest.fixture(autouse=True)
def _close_top_level_widgets():
    yield
    app = QApplication.instance()
    if app is None:
        return
    for widget in list(app.topLevelWidgets()):
        # hide()+deleteLater(), not close(): close() runs closeEvent, and a dirty
        # MainWindow's closeEvent pops a modal Save/Discard/Cancel box that would
        # block the teardown forever. Deleting the widget frees its native window
        # without any closeEvent.
        widget.hide()
        widget.deleteLater()
    app.processEvents()

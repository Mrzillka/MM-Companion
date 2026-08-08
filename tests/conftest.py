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
from PySide6.QtCore import QEvent
from PySide6.QtWidgets import QApplication

from mm_companion.core import storage
from mm_companion.core.session.relay import RELAY_SCHEME_PLAIN
from mm_companion.relay import RelayServer
from mm_companion.ui import theme
from mm_companion.ui.compact import CompactController
from mm_companion.ui.sections.equipment import EquipmentSection
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
def _isolated_workspace(tmp_path, monkeypatch):
    """Give every test a throwaway workspace, and a clean theme cache around it.

    Otherwise a test reads — and writes — the developer's real workspace. Both
    directions bite: ``library.save_character`` leaves files in their actual
    ``characters/`` dir, and a preset they happen to have chosen decides what
    colour the sheet paints a penalty, so an assertion about a tint passes or
    fails depending on who runs it. Neither shows up on CI, where the workspace
    is always empty, which is exactly what makes it worth pinning here.

    A test file wanting its own workspace still sets the variable itself; a
    module-level autouse fixture runs after this one and wins. This is the floor.
    """
    monkeypatch.setenv(storage.HOME_ENV_VAR, str(tmp_path / "workspace"))
    # The active preset is cached, so moving the workspace has to invalidate it on
    # the way in and on the way back out.
    theme.reset()
    yield
    theme.reset()


@pytest.fixture(autouse=True)
def _instant_power_card_transitions():
    """Switch a card between its live and off looks instantly, not over a timer.

    A card eases into its switched-off look, which means the state a test asserts on
    right after a toggle is only the *first frame* of that transition — and no frame
    ever runs, because a test has no event loop turning. Zeroing the duration makes
    every card land on its resting look synchronously. A test that is specifically
    about the animation restores a real duration itself.

    Both card boards, since they animate the same way: a power switching off and an
    item being stowed run the identical easing.
    """
    originals = (PowersSection.TRANSITION_MS, EquipmentSection.TRANSITION_MS)
    PowersSection.TRANSITION_MS = 0
    EquipmentSection.TRANSITION_MS = 0
    yield
    PowersSection.TRANSITION_MS, EquipmentSection.TRANSITION_MS = originals


@pytest.fixture(autouse=True)
def _instant_compact_transitions():
    """Let a window snap between full and compact rather than easing over a timer.

    The sibling of :func:`_instant_power_card_transitions`, and it exists for the
    same reason: the geometry a test reads right after a toggle would otherwise be
    the animation's first frame, and no frame ever runs without an event loop. A
    test about the animation itself restores a real duration.
    """
    original = CompactController.ANIMATION_MS
    CompactController.ANIMATION_MS = 0
    yield
    CompactController.ANIMATION_MS = original


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
    # processEvents() does not run deferred deletions — Qt holds those until the
    # event loop that posted them unwinds, and a test never starts one. Without
    # this the widgets above are only *scheduled* to die and in fact pile up all
    # session, which is the very thing this fixture exists to prevent.
    app.sendPostedEvents(None, QEvent.Type.DeferredDelete)

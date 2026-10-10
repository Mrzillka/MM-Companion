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

import shutil
import subprocess
import threading
from dataclasses import dataclass

import pytest
from PySide6.QtCore import QEvent, Qt
from PySide6.QtWidgets import QApplication, QToolTip

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
def _no_roll_notifications():
    """Keep roll notifications off the screen of whoever runs the suite.

    Every roll a test makes would otherwise pop up a real always-on-top window in a
    corner of the developer's desktop. A test about the notifications themselves
    sets ``toasts.SUPPRESSED = False`` for its own duration.
    """
    from mm_companion.ui import toasts

    original = toasts.SUPPRESSED
    toasts.SUPPRESSED = True
    yield
    toasts.SUPPRESSED = original


@pytest.fixture(autouse=True)
def _no_spare_npc_sheets():
    """Keep a GM window from building an NPC sheet ahead of time behind a test's back.

    The suite opens hundreds of GM windows, and each would otherwise build a whole
    sheet off a timer — slow, and a window nobody asked for in every test that
    happens to wait on the event loop. The tests about the spare call
    ``_prime_spare_npc`` themselves, or set a delay for their own duration.
    """
    from mm_companion.ui.gm_window import GMWindow

    original = GMWindow.SPARE_NPC_DELAY_MS
    GMWindow.SPARE_NPC_DELAY_MS = None
    yield
    GMWindow.SPARE_NPC_DELAY_MS = original


@pytest.fixture(autouse=True)
def _reset_quick_npc_memory():
    """Start every test from a fresh run's Quick NPC dialog: PL and preset unremembered."""
    from mm_companion.ui import npc_quick_dialog

    npc_quick_dialog.reset_memory()
    yield
    npc_quick_dialog.reset_memory()


@pytest.fixture(autouse=True)
def _close_top_level_widgets():
    yield
    app = QApplication.instance()
    if app is None:
        return
    # Take any tip down first, and then leave its window alone below. Qt keeps
    # *one* internal label for every tooltip in the process and hands the same one
    # out again; deleting it here left the next `QToolTip.showText` writing into
    # freed memory, which is an access violation rather than an exception — the
    # whole worker dies and the test that happens to be running is blamed for it.
    QToolTip.hideText()
    for widget in list(app.topLevelWidgets()):
        if widget.windowType() == Qt.WindowType.ToolTip:
            continue  # Qt's, not the test's (see above)
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


@pytest.fixture(scope="session")
def tls_cert(tmp_path_factory):
    """A throwaway self-signed certificate for ``localhost``.

    Generated rather than checked in — a private key in the repository is a
    liability, and a checked-in certificate expires. Skips where openssl is not
    installed; CI has it.
    """
    openssl = shutil.which("openssl")
    if openssl is None:  # pragma: no cover - depends on the machine
        pytest.skip("openssl is not installed")
    directory = tmp_path_factory.mktemp("relay-tls")
    cert, key = directory / "cert.pem", directory / "key.pem"
    result = subprocess.run(
        [
            openssl,
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-keyout",
            str(key),
            "-out",
            str(cert),
            "-days",
            "3650",
            "-subj",
            "/CN=localhost",
            "-addext",
            "subjectAltName=DNS:localhost,IP:127.0.0.1",
        ],
        capture_output=True,
    )
    if result.returncode != 0:  # pragma: no cover - depends on the machine
        pytest.skip(f"openssl could not make a certificate: {result.stderr.decode()[:200]}")
    return cert, key

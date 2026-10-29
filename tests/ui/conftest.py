"""Fixtures for the tests that build widgets.

GUI tests build heavyweight top-level widgets (``CharacterSheet``,
``MainWindow``, …) and many ``.show()`` them. Left undestroyed, every one of
those windows survives for the whole pytest process; the growing pile makes each
later test's event processing and window creation progressively slower, turning
a ~90s suite into a 20-minute crawl. The autouse teardown below closes and
deletes any leftover top-level widgets after every test so windows never
accumulate across the session.
"""

from __future__ import annotations

import pytest
from PySide6.QtCore import QEvent, Qt
from PySide6.QtWidgets import QApplication, QToolTip

from mm_companion.ui import theme
from mm_companion.ui.compact import CompactController
from mm_companion.ui.sections.equipment import EquipmentSection
from mm_companion.ui.sections.powers import PowersSection


@pytest.fixture(autouse=True)
def _fresh_theme():
    """Drop the cached theme preset around every test.

    The active preset is cached, and the root conftest moves the workspace it was
    read from for every test, so the cache has to be invalidated on the way in and
    on the way back out.
    """
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
    """Keep roll notifications from popping up a window for every roll.

    Every roll a test makes would otherwise open a real always-on-top window — on
    a developer's desktop if they run on a real platform. A test about the
    notifications themselves sets ``toasts.SUPPRESSED = False`` for its own duration.
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


@pytest.fixture
def instant_die(monkeypatch):
    """A roll that lands at once, on an 11, so a test can read the roller straight after."""
    from mm_companion.ui import dice_roller

    monkeypatch.setattr(dice_roller, "ROLL_DURATION_MS", 0)
    monkeypatch.setattr(dice_roller, "roll_d20", lambda *a, **k: 11)


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

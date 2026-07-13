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

import pytest
from PySide6.QtWidgets import QApplication


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

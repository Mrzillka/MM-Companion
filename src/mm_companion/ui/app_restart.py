"""Relaunching the app in place, for changes that only apply at startup.

Two settings are read once and then baked into what has already been built: the
enabled mod stack (:mod:`mm_companion.core.mods`, whose Python hooks fire before
any game data is parsed) and, for anything a widget styled in its constructor,
the active theme. Both offer the user a restart rather than pretending a live
switch reached everywhere.
"""

from __future__ import annotations

import sys

from PySide6.QtCore import QProcess
from PySide6.QtWidgets import QApplication


def restart_app() -> None:
    """Relaunch the app; abort if a window won't close.

    Closes every open window first so character sheets run their unsaved-change
    guards; if any window refuses (the user cancelled a Save) the relaunch is
    aborted and the app keeps running — the change then applies at the next
    launch instead of costing the user their edits. Otherwise a fresh process is
    spawned via ``python -m mm_companion`` (the launch path that works however
    the app was started) and this one quits.
    """
    app = QApplication.instance()
    if app is None:
        return
    app.closeAllWindows()
    if any(w.isVisible() and w.isWindow() for w in app.topLevelWidgets()):
        return  # a window refused to close — stay running, changes apply next launch
    QProcess.startDetached(sys.executable, ["-m", "mm_companion"])
    app.quit()

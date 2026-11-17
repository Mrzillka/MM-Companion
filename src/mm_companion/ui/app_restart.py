"""Relaunching the app in place, for changes that only apply at startup.

Two settings are read once and then baked into what has already been built: the
enabled mod stack (:mod:`mm_companion.core.mods`, whose Python hooks fire before
any game data is parsed) and, for anything a widget styled in its constructor,
the active theme. Both offer the user a restart rather than pretending a live
switch reached everywhere.
"""

from __future__ import annotations

import sys
from collections.abc import Callable

from PySide6.QtCore import QProcess, QTimer
from PySide6.QtWidgets import QApplication, QMessageBox, QWidget

from mm_companion.core import diagnostics

#: Set by the in-app update once it is closing windows: a restart a window asks for
#: on its way out (Settings, after a theme change) would start a second copy of the
#: app that holds the files the installer is about to replace.
_suppressed = False
_suppression_epoch = 0


def suppress_restart(suppressed: bool = True) -> None:
    """Temporarily block restarts and invalidate requests from before an update."""
    global _suppressed, _suppression_epoch
    _suppressed = suppressed
    if suppressed:
        _suppression_epoch += 1


def restart_allowed() -> bool:
    return not _suppressed


def defer_restart(callback: Callable[[], None]) -> None:
    """Queue a restart only if no intervening update has invalidated it."""
    if _suppressed:
        return
    epoch = _suppression_epoch

    def run() -> None:
        if not _suppressed and epoch == _suppression_epoch:
            callback()

    QTimer.singleShot(0, run)


def _closing_order(window: QWidget) -> int:
    """Build editors first, their character sheets next, then other windows.

    GM Mode closes the NPC sheets it opened as part of its own close and does not
    pass on a refusal, so reaching an unsaved NPC sheet through GM Mode first would
    ask about it twice. Asked directly first, each sheet asks once.
    """
    from mm_companion.ui.main_window import MainWindow

    if callable(getattr(window, "request_close", None)):
        return 0
    return 1 if isinstance(window, MainWindow) else 2


def close_windows(*, keep: tuple[QWidget | None, ...] = ()) -> bool:
    """Ask independent windows to close, letting owners handle their own children."""
    windows = [
        window
        for window in QApplication.topLevelWidgets()
        if window not in keep and window.parentWidget() is None
    ]
    for window in sorted(windows, key=_closing_order):
        if not window.isVisible():
            continue
        request_close = getattr(window, "request_close", None)
        if callable(request_close):
            request_close()
        else:
            window.close()
        if window.isVisible():
            return False
    return True


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
    if app is None or _suppressed:
        return
    from mm_companion.ui.start_window import StartWindow

    launcher = next((w for w in app.topLevelWidgets() if isinstance(w, StartWindow)), None)
    quit_on_close = app.quitOnLastWindowClosed()
    app.setQuitOnLastWindowClosed(False)
    suppress_restart()
    try:
        if not close_windows():
            return  # a window refused — changes apply next launch
        args = [] if getattr(sys, "frozen", False) else ["-m", "mm_companion"]
        result = QProcess.startDetached(sys.executable, args)
        started = result[0] if isinstance(result, tuple) else bool(result)
        if not started:
            diagnostics.log_message("Could not start the replacement process for a restart")
            launcher = launcher or StartWindow()
            app._restart_recovery_window = launcher
            launcher.show()
            QMessageBox.warning(
                launcher,
                "Could not restart MM-Companion",
                "The replacement process could not start. Restart MM-Companion manually "
                "when you are ready; your saved changes are kept.",
            )
            return
        app.quit()
    finally:
        suppress_restart(False)
        app.setQuitOnLastWindowClosed(quit_on_close)

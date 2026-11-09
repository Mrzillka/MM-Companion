"""Launch the MM-Companion desktop application.

Run with ``python -m mm_companion`` (or the ``mm-companion`` console script).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from types import ModuleType

from PySide6.QtCore import Qt, QTimer, QtMsgType, qInstallMessageHandler
from PySide6.QtGui import QCursor, QGuiApplication
from PySide6.QtWidgets import QApplication, QWidget

from mm_companion import __version__
from mm_companion.core import diagnostics
from mm_companion.core.mods import initialize_mods
from mm_companion.core.storage import ensure_workspace, get_workspace
from mm_companion.ui import gc_guard, loading, theme
from mm_companion.ui.app_icon import app_icon


def _make_splash() -> QWidget:
    """The card shown while the app starts, centred on the screen under the pointer.

    A plain frameless window rather than a ``QSplashScreen``: on Windows that one's
    ``show()`` waits for the window to report itself exposed, and gives up only after
    a full second — a second added to every start. A loading bar is laid over it.
    """
    splash = QWidget(None, Qt.WindowType.SplashScreen | Qt.WindowType.FramelessWindowHint)
    splash.setWindowTitle("MM-Companion")
    splash.resize(int(theme.metric("splash.width")), int(theme.metric("splash.height")))
    # In the splash's own colours, down to the loading screen laid over it — a sheet
    # rule, since the application's sheet would win over a palette.
    splash.setStyleSheet(
        f"QWidget {{ background: {theme.color('splash.background')};"
        f" color: {theme.color('splash.text')}; }}"
    )
    screen = QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
    if screen is not None:
        frame = splash.frameGeometry()
        frame.moveCenter(screen.availableGeometry().center())
        splash.move(frame.topLeft())
    return splash


def _import_interface() -> ModuleType:
    """Import the interface a module at a time, a step of the bar each; the launcher's.

    Most of a start is spent here — the sheet's blocks, the rules under them, the GM
    window — and no window, not even the splash, can exist before the QApplication
    does; so the splash goes up first and these come after it. Written out as import
    statements, not a list of names for ``importlib``: this is the root of the
    interface's import graph, and PyInstaller finds what to bundle by reading them.
    """
    steps = 5
    loading.expect(steps)
    import mm_companion.core.rules  # noqa: F401

    loading.step()
    import mm_companion.ui.character_sheet  # noqa: F401

    loading.step()
    import mm_companion.ui.main_window  # noqa: F401

    loading.step()
    import mm_companion.ui.gm_window  # noqa: F401

    loading.step()
    from mm_companion.ui import start_window

    loading.step()
    return start_window


def _apply_portable_home() -> None:
    """Point the workspace next to the executable for a portable install.

    Only meaningful in a frozen (PyInstaller) build: if a ``portable.flag`` file
    sits beside the real executable, the workspace is redirected to a local
    ``data`` folder instead of ``%APPDATA%\\MM-Companion`` — reusing the existing
    ``MM_COMPANION_HOME`` support so no ``core`` change is needed. An explicit
    ``MM_COMPANION_HOME`` in the environment always wins and is left untouched.
    """
    if not getattr(sys, "frozen", False) or "MM_COMPANION_HOME" in os.environ:
        return
    exe_dir = Path(sys.executable).resolve().parent
    if (exe_dir / "portable.flag").exists():
        os.environ["MM_COMPANION_HOME"] = str(exe_dir / "data")


def _route_qt_messages() -> None:
    """Send Qt's own warnings to the errors log as well as the console.

    Qt reports the faults that come before a crash — a widget painted by two
    painters, a recursive repaint, a layout asked to do the impossible — as
    warnings on stderr, which a windowed build does not have. Debug and info
    chatter stays out; repeats are counted rather than written again.
    """

    def handler(kind: QtMsgType, context, message: str) -> None:  # noqa: ANN001
        if kind in (QtMsgType.QtWarningMsg, QtMsgType.QtCriticalMsg, QtMsgType.QtFatalMsg):
            diagnostics.log_message(
                f"Qt {kind.name.removeprefix('Qt').removesuffix('Msg')}: {message}"
            )
        if sys.stderr is not None:
            print(message, file=sys.stderr)

    qInstallMessageHandler(handler)


def main() -> int:
    # Redirect the workspace for a portable install before anything reads it.
    _apply_portable_home()
    # Before anything else can fail: a windowed build has no console, so this is
    # the only record an error or a crash will leave (see core.diagnostics).
    diagnostics.install(get_workspace().logs_dir, version=__version__)
    _route_qt_messages()

    app = QApplication(sys.argv)
    # Python's cycle collector would otherwise run on whichever thread is allocating,
    # and destroy a Qt object there — fatal off the GUI thread (see ui/gc_guard).
    gc_guard.install(app)
    # The application icon is Qt's default for every top-level window, so no
    # window needs to set it individually.
    app.setWindowIcon(app_icon())
    # On Linux, the name of the menu entry the installer writes
    # (installer/linux/install.sh) — how a Wayland desktop matches the window to
    # it, and so to its icon. Ignored elsewhere.
    app.setDesktopFileName("mm-companion")
    # Install the theme's stylesheet before the first widget exists, so nothing —
    # not even the splash — is ever painted in the wrong look and re-dressed.
    theme.apply(app)

    # A splash with a loading bar, up before the slow part of a start: importing the
    # interface, first-run setup (the APPDATA workspace, default settings, character
    # directories) and building the launcher. Nothing else exists yet, so this one
    # turn of the event loop is safe — it gets the splash on screen, after which the
    # bar paints itself (see ui/loading).
    splash = _make_splash()
    splash.show()
    app.processEvents()

    with loading.loading(splash, "Starting MM-Companion…", at_once=True):
        with loading.part(0.75):
            start_window = _import_interface()
        loading.say("Preparing workspace…")
        with loading.part(0.3):
            ensure_workspace()
            # Import trusted enabled mods' Python modules so their register_* hooks
            # fire before any game data is parsed or rendered.
            initialize_mods()
        loading.say("Opening the launcher…")
        window = start_window.StartWindow()
    window.show()
    splash.close()
    # Once the event loop runs, so a report of a failed update opens over a
    # launcher that has already painted.
    QTimer.singleShot(0, window.run_startup_checks)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())

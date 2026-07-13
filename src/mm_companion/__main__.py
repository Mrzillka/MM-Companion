"""Launch the MM-Companion desktop application.

Run with ``python -m mm_companion`` (or the ``mm-companion`` console script).
"""

from __future__ import annotations

import sys

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPixmap
from PySide6.QtWidgets import QApplication, QSplashScreen

from mm_companion.core.mods import initialize_mods
from mm_companion.core.storage import ensure_workspace
from mm_companion.ui.app_icon import app_icon
from mm_companion.ui.start_window import StartWindow


def _make_splash() -> QSplashScreen:
    """A minimal loading screen shown while the workspace is prepared."""
    pixmap = QPixmap(420, 220)
    pixmap.fill(QColor("#2b2b3a"))
    splash = QSplashScreen(pixmap)
    splash.showMessage(
        "MM-Companion\nPreparing workspace…",
        Qt.AlignmentFlag.AlignCenter,
        QColor("white"),
    )
    return splash


def main() -> int:
    app = QApplication(sys.argv)
    # The application icon is Qt's default for every top-level window, so no
    # window needs to set it individually.
    app.setWindowIcon(app_icon())

    # Hide first-run setup (creating the APPDATA workspace, default settings,
    # character directories) behind a loading screen.
    splash = _make_splash()
    splash.show()
    app.processEvents()

    ensure_workspace()
    # Import trusted enabled mods' Python modules so their register_* hooks fire
    # before any game data is parsed or rendered.
    initialize_mods()

    window = StartWindow()
    window.show()
    splash.finish(window)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())

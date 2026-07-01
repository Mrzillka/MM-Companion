"""Launch the MM-Companion desktop application.

Run with ``python -m mm_companion`` (or the ``mm-companion`` console script).
"""

from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from mm_companion.ui.main_window import MainWindow


def main() -> int:
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())

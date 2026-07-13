"""Developer entry point: launch straight into the Power Constructor.

Skips the launcher and opens the standalone
:class:`~mm_companion.ui.power_constructor.PowerConstructorWindow`, so the
brick-builder can be started straight from an IDE's Run button (or
``python run_power_constructor.py``) while iterating on it.
"""

from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from mm_companion.ui.app_icon import app_icon
from mm_companion.ui.power_constructor import PowerConstructorWindow


def main() -> int:
    app = QApplication(sys.argv)
    app.setWindowIcon(app_icon())
    window = PowerConstructorWindow()  # loads game data itself
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())

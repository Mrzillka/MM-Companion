"""Developer entry point: launch straight into the character sheet.

Skips the launcher and opens an editable :class:`~mm_companion.ui.main_window.MainWindow`
on a fresh character, so the sheet UI can be started straight from an IDE's Run
button (or ``python run_character_sheet.py``) while iterating on it.
"""

from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from mm_companion.core.storage import ensure_workspace
from mm_companion.ui.app_icon import app_icon
from mm_companion.ui.main_window import MainWindow


def main() -> int:
    app = QApplication(sys.argv)
    app.setWindowIcon(app_icon())
    ensure_workspace()  # Save/Open target the per-user workspace.
    window = MainWindow(locked=False)  # unlocked: editable for iterating on the sheet
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())

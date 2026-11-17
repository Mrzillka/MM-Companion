"""Character-file errors reported consistently by each window that opens a sheet."""

from pathlib import Path

from PySide6.QtWidgets import QMessageBox, QWidget

from mm_companion.core import library
from mm_companion.core.character import Character
from mm_companion.core.diagnostics import log_exception


def read_character(parent: QWidget, path: Path) -> Character | None:
    """Read a character, or keep the current window and explain why opening failed."""
    try:
        return library.load_character(path)
    except (OSError, ValueError) as exc:
        log_exception(f"Opening character {path}")
        QMessageBox.warning(parent, "Could not open character", f"{path}\n\n{exc}")
        return None

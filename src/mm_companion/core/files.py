"""Whole-file writes that preserve the last saved copy when a write fails."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path


def atomic_write_text(path: Path, text: str) -> None:
    """Write UTF-8 text beside *path*, then replace it only after closing the file.

    Each writer owns a unique temporary file, so concurrent writers cannot truncate
    or rename each other's unfinished output. The caller owns directory creation.
    """
    path = Path(path)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=".mm-companion-",
            suffix=".tmp",
            delete=False,
        ) as stream:
            temporary = Path(stream.name)
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass  # Do not hide the original write or replacement error.

"""The application window icon, bundled as package data.

The ``.ico`` lives under ``ui/assets/`` (a UI asset, not OGL game content) and is
loaded via :mod:`importlib.resources` so it resolves when installed as a package.
Setting it on the :class:`QApplication` makes Qt use it as the default icon for
every top-level window, so individual windows need not set it themselves.
"""

from __future__ import annotations

from functools import lru_cache
from importlib.resources import as_file, files

from PySide6.QtGui import QIcon

RESOURCE_PACKAGE = "mm_companion.ui"
RESOURCE_NAME = "assets/mm.ico"


@lru_cache(maxsize=1)
def app_icon() -> QIcon:
    """Load the application icon (cached — one load per process)."""
    resource = files(RESOURCE_PACKAGE).joinpath(RESOURCE_NAME)
    with as_file(resource) as path:
        return QIcon(str(path))

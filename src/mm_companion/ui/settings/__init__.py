"""The Settings window and the pages it shows.

:class:`~mm_companion.ui.settings.window.SettingsWindow` is the only name the rest
of the app needs, plus a page's ``title`` when a caller wants the window to open
on it. Inside: :mod:`~mm_companion.ui.settings.theme_page`, built on the generated
token form in :mod:`~mm_companion.ui.settings.token_editor`, and
:mod:`~mm_companion.ui.settings.gm_page`, the GM window's own settings.
"""

from __future__ import annotations

from mm_companion.ui.settings.gm_page import GMPage
from mm_companion.ui.settings.window import PAGES, SettingsWindow

__all__ = ["PAGES", "GMPage", "SettingsWindow"]

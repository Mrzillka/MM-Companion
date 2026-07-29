"""The Settings window and the pages it shows.

:class:`~mm_companion.ui.settings.window.SettingsWindow` is the only name the rest
of the app needs. Inside, :mod:`~mm_companion.ui.settings.theme_page` is the one
page there is so far, built on the generated token form in
:mod:`~mm_companion.ui.settings.token_editor`.
"""

from __future__ import annotations

from mm_companion.ui.settings.window import PAGES, SettingsWindow

__all__ = ["PAGES", "SettingsWindow"]

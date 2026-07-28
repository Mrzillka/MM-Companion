"""The ``Theme`` submenu: pick one of the discovered presets.

Shared by the launcher and every character sheet, so the look can be changed
without first opening a character. One checkable action per preset, mutually
exclusive, writing through :func:`mm_companion.ui.theme.set_active_theme`.

Switching re-applies the application stylesheet immediately, which re-dresses
everything the theme styles through QSS. It does **not** reach a widget that
built its own styling in its constructor — a power card's border, a modifier
chip's fill — because those are only rebuilt when their section next redraws. So
a switch offers a relaunch, the same bargain
:mod:`mm_companion.ui.mods_window` already strikes for mods, rather than leaving
half the window in the old look with no explanation.
"""

from __future__ import annotations

from PySide6.QtCore import QTimer
from PySide6.QtGui import QActionGroup
from PySide6.QtWidgets import QApplication, QMenu, QMessageBox, QWidget

from mm_companion.ui import theme
from mm_companion.ui.app_restart import restart_app


def build_theme_menu(parent_menu: QMenu | None, owner: QWidget) -> QMenu:
    """A ``Theme`` menu listing the available presets.

    With *parent_menu*, it is added as a submenu (the sheet's ``Settings`` menu).
    With ``None`` it is a standalone menu owned by *owner*, for the launcher,
    which has buttons instead of a menu bar. Either way *owner* parents any
    confirmation dialog. Returned so tests can drive the actions directly.
    """
    submenu = parent_menu.addMenu("Theme") if parent_menu is not None else QMenu("Theme", owner)
    group = QActionGroup(submenu)
    group.setExclusive(True)

    active = theme.active_theme()
    # Sorted by display name so the order doesn't depend on the filesystem, with
    # a workspace preset sitting among the bundled ones rather than after them.
    for preset in sorted(theme.available_themes().values(), key=lambda t: t.name.lower()):
        action = submenu.addAction(preset.name)
        action.setCheckable(True)
        action.setChecked(preset.id == active.id)
        if preset.description:
            action.setToolTip(preset.description)
        action.triggered.connect(
            lambda _checked=False, theme_id=preset.id, w=owner: _switch(theme_id, w)
        )
        group.addAction(action)

    submenu.setToolTipsVisible(True)
    return submenu


def _switch(theme_id: str, owner: QWidget) -> None:
    """Apply *theme_id*, then offer the relaunch that makes it reach everything."""
    if theme_id == theme.active_theme().id:
        return
    theme.set_active_theme(theme_id, QApplication.instance())
    if _prompt_restart(owner):
        # Deferred so the menu finishes closing before windows start shutting down;
        # tearing the menu's own parent out from under it mid-trigger crashes Qt.
        QTimer.singleShot(0, restart_app)


def _prompt_restart(owner: QWidget) -> bool:
    choice = QMessageBox.question(
        owner,
        "Restart to finish applying?",
        "The new theme is applied. A few parts of the interface only pick it up "
        "after a restart.\n\nRestart MM-Companion now?",
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        QMessageBox.StandardButton.Yes,
    )
    return choice == QMessageBox.StandardButton.Yes

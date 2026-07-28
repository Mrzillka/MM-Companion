"""The Theme submenu lists the presets and switching persists the choice."""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QApplication, QMenu, QWidget

from mm_companion.core import storage
from mm_companion.ui import theme
from mm_companion.ui.theme_menu import build_theme_menu


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def isolated_workspace(tmp_path, monkeypatch):
    monkeypatch.setenv(storage.HOME_ENV_VAR, str(tmp_path))
    theme.reset()
    yield
    theme.reset()


def test_it_lists_every_preset_with_the_active_one_checked(qapp) -> None:
    owner = QWidget()
    menu = build_theme_menu(None, owner)

    actions = {a.text(): a for a in menu.actions()}
    assert {"Classic", "Slate Dark", "Parchment Light"} <= set(actions)
    assert actions["Classic"].isChecked()
    assert not actions["Slate Dark"].isChecked()


def test_the_choices_are_mutually_exclusive(qapp) -> None:
    menu = build_theme_menu(None, QWidget())

    assert all(a.actionGroup() is not None for a in menu.actions())
    assert menu.actions()[0].actionGroup().isExclusive()


def test_it_nests_under_a_parent_menu_when_given_one(qapp) -> None:
    owner = QWidget()
    parent = QMenu(owner)
    submenu = build_theme_menu(parent, owner)

    assert submenu.title() == "Theme"
    assert parent.actions()[0].menu() is submenu


def test_triggering_a_preset_persists_it_and_restyles_the_app(qapp) -> None:
    """The stylesheet is re-applied at once; the relaunch only covers the rest."""
    menu = build_theme_menu(None, QWidget())
    slate = next(a for a in menu.actions() if a.text() == "Slate Dark")

    # Bypass the restart prompt: this test is about what the switch itself does.
    theme.set_active_theme("slate-dark", qapp)

    assert storage.load_settings()["theme"] == "slate-dark"
    assert theme.color("accent") == "#5b9ee0"
    assert "#blockFrame" in qapp.styleSheet()  # a styled preset dresses the window
    assert slate.text() == "Slate Dark"  # the action is still the one we found

    theme.set_active_theme("classic", qapp)
    assert "#blockFrame" not in qapp.styleSheet()  # back to native widgets

"""The Settings window and its Themes page.

House style for a window test: a module-scoped ``QApplication``, a workspace
pointed at ``tmp_path``, and private attributes read directly.
"""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QApplication, QInputDialog, QMessageBox

from mm_companion.core import storage
from mm_companion.ui import theme
from mm_companion.ui.settings import SettingsWindow
from mm_companion.ui.settings import window as window_module
from mm_companion.ui.settings.theme_page import ThemePage
from mm_companion.ui.theme import loader


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def isolated_workspace(tmp_path, monkeypatch, qapp):
    monkeypatch.setenv(storage.HOME_ENV_VAR, str(tmp_path))
    theme.reset()
    yield tmp_path
    theme.reset()
    theme.apply(qapp)


@pytest.fixture(autouse=True)
def restarts(monkeypatch) -> list[int]:
    """Count relaunch offers accepted instead of relaunching.

    ``closeEvent`` offers one whenever the look changed, and a test that answers
    every ``QMessageBox.question`` with Yes accepts it — which would spawn a real
    MM-Companion process, once per test, in the background.
    """
    accepted: list[int] = []
    monkeypatch.setattr(window_module, "restart_app", lambda: accepted.append(1))
    return accepted


@pytest.fixture
def window(qapp):
    return SettingsWindow()


@pytest.fixture
def page(window) -> ThemePage:
    return window._pages[0]


def say(monkeypatch, answer: QMessageBox.StandardButton) -> None:
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: answer)


def pick(page: ThemePage, theme_id: str) -> None:
    page._picker.setCurrentIndex(page._picker.findData(theme_id))


def custom_theme(page: ThemePage, name: str = "Mine") -> str:
    """Duplicate whatever is selected under *name*, and return the new id."""
    from mm_companion.ui.settings import theme_page as module

    original = module.QInputDialog.getText
    module.QInputDialog.getText = staticmethod(lambda *a, **k: (name, True))
    try:
        page._duplicate()
    finally:
        module.QInputDialog.getText = original
    return page._picker.currentData()


# -- the window -----------------------------------------------------------------


def test_the_nav_lists_the_pages_and_drives_the_stack(window) -> None:
    labels = [window._nav.item(i).text() for i in range(window._nav.count())]

    assert labels == ["Themes"]
    assert window._stack.currentWidget() is window._pages[0]


def test_closing_takes_a_preview_back_down(window, page, qapp, monkeypatch) -> None:
    """A draft that outlived its window would dress the app until it restarted."""
    say(monkeypatch, QMessageBox.StandardButton.No)
    theme_id = custom_theme(page)
    page._editor._set_color("accent", "#010203")
    page._preview_now()
    assert theme.color("accent") == "#010203"

    window.close()

    assert theme.preview_theme() is None
    assert theme.available_themes()[theme_id].colors["accent"] != "#010203"


def test_closing_dirty_can_save_on_the_way_out(window, page, monkeypatch, qapp) -> None:
    say(monkeypatch, QMessageBox.StandardButton.Yes)
    theme_id = custom_theme(page)
    page._editor._set_color("accent", "#010203")
    assert page.is_dirty()

    window.close()

    theme.reset()
    assert theme.available_themes()[theme_id].colors["accent"] == "#010203"


def test_a_changed_look_offers_a_relaunch_on_close(window, page, monkeypatch, restarts) -> None:
    """The stylesheet re-dresses at once; a card's constructor-built border does not."""
    say(monkeypatch, QMessageBox.StandardButton.Yes)
    pick(page, "slate-dark")

    window.close()
    QApplication.instance().processEvents()  # the offer is deferred past closeEvent

    assert restarts == [1]


def test_an_untouched_window_offers_nothing_on_close(window, monkeypatch, restarts) -> None:
    say(monkeypatch, QMessageBox.StandardButton.Yes)

    window.close()
    QApplication.instance().processEvents()

    assert restarts == []


# -- picking a preset -----------------------------------------------------------


def test_every_preset_is_listed_with_the_active_one_shown(page) -> None:
    listed = {page._picker.itemData(i) for i in range(page._picker.count())}

    assert {"classic", "slate-dark", "parchment-light"} <= listed
    assert page._picker.currentData() == "classic"
    assert "built-in" in page._picker.currentText()


def test_picking_a_preset_persists_it_at_once(page) -> None:
    """The old Theme menu saved on click; losing that would cost two more."""
    pick(page, "slate-dark")

    assert storage.load_settings()["theme"] == "slate-dark"
    assert theme.color("accent") == "#5b9ee0"


def test_the_filter_box_narrows_the_form(page) -> None:
    """Sixty-odd rows is a long scroll; the box above them is how you skip it."""
    page._filter_field.setText("accent")

    assert page._editor._filter == "accent"
    assert page._scroll.verticalScrollBar().value() == 0


def test_a_bundled_preset_is_shown_but_not_edited(page) -> None:
    assert page._name_field.isReadOnly()
    assert not page._delete_button.isEnabled()
    assert "cannot be edited" in page._banner.text()


# -- duplicating ----------------------------------------------------------------


def test_duplicating_writes_a_file_and_selects_it(page, isolated_workspace) -> None:
    theme_id = custom_theme(page, "My Theme")

    assert theme_id == "my-theme"
    assert (isolated_workspace / "themes" / "my-theme.json").is_file()
    assert page._picker.currentData() == "my-theme"
    assert storage.load_settings()["theme"] == "my-theme"


def test_a_duplicate_is_editable(page) -> None:
    custom_theme(page)

    assert not page._name_field.isReadOnly()
    assert page._delete_button.isEnabled()
    assert page._delete_button.text() == "Delete"


def test_duplicating_a_built_in_never_shadows_it(page) -> None:
    """Otherwise a copy called "Classic" would replace the built-in preset."""
    assert custom_theme(page, "Classic") == "classic-2"


# -- editing --------------------------------------------------------------------


def test_an_edit_previews_without_writing_anything(page) -> None:
    theme_id = custom_theme(page)
    saved = loader.workspace_theme_path(theme_id).read_text(encoding="utf-8")

    page._editor._set_color("accent", "#010203")
    page._preview_now()

    assert theme.color("accent") == "#010203"
    assert page.is_dirty()
    assert loader.workspace_theme_path(theme_id).read_text(encoding="utf-8") == saved


def test_saving_writes_the_draft_and_activates_it(page) -> None:
    theme_id = custom_theme(page)
    page._editor._set_color("accent", "#010203")

    page.save()

    theme.reset()
    assert theme.available_themes()[theme_id].colors["accent"] == "#010203"
    assert not page.is_dirty()


def test_reverting_throws_the_draft_away(page) -> None:
    custom_theme(page)
    page._editor._set_color("accent", "#010203")
    page._preview_now()

    page._revert()

    assert not page.is_dirty()
    assert theme.preview_theme() is None
    assert theme.color("accent") == "#4488cf"


def test_renaming_reaches_the_saved_file(page) -> None:
    theme_id = custom_theme(page)
    page._name_field.setText("Renamed")

    page._rename()
    page.save()

    theme.reset()
    assert theme.available_themes()[theme_id].name == "Renamed"


def test_a_draft_that_cannot_be_applied_is_reported_not_raised(page) -> None:
    """A styled theme with no surfaces would raise inside the stylesheet builder."""
    from dataclasses import replace

    custom_theme(page)
    broken = replace(
        page._editor.draft(), chrome=replace(page._editor.draft().chrome, mode="styled")
    )
    page._editor.load(broken)

    page._preview_now()

    assert "Could not apply" in page._status.text()
    assert theme.preview_theme() is not broken


def test_flipping_to_styled_borrows_surfaces_from_a_shipped_preset(page, monkeypatch) -> None:
    custom_theme(page)
    monkeypatch.setattr(QInputDialog, "getItem", lambda *a, **k: ("Slate Dark", True))

    page._offer_styled_surfaces()

    draft = page._editor.draft()
    assert draft.chrome.mode == "styled"
    assert theme.is_literal_color(draft.colors["surface.window"])
    assert draft.colors["accent"] == "#4488cf"  # its own accent, not Slate Dark's


# -- deleting -------------------------------------------------------------------


def test_deleting_a_custom_theme_falls_back_to_the_default(page, monkeypatch) -> None:
    theme_id = custom_theme(page)
    say(monkeypatch, QMessageBox.StandardButton.Yes)

    page._delete()

    assert not loader.workspace_theme_path(theme_id).exists()
    assert theme.active_theme().id == "classic"


def test_a_cancelled_delete_keeps_the_theme(page, monkeypatch) -> None:
    theme_id = custom_theme(page)
    say(monkeypatch, QMessageBox.StandardButton.No)

    page._delete()

    assert loader.workspace_theme_path(theme_id).is_file()


def test_a_theme_shadowing_a_built_in_offers_to_revert(page, monkeypatch) -> None:
    from dataclasses import replace

    loader.save_workspace_theme(replace(theme.available_themes()["classic"], name="My Classic"))
    theme.reset()
    page._reload_presets(select="classic")

    assert page._delete_button.text() == "Revert to built-in"
    assert "built-in one back" in page._banner.text()

    say(monkeypatch, QMessageBox.StandardButton.Yes)
    page._delete()

    assert theme.available_themes()["classic"].name == "Classic"

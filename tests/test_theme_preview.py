"""The live-preview seam: an unsaved draft dresses the app without being saved.

Needs a ``QApplication`` because previewing goes through ``theme.apply``, which
installs a palette and a stylesheet.
"""

from __future__ import annotations

from dataclasses import replace

import pytest
from PySide6.QtWidgets import QApplication

from mm_companion.core import storage
from mm_companion.ui import block_sizes, theme
from mm_companion.ui.theme import loader


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def isolated_workspace(tmp_path, monkeypatch):
    monkeypatch.setenv(storage.HOME_ENV_VAR, str(tmp_path))
    theme.reset()
    yield tmp_path
    theme.reset()


def draft_of(theme_id: str, **changes):
    """The bundled preset *theme_id* with *changes* applied, as an unsaved draft."""
    return replace(loader.available_themes()[theme_id], **changes)


def test_a_preview_wins_over_the_saved_preset_without_saving_it() -> None:
    saved = storage.load_settings().get("theme")
    draft = draft_of("classic", colors={**theme.active_theme().colors, "accent": "#010203"})

    theme.set_preview(draft)

    assert theme.color("accent") == "#010203"
    assert theme.preview_theme() is draft
    assert storage.load_settings().get("theme") == saved  # nothing written


def test_clearing_the_preview_restores_the_saved_preset() -> None:
    theme.set_preview(draft_of("classic", colors={"accent": "#010203"}))

    theme.set_preview(None)

    assert theme.preview_theme() is None
    assert theme.color("accent") == "#4488cf"


def test_reset_drops_a_preview() -> None:
    """``reset`` means forget everything; a preview surviving it would be a lie."""
    theme.set_preview(draft_of("classic", colors={"accent": "#010203"}))

    theme.reset()

    assert theme.preview_theme() is None
    assert theme.color("accent") == "#4488cf"


def test_a_previewed_block_override_reaches_the_block_sizes_and_reverts() -> None:
    """The bounds cache is keyed by theme id, which a draft shares with its origin."""
    baseline = block_sizes.load_block_sizes()["abilities"].min_width

    theme.set_preview(draft_of("classic", blocks={"abilities": {"min_width": 999}}))
    assert block_sizes.load_block_sizes()["abilities"].min_width == 999

    theme.set_preview(None)
    assert block_sizes.load_block_sizes()["abilities"].min_width == baseline


def test_previewing_a_styled_draft_dresses_the_app_and_undresses_it_again(qapp) -> None:
    slate = loader.available_themes()["slate-dark"]

    theme.set_preview(slate, qapp)
    assert "#blockFrame" in qapp.styleSheet()
    assert qapp.palette().window().color().name() == slate.colors["surface.window"]

    theme.set_preview(None, qapp)
    assert "#blockFrame" not in qapp.styleSheet()  # classic states no chrome


def test_saving_over_a_preview_leaves_no_draft_behind() -> None:
    """``set_active_theme`` goes through ``reset``, so the two converge."""
    theme.set_preview(draft_of("classic", colors={"accent": "#010203"}))

    theme.set_active_theme("slate-dark")

    assert theme.preview_theme() is None
    assert theme.color("accent") == "#5b9ee0"

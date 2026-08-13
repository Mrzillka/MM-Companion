"""Toggling the lock may change a block's height. It must never change its width.

Locking sheds a field's border and its padding, and several blocks hide their
editing entry points outright — so a block's own minimum really does move. The
standing rule is that only the *height* may: the window does not resize itself on
a lock toggle, so a block that got wider when unlocked simply clipped its content
against a window the user had already sized.

Two mechanisms keep the width still, and this file fences both:

* a block whose extra controls are genuinely needed reserves the room while locked,
  which for most blocks is the ``min_width`` floor in ``ui/block_sizes.json``
  already covering the unlocked content;
* a block whose controls were merely *laid out* too wide wraps them instead — the
  Equipment block's three "Add…" buttons, which were 120px wider abreast than the
  block's floor and are a :class:`FlowContainer` now.

The floors mask the first group, so these tests deliberately assert on the
**frame**, which is what the page and the strip actually negotiate with. A preset
that lowers a floor, or a denser font, would unmask one — and that is exactly the
regression this is here to catch.
"""

from __future__ import annotations

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from mm_companion.core.data_loader import load_game_data
from mm_companion.ui import theme
from mm_companion.ui.character_sheet import CharacterSheet


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


@pytest.fixture
def sheet(qapp: QApplication):
    """A laid-out sheet under the real application stylesheet.

    The theme has to be applied: without it there is no arrow-column padding on a
    spin box at all, and the locked/unlocked delta this file is about would be
    nearly zero — the test would pass by measuring nothing. ``conftest``'s autouse
    workspace fixture calls ``theme.reset()`` on both sides, so it is applied per
    test rather than once for the module.
    """
    theme.apply(qapp)
    built = CharacterSheet(load_game_data())
    built.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
    built.resize(1100, 900)
    built.show()
    _settle()
    yield built
    built.hide()
    built.deleteLater()
    QApplication.processEvents()


def _settle(times: int = 8) -> None:
    for _ in range(times):
        QApplication.processEvents()


def _frame_widths(sheet: CharacterSheet) -> dict[str, int]:
    _settle()
    return {key: sheet.block_frame(key).minimumSizeHint().width() for key in sheet.block_keys()}


def test_no_block_changes_width_when_the_lock_is_toggled(sheet: CharacterSheet) -> None:
    sheet.set_locked(True)
    locked = _frame_widths(sheet)
    sheet.set_locked(False)
    unlocked = _frame_widths(sheet)

    # Per block rather than as a whole dict, so a failure names the block that grew
    # and by how much — which is the first thing you want to know.
    grew = {key: (locked[key], unlocked[key]) for key in locked if locked[key] != unlocked[key]}
    assert not grew, f"these blocks changed width on unlock: {grew}"


def test_the_lock_really_is_changing_the_widgets(sheet: CharacterSheet) -> None:
    """The guard on the test above: prove the toggle does something measurable.

    If a future preset or platform style made unlocking a no-op for every widget,
    the invariance test would pass while measuring nothing at all. Something must
    move — it just has to be the height, or a width the block's floor absorbs.
    """
    sheet.set_locked(True)
    locked = {key: sheet.block_frame(key).section.sizeHint().width() for key in sheet.block_keys()}
    sheet.set_locked(False)
    _settle()
    unlocked = {
        key: sheet.block_frame(key).section.sizeHint().width() for key in sheet.block_keys()
    }
    assert any(locked[key] != unlocked[key] for key in locked)


def test_equipments_buttons_wrap_instead_of_widening_the_block(sheet: CharacterSheet) -> None:
    """The Equipment block was the one whose floor did not cover its unlocked width.

    Its three "Add…" buttons were a fixed row 360px wide against a 240px floor, and
    they only appear unlocked — so the block was 120px wider unlocked than locked.
    They wrap now, which puts the unlocked minimum back under the floor.
    """
    section = sheet.block_frame("equipment").section
    sheet.set_locked(False)
    _settle()
    assert section._buttons.isVisible()
    # One button's worth, not three abreast.
    widest = max(
        button.sizeHint().width()
        for button in (section._add_button, section._custom_button, section._platform_button)
    )
    row = sum(
        button.sizeHint().width()
        for button in (section._add_button, section._custom_button, section._platform_button)
    )
    assert section._buttons.minimumSizeHint().width() < row
    assert section._buttons.minimumSizeHint().width() <= widest + 12


def test_the_page_minimum_follows_a_lock_toggle(sheet: CharacterSheet) -> None:
    """The page's minimum is an explicit number behind a QScrollArea — the one link
    no invalidation crosses on its own, so ``set_locked`` recomputes it."""
    sheet.set_locked(False)
    _settle()
    scroll = sheet.page_scroll_area()
    bar = scroll.verticalScrollBar().sizeHint().width()
    assert scroll.minimumWidth() == sheet.canvas.content_minimum_width() + bar + 2


def test_locking_marks_nothing_dirty(sheet: CharacterSheet) -> None:
    """Locking is a view switch: it must not read as an edit."""
    seen: list[str] = []
    sheet.edited.connect(lambda: seen.append("edited"))
    sheet.set_locked(False)
    sheet.set_locked(True)
    _settle()
    assert seen == []

"""The recommended sizes are measured, not guessed.

`ui/block_sizes.json` says how wide each block reads well — the size it opens at,
and the size a divider's detent sticks at. Those numbers were inherited from the
days when they were *floors* the layout enforced, and a number that used to be a
floor is not automatically a good recommendation: several were set to whatever
stopped a block clipping, which is a different question from where a block stops
being comfortable to read.

So this measures. For every block it narrows the frame until the content no
longer fits — the point where reflow has run out and the block would start
scrolling inside itself — and checks the recommendation sits above that and not
absurdly above it. A recommendation *under* the measured floor is the one worth
catching: it would put the detent, and the size the block opens at, somewhere the
block is already scrolling.

The numbers themselves are deliberately not asserted exactly. They move with the
font, the preset and the platform, and a test that pinned them would fail on
somebody else's machine for no reason anybody could act on. Run this file with
``-s`` to see the measured table.
"""

from __future__ import annotations

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from mm_companion.core.data_loader import load_game_data
from mm_companion.ui.block_sizes import load_block_sizes
from mm_companion.ui.character_sheet import CharacterSheet

#: How far above the measured floor a recommendation may sit before it is simply
#: wasteful. Generous: a block is allowed elbow room.
SLACK = 260

#: The narrowest width the search tries. A block that still fits here has no
#: measurable comfort width at all — it reflows the whole way down — so only the
#: "not narrower than this" half of the check means anything for it.
SEARCH_FLOOR = 60

#: Blocks whose comfortable width is set by something other than their content
#: fitting. The roller reflows all the way down and would measure tiny, but a die
#: and a history in 100px is not a roller anybody wants; Notes has no natural
#: width at all, since a note is as wide as you make it.
MEASURED_EXEMPT = {"dice", "scene", "notes"}


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


@pytest.fixture
def sheet(qapp: QApplication) -> CharacterSheet:
    """A laid-out sheet. Function-scoped, and it has to be: ``conftest``'s autouse
    teardown deletes every top-level widget after each test, so a module-scoped
    one would be a pile of dead C++ objects by the second."""
    built = CharacterSheet(load_game_data())
    built.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
    built.resize(1600, 900)
    built.show()
    _settle(qapp, 10)
    yield built
    built.hide()
    built.deleteLater()


def _settle(qapp: QApplication, rounds: int = 5) -> None:
    for _ in range(rounds):
        qapp.processEvents()


def comfortable_width(sheet: CharacterSheet, key: str, qapp: QApplication) -> int:
    """The narrowest width at which *key*'s content still fits its frame.

    Measured with the block **floated out**, which is the only way to give one
    frame a width of our choosing: docked, it sits in a splitter that lays it out
    again the moment anything resizes it. Floating is an operation the canvas
    supports, so this measures the same widget a user would see, and it is docked
    again afterwards.

    Binary-searched rather than stepped: settling a frame costs several event-loop
    turns, and a linear walk over 800px would take a minute a block.
    """
    canvas = sheet.canvas
    was_pinned = canvas.is_pinned(key)
    canvas.float_block(key)
    _settle(qapp)
    window = canvas.block_window(key)
    assert window is not None, f"{key} did not float"

    def fits(width: int) -> bool:
        window.resize(width, 720)
        _settle(qapp)
        frame = sheet.block_frame(key)
        # Whether the section still fits, asked of the section rather than of a
        # scrollbar. A block scrolls vertically only now (see ``_InnerScroll``), so
        # there is no horizontal bar left to read; what "no longer fits" means is
        # that the section's own minimum — everything it can shed already shed —
        # is wider than the room it has.
        return frame.section.minimumSizeHint().width() <= frame._scroll.viewport().width()

    try:
        low, high = SEARCH_FLOOR, 880
        if fits(low):
            return low
        if not fits(high):
            return high
        while high - low > 8:
            middle = (low + high) // 2
            if fits(middle):
                high = middle
            else:
                low = middle
        return high
    finally:
        if was_pinned:
            canvas.pin_block(key)
        else:
            canvas.dock_block(key, 0, 0, new_row=True)
        _settle(qapp)


def test_the_recommendations_match_what_the_blocks_actually_need(sheet, qapp) -> None:
    """One pass, both directions, because building and measuring a sheet is slow
    and splitting this in two would double it for no extra coverage."""
    sizes = load_block_sizes()
    measured: dict[str, int] = {}
    short: list[str] = []
    fat: list[str] = []

    for key, size in sorted(sizes.items()):
        if key.startswith("gm_") or not size.width or key in MEASURED_EXEMPT:
            continue
        needs = comfortable_width(sheet, key, qapp)
        measured[key] = needs
        if size.width < needs:
            short.append(f"{key}: recommends {size.width}, needs {needs}")
        elif needs > SEARCH_FLOOR and size.width > needs + SLACK:
            # Only where the measurement means something. A block that still fits
            # at the search floor reflows the whole way down and has no measurable
            # comfort width — how wide Conditions wants to be is a judgement about
            # how its chips read, not a number a scrollbar can answer.
            fat.append(f"{key}: recommends {size.width}, needs only {needs}")

    print("\n  measured comfortable widths:")
    for key, needs in measured.items():
        floor = " (reflows all the way down)" if needs <= SEARCH_FLOOR else ""
        print(f"    {key:18} recommends {sizes[key].width:4}  needs {needs:4}{floor}")

    assert measured, "nothing was measured, so this test proves nothing"
    assert not short, "recommendations below what the block can show:\n  " + "\n  ".join(short)
    assert not fat, "recommendations far above what the block needs:\n  " + "\n  ".join(fat)


def test_the_blocks_that_state_nothing_are_the_ones_that_measure_themselves(sheet) -> None:
    """Abilities and Resistances say nothing in either dimension: their tables
    report their real columns and rows, which beats a number a denser preset would
    make wrong in both directions."""
    sizes = load_block_sizes()

    for key in ("abilities", "resistances"):
        assert not sizes[key]
        assert sheet.block_frame(key).sizeHint().width() > 0


def test_a_measured_floor_is_the_reflowed_one(sheet, qapp) -> None:
    """The width being measured is what the block needs *after* it has shed
    columns and wrapped its forms — not the block at its roomiest. If those were
    the same number, nothing would be adapting and the measurement would be
    meaningless."""
    for key in ("skills", "system_info"):
        needs = comfortable_width(sheet, key, qapp)
        assert needs < sheet.block_frame(key).content_size_hint().width(), key

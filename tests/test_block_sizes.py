"""Block size constraints load from config and are applied to the docks."""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QApplication

from mm_companion.core.data_loader import load_game_data
from mm_companion.ui.block_sizes import UNBOUNDED, BlockSize, load_block_sizes
from mm_companion.ui.character_sheet import CharacterSheet


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_block_sizes_load_for_every_block() -> None:
    sizes = load_block_sizes()

    assert set(sizes) == {
        "base_info",
        "system_info",
        "character_image",
        "abilities",
        "resistances",
        "conditions",
        "advantages",
        "skills",
        "powers",
    }
    assert all(isinstance(s, BlockSize) for s in sizes.values())
    # The inline "_comment" key is not a block.
    assert "_comment" not in sizes


def test_horizontally_pinned_blocks_have_a_max_width() -> None:
    sizes = load_block_sizes()

    # Abilities and resistances are compact grids that shouldn't stretch wide.
    assert sizes["abilities"].max_width < UNBOUNDED
    assert sizes["resistances"].max_width < UNBOUNDED
    # Base info grows tall on demand — conditions (which can bundle into several
    # chips) must never be clipped, so its height is unbounded.
    assert sizes["base_info"].max_height == UNBOUNDED
    # The content blocks grow freely both ways.
    assert sizes["skills"].max_width == UNBOUNDED
    assert sizes["powers"].max_height == UNBOUNDED


def test_abilities_and_resistances_share_one_fixed_size() -> None:
    sizes = load_block_sizes()
    abilities, resistances = sizes["abilities"], sizes["resistances"]

    # Identical constraints, and fixed (non-resizable) in both dimensions.
    assert abilities == resistances
    assert abilities.min_width == abilities.max_width
    assert abilities.min_height == abilities.max_height


def test_abilities_and_resistances_frames_are_fixed_and_equal(qapp: QApplication) -> None:
    sheet = CharacterSheet(load_game_data())

    ability_frame = sheet.block_frame("abilities")
    resistance_frame = sheet.block_frame("resistances")
    for frame in (ability_frame, resistance_frame):
        assert frame.minimumWidth() == frame.maximumWidth()
        # Height is pinned via the effective minimum (minimumSizeHint), capped at
        # the configured max, so a fixed block can neither grow nor shrink.
        assert frame.minimumSizeHint().height() == frame.maximumHeight()
    assert ability_frame.minimumSizeHint() == resistance_frame.minimumSizeHint()


def test_block_frames_apply_the_configured_constraints(qapp: QApplication) -> None:
    sheet = CharacterSheet(load_game_data())
    sizes = load_block_sizes()

    for key, spec in sizes.items():
        frame = sheet.block_frame(key)
        # The configured minimum is a floor. The section sits directly in the frame
        # (no inner scroll area), so a block whose content needs more than the
        # configured minimum — e.g. Base Information or the Advantages picker —
        # reports the larger content-driven minimum instead. Height is enforced
        # through the effective minimum (minimumSizeHint), so a block is never
        # squashed below its content; the page scrolls instead.
        assert frame.minimumWidth() >= spec.min_width
        assert frame.minimumSizeHint().height() >= spec.min_height
        # A configured max pins the frame exactly; an unbounded dimension is left
        # effectively unconstrained (Qt reports its own large max).
        if spec.max_width < UNBOUNDED:
            assert frame.maximumWidth() == spec.max_width
        else:
            assert frame.maximumWidth() >= 100_000
        if spec.max_height < UNBOUNDED:
            assert frame.maximumHeight() == spec.max_height
        else:
            assert frame.maximumHeight() >= 100_000

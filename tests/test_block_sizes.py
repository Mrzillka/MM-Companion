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
        "abilities",
        "resistances",
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
    # Base info shouldn't grow tall.
    assert sizes["base_info"].max_height < UNBOUNDED
    # The content blocks grow freely both ways.
    assert sizes["skills"].max_width == UNBOUNDED
    assert sizes["powers"].max_height == UNBOUNDED


def test_docks_apply_the_configured_constraints(qapp: QApplication) -> None:
    sheet = CharacterSheet(load_game_data())
    sizes = load_block_sizes()

    for key, spec in sizes.items():
        dock = sheet.docks[f"dock_{key}"]
        assert dock.minimumWidth() == spec.min_width
        assert dock.minimumHeight() == spec.min_height
        # A configured max pins the dock exactly; an unbounded dimension is left
        # effectively unconstrained (Qt's dock layout reports its own large max).
        if spec.max_width < UNBOUNDED:
            assert dock.maximumWidth() == spec.max_width
        else:
            assert dock.maximumWidth() >= 100_000
        if spec.max_height < UNBOUNDED:
            assert dock.maximumHeight() == spec.max_height
        else:
            assert dock.maximumHeight() >= 100_000

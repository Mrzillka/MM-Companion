"""The block registry is the single source of truth for the sheet's block set."""

from __future__ import annotations

import pytest

from mm_companion.ui.block_sizes import BlockSize
from mm_companion.ui.blocks import (
    BlockDescriptor,
    block_descriptors,
    default_pin_lines,
    default_rows,
    register_block,
    unregister_block,
)

BASE_KEYS = {
    "base_info",
    "system_info",
    "character_image",
    "abilities",
    "resistances",
    "conditions",
    "advantages",
    "complications",
    "skills",
    "powers",
    "equipment",
    "notes",
    "dice",
}

# The default arrangement the registry must reproduce. The Dice block is absent on
# purpose: it starts in the pinned strip, not in a row (see default_pin_lines).
EXPECTED_DEFAULT_ROWS = [
    ["base_info", "system_info", "character_image"],
    ["abilities", "resistances", "conditions"],
    ["advantages"],
    ["complications"],
    ["skills"],
    ["powers"],
    ["equipment"],
    ["notes"],
]


def test_base_blocks_are_registered() -> None:
    keys = {d.key for d in block_descriptors()}
    assert keys == BASE_KEYS


def test_default_rows_match_the_historical_layout() -> None:
    assert default_rows() == EXPECTED_DEFAULT_ROWS


def test_the_dice_block_starts_in_the_pinned_strip() -> None:
    # A die that scrolls away with the page is no use mid-fight, so the roller is
    # the one base block whose default home is the strip.
    assert default_pin_lines() == [["dice"]]


def test_rows_and_pinned_lines_cover_every_block_exactly_once() -> None:
    # The invariant the arrangement model enforces: a block placed twice (or not at
    # all) makes the whole default layout invalid.
    placed = [key for row in default_rows() for key in row]
    placed += [key for line in default_pin_lines() for key in line]
    assert sorted(placed) == sorted(BASE_KEYS)


def test_every_base_descriptor_carries_a_size_and_factory() -> None:
    for descriptor in block_descriptors():
        assert callable(descriptor.factory)
        assert isinstance(descriptor.size, BlockSize)
        assert descriptor.title  # a non-empty dock title


def test_registering_a_mod_block_extends_the_set_and_layout() -> None:
    descriptor = BlockDescriptor(
        key="mod_notes",
        title="Mod Notes",
        factory=lambda data, character: None,  # never built in this pure test
        size=BlockSize(min_width=200, min_height=100),
        default_row=8,
        default_col=0,
    )
    register_block(descriptor)
    try:
        assert descriptor in block_descriptors()
        # The new block lands in its own trailing row, past the last base one.
        assert default_rows()[-1] == ["mod_notes"]
    finally:
        unregister_block("mod_notes")

    assert {d.key for d in block_descriptors()} == BASE_KEYS


def test_registering_a_duplicate_key_raises_without_replace() -> None:
    dupe = BlockDescriptor("skills", "Skills", lambda data, character: None)
    with pytest.raises(KeyError):
        register_block(dupe)

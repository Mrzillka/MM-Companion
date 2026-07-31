"""Block size constraints load from config and are applied to the docks."""

from __future__ import annotations

import json

import pytest
from PySide6.QtWidgets import QApplication

from mm_companion.core.data_loader import load_game_data
from mm_companion.ui.block_sizes import UNBOUNDED, BlockSize, load_block_sizes
from mm_companion.ui.character_sheet import CharacterSheet


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


SHEET_BLOCKS = {
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
    "dice",
}
# The GM window's blocks live in the same file under a gm_ prefix, so a theme can
# retune them the same way; they are not part of the character sheet.
GM_BLOCKS = {"gm_players", "gm_npcs", "gm_rolls"}


def test_block_sizes_load_for_every_block() -> None:
    sizes = load_block_sizes()

    assert set(sizes) == SHEET_BLOCKS | GM_BLOCKS
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


def test_a_long_title_does_not_widen_its_block(qapp: QApplication) -> None:
    """A caption describes its block; it does not get to decide how wide it is.

    A section's live title grows with its point cost ("Abilities — 24 PP"), and a
    plain label would report that string's whole width as a minimum — pushing a
    fixed-width block past its own ``max_width``, and thickening the pinned strip
    beyond the ``min_width`` that is meant to set it. The title elides instead.
    """
    sheet = CharacterSheet(load_game_data())
    frame = sheet.block_frame("abilities")
    before = frame.minimumSizeHint().width()

    frame.title_bar.set_title("Abilities — 248 PP, and then some more words besides")

    assert frame.minimumSizeHint().width() == before
    # Nothing is lost: the caption still reads in full, just not necessarily on screen.
    assert frame.title_bar.title_text() == "Abilities — 248 PP, and then some more words besides"


def test_block_frames_apply_the_configured_constraints(qapp: QApplication) -> None:
    sheet = CharacterSheet(load_game_data())
    sizes = load_block_sizes()

    for key, spec in sizes.items():
        if key not in SHEET_BLOCKS:
            continue  # a GM-window block: not on this sheet
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


def test_a_theme_overrides_block_bounds_one_at_a_time(tmp_path, monkeypatch) -> None:
    """A preset's ``blocks`` map layers over the shipped file, bound by bound."""
    from mm_companion.core import storage
    from mm_companion.ui import theme

    monkeypatch.setenv(storage.HOME_ENV_VAR, str(tmp_path))
    (tmp_path / "themes").mkdir()
    (tmp_path / "themes" / "dense.json").write_text(
        json.dumps(
            {
                "id": "dense",
                "name": "Dense",
                "extends": "classic",
                "blocks": {"skills": {"min_width": 220}},
            }
        ),
        encoding="utf-8",
    )
    storage.save_settings({"theme": "dense"})
    theme.reset()
    try:
        sizes = load_block_sizes()
        # The named bound moved...
        assert sizes["skills"].min_width == 220
        # ...its siblings on the same block did not, and neither did other blocks.
        assert sizes["skills"].min_height == load_block_sizes()["skills"].min_height == 180
        assert sizes["powers"].min_width == 240
    finally:
        theme.reset()

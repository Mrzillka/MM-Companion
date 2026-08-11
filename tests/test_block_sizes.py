"""Block size constraints load from config and are applied to the docks."""

from __future__ import annotations

import json

import pytest
from PySide6.QtWidgets import QApplication, QSplitter

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
    "equipment",
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


def test_only_the_image_block_pins_a_dimension() -> None:
    sizes = load_block_sizes()

    # Base info grows tall on demand — conditions (which can bundle into several
    # chips) must never be clipped, so its height is unbounded.
    assert sizes["base_info"].max_height == UNBOUNDED
    # The content blocks grow freely both ways.
    assert sizes["skills"].max_width == UNBOUNDED
    assert sizes["powers"].max_height == UNBOUNDED
    # The portrait is the one block that would look wrong stretched wide.
    assert sizes["character_image"].max_width < UNBOUNDED


def test_abilities_and_resistances_state_no_bounds_at_all() -> None:
    """The two stat grids are sized by their own tables, not by this file.

    They used to share a hardcoded 300x340 in both dimensions — a number that
    compensated for the tables measuring themselves once at build time, and that a
    denser or roomier preset made wrong in both directions. The tables report their
    real rows and columns now, so there is nothing left here to state.
    """
    sizes = load_block_sizes()

    for key in ("abilities", "resistances"):
        assert sizes[key].min_width == 0
        assert sizes[key].min_height == 0
        assert sizes[key].max_width == UNBOUNDED
        assert sizes[key].max_height == UNBOUNDED


def test_abilities_and_resistances_frames_ask_for_their_content(qapp: QApplication) -> None:
    sheet = CharacterSheet(load_game_data())

    for key in ("abilities", "resistances"):
        frame = sheet.block_frame(key)
        # Unbounded, so the block shares its row's width rather than being pinned...
        assert frame.maximumWidth() >= 100_000
        assert frame.maximumHeight() >= 100_000
        # ...and its effective minimum is its own content, which is a real size and
        # exactly what the block wants to be (nothing is capping it any more).
        assert frame.minimumSizeHint().height() > 0
        assert frame.minimumSizeHint().height() == frame.sizeHint().height()


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


def test_a_configured_width_never_caps_what_a_block_asks_a_layout_for(
    qapp: QApplication,
) -> None:
    """The JSON width is a floor to the *layout*, not only to ``minimumSizeHint``.

    It used to be both, and the second one silently: ``setMinimumWidth`` does not
    raise a widget's layout minimum, it replaces it — ``qSmartMinSize`` ends with
    ``if (minSize.width() > 0) s.setWidth(minSize.width())``. So a block whose
    content needed more than its JSON number told every enclosing layout it did
    not, and the pinned strip (whose own minimum is its splitter's) squashed it to
    the number. Equipment is the block that already needed more; the roller under
    the Extended layout is what made it visible.
    """
    sheet = CharacterSheet(load_game_data())
    frame = sheet.block_frame("equipment")
    content = frame.minimumSizeHint().width()

    assert content > load_block_sizes()["equipment"].min_width

    holder = QSplitter()
    holder.addWidget(frame)

    assert holder.minimumSizeHint().width() >= content


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
        # reports the larger content-driven minimum instead.
        # Both floors are stated through the effective minimum (minimumSizeHint),
        # so a block is never squashed below its content in either dimension; an
        # explicit setMinimumWidth would *replace* the content minimum rather than
        # raise it (see BlockFrame.set_block_size).
        assert frame.minimumSizeHint().width() >= spec.min_width
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

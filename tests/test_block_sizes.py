"""Recommended block sizes load from config, and constrain nothing.

The whole point of this file inverted with the resizable grid. It used to prove
that the configured numbers were *floors* that reached every enclosing layout —
that a block could never be squashed below its content, and that the width floor
survived the ``qSmartMinSize`` trap. Now it proves the opposite: that a block can
be given any size at all, and that the numbers are only ever advice about the
size it opens at and the size its dividers stick at.
"""

from __future__ import annotations

import json

import pytest
from PySide6.QtWidgets import QApplication, QSplitter

from mm_companion.core.data_loader import load_game_data
from mm_companion.ui import theme
from mm_companion.ui.block_sizes import BOUNDS, RecommendedSize, load_block_sizes
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
    "notes",
    "dice",
    "scene",
}
# The GM window's blocks live in the same file under a gm_ prefix, so a theme can
# retune them the same way; they are not part of the character sheet.
GM_BLOCKS = {"gm_players", "gm_npcs", "gm_rolls", "gm_scene"}


def test_recommendations_load_for_every_block() -> None:
    sizes = load_block_sizes()

    assert set(sizes) == SHEET_BLOCKS | GM_BLOCKS
    assert all(isinstance(size, RecommendedSize) for size in sizes.values())


def test_abilities_and_resistances_state_no_opinion() -> None:
    """Their tables measure their real columns and rows, so a number would only
    be a worse answer that a denser or roomier preset would make wrong."""
    sizes = load_block_sizes()

    for key in ("abilities", "resistances"):
        assert sizes[key] == RecommendedSize()
        assert not sizes[key]


def test_every_other_block_recommends_at_least_a_width() -> None:
    sizes = load_block_sizes()
    stated = {key for key, size in sizes.items() if size.width}

    assert stated == (SHEET_BLOCKS | GM_BLOCKS) - {"abilities", "resistances"}


def test_the_old_min_names_are_still_read(monkeypatch: pytest.MonkeyPatch) -> None:
    """A mod's blocks.json is somebody else's file, and the mods repository pins
    an engine version — so ``min_width`` still means the recommendation."""
    from mm_companion.ui import block_sizes

    monkeypatch.setattr(
        block_sizes, "_baseline", lambda: {"legacy": {"min_width": 210, "min_height": 120}}
    )
    block_sizes.clear_block_size_cache()
    try:
        assert block_sizes.load_block_sizes()["legacy"] == RecommendedSize(210, 120)
    finally:
        block_sizes.clear_block_size_cache()


def test_a_theme_overrides_one_recommendation_at_a_time() -> None:
    """Overrides merge per number, so a preset that only wants Skills narrower
    says exactly that and inherits the rest."""
    from dataclasses import replace

    before = load_block_sizes()
    draft = replace(theme.active_theme(), blocks={"skills": {"recommended_width": 220}})
    theme.set_preview(draft)
    try:
        after = load_block_sizes()
        assert after["skills"].width == 220
        assert after["skills"].height == before["skills"].height
        assert after["powers"] == before["powers"]
    finally:
        theme.set_preview(None)


def test_the_json_is_documented_and_parses() -> None:
    from importlib.resources import files

    from mm_companion.ui.block_sizes import RESOURCE_NAME, RESOURCE_PACKAGE

    raw = json.loads(files(RESOURCE_PACKAGE).joinpath(RESOURCE_NAME).read_text(encoding="utf-8"))

    assert raw["_comment"], "the file explains itself to whoever retunes it next"
    for key, spec in raw.items():
        if key.startswith("_"):
            continue
        assert set(spec) <= set(BOUNDS), f"{key} states something that is not a recommendation"


# -- what the frames actually do with them -----------------------------------


def test_a_block_opens_at_its_recommendation(qapp: QApplication) -> None:
    sheet = CharacterSheet(load_game_data())
    sizes = load_block_sizes()

    for key in ("skills", "powers", "notes"):
        assert sheet.block_frame(key).sizeHint().width() == sizes[key].width


def test_a_block_with_no_opinion_opens_at_its_content(qapp: QApplication) -> None:
    sheet = CharacterSheet(load_game_data())

    for key in ("abilities", "resistances"):
        frame = sheet.block_frame(key)
        assert frame.sizeHint().width() == frame.content_size_hint().width()
        assert frame.sizeHint().width() > 0


def test_no_block_holds_a_layout_open_at_its_content(qapp: QApplication) -> None:
    """The rule this file exists for, and the exact inverse of what it used to be.

    A frame's minimum used to be its whole content, in both dimensions, and that
    climbed out through the row, the page, the pinned strip and the window — which
    is what made the application refuse to be made smaller than the blocks inside
    it. A page the user drags cannot work that way: a minimum is a refusal, and
    whether a block is too small to read has to be the user's call.
    """
    sheet = CharacterSheet(load_game_data())
    floor = int(theme.metric("block.min-extent"))

    for key in sorted(SHEET_BLOCKS):
        frame = sheet.block_frame(key)
        minimum = frame.minimumSizeHint()
        content = frame.content_size_hint()
        assert minimum.width() <= floor, key
        assert minimum.height() < content.height() or content.height() <= floor, key


def test_a_block_can_be_squashed_by_the_layout_that_holds_it(qapp: QApplication) -> None:
    """The same claim from the outside: a splitter really can make one small.

    This is the shape the old test used to *forbid* — it put a frame in a bare
    ``QSplitter`` and asserted the holder's minimum was at least the block's
    content. The strip squashing a block was the bug then; it is the feature now.
    """
    sheet = CharacterSheet(load_game_data())

    for key in ("skills", "equipment", "dice"):
        holder = QSplitter()
        holder.addWidget(sheet.block_frame(key))
        content = sheet.block_frame(key).content_size_hint().width()
        assert holder.minimumSizeHint().width() <= content


def test_a_squashed_block_keeps_a_title_bar_to_drag_back_open(qapp: QApplication) -> None:
    """The floor that is left is about being able to find a block you shrank."""
    sheet = CharacterSheet(load_game_data())
    frame = sheet.block_frame("skills")

    assert frame.minimumSizeHint().height() >= frame.title_bar.minimumSizeHint().height()


def test_no_block_pins_a_dimension_any_more(qapp: QApplication) -> None:
    """Pinning a width was how the old page stopped Abilities being stretched;
    a page whose columns the user drags has no use for it."""
    sheet = CharacterSheet(load_game_data())

    for key in sorted(SHEET_BLOCKS):
        frame = sheet.block_frame(key)
        assert frame.maximumWidth() >= 100_000, key
        assert frame.maximumHeight() >= 100_000, key


def test_a_long_title_does_not_widen_its_block(qapp: QApplication) -> None:
    """A live caption ("Abilities — 24 PP") must not become a width the block can
    never be dragged under. The eliding label is what keeps that true."""
    sheet = CharacterSheet(load_game_data())
    frame = sheet.block_frame("abilities")
    before = frame.minimumSizeHint().width()

    frame.title_bar.set_title("Abilities — 248 PP, and then some more words besides")

    assert frame.minimumSizeHint().width() == before
    assert frame.title_bar.title_text() == "Abilities — 248 PP, and then some more words besides"

"""The simple sheet's two presets, as pure data (see ``mm_companion.ui.simple.layout``)."""

from __future__ import annotations

from mm_companion.core.rules import RollSpec
from mm_companion.ui import layout_tree as lt
from mm_companion.ui.cards.rolls import split_roll_label
from mm_companion.ui.simple.layout import (
    STANDARD_EDGE,
    custom_layout,
    printable_page,
    standard_layout,
)

BASE_KEYS = [
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
    "scene",
    "dice",
]


def test_standard_places_every_block_exactly_once() -> None:
    layout = standard_layout(BASE_KEYS)

    assert sorted(layout.keys()) == sorted(BASE_KEYS)
    assert len(layout.keys()) == len(set(layout.keys()))


def test_standard_keeps_the_roller_beside_the_page() -> None:
    layout = standard_layout(BASE_KEYS)

    assert lt.keys(layout.strip) == ["dice", "scene"]
    assert layout.edge == STANDARD_EDGE
    assert "dice" not in lt.keys(layout.page)


def test_standard_opens_with_who_the_character_is() -> None:
    """The top row is the portrait, the name and the level — a printed sheet's header."""
    first_row = layout_rows(standard_layout(BASE_KEYS))[0]

    assert lt.keys(first_row) == ["character_image", "base_info", "system_info"]


def layout_rows(layout) -> list:
    return list(layout.page.children)


def test_a_missing_block_is_left_out_and_its_row_keeps_its_proportions() -> None:
    keys = [key for key in BASE_KEYS if key != "character_image"]
    first_row = layout_rows(standard_layout(keys))[0]

    assert lt.keys(first_row) == ["base_info", "system_info"]
    assert first_row.sizes == (5, 4)


def test_a_block_the_preset_does_not_know_gets_a_row_at_the_end() -> None:
    layout = standard_layout([*BASE_KEYS, "mod_tracker"])

    assert lt.keys(layout.page)[-1] == "mod_tracker"


def test_a_second_notes_block_sits_with_the_first() -> None:
    layout = standard_layout([*BASE_KEYS, "notes#2"])

    leaf = lt.leaf_for(layout.page, "notes#2")
    assert leaf is not None and leaf.keys == ("notes", "notes#2")


def _arrangement(page, *, strip=None, edge="left", floating=None, extent=300) -> dict:
    return {
        "page": lt.to_dict(page),
        "region": {"edge": edge, "extent": extent, "root": lt.to_dict(strip)},
        "floating": floating or {},
        "hidden": [],
    }


def test_custom_is_the_edit_sheets_own_arrangement() -> None:
    page = lt.rows_to_page([["skills", "abilities"], ["powers"]])
    strip = lt.Leaf(("dice",))
    layout = custom_layout(_arrangement(page, strip=strip), BASE_KEYS)

    assert lt.keys(layout.page) == ["skills", "abilities", "powers"]
    assert lt.keys(layout.strip) == ["dice"]
    assert layout.edge == "left"


def test_custom_gives_a_floated_block_a_row_and_leaves_a_closed_one_out() -> None:
    page = lt.rows_to_page([["skills"]])
    layout = custom_layout(_arrangement(page, floating={"powers": {}}), BASE_KEYS)

    assert lt.keys(layout.page) == ["skills", "powers"]
    assert "abilities" not in layout.keys()


def test_custom_falls_back_to_standard_on_an_arrangement_it_cannot_read() -> None:
    layout = custom_layout({"page": {"type": "nonsense"}}, BASE_KEYS)

    assert sorted(layout.keys()) == sorted(BASE_KEYS)


def test_the_printable_page_drops_what_does_not_belong_on_paper() -> None:
    layout = standard_layout(BASE_KEYS)
    printable = set(BASE_KEYS) - {"dice", "scene", "powers"}

    page = printable_page(layout, printable)

    assert "powers" not in lt.keys(page)
    assert "skills" in lt.keys(page)


# -- the roll chips' big numbers -------------------------------------------------------


def test_an_attack_reads_as_its_bonus() -> None:
    assert split_roll_label(RollSpec("10 vs. Defense", 10)) == ("+10", "vs. Defense")
    assert split_roll_label(RollSpec("-2 vs. Defense", -2)) == ("-2", "vs. Defense")


def test_a_save_reads_as_its_dc() -> None:
    save = RollSpec("Toughness vs. 16", 0, 16, rolled_by_target=True)
    assert split_roll_label(save) == ("DC 16", "Toughness")
    area = RollSpec("Dodge vs. DC 18", 0, 18, rolled_by_target=True)
    assert split_roll_label(area) == ("DC 18", "Dodge")


def test_a_prefixed_line_keeps_its_prefix_and_its_qualifier() -> None:
    spec = RollSpec("Damage: 10 vs. Defense (area; Dodge for half)", 10)
    assert split_roll_label(spec) == ("+10", "Damage: vs. Defense (area; Dodge for half)")


def test_a_line_with_no_number_in_it_keeps_all_of_its_words() -> None:
    assert split_roll_label(RollSpec("opposed vs. Nullify", 8)) == ("+8", "opposed vs. Nullify")
    check = RollSpec("Check Required (Acrobatics)", 0, 15)
    assert split_roll_label(check) == ("DC 15", "Check Required (Acrobatics)")

"""The Equipment block: grouping, drag reorder, the wear switch, and the budget bar.

Real drag-and-drop is unreliable headless, so — like ``test_powers_section.py`` — these
drive the public seams the drop handlers delegate to (``_on_item_moved`` /
``_on_group_moved``) and the group list's own admission rule, and assert on the
resulting ``Character.equipment``.
"""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QApplication, QLabel

from mm_companion.core.character import AdvantageSelection, Character
from mm_companion.core.data_loader import load_game_data
from mm_companion.core.powers import Power, PowerEffectInstance
from mm_companion.core.rules import build_item_from_entry, item_superseded
from mm_companion.ui.cards import DraggableCard, NodeList
from mm_companion.ui.character_sheet import CharacterSheet
from mm_companion.ui.sections.equipment import (
    EQUIPMENT_GROUP_MIME,
    EQUIPMENT_MIME,
    EquipmentSection,
)
from mm_companion.ui.sections.equipment_picker import EquipmentPickerDialog, cost_text


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


@pytest.fixture(scope="module")
def data():
    return load_game_data()


def _hero(data, *catalog_ids: str) -> Character:
    """A character with 5 ranks of Equipment and the named gear already picked."""
    char = Character.new_default(data)
    char.advantages.append(AdvantageSelection(name="Equipment", rank=5))
    for catalog_id in catalog_ids:
        char.equipment.append(build_item_from_entry(data.equipment_catalog()[catalog_id], data))
    return char


def _section(data, char) -> EquipmentSection:
    return EquipmentSection(data, char)


def _ids(char) -> list[str]:
    return [item.catalog_id for item in char.equipment]


def _labels(widget) -> list[str]:
    return [label.text() for label in widget.findChildren(QLabel)]


def _group_list(section, category: str) -> NodeList:
    """The drop list holding one category's item cards."""
    return next(node for node in section.findChildren(NodeList) if node.parent_id == category)


# -- grouping -------------------------------------------------------------------------


def test_items_are_grouped_by_category_in_the_rulesets_order(qapp, data) -> None:
    # Picked armour-first; the block still draws weapons first, because that is the
    # order _meta.equipmentCategories declares.
    char = _hero(data, "leather_armor", "sword")
    section = _section(data, char)

    cards = section._groups_host.findChildren(DraggableCard)
    groups = [c.node_id for c in cards if c.node_id in {"armor", "close_weapon"}]
    assert groups == ["close_weapon", "armor"]


def test_the_characters_own_group_order_wins(qapp, data) -> None:
    char = _hero(data, "sword", "leather_armor")
    char.equipment_group_order = ["armor", "close_weapon"]
    section = _section(data, char)

    assert section._ordered_categories(section._grouped_items()) == ["armor", "close_weapon"]


def test_a_category_the_ruleset_does_not_name_still_gets_a_group(qapp, data) -> None:
    """A mod may ship an item before it ships a heading for it — it must not vanish."""
    char = _hero(data, "sword")
    char.equipment[0].category = "gizmos"
    section = _section(data, char)

    assert section._ordered_categories(section._grouped_items()) == ["gizmos"]
    assert section._category_title("gizmos") == "gizmos"


# -- drag: reorder within a group, reorder the groups ---------------------------------


def test_dragging_an_item_reorders_it_within_its_group(qapp, data) -> None:
    char = _hero(data, "sword", "axe", "club")
    section = _section(data, char)
    club = char.equipment[2]

    section._on_item_moved(club.id, "close_weapon", 0)

    assert _ids(char) == ["club", "sword", "axe"]


def test_dragging_a_group_reorders_the_groups_and_is_remembered(qapp, data) -> None:
    char = _hero(data, "sword", "leather_armor")
    section = _section(data, char)

    section._on_group_moved("armor", "", 0)

    assert char.equipment_group_order[:2] == ["armor", "close_weapon"]
    # The flat model list is laid out group by group, so the order persists.
    assert _ids(char) == ["leather_armor", "sword"]


def test_a_group_order_survives_the_group_being_emptied(qapp, data) -> None:
    char = _hero(data, "sword", "leather_armor")
    section = _section(data, char)
    section._on_group_moved("armor", "", 0)

    section._remove_item(char.equipment[0])  # the armour

    assert "armor" in char.equipment_group_order


def test_an_item_cannot_be_dragged_into_another_groups_list(qapp, data) -> None:
    """The category is a rules fact, so the armour list refuses a weapon — and shows it."""
    char = _hero(data, "sword", "leather_armor")
    section = _section(data, char)
    sword = next(i for i in char.equipment if i.catalog_id == "sword")

    armor_list = _group_list(section, "armor")
    assert armor_list._accepts(sword.id) is False
    assert armor_list._refuses(sword.id) is True
    assert armor_list._drops.state == "reject"

    # And the model is unmoved even if a drop somehow arrived.
    section._on_item_moved(sword.id, "armor", 0)
    assert _ids(char) == ["sword", "leather_armor"]


def test_the_two_boards_use_different_drag_formats(qapp, data) -> None:
    """An item drag must never resolve against the group board, or the reverse."""
    char = _hero(data, "sword")
    section = _section(data, char)

    assert section._groups_host._mime == EQUIPMENT_GROUP_MIME
    weapons = _group_list(section, "close_weapon")
    assert weapons._mime == EQUIPMENT_MIME
    assert EQUIPMENT_MIME != EQUIPMENT_GROUP_MIME


def test_neither_board_offers_to_combine_two_cards(qapp, data) -> None:
    """Powers group by drag; equipment groups itself, so a drop is always a reorder."""
    char = _hero(data, "sword", "axe")
    section = _section(data, char)
    weapons = _group_list(section, "close_weapon")

    assert section._groups_host._combinable is False
    assert weapons._combinable is False
    assert all(kind == "reorder" for kind, *_ in (weapons._target(y) for y in range(0, 200, 7)))


# -- the wear switch ------------------------------------------------------------------


def test_clicking_a_card_stows_the_item_and_dims_it(qapp, data) -> None:
    char = _hero(data, "leather_armor")
    section = _section(data, char)
    item = char.equipment[0]

    section._toggle_worn(item)

    assert item.worn is False
    card = next(c for c in section.findChildren(DraggableCard) if c.node_id == item.id)
    assert card.off_progress() == pytest.approx(1.0)


def test_every_worn_item_applies_there_is_no_exclusivity(qapp, data) -> None:
    char = _hero(data, "leather_armor", "sword")
    section = _section(data, char)

    section._toggle_worn(char.equipment[0])

    assert [i.worn for i in char.equipment] == [False, True]


def test_wearing_is_a_runtime_change_not_a_build_edit(qapp, data) -> None:
    char = _hero(data, "leather_armor")
    section = _section(data, char)
    edits: list[int] = []
    runtime: list[int] = []
    section.changed.connect(lambda: edits.append(1))
    section.runtimeChanged.connect(lambda: runtime.append(1))

    section._toggle_worn(char.equipment[0])

    assert runtime == [1]
    assert edits == []


def test_wearing_stays_available_in_the_locked_read_only_sheet(qapp, data) -> None:
    char = _hero(data, "leather_armor")
    section = _section(data, char)
    section.set_locked(True)
    item = char.equipment[0]

    card = next(c for c in section.findChildren(DraggableCard) if c.node_id == item.id)
    assert card.is_clickable() is True
    assert section._add_button.isVisibleTo(section) is False


# -- the budget bar -------------------------------------------------------------------


def test_the_budget_bar_reads_spend_over_budget(qapp, data) -> None:
    char = _hero(data, "chain_mail")  # 7 EP against a rank-5 budget of 25
    section = _section(data, char)

    assert "7 / 25 EP" in _labels(section._budget)
    assert section._budget._warning.isVisibleTo(section._budget) is False


def test_overspending_warns_and_never_blocks(qapp, data) -> None:
    char = Character.new_default(data)
    char.advantages.append(AdvantageSelection(name="Equipment", rank=1))  # 5 EP
    for _ in range(3):
        char.equipment.append(build_item_from_entry(data.equipment_catalog()["chain_mail"], data))
    section = _section(data, char)

    assert "21 / 5 EP" in _labels(section._budget)
    assert section._budget._warning.toolTip()


def test_the_budget_follows_the_equipment_advantage(qapp, data) -> None:
    char = _hero(data, "leather_armor")
    section = _section(data, char)
    char.advantages[-1].rank = 2

    section.refresh()  # what the bus calls on a facts-changed

    assert "1 / 10 EP" in _labels(section._budget)


def test_the_two_currencies_stay_apart(qapp, data) -> None:
    """Gear costs Equipment Points; the sheet's Power Point total must not move."""
    from mm_companion.core.rules import power_points_spent

    char = _hero(data)
    before = power_points_spent(char, data)
    char.equipment.append(build_item_from_entry(data.equipment_catalog()["full_plate"], data))

    assert power_points_spent(char, data) == before


# -- the no-stacking annotation -------------------------------------------------------


def test_an_outclassed_item_says_what_beat_it(qapp, data) -> None:
    char = _hero(data, "leather_armor", "chain_mail")  # Protection 1 and Protection 3
    section = _section(data, char)
    leather = char.equipment[0]

    beaten = item_superseded(leather, char, data)
    assert [b.beaten_by for b in beaten] == ["Chain-Mail"]
    assert any("superseded by Chain-Mail" in text for text in _labels(section))


def test_a_power_beats_gear_and_the_card_names_it(qapp, data) -> None:
    char = _hero(data, "chain_mail")
    char.powers.append(
        Power(name="Force Field", effects=[PowerEffectInstance("protection", rank=8)])
    )
    section = _section(data, char)

    assert any("superseded by Force Field" in text for text in _labels(section))


def test_an_item_that_is_granting_its_bonus_says_nothing(qapp, data) -> None:
    char = _hero(data, "chain_mail")
    section = _section(data, char)

    assert item_superseded(char.equipment[0], char, data) == ()
    assert not any("superseded" in text for text in _labels(section))


def test_a_stowed_item_supersedes_nothing(qapp, data) -> None:
    """It is already dimmed, which is the honest explanation for a stowed item."""
    char = _hero(data, "leather_armor", "chain_mail")
    char.equipment[0].worn = False

    assert item_superseded(char.equipment[0], char, data) == ()


# -- the enhancement columns ----------------------------------------------------------


def test_worn_gear_shows_in_the_resistances_enhancement_column(qapp, data) -> None:
    """The column was powers-only; gear raised the total without appearing in it."""
    char = _hero(data, "chain_mail")  # Protection 3
    sheet = CharacterSheet(data, char)

    cell = sheet.resistances._resistance_enh["TOUGHNESS"]
    bought = sheet.resistances._resistances["TOUGHNESS"].value()
    assert cell.text() == f"→ {bought + 3}"
    assert "Chain-Mail" in cell.toolTip()


def test_stowing_gear_takes_it_back_out_of_that_column(qapp, data) -> None:
    char = _hero(data, "chain_mail")
    sheet = CharacterSheet(data, char)

    sheet.equipment._toggle_worn(char.equipment[0])

    assert sheet.resistances._resistance_enh["TOUGHNESS"].text() == ""


# -- the catalog picker ---------------------------------------------------------------


def test_the_picker_lists_the_whole_catalog_grouped_and_priced(qapp, data) -> None:
    picker = EquipmentPickerDialog(data)

    headings = [
        picker._tree.topLevelItem(i).text(0) for i in range(picker._tree.topLevelItemCount())
    ]
    assert "Close Weapons" in headings
    assert "Armor" in headings

    rows = sum(
        picker._tree.topLevelItem(i).childCount() for i in range(picker._tree.topLevelItemCount())
    )
    assert rows == len(data.equipment)


def test_the_picker_filters_on_every_word(qapp, data) -> None:
    picker = EquipmentPickerDialog(data)
    picker._apply_filter("chain mail")

    shown = [
        child.text(0)
        for i in range(picker._tree.topLevelItemCount())
        for child in (
            picker._tree.topLevelItem(i).child(j)
            for j in range(picker._tree.topLevelItem(i).childCount())
        )
        if not child.isHidden()
    ]
    assert shown == ["Chain-Mail"]


def test_choosing_from_the_picker_puts_the_item_on_the_character(qapp, data) -> None:
    char = _hero(data)
    section = _section(data, char)
    edits: list[int] = []
    section.changed.connect(lambda: edits.append(1))

    section._add_entry(data.equipment_catalog()["sword"])

    assert _ids(char) == ["sword"]
    assert char.equipment[0].category == "close_weapon"
    assert edits == [1]


def test_a_per_rank_entry_reads_as_a_price_per_rank(data) -> None:
    entry = data.equipment_catalog()["armor_cloth"]
    assert cost_text(entry, "EP") == "1 EP / rank"
    assert cost_text(data.equipment_catalog()["sword"], "EP") == "3 EP"


def test_an_item_with_no_printed_price_shows_its_note(data) -> None:
    entry = data.equipment_catalog()["omni_equipment"]
    assert entry.cost is None
    assert cost_text(entry, "EP") == entry.cost_note


# -- chrome ---------------------------------------------------------------------------


def test_removing_an_item_is_a_build_edit(qapp, data) -> None:
    char = _hero(data, "sword")
    section = _section(data, char)
    edits: list[int] = []
    section.changed.connect(lambda: edits.append(1))

    section._remove_item(char.equipment[0])

    assert char.equipment == []
    assert edits == [1]


def test_a_homerule_item_is_badged(qapp, data) -> None:
    char = _hero(data, "leather_armor")
    section = _section(data, char)
    assert section._is_homerule(char.equipment[0]) is False

    char.equipment[0].stacks = True
    assert section._is_homerule(char.equipment[0]) is True

    char.equipment[0].stacks = False
    char.equipment[0].ep_override = 2
    assert section._is_homerule(char.equipment[0]) is True


def test_an_empty_block_says_so(qapp, data) -> None:
    section = _section(data, _hero(data))
    assert section._empty.isVisibleTo(section) is True

    section._add_entry(data.equipment_catalog()["sword"])
    assert section._empty.isVisibleTo(section) is False

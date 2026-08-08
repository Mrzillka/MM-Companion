"""The Equipment block: grouping, drag reorder, the wear switch, and the budget bar.

Real drag-and-drop is unreliable headless, so — like ``test_powers_section.py`` — these
drive the public seams the drop handlers delegate to (``_on_item_moved`` /
``_on_group_moved``) and the group list's own admission rule, and assert on the
resulting ``Character.equipment``.
"""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QApplication, QDialog, QLabel, QPushButton

from mm_companion.core.character import AdvantageSelection, Character
from mm_companion.core.data_loader import load_game_data
from mm_companion.core.equipment import (
    PLATFORM_INSTALLATION,
    PLATFORM_VEHICLE,
    EquipmentItem,
)
from mm_companion.core.powers import Power, PowerEffectInstance
from mm_companion.core.rules import (
    PIN_EQUIPMENT,
    PinRef,
    apply_platform,
    build_item_from_entry,
    item_ep_cost,
    item_superseded,
    new_platform,
    platform_rules_category,
    resolve_pin,
)
from mm_companion.ui.cards import DraggableCard, NodeList, RollLine, RollsFooter
from mm_companion.ui.character_sheet import CharacterSheet
from mm_companion.ui.platform_editor import PlatformEditorDialog, platform_kind_title
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
    # Vehicles are catalog entries too, so they are simply another group.
    assert "Vehicles" in headings

    rows = sum(
        picker._tree.topLevelItem(i).childCount() for i in range(picker._tree.topLevelItemCount())
    )
    assert rows == len(data.equipment_catalog())


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


# -- the dice footer (Phase 6) --------------------------------------------------------


def _roll_lines(card) -> list[RollLine]:
    return card.findChildren(RollLine)


def _card(section, item) -> DraggableCard:
    return next(c for c in section.findChildren(DraggableCard) if c.node_id == item.id)


def test_a_weapon_card_carries_a_dice_footer(qapp, data) -> None:
    """The powers card's footer, from the same specs — an item wraps a real Power."""
    char = _hero(data, "sword")
    section = _section(data, char)

    lines = _roll_lines(_card(section, char.equipment[0]))

    # An attack and the save it forces are two rolls made by two people: a line each.
    assert len(lines) == 2
    # Only the attack is the wielder's. The save reaches whoever makes it as the
    # follow-up chip on the attack's history card.
    assert [line.is_rollable() for line in lines] == [True, False]


def test_gear_that_rolls_nothing_has_no_footer(qapp, data) -> None:
    """No placeholder line, and no rule above a footer that is not there."""
    char = _hero(data, "leather_armor")
    section = _section(data, char)

    assert _roll_lines(_card(section, char.equipment[0])) == []


def test_a_stowed_weapon_keeps_its_footer(qapp, data) -> None:
    """Drawing a sheathed sword is one motion; a roll is not a build fact."""
    char = _hero(data, "sword")
    char.equipment[0].worn = False
    section = _section(data, char)

    assert len(_roll_lines(_card(section, char.equipment[0]))) == 2


def test_clicking_a_roll_line_asks_for_the_roll_and_changes_nothing(qapp, data) -> None:
    char = _hero(data, "sword")
    section = _section(data, char)
    item = char.equipment[0]
    seen: list = []
    edits: list[int] = []
    section.rollRequested.connect(seen.append)
    section.changed.connect(lambda: edits.append(1))
    section.runtimeChanged.connect(lambda: edits.append(1))

    line = next(line for line in _roll_lines(_card(section, item)) if line.is_rollable())
    line.clicked.emit()

    assert len(seen) == 1 and seen[0].dc is None  # the attack, not the save
    assert edits == []  # rolling is neither an edit nor a wear toggle
    assert item.worn is True  # and the click did not reach the card's switch


def test_a_roll_line_pins_by_naming_the_item(qapp, data) -> None:
    """A pin is a reference: the item's own id, plus which of its rolls."""
    char = _hero(data, "sword")
    section = _section(data, char)
    section.set_pin_target(True)
    item = char.equipment[0]

    footer = _card(section, item).findChild(RollsFooter)
    ref = footer._pin_ref(0)

    assert ref == PinRef(PIN_EQUIPMENT, item.id, 0)
    assert not resolve_pin(char, data, ref).missing


def test_no_pin_menu_without_a_card_beside_the_sheet(qapp, data) -> None:
    char = _hero(data, "sword")
    section = _section(data, char)
    asked: list = []
    section.pinRequested.connect(asked.append)

    footer = _card(section, char.equipment[0]).findChild(RollsFooter)
    footer._show_pin_menu(footer, footer.rect().center(), 0)  # must not raise or emit

    assert asked == []


def test_wearing_a_glider_reaches_the_speed_readout(qapp, data) -> None:
    """End to end over the bus: the wear toggle raises DERIVED_CHANGED, and the
    System block recomputes its Speed lines off it."""
    char = _hero(data, "glider")
    sheet = CharacterSheet(data, char)

    assert "Glider" in sheet.system_info._speed._lines_label.text()

    sheet.equipment._toggle_worn(char.equipment[0])

    assert "Glider" not in sheet.system_info._speed._lines_label.text()


# --- Phase 8: accessories and the breakage warning on a card ---------------------------


def _fit(section, char, host_id: str, accessory_id: str):
    """Fit the named accessory to the named host through the block's own seam."""
    host = next(i for i in char.equipment if i.catalog_id == host_id)
    accessory = next(i for i in char.equipment if i.catalog_id == accessory_id)
    section._attach(host, accessory)
    return host, accessory


def test_fitting_an_accessory_takes_its_card_off_the_board(qapp, data) -> None:
    """It has stopped being a thing you own and become part of one."""
    char = _hero(data, "rifle", "laser_sight")
    section = _section(data, char)

    _fit(section, char, "rifle", "laser_sight")

    assert _ids(char) == ["rifle"]
    assert [a.catalog_id for a in char.equipment[0].accessories] == ["laser_sight"]


def test_a_host_card_lists_what_is_fitted_to_it(qapp, data) -> None:
    char = _hero(data, "rifle", "laser_sight")
    section = _section(data, char)
    host, _ = _fit(section, char, "rifle", "laser_sight")

    labels = _labels(section._make_card(host))

    assert any("Laser Sight" in text for text in labels)


def test_a_fitted_accessory_moves_the_hosts_attack_and_its_price(qapp, data) -> None:
    """Both readings on the card change, because both come off the effective build."""
    char = _hero(data, "rifle", "laser_sight")
    section = _section(data, char)
    rifle = char.equipment[0]
    before = _labels(section._make_card(rifle))

    _fit(section, char, "rifle", "laser_sight")

    card = section._make_card(rifle)
    assert _labels(card) != before
    assert any("2 vs. Defense" in text for text in _labels(card))
    assert "9 EP" in _labels(card)  # the rifle's printed 8 plus the sight's 1


def test_taking_an_accessory_off_puts_its_card_back(qapp, data) -> None:
    char = _hero(data, "rifle", "laser_sight")
    section = _section(data, char)
    host, accessory = _fit(section, char, "rifle", "laser_sight")

    section._detach(host, accessory)

    assert sorted(_ids(char)) == ["laser_sight", "rifle"]
    assert host.accessories == []


def test_removing_a_fitted_accessory_reaches_into_its_host(qapp, data) -> None:
    """It is not in the flat list any more, so the plain filter would walk past it."""
    char = _hero(data, "rifle", "laser_sight")
    section = _section(data, char)
    host, accessory = _fit(section, char, "rifle", "laser_sight")

    section._remove_item(accessory)

    assert _ids(char) == ["rifle"]
    assert host.accessories == []


def test_an_item_grants_its_advantages_on_its_own_card(qapp, data) -> None:
    """Never in the Advantages block: the axe's Improved Smash is the axe's."""
    char = _hero(data, "axe")
    section = _section(data, char)

    labels = _labels(section._make_card(char.equipment[0]))

    assert "Grants Improved Smash" in labels
    assert [selection.name for selection in char.advantages] == ["Equipment"]


def test_an_accessory_grant_is_drawn_on_the_host_and_named_after_the_accessory(qapp, data) -> None:
    char = _hero(data, "rifle", "targeting_scope")
    section = _section(data, char)
    host, _ = _fit(section, char, "rifle", "targeting_scope")

    assert "Grants Improved Aim (Targeting Scope)" in _labels(section._make_card(host))


def test_a_card_warns_when_the_wielder_will_break_the_weapon(qapp, data) -> None:
    char = _hero(data, "sword")
    section = _section(data, char)
    char.abilities["STR"] = 3

    assert not any(
        "break on use" in text for text in _labels(section._make_card(char.equipment[0]))
    )

    char.abilities["STR"] = 12

    warnings = [t for t in _labels(section._make_card(char.equipment[0])) if "break on use" in t]
    assert len(warnings) == 1
    assert "Toughness 7" in warnings[0]


# -- vehicles -------------------------------------------------------------------------


def test_a_vehicle_card_shows_its_platform_traits(qapp, data) -> None:
    """Five bought traits where an item shows its game terms — that is what a vehicle is."""
    char = _hero(data, "tank")
    section = _section(data, char)

    labels = _labels(section._make_card(char.equipment[0]))

    assert "Strength:" in labels and "Toughness:" in labels
    assert "Defense Class:" in labels
    assert "14 moving / 8 stationary" in labels
    assert "12 (Impervious 4)" in labels
    # And not the effect table an item's card leads with.
    assert "Range:" not in labels


def test_a_vehicle_keeps_its_dice_footer_with_the_weapons_named(qapp, data) -> None:
    char = _hero(data, "tank")
    section = _section(data, char)

    footer = _card(section, char.equipment[0]).findChild(RollsFooter)
    lines = [line.text() for line in footer.findChildren(QLabel)]

    assert any(text.startswith("Cannon:") for text in lines)
    assert any(text.startswith("Heavy machine gun:") for text in lines)


def test_a_vehicle_files_itself_under_the_vehicles_group(qapp, data) -> None:
    char = _hero(data, "tank", "sword")
    section = _section(data, char)

    assert section._grouped_items()["vehicle"][0].catalog_id == "tank"
    assert section._category_title("vehicle") == "Vehicles"


def test_parking_a_vehicle_reads_as_parking_not_as_stowing(qapp, data) -> None:
    """Same switch, honest wording: a parked car is not an unworn jacket."""
    char = _hero(data, "tank", "sword")
    section = _section(data, char)

    tank, sword = char.equipment[0], char.equipment[1]
    assert "park" in _card(section, tank).toolTip()
    assert "stow" in _card(section, sword).toolTip()


# -- installations and custom platforms -------------------------------------------------


def _platform(data, kind: str) -> EquipmentItem:
    """A blank platform, the way the block's Create Platform button makes one."""
    spec = new_platform(kind, data)
    item = EquipmentItem(category=platform_rules_category(kind, data), platform=spec)
    item.build.name = f"New {platform_kind_title(kind, data)}"
    apply_platform(item, spec, data)
    return item


def test_an_installation_card_shows_its_two_traits_and_its_features(qapp, data) -> None:
    """A far shorter grid than a vehicle's, because an installation is a shorter thing."""
    char = _hero(data, "moon_base")
    section = _section(data, char)

    labels = _labels(section._make_card(char.equipment[0]))

    assert "Size:" in labels and "Toughness:" in labels and "Features:" in labels
    assert "Defense Class:" not in labels  # a base does not dodge
    assert "Range:" not in labels  # nor is it a bundle of effects


def test_opening_a_base_reads_as_opening_not_as_wearing(qapp, data) -> None:
    """The same switch, in the third set of words it needs: worn / boarded / opened."""
    char = _hero(data, "moon_base", "tank", "sword")
    section = _section(data, char)

    base, tank, sword = char.equipment[0], char.equipment[1], char.equipment[2]
    assert "close" in _card(section, base).toolTip()
    assert "park" in _card(section, tank).toolTip()
    assert "stow" in _card(section, sword).toolTip()


def test_editing_a_platforms_traits_leaves_it_parked(qapp, data, monkeypatch) -> None:
    """The working copy is a deepcopy, not a save round trip.

    ``to_dict`` leaves runtime state out on purpose, so ``from_dict`` handed the editor
    a vehicle at its defaults — boarded, throttle wide open. A car sitting still at
    Speed 2 came back moving flat out, with a different Defense Class, from having had
    a Feature added to it.
    """
    char = _hero(data, "tank")
    tank = char.equipment[0]
    tank.worn = False
    tank.current_speed = 2
    section = _section(data, char)

    captured: dict = {}

    def _accept(self):
        captured["item"] = self.item
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(PlatformEditorDialog, "exec", _accept)
    section._edit_platform(tank)

    edited = char.equipment[0]
    assert edited.worn is False
    assert edited.current_speed == 2
    assert edited.id == tank.id


def test_a_rebuild_forgets_the_trait_hosts_it_deleted(qapp, data) -> None:
    """They are widgets, and the rebuild deletes them — a stale entry is a dead pointer."""
    char = _hero(data, "tank")
    section = _section(data, char)
    assert section._trait_hosts

    char.equipment.clear()
    section._rebuild_list()

    assert section._trait_hosts == {}


def test_gear_with_a_printed_modifier_and_no_effect_still_says_what_it_is(qapp, data) -> None:
    """Armour Cloth is Hardened 1 and nothing else — its card used to be blank."""
    char = _hero(data, "armor_cloth")
    section = _section(data, char)

    block = section._printed_modifiers_block(char.equipment[0])
    assert block is not None
    assert any("Hardened" in text for text in _labels(block))


def test_gear_with_effects_prints_no_modifier_line(qapp, data) -> None:
    """The block is the *substitute* for a terms table, not an addition to one."""
    char = _hero(data, "sword")
    section = _section(data, char)

    assert section._printed_modifiers_block(char.equipment[0]) is None


def test_a_cross_group_drop_is_refused_over_the_groups_indent(qapp, data) -> None:
    """The 14px margin is part of the list, so the reject wash reaches it.

    It used to be a wrapper *around* the list and no drop target at all: a drag over
    the item cards showed the refusal and a drag two pixels to their left showed
    nothing, which reads as a target that simply did not notice.
    """
    char = _hero(data, "sword", "leather_armor")
    section = _section(data, char)
    armor = _group_list(section, "armor")

    assert armor.layout().contentsMargins().left() == 14
    assert armor._drops is not None


def test_a_vehicle_card_carries_a_throttle_and_an_installation_does_not(qapp, data) -> None:
    """The one number that changes within a round, and the card it belongs on."""
    char = _hero(data, "tank", "moon_base", "sword")
    section = _section(data, char)
    tank, base, sword = char.equipment[0], char.equipment[1], char.equipment[2]

    assert section._throttle_row(tank) is not None
    assert section._throttle_row(base) is None
    assert section._throttle_row(sword) is None


def test_the_throttle_restates_the_defense_class_without_rebuilding_the_card(qapp, data) -> None:
    """It must not: the spin box being dragged is a child of the card it would destroy."""
    char = _hero(data, "tank")
    section = _section(data, char)
    tank = char.equipment[0]
    card = _card(section, tank)
    assert "14 moving / 8 stationary" in _labels(card)

    dirtied = []
    section.changed.connect(lambda: dirtied.append(True))
    section._on_throttle(tank, 2)

    assert card is _card(section, tank)  # the same card, restated in place
    assert "10 moving / 8 stationary" in _labels(card)
    assert not dirtied  # driving slowly is not a build edit


def test_the_platform_editor_prices_the_whole_item(qapp, data) -> None:
    """Traits, Features and the movement effect — the number the card will show.

    The dialog computes nothing itself: it applies the spec to a working item and asks
    the rules layer, which is what stops the two numbers ever disagreeing.
    """
    char = _hero(data)
    item = _platform(data, PLATFORM_VEHICLE)
    dialog = PlatformEditorDialog(data, char, item)

    dialog._size.setValue(2)
    dialog._strength.setValue(10)
    dialog._toughness.setValue(12)
    dialog._defense.setValue(-2)  # the penalty its size confers, left unbought
    dialog._speed.setValue(6)
    dialog.accept()

    assert dialog.item.platform.size == 2
    assert item_ep_cost(dialog.item, data, char) == 17  # 11 of traits + Speed 6
    assert dialog._cost.text() == "17 EP"


def test_choosing_a_size_carries_the_traits_it_sets(qapp, data) -> None:
    """Size is chosen first, and you keep what you bought above each baseline."""
    char = _hero(data)
    dialog = PlatformEditorDialog(data, char, _platform(data, PLATFORM_VEHICLE))
    assert dialog._strength.value() == 2  # what size 0 gives
    dialog._strength.setValue(4)  # two points of bought Strength

    dialog._size.setValue(3)

    assert dialog._strength.value() == 10  # the new baseline of 8, plus the two bought
    assert dialog._strength.minimum() == 8  # and cannot be typed back under it
    assert dialog._toughness.value() == 8
    # The Defense penalty follows too, rather than leaving three points quietly bought.
    assert dialog._defense.value() == -3
    assert item_ep_cost(dialog.item, data, char) == 3 + 2 + 1  # size, Strength, Speed 1


def test_an_editor_opened_on_a_printed_platform_leaves_it_alone(qapp, data) -> None:
    """A sailboat's printed Toughness is one under its size's, and that is not ours to fix.

    Correcting it on open would re-price a boat someone opened to add a Feature to.
    """
    char = _hero(data, "sailboat")
    dialog = PlatformEditorDialog(data, char, char.equipment[0])

    assert dialog._toughness.value() == 6
    dialog.accept()
    assert dialog.item.platform.toughness == 6


def test_the_editor_shows_the_vehicle_modifiers_in_the_other_currency(qapp, data) -> None:
    """Durable prices the Equipment *advantage* in Power Points, and says so on its own line."""
    char = _hero(data)
    dialog = PlatformEditorDialog(data, char, _platform(data, PLATFORM_VEHICLE))
    assert not dialog._advantage_cost.text()

    dialog._modifier_boxes["durable"].setChecked(True)

    assert "PP" in dialog._advantage_cost.text()
    assert "EP" in dialog._cost.text()
    assert "PP" not in dialog._cost.text()  # the two never mix


def test_an_installation_editor_warns_about_its_own_cap_pair(qapp, data) -> None:
    """Toughness to twice PL is fine; Impervious past PL is not (§6)."""
    char = _hero(data)
    dialog = PlatformEditorDialog(data, char, _platform(data, PLATFORM_INSTALLATION))

    dialog._toughness.setValue(char.power_level * 2)
    assert not dialog._warnings.text()

    dialog._toughness.setValue(char.power_level * 2 + 1)
    assert "installation cap" in dialog._warnings.text()

    dialog._toughness.setValue(char.power_level * 2)
    dialog._impervious.setValue(char.power_level + 1)
    assert "Impervious" in dialog._warnings.text()


def test_an_installations_features_are_bought_in_the_editor(qapp, data) -> None:
    """One point each, and a repeatable one bought twice reads as two ranks."""
    char = _hero(data)
    dialog = PlatformEditorDialog(data, char, _platform(data, PLATFORM_INSTALLATION))

    dialog._feature_boxes["laboratory"].setValue(1)
    dialog._feature_boxes["concealed"].setValue(2)
    dialog.accept()

    assert dialog.item.platform.features.count("concealed") == 2
    assert item_ep_cost(dialog.item, data, char) == 3
    assert dialog._feature_boxes["laboratory"].maximum() == 1  # not repeatable


def test_a_platform_card_offers_both_of_its_editors(qapp, data) -> None:
    """Traits and effects are two different things and live in two different editors."""
    char = _hero(data, "tank", "sword")
    section = _section(data, char)

    tank_edit = [b for b in _card(section, char.equipment[0]).findChildren(QPushButton)]
    sword_edit = [b for b in _card(section, char.equipment[1]).findChildren(QPushButton)]

    assert any("traits" in b.toolTip() for b in tank_edit)
    assert not any("traits" in b.toolTip() for b in sword_edit)

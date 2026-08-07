"""The Power Constructor in **gear mode**: building and editing one piece of equipment.

There is only ever one builder — an ``EquipmentItem`` wraps a real ``Power``, so the
palette, the canvas and every Dev-mode override work on gear untouched. What these
cover is the handful of places where gear is *not* a power: the currency it is priced
in, where a hand-set price is stored, the group a card files under, the no-stacking
opt-out, and the rule that an item needs a name rather than an effect.

The block's own side of it — "Create Custom Item", a card's ✎, and the deep-copy
contract an edit honours — is here too, since the two halves are one feature.
"""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QApplication, QMessageBox

from mm_companion.core.character import AdvantageSelection, Character
from mm_companion.core.data_loader import load_game_data
from mm_companion.core.equipment import EquipmentItem
from mm_companion.core.rules import build_item_from_entry, item_ep_cost, item_is_stock
from mm_companion.ui.power_constructor import PowerConstructorWindow
from mm_companion.ui.sections.equipment import EquipmentSection


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


@pytest.fixture(scope="module")
def data():
    return load_game_data()


def _hero(data, *catalog_ids: str) -> Character:
    char = Character.new_default(data)
    char.advantages.append(AdvantageSelection(name="Equipment", rank=5))
    for catalog_id in catalog_ids:
        char.equipment.append(build_item_from_entry(data.equipment_catalog()[catalog_id], data))
    return char


def _gear_window(data, char=None, item=None) -> PowerConstructorWindow:
    if item is not None:
        return PowerConstructorWindow(data, character=char, item=item)
    return PowerConstructorWindow(data, character=char, gear=True)


# -- what gear mode changes -----------------------------------------------------------


def test_a_new_item_opens_as_the_equipment_constructor(qapp, data) -> None:
    window = _gear_window(data)

    assert window.windowTitle() == "Equipment Constructor"
    assert window._save_button.text() == "Save Item"
    # The build is the item's own, so every brick dropped on the canvas lands on it.
    assert window.power is window.item.build


def test_an_edited_item_opens_as_edit_equipment(qapp, data) -> None:
    char = _hero(data, "sword")
    window = _gear_window(data, char, char.equipment[0])

    assert window.windowTitle() == "Edit Equipment"
    assert window._save_button.text() == "Save Changes"
    assert window._name.text() == "Sword"


def test_the_running_total_reads_equipment_points(qapp, data) -> None:
    """And it is the item's price, not its build's — a stock sword shows the book's 3."""
    char = _hero(data, "sword")
    window = _gear_window(data, char, char.equipment[0])

    assert window._cost.text() == "Total cost: 3 EP"


def test_a_power_still_reads_power_points(qapp, data) -> None:
    """The other currency is untouched: gear mode is the exception, not the new rule."""
    window = PowerConstructorWindow(data)

    assert window._cost.text().endswith("PP")


def test_every_cost_in_the_window_agrees_on_the_currency(qapp, data) -> None:
    """The effect card's own formula included — one reading "= 3 PP" under a total
    reading "3 EP" is two answers to the same question."""
    char = _hero(data, "sword")
    window = _gear_window(data, char, char.equipment[0])

    assert window.canvas.cards[0]._cost.text().endswith("= 3 EP")


def test_a_powers_effect_card_still_prices_itself_in_power_points(qapp, data) -> None:
    window = PowerConstructorWindow(data)
    card = window.canvas.add_effect("damage")

    assert card._cost.text().endswith("PP")


def test_the_group_combo_sets_the_items_category(qapp, data) -> None:
    window = _gear_window(data)
    window._select_category("armor")

    assert window.item.category == "armor"


def test_a_custom_item_lands_in_a_real_group_untouched(qapp, data) -> None:
    """The combo opens on something, and that something is what the item gets — an
    item saved without the combo ever being clicked must still have a home."""
    window = _gear_window(data)

    assert window.item.category == data.equipment_categories[0].id


def test_an_edited_item_opens_on_its_own_group(qapp, data) -> None:
    char = _hero(data, "leather_armor")
    window = _gear_window(data, char, char.equipment[0])

    assert window._category.currentData() == "armor"


def test_the_stacks_box_writes_the_no_stacking_opt_out(qapp, data) -> None:
    window = _gear_window(data)
    assert window.item.stacks is False

    window._stacks.setChecked(True)

    assert window.item.stacks is True


def test_an_items_group_and_stacks_seed_from_the_item_being_edited(qapp, data) -> None:
    char = _hero(data, "leather_armor")
    char.equipment[0].stacks = True
    window = _gear_window(data, char, char.equipment[0])

    assert window._stacks.isChecked() is True


# -- the hand-set price, and the currency it is stored in ------------------------------


def test_a_hand_set_price_is_stored_on_the_item_in_equipment_points(qapp, data) -> None:
    """The whole reason for :class:`CostOverrideTarget`: a price typed here is EP, and
    writing it to ``Power.cost_override`` would hand every caller a PP-labelled lie."""
    char = _hero(data, "sword")
    item = char.equipment[0]
    window = _gear_window(data, char, item)
    window._dev_mode.setChecked(True)

    window._terms._cost_override_check.setChecked(True)
    window._terms._cost_override_spin.setValue(7)

    assert window.item.ep_override == 7
    assert window.item.build.cost_override is None  # the other currency stayed empty
    assert item_ep_cost(window.item, data) == 7


def test_the_override_spin_is_denominated_in_equipment_points(qapp, data) -> None:
    window = _gear_window(data, _hero(data))
    window._dev_mode.setChecked(True)

    assert window._terms._cost_override_spin.suffix() == " EP"


def test_a_powers_override_still_writes_power_points(qapp, data) -> None:
    window = PowerConstructorWindow(data)
    window.canvas.add_effect("damage")
    window._dev_mode.setChecked(True)

    window._terms._cost_override_check.setChecked(True)
    window._terms._cost_override_spin.setValue(4)

    assert window.power.cost_override == 4
    assert window._terms._cost_override_spin.suffix() == " PP"


def test_clearing_the_override_returns_the_item_to_its_derived_price(qapp, data) -> None:
    char = _hero(data, "sword")
    window = _gear_window(data, char, char.equipment[0])
    window._dev_mode.setChecked(True)
    window._terms._cost_override_check.setChecked(True)
    window._terms._cost_override_spin.setValue(7)

    window._terms._cost_override_check.setChecked(False)

    assert window.item.ep_override is None
    assert item_ep_cost(window.item, data) == 3  # the book's printed price again


def test_the_override_starts_at_the_engines_own_answer(qapp, data) -> None:
    """The spin opens on what the item costs, so a homerule price is edited *from* the
    derived one rather than from zero."""
    char = _hero(data, "sword")
    window = _gear_window(data, char, char.equipment[0])
    window._dev_mode.setChecked(True)

    assert window._terms._cost_override_spin.value() == 3


def test_an_item_priced_by_hand_opens_in_dev_mode(qapp, data) -> None:
    char = _hero(data, "sword")
    char.equipment[0].ep_override = 9
    window = _gear_window(data, char, char.equipment[0])

    assert window._dev_mode.isChecked() is True
    assert window._cost.text() == "Total cost: 9 EP (homerule)"


# -- saving ---------------------------------------------------------------------------


def test_an_unnamed_item_is_not_saved(qapp, data, monkeypatch) -> None:
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: None)
    window = _gear_window(data)
    window.canvas.add_effect("damage")
    saved: list = []
    window.itemSaved.connect(saved.append)

    window._save_power()

    assert saved == []


def test_a_named_item_with_no_effects_saves(qapp, data) -> None:
    """Gear with no effects is ordinary — an accessory only modifies its host — so an
    item is asked for a name where a power is asked for an effect."""
    window = _gear_window(data)
    window._name.setText("Scope")
    saved: list = []
    window.itemSaved.connect(saved.append)

    window._save_power()

    assert [item.name for item in saved] == ["Scope"]
    assert not window.isVisible()


def test_saving_an_item_never_emits_powerSaved(qapp, data) -> None:
    """The two signals are how a host tells what it is being handed."""
    window = _gear_window(data)
    window._name.setText("Scope")
    powers: list = []
    items: list = []
    window.powerSaved.connect(powers.append)
    window.itemSaved.connect(items.append)

    window._save_power()

    assert powers == [] and len(items) == 1


# -- the block's side: creating and editing --------------------------------------------


def test_create_custom_item_puts_it_on_the_character(qapp, data) -> None:
    char = _hero(data)
    section = EquipmentSection(data, char)
    changes: list = []
    section.changed.connect(lambda: changes.append(True))

    section._create_custom_item()
    window = section._windows[0]
    window._name.setText("Monowire Garrote")
    window._select_category("close_weapon")
    window.canvas.add_effect("damage")
    window._save_power()

    assert [item.name for item in char.equipment] == ["Monowire Garrote"]
    assert char.equipment[0].category == "close_weapon"
    assert char.equipment[0].catalog_id == ""  # off-catalog, so priced from its effects
    assert changes and section._windows == []


def test_editing_an_item_replaces_it_in_place(qapp, data) -> None:
    char = _hero(data, "sword")
    section = EquipmentSection(data, char)
    original = char.equipment[0]

    section._edit_item(original)
    window = section._windows[0]
    window._name.setText("Ancestral Blade")
    window._save_power()

    assert len(char.equipment) == 1
    assert char.equipment[0].name == "Ancestral Blade"
    assert char.equipment[0].id == original.id  # the same item, not a second one
    assert char.equipment[0] is not original  # but the copy, so an abandon is a no-op


def test_closing_the_editor_without_saving_changes_nothing(qapp, data) -> None:
    char = _hero(data, "sword")
    section = EquipmentSection(data, char)
    original = char.equipment[0]

    section._edit_item(original)
    window = section._windows[0]
    window._name.setText("Ancestral Blade")
    window._stacks.setChecked(True)
    window.close()

    assert char.equipment[0] is original
    assert original.name == "Sword" and original.stacks is False
    assert section._windows == []


def test_editing_an_items_build_reprices_it_off_the_catalog(qapp, data) -> None:
    """A sword the book prices at 3 EP stops being the book's sword the moment its
    build changes, and is priced from its effects from then on."""
    char = _hero(data, "sword")
    section = EquipmentSection(data, char)

    section._edit_item(char.equipment[0])
    window = section._windows[0]
    window.canvas.cards[0]._rank.setValue(9)
    window._save_power()

    item = char.equipment[0]
    assert item_is_stock(item, data) is False
    assert item_ep_cost(item, data, char) == 9  # nine ranks of Damage, undiscounted


def test_changing_an_items_group_moves_its_card(qapp, data) -> None:
    char = _hero(data, "sword")
    section = EquipmentSection(data, char)

    section._edit_item(char.equipment[0])
    window = section._windows[0]
    window._select_category("utility")
    window._save_power()

    assert char.equipment[0].category == "utility"
    assert [c for c in section._grouped_items()] == ["utility"]


def test_an_edited_item_stopped_from_stacking_keeps_the_homerule_badge(qapp, data) -> None:
    char = _hero(data, "leather_armor")
    section = EquipmentSection(data, char)

    section._edit_item(char.equipment[0])
    window = section._windows[0]
    window._stacks.setChecked(True)
    window._save_power()

    assert char.equipment[0].stacks is True
    assert section._is_homerule(char.equipment[0]) is True


def test_an_item_removed_while_its_editor_was_open_is_re_added(qapp, data) -> None:
    char = _hero(data, "sword")
    section = EquipmentSection(data, char)
    original = char.equipment[0]

    section._edit_item(original)
    window = section._windows[0]
    section._remove_item(original)
    window._save_power()

    assert [item.name for item in char.equipment] == ["Sword"]


# -- view mode -------------------------------------------------------------------------


def test_the_builder_is_out_of_reach_in_the_locked_sheet(qapp, data) -> None:
    char = _hero(data, "sword")
    section = EquipmentSection(data, char)
    section._create_custom_item()

    section.set_locked(True)

    assert section._custom_button.isVisibleTo(section) is False
    assert section._windows == []  # an editor left open is not a read-only view


def test_a_custom_item_survives_a_save_and_load(qapp, data) -> None:
    """The build state the constructor writes is all persisted; ``worn`` deliberately
    is not, exactly as a power's ``activated`` is not."""
    item = EquipmentItem(category="utility", stacks=True, ep_override=4)
    item.build.name = "Grapnel"

    restored = EquipmentItem.from_dict(item.to_dict())

    assert restored.name == "Grapnel"
    assert (restored.category, restored.stacks, restored.ep_override) == ("utility", True, 4)

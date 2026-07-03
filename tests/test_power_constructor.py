"""The Power Constructor window builds and mutates a Power via its drop seams.

Real drag-and-drop events are unreliable headless, so these drive the public
mutation methods the drop handlers delegate to (``add_effect`` / ``attach_modifier``).
"""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QApplication

from mm_companion.core.data_loader import load_game_data
from mm_companion.ui.character_sheet import CharacterSheet
from mm_companion.ui.power_constructor import PowerConstructorWindow


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_dropping_an_effect_adds_a_card_and_costs(qapp: QApplication) -> None:
    window = PowerConstructorWindow(load_game_data())
    card = window.canvas.add_effect("damage")
    assert len(window.canvas.cards) == 1
    assert window.power.effects[0].effect_id == "damage"
    assert window._cost.text() == "Total cost: 1 PP"  # Damage rank 1

    card._rank.setValue(8)
    assert window.power.effects[0].rank == 8
    assert window._cost.text() == "Total cost: 8 PP"


def test_attaching_a_modifier_updates_model_and_cost(qapp: QApplication) -> None:
    window = PowerConstructorWindow(load_game_data())
    card = window.canvas.add_effect("damage")
    card._rank.setValue(8)
    card.attach_modifier("ranged")  # per-rank extra: (1 + 1) * 8 = 16

    assert window.power.effects[0].extras[0].modifier_id == "ranged"
    assert window._cost.text() == "Total cost: 16 PP"


def test_ranked_modifier_chip_has_a_rank_spin_box_that_drives_cost(qapp: QApplication) -> None:
    from PySide6.QtWidgets import QSpinBox

    window = PowerConstructorWindow(load_game_data())
    card = window.canvas.add_effect("damage")
    card._rank.setValue(5)
    card.attach_modifier("accurate")  # ranked flat extra

    chip = card._chips[0]
    spin = chip.findChild(QSpinBox)
    assert spin is not None  # ranked modifiers expose a rank spin box
    spin.setValue(3)

    assert window.power.effects[0].extras[0].rank == 3
    assert window._cost.text() == "Total cost: 8 PP"  # 1*5 + 1*3


def test_unranked_modifier_chip_has_no_rank_spin_box(qapp: QApplication) -> None:
    from PySide6.QtWidgets import QSpinBox

    window = PowerConstructorWindow(load_game_data())
    card = window.canvas.add_effect("damage")
    card.attach_modifier("ranged")  # per-rank, not ranked

    assert card._chips[0].findChild(QSpinBox) is None


def test_removing_an_effect_clears_it(qapp: QApplication) -> None:
    window = PowerConstructorWindow(load_game_data())
    card = window.canvas.add_effect("damage")
    window.canvas._remove_card(card)
    assert window.canvas.cards == []
    assert window.power.effects == []
    assert window._cost.text() == "Total cost: 0 PP"


def test_name_and_description_write_to_model(qapp: QApplication) -> None:
    window = PowerConstructorWindow(load_game_data())
    window._name.setText("Fire Blast")
    window._description.setPlainText("whoosh")
    assert window.power.name == "Fire Blast"
    assert window.power.description == "whoosh"


def test_powers_section_launches_and_locks(qapp: QApplication) -> None:
    sheet = CharacterSheet(load_game_data())
    button = sheet.powers._add_button
    assert button.isVisibleTo(sheet.powers)
    sheet.set_locked(True)
    assert not button.isVisibleTo(sheet.powers)

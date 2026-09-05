"""No widget on a refresh path may be shown while it is parentless.

``setParent(None)`` makes a widget a *top-level window*, and so does building one
with no parent. Either way, showing it from there realizes a real native window:
on Windows a small grey rectangle flashes on screen and is gone again a moment
later, and realizing/destroying the native window is slow enough to be felt as a
hitch. It has bitten this project twice — ``2536db9`` for the power card's header
buttons (``setVisible(True)`` before parenting), and again for the *teardown* half
that :func:`~mm_companion.ui.widgets.discard_widget` now owns, which flashed a row
of the System block's speed readout on **every** spin-box step of an ability.

These tests watch for the symptom itself rather than for either cause, so a third
road to it fails here too: an application-wide event filter records every ``Show``
delivered to a widget with no parent, and the refresh paths are then driven.
"""

from __future__ import annotations

import pytest
from PySide6.QtCore import QEvent, QObject
from PySide6.QtWidgets import QApplication, QWidget

from mm_companion.core.character import AdvantageSelection, Character
from mm_companion.core.data_loader import load_game_data
from mm_companion.core.powers import Power, PowerEffectInstance
from mm_companion.core.rules.equipment import build_item_from_entry
from mm_companion.ui.character_sheet import CharacterSheet
from mm_companion.ui.damage_row import DamageRow


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


@pytest.fixture(scope="module")
def data():
    return load_game_data()


class ParentlessShowWatcher(QObject):
    """Records every ``Show`` event delivered to a widget that has no parent.

    Installed on the application, so it sees the shows Qt itself posts from C++ —
    which is how the teardown flash arrived, with no Python frame anywhere on the
    stack to catch it by patching.
    """

    def __init__(self) -> None:
        super().__init__()
        self.hits: list[str] = []

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        if (
            event.type() == QEvent.Type.Show
            and isinstance(obj, QWidget)
            and obj.parentWidget() is None
        ):
            self.hits.append(f"{type(obj).__module__}.{type(obj).__name__}")
        return False


@pytest.fixture
def watcher(qapp):
    """Watch for parentless shows, from the point the test starts driving.

    Kept in a fixture because the filter must outlive the call that installs it —
    a filter created inline is garbage collected immediately and silently sees
    nothing, which reads exactly like a passing test.
    """
    watch = ParentlessShowWatcher()
    qapp.installEventFilter(watch)
    yield watch
    qapp.removeEventFilter(watch)


def _furnished_hero(data) -> Character:
    """A character with enough on it that every card-bearing block has content."""
    char = Character.new_default(data)
    char.advantages.append(AdvantageSelection(name="Equipment", rank=5))
    for catalog_id in ("chain_mail", "leather_armor"):
        char.equipment.append(build_item_from_entry(data.equipment_catalog()[catalog_id], data))
    char.powers.append(Power(name="Blast", effects=[PowerEffectInstance("damage", rank=6)]))
    char.powers.append(Power(name="Armor", effects=[PowerEffectInstance("protection", rank=6)]))
    return char


def test_ticking_an_ability_flashes_no_window(qapp, data, watcher) -> None:
    """The report that started this: a small window on every ability spin step.

    The System block redraws its speed rows on ``derived-changed``, and the
    discarded row became a window on its way to ``deleteLater``.
    """
    sheet = CharacterSheet(data, _furnished_hero(data))
    sheet.show()
    qapp.processEvents()
    watcher.hits.clear()

    for spin in sheet.abilities._abilities.values():
        spin.setValue(spin.value() + 1)
        qapp.processEvents()

    assert watcher.hits == []


def test_ticking_a_resistance_flashes_no_window(qapp, data, watcher) -> None:
    sheet = CharacterSheet(data, _furnished_hero(data))
    sheet.show()
    qapp.processEvents()
    watcher.hits.clear()

    for spin in sheet.resistances._resistances.values():
        spin.setValue(spin.value() + 1)
        qapp.processEvents()

    assert watcher.hits == []


def test_a_damage_row_is_not_visible_before_it_is_parented(qapp, data) -> None:
    """``npc_card`` builds one with no parent and adds it to a layout afterwards.

    A visible parentless widget *is* a window, so this is the whole assertion: what
    the GM saw flashing on every condition they applied was this row.
    """
    row = DamageRow(data)

    assert not row.isVisible()

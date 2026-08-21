"""The Dynamic pool dialog and the allocator above its spin boxes.

The arithmetic itself lives in ``tests/test_powers.py`` (``dynamic_rank_share``,
``array_pool_points``); what is asserted here is the *control* — that the two ways of
making a split agree, that the allocator can only describe a legal one, and that the
spin boxes stay the state everything else reads.
"""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QApplication

from mm_companion.core.character import Character
from mm_companion.core.data_loader import load_game_data
from mm_companion.core.powers import (
    STRUCTURE_ARRAY,
    Power,
    PowerEffectInstance,
    PowerGroup,
)
from mm_companion.ui.sections.dynamic_pool_dialog import DynamicPoolDialog
from mm_companion.ui.sections.pool_allocator import TwoWayPoolSlider, make_allocator


@pytest.fixture()
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


def _array(*dynamic: bool) -> tuple[PowerGroup, Character, object]:
    """An array whose members are Damage 10 (10 PP), Flight 5 (10) and Protection 8 (8),
    each marked Dynamic or not as asked. The base — and so the pool — is the Damage: it
    ties with the Flight on cost, and a tie breaks to the first member."""

    data = load_game_data()
    char = Character.new_default(data)
    builds = (
        ("Fire Blast", "damage", 10),
        ("Flame Jets", "flight", 5),
        ("Heat Shield", "protection", 8),
    )
    children = []
    for (name, effect_id, rank), is_dynamic in zip(builds, dynamic, strict=False):
        power = Power(name=name, effects=[PowerEffectInstance(effect_id, rank=rank)])
        power.dynamic = is_dynamic
        children.append(power)
    group = PowerGroup(mode=STRUCTURE_ARRAY, name="Fire Control", children=children)
    char.powers.append(group)
    return group, char, data


def test_two_dynamic_members_get_a_slider(qapp: QApplication) -> None:
    group, char, data = _array(False, True, True)
    dialog = DynamicPoolDialog(group, data, char)
    assert isinstance(dialog._allocator, TwoWayPoolSlider)
    # Both ends name their member and say what its share currently buys.
    assert dialog._allocator_detail(0, 4) == "Flight 2 of 5"
    assert dialog._allocator_detail(1, 0) == "off"


def test_any_other_number_of_members_falls_back_to_the_spin_boxes(qapp: QApplication) -> None:
    # One Dynamic member has nothing to divide *between*; three want the polygon that is
    # not built yet. Neither is a failure — the spin grid expresses every split.
    for flags in ((False, True, False), (True, True, True)):
        group, char, data = _array(*flags)
        assert DynamicPoolDialog(group, data, char)._allocator is None
    assert make_allocator(["A", "B"], 0) is None  # nothing to split
    with pytest.raises(ValueError):
        TwoWayPoolSlider(["A", "B", "C"], 10)


def test_the_slider_writes_a_whole_pool_split_through_to_the_spins(qapp: QApplication) -> None:
    group, char, data = _array(False, True, True)
    dialog = DynamicPoolDialog(group, data, char)
    assert dialog._pool == 10  # the Damage 10 base is what the array's points are

    dialog._allocator._slider.setValue(7)
    assert [row.points for row in dialog._rows] == [7, 3]
    assert dialog.assigned() == dialog._pool

    # ...and back the other way, past the bound the *previous* split left on each row.
    dialog._allocator._slider.setValue(2)
    assert [row.points for row in dialog._rows] == [2, 8]
    assert dialog.assigned() == dialog._pool


def test_a_split_typed_into_the_spins_moves_the_handle(qapp: QApplication) -> None:
    group, char, data = _array(False, True, True)
    dialog = DynamicPoolDialog(group, data, char)

    dialog._rows[0].spin.setValue(6)
    assert dialog._allocator.shares() == [6, 0]
    assert dialog._allocator._slider.value() == 6
    # The spins can leave part of the pool spare, which no allocator movement can — so
    # the handle reports where it is and the note says what is unspent.
    assert "4 PP of the pool is unassigned" in dialog._allocator._note.text()

    dialog._rows[1].spin.setValue(4)
    assert dialog._allocator.shares() == [6, 4]
    assert not dialog._allocator._note.isVisible()


def test_clearing_the_split_resets_both_controls(qapp: QApplication) -> None:
    group, char, data = _array(False, True, True)
    dialog = DynamicPoolDialog(group, data, char)
    dialog._allocator._slider.setValue(7)

    dialog._clear()
    assert [row.points for row in dialog._rows] == [0, 0]
    assert dialog._allocator.shares() == [0, 0]
    # A row left at zero is stored as no share at all, so clearing leaves a saved
    # character byte-for-byte what it was.
    dialog.apply_to()
    assert [child.dynamic_points for child in group.children] == [None, None, None]


def test_a_share_the_handle_is_only_passing_over_still_describes_itself(
    qapp: QApplication,
) -> None:
    group, char, data = _array(False, True, True)
    dialog = DynamicPoolDialog(group, data, char)
    # The allocator asks what a share buys before the spins hold it, so the numbers move
    # under the thumb rather than a beat behind it.
    assert dialog._allocator_detail(0, 2) == "Flight 1 of 5"
    assert dialog._allocator_detail(0, 1) == "too few points to run"
    assert [row.points for row in dialog._rows] == [0, 0]

"""Undo and redo over a live character sheet.

The controller is driven through its explicit ``flush()`` rather than its timer, so
these need no event loop — the same bargain ``test_session_player.py`` strikes with
``push_now()``. What is *recorded* is tested here; what a restore does to the
model is ``test_character_restore.py``, and what it does to the widgets is
``test_ui_wiring.py``.
"""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QApplication

from mm_companion.core.character import Character
from mm_companion.core.data_loader import load_game_data
from mm_companion.core.powers import ModifierSelection, Power, PowerEffectInstance
from mm_companion.ui.character_sheet import CharacterSheet
from mm_companion.ui.sections.powers import _DraggableCard
from mm_companion.ui.undo import UndoController, absorbing


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


def _sheet_and_undo(character: Character | None = None) -> tuple[CharacterSheet, UndoController]:
    sheet = CharacterSheet(load_game_data(), character)
    return sheet, UndoController(sheet)


def test_a_burst_of_edits_is_one_undo_step(qapp: QApplication) -> None:
    """Dragging a spin box fires one signal per step; one gesture is one step back."""
    sheet, undo = _sheet_and_undo()
    for value in (1, 2, 3, 4):
        sheet.abilities._abilities["STR"].setValue(value)
    assert undo.pending  # armed, not yet recorded

    undo.flush()

    assert undo.can_undo
    undo.undo()
    assert sheet.character.abilities["STR"] == 0
    assert not undo.can_undo


def test_undo_restores_the_model_and_the_widget(qapp: QApplication) -> None:
    """A restore nobody can see would be worse than none."""
    sheet, undo = _sheet_and_undo()
    sheet.abilities._abilities["STR"].setValue(6)
    undo.flush()

    undo.undo()

    assert sheet.character.abilities["STR"] == 0
    assert sheet.abilities._abilities["STR"].value() == 0


def test_redo_reapplies_what_undo_took_back(qapp: QApplication) -> None:
    sheet, undo = _sheet_and_undo()
    sheet.abilities._abilities["STR"].setValue(6)
    undo.flush()
    undo.undo()

    assert undo.can_redo
    undo.redo()

    assert sheet.character.abilities["STR"] == 6
    assert sheet.abilities._abilities["STR"].value() == 6
    assert not undo.can_redo


def test_an_edit_after_an_undo_clears_the_redo_stack(qapp: QApplication) -> None:
    """A fresh edit is the user branching away from what they walked back through."""
    sheet, undo = _sheet_and_undo()
    sheet.abilities._abilities["STR"].setValue(6)
    undo.flush()
    undo.undo()
    assert undo.can_redo

    sheet.abilities._abilities["AGL"].setValue(2)
    undo.flush()

    assert not undo.can_redo


def test_a_signal_with_no_model_change_records_nothing(qapp: QApplication) -> None:
    """A block may restate itself and emit; only a moved model is a step.

    This is what makes the design tolerant of a spurious ``changed`` — including the
    normalisation a render is allowed to do — for the price of one string compare.
    """
    sheet, undo = _sheet_and_undo()

    sheet.powers.changed.emit()
    undo.flush()

    assert not undo.can_undo


def test_the_history_is_capped_and_drops_the_oldest(qapp: QApplication) -> None:
    sheet, undo = _sheet_and_undo()
    undo._depth = 3

    for value in range(1, 6):
        sheet.abilities._abilities["STR"].setValue(value)
        undo.flush()

    assert len(undo._undo) == 3
    for _ in range(3):
        undo.undo()
    assert not undo.can_undo
    assert sheet.character.abilities["STR"] == 2  # as far back as the cap allows


def test_a_runtime_toggle_is_a_step_of_its_own(qapp: QApplication) -> None:
    """Switching a power off is a play action, and one that is saved with the build.

    It used to be neither undoable nor in the snapshot — a second click was the only
    way back. Now that the flags round-trip, a toggle is an ordinary step: ``Ctrl+Z``
    puts the power back on, and undoing something *else* still leaves it where the
    player left it, because every snapshot in the history carries it.
    """
    char = Character.new_default(load_game_data())
    char.powers.append(
        Power(
            name="Armor",
            effects=[
                PowerEffectInstance(
                    effect_id="protection", rank=6, flaws=[ModifierSelection("removable")]
                )
            ],
        )
    )
    sheet, undo = _sheet_and_undo(char)

    card = sheet.powers.findChild(_DraggableCard)
    card.clicked.emit()  # the card *is* the on/off switch
    undo.flush()
    assert undo.can_undo  # the switch is saved, so taking it back is a step
    assert char.powers[0].item_present is False

    sheet.abilities._abilities["STR"].setValue(4)
    undo.flush()
    undo.undo()

    assert char.abilities["STR"] == 0  # the build edit went back
    assert char.powers[0].item_present is False  # the switch did not

    undo.undo()  # and one more step does take the switch back
    assert char.powers[0].item_present is True


def test_an_absorbed_change_becomes_the_baseline(qapp: QApplication) -> None:
    """A GM's push is not the player's to take back — but their own edit still is."""
    sheet, undo = _sheet_and_undo()
    sheet.abilities._abilities["STR"].setValue(4)

    with absorbing(sheet):
        sheet.conditions.apply_condition_by_id("dazed", None)

    assert [c.condition_id for c in sheet.character.conditions] == ["dazed"]
    assert undo.can_undo  # the local edit, landed by absorb()'s own flush

    undo.undo()

    assert sheet.character.abilities["STR"] == 0
    assert [c.condition_id for c in sheet.character.conditions] == ["dazed"]
    assert not undo.can_undo


def test_an_absorbed_change_survives_walking_further_back(qapp: QApplication) -> None:
    """ "Not undoable" has to hold at every depth, not just for the next Ctrl+Z.

    Every remembered state predates the GM's condition, so without replaying the
    change onto the history, stepping back two edits would quietly shed it — and the
    snapshot pusher would send that back to the table.
    """
    sheet, undo = _sheet_and_undo()
    sheet.abilities._abilities["STR"].setValue(4)
    undo.flush()
    sheet.abilities._abilities["AGL"].setValue(2)
    undo.flush()

    with absorbing(sheet):
        sheet.conditions.apply_condition_by_id("dazed", None)

    undo.undo()
    undo.undo()

    assert sheet.character.abilities["STR"] == 0
    assert sheet.character.abilities["AGL"] == 0
    assert [c.condition_id for c in sheet.character.conditions] == ["dazed"]


def test_replaying_a_hero_point_command_leaves_a_local_power_level_edit_alone(
    qapp: QApplication,
) -> None:
    """Both live in ``characteristics``, so the replay has to go a level in."""
    sheet, undo = _sheet_and_undo()
    sheet.system_info._power_level.setValue(12)
    undo.flush()

    with absorbing(sheet):
        sheet.system_info.set_hero_points(4)

    undo.undo()

    assert sheet.character.characteristics["power_level"] == 10  # the local edit went back
    assert sheet.character.characteristics["hero_points"] == 4  # the GM's did not


def test_absorbing_a_sheet_with_no_controller_is_a_no_op(qapp: QApplication) -> None:
    """A GM's read-only view has no history; the caller must not have to know."""
    sheet = CharacterSheet(load_game_data())

    with absorbing(sheet):
        sheet.conditions.apply_condition_by_id("dazed", None)

    assert [c.condition_id for c in sheet.character.conditions] == ["dazed"]


def test_undoing_back_to_the_saved_state_reports_clean(qapp: QApplication) -> None:
    """Content, not a stack position — walking back by any route is clean."""
    sheet, undo = _sheet_and_undo()
    undo.mark_saved()
    assert undo.at_saved_state()

    sheet.abilities._abilities["STR"].setValue(4)
    undo.flush()
    assert not undo.at_saved_state()

    undo.undo()
    assert undo.at_saved_state()


def test_a_fresh_sheet_is_not_clean_before_it_has_been_saved(qapp: QApplication) -> None:
    """Nothing to be clean *against* — which is what keeps the window's ``*`` honest."""
    _sheet, undo = _sheet_and_undo()
    assert not undo.at_saved_state()


def test_a_restore_does_not_mark_the_sheet_edited(qapp: QApplication) -> None:
    """Re-seeding the blocks writes their widgets, which normally reports an edit."""
    sheet, undo = _sheet_and_undo()
    sheet.abilities._abilities["STR"].setValue(4)
    undo.flush()

    edits: list[int] = []
    sheet.edited.connect(lambda: edits.append(1))
    undo.undo()

    assert not edits


def test_undo_works_on_a_locked_sheet(qapp: QApplication) -> None:
    """Conditions and hero points are editable while locked, so undo must be too."""
    sheet, undo = _sheet_and_undo()
    sheet.set_locked(True)

    sheet.conditions.apply_condition_by_id("dazed", None)
    undo.flush()
    assert undo.can_undo

    undo.undo()

    assert sheet.character.conditions == []


def test_can_undo_counts_a_step_still_coalescing(qapp: QApplication) -> None:
    """Otherwise the button is dead for 400ms after the edit that should have lit it."""
    sheet, undo = _sheet_and_undo()

    sheet.abilities._abilities["STR"].setValue(4)

    assert undo.can_undo
    undo.undo()
    assert sheet.character.abilities["STR"] == 0

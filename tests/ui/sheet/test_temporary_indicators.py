"""The sheet's temporary-condition indicators (combat maneuvers, phase 2).

A temporary condition — an All-Out Attack's −Defense, Defending's +2 — lasts until the
start of its bearer's next turn and is removed by hand. The sheet says which numbers are
only borrowed: a dot beside every stat it moves, its chip in its tone's colour with a ✕,
and a "Clear temporary" that is there only while something is.
"""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QApplication, QInputDialog, QToolButton

from mm_companion.core.character import Character
from mm_companion.core.powers import Power, PowerEffectInstance
from mm_companion.core.rules import MARK_BETTER, MARK_WORSE
from mm_companion.ui import theme
from mm_companion.ui.cards.rolls import RollsFooter
from mm_companion.ui.character_sheet import CharacterSheet
from mm_companion.ui.sections.conditions import DISMISS_GLYPH
from mm_companion.ui.simple.views import ConditionsView, ResistancesView
from mm_companion.ui.stat_mark import MARK_ROLE, StatMark


def _hero(data) -> Character:
    char = Character.new_default(data)
    char.abilities.update({"STR": 4, "STA": 5, "AGL": 3, "AWE": 2, "ATK": 7})
    char.resistances["DEF"] = 8
    char.powers.append(Power(name="Blast", effects=[PowerEffectInstance("damage", rank=8)]))
    return char


@pytest.fixture
def sheet(qapp: QApplication, data) -> CharacterSheet:
    made = CharacterSheet(data, _hero(data))
    yield made
    made.close()


def _settle(qapp: QApplication, sheet: CharacterSheet) -> None:
    sheet.bus.flush()
    qapp.processEvents()


def _apply(qapp, sheet, condition_id: str, **kw) -> None:
    sheet.conditions.apply_condition_by_id(condition_id, **kw)
    _settle(qapp, sheet)


def _defense(sheet: CharacterSheet):
    return sheet.resistances._resistance_total["DEF"]


def _clear_button(sheet: CharacterSheet) -> QToolButton:
    return sheet.conditions._clear_temporary_button


# -- dots ---------------------------------------------------------------------------------


def test_a_temporary_penalty_marks_the_defense_total_worse(qapp, sheet) -> None:
    assert _defense(sheet).data(MARK_ROLE) is None
    _apply(qapp, sheet, "all_out_attack", amount=3)

    cell = _defense(sheet)
    assert cell.text() == "5"
    assert cell.data(MARK_ROLE) == MARK_WORSE
    assert "All-Out Attack -3" in cell.toolTip()
    assert cell.foreground().color().name() == theme.color("tint.worse")


def test_a_temporary_bonus_marks_it_better_and_tints_it_green(qapp, sheet) -> None:
    _apply(qapp, sheet, "defending")

    cell = _defense(sheet)
    assert cell.text() == "10"
    assert cell.data(MARK_ROLE) == MARK_BETTER
    # It used to read in the penalty red like every condition overlay.
    assert cell.foreground().color().name() == theme.color("tint.better")


def test_the_mark_goes_when_the_condition_does(qapp, sheet) -> None:
    _apply(qapp, sheet, "defending")
    sheet.conditions.clear_temporary_conditions()
    _settle(qapp, sheet)
    assert _defense(sheet).data(MARK_ROLE) is None


def test_a_marked_table_still_paints(qapp, sheet) -> None:
    """The delegate draws the dot over the cell — exercised, not just configured."""
    _apply(qapp, sheet, "all_out_attack", amount=2)
    assert not sheet.resistances.table.grab().isNull()


def test_the_attack_line_wears_the_attack_checks_dot(qapp, sheet) -> None:
    footer = sheet.powers.findChildren(RollsFooter)[0]
    assert footer.marked_lines() == []

    _apply(qapp, sheet, "defensive_attack", amount=2)

    footer = sheet.powers.findChildren(RollsFooter)[0]
    assert footer.marked_lines() == [MARK_WORSE]  # the attack line; the save has none


def test_a_stat_mark_keeps_its_room_while_hidden() -> None:
    mark = StatMark()
    assert mark.isHidden() and mark.sizePolicy().retainSizeWhenHidden()
    assert mark.tone == ""


# -- chips --------------------------------------------------------------------------------


def _dismiss_buttons(sheet: CharacterSheet) -> list[QToolButton]:
    return [b for b in sheet.conditions.findChildren(QToolButton) if b.text() == DISMISS_GLYPH]


def test_a_temporary_chip_has_its_own_cross(qapp, sheet) -> None:
    _apply(qapp, sheet, "prone")
    assert _dismiss_buttons(sheet) == []

    _apply(qapp, sheet, "defending")
    buttons = _dismiss_buttons(sheet)
    assert len(buttons) == 1
    buttons[0].click()
    _settle(qapp, sheet)
    assert [c.condition_id for c in sheet.character.conditions] == ["prone"]


def test_a_benefit_chip_is_drawn_in_the_benefit_tint(qapp, sheet) -> None:
    _apply(qapp, sheet, "defending")
    chip = sheet.conditions._condition_chips[0]
    assert theme.color("tint.better") in chip.styleSheet()


# -- Clear temporary ----------------------------------------------------------------------


def test_clear_temporary_shows_only_while_something_is_temporary(qapp, sheet) -> None:
    sheet.show()
    _apply(qapp, sheet, "prone")
    assert not _clear_button(sheet).isVisibleTo(sheet.conditions)

    _apply(qapp, sheet, "all_out_attack", amount=2)
    assert _clear_button(sheet).isVisibleTo(sheet.conditions)


def test_clear_temporary_takes_only_the_temporary_ones_and_says_so(qapp, sheet) -> None:
    notes: list[str] = []
    sheet.conditions.noteRequested.connect(notes.append)
    _apply(qapp, sheet, "prone")
    _apply(qapp, sheet, "all_out_attack", amount=2)
    _apply(qapp, sheet, "defending")

    _clear_button(sheet).click()
    _settle(qapp, sheet)

    assert [c.condition_id for c in sheet.character.conditions] == ["prone"]
    assert notes == ["temporary conditions cleared: All-Out Attack -2, Defending +2"]
    assert not _clear_button(sheet).isVisibleTo(sheet.conditions)


def test_the_plus_menu_asks_how_big_an_all_out_attack_is(qapp, sheet, data, monkeypatch) -> None:
    asked: list[int] = []

    def fake_get_int(parent, title, label, value, low, high):
        asked.append(high)
        return 3, True

    monkeypatch.setattr(QInputDialog, "getInt", staticmethod(fake_get_int))
    sheet.conditions.choose_condition(data.condition_catalog()["all_out_attack"])

    assert asked == [5]  # the record's maxAmount
    applied = sheet.character.conditions[0]
    assert applied.amount == 3 and applied.temporary


def test_cancelling_the_amount_applies_nothing(qapp, sheet, data, monkeypatch) -> None:
    monkeypatch.setattr(QInputDialog, "getInt", staticmethod(lambda *a: (1, False)))
    sheet.conditions.choose_condition(data.condition_catalog()["defensive_attack"])
    assert sheet.character.conditions == []


def test_defending_is_applied_without_asking(qapp, sheet, data, monkeypatch) -> None:
    def refuse(*_args):
        raise AssertionError("asked for an amount Defending has by default")

    monkeypatch.setattr(QInputDialog, "getInt", staticmethod(refuse))
    sheet.conditions.choose_condition(data.condition_catalog()["defending"])
    assert sheet.character.conditions[0].amount == 2


# -- the simple sheet ---------------------------------------------------------------------


def _simple(qapp, sheet: CharacterSheet) -> CharacterSheet:
    sheet.resize(1100, 900)
    sheet.show()
    _settle(qapp, sheet)
    sheet.set_simple(True)
    _settle(qapp, sheet)
    return sheet


def test_the_simple_sheets_defense_box_wears_the_dot(qapp, sheet) -> None:
    _simple(qapp, sheet)
    _apply(qapp, sheet, "defending")
    view = sheet.simple_sheet.findChildren(ResistancesView)[0]
    view.refresh()
    assert view.boxes["DEF"].mark.tone == MARK_BETTER
    assert view.boxes["DODGE"].mark.tone == ""


def test_the_simple_sheet_clears_temporary_too(qapp, sheet) -> None:
    _simple(qapp, sheet)
    view = sheet.simple_sheet.findChildren(ConditionsView)[0]
    _apply(qapp, sheet, "all_out_attack", amount=1)
    view.refresh()
    assert view.clear_button.isVisibleTo(view)

    view.clear_button.click()
    _settle(qapp, sheet)
    view.refresh()

    assert sheet.character.conditions == []
    assert not view.clear_button.isVisibleTo(view)
